"""KOID-aligned canonical Alan & Tan firm template (CANONICAL FORMAT).

This is the SECOND source-of-truth file referenced in CLAUDE.md §10x.24 + §10x.25
(see also §10x.206 — the canonical clause structure burn-in). The first is
documents/sample_will_phek_yi_ting.py.

Verified against:
  - PHEK YI TING sample (original §10x.25 — documents/sample_will_phek_yi_ting.py)
  - KOID BENG SUN sample (`Sample KOID BENG SUN The Last Will and Testament .docx`
    — the legally authoritative firm template)

Last updated: 2026-05-12 (commit 53ebc33 — template-conformance ship)

------------------------------------------------------------------------------
FORMAT RULES CODIFIED BELOW — DO NOT ADD PATTERNS THAT DON'T APPEAR IN BOTH
SAMPLES WITHOUT EXPLICIT USER CONFIRMATION.
------------------------------------------------------------------------------

Why TWO sample files exist:
  - PHEK was the original (Plentong landed property, sister beneficiaries,
    simple sole-share residuary).
  - KOID exercises the full pattern grammar: 4 banks (SG + MY mix),
    5 properties (sole + joint, landed + strata, with strata-suffix titles
    and historical-titles), 3 insurance policies (combined clause),
    sister-in-law executor (with substitute), wife-100% residuary with
    survivor cascade to two children.

The drafter (documents/template_filler.py + ai/drafter.py + models/gift.py)
MUST produce text matching BOTH samples for their respective inputs.
tests/will_gen/test_template_structure.py is the snapshot regression gate.

If a NEW format change request arrives (e.g. firm rebranding, NLC s.292
amendment, new asset kind), update BOTH samples + §10x.24 + the snapshot
test in the SAME commit. Drift between samples = drift between client wills.
"""

# Each clause is rendered as the AI would output it. The PDF generator handles
# header/footer/cover/signing-page styling around this body text.
#
# CANONICAL phrasing rules (excerpted from §10x.24; full table is in CLAUDE.md):
#   - Address suffix: NEVER ", MALAYSIA". End with state name.
#   - State word: "Negeri of <TitleCase>" (Malay + Title Case, NOT "State of JOHOR")
#   - Title prefix: bare type then bare number (no "No." between).
#     Examples: "Geran 528881", "Strata Title Geran 564662/M1C/30/710",
#     "H.S.(D) 251041", "PTD 127082", "Pajakan Negeri 12345".
#   - Lot prefix: "Lot <number>" — no "No." between.
#   - Leading zeros: STRIP from title_number AND lot_number.
#     "00528881" → "528881".
#   - Equal-share idiom: SINGULAR "in equal share". NEVER "in equal shares"
#     (even for 3+ beneficiaries — firm convention).
#   - Beneficiary name in clause body: UPPERCASE (e.g. "LIM BEE YAN").
#   - Revocation clause: "By signing this Will, I revoke all earlier Wills…"
#     — NO "hereby" (firm convention; differs from PHEK which DOES use "hereby"
#     for executor-appointment clause).
#   - Executor substitute single-line: "In the event that my <relationship>
#     is unwilling or unable to act for whatsoever reason, then I appoint
#     <substitute_id> to be the Executor of this Will."
#   - Executor substitute address: from Person.residential_address (residence),
#     NEVER from any gift address.
#   - Sole property with building-type descriptor:
#       "all my shares in the <building_type> known as <ADDRESS> held under …"
#     (e.g. "all my shares in the Single Storey Medium Low Cost Shop known as")
#   - Sole property without building-type descriptor:
#       "all my shares in the property known as <ADDRESS> held under …"
#   - Joint property:
#       "all my <fraction> undivided shares in the property known as <ADDRESS>
#        held under …"
#     (e.g. "all my ½ undivided shares")
#   - Bank type-name strip: drop marketing prefixes like "Plus" from
#     account_type → "Public Bank Berhad Saving Account", not
#     "Public Bank Berhad Plus Saving Account".
#   - Insurance position: AFTER all property clauses, right before residuary
#     (single combined clause per Insurance Act 1996 s.130, with
#     roman-numeral policy list).
#   - Bank substitute position: IMMEDIATELY after the bank clauses
#     (e.g. clause 8 for 4 banks 4-7), NOT after properties.
#   - Sibling order in substitutes: family-role order — spouse → son(s) →
#     daughter(s) → others. NEVER alphabetical.
#   - Residuary main "absolutely": the word "absolutely" MUST appear between
#     the main residuary beneficiary's NRIC paren and the next sentence
#     (the substitute cascade).
#   - Property substitute INLINE: every property gift with a substitute
#     clause emits it INLINE within the same clause, NOT as a separate
#     post-loop clause.
#   - Address case: property addresses rendered ALL CAPS.
#   - Signing-page parenthetical: "signed by the Testator (appeared
#     thoroughly to understand this WILL and approve it) in the presence
#     of us both…"
#   - Witness blocks: numbered "First Witness …" / "Second Witness …"
#     (NOT just "Full Name" / "Address").
#
# Anyone modifying drafter / models/gift.py and changing any of the above
# patterns MUST update §10x.24 and run the snapshot test before commit.

SAMPLE_WILL_TEXT_KOID = """LAST WILL AND TESTAMENT OF
KOID BENG SUN

This Will is made by me KOID BENG SUN (MALAYSIA NRIC No. 631204-07-5743) of NO.600, JALAN MUTIARA HIJAU 17, TAMAN MUTIARA HIJAU, 81000 KULAI, JOHOR.

Revocation

1.  By signing this Will, I revoke all earlier Wills and exclude my movable and immovable assets located in any country in which I have a separate Will made according to the laws of that country before my demise. In the event I do not have a separate Will made according to the laws of a particular country where my assets are located, then those assets shall form part of this Will and shall be distributed accordingly.

Appointment of Executor(s)

2.  I hereby appoint my sister-in-law LIM LAY CHENG (MALAYSIA NRIC No. 650629-04-5308) of NO 18, JALAN SUTERA KUNING, TAMAN SUTERA, 81200 JOHOR BAHRU, JOHOR as the Executor of this Will. In the event that my sister-in-law is unwilling or unable to act for whatsoever reason, then I appoint my son JOSHUA KOID TECK SENG (MALAYSIA NRIC No. 960525-07-5039) of NO.600, JALAN MUTIARA HIJAU 17, TAMAN MUTIARA HIJAU, 81000 KULAI, JOHOR to be the Executor of this Will.

3.  In this Will unless it is specifically stated to the contrary, my Executor(s) shall also act as my Trustee(s).

Non-Residuary Gift(s)

4.  I hereby devise and bequeath the monies in my Singapore POSB bank Account No. 030-25917-3 together with all interests/dividends already accrued due or accruing thereon unto my wife LIM BEE YAN (MALAYSIA NRIC No. 661126-04-5182) absolutely.

5.  I hereby devise and bequeath the monies in my Singapore Maybank Account No. 14200692259 together with all interests/dividends already accrued due or accruing thereon unto my wife LIM BEE YAN (MALAYSIA NRIC No. 661126-04-5182) absolutely.

6.  I hereby devise and bequeath the monies in my Public Bank Berhad Current Account No. 3244955834 together with all interests/dividends already accrued due or accruing thereon unto my wife LIM BEE YAN (MALAYSIA NRIC No. 661126-04-5182) absolutely.

7.  I hereby devise and bequeath the monies in my Public Bank Berhad Saving Account No. 5045324309 together with all interests/dividends already accrued due or accruing thereon unto my wife LIM BEE YAN (MALAYSIA NRIC No. 661126-04-5182) absolutely.

8.  In the event my wife LIM BEE YAN (MALAYSIA NRIC No. 661126-04-5182) does not survive me, my monies bequeathed to her in Clause 4-7 above shall be given to my son JOSHUA KOID TECK SENG (MALAYSIA NRIC No. 960525-07-5039) and my daughter ESTHER KOID EN HUI (MALAYSIA NRIC No. 010522-01-1110) in equal share.

9.  I hereby devise and bequeath all my shares in the Single Storey Medium Low Cost Shop known as NO.3, JALAN GUNUNG 4, SERI ALAM MASAI, 81750 MASAI, JOHOR held under H.S.(D) 251041, PTD 127082, Mukim Plentong, District of Johor Bahru, Negeri of Johor unto my son JOSHUA KOID TECK SENG (MALAYSIA NRIC No. 960525-07-5039) and my daughter ESTHER KOID EN HUI (MALAYSIA NRIC No. 010522-01-1110) in equal share. If either of my children does not survive me, then the benefit which that child would have received shall be given to and vest in the surviving child absolutely.

Unless specifically stated to the contrary in this Will, I direct that any sums required to discharge a charge or to withdraw a private caveat or lien attached to this property shall be paid out of my residuary estate.

10.  I hereby devise and bequeath all my ½ undivided shares in the property known as #30-08, MENARA C, PANGSAPURI TEPIAN BAYU, JALAN SULTANAH AMINAH, MARINA COVE, 80050 JOHOR BAHRU, JOHOR held under Strata Title Geran 564662/M1C/30/710, Lot 207922, Mukim Plentong, District of Johor Bahru, Negeri of Johor unto my daughter ESTHER KOID EN HUI (MALAYSIA NRIC No. 010522-01-1110) absolutely. If my daughter does not survive me, then the benefit she would have received shall be given to my son JOSHUA KOID TECK SENG (MALAYSIA NRIC No. 960525-07-5039).

Unless specifically stated to the contrary in this Will, I direct that any sums required to discharge a charge or to withdraw a private caveat or lien attached to this property shall be paid out of my residuary estate.

11.  I hereby devise and bequeath all my ½ undivided shares in the property known as B-05-11, MERAK KAYANGAN PERSIARAN MEDINI UTARA 3, BANDAR MEDINI ISKANDAR, 79250 ISKANDAR PUTERI, JOHOR held under Geran 528881, Lot 194139/M1B/5/209, Mukim Pulai, District of Johor, Negeri of Johor unto my son JOSHUA KOID TECK SENG (MALAYSIA NRIC No. 960525-07-5039) and my daughter ESTHER KOID EN HUI (MALAYSIA NRIC No. 010522-01-1110) in equal share. If either of my children does not survive me, then the benefit which that child would have received shall be given to and vest in the surviving child absolutely.

Unless specifically stated to the contrary in this Will, I direct that any sums required to discharge a charge or to withdraw a private caveat or lien attached to this property shall be paid out of my residuary estate.

12.  I hereby devise and bequeath all my shares in the property known as #05-01, MENARA C, PANGSAPURI TEPIAN BAYU, JALAN SULTANAH AMINAH, MARINA COVE, 80050 JOHOR BAHRU, JOHOR held under Strata Title Geran 564662/M1C/5/517, Lot 207922, Mukim Plentong, District of Johor Bahru, Negeri of Johor unto my daughter ESTHER KOID EN HUI (MALAYSIA NRIC No. 010522-01-1110) absolutely. If my daughter does not survive me, then the benefit she would have received shall be given to my son JOSHUA KOID TECK SENG (MALAYSIA NRIC No. 960525-07-5039).

Unless specifically stated to the contrary in this Will, I direct that any sums required to discharge a charge or to withdraw a private caveat or lien attached to this property shall be paid out of my residuary estate.

13.  I hereby devise and bequeath all my ½ undivided shares in the property known as NO.10, JALAN SRI LAGUNA 1/7, TAMAN LAGUNA, 81200 JOHOR BAHRU, JOHOR held under Geran 337203, Lot 135402 (Formerly known as HS(D) 431161 PTD 143086), Mukim Pulai, District of Johor Bahru, Negeri of Johor unto my son JOSHUA KOID TECK SENG (MALAYSIA NRIC No. 960525-07-5039) absolutely. If my son does not survive me, then the benefit he would have received shall be given to my daughter ESTHER KOID EN HUI (MALAYSIA NRIC No. 010522-01-1110).

Unless specifically stated to the contrary in this Will, I direct that any sums required to discharge a charge or to withdraw a private caveat or lien attached to this property shall be paid out of my residuary estate.

14.  If any nomination under my insurance policies below fails, is invalid, revoked or otherwise ineffective, the proceeds shall be given to my wife LIM BEE YAN (MALAYSIA NRIC No. 661126-04-5182) absolutely.

(i) Policy No. 1811500170 NTUC Income Singapore

(ii) Policy No. 10030125 Etiqa Insurance Malaysia

(iii) Policy No. L516911049 AIA Bhd Malaysia

Residuary Estate

15.  Unless specifically stated to the contrary in this Will, my Trustee(s) shall hold the rest of my estate on trust to retain or sell any part thereof and:

(a) To pay debts including any sums required to secure a discharge of any charge or a withdrawal of any private caveat or lien on any of my immovable properties, funeral and executorship expenses;

(b) To give the residue ('my residuary estate') to my wife LIM BEE YAN (MALAYSIA NRIC No. 661126-04-5182) absolutely. If my wife does not survive me, then the benefit she would have received shall be given to my son JOSHUA KOID TECK SENG (MALAYSIA NRIC No. 960525-07-5039) and my daughter ESTHER KOID EN HUI (MALAYSIA NRIC No. 010522-01-1110) in equal share.

Declaration

16.  I have given due consideration to all the other Beneficiaries who may have an interest in my abovementioned properties upon my death before I make this Will. Nevertheless, I do not intend to give any party or portion of the abovementioned properties to them and I leave the abovementioned properties to the persons specifically mentioned herein.

17.  For the purpose of ascertaining entitlement under this Will any beneficiary who does not survive me by thirty days shall be treated as having died before me.

********************the remaining page is intentionally left blank*********************

Signature of the Testator: _______________________________________________

Date of this Will: ___________________________________(dd/mm/yyyy)

This Last Will and Testament was signed by the Testator (appeared thoroughly to understand this WILL and approve it) in the presence of us both and attested by us in the presence of both Testator and of each other:

Signature of First Witness: _______________________________________________

First Witness Full Name: _______________________________________________

First Witness Identification: _______________________________________________

First Witness Address: _______________________________________________

                                                        _______________________________________________

                                                        _______________________________________________

First Witness Contact Number: _______________________________________________

Signature of Second Witness: _______________________________________________

Second Witness Full Name: _______________________________________________

Second Witness Identification: _______________________________________________

Second Witness Address: _______________________________________________

                                                        _______________________________________________

                                                        _______________________________________________

Second Witness Contact Number: _______________________________________________

- End of Document -"""


# ---------------------------------------------------------------------------
# Canonical clause structure (the locked order — see §10x.206).
# This is what the snapshot regression in tests/will_gen/ verifies.
# ---------------------------------------------------------------------------
CANONICAL_CLAUSE_STRUCTURE = [
    # (clause_label, is_dynamic, notes)
    ('revocation',                  False, 'Clause 1 — no "hereby"'),
    ('executor_with_inline_sub',    False, 'Clause 2 — primary + substitute inline'),
    ('trustee_equals_executor',     False, 'Clause 3 — boilerplate'),
    ('bank_gifts',                  True,  'Clauses 4..M — one per bank account'),
    ('bank_substitute_consolidated', True, 'Clause M+1 — references "Clause 4-M above"'),
    ('property_gifts_with_inline_sub', True, 'Clauses M+2..P — substitute INLINE per property'),
    ('insurance_combined',          True,  'Clause P+1 — single clause + roman list, OR absent if no insurance'),
    ('residuary_with_cascade',      False, 'Clause P+2 — "absolutely" + survivor cascade'),
    ('declaration_considered',      False, 'Clause P+3 — boilerplate'),
    ('declaration_30day',           False, 'Clause P+4 — boilerplate'),
    ('signing_page',                False, 'After clauses — page break + witness blocks'),
]
