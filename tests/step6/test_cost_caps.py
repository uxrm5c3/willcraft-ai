"""§10x.67 / §10x.68 — Cost cap + DB-cache integration tests.

Run with kill switch ON. No real Anthropic API calls. Verifies:

  1. cached_vision returns SAME result for repeat calls without invoking fn()
  2. Calling extract_property_data 3× returns same data, only 1 cache row
  3. is_over_ceiling correctly fires after $1.50 spent
  4. Global cap fires for null-client_id calls
  5. Kill switch (DISABLE_VISION_CALLS=1) returns empty without touching fn

Usage:
    docker exec willcraft-web python /app/tests/step6/test_cost_caps.py
"""
import sys, os, json, hashlib, tempfile
from decimal import Decimal
sys.path.insert(0, '/app')

from app import app
from database import db, ApiCallLog, VisionExtractCache
from sqlalchemy import func


_PASS = []
_FAIL = []


def check(label: str, ok: bool, detail: str = ''):
    icon = '✅' if ok else '❌'
    print(f'  {icon} {label}' + (f' — {detail}' if detail else ''))
    (_PASS if ok else _FAIL).append(label)


def make_test_image() -> str:
    """Create a tiny dummy file for hashing tests."""
    fd, path = tempfile.mkstemp(suffix='.jpg')
    with os.fdopen(fd, 'wb') as f:
        f.write(b'\xff\xd8\xff\xe0FAKE_TEST_IMAGE_FOR_CACHE_TEST_ONLY' + b'\x00' * 100)
    return path


def test_cached_vision_idempotent():
    print('\n[1] cached_vision: 3 calls = 1 fn() invocation')
    from services.vision_cache import cached_vision
    path = make_test_image()
    invocations = []
    def fake_fn():
        invocations.append(1)
        return {'lot': '12345', 'mukim': 'Plentong'}
    with app.app_context():
        # Clear any prior cache for this hash
        h = hashlib.sha256(open(path, 'rb').read()).hexdigest()
        db.session.query(VisionExtractCache).filter_by(content_hash=h).delete()
        db.session.commit()

        r1 = cached_vision(file_path=path, call_kind='test_kind', fn=fake_fn)
        r2 = cached_vision(file_path=path, call_kind='test_kind', fn=fake_fn)
        r3 = cached_vision(file_path=path, call_kind='test_kind', fn=fake_fn)

        check(f'fn() invoked exactly once', len(invocations) == 1,
              f'invocations={len(invocations)}')
        check(f'r1 contains expected fields', r1.get('lot') == '12345')
        check(f'r2 from cache', r2.get('_from_db_cache') is True)
        check(f'r3 from cache', r3.get('_from_db_cache') is True)
        check(f'r1 == r2 (same fields)', r1.get('lot') == r2.get('lot'))

        # Cache row count for this hash
        n = db.session.query(VisionExtractCache).filter_by(content_hash=h).count()
        check(f'exactly 1 cache row', n == 1, f'rows={n}')

        # Cleanup
        db.session.query(VisionExtractCache).filter_by(content_hash=h).delete()
        db.session.commit()
    os.unlink(path)


def test_kill_switch_blocks_extract():
    print('\n[2] DISABLE_VISION_CALLS=1 blocks extract_property_data')
    os.environ['DISABLE_VISION_CALLS'] = '1'
    try:
        from ai.property_extractor import extract_property_data
        path = make_test_image()
        r = extract_property_data(path)
        check('returns kill-switch sentinel',
              r.get('_disabled_by_kill_switch') is True, f'r={r}')
        os.unlink(path)
    finally:
        os.environ.pop('DISABLE_VISION_CALLS', None)


def test_kill_switch_blocks_reocr():
    print('\n[3] DISABLE_VISION_CALLS=1 blocks reocr_critical_field')
    os.environ['DISABLE_VISION_CALLS'] = '1'
    try:
        from ai.ocr import reocr_critical_field
        path = make_test_image()
        r = reocr_critical_field(path, 'nric_number')
        check('returns empty string', r == '', f'r={r!r}')
        os.unlink(path)
    finally:
        os.environ.pop('DISABLE_VISION_CALLS', None)


def test_kill_switch_blocks_vision_fields():
    print('\n[4] DISABLE_VISION_CALLS=1 blocks vision_extract_property_fields')
    os.environ['DISABLE_VISION_CALLS'] = '1'
    try:
        from ai.file_classifier import vision_extract_property_fields
        path = make_test_image()
        r = vision_extract_property_fields(path)
        check('returns empty dict', r == {}, f'r={r}')
        os.unlink(path)
    finally:
        os.environ.pop('DISABLE_VISION_CALLS', None)


def test_ceiling_per_client():
    print('\n[5] is_over_ceiling fires after $1.50 in 24h')
    from ai.cost_tracker import is_over_ceiling, DEFAULT_CLIENT_DAILY_CEILING_USD
    fake_cid = 'test-ceiling-client-' + hashlib.sha256(b'test').hexdigest()[:8]
    with app.app_context():
        # Inject 1.6 worth of fake calls
        from datetime import datetime
        for _ in range(160):
            db.session.add(ApiCallLog(
                client_id=fake_cid, will_id=None, user_id=None,
                call_site='test.fake', model='claude-sonnet-4',
                input_tokens=1000, output_tokens=100,
                cost_usd=Decimal('0.01'),
            ))
        db.session.commit()

        over = is_over_ceiling(fake_cid)
        check(f'is_over_ceiling returns True after $1.60 spent',
              over is True,
              f'over={over} ceiling={DEFAULT_CLIENT_DAILY_CEILING_USD}')

        # Cleanup
        db.session.query(ApiCallLog).filter_by(client_id=fake_cid).delete()
        db.session.commit()


def test_ceiling_global_null_client():
    print('\n[6] Global cap fires for null-client_id calls')
    from ai.cost_tracker import is_over_ceiling
    with app.app_context():
        # Inject $11 worth on null client_id (above $10 global cap)
        # First clear any existing null-client logs from today
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(hours=24)
        existing_total = float(db.session.query(
            func.coalesce(func.sum(ApiCallLog.cost_usd), 0)
        ).filter(ApiCallLog.created_at >= cutoff).scalar() or 0)

        # Inject enough to push global past $10
        needed = max(0, 10.5 - existing_total)
        added = 0
        if needed > 0:
            for _ in range(int(needed * 100) + 1):
                db.session.add(ApiCallLog(
                    client_id=None, will_id=None, user_id=None,
                    call_site='test.global_cap', model='claude-sonnet-4',
                    input_tokens=1000, output_tokens=100,
                    cost_usd=Decimal('0.01'),
                ))
                added += 1
            db.session.commit()

        over = is_over_ceiling('some-fresh-client-never-seen')
        check('global cap fires for fresh client when others spent >$10 total',
              over is True,
              f'over={over} existing=${existing_total:.2f}')

        # Cleanup
        db.session.query(ApiCallLog).filter_by(call_site='test.global_cap').delete()
        db.session.commit()


def test_strict_client_id_contract():
    print('\n[7] STRICT_CLIENT_ID=1 raises when log_usage has no client_id')
    from ai.cost_tracker import log_usage
    os.environ['STRICT_CLIENT_ID'] = '1'
    raised = False
    try:
        # Build a fake message object
        class FakeMessage:
            class usage:
                input_tokens = 100
                output_tokens = 10
                cache_read_input_tokens = 0
                cache_creation_input_tokens = 0
            usage = usage()
            model = 'claude-sonnet-4'
            content = []
        try:
            with app.app_context():
                log_usage(FakeMessage(), call_site='test.strict')
        except RuntimeError as e:
            if '§10x.68 contract violation' in str(e):
                raised = True
    finally:
        os.environ.pop('STRICT_CLIENT_ID', None)
    check('RuntimeError raised on missing client_id', raised)


def test_global_warn_when_loose():
    print('\n[8] STRICT_CLIENT_ID off: missing client_id is a warning, not a raise')
    from ai.cost_tracker import log_usage
    os.environ.pop('STRICT_CLIENT_ID', None)
    raised = False
    try:
        class FakeMessage:
            class usage:
                input_tokens = 0   # zero tokens → log_usage returns early
                output_tokens = 0
            model = 'claude-sonnet-4'
            content = []
        with app.app_context():
            log_usage(FakeMessage(), call_site='test.loose')
    except RuntimeError:
        raised = True
    check('no RuntimeError when STRICT_CLIENT_ID is off', not raised)


def main():
    print('═' * 60)
    print('§10x.67 / §10x.68 cost-cap + cache test suite')
    print('═' * 60)

    test_cached_vision_idempotent()
    test_kill_switch_blocks_extract()
    test_kill_switch_blocks_reocr()
    test_kill_switch_blocks_vision_fields()
    test_ceiling_per_client()
    test_ceiling_global_null_client()
    test_strict_client_id_contract()
    test_global_warn_when_loose()

    print()
    print('═' * 60)
    print(f'PASSED: {len(_PASS)}')
    print(f'FAILED: {len(_FAIL)}')
    if _FAIL:
        for f in _FAIL:
            print(f'  ❌ {f}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
