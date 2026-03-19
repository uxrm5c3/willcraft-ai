from pydantic import BaseModel
from typing import Optional, List
from models.testator import Testator
from models.executor import Executor
from models.guardian import Guardian, GuardianAllowance
from models.beneficiary import Beneficiary
from models.gift import Gift
from models.residuary import ResiduaryEstate
from models.trust import TestamentaryTrust
from models.other_matters import OtherMatters


class Trustee(BaseModel):
    full_name: str
    address: str = ''
    nric_passport: str = ''
    relationship: str = ''
    person_id: Optional[str] = None
    nationality: str = "Malaysian"


class WillData(BaseModel):
    testator: Testator
    executors: List[Executor] = []
    trustee_same_as_executor: bool = True
    trustees: Optional[List[Trustee]] = None
    substitute_trustee: Optional[Trustee] = None  # backward compat (first substitute)
    substitute_trustees: Optional[List[Trustee]] = None  # multiple joint substitutes
    guardians: Optional[List[Guardian]] = None
    guardian_allowance: Optional[GuardianAllowance] = None
    exclude_spouse_as_guardian: bool = False
    exclude_spouse_guardian_reason: Optional[str] = None
    beneficiaries: List[Beneficiary] = []
    gifts: Optional[List[Gift]] = None
    residuary_estate: ResiduaryEstate = ResiduaryEstate()
    testamentary_trust: Optional[TestamentaryTrust] = None
    other_matters: Optional[OtherMatters] = None
    identities: Optional[List[dict]] = None  # Raw identity registry for cross-validation
