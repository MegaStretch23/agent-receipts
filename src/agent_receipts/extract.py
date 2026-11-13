"""Deterministic claim extraction: regexes only, no model calls.

A claim is a dict: {"type", "target", "text", "source"} (+ "sha"/"message" for commits).
Types: file_created, file_modified, file_deleted, tests_pass, function_added, class_added,
package_installed, commit_made.
"""
from __future__ import annotations

import re

TYPES = ("file_created", "file_modified", "file_deleted", "tests_pass", "function_added",
         "class_added", "package_installed", "commit_made")

ALIASES = {
    "created": "file_created", "create": "file_created", "added_file": "file_created", "file_added": "file_created",
    "modified": "file_modified", "modify": "file_modified", "updated": "file_modified", "edited": "file_modified",
    "file_updated": "file_modified", "file_edited": "file_modified",
    "deleted": "file_deleted", "delete": "file_deleted", "removed": "file_deleted", "file_removed": "file_deleted",
    "tests": "tests_pass", "test": "tests_pass", "tests_passed": "tests_pass", "tests_passing": "tests_pass",
    "function": "function_added", "method": "function_added", "function_created": "function_added", "method_added": "function_added",
    "class": "class_added", "class_created": "class_added",
    "package": "package_installed", "installed": "package_installed", "dependency_added": "package_installed",
    "dependency": "package_installed", "package_added": "package_installed",
    "commit": "commit_made", "committed": "commit_made",
}

# clauses containing these are not claims of work done (negated, planned or hypothetical)
NEG = re.compile(r"(?:\b(?:not|never|unable|cannot|without|skipped|todo|will|would|should|could|might|"
                 r"plans?\s+to|planned\s+to|going\s+to|needs?\s+to|yet\s+to|failed\s+to|fail\s+to|no\s+longer)\b|n't\b)", re.I)
FAIL_WORDS = re.compile(r"\bfail(?:s|ed|ing|ure|ures)?\b|\berrors?\b", re.I)

FILE_VERBS = {
    "file_created": r"created|added|wrote|written|generated|scaffolded|introduced",
    "file_modified": r"modified|updated|edited|changed|fixed|refactored|rewrote|patched|touched",
    "file_deleted": r"deleted|removed|dropped|git\s+rm",
}
VERB_RE = re.compile(r"\b(?:(?P<file_created>" + FILE_VERBS["file_created"] + r")|(?P<file_modified>" + FILE_VERBS["file_modified"] +
                     r")|(?P<file_deleted>" + FILE_VERBS["file_deleted"] + r"))\b", re.I)
SEG_SKIP = re.compile(r"^\W*(?:(?:a|an|the|new|python|helper|unit|public|private|async|some|more|one|two|three|several)\s+)*"
                      r"(?:functions?|methods?|class(?:es)?|packages?|dependenc\w+|librar\w+|tests?|commits?|imports?|endpoints?|"
                      r"routes?|handlers?|options?|flags?|sections?|comments?|docstrings?|entr(?:y|ies)|modules?|lines?|"
                      r"features?|support|check|checks|bugs?|typos?|errors?|issues?)(?![\w/-]|\.\w)", re.I)
SEG_CUT = re.compile(r"\s(?:from|using|via|based\s+on|by|per|like|with|for|after|before|instead\s+of|following|because|so\s+that)\s", re.I)
KNOWN_NOEXT = {"Makefile", "Dockerfile", "LICENSE", "README", "Procfile", "Jenkinsfile", "Gemfile", "Rakefile", "CODEOWNERS"}
TLDS = {"com", "org", "net", "io", "dev", "ai", "app", "co", "edu", "gov"}
NOT_FILES = {"e.g", "i.e", "etc.", "vs.", "e.g.", "i.e."}
BACKTICK = re.compile(r"`([^`\s]+)`")
BARE_PATH = re.compile(r"(?<![\w/.:@-])((?:\.{0,2}/)?[\w@~+.-]+(?:/[\w@~+.-]+)*\.[A-Za-z0-9]{1,8})(?![\w/-])")


def _is_path(tok: str, quoted: bool) -> bool:
    tok = tok.strip().rstrip(".,;:")
    if not tok or tok in NOT_FILES or "://" in tok or tok.startswith("www.") or tok.endswith("()"):
        return False
    if tok in KNOWN_NOEXT:
        return True
    base = tok.rsplit("/", 1)[-1]
    if "/" in tok and (quoted or "." in base):
        return not tok.endswith("/")
    m = re.search(r"\.([A-Za-z0-9]{1,8})$", base)
    if not m or not re.search(r"[A-Za-z]", m.group(1)):
        return False
    if m.group(1).lower() in TLDS and "/" not in tok:
        return False
    if re.fullmatch(r"\d+(?:\.\d+)+", tok):
        return False
    return True


def _paths_in(seg: str) -> list[str]:
    found: list[tuple[int, str]] = []
    for m in BACKTICK.finditer(seg):
        if _is_path(m.group(1), True):
            found.append((m.start(), m.group(1).strip().rstrip(".,;:")))
    masked = BACKTICK.sub(lambda m: " " * len(m.group(0)), seg)
    for m in BARE_PATH.finditer(masked):
        if _is_path(m.group(1), False):
            found.append((m.start(), m.group(1).rstrip(".,;:")))
    return [p for _, p in sorted(found)]


def split_clauses(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:[-*•>]+|\d+[.)])\s+", "", line.strip())
        line = line.replace("**", "").replace("__", "")
        if not line:
            continue
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z`\-*\d])", line):
            for cl in re.split(r";|\b(?:but|however|although|though)\b", sent, flags=re.I):
                if cl.strip():
                    out.append(cl.strip())
    return out


def _fn_names() -> list[tuple[re.Pattern, tuple[int, int]]]:
    art = r"(?:(?:a|an|the|new|python|helper|public|private|async|static)\s+)*"
    verb = r"(?:added|created|implemented|wrote|introduced|defined)"
    return [
        (re.compile(r"\b" + verb + r"\s+" + art + r"(function|method|class)\s+(?:named\s+|called\s+)?`?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?:\(\))?`?", re.I), (1, 2)),
        (re.compile(r"\b" + verb + r"\s+" + art + r"`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?:\(\))?`\s+(function|method|class)\b", re.I), (2, 1)),
        (re.compile(r"\b" + verb + r"\s+" + art + r"`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\(\)`", re.I), (0, 1)),
    ]


FN_PATTERNS = _fn_names()
PKG_CMD = re.compile(r"\b(?:pip3?\s+install|uv\s+pip\s+install|uv\s+add|poetry\s+add|npm\s+(?:install|i)|yarn\s+add|pnpm\s+add|cargo\s+add)\s+([^\n;&|]+)", re.I)
PKG_INSTALLED = re.compile(r"\binstalled\s+(?:(?:the|a|new|python|pip|npm)\s+)*(?:(?:package|dependency|library|module)s?\s+)?`?([A-Za-z0-9_@][\w.@/-]*)`?", re.I)
PKG_ADDED = re.compile(r"\badded\s+(?:(?:the|a|new|python|pip|npm)\s+)*(?:package|dependency|library)\s+`?([A-Za-z0-9_@][\w.@/-]*)`?", re.I)
PKG_MORE = re.compile(r"(?:\s*,\s*|\s+and\s+)`([A-Za-z0-9_@][\w.@/-]*)`")
PKG_STOP = {"and", "as", "in", "on", "to", "from", "it", "them", "all", "everything", "successfully", "dependencies", "deps", "requirements",
            "packages", "libraries", "modules", "locally", "globally", "via", "using", "with", "for", "a", "an", "the", "that", "this", "pip", "npm", "uv", "of"}
TESTS_RE = re.compile(r"\b(?:all\s+)?(?:\d+\s+)?(?:(?:unit|the|existing|new|full|whole|project)\s+)*(?:tests?|test\s+suite|pytest|unittest|jest|cargo\s+test|go\s+test)\s+"
                      r"(?:are\s+|is\s+|did\s+|now\s+|all\s+|were\s+|also\s+|still\s+|suite\s+)*(?:pass(?:ed|es|ing)?|green|succeed(?:ed|s)?|ran\s+clean)\b", re.I)
PYTEST_RE = re.compile(r"\b\d+\s+passed\b", re.I)
SHA_RE = re.compile(r"\bcommit(?:ted)?\s+(?:as\s+|is\s+|id\s+|hash\s+|sha\s+)?`?([0-9a-f]{7,40})`?\b", re.I)
MSG_RE = re.compile(r"\bcommit(?:ted)?\b(?:[^\"\u201c'`\n]{0,40}?(?:message|as|with|:))?\s*[\"\u201c']([^\"\u201d']{3,200})[\"\u201d']", re.I)
GITMSG_RE = re.compile(r"git\s+commit\b[^\n]*?-m\s+[\"']([^\"']{1,200})[\"']", re.I)
COMMIT_RE = re.compile(r"\b(?:committed|(?:made|created|pushed)\s+(?:a|the|new|one|an)\s+(?:initial\s+|new\s+)?commits?|git\s+commit)\b", re.I)


def _claim(typ: str, target: str, text: str, **extra) -> dict:
    return {"type": typ, "target": target, "text": text, "source": "text", **extra}


def _pkg_name(tok: str) -> str:
    tok = tok.strip("`'\",.;:")
    tok = re.split(r"(?:==|>=|<=|~=|!=|>|<|=)", tok, maxsplit=1)[0]
    if tok.startswith("@") and tok.count("@") > 1:
        tok = "@" + tok[1:].split("@", 1)[0]
    elif not tok.startswith("@"):
        tok = tok.split("@", 1)[0]
    tok = re.sub(r"\[.*\]$", "", tok)
    return tok


def _extract_clause(cl: str) -> list[dict]:
    claims: list[dict] = []
    # tests
    if (TESTS_RE.search(cl) or PYTEST_RE.search(cl)) and not FAIL_WORDS.search(cl):
        claims.append(_claim("tests_pass", "", cl))
    # functions / classes
    for rx, (kind_i, name_i) in FN_PATTERNS:
        for m in rx.finditer(cl):
            kind = (m.group(kind_i) if kind_i else "function").lower()
            name = m.group(name_i).split(".")[-1]
            if name.lower() in {"function", "class", "method", "named", "called"} or not name:
                continue
            claims.append(_claim("class_added" if kind == "class" else "function_added", name, cl))
    # packages
    for m in PKG_CMD.finditer(cl):
        toks = m.group(1).split()
        skip = False
        for t in toks:
            if skip:
                skip = False
                continue
            if t in ("-r", "-e", "-c", "--requirement", "--editable", "--constraint", "-i", "--index-url", "--extra-index-url"):
                skip = True
                continue
            if t.startswith("-") or t in (".", "..") or "/" in t.strip("@") and not t.startswith("@") or t.lower() in PKG_STOP:
                continue
            n = _pkg_name(t)
            if n and re.match(r"[A-Za-z0-9@]", n):
                claims.append(_claim("package_installed", n, cl))
    for rx in (PKG_INSTALLED, PKG_ADDED):
        for m in rx.finditer(cl):
            n = _pkg_name(m.group(1))
            if n and n.lower() not in PKG_STOP:
                claims.append(_claim("package_installed", n, cl))
                pos = m.end()
                while True:
                    m2 = PKG_MORE.match(cl, pos)
                    if not m2:
                        break
                    claims.append(_claim("package_installed", _pkg_name(m2.group(1)), cl))
                    pos = m2.end()
    # commits
    n_commit = 0
    for m in SHA_RE.finditer(cl):
        sha = m.group(1)
        if re.search(r"\d", sha) or "`" in m.group(0):
            claims.append(_claim("commit_made", sha, cl, sha=sha)); n_commit += 1
    for rx in (MSG_RE, GITMSG_RE):
        for m in rx.finditer(cl):
            claims.append(_claim("commit_made", m.group(1), cl, message=m.group(1))); n_commit += 1
    if not n_commit and COMMIT_RE.search(cl):
        claims.append(_claim("commit_made", "", cl))
    # files
    verbs = list(VERB_RE.finditer(cl))
    for i, vm in enumerate(verbs):
        typ = next(k for k in FILE_VERBS if vm.group(k))
        end = verbs[i + 1].start() if i + 1 < len(verbs) else len(cl)
        seg = cl[vm.end():end]
        if SEG_SKIP.match(seg):
            continue
        cut = SEG_CUT.search(seg)
        if cut:
            seg = seg[:cut.start()]
        for p in _paths_in(seg):
            claims.append(_claim(typ, p, cl))
    return claims


def extract_claims(text: str) -> tuple[list[dict], list[dict]]:
    """Return (claims, skipped). skipped = clauses that looked like claims but were negated or planned."""
    claims: list[dict] = []
    skipped: list[dict] = []
    seen: set[tuple] = set()
    for cl in split_clauses(text):
        if NEG.search(cl):
            if _extract_clause(re.sub(NEG, " ", cl)):
                skipped.append({"text": cl, "reason": "negated, future or hypothetical wording"})
            continue
        for c in _extract_clause(cl):
            key = (c["type"], c["target"])
            if key not in seen:
                seen.add(key)
                claims.append(c)
    return claims, skipped


def normalize_claims(data) -> list[dict]:
    """Accept a list of claim objects (or strings = free text) or {"claims": [...]}. Raises ValueError on bad input."""
    if isinstance(data, dict):
        if "claims" not in data:
            raise ValueError('claims JSON object needs a "claims" list')
        data = data["claims"]
    if not isinstance(data, list):
        raise ValueError("claims JSON must be a list of claim objects")
    out = []
    for i, c in enumerate(data):
        if isinstance(c, str):
            got, _ = extract_claims(c)
            for g in got:
                g["source"] = "json"
            out.extend(got)
            continue
        if not isinstance(c, dict) or "type" not in c:
            raise ValueError(f"claim #{i + 1} needs a \"type\"")
        t = str(c["type"]).strip().lower().replace("-", "_").replace(" ", "_")
        t = ALIASES.get(t, t)
        if t not in TYPES:
            raise ValueError(f"claim #{i + 1}: unknown type {c['type']!r}; use one of {', '.join(TYPES)}")
        target = str(c.get("target", c.get("path", c.get("name", c.get("package", "")))) or "")
        if t not in ("tests_pass", "commit_made") and not target:
            raise ValueError(f"claim #{i + 1} ({t}) needs a \"target\"")
        item = {"type": t, "target": target, "text": str(c.get("text", "")), "source": "json"}
        for k in ("sha", "message"):
            if c.get(k):
                item[k] = str(c[k])
        out.append(item)
    return out
