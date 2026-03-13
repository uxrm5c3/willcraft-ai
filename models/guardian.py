from pydantic import BaseModel
from typing import Optional, Literal


class Guardian(BaseModel):
    full_name: str
    address: str
    nric_passport: str
    relationship: str
    role: Literal["Primary", "Joint", "Substitute"] = "Primary"


class GuardianAllowance(BaseModel):
    payment_mode: Literal["One-Off", "Monthly", "Quarterly", "Yearly", "Discretion of Executor/Trustee", "Other"] = "Monthly"
    other_mode: Optional[str] = None
    amount: Optional[str] = None
    until_age: Optional[int] = None
    source_of_payment: Optional[str] = None
