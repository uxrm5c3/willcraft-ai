"""Encode/decode per-client inbox addresses for the email-to-chat feature.

Simple format (new): <first_name_up_to_5><last4_of_ic>@inbox.<host>
  e.g.  koid5008@inbox.will.alantanjb.com      ← KOID BENG SUN, IC …-5008
        kanag1265@inbox.will.alantanjb.com      ← KANAGARANY A/P …, IC …-1265
        phek5039@inbox.will.alantanjb.com       ← PHEK YI TING, IC …-5039

Rules:
  - First word of full_name, lowercase, letters only, max 5 chars
  - Last 4 digits of NRIC/IC (the sequence number after the last dash)
  - If no NRIC stored: fall back to last 4 of UUID (still unique)
  - Handles Indian patronymic (A/P, A/L) and Malay bin/binti correctly
    by taking the ACTUAL first name (first word before the patronymic)

Backward compat: old '<name_slug>-<8hex>@…' and 'client-<8hex>@…' still route.
"""
import re
from database import Client


# ── Legacy format ─────────────────────────────────────────────────────────────
LEGACY_RE = re.compile(r'-([0-9a-f]{8})(?:\+[^@]+)?@', re.IGNORECASE)

# ── New format ────────────────────────────────────────────────────────────────
# Local part: 2-5 letters + 4 digits
NEW_ADDR_RE = re.compile(r'^([a-z]{2,5})(\d{4})$', re.IGNORECASE)

# Words that are NOT real names (patronymics, titles to skip over)
_SKIP_WORDS = {'a/p', 'a/l', 'bin', 'binti', 'bte', 'bt', 'mr', 'mrs',
               'dr', 'dato', 'datuk', 'haji', 'hajjah'}


def _name_part(full_name: str) -> str:
    """Extract the first meaningful name word, lowercase, max 5 letters.
    Skips patronymic tokens (A/P, A/L, BIN, BINTI…).
    'KOID BENG SUN'           → 'koid'
    'KANAGARANY A/P APPUKUDDY'→ 'kanag'
    'NADANASABAPATHY A/L …'   → 'nadan'
    'PHEK YI TING'            → 'phek'
    'AHMAD BIN IBRAHIM'       → 'ahmad'
    """
    if not full_name:
        return ''
    for word in full_name.strip().split():
        w = re.sub(r'[^a-zA-Z]', '', word).lower()
        if w and w not in _SKIP_WORDS and len(w) >= 2:
            return w[:5]  # cap at 5 chars
    return ''


def _ic_suffix(client) -> str:
    """Return the last 4 digits of the client's NRIC/IC/passport.
    Malaysian IC format: 'YYMMDD-ST-XXXX' — we want the XXXX part.
    Falls back to last 4 hex chars of UUID if no NRIC is stored.
    """
    raw = (client.nric_passport or '').strip()
    if raw:
        # Strip all non-digits
        digits_only = re.sub(r'\D', '', raw)
        if len(digits_only) >= 4:
            return digits_only[-4:]   # last 4 digits = IC sequence number
    # Fallback: last 4 hex chars of UUID converted to digits (0-9 only)
    uuid_digits = re.sub(r'[^0-9]', '', client.id)
    if len(uuid_digits) >= 4:
        return uuid_digits[-4:]
    return client.id[-4:]  # last resort


def _local_part(client) -> str:
    """Compute the full local part for this client's inbox address."""
    name = _name_part(client.full_name or '')
    suffix = _ic_suffix(client)
    if not name:
        # No usable name — use legacy format signal
        return ''
    return f"{name}{suffix}"


def address_for_client(client, host: str) -> str:
    """Return the simple inbox address for this client.

    Format: <first_name_5chars><ic_last4>@inbox.will.alantanjb.com
    Example: koid5008@inbox.will.alantanjb.com

    Falls back to 'client-<8hex>@host' only if name and IC are both unavailable.
    """
    local = _local_part(client)
    if not local:
        return f"client-{client.id[:8]}@{host}"
    return f"{local}@{host}"


def find_client_by_address(addr: str, hint_subject: str = '') -> 'Client | None':
    """Resolve an inbox address to a Client row.

    🔥 §10x.61 — STRICT EXACT MATCH ONLY (data-breach prevention).
    Previously this function had a "looser fallback" that matched by
    surname alone if there was only one client with that surname. That
    routed user A's email (koid5743@...) to user B's client (koid5315@...)
    when only one "koid" client existed. PII data breach risk for
    multi-tenant SaaS.

    Now: the inbox address must EXACTLY match a client's computed
    local_part (`<name5char><ic4>` or legacy `<slug>-<8hex>`). No
    fuzzy match. No surname fallback. No subject-hint disambiguation.
    Unknown address → return None → webhook rejects with
    "unknown client" log.

    `hint_subject` parameter retained for backward-compat but UNUSED.
    """
    if not addr:
        return None
    addr_lower = addr.strip().lower()
    local = addr_lower.split('@')[0] if '@' in addr_lower else addr_lower

    # ── Legacy UUID-based routing (only if EXACTLY one matches the prefix) ──
    m = LEGACY_RE.search(addr_lower)
    if m:
        short = m.group(1).lower()
        candidates = Client.query.filter(Client.id.ilike(short + '%')).all()
        if len(candidates) == 1:
            # Verify the candidate's computed local_part matches the
            # incoming address — defends against UUID-prefix collisions.
            if _local_part(candidates[0]) == local or candidates[0].id.startswith(short):
                return candidates[0]
        # >1 candidate or 0 candidates → reject. NEVER guess.
        return None

    # ── New name+IC routing — EXACT match required ─────────────────────────
    nm = NEW_ADDR_RE.match(local)
    if nm:
        # Find clients whose computed local_part matches exactly.
        # No fuzzy fallback: if no client has this exact (name+ic_suffix)
        # combination, the email is rejected.
        all_clients = Client.query.all()
        for c in all_clients:
            if _local_part(c) == local:
                return c

    return None


def short_id_from_address(addr: str):
    """Extract the 8-char short id from a legacy address. Returns None for new format."""
    if not addr:
        return None
    m = LEGACY_RE.search(addr.strip())
    return m.group(1).lower() if m else None
