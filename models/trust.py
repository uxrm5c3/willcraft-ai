from pydantic import BaseModel
from typing import List, Optional, Literal


class TrustBeneficiary(BaseModel):
    beneficiary_name: str
    share: str
    role: Literal["MB", "SB"] = "MB"


class TestamentaryTrust(BaseModel):
    beneficiaries: List[TrustBeneficiary] = []
    purposes: List[str] = []  # "Education", "Health", "Maintenance", "Others"
    other_purpose: Optional[str] = None
    # For immovable properties
    property_use: Optional[Literal["Let/Lease", "Reside"]] = None
    duration: Optional[str] = None
    assets_from_gifts: List[str] = []  # Gift numbers, e.g., ["Gift 1", "Gift 3"]
    payment_mode: Optional[Literal["Monthly", "Quarterly", "Yearly", "Discretion of Trustee", "Other"]] = None
    payment_amount: Optional[str] = None
    other_payment_mode: Optional[str] = None
    # Balance of trust
    balance_beneficiaries: List[TrustBeneficiary] = []
    # Separate trustee (if different from executors)
    separate_trustee: bool = False
    trustee_name: Optional[str] = None
    trustee_address: Optional[str] = None
    trustee_nric: Optional[str] = None
    trustee_relationship: Optional[str] = None
