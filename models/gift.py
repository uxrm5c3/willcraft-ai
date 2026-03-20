from pydantic import BaseModel
from typing import Any, List, Literal, Optional


class PropertyDetails(BaseModel):
    """Malaysian standard property format fields."""
    property_address: str = ""
    title_type: str = ""        # HSD, HSM, GRN, EMR, PM, PN, PAJAKAN, etc.
    title_number: str = ""
    lot_number: str = ""
    bandar_pekan: str = ""      # Bandar/Pekan (township)
    daerah: str = ""            # District
    negeri: str = ""            # State

    def to_formatted_description(self, ownership_prefix: str = "") -> str:
        """Generate Malaysian standard property description."""
        if not self.property_address:
            return ""
        prefix = ownership_prefix or "my property"
        parts = [f"{prefix} known as {self.property_address}"]
        title_parts = []
        if self.title_type and self.title_number:
            title_parts.append(f"held under {self.title_type} No. {self.title_number}")
        if self.lot_number:
            title_parts.append(f"LOT No. {self.lot_number}")
        if self.bandar_pekan:
            title_parts.append(f"BANDAR {self.bandar_pekan.upper()}")
        if self.daerah:
            title_parts.append(f"DAERAH {self.daerah.upper()}")
        if self.negeri:
            title_parts.append(f"NEGERI {self.negeri.upper()}")
        if title_parts:
            parts.extend(title_parts)
        parts.append("MALAYSIA")
        return ", ".join(parts) + ";"


class FinancialDetails(BaseModel):
    """Financial/other asset structured fields."""
    institution: str = ""
    account_number: str = ""
    asset_type: str = ""        # savings, current, fixed_deposit, etc.
    description: str = ""

    def to_formatted_description(self, ownership_prefix: str = "") -> str:
        """Generate formatted financial asset description."""
        parts = []
        if ownership_prefix:
            parts.append(ownership_prefix)
        if self.institution:
            parts.append(self.institution)
        if self.account_number:
            parts.append(f"(Account No. {self.account_number})")
        if self.asset_type:
            parts.append(f"- {self.asset_type}")
        if self.description:
            parts.append(f": {self.description}")
        return " ".join(parts) if parts else self.description


class SubstituteBeneficiary(BaseModel):
    """A substitute beneficiary linked to a specific main beneficiary."""
    beneficiary_name: str
    share: str = "100%"


class GiftAllocation(BaseModel):
    beneficiary_name: str
    share: str  # e.g., "100%", "50%", "Equally"
    role: Literal["MB", "SB"] = "MB"  # Kept for backward compat; new code uses substitutes list
    substitutes: List[SubstituteBeneficiary] = []  # Individual substitute mode: linked SBs for this MB


class Gift(BaseModel):
    gift_type: Literal["property", "financial", "other"] = "other"
    description: str = ""  # Kept for backward compat & manual override / "other" type
    property_details: Optional[PropertyDetails] = None
    financial_details: Optional[FinancialDetails] = None
    allocations: List[GiftAllocation] = []
    subject_to_trust: bool = False
    subject_to_guardian_allowance: bool = False
    # Sell property directive
    sell_property: bool = False
    # Substitute mode: what happens if a main beneficiary predeceases testator
    substitute_mode: Literal["equal", "prorata", "specific", "survivorship", "individual"] = "equal"
    # Joint ownership fields
    ownership_type: Literal["sole", "joint"] = "sole"
    testator_share: Optional[str] = None   # e.g., "1/2", "1/3", "equal share"
    joint_owners: Optional[str] = None     # name(s) of co-owner(s)
    # Encumbrance (property)
    encumbrance_status: Literal["clean", "encumbered"] = "clean"
    debt_source: Optional[str] = None      # residuary, sale, insurance, specific
    # Account ownership (financial)
    account_ownership: Literal["individual", "joint"] = "individual"

    def _ownership_prefix(self) -> str:
        """Build ownership prefix for asset descriptions."""
        if self.ownership_type == "joint" and self.testator_share:
            frac = self.testator_share
            if self.gift_type == "property":
                return f"my {frac} undivided share and interest in the property"
            elif self.gift_type == "financial":
                return f"my share of the moneys in my joint account at"
        return ""

    def get_formatted_description(self) -> str:
        """Return the final formatted description based on gift type."""
        prefix = self._ownership_prefix()
        if self.gift_type == "property" and self.property_details:
            formatted = self.property_details.to_formatted_description(
                ownership_prefix=prefix or "my property"
            )
            if formatted:
                return formatted
        elif self.gift_type == "financial" and self.financial_details:
            formatted = self.financial_details.to_formatted_description(
                ownership_prefix=prefix
            )
            if formatted:
                return formatted
        return self.description

    # Accept extra fields from old data without error
    class Config:
        extra = "ignore"
