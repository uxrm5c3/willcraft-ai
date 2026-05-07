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
    return f"{pref}{name} ({id_block})"


def _format_gift_property(gift) -> str:
    """Render the asset description body for a property gift.
    Uses the Phek phrasing pre-built into models/gift.py."""
    try:
        return gift.get_formatted_description()
    except Exception:
        return getattr(gift, 'description', '') or ''


def _allocations_phrase(gift, beneficiaries_index: dict) -> str:
    """Render '<X> and <Y> in equal shares' (or single ben) for a gift.
    beneficiaries_index maps name → (nric, relationship, nationality).
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
        return f"unto {parts[0]}"
    if len(parts) == 2:
        joined = " and ".join(parts)
    else:
        joined = ", ".join(parts[:-1]) + ", and " + parts[-1]
    # Equal shares?
    shares = [str(a.share or '').strip() for a in allocs]
    all_equal = len(set(shares)) == 1
    if all_equal:
        return f"unto {joined} in equal shares"
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

def _render_property_clause(clause_num: int, gift, beneficiaries_index: dict) -> str:
    """Property gift clause — Phek format §10x.24:
        "I hereby devise and bequeath unto X all my ¼ undivided shares in the
         property known as ... held under Geran No. ..., Lot No. ..., Mukim ...,
         District of ..., State of ....

         Unless specifically stated to the contrary in this Will, I direct
         that any sums required to discharge a charge or to withdraw a
         private caveat or lien attached to this property shall be paid out
         of my residuary estate."
    """
    desc = _format_gift_property(gift)        # body w/o trailing punctuation
    bens = _allocations_phrase(gift, beneficiaries_index)
    if not bens:
        body = (f"{clause_num}.  I hereby devise and bequeath {desc} "
                f"[BENEFICIARIES TO BE CONFIRMED].")
    else:
        body = f"{clause_num}.  I hereby devise and bequeath {bens} {desc}."
    # Phek attaches the discharge clause directly under each property gift
    discharge = (
        "Unless specifically stated to the contrary in this Will, I direct "
        "that any sums required to discharge a charge or to withdraw a "
        "private caveat or lien attached to this property shall be paid "
        "out of my residuary estate.")
    return body + "\n\n" + discharge


def _render_financial_clause(clause_num: int, gift, beneficiaries_index: dict) -> str:
    """Banks / insurance / EPF / mutual fund — financial assets.
    The Phek phrasing comes from models/gift.py::FinancialDetails.to_formatted_description.
    Ends with "." per Phek format.
    """
    desc = _format_gift_property(gift)
    bens = _allocations_phrase(gift, beneficiaries_index)
    if not bens:
        return f"{clause_num}.  I hereby devise and bequeath {desc} [BENEFICIARIES TO BE CONFIRMED]."
    return f"{clause_num}.  I hereby devise and bequeath {bens} {desc}."


def _render_other_clause(clause_num: int, gift, beneficiaries_index: dict) -> str:
    desc = (gift.description or '').strip() or '[ASSET DESCRIPTION TO BE CONFIRMED]'
    bens = _allocations_phrase(gift, beneficiaries_index)
    if not bens:
        return f"{clause_num}.  I hereby devise and bequeath {desc} [BENEFICIARIES TO BE CONFIRMED]."
    return f"{clause_num}.  I hereby devise and bequeath {bens} {desc}."


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
        sub_str = " and ".join(sub_phrases) + " in equal shares"
    else:
        sub_str = ", ".join(sub_phrases[:-1]) + ", and " + sub_phrases[-1] + " in equal shares"
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
    parts.append(TITLE_TEMPLATE.format(testator_name=t.full_name))
    parts.append('')
    parts.append(PREAMBLE_TEMPLATE.format(
        testator_name=t.full_name,
        nric=t.nric_passport,
        address=t.residential_address,
    ))
    parts.append('')

    # ── Clause 1: Revocation ────────────────────────────────────────────
    parts.append(REVOCATION_TEMPLATE)
    parts.append('')

    # ── Clause 2: Executor(s) ───────────────────────────────────────────
    execs = will_data.executors or []
    primary = [e for e in execs if e.role in ('Primary', 'Joint')]
    substitutes = [e for e in execs if e.role == 'Substitute']
    if len(primary) >= 2:
        e1, e2 = primary[0], primary[1]
        parts.append(EXECUTOR_JOINT_TEMPLATE.format(
            rel1=e1.relationship.lower(), name1=e1.full_name,
            nric1=e1.nric_passport, address1=e1.address,
            rel2=e2.relationship.lower(), name2=e2.full_name,
            nric2=e2.nric_passport, address2=e2.address,
        ))
    elif len(primary) == 1 and substitutes:
        e1 = primary[0]
        s1 = substitutes[0]
        parts.append(EXECUTOR_SINGLE_WITH_SUBSTITUTE_TEMPLATE.format(
            relationship=e1.relationship.lower(), executor_name=e1.full_name,
            nric=e1.nric_passport, address=e1.address,
            he_she=_he_she(e1, will_data),
            sub_relationship=s1.relationship.lower(), substitute_name=s1.full_name,
            sub_nric=s1.nric_passport, sub_address=s1.address,
        ))
    elif len(primary) == 1:
        e1 = primary[0]
        parts.append(EXECUTOR_SINGLE_TEMPLATE.format(
            relationship=e1.relationship.lower(), executor_name=e1.full_name,
            nric=e1.nric_passport, address=e1.address,
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

        gift_clause_starts: dict = {}   # gift index → its clause number
        for idx, g in enumerate(gifts_sorted):
            gtype = getattr(g, 'gift_type', 'other')
            if gtype == 'property':
                clause_num += 1
                gift_clause_starts[idx] = clause_num
                parts.append(_render_property_clause(clause_num, g, bidx))
            elif gtype == 'financial':
                clause_num += 1
                gift_clause_starts[idx] = clause_num
                parts.append(_render_financial_clause(clause_num, g, bidx))
            else:
                clause_num += 1
                gift_clause_starts[idx] = clause_num
                parts.append(_render_other_clause(clause_num, g, bidx))
            parts.append('')

        # ── Substitute clauses (one per gift that has substitutes) ─────
        for idx, g in enumerate(gifts_sorted):
            ref_clause = gift_clause_starts[idx]
            clause_num += 1
            sub = _render_substitute_clause(clause_num, ref_clause, g, bidx)
            if sub:
                parts.append(sub)
                parts.append('')
            else:
                # No substitute for this gift — roll back the clause counter
                clause_num -= 1

    # ── Residuary ───────────────────────────────────────────────────────
    parts.append(RESIDUARY_ESTATE_HEADING)
    parts.append('')
    clause_num += 1
    res = will_data.residuary_estate
    res_main = list(res.main_beneficiaries) if (
        res and res.main_beneficiaries) else []
    # Resolve names against beneficiaries_index (residuary entries don't carry
    # NRIC / relationship; we look those up so the clause renders with full IDs)
    if len(res_main) >= 2:
        ben_phrases = []
        for rb in res_main:
            nm = (rb.beneficiary_name or '').strip()
            info = bidx.get(nm.upper(), {})
            ben_phrases.append(_ben_phrase(
                nm, info.get('nric', ''),
                info.get('nationality', 'Malaysian'),
                with_relationship=info.get('relationship', '')))
        ben_text = (" and ".join(ben_phrases) if len(ben_phrases) == 2
                    else ", ".join(ben_phrases[:-1]) + ", and " + ben_phrases[-1])
        parts.append(RESIDUARY_MULTIPLE_TEMPLATE.format(
            clause_num=clause_num, beneficiary_list_text=ben_text))
    elif len(res_main) == 1:
        rb = res_main[0]
        nm = (rb.beneficiary_name or '').strip()
        info = bidx.get(nm.upper(), {})
        parts.append(RESIDUARY_TEMPLATE.format(
            clause_num=clause_num,
            relationship=(info.get('relationship', '') or '').lower(),
            beneficiary_name=nm,
            nric=info.get('nric', ''),
        ))
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
    parts.append('********************the remaining page is intentionally left blank*********************')
    parts.append('')
    parts.append('Signature of the Testator: _______________________________________________')
    parts.append('')
    parts.append('Date of this Will: ___________________________________(dd/mm/yyyy)')
    parts.append('')
    parts.append('This Last Will and Testament was signed by the Testator in the '
                  'presence of us both and attested by us in the presence of both '
                  'Testator and of each other:')
    parts.append('')
    for w in ('First Witness', 'Second Witness'):
        parts.append(f'Signature of {w}: _______________________________________________')
        parts.append('')
        parts.append('Full Name: _______________________________________________')
        parts.append('')
        parts.append('NRIC / Passport No.: _______________________________________________')
        parts.append('')
        parts.append('Address: _______________________________________________')
        parts.append('')
        parts.append('         _______________________________________________')
        parts.append('')
        parts.append('         _______________________________________________')
        parts.append('')
        parts.append('Contact No.: _______________________________________________')
        parts.append('')
    parts.append('- End of Document -')

    return '\n'.join(parts)
