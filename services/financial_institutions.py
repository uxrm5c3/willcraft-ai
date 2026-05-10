"""🔥 §10x.149 — Canonical database of Malaysian + Singaporean financial
institutions (banks, insurance/takaful, asset managers).

Used by ai/chat_planner.py::_extract_ai_summary_banks and
::_extract_ai_summary_insurance to:
  1. Canonicalise misspellings (e.g. "eaTiQa" → "Etiqa")
  2. Tag each entry with country + license type
  3. Surface a clarification card when a name doesn't match anything
     known — the chat asks the user to confirm before saving.

Sources for the institution list:
  • Bank Negara Malaysia (BNM) licensed financial institutions
  • Monetary Authority of Singapore (MAS) Financial Institutions
    Directory
  • Persatuan Insurans Am Malaysia (PIAM)
  • Life Insurance Association of Singapore (LIA)

Update by adding new entries; the matcher uses fuzzy matching so
small spelling drift survives without a code change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FinancialInstitution:
    canonical: str            # The CORRECT spelling we will display & store
    aliases: List[str]        # Alternate spellings, abbreviations, OCR drift
    country: str              # 'MY' / 'SG' / 'GLOBAL'
    kind: str                 # 'bank' / 'insurance' / 'takaful' / 'asset_manager'
    legal_form: str = ''      # 'Berhad' / 'Pte Ltd' / 'Limited' / ''
    notes: str = ''


# ── BANKS ─────────────────────────────────────────────────────────────────
_BANKS_MY: List[FinancialInstitution] = [
    FinancialInstitution('Maybank', ['Malayan Banking', 'May Bank', 'MBB'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('CIMB Bank', ['CIMB', 'C.I.M.B.', 'CIMB Bank Berhad'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Public Bank', ['Public Bank Berhad', 'PBB'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('RHB Bank', ['RHB', 'Rashid Hussain'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Hong Leong Bank', ['HLB', 'Hong Leong'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('AmBank', ['Am Bank', 'AMMB', 'AMMB Holdings'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Affin Bank', ['Affin', 'Affin Hwang'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Alliance Bank', ['Alliance', 'ABMB'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Bank Islam', ['Bank Islam Malaysia', 'BIMB'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Bank Muamalat', ['Bank Muamalat Malaysia', 'BMMB'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Bank Rakyat', ['Bank Kerjasama Rakyat'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Bank Simpanan Nasional', ['BSN'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('MBSB Bank', ['MBSB', 'Malaysia Building Society'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Agrobank', ['Agro Bank', 'Bank Pertanian Malaysia'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Co-op Bank Pertama', ['Co-operative Bank Pertama', 'Bank Persatuan'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('HSBC Bank Malaysia', ['HSBC Malaysia', 'HSBC'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Standard Chartered Bank Malaysia', ['SCB Malaysia', 'StanChart Malaysia', 'Standard Chartered MY'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('OCBC Bank Malaysia', ['OCBC Malaysia', 'OCBC MY'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('UOB Malaysia', ['United Overseas Bank Malaysia', 'UOB MY'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Citibank Malaysia', ['Citi Malaysia', 'Citibank MY'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Bank of China Malaysia', ['BOC Malaysia', 'BOCM'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('ICBC Malaysia', ['Industrial and Commercial Bank of China'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('China Construction Bank Malaysia', ['CCB Malaysia'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Kuwait Finance House Malaysia', ['KFH Malaysia', 'KFH'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Al Rajhi Bank Malaysia', ['Al-Rajhi Bank', 'Al Rajhi'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Mizuho Bank Malaysia', ['Mizuho Malaysia'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Sumitomo Mitsui Banking Corporation Malaysia', ['SMBC Malaysia'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Bangkok Bank Berhad', ['Bangkok Bank Malaysia'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('BNP Paribas Malaysia', ['BNP Paribas MY'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Deutsche Bank Malaysia', ['Deutsche Bank MY'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('JP Morgan Chase Bank Malaysia', ['JPMorgan Malaysia', 'J.P. Morgan'], 'MY', 'bank', 'Berhad'),
    FinancialInstitution('Bank of America Malaysia', ['BofA Malaysia'], 'MY', 'bank', 'Berhad'),
]

_BANKS_SG: List[FinancialInstitution] = [
    FinancialInstitution('DBS Bank', ['DBS', 'Development Bank of Singapore'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('POSB Bank', ['POSB', 'Post Office Savings Bank', 'POSBank'], 'SG', 'bank', 'Limited',
                          notes='Subsidiary of DBS Bank since 1998'),
    FinancialInstitution('OCBC Bank', ['OCBC', 'Oversea-Chinese Banking Corporation', 'OCBC Singapore'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('UOB Singapore', ['UOB', 'United Overseas Bank', 'United Overseas Bank Singapore'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('Maybank Singapore', ['Maybank SG', 'Malayan Banking Singapore'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('Standard Chartered Bank Singapore', ['StanChart Singapore', 'SCB Singapore', 'Standard Chartered SG'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('HSBC Singapore', ['HSBC SG', 'HSBC Bank Singapore'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('Citibank Singapore', ['Citi Singapore', 'Citi SG'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('CIMB Bank Singapore', ['CIMB SG'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('RHB Bank Singapore', ['RHB SG'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('Hong Leong Finance Singapore', ['HL Bank Singapore', 'Hong Leong Singapore'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('Bank of China Singapore', ['BOC Singapore'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('ICBC Singapore', ['ICBC SG'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('Mizuho Bank Singapore', ['Mizuho SG'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('Sumitomo Mitsui Banking Corporation Singapore', ['SMBC Singapore', 'SMBC SG'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('ICICI Bank Singapore', ['ICICI Singapore'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('State Bank of India Singapore', ['SBI Singapore'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('BNP Paribas Singapore', ['BNP Paribas SG'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('Deutsche Bank Singapore', ['Deutsche SG'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('JP Morgan Chase Bank Singapore', ['JPMorgan Singapore'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('Bank of America Singapore', ['BofA Singapore'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('ANZ Singapore', ['ANZ', 'Australia and New Zealand Banking Group'], 'SG', 'bank', 'Limited'),
    FinancialInstitution('Bangkok Bank Singapore', ['Bangkok Bank SG'], 'SG', 'bank', 'Limited'),
]

# ── INSURANCE / TAKAFUL ───────────────────────────────────────────────────
_INSURANCE_MY: List[FinancialInstitution] = [
    FinancialInstitution('AIA Bhd', ['AIA Malaysia', 'AIA', 'AIA Berhad'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Allianz Life Insurance Malaysia', ['Allianz Malaysia', 'Allianz Life'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Allianz General Insurance Malaysia', ['Allianz General'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('AmGeneral Insurance', ['AmGeneral', 'Am General Insurance'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Berjaya Sompo Insurance', ['Berjaya Sompo', 'Sompo Malaysia'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Etiqa Insurance', ['Etiqa', 'eTiqa', 'eaTiQa', 'Etiqa Insurance Berhad', 'Etiqa General'], 'MY', 'insurance', 'Berhad',
                          notes='Maybank-owned. Common OCR misreads: eaTiQa, eTiqa.'),
    FinancialInstitution('Etiqa Family Takaful', ['Etiqa Takaful', 'Etiqa Family', 'Etiqa Takaful Berhad'], 'MY', 'takaful', 'Berhad'),
    FinancialInstitution('Etiqa General Takaful', ['Etiqa General Takaful Berhad'], 'MY', 'takaful', 'Berhad'),
    FinancialInstitution('Generali Insurance Malaysia', ['Generali Malaysia', 'Generali'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Great Eastern Life Assurance Malaysia', ['Great Eastern Malaysia', 'GE Life Malaysia', 'GELM'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Great Eastern General Insurance Malaysia', ['Great Eastern General'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Hong Leong Assurance', ['HLA', 'Hong Leong Insurance'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Hong Leong MSIG Takaful', ['Hong Leong Takaful', 'HL Takaful'], 'MY', 'takaful', 'Berhad'),
    FinancialInstitution('MCIS Insurance', ['MCIS', 'MCIS Life'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('MSIG Insurance Malaysia', ['MSIG Malaysia', 'MSIG'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Manulife Insurance Malaysia', ['Manulife Malaysia', 'Manulife'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Pacific Insurance', ['Pacific Insurance Berhad'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Prudential Assurance Malaysia', ['Prudential Malaysia', 'Prudential', 'Prudential BSN Takaful'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('RHB Insurance', ['RHB Insurance Berhad'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Sun Life Malaysia', ['Sun Life', 'Sunlife Malaysia'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Tokio Marine Insurans Malaysia', ['Tokio Marine Malaysia', 'Tokio Marine'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Tune Protect', ['Tune Insurance', 'Tune Protect Group'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Zurich General Insurance Malaysia', ['Zurich Malaysia', 'Zurich Insurance'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Zurich Life Insurance Malaysia', ['Zurich Life Malaysia'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('AXA Affin Life Insurance', ['AXA Affin Life', 'AXA Affin'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('AXA Affin General Insurance', ['AXA Affin General'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Liberty Insurance Malaysia', ['Liberty Malaysia'], 'MY', 'insurance', 'Berhad'),
    FinancialInstitution('Takaful Ikhlas', ['Takaful Ikhlas Family', 'Ikhlas'], 'MY', 'takaful', 'Berhad'),
    FinancialInstitution('Syarikat Takaful Malaysia', ['STMB', 'Takaful Malaysia'], 'MY', 'takaful', 'Berhad'),
    FinancialInstitution('FWD Takaful', ['FWD Malaysia'], 'MY', 'takaful', 'Berhad'),
]

_INSURANCE_SG: List[FinancialInstitution] = [
    FinancialInstitution('AIA Singapore', ['AIA SG'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('Allianz Singapore', ['Allianz SG', 'Allianz Insurance Singapore'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('AXA Singapore', ['AXA SG', 'AXA Insurance Singapore'], 'SG', 'insurance', 'Pte Ltd',
                          notes='Acquired by HSBC in 2022; brand transitioning to HSBC Life Singapore'),
    FinancialInstitution('Aviva Singapore', ['Aviva Limited Singapore'], 'SG', 'insurance', 'Limited',
                          notes='Rebranded as Singlife in 2022'),
    FinancialInstitution('China Life Insurance Singapore', ['China Life SG'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('China Taiping Insurance Singapore', ['China Taiping SG', 'CTIS'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('Etiqa Insurance Singapore', ['Etiqa SG', 'Etiqa Singapore'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('FWD Singapore', ['FWD SG', 'FWD Insurance Singapore'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('Great Eastern Life Singapore', ['Great Eastern SG', 'GE Life Singapore', 'GE'], 'SG', 'insurance', 'Limited'),
    FinancialInstitution('HSBC Life Singapore', ['HSBC Insurance', 'HSBC Insurance Singapore'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('India International Insurance', ['III Singapore', 'III'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('Liberty Insurance Singapore', ['Liberty SG'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('Manulife Singapore', ['Manulife SG'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('MSIG Singapore', ['MSIG SG', 'Mitsui Sumitomo Singapore'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('Income Insurance', ['NTUC Income', 'Income', 'NTUC'], 'SG', 'insurance', 'Limited',
                          notes='Was NTUC Income Co-operative; corporatised to Income Insurance Limited in 2022.'),
    FinancialInstitution('Prudential Singapore', ['Prudential SG', 'Prudential Assurance Singapore'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('QBE Insurance Singapore', ['QBE SG', 'QBE'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('Singlife', ['Singapore Life', 'Singlife with Aviva'], 'SG', 'insurance', 'Pte Ltd',
                          notes='Formed from merger of Aviva Singapore + Singapore Life in 2022.'),
    FinancialInstitution('Sompo Insurance Singapore', ['Sompo SG'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('Tokio Marine Singapore', ['Tokio Marine SG', 'Tokio Marine Insurance Singapore'], 'SG', 'insurance', 'Pte Ltd'),
    FinancialInstitution('Zurich Insurance Singapore', ['Zurich SG'], 'SG', 'insurance', 'Pte Ltd'),
]

ALL_INSTITUTIONS: List[FinancialInstitution] = (
    _BANKS_MY + _BANKS_SG + _INSURANCE_MY + _INSURANCE_SG
)


def _normalise(s: str) -> str:
    """Lowercase, strip non-alnum, collapse whitespace. Used for matching."""
    if not s:
        return ''
    s = s.lower()
    s = re.sub(r'[^a-z0-9]+', '', s)
    return s


def _levenshtein(a: str, b: str) -> int:
    """Iterative Levenshtein distance — pure Python, no dependency."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            ins = curr[j-1] + 1
            dele = prev[j] + 1
            sub = prev[j-1] + (0 if ca == cb else 1)
            curr.append(min(ins, dele, sub))
        prev = curr
    return prev[-1]


def match_institution(name: str, kind: Optional[str] = None,
                       country_hint: Optional[str] = None
                       ) -> Optional[dict]:
    """Look up `name` against the institution database.

    Returns a dict {canonical, country, kind, legal_form, notes,
                    confidence ('exact' / 'alias' / 'fuzzy'),
                    matched_alias, original_input} on match,
    or None if NO entry came within edit-distance 2 of any alias.

    `kind` filter ('bank' / 'insurance' / 'takaful' / None) limits
    the candidate pool. `country_hint` ('MY' / 'SG' / None) is used
    for tie-breaking when multiple aliases match.
    """
    if not name:
        return None
    n_norm = _normalise(name)
    if not n_norm:
        return None
    pool = [fi for fi in ALL_INSTITUTIONS
            if (kind is None or fi.kind == kind
                or (kind == 'insurance' and fi.kind == 'takaful')
                or (kind == 'takaful' and fi.kind == 'insurance'))]
    # Stage 1: exact match against canonical
    for fi in pool:
        if _normalise(fi.canonical) == n_norm:
            return _to_dict(fi, n_norm, 'exact', fi.canonical, name)
    # Stage 2: alias match (any alias's normalised form == input)
    for fi in pool:
        for al in fi.aliases:
            if _normalise(al) == n_norm:
                return _to_dict(fi, n_norm, 'alias', al, name)
    # Stage 3: substring match (input is contained in canonical or vice versa)
    sub_hits = []
    for fi in pool:
        c_norm = _normalise(fi.canonical)
        if n_norm in c_norm or c_norm in n_norm:
            sub_hits.append((fi, fi.canonical))
        else:
            for al in fi.aliases:
                a_norm = _normalise(al)
                if n_norm in a_norm or a_norm in n_norm:
                    sub_hits.append((fi, al))
                    break
    if sub_hits:
        # Prefer same country if hint given
        if country_hint:
            same_country = [(f, a) for f, a in sub_hits if f.country == country_hint]
            if same_country:
                sub_hits = same_country
        fi, al = sub_hits[0]
        return _to_dict(fi, n_norm, 'alias', al, name)
    # Stage 4: fuzzy match (Levenshtein ≤ 2 per token)
    best = None
    best_dist = 10**9
    for fi in pool:
        for candidate in [fi.canonical] + fi.aliases:
            c_norm = _normalise(candidate)
            d = _levenshtein(n_norm, c_norm)
            # Allow up to ~20% of the longer string as edit distance
            tolerance = max(2, len(c_norm) // 5)
            if d <= tolerance and d < best_dist:
                best = (fi, candidate, d)
                best_dist = d
    if best:
        fi, al, _d = best
        return _to_dict(fi, n_norm, 'fuzzy', al, name)
    return None


def _to_dict(fi: FinancialInstitution, n_norm: str, confidence: str,
              matched_alias: str, original: str) -> dict:
    return {
        'canonical': fi.canonical,
        'country': fi.country,
        'kind': fi.kind,
        'legal_form': fi.legal_form,
        'notes': fi.notes,
        'confidence': confidence,
        'matched_alias': matched_alias,
        'original_input': original,
    }


def canonicalise_or_flag(name: str, kind: Optional[str] = None,
                          country_hint: Optional[str] = None
                          ) -> dict:
    """Return {canonical, was_corrected, confidence, suggestion} for any
    name. If no DB entry matched, returns
    {canonical: name, was_corrected: False, confidence: 'unknown',
     suggestion: None, needs_user_verification: True}.
    """
    m = match_institution(name, kind=kind, country_hint=country_hint)
    if not m:
        return {
            'canonical': name.strip(),
            'was_corrected': False,
            'confidence': 'unknown',
            'suggestion': None,
            'needs_user_verification': True,
            'original_input': name,
        }
    canonical = m['canonical']
    was_corrected = (m['confidence'] in ('alias', 'fuzzy')
                     and _normalise(name) != _normalise(canonical))
    return {
        'canonical': canonical,
        'was_corrected': was_corrected,
        'confidence': m['confidence'],
        'suggestion': canonical if was_corrected else None,
        'needs_user_verification': m['confidence'] == 'fuzzy',
        'country': m['country'],
        'kind': m['kind'],
        'notes': m.get('notes', ''),
        'original_input': name,
    }


def list_all(kind: Optional[str] = None, country: Optional[str] = None) -> list:
    """Return all institutions (optionally filtered) as JSON-friendly dicts."""
    out = []
    for fi in ALL_INSTITUTIONS:
        if kind and fi.kind != kind:
            continue
        if country and fi.country != country:
            continue
        out.append({
            'canonical': fi.canonical,
            'aliases': fi.aliases,
            'country': fi.country,
            'kind': fi.kind,
            'legal_form': fi.legal_form,
            'notes': fi.notes,
        })
    return out
