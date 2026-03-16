from pydantic import BaseModel
from typing import Optional


class Beneficiary(BaseModel):
    full_name: str
    nric_passport_birthcert: str
    relationship: str
    person_id: Optional[str] = None
    nationality: str = "Malaysian"
