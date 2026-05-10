import re
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
        """Generate Phek-format property description.

        🔒 LOCKED to verbatim Phek Yi Ting sample (CLAUDE.md §10x.24):
            "all my ¼ undivided shares in the property known as
             NO. 68, JALAN SONGKIT 3, TAMAN SENTOSA, 80150 JOHOR BAHRU, JOHOR
             held under Geran No. 433036, Lot No. 12058, Mukim Plentong,
             District of Johor Bahru, State of Johor"

        Key points (must match verbatim):
          - Address keeps trailing state — do NOT strip it
          - "Mukim X" stays Malay
          - "District of X" + "State of X" use ENGLISH (not Daerah / Negeri)
          - State name is plain ("State of Johor"), no Darul Ta'zim honorific
          - Period at end (".") — set by the caller, NOT by this fn
        """
        if not self.property_address:
            return ""
        prefix = ownership_prefix or "my property"
        # 🔒 Phek-lock + §10x.194 (T-29) — apply _format_address_phek for
        # full normalisation: strip parens metadata, convert "House at NN"
        # → "NO.NN", collapse newlines, append MALAYSIA suffix when missing.
        addr = (self.property_address or '').strip()
        try:
            from documents.template_filler import _format_address_phek
            addr = _format_address_phek(addr, nationality='Malaysian')
            # Property addresses don't need MALAYSIA suffix when followed by
            # "held under..." — strip if present (Phek doesn't add it on
            # property bequests, only on testator/executor opening).
            addr = re.sub(r',\s*MALAYSIA\s*$', '', addr, flags=re.IGNORECASE).strip().rstrip(',')
        except Exception:
            # Fall back to original collapse-only behaviour
            addr = re.sub(r'\s*,\s*', ', ', addr)
            addr = re.sub(r'\s+', ' ', addr).strip().rstrip(',')
        parts = [f"{prefix} known as {addr}"]
        title_parts = []
        # 🔥 §10x.193 + §10x.39 — Phek format demands a "held under [TYPE] No. N"
        # prefix on the title number. Auto-detect type when not set:
        #   - title_number contains "/" → Strata Title Geran (per T-39)
        #   - title_number prefix contains H.S.(D)/HSD → H.S.(D)
        #   - else default to "Geran"
        # Also: when title_type is HSD/HSM, the "lot_number" is actually a
        # PTD/PT (Pejabat Tanah Daerah) number — emit "PTD N" not "Lot No. N"
        # per T-31.
        tt_raw = (self.title_type or '').strip()
        tn = (self.title_number or '').strip()
        ln = (self.lot_number or '').strip()
        # Auto-detect title type from title_number pattern
        if tn and not tt_raw:
            if '/' in tn:
                tt_raw = 'Strata Title Geran'
            elif tn.upper().startswith(('HSD', 'HS(D)', 'H.S.(D)')):
                tt_raw = 'HSD'
            elif tn.upper().startswith(('HSM', 'HS(M)', 'H.S.(M)')):
                tt_raw = 'HSM'
            else:
                tt_raw = 'Geran'
        tt_map = {'GRN': 'Geran', 'GERAN': 'Geran', 'GM': 'Geran',
                   'HAKMILIK': 'Hakmilik', 'PAJAKAN': 'Pajakan Negeri',
                   'PAJAKAN NEGERI': 'Pajakan Negeri',
                   'HSD': 'H.S.(D)', 'HSM': 'H.S.(M)', 'HS(D)': 'H.S.(D)',
                   'HS(M)': 'H.S.(M)', 'PTD': 'PTD', 'PTM': 'PTM',
                   'STRATA TITLE GERAN': 'Strata Title Geran'}
        tt = tt_map.get(tt_raw.upper(), tt_raw) if tt_raw else ''
        if tt and tn:
            title_parts.append(f"held under {tt} No. {tn}")
        elif tn:  # have title number but no type — emit anyway with default
            title_parts.append(f"held under Geran No. {tn}")
        if ln:
            # 🔥 §10x.31 / T-31 — HSD-titled properties use PTD prefix not Lot No.
            tt_upper = (tt or tt_raw).upper()
            if 'HSD' in tt_upper or 'HS(D)' in tt_upper or 'H.S.(D)' in tt_upper:
                title_parts.append(f"PTD {ln}")
            elif 'HSM' in tt_upper or 'HS(M)' in tt_upper:
                title_parts.append(f"PT {ln}")
            else:
                title_parts.append(f"Lot No. {ln}")
        if self.bandar_pekan:
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
            # 🔒 Phek uses ENGLISH "District of X" (not Malay "Daerah X")
            title_parts.append(f"District of {daerah_val}")
        if self.negeri:
            negeri_val = self.negeri.strip()
            for pfx in ['NEGERI ', 'Negeri ', 'STATE OF ', 'State of ']:
                if negeri_val.upper().startswith(pfx.upper()):
                    negeri_val = negeri_val[len(pfx):]
                    break
            # 🔒 Phek uses plain state name ("State of Johor") — no
            # "Darul Ta'zim" honorific appended. The lookup table is
            # available below for cases where the firm explicitly wants
            # the honorific, but DEFAULT is plain.
            title_parts.append(f"State of {negeri_val}")
        # 🔒 Phek format: address is followed by " held under" (SPACE, no
        # comma). The remaining title-parts are then comma-joined together
        # AFTER "held under". Verbatim:
        #   "...80150 JOHOR BAHRU, JOHOR held under Geran No. 433036,
        #    Lot No. 12058, Mukim Plentong, District of Johor Bahru,
        #    State of Johor"
        if title_parts:
            # First item carries "held under <type> No. N"; subsequent items
            # are simple "Lot No. N" / "Mukim X" / etc. comma-joined.
            head = title_parts[0]   # "held under <type> No. N"
            rest = title_parts[1:]
            joined = head + (", " + ", ".join(rest) if rest else '')
            return f"{parts[0]} {joined}"
        return parts[0]


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
            # 🔒 Phek format example:
            # "the monies in my United Overseas Bank Saving Account
            #  No. 9613005435 together with all interests/dividends
            #  already accrued due or accruing thereon"
            # The account type ("Saving"/"Current"/"Fixed Deposit") is
            # injected between the institution name and "Account No.".
            acct_type = (self.description or '').strip()
            # Common normalisations
            if acct_type:
                _t_map = {'saving': 'Saving', 'savings': 'Saving',
                           'current': 'Current',
                           'current account': 'Current',
                           'savings account': 'Saving',
                           'saving account': 'Saving',
                           'fixed deposit': 'Fixed Deposit',
                           'fixed_deposit': 'Fixed Deposit',
                           'fd': 'Fixed Deposit',
                           'plus saving': 'Plus Saving',
                           'plus saving account': 'Plus Saving'}
                acct_type = _t_map.get(acct_type.lower(), acct_type)
                # 🔥 §10x.151d — strip trailing " Account" to prevent
                # duplicate when we append " Account No. ..." below.
                # AI Summary JSON injects account_type strings like
                # "Plus Saving Account" / "Current Account" verbatim;
                # without this strip the will reads "...Plus Saving
                # Account Account No. ...".
                if acct_type.lower().endswith(' account'):
                    acct_type = acct_type[:-len(' account')].strip()
            if ownership_prefix and 'joint' in ownership_prefix.lower():
                # joint-account variant
                base = f'{ownership_prefix} {inst}' if inst else ownership_prefix
                if acct_type:
                    base += f' {acct_type}'
                if acct:
                    base += f' Account No. {acct}'
                return base + (
                    " together with all interests/dividends already accrued "
                    "due or accruing thereon"
                )
            base = "the monies in my"
            if inst:
                base += f' {inst}'
            if acct_type:
                base += f' {acct_type}'
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
        🔒 §10x.24 / Phek format — fractions rendered with unicode glyphs
        (¼, ½, ¾, ⅓, ⅔) when standard, ASCII otherwise.
        """
        # Unicode fraction map for Phek format
        _FRAC = {'1/2': '½', '1/3': '⅓', '2/3': '⅔', '1/4': '¼',
                 '3/4': '¾', '1/5': '⅕', '2/5': '⅖', '3/5': '⅗',
                 '4/5': '⅘', '1/6': '⅙', '5/6': '⅚', '1/8': '⅛',
                 '3/8': '⅜', '5/8': '⅝', '7/8': '⅞'}
        if self.gift_type == "property":
            ts = (self.testator_share or '').strip()
            ot = (self.ownership_type or '').strip().lower()
            # Sole ownership — full property
            if ts in ('1/1', '1', '') and ot != 'joint':
                return "the property"
            # Joint ownership — testator's share only
            if ts and ts not in ('1/1', '1'):
                share_glyph = _FRAC.get(ts, ts)
                return f"all my {share_glyph} undivided shares in the property"
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
