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

        # 🔥 §10x.145 — Wizard rewrites strip `kind=insurance`; gifts come
        # through here as `bank` even when they're insurance policies.
        # AIA in bank-only pool fuzzy-matches ANZ Singapore (wrong).
        # Strategy: try BOTH bank + insurance pools. If only one returns
        # an EXACT/ALIAS match (high-confidence), use that. If both return
        # only fuzzy hits, return None rather than risk wrong match.
        kinds_to_try = [kind] if kind in ('insurance', 'takaful') else [
            'insurance', 'bank']
        best = None
        for k in kinds_to_try:
            try:
                m = match_institution(institution, kind=k)
            except Exception:
                m = None
            if m and m.get('confidence') in ('exact', 'alias'):
                best = m
                break  # high-confidence match wins
            if m and best is None:
                best = m  # fuzzy fallback only if nothing better

        m = best
        if m and m.get('country') and m.get('confidence') != 'fuzzy':
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


# 🔥 §10x.147 — process-level cache for LLM cross-ref. Keyed by
# (client_id, gift_address_hash, field). Lives for the gunicorn worker's
# lifetime; redeploy wipes. Acceptable tradeoff vs DB-roundtrip per
# render. Each KOID page render costs ~$0.005 first time, $0 on
# subsequent renders within same worker.
_LLM_MATCH_CACHE: Dict[str, Dict[str, Any]] = {}


def _llm_cache_key(client_id: str, gift: Dict[str, Any], field: str) -> str:
    # 🔥 §10x.147 — MERGE both schemas instead of first-truthy. Quick-fix
    # writes to property_info; chat handlers wrote to property_details.
    # First-truthy returned only one, missing fields from the other.
    pi = {}
    pi.update(gift.get('property_details') or {})
    pi.update({k: v for k, v in (gift.get('property_info') or {}).items() if v})
    addr = (pi.get('property_address') or gift.get('address') or '').strip()
    import hashlib
    h = hashlib.sha256(addr.encode('utf-8')).hexdigest()[:12]
    return f'{client_id}:{h}:{field}'


def suggest_title_or_lot_via_llm(gift: Dict[str, Any], field: str,
                                    client_id: str) -> Optional[Dict[str, Any]]:
    """🔥 §10x.147 — LLM fallback when token-cross-ref fails.

    OCR sometimes garbles property doc addresses (e.g. Sri Laguna SPA
    OCR'd as 'Marsiling Lane Singapore'). Token-match can't find such
    docs. This helper sends ALL the user's property docs + the gift's
    address to Claude Haiku and asks it to pick the most likely match.

    Cost: ~$0.0008/call. Result is cached process-level by
    (client_id, addr-hash, field).
    """
    if not client_id or field not in ('title_number', 'lot_number'):
        return None
    cache_key = _llm_cache_key(client_id, gift, field)
    if cache_key in _LLM_MATCH_CACHE:
        c = _LLM_MATCH_CACHE[cache_key]
        return c if (c and c.get('value')) else None

    try:
        from app import db
        from database import Document, Will
        import json as _json
        import os
        import anthropic
    except Exception:
        return None

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return None

    # 🔥 §10x.147 — MERGE both schemas instead of first-truthy. Quick-fix
    # writes to property_info; chat handlers wrote to property_details.
    # First-truthy returned only one, missing fields from the other.
    pi = {}
    pi.update(gift.get('property_details') or {})
    pi.update({k: v for k, v in (gift.get('property_info') or {}).items() if v})
    gift_addr = (pi.get('property_address') or
                 gift.get('address') or '').strip()
    if not gift_addr:
        return None

    # Collect all property-related docs with at least one of title/lot
    docs = Document.query.filter_by(client_id=client_id).filter(
        Document.category.in_(['property_title', 'property_spa',
                                'property_tax', 'property_transfer',
                                'loan_agreement'])).all()
    candidates = []
    for d in docs:
        try:
            ex = _json.loads(d.extracted_data or '{}')
        except Exception:
            continue
        if not isinstance(ex, dict):
            continue
        t = (ex.get('title_number') or '').strip()
        l = (ex.get('lot_number') or '').strip()
        a = (ex.get('property_address') or '').strip()
        own = (ex.get('owner_name') or ex.get('owner_names') or '')
        # Skip docs with no useful identifier
        if not (t or l):
            continue
        # 🔥 §10x.156 — pre-filter docs whose ONLY identifier is a
        # placeholder per §10x.152h. Without this filter, the LLM keeps
        # picking docs like 'title=Folio 5' because their address matches
        # the will exactly — even though Folio 5 isn't a real Malaysian
        # title. Pre-rejecting at candidate-build time keeps the LLM
        # honest: it can only choose docs whose values would survive
        # the regex check downstream.
        t_low = t.lower().strip()
        l_low = l.lower().strip()
        bogus_re = re.compile(
            r'^(folio|vol\.?|page|title\s*no\.?\s*\(.*\)|\(.*\))\s*\d*$'
        )
        t_bogus = (not t) or bool(bogus_re.match(t_low)) or 'unreadable' in t_low
        l_bogus = (not l) or bool(bogus_re.match(l_low)) or 'unreadable' in l_low
        if t_bogus and l_bogus:
            # Both fields are unusable — skip this candidate entirely
            continue
        # 🔥 §10x.156 — surface mukim/daerah so LLM can use them as primary
        # signals when address is a party-residence (chargor/purchaser/lawyer).
        candidates.append({
            'doc_id': d.id,
            'category': d.category,
            'title_number': '' if t_bogus else t,
            'lot_number': '' if l_bogus else l,
            'address': a[:160],
            'owners': str(own)[:80],
            'mukim': (ex.get('mukim') or '').strip()[:40],
            'daerah': (ex.get('daerah') or '').strip()[:40],
        })
    if not candidates:
        return None

    # 🔥 §10x.156 — per-field filter: when caller asks for title_number,
    # only include docs whose title_number is non-empty (and similarly
    # for lot_number). Without this, the LLM picks the doc whose address
    # best matches the will, then we discover its requested field is
    # empty → return None. By pre-filtering, the LLM is forced to choose
    # among docs that actually CAN provide the requested value.
    field_key = 'title_number' if field == 'title_number' else 'lot_number'
    candidates = [c for c in candidates if c.get(field_key)]
    if not candidates:
        _LLM_MATCH_CACHE[cache_key] = {
            'value': '',
            'source': f'no doc has a usable {field}',
        }
        return None

    # 🔥 §10x.156 DETERMINISTIC PRE-LLM PATH: if exactly ONE candidate
    # has mukim agreement with the will's resolved mukim AND its owner
    # matches a known family/testator name, that's a HIGH-confidence
    # match — no need to ask the LLM. (The LLM keeps over-weighting
    # address similarity even with explicit prompt instructions.)
    will_mukim_pre = ''
    family_set_pre: set = set()
    try:
        from database import Person, Will as _W
        ws = _W.query.filter_by(client_id=client_id).filter(
            _W.deleted_at.is_(None)).order_by(_W.updated_at.desc()).first()
        if ws:
            s1 = _json.loads(ws.step1_data or '{}')
            tn = (s1.get('full_name') or '').strip().upper()
            if tn:
                family_set_pre.add(tn)
            for p in Person.query.filter_by(client_id=client_id).all():
                if p.full_name:
                    family_set_pre.add(p.full_name.strip().upper())
        try:
            from ai.chat_planner import _GEO_BRIDGE
            # _GEO_BRIDGE values are tuples (mukim, daerah, negeri).
            # Longest key first so 'bandar seri alam' beats 'seri alam'.
            ga_lc = (gift_addr or '').lower()
            for k in sorted((_GEO_BRIDGE or {}).keys(),
                            key=len, reverse=True):
                if k.lower() in ga_lc:
                    v = _GEO_BRIDGE[k]
                    if isinstance(v, tuple) and v:
                        will_mukim_pre = (v[0] or '').lower()
                    elif isinstance(v, dict):
                        will_mukim_pre = (v.get('mukim') or '').lower()
                    if will_mukim_pre:
                        break
        except Exception:
            pass
    except Exception:
        pass

    # Same lookup for the in-prompt context block below.
    will_mukim = will_mukim_pre.title() if will_mukim_pre else ''
    # Cross-AssetItem ambiguity check: if MORE THAN ONE AssetItem (in
    # this client's will) resolves to the same Mukim via geo bridge,
    # mukim+family-owner alone is insufficient to pick a specific gift.
    # The canonical matcher (services/asset_pipeline) handles these via
    # candidate-with-confirm cards in the chat flow. Wizard banner here
    # falls through to the LLM (which appropriately returns null when
    # ambiguous) so the field stays blank and the user picks via chat.
    n_in_same_mukim = 0
    if will_mukim_pre:
        try:
            from services.asset_pipeline import parse_canonical_assets
            for ai in parse_canonical_assets(client_id):
                if ai.kind != 'property':
                    continue
                m = (ai.fields.get('mukim') or '').lower().strip()
                if m == will_mukim_pre:
                    n_in_same_mukim += 1
        except Exception:
            n_in_same_mukim = 99   # fail-safe: skip det match on error
    if (will_mukim_pre and family_set_pre and n_in_same_mukim == 1):
        det_hits = []
        for c in candidates:
            cm = (c.get('mukim') or '').lower().strip()
            cm = re.sub(r'^mukim\s+', '', cm)
            if cm != will_mukim_pre:
                continue
            owners_up = (c.get('owners') or '').upper()
            owner_hit = False
            for fam_name in family_set_pre:
                # Match if any 4+ char family-name token appears in owners
                for tok in re.findall(r'[A-Z]{4,}', fam_name):
                    if tok in owners_up:
                        owner_hit = True
                        break
                if owner_hit:
                    break
            if owner_hit:
                det_hits.append(c)
        if len(det_hits) == 1:
            # Single deterministic match (sole property in this mukim,
            # single matching family-owner doc) — pick it without LLM.
            matched = det_hits[0]
            value = (matched.get('title_number') if field == 'title_number'
                     else matched.get('lot_number') or '')
            if value:
                out = {
                    'value': value,
                    'source': (f"deterministic match (sole property in "
                               f"Mukim {will_mukim_pre.title()} + "
                               f"family-owner): {matched['category']} doc"),
                }
                _LLM_MATCH_CACHE[cache_key] = out
                return out

    # Build prompt for Claude
    prompt = (
        "You are matching uploaded Malaysian property documents to a "
        "specific property mentioned in a will.\n\n"
        f"PROPERTY (from will): {gift_addr}\n\n"
        f"FIELD REQUESTED: {field}\n\n"
        "UPLOADED PROPERTY DOCS (each has a non-empty value for the "
        f"requested {field}; addresses may be a party residence not the "
        "subject property):\n"
    )
    # Reuse the lookups already done in the deterministic pre-LLM block
    family_blob = ', '.join(sorted(family_set_pre)) if family_set_pre else ''

    for i, c in enumerate(candidates):
        # Highlight title/lot non-emptiness — the LLM should prefer docs
        # with REAL identifiers over docs with just an address match
        # (the wizard banner needs the Geran/lot, not just confirmation
        # that a doc exists for the address).
        has_real_id = bool(c['title_number'] and c['title_number'].lower()
                            not in ('folio', 'vol', 'unreadable'))
        marker = '⭐ has-id' if has_real_id else '○ no-id'
        prompt += (f"  [{i}] {marker} cat={c['category']} "
                   f"addr={c['address']!r} title={c['title_number']!r} "
                   f"lot={c['lot_number']!r} mukim={c['mukim']!r} "
                   f"daerah={c['daerah']!r} owners={c['owners']!r}\n")
    if family_blob:
        prompt += f"\nKnown family/testator names: {family_blob[:200]}\n"
    if will_mukim:
        prompt += f"Property's resolved Mukim (from address geo bridge): {will_mukim}\n"
    prompt += (
        f"\nWhich doc index is MOST LIKELY the official title document "
        f"for this property AND has a USABLE title No. or lot No. "
        f"(non-empty, not 'Folio N', not '(unreadable)')?\n\n"
        f"⭐ marker = doc has a real identifier; ○ = doc has only an "
        f"empty / placeholder value. Prefer ⭐ docs unless ○ is "
        f"obviously the subject (e.g. exact address + owner match).\n\n"
        f"PRIORITY ORDER OF SIGNALS (strongest first):\n"
        f"  1. Title No. or Lot No. that the will already mentions "
        f"(direct identifier match — rare but decisive)\n"
        f"  2. Owner name matches a known family/testator name\n"
        f"  3. Mukim/Daerah agreement with the property's resolved Mukim\n"
        f"  4. Address keywords (LEAST reliable on SPA/Charge/Loan because "
        f"those forms typically print the PARTY's residence, not the "
        f"subject property)\n\n"
        f"🔥 §10x.156 CRITICAL — addresses on legal forms are OFTEN a "
        f"PARTY's residence, NOT the subject property:\n"
        f"  • SPA: 'address' shown is usually the PURCHASER's residence "
        f"(can be in a different city, even Singapore). The subject "
        f"property is described in the FIRST SCHEDULE.\n"
        f"  • Charge / Loan agreement (Borang 16A): 'address' is "
        f"typically the CHARGOR's residence. The subject property is in "
        f"the SECURITY / CHARGED LAND schedule.\n"
        f"  • Cukai Tanah: address is the bill-to mailing address; the "
        f"property is identified by Lot + Mukim.\n"
        f"  → DO NOT reject a doc just because its 'address' field "
        f"differs from the will's property address. Use mukim, lot No., "
        f"title No., and OWNER NAME as primary signals.\n\n"
        f"🔥 §10x.157 — OCR FIELD-SWAP awareness: vision regularly "
        f"swaps title and lot fields on Charge / Loan / SPA forms "
        f"because multiple numeric IDs (charge account No., title No., "
        f"lot No.) are printed close together. A 6-7-digit value in the "
        f"'lot' field MAY actually be the Geran/title No., and vice "
        f"versa. Consider both possibilities when scoring.\n\n"
        f"🔥 §10x.152h — REJECT bogus values: 'Folio N' / 'Vol N' / "
        f"'(unreadable)' / 'Page N' are NOT valid Malaysian NLC titles. "
        f"If the matching doc's title field has any such value, return "
        f"best_doc_index=null.\n\n"
        f"Respond with ONLY a JSON object: "
        f'{{"best_doc_index": <int>, "confidence": "high"|"medium"|"low", '
        f'"reason": "<one short sentence — explain WHICH signal won>"}}\n'
        f"If NO doc plausibly matches, respond "
        f'{{"best_doc_index": null, "confidence": "low", '
        f'"reason": "no match"}}.\n'
        f"Output ONLY the JSON, no other text."
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model='claude-haiku-4-5',
            max_tokens=200,
            temperature=0,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = resp.content[0].text.strip()
        # Strip markdown fences if any
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text).strip()
        result = _json.loads(text)
    except Exception as e:
        return None

    idx = result.get('best_doc_index')
    if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(candidates):
        _LLM_MATCH_CACHE[cache_key] = {'value': '', 'source': 'no LLM match'}
        return None

    matched = candidates[idx]
    value = (matched.get('title_number') if field == 'title_number'
             else matched.get('lot_number') or '')
    if not value:
        _LLM_MATCH_CACHE[cache_key] = {
            'value': '', 'source': 'matched doc has empty ' + field}
        return None
    # 🔥 §10x.147 — Reject Folio/Vol/Page/(unreadable) per §10x.152h.
    # Singapore Land Registry references ('Folio 5') or OCR garbage are
    # NOT valid Malaysian NLC titles. Don't surface them as suggestions.
    v_low = value.lower().strip()
    if (re.match(r'^\s*(folio|vol\.?|page|title\s*no\.?\s*\(.*\)|\(.*\))\s*\d*\s*$',
                  v_low)
        or 'unreadable' in v_low or 'cannot read' in v_low):
        _LLM_MATCH_CACHE[cache_key] = {
            'value': '', 'source': f'rejected non-NLC value {value!r}'}
        return None

    confidence = result.get('confidence', 'low')
    reason = result.get('reason', '')[:120]
    out = {
        'value': value,
        'source': (f"AI-matched doc (cat={matched['category']}, "
                   f"confidence={confidence}): {reason}"),
    }
    _LLM_MATCH_CACHE[cache_key] = out
    return out


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

    # 🔥 §10x.147 — MERGE both schemas instead of first-truthy. Quick-fix
    # writes to property_info; chat handlers wrote to property_details.
    # First-truthy returned only one, missing fields from the other.
    pi = {}
    pi.update(gift.get('property_details') or {})
    pi.update({k: v for k, v in (gift.get('property_info') or {}).items() if v})
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

    Match strategies (in order):
      1. `_ai_summary_idx` field (chat-side schema)
      2. Address fuzzy match (Step 6-rewritten schema drops the idx)
    """
    if not client_id:
        return None
    try:
        from ai.chat_planner import _extract_ai_summary_properties
        ai_props = _extract_ai_summary_properties(client_id) or []
    except Exception:
        return None
    if not ai_props:
        return None

    ap = None
    # Strategy 1: explicit ai_summary_idx
    ai_idx = gift.get('_ai_summary_idx')
    if ai_idx is not None and 0 <= int(ai_idx) < len(ai_props):
        ap = ai_props[int(ai_idx)]

    # Strategy 2: address fuzzy match
    if ap is None:
        # 🔥 §10x.147 — MERGE both schemas instead of first-truthy.
        # Quick-fix writes to property_info; chat handlers wrote to
        # property_details. First-truthy returned only one, missing
        # fields from the other.
        pi = {}
        pi.update(gift.get('property_details') or {})
        pi.update({k: v for k, v in (gift.get('property_info') or {}).items() if v})
        gift_addr = (pi.get('property_address') or
                     gift.get('address') or '').lower()
        if not gift_addr:
            return None
        # Token overlap
        SKIP = {'jalan', 'taman', 'bandar', 'condominium', 'unit', 'no',
                'malaysia', 'singapore', 'johor', 'bahru', 'persiaran',
                'mukim', 'daerah', 'negeri', 'state', 'block', 'house', 'shop'}
        gift_tokens = {t for t in re.findall(r'[a-z0-9\-]{3,}', gift_addr)
                       if t not in SKIP}
        if not gift_tokens:
            return None
        best_score = 0
        for cand in ai_props:
            cand_addr = ((cand.get('address') or '') + ' ' +
                         (cand.get('name') or '')).lower()
            cand_tokens = {t for t in re.findall(r'[a-z0-9\-]{3,}', cand_addr)
                           if t not in SKIP}
            score = len(gift_tokens & cand_tokens)
            if score > best_score:
                best_score = score
                ap = cand
        if best_score == 0:
            return None

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
        # 🔥 §10x.147 — MERGE both schemas instead of first-truthy.
        # Quick-fix writes to property_info; chat handlers wrote to
        # property_details. First-truthy returned only one, missing
        # fields from the other.
        pi = {}
        pi.update(gift.get('property_details') or {})
        pi.update({k: v for k, v in (gift.get('property_info') or {}).items() if v})
        # Try address-regex first
        s = suggest_property_field(pi, field)
        if s:
            return s
        # Fallback 1: cross-reference uploaded docs for title/lot via tokens
        if field in ('title_number', 'lot_number') and client_id:
            s = suggest_title_or_lot_from_docs(gift, field, client_id)
            if s and s.get('value'):
                return s
            # Fallback 2 (§10x.147): LLM cross-ref when token-match fails
            # OR returned a doc with empty value for this field. Costs
            # ~$0.0008/call but result is cached on the gift.
            return suggest_title_or_lot_via_llm(gift, field, client_id)
        return None
    fd = gift.get('financial_details') or {}
    # 🔥 §10x.145 — Heuristic: account_number starting with a letter or
    # being unusually long suggests insurance policy. Insurer field
    # populated also signals insurance. Bank account numbers are
    # numeric. When in doubt, suggester tries BOTH pools per
    # suggest_financial_field's new strategy.
    acct = (fd.get('account_number') or '').strip()
    looks_like_policy = (
        bool(acct) and (
            re.match(r'^[A-Za-z]', acct)  # starts with letter (e.g. L516911049)
            or len(acct) > 12  # unusually long
        )
    )
    if 'insurance' in kind or 'policy' in kind or fd.get('insurer') or looks_like_policy:
        return suggest_financial_field(fd, field, kind='insurance')
    return suggest_financial_field(fd, field, kind='bank')
