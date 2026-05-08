"""§10x.67 — DB-backed cache for expensive Sonnet vision calls.

Single source of truth: any Claude vision call that takes a file_path
and returns structured JSON should wrap itself in this cache.

Pattern:
    from services.vision_cache import cached_vision

    def extract_property_data(file_path):
        return cached_vision(
            file_path=file_path,
            call_kind='extract_property',
            fn=lambda: _do_actual_extraction(file_path),
        )

The helper:
  • Computes sha256(file content)
  • Queries VisionExtractCache by (content_hash, call_kind)
  • On hit: returns the cached JSON dict (no API call, $0 cost)
  • On miss: invokes `fn()`, persists the result, returns it
  • Caches BOTH success AND empty/failed results (with `_failed=True`)
    to prevent retry storms

Returns dict (or empty dict if fn returned None/exception).
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
from typing import Callable, Any, Dict

log = logging.getLogger(__name__)


def _file_content_hash(file_path: str) -> str:
    """Return sha256 of the file content, or empty string on error."""
    try:
        h = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ''


def cached_vision(
    *,
    file_path: str,
    call_kind: str,
    fn: Callable[[], Any],
    accept_empty: bool = True,
) -> Dict[str, Any]:
    """DB-cached wrapper around a vision API call.

    Args:
      file_path: image / PDF path. Hashed for the cache key.
      call_kind: short string identifying which vision endpoint
        (e.g. 'extract_property', 'reocr_nric', 'classify_kind').
        Different endpoints cache separately because they ask different
        questions of the same image.
      fn: callable that performs the actual API call when cache misses.
        Must return a dict (or None / raise on failure).
      accept_empty: if True (default), empty results are cached too —
        an unreadable image won't trigger retry storms. Set False
        only for endpoints where retrying might succeed (rare).

    Returns: dict (the cached or freshly-computed result).
    """
    if not file_path or not os.path.isfile(file_path):
        return {}

    h = _file_content_hash(file_path)
    if not h:
        # Hash failed — fall back to direct call without caching
        try:
            result = fn() or {}
            if not isinstance(result, dict):
                return {}
            return result
        except Exception:
            return {}

    # ── Check cache ────────────────────────────────────────────────
    try:
        from database import db, VisionExtractCache
        cached = (db.session.query(VisionExtractCache)
                  .filter_by(content_hash=h, call_kind=call_kind)
                  .first())
        if cached:
            try:
                data = json.loads(cached.extracted_json or '{}')
                if isinstance(data, dict):
                    data['_from_db_cache'] = True
                    return data
            except Exception:
                pass   # corrupt row — fall through to recompute
    except Exception as e:
        log.warning("vision_cache lookup failed: %s", e)

    # ── Cache miss — invoke fn ─────────────────────────────────────
    try:
        result = fn() or {}
        if not isinstance(result, dict):
            result = {}
    except Exception as e:
        log.warning("vision_cache fn() failed (call_kind=%s): %s", call_kind, e)
        result = {'_failed': True, '_reason': str(e)[:200]}

    if not result and not accept_empty:
        # Don't cache empty when caller explicitly opted out
        return {}

    # ── Persist ────────────────────────────────────────────────────
    try:
        from database import db, VisionExtractCache
        # Use INSERT ... ON CONFLICT DO NOTHING semantics — if another
        # worker beat us to the insert, drop ours silently
        existing = (db.session.query(VisionExtractCache)
                    .filter_by(content_hash=h, call_kind=call_kind)
                    .first())
        if not existing:
            row = VisionExtractCache(
                content_hash=h, call_kind=call_kind,
                extracted_json=json.dumps(result)[:200000],
            )
            db.session.add(row)
            db.session.commit()
    except Exception as e:
        log.warning("vision_cache write failed: %s", e)
        try:
            from database import db as _db
            _db.session.rollback()
        except Exception:
            pass

    return result


def cache_size() -> int:
    """Return number of cached rows (for monitoring)."""
    try:
        from database import db, VisionExtractCache
        return db.session.query(VisionExtractCache).count() or 0
    except Exception:
        return 0


def clear_cache_for(content_hash: str = '', call_kind: str = '') -> int:
    """Delete cached rows. Useful for forcing re-extraction (e.g. after
    a vision-prompt change). Returns count deleted."""
    try:
        from database import db, VisionExtractCache
        q = db.session.query(VisionExtractCache)
        if content_hash:
            q = q.filter_by(content_hash=content_hash)
        if call_kind:
            q = q.filter_by(call_kind=call_kind)
        n = q.count()
        q.delete()
        db.session.commit()
        return n
    except Exception:
        try:
            from database import db as _db
            _db.session.rollback()
        except Exception:
            pass
        return 0
