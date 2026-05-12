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


def _score_ic_confidence(name: str, recent_text: str,
                          role_matcher_outsider_names: set,
                          nric: str = '') -> int:
    """🔥 §10x.30 BURN-IN — Identity matching: HIGH → LOW confidence.

    Mirrors §10e for asset matching, applied to identities. The IC
    walkthrough orders pending identities by HOW CONFIDENTLY the
    relationship can be deduced from the message.

    Score grid:
        5 — HIGH:   Name + family-role word ('son', 'daughter', 'wife',
                    'husband', 'spouse', 'father', 'mother', 'brother',
                    'sister') within 30 chars before / 60 chars after.
                    e.g. "Joshua Koid Teck Seng(son)" → 5
        4 — HIGH:   Name + co-owner phrase ('I share with', 'joint with',
                    'co-owned with'). Per §10x.19, co-owners are NOT
                    Person rows — they're stored on property only —
                    but the deduction is still HIGH so the user sees a
                    "co-owner of property X" suggestion.
                    e.g. "I share with Chai Mei Fun 50/50" → 4
        3 — MEDIUM: Name appears in message, no role word adjacent.
                    User has to choose.
        1 — LOW:    Name NOT in message; outsider-elimination identifies
                    them as the lone non-family candidate (§10x.21).
                    e.g. "My Sister in law Tel:+6016-..." with LIM LAY
                    CHENG as the only IC whose name doesn't match any
                    family member → 1
        0 — NONE:   No signal at all.
    """
    nm = (name or '').strip().upper()
    text_upper = (recent_text or '').upper()
    # 🔥 §10x.89 — Loose name matching. The IC's full name need not appear
    # verbatim in the message. If ANY 4+ char token of the name appears
    # in the message AND a family-role word is near it, that's a match.
    # Example:
    #   IC name : "JOSHUA KOID TECK SENG"
    #   Message : "my son Joshua"
    #   Token "JOSHUA" appears → match (was previously requiring full
    #   "JOSHUA KOID TECK SENG" substring → no match → score 0).
    name_tokens_4plus = []
    if nm:
        import re as _re_nt
        name_tokens_4plus = [t for t in _re_nt.split(r'[\s\-]+', nm)
                              if len(t) >= 4 and t.isalpha()]
    # 🔥 §10x.87 — NRIC-age tiebreaker for empty-name ICs. When two ICs
    # both have name='' (Tesseract failed on both), upload-time order
    # is the only tiebreaker — which surfaces the OUTSIDER (sister-in-
    # law) before the named family member (Joshua) just because of
    # email arrival order. NRIC year-of-birth tells us which generation
    # the IC belongs to: 20-40yo NRIC near a "(son)" / "(daughter)"
    # mention scores higher than an unmatched outsider.
    if not nm and nric:
        import re as _re
        m = _re.search(r'(\d{2})', nric)
        if m:
            yy = int(m.group(1))
            year = 1900 + yy if yy >= 31 else 2000 + yy
            from datetime import datetime
            age = datetime.utcnow().year - year
            # Score 2 if age fits a CHILD/SPOUSE band that's named in
            # the message; outsider-only IC stays at 0/1.
            CHILD_ROLES = ('SON', 'DAUGHTER', 'SPOUSE', 'WIFE', 'HUSBAND')
            if any(r in text_upper for r in CHILD_ROLES) and 5 <= age <= 60:
                return 2   # better than 1 (outsider) but lower than 3 (name in msg)
    if not nm:
        return 0
    # Try direct full-name substring match first (highest fidelity)
    if nm in text_upper:
        idx = text_upper.find(nm)
        match_len = len(nm)
    else:
        # 🔥 §10x.89 — Token-overlap loose match. Any 4+ char name
        # token that appears as a whole word in the message counts as
        # a hit. We require the longest such token to disambiguate
        # (avoid 'KOID' alone matching 'KOID BENG SUN' for Joshua).
        idx = -1
        match_len = 0
        FAMILY_ROLES = ('SON', 'DAUGHTER', 'WIFE', 'HUSBAND', 'SPOUSE',
                         'FATHER', 'MOTHER', 'BROTHER', 'SISTER')
        for tok in sorted(name_tokens_4plus, key=len, reverse=True):
            import re as _re_tok
            m = _re_tok.search(rf'\b{tok}\b', text_upper)
            if m:
                idx = m.start()
                match_len = len(tok)
                break
        if idx < 0:
            # Outsider-elimination match (sister-in-law case)
            if nm in role_matcher_outsider_names:
                return 1
            return 0
    # We have a hit at idx. Score by what's around it.
    ctx = text_upper[max(0, idx - 30): idx + match_len + 60]
    FAMILY_ROLES = ('SON', 'DAUGHTER', 'WIFE', 'HUSBAND', 'SPOUSE',
                     'FATHER', 'MOTHER', 'BROTHER', 'SISTER')
    if any(r in ctx for r in FAMILY_ROLES):
        return 5
    before = text_upper[max(0, idx - 50): idx]
    CO_OWNER_PHRASES = ('SHARE WITH', 'JOINT WITH', 'CO-OWNED WITH',
                         'CO OWNED WITH', 'JOINTLY WITH')
    if any(p in before for p in CO_OWNER_PHRASES):
        return 4
    return 3


def get_pending_ic_documents(client_id: str) -> List[Dict[str, Any]]:
    """ICs that don't yet have a corresponding Person. Dedupes by extracted
    name (case-insensitive) and NRIC number, so re-uploading the same IC
    twice or having it already in the wizard doesn't repeat the question.

    A Document is skipped if ANY of:
      - it's already linked to a Person (document_id match), OR
      - any Person already exists with the same extracted name, OR
      - any Person already exists with the same extracted NRIC, OR
      - user soft-deleted it via the chat 🗑 button (_chat_skipped flag —
        kept in code for backward compat with old skipped docs; new Skip
        clicks no longer set this flag, see §10x.31).

    🔥 §10e (identity edition) — pending list is sorted by deduction
    confidence DESC: ICs whose relationship is OBVIOUS from the message
    (Joshua = son, Esther = daughter) come FIRST. Outsider-elimination
    cases (LIM LAY CHENG = sister-in-law because she's the only non-
    family name) come LAST. Same HIGH→LOW order as asset matching.
    """
    docs = (Document.query
            .filter_by(client_id=client_id, category='nric')
            .order_by(Document.created_at.asc())
            .all())
    # 🔥 §10x.210 — REMOVED early-return on `not docs`. When the user
    # describes a family member in text only (no IC uploaded), §10x.34
    # H3 placeholders MUST still surface. The old `if not docs: return []`
    # short-circuited the H3 synthesis block at the bottom of this
    # function, leaving text-only clients with zero pending identities.
    # Now we fall through with `docs=[]` and the H3 block at line ~352
    # picks up the family-name pairs from AI Summary text.

    # All Persons for this client — used to dedupe by name AND nric.
    # NRICs are normalised to canonical 12-digit form so embedded/garbage
    # extractions still match (see CLAUDE.md §4a).
    persons = Person.query.filter_by(client_id=client_id).all()
    known_names = {_clean_person_name(p.full_name) for p in persons if p.full_name}
    known_names.discard('')
    known_nrics = {_canonical_nric(p.nric_passport) for p in persons if p.nric_passport}
    known_nrics.discard('')
    linked_doc_ids = {p.document_id for p in persons if p.document_id}

    # 🔥 §10x.81 — front+back IC dedup. When the user uploads the FRONT
    # of an IC (name + NRIC visible) and then the BACK separately
    # (address only, name extraction fails), the walker used to treat
    # them as two separate pending ICs — confusing the user with a
    # "(name not extracted) — <NRIC>" card for an IC they already saw.
    # Pre-pass: when two docs share the same canonical NRIC, keep the
    # one with MORE extracted fields and drop the rest.
    docs_by_nric: Dict[str, Any] = {}
    other_docs = []
    for d in docs:
        try:
            ex = json.loads(d.extracted_data) if d.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            ex = {}
        nric_key = _canonical_nric(ex.get('nric_number') or '')
        if not nric_key:
            nric_key = _canonical_nric(ex.get('full_name') or '')
        if not nric_key:
            other_docs.append(d)
            continue
        # Prefer the doc with the most non-empty fields (front beats back).
        existing = docs_by_nric.get(nric_key)
        if existing is None:
            docs_by_nric[nric_key] = d
        else:
            try:
                ex_old = json.loads(existing.extracted_data) if existing.extracted_data else {}
            except (json.JSONDecodeError, TypeError):
                ex_old = {}
            score_new = sum(1 for k in ('full_name','nric_number','date_of_birth','address','gender') if (ex.get(k) or '').strip())
            score_old = sum(1 for k in ('full_name','nric_number','date_of_birth','address','gender') if (ex_old.get(k) or '').strip())
            if score_new > score_old:
                docs_by_nric[nric_key] = d
    docs = list(docs_by_nric.values()) + other_docs

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

    # ── 🔥 §10e — sort pending by deduction confidence DESC ────────
    # Build the deduction inputs once, score every pending IC.
    recent_text = _gather_message_text(client_id) or ''
    outsider_names = _outsider_eliminated_names(client_id)
    for p in pending:
        nm = (p['extracted'].get('full_name') or '').strip()
        nric_val = (p['extracted'].get('nric_number') or '').strip()
        p['_deduction_score'] = _score_ic_confidence(
            nm, recent_text, outsider_names, nric=nric_val)

    # ╔════════════════════════════════════════════════════════════════════╗
    # ║  🔥 §10x.34 / §10hg — H3 IDENTITY PLACEHOLDERS                     ║
    # ║  Family members named in AI Summary text but without an uploaded   ║
    # ║  IC (e.g. "my wife (Lim Bee Yan)") MUST appear as pending          ║
    # ║  identity cards. Same rule as §10x.12 for assets — text alone is   ║
    # ║  sufficient. The user confirms with one click; Person row created  ║
    # ║  with relationship + name (no document_id, no nric).               ║
    # ╚════════════════════════════════════════════════════════════════════╝
    from_text = _extract_family_name_role_pairs(recent_text)
    for nm, role in from_text:
        nm_upper = nm.strip().upper()
        # Skip if already a Person (any case-form match)
        if nm_upper in known_names:
            continue
        # Skip if already in pending (deduped name)
        if nm_upper in seen_in_pending:
            continue
        seen_in_pending.add(nm_upper)
        pending.append({
            'document_id': None,    # no IC uploaded
            '_h3_placeholder': True,
            '_h3_role': role,
            'extracted': {
                'full_name': nm,
                'nric_number': '',
                '_h3_source': 'ai_summary',
            },
            'original_filename': '',
            'created_at': '',
            '_deduction_score': 5,  # name+role in message = HIGH per §10x.30
        })

    # Stable sort: highest score first, then by upload time as tie-breaker
    pending.sort(key=lambda p: (-p['_deduction_score'], p.get('created_at', '')))
    return pending


def _extract_family_name_role_pairs(text: str) -> List[tuple]:
    """🔥 §10x.34 — Pull (full_name, family_role) pairs from message text.

    Recognises patterns:
        "my wife (Lim Bee Yan)" → (Lim Bee Yan, Wife)
        "wife Lim Bee Yan"       → (Lim Bee Yan, Wife)
        "Joshua Koid Teck Seng (son)" → (Joshua Koid Teck Seng, Son)
        "(daughter) Esther Koid" → (Esther Koid, Daughter)

    Returns list of (name, Title-Case role) tuples. Names are reasonable
    multi-token capitalised strings (filtered against stopword/junk).
    """
    if not text:
        return []
    out: list = []
    seen: set = set()
    # 🔥 §10x.230 — include SPACED variants of in-law roles BEFORE hyphenated
    # forms so regex alternation prefers the longer/specific form. Without
    # this, "My Sister in law LIM LAY CHENG" would match role='sister' and
    # name='in law LIM LAY CHENG' — completely wrong.
    FAM_ROLES = (
        # spaced in-law variants (longest-first for proper alternation)
        'sister in law', 'brother in law',
        'mother in law', 'father in law',
        'son in law', 'daughter in law',
        # hyphenated in-law variants
        'sister-in-law', 'brother-in-law',
        'mother-in-law', 'father-in-law',
        'son-in-law', 'daughter-in-law',
        # single-word family roles
        'wife', 'husband', 'spouse', 'son', 'daughter',
        'father', 'mother', 'brother', 'sister',
    )
    role_alt = '|'.join(re.escape(r) for r in FAM_ROLES)

    def _canon_role(r: str) -> str:
        """Normalise role to Title-Case canonical form. Maps spaced
        in-law forms back to hyphenated canonical (Sister-in-law)."""
        s = r.strip().lower()
        # Map "sister in law" → "sister-in-law" before titlecase
        s = re.sub(r'\s+in\s+law\b', '-in-law', s)
        # Title-case each component
        parts = s.split('-')
        return '-'.join(p.capitalize() if i == 0 else p for i, p in enumerate(parts))
    # 🔥 §10x.210 — Use `(?-i:...)` inline modifier so [A-Z] is
    # case-sensitive even when re.IGNORECASE is enabled on the whole
    # pattern. Without this, "children Joshua Koid Teck Seng" matches
    # as a 5-token "name" (because IGNORECASE turns [A-Z] into [A-Za-z])
    # and the JUNK filter then rejects the whole capture, losing Joshua
    # entirely. The role part still matches case-insensitively.
    name_pat = (r"(?-i:[A-Z])[A-Za-z\-\']{1,}"
                r"(?:\s+(?-i:[A-Z])[A-Za-z\-\']{1,}){1,4}")

    # Pattern 1: "(my|his|her) <role> (<NAME>)" / "<role> (<NAME>)" /
    # ", <role> (<NAME>)" / "and <role> (<NAME>)"
    for m in re.finditer(
        rf'(?:\b(?:my|his|her)\s+|,\s*|\band\s+)(?P<role>{role_alt})\s*\(\s*(?P<name>{name_pat})\s*\)',
        text, re.IGNORECASE):
        nm  = m.group('name').strip()
        role = _canon_role(m.group('role'))
        if nm and (nm.upper() not in seen):
            seen.add(nm.upper())
            out.append((nm, role))

    # Pattern 2: "<NAME> (<role>)" — name + role in parens
    for m in re.finditer(
        rf'\b(?P<name>{name_pat})\s*\(\s*(?P<role>{role_alt})\s*\)',
        text, re.IGNORECASE):
        nm  = m.group('name').strip()
        role = _canon_role(m.group('role'))
        if nm and (nm.upper() not in seen):
            seen.add(nm.upper())
            out.append((nm, role))

    # Pattern 3: "<NAME>(<role>)" — no space (KOID style)
    for m in re.finditer(
        rf'\b(?P<name>{name_pat})\(\s*(?P<role>{role_alt})\s*\)',
        text, re.IGNORECASE):
        nm  = m.group('name').strip()
        role = _canon_role(m.group('role'))
        if nm and (nm.upper() not in seen):
            seen.add(nm.upper())
            out.append((nm, role))

    # Pattern 4: "my <role> <NAME>" — bare name after role
    for m in re.finditer(
        rf'\bmy\s+(?P<role>{role_alt})\s+(?P<name>{name_pat})\b',
        text, re.IGNORECASE):
        nm  = m.group('name').strip()
        role = _canon_role(m.group('role'))
        if nm and (nm.upper() not in seen):
            seen.add(nm.upper())
            out.append((nm, role))

    # 🔥 §10x.139 — Pattern 5: optional pronoun OR comma-separated list.
    # Catches AI Summary phrasings like "his wife Lim Bee Yan, son Joshua
    # Koid Teck Seng, and daughter Esther Koid En Hui" where bare role
    # followed by a name occurs without the "my" prefix.
    for m in re.finditer(
        rf'(?:\b(?:my|his|her)\s+|,\s*|\band\s+)(?P<role>{role_alt})\s+(?P<name>{name_pat})\b',
        text, re.IGNORECASE):
        nm  = m.group('name').strip()
        role = _canon_role(m.group('role'))
        if nm and (nm.upper() not in seen):
            seen.add(nm.upper())
            out.append((nm, role))

    # Filter junk-name tokens (no stopwords, must be 2-5 capitalised parts)
    # 🔥 §10x.212 — section-header words leak when regex greedy-matches 4
    # additional tokens. Common offenders: "Joshua Koid Teck Seng ASSETS",
    # "Lim Bee Yan PROPERTIES". Extended JUNK to cover section headers
    # the email body typically contains. Strips trailing junk in the
    # while-loop below; rejects entries with interior junk.
    JUNK = {'WITH', 'ALL', 'AND', 'OR', 'THE', 'TO', 'GO', 'OF',
            'FROM', 'BY', 'IN', 'ON', 'FOR', 'BANK', 'INSURANCE',
            'POLICY', 'ACCOUNT', 'NRIC', 'PROPERTY', 'SHARE',
            'JOINT', 'CO', 'CONDOMINIUM', 'HOUSE', 'SHOP',
            # §10x.212 — section headers (any plural / ALL CAPS form)
            'ASSETS', 'PROPERTIES', 'BANKS', 'POLICIES', 'ACCOUNTS',
            'RESIDUARY', 'TRUST', 'TRUSTEE', 'TRUSTEES', 'EXECUTOR',
            'EXECUTORS', 'BENEFICIARY', 'BENEFICIARIES', 'DOCUMENTS',
            'LIABILITIES', 'DEBTS', 'NOTES', 'ATTACHMENTS',
            'DISTRIBUTION', 'ESTATE', 'INFORMATION', 'DETAILS',
            # §10x.213 — common doc-type abbreviations that leak when an
            # email subject like 'Wife IC — Lim Bee Yan' or body line
            # 'Lim Bee Yan IC attached' gets regex-matched.
            'IC', 'NRIC', 'MYKAD', 'KAD', 'PHOTO', 'IMAGE', 'PIC',
            'COPY', 'ATTACHED', 'BIRTH', 'CERT', 'CERTIFICATE'}
    cleaned: list = []
    for nm, role in out:
        toks = re.split(r'\s+', nm.strip())
        # 🔥 §10x.230 — strip TRAILING junk tokens. Pattern 4/5 can greedy-
        # match "LIM LAY CHENG NRIC" because NRIC is also capitalised. Drop
        # trailing JUNK tokens before length validation rather than reject
        # the whole entry.
        while toks and toks[-1].upper() in JUNK:
            toks.pop()
        if not (2 <= len(toks) <= 5):
            continue
        # Reject if interior tokens (not just trailing) are junk
        if any(t.upper() in JUNK for t in toks):
            continue
        # Each token must start with uppercase
        if not all(re.match(r"^[A-Z][A-Za-z'\-]{1,}$", t) for t in toks):
            continue
        cleaned.append((' '.join(toks), role))
    return cleaned


def _gather_message_text(client_id: str) -> str:
    """Lazy import — pull the AI Summary + raw forward text for relationship
    inference. Mirrors what role_matcher uses."""
    try:
        from ai.chat_planner import _gather_summary_source_text
        summary = _gather_summary_source_text(client_id) or ''
    except Exception:
        summary = ''
    raw = ''
    try:
        from database import db, Will
        import json as _json
        w = (Will.query.filter_by(client_id=client_id)
             .order_by(Will.created_at.desc()).first())
        if w and w.step6_data:
            raw = (_json.loads(w.step6_data) or {}).get('_raw_forward_text', '') or ''
    except Exception:
        raw = ''
    return (summary + '\n\n' + raw).strip()


def _outsider_eliminated_names(client_id: str) -> set:
    """Return the set of full-names (UPPER) that the role_matcher's
    outsider-elimination identified as candidates for executor / witness /
    etc. (§10x.21). Used to give those ICs a baseline confidence even
    when their name doesn't appear in the message."""
    try:
        from services.role_matcher import (
            extract_role_mentions, find_unassigned_ic_candidates,
            match_role_to_candidates,
        )
        mentions = extract_role_mentions(client_id) or []
        cands = find_unassigned_ic_candidates(client_id) or []
        out = set()
        for m in mentions:
            ranked = match_role_to_candidates(m, cands, client_id=client_id)
            for c, conf, _reason in ranked:
                if conf == 'high':
                    nm = (c.get('full_name') or '').strip().upper()
                    if nm:
                        out.add(nm)
        return out
    except Exception:
        return set()


def skip_pending_ic_document(client_id: str) -> Optional[Dict[str, Any]]:
    """🔥 §10x.31 — Skip is now a NO-OP that just acknowledges the user
    saw the card. The same IC is shown AGAIN on the next turn until the
    user either:
        (a) confirms a relationship  ✓ Yes — <role>
        (b) deletes the IC           🗑 Delete

    Earlier behaviour wrote `_chat_skipped=True` and dismissed the IC
    forever, which let users accidentally drop family members from the
    will (one mis-click → that person is gone). Per user instruction
    (May 2026): "if skip, show back again until user select delete.
    then only go to next step".
    """
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
    # 🔒 §10x.31 — DO NOT set _chat_skipped. Same IC shows again next turn.
    # We DO bump a "skip count" so the chat can offer extra hints after
    # repeated skips ("Are you sure? Click Delete if this is the wrong upload").
    ex['_skip_count'] = int(ex.get('_skip_count') or 0) + 1
    doc.extracted_data = _json.dumps(ex)
    # 🔒 §10x.31 — Do NOT skip duplicate ICs either. Skip = no-op.
    # The user must Confirm or Delete to advance.
    label = (ex.get('full_name') or target.get('original_filename', 'this IC')).strip()
    return {'name': label, 'action': 'skipped',
             'document_id': target['document_id'],
             'skip_count': ex.get('_skip_count', 1)}


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

    🔥 §10x.124 — delegates to services.role_registry.parse_role_from_freetext
    so HYPHENATED, SPACED, CAMEL-CASE and NO-SEPARATOR variants of the
    same role all resolve consistently. The legacy keyword table at
    line 72 only had SPACED forms ('sister in law'), so a button value
    like 'sister-in-law' (hyphenated) silently failed to match and the
    save handler returned None → walker re-rendered the same card
    forever. The role_registry is the single source of truth.
    """
    if not text:
        return None
    try:
        from services.role_registry import parse_role_from_freetext
        return parse_role_from_freetext(text)
    except Exception:
        # Defensive fallback: legacy keyword table
        t = ' ' + text.lower() + ' '
        for kw in sorted(RELATIONSHIP_KEYWORDS, key=len, reverse=True):
            if (' ' + kw + ' ') in t:
                return RELATIONSHIP_KEYWORDS[kw]
        return None


# ---------------------------------------------------------------------------
# 🔥🔥🔥 §10x.227 — Extract testator/executor address from forward text
# ---------------------------------------------------------------------------
def extract_address_for_person_from_text(text: str, name: str = '',
                                          nric: str = '') -> str:
    """Find an address clause `of <ADDRESS>` near the given person's
    name or NRIC in the WhatsApp/email forward text.

    Common patterns the WhatsApp forward uses:

        "My name is KOID BENG SUN, NRIC 631204-07-5743, of NO.600,
         JALAN MUTIARA HIJAU 17, TAMAN MUTIARA HIJAU, 81000 KULAI, JOHOR."

        "I, JANE TAN (NRIC 800101-01-1234) of 12 Jalan Bahagia,
         Petaling Jaya, hereby ..."

        "Executor: MARY SMITH of 5 Jalan Tun Razak, Kuala Lumpur."

    Pattern: <NAME>...NRIC <id> of <ADDRESS> [.|\\n]   (any order)
    or       <NAME>...of <ADDRESS> [.|\\n]
    The address terminates at the first `.`, `\\n`, or `;`.

    Returns a cleaned single-line address string, or '' when not found.
    """
    if not text or not (name or nric):
        return ''
    # Build anchor candidates: (a) exact name (case-insens),
    # (b) canonical NRIC digits.
    anchors: list = []
    if name:
        anchors.append(re.escape(name.strip()))
    if nric:
        digits = re.sub(r'\D', '', nric)
        if len(digits) == 12:
            anchors.append(re.escape(f'{digits[:6]}-{digits[6:8]}-{digits[8:]}'))
            anchors.append(re.escape(digits))
    if not anchors:
        return ''

    # Search a generous window around each anchor for `of <ADDRESS>`.
    # The clause terminator is a SENTENCE-ENDING boundary: `. ` (period
    # followed by whitespace), `\n`, or `;`. A bare `.` is allowed
    # because Malaysian addresses contain `NO.600`, `H.S.(D)`, etc.
    # Greedy match capped at 250 chars; we then trim at the terminator.
    addr_inner = r'(?P<addr>(?:[^.\n;]|\.(?!\s))+)'
    for anchor in anchors:
        # Pattern A: anchor ... of <ADDR>
        pat = re.compile(
            anchor + r'[\s\S]{0,200}?\bof\s+' + addr_inner,
            re.IGNORECASE)
        m = pat.search(text)
        if m:
            raw = (m.group('addr') or '').strip()
            cleaned = _clean_address_clause(raw)
            if _looks_like_address(cleaned):
                return cleaned[:250]
        # Pattern B (reverse): <ADDR> ... anchor
        # Less common but covers "of <ADDR>, my name is <NAME>".
        pat_rev = re.compile(
            r'\bof\s+(?P<addr>(?:[^.\n;]|\.(?!\s))+?)[,\.\s\n]{1,4}[\s\S]{0,80}?' + anchor,
            re.IGNORECASE)
        m = pat_rev.search(text)
        if m:
            raw = (m.group('addr') or '').strip()
            cleaned = _clean_address_clause(raw)
            if _looks_like_address(cleaned):
                return cleaned[:250]
    return ''


def _clean_address_clause(raw: str) -> str:
    """Strip noise from a captured address clause."""
    if not raw:
        return ''
    # Collapse whitespace + newlines into single spaces (§10x.226 also
    # strips embedded newlines from any address coming back through here).
    s = re.sub(r'\s+', ' ', raw).strip()
    # Drop trailing connectors: hereby, and, who, that, has, holding, ...
    s = re.sub(
        r'(?i)\s+(?:hereby|and|who|whose|that|has|holding|appoint(?:s|ed)?|wishes?|do(?:es)?)\b.*$',
        '', s)
    # Drop trailing comma/period/punctuation noise
    s = s.rstrip(',;:. ')
    return s


def _looks_like_address(s: str) -> bool:
    """Heuristic check: address must have postcode OR a street keyword."""
    if not s or len(s) < 8:
        return False
    if re.search(r'\b\d{5}\b', s):
        return True
    addr_kw = ('jalan ', 'lorong ', 'taman ', 'persiaran', 'lot ',
              'no.', 'no ', 'kampung ', 'kg ', 'jln ', 'apartment',
              'condominium', 'unit ', 'block ')
    sl = s.lower()
    return any(kw in sl for kw in addr_kw)


def clean_person_address(addr: str) -> str:
    """🔥 §10x.226 — Normalise a Person.address value.

    OCR'd IC addresses contain literal newlines (multi-line IC text dump).
    Will-generation clauses want a single comma-separated line. Always
    collapse whitespace runs to single spaces.
    """
    if not addr:
        return ''
    return re.sub(r'\s+', ' ', addr).strip()
