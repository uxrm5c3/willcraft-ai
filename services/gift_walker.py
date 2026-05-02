"""Walk-through helper for Step 6: Specific Gifts.

Finds Documents (property titles, bank statements, vehicles) that haven't
yet been turned into a Gift entry in the Will's step5_data, and parses
free-form user replies like 'Joshua 50%, Esther 50%' into beneficiary +
share assignments.
"""
import json
import re
from typing import List, Dict, Any
from database import Document, Will


_GIFT_KINDS = ('property_title', 'bank_statement', 'vehicle')


def get_pending_gift_documents(client_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Returns {'property': [...], 'bank': [...], 'vehicle': [...]} with
    Documents not yet referenced in any Gift in the active draft Will's
    step5_data."""
    out = {'property': [], 'bank': [], 'vehicle': []}
    will = (Will.query.filter_by(client_id=client_id, status='draft')
            .filter(Will.deleted_at.is_(None))
            .order_by(Will.updated_at.desc()).first())
    if not will:
        return out

    try:
        gifts = json.loads(will.step5_data) if will.step5_data else []
    except (json.JSONDecodeError, TypeError):
        gifts = []
    if not isinstance(gifts, list):
        gifts = []

    referenced_doc_ids = set()
    for g in gifts:
        if isinstance(g, dict) and g.get('document_id'):
            referenced_doc_ids.add(g['document_id'])

    docs = (Document.query.filter(
        Document.client_id == client_id,
        Document.category.in_(_GIFT_KINDS),
    ).order_by(Document.created_at.asc()).all())

    seen_keys = set()  # dedupe properties by title_number, banks by account_number
    for d in docs:
        if d.id in referenced_doc_ids:
            continue
        try:
            ex = json.loads(d.extracted_data) if d.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            ex = {}
        if d.category == 'property_title':
            key = ('property', (ex.get('title_number') or '').strip().upper())
        elif d.category == 'bank_statement':
            key = ('bank', (ex.get('account_number') or '').strip())
        else:
            key = ('vehicle', (ex.get('reg_number') or '').strip().upper())
        if key[1] and key in seen_keys:
            continue
        seen_keys.add(key)
        item = {
            'document_id': d.id,
            'category': d.category,
            'extracted': ex,
            'original_filename': d.original_filename,
            'created_at': d.created_at.isoformat() if d.created_at else '',
        }
        if d.category == 'property_title':
            out['property'].append(item)
        elif d.category == 'bank_statement':
            out['bank'].append(item)
        else:
            out['vehicle'].append(item)
    return out


# Match phrases like "Joshua 50%", "Joshua 1/2", "Joshua equal", "Joshua and Esther equal share"
_SHARE_RE = re.compile(
    r'(\d+\s*%|\d+/\d+|equal(?:ly|\s+shares?)?)',
    re.IGNORECASE,
)


def parse_beneficiary_shares(text: str, known_names: List[str]) -> List[Dict[str, str]]:
    """Parse user text mentioning beneficiaries + their shares.
    Returns [{'name': 'JOSHUA…', 'share': '50%'}, ...].

    Strategy: find every known_name mentioned in the text (case-insensitive),
    then for each, look at adjacent text for a share token. If no share
    found and there's only one beneficiary, default to '100%'.  If multiple
    and no shares, default to 'equal'.
    """
    if not text or not known_names:
        return []
    t = text.lower()
    found = []
    for name in known_names:
        n = name.lower().strip()
        if not n:
            continue
        idx = t.find(n)
        if idx == -1:
            continue
        # Look for a share within 40 chars after the name (or before)
        window = t[idx: min(len(t), idx + len(n) + 40)] + ' ' + t[max(0, idx-25): idx]
        sh = _SHARE_RE.search(window)
        share = sh.group(1).strip() if sh else None
        found.append({'name': name, 'share': share, '_idx': idx})

    # Sort by position in text
    found.sort(key=lambda x: x['_idx'])
    for f in found:
        del f['_idx']

    if not found:
        return []
    # Defaults: single → 100%, multiple → equal
    missing = [f for f in found if not f['share']]
    if len(found) == 1 and missing:
        found[0]['share'] = '100%'
    elif missing:
        for f in missing:
            f['share'] = 'equal'
    return found
