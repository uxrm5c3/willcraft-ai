"""AI Will Drafting Engine using Claude API."""

import re
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, MAX_TOKENS
from ai.prompts.system_prompt import SYSTEM_PROMPT


def _is_malaysian_nric(id_str: str) -> bool:
    """Check if an ID string is a Malaysian NRIC (12 digits, with optional dashes)."""
    cleaned = re.sub(r'[-\s]', '', id_str)
    return bool(re.match(r'^\d{12}$', cleaned))


def format_id_for_will(nric_passport: str, nationality: str = "Malaysian") -> str:
    """Format person ID for will text.
    - Malaysian NRIC: 'MALAYSIA NRIC No. 123456-01-1234'
    - Passport: '[NATIONALITY] Passport No. AB1234567'
    """
    if _is_malaysian_nric(nric_passport):
        return f"MALAYSIA NRIC No. {nric_passport}"
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
        }
        country = nat_map.get(nat, nat)
        return f"{country} Passport No. {nric_passport}"


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
            for a in g.allocations:
                role_label = "Main Beneficiary" if a.role == "MB" else "Substitute Beneficiary"
                gift_lines.append(f"    - {a.beneficiary_name}: {a.share} ({role_label})")
        sections.append(f"""
## SPECIFIC GIFTS / BEQUESTS
{chr(10).join(gift_lines)}""")

    # Section F: Residuary Estate
    if will_data.residuary_estate:
        re = will_data.residuary_estate
        res_lines = ["  Main Beneficiaries:"]
        for rb in re.main_beneficiaries:
            res_lines.append(f"    - {rb.beneficiary_name}: {rb.share}")

        if re.substitute_groups:
            for i, group in enumerate(re.substitute_groups, 1):
                res_lines.append(f"  Substitute Group {i}:")
                for rb in group:
                    res_lines.append(f"    - {rb.beneficiary_name}: {rb.share}")

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
            trust_lines.append(f"    - {tb.beneficiary_name}: {tb.share}")
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
                trust_lines.append(f"    - {bb.beneficiary_name}: {bb.share}")

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
        if om.additional_instructions:
            other_lines.append(f"  Additional: {om.additional_instructions}")

        if other_lines:
            sections.append(f"""
## OTHER MATTERS
{chr(10).join(other_lines)}""")

    return "\n".join(sections)


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
- Include substitute beneficiary clauses where substitute beneficiaries are provided
- Include the commorientes clause (30-day survivorship rule) in the Declaration section
- Include the testator declaration clause at the end
- End with "THE REST OF THE PAGE IS INTENTIONALLY LEFT BLANK"
- Include proper attestation with testator and two witness signature blocks
- Use "MALAYSIA NRIC No." format (not just "NRIC No.")

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

    return message.content[0].text


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

2.  I appoint as my joint Executors {_format_executor(pe0)} and {_format_executor(pe1)}. If any of them is unwilling or unable to act for whatsoever reason then the remaining Executor named herein shall acts as my sole Executor."""
    elif primary_executors:
        pe0 = primary_executors[0]
        exec_clause = f"""Appointment of Executor(s)

2.  I appoint as my sole Executor {_format_executor(pe0)}."""

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
{next_clause}.  With reference to Clause 2 above, if all the persons named therein are unable or unwilling to act for whatsoever reason, then I appoint as my joint Substitute Executors {sub_names}. If any of them is unwilling or unable to act for whatsoever reason then the remaining Substitute Executor named herein shall act as my sole Executor."""
        else:
            sub = substitute_executors[0]
            sub_text = _format_sub_exec(sub)
            substitute_clause = f"""
{next_clause}.  With reference to Clause 2 above, if all the persons named therein are unable or unwilling to act for whatsoever reason, then I appoint as my Substitute Executor {sub_text}."""
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
            ben_lines.append(f"    ({numeral}) {rel_str}{rb.beneficiary_name.upper()}{nric_str} ({rb.share} share)")

        if len(ben_lines) == 1:
            # Single residuary beneficiary
            rb = will_data.residuary_estate.main_beneficiaries[0]
            nric, nat, relationship = _lookup_ben(rb.beneficiary_name)
            nric_str = f" {format_id_for_will(nric, nat)}" if nric else ""
            rel_str = f"my {relationship} " if relationship else ""
            residuary_text = f"""Residuary Estate

{next_clause}.  Unless specifically stated to the contrary in this Will, my Trustee(s) shall hold the rest of my estate on trust to retain or sell any part thereof and:

(a) To pay debts including any sums required to secure a discharge of any charge or a withdrawal of any lien on any of my immovable properties, funeral and executorship expenses.

(b) To give the residue ('my residuary estate') to {rel_str}{rb.beneficiary_name.upper()}{nric_str}."""
        else:
            # Multiple residuary beneficiaries
            residuary_text = f"""Residuary Estate

{next_clause}.  Unless specifically stated to the contrary in this Will, my Trustee(s) shall hold the rest of my estate on trust to retain or sell any part thereof and:

(a) To pay debts including any sums required to secure a discharge of any charge or a withdrawal of any lien on any of my immovable properties, funeral and executorship expenses.

(b) To divide the residue ('my residuary estate') among the following beneficiaries named below in the shares indicated. If any beneficiary named in this clause does not survive me, then the benefit that beneficiary would have received shall be given to the other surviving beneficiaries in equal shares.

{chr(10).join(ben_lines)}"""
        next_clause += 1

    # Non-residuary gifts
    non_residuary_text = f"""Non Residuary Gift(s)

{next_clause}.  I give the moneys standing to my credit in all my joint bank accounts to the respective joint account holder(s), if more than one in equal shares."""
    next_clause += 1

    # Bank accounts gift
    non_residuary_text += f"""

{next_clause}.  I give to my Executor the moneys standing to my credit in all my bank accounts. If my Executor does not survive me, then the benefit shall form part of my residuary estate.

The expression 'all bank accounts' in this clause shall exclude any account which has been specifically given away in this Will."""
    next_clause += 1

    # EPF fallback
    non_residuary_text += f"""

{next_clause}.  If the nomination(s) made by me in my Employees' Provident Fund do(es) not take effect for whatsoever reason, then I give the benefits of the nomination(s) to form part of my residuary estate."""
    next_clause += 1

    # Declaration
    declaration_text = f"""Declaration

{next_clause}.  For the purpose of ascertaining entitlement under this Will any beneficiary who does not survive me by 30 days shall be treated as having died before me.

----------------------- THE REST OF THE PAGE IS INTENTIONALLY LEFT BLANK -----------------------"""

    will_text = f"""LAST WILL AND TESTAMENT OF
{t.full_name.upper()}

This Will is made by me {t.full_name.upper()} {t_id} born on {t.date_of_birth} of {t.residential_address.upper()}.

Revocation

1.  By signing this Will, I revoke all earlier Wills and exclude my movable and immovable assets located in any country in which I have a separate Will made according to the laws of that country before my demise. In the event I do not have a separate Will made according to the laws of a particular country where my assets are located, then those assets shall form part of this Will and shall be distributed accordingly. I hereby declare that I am domiciled in Malaysia.

{exec_clause}
{substitute_clause}
{trustee_clause}

{non_residuary_text}

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
