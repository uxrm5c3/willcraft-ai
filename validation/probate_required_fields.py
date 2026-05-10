"""🔥 §10x.193 — Canonical schema of fields REQUIRED to satisfy:
  - National Land Code 1965 (NLC) — for property identification
  - Probate & Administration Act 1959
  - Land Registration Form 14A (Memorandum of Transfer)
  - Section 346 (Small Estates Distribution Act 1955)
  - Sijil Faraid (for Muslim deceased)
  - Insurance Act 1996 / Singapore Insurance Act 1966 — nomination handling

Used by validation/legal_rules.py to BLOCK will-generation when any
probate-critical field is missing or contains placeholder values like
'TBD' / 'N/A' / 'TODO'.

Used by ai/chat_planner.py + wizard step6 amber banner to flag missing
fields visibly to the user before save.

References:
- NLC s.5 (interpretation): "title" includes register document of title
  and includes any separate register document of title issued under any
  previous land law
- NLC s.207 + s.215: Mukim Register / Bandar / Pekan Register required
- NLC s.292 (Form 14A) — required fields:
    Title No., Lot No. / PT No. / PTD No., Mukim/Bandar/Pekan, Daerah,
    Negeri, Class of land, Tenure (freehold/leasehold + lease term),
    Area (hectares/sq.m), Restrictions/conditions, Description of land
- Probate & Administration Act 1959 s.49(1)+Sched: assets list must
  identify each immovable property with full title + description + value
- s.346 small estates: same as NLC s.292 PLUS the deceased's NRIC, DOB,
  date of death, marital status, religion (for Faraid), beneficiary
  details
- Insurance Act 1996 s.130 / Sg. Insurance Act 1966 s.49L: a NOMINATED
  insurance policy bypasses the will entirely; only failed nominations
  fall into the estate. Will clause must use NOMINATION-FALLBACK wording
  not direct bequest (per §10x.182 / T-28).
"""
from __future__ import annotations

from typing import Dict, List

# ── PROPERTY GIFTS — PROBATE-REQUIRED FIELDS ─────────────────────────────
PROPERTY_REQUIRED = [
    # Identification of the parcel (NLC s.5 + s.207)
    'property_address',     # full street address
    'postcode',             # Malaysian 5-digit postcode (helps geo-validate)
    'city',                 # Bandar / town / pekan
    'state',                # Negeri (one of 13 + 3 federal territories)
    'country',              # Malaysia / Singapore (default Malaysia)
    # Land registry identifiers (NLC s.292 Form 14A)
    'title_type',           # Geran / Hakmilik / HSD / HSM / Pajakan / Strata
    'title_number',         # Geran No. / Hakmilik No. / HSD No.
    'lot_number',           # Lot No. / PT No. / PTD No.
    'mukim',                # Mukim / Bandar / Pekan
    'daerah',               # District (Daerah)
    'negeri',               # State (Negeri) — duplicate of `state` for legacy
    # Ownership (Form 14A + s.346)
    'ownership_type',       # sole / joint
    'testator_share',       # 1/1 (sole) or 1/N (joint with N parties)
    # Encumbrance status (NLC s.281 charges, s.323 caveats)
    'encumbrance_status',   # clean / encumbered (loan/charge/caveat)
]

# OPTIONAL but Phek/Form-14A best practice
PROPERTY_OPTIONAL = [
    'co_owners',            # names of joint owners (when joint)
    'tenure',               # freehold / leasehold (+ lease term)
    'land_area',            # in m² / hectares (Form 14A)
    'land_class',           # Bandar / Kampung / Tanah (NLC s.52)
    'restrictions',         # restrictions in interest (NLC s.207)
    'building_description', # 'Single Storey Medium Low Cost Shop' etc
]

# ── BANK ACCOUNT GIFTS ────────────────────────────────────────────────────
BANK_REQUIRED = [
    'bank_name',            # canonical (per §10x.149)
    'account_number',       # full account number
    'country',              # MY / SG (per §10x.152 ambiguous-brand)
]

BANK_OPTIONAL = [
    'account_type',         # Current / Savings / Plus / FD
    'currency',             # MYR / SGD / USD
    'branch',               # branch name (for foreign accounts)
]

# ── INSURANCE POLICY GIFTS ────────────────────────────────────────────────
# Per Insurance Act 1996 s.130 (MY) / Singapore Insurance Act 1966 s.49L
# nominated policies BYPASS the will. Will clause is a FALLBACK.
INSURANCE_REQUIRED = [
    'insurer',              # canonical (per §10x.149)
    'policy_number',        # policy number
    'country',              # MY / SG
    # 'nomination_status': 'nominated' | 'no_nomination' | 'unknown'
    # — if nominated, will clause should be fallback wording (§10x.182)
]

INSURANCE_OPTIONAL = [
    'policy_type',          # life / health / endowment / takaful
    'sum_assured',          # for valuation / probate inventory
    'nominee_name',         # for nominated policies
]

# ── PERSON (testator / executor / beneficiary / guardian / trustee) ───
PERSON_REQUIRED = [
    'full_name',            # uppercase per Phek
    'nric_passport',        # MyKad / passport number
    'address',              # residential address
    'relationship',         # to testator (for non-testator persons)
]

PERSON_OPTIONAL = [
    'date_of_birth',        # auto-derived from NRIC if Malaysian
    'gender',               # auto-derived from NRIC last digit
    'nationality',          # default Malaysian
    'occupation',           # for testator only (Phek includes when known)
    'email',                # contact for executor
    'phone',                # contact for executor
]

# Testator additionally needs:
TESTATOR_REQUIRED = PERSON_REQUIRED + ['marital_status', 'religion']
# religion needed for Faraid distribution if Muslim


# ── PLACEHOLDER VALUES that count as MISSING ──────────────────────────────
# When a field contains one of these, treat as empty (probate-blocking).
PLACEHOLDER_VALUES = {
    '', 'TBD', 'TODO', 'N/A', 'NA', 'NIL', 'PENDING', 'UNKNOWN',
    '???', '-', '--', 'TBC', 'TO BE CONFIRMED', 'PLACEHOLDER',
    'XXX', 'XXXX', '[REQUIRED]', '[ADDRESS REQUIRED]', '[TITLE REQUIRED]',
}


def is_missing(value) -> bool:
    """True if `value` should be treated as missing for probate purposes.
    Catches None, empty string, whitespace-only, AND known placeholders."""
    if value is None:
        return True
    if not isinstance(value, str):
        # Non-string truthy values (lists, ints, dicts) — check via len/bool
        try:
            return len(value) == 0
        except TypeError:
            return False
    s = value.strip().upper()
    if not s:
        return True
    return s in PLACEHOLDER_VALUES


def missing_fields_for_property(pd: dict) -> List[str]:
    """Return list of probate-required PROPERTY fields that are missing.
    Each entry is a human-readable label (used in the wizard amber banner)."""
    if not isinstance(pd, dict):
        return PROPERTY_REQUIRED
    out = []
    label_map = {
        'property_address': 'street address',
        'postcode': 'postcode',
        'city': 'city',
        'state': 'state',
        'country': 'country',
        'title_type': 'title type (Geran/HSD/Hakmilik)',
        'title_number': 'title No.',
        'lot_number': 'lot No. / PT / PTD',
        'mukim': 'mukim',
        'daerah': 'daerah (district)',
        'negeri': 'negeri',
        'ownership_type': 'ownership type (sole/joint)',
        'testator_share': "testator's share",
        'encumbrance_status': 'encumbrance status',
    }
    for key in PROPERTY_REQUIRED:
        # Mukim is also stored as 'bandar_pekan' in the legacy schema
        if key == 'mukim':
            if is_missing(pd.get('mukim')) and is_missing(pd.get('bandar_pekan')):
                out.append(label_map[key])
        # Negeri/state — accept either field
        elif key == 'negeri':
            if is_missing(pd.get('negeri')) and is_missing(pd.get('state')):
                out.append(label_map[key])
        elif key == 'state':
            if is_missing(pd.get('state')) and is_missing(pd.get('negeri')):
                out.append(label_map[key])
        else:
            if is_missing(pd.get(key)):
                out.append(label_map.get(key, key))
    # Dedupe while preserving order
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def missing_fields_for_bank(fd: dict) -> List[str]:
    """Schema accepts BOTH the wizard's `financial_details.institution`
    AND the chat's `bank_name` field. Same for account_number / country."""
    if not isinstance(fd, dict):
        return BANK_REQUIRED
    out = []
    # bank_name: check both 'bank_name' and legacy 'institution'
    if is_missing(fd.get('bank_name')) and is_missing(fd.get('institution')):
        out.append('bank name')
    if is_missing(fd.get('account_number')):
        out.append('account No.')
    if is_missing(fd.get('country')):
        out.append('country (MY/SG)')
    return out


def missing_fields_for_insurance(fd: dict) -> List[str]:
    """Schema accepts BOTH `financial_details.institution` and `insurer`."""
    if not isinstance(fd, dict):
        return INSURANCE_REQUIRED
    out = []
    if is_missing(fd.get('insurer')) and is_missing(fd.get('institution')):
        out.append('insurer')
    if is_missing(fd.get('policy_number')) and is_missing(fd.get('account_number')):
        out.append('policy No.')
    if is_missing(fd.get('country')):
        out.append('country (MY/SG)')
    return out


def missing_fields_for_person(p: dict, is_testator: bool = False) -> List[str]:
    if not isinstance(p, dict):
        return TESTATOR_REQUIRED if is_testator else PERSON_REQUIRED
    out = []
    label_map = {
        'full_name': 'full name', 'nric_passport': 'NRIC / passport',
        'address': 'address', 'relationship': 'relationship',
        'marital_status': 'marital status', 'religion': 'religion (for Faraid)',
    }
    fields = TESTATOR_REQUIRED if is_testator else PERSON_REQUIRED
    for key in fields:
        if is_missing(p.get(key)):
            out.append(label_map.get(key, key))
    return out
