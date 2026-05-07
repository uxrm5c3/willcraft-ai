"""§10x.49 — Multi-fixture audit gate.

Runs the §10x.48 pipeline + verifier against every committed fixture.
Exits non-zero if ANY fixture fails. This is the gate that runs before
any deploy that touches services/asset_pipeline.py, services/gift_walker.py,
ai/chat_planner.py, or app.py savers.

NEVER bypass this. The pre-push hook calls it. The autonomous /loop
calls it. Manual "I tested it locally" deploys MUST run it first.

Adding a new fixture:
  1. Create tests/step6/fixtures/<name>.py with seed_fixture(client_id) function
  2. Add the client_id + fixture name to FIXTURES below
  3. The audit will pick it up automatically.

Usage:
  docker exec willcraft-web python /app/tests/step6/run_audit.py
  → exit 0 if all PASS, 1 if any FAIL
"""
import sys
import os
import subprocess

# Repo-relative paths — works whether invoked from /app or local repo
HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY_PY = os.path.join(HERE, 'verify_step6.py')

# Ensure the app root is on sys.path so `from services.asset_pipeline` works
# whether the audit is invoked from /app, from the repo root, or from
# tests/step6/. Walk up looking for a directory that contains 'services/'.
_d = HERE
for _ in range(5):
    if os.path.isdir(os.path.join(_d, 'services')) and os.path.isfile(os.path.join(_d, 'app.py')):
        sys.path.insert(0, _d)
        break
    _d = os.path.dirname(_d)
# Also add /app explicitly for container invocations
if os.path.isdir('/app') and '/app' not in sys.path:
    sys.path.insert(0, '/app')

# (client_id, friendly name, expected fixture_mode)
# Add new fixtures here. Each must have a seeder in tests/step6/fixtures/
# that prepares state via reset+seed before the audit runs.
FIXTURES = [
    ('0590d69b-f171-4764-8bf6-642b20b824f6', 'KOID text-only', 'text_only'),
    # ('d25b80b1-...',                       'KOID mixed',      'mixed'),
    # ('<image-only-client>',                'Image-only',      'mixed'),
]


def run_one(client_id: str, name: str, expected_mode: str) -> bool:
    print('═' * 72)
    print(f'AUDIT FIXTURE: {name}  ({client_id})')
    print(f'expected mode: {expected_mode}')
    print('═' * 72)

    # 1. Pipeline self-validation (Stage 0-4 contract assertions)
    try:
        from services.asset_pipeline import run_pipeline, ContractViolation
        from app import app
    except ImportError as e:
        print(f'❌ Pipeline import failed: {e}')
        return False
    with app.app_context():
        try:
            r = run_pipeline(client_id)
            print(f'Pipeline ran cleanly: '
                  f'{len(r["asset_items"])} AssetItems / '
                  f'{len(r["doc_groups"])} DocGroups / '
                  f'{len(r["bindings"])} Bindings / '
                  f'{len(r["residuals"])} Residuals / '
                  f'{len(r["gifts"])} Gifts')
        except ContractViolation as e:
            print(f'❌ §10x.49 ContractViolation: {e}')
            return False
        except Exception as e:
            print(f'❌ Pipeline crashed: {type(e).__name__}: {e}')
            return False

    # 2. Reset + walk + verify (the integration test)
    repo_data = os.path.join(os.path.dirname(HERE), '..', 'data')
    if os.path.isdir('/app/data'):
        repo_data = '/app/data'

    rc = subprocess.run(['python', os.path.join(repo_data, 'reset_step6.py'), client_id],
                        capture_output=True, text=True).returncode
    if rc != 0:
        print(f'❌ reset failed (rc={rc})'); return False

    rc = subprocess.run(['python', os.path.join(repo_data, 'walk_step6.py'), client_id, '40'],
                        capture_output=True, text=True).returncode
    if rc != 0:
        print(f'❌ walk failed (rc={rc})'); return False

    res = subprocess.run(['python', VERIFY_PY, client_id], capture_output=True, text=True)
    print(res.stdout[-1500:])
    if res.returncode != 0:
        print(f'❌ verifier FAIL (rc={res.returncode})')
        return False

    print('✓ fixture passed')
    return True


def main() -> int:
    failed = []
    for cid, name, mode in FIXTURES:
        if not run_one(cid, name, mode):
            failed.append(name)
    print()
    print('═' * 72)
    if failed:
        print(f'❌ AUDIT FAIL — {len(failed)} fixture(s) failed: {failed}')
        return 1
    print(f'✅ AUDIT PASS — all {len(FIXTURES)} fixtures pass §10x.48 + §10x.49')
    return 0


if __name__ == '__main__':
    sys.exit(main())
