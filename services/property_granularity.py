"""§10x.159 — Property granularity classifier + level-aware field pull.

Malaysian probate docs come at DIFFERENT GRANULARITIES:
  • Hakmilik Strata (Geran Strata) — the unit's OWN strata sub-title.
    Carries: strata_title_no + parcel_no + master_lot + mukim + daerah +
    negeri + sole proprietor (or joint). This is THE doc probate needs.
  • Hakmilik Induk (Master Geran) — the parent NLC parcel that contains
    a strata building. Carries: master_lot + mukim + daerah + negeri
    + master proprietors (often developer + parcel buyers). Does NOT
    carry strata sub-title No. — that lives on each individual strata
    title issued from this master.
  • Cukai Tanah (property tax assessment) — typically issued at
    master-parcel level, NOT per strata sub-parcel. Address is the
    master parcel's; lot is the master lot. Owners include the developer
    plus the strata-parcel buyers because the master title hasn't fully
    sub-divided yet.
  • SPA / Charge / Loan — describes a SECURED PROPERTY in their schedule
    section, usually at sub-parcel level (the unit being sold/charged).
    Address fields elsewhere on these docs are PARTY residences, not
    subject property.
  • JMB statement / strata maintenance — building-level, no NLC fields.

The matcher MUST recognise these levels and pull only the fields that
are AUTHORITATIVE at the doc's level. A master-parcel Cukai is RELEVANT
to a strata unit gift (confirms ownership context, mukim, daerah) but
its `lot_number` is the MASTER lot — pulling it into the unit's
`lot_number` field would be a level-mismatch error.

This module exposes:
  • classify_doc_level(extracted: dict) -> 'strata' | 'master' |
                                          'sub_parcel' | 'unknown'
  • PROBATE_FIELDS_BY_LEVEL — what fields a doc at each level can fill
  • PROBATE_REQUIRED_FOR_UNIT — what every strata/landed unit needs
  • level_compatible_fields(doc_level: str, gift_level: str) -> set
"""
import re
from typing import Dict, Any, Set, Optional, List


# ── Granularity classification ──────────────────────────────────────────────

# Strata title sub-token pattern: '564662/M1C/30/710' (master/block/floor/parcel)
_STRATA_TITLE_RE = re.compile(r'/[A-Z]?\d', re.IGNORECASE)

# Words that strongly indicate strata-level extraction
_STRATA_WORDS = (
    'strata', 'parcel no', 'parcel.no', 'parcel:',
    'level', 'storey', 'tingkat',
    'block', 'tower', 'menara',
    'unit b-', 'unit c-', 'unit a-',
    'apartment', 'condo', 'pangsapuri', 'kondominium',
)

# Developer corporate suffixes — used for owner-pattern signature
_DEV_SUFFIX_RE = re.compile(
    r'\b(?:berhad|bhd|sdn(?:\s*bhd)?|investments?|holdings?|'
    r'developments?|properties|estates?|corporation|corp|ltd|limited|'
    r'realty|land\s*&\s*development|properties\s+sdn)\b',
    re.IGNORECASE
)

# Conservatively count likely individual names (ALL-CAPS multi-word
# tokens like "KOID BENG SUN", "CHAI MEI FUN")
_INDIVIDUAL_NAME_RE = re.compile(
    r'\b[A-Z][A-Z\']{1,}(?:\s+[A-Z][A-Z\']{1,}){1,}\b'
)

# Cukai-only signature (assessment forms)
_CUKAI_HEADER_RE = re.compile(
    r'cukai\s+(?:tanah|harta|pintu|taksiran)|kadar\s+taksiran|'
    r'assessment\s+(?:no|number)|akaun\s+cukai',
    re.IGNORECASE
)


def classify_doc_level(extracted: Dict[str, Any],
                       category: str = '') -> str:
    """Return one of: 'strata' | 'master' | 'sub_parcel' | 'unknown'.

    Hard rules (in order):
      1. Title contains strata sub-token (`/M1C/30/710`) → 'strata'
      2. property_description / category mentions strata words → 'strata'
      3. Owner pattern is "developer + 2+ individuals" AND no strata
         indicator → 'master' (parent parcel still co-owned with developer)
      4. Cukai header present → 'master' (Cukai is master-level by default
         unless explicit strata sub-parcel printed)
      5. Title looks like a plain Geran (5-7 digits, no slashes) AND
         doc_category is property_title → 'sub_parcel' (landed Geran)
      6. Anything else → 'unknown'
    """
    if not isinstance(extracted, dict):
        return 'unknown'

    title = str(extracted.get('title_number') or '').strip()
    desc  = str(extracted.get('property_description') or '').strip()
    owners_raw = (extracted.get('owner_name')
                  or extracted.get('owner_names')
                  or '')
    if isinstance(owners_raw, list):
        owners_str = ' & '.join(str(x) for x in owners_raw)
    else:
        owners_str = str(owners_raw or '')

    cat_low  = (category or extracted.get('document_type') or '').lower()
    desc_low = desc.lower()

    # 1) Strata title sub-token
    if title and _STRATA_TITLE_RE.search(title):
        return 'strata'

    # 2) Strata words in description / category
    if any(w in desc_low for w in _STRATA_WORDS):
        return 'strata'
    if 'strata' in cat_low:
        return 'strata'

    # 3) Owner pattern: developer + 2+ individuals
    has_developer  = bool(_DEV_SUFFIX_RE.search(owners_str))
    n_individuals  = len(_INDIVIDUAL_NAME_RE.findall(owners_str))
    if has_developer and n_individuals >= 1:
        return 'master'

    # 4) Cukai header → master (unless strata indicator above already fired)
    if cat_low in ('property_tax', 'cukai_tanah', 'cukai_pintu'):
        return 'master'
    if _CUKAI_HEADER_RE.search(desc):
        return 'master'

    # 5) Plain Geran shape + property_title category → sub_parcel (landed)
    if (cat_low in ('property_title', 'title')
            and re.fullmatch(r'\d{4,7}', re.sub(r'\D', '', title))):
        return 'sub_parcel'

    # 6) SPA / Charge / Loan / Transfer with a non-empty title or lot
    # describe a SPECIFIC PROPERTY in their schedule section. Default
    # them to 'sub_parcel' level — their title/lot is authoritative for
    # the unit (whether landed or strata). If they were truly at master
    # level, rule 3 (developer + individuals) would have caught them.
    if (cat_low in ('property_spa', 'loan_agreement', 'property_transfer',
                     'charge', 'spa', 'memorandum_of_transfer')
            and (title or extracted.get('lot_number'))):
        return 'sub_parcel'

    return 'unknown'


# ── Field whitelist by level ────────────────────────────────────────────────

# Fields a doc at this level CAN authoritatively fill on the will gift.
# Everything else extracted from the doc is "supporting evidence" only —
# matchable for verification, not used for auto-fill.
PROBATE_FIELDS_BY_LEVEL: Dict[str, Set[str]] = {
    'strata': {
        'title_number',      # the unit's strata title No.
        'lot_number',        # master lot the unit sits on
        'mukim', 'daerah', 'negeri',
        'owner_name', 'co_owners',
        'encumbrance', 'encumbrance_status',
    },
    'sub_parcel': {           # landed Geran (no strata)
        'title_number',
        'lot_number',
        'mukim', 'daerah', 'negeri',
        'owner_name', 'co_owners',
        'encumbrance', 'encumbrance_status',
    },
    'master': {               # Cukai Tanah / Hakmilik Induk
        'mukim', 'daerah', 'negeri',
        'co_owners',          # developer + co-owners shown
        'master_lot',         # NEW — separate field for master-parcel lot
        # Explicitly NOT: title_number (master title ≠ unit's strata title),
        # NOT: lot_number on the unit's gift (master_lot ≠ unit's parcel)
    },
    'unknown': {
        # Conservative — only geo-context, never identifiers
        'mukim', 'daerah', 'negeri',
    },
}

# What probate (Borang 14A / Deed of Transmission) requires for any
# property gift. The wizard banner uses this to flag missing fields.
PROBATE_REQUIRED_FOR_UNIT: Set[str] = {
    'title_type', 'title_number', 'lot_number',
    'mukim', 'daerah', 'negeri',
    'ownership_type', 'testator_share',
}


def level_compatible_fields(doc_level: str,
                              wanted_fields: Optional[Set[str]] = None
                              ) -> Set[str]:
    """Intersection of (what the doc can fill) and (what's wanted).
    If wanted_fields is None, returns all fields the doc level can fill.
    """
    can_fill = PROBATE_FIELDS_BY_LEVEL.get(doc_level,
                                           PROBATE_FIELDS_BY_LEVEL['unknown'])
    if wanted_fields is None:
        return set(can_fill)
    return set(can_fill) & set(wanted_fields)


def evidence_summary(doc_level: str,
                     ai_index: int,
                     fields_pulled: Set[str]) -> Dict[str, Any]:
    """Build a small dict the wizard can render under the gift card
    explaining what this doc contributed."""
    role_label = {
        'strata':     'authoritative (strata sub-title)',
        'sub_parcel': 'authoritative (landed Geran)',
        'master':     'supporting (master parcel — strata sub-title still required)',
        'unknown':    'supporting (level unclear)',
    }.get(doc_level, 'supporting')
    return {
        'ai_index':       ai_index,
        'doc_level':      doc_level,
        'role_label':     role_label,
        'fields_pulled':  sorted(fields_pulled),
    }


__all__ = [
    'classify_doc_level',
    'level_compatible_fields',
    'evidence_summary',
    'PROBATE_FIELDS_BY_LEVEL',
    'PROBATE_REQUIRED_FOR_UNIT',
]
