"""agent-receipts verify [claims.json] [--text FILE|-] [--summary STR] --repo . [--test-cmd CMD] [--base REF]

Exit codes: 0 no claim contradicted, 1 at least one claim contradicted (or unverifiable with --strict), 2 bad input."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .checks import Repo, verify_claims
from .extract import extract_claims, normalize_claims
from .receipt import build_receipt, render_markdown


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="agent-receipts", description="Check what an AI agent claimed against the repo.")
    ap.add_argument("--version", action="version", version=f"agent-receipts {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    v = sub.add_parser("verify", help="verify claims (JSON file and/or agent summary text) against a repo")
    v.add_argument("claims", nargs="?", help="claims.json: a list of {type, target} objects (or {\"claims\": [...]})")
    v.add_argument("--text", metavar="FILE", help="plain-text agent summary to extract claims from ('-' reads stdin)")
    v.add_argument("--summary", metavar="STRING", help="agent summary passed inline")
    v.add_argument("--repo", default=".", help="repo root (default .)")
    v.add_argument("--base", help="git ref that starts the window: files changed in REF..HEAD plus the working tree (default: last commit plus working tree)")
    v.add_argument("--test-cmd", help="command re-run to check 'tests pass' claims (exit code 0 = pass)")
    v.add_argument("--test-timeout", type=int, default=600, help="seconds before the test command counts as unverifiable (default 600)")
    v.add_argument("--json", dest="out_json", metavar="FILE", help="write the JSON receipt here")
    v.add_argument("--md", dest="out_md", metavar="FILE", help="write the markdown receipt here")
    v.add_argument("--format", choices=("md", "json", "none"), default="md", help="what to print on stdout (default md)")
    v.add_argument("--strict", action="store_true", help="also exit 1 when a claim is UNVERIFIABLE")
    v.add_argument("--no-timestamp", action="store_true", help="omit created_at (reproducible output)")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _parser()
    a = ap.parse_args(argv)
    if a.cmd != "verify":
        ap.print_help()
        return 2
    if not (a.claims or a.text or a.summary):
        print("agent-receipts: give a claims.json, --text FILE or --summary STRING", file=sys.stderr)
        return 2
    repo_path = Path(a.repo)
    if not repo_path.is_dir():
        print(f"agent-receipts: repo path not found: {a.repo}", file=sys.stderr)
        return 2
    claims: list[dict] = []
    skipped: list[dict] = []
    try:
        if a.claims:
            claims += normalize_claims(json.loads(Path(a.claims).read_text(encoding="utf-8")))
        texts = []
        if a.text:
            texts.append(sys.stdin.read() if a.text == "-" else Path(a.text).read_text(encoding="utf-8"))
        if a.summary:
            texts.append(a.summary)
        for t in texts:
            got, sk = extract_claims(t)
            claims += got
            skipped += sk
    except (OSError, ValueError) as e:   # includes json.JSONDecodeError
        print(f"agent-receipts: {e}", file=sys.stderr)
        return 2
    seen, uniq = set(), []
    for c in claims:
        k = (c["type"], c["target"], c.get("sha"), c.get("message"))
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    repo = Repo(repo_path, a.base)
    if a.base and repo.is_git and repo.git("rev-parse", "--verify", "-q", f"{a.base}^{{commit}}").returncode != 0:
        print(f"agent-receipts: --base {a.base!r} is not a commit in this repo", file=sys.stderr)
        return 2
    results = verify_claims(uniq, repo, a.test_cmd, a.test_timeout)
    head = repo.git("rev-parse", "HEAD").stdout.strip() if repo.has_head else None
    rec = build_receipt(results, a.repo, head, a.base, a.test_cmd, skipped, timestamp=not a.no_timestamp)
    md = render_markdown(rec)
    if a.out_json:
        Path(a.out_json).write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    if a.out_md:
        Path(a.out_md).write_text(md, encoding="utf-8")
    if a.format == "md":
        sys.stdout.write(md)
    elif a.format == "json":
        sys.stdout.write(json.dumps(rec, indent=2) + "\n")
    bad = rec["summary"]["contradicted"] + (rec["summary"]["unverifiable"] if a.strict else 0)
    return 1 if bad else 0
