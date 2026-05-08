"""Per-call Anthropic usage + cost logger.

Drop a single line after each `client.messages.create(...)`:

    from ai.cost_tracker import log_usage
    msg = client.messages.create(model=..., max_tokens=..., messages=[...])
    log_usage(msg, call_site='ai.ocr.extract_nric',
              client_id=client_id, will_id=will_id)

The helper is fail-safe — if anything goes wrong (DB locked, missing
columns, etc.) it logs a warning and returns 0. It never raises into
the caller, because losing telemetry should NEVER break a real API call.

Pricing is per-million-token rates captured here. Update when Anthropic
publishes new tiers — historic rows keep their original cost.
"""
import contextvars
import logging
from contextlib import contextmanager
from decimal import Decimal
from typing import Optional

log = logging.getLogger(__name__)

# Thread-/coroutine-local tracking context. Set once per Flask request via
# `track_context(client_id=..., will_id=..., user_id=...)`. Every
# `log_usage(msg, call_site=...)` call inside that block automatically
# attaches the IDs — so AI helpers don't need to thread params through.
_TRACK_CTX: contextvars.ContextVar = contextvars.ContextVar('cost_track_ctx', default=None)


@contextmanager
def track_context(*, client_id: Optional[str] = None,
                  will_id: Optional[str] = None,
                  user_id: Optional[str] = None):
    """Use as `with track_context(client_id=..., will_id=...): ...`.

    Within the block, every log_usage(...) auto-attaches these IDs.
    Restored on exit, so nested contexts compose safely.
    """
    token = _TRACK_CTX.set({
        'client_id': client_id, 'will_id': will_id, 'user_id': user_id,
    })
    try:
        yield
    finally:
        _TRACK_CTX.reset(token)


def _ctx() -> dict:
    return _TRACK_CTX.get() or {}

# Anthropic per-million-token USD pricing snapshot.
# Order matters: match the LONGEST model-name prefix first.
_PRICING = [
    # (model name prefix, input_per_1m, output_per_1m, cache_read_per_1m, cache_write_per_1m)
    ('claude-opus-4',       15.0, 75.0,  1.50,  18.75),
    ('claude-sonnet-4',      3.0, 15.0,  0.30,   3.75),
    ('claude-haiku-4',       1.0,  5.0,  0.10,   1.25),
    ('claude-3-5-sonnet',    3.0, 15.0,  0.30,   3.75),
    ('claude-3-5-haiku',     0.80, 4.0,  0.08,   1.0),
    ('claude-3-opus',       15.0, 75.0,  1.50,  18.75),
]

# Cheap fallback if we ever see an unknown model — assume Sonnet pricing
# so we don't accidentally under-bill ourselves in the dashboard.
_FALLBACK = (3.0, 15.0, 0.30, 3.75)


def _rates_for(model: str):
    if not model:
        return _FALLBACK
    for prefix, *rates in _PRICING:
        if model.startswith(prefix):
            return tuple(rates)
    return _FALLBACK


def estimate_cost(model: str, input_tokens: int = 0, output_tokens: int = 0,
                  cache_read_tokens: int = 0,
                  cache_creation_tokens: int = 0,
                  web_searches: int = 0) -> Decimal:
    """Pure function — returns USD cost for a single call. No side effects.
    Useful for unit tests and 'dry-run' projections.

    🔥 §10x.71 — web_searches now billed too. Anthropic charges
    $10 per 1000 web searches separately from token cost. Today's
    discrepancy: \$8.65 web search cost was untracked entirely.
    """
    in_rate, out_rate, read_rate, write_rate = _rates_for(model)
    cents = (
        Decimal(input_tokens or 0) * Decimal(str(in_rate))
        + Decimal(output_tokens or 0) * Decimal(str(out_rate))
        + Decimal(cache_read_tokens or 0) * Decimal(str(read_rate))
        + Decimal(cache_creation_tokens or 0) * Decimal(str(write_rate))
    )
    token_cost = (cents / Decimal(1_000_000)).quantize(Decimal('0.000001'))
    # Web search: $10 per 1000 searches = $0.01 per search
    web_cost = Decimal(web_searches or 0) * Decimal('0.01')
    return token_cost + web_cost


def log_usage(message_response, *, call_site: str,
              client_id: Optional[str] = None,
              will_id: Optional[str] = None,
              user_id: Optional[str] = None,
              duration_ms: Optional[int] = None) -> Decimal:
    """Persist one ApiCallLog row from an Anthropic Message object.

    IDs default to the active `track_context(...)` if present — so AI
    helpers can simply call `log_usage(msg, call_site='...')` without
    knowing about request context. Returns the computed cost (Decimal),
    or Decimal('0') on any failure (never raises).
    """
    ctx = _ctx()
    if client_id is None: client_id = ctx.get('client_id')
    if will_id is None:   will_id   = ctx.get('will_id')
    if user_id is None:   user_id   = ctx.get('user_id')
    # 🔥 §10x.68 — strict client_id contract. When STRICT_CLIENT_ID=1
    # in env, log_usage RAISES if client_id is missing. This catches
    # leaks at development/test time. In production it's a loud WARN
    # (so prod doesn't crash on a logging issue).
    import os as _os_lu
    if client_id is None:
        msg = (f"§10x.68 contract violation: log_usage called with no "
               f"client_id (call_site={call_site}, will={will_id}). "
               f"Wrap in `with track_context(client_id=...):` or pass "
               f"client_id= explicitly.")
        if _os_lu.environ.get('STRICT_CLIENT_ID', '').strip() == '1':
            raise RuntimeError(msg)
        log.warning(msg)
    try:
        from database import db, ApiCallLog
        usage = getattr(message_response, 'usage', None)
        if usage is None:
            return Decimal('0')
        # Anthropic v1 SDK exposes these attributes — guard each one because
        # field names have shifted across SDK versions.
        in_tok = int(getattr(usage, 'input_tokens', 0) or 0)
        out_tok = int(getattr(usage, 'output_tokens', 0) or 0)
        cache_read = int(getattr(usage, 'cache_read_input_tokens', 0) or 0)
        cache_write = int(getattr(usage, 'cache_creation_input_tokens', 0) or 0)
        # 🔥 §10x.71 — web_search count from server_tool_use field.
        # Anthropic returns this when web_search tool is used.
        web_searches = 0
        try:
            stu = getattr(usage, 'server_tool_use', None)
            if stu:
                web_searches = int(getattr(stu, 'web_search_requests', 0) or 0)
        except Exception:
            pass
        model = getattr(message_response, 'model', '') or ''
        cost = estimate_cost(model, in_tok, out_tok, cache_read, cache_write,
                             web_searches=web_searches)
        row = ApiCallLog(
            client_id=client_id, will_id=will_id, user_id=user_id,
            call_site=call_site[:80], model=model[:80],
            input_tokens=in_tok, output_tokens=out_tok,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_write,
            cost_usd=cost,
            duration_ms=duration_ms,
        )
        db.session.add(row)
        db.session.commit()
        return cost
    except Exception as e:
        log.warning("cost_tracker.log_usage failed (call_site=%s): %s",
                    call_site, e)
        try:
            from database import db as _db
            _db.session.rollback()
        except Exception:
            pass
        return Decimal('0')


def total_for_will(will_id: str) -> Decimal:
    """Sum of cost_usd across every API call ever logged for this will."""
    if not will_id:
        return Decimal('0')
    try:
        from database import db, ApiCallLog
        from sqlalchemy import func
        total = (db.session.query(func.coalesce(func.sum(ApiCallLog.cost_usd), 0))
                 .filter(ApiCallLog.will_id == will_id).scalar())
        return Decimal(str(total or 0))
    except Exception:
        return Decimal('0')


def total_for_client(client_id: str) -> Decimal:
    """Sum of cost_usd across every API call for this client (all wills,
    chat, OCR, drafter — everything). Use this for SaaS COGS sizing."""
    if not client_id:
        return Decimal('0')
    try:
        from database import db, ApiCallLog
        from sqlalchemy import func
        total = (db.session.query(func.coalesce(func.sum(ApiCallLog.cost_usd), 0))
                 .filter(ApiCallLog.client_id == client_id).scalar())
        return Decimal(str(total or 0))
    except Exception:
        return Decimal('0')


# 🔥 §10x.59 — per-client cost ceiling. Defends against runaway loops
# (e.g. watchdog re-firing or accidental retry storms) that would burn
# the credit pool before any single client gets useful output.
#
# Default ceiling: $2.00/day per client.
# Production target: $1/will end-to-end. The $2/day ceiling gives 100%
# headroom for retries, mid-flow uploads, conflict re-walkthroughs etc.
#
# When the ceiling fires, callers should:
#   • return None / empty for the requested AI call
#   • surface a "manual review needed" status to the user
#   • NEVER raise into Sentry-style noise — this is a defensive feature
DEFAULT_CLIENT_DAILY_CEILING_USD = 1.50


def cost_today_for_client(client_id: str) -> Decimal:
    """Sum of cost_usd in the last 24 hours for this client."""
    if not client_id:
        return Decimal('0')
    try:
        from datetime import datetime, timedelta
        from database import db, ApiCallLog
        from sqlalchemy import func
        cutoff = datetime.utcnow() - timedelta(hours=24)
        total = (db.session.query(func.coalesce(func.sum(ApiCallLog.cost_usd), 0))
                 .filter(ApiCallLog.client_id == client_id,
                          ApiCallLog.created_at >= cutoff).scalar())
        return Decimal(str(total or 0))
    except Exception:
        return Decimal('0')


def is_over_ceiling(client_id: str,
                    ceiling_usd: float = DEFAULT_CLIENT_DAILY_CEILING_USD
                    ) -> bool:
    """True if this client has spent ≥ ceiling_usd in the last 24 hours.
    🔥 §10x.65 — also returns True if global last-24h spend exceeds
    5× the per-client ceiling, regardless of client_id. Defends against
    the `client_id=NULL` leak where calls bypassed the per-client cap
    (today: 724 calls without client_id burned \$5.47).
    """
    try:
        # Per-client check
        if client_id and float(cost_today_for_client(client_id)) >= float(ceiling_usd):
            return True
        # Global safety net for null-client_id calls
        from datetime import datetime, timedelta
        from database import db, ApiCallLog
        from sqlalchemy import func
        cutoff = datetime.utcnow() - timedelta(hours=24)
        global_total = (db.session.query(func.coalesce(func.sum(ApiCallLog.cost_usd), 0))
                         .filter(ApiCallLog.created_at >= cutoff).scalar())
        if float(global_total or 0) >= float(ceiling_usd) * 5:   # $10 global cap
            return True
        return False
    except Exception:
        return False


def breakdown_for_will(will_id: str) -> list:
    """Per-call-site breakdown for a will. Returns [{call_site, calls,
    input_tokens, output_tokens, cost_usd}]."""
    if not will_id:
        return []
    try:
        from database import db, ApiCallLog
        from sqlalchemy import func
        rows = (db.session.query(
                    ApiCallLog.call_site,
                    func.count(ApiCallLog.id).label('calls'),
                    func.coalesce(func.sum(ApiCallLog.input_tokens), 0).label('in_tok'),
                    func.coalesce(func.sum(ApiCallLog.output_tokens), 0).label('out_tok'),
                    func.coalesce(func.sum(ApiCallLog.cost_usd), 0).label('cost'),
                )
                .filter(ApiCallLog.will_id == will_id)
                .group_by(ApiCallLog.call_site)
                .order_by(func.sum(ApiCallLog.cost_usd).desc())
                .all())
        return [{
            'call_site': r.call_site,
            'calls': int(r.calls or 0),
            'input_tokens': int(r.in_tok or 0),
            'output_tokens': int(r.out_tok or 0),
            'cost_usd': float(r.cost or 0),
        } for r in rows]
    except Exception:
        return []
