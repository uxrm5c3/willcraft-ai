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

    def _clean_address(self) -> str:
        """Remove duplicate postcode/city/state from property address."""
        import re
        addr = self.property_address
        if not addr:
            return addr
        # Find first 5-digit postcode
        pc_match = re.search(r',\s*(\d{5})\s+', addr)
        if pc_match:
            # Keep only text before the first postcode occurrence
            street = addr[:pc_match.start()].rstrip(', ')
            postcode = pc_match.group(1)
            # Extract city (text after postcode until comma)
            after = addr[pc_match.end():]
            city_match = re.match(r'([^,]+)', after)
            city = city_match.group(1).strip() if city_match else ''
            # Rebuild: street + postcode city (once)
            if postcode and city:
                return f"{street}, {postcode} {city}"
            elif postcode:
                return f"{street}, {postcode}"
            return street
        return addr

    def to_formatted_description(self, ownership_prefix: str = "") -> str:
        """Generate Malaysian standard property description (top-tier law firm format)."""
        if not self.property_address:
            return ""
        prefix = ownership_prefix or "my property"
        clean_addr = self._clean_address()
        parts = [f"{prefix} known as {clean_addr}"]
        title_parts = []
        if self.title_type and self.title_number:
            # Normalize title type to proper case
            tt = self.title_type
            tt_map = {'GRN': 'Geran', 'GERAN': 'Geran', 'GM': 'Geran',
                       'HAKMILIK': 'Hakmilik', 'PAJAKAN': 'Pajakan Negeri',
                       'PAJAKAN NEGERI': 'Pajakan Negeri'}
            tt = tt_map.get(tt.upper(), tt) if tt else tt
            title_parts.append(f"held under {tt} No. {self.title_number}")
        if self.lot_number:
            title_parts.append(f"Lot No. {self.lot_number}")
        if self.bandar_pekan:
            # Strip leading "Mukim"/"MUKIM"/"Bandar" to avoid duplication
            mukim_val = self.bandar_pekan.strip()
            for pfx in ['MUKIM ', 'Mukim ', 'BANDAR ', 'Bandar ']:
                if mukim_val.upper().startswith(pfx.upper()):
                    mukim_val = mukim_val[len(pfx):]
                    break
            title_parts.append(f"Mukim {mukim_val}")
        if self.daerah:
            daerah_val = self.daerah.strip()
            for pfx in ['DAERAH ', 'Daerah ', 'DISTRICT OF ', 'District of ']:
                if daerah_val.upper().startswith(pfx.upper()):
                    daerah_val = daerah_val[len(pfx):]
                    break
            title_parts.append(f"Daerah {daerah_val}")
        if self.negeri:
            negeri_val = self.negeri.strip()
            for pfx in ['NEGERI ', 'Negeri ', 'STATE OF ', 'State of ']:
                if negeri_val.upper().startswith(pfx.upper()):
                    negeri_val = negeri_val[len(pfx):]
                    break
            # Normalize to official state name with honorific
            _STATE_NAMES = {
                'JOHOR': 'Johor Darul Ta\'zim', 'KEDAH': 'Kedah Darul Aman',
                'KELANTAN': 'Kelantan Darul Naim', 'MELAKA': 'Melaka',
                'NEGERI SEMBILAN': 'Negeri Sembilan Darul Khusus',
                'PAHANG': 'Pahang Darul Makmur', 'PERAK': 'Perak Darul Ridzuan',
                'PERLIS': 'Perlis Indera Kayangan', 'PULAU PINANG': 'Pulau Pinang',
                'SABAH': 'Sabah', 'SARAWAK': 'Sarawak',
                'SELANGOR': 'Selangor Darul Ehsan', 'TERENGGANU': 'Terengganu Darul Iman',
                'W.P. KUALA LUMPUR': 'Wilayah Persekutuan Kuala Lumpur',
                'W.P. LABUAN': 'Wilayah Persekutuan Labuan',
                'W.P. PUTRAJAYA': 'Wilayah Persekutuan Putrajaya',
            }
            negeri_val = _STATE_NAMES.get(negeri_val.upper(), negeri_val)
            title_parts.append(f"Negeri {negeri_val}")
        if title_parts:
            parts.extend(title_parts)
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
        """Build ownership prefix for asset descriptions.
        Returns prefix ending with 'property' — to_formatted_description appends 'known as [address]'.
        """
        if self.gift_type == "property":
            if self.testator_share:
                return f"all my {self.testator_share} undivided shares in the property"
            elif self.ownership_type == "joint":
                return "my undivided share in the property"
            else:
                return "my property"
        elif self.gift_type == "financial":
            if self.ownership_type == "joint":
                return "my share of the moneys in my joint account at"
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
