from pydantic import BaseModel
from typing import List, Literal


class GiftAllocation(BaseModel):
    beneficiary_name: str
    share: str  # e.g., "100%", "50%", "Equally"
    role: Literal["MB", "SB"] = "MB"  # Main Beneficiary or Substitute Beneficiary


class Gift(BaseModel):
    description: str  # e.g., "My property at No. 10, Jalan ..."
    allocations: List[GiftAllocation] = []
    subject_to_trust: bool = False
    subject_to_guardian_allowance: bool = False
