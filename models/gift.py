from pydantic import BaseModel
from typing import List, Literal, Optional


class PropertyDetails(BaseModel):
    """Malaysian standard property format fields."""
    property_address: str = ""
    title_type: str = ""        # HSD, HSM, GRN, EMR, PM, PN, PAJAKAN, etc.
    title_number: str = ""
    lot_number: str = ""
    bandar_pekan: str = ""      # Bandar/Pekan (township)
    daerah: str = ""            # District
    negeri: str = ""            # State

    def to_formatted_description(self) -> str:
        """Generate Malaysian standard property description."""
        if not self.property_address:
            return ""
        parts = [f"my property known as {self.property_address}"]
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

    def to_formatted_description(self) -> str:
        """Generate formatted financial asset description."""
        parts = []
        if self.institution:
            parts.append(self.institution)
        if self.account_number:
            parts.append(f"(Account No. {self.account_number})")
        if self.asset_type:
            parts.append(f"- {self.asset_type}")
        if self.description:
            parts.append(f": {self.description}")
        return " ".join(parts) if parts else self.description


class GiftAllocation(BaseModel):
    beneficiary_name: str
    share: str  # e.g., "100%", "50%", "Equally"
    role: Literal["MB", "SB"] = "MB"  # Main Beneficiary or Substitute Beneficiary


class Gift(BaseModel):
    gift_type: Literal["property", "financial", "other"] = "other"
    description: str = ""  # Kept for backward compat & manual override / "other" type
    property_details: Optional[PropertyDetails] = None
    financial_details: Optional[FinancialDetails] = None
    allocations: List[GiftAllocation] = []
    subject_to_trust: bool = False
    subject_to_guardian_allowance: bool = False

    def get_formatted_description(self) -> str:
        """Return the final formatted description based on gift type."""
        if self.gift_type == "property" and self.property_details:
            formatted = self.property_details.to_formatted_description()
            if formatted:
                return formatted
        elif self.gift_type == "financial" and self.financial_details:
            formatted = self.financial_details.to_formatted_description()
            if formatted:
                return formatted
        return self.description
