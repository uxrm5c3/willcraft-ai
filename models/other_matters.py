from pydantic import BaseModel
from typing import Optional


class OtherMatters(BaseModel):
    # Terms of endearment
    terms_of_endearment: Optional[str] = None
    # Commorientes - survivorship clause
    commorientes_enabled: bool = False
    commorientes_days: Optional[int] = None
    # Exclusion of family members
    exclusion_enabled: bool = False
    exclusion_name: Optional[str] = None
    exclusion_nric: Optional[str] = None
    exclusion_relationship: Optional[str] = None
    exclusion_reason: Optional[str] = None
    # Unnamed children inclusion
    unnamed_children_enabled: bool = False
    unnamed_children_spouse_name: Optional[str] = None
    unnamed_children_spouse_nric: Optional[str] = None
    # Additional instructions
    additional_instructions: Optional[str] = None
