from pydantic import BaseModel
from typing import List, Literal, Optional


class ResiduaryBeneficiary(BaseModel):
    beneficiary_name: str
    share: str  # e.g., "50%", "Equally"
    group: str = "main"  # "main", "substitute_1", "substitute_2", "substitute_3"


class IndividualSubstitute(BaseModel):
    """Links a substitute beneficiary to a specific main beneficiary."""
    for_beneficiary: str       # name of the main beneficiary this substitutes for
    substitute_name: str       # name of the substitute beneficiary
    substitute_person_id: str = ""


class ResiduaryEstate(BaseModel):
    main_beneficiaries: List[ResiduaryBeneficiary] = []
    substitute_groups: List[List[ResiduaryBeneficiary]] = []
    additional_notes: Optional[str] = None
    # Substitute mode: survivorship (share to other MBs) or individual (each MB has own SB)
    substitute_mode: Literal["survivorship", "individual"] = "survivorship"
    individual_substitutes: List[IndividualSubstitute] = []
