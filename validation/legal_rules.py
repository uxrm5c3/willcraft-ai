"""
Legal validation rules for Malaysian wills.
Based on:
- Wills Act 1959 (Act 346)
- Probate and Administration Act 1959 (Act 97)
- Distribution Act 1958 (Act 300)
"""

from dataclasses import dataclass
from typing import List, Literal
from datetime import date
import re


@dataclass
class ValidationResult:
    rule_id: str
    severity: Literal["ERROR", "WARNING", "INFO"]
    message: str
    field: str = ""


def validate_will_data(will_data) -> List[ValidationResult]:
    results = []

    # Rule 1: Testator must be at least 18 years old (Wills Act 1959 s.4)
    age = will_data.testator.get_age()
    if 0 < age < 18:
        results.append(ValidationResult(
            rule_id="WILLS_ACT_S4",
            severity="ERROR",
            message=f"Testator must be at least 18 years old to make a will (Wills Act 1959 s.4). Current age: {age}.",
            field="testator.date_of_birth"
        ))

    # Rule 2: Maximum 4 executors/administrators (PAA s.4(1))
    if len(will_data.executors) > 4:
        results.append(ValidationResult(
            rule_id="PAA_S4_1",
            severity="ERROR",
            message="Maximum 4 executors/administrators allowed for the same property (Probate and Administration Act 1959 s.4(1)).",
            field="executors"
        ))

    # Rule 3: If any beneficiary is a minor, recommend at least 2 executors (PAA s.4(2))
    has_minor_beneficiary = _check_minor_beneficiaries(will_data)
    non_substitute_executors = [e for e in will_data.executors if e.role != "Substitute"]
    if has_minor_beneficiary and len(non_substitute_executors) < 2:
        # Check if sole executor is spouse — downgrade to WARNING (user can override)
        is_spouse_executor = False
        if len(non_substitute_executors) == 1:
            rel = non_substitute_executors[0].relationship.lower()
            if rel in ("spouse", "husband", "wife"):
                is_spouse_executor = True
        if is_spouse_executor:
            results.append(ValidationResult(
                rule_id="PAA_S4_2_SPOUSE",
                severity="WARNING",
                message="Testator has named spouse as sole executor/trustee but minor children exist as beneficiaries. Joint executors are recommended under PAA s.4(2).",
                field="executors"
            ))
        else:
            results.append(ValidationResult(
                rule_id="PAA_S4_2",
                severity="WARNING",
                message="Joint executors are recommended when a beneficiary is a minor (Probate and Administration Act 1959 s.4(2)). You may proceed with a single executor at your own discretion.",
                field="executors"
            ))

    # Rule 4: Executors should not be minors (PAA s.20)
    # Note: We can't check executor age without their DOB, so this is a warning
    results.append(ValidationResult(
        rule_id="PAA_S20",
        severity="INFO",
        message="Ensure all executors and trustees are at least 21 years old and of sound mind (Probate and Administration Act 1959 s.20).",
        field="executors"
    ))

    # Rule 5: Must have at least one executor
    if len(will_data.executors) == 0:
        results.append(ValidationResult(
            rule_id="EXECUTOR_REQUIRED",
            severity="ERROR",
            message="At least one executor must be appointed.",
            field="executors"
        ))

    # Rule 6: Must have at least one beneficiary
    if len(will_data.beneficiaries) == 0:
        results.append(ValidationResult(
            rule_id="BENEFICIARY_REQUIRED",
            severity="ERROR",
            message="At least one beneficiary must be named in the will.",
            field="beneficiaries"
        ))

    # Rule 7: Residuary estate should have more than one beneficiary to avoid partial intestacy
    if will_data.residuary_estate and len(will_data.residuary_estate.main_beneficiaries) == 1:
        if not will_data.residuary_estate.substitute_groups:
            results.append(ValidationResult(
                rule_id="PARTIAL_INTESTACY",
                severity="WARNING",
                message="Consider having more than one residuary estate beneficiary (or substitute beneficiaries) to avoid partial intestacy.",
                field="residuary_estate"
            ))

    # Rule 8: Shares must total 100% for residuary estate main beneficiaries
    if will_data.residuary_estate and will_data.residuary_estate.main_beneficiaries:
        total = _calculate_share_total(will_data.residuary_estate.main_beneficiaries)
        if total is not None and abs(total - 100.0) > 0.01:
            results.append(ValidationResult(
                rule_id="RESIDUARY_SHARES",
                severity="ERROR",
                message=f"Residuary estate shares must total 100%. Current total: {total}%.",
                field="residuary_estate.main_beneficiaries"
            ))

    # Rule 9: Gift shares must total 100% per gift
    if will_data.gifts:
        for i, gift in enumerate(will_data.gifts):
            mb_allocations = [a for a in gift.allocations if a.role == "MB"]
            total = _calculate_gift_share_total(mb_allocations)
            if total is not None and abs(total - 100.0) > 0.01:
                results.append(ValidationResult(
                    rule_id=f"GIFT_{i+1}_SHARES",
                    severity="ERROR",
                    message=f"Gift {i+1} main beneficiary shares must total 100%. Current total: {total}%.",
                    field=f"gifts.{i}"
                ))

    # Rule 10: Translator required if special circumstances exist
    if will_data.testator.special_circumstances:
        if not will_data.testator.translator_name:
            results.append(ValidationResult(
                rule_id="TRANSLATOR_REQUIRED",
                severity="ERROR",
                message="A translator/interpreter is required when the testator has special circumstances (disability, illiterate, blind, or not proficient in English).",
                field="testator.translator_name"
            ))

    # Rule 11: Beneficiaries and their spouses cannot be translator (Wills Act s.9 principle)
    if will_data.testator.translator_name:
        for b in will_data.beneficiaries:
            if b.full_name.upper() == will_data.testator.translator_name.upper():
                results.append(ValidationResult(
                    rule_id="TRANSLATOR_BENEFICIARY",
                    severity="ERROR",
                    message=f"Beneficiary '{b.full_name}' cannot be the translator/interpreter of the will.",
                    field="testator.translator_name"
                ))

    # Rule 12: NRIC format validation (YYMMDD-SS-NNNN)
    nric = will_data.testator.nric_passport.replace("-", "").replace(" ", "")
    if len(nric) == 12 and nric.isdigit():
        # Malaysian NRIC format
        if not _validate_nric(nric):
            results.append(ValidationResult(
                rule_id="NRIC_FORMAT",
                severity="WARNING",
                message="NRIC number format may be invalid. Expected format: YYMMDD-SS-NNNN.",
                field="testator.nric_passport"
            ))

    # Rule 13: Guardian recommended if testator has minor children
    if will_data.testator.marital_status in ["Married", "Divorced", "Widow/Widower"]:
        if has_minor_beneficiary and (not will_data.guardians or len(will_data.guardians) == 0):
            results.append(ValidationResult(
                rule_id="GUARDIAN_RECOMMENDED",
                severity="WARNING",
                message="It is recommended to appoint a guardian for minor children (under 21 years old).",
                field="guardians"
            ))

    # Rule 14: Contemplation of marriage - fiance details required
    if will_data.testator.contemplation_of_marriage:
        if not will_data.testator.fiance_name:
            results.append(ValidationResult(
                rule_id="FIANCE_REQUIRED",
                severity="ERROR",
                message="Fiancé/fiancée details are required when the will is made in contemplation of marriage.",
                field="testator.fiance_name"
            ))

    # Rule 15: Cross-reference - all beneficiaries in gifts/residuary must be in Section D list
    beneficiary_names = {b.full_name.upper() for b in will_data.beneficiaries}
    if will_data.gifts:
        for i, gift in enumerate(will_data.gifts):
            for alloc in gift.allocations:
                if alloc.beneficiary_name.upper() not in beneficiary_names:
                    results.append(ValidationResult(
                        rule_id=f"GIFT_{i+1}_XREF",
                        severity="ERROR",
                        message=f"Gift {i+1} beneficiary '{alloc.beneficiary_name}' is not in the beneficiaries list (Section D).",
                        field=f"gifts.{i}"
                    ))

    if will_data.residuary_estate:
        for rb in will_data.residuary_estate.main_beneficiaries:
            if rb.beneficiary_name.upper() not in beneficiary_names:
                results.append(ValidationResult(
                    rule_id="RESIDUARY_XREF",
                    severity="WARNING",
                    message=f"Residuary beneficiary '{rb.beneficiary_name}' is not in the beneficiaries list (Step 5). This is allowed but please verify.",
                    field="residuary_estate"
                ))

    # Rule 16: Cross-check all names against identity registry for consistency
    # Build identity name→NRIC lookup from identities
    if hasattr(will_data, 'identities') and will_data.identities:
        id_lookup = {p.get('full_name', '').upper(): p.get('nric_passport', '') for p in will_data.identities if p.get('full_name')}

        # Check executors match identities
        for ex in will_data.executors:
            ex_upper = ex.full_name.upper()
            if ex_upper in id_lookup and id_lookup[ex_upper] != ex.nric_passport:
                results.append(ValidationResult(
                    rule_id="EXECUTOR_NRIC_MISMATCH",
                    severity="ERROR",
                    message=f"Executor '{ex.full_name}' NRIC '{ex.nric_passport}' does not match identity registry '{id_lookup[ex_upper]}'. Update identity or re-select executor.",
                    field="executors"
                ))

        # Check beneficiaries match identities
        for b in will_data.beneficiaries:
            b_upper = b.full_name.upper()
            if b_upper in id_lookup and id_lookup[b_upper] != b.nric_passport_birthcert:
                results.append(ValidationResult(
                    rule_id="BENEFICIARY_NRIC_MISMATCH",
                    severity="ERROR",
                    message=f"Beneficiary '{b.full_name}' NRIC '{b.nric_passport_birthcert}' does not match identity registry '{id_lookup[b_upper]}'. Update identity or re-select beneficiary.",
                    field="beneficiaries"
                ))

    return results


def get_errors(results: List[ValidationResult]) -> List[ValidationResult]:
    return [r for r in results if r.severity == "ERROR"]


def get_warnings(results: List[ValidationResult]) -> List[ValidationResult]:
    return [r for r in results if r.severity == "WARNING"]


def _check_minor_beneficiaries(will_data) -> bool:
    """Check if any beneficiary might be a minor based on relationship keywords."""
    minor_keywords = ["son", "daughter", "child", "children", "infant", "minor", "grandson", "granddaughter"]
    for b in will_data.beneficiaries:
        rel = b.relationship.lower()
        if any(kw in rel for kw in minor_keywords):
            # Check if NRIC suggests they might be young
            nric = b.nric_passport_birthcert.replace("-", "").replace(" ", "")
            if len(nric) == 12 and nric.isdigit():
                try:
                    year = int(nric[0:2])
                    # Assume 2000s for years 00-26, 1900s for 27-99
                    full_year = 2000 + year if year <= 26 else 1900 + year
                    age = date.today().year - full_year
                    if age < 18:
                        return True
                except ValueError:
                    pass
    return False


def _calculate_share_total(beneficiaries) -> float:
    """Calculate total share percentage. Returns None if shares use 'Equally' format."""
    total = 0.0
    for b in beneficiaries:
        share = b.share.strip().replace("%", "")
        if share.lower() == "equally":
            return None  # Can't sum "Equally"
        try:
            total += float(share)
        except ValueError:
            return None
    return total


def _calculate_gift_share_total(allocations) -> float:
    """Calculate total share percentage for gift allocations."""
    if not allocations:
        return None  # No allocations = skip validation
    # Single MB with empty share → assume 100%
    if len(allocations) == 1 and not allocations[0].share.strip():
        return 100.0
    total = 0.0
    for a in allocations:
        share = a.share.strip().replace("%", "")
        if not share:
            continue  # Skip empty shares
        if share.lower() == "equally":
            return None  # Can't sum "Equally"
        try:
            total += float(share)
        except ValueError:
            return None
    return total


def _validate_nric(nric: str) -> bool:
    """Basic NRIC validation: YYMMDD + state code + serial."""
    if len(nric) != 12:
        return False
    try:
        year = int(nric[0:2])
        month = int(nric[2:4])
        day = int(nric[4:6])
        if not (1 <= month <= 12):
            return False
        if not (1 <= day <= 31):
            return False
        return True
    except ValueError:
        return False
