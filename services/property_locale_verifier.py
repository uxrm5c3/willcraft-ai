"""Cross-check an extracted property's mukim/daerah/negeri against a
quick web search so OCR errors that swap one daerah for another get
flagged before the lawyer files Borang 14A.

Cheap and best-effort: a single Haiku call with the web_search server
tool. Caches by a hash of the (address, mukim, daerah, negeri) tuple
so the same property never costs us twice. Returns a warning string
if the search contradicts the OCR'd locale, or '' on confirmation /
inconclusive / failure (we never block the writer on a network hiccup).

Telemetry: every call is logged via cost_tracker so the per-will SaaS
COGS dashboard sees the search expense too.
"""
import hashlib
import logging
import time

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_CHEAP

log = logging.getLogger(__name__)

# In-process cache. Key = sha1(address|mukim|daerah|negeri), value =
# (timestamp, warning_string). 7-day TTL — locale doesn't change.
_CACHE: dict = {}
_CACHE_TTL = 7 * 24 * 3600
_CACHE_MAX = 500


def _key(address: str, mukim: str, daerah: str, negeri: str) -> str:
    raw = f"{(address or '').strip().lower()}|{(mukim or '').strip().lower()}|{(daerah or '').strip().lower()}|{(negeri or '').strip().lower()}"
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()


def _cache_get(k: str):
    hit = _CACHE.get(k)
    if not hit:
        return None
    ts, body = hit
    if time.time() - ts > _CACHE_TTL:
        _CACHE.pop(k, None)
        return None
    return body


def _cache_set(k: str, body: str):
    if len(_CACHE) >= _CACHE_MAX:
        oldest = min(_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _CACHE.pop(oldest, None)
    _CACHE[k] = (time.time(), body)
    # 🔥 §10x.109 — also persist to DB so the verdict survives gunicorn
    # worker recycle / redeploy. body='' (locale OK) is just as valuable
    # to cache as a real warning.
    try:
        from database import db, VisionExtractCache
        row = VisionExtractCache(
            content_hash=f'locale:{k}',
            call_kind='property_locale_verifier_v1',
            extracted_json=body or '',
        )
        db.session.merge(row)
        db.session.commit()
    except Exception:
        try:
            from database import db as _db
            _db.session.rollback()
        except Exception:
            pass


def verify_locale(address: str = '', mukim: str = '', daerah: str = '',
                  negeri: str = '') -> str:
    """Returns a warning string (e.g. "⚠️ Web check: address looks like
    it's in **Daerah Petaling**, not **Daerah Klang**.") or '' if the
    locale checks out / can't be verified.

    Skips the call when the inputs are too thin to be useful — needs at
    least an address + ONE of mukim/daerah to compare against."""
    address = (address or '').strip()
    mukim = (mukim or '').strip()
    daerah = (daerah or '').strip()
    negeri = (negeri or '').strip()
    if not address or (not mukim and not daerah):
        return ''

    k = _key(address, mukim, daerah, negeri)
    hit = _cache_get(k)
    if hit is not None:
        return hit

    # 🔥 §10x.109 — DB-backed cache layer survives gunicorn worker
    # recycle / container restart / redeploy. Same pattern as §10x.74
    # (geo_resolver) and §10x.104 (web_property_clues). Locale doesn't
    # change, so cached verdicts are valid indefinitely.
    try:
        from database import db, VisionExtractCache
        row = db.session.query(VisionExtractCache).filter_by(
            content_hash=f'locale:{k}',
            call_kind='property_locale_verifier_v1',
        ).first()
        if row is not None:
            cached_body = row.extracted_json or ''
            # Persist to in-process cache so subsequent calls in this
            # process skip the DB hit too.
            _cache_set(k, cached_body)
            return cached_body
    except Exception:
        pass

    parts = [f"Address: {address}"]
    if mukim:  parts.append(f"Mukim claimed: {mukim}")
    if daerah: parts.append(f"Daerah claimed: {daerah}")
    if negeri: parts.append(f"Negeri claimed: {negeri}")
    summary = ' | '.join(parts)

    prompt = f"""Verify whether this Malaysian property locale is correct.

{summary}

Use web_search ONCE to check whether the given Mukim / Daerah / Negeri
matches the address. If the address is in a different mukim or daerah
(common OCR error), report it.

Respond in EXACTLY one of these two formats:

OK
(when the locale matches, or there's no contradicting evidence)

WARN: <one short sentence — what's wrong, what it should be, plus the URL you used>
(when the search clearly contradicts the claimed locale)

No preface, no other text. If unsure, respond OK."""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=20.0)
        msg = client.messages.create(
            model=CLAUDE_MODEL_CHEAP,
            max_tokens=200,
            # 🔥 §10x.99 — deterministic verdict. Same address+mukim
            # always returns the same OK/WARN result.
            temperature=0,
            tools=[{
                'type': 'web_search_20250305',
                'name': 'web_search',
                'max_uses': 1,
            }],
            messages=[{'role': 'user', 'content': prompt}],
        )
    except Exception as e:
        log.info("verify_locale skipped (web_search unavailable?): %s", e)
        _cache_set(k, '')
        return ''

    # Telemetry
    try:
        from ai.cost_tracker import log_usage
        log_usage(msg, call_site='services.property_locale_verifier.verify_locale')
    except Exception:
        pass

    # Concatenate text blocks
    body = ''
    for block in (msg.content or []):
        if getattr(block, 'type', None) == 'text':
            body += (getattr(block, 'text', '') or '')
    body = body.strip()
    if not body:
        _cache_set(k, '')
        return ''

    # Parse
    if body.upper().startswith('OK'):
        _cache_set(k, '')
        return ''
    if body.upper().startswith('WARN'):
        # Strip the "WARN:" prefix
        msg_text = body.split(':', 1)[-1].strip()
        warn = f"⚠️  **Web check:** {msg_text[:300]}"
        _cache_set(k, warn)
        return warn
    # Unknown shape — be conservative, don't show a noisy warning
    _cache_set(k, '')
    return ''
