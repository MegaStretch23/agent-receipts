# agent-receipts

An AI coding agent ends its run with a summary: "created `notes.md`, added `multiply()`, installed `requests`, all tests pass, committed." **agent-receipts checks those sentences against the repo** and writes a receipt: every claim is `VERIFIED`, `CONTRADICTED` or `UNVERIFIABLE`, with the evidence next to it. The exit code is non-zero when any claim is contradicted, so it can gate a CI step or a script that runs your agent.

- Python 3.11+, **standard library only**, one command: `agent-receipts`.
- **Deterministic: no model calls, no network.** Claims are pulled from the summary text with regexes; checks use `git`, the file system, Python's `ast` and your own test command. The same repo and the same summary always give the same receipt.
- Output: a JSON receipt and a markdown receipt.

## Quickstart

```bash
git clone https://github.com/MegaStretch23/agent-receipts.git
cd agent-receipts
pip install .

# 1. claims as JSON (a list of {"type", "target"} objects)
agent-receipts verify claims.json --repo . --test-cmd "pytest -q" --json receipt.json --md receipt.md

# 2. or the agent's own plain-text summary (a file, or '-' for stdin; --summary "..." passes it inline)
agent-receipts verify --text summary.txt --repo . --test-cmd "pytest -q"
```

(`PYTHONPATH=src python -m agent_receipts ...` works without installing. It is not on PyPI yet.)

### Real output

Both runs below are saved in `examples/` and re-created by `examples/regenerate.sh` on a throwaway git repo (`examples/make_sample_repo.sh`: a tiny `calc.py` project where the "agent" edited `calc.py`, added `notes.md` and committed). Commit ids are the same on every run.

**1. Claims JSON where everything is true** (`examples/claims.json`, `--test-cmd "python3 -c 'import calc; assert calc.add(1,2)==3'"`):

```text
# Agent receipt

- Repo: `.` at `658e5f7f5`
- Verdict: **OK** - 5 verified, 0 contradicted, 0 unverifiable (of 5)
- Test command: `python3 -c 'import calc; assert calc.add(1,2)==3'`

| # | Claim | Status | Evidence |
|---|-------|--------|----------|
| 1 | modified file `calc.py` | VERIFIED | git shows calc.py modified (last commit plus working tree) |
| 2 | created file `notes.md` | VERIFIED | notes.md exists and git shows it added (last commit plus working tree) |
| 3 | added function `multiply` | VERIFIED | function multiply defined at calc.py:5 (ast); file changed in window (last commit plus working tree) |
| 4 | tests pass | VERIFIED | re-ran `python3 -c 'import calc; assert calc.add(1,2)==3'`: exit 0 |
| 5 | commit made | VERIFIED | commit 658e5f7 add multiply and notes matches the message |
exit code: 0
```

**2. A plain-text summary with made-up claims** (`examples/summary.txt`, `--test-cmd "python3 -c 'import sys; sys.exit(1)'"`, a test command that fails on purpose):

```text
# Agent receipt

- Repo: `.` at `658e5f7f5`
- Verdict: **CONTRADICTED** - 4 verified, 4 contradicted, 0 unverifiable (of 8)
- Test command: `python3 -c 'import sys; sys.exit(1)'`

| # | Claim | Status | Evidence |
|---|-------|--------|----------|
| 1 | added function `divide` | CONTRADICTED | no function named divide found in any source file |
| 2 | modified file `calc.py` | VERIFIED | git shows calc.py modified (last commit plus working tree) |
| 3 | created file `notes.md` | VERIFIED | notes.md exists and git shows it added (last commit plus working tree) |
| 4 | created file `docs/usage.md` | CONTRADICTED | docs/usage.md does not exist |
| 5 | installed package `requests` | VERIFIED | requests is declared in pyproject.toml |
| 6 | installed package `numpy` | CONTRADICTED | numpy not declared in pyproject.toml |
| 7 | tests pass | CONTRADICTED | re-ran `python3 -c 'import sys; sys.exit(1)'`: exit 1 |
| 8 | commit made `add multiply and notes` | VERIFIED | commit 658e5f7 add multiply and notes matches the message |
exit code: 1
```

The JSON receipt for run 2 is `examples/summary_receipt.json`; the markdown receipt is `examples/summary_receipt.md`.

## What it checks

| Claim type | How it is checked | VERIFIED | CONTRADICTED | UNVERIFIABLE |
|---|---|---|---|---|
| `file_created` ("created / added / wrote X") | `git diff-tree` of the last commit (or `--base REF..HEAD`) plus `git status` | the file exists and git shows it added | missing, or git shows it was only modified | exists but untouched in the window (default window); not a git repo; path outside the repo |
| `file_modified` ("updated / edited / fixed X") | same | git shows it modified (or new) | missing; with `--base`: untouched in the window | untouched in the default window; git-ignored |
| `file_deleted` ("deleted / removed X") | same, plus `git log` for history | absent and git shows the deletion | still exists | absent but never in git, or the deletion is outside the default window |
| `tests_pass` ("all tests pass") | re-runs `--test-cmd` once, compares the exit code | exit 0 | non-zero exit | no `--test-cmd`, timeout, command could not start |
| `function_added` / `class_added` | Python: `ast` over every tracked or untracked-not-ignored `.py` file; other languages (JS/TS, Go, Rust, Java, Ruby, PHP, C/C++, C#, Swift, shell): a grep for the definition keyword | defined in a file changed in the window | no definition anywhere (comments do not count); with `--base`: only in files untouched in the window | defined only in files untouched in the default window |
| `package_installed` | `pyproject.toml` (project, optional, dependency groups, build requires, Poetry, uv dev), `requirements*.txt`, `package.json`, `Pipfile`, `uv.lock`, `poetry.lock`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`; names are normalised (`Flask_Cors` = `flask-cors`) | declared in a manifest or lockfile | manifests exist and do not list it | no manifest at all |
| `commit_made` | `git log`: by sha, by message (last 200 commits), or `--base` (any commit in `REF..HEAD`) | found | sha or message not found; no commits in `REF..HEAD` | a bare "committed" with no sha, message or `--base` |

**The window.** By default, "what changed" means the last commit plus the working tree. Pass `--base <ref>` to widen it to `<ref>..HEAD` plus the working tree. A file that exists but is untouched in the default window is `UNVERIFIABLE` (the agent may have changed it in an earlier commit); with an explicit `--base` the same case is `CONTRADICTED`, because the window is then exactly what you asked about.

## Claims JSON

```json
[
  {"type": "file_created", "target": "notes.md"},
  {"type": "file_modified", "target": "src/app.py"},
  {"type": "file_deleted", "target": "old.txt"},
  {"type": "function_added", "target": "multiply"},
  {"type": "class_added", "target": "Widget"},
  {"type": "package_installed", "target": "requests"},
  {"type": "tests_pass"},
  {"type": "commit_made", "sha": "658e5f7"},
  {"type": "commit_made", "message": "add multiply and notes"}
]
```

`{"claims": [...]}` is accepted too, and so are short aliases (`created`, `modified`, `deleted`, `tests`, `function`, `class`, `package`, `commit`). A string item is run through the text extractor.

## Text extraction

Extraction is a fixed set of regexes (`src/agent_receipts/extract.py`), applied clause by clause (split on sentences, `;`, "but", "however"):

- file verbs: created / added / wrote / generated (create), updated / edited / changed / fixed / refactored (modify), deleted / removed (delete), followed by one or more paths (backticked or bare, with an extension or a `/`);
- `added|created|implemented ... function|method|class NAME` and ``added `name()` ``;
- `installed X`, `added dependency X`, and `pip install`, `uv add`, `poetry add`, `npm install`, `yarn add`, `pnpm add`, `cargo add` commands;
- "all tests pass", "test suite is passing", "12 passed";
- "committed", "made a commit", `committed abc1234`, `committed with message "..."`, `git commit -m "..."`.

A clause with negated, future or hypothetical wording ("did not", "will", "should", "couldn't", "failed to") is **not** treated as a claim; it is listed under "Skipped" in the receipt when it looked like one. The extractor reads English only and misses claims it has no pattern for: **an empty receipt does not mean the agent was honest**, it means no pattern matched. For a complete check, give `claims.json`.

## Limits (read before you trust a green receipt)

- It checks the **existence and change** of things, not their quality. "Added function `f`" is VERIFIED when a definition named `f` is in a changed file, not when `f` does what the agent said.
- `tests_pass` re-runs *your* command and compares the exit code. A test command that always exits 0 verifies anything. It does not check the number of tests or which tests ran.
- A function name is matched by name only (any file, any scope). Two functions with one name can hide a missing one when both sit in changed files.
- Grep-based detection for non-Python languages is a heuristic and can miss unusual definition styles.
- `package_installed` reads the manifests and lockfiles, not the installed environment.
- The test command runs through your shell with your permissions; use a command you trust.
- Git history is read, not modified. Nothing is written except the receipt files you ask for.

## Exit codes

`0` no claim contradicted; `1` at least one claim contradicted (with `--strict`, also if any is unverifiable); `2` bad input (unreadable JSON, unknown claim type, missing repo path, unknown `--base`).

Options: `--base REF`, `--test-cmd CMD`, `--test-timeout SECONDS` (default 600), `--json FILE`, `--md FILE`, `--format md|json|none` (stdout, default md), `--strict`, `--no-timestamp` (omit `created_at` for reproducible output).

## Tests

```bash
pip install pytest && pytest -q
```

43 tests run against real temporary git repos: extraction, every check type, edge cases (renames, files outside the repo, non-git folders, syntax errors, timeouts), the receipt formats and the CLI exit codes.

## Hosted / more tools

agent-receipts is free and has no paid version. Other things I sell (prices read from the Apify and Gumroad APIs on 2026-11-13):

- Apify, pay per event: [AI API Key Leak Scanner](https://apify.com/johnveds15/ai-api-key-leak-scanner?utm_source=github&utm_medium=readme&utm_campaign=agent-receipts) ($0.01 per repository or URL scanned), [SEC Form 4 insider cluster scanner](https://apify.com/johnveds15/sec-form4-insider-clusters?utm_source=github&utm_medium=readme&utm_campaign=agent-receipts) ($0.0004 per Form 4 filing parsed, $0.02 per insider cluster detected).
- Gumroad source packs: [Doom-detector for headless Claude Code runs](https://vedovino.gumroad.com/l/mjxcmw?utm_source=github&utm_medium=readme&utm_campaign=agent-receipts) ($15), [pdfguard: find hidden prompt-injection text in PDFs](https://vedovino.gumroad.com/l/gvvcsh?utm_source=github&utm_medium=readme&utm_campaign=agent-receipts) ($19).

MIT licensed.
