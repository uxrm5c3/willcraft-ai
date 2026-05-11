"""🔥 §10x.144 — Suggest values for missing wizard fields.

Uses cheap deterministic sources (no LLM, no web search per call) to
pre-fill the Step 10 quick-fix banner inputs:

  - Financial gifts: institution → country via financial_institutions
    registry. Bare-name ambiguous brands (AIA, HSBC) return both options.
  - Property gifts: postcode/city extraction from address regex; country
    default Malaysia (most common); state via postcode → state lookup
    when known.

Returns one suggestion per field. The banner pre-selects this value
(amber badge "✨ AI suggested") and user can override + Save.

Web search via services.web_property_clues is intentionally NOT called
here because it's $0.01/call and we need it free + fast for the wizard
render. The banner can fall back to "Open in Step 6" for fields that
can't be auto-suggested.
"""
from __future__ import annotations
import re
from typing import Optional, List, Dict, Any

# Postcode → State / Daerah lookup for the most common Malaysian
# postcodes. Sourced from Pos Malaysia public data.
# Format: postcode_prefix → (state, common_city)
# This is a partial table — extend per common usage. Unknown → returns None.
_POSTCODE_PREFIX_TO_STATE: Dict[str, str] = {
    # Johor
    '79': 'Johor', '80': 'Johor', '81': 'Johor', '82': 'Johor', '83': 'Johor',
    '84': 'Johor', '85': 'Johor', '86': 'Johor',
    # Kedah
    '05': 'Kedah', '06': 'Kedah', '07': 'Kedah', '08': 'Kedah', '09': 'Kedah',
    # Kelantan
    '15': 'Kelantan', '16': 'Kelantan', '17': 'Kelantan', '18': 'Kelantan',
    # Melaka
    '75': 'Melaka', '76': 'Melaka', '77': 'Melaka', '78': 'Melaka',
    # Negeri Sembilan
    '70': 'Negeri Sembilan', '71': 'Negeri Sembilan', '72': 'Negeri Sembilan',
    '73': 'Negeri Sembilan',
    # Pahang
    '25': 'Pahang', '26': 'Pahang', '27': 'Pahang', '28': 'Pahang',
    '36': 'Pahang', '39': 'Pahang', '49': 'Pahang', '69': 'Pahang',
    # Perak
    '30': 'Perak', '31': 'Perak', '32': 'Perak', '33': 'Perak', '34': 'Perak',
    '35': 'Perak', '36': 'Perak',
    # Perlis
    '01': 'Perlis', '02': 'Perlis',
    # Pulau Pinang
    '10': 'Pulau Pinang', '11': 'Pulau Pinang', '12': 'Pulau Pinang',
    '13': 'Pulau Pinang', '14': 'Pulau Pinang',
    # Selangor
    '40': 'Selangor', '41': 'Selangor', '42': 'Selangor', '43': 'Selangor',
    '44': 'Selangor', '45': 'Selangor', '46': 'Selangor', '47': 'Selangor',
    '48': 'Selangor', '63': 'Selangor', '64': 'Selangor', '68': 'Selangor',
    # Terengganu
    '20': 'Terengganu', '21': 'Terengganu', '22': 'Terengganu',
    '23': 'Terengganu', '24': 'Terengganu',
    # KL
    '50': 'Kuala Lumpur', '51': 'Kuala Lumpur', '52': 'Kuala Lumpur',
    '53': 'Kuala Lumpur', '54': 'Kuala Lumpur', '55': 'Kuala Lumpur',
    '56': 'Kuala Lumpur', '57': 'Kuala Lumpur', '58': 'Kuala Lumpur',
    '59': 'Kuala Lumpur', '60': 'Kuala Lumpur',
    # Putrajaya
    '62': 'Putrajaya',
    # Labuan
    '87': 'Labuan',
    # Sabah
    '88': 'Sabah', '89': 'Sabah', '90': 'Sabah', '91': 'Sabah',
    # Sarawak
    '93': 'Sarawak', '94': 'Sarawak', '95': 'Sarawak', '96': 'Sarawak',
    '97': 'Sarawak', '98': 'Sarawak',
}


def suggest_property_field(pi: Dict[str, Any], field: str) -> Optional[Dict[str, Any]]:
    """Suggest a value for a missing PROPERTY field.

    Returns:
      {value: <str>, source: <str>, options?: [<str>, ...]} or None.

    `source` is a short human-readable provenance like "from address",
    "Malaysian default", "extracted from postcode 81750 → Johor".
    `options` (optional) means the suggester is uncertain — show
    multiple choices.
    """
    if not isinstance(pi, dict):
        return None
    addr = (pi.get('property_address') or '').strip()

    if field == 'country':
        # Default Malaysia unless address suggests Singapore/elsewhere
        if re.search(r'\bsingapore\b', addr, re.IGNORECASE):
            return {'value': 'Singapore', 'source': 'address mentions Singapore'}
        if re.search(r'\b(jakarta|indonesia|bangkok|thailand|hong kong)\b',
                      addr, re.IGNORECASE):
            return {'value': 'Other', 'source': 'foreign address detected'}
        return {'value': 'Malaysia', 'source': 'Malaysian default (most common)'}

    if field == 'postcode':
        m = re.search(r'\b(\d{5})\b', addr)
        if m:
            return {'value': m.group(1), 'source': 'extracted from address'}
        return None

    if field == 'city':
        # Extract token before/after postcode. Sample: "Seri Alam Masai,
        # 81750 Masai, Johor" → city = "Masai"
        m = re.search(r'\b\d{5}\s+([A-Za-z][\w\s]{1,30}?)[,\s]+', addr + ',')
        if m:
            return {'value': m.group(1).strip(), 'source': 'extracted from address'}
        # Try city before postcode: "Seri Alam Masai, 81750 ..." → "Masai"
        m = re.search(r'([A-Za-z][\w\s]{1,30}?),\s*\d{5}', addr)
        if m:
            return {'value': m.group(1).strip(), 'source': 'extracted from address'}
        return None

    if field == 'state':
        m = re.search(r'\b(\d{5})\b', addr)
        if m:
            prefix = m.group(1)[:2]
            st = _POSTCODE_PREFIX_TO_STATE.get(prefix)
            if st:
                return {'value': st, 'source': f'postcode {m.group(1)} → {st}'}
        # Try address tail: ", JOHOR" / ", Selangor"
        for st in ['Johor', 'Selangor', 'Pulau Pinang', 'Penang',
                   'Kuala Lumpur', 'Kedah', 'Melaka', 'Negeri Sembilan',
                   'Pahang', 'Perak', 'Perlis', 'Sabah', 'Sarawak',
                   'Terengganu', 'Kelantan', 'Putrajaya', 'Labuan']:
            if re.search(rf'\b{re.escape(st)}\b', addr, re.IGNORECASE):
                # Normalise Penang → Pulau Pinang per the dropdown options
                return {'value': 'Pulau Pinang' if st == 'Penang' else st,
                        'source': 'extracted from address'}
        return None

    if field == 'negeri':
        # Same as state
        return suggest_property_field(pi, 'state')

    if field == 'ownership_type':
        # If co_owners list non-empty → joint. Else sole (default).
        if (pi.get('co_owners') or []):
            return {'value': 'joint', 'source': 'co_owners present in saved data'}
        return {'value': 'sole', 'source': 'default (no co-owners detected)'}

    if field == 'testator_share':
        # If joint detected → 1/2 default. Else 1/1.
        if (pi.get('co_owners') or []):
            return {'value': '1/2', 'source': 'joint owner default'}
        return {'value': '1/1', 'source': 'sole owner default'}

    if field == 'encumbrance_status':
        # Default to Clean (most common). User overrides if mortgaged.
        return {'value': 'clean', 'source': 'default (most properties unencumbered)'}

    if field == 'title_type':
        tn = (pi.get('title_number') or '').strip()
        if tn:
            tn_up = tn.upper()
            if '/' in tn_up or 'M1' in tn_up or 'STRATA' in tn_up:
                return {'value': 'Strata Title Geran',
                        'source': f'title pattern "{tn[:30]}" suggests strata'}
            if tn_up.startswith(('HSD', 'HS(D)', 'H.S.(D)')):
                return {'value': 'HSD', 'source': 'title number prefix'}
            if tn_up.startswith(('HSM', 'HS(M)', 'H.S.(M)')):
                return {'value': 'HSM', 'source': 'title number prefix'}
        # Fallback heuristic from address
        if re.search(r'\b(condominium|condo|pangsapuri|apartment|unit\s+\w+\-)',
                      addr, re.IGNORECASE):
            return {'value': 'Strata Title Geran',
                    'source': 'condo/apartment address pattern'}
        return {'value': 'Geran',
                'source': 'most common landed title type'}

    return None


def suggest_financial_field(fd: Dict[str, Any], field: str,
                              kind: str = 'bank') -> Optional[Dict[str, Any]]:
    """Suggest a value for a missing FINANCIAL field.

    Most useful: country lookup via financial_institutions registry.
    AIA / HSBC / etc. ambiguous brands return both MY+SG as options.
    """
    if not isinstance(fd, dict):
        return None
    institution = (fd.get('institution') or fd.get('insurer')
                   or fd.get('bank_name') or '').strip()

    if field == 'country':
        if not institution:
            return None
        try:
            from services.financial_institutions import (match_institution,
                                                            ALL_INSTITUTIONS)
        except Exception:
            return None

        # Direct match (returns dict or None)
        try:
            m = match_institution(institution, kind=kind)
        except Exception:
            m = None

        if m and m.get('country'):
            country_label = {'MY': 'Malaysia', 'SG': 'Singapore'}.get(
                m['country'], m['country'])
            return {'value': country_label,
                    'source': f'{m.get("canonical", institution)} is registered in {country_label}'}

        # Fuzzy: check if any institution's canonical/alias contains the
        # institution name (case-insensitive). For ambiguous brands like
        # bare "AIA" that don't have a single match, return all options.
        inst_lower = institution.lower()
        matches = []
        for fi in ALL_INSTITUTIONS:
            for candidate in [fi.canonical] + fi.aliases:
                if (inst_lower in candidate.lower()
                        or candidate.lower() in inst_lower):
                    matches.append(fi)
                    break
        if matches:
            countries = sorted(set(
                {'MY': 'Malaysia', 'SG': 'Singapore'}.get(m.country, m.country)
                for m in matches
            ))
            if len(countries) == 1:
                return {'value': countries[0],
                        'source': f'{institution!r} matched {matches[0].canonical}'}
            else:
                # Ambiguous: present both options. Prefer most common as default.
                return {'value': countries[0],
                        'source': f'{institution!r} is ambiguous',
                        'options': countries}
        return None

    return None


def suggest_title_or_lot_from_docs(gift: Dict[str, Any], field: str,
                                     client_id: str) -> Optional[Dict[str, Any]]:
    """🔥 §10x.145 — Cross-reference uploaded Documents for title/lot.

    When a gift has NO direct doc binding (e.g. B-05-11 H3) but the user
    uploaded property docs that mention the same building/street, scan
    them and return candidate title/lot values. Catches OCR-misaddressed
    docs that the binding pipeline missed.
    """
    if not client_id or field not in ('title_number', 'lot_number'):
        return None
    try:
        from app import db
        from database import Document
        import json as _json
    except Exception:
        return None

    pi = gift.get('property_info') or gift.get('property_details') or {}
    addr = (pi.get('property_address') or gift.get('address') or '').lower()
    label = (gift.get('label') or pi.get('property_address') or '').lower()
    addr_tokens: set = set()
    # Extract distinctive multi-char tokens from the address (skip common words)
    SKIP = {'jalan', 'taman', 'bandar', 'condominium', 'unit', 'no',
            'malaysia', 'singapore', 'johor', 'bahru', 'persiaran',
            'mukim', 'daerah', 'negeri', 'state', 'block'}
    for t in re.findall(r'[a-z]{4,}', addr + ' ' + label):
        if t in SKIP:
            continue
        addr_tokens.add(t)

    if not addr_tokens:
        return None

    own_doc_id = gift.get('document_id') or ''
    candidates = []
    try:
        docs = Document.query.filter_by(client_id=client_id).filter(
            Document.category.in_(['property_title', 'property_spa',
                                    'property_tax', 'property_transfer',
                                    'loan_agreement'])).all()
    except Exception:
        return None
    for d in docs:
        if d.id == own_doc_id:
            continue
        try:
            ex = _json.loads(d.extracted_data or '{}')
        except Exception:
            continue
        if not isinstance(ex, dict):
            continue
        v = (ex.get(field) or '').strip()
        if not v or v.upper() in ('UNREADABLE', 'CANNOT READ', 'NONE'):
            continue
        if re.match(r'^\s*(folio|vol\.?|page)\s*\d*\s*$', v.lower()):
            continue
        # Score: count overlapping distinctive tokens between gift addr
        # and this doc's addr
        d_addr = (ex.get('property_address') or '').lower()
        d_tokens = set(re.findall(r'[a-z]{4,}', d_addr))
        d_tokens -= SKIP
        overlap = addr_tokens & d_tokens
        if not overlap:
            continue
        candidates.append({
            'value': v,
            'score': len(overlap),
            'doc_addr': (ex.get('property_address') or '')[:50],
            'doc_id': d.id,
        })

    if not candidates:
        return None
    candidates.sort(key=lambda c: -c['score'])
    top = candidates[0]
    others = [c['value'] for c in candidates[1:4] if c['value'] != top['value']]
    src = (f"matched doc with address {top['doc_addr']!r} "
           f"(shared {top['score']} address tokens)")
    out = {'value': top['value'], 'source': src}
    if others:
        out['options'] = [top['value']] + others
    return out


def suggest_main_beneficiary(gift: Dict[str, Any],
                               client_id: str) -> Optional[Dict[str, Any]]:
    """🔥 §10x.145 — Pull beneficiary suggestion from AI Summary structured
    JSON for property gifts. Each AI Summary property has its own
    `beneficiaries[]` array — return as a comma-joined "Name share%,
    Name share%" suggestion.
    """
    if not client_id:
        return None
    ai_idx = gift.get('_ai_summary_idx')
    if ai_idx is None:
        return None
    try:
        from ai.chat_planner import _extract_ai_summary_properties
        ai_props = _extract_ai_summary_properties(client_id) or []
    except Exception:
        return None
    if not (0 <= int(ai_idx) < len(ai_props)):
        return None
    ap = ai_props[int(ai_idx)]
    bens = ap.get('beneficiaries') or []
    if not bens:
        return None
    parts = []
    for b in bens:
        if not isinstance(b, dict):
            continue
        nm = (b.get('name') or '').strip()
        sh = str(b.get('share_of_testator') or b.get('share') or '').strip()
        if nm:
            parts.append(f"{nm} {sh}".strip())
    if not parts:
        return None
    return {
        'value': ', '.join(parts),
        'source': 'from your message: "' +
                  (ap.get('beneficiary') or ', '.join(parts))[:80] + '"',
    }


# Convenience wrapper for both kinds — used by the wizard banner.
def suggest_for_gift(gift: Dict[str, Any], field: str,
                       client_id: str = '') -> Optional[Dict[str, Any]]:
    """Returns suggestion for a gift's missing field, branching on kind."""
    if not isinstance(gift, dict):
        return None
    kind = (gift.get('kind') or gift.get('asset_type') or
            ('property' if gift.get('gift_type') == 'property' else '')).lower()

    # 🔥 §10x.145 — Beneficiary suggestion via AI Summary lookup
    if field == 'main_beneficiary' or field == 'main beneficiary':
        if client_id:
            return suggest_main_beneficiary(gift, client_id)
        return None

    if kind == 'property' or gift.get('gift_type') == 'property':
        pi = gift.get('property_info') or gift.get('property_details') or {}
        # Try address-regex first
        s = suggest_property_field(pi, field)
        if s:
            return s
        # Fallback: cross-reference uploaded docs for title/lot
        if field in ('title_number', 'lot_number') and client_id:
            return suggest_title_or_lot_from_docs(gift, field, client_id)
        return None
    fd = gift.get('financial_details') or {}
    if 'insurance' in kind or 'policy' in kind:
        return suggest_financial_field(fd, field, kind='insurance')
    return suggest_financial_field(fd, field, kind='bank')
