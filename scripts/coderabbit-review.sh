#!/usr/bin/env bash
# Local CodeRabbit review of this branch's changes against the base, the last part of the
# no-mistakes lint command. Fails on findings. Fails open (prints a skip line, exits 0) when the
# CLI is missing, signed out, rate-limited, offline or otherwise does not finish a review within 15 minutes.
#   scripts/coderabbit-review.sh [base-ref]   default: origin/HEAD, else origin/main, else main
set -uo pipefail

skip() { echo "coderabbit: skipped ($1)"; exit 0; }

command -v coderabbit > /dev/null 2>&1 || skip "CLI not installed"
# Signed out, `review` would sit waiting for a browser login; `auth status` exits 0 either way.
coderabbit auth status 2>&1 | grep -qi "signed out" && skip "not signed in"

base=""
for ref in ${1:-} origin/HEAD origin/main main; do
  base="$(git merge-base HEAD "$ref" 2> /dev/null)" && break
done
[ -n "$base" ] || skip "no base branch found"
[ "$(git rev-parse HEAD)" != "$base" ] || skip "no changes against the base"

# --fresh: a rerun otherwise reuses the last checkpoint and reports 0 findings.
# 15-minute cap through perl's alarm: macOS has no `timeout` unless coreutils is installed.
out="$(perl -e 'alarm shift; exec @ARGV' 900 coderabbit review --agent --fresh --base-commit "$base" 2>&1)"

done_line="$(grep '"type":"complete"' <<< "$out" | tail -1)"
if [ -z "$done_line" ]; then
  reason="$(grep '"type":"error"' <<< "$out" | tail -1 | python3 -c 'import json,sys; print(json.load(sys.stdin).get("message",""))' 2> /dev/null)"
  skip "${reason:-review did not finish}"
fi

grep '"type":"finding"' <<< "$out" | python3 -c '
import json, sys
for line in sys.stdin:
    f = json.loads(line)
    print("\n[%s] %s" % (f.get("severity", "?"), f.get("fileName", "?")))
    print((f.get("codegenInstructions") or f.get("comment") or "").split("\n\n", 1)[-1])
'
read -r status count <<< "$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("status"), d.get("findings", 0))' "$done_line")"
if [ "$count" -gt 0 ]; then
  echo "coderabbit: $count finding(s)"
  exit 1
fi
[ "$status" = review_completed ] || skip "review status: $status"
echo "coderabbit: no findings"
