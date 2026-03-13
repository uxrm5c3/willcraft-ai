"""Professional will clause templates for Malaysian wills.
Based on Rockwills Trustee Berhad standard format and professional Malaysian estate planning practice."""

# ============================================================
# TITLE AND PREAMBLE
# ============================================================

TITLE_TEMPLATE = """LAST WILL AND TESTAMENT OF
{testator_name}"""

PREAMBLE_TEMPLATE = """This Will is made by me {testator_name} MALAYSIA NRIC No. {nric} born on {date_of_birth} of {address}."""

# ============================================================
# REVOCATION (Clause 1)
# ============================================================

REVOCATION_TEMPLATE = """Revocation

1.  By signing this Will, I revoke all earlier Wills and exclude my movable and immovable assets located in any country in which I have made a separate Will made according to the laws of that country before my demise. In the event I do not have a separate Will made according to the laws of a particular country where my assets are located, then those assets shall form part of this Will and shall be distributed accordingly. I hereby declare that I am domiciled in Malaysia."""

# ============================================================
# APPOINTMENT OF EXECUTOR(S) (Clause 2)
# ============================================================

EXECUTOR_SINGLE_TEMPLATE = """Appointment of Executor(s)

2.  I appoint as my sole Executor my {relationship} {executor_name} MALAYSIA NRIC No. {nric} of {address}."""

EXECUTOR_JOINT_TEMPLATE = """Appointment of Executor(s)

2.  I appoint as my joint Executors my {rel1} {name1} MALAYSIA NRIC No. {nric1} of {address1} and my {rel2} {name2} MALAYSIA NRIC No. {nric2} of {address2}. If any of them is unwilling or unable to act for whatsoever reason then the remaining Executor named herein shall acts as my sole Executor."""

EXECUTOR_SUBSTITUTE_TEMPLATE = """3.  With reference to Clause 2 above, if all the persons named therein are unable or unwilling to act for whatsoever reason, then I appoint as my Executor {substitute_name} MALAYSIA NRIC No. {nric} of {address} ({relationship})."""

EXECUTOR_CORPORATE_SUBSTITUTE_TEMPLATE = """3.  With reference to Clause 2 above, if all the persons named therein are unable or unwilling to act for whatsoever reason, then I appoint as my Executor Rockwills Trustee Berhad [Company No. 200501026798 (708932-T)]. The conditions on which Rockwills Trustee Berhad [Company No. 200501026798 (708932-T)] acts as Executor shall be based on the terms last published before the date of this Will and Rockwills Trustee Berhad [Company No. 200501026798 (708932-T)] shall be remunerated in accordance with scale of fees current at my death as varied from time to time during the administration of any trust arising under this Will."""

EXECUTOR_AS_TRUSTEE_TEMPLATE = """4.  In this Will unless it is specifically stated to the contrary, my Executor(s) shall also act as my Trustee(s)."""

# ============================================================
# CONTEMPLATION OF MARRIAGE
# ============================================================

CONTEMPLATION_OF_MARRIAGE_TEMPLATE = """I declare that this Will is made in contemplation of my marriage to {fiance_name} MALAYSIA NRIC No. {fiance_nric} and that this Will shall not be revoked by such marriage."""

# ============================================================
# APPOINTMENT OF GUARDIAN(S)
# ============================================================

GUARDIAN_TEMPLATE = """Appointment of Guardian(s)

In the event that my {spouse_term} shall not survive me or shall be unable to act as the guardian of my minor children, I appoint my {relationship} {guardian_name} MALAYSIA NRIC No. {nric} of {address} as the guardian of my minor children below the age of twenty-one (21) years."""

GUARDIAN_ALLOWANCE_TEMPLATE = """I direct my Executor to pay to the guardian(s) of my minor children a sum of RM{amount} {frequency} from {source} for the maintenance, education and upbringing of my said minor children until they attain the age of {age} years."""

# ============================================================
# NON-RESIDUARY GIFTS / SPECIFIC GIFTS
# ============================================================

NON_RESIDUARY_HEADING = """Non Residuary Gift(s)"""

# Joint bank accounts - give to joint holder(s)
JOINT_BANK_ACCOUNTS_TEMPLATE = """{clause_num}.  I give the moneys standing to my credit in all my joint bank accounts to the respective joint account holder(s), if more than one in equal shares."""

# Sole bank accounts - give directly to beneficiary with inline substitute
BANK_ACCOUNTS_DIRECT_TEMPLATE = """{clause_num}.  I give to my {relationship} {beneficiary_name} MALAYSIA NRIC No. {nric} the moneys standing to my credit in all my bank accounts. If my {relationship} does not survive me, then the benefit {he_she} would have received shall be given to {substitute_clause}.

The expression 'all bank accounts' in this clause shall exclude any account which has been specifically given away in this Will."""

# Bank accounts - transfer to "The Moneys" (pooling approach)
BANK_ACCOUNTS_TO_MONEYS_TEMPLATE = """{clause_num}.  I direct my Executor to transfer the moneys standing to my credit in all my bank accounts in Malaysia and in any foreign countries that are held under my sole name and those held under joint names (subject to the laws and regulations of the particular country) to form part of 'The Moneys' mentioned in Clause {moneys_clause} below."""

# EPF fallback - give directly to beneficiary
EPF_FALLBACK_DIRECT_TEMPLATE = """{clause_num}.  If the nomination(s) made by me in my Employees' Provident Fund do(es) not take effect for whatsoever reason, then I give the benefits of the nomination(s) to {beneficiary_clause}."""

# EPF/Insurance fallback - pool into The Moneys
EPF_INSURANCE_FALLBACK_TEMPLATE = """{clause_num}.  If the nomination(s) made by me in my Employees' Provident Fund and/or insurance policies do(es) not take effect for whatsoever reason, then the benefits of the nomination(s) shall form part of 'The Moneys' mentioned in Clause {moneys_clause} below."""

# Immovable properties - sell and add to "The Moneys"
PROPERTIES_SELL_TO_MONEYS_TEMPLATE = """{clause_num}.  I direct my Executor to sell all the immovable properties listed below and the net proceeds of the sale shall form part of 'The Moneys' mentioned in Clause {moneys_clause} below.

{property_list}

Unless specifically stated to the contrary in this Will, I direct that any sums required to discharge a charge or to withdraw a lien attached to this/these property(ies) shall be paid out of my residuary estate."""

# Individual property listing format
PROPERTY_ITEM_TEMPLATE = """({letter}) my property known as {address}, held under {title_reference};"""

# Specific property gift to named beneficiary with inline substitute
PROPERTY_GIFT_DIRECT_TEMPLATE = """{clause_num}.  I give to my {relationship} {beneficiary_name} MALAYSIA NRIC No. {nric} my undivided share in the property known as {property_description}. If my {relationship} does not survive me, then the benefit {he_she} would have received shall be given to {substitute_clause}.

Unless specifically stated to the contrary in this Will, I direct that any sums required to discharge a charge or to withdraw a lien attached to this property shall be paid out of my residuary estate."""

# Specific property gift to named beneficiary (no substitute)
SPECIFIC_GIFT_ABSOLUTE_TEMPLATE = """{clause_num}.  I give my undivided share in the property known as {property_description} to my {relationship} {beneficiary_name} MALAYSIA NRIC No. {nric}.

Unless specifically stated to the contrary in this Will, I direct that any sums required to discharge a charge or to withdraw a lien attached to this property shall be paid out of my residuary estate."""

# Specific gift shared among multiple beneficiaries
SPECIFIC_GIFT_SHARED_TEMPLATE = """{clause_num}.  I GIVE DEVISE AND BEQUEATH {gift_description} unto the following beneficiaries in the following shares:

{beneficiary_list}"""

# Monetary gift to named beneficiary
MONETARY_GIFT_TEMPLATE = """{clause_num}.  I give the sum of RM{amount} to my {relationship} {beneficiary_name} MALAYSIA NRIC No. {nric} absolutely."""

# ============================================================
# DISTRIBUTION OF "THE MONEYS"
# ============================================================

MONEYS_DISTRIBUTION_TEMPLATE = """{clause_num}.  With reference to Clause {from_clause} to Clause {to_clause} above, I direct my Executor to give the fund and assets stated in the respective clauses and any other provisions set aside for this clause (hereinafter known as 'The Moneys') to the beneficiaries named below in the shares indicated.

{beneficiary_shares}"""

MONEYS_BENEFICIARY_ITEM = """({roman}) my {relationship} {name} MALAYSIA NRIC No. {nric} ({share} share)"""

# ============================================================
# SUBSTITUTE BENEFICIARIES
# ============================================================

SUBSTITUTE_BENEFICIARY_TEMPLATE = """{clause_num}.  With reference to Clause {ref_clause}{sub_ref} above, if my {relationship} {name} MALAYSIA NRIC No. {nric} does not survive me, then the benefit {he_she} would have received shall be given to my {sub_relationship} {sub_name} MALAYSIA NRIC No. {sub_nric}."""

SUBSTITUTE_BENEFICIARY_MULTIPLE_TEMPLATE = """{clause_num}.  With reference to Clause {ref_clause}{sub_ref} above, if my {relationship} {name} MALAYSIA NRIC No. {nric} does not survive me, then the benefit {he_she} would have received shall be divided equally between:

{substitute_list}

If one of them does not survive me, then the other named beneficiary in this clause shall be the sole beneficiary of this gift."""

# ============================================================
# RESIDUARY ESTATE
# ============================================================

RESIDUARY_ESTATE_HEADING = """Residuary Estate"""

RESIDUARY_TEMPLATE = """{clause_num}.  Unless specifically stated to the contrary in this Will, my Trustee(s) shall hold the rest of my estate on trust to retain or sell any part thereof and:

(a) To pay debts including any sums required to secure a discharge of any charge or a withdrawal of any lien on any of my immovable properties, funeral and executorship expenses.

(b) To give the residue ('my residuary estate') to my {relationship} {beneficiary_name} MALAYSIA NRIC No. {nric}."""

RESIDUARY_WITH_SUBSTITUTE_TEMPLATE = """{clause_num}.  Unless specifically stated to the contrary in this Will, my Trustee(s) shall hold the rest of my estate on trust to retain or sell any part thereof and:

(a) To pay debts including any sums required to secure a discharge of any charge or a withdrawal of any lien on any of my immovable properties, funeral and executorship expenses.

(b) To give the residue ('my residuary estate') to my {relationship} {beneficiary_name} MALAYSIA NRIC No. {nric}.

(c) But if {he_she} does not survive me, to divide the residue ('my residuary estate') equally between {substitute_beneficiaries}. If one of them does not survive me, then the other named beneficiary in this clause shall be the sole beneficiary of this gift."""

RESIDUARY_MULTIPLE_TEMPLATE = """{clause_num}.  Unless specifically stated to the contrary in this Will, my Trustee(s) shall hold the rest of my estate on trust to retain or sell any part thereof and:

(a) To pay debts including any sums required to secure a discharge of any charge or a withdrawal of any lien on any of my immovable properties, funeral and executorship expenses.

(b) To divide the residue ('my residuary estate') among the following beneficiaries named below in the shares indicated. If any beneficiary named in this clause does not survive me, then the benefit that beneficiary would have received shall be given to the other surviving beneficiaries in equal shares.

{beneficiary_list}

(c) But if all of them do not survive me, to give the residue ('my residuary estate') to {fallback_beneficiary}."""

# ============================================================
# TESTAMENTARY TRUST
# ============================================================

TESTAMENTARY_TRUST_TEMPLATE = """I DIRECT my Executor to hold the share of {beneficiary_name} upon trust and to apply the income and/or capital thereof for the following purposes: {purposes}, until {duration}."""

TESTAMENTARY_TRUST_TRUSTEE_TEMPLATE = """I hereby appoint my {relationship} {trustee_name} MALAYSIA NRIC No. {nric} as the Trustee of the testamentary trust established for {beneficiary_name}."""

# ============================================================
# DECLARATION (Commorientes + Testator Declaration)
# ============================================================

DECLARATION_HEADING = """Declaration"""

COMMORIENTES_TEMPLATE = """{clause_num}.  For the purpose of ascertaining entitlement under this Will any beneficiary who does not survive me by {days} days shall be treated as having died before me."""

TESTATOR_DECLARATION_TEMPLATE = """{clause_num}.  In making this Will, I have conscientiously considered all aspects and all my surrounding circumstances and I am thoroughly satisfied that the provisions made in this Will absolutely reflect my wishes and intentions."""

# ============================================================
# EXCLUSION CLAUSE
# ============================================================

EXCLUSION_TEMPLATE = """{clause_num}.  I have intentionally and with full knowledge excluded {name} MALAYSIA NRIC No. {nric}, my {relationship}, from this my Will for the following reason: {reason}."""

# ============================================================
# ADMINISTRATIVE POWERS
# ============================================================

ADMIN_POWERS_TEMPLATE = """I GIVE AND GRANT unto my Executor the following powers in addition to those conferred by law:

(a) To sell, call in, collect and convert into money any part of my estate at such time and in such manner as my Executor shall in his/her absolute discretion think fit, with power to postpone the sale, calling in, collection or conversion of the whole or any part of my estate for so long as my Executor shall in his/her absolute discretion think fit without being responsible for any loss occasioned thereby;

(b) To invest and change the investments of any monies forming part of my estate in or into investments of any nature as my Executor shall in his/her absolute discretion think fit as though my Executor were the absolute beneficial owner thereof;

(c) To manage, develop, lease, mortgage, charge, or otherwise deal with any immovable property forming part of my estate as my Executor shall in his/her absolute discretion think fit;

(d) To exercise all rights, powers and privileges attached to any shares, stocks, bonds, debentures or other securities forming part of my estate, including the power to vote at meetings, to accept any composition or arrangement, and to agree to any reconstruction or amalgamation;

(e) To employ and pay solicitors, accountants, valuers, estate agents and other professional persons and agents as my Executor shall think necessary for the proper administration of my estate;

(f) To appropriate any part of my estate in or towards the satisfaction of any legacy or share in my estate without requiring the consent of any person;

(g) To apply the whole or any part of the capital or income of any minor beneficiary's share for or towards the maintenance, education and benefit of that minor beneficiary as my Executor shall in his/her absolute discretion think fit."""

# ============================================================
# TESTIMONIUM AND ATTESTATION
# ============================================================

BLANK_PAGE_TEMPLATE = """----------------------- THE REST OF THE PAGE IS INTENTIONALLY LEFT BLANK -----------------------"""

ATTESTATION_TEMPLATE = """Signature of the Testator: ________________________________________________________

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

Second Witness Contact Number: ________________________________________________________"""

# Alternative old-style attestation (for reference)
ATTESTATION_OLD_STYLE_TEMPLATE = """IN WITNESS WHEREOF I the said {testator_name} have hereunto set my hand to this my Last Will and Testament this ______ day of _____________ 20_____.


SIGNED by the abovenamed Testator    )
{testator_name}                       )
as and for {his_her} Last Will and    )  ________________________
Testament in the presence of us       )  {testator_name}
both present at the same time who     )
at {his_her} request and in {his_her} )
presence and in the presence of       )
each other have hereunto              )
subscribed our names as witnesses:    )


Witness 1:                            Witness 2:

Signature: ________________________   Signature: ________________________

Name: _____________________________   Name: _____________________________

NRIC No.: _________________________   NRIC No.: _________________________

Address: __________________________   Address: __________________________

       __________________________          __________________________

Occupation: _______________________   Occupation: _______________________"""

# ============================================================
# TRANSLATOR ATTESTATION (for special circumstances)
# ============================================================

TRANSLATOR_ATTESTATION_TEMPLATE = """I, {translator_name} MALAYSIA NRIC No. {translator_nric}, do hereby confirm that this Will was read over and explained by me to the Testator in the {language} language and the Testator appeared to perfectly understand the contents thereof and made {his_her} mark/signature in my presence and in the presence of the two witnesses named above.


________________________
{translator_name}
NRIC No.: {translator_nric}
"""

# ============================================================
# PAGE FOOTER (for multi-page wills)
# ============================================================

PAGE_FOOTER_TEMPLATE = """Page| {page_num}             {reference_number}             Continued on Page {next_page}
         Testator           |           Witness 1           |           Witness 2          |"""
