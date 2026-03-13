from pydantic import BaseModel, field_validator
from typing import Optional, List, Literal
from datetime import date


class Testator(BaseModel):
    full_name: str
    nric_passport: str
    residential_address: str
    nationality: str = "Malaysian"
    country_of_residence: str = "Malaysia"
    date_of_birth: str  # DD-MM-YYYY string from form
    occupation: str
    religion: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    gender: Literal["Male", "Female"] = "Male"
    marital_status: Literal["Single", "Married", "Divorced", "Widow/Widower"] = "Single"
    has_prior_will: bool = False
    property_coverage: Literal["Malaysia", "Overseas", "Both"] = "Malaysia"
    overseas_country: Optional[str] = None
    # Contemplation of marriage
    contemplation_of_marriage: bool = False
    fiance_name: Optional[str] = None
    fiance_nric: Optional[str] = None
    # Execution details
    signing_method: Literal["Signature", "Thumbprint"] = "Signature"
    special_circumstances: List[str] = []
    translator_name: Optional[str] = None
    translator_nric: Optional[str] = None
    translator_relationship: Optional[str] = None
    translator_language: Optional[str] = None

    @field_validator("full_name")
    @classmethod
    def name_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Full name is required")
        return v.strip().upper()

    @field_validator("nric_passport")
    @classmethod
    def nric_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("NRIC/Passport number is required")
        return v.strip()

    def get_age(self) -> int:
        try:
            parts = self.date_of_birth.split("-")
            if len(parts) == 3:
                dob = date(int(parts[2]), int(parts[1]), int(parts[0]))
                today = date.today()
                return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        except (ValueError, IndexError):
            pass
        return 0
