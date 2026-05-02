"""Encode/decode per-client inbox addresses for the email-to-chat feature.

Pattern: <name_slug>-<short_id>@inbox.<host>
  e.g.   koid_beng_sun-0590d69b@inbox.will.alantanjb.com

The short_id (first 8 hex chars of Client.id) is the authoritative routing
key — names can change, but the id never does. The name slug is a visual
sanity check for the human pasting the address into a 'To:' field.

Backward compatibility: old 'client-<short>@…' addresses still resolve
because the regex below just looks for any '-<8hex>' chunk before the @,
regardless of what comes before the hyphen.
"""
import re
from database import Client


# Match the LAST '-<8hex>' chunk before '@' in the local part. This
# accepts both 'client-0590d69b@' and 'koid_beng_sun-0590d69b@'.
INBOX_RE = re.compile(r'-([0-9a-f]{8})(?:\+[^@]+)?@', re.IGNORECASE)
_PLACEHOLDER_NAMES = {'new_client', 'client', '', 'unknown'}


def _name_slug(name: str) -> str:
    """Lowercase, alphanumerics + underscores only. Collapses runs."""
    if not name:
        return ''
    s = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    return s


def address_for_client(client, host: str) -> str:
    """Return the inbox address for a client, given the inbox host."""
    short = client.id[:8]
    slug = _name_slug(client.full_name or '')
    if not slug or slug in _PLACEHOLDER_NAMES:
        # No real name yet — keep the legacy 'client-' prefix until they
        # add one (avoids weird-looking addresses like 'new_client-…').
        return f"client-{short}@{host}"
    # Cap slug length so addresses stay reasonable (gmail rejects > 64 chars
    # in the local part). 40 leaves room for the '-{8hex}' suffix.
    if len(slug) > 40:
        slug = slug[:40].rstrip('_')
    return f"{slug}-{short}@{host}"


def short_id_from_address(addr: str):
    """Extract the 8-char client short id from a 'To' address; returns None on miss."""
    if not addr:
        return None
    m = INBOX_RE.search(addr.strip())
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
