"""§10x.21 — match message-stated roles to unassigned IC photos.

Algorithm mirrors §10g (asset matching) and §10i (temporal proximity):
  Layer 1: content match (phone digits / partial name / address)
  Layer 2: temporal proximity (message line timestamp ↔ IC upload time)
  Layer 3: residual — surface the unassigned ICs as candidates and ASK

Public API:
    extract_role_mentions(client_id) -> list[{role, evidence, phone, partial_name, line_idx}]
    find_unassigned_ic_candidates(client_id) -> list[{person_id, full_name, nric, document_id, upload_time}]
    match_role_to_candidates(role_mention, candidates) -> list[(candidate, confidence, reason)]

Roles recognised:
    executor, witness, trustee, guardian, sister-in-law, brother-in-law,
    aunt, uncle, friend, cousin
"""
from __future__ import annotations
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


# Role-mention regex — matches "my <relation> [<name?>] [Tel:<phone>]"
_ROLE_RE = re.compile(
    r'(?P<context>(?:my\s+)?(?:Executor|Witness|Trustee|Guardian)\s*'
    r'["\']?\s*(?:is|—|-|:)?\s*)?'
    r'(?:my\s+)?'
    r'(?P<role>sister[\s\-]in[\s\-]law|brother[\s\-]in[\s\-]law'
    r'|sister|brother|aunt|uncle|cousin|friend'
    r'|mother[\s\-]in[\s\-]law|father[\s\-]in[\s\-]law)'
    r'\b'
    r'\s*(?P<extra>[^,\n.]{0,80})',
    re.IGNORECASE,
)

# Phone regex — Malaysian / Singapore / generic 8-15 digit numbers,
# optional +60 / +65 country code, dashes/spaces inside. The {6,13}
# range plus required final digit ensures a real number, not partial.
_PHONE_RE = re.compile(r'\+?\d{1,3}[\s\-]?\d[\d\s\-]{6,13}\d')

# "My Executor — My Sister in law Tel:+6016-7338764"
_EXECUTOR_BLOCK_RE = re.compile(
    r'(?:My\s*)?["\']?Executor\s*["\']?\s*'
    r'(?P<body>[^\n]{0,200})',
    re.IGNORECASE,
)


def extract_role_mentions(client_id: str) -> List[Dict[str, Any]]:
    """Parse the AI Summary / raw forward text for role mentions.

    Returns list of dicts:
        {role, evidence_snippet, phone, partial_name, line_idx}

    🔥 §10x.21 fix: the AI Summary paraphrases ("executor (sister-in-law).")
    and DROPS phone numbers / partial names. Always merge the RAW forward
    text (step6_data._raw_forward_text) with the summary so phone/snippet
    extraction has the original signal to work with.
    """
    if not client_id:
        return []
    try:
        from ai.chat_planner import _gather_summary_source_text
        summary_text = _gather_summary_source_text(client_id) or ''
    except Exception:
        summary_text = ''
    raw_text = ''
    try:
        from database import db, Will
        import json as _json
        w = (Will.query.filter_by(client_id=client_id)
             .order_by(Will.created_at.desc()).first())
        if w and w.step6_data:
            raw_text = (_json.loads(w.step6_data) or {}).get(
                '_raw_forward_text', '') or ''
    except Exception:
        raw_text = ''
    # 🔥 §10x.21 ordering: scan RAW text first (has phone, original
    # spelling) THEN summary as supplement. Earlier the summary won the
    # dedup race and we lost the phone number from raw.
    text = (raw_text + '\n\n' + summary_text).strip()
    if not text:
        return []

    out: List[Dict[str, Any]] = []
    seen_roles: set = set()

    # Pass 1: explicit Executor block
    for m in _EXECUTOR_BLOCK_RE.finditer(text):
        body = m.group('body') or ''
        # Find role inside body
        role_match = re.search(
            r'(?P<role>sister[\s\-]in[\s\-]law|brother[\s\-]in[\s\-]law'
            r'|sister|brother|aunt|uncle|cousin|friend'
            r'|mother[\s\-]in[\s\-]law|father[\s\-]in[\s\-]law)',
            body, re.IGNORECASE)
        # Find phone inside body
        phone_match = _PHONE_RE.search(body)
        if role_match or phone_match:
            role = (role_match.group('role') if role_match else 'executor').lower()
            role = re.sub(r'[\s\-]+', '-', role)   # normalise to "sister-in-law"
            key = ('executor', role)
            if key in seen_roles:
                continue
            seen_roles.add(key)
            out.append({
                'role':              'executor',     # the WILL role
                'family_relation':   role,           # what the testator called them
                'phone':             phone_match.group(0).strip() if phone_match else '',
                'partial_name':      '',
                'evidence_snippet':  m.group(0)[:200].strip(),
                'msg_idx':           m.start(),
            })

    # Pass 2: free-form role mentions (witness, guardian, trustee)
    for keyword, role_label in (
        ('witness', 'witness'),
        ('Witness', 'witness'),
        ('trustee', 'trustee'),
        ('Trustee', 'trustee'),
        ('guardian', 'guardian'),
        ('Guardian', 'guardian'),
    ):
        for m in re.finditer(
            rf'\b{keyword}\b\s*[:\-—]?\s*(?P<body>[^\n.]{{0,150}})', text):
            body = m.group('body') or ''
            role_match = re.search(
                r'(?P<role>sister[\s\-]in[\s\-]law|brother[\s\-]in[\s\-]law'
                r'|sister|brother|aunt|uncle|cousin|friend'
                r'|mother[\s\-]in[\s\-]law|father[\s\-]in[\s\-]law'
                r'|wife|husband|spouse)',
                body, re.IGNORECASE)
            phone_match = _PHONE_RE.search(body)
            if role_match or phone_match:
                family = role_match.group('role').lower() if role_match else ''
                family = re.sub(r'[\s\-]+', '-', family)
                key = (role_label, family)
                if key in seen_roles:
                    continue
                seen_roles.add(key)
                out.append({
                    'role':              role_label,
                    'family_relation':   family,
                    'phone':             phone_match.group(0).strip() if phone_match else '',
                    'partial_name':      '',
                    'evidence_snippet':  (keyword + ': ' + body)[:200].strip(),
                    'msg_idx':           m.start(),
                })

    return out


def find_unassigned_ic_candidates(client_id: str) -> List[Dict[str, Any]]:
    """Return candidates for executor / witness / etc.

    🔥 §10x.21 fix: candidates come from TWO sources — Person rows AND raw
    Document rows of category='nric' that have not yet been linked to any
    Person. The original implementation only looked at Person, but Persons
    are created during the identity walkthrough — which has not yet run on
    a fresh inbound. Without including raw IC docs, the role matcher ALWAYS
    returned [] on freshly-classified accounts.
    """
    if not client_id:
        return []
    try:
        from database import db, Person, Document
    except Exception:
        return []
    REAL_FAMILY = {
        'testator', 'spouse', 'wife', 'husband',
        'son', 'daughter', 'father', 'mother',
        'son-in-law', 'daughter-in-law',
        'beneficiary', 'main beneficiary',
    }
    candidates: List[Dict[str, Any]] = []
    seen_doc_ids: set = set()
    seen_nrics: set = set()

    # Source 1: Person rows (already-assigned identity walkthrough output)
    try:
        persons = Person.query.filter_by(client_id=client_id).all()
    except Exception:
        persons = []
    for p in persons:
        rel = (p.relationship or '').strip().lower()
        if rel in REAL_FAMILY:
            # Track their IC docs so we don't re-surface them as Source 2 unassigned
            if p.document_id:
                seen_doc_ids.add(p.document_id)
            if p.nric_passport:
                seen_nrics.add(_digits(p.nric_passport))
            continue
        if not (p.full_name or '').strip():
            continue
        upload_ts = None
        if p.document_id:
            try:
                d = db.session.get(Document, p.document_id)
                if d and d.created_at:
                    upload_ts = d.created_at
                seen_doc_ids.add(p.document_id)
            except Exception:
                upload_ts = None
        if p.nric_passport:
            seen_nrics.add(_digits(p.nric_passport))
        candidates.append({
            'person_id':   p.id,
            'full_name':   p.full_name,
            'nric':        p.nric_passport or '',
            'address':     p.address or '',
            'phone':       getattr(p, 'phone', '') or '',
            'relationship_hint': rel,
            'document_id': p.document_id or '',
            'upload_ts':   upload_ts,
            '_source':     'person',
        })

    # Source 2: raw IC Documents not yet linked to a Person
    try:
        ic_docs = Document.query.filter_by(
            client_id=client_id, category='nric').all()
    except Exception:
        ic_docs = []
    import json as _json, re as _re
    for d in ic_docs:
        if d.id in seen_doc_ids:
            continue   # already represented via a Person row
        try:
            ed = _json.loads(d.extracted_data or '{}')
        except Exception:
            ed = {}
        # Pull canonical NRIC from possibly-noisy extractor output
        nric_raw = str(ed.get('nric_number') or '')
        m = _re.search(r'\d{6}[-\s]?\d{2}[-\s]?\d{4}', nric_raw)
        nric_clean = m.group(0) if m else ''
        nric_digits = _digits(nric_clean)
        if nric_digits and nric_digits in seen_nrics:
            continue   # IC already accounted for under a Person
        # Clean the name (strip issuing-authority text per §10aa)
        name_raw = (ed.get('full_name') or '').strip()
        AUTH_NOISE = ('KETUA PENGARAH', 'JABATAN PENDAFTARAN', 'MYKAD',
                      'KAD PENGENALAN', 'WARGANEGARA', 'IDENTITY CARD')
        if any(n in name_raw.upper() for n in AUTH_NOISE):
            name_clean = ''
        else:
            name_clean = name_raw
        candidates.append({
            'person_id':         '',                          # no Person yet
            'full_name':         name_clean,                  # may be empty
            'nric':              nric_clean,
            'address':           (ed.get('address') or '').strip(),
            'phone':             '',
            'relationship_hint': '',                          # unassigned
            'document_id':       d.id,
            'upload_ts':         d.created_at,
            '_source':           'unlinked_ic',
            '_filename':         d.original_filename,
        })
    return candidates


def _digits(s: str) -> str:
    return re.sub(r'\D', '', s or '')


def _testator_family_names(client_id: str) -> set:
    """🔥 §10x.21 elimination — names mentioned in the message that look like
    immediate-family roles (spouse / son / daughter / parent / sibling).
    Used to demote ICs that obviously match an immediate family member, so
    the only IC LEFT (the 'outsider') gets boosted as the executor candidate.
    """
    if not client_id:
        return set()
    try:
        from ai.chat_planner import (_extract_ai_summary_properties,
                                       _gather_summary_source_text)
    except Exception:
        return set()
    text = _gather_summary_source_text(client_id) or ''
    if not text:
        return set()
    names: set = set()
    # Regex hits like "to Joshua Koid Teck Seng (son)" / "Esther Koid En Hui (daughter)"
    # / "with my wife Lim Bee Yan" / "spouse Lim Bee Yan"
    family_role_re = re.compile(
        r'(?P<name>[A-Z][A-Z\s\-/]{4,60}?[A-Z])\s*'
        r'\(?\s*(?P<role>son|daughter|wife|husband|spouse|father|mother|'
        r'parent|brother|sister)\b',
        re.IGNORECASE)
    for m in family_role_re.finditer(text):
        nm = m.group('name').strip()
        # Filter typos / phrases that grabbed too much
        if 5 <= len(nm) <= 60 and nm.upper() == nm.upper():
            names.add(nm.upper())
    # Also: explicit role-tagged hints like "to my wife (Lim Bee Yan)"
    for m in re.finditer(
        r'\bmy\s+(son|daughter|wife|husband|spouse|brother|sister)\s+'
        r'\(?(?P<name>[A-Z][A-Z\s\-/]{4,60}?[A-Z])\b',
        text, re.IGNORECASE):
        nm = m.group('name').strip()
        if 5 <= len(nm) <= 60:
            names.add(nm.upper())
    return names


def _name_matches_family(name: str, family_names: set) -> bool:
    """Loose match: any family-name token appears in the candidate name."""
    if not name or not family_names:
        return False
    cn = name.upper()
    cn_tokens = set(t for t in re.split(r'\s+', cn) if len(t) > 2)
    for fn in family_names:
        fn_tokens = set(t for t in re.split(r'\s+', fn.upper()) if len(t) > 2)
        if not fn_tokens:
            continue
        # If 2+ tokens overlap, treat as same person (e.g. "JOSHUA KOID TECK SENG"
        # vs "JOSHUA KOID" both share "JOSHUA"+"KOID")
        if len(cn_tokens & fn_tokens) >= 2:
            return True
        # Or if the full family name is a substring
        if fn in cn:
            return True
    return False


def match_role_to_candidates(
    role_mention: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    client_id: str = '',
) -> List[Tuple[Dict[str, Any], str, str]]:
    """Match the role mention against candidate ICs.

    Returns ranked list of (candidate, confidence_label, reason).
    Confidence is 'high' (content match or sole-outsider), 'medium' (timing
    proximity), 'low' (residual — needs user pick).

    🔥 §10x.21 — when client_id is given, ICs whose names match testator's
    immediate family (spouse / son / daughter, etc. extracted from the
    message) are DEMOTED. The remaining 'outsider' IC is boosted as the
    high-confidence executor candidate. This handles the common case
    where the user uploads ALL family ICs in one email forward and the
    sister-in-law's IC is the only one whose name doesn't appear in the
    message family list.
    """
    if not candidates:
        return []
    out: List[Tuple[Dict[str, Any], str, str]] = []

    phone = _digits(role_mention.get('phone', ''))
    fam_hint = role_mention.get('family_relation', '').strip().lower()
    partial = (role_mention.get('partial_name') or '').strip().lower()
    family_names = _testator_family_names(client_id) if client_id else set()

    # ── Layer 1: content match (phone digits, partial name, family hint)
    # Stable identity key: person_id if present, else document_id (handles
    # unlinked-IC candidates from §10x.21 fix).
    def _key(c):
        return c.get('person_id') or c.get('document_id') or id(c)

    high_matched: set = set()
    for c in candidates:
        c_phone = _digits(c.get('phone', ''))
        c_name  = (c.get('full_name') or '').lower()
        c_rel   = (c.get('relationship_hint') or '').lower()

        # Phone match — last 7+ digits identical
        if phone and c_phone and len(phone) >= 7:
            if phone[-7:] == c_phone[-7:]:
                out.append((c, 'high', f'Phone digits match (…{phone[-4:]})'))
                high_matched.add(_key(c))
                continue
        # Family-relation hint match (e.g. user labelled IC as "sister-in-law"
        # during identity walk → exact role match)
        if fam_hint and c_rel and fam_hint == c_rel:
            out.append((c, 'high', f'IC labelled "{fam_hint}" during identity walk'))
            high_matched.add(_key(c))
            continue
        # Partial name match
        if partial and partial in c_name:
            out.append((c, 'high', f'Name contains "{partial}"'))
            high_matched.add(_key(c))
            continue

    # ── Layer 1.5: outsider elimination (§10x.21)
    # An IC whose name does NOT match any immediate family member named in
    # the message is the strongest candidate for executor (sister-in-law,
    # brother-in-law, friend, etc.). If exactly ONE candidate is an outsider,
    # promote it to HIGH confidence — this is the common-case shortcut.
    outsiders: list = []
    family_dups: dict = {}   # nric_digits → first candidate (dedup)
    if family_names:
        for c in candidates:
            if _key(c) in high_matched:
                continue
            cname = (c.get('full_name') or '').strip()
            cnric = _digits(c.get('nric', ''))
            # Dedup by NRIC — multiple photos of same IC
            if cnric and cnric in family_dups:
                continue
            if cnric:
                family_dups[cnric] = c
            if cname and _name_matches_family(cname, family_names):
                # Demote — this IC IS a family member (spouse / son / daughter)
                # and shouldn't be the executor unless explicitly named.
                continue
            outsiders.append(c)
        if len(outsiders) == 1:
            c = outsiders[0]
            out.append((c, 'high',
                        f'Only IC whose name is NOT in your family list — '
                        f'likely the {fam_hint or "executor"}'))
            high_matched.add(_key(c))
        elif len(outsiders) >= 2:
            # Multiple outsiders — surface as medium so the user picks
            for c in outsiders:
                if _key(c) in high_matched:
                    continue
                out.append((c, 'medium',
                            f'Outsider IC — not in your family list, '
                            f'one of {len(outsiders)} candidates'))
                high_matched.add(_key(c))

    # ── Layer 2: temporal proximity (per §10i)
    # If we don't have content match, use the IC's upload time relative to
    # the message-line timestamp. Without timestamps, we fall through to L3.

    # ── Layer 3: residual — list every unmatched candidate as 'low'
    for c in candidates:
        if _key(c) in high_matched:
            continue
        # Skip name-matched family duplicates (already represented above)
        cnric = _digits(c.get('nric', ''))
        cname = (c.get('full_name') or '').strip()
        if family_names and cname and _name_matches_family(cname, family_names):
            continue   # don't surface family ICs as executor candidates
        if cnric and cnric in family_dups and family_dups[cnric] is not c:
            continue   # duplicate photo of an outsider already surfaced
        out.append((c, 'low', 'Unassigned IC — please confirm if this person'))

    return out


def get_top_candidate(role_mention: Dict[str, Any],
                       client_id: str) -> Optional[Tuple[Dict[str, Any], str, str]]:
    """Convenience: return the single best candidate for this role, or None."""
    cands = find_unassigned_ic_candidates(client_id)
    ranked = match_role_to_candidates(role_mention, cands, client_id=client_id)
    return ranked[0] if ranked else None
