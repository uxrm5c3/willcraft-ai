from pydantic import BaseModel
from typing import List, Optional


class ResiduaryBeneficiary(BaseModel):
    beneficiary_name: str
    share: str  # e.g., "50%", "Equally"
    group: str = "main"  # "main", "substitute_1", "substitute_2", "substitute_3"


class ResiduaryEstate(BaseModel):
    main_beneficiaries: List[ResiduaryBeneficiary] = []
    substitute_groups: List[List[ResiduaryBeneficiary]] = []
    additional_notes: Optional[str] = None
