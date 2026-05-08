"""§10x.69 — GLOBAL Anthropic kill switch.

User caught: my function-level kill switches only blocked 3 of 26
Anthropic callsites. Haiku calls (classify_file, _summarise_message,
_claude_semantic_match, verify_locale, etc.) kept burning credit
even with DISABLE_VISION_CALLS=1.

This module monkey-patches `anthropic.Anthropic` so that EVERY
.messages.create() call across the entire codebase checks the kill
switch. When DISABLE_VISION_CALLS=1, the call returns a fake response
object (matching the SDK's response shape) WITHOUT making any HTTP
request.

Import this module ONCE at app startup, before any code calls
anthropic.Anthropic(). Then every callsite is gated automatically.

The fake response object has:
  - .content = []  (so callers reading .content[0].text see empty)
  - .model, .usage with zeros so log_usage doesn't crash

This is application-level last-resort. The proper fix is a per-callsite
audit + cost tracking integrity. This guard means even unaudited code
can't bleed credit while the kill switch is on.
"""
from __future__ import annotations
import os
import logging

log = logging.getLogger(__name__)


class _KilledUsage:
    input_tokens = 0
    output_tokens = 0
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _KilledMessage:
    """Mock response that matches anthropic.Message shape minimally.
    Returned when the kill switch is on. Token counts are zero so
    log_usage doesn't bill anything.
    """
    def __init__(self):
        self.content = []
        self.model = 'killed-by-kill-switch'
        self.id = 'msg_killed'
        self.role = 'assistant'
        self.stop_reason = 'end_turn'
        self.usage = _KilledUsage()
        self.type = 'message'


def _is_killed() -> bool:
    return os.environ.get('DISABLE_VISION_CALLS', '').strip() == '1'


_PATCHED = False


def install_global_killswitch() -> None:
    """Monkey-patch anthropic.Anthropic.messages.create so EVERY call
    in the codebase respects DISABLE_VISION_CALLS=1.

    Idempotent — calling twice is safe (won't double-patch).
    """
    global _PATCHED
    if _PATCHED:
        return
    try:
        import anthropic
        from anthropic.resources.messages import Messages
        original_create = Messages.create

        def gated_create(self, *args, **kwargs):
            if _is_killed():
                # Surface in logs so we can see when the kill switch fires
                model = kwargs.get('model', '?')
                log.warning(
                    "§10x.69 GLOBAL kill switch BLOCKED a Claude call "
                    "(model=%s). Set DISABLE_VISION_CALLS= to unblock.",
                    model,
                )
                return _KilledMessage()
            return original_create(self, *args, **kwargs)

        Messages.create = gated_create
        _PATCHED = True
        log.info("§10x.69 — global Anthropic kill switch installed.")
    except Exception as e:
        log.error("Failed to install global kill switch: %s", e)
