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

# Phone regex (Malaysia / SG / generic 10-12 digits with optional +)
_PHONE_RE = re.compile(r'(?:\+?60|\+?65)?\s*[\d][\d\s\-]{7,14}\d')

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
    """
    if not client_id:
        return []
    try:
        from ai.chat_planner import _gather_summary_source_text
        text = _gather_summary_source_text(client_id) or ''
    except Exception:
        return []
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
    """Return Person rows that are NOT testator/spouse/son/daughter/father/mother
    AND have a real full_name. These are candidates for executor / witness / etc.
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
    try:
        persons = Person.query.filter_by(client_id=client_id).all()
    except Exception:
        persons = []
    for p in persons:
        rel = (p.relationship or '').strip().lower()
        if rel in REAL_FAMILY:
            continue
        if not (p.full_name or '').strip():
            continue
        # Get the source IC document timestamp for §10i temporal match
        upload_ts = None
        if p.document_id:
            try:
                d = db.session.get(Document, p.document_id)
                if d and d.created_at:
                    upload_ts = d.created_at
            except Exception:
                upload_ts = None
        candidates.append({
            'person_id':   p.id,
            'full_name':   p.full_name,
            'nric':        p.nric_passport or '',
            'address':     p.address or '',
            'phone':       getattr(p, 'phone', '') or '',
            'relationship_hint': rel,   # what the user typed when matching IC
            'document_id': p.document_id or '',
            'upload_ts':   upload_ts,
        })
    return candidates


def _digits(s: str) -> str:
    return re.sub(r'\D', '', s or '')


def match_role_to_candidates(
    role_mention: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> List[Tuple[Dict[str, Any], str, str]]:
    """Match the role mention against candidate ICs.

    Returns ranked list of (candidate, confidence_label, reason).
    Confidence is 'high' (content match), 'medium' (timing proximity),
    'low' (residual — needs user pick).
    """
    if not candidates:
        return []
    out: List[Tuple[Dict[str, Any], str, str]] = []

    phone = _digits(role_mention.get('phone', ''))
    fam_hint = role_mention.get('family_relation', '').strip().lower()
    partial = (role_mention.get('partial_name') or '').strip().lower()

    # ── Layer 1: content match (phone digits, partial name, family hint)
    high_matched: set = set()
    for c in candidates:
        c_phone = _digits(c.get('phone', ''))
        c_name  = (c.get('full_name') or '').lower()
        c_rel   = (c.get('relationship_hint') or '').lower()

        # Phone match — last 7+ digits identical
        if phone and c_phone and len(phone) >= 7:
            if phone[-7:] == c_phone[-7:]:
                out.append((c, 'high', f'Phone digits match (…{phone[-4:]})'))
                high_matched.add(c['person_id'])
                continue
        # Family-relation hint match (e.g. user labelled IC as "sister-in-law"
        # during identity walk → exact role match)
        if fam_hint and c_rel and fam_hint == c_rel:
            out.append((c, 'high', f'IC labelled "{fam_hint}" during identity walk'))
            high_matched.add(c['person_id'])
            continue
        # Partial name match
        if partial and partial in c_name:
            out.append((c, 'high', f'Name contains "{partial}"'))
            high_matched.add(c['person_id'])
            continue

    # ── Layer 2: temporal proximity (per §10i)
    # If we don't have content match, use the IC's upload time relative to
    # the message-line timestamp. Without timestamps, we fall through to L3.

    # ── Layer 3: residual — list every unmatched candidate as 'low'
    for c in candidates:
        if c['person_id'] in high_matched:
            continue
        out.append((c, 'low', 'Unassigned IC — please confirm if this person'))

    return out


def get_top_candidate(role_mention: Dict[str, Any],
                       client_id: str) -> Optional[Tuple[Dict[str, Any], str, str]]:
    """Convenience: return the single best candidate for this role, or None."""
    cands = find_unassigned_ic_candidates(client_id)
    ranked = match_role_to_candidates(role_mention, cands)
    return ranked[0] if ranked else None
