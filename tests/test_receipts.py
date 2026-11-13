import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_receipts.checks import Repo, verify_claims, CONTRADICTED, UNVERIFIABLE, VERIFIED
from agent_receipts.cli import main
from agent_receipts.extract import extract_claims, normalize_claims
from agent_receipts.receipt import build_receipt, render_markdown

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env={**os.environ, **GIT_ENV})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def write(repo, rel, text):
    p = Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    write(r, "app.py", "def old():\n    return 1\n")
    write(r, "README.md", "hello\n")
    write(r, "pyproject.toml", '[project]\nname="x"\nversion="0"\ndependencies=["requests>=2", "Flask_Cors"]\n[project.optional-dependencies]\ndev=["pytest"]\n')
    git(r, "add", ".")
    git(r, "commit", "-qm", "initial commit")
    write(r, "notes.txt", "n\n")   # a second commit, so the default window (last commit) does not include the files above
    git(r, "add", ".")
    git(r, "commit", "-qm", "add notes")
    return r


def claims_of(text):
    return extract_claims(text)[0]


def kinds(text):
    return [(c["type"], c["target"]) for c in claims_of(text)]


def run(repo, claims, **kw):
    return verify_claims(normalize_claims(claims), Repo(repo, kw.pop("base", None)), **kw)


def status(res, i=0):
    return res[i]["status"]


# ------------------------------------------------------------ extraction
def test_extract_file_verbs():
    assert kinds("Created `src/new.py`. Updated README.md. Deleted old/legacy.txt.") == [
        ("file_created", "src/new.py"), ("file_modified", "README.md"), ("file_deleted", "old/legacy.txt")]


def test_extract_multiple_files_in_one_clause():
    assert kinds("Added tests/a.py, tests/b.py and `tests/c.py`") == [("file_created", "tests/a.py"), ("file_created", "tests/b.py"), ("file_created", "tests/c.py")]


def test_extract_ignores_non_paths():
    assert kinds("Updated the version to 3.11 e.g. for CI, see example.com") == []


def test_extract_function_and_class_variants():
    got = kinds("Added a function `parse_args`. Created class Foo. Implemented the `Bar` class. Wrote `helper()`.")
    assert ("function_added", "parse_args") in got and ("class_added", "Foo") in got
    assert ("class_added", "Bar") in got and ("function_added", "helper") in got


def test_extract_method_dotted_uses_last_part():
    assert ("function_added", "run") in kinds("Added method `Runner.run`")


def test_extract_file_not_confused_with_symbol():
    # "added a function in app.py" is a function claim, not a created-file claim
    assert ("file_created", "app.py") not in kinds("Added a function `go` to app.py")


def test_extract_tests_claim():
    assert kinds("All tests pass.") == [("tests_pass", "")]
    assert kinds("The test suite is now passing") == [("tests_pass", "")]
    assert kinds("12 passed in 0.4s") == [("tests_pass", "")]


def test_extract_tests_with_failure_word_is_not_a_claim():
    assert kinds("5 passed, 2 failed") == []


def test_extract_packages():
    assert ("package_installed", "requests") in kinds("Installed `requests`.")
    assert ("package_installed", "rich") in kinds("Ran pip install rich==13.0 to get colours")
    got = kinds("Installed `alpha`, `beta` and `gamma`")
    assert [t for k, t in got] == ["alpha", "beta", "gamma"]
    assert ("package_installed", "tqdm") in kinds("Added dependency tqdm")
    assert kinds("npm install -D vitest")[0] == ("package_installed", "vitest")
    assert kinds("pip install -r requirements.txt") == []


def test_extract_commit_variants():
    assert kinds("Committed the work.") == [("commit_made", "")]
    assert kinds("Committed as abc1234 on main") == [("commit_made", "abc1234")]
    assert kinds('Committed with message "fix parser"') == [("commit_made", "fix parser")]
    assert kinds("I did git commit -m 'add tests'") == [("commit_made", "add tests")]
    assert kinds("There are uncommitted changes") == []


def test_extract_skips_negated_and_future():
    claims, skipped = extract_claims("The tests did not pass. I could not run the linter. Will add `x.py` later. Created `y.py`.")
    assert [(c["type"], c["target"]) for c in claims] == [("file_created", "y.py")]
    assert [s["text"] for s in skipped] == ["The tests did not pass."]


def test_extract_splits_on_but():
    assert kinds("Created `a.py`, but the tests did not pass") == [("file_created", "a.py")]


def test_extract_dedupes_and_handles_bullets():
    got = kinds("- **Created** `a.py`\n- Created `a.py`\n1. Updated `b.py`")
    assert got == [("file_created", "a.py"), ("file_modified", "b.py")]


def test_normalize_aliases_and_errors():
    got = normalize_claims({"claims": [{"type": "created", "target": "a"}, {"type": "tests"}, {"type": "Function Added", "name": "f"}]})
    assert [c["type"] for c in got] == ["file_created", "tests_pass", "function_added"]
    with pytest.raises(ValueError):
        normalize_claims([{"type": "wizardry", "target": "a"}])
    with pytest.raises(ValueError):
        normalize_claims([{"type": "file_created"}])
    with pytest.raises(ValueError):
        normalize_claims("nope")


# ------------------------------------------------------------ file checks
def test_created_verified_for_untracked_file(repo):
    write(repo, "new.py", "x = 1\n")
    assert status(run(repo, [{"type": "file_created", "target": "new.py"}])) == VERIFIED


def test_created_contradicted_when_missing(repo):
    r = run(repo, [{"type": "file_created", "target": "ghost.py"}])
    assert status(r) == CONTRADICTED and "does not exist" in r[0]["evidence"]


def test_created_contradicted_when_only_modified(repo):
    write(repo, "app.py", "def old():\n    return 2\n")
    r = run(repo, [{"type": "file_created", "target": "app.py"}])
    assert status(r) == CONTRADICTED and "modified, not added" in r[0]["evidence"]


def test_modified_verified_and_unchanged_is_unverifiable_by_default(repo):
    write(repo, "README.md", "changed\n")
    r = run(repo, [{"type": "file_modified", "target": "README.md"}, {"type": "file_modified", "target": "pyproject.toml"}])
    assert status(r, 0) == VERIFIED
    assert status(r, 1) == UNVERIFIABLE   # untouched in the default window


def test_unchanged_file_default_unverifiable_explicit_base_contradicted(repo):
    write(repo, "later.txt", "l\n")
    git(repo, "add", "."); git(repo, "commit", "-qm", "later")
    c = [{"type": "file_modified", "target": "README.md"}]
    assert status(run(repo, c)) == UNVERIFIABLE
    assert status(run(repo, c, base="HEAD~1")) == CONTRADICTED


def test_deleted_verified_via_working_tree_and_commit(repo):
    (repo / "README.md").unlink()
    assert status(run(repo, [{"type": "file_deleted", "target": "README.md"}])) == VERIFIED
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "rm")
    assert status(run(repo, [{"type": "file_deleted", "target": "README.md"}])) == VERIFIED


def test_deleted_contradicted_when_present(repo):
    assert status(run(repo, [{"type": "file_deleted", "target": "app.py"}])) == CONTRADICTED


def test_deleted_never_existed_is_unverifiable(repo):
    assert status(run(repo, [{"type": "file_deleted", "target": "nothing.py"}])) == UNVERIFIABLE


def test_path_outside_repo_not_checked(repo):
    r = run(repo, [{"type": "file_created", "target": "../elsewhere.txt"}])
    assert status(r) == UNVERIFIABLE and "outside the repo" in r[0]["evidence"]


def test_not_a_git_repo_degrades_to_existence(tmp_path):
    write(tmp_path, "a.txt", "x")
    r = run(tmp_path, [{"type": "file_created", "target": "a.txt"}, {"type": "file_created", "target": "b.txt"}, {"type": "commit_made"}])
    assert [x["status"] for x in r] == [UNVERIFIABLE, CONTRADICTED, UNVERIFIABLE]


def test_renamed_file_counts_as_added_and_deleted(repo):
    git(repo, "mv", "README.md", "READ.md")
    r = run(repo, [{"type": "file_created", "target": "READ.md"}, {"type": "file_deleted", "target": "README.md"}])
    assert [x["status"] for x in r] == [VERIFIED, VERIFIED]


# ------------------------------------------------------------ symbols
def test_function_found_by_ast_in_changed_file(repo):
    write(repo, "app.py", "def old():\n    return 1\n\n\nasync def fresh():\n    pass\n")
    r = run(repo, [{"type": "function_added", "target": "fresh"}])
    assert status(r) == VERIFIED and "app.py:5 (ast)" in r[0]["evidence"]


def test_function_missing_is_contradicted_and_comment_does_not_count(repo):
    write(repo, "app.py", "# def sneaky(): pass\ndef old():\n    return 1\n")
    assert status(run(repo, [{"type": "function_added", "target": "sneaky"}])) == CONTRADICTED


def test_class_and_method_found(repo):
    write(repo, "m.py", "class Widget:\n    def spin(self):\n        pass\n")
    r = run(repo, [{"type": "class_added", "target": "Widget"}, {"type": "function_added", "target": "Widget.spin"}])
    assert [x["status"] for x in r] == [VERIFIED, VERIFIED]


def test_function_in_untouched_file_unverifiable_then_contradicted_with_base(repo):
    write(repo, "z.txt", "z")
    git(repo, "add", "."); git(repo, "commit", "-qm", "z")
    c = [{"type": "function_added", "target": "old"}]
    assert status(run(repo, c)) == UNVERIFIABLE
    assert status(run(repo, c, base="HEAD~1")) == CONTRADICTED


def test_function_grep_for_javascript_and_syntax_error_python(repo):
    write(repo, "util.js", "export function hello() { return 1 }\nconst arrow = (x) => x\n")
    write(repo, "broken.py", "def fine(:\n")
    r = run(repo, [{"type": "function_added", "target": "hello"}, {"type": "function_added", "target": "arrow"}, {"type": "function_added", "target": "fine"}])
    assert [x["status"] for x in r] == [VERIFIED, VERIFIED, VERIFIED]
    assert "(grep)" in r[0]["evidence"]


# ------------------------------------------------------------ packages
def test_package_in_pyproject_is_verified_with_name_normalisation(repo):
    r = run(repo, [{"type": "package_installed", "target": "Requests"}, {"type": "package_installed", "target": "flask-cors"},
                   {"type": "package_installed", "target": "pytest"}, {"type": "package_installed", "target": "numpy"}])
    assert [x["status"] for x in r] == [VERIFIED, VERIFIED, VERIFIED, CONTRADICTED]


def test_package_in_requirements_and_package_json_and_lock(repo):
    write(repo, "requirements.txt", "# c\nrich==13.0\n-r other.txt\nPyYAML>=6 ; python_version>'3'\n")
    write(repo, "web/package.json", json.dumps({"dependencies": {"left-pad": "1"}, "devDependencies": {"vitest": "2"}}))
    write(repo, "uv.lock", 'version = 1\n[[package]]\nname = "tomli-w"\nversion = "1.0"\n')
    r = run(repo, [{"type": "package_installed", "target": t} for t in ("rich", "pyyaml", "left-pad", "vitest", "tomli_w")])
    assert all(x["status"] == VERIFIED for x in r)


def test_package_unverifiable_without_any_manifest(tmp_path):
    git(tmp_path, "init", "-q")
    assert status(run(tmp_path, [{"type": "package_installed", "target": "x"}])) == UNVERIFIABLE


# ------------------------------------------------------------ tests / commits
def test_tests_pass_uses_exit_code(repo):
    ok = run(repo, [{"type": "tests_pass"}], test_cmd=f"{sys.executable} -c 'print(1)'")
    bad = run(repo, [{"type": "tests_pass"}], test_cmd=f"{sys.executable} -c 'import sys; print(\"2 failed\"); sys.exit(3)'")
    assert status(ok) == VERIFIED and status(bad) == CONTRADICTED
    assert "exit 3" in bad[0]["evidence"] and "2 failed" in bad[0]["evidence"]


def test_tests_pass_without_cmd_or_with_timeout_is_unverifiable(repo):
    assert status(run(repo, [{"type": "tests_pass"}])) == UNVERIFIABLE
    assert status(run(repo, [{"type": "tests_pass"}], test_cmd=f"{sys.executable} -c 'import time; time.sleep(5)'", test_timeout=1)) == UNVERIFIABLE


def test_test_command_runs_once_for_many_claims(repo):
    marker = repo / "count.txt"
    cmd = f"{sys.executable} -c \"open('count.txt','a').write('x')\""
    run(repo, [{"type": "tests_pass", "target": "a"}, {"type": "tests_pass", "target": "b"}], test_cmd=cmd)
    assert marker.read_text() == "x"


def test_commit_by_sha_message_and_base(repo):
    sha = git(repo, "rev-parse", "--short", "HEAD")
    first = git(repo, "rev-parse", "HEAD")
    write(repo, "n.txt", "n"); git(repo, "add", "."); git(repo, "commit", "-qm", "second thing")
    r = run(repo, [{"type": "commit_made", "sha": sha}, {"type": "commit_made", "sha": "deadbeef"},
                   {"type": "commit_made", "message": "second thing"}, {"type": "commit_made", "message": "never happened"},
                   {"type": "commit_made"}])
    assert [x["status"] for x in r] == [VERIFIED, CONTRADICTED, VERIFIED, CONTRADICTED, UNVERIFIABLE]
    assert status(run(repo, [{"type": "commit_made"}], base=first)) == VERIFIED
    assert status(run(repo, [{"type": "commit_made"}], base="HEAD")) == CONTRADICTED


# ------------------------------------------------------------ receipt + CLI
def test_receipt_counts_and_markdown_escape(repo):
    res = run(repo, [{"type": "file_created", "target": "ghost|x.py"}, {"type": "file_modified", "target": "app.py", "text": "t"}])
    rec = build_receipt(res, ".", "abcdef1234", None, None, timestamp=False)
    assert rec["summary"] == {"total": 2, "verified": 0, "contradicted": 1, "unverifiable": 1} and rec["verdict"] == "CONTRADICTED"
    assert "created_at" not in rec
    md = render_markdown(rec)
    assert "ghost\\|x.py" in md and "**CONTRADICTED**" in md


def test_cli_exit_codes_and_outputs(repo, tmp_path, capsys):
    write(repo, "n.py", "def made():\n    pass\n")
    claims = tmp_path / "c.json"
    claims.write_text(json.dumps([{"type": "file_created", "target": "n.py"}]))
    out_json, out_md = tmp_path / "r.json", tmp_path / "r.md"
    assert main(["verify", str(claims), "--repo", str(repo), "--json", str(out_json), "--md", str(out_md), "--format", "none", "--no-timestamp"]) == 0
    rec = json.loads(out_json.read_text())
    assert rec["verdict"] == "OK" and rec["claims"][0]["status"] == VERIFIED and "Agent receipt" in out_md.read_text()
    claims.write_text(json.dumps([{"type": "file_created", "target": "n.py"}, {"type": "file_created", "target": "nope.py"}]))
    assert main(["verify", str(claims), "--repo", str(repo), "--format", "none"]) == 1


def test_cli_text_and_strict(repo, tmp_path, capsys):
    t = tmp_path / "s.txt"
    t.write_text("All tests pass.")
    assert main(["verify", "--text", str(t), "--repo", str(repo), "--format", "none"]) == 0   # unverifiable, not contradicted
    assert main(["verify", "--text", str(t), "--repo", str(repo), "--format", "none", "--strict"]) == 1
    assert main(["verify", "--summary", "All tests pass.", "--repo", str(repo), "--test-cmd", "exit 1", "--format", "none"]) == 1


def test_cli_bad_input_exits_2(repo, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert main(["verify", str(bad), "--repo", str(repo)]) == 2
    assert main(["verify", "--repo", str(repo)]) == 2
    assert main(["verify", "--summary", "x", "--repo", str(tmp_path / "missing")]) == 2
    assert main(["verify", "--summary", "x", "--repo", str(repo), "--base", "nonexistent-ref"]) == 2


def test_cli_json_stdout_is_valid_json(repo, capsys):
    main(["verify", "--summary", "Created `ghost.py`", "--repo", str(repo), "--format", "json"])
    rec = json.loads(capsys.readouterr().out)
    assert rec["verdict"] == "CONTRADICTED" and rec["claims"][0]["target"] == "ghost.py"


def test_module_entrypoint_runs():
    r = subprocess.run([sys.executable, "-m", "agent_receipts", "--version"], capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
    assert r.returncode == 0 and "agent-receipts" in r.stdout
