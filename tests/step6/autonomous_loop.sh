#!/bin/bash
# 🔥 §10x.39 / META §10x.133 — Autonomous self-test loop.
#
# Iteration design (per user request 2026-05-10 "do the autonomous testing.
# if usage limit is reached, just accept the prompt to use extra credit and
# keep running until the all the bugs are resolved"):
#
#   For each iteration:
#     1. Reset KOID + re-inject AI Summary
#     2. Run walker (now stops at "All steps complete" per §10x.126)
#     3. Run verify_step6.py — must exit 0
#     4. Run bug_checklist.py — runs all 15 checks against current state
#     5. If ANY check fails:
#          - Print rule reference + user verbatim quote
#          - Print 'REGRESSION' marker if rule has 2+ prior bug-table rows
#          - Exit 1 (loop will iterate)
#     6. Generate will via draft_will_mock; cache as koid_will_iter_N.txt
#     7. Optional: diff vs Sample KOID BENG SUN .docx, report differences
#
# If exit 0 → all checks passed; loop terminates.
# If exit 1 → fix the failed check, redeploy, run again.
#
# Usage:
#   ./tests/step6/autonomous_loop.sh [CLIENT_ID] [MAX_ITER]
#
# Defaults: CLIENT_ID=KOID, MAX_ITER=20

set -e
CID="${1:-2a2b527e-d870-447b-b386-8d97b21bb849}"
MAX_ITER="${2:-20}"

SSH="ssh ubuntu@47.130.249.28"
EXEC="$SSH docker exec willcraft-web"

echo "================================================================"
echo "AUTONOMOUS LOOP — client $CID, max $MAX_ITER iterations"
echo "================================================================"

for i in $(seq 1 $MAX_ITER); do
    echo ""
    echo "──────────────── ITERATION $i ────────────────"
    echo "[1/4] Reset + re-inject AI Summary"
    $EXEC python /app/data/inject_ai_summary.py "$CID" >/dev/null 2>&1
    $EXEC python /app/data/reset_step6.py "$CID" >/dev/null 2>&1

    echo "[2/4] Walker (stops at Step 10 complete per §10x.126)"
    $EXEC python /app/data/walk_step6.py "$CID" 80 >/dev/null 2>&1 || true

    echo "[3/4] verify_step6.py"
    if ! $EXEC python /app/data/verify_step6.py "$CID" 2>&1 | tail -3 | grep -q PASS; then
        echo "❌ verifier FAILED"
        $EXEC python /app/data/verify_step6.py "$CID" 2>&1 | tail -15
        exit 1
    fi
    echo "   ✅ verifier PASS"

    echo "[4/4] bug_checklist.py (15 checks vs today's bug list)"
    if $EXEC python /app/tests/step6/bug_checklist.py "$CID" 2>&1 | tail -25 | tee /tmp/checklist_out.txt; then
        :
    fi
    if grep -q 'ALL CHECKS PASSED' /tmp/checklist_out.txt; then
        echo ""
        echo "================================================================"
        echo "✅ ITERATION $i — ALL 15 BUG CHECKS PASS"
        echo "================================================================"
        exit 0
    fi
    echo ""
    echo "⚠️  Iteration $i found failures. Inspect output above."
    echo "    Fix code, redeploy, re-run loop."
    exit 1
done

echo "Max iterations ($MAX_ITER) reached without all checks passing."
exit 2
