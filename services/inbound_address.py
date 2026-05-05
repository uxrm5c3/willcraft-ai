"""Encode/decode per-client inbox addresses for the email-to-chat feature.

Simple format (new): <initials>@inbox.<host>
  e.g.  kbs@inbox.will.alantanjb.com     ← KOID BENG SUN (K.B.S.)
        tmw@inbox.will.alantanjb.com     ← TAN MING WEI (T.M.W.)
        kbs2@inbox.will.alantanjb.com    ← second client with same initials

Initials = first letter of each word in full_name, lowercase.
Easy to type on mobile. Clients know their own initials.

Backward compatibility: old '<name_slug>-<8hex>@…' and 'client-<8hex>@…'
addresses still resolve via the LEGACY_RE fallback.
"""
import re
from database import Client


# ── Legacy format support ────────────────────────────────────────────────────
LEGACY_RE = re.compile(r'-([0-9a-f]{8})(?:\+[^@]+)?@', re.IGNORECASE)

# ── New initials format ──────────────────────────────────────────────────────
# Local part: 2-6 letters optionally followed by a digit.
INITIALS_RE = re.compile(r'^([a-z]{2,6}\d*)$', re.IGNORECASE)


def _initials_tag(full_name: str) -> str:
    """First letter of each word, lowercase.
    'KOID BENG SUN' → 'kbs'.  'TAN MING WEI' → 'tmw'.
    Returns '' if fewer than 2 letters extractable.
    """
    if not full_name:
        return ''
    words = full_name.strip().split()
    initials = ''.join(w[0].lower() for w in words if w and w[0].isalpha())
    return initials[:6] if len(initials) >= 2 else ''


def address_for_client(client, host: str) -> str:
    """Return the simplest unique inbox address for this client.

    Format: <initials>@host  (e.g. kbs@inbox.will.alantanjb.com)
    Collision suffix: kbs2, kbs3 … for duplicate initials.
    Falls back to legacy 'client-<8hex>@host' if name is unusable.
    """
    tag = _initials_tag(client.full_name or '')
    if not tag:
        return f"client-{client.id[:8]}@{host}"

    # Find all clients who share the same initials, ordered by creation date
    # so the assignment is stable (first created → no suffix, next → 2, …).
    all_clients = (Client.query.order_by(Client.created_at.asc()).all())
    matching = [c for c in all_clients if _initials_tag(c.full_name or '') == tag]

    if not matching:
        return f"{tag}@{host}"

    try:
        pos = [c.id for c in matching].index(client.id)
    except ValueError:
        pos = 0

    if pos == 0:
        return f"{tag}@{host}"
    return f"{tag}{pos + 1}@{host}"


def find_client_by_address(addr: str, hint_subject: str = '') -> 'Client | None':
    """Resolve an inbox address to a Client row.

    Lookup order:
      1. Legacy '<slug>-<8hex>@…' — UUID-based (old format)
      2. Simple '<initials>@…' or '<initials>2@…' — initials-based (new format)
    """
    if not addr:
        return None
    addr_lower = addr.strip().lower()
    local = addr_lower.split('@')[0] if '@' in addr_lower else addr_lower

    # ── 1. Legacy UUID-based routing ─────────────────────────────────────────
    m = LEGACY_RE.search(addr_lower)
    if m:
        short = m.group(1).lower()
        candidates = Client.query.filter(Client.id.ilike(short + '%')).all()
        if candidates:
            if len(candidates) == 1:
                return candidates[0]
            if hint_subject:
                sub = hint_subject.lower()
                for c in candidates:
                    if c.full_name and c.full_name.lower() in sub:
                        return c
            candidates.sort(key=lambda c: c.updated_at or c.created_at, reverse=True)
            return candidates[0]

    # ── 2. Initials-based routing ─────────────────────────────────────────────
    im = INITIALS_RE.match(local)
    if im:
        # 'kbs' → base='kbs', pos=0.  'kbs2' → base='kbs', pos=1
        base = re.sub(r'\d+$', '', local)        # strip trailing digit
        suffix = local[len(base):]
        pos = (int(suffix) - 1) if suffix.isdigit() else 0

        all_clients = Client.query.order_by(Client.created_at.asc()).all()
        matching = [c for c in all_clients if _initials_tag(c.full_name or '') == base]
        if matching and pos < len(matching):
            return matching[pos]

    return None


def short_id_from_address(addr: str):
    """Extract the 8-char short id from a legacy address. Returns None for new format."""
    if not addr:
        return None
    m = LEGACY_RE.search(addr.strip())
    return m.group(1).lower() if m else None
