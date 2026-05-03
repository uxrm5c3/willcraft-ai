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


# Only `property_title` proves OWNERSHIP and triggers a "who inherits this?"
# question. `property_spa` (contract, transfer pending) and `property_tax`
# (just a payment receipt) are SUPPORTING docs — we attach them to the
# matching title as evidence, but never as standalone gifts.
_GIFT_KINDS = ('property_title', 'bank_statement', 'vehicle')
# Anything tied to a property address but NOT a title — clusters under
# the matching title in the chat for context, never as its own gift.
_PROPERTY_SUPPORT_KINDS = ('property_spa', 'property_tax', 'property_transfer',
                           'utility_bill', 'bank_letter')


def _norm_addr(s: str) -> str:
    """Normalise an address-ish string for fuzzy group matching:
    uppercase, strip punctuation, collapse whitespace."""
    if not s:
        return ''
    out = re.sub(r'[^\w\s/]', ' ', s.upper())
    return ' '.join(out.split())


def _property_group_key(ex: Dict[str, Any]) -> str:
    """A loose identifier for grouping multiple uploaded images that all
    refer to the SAME property (front/back of geran, SPA, cukai tanah,
    electric bill, bank letter). Order of preference:

      1. title_number (most precise)
      2. lot_number + mukim
      3. property_address / description
      4. property_hint from the vision classifier (covers utility bills
         and bank letters that don't have title fields but DO mention
         the address)

    Empty string means the doc couldn't be grouped (treat as own item).
    """
    if not isinstance(ex, dict):
        return ''
    tn = (ex.get('title_number') or '').strip().upper()
    if tn:
        return f'TN:{tn}'
    lot = (ex.get('lot_number') or '').strip().upper()
    mukim = (ex.get('mukim') or '').strip().upper()
    if lot:
        return f'LOT:{lot}|{mukim}'
    addr = _norm_addr(ex.get('description') or ex.get('property_address') or '')
    if addr:
        return f'ADDR:{addr}'
    hint = _norm_addr(ex.get('property_hint') or '')
    return f'HINT:{hint}' if hint else ''


def get_pending_gift_documents(client_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Returns {'property': [...], 'bank': [...], 'vehicle': [...]} with
    Documents not yet referenced in any Gift in the active draft Will's
    step5_data.

    Property handling: users often dump multiple images per property
    (front + back of geran, SPA, cukai tanah). We GROUP these so the user
    sees ONE property card with all supporting images listed under it,
    not three separate "who inherits this?" questions.
    """
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
    referenced_group_keys = set()  # so SPA for an already-gifted property is hidden too
    for g in gifts:
        if isinstance(g, dict) and g.get('document_id'):
            referenced_doc_ids.add(g['document_id'])
        # If a gift was saved with the property identifier, remember it so
        # later-uploaded SPA/tax for the same lot don't resurface as new.
        if isinstance(g, dict):
            gk = _property_group_key(g.get('property_info') or g)
            if gk:
                referenced_group_keys.add(gk)

    # Pull title docs AND supporting docs in one pass so we can attach.
    all_kinds = _GIFT_KINDS + _PROPERTY_SUPPORT_KINDS
    docs = (Document.query.filter(
        Document.client_id == client_id,
        Document.category.in_(all_kinds),
    ).order_by(Document.created_at.asc()).all())

    # First pass: index property-related docs (title + support) by group key
    prop_groups: Dict[str, Dict[str, Any]] = {}
    seen_keys = set()  # for bank/vehicle dedupe
    for d in docs:
        if d.id in referenced_doc_ids:
            continue
        try:
            ex = json.loads(d.extracted_data) if d.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            ex = {}

        if d.category in ('property_title',) + _PROPERTY_SUPPORT_KINDS:
            gk = _property_group_key(ex) or f'DOC:{d.id}'  # un-groupable → own bucket
            if gk in referenced_group_keys:
                continue  # already gifted under another doc
            grp = prop_groups.setdefault(gk, {
                'group_key': gk,
                'title_doc': None,        # the primary property_title (if any)
                'support_docs': [],       # SPA + cukai tanah for the same property
                'all_doc_ids': [],
            })
            doc_summary = {
                'document_id': d.id,
                'category': d.category,
                'extracted': ex,
                'purpose': (ex.get('purpose') or '').strip(),
                'original_filename': d.original_filename,
                'created_at': d.created_at.isoformat() if d.created_at else '',
            }
            grp['all_doc_ids'].append(d.id)
            if d.category == 'property_title':
                # Keep the FIRST title doc for this group; later titles for
                # the same lot are duplicates (e.g. front & back uploaded
                # both classified as title) → fold into support.
                if grp['title_doc'] is None:
                    grp['title_doc'] = doc_summary
                else:
                    grp['support_docs'].append(doc_summary)
            else:
                grp['support_docs'].append(doc_summary)
            continue

        # Bank / vehicle — same dedupe as before
        if d.category == 'bank_statement':
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
            'purpose': (ex.get('purpose') or '').strip(),
            'original_filename': d.original_filename,
            'created_at': d.created_at.isoformat() if d.created_at else '',
        }
        if d.category == 'bank_statement':
            out['bank'].append(item)
        else:
            out['vehicle'].append(item)

    # Second pass: emit one property item per group, ONLY if a title is
    # present (we need ownership evidence to put it in the will). Groups
    # with only SPA/cukai tanah → user uploaded supporting docs but no
    # title; surface as 'property_orphan' so chat can prompt "got the
    # geran for this?" without offering it as a giftable asset.
    for gk, grp in prop_groups.items():
        primary = grp['title_doc']
        if primary:
            primary['support_docs'] = grp['support_docs']
            primary['group_key'] = gk
            out['property'].append(primary)
        # Else: orphaned support docs — leave them out of the gift walk.
        # They still exist on disk for the user's records.
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
