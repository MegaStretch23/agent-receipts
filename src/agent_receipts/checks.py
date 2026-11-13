"""Verify claims against a git repo, the file system and a user-supplied test command. No network, no model."""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

VERIFIED, CONTRADICTED, UNVERIFIABLE = "VERIFIED", "CONTRADICTED", "UNVERIFIABLE"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", "dist", "build", ".mypy_cache", ".pytest_cache"}
CODE_EXT = {".py": "py", ".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js", ".mjs": "js", ".go": "go", ".rs": "rs", ".java": "java",
            ".rb": "rb", ".php": "php", ".c": "c", ".h": "c", ".cc": "c", ".cpp": "c", ".hpp": "c", ".cs": "cs", ".kt": "java", ".swift": "swift", ".sh": "sh"}


class Repo:
    def __init__(self, path: str | Path, base: str | None = None):
        self.root = Path(path).resolve()
        self.base = base
        self._git_ok: bool | None = None
        self._changes: dict[str, set[str]] | None = None
        self._files: list[str] | None = None

    # ---- git plumbing
    def git(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, env=env)

    @property
    def is_git(self) -> bool:
        if self._git_ok is None:
            try:
                r = self.git("rev-parse", "--show-toplevel")
                self._git_ok = r.returncode == 0 and Path(r.stdout.strip()).resolve() == self.root
            except FileNotFoundError:
                self._git_ok = False
        return self._git_ok

    @property
    def has_head(self) -> bool:
        return self.is_git and self.git("rev-parse", "--verify", "-q", "HEAD").returncode == 0

    def rel(self, target: str) -> str | None:
        """Repo-relative posix path, or None if the target escapes the repo."""
        p = (self.root / target).resolve() if not os.path.isabs(target) else Path(target).resolve()
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return None

    def changes(self) -> dict[str, set[str]]:
        """path -> set of {A, M, D} over the window: working tree vs HEAD, plus --base..HEAD or the last commit."""
        if self._changes is not None:
            return self._changes
        ch: dict[str, set[str]] = {}

        def add(path: str, op: str):
            ch.setdefault(path, set()).add(op)

        if self.has_head:
            if self.base:
                r = self.git("diff", "--name-status", "-z", "--no-renames", self.base, "HEAD")
            else:
                r = self.git("diff-tree", "--root", "-r", "--no-commit-id", "--name-status", "-z", "--no-renames", "HEAD")
            parts = [x for x in r.stdout.split("\0") if x] if r.returncode == 0 else []
            for i in range(0, len(parts) - 1, 2):
                add(parts[i + 1], parts[i][0])
        if self.is_git:
            r = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all")
            parts = r.stdout.split("\0") if r.returncode == 0 else []
            i = 0
            while i < len(parts):
                e = parts[i]
                i += 1
                if len(e) < 4:
                    continue
                x, y, path = e[0], e[1], e[3:]
                if x in "RC":
                    old = parts[i] if i < len(parts) else ""
                    i += 1
                    if x == "R" and old:
                        add(old, "D")
                    add(path, "A")
                elif x == "?" or "A" in (x, y):
                    add(path, "A")
                elif "D" in (x, y):
                    add(path, "D")
                elif "M" in (x, y) or "T" in (x, y):
                    add(path, "M")
        self._changes = ch
        return ch

    def source_files(self) -> list[str]:
        if self._files is not None:
            return self._files
        files: list[str] = []
        if self.is_git:
            r = self.git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
            if r.returncode == 0:
                files = sorted({f for f in r.stdout.split("\0") if f and (self.root / f).is_file()})
        if not files and not self.is_git:
            for dp, dns, fns in os.walk(self.root):
                dns[:] = [d for d in dns if d not in SKIP_DIRS]
                files += [os.path.relpath(os.path.join(dp, f), self.root).replace(os.sep, "/") for f in fns]
            files.sort()
        self._files = files
        return files

    def ever_existed(self, rel: str) -> bool:
        if not self.has_head:
            return False
        r = self.git("log", "--all", "--format=%h", "-1", "--", rel)
        return r.returncode == 0 and bool(r.stdout.strip())


def _res(claim: dict, status: str, evidence: str) -> dict:
    return {**claim, "status": status, "evidence": evidence}


def _window_note(repo: Repo) -> str:
    return f"{repo.base}..HEAD plus working tree" if repo.base else "last commit plus working tree"


# ---------------------------------------------------------------- files
def check_file(repo: Repo, claim: dict) -> dict:
    typ, target = claim["type"], claim["target"]
    rel = repo.rel(target)
    if not rel:
        return _res(claim, UNVERIFIABLE, f"{target!r} resolves outside the repo (or is the repo itself); not checked")
    exists = (repo.root / rel).exists()
    if not repo.is_git or (not repo.has_head and not repo.changes()):
        why = "not a git repo" if not repo.is_git else "no commits and no changes"
        if typ == "file_deleted":
            return _res(claim, CONTRADICTED, f"{rel} still exists") if exists else _res(claim, UNVERIFIABLE, f"{rel} is absent, but there is no git history to show it ever existed ({why})")
        if not exists:
            return _res(claim, CONTRADICTED, f"{rel} does not exist")
        return _res(claim, UNVERIFIABLE, f"{rel} exists, but {why}: cannot tell what the agent did to it")
    ops = repo.changes().get(rel, set())
    win = _window_note(repo)
    explicit = bool(repo.base)
    if typ == "file_deleted":
        if exists:
            return _res(claim, CONTRADICTED, f"{rel} still exists")
        if "D" in ops:
            return _res(claim, VERIFIED, f"{rel} is absent and git shows it deleted ({win})")
        if repo.ever_existed(rel):
            if explicit:
                return _res(claim, CONTRADICTED, f"{rel} is absent and was in git history, but the deletion is not in {win}")
            return _res(claim, UNVERIFIABLE, f"{rel} is absent and was in git history, but the deletion is not in the default window ({win}); pass --base <ref>")
        return _res(claim, UNVERIFIABLE, f"{rel} is absent, but git history never had it")
    if not exists:
        return _res(claim, CONTRADICTED, f"{rel} does not exist" + (" (git shows it deleted)" if "D" in ops else ""))
    if typ == "file_created":
        if "A" in ops:
            return _res(claim, VERIFIED, f"{rel} exists and git shows it added ({win})")
        if "M" in ops:
            return _res(claim, CONTRADICTED, f"{rel} existed before: git shows it modified, not added ({win})")
    else:  # file_modified
        if "M" in ops:
            return _res(claim, VERIFIED, f"git shows {rel} modified ({win})")
        if "A" in ops:
            return _res(claim, VERIFIED, f"{rel} is new in the window (shown as added, not modified) ({win})")
    if explicit:
        return _res(claim, CONTRADICTED, f"{rel} exists but has no change in {win}")
    if repo.git("check-ignore", "-q", "--", rel).returncode == 0:
        return _res(claim, UNVERIFIABLE, f"{rel} is git-ignored, so git cannot show what happened to it")
    return _res(claim, UNVERIFIABLE, f"{rel} exists with no change in the default window ({win}); pass --base <ref> to check a longer range")


# ---------------------------------------------------------------- symbols
def _py_defs(src: str, name: str, want: str) -> list[int]:
    hits = []
    for n in ast.walk(ast.parse(src)):
        if want == "class" and isinstance(n, ast.ClassDef) and n.name == name:
            hits.append(n.lineno)
        elif want == "function" and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            hits.append(n.lineno)
    return hits


def _grep_re(name: str, want: str) -> re.Pattern:
    nm = re.escape(name)
    if want == "class":
        return re.compile(rf"\b(?:class|struct|interface|enum|trait)\s+{nm}\b")
    return re.compile(rf"\b(?:def|function|func|fn|sub)\s+(?:\([^)]*\)\s*)?{nm}\b|\b{nm}\s*[:=]\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>)")


def check_symbol(repo: Repo, claim: dict) -> dict:
    want = "class" if claim["type"] == "class_added" else "function"
    name = claim["target"].split(".")[-1]
    hits: list[tuple[str, int, str]] = []
    unparsable = 0
    for rel in repo.source_files():
        ext = Path(rel).suffix.lower()
        if ext not in CODE_EXT:
            continue
        try:
            src = (repo.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if name not in src:
            continue
        if CODE_EXT[ext] == "py":
            try:
                hits += [(rel, ln, "ast") for ln in _py_defs(src, name, want)]
                continue
            except SyntaxError:
                unparsable += 1
        m = _grep_re(name, want).search(src)
        if m:
            hits.append((rel, src.count("\n", 0, m.start()) + 1, "grep"))
    if not hits:
        if not any(Path(f).suffix.lower() in CODE_EXT for f in repo.source_files()):
            return _res(claim, UNVERIFIABLE, "no source files found in the repo")
        extra = f" ({unparsable} .py file(s) failed to parse and were grepped instead)" if unparsable else ""
        return _res(claim, CONTRADICTED, f"no {want} named {name} found in any source file{extra}")
    ch = repo.changes()
    touched = [h for h in hits if h[0] in ch]
    h0 = (touched or hits)[0]
    where = f"{h0[0]}:{h0[1]} ({h0[2]})" + (f" and {len(hits) - 1} more" if len(hits) > 1 else "")
    if touched:
        return _res(claim, VERIFIED, f"{want} {name} defined at {where}; file changed in window ({_window_note(repo)})")
    if repo.has_head:
        if repo.base:
            return _res(claim, CONTRADICTED, f"{want} {name} is defined at {where}, but that file has no change in {_window_note(repo)}")
        return _res(claim, UNVERIFIABLE, f"{want} {name} is defined at {where}, but that file has no change in the default window ({_window_note(repo)}); pass --base <ref>")
    return _res(claim, VERIFIED, f"{want} {name} defined at {where} (no git history to check that it is new)")


# ---------------------------------------------------------------- packages
def _norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n.strip().lower())


def _req_name(spec: str) -> str | None:
    s = spec.strip()
    if not s or s.startswith(("-", "#")):
        return None
    m = re.match(r"(@?[A-Za-z0-9][A-Za-z0-9._/@-]*)", s)
    return _norm(m.group(1)) if m else None


def _declared(repo: Repo) -> tuple[set[str], list[str]]:
    names: set[str] = set()
    files: list[str] = []
    for rel in repo.source_files():
        base = rel.rsplit("/", 1)[-1]
        full = repo.root / rel
        try:
            if base == "pyproject.toml":
                d = tomllib.loads(full.read_text(encoding="utf-8"))
                files.append(rel)
                proj = d.get("project", {})
                specs = list(proj.get("dependencies", []))
                for v in proj.get("optional-dependencies", {}).values():
                    specs += v
                for v in d.get("dependency-groups", {}).values():
                    specs += [x for x in v if isinstance(x, str)]
                specs += d.get("build-system", {}).get("requires", [])
                specs += d.get("tool", {}).get("uv", {}).get("dev-dependencies", [])
                poetry = d.get("tool", {}).get("poetry", {})
                names |= set(map(_norm, poetry.get("dependencies", {}))) | set(map(_norm, poetry.get("dev-dependencies", {})))
                for g in poetry.get("group", {}).values():
                    names |= set(map(_norm, g.get("dependencies", {})))
                names |= {n for n in map(_req_name, specs) if n}
            elif re.fullmatch(r"requirements[\w.-]*\.(?:txt|in)|constraints\.txt", base):
                files.append(rel)
                names |= {n for n in map(_req_name, full.read_text(encoding="utf-8").splitlines()) if n}
            elif base == "package.json":
                d = json.loads(full.read_text(encoding="utf-8"))
                files.append(rel)
                for k in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                    names |= set(map(_norm, d.get(k, {})))
            elif base == "Pipfile":
                files.append(rel)
                d = tomllib.loads(full.read_text(encoding="utf-8"))
                names |= set(map(_norm, d.get("packages", {}))) | set(map(_norm, d.get("dev-packages", {})))
            elif base in ("uv.lock", "poetry.lock"):
                files.append(rel)
                d = tomllib.loads(full.read_text(encoding="utf-8"))
                names |= {_norm(p["name"]) for p in d.get("package", []) if "name" in p}
            elif base in ("package-lock.json", "Pipfile.lock"):
                files.append(rel)
                d = json.loads(full.read_text(encoding="utf-8"))
                for k in ("default", "develop"):
                    names |= set(map(_norm, d.get(k, {})))
                for k in d.get("packages", {}):
                    if "node_modules/" in k:
                        names.add(_norm(k.rsplit("node_modules/", 1)[1]))
            elif base == "yarn.lock":
                files.append(rel)
                txt = full.read_text(encoding="utf-8")
                names |= {_norm(m.group(1)) for m in re.finditer(r'^"?(@?[A-Za-z0-9][\w.-]*(?:/[\w.-]+)?)@', txt, re.M)}
            elif base == "pnpm-lock.yaml":
                files.append(rel)
                txt = full.read_text(encoding="utf-8")
                names |= {_norm(m.group(1)) for m in re.finditer(r"^\s+/?'?(@?[A-Za-z0-9][\w.-]*(?:/[\w.-]+)?)@[\w^~<>=*.+-]+'?:\s*$", txt, re.M)}
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            files.append(rel + " (unreadable)")
    return names, files


def check_package(repo: Repo, claim: dict) -> dict:
    want = _norm(claim["target"])
    names, files = _declared(repo)
    if not files:
        return _res(claim, UNVERIFIABLE, "no pyproject.toml, requirements*.txt, package.json or lockfile in the repo")
    shown = ", ".join(files[:4]) + (" and others" if len(files) > 4 else "")
    if want in names:
        return _res(claim, VERIFIED, f"{claim['target']} is declared in {shown}")
    return _res(claim, CONTRADICTED, f"{claim['target']} not declared in {shown}")


# ---------------------------------------------------------------- tests
def check_tests(repo: Repo, claim: dict, test_cmd: str | None, timeout: int, cache: dict) -> dict:
    if not test_cmd:
        return _res(claim, UNVERIFIABLE, "no --test-cmd given; the tests were not re-run")
    if "run" not in cache:
        try:
            r = subprocess.run(test_cmd, shell=True, cwd=repo.root, capture_output=True, text=True, timeout=timeout)
            tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
            cache["run"] = (r.returncode, tail[0][:160])
        except subprocess.TimeoutExpired:
            cache["run"] = ("timeout", "")
        except OSError as e:
            cache["run"] = ("error", str(e)[:160])
    code, tail = cache["run"]
    if code == "timeout":
        return _res(claim, UNVERIFIABLE, f"`{test_cmd}` did not finish within {timeout}s")
    if code == "error":
        return _res(claim, UNVERIFIABLE, f"`{test_cmd}` could not start: {tail}")
    if code == 0:
        return _res(claim, VERIFIED, f"re-ran `{test_cmd}`: exit 0" + (f" (last line: {tail})" if tail else ""))
    return _res(claim, CONTRADICTED, f"re-ran `{test_cmd}`: exit {code}" + (f" (last line: {tail})" if tail else ""))


# ---------------------------------------------------------------- commits
def check_commit(repo: Repo, claim: dict) -> dict:
    if not repo.is_git:
        return _res(claim, UNVERIFIABLE, "not a git repo")
    if not repo.has_head:
        return _res(claim, CONTRADICTED, "the repo has no commits")
    target = claim.get("target", "")
    sha = claim.get("sha") or (target if re.fullmatch(r"[0-9a-f]{7,40}", target, re.I) and not claim.get("message") else "")
    msg = claim.get("message") or ("" if sha else target)
    if sha:
        r = repo.git("log", "-1", "--format=%h %s", f"{sha}^{{commit}}")
        if r.returncode != 0:
            return _res(claim, CONTRADICTED, f"no commit {sha} in this repo")
        return _res(claim, VERIFIED, f"commit {r.stdout.strip()[:120]}")
    if msg:
        r = repo.git("log", "-200", "--format=%h %s", "--fixed-strings", f"--grep={msg}")
        line = r.stdout.strip().splitlines()[:1]
        if line:
            return _res(claim, VERIFIED, f"commit {line[0][:120]} matches the message")
        return _res(claim, CONTRADICTED, f"none of the last 200 commits has a message containing {msg!r}")
    if repo.base:
        r = repo.git("rev-list", "--count", f"{repo.base}..HEAD")
        if r.returncode != 0:
            return _res(claim, UNVERIFIABLE, f"could not read {repo.base}..HEAD")
        n = int(r.stdout.strip() or 0)
        return _res(claim, VERIFIED if n else CONTRADICTED, f"{n} commit(s) in {repo.base}..HEAD")
    r = repo.git("log", "-1", "--format=%h %s")
    return _res(claim, UNVERIFIABLE, f"no sha, message or --base to bound it; latest commit is {r.stdout.strip()[:120]}")


# ---------------------------------------------------------------- driver
def verify_claims(claims: list[dict], repo: Repo, test_cmd: str | None = None, test_timeout: int = 600) -> list[dict]:
    cache: dict = {}
    out = []
    for c in claims:
        t = c["type"]
        if t in ("file_created", "file_modified", "file_deleted"):
            out.append(check_file(repo, c))
        elif t in ("function_added", "class_added"):
            out.append(check_symbol(repo, c))
        elif t == "package_installed":
            out.append(check_package(repo, c))
        elif t == "tests_pass":
            out.append(check_tests(repo, c, test_cmd, test_timeout, cache))
        elif t == "commit_made":
            out.append(check_commit(repo, c))
        else:
            out.append(_res(c, UNVERIFIABLE, f"unknown claim type {t}"))
    return out
