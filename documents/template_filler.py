"""🔒 LOCKED TEMPLATE FILLER — Phek Yi Ting / Alan & Tan format.

Per §10x.24 + §10x.25 + user instruction (May 2026):
    "use the existing will as template. only fill in the blanks. NO CREATIVITY"

This module replaces the LLM-based ai/drafter.py::draft_will() for the will
BODY. It assembles the will text deterministically by calling fixed clause
templates from ai/prompts/clause_templates.py and substituting tokens from
WillData. There is NO call to Claude — every word is from the firm-approved
template library.

Why no LLM?
    - Claude introduces format drift (different phrasings on each call).
    - The firm has approved ONE format. Drift is a regression.
    - "Insert any clause based on wizard so the format sticks" — user.

Public API:
    fill_will(will_data: WillData) -> str
        Returns the full will text, ready to feed into pdf_generator.generate_pdf().

Clause numbering is sequential through the document. Substitute clauses use
"With reference to Clause N above..." format (Phek-style).
"""
from __future__ import annotations
import re
from typing import Optional, List

from ai.prompts.clause_templates import (
    TITLE_TEMPLATE, PREAMBLE_TEMPLATE,
    REVOCATION_TEMPLATE,
    EXECUTOR_SINGLE_TEMPLATE, EXECUTOR_SINGLE_WITH_SUBSTITUTE_TEMPLATE,
    EXECUTOR_JOINT_TEMPLATE,
    EXECUTOR_AS_TRUSTEE_TEMPLATE,
    NON_RESIDUARY_HEADING,
    RESIDUARY_ESTATE_HEADING, RESIDUARY_TEMPLATE,
    RESIDUARY_WITH_SUBSTITUTE_TEMPLATE, RESIDUARY_MULTIPLE_TEMPLATE,
    DECLARATION_HEADING,
    INTENTIONAL_EXCLUSION_TEMPLATE, COMMORIENTES_TEMPLATE,
)


# ─── Helpers ───────────────────────────────────────────────────────────────

def _fmt_id(person) -> str:
    """Format the person's ID block: 'MALAYSIA NRIC No. NNNNNN-NN-NNNN' or
    '<Country> Identification No. <ID>' for foreign nationals.
    """
    nric = (getattr(person, 'nric_passport', None)
            or getattr(person, 'nric_passport_birthcert', None) or '')
    nationality = (getattr(person, 'nationality', None) or 'Malaysian').strip()
    if nationality.lower().startswith('malay'):
        return f"MALAYSIA NRIC No. {nric}"
    return f"{nationality.upper()} Identification No. {nric}"


def _he_she(person, will_data=None) -> str:
    """Phek-style: 'he' or 'she'. Tries the person's own .gender first, then
    falls back to looking up in WillData.identities or beneficiaries by name
    (Executor model lacks a gender field but the IC scan recorded one)."""
    g = (getattr(person, 'gender', None) or '').strip().lower()
    if g.startswith('f'):
        return 'she'
    if g.startswith('m'):
        return 'he'
    # Try identities lookup
    if will_data is not None and getattr(will_data, 'identities', None):
        nm = (getattr(person, 'full_name', '') or '').strip().upper()
        for ident in will_data.identities:
            if (ident.get('full_name') or '').strip().upper() == nm:
                ig = (ident.get('gender') or '').strip().lower()
                if ig.startswith('f'):
                    return 'she'
                if ig.startswith('m'):
                    return 'he'
    # Heuristic: relationship word implies gender for common ones
    rel = (getattr(person, 'relationship', '') or '').strip().lower()
    if rel in ('sister', 'mother', 'daughter', 'wife', 'aunt', 'sister-in-law',
                'mother-in-law', 'daughter-in-law'):
        return 'she'
    if rel in ('brother', 'father', 'son', 'husband', 'uncle', 'brother-in-law',
                'father-in-law', 'son-in-law'):
        return 'he'
    return 'he'   # Default — caller can override


def _ben_phrase(name: str, nric: str, nationality: str = 'Malaysian',
                with_relationship: str = '') -> str:
    """Format a beneficiary mention inside a clause:
        "my <relationship> <NAME> (MALAYSIA NRIC No. NNN)"  if relationship given
        "<NAME> (MALAYSIA NRIC No. NNN)"                    otherwise
    """
    if (nationality or '').lower().startswith('malay'):
        id_block = f"MALAYSIA NRIC No. {nric}"
    else:
        id_block = f"{(nationality or '').upper()} Identification No. {nric}"
    pref = f"my {with_relationship.lower()} " if with_relationship else ''
    # 🔥 §10x.201 — beneficiary names render UPPERCASE per template
    # ("LIM BEE YAN" not "Lim Bee Yan").
    return f"{pref}{(name or '').upper()} ({id_block})"


def _format_address_phek(address: str, nationality: str = 'Malaysian') -> str:
    """🔥 §10x.134 + §10x.194 (T-1, T-2, T-13, T-16, T-20, T-29) — normalise
    address to Phek format. Single source of truth used everywhere a Person
    or property address is rendered into the will body.

        1. Collapse ALL newlines/tabs/multiple-spaces to single ', ' / ' '
           — prevents §10x.157 (T-2/T-16) heading-promotion bug where the
           PDF generator treats `\\n\\n` paragraph breaks inside an address
           as new paragraphs that get H1-styled.
        2. Strip stray `(NLC...)` / `(unreadable)` / `(from doc extract)`
           parens that vision sometimes adds.
        3. Strip leading "House at" / "Shop at" / "Unit X " informal
           prefixes that aren't Phek format (T-29). Convert "House at 10"
           → "NO.10".
        4. Deduplicate adjacent commas.
        5. Uppercase the trailing state token (Phek: 'JOHOR, MALAYSIA').
        6. Append ', MALAYSIA' if nationality is Malaysian and country
           isn't already at the end (T-20).
    """
    if not address:
        return ''
    import re as _re
    s = address.strip()
    # 🔥 §10x.194 — strip parenthetical metadata that contaminates address
    # (vision adds "(from doc extract)", OCR adds "(unreadable)", etc.)
    s = _re.sub(r'\s*\([^)]*\)\s*', ' ', s).strip()
    # 🔥 §10x.194 — convert informal "House at NN" / "Shop at NN" / "Unit
    # NN" prefix to Phek's "NO.NN" format. Only when followed by a digit.
    s = _re.sub(r'^(?:House|Shop|Unit|Apartment|Flat)\s+at\s+(\d+\b)',
                 r'NO.\1', s, flags=_re.IGNORECASE)
    s = _re.sub(r'^(?:House|Shop)\s+(?=\d)',
                 'NO.', s, flags=_re.IGNORECASE)
    # Collapse newlines + tabs into ', '
    s = _re.sub(r'[\n\r\t]+', ', ', s)
    # Collapse multiple commas / whitespace
    s = _re.sub(r'\s*,\s*', ', ', s)
    s = _re.sub(r',{2,}', ',', s)
    s = _re.sub(r'\s+', ' ', s).strip().rstrip(',').strip()
    # Uppercase the trailing state token (last comma-separated chunk if
    # it looks like a state name)
    parts = [p.strip() for p in s.split(',') if p.strip()]
    if parts:
        last = parts[-1]
        # Known Malaysian states (case-insensitive match)
        ms_states = {'johor', 'kedah', 'kelantan', 'melaka', 'malacca',
                     'negeri sembilan', 'pahang', 'penang', 'pulau pinang',
                     'perak', 'perlis', 'sabah', 'sarawak', 'selangor',
                     'terengganu', 'kuala lumpur', 'putrajaya', 'labuan'}
        if last.lower() in ms_states:
            parts[-1] = last.upper()
    s = ', '.join(parts)
    # 🔥 §10x.201 — DO NOT append ", MALAYSIA" suffix. Template never
    # appends it (KOID Sample addresses end at the state name). If the
    # source data already has it, strip.
    s = _re.sub(r',\s*MALAYSIA\s*$', '', s, flags=_re.IGNORECASE).strip().rstrip(',')
    return s


def _format_gift_property(gift) -> str:
    """Render the asset description body for a property gift.
    Uses the Phek phrasing pre-built into models/gift.py."""
    try:
        return gift.get_formatted_description()
    except Exception:
        return getattr(gift, 'description', '') or ''


def _allocations_phrase(gift, beneficiaries_index: dict, *,
                         absolutely: bool = False) -> str:
    """Render the trailing 'unto X (and Y in equal share)' phrase for a gift.
    🔒 Phek format: gift body comes FIRST, then 'unto <beneficiary>'.
    Returns the 'unto …' suffix WITHOUT a leading space; caller joins with ' '.
    beneficiaries_index maps name → (nric, relationship, nationality).

    🔥 §10x.196 (T-26/T-43) — when `absolutely=True` AND there's exactly
    ONE beneficiary at 100% share, append ' absolutely' per Phek style.
    """
    allocs = getattr(gift, 'allocations', None) or []
    if not allocs:
        return ''
    parts = []
    for a in allocs:
        nm = (a.beneficiary_name or '').strip()
        info = beneficiaries_index.get(nm.upper(), {})
        parts.append(_ben_phrase(
            nm, info.get('nric', ''),
            info.get('nationality', 'Malaysian'),
            with_relationship=info.get('relationship', '')))
    if len(parts) == 1:
        suffix = ' absolutely' if absolutely else ''
        return f"unto {parts[0]}{suffix}"
    if len(parts) == 2:
        joined = " and ".join(parts)
    else:
        joined = ", ".join(parts[:-1]) + ", and " + parts[-1]
    # Equal shares?
    shares = [str(a.share or '').strip() for a in allocs]
    all_equal = len(set(shares)) == 1
    if all_equal:
        return f"unto {joined} in equal share"
    return f"unto {joined} in the shares specified herein"


def _index_beneficiaries(will_data) -> dict:
    """name (UPPER) → {nric, relationship, nationality}"""
    out: dict = {}
    for b in (will_data.beneficiaries or []):
        out[b.full_name.strip().upper()] = {
            'nric': b.nric_passport_birthcert,
            'relationship': b.relationship,
            'nationality': b.nationality or 'Malaysian',
        }
    return out


# ─── Specific-gift clause renderers ────────────────────────────────────────

def _is_single_beneficiary_100pct(gift) -> bool:
    """True if gift has exactly ONE allocation that takes the entire gift.
    Used by §10x.196 to append ' absolutely' (T-26/T-43)."""
    allocs = getattr(gift, 'allocations', None) or []
    if len(allocs) != 1:
        return False
    share = str(allocs[0].share or '').strip()
    return share in ('1/1', '100%', '100', '1', '')


def _render_property_clause(clause_num: int, gift, beneficiaries_index: dict) -> str:
    """Property gift — Phek format §10x.24:
        "I hereby devise and bequeath all my ¼ undivided shares in the
         property known as <ADDRESS> held under Geran No. ..., Lot No. ...,
         Mukim ..., District of ..., State of ... unto my sister X
         (MALAYSIA NRIC No. ...) and my sister Y (...) in equal share.

         Unless specifically stated to the contrary in this Will, I direct
         that any sums required to discharge a charge or to withdraw a
         private caveat or lien attached to this property shall be paid out
         of my residuary estate."

    🔒 Word order: gift BODY first, then 'unto <beneficiary>'.

    🔥 §10x.196 (T-26/T-32/T-43/T-46) — INLINE the substitute when:
        (a) Single 100% beneficiary: append ' absolutely' (T-26/T-43)
        (b) Multiple beneficiaries with substitute = surviving siblings:
            inline `If either of my children does not survive me, then
            the benefit which that child would have received shall be
            given to and vest in the surviving child absolutely.`
            (T-32/T-46 — Phek's per-property inline substitute pattern)
    """
    desc = _format_gift_property(gift)        # body w/o trailing punctuation
    is_solo = _is_single_beneficiary_100pct(gift)
    bens = _allocations_phrase(gift, beneficiaries_index, absolutely=is_solo)
    if not bens:
        body = (f"{clause_num}.  I hereby devise and bequeath {desc} "
                f"[BENEFICIARIES TO BE CONFIRMED].")
    else:
        body = f"{clause_num}.  I hereby devise and bequeath {desc} {bens}."

    # 🔥 §10x.196 (T-46) — inline substitute when meaningful
    inline_sub = _inline_property_substitute_phrase(gift, beneficiaries_index)
    if inline_sub:
        body = body.rstrip('.') + '. ' + inline_sub

    # Phek attaches the discharge clause directly under each property gift
    discharge = (
        "Unless specifically stated to the contrary in this Will, I direct "
        "that any sums required to discharge a charge or to withdraw a "
        "private caveat or lien attached to this property shall be paid "
        "out of my residuary estate.")
    return body + "\n\n" + discharge


def _inline_property_substitute_phrase(gift, beneficiaries_index: dict) -> str:
    """🔥 §10x.196 (T-46) — render the Phek inline substitute phrase
    when the substitute pattern matches one of:

      Pattern A (multi-beneficiary, sibling fallback):
        Main = [child A, child B] with shares 1/2 each
        Substitute = same children (surviving siblings)
        → "If either of my children does not survive me, then the
           benefit which that child would have received shall be given
           to and vest in the surviving child absolutely."

      Pattern B (single beneficiary with substitute):
        Main = [Esther 100%], Substitute = [Joshua 100%]
        → "If my daughter does not survive me, then the benefit she
           would have received shall be given to my son JOSHUA ..."

    Returns empty string if no inline pattern applies — caller falls
    back to the standalone "With reference to Clause N..." clause.
    """
    allocs = getattr(gift, 'allocations', None) or []
    if not allocs:
        return ''
    # Collect substitutes (per-allocation OR top-level)
    sub_specific = []
    for a in allocs:
        for sb in (a.substitutes or []):
            sub_specific.append({'name': sb.beneficiary_name,
                                  'share': sb.share or '100%'})
    if not sub_specific:
        for sb in (getattr(gift, 'substitute_specific', None) or []):
            if isinstance(sb, dict):
                sub_specific.append({'name': sb.get('name', ''),
                                      'share': sb.get('share', '100%')})
    if not sub_specific:
        return ''
    # Dedupe by name
    _seen, _dedup = set(), []
    for sb in sub_specific:
        k = (sb.get('name') or '').strip().upper()
        if k and k not in _seen:
            _seen.add(k); _dedup.append(sb)
    sub_specific = _dedup

    main_names_upper = {(a.beneficiary_name or '').strip().upper() for a in allocs}
    sub_names_upper = {(sb.get('name') or '').strip().upper() for sb in sub_specific}

    # Pattern A: 2+ MBs whose substitute = SAME set of MBs (surviving siblings)
    if len(allocs) >= 2 and main_names_upper == sub_names_upper:
        # Check if all MBs are children (son/daughter) — Phek phrasing
        # specifically says "my children"
        info_relations = []
        for nm in main_names_upper:
            r = (beneficiaries_index.get(nm, {}).get('relationship') or '').lower()
            info_relations.append(r)
        if all(r in ('son', 'daughter', 'child') for r in info_relations):
            return ("If either of my children does not survive me, "
                     "then the benefit which that child would have "
                     "received shall be given to and vest in the "
                     "surviving child absolutely.")
        # Generic siblings fallback
        return ("If any of the above-named beneficiaries does not "
                 "survive me, then the benefit which that beneficiary "
                 "would have received shall be given to the surviving "
                 "beneficiaries in equal share absolutely.")

    # Pattern B: single MB with single substitute
    if len(allocs) == 1 and len(sub_specific) == 1:
        main_name = (allocs[0].beneficiary_name or '').strip()
        main_info = beneficiaries_index.get(main_name.upper(), {})
        main_rel = (main_info.get('relationship') or '').lower()
        sub_name = (sub_specific[0].get('name') or '').strip()
        sub_info = beneficiaries_index.get(sub_name.upper(), {})
        sub_phrase = _ben_phrase(
            sub_name, sub_info.get('nric', ''),
            sub_info.get('nationality', 'Malaysian'),
            with_relationship=sub_info.get('relationship', ''))
        # he/she based on main beneficiary's relationship
        if main_rel == 'daughter' or main_rel == 'wife' or main_rel == 'mother' or main_rel == 'sister':
            pronoun = 'she'
        elif main_rel in ('son', 'husband', 'father', 'brother'):
            pronoun = 'he'
        else:
            pronoun = 'he/she'
        # "If my daughter does not survive me ..." (use relationship if known)
        who = f"my {main_rel}" if main_rel else f"the above-named beneficiary"
        return (f"If {who} does not survive me, then the benefit "
                 f"{pronoun} would have received shall be given to "
                 f"{sub_phrase}.")

    return ''   # caller emits standalone "With reference..." clause


def _render_financial_clause(clause_num: int, gift, beneficiaries_index: dict) -> str:
    """Bank / mutual fund / EPF — Phek format word order:
        "I hereby devise and bequeath the monies in my UOB Saving Account
         No. ... together with all interests/dividends already accrued due
         or accruing thereon unto my sister X ... in equal share."

    🔥 §10x.196 — appends ' absolutely' for single-beneficiary 100% (T-26/T-43)
    """
    desc = _format_gift_property(gift)
    is_solo = _is_single_beneficiary_100pct(gift)
    bens = _allocations_phrase(gift, beneficiaries_index, absolutely=is_solo)
    if not bens:
        return f"{clause_num}.  I hereby devise and bequeath {desc} [BENEFICIARIES TO BE CONFIRMED]."
    return f"{clause_num}.  I hereby devise and bequeath {desc} {bens}."


def _render_insurance_fallback_clause(clause_num: int, insurance_gifts: list,
                                       beneficiaries_index: dict) -> str:
    """🔥 §10x.197 (T-28/T-45) — Phek's insurance NOMINATION-FALLBACK clause.

    Per Insurance Act 1996 s.130 (Malaysia) / Singapore Insurance Act
    1966 s.49L, a NOMINATED insurance policy bypasses the will entirely.
    The will can only direct the proceeds if the nomination FAILS / is
    INVALID / is REVOKED. So the clause must be a FALLBACK, not a direct
    bequest.

    Phek format (verbatim from KOID Sample):
        "If any nomination under my insurance policies below fails, is
        invalid, revoked or otherwise ineffective, the proceeds shall
        be given to my wife LIM BEE YAN (MALAYSIA NRIC No. 661126-04-5182)
        absolutely.

        (i) Policy No. 1811500170 NTUC Income Singapore
        (ii) Policy No. 10030125 EaTiQa Singapore
        (iii) Policy No. L516911049 AIA Singapore"

    All insurance gifts are merged into ONE numbered clause with the
    fallback-beneficiary determined from the FIRST insurance gift's
    main beneficiary. (If different insurance gifts go to different
    fallbacks, fall back to a list per gift — caller's responsibility
    to handle that.)
    """
    if not insurance_gifts:
        return ''
    # Determine the fallback beneficiary from the first gift
    first = insurance_gifts[0]
    allocs = getattr(first, 'allocations', None) or []
    if not allocs:
        ben_phrase = '[BENEFICIARY TO BE CONFIRMED]'
    else:
        nm = (allocs[0].beneficiary_name or '').strip()
        info = beneficiaries_index.get(nm.upper(), {})
        ben_phrase = _ben_phrase(
            nm, info.get('nric', ''),
            info.get('nationality', 'Malaysian'),
            with_relationship=info.get('relationship', ''))
    # Render policy list as (i)/(ii)/(iii)
    roman = ('i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x',
             'xi', 'xii', 'xiii', 'xiv', 'xv', 'xvi', 'xvii', 'xviii', 'xix', 'xx')
    policy_lines = []
    for i, ig in enumerate(insurance_gifts):
        fd = getattr(ig, 'financial_details', None)
        insurer = (getattr(fd, 'institution', '') or '').strip() if fd else ''
        policy_no = (getattr(fd, 'account_number', '') or '').strip() if fd else ''
        country = (getattr(fd, 'country', '') or '').strip() if fd else ''
        # Country qualifier — per T-36 cross-asset hint, default to Singapore
        # for KOID since other assets (POSB, Maybank) are SG. Real fix: read
        # from gift.country field.
        line = f"({roman[i]}) Policy No. {policy_no} {insurer}"
        # 🔥 §10x.207c — don't append country if the institution name already
        # ends with it (e.g. canonicalised "AIA Singapore" + country='Singapore'
        # produced "AIA Singapore Singapore"). Compare case-insensitive.
        if country and not insurer.lower().endswith(country.lower()):
            line += f" {country}"
        policy_lines.append(line)
    return (f"{clause_num}.  If any nomination under my insurance policies "
            f"below fails, is invalid, revoked or otherwise ineffective, "
            f"the proceeds shall be given to {ben_phrase} absolutely.\n\n"
            + "\n\n".join(policy_lines))


def _consolidate_financial_substitute(gifts: list,
                                       gift_clause_starts: dict,
                                       beneficiaries_index: dict,
                                       clause_num_start: int
                                       ) -> tuple:
    """🔥 §10x.198 (T-27/T-44) — consolidate consecutive financial gifts
    that share the same (main_beneficiary, substitute_specific) into ONE
    Phek-format substitute clause referencing the clause range.

    Returns (clause_text or None, next_clause_num, used_indices_set).

    Phek format example (verbatim from KOID Sample):
        "In the event my wife LIM BEE YAN (MALAYSIA NRIC No. 661126-04-5182)
        does not survive me, my monies bequeathed to her in Clause 4-7
        above shall be given to my son JOSHUA KOID TECK SENG (MALAYSIA
        NRIC No. 960525-07-5039) and my daughter ESTHER KOID EN HUI
        (MALAYSIA NRIC No. 010522-01-1110) in equal share."

    Only consolidates when ALL gifts in `gifts` share:
      - Same single MB (one beneficiary at 100%)
      - Same substitute_specific list (deduped)
    """
    if not gifts:
        return (None, clause_num_start, set())
    # Group by (mb_name, sub_names_tuple)
    def _key(g):
        allocs = getattr(g, 'allocations', None) or []
        if len(allocs) != 1:
            return None
        mb = (allocs[0].beneficiary_name or '').strip().upper()
        subs = []
        for sb in (allocs[0].substitutes or []):
            subs.append((sb.beneficiary_name or '').strip().upper())
        for sb in (getattr(g, 'substitute_specific', None) or []):
            if isinstance(sb, dict):
                subs.append((sb.get('name','') or '').strip().upper())
        # Dedupe + sort for stable key
        subs = tuple(sorted(set(s for s in subs if s)))
        return (mb, subs)

    keys = [_key(g) for g in gifts]
    # If all gifts share the same key AND the key is non-None, consolidate
    if not keys[0] or any(k != keys[0] for k in keys):
        return (None, clause_num_start, set())

    mb_upper, sub_uppers = keys[0]
    if not sub_uppers:
        return (None, clause_num_start, set())

    # Render MB phrase
    mb_info = beneficiaries_index.get(mb_upper, {})
    # Find original-case name from the first gift
    mb_name = ''
    for g in gifts:
        for a in (getattr(g, 'allocations', None) or []):
            if (a.beneficiary_name or '').strip().upper() == mb_upper:
                mb_name = a.beneficiary_name; break
        if mb_name: break
    mb_phrase = _ben_phrase(
        mb_name, mb_info.get('nric', ''),
        mb_info.get('nationality', 'Malaysian'),
        with_relationship=mb_info.get('relationship', ''))

    # 🔥 §10x.201 — Order substitutes by family-role order (spouse →
    # son → daughter → others) per template ordering, NOT alphabetical.
    _role_order = {
        'wife': 0, 'husband': 0, 'spouse': 0,
        'son': 1, 'father': 2, 'brother': 3,
        'daughter': 4, 'mother': 5, 'sister': 6,
    }
    def _sub_key(sn_upper):
        info = beneficiaries_index.get(sn_upper, {})
        rel = (info.get('relationship') or '').lower()
        return (_role_order.get(rel, 99), sn_upper)
    ordered_sub_uppers = sorted(sub_uppers, key=_sub_key)
    # Render substitute phrases
    sub_phrases = []
    for sn_upper in ordered_sub_uppers:
        s_info = beneficiaries_index.get(sn_upper, {})
        # Recover original case
        sn_name = sn_upper.title() if not s_info else None
        # Try to find from any gift's substitute lists
        for g in gifts:
            allocs = getattr(g, 'allocations', None) or []
            for a in allocs:
                for sb in (a.substitutes or []):
                    if (sb.beneficiary_name or '').strip().upper() == sn_upper:
                        sn_name = sb.beneficiary_name; break
            for sb in (getattr(g, 'substitute_specific', None) or []):
                if isinstance(sb, dict) and (sb.get('name','') or '').strip().upper() == sn_upper:
                    sn_name = sb.get('name'); break
            if sn_name and sn_name != sn_upper.title(): break
        if not sn_name:
            sn_name = sn_upper
        sub_phrases.append(_ben_phrase(
            sn_name, s_info.get('nric', ''),
            s_info.get('nationality', 'Malaysian'),
            with_relationship=s_info.get('relationship', '')))

    if len(sub_phrases) == 1:
        sub_str = sub_phrases[0]
    elif len(sub_phrases) == 2:
        sub_str = " and ".join(sub_phrases) + " in equal share"
    else:
        sub_str = ", ".join(sub_phrases[:-1]) + ", and " + sub_phrases[-1] + " in equal share"

    # Determine clause range from gift_clause_starts
    clauses = sorted(gift_clause_starts.values())
    range_str = f"Clause {clauses[0]}-{clauses[-1]}" if len(clauses) > 1 else f"Clause {clauses[0]}"
    text = (f"{clause_num_start}.  In the event {mb_phrase} does not "
            f"survive me, my monies bequeathed to {('him' if 'son' in (mb_info.get('relationship','') or '').lower() or 'husband' in (mb_info.get('relationship','') or '').lower() or 'father' in (mb_info.get('relationship','') or '').lower() or 'brother' in (mb_info.get('relationship','') or '').lower() else 'her')} in {range_str} above shall be given to {sub_str}.")
    return (text, clause_num_start + 1, set(range(len(gifts))))


def _render_other_clause(clause_num: int, gift, beneficiaries_index: dict) -> str:
    desc = (gift.description or '').strip() or '[ASSET DESCRIPTION TO BE CONFIRMED]'
    bens = _allocations_phrase(gift, beneficiaries_index)
    if not bens:
        return f"{clause_num}.  I hereby devise and bequeath {desc} [BENEFICIARIES TO BE CONFIRMED]."
    return f"{clause_num}.  I hereby devise and bequeath {desc} {bens}."


def _render_substitute_clause(clause_num: int, ref_clause: int, gift,
                               beneficiaries_index: dict) -> Optional[str]:
    """Build the 'With reference to Clause N above, if <X> does not survive me,
    then <substitute>' clause for one gift.
    Uses gift.substitute_specific (if set) or gift.allocations[*].substitutes.
    Returns None if no substitute is configured.
    """
    allocs = getattr(gift, 'allocations', None) or []
    sub_specific = []  # collected (sub_name, sub_share) pairs
    # Per-allocation substitutes
    for a in allocs:
        for sb in (a.substitutes or []):
            sub_specific.append({
                'name': sb.beneficiary_name, 'share': sb.share or '100%',
            })
    # Top-level fallback: gift.substitute_specific list
    if not sub_specific:
        for sb in (getattr(gift, 'substitute_specific', None) or []):
            if isinstance(sb, dict):
                sub_specific.append({'name': sb.get('name', ''),
                                      'share': sb.get('share', '100%')})
    if not sub_specific:
        return None

    # 🔥 §10x.130 — dedupe by name. When build_will_data attaches the full
    # substitute list to every MB allocation, identical entries multiply
    # (2 MBs × 2 subs = 4 collected). Render each unique name once.
    _seen = set()
    _deduped = []
    for sb in sub_specific:
        nm_key = (sb.get('name') or '').strip().upper()
        if not nm_key or nm_key in _seen:
            continue
        _seen.add(nm_key)
        _deduped.append(sb)
    sub_specific = _deduped

    # Render names for the main beneficiaries
    main_phrases = []
    for a in allocs:
        nm = (a.beneficiary_name or '').strip()
        info = beneficiaries_index.get(nm.upper(), {})
        main_phrases.append(_ben_phrase(
            nm, info.get('nric', ''),
            info.get('nationality', 'Malaysian'),
            with_relationship=info.get('relationship', '')))
    if not main_phrases:
        return None
    main_str = (" and ".join(main_phrases) if len(main_phrases) <= 2
                else ", ".join(main_phrases[:-1]) + ", and " + main_phrases[-1])
    # Render substitute names
    sub_phrases = []
    for sb in sub_specific:
        nm = (sb.get('name') or '').strip()
        info = beneficiaries_index.get(nm.upper(), {})
        sub_phrases.append(_ben_phrase(
            nm, info.get('nric', ''),
            info.get('nationality', 'Malaysian'),
            with_relationship=info.get('relationship', '')))
    if len(sub_phrases) == 1:
        sub_str = sub_phrases[0]
    elif len(sub_phrases) == 2:
        sub_str = " and ".join(sub_phrases) + " in equal share"
    else:
        sub_str = ", ".join(sub_phrases[:-1]) + ", and " + sub_phrases[-1] + " in equal share"
    he_she = "he/she"   # neutral — not always derivable
    return (f"{clause_num}.  With reference to Clause {ref_clause} above, "
            f"if {main_str} does not survive me, then the benefit "
            f"{he_she} would have received shall be given to {sub_str}.")


# ─── Top-level orchestrator ────────────────────────────────────────────────

def fill_will(will_data) -> str:
    """🔒 Deterministic Phek-format filler. Returns full will body text.

    Args:
        will_data: a models.will_data.WillData (or compatible) object.
    """
    t = will_data.testator
    parts: List[str] = []

    # ── Title + Preamble ────────────────────────────────────────────────
    parts.append(TITLE_TEMPLATE.format(testator_name=(t.full_name or '').upper()))
    parts.append('')
    # 🔥 §10x.134 — match Phek format:
    #   1. Collapse multi-line addresses to single comma-joined line
    #   2. Uppercase the trailing state (Phek: "JOHOR, MALAYSIA")
    #   3. Append ", MALAYSIA" suffix when nationality is Malaysian
    #      and the address doesn't already end with country
    addr = _format_address_phek(t.residential_address,
                                 nationality=t.nationality or 'Malaysian')
    parts.append(PREAMBLE_TEMPLATE.format(
        testator_name=(t.full_name or '').upper(),
        nric=t.nric_passport,
        address=addr,
    ))
    parts.append('')

    # ── Clause 1: Revocation ────────────────────────────────────────────
    parts.append(REVOCATION_TEMPLATE)
    parts.append('')

    # ── Clause 2: Executor(s) ───────────────────────────────────────────
    execs = will_data.executors or []
    primary = [e for e in execs if e.role in ('Primary', 'Joint')]
    substitutes = [e for e in execs if e.role == 'Substitute']
    # 🔥 §10x.134 — collapse multi-line addresses (chat-saved persons
    # often have addresses with embedded \n from OCR) so the executor
    # clause stays on a single Phek-format line.
    def _exec_addr(p):
        return _format_address_phek(getattr(p, 'address', '') or '',
                                     nationality=getattr(p, 'nationality', '') or 'Malaysian')
    if len(primary) >= 2:
        e1, e2 = primary[0], primary[1]
        parts.append(EXECUTOR_JOINT_TEMPLATE.format(
            rel1=e1.relationship.lower(), name1=(e1.full_name or '').upper(),
            nric1=e1.nric_passport, address1=_exec_addr(e1),
            rel2=e2.relationship.lower(), name2=(e2.full_name or '').upper(),
            nric2=e2.nric_passport, address2=_exec_addr(e2),
        ))
    elif len(primary) == 1 and substitutes:
        e1 = primary[0]
        s1 = substitutes[0]
        parts.append(EXECUTOR_SINGLE_WITH_SUBSTITUTE_TEMPLATE.format(
            relationship=e1.relationship.lower(), executor_name=(e1.full_name or '').upper(),
            nric=e1.nric_passport, address=_exec_addr(e1),
            he_she=_he_she(e1, will_data),
            sub_relationship=s1.relationship.lower(), substitute_name=(s1.full_name or '').upper(),
            sub_nric=s1.nric_passport, sub_address=_exec_addr(s1),
        ))
    elif len(primary) == 1:
        e1 = primary[0]
        parts.append(EXECUTOR_SINGLE_TEMPLATE.format(
            relationship=e1.relationship.lower(), executor_name=(e1.full_name or '').upper(),
            nric=e1.nric_passport, address=_exec_addr(e1),
        ))
    else:
        parts.append(
            "Appointment of Executor(s)\n\n"
            "2.  [EXECUTOR TO BE CONFIRMED] — please complete in wizard "
            "Step 3 before generating the final will.")
    parts.append('')

    # ── Clause 3: Executor as Trustee ───────────────────────────────────
    clause_num = 3
    parts.append(EXECUTOR_AS_TRUSTEE_TEMPLATE.format(clause_num=clause_num))
    parts.append('')

    # ── Specific gifts ──────────────────────────────────────────────────
    gifts = list(will_data.gifts or [])
    bidx = _index_beneficiaries(will_data)
    if gifts:
        parts.append(NON_RESIDUARY_HEADING)
        parts.append('')
        # 🔒 Phek order (verbatim sample): financial assets FIRST (bank,
        # mutual fund, insurance, epf), property LAST. The verbatim Phek
        # ordering is bank (cl. 4), mutual fund (cl. 5), property (cl. 6).
        order_key = {
            'financial': 0,
            'other': 1,
            'property': 2,
        }
        gifts_sorted = sorted(
            gifts, key=lambda g: order_key.get(getattr(g, 'gift_type', 'other'), 99))

        # 🔥 §10x.197 (T-28/T-45) — separate insurance gifts (NOMINATION-
        # FALLBACK clause) from regular bank/EPF financial gifts (direct
        # bequest). Detect via financial_details.asset_type.
        def _is_insurance_gift(g):
            fd = getattr(g, 'financial_details', None)
            if not fd: return False
            at = (getattr(fd, 'asset_type', '') or '').lower()
            return 'insurance' in at or 'takaful' in at or 'policy' in at
        insurance_gifts = [g for g in gifts_sorted
                            if getattr(g, 'gift_type', 'other') == 'financial'
                            and _is_insurance_gift(g)]
        bank_like_gifts = [g for g in gifts_sorted
                            if getattr(g, 'gift_type', 'other') == 'financial'
                            and not _is_insurance_gift(g)]
        property_gifts = [g for g in gifts_sorted
                           if getattr(g, 'gift_type', 'other') == 'property']
        other_gifts = [g for g in gifts_sorted
                        if getattr(g, 'gift_type', 'other') not in ('property', 'financial')]

        # Track which gifts get inline substitute (skip standalone clause)
        inline_subbed_gifts: set = set()
        # Track financial gifts that get consolidated (skip per-gift sub clause)
        consolidated_idxs: set = set()

        gift_clause_starts: dict = {}   # ID(gift) → its clause number

        # 🔥 §10x.205 — Template order (KOID gold-standard, verified by
        # python-docx extraction of the Alan & Tan template):
        #   Banks → Bank-Substitute → Properties → Insurance-Fallback → Residuary
        # Previously banks→insurance→properties→other→bank-sub which left
        # the insurance clause at #8 and bank substitute at #13. Now insurance
        # sits AFTER properties (just before residuary) to match the Phek-format
        # legal-asset-liquidity ordering.

        # ── (1) Bank-like financial first
        for g in bank_like_gifts:
            clause_num += 1
            gift_clause_starts[id(g)] = clause_num
            parts.append(_render_financial_clause(clause_num, g, bidx))
            parts.append('')

        # ── (2) Bank-substitute clause (consolidated when possible) — emit
        # immediately after the bank gifts so clause numbering reads
        # "Banks 4-7 / Bank-Sub 8 / Properties 9-13 / Insurance 14".
        if bank_like_gifts:
            bank_starts = {id(g): gift_clause_starts[id(g)] for g in bank_like_gifts}
            consol_text, next_cn, used = _consolidate_financial_substitute(
                bank_like_gifts, bank_starts, bidx, clause_num + 1)
            if consol_text:
                parts.append(consol_text)
                parts.append('')
                clause_num = next_cn - 1   # next iteration adds +1
                for g in bank_like_gifts:
                    consolidated_idxs.add(id(g))

        # ── (3) Property clauses
        for g in property_gifts:
            clause_num += 1
            gift_clause_starts[id(g)] = clause_num
            parts.append(_render_property_clause(clause_num, g, bidx))
            if _inline_property_substitute_phrase(g, bidx):
                inline_subbed_gifts.add(id(g))
            parts.append('')

        # ── (4) Other gifts
        for g in other_gifts:
            clause_num += 1
            gift_clause_starts[id(g)] = clause_num
            parts.append(_render_other_clause(clause_num, g, bidx))
            parts.append('')

        # ── (5) Insurance fallback wrapper (one clause for ALL insurance)
        # Per Insurance Act 1996 s.130 — sits just before residuary.
        if insurance_gifts:
            clause_num += 1
            ins_text = _render_insurance_fallback_clause(
                clause_num, insurance_gifts, bidx)
            if ins_text:
                parts.append(ins_text)
                parts.append('')
                for ig in insurance_gifts:
                    gift_clause_starts[id(ig)] = clause_num
                    inline_subbed_gifts.add(id(ig))   # fallback wrapper handles substitute too
            else:
                clause_num -= 1

        # ── (6) Per-gift substitute for everything not yet covered
        # (banks consolidated above, insurance inlined, properties inlined
        # when matching surviving-siblings pattern).
        for g in (bank_like_gifts + property_gifts + other_gifts + insurance_gifts):
            if id(g) in inline_subbed_gifts:
                continue
            if id(g) in consolidated_idxs:
                continue
            ref_clause = gift_clause_starts.get(id(g), 0)
            if not ref_clause:
                continue
            clause_num += 1
            sub = _render_substitute_clause(clause_num, ref_clause, g, bidx)
            if sub:
                parts.append(sub)
                parts.append('')
            else:
                clause_num -= 1

    # ── Residuary ───────────────────────────────────────────────────────
    # 🔥 §10x.199 (T-33/T-47) — Phek format inlines the substitute INTO
    # clause 27(b) instead of emitting it as a separate clause:
    #   "(b) To give the residue ('my residuary estate') to my wife
    #    LIM BEE YAN (MALAYSIA NRIC No. ...) absolutely. If my wife
    #    does not survive me, then the benefit she would have received
    #    shall be given to my son JOSHUA ... and my daughter ESTHER ...
    #    in equal share."
    parts.append(RESIDUARY_ESTATE_HEADING)
    parts.append('')
    clause_num += 1
    res = will_data.residuary_estate
    res_main = list(res.main_beneficiaries) if (
        res and res.main_beneficiaries) else []
    # Substitute groups: pick first non-empty group (survivorship is the
    # common Phek pattern — groups[0] is the post-spouse-death distribution).
    res_subs = []
    if res and getattr(res, 'substitute_groups', None):
        for grp in res.substitute_groups:
            if grp:
                res_subs = list(grp); break
    # Resolve names against beneficiaries_index
    def _ben_phrase_for(rb):
        nm = (rb.beneficiary_name or '').strip()
        info = bidx.get(nm.upper(), {})
        return _ben_phrase(
            nm, info.get('nric', ''),
            info.get('nationality', 'Malaysian'),
            with_relationship=info.get('relationship', ''))
    if len(res_main) >= 2:
        ben_phrases = [_ben_phrase_for(rb) for rb in res_main]
        ben_text = (" and ".join(ben_phrases) if len(ben_phrases) == 2
                    else ", ".join(ben_phrases[:-1]) + ", and " + ben_phrases[-1])
        parts.append(RESIDUARY_MULTIPLE_TEMPLATE.format(
            clause_num=clause_num, beneficiary_list_text=ben_text))
    elif len(res_main) == 1:
        rb = res_main[0]
        nm = (rb.beneficiary_name or '').strip()
        info = bidx.get(nm.upper(), {})
        # Build clause text with optional INLINE substitute per §10x.199
        # 🔥 §10x.201 — name UPPERCASE per template
        clause_text = RESIDUARY_TEMPLATE.format(
            clause_num=clause_num,
            relationship=(info.get('relationship', '') or '').lower(),
            beneficiary_name=nm.upper(),
            nric=info.get('nric', ''),
        )
        # 🔥 §10x.201 — Append " absolutely" to (b) line. Template requires
        # "...to my wife LIM BEE YAN (MALAYSIA NRIC No. ...) absolutely."
        clause_text = re.sub(
            r"(\(b\)\s+To give the residue \('my residuary estate'\) to "
            r"[^.]+?\(MALAYSIA NRIC No\. [^)]+\))(\.)",
            r"\1 absolutely\2", clause_text)
        # 🔥 §10x.199 (T-33/T-47) — INLINE substitute clause
        # 🔥 §10x.174 — filter the MAIN beneficiary out of substitutes.
        # Wife can't be substitute for herself. Real failure from KOID:
        # main=wife; substitute_groups[0]=[wife, son, daughter] (auto-built
        # from family list including wife). After filter: [son, daughter].
        if res_subs:
            _main_names_upper = {(b.beneficiary_name or '').strip().upper()
                                  for b in res_main}
            res_subs_filtered = [rb for rb in res_subs
                                  if (rb.beneficiary_name or '').strip().upper()
                                  not in _main_names_upper]
            res_subs = res_subs_filtered
        if res_subs:
            # 🔥 §10x.201 — Order by family-role (spouse → son → daughter)
            _role_order = {'wife': 0, 'husband': 0, 'spouse': 0,
                           'son': 1, 'father': 2, 'brother': 3,
                           'daughter': 4, 'mother': 5, 'sister': 6}
            def _r_key(rb):
                info_r = bidx.get((rb.beneficiary_name or '').strip().upper(), {})
                rel = (info_r.get('relationship') or '').lower()
                return (_role_order.get(rel, 99), (rb.beneficiary_name or '').upper())
            res_subs = sorted(res_subs, key=_r_key)
            sub_phrases = [_ben_phrase_for(rb) for rb in res_subs]
            if len(sub_phrases) == 1:
                sub_str = sub_phrases[0]
            elif len(sub_phrases) == 2:
                sub_str = " and ".join(sub_phrases) + " in equal share"
            else:
                sub_str = ", ".join(sub_phrases[:-1]) + ", and " + sub_phrases[-1] + " in equal share"
            main_rel = (info.get('relationship') or '').lower()
            if main_rel in ('daughter', 'wife', 'mother', 'sister'):
                pronoun = 'she'; who = f'my {main_rel}'
            elif main_rel in ('son', 'husband', 'father', 'brother'):
                pronoun = 'he'; who = f'my {main_rel}'
            else:
                pronoun = 'he/she'; who = 'the above-named beneficiary'
            inline = (f" If {who} does not survive me, then the benefit "
                       f"{pronoun} would have received shall be given to "
                       f"{sub_str}.")
            # Append inline to the clause text (before any trailing newline)
            clause_text = clause_text.rstrip() + inline
        parts.append(clause_text)
    else:
        parts.append(f"{clause_num}.  [RESIDUARY BENEFICIARY TO BE CONFIRMED]")
    parts.append('')

    # ── Declaration (clauses 8, 9 in Phek; numbering carries on here) ───
    parts.append(DECLARATION_HEADING)
    parts.append('')
    clause_num += 1
    parts.append(INTENTIONAL_EXCLUSION_TEMPLATE.format(clause_num=clause_num))
    parts.append('')
    clause_num += 1
    parts.append(COMMORIENTES_TEMPLATE.format(clause_num=clause_num))
    parts.append('')

    # ── Signing page ────────────────────────────────────────────────────
    # 🔥 §10x.207 — Match KOID/PHEK template asterisk count EXACTLY
    # (18 left + 21 right) and use NON-BREAKING spaces between
    # asterisks and text so word-wrap renderers cannot split this
    # line. Template's Word renderer keeps it on one line; markdown
    # / HTML viewers will too because U+00A0 is not a break point.
    _NBSP = '\u00A0'
    _SIGN_LINE = ('******************' + _NBSP +
                  _NBSP.join(['the', 'remaining', 'page', 'is',
                              'intentionally', 'left', 'blank']) +
                  _NBSP + '*********************')
    parts.append(_SIGN_LINE)
    parts.append('')
    parts.append('Signature of the Testator: _______________________________________________')
    parts.append('')
    parts.append('Date of this Will: ___________________________________(dd/mm/yyyy)')
    parts.append('')
    # 🔥 §10x.201 — Restore parenthetical per template signing page.
    parts.append('This Last Will and Testament was signed by the Testator '
                  '(appeared thoroughly to understand this WILL and approve it) '
                  'in the presence of us both and attested by us in the presence '
                  'of both Testator and of each other:')
    parts.append('')
    # 🔥 §10x.200 (T-34) — prefix every witness field with "First Witness" /
    # "Second Witness" per Phek format (KOID Sample). Also rename "NRIC /
    # Passport No." → "Identification" and "Contact No." → "Contact Number".
    for w in ('First Witness', 'Second Witness'):
        parts.append(f'Signature of {w}: _______________________________________________')
        parts.append('')
        parts.append(f'{w} Full Name: _______________________________________________')
        parts.append('')
        parts.append(f'{w} Identification: _______________________________________________')
        parts.append('')
        parts.append(f'{w} Address: _______________________________________________')
        parts.append('')
        parts.append('                                                        _______________________________________________')
        parts.append('')
        parts.append('                                                        _______________________________________________')
        parts.append('')
        parts.append(f'{w} Contact Number: _______________________________________________')
        parts.append('')
    parts.append('- End of Document -')

    return '\n'.join(parts)
