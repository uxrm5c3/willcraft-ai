"""AI Will Drafting Engine using Claude API."""

import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, MAX_TOKENS
from ai.prompts.system_prompt import SYSTEM_PROMPT


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
            exec_lines.append(f"  {i}. {e.full_name} (NRIC: {e.nric_passport}), {e.address}, Relationship: {e.relationship}, Role: {e.role}")
        sections.append(f"""
## EXECUTORS AND TRUSTEES
{chr(10).join(exec_lines)}""")

    # Section C: Guardians
    if will_data.guardians:
        guard_lines = []
        for i, g in enumerate(will_data.guardians, 1):
            guard_lines.append(f"  {i}. {g.full_name} (NRIC: {g.nric_passport}), {g.address}, Relationship: {g.relationship}, Role: {g.role}")
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
            ben_lines.append(f"  {i}. {b.full_name} (NRIC: {b.nric_passport_birthcert}), Relationship: {b.relationship}")
        sections.append(f"""
## LIST OF BENEFICIARIES
{chr(10).join(ben_lines)}""")

    # Section E: Specific Gifts
    if will_data.gifts:
        gift_lines = []
        for i, g in enumerate(will_data.gifts, 1):
            gift_lines.append(f"  Gift {i}: {g.description}")
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
- Follow the Rockwills Trustee Berhad professional standard format exactly
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

Draft the complete will now, following the Rockwills professional format and clause ordering specified in your instructions."""

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )

    return message.content[0].text


def draft_will_mock(will_data) -> str:
    """Mock will drafter for testing without API key."""
    t = will_data.testator
    his_her = "his" if t.gender == "Male" else "her"

    # Format executor appointment
    executors = will_data.executors
    primary_executors = [e for e in executors if e.role in ("Primary", "Joint")]
    substitute_executors = [e for e in executors if e.role == "Substitute"]

    if len(primary_executors) >= 2:
        exec_clause = f"""Appointment of Executor(s)

2.  I appoint as my joint Executors my {primary_executors[0].relationship.lower()} {primary_executors[0].full_name.upper()} MALAYSIA NRIC No. {primary_executors[0].nric_passport} of {primary_executors[0].address} and my {primary_executors[1].relationship.lower()} {primary_executors[1].full_name.upper()} MALAYSIA NRIC No. {primary_executors[1].nric_passport} of {primary_executors[1].address}. If any of them is unwilling or unable to act for whatsoever reason then the remaining Executor named herein shall acts as my sole Executor."""
    elif primary_executors:
        exec_clause = f"""Appointment of Executor(s)

2.  I appoint as my sole Executor my {primary_executors[0].relationship.lower()} {primary_executors[0].full_name.upper()} MALAYSIA NRIC No. {primary_executors[0].nric_passport} of {primary_executors[0].address}."""

    next_clause = 3
    substitute_clause = ""
    if substitute_executors:
        sub = substitute_executors[0]
        substitute_clause = f"""
{next_clause}.  With reference to Clause 2 above, if all the persons named therein are unable or unwilling to act for whatsoever reason, then I appoint as my Executor my {sub.relationship.lower()} {sub.full_name.upper()} MALAYSIA NRIC No. {sub.nric_passport} of {sub.address}."""
        next_clause += 1

    trustee_clause = f"""
{next_clause}.  In this Will unless it is specifically stated to the contrary, my Executor(s) shall also act as my Trustee(s)."""
    next_clause += 1

    # Residuary estate
    residuary_text = ""
    if will_data.residuary_estate and will_data.residuary_estate.main_beneficiaries:
        roman_numerals = ['i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x']
        ben_lines = []
        for i, rb in enumerate(will_data.residuary_estate.main_beneficiaries):
            # Try to find NRIC and relationship from beneficiary list
            nric = ""
            relationship = ""
            for b in will_data.beneficiaries:
                if b.full_name.lower() == rb.beneficiary_name.lower():
                    nric = b.nric_passport_birthcert
                    relationship = b.relationship.lower()
                    break
            numeral = roman_numerals[i] if i < len(roman_numerals) else str(i + 1)
            nric_str = f" MALAYSIA NRIC No. {nric}" if nric else ""
            rel_str = f"my {relationship} " if relationship else ""
            ben_lines.append(f"    ({numeral}) {rel_str}{rb.beneficiary_name.upper()}{nric_str} ({rb.share} share)")

        if len(ben_lines) == 1:
            # Single residuary beneficiary
            rb = will_data.residuary_estate.main_beneficiaries[0]
            nric = ""
            relationship = ""
            for b in will_data.beneficiaries:
                if b.full_name.lower() == rb.beneficiary_name.lower():
                    nric = b.nric_passport_birthcert
                    relationship = b.relationship.lower()
                    break
            nric_str = f" MALAYSIA NRIC No. {nric}" if nric else ""
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

This Will is made by me {t.full_name.upper()} MALAYSIA NRIC No. {t.nric_passport} born on {t.date_of_birth} of {t.residential_address.upper()}.

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
