"""Web-search property clues — given an address from the AI Summary,
search the web for property-type clues (type, tenure, mukim, building,
postcode area) and use them to filter image candidates.

ENFORCES CLAUDE.md §10hf — "WEB-SEARCH THE ADDRESS: GET PROPERTY-TYPE
CLUES." This step is MANDATORY before any "no image matches" conclusion.
You have the address → you MUST search.

Anti-assumption pattern (same as services/geo_resolver.py):
  - Every clue carries a source URL citation.
  - The system prompt forbids training-memory answers.
  - Empty/uncited results return None — caller must NOT guess.
  - There is NO `else: return some_default` branch.

Public API:
  search_property_clues(address: str, claude_client) -> Optional[PropertyClues]
  filter_images_by_clues(images: list[dict], clues: PropertyClues) -> list[dict]
  is_compatible(image_extracted: dict, clues: PropertyClues) -> tuple[bool, str]
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
import json
import re


# ─────────────────────────────────────────────────────────────────────────────
#  THE CLUES STRUCTURE — every field carries a source citation set
# ─────────────────────────────────────────────────────────────────────────────

PROPERTY_TYPES = (
    "landed_residential",   # terrace / superlink / bungalow / semi-d
    "apartment_condo",      # strata residential
    "shoplot",              # shop-office / shop-house
    "factory",              # industrial
    "agricultural",         # estate / kebun / sawah
    "mixed_use",            # SOFO, SOHO
    "unknown",              # web search couldn't determine
)

TENURES = ("freehold", "leasehold", "unknown")


@dataclass(frozen=True)
class PropertyClues:
    """Web-search result for an address. Every field is either non-empty
    with a citation, or empty/unknown. Never invented from memory."""
    address: str
    type: str                            # one of PROPERTY_TYPES
    tenure: str                          # one of TENURES
    locality: str                        # township / neighbourhood
    mukim: str                           # NLC mukim, if findable
    daerah: str
    negeri: str
    building_name: str                   # for strata only
    postcode: str
    sources: Tuple[str, ...] = ()        # URLs actually consulted

    def __post_init__(self):
        if self.type not in PROPERTY_TYPES:
            raise ValueError(f"type must be one of {PROPERTY_TYPES}, got {self.type!r}")
        if self.tenure not in TENURES:
            raise ValueError(f"tenure must be one of {TENURES}, got {self.tenure!r}")
        # Sources are mandatory if any clue is non-default
        has_clues = any([self.type != "unknown", self.tenure != "unknown",
                         self.locality, self.mukim, self.building_name])
        if has_clues and not self.sources:
            raise ValueError(
                "PropertyClues with non-default values MUST cite sources. "
                "See CLAUDE.md §10hf — no memory-based clues."
            )
        # Each source must be an http(s) URL
        for s in self.sources:
            if not s.startswith(("http://", "https://")):
                raise ValueError(
                    f"Source {s!r} is not a URL. Citations must be web URLs."
                )

    def as_summary_lines(self) -> List[str]:
        """Render as a list of evidence lines for the property card."""
        out = []
        if self.type and self.type != "unknown":
            out.append(f"🏷  Type: {self.type.replace('_', ' ')}")
        if self.tenure and self.tenure != "unknown":
            out.append(f"📜 Tenure: {self.tenure}")
        if self.mukim:
            out.append(f"🏘  Mukim: {self.mukim}, Daerah {self.daerah}, {self.negeri}")
        if self.building_name:
            out.append(f"🏢 Building: {self.building_name}")
        if self.sources:
            out.append("🔗 Sources: " + ", ".join(self.sources[:3]))
        return out


# ─────────────────────────────────────────────────────────────────────────────
#  THE WEB-SEARCH PROMPT — hostile to guessing
# ─────────────────────────────────────────────────────────────────────────────

PROPERTY_CLUES_SYSTEM_PROMPT = """\
You are a Malaysian property research tool. Given an address, search the
web and return STRUCTURED clues about the property.

The fields you must extract:
  - type: one of [landed_residential, apartment_condo, shoplot, factory,
                  agricultural, mixed_use, unknown]
    "landed_residential" = terrace, superlink, bungalow, semi-d
    "apartment_condo"    = condo, apartment, serviced apartment, SOHO
    "shoplot"            = shop-office, shop-house, retail unit
    "factory"            = industrial / manufacturing
    "agricultural"       = estate, kebun, sawah, oil palm
    "unknown"            = cannot determine from search results
  - tenure: one of [freehold, leasehold, unknown]
  - locality: the township / neighbourhood name (e.g. "Taman Laguna",
              "Bandar Medini Iskandar")
  - mukim: official NLC mukim if findable (e.g. "Pulai", "Plentong",
           "Tebrau"). Empty if not findable from search results.
  - daerah: official daerah (e.g. "Johor Bahru")
  - negeri: state (e.g. "Johor")
  - building_name: only for apartment_condo / shoplot — the development
                   name (e.g. "Paradiso Nuova", "Pangsapuri Tepian Bayu")
  - postcode: the postcode from the address
  - sources: list of URLs you ACTUALLY visited via web_search this turn

HARD RULES — VIOLATING ANY OF THESE INVALIDATES YOUR ANSWER:

1. DO NOT use general knowledge or training-memory. Every non-default
   field must be supported by a search result you saw in this turn.
2. If you cannot find supporting search results, return type="unknown",
   tenure="unknown", and leave optional fields empty. Do NOT guess.
3. If sources disagree, prefer "unknown" over picking a side.
4. If the address doesn't appear in any search result, return:
   {"address_not_found": true, "sources_consulted": [...]}
5. Return JSON only — no prose, no markdown fences.

JSON output schema (when found):
{
  "type": "landed_residential",
  "tenure": "freehold",
  "locality": "Taman Laguna",
  "mukim": "Pulai",
  "daerah": "Johor Bahru",
  "negeri": "Johor",
  "building_name": "",
  "postcode": "81200",
  "sources": ["https://...", "https://..."]
}

Or when not found:
{"address_not_found": true, "sources_consulted": ["https://..."]}

A confident-sounding wrong answer is worse than "unknown".
"""


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC API — search_property_clues
# ─────────────────────────────────────────────────────────────────────────────

def search_property_clues(
    address: str,
    claude_client,
    *,
    model: str = "claude-haiku-4-5",
    max_tokens: int = 800,
) -> Optional[PropertyClues]:
    """Web-search the given address and return PropertyClues, or None
    if the search couldn't find it.

    NEVER falls back to memory. NEVER returns a partially-fabricated
    PropertyClues. Either the search yielded citable evidence, or None.

    Args:
      address: e.g. "10 Jalan Sri Laguna 1/7, Taman Laguna, 81200 JB"
      claude_client: an Anthropic client instance.

    Returns: PropertyClues (with at least one URL in sources) or None.
    """
    if not address or len(address.strip()) < 5:
        return None

    try:
        # 🔥 §10x.60 — prompt caching. Anthropic charges 10% of normal
        # input price for cached tokens (5min TTL, ≥1024 token min).
        # PROPERTY_CLUES_SYSTEM_PROMPT is ~1300 tokens and called for
        # every property (5 props × N polls = many repeats within 5min).
        msg = claude_client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{
                'type': 'text',
                'text': PROPERTY_CLUES_SYSTEM_PROMPT,
                'cache_control': {'type': 'ephemeral'},
            }],
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": f"Address: {address.strip()}"}],
        )
        # 🔥 §10x.70 — was MISSING, leaked $$ untracked. Added now.
        try:
            from ai.cost_tracker import log_usage
            log_usage(msg, call_site='services.web_property_clues.search')
        except Exception:
            pass
    except Exception:
        return None

    # Extract the JSON from the final text block
    try:
        blocks = [b for b in msg.content if getattr(b, "type", "") == "text"]
        text_out = blocks[-1].text if blocks else ""
        data = json.loads(_extract_json(text_out))
    except Exception:
        return None

    if data.get("address_not_found"):
        return None

    # Build PropertyClues — its __post_init__ rejects un-cited clues.
    try:
        return PropertyClues(
            address=address.strip(),
            type=data.get("type") or "unknown",
            tenure=data.get("tenure") or "unknown",
            locality=(data.get("locality") or "").strip(),
            mukim=(data.get("mukim") or "").strip(),
            daerah=(data.get("daerah") or "").strip(),
            negeri=(data.get("negeri") or "").strip(),
            building_name=(data.get("building_name") or "").strip(),
            postcode=(data.get("postcode") or "").strip(),
            sources=tuple(s for s in (data.get("sources") or [])
                          if isinstance(s, str) and s.startswith("http")),
        )
    except ValueError:
        # __post_init__ rejected the result (e.g. clues without sources).
        # That's the safety net — return None and force ASK USER path.
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC API — filter_images_by_clues + is_compatible
# ─────────────────────────────────────────────────────────────────────────────

def is_compatible(image_extracted: Dict[str, Any],
                  clues: PropertyClues) -> Tuple[bool, str]:
    """Check whether this image's extracted data is CONSISTENT with the
    web-search clues for an AI-Summary property.

    Returns (compatible, reason). `compatible=False` means: this image
    cannot be the title doc for the property described by `clues`.

    The check is conservative — when in doubt, returns True (lets the
    image stay in contention). The hard-NO cases:
      - clues say landed_residential, image is clearly strata
      - clues say apartment_condo, image is clearly landed
      - clues say a specific mukim, image extracted a different mukim
      - clues say freehold, image extracted leasehold (or vice versa)
    """
    ex = image_extracted or {}

    # ── Type compatibility ───────────────────────────────────────────────
    img_strata = _is_strata(ex)
    if clues.type == "landed_residential" and img_strata:
        return (False, f"clues say landed but image is strata "
                       f"(title='{ex.get('title_number','')}')")
    if clues.type == "apartment_condo" and not img_strata:
        # Allow if image has no clear landed signal — strata might
        # be missing the "/Block/Storey" encoding due to OCR loss.
        if _is_clearly_landed(ex):
            return (False, "clues say apartment but image is clearly landed")
    if clues.type == "shoplot":
        # Shoplots can be landed or strata-titled. No hard exclusion.
        pass
    if clues.type == "factory" and img_strata:
        return (False, "clues say factory but image is strata residential")

    # ── Mukim compatibility ──────────────────────────────────────────────
    img_mukim = _norm(ex.get("mukim") or "")
    clue_mukim = _norm(clues.mukim or "")
    if img_mukim and clue_mukim and img_mukim != clue_mukim:
        # Yellow flag — different mukim. Could be OCR garbage on image side.
        if not _looks_like_ocr_noise(ex.get("mukim") or ""):
            return (False, f"mukim mismatch: image says '{img_mukim}', "
                           f"clues say '{clue_mukim}'")

    # ── Tenure compatibility ─────────────────────────────────────────────
    img_tenure = _detect_tenure(ex)
    if (clues.tenure in ("freehold", "leasehold")
            and img_tenure in ("freehold", "leasehold")
            and img_tenure != clues.tenure):
        return (False, f"tenure mismatch: image '{img_tenure}', clues '{clues.tenure}'")

    # ── Building-name compatibility (strata only) ────────────────────────
    if clues.building_name and img_strata:
        img_desc = (ex.get("property_description") or "").upper()
        img_addr = (ex.get("property_address") or "").upper()
        bld = clues.building_name.upper()
        if bld not in img_desc and bld not in img_addr:
            # Don't reject outright — building name might be stripped by OCR.
            # But mark as low signal.
            return (True, f"building '{clues.building_name}' not found in "
                          f"image but allowing (OCR may have dropped it)")

    return (True, "compatible")


def filter_images_by_clues(
    images: List[Dict[str, Any]],
    clues: PropertyClues,
) -> List[Dict[str, Any]]:
    """Drop images that are clearly incompatible with `clues`. Returns
    the surviving candidates that the timing/two-hint matcher should
    consider for this AI-Summary property."""
    if clues is None:
        return images
    out = []
    for img in images:
        ex = img.get("extracted") or {}
        ok, _why = is_compatible(ex, clues)
        if ok:
            out.append(img)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers — strata detection mirrors services/gift_walker._is_strata
#  (kept here to avoid import cycle; keep in sync with §10hd)
# ─────────────────────────────────────────────────────────────────────────────

_STRATA_TITLE_TYPE_TOKENS = ("STRATA", "HAKMILIK STRATA",
                              "GERAN MUKIM STRATA", "GMS", "STRATA TITLE")
_STRATA_DESCRIPTION_TOKENS = ("LEVEL ", "STOREY", "TINGKAT", "PARCEL NO",
                               "PARCEL ", "PETAK", "BLOCK ", "BLOK ",
                               "BUILDING M", "BUILT UP AREA")
_LANDED_DESCRIPTION_TOKENS = ("TERRACE", "BUNGALOW", "SEMI-D", "SEMI DETACHED",
                               "SUPERLINK", "DOUBLE STOREY", "SINGLE STOREY",
                               "DETACHED HOUSE", "BANGLO")


def _is_strata(extracted: Dict[str, Any]) -> bool:
    if not extracted:
        return False
    tt = (extracted.get("title_type") or "").upper()
    if any(tok in tt for tok in _STRATA_TITLE_TYPE_TOKENS):
        return True
    tn = (extracted.get("title_number") or "").strip()
    if "/" in tn and any(c.isdigit() for c in tn):
        return True
    desc = (extracted.get("property_description") or "").upper()
    if any(tok in desc for tok in _STRATA_DESCRIPTION_TOKENS):
        return True
    if (extracted.get("document_type") or "").lower() == "strata_title":
        return True
    return False


def _is_clearly_landed(extracted: Dict[str, Any]) -> bool:
    desc = (extracted.get("property_description") or "").upper()
    return any(tok in desc for tok in _LANDED_DESCRIPTION_TOKENS)


def _detect_tenure(extracted: Dict[str, Any]) -> str:
    blob = " ".join(str(v).upper() for v in extracted.values() if v).lower()
    if any(t in blob for t in ("freehold", "pegangan bebas", "selama-lamanya")):
        return "freehold"
    if any(t in blob for t in ("leasehold", "pajakan", "tempoh tahun")):
        return "leasehold"
    return "unknown"


_OCR_NOISE_TOKENS = ("UNREADABLE", "CANNOT READ", "NOT VISIBLE", "VALUE:",
                      "(BLURRED)", "(UNREADABLE)")


def _looks_like_ocr_noise(s: str) -> bool:
    up = (s or "").upper()
    return any(t in up for t in _OCR_NOISE_TOKENS)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).upper()


def _extract_json(text: str) -> str:
    """Pull the first {...} block out of a string."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return m.group(0) if m else "{}"
