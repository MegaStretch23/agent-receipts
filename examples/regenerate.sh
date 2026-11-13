#!/usr/bin/env bash
# Re-creates every saved file in this folder from a fresh temp repo. Run from this folder: ./regenerate.sh
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
bash "$here/make_sample_repo.sh" "$tmp/repo"
cd "$tmp/repo"
export PYTHONPATH="$here/../src"
set +e
python3 -m agent_receipts verify "$here/claims.json" --repo . --test-cmd "python3 -c 'import calc; assert calc.add(1,2)==3'" \
    --json "$here/receipt.json" --md "$here/receipt.md" --no-timestamp > "$here/claims_run.txt"
echo "exit code: $?" >> "$here/claims_run.txt"
python3 -m agent_receipts verify --text "$here/summary.txt" --repo . --test-cmd "python3 -c 'import sys; sys.exit(1)'" \
    --json "$here/summary_receipt.json" --md "$here/summary_receipt.md" --no-timestamp > "$here/summary_run.txt"
echo "exit code: $?" >> "$here/summary_run.txt"
