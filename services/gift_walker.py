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

    # Pull title docs + supporting docs + unclassified (chat_inbox/other) in
    # one pass. Unclassified docs are needed so we can attach them to the
    # matching property group when they arrived in the same email (same
    # chat_message_id) as a geran — typical for multi-page WhatsApp forwards
    # where back-pages get classified as 'other' because OCR sees no title.
    _SIBLING_KINDS = ('chat_inbox', 'other')
    all_kinds = _GIFT_KINDS + _PROPERTY_SUPPORT_KINDS + _SIBLING_KINDS
    docs = (Document.query.filter(
        Document.client_id == client_id,
        Document.category.in_(all_kinds),
    ).order_by(Document.created_at.asc()).all())

    # First pass: index property-related docs (title + support) by group key.
    # Also track chat_message_id → best group key so we can later absorb
    # sibling docs (same email batch, unclassified pages) into the group.
    prop_groups: Dict[str, Dict[str, Any]] = {}
    seen_keys = set()        # for bank/vehicle dedupe
    # msg_id → best group key rank: TN=4 > LOT=3 > ADDR=2 > HINT=1 > DOC=0
    _GK_RANK = {'TN:': 4, 'LOT:': 3, 'ADDR:': 2, 'HINT:': 1}
    msg_id_to_gk: Dict[str, str] = {}  # chat_message_id str → group key

    # Sibling pool: unclassified docs keyed by chat_message_id
    sibling_pool: Dict[str, List[Dict]] = {}

    for d in docs:
        if d.id in referenced_doc_ids:
            continue
        try:
            ex = json.loads(d.extracted_data) if d.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            ex = {}

        doc_summary = {
            'document_id': d.id,
            'category': d.category,
            'extracted': ex,
            'purpose': (ex.get('purpose') or '').strip(),
            'original_filename': d.original_filename,
            'created_at': d.created_at.isoformat() if d.created_at else '',
            'chat_message_id': d.chat_message_id,
        }

        # ── Unclassified siblings ──────────────────────────────────────────
        if d.category in _SIBLING_KINDS:
            mid = str(d.chat_message_id or '')
            if mid:
                sibling_pool.setdefault(mid, []).append(doc_summary)
            continue

        # ── Property docs ──────────────────────────────────────────────────
        if d.category in ('property_title',) + _PROPERTY_SUPPORT_KINDS:
            gk = _property_group_key(ex) or f'DOC:{d.id}'
            if gk in referenced_group_keys:
                continue
            grp = prop_groups.setdefault(gk, {
                'group_key': gk,
                'title_doc': None,
                'support_docs': [],
                'all_doc_ids': [],
            })
            grp['all_doc_ids'].append(d.id)
            if d.category == 'property_title':
                if grp['title_doc'] is None:
                    grp['title_doc'] = doc_summary
                else:
                    grp['support_docs'].append(doc_summary)
            else:
                grp['support_docs'].append(doc_summary)

            # Track the best group key seen for this chat message so later
            # unclassified docs from the same email can join this group.
            mid = str(d.chat_message_id or '')
            if mid:
                cur_rank = next((v for k, v in _GK_RANK.items() if gk.startswith(k)), 0)
                ex_gk = msg_id_to_gk.get(mid, '')
                ex_rank = next((v for k, v in _GK_RANK.items() if ex_gk.startswith(k)), 0)
                if cur_rank >= ex_rank:
                    msg_id_to_gk[mid] = gk
            continue

        # ── Bank / vehicle ─────────────────────────────────────────────────
        if d.category == 'bank_statement':
            key = ('bank', (ex.get('account_number') or '').strip())
        else:
            key = ('vehicle', (ex.get('reg_number') or '').strip().upper())
        if key[1] and key in seen_keys:
            continue
        seen_keys.add(key)
        if d.category == 'bank_statement':
            out['bank'].append(doc_summary)
        else:
            out['vehicle'].append(doc_summary)

    # ── Sibling merge ──────────────────────────────────────────────────────
    # Attach unclassified docs from the same email to the matching property
    # group. This fixes the "8 images but only 1 shown" problem: back-pages
    # of a geran that OCR couldn't read land as 'other'; we absorb them as
    # supporting docs under the geran that WAS identified.
    for mid, siblings in sibling_pool.items():
        gk = msg_id_to_gk.get(mid)
        if not gk or gk not in prop_groups:
            continue
        grp = prop_groups[gk]
        known_ids = set(grp['all_doc_ids'])
        for s in siblings:
            if s['document_id'] not in known_ids:
                grp['support_docs'].append(s)
                grp['all_doc_ids'].append(s['document_id'])
                known_ids.add(s['document_id'])

    # ── DOC:{id} merge ─────────────────────────────────────────────────────
    # Property docs that had no extractable lot/title (DOC:{id} bucket) can
    # still be merged into a named group if they share the same email batch.
    ungrouped_keys = [gk for gk in list(prop_groups.keys()) if gk.startswith('DOC:')]
    for gk in ungrouped_keys:
        grp = prop_groups[gk]
        # Find the chat_message_id for any doc in this group
        all_mids = set()
        for did in grp['all_doc_ids']:
            # Lookup from doc_summary in title_doc or support_docs
            for ds in ([grp['title_doc']] if grp['title_doc'] else []) + grp['support_docs']:
                if ds and ds.get('document_id') == did:
                    mid = str(ds.get('chat_message_id') or '')
                    if mid:
                        all_mids.add(mid)
        for mid in all_mids:
            target_gk = msg_id_to_gk.get(mid)
            if target_gk and target_gk != gk and target_gk in prop_groups:
                # Merge this DOC:{id} group into the named group
                target = prop_groups[target_gk]
                known_ids = set(target['all_doc_ids'])
                for ds in ([grp['title_doc']] if grp['title_doc'] else []) + grp['support_docs']:
                    if ds and ds['document_id'] not in known_ids:
                        target['support_docs'].append(ds)
                        target['all_doc_ids'].append(ds['document_id'])
                        known_ids.add(ds['document_id'])
                del prop_groups[gk]
                break

    # ── Emit one card per group ────────────────────────────────────────────
    # Only emit if a property_title is present (ownership evidence required).
    # Groups with only SPA/cukai tanah are orphaned — skip for now.
    for gk, grp in prop_groups.items():
        primary = grp['title_doc']
        if primary:
            # Deduplicate support docs by original_filename
            seen_fnames: set = set()
            primary_fname = (primary.get('original_filename') or '').strip()
            if primary_fname:
                seen_fnames.add(primary_fname)
            deduped: list = []
            for s in grp['support_docs']:
                fname = (s.get('original_filename') or '').strip()
                if fname and fname in seen_fnames:
                    continue
                seen_fnames.add(fname or str(s.get('document_id', '')))
                deduped.append(s)
            primary['support_docs'] = deduped
            primary['group_key'] = gk
            out['property'].append(primary)
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
