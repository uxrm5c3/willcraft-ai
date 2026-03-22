"""AI Will Drafting Engine using Claude API."""

import re
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, MAX_TOKENS
from ai.prompts.system_prompt import SYSTEM_PROMPT


def _to_fraction(value: str) -> str:
    """Convert a share value to fraction. '40' -> '4/10', '31' -> '31/100', '1/3' -> '1/3'."""
    if not value or value == '-' or value == '?':
        return value
    s = str(value).strip().rstrip('%')
    if '/' in s:
        return s
    try:
        n = int(float(s))
        if n == 100:
            return "1/1"
        if n % 10 == 0:
            return f"{n // 10}/10"
        return f"{n}/100"
    except (ValueError, ZeroDivisionError):
        return s


def _is_malaysian_nric(id_str: str) -> bool:
    """Check if an ID string is a Malaysian NRIC (12 digits, with optional dashes)."""
    cleaned = re.sub(r'[-\s]', '', id_str)
    return bool(re.match(r'^\d{12}$', cleaned))


def format_id_for_will(nric_passport: str, nationality: str = "Malaysian") -> str:
    """Format person ID for will text in parentheses.
    - Malaysian NRIC: 'MALAYSIA (NRIC No. 123456-01-1234)'
    - Foreign ID: '([COUNTRY] Identification No. AB1234567)'
    """
    if _is_malaysian_nric(nric_passport):
        return f"MALAYSIA (NRIC No. {nric_passport})"
    else:
        nat = (nationality or 'Malaysian').upper()
        # Map common nationality values to country names
        nat_map = {
            'MALAYSIAN': 'MALAYSIA', 'SINGAPOREAN': 'SINGAPORE',
            'INDONESIAN': 'INDONESIA', 'THAI': 'THAILAND',
            'BRITISH': 'UNITED KINGDOM', 'AMERICAN': 'UNITED STATES',
            'AUSTRALIAN': 'AUSTRALIA', 'INDIAN': 'INDIA', 'CHINESE': 'CHINA',
            'JAPANESE': 'JAPAN', 'KOREAN': 'SOUTH KOREA',
            'FILIPINO': 'PHILIPPINES', 'VIETNAMESE': 'VIETNAM',
            'GERMAN': 'FEDERAL REPUBLIC OF GERMANY',
            'FRENCH': 'FRANCE', 'ITALIAN': 'ITALY', 'SPANISH': 'SPAIN',
            'DUTCH': 'NETHERLANDS', 'SWISS': 'SWITZERLAND',
            'CANADIAN': 'CANADA', 'NEW ZEALANDER': 'NEW ZEALAND',
        }
        country = nat_map.get(nat, nat)
        return f"({country} Identification No. {nric_passport})"


def format_will_data(will_data) -> str:
    """Convert WillData into a structured prompt for Claude."""
    sections = []

    # Section A: Testator
    t = will_data.testator
    sections.append(f"""## TESTATOR INFORMATION
- Full Name: {t.full_name}
- NRIC/Passport: {t.nric_passport}
- Address: {t.residential_address}
- Nationality: {t.nationality}
- Country of Residence: {t.country_of_residence}
- Date of Birth: {t.date_of_birth}
- Occupation: {t.occupation}
- Religion: {t.religion or 'Not specified'}
- Gender: {t.gender}
- Marital Status: {t.marital_status}
- Property Coverage: {t.property_coverage}""")

    if t.contemplation_of_marriage:
        sections.append(f"""
## CONTEMPLATION OF MARRIAGE
- Fiancé/Fiancée: {t.fiance_name}
- NRIC/Passport: {t.fiance_nric}""")

    if t.special_circumstances:
        sections.append(f"""
## SPECIAL CIRCUMSTANCES
- Circumstances: {', '.join(t.special_circumstances)}
- Translator: {t.translator_name} (NRIC: {t.translator_nric})
- Language: {t.translator_language}""")

    # Section B: Executors
    if will_data.executors:
        exec_lines = []
        for i, e in enumerate(will_data.executors, 1):
            is_corp = getattr(e, 'is_corporate', False) or (hasattr(e, 'relationship') and e.relationship == 'Corporate Trustee')
            if is_corp:
                exec_lines.append(f"  {i}. {e.full_name} (Company No. {e.nric_passport}), {e.address}, Type: CORPORATE TRUSTEE, Role: {e.role}")
            else:
                id_str = format_id_for_will(e.nric_passport, getattr(e, 'nationality', 'Malaysian'))
                exec_lines.append(f"  {i}. {e.full_name} ({id_str}), {e.address}, Relationship: {e.relationship}, Role: {e.role}")
        sections.append(f"""
## EXECUTORS
{chr(10).join(exec_lines)}""")

    # Section B2: Trustees
    if will_data.trustee_same_as_executor:
        sections.append("""
## TRUSTEES
- Trustees: Same as Executors (Executors shall also act as Trustees)
- INCLUDE the "Executor as Trustee" clause in the will""")
    elif will_data.trustees:
        trustee_lines = []
        for i, tr in enumerate(will_data.trustees, 1):
            id_str = format_id_for_will(tr.nric_passport, getattr(tr, 'nationality', 'Malaysian'))
            trustee_lines.append(f"  {i}. {tr.full_name} ({id_str}), {tr.address}, Relationship: {tr.relationship}")
        sub_trustees = will_data.substitute_trustees or ([will_data.substitute_trustee] if will_data.substitute_trustee else [])
        for j, st in enumerate(sub_trustees, 1):
            id_str = format_id_for_will(st.nric_passport, getattr(st, 'nationality', 'Malaysian'))
            trustee_lines.append(f"  Substitute {j}: {st.full_name} ({id_str}), {st.address}, Relationship: {st.relationship}")
        sections.append(f"""
## TRUSTEES (SEPARATE FROM EXECUTORS)
{chr(10).join(trustee_lines)}""")
    else:
        sections.append("""
## TRUSTEES
- NO TRUSTEE APPOINTED. Do NOT include the "Executor as Trustee" clause.
- Do NOT use "my Trustee" language in the residuary estate clause. Use "my Executor" or plain language instead.""")

    # Section C: Guardians
    if will_data.guardians:
        guard_lines = []
        for i, g in enumerate(will_data.guardians, 1):
            id_str = format_id_for_will(g.nric_passport, getattr(g, 'nationality', 'Malaysian'))
            guard_lines.append(f"  {i}. {g.full_name} ({id_str}), {g.address}, Relationship: {g.relationship}, Role: {g.role}")
        sections.append(f"""
## GUARDIANS OF MINOR CHILDREN
{chr(10).join(guard_lines)}""")

        if will_data.guardian_allowance:
            ga = will_data.guardian_allowance
            sections.append(f"""
## GUARDIAN ALLOWANCE
- Payment Mode: {ga.payment_mode}
- Amount: RM {ga.amount or 'Not specified'}
- Until children attain age: {ga.until_age or 'Not specified'}
- Source of Payment: {ga.source_of_payment or 'Not specified'}""")

    # Section D: Beneficiaries
    if will_data.beneficiaries:
        ben_lines = []
        for i, b in enumerate(will_data.beneficiaries, 1):
            id_str = format_id_for_will(b.nric_passport_birthcert, getattr(b, 'nationality', 'Malaysian'))
            ben_lines.append(f"  {i}. {b.full_name} ({id_str}), Relationship: {b.relationship}")
        sections.append(f"""
## LIST OF BENEFICIARIES
{chr(10).join(ben_lines)}""")

    # Section E: Specific Gifts
    if will_data.gifts:
        gift_lines = []
        for i, g in enumerate(will_data.gifts, 1):
            gift_lines.append(f"  Gift {i} ({g.gift_type}): {g.get_formatted_description()}")
            # Property-specific details (ownership & encumbrance on Gift model, not PropertyDetails)
            if g.gift_type == 'property' and g.property_details:
                own = getattr(g, 'ownership_type', 'sole') or 'sole'
                if own == 'joint':
                    share = getattr(g, 'testator_share', '?') or '?'
                    gift_lines.append(f"    Ownership: JOINT — testator's {share} undivided share")
                enc = getattr(g, 'encumbrance_status', 'clean') or 'clean'
                if enc == 'encumbered':
                    debt_src = getattr(g, 'debt_source', 'residuary') or 'residuary'
                    gift_lines.append(f"    Encumbrance: HAS LOAN — pay from {debt_src}")
                else:
                    gift_lines.append(f"    Encumbrance: CLEAN — do NOT include charge/lien discharge clause")
            # Financial-specific details (account_ownership on Gift model)
            if g.gift_type == 'financial' and g.financial_details:
                acc_own = getattr(g, 'account_ownership', 'individual') or 'individual'
                if acc_own == 'joint':
                    gift_lines.append(f"    Account: JOINT ACCOUNT — use 'my share of the moneys in my joint account'")
            if g.subject_to_trust:
                gift_lines.append(f"    (Subject to Testamentary Trust)")
            if g.subject_to_guardian_allowance:
                gift_lines.append(f"    (Subject to Guardian Allowance)")
            if getattr(g, 'sell_property', False) and g.gift_type == 'property':
                gift_lines.append(f"    SELL DIRECTIVE: Direct executor to sell property and distribute net proceeds of sale to beneficiaries")
            for a in g.allocations:
                gift_lines.append(f"    - {a.beneficiary_name}: {_to_fraction(a.share)} (Main Beneficiary)")
            # Substitute beneficiary instructions
            sub_mode = getattr(g, 'substitute_mode', 'equal') or 'equal'
            mb_allocs = [a for a in g.allocations if a.role == 'MB']
            if sub_mode == 'equal':
                if len(mb_allocs) > 1:
                    gift_lines.append(f"    Substitute: If any beneficiary named in this clause does not survive me, then the benefit that beneficiary would have received shall be given to the other surviving beneficiaries in equal shares or to the survivor of them if one of them does not survive me.")
                else:
                    gift_lines.append(f"    Substitute: (single beneficiary — use specific substitutes if needed)")
            elif sub_mode == 'prorata':
                gift_lines.append(f"    Substitute: If any beneficiary named in this clause does not survive me, then the benefit that beneficiary would have received shall be given to the other surviving beneficiaries in the same ratio as their respective shares.")
            elif sub_mode == 'specific':
                # Helper to look up beneficiary ID and relationship
                def _lookup(name):
                    for b in will_data.beneficiaries:
                        if b.full_name.lower() == name.lower():
                            return format_id_for_will(b.nric_passport_birthcert, getattr(b, 'nationality', 'Malaysian')), b.relationship.lower()
                    return "", ""

                for a in g.allocations:
                    if a.substitutes:
                        mb_id, mb_rel = _lookup(a.beneficiary_name)
                        mb_id_str = f" {mb_id}" if mb_id else ""
                        mb_rel_str = f"my {mb_rel} " if mb_rel else ""
                        sub_parts = []
                        for s in a.substitutes:
                            s_id, s_rel = _lookup(s.beneficiary_name)
                            s_id_str = f" {s_id}" if s_id else ""
                            s_rel_str = f"my {s_rel} " if s_rel else ""
                            sub_parts.append(f"{s_rel_str}{s.beneficiary_name.upper()}{s_id_str} ({_to_fraction(s.share)})")
                        gift_lines.append(f"    Substitute for {mb_rel_str}{a.beneficiary_name.upper()}{mb_id_str}: the benefit shall be given to {', '.join(sub_parts)}")
        sections.append(f"""
## SPECIFIC GIFTS / BEQUESTS
{chr(10).join(gift_lines)}""")

    # Section F: Residuary Estate
    if will_data.residuary_estate:
        re = will_data.residuary_estate
        res_lines = ["  Main Beneficiaries:"]

        def _lookup_res_ben(name):
            """Look up NRIC/passport, nationality, and relationship from beneficiary list."""
            for b in will_data.beneficiaries:
                if b.full_name.lower() == name.lower():
                    return b.nric_passport_birthcert, getattr(b, 'nationality', 'Malaysian'), b.relationship.lower()
            return "", "Malaysian", ""

        for rb in re.main_beneficiaries:
            nric, nat, rel = _lookup_res_ben(rb.beneficiary_name)
            id_str = f" {format_id_for_will(nric, nat)}" if nric else ""
            rel_str = f"my {rel} " if rel else ""
            res_lines.append(f"    - {rel_str}{rb.beneficiary_name.upper()}{id_str}: {_to_fraction(rb.share)}")

        if re.substitute_groups:
            res_lines.append("  Substitute Beneficiaries (for residuary clause (c)):")
            for i, group in enumerate(re.substitute_groups, 1):
                res_lines.append(f"    Substitute Group {i}:")
                for rb in group:
                    nric, nat, rel = _lookup_res_ben(rb.beneficiary_name)
                    id_str = f" {format_id_for_will(nric, nat)}" if nric else ""
                    rel_str = f"my {rel} " if rel else ""
                    res_lines.append(f"      - {rel_str}{rb.beneficiary_name.upper()}{id_str}: {_to_fraction(rb.share)}")
            res_lines.append("  Use clause (c) pattern: 'But if [he/she] does not survive me, to divide the residue equally between [substitute names with IDs]. If one of them does not survive me, then the other named beneficiary in this clause shall be the sole beneficiary of this gift.'")

        if re.additional_notes:
            res_lines.append(f"  Notes: {re.additional_notes}")

        sections.append(f"""
## RESIDUARY ESTATE
{chr(10).join(res_lines)}""")

    # Section G: Testamentary Trust
    if will_data.testamentary_trust:
        tt = will_data.testamentary_trust
        trust_lines = ["  Trust Beneficiaries:"]
        for tb in tt.beneficiaries:
            trust_lines.append(f"    - {tb.beneficiary_name}: {_to_fraction(tb.share)}")
        trust_lines.append(f"  Purposes: {', '.join(tt.purposes)}")
        if tt.duration:
            trust_lines.append(f"  Duration: {tt.duration}")
        if tt.payment_mode:
            trust_lines.append(f"  Payment: {tt.payment_mode} - RM {tt.payment_amount or 'Discretion of Trustee'}")
        if tt.assets_from_gifts:
            trust_lines.append(f"  Assets from: {', '.join(tt.assets_from_gifts)}")
        if tt.balance_beneficiaries:
            trust_lines.append("  Balance of Trust to:")
            for bb in tt.balance_beneficiaries:
                trust_lines.append(f"    - {bb.beneficiary_name}: {_to_fraction(bb.share)}")

        sections.append(f"""
## TESTAMENTARY TRUST
{chr(10).join(trust_lines)}""")

    # Section H & I: Other Matters
    if will_data.other_matters:
        om = will_data.other_matters
        other_lines = []
        if om.terms_of_endearment:
            other_lines.append(f"  Personal Message: {om.terms_of_endearment}")
        if om.commorientes_enabled:
            other_lines.append(f"  Commorientes: Beneficiaries must survive {om.commorientes_days} days")
        if om.exclusion_enabled:
            other_lines.append(f"  Exclusion: {om.exclusion_name} ({om.exclusion_relationship}) - Reason: {om.exclusion_reason}")
        if om.unnamed_children_enabled:
            other_lines.append(f"  Include unnamed children with spouse: {om.unnamed_children_spouse_name}")
        if getattr(om, 'joint_account_clause_enabled', False):
            other_lines.append("  Joint Account Clause: ENABLED — include the joint bank account surviving holder clause")
        else:
            other_lines.append("  Joint Account Clause: NOT enabled — do NOT include any joint bank account clause")
        if getattr(om, 'testator_satisfaction_enabled', True):
            other_lines.append("  Testator Satisfaction Clause: ENABLED — include clause: 'In making this Will, I have conscientiously considered all aspects and all my surrounding circumstances and I am thoroughly satisfied that the provisions made in this Will absolutely reflect my wishes and intentions.'")
        if getattr(om, 'translator_enabled', False):
            t_name = getattr(om, 'translator_name', '') or ''
            t_nric = getattr(om, 'translator_nric', '') or ''
            t_lang = getattr(om, 'translator_language', '') or ''
            t_addr = getattr(om, 'translator_address', '') or ''
            other_lines.append(f"  Translator Attestation: ENABLED")
            other_lines.append(f"    Translator Name: {t_name}")
            other_lines.append(f"    Translator NRIC: {t_nric}")
            other_lines.append(f"    Language: {t_lang}")
            if t_addr:
                other_lines.append(f"    Translator Address: {t_addr}")
            other_lines.append(f"    USE TRANSLATOR ATTESTATION format instead of standard attestation clause.")
        if om.additional_instructions:
            other_lines.append(f"  Additional: {om.additional_instructions}")

        if other_lines:
            sections.append(f"""
## OTHER MATTERS
{chr(10).join(other_lines)}""")

    # Build mandatory substitute clauses summary
    sub_clauses = []
    for i, g in enumerate(will_data.gifts):
        sub_mode = getattr(g, 'substitute_mode', 'equal') or 'equal'
        if sub_mode == 'specific':
            def _lookup_sub(name):
                for b in will_data.beneficiaries:
                    if b.full_name.lower() == name.lower():
                        return format_id_for_will(b.nric_passport_birthcert, getattr(b, 'nationality', 'Malaysian')), b.relationship.lower()
                return "", ""
            for a in g.allocations:
                if a.substitutes:
                    mb_id, mb_rel = _lookup_sub(a.beneficiary_name)
                    mb_id_str = f" {mb_id}" if mb_id else ""
                    mb_rel_str = f"my {mb_rel} " if mb_rel else ""
                    he_she = "she" if mb_rel in ("sister", "daughter", "mother", "niece", "aunt", "grandmother", "wife") else "he"
                    sub_parts = []
                    for s in a.substitutes:
                        s_id, s_rel = _lookup_sub(s.beneficiary_name)
                        s_id_str = f" {s_id}" if s_id else ""
                        s_rel_str = f"my {s_rel} " if s_rel else ""
                        sub_parts.append(f"{s_rel_str}{s.beneficiary_name.upper()}{s_id_str}")
                    if len(sub_parts) == 1:
                        sub_text = sub_parts[0]
                    else:
                        sub_text = " and ".join(sub_parts) + " in equal shares or to the survivor of them if one of them does not survive me"
                    sub_clauses.append(f"  - If {mb_rel_str}{a.beneficiary_name.upper()}{mb_id_str} does not survive me, then the benefit {he_she} would have received shall be given to {sub_text}.")

    if sub_clauses:
        sections.append(f"""
## MANDATORY SUBSTITUTE BENEFICIARY CLAUSES
CRITICAL: You MUST include the following substitute beneficiary clause(s) in the will. Each one should appear as a separate numbered clause after the gift clauses, using "Pursuant to Clause [X] above" to reference the relevant gift clause:
{chr(10).join(sub_clauses)}""")

    return "\n".join(sections)


def _inject_missing_substitutes(will_text: str, will_data) -> str:
    """Post-process AI-generated will to inject any missing specific substitute clauses.
    Only injects if the AI drafter omitted them. Deduplicates by MB name."""
    import re as re_mod

    will_upper = will_text.upper()
    # Track MB names we've already processed to avoid duplicates across gifts
    processed_mbs = set()

    sub_entries = []
    for gi, g in enumerate(will_data.gifts):
        sub_mode = getattr(g, 'substitute_mode', 'equal') or 'equal'
        if sub_mode != 'specific':
            continue
        for a in g.allocations:
            if not a.substitutes:
                continue

            mb_key = a.beneficiary_name.upper()
            # Skip if we already processed this MB (same person across multiple gifts)
            if mb_key in processed_mbs:
                continue
            processed_mbs.add(mb_key)

            # Check if a substitute clause already exists for this MB
            # Pattern: "[MB NAME] ... does not survive me ... shall be given to"
            mb_escaped = re_mod.escape(mb_key)
            already_has = re_mod.search(
                mb_escaped + r'.*?DOES NOT SURVIVE ME.*?SHALL BE GIVEN TO',
                will_upper,
                re_mod.DOTALL
            )
            if already_has:
                continue

            # Look up MB info
            mb_nric, mb_nat, mb_rel = "", "Malaysian", ""
            for b in will_data.beneficiaries:
                if b.full_name.lower() == a.beneficiary_name.lower():
                    mb_nric = b.nric_passport_birthcert
                    mb_nat = getattr(b, 'nationality', 'Malaysian')
                    mb_rel = b.relationship.lower()
                    break
            mb_id_str = f" {format_id_for_will(mb_nric, mb_nat)}" if mb_nric else ""
            mb_rel_str = f"my {mb_rel} " if mb_rel else ""
            he_she = "she" if mb_rel in ("sister", "daughter", "mother", "niece", "aunt", "grandmother", "wife") else "he"

            # Build substitute parts
            sub_parts = []
            for s in a.substitutes:
                s_nric, s_nat, s_rel = "", "Malaysian", ""
                for b in will_data.beneficiaries:
                    if b.full_name.lower() == s.beneficiary_name.lower():
                        s_nric = b.nric_passport_birthcert
                        s_nat = getattr(b, 'nationality', 'Malaysian')
                        s_rel = b.relationship.lower()
                        break
                s_id_str = f" {format_id_for_will(s_nric, s_nat)}" if s_nric else ""
                s_rel_str = f"my {s_rel} " if s_rel else ""
                sub_parts.append(f"{s_rel_str}{s.beneficiary_name.upper()}{s_id_str}")

            if len(sub_parts) == 1:
                sub_text = sub_parts[0]
                trailing = "."
            else:
                sub_text = " and ".join(sub_parts)
                trailing = " in equal shares or to the survivor of them if one of them does not survive me."

            # Find clause numbers that reference this gift's MB
            ref_clauses = []
            for m in re_mod.finditer(r'(\d+)\.\s+I give', will_text):
                clause_start = m.start()
                clause_end = will_text.find('\n\n', clause_start + 1)
                if clause_end == -1:
                    clause_end = len(will_text)
                clause_text = will_text[clause_start:clause_end]
                if mb_key in clause_text.upper():
                    ref_clauses.append(m.group(1))

            if ref_clauses:
                ref_text = f"Pursuant to Clause{'s' if len(ref_clauses) > 1 else ''} {' and '.join(ref_clauses)} above, if"
            else:
                ref_text = "If"

            sub_entries.append(
                f"{ref_text} {mb_rel_str}{a.beneficiary_name.upper()}{mb_id_str} does not survive me, then the benefit {he_she} would have received shall be given to {sub_text}{trailing}"
            )

    if not sub_entries:
        return will_text

    # Find insertion point before "Residuary Estate" or "Declaration"
    insert_before = None
    for marker in ["Residuary Estate", "Declaration"]:
        idx = will_text.find(marker)
        if idx != -1:
            insert_before = idx
            break

    if insert_before is None:
        return will_text

    # Find the last clause number before insertion point
    last_clause = 0
    for m in re_mod.finditer(r'^(\d+)\.', will_text[:insert_before], re_mod.MULTILINE):
        last_clause = int(m.group(1))

    # Build and inject
    inject_lines = []
    for i, entry in enumerate(sub_entries):
        inject_lines.append(f"{last_clause + 1 + i}.  {entry}")

    inject_text = "\n\n".join(inject_lines) + "\n\n"
    before = will_text[:insert_before]
    after = will_text[insert_before:]

    # Renumber subsequent clauses
    offset = len(sub_entries)
    def _renumber(match):
        old_num = int(match.group(1))
        return f"{old_num + offset}." if old_num > last_clause else match.group(0)

    after = re_mod.sub(r'^(\d+)\.', _renumber, after, flags=re_mod.MULTILINE)

    return before + inject_text + after


def draft_will(will_data) -> str:
    """Use Claude API to draft a complete will document."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    user_prompt = f"""Please draft a complete Last Will and Testament based on the following information.
Output ONLY the will document text - no explanatory notes, comments, or markdown formatting.

IMPORTANT DRAFTING INSTRUCTIONS:
- Follow the professional Malaysian will standard format exactly
- Do NOT add any corporate trustee (e.g. Rockwills, Amanah Raya) as substitute executor unless explicitly provided in the data
- Use section headings: "Revocation", "Appointment of Executor(s)", "Non Residuary Gift(s)", "Residuary Estate", "Declaration"
- For bank accounts and properties to be sold, use the "The Moneys" pooling mechanism
- Include EPF/insurance fallback clause if not specifically excluded
- Each beneficiary mentioned in gifts/residuary MUST include their NRIC number from the beneficiary list
- CRITICAL: Include ALL substitute beneficiary clauses listed in the MANDATORY SUBSTITUTE BENEFICIARY CLAUSES section. Each must appear as a separate numbered clause using "Pursuant to Clause [X] above" format
- Include the commorientes clause (30-day survivorship rule) in the Declaration section
- Include the testator declaration clause at the end
- End with "THE REST OF THE PAGE IS INTENTIONALLY LEFT BLANK"
- Include proper attestation with testator and two witness signature blocks
- Use "MALAYSIA NRIC No." format for Malaysian IDs, "[COUNTRY] Identification No." for foreign nationals

{format_will_data(will_data)}

Draft the complete will now, following the professional format and clause ordering specified in your instructions."""

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )

    will_text = message.content[0].text

    # Post-process: inject missing specific substitute clauses
    will_text = _inject_missing_substitutes(will_text, will_data)

    return will_text


def _fid(person) -> str:
    """Shorthand to format person's ID string for will text."""
    nric = getattr(person, 'nric_passport', '') or getattr(person, 'nric_passport_birthcert', '') or ''
    nat = getattr(person, 'nationality', 'Malaysian')
    return format_id_for_will(nric, nat)


def draft_will_mock(will_data) -> str:
    """Mock will drafter for testing without API key."""
    t = will_data.testator
    his_her = "his" if t.gender == "Male" else "her"
    t_id = format_id_for_will(t.nric_passport, t.nationality)

    # Format executor appointment
    executors = will_data.executors
    primary_executors = [e for e in executors if e.role in ("Primary", "Joint")]
    substitute_executors = [e for e in executors if e.role == "Substitute"]

    def _format_executor(e):
        """Format an executor — individual or corporate."""
        is_corp = getattr(e, 'is_corporate', False) or (hasattr(e, 'relationship') and e.relationship == 'Corporate Trustee')
        if is_corp:
            return f"{e.full_name.upper()} (Company No. {e.nric_passport}) of {e.address}"
        return f"my {e.relationship.lower()} {e.full_name.upper()} {_fid(e)} of {e.address}"

    if len(primary_executors) >= 2:
        pe0, pe1 = primary_executors[0], primary_executors[1]
        exec_clause = f"""Appointment of Executor(s)

2.  I hereby appoint {_format_executor(pe0)} and {_format_executor(pe1)} as my joint Executors. If any of them is unwilling or unable to act for whatsoever reason then the remaining Executor named herein shall act as my sole Executor."""
    elif primary_executors:
        pe0 = primary_executors[0]
        exec_clause = f"""Appointment of Executor(s)

2.  I hereby appoint {_format_executor(pe0)} as my sole Executor."""

    next_clause = 3
    substitute_clause = ""
    if substitute_executors:
        def _format_sub_exec(s):
            """Format a substitute executor — individual or corporate."""
            is_corp = getattr(s, 'is_corporate', False) or (hasattr(s, 'relationship') and s.relationship == 'Corporate Trustee')
            if is_corp:
                return f"{s.full_name.upper()} (Company No. {s.nric_passport}) of {s.address}"
            return f"my {s.relationship.lower()} {s.full_name.upper()} {_fid(s)} of {s.address}"

        if len(substitute_executors) >= 2:
            sub_names = " and ".join(_format_sub_exec(s) for s in substitute_executors)
            substitute_clause = f"""
{next_clause}.  With reference to Clause 2 above, if all the persons named therein are unable or unwilling to act for whatsoever reason, then I hereby appoint {sub_names} as my joint Substitute Executors. If any of them is unwilling or unable to act for whatsoever reason then the remaining Substitute Executor named herein shall act as my sole Executor."""
        else:
            sub = substitute_executors[0]
            sub_text = _format_sub_exec(sub)
            substitute_clause = f"""
{next_clause}.  With reference to Clause 2 above, if all the persons named therein are unable or unwilling to act for whatsoever reason, then I hereby appoint {sub_text} as my Substitute Executor."""
        next_clause += 1

    # Trustee clause
    if will_data.trustee_same_as_executor:
        trustee_clause = f"""
{next_clause}.  In this Will unless it is specifically stated to the contrary, my Executor(s) shall also act as my Trustee(s)."""
        next_clause += 1
    elif will_data.trustees:
        trustees = will_data.trustees
        if len(trustees) >= 2:
            t0, t1 = trustees[0], trustees[1]
            trustee_clause = f"""
{next_clause}.  I appoint as my joint Trustees my {t0.relationship.lower()} {t0.full_name.upper()} {_fid(t0)} of {t0.address} and my {t1.relationship.lower()} {t1.full_name.upper()} {_fid(t1)} of {t1.address}. If any of them is unwilling or unable to act for whatsoever reason then the remaining Trustee named herein shall act as my sole Trustee."""
            next_clause += 1
        else:
            t0 = trustees[0]
            trustee_clause = f"""
{next_clause}.  I appoint as my sole Trustee my {t0.relationship.lower()} {t0.full_name.upper()} {_fid(t0)} of {t0.address}."""
            next_clause += 1

        # Handle multiple substitute trustees
        sub_trustees = will_data.substitute_trustees or ([will_data.substitute_trustee] if will_data.substitute_trustee else [])
        if sub_trustees:
            if len(sub_trustees) >= 2:
                sub_names = " and ".join(
                    f"my {st.relationship.lower()} {st.full_name.upper()} {_fid(st)} of {st.address}"
                    for st in sub_trustees
                )
                trustee_clause += f"""

{next_clause}.  With reference to Clause {next_clause - 1} above, if all the Trustees named therein are unable or unwilling to act for whatsoever reason, then I appoint as my joint Substitute Trustees {sub_names}. If any of them is unwilling or unable to act for whatsoever reason then the remaining Substitute Trustee named herein shall act as my sole Trustee."""
            else:
                st = sub_trustees[0]
                trustee_clause += f"""

{next_clause}.  With reference to Clause {next_clause - 1} above, if all the Trustees named therein are unable or unwilling to act for whatsoever reason, then I appoint as my Substitute Trustee my {st.relationship.lower()} {st.full_name.upper()} {_fid(st)} of {st.address}."""
            next_clause += 1
    else:
        trustee_clause = f"""
{next_clause}.  In this Will unless it is specifically stated to the contrary, my Executor(s) shall also act as my Trustee(s)."""
        next_clause += 1

    # Residuary estate
    residuary_text = ""
    if will_data.residuary_estate and will_data.residuary_estate.main_beneficiaries:
        roman_numerals = ['i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x']

        def _lookup_ben(name):
            """Look up NRIC/passport, nationality, and relationship from beneficiary list."""
            for b in will_data.beneficiaries:
                if b.full_name.lower() == name.lower():
                    return b.nric_passport_birthcert, getattr(b, 'nationality', 'Malaysian'), b.relationship.lower()
            return "", "Malaysian", ""

        ben_lines = []
        for i, rb in enumerate(will_data.residuary_estate.main_beneficiaries):
            nric, nat, relationship = _lookup_ben(rb.beneficiary_name)
            numeral = roman_numerals[i] if i < len(roman_numerals) else str(i + 1)
            nric_str = f" {format_id_for_will(nric, nat)}" if nric else ""
            rel_str = f"my {relationship} " if relationship else ""
            ben_lines.append(f"    ({numeral}) {rel_str}{rb.beneficiary_name.upper()}{nric_str} ({_to_fraction(rb.share)} share)")

        # Build substitute clause (c) if substitute groups exist
        sub_clause_c = ""
        re_data = will_data.residuary_estate
        if re_data.substitute_groups:
            # Flatten all substitute groups into a list of substitute beneficiaries
            all_subs = []
            for group in re_data.substitute_groups:
                for rb in group:
                    nric, nat, rel = _lookup_ben(rb.beneficiary_name)
                    id_str = f" {format_id_for_will(nric, nat)}" if nric else ""
                    rel_str = f"my {rel} " if rel else ""
                    all_subs.append(f"{rel_str}{rb.beneficiary_name.upper()}{id_str}")

            if len(all_subs) == 1:
                sub_clause_c = f"""

(c) But if {'he' if t.gender == 'Male' else 'she'} does not survive me, to give the residue ('my residuary estate') to {all_subs[0]}."""
            elif len(all_subs) >= 2:
                sub_names = " and ".join(all_subs)
                sub_clause_c = f"""

(c) But if {'he' if t.gender == 'Male' else 'she'} does not survive me, to divide the residue ('my residuary estate') equally between {sub_names}. If one of them does not survive me, then the other named beneficiary in this clause shall be the sole beneficiary of this gift."""

        if len(ben_lines) == 1:
            # Single residuary beneficiary
            rb = will_data.residuary_estate.main_beneficiaries[0]
            nric, nat, relationship = _lookup_ben(rb.beneficiary_name)
            nric_str = f" {format_id_for_will(nric, nat)}" if nric else ""
            rel_str = f"my {relationship} " if relationship else ""
            residuary_text = f"""Residuary Estate

{next_clause}.  My Trustee(s) shall hold the rest of my estate on trust to retain or sell it and :

(a) To pay debts including any sums required to secure a discharge of any charge or a withdrawal of any lien on any of my immovable properties, funeral and executorship expenses.

(b) To give the residue ('my residuary estate') to {rel_str}{rb.beneficiary_name.upper()}{nric_str}.{sub_clause_c}"""
        else:
            # Multiple residuary beneficiaries
            residuary_text = f"""Residuary Estate

{next_clause}.  My Trustee(s) shall hold the rest of my estate on trust to retain or sell it and :

(a) To pay debts including any sums required to secure a discharge of any charge or a withdrawal of any lien on any of my immovable properties, funeral and executorship expenses.

(b) To divide the residue ('my residuary estate') among the following beneficiaries named below in the shares indicated.

{chr(10).join(ben_lines)}{sub_clause_c}"""
        next_clause += 1

    # Non-residuary gifts
    non_residuary_text = f"""Non Residuary Gift(s)

{next_clause}.  I hereby devise and bequeath the moneys standing to my credit in all my joint bank accounts to the respective joint account holder(s), if more than one in equal shares."""
    next_clause += 1

    # Bank accounts gift
    non_residuary_text += f"""

{next_clause}.  I hereby devise and bequeath to my Executor the moneys standing to my credit in all my bank accounts. If my Executor does not survive me, then the benefit shall form part of my residuary estate. The expression 'all bank accounts' in this clause shall exclude any account which has been specifically given away in this Will."""
    next_clause += 1

    # EPF fallback
    non_residuary_text += f"""

{next_clause}.  If the nomination(s) made by me in my Employees' Provident Fund do(es) not take effect for whatsoever reason, then I hereby devise and bequeath the benefits of the nomination(s) to form part of my residuary estate."""
    next_clause += 1

    # Specific gift clauses
    specific_gifts_text = ""
    gift_clause_map = {}  # Maps gift index to clause number for substitute references
    if will_data.gifts:
        for gi, g in enumerate(will_data.gifts):
            desc = g.get_formatted_description()
            if not desc:
                continue
            # Build beneficiary list
            alloc_parts = []
            for a in g.allocations:
                nric, nat, rel = "", "Malaysian", ""
                for b in will_data.beneficiaries:
                    if b.full_name.lower() == a.beneficiary_name.lower():
                        nric = b.nric_passport_birthcert
                        nat = getattr(b, 'nationality', 'Malaysian')
                        rel = b.relationship.lower()
                        break
                id_str = f" {format_id_for_will(nric, nat)}" if nric else ""
                rel_str = f"my {rel} " if rel else ""
                alloc_parts.append(f"{rel_str}{a.beneficiary_name.upper()}{id_str}")

            if not alloc_parts:
                continue

            gift_clause_map[gi] = next_clause

            is_sell = getattr(g, 'sell_property', False) and g.gift_type == 'property'

            if is_sell and len(alloc_parts) > 1:
                # Sell directive with multiple beneficiaries: use sub-items
                specific_gifts_text += f"""

{next_clause}.  I direct my Executor to sell my undivided share in {desc.lstrip('my ').rstrip(';')} and distribute the net proceeds of the sale to the following beneficiaries named below in the shares indicated."""
                roman = ['(i)', '(ii)', '(iii)', '(iv)', '(v)', '(vi)']
                for idx_a, a in enumerate(g.allocations):
                    r = roman[idx_a] if idx_a < len(roman) else f'({idx_a+1})'
                    specific_gifts_text += f"\n{r}  {alloc_parts[idx_a]} ({_to_fraction(a.share)} share)"
            elif len(alloc_parts) == 1:
                ben_text = alloc_parts[0]
                specific_gifts_text += f"""

{next_clause}.  I hereby devise and bequeath to {ben_text} {desc}"""
            else:
                ben_text = ", ".join(alloc_parts[:-1]) + " and " + alloc_parts[-1]
                shares = [_to_fraction(a.share) for a in g.allocations]
                if all(s == shares[0] for s in shares):
                    share_text = f" in equal shares ({shares[0]} each)"
                else:
                    share_text = f" in the shares indicated ({', '.join(shares)})"

                specific_gifts_text += f"""

{next_clause}.  I hereby devise and bequeath to {ben_text} {desc}{share_text}."""

            # Add substitute clause inline for equal/prorata modes
            sub_mode = getattr(g, 'substitute_mode', 'equal') or 'equal'
            mb_allocs = [a for a in g.allocations if a.role == 'MB']
            if sub_mode == 'equal' and len(mb_allocs) > 1:
                specific_gifts_text += f" If any beneficiary named in this clause does not survive me, then the benefit that beneficiary would have received shall be given to the other surviving beneficiaries in equal shares or to the survivor of them if one of them does not survive me."
            elif sub_mode == 'prorata' and len(mb_allocs) > 1:
                specific_gifts_text += f" If any beneficiary named in this clause does not survive me, then the benefit that beneficiary would have received shall be given to the other surviving beneficiaries in the same ratio as their respective shares."

            next_clause += 1

        # Add specific substitute clauses (for gifts with specific substitute mode)
        for gi, g in enumerate(will_data.gifts):
            sub_mode = getattr(g, 'substitute_mode', 'equal') or 'equal'
            if sub_mode != 'specific' or gi not in gift_clause_map:
                continue
            ref_clause = gift_clause_map[gi]
            for a in g.allocations:
                if not a.substitutes:
                    continue
                mb_nric, mb_nat, mb_rel = "", "Malaysian", ""
                for b in will_data.beneficiaries:
                    if b.full_name.lower() == a.beneficiary_name.lower():
                        mb_nric = b.nric_passport_birthcert
                        mb_nat = getattr(b, 'nationality', 'Malaysian')
                        mb_rel = b.relationship.lower()
                        break
                mb_id_str = f" {format_id_for_will(mb_nric, mb_nat)}" if mb_nric else ""
                mb_rel_str = f"my {mb_rel} " if mb_rel else ""
                he_she = "she" if mb_rel in ("sister", "daughter", "mother", "niece", "aunt", "grandmother", "wife") else "he"

                sub_parts = []
                for s in a.substitutes:
                    s_nric, s_nat, s_rel = "", "Malaysian", ""
                    for b in will_data.beneficiaries:
                        if b.full_name.lower() == s.beneficiary_name.lower():
                            s_nric = b.nric_passport_birthcert
                            s_nat = getattr(b, 'nationality', 'Malaysian')
                            s_rel = b.relationship.lower()
                            break
                    s_id_str = f" {format_id_for_will(s_nric, s_nat)}" if s_nric else ""
                    s_rel_str = f"my {s_rel} " if s_rel else ""
                    sub_parts.append(f"{s_rel_str}{s.beneficiary_name.upper()}{s_id_str}")

                if len(sub_parts) == 1:
                    sub_text = sub_parts[0]
                    trailing = ""
                else:
                    sub_text = " and ".join(sub_parts)
                    trailing = " in equal shares or to the survivor of them if one of them does not survive me"

                specific_gifts_text += f"""

{next_clause}.  Pursuant to Clause {ref_clause} above, if {mb_rel_str}{a.beneficiary_name.upper()}{mb_id_str} does not survive me, then the benefit {he_she} would have received shall be given to {sub_text}{trailing}."""
                next_clause += 1

    # Declaration
    declaration_text = f"""Declaration

{next_clause}.  For the purpose of ascertaining entitlement under this Will any beneficiary who does not survive me by 30 days shall be treated as having died before me.

----------------------- THE REST OF THE PAGE IS INTENTIONALLY LEFT BLANK -----------------------"""

    will_text = f"""LAST WILL AND TESTAMENT OF
{t.full_name.upper()}

This Will is made by me {t.full_name.upper()} {t_id} of {t.residential_address.upper()}.

Revocation

1.  By signing this Will, I revoke all earlier Wills and exclude my movable and immovable assets located in any country in which I have a separate Will made according to the laws of that country before my demise. In the event I do not have a separate Will made according to the laws of a particular country where my assets are located, then those assets shall form part of this Will and shall be distributed accordingly.

{exec_clause}
{substitute_clause}
{trustee_clause}

{non_residuary_text}
{specific_gifts_text}

{residuary_text}

{declaration_text}


Signature of the Testator: ________________________________________________________

Date of this Will: _____________________________________________(dd/mm/yyyy)

This Last Will and Testament was signed by the Testator in the presence of us both and attested by us in the presence of both Testator and of each other:

Signature of First Witness: ________________________________________________________

First Witness Full Name: ________________________________________________________

First Witness Identification: ________________________________________________________

First Witness Address: ________________________________________________________

                       ________________________________________________________

                       ________________________________________________________

First Witness Contact Number: ________________________________________________________

Signature of Second Witness: ________________________________________________________

Second Witness Full Name: ________________________________________________________

Second Witness Identification: ________________________________________________________

Second Witness Address: ________________________________________________________

                        ________________________________________________________

                        ________________________________________________________

Second Witness Contact Number: ________________________________________________________
"""
    return will_text
