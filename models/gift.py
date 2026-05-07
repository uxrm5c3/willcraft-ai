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
        """🔥 §10x.16/.23 — match Phek Yi Ting standard:
            Bank:     "the monies in my [BANK] [SAVING/CURRENT/FIXED] Account
                       No. [N] together with all interests/dividends already
                       accrued due or accruing thereon"
            Insurance: "the benefits of my [INSURER] insurance policy
                        No. [N] together with all bonuses or accretions
                        already declared or accruing thereon"
            EPF:      "the moneys standing to my credit in my Employees'
                       Provident Fund Account No. [N]"
            Mutual fund: "all monies held in any of my [INSTITUTION] funds
                          together with all interests/dividends..."
        Generic ownership_prefix is applied for joint accounts (e.g.
        "my share of the moneys in my joint account at" + institution).
        """
        kind = (self.asset_type or '').strip().lower()
        inst = (self.institution or '').strip()
        acct = (self.account_number or '').strip()

        if kind == 'bank' or 'bank' in kind:
            # "the monies in my POSB Bank Account No. 030-25917-3 together
            #  with all interests/dividends already accrued due or accruing
            #  thereon"
            if ownership_prefix and 'joint' in ownership_prefix.lower():
                # joint-account variant
                base = f'{ownership_prefix} {inst}' if inst else ownership_prefix
                if acct:
                    base += f' Account No. {acct}'
                return base + (
                    " together with all interests/dividends already accrued "
                    "due or accruing thereon"
                )
            base = "the monies in my"
            if inst:
                base += f' {inst}'
            if acct:
                base += f' Account No. {acct}'
            return base + (
                " together with all interests/dividends already accrued "
                "due or accruing thereon"
            )
        if kind == 'insurance':
            base = "the benefits of my"
            if inst:
                base += f' {inst}'
            base += " insurance policy"
            if acct:
                base += f' No. {acct}'
            return base + (
                " together with all bonuses or accretions already declared "
                "or accruing thereon"
            )
        if kind == 'epf' or kind == 'kwsp':
            base = "the moneys standing to my credit in my Employees' Provident Fund"
            if acct:
                base += f' Account No. {acct}'
            return base
        if kind in ('mutual_fund', 'unit_trust', 'shares'):
            base = "all monies held in any of my"
            if inst:
                base += f' {inst} funds'
            return base + (
                " together with all interests/dividends already accrued "
                "due or accruing thereon"
            )
        # Fallback: legacy concatenation
        parts = []
        if ownership_prefix:
            parts.append(ownership_prefix)
        if inst:
            parts.append(inst)
        if acct:
            parts.append(f"(Account No. {acct})")
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
        🔥 §10x.13 — sole properties (testator_share='1/1' OR no share)
        say "the property known as ..." — NOT "all my 1/1 undivided shares".
        Joint properties say "all my [N/M] undivided shares in the property".
        """
        if self.gift_type == "property":
            ts = (self.testator_share or '').strip()
            ot = (self.ownership_type or '').strip().lower()
            # Sole ownership — full property
            if ts in ('1/1', '1', '') and ot != 'joint':
                return "the property"
            # Joint ownership — testator's share only
            if ts and ts not in ('1/1', '1'):
                return f"all my {ts} undivided shares in the property"
            if ot == "joint":
                return "my undivided share in the property"
            return "the property"
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
