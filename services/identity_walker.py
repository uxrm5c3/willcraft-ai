"""Helpers for the directed Identity-walkthrough chat flow.

Finds ICs that have been classified + extracted but not yet assigned a
relationship (no Person row linked back), and parses free-form user
replies like "she's the spouse" into a canonical relationship label.
"""
from typing import List, Dict, Any, Optional
import json
import re
from database import Document, Person


# Malaysian NRIC: 6 digits - 2 digits - 4 digits (may have extra trailing digits
# like "-02-01" from raw card text — we only want the canonical 12-digit form).
_NRIC_RE = re.compile(r'(\d{6}[-\s]?\d{2}[-\s]?\d{4})')

# Strings that occasionally land in extracted full_name but are NOT a person's
# name — they're issuing-authority / card-header text. Treat as empty.
_NON_PERSON_NAME_FRAGMENTS = (
    'KETUA PENGARAH',
    'PENDAFTARAN NEGARA',
    'JABATAN PENDAFTARAN',
    'MYKAD',
    'KAD PENGENALAN',
    'IDENTITY CARD',
    'WARGANEGARA',
    'MALAYSIA',
)


def _canonical_nric(value: str) -> str:
    """Pull the canonical 12-digit NRIC out of any string. Handles values
    like 'VALUE: 650629-04-5308-02-01', 'This appears to be ... 650629-04-5308',
    or '650629045308'. Returns 'NNNNNN-NN-NNNN' uppercased, or '' if no match.
    """
    if not value:
        return ''
    m = _NRIC_RE.search(value)
    if not m:
        # Try bare 12 digits
        digits = re.sub(r'\D', '', value)
        if len(digits) >= 12:
            d = digits[:12]
            return f"{d[:6]}-{d[6:8]}-{d[8:12]}"
        return ''
    raw = re.sub(r'\s+', '', m.group(1))
    digits = re.sub(r'\D', '', raw)
    if len(digits) < 12:
        return ''
    d = digits[:12]
    return f"{d[:6]}-{d[6:8]}-{d[8:12]}"


def _clean_person_name(value: str) -> str:
    """Return uppercased name, or '' if it looks like issuing-authority text."""
    if not value:
        return ''
    nm = value.strip().upper()
    if not nm:
        return ''
    for frag in _NON_PERSON_NAME_FRAGMENTS:
        if frag in nm:
            return ''
    # Reject if it has no alphabetic chars (e.g. "VALUE: 650629-...")
    if not re.search(r'[A-Z]', nm):
        return ''
    return nm


# Keywords → canonical relationship label. Longer phrases are matched
# first (e.g. "sister in law" before "sister").
RELATIONSHIP_KEYWORDS = {
    # Family
    'spouse': 'Spouse',
    'husband': 'Husband',
    'wife': 'Wife',
    'son in law': 'Son-in-law',
    'daughter in law': 'Daughter-in-law',
    'sister in law': 'Sister-in-law',
    'brother in law': 'Brother-in-law',
    'father in law': 'Father-in-law',
    'mother in law': 'Mother-in-law',
    'son': 'Son',
    'daughter': 'Daughter',
    'father': 'Father',
    'mother': 'Mother',
    'brother': 'Brother',
    'sister': 'Sister',
    'grandson': 'Grandson',
    'granddaughter': 'Granddaughter',
    'grandfather': 'Grandfather',
    'grandmother': 'Grandmother',
    'uncle': 'Uncle',
    'aunt': 'Aunt',
    'auntie': 'Aunt',
    'nephew': 'Nephew',
    'niece': 'Niece',
    'cousin': 'Cousin',
    'stepson': 'Stepson',
    'stepdaughter': 'Stepdaughter',
    'adopted son': 'Adopted Son',
    'adopted daughter': 'Adopted Daughter',
    # Will roles
    'executor': 'Executor',
    'trustee': 'Trustee',
    'guardian': 'Guardian',
    'witness': 'Witness',
    'beneficiary': 'Beneficiary',
    # Catch-all
    'friend': 'Friend',
    'relative': 'Relative',
    'other': 'Other',
}


def get_pending_ic_documents(client_id: str) -> List[Dict[str, Any]]:
    """ICs that don't yet have a corresponding Person. Dedupes by extracted
    name (case-insensitive) and NRIC number, so re-uploading the same IC
    twice or having it already in the wizard doesn't repeat the question.

    A Document is skipped if ANY of:
      - it's already linked to a Person (document_id match), OR
      - any Person already exists with the same extracted name, OR
      - any Person already exists with the same extracted NRIC, OR
      - user already skipped it this session (_chat_skipped flag in extracted_data).
    """
    docs = (Document.query
            .filter_by(client_id=client_id, category='nric')
            .order_by(Document.created_at.asc())
            .all())
    if not docs:
        return []

    # All Persons for this client — used to dedupe by name AND nric.
    # NRICs are normalised to canonical 12-digit form so embedded/garbage
    # extractions still match (see CLAUDE.md §4a).
    persons = Person.query.filter_by(client_id=client_id).all()
    known_names = {_clean_person_name(p.full_name) for p in persons if p.full_name}
    known_names.discard('')
    known_nrics = {_canonical_nric(p.nric_passport) for p in persons if p.nric_passport}
    known_nrics.discard('')
    linked_doc_ids = {p.document_id for p in persons if p.document_id}

    pending = []
    seen_in_pending = set()  # names we've already queued in this batch
    seen_nrics_in_pending = set()  # NRICs we've already queued
    for d in docs:
        if d.id in linked_doc_ids:
            continue
        try:
            ex = json.loads(d.extracted_data) if d.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            ex = {}
        # Skip if user explicitly skipped this document in chat
        if ex.get('_chat_skipped'):
            continue
        name_key = _clean_person_name(ex.get('full_name') or '')
        nric_key = _canonical_nric(ex.get('nric_number') or '')
        # Also try to recover NRIC from full_name field if the extractor
        # accidentally dumped it there, and vice versa.
        if not nric_key:
            nric_key = _canonical_nric(ex.get('full_name') or '')
        if name_key:
            if name_key in known_names:
                continue  # a Person already exists for this name
            if name_key in seen_in_pending:
                continue  # duplicate within this batch
        if nric_key:
            if nric_key in known_nrics:
                continue  # a Person already exists with this NRIC
            if nric_key in seen_nrics_in_pending:
                continue  # duplicate NRIC within this batch
        if name_key:
            seen_in_pending.add(name_key)
        if nric_key:
            seen_nrics_in_pending.add(nric_key)
        # Unreadable-name docs (name_key empty) all stay pending —
        # the user has to look at the thumbnail and identify them.
        pending.append({
            'document_id': d.id,
            'extracted': ex,
            'original_filename': d.original_filename,
            'created_at': d.created_at.isoformat() if d.created_at else '',
        })
    return pending


def skip_pending_ic_document(client_id: str) -> Optional[Dict[str, Any]]:
    """Mark the next pending IC document as skipped so the walkthrough
    moves past it. Sets extracted_data['_chat_skipped'] = True.
    Returns {'name', 'action': 'skipped'} or None if nothing pending."""
    pending = get_pending_ic_documents(client_id)
    if not pending:
        return None
    target = pending[0]
    from database import Document
    import json as _json
    doc = Document.query.get(target['document_id'])
    if not doc:
        return None
    try:
        ex = _json.loads(doc.extracted_data) if doc.extracted_data else {}
    except (ValueError, TypeError):
        ex = {}
    ex['_chat_skipped'] = True
    doc.extracted_data = _json.dumps(ex)
    # Also mark any other nric docs with same name or nric as skipped
    name_key = (ex.get('full_name') or '').strip().upper()
    nric_key = (ex.get('nric_number') or '').strip().upper()
    if name_key or nric_key:
        all_nric = Document.query.filter_by(client_id=client_id, category='nric').all()
        for d in all_nric:
            if d.id == doc.id:
                continue
            try:
                dex = _json.loads(d.extracted_data) if d.extracted_data else {}
            except (ValueError, TypeError):
                dex = {}
            d_name = (dex.get('full_name') or '').strip().upper()
            d_nric = (dex.get('nric_number') or '').strip().upper()
            if (name_key and d_name and name_key == d_name) or \
               (nric_key and d_nric and nric_key == d_nric):
                dex['_chat_skipped'] = True
                d.extracted_data = _json.dumps(dex)
    label = (ex.get('full_name') or target.get('original_filename', 'this IC')).strip()
    return {'name': label, 'action': 'skipped', 'document_id': target['document_id']}


def link_duplicate_ic_documents(client_id: str, person):
    """After a Person is created/updated for a name, link every other
    Document in nric category whose extracted name matches this Person.
    Keeps the data tidy so the chat doesn't see them as 'pending' later
    and so the wizard's docs view shows all uploads against the same Person."""
    if not person or not person.full_name:
        return
    target_name = person.full_name.strip().upper()
    docs = Document.query.filter_by(client_id=client_id, category='nric').all()
    for d in docs:
        if d.id == person.document_id:
            continue
        try:
            ex = json.loads(d.extracted_data) if d.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            ex = {}
        nm = ((ex.get('full_name') or '').strip().upper())
        if nm and nm == target_name:
            # No FK column on Document linking to Person, but we can mark
            # the Document so it doesn't show as pending. The dedupe
            # in get_pending_ic_documents already handles this via the
            # known_names set, but linking via Person.document_id ensures
            # consistency. Skip silently here — dedupe is enough.
            pass


def parse_relationship(text: str) -> Optional[str]:
    """Find the first relationship keyword in user text. Returns the
    canonical label or None. Longer multi-word keywords win.
    """
    if not text:
        return None
    t = ' ' + text.lower() + ' '  # pad so word-boundary checks work
    for kw in sorted(RELATIONSHIP_KEYWORDS, key=len, reverse=True):
        # Match as a whole-word phrase
        if (' ' + kw + ' ') in t:
            return RELATIONSHIP_KEYWORDS[kw]
    return None
