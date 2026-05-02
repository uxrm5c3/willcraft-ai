"""Empty template will — same Alan & Tan format but with placeholder tokens
in lieu of real data. Used by admin to visually verify the format spec
without populating actual testator info.

Open via /admin/will-format-preview?empty=1
"""

EMPTY_TEMPLATE_WILL_TEXT = """LAST WILL AND TESTAMENT OF
{{TESTATOR_FULL_NAME}}

This Will is made by me {{TESTATOR_FULL_NAME}} (MALAYSIA NRIC No. {{TESTATOR_NRIC}}) of {{TESTATOR_ADDRESS}}.

Revocation

1.  By signing this Will, I hereby revoke all earlier Wills and exclude my movable and immovable assets located in any country in which I have a separate Will made according to the laws of that country before my demise. In the event I do not have a separate Will made according to the laws of a particular country where my assets are located, then those assets shall form part of this Will and shall be distributed accordingly.

Appointment of Executor(s)

2.  I hereby appoint my {{EXEC_RELATIONSHIP}} {{EXEC_FULL_NAME}} (MALAYSIA NRIC No. {{EXEC_NRIC}}) of {{EXEC_ADDRESS}} as the Executor of this Will. In the event that {{EXEC_PRONOUN}} is unable or unwilling to act for whatsoever reason, then I appoint my {{SUB_EXEC_RELATIONSHIP}} {{SUB_EXEC_FULL_NAME}} (MALAYSIA NRIC No. {{SUB_EXEC_NRIC}}) of {{SUB_EXEC_ADDRESS}} to be the Executor of this Will.

3.  In this Will unless it is specifically stated to the contrary, my Executor(s) shall also act as my Trustee(s).

Non-Residuary Gift(s)

4.  I hereby devise and bequeath {{GIFT_1_DESCRIPTION}} unto my {{GIFT_1_BENEFICIARY_RELATIONSHIP}} {{GIFT_1_BENEFICIARY_NAME}} (MALAYSIA NRIC No. {{GIFT_1_BENEFICIARY_NRIC}}){{GIFT_1_ADDITIONAL_BENEFICIARIES}} {{GIFT_1_DISTRIBUTION_NOTE}}.

5.  I hereby devise and bequeath {{GIFT_2_DESCRIPTION}} unto my {{GIFT_2_BENEFICIARY_RELATIONSHIP}} {{GIFT_2_BENEFICIARY_NAME}} (MALAYSIA NRIC No. {{GIFT_2_BENEFICIARY_NRIC}}){{GIFT_2_ADDITIONAL_BENEFICIARIES}} {{GIFT_2_DISTRIBUTION_NOTE}}.

6.  I hereby devise and bequeath {{REAL_PROPERTY_SHARE}} undivided shares in the property known as {{PROPERTY_ADDRESS}} held under {{TITLE_TYPE}} No. {{TITLE_NUMBER}}, Lot No. {{LOT_NUMBER}}, Mukim {{MUKIM}}, District of {{DISTRICT}}, State of {{STATE}} unto my {{GIFT_3_BENEFICIARY_RELATIONSHIP}} {{GIFT_3_BENEFICIARY_NAME}} (MALAYSIA NRIC No. {{GIFT_3_BENEFICIARY_NRIC}}){{GIFT_3_ADDITIONAL_BENEFICIARIES}} {{GIFT_3_DISTRIBUTION_NOTE}}.

Unless specifically stated to the contrary in this Will, I direct that any sums required to discharge a charge or to withdraw a private caveat or lien attached to this property shall be paid out of my residuary estate.

Residuary Estate

7.  Unless specifically stated to the contrary in this Will, my Trustee(s) shall hold the rest of my estate on trust to retain or sell any part thereof and:

(a) To pay debts including any sums required to secure a discharge of any charge or a withdrawal of any private caveat or lien on any of my immovable properties, funeral and executorship expenses;

(b) To give the residue ('my residuary estate') to {{RESIDUARY_BENEFICIARIES_LIST}} {{RESIDUARY_DISTRIBUTION_NOTE}}.

Declaration

8.  I have given due consideration to all the other Beneficiaries who may have an interest in my abovementioned properties upon my death before I make this Will. Nevertheless, I do not intend to give any party or portion of the abovementioned properties to them and I leave the abovementioned properties to the persons specifically mentioned herein.

9.  For the purpose of ascertaining entitlement under this Will any beneficiary who does not survive me by thirty days shall be treated as having died before me.

********************the remaining page is intentionally left blank*********************

Signature of the Testator: _______________________________________________

Date of this Will: ___________________________________(dd/mm/yyyy)

This Last Will and Testament was signed by the Testator in the presence of us both and attested by us in the presence of both Testator and of each other:

Signature of First Witness: _______________________________________________

Full Name: _______________________________________________

NRIC / Passport No.: _______________________________________________

Address: _______________________________________________

         _______________________________________________

         _______________________________________________

Contact No.: _______________________________________________

Signature of Second Witness: _______________________________________________

Full Name: _______________________________________________

NRIC / Passport No.: _______________________________________________

Address: _______________________________________________

         _______________________________________________

         _______________________________________________

Contact No.: _______________________________________________

- End of Document -"""
