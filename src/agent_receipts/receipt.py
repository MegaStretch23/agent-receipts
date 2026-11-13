"""Receipt: JSON + markdown for a set of verified claims."""
from __future__ import annotations

import datetime as dt

from .checks import CONTRADICTED, UNVERIFIABLE, VERIFIED

LABEL = {"file_created": "created file", "file_modified": "modified file", "file_deleted": "deleted file", "tests_pass": "tests pass",
         "function_added": "added function", "class_added": "added class", "package_installed": "installed package", "commit_made": "commit made"}


def build_receipt(results: list[dict], repo_arg: str, head: str | None, base: str | None, test_cmd: str | None,
                  skipped: list[dict] | None = None, timestamp: bool = True) -> dict:
    counts = {VERIFIED: 0, CONTRADICTED: 0, UNVERIFIABLE: 0}
    for r in results:
        counts[r["status"]] += 1
    rec = {"receipt_version": 1, "tool": "agent-receipts", "repo": repo_arg, "head": head, "base": base, "test_cmd": test_cmd,
           "summary": {"total": len(results), "verified": counts[VERIFIED], "contradicted": counts[CONTRADICTED], "unverifiable": counts[UNVERIFIABLE]},
           "verdict": "CONTRADICTED" if counts[CONTRADICTED] else ("NO CLAIMS" if not results else "OK"),
           "claims": [{"type": r["type"], "target": r["target"], "status": r["status"], "evidence": r["evidence"],
                       "source": r.get("source"), "text": r.get("text", "")} for r in results],
           "skipped": skipped or []}
    if timestamp:
        rec["created_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    return rec


def _cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def render_markdown(rec: dict) -> str:
    s = rec["summary"]
    lines = ["# Agent receipt", "",
             f"- Repo: `{_cell(rec['repo'])}`" + (f" at `{rec['head'][:9]}`" if rec.get("head") else ""),
             f"- Verdict: **{rec['verdict']}** - {s['verified']} verified, {s['contradicted']} contradicted, {s['unverifiable']} unverifiable (of {s['total']})"]
    if rec.get("base"):
        lines.append(f"- Window: `{_cell(rec['base'])}..HEAD` plus working tree")
    if rec.get("test_cmd"):
        lines.append(f"- Test command: `{_cell(rec['test_cmd'])}`")
    if rec.get("created_at"):
        lines.append(f"- Created: {rec['created_at']}")
    lines += ["", "| # | Claim | Status | Evidence |", "|---|-------|--------|----------|"]
    for i, c in enumerate(rec["claims"], 1):
        what = LABEL.get(c["type"], c["type"]) + (f" `{_cell(c['target'])}`" if c["target"] else "")
        lines.append(f"| {i} | {what} | {c['status']} | {_cell(c['evidence'])} |")
    if not rec["claims"]:
        lines.append("| - | (no claims found) | - | - |")
    if rec.get("skipped"):
        lines += ["", "Skipped (negated, future or hypothetical wording, not treated as claims):", ""]
        lines += [f"- {_cell(x['text'])}" for x in rec["skipped"]]
    return "\n".join(lines) + "\n"
