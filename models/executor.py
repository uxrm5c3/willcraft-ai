from pydantic import BaseModel
from typing import Literal, Optional


class Executor(BaseModel):
    full_name: str
    address: str
    nric_passport: str
    relationship: str
    role: Literal["Primary", "Joint", "Substitute"] = "Primary"
    person_id: Optional[str] = None
    nationality: str = "Malaysian"
