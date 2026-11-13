#!/usr/bin/env bash
# Builds the small temp git repo used for the saved sample run (deterministic commit ids). Usage: make_sample_repo.sh DIR
set -euo pipefail
d="$1"; mkdir -p "$d"; cd "$d"
export GIT_AUTHOR_NAME=sample GIT_AUTHOR_EMAIL=sample@example.com GIT_COMMITTER_NAME=sample GIT_COMMITTER_EMAIL=sample@example.com
export GIT_AUTHOR_DATE="2026-01-01T00:00:00Z" GIT_COMMITTER_DATE="2026-01-01T00:00:00Z"
git init -q -b main
printf 'def add(a, b):\n    return a + b\n' > calc.py
printf '[project]\nname = "calc"\nversion = "0.1.0"\ndependencies = ["requests>=2"]\n' > pyproject.toml
printf 'import calc\n\n\ndef test_add():\n    assert calc.add(1, 2) == 3\n' > test_calc.py
git add . && git commit -qm "initial calc"
# what the "agent" did on top: edited calc.py (added multiply), created notes.md, committed, left an untracked file
printf 'def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n' > calc.py
printf '# Notes\n' > notes.md
git add . && git commit -qm "add multiply and notes"
printf 'scratch\n' > scratch.txt
