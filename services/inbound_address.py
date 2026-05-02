"""Encode/decode per-client inbox addresses for the email-to-chat feature.

Pattern: client-<short_id>@inbox.<host>
where short_id is the first 8 hex chars of Client.id (matching folder_name).

We pick the short_id (not the full UUID) for two reasons:
  1. It's already what the user sees in the folder name, so it's
     copy-pasteable from the Client Folder UI without translation.
  2. Email "To:" addresses look less intimidating in a phone contact card.

Collisions on 8-hex chars are unlikely at our scale (~1 in 4 billion per
pair); if a real collision happens we resolve by also matching the
client's full_name from the email subject as a tiebreaker.
"""
import re
from database import Client


INBOX_RE = re.compile(r'^client-([0-9a-f]{8})(?:\+[^@]+)?@', re.IGNORECASE)


def address_for_client(client, host: str) -> str:
    """Return the inbox address for a client, given the inbox host."""
    short = client.id[:8]
    return f"client-{short}@{host}"


def short_id_from_address(addr: str):
    """Extract the 8-char client short id from a 'To' address; returns None on miss."""
    if not addr:
        return None
    m = INBOX_RE.match(addr.strip())
    if not m:
        return None
    return m.group(1).lower()


def find_client_by_address(addr: str, hint_subject: str = ''):
    """Resolve an inbox address to a Client row.

    If multiple clients share the short_id (extremely rare), the hint
    subject line is searched for one of their full names as a tiebreaker.
    Returns None if no match.
    """
    short = short_id_from_address(addr)
    if not short:
        return None
    candidates = Client.query.filter(Client.id.ilike(short + '%')).all()
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if hint_subject:
        sub = hint_subject.lower()
        for c in candidates:
            if c.full_name and c.full_name.lower() in sub:
                return c
    # Ambiguous — return the most recently updated one as a safe default
    candidates.sort(key=lambda c: c.updated_at or c.created_at, reverse=True)
    return candidates[0]
