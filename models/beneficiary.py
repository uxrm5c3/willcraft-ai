from pydantic import BaseModel


class Beneficiary(BaseModel):
    full_name: str
    nric_passport_birthcert: str
    relationship: str
