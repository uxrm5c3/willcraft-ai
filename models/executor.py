from pydantic import BaseModel
from typing import Literal


class Executor(BaseModel):
    full_name: str
    address: str
    nric_passport: str
    relationship: str
    role: Literal["Primary", "Joint", "Substitute"] = "Primary"
