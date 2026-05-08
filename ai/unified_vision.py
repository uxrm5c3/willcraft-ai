"""§10x.73 — One-shot vision: ONE Sonnet call extracts EVERY field.

Replaces the 4-5 sequential vision passes (classify, extract_nric_data,
extract_property_data, extract_asset_document, _vision_classify_fallback)
with a single multi-category extractor.

Why
---
Previous pipeline per image:
  1. classify_file (vision)        — what kind of doc?
  2. extract_nric_data             — IC fields IF nric
  3. extract_property_data         — property fields IF property
  4. extract_asset_document        — bank/insurance fields IF asset
  5. reocr_critical_field          — re-OCR critical fields after extract

Each is its own Sonnet vision call (~$0.011-$0.015). 5 calls per image =
$0.05-$0.075. With 20 images per will → $1-$1.50/will just on vision.

This module collapses 5 calls → 1: a single classify-AND-extract prompt
that returns category + every category's fields in one JSON. ~$0.015 per
image regardless of category. Net cost drop: 60-80%.

Hard rules
----------
1. Result is cached via §10x.67 cached_vision (DB-backed, sha256 keyed).
   Same image is never extracted twice.
2. Kill switch §10x.65 honoured — returns sentinel dict if disabled.
3. log_usage §10x.70 fires synchronously after the API call.
4. Returns a SUPERSET of every old function's keys, so callers can
   destructure exactly what they need.
5. Feature-flagged by env UNIFIED_VISION=1 — when off, the legacy
   per-category functions still work unchanged. Old code is fallback.

Output schema
-------------
{
  "kind": "nric | property_title | property_spa | property_tax |
           loan_agreement | bank_statement | insurance | vehicle |
           will | death_certificate | unrelated | other",
  "confidence": "high | medium | low",
  "manual_review": bool,
  "reason": str,

  # NRIC (when kind=nric or document carries IC info)
  "full_name": str, "nric_number": str, "date_of_birth": str,
  "address": str, "gender": str, "nationality": str, "passport_expiry": str,

  # Property (when kind starts with "property_")
  "property_address": str, "title_type": str, "lot_number": str,
  "title_number": str, "mukim": str, "daerah": str, "negeri": str,
  "owner_name": str, "owner_ic": str, "property_description": str,
  "title_type_confidence": "high | medium | low",

  # Bank / financial asset
  "bank_name": str, "account_number": str, "currency": str,
  "account_type": str,

  # Insurance
  "insurer": str, "policy_number": str, "policyholder_name": str,

  # Vehicle
  "registration_number": str, "make": str, "model": str,

  # Will / death cert
  "testator_name": str, "deceased_name": str, "date_of_death": str,
}
"""
import base64
import json
import os
import re
import logging
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_FAST

log = logging.getLogger(__name__)


# Env flag — when 1, callers SHOULD route through extract_all().
def is_enabled() -> bool:
    return os.environ.get('UNIFIED_VISION', '').strip() == '1'


_BLANK_RESULT: dict = {
    'kind': 'other', 'confidence': 'low', 'manual_review': False,
    'reason': '',
    'full_name': '', 'nric_number': '', 'date_of_birth': '',
    'address': '', 'gender': '', 'nationality': '',
    'passport_expiry': '',
    'property_address': '', 'title_type': '', 'lot_number': '',
    'title_number': '', 'mukim': '', 'daerah': '', 'negeri': '',
    'owner_name': '', 'owner_ic': '', 'property_description': '',
    'title_type_confidence': 'low',
    'bank_name': '', 'account_number': '', 'currency': '',
    'account_type': '',
    'insurer': '', 'policy_number': '', 'policyholder_name': '',
    'registration_number': '', 'make': '', 'model': '',
    'testator_name': '', 'deceased_name': '', 'date_of_death': '',
}


def _media_type_for(file_path: str) -> str:
    ext = file_path.rsplit('.', 1)[-1].lower()
    if ext == 'pdf':
        return 'application/pdf'
    return {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'png': 'image/png', 'gif': 'image/gif',
        'webp': 'image/webp',
    }.get(ext, 'image/jpeg')


_PROMPT = """You are a Malaysian legal-document classifier and extractor.
ONE pass — read the image carefully and emit EVERY field that applies, no
matter how many categories overlap (e.g. an SPA mentions both property AND
buyer/seller IC numbers).

STEP 1 — IDENTIFY the document type. Pick exactly one `kind`:
  • nric                — MyKad / IC card / Malaysian passport / foreign ID
  • property_title      — Geran / Hakmilik / HSD / PTD strata or landed title
  • property_spa        — Sale & Purchase Agreement, transfer / lease docs
  • property_tax        — Cukai Tanah / Cukai Pintu / quit rent / assessment
  • loan_agreement      — bank loan, mortgage, charge document
  • bank_statement      — bank statement, FD certificate, savings book
  • insurance           — insurance policy, schedule, premium notice
  • vehicle             — vehicle ownership card / Geran kereta / V5
  • will                — last will and testament document
  • death_certificate   — death cert, JPN
  • unrelated           — receipts, photos with no probate value
  • other               — looks document-like but doesn't fit the above

STEP 2 — EXTRACT every applicable field. Fields you cannot read → empty
string. Do not invent or guess. Use the exact RULES per category.

  NRIC rules:
    full_name: UPPERCASE exactly as printed; include BIN/BINTI/A/L/A/P
    nric_number: 12 digits with dashes YYMMDD-SS-NNNN (validate month
                 01-12, day 01-31; common misreads 0/8, 1/7, 3/8, 4/1)
    date_of_birth: DD-MM-YYYY (derive from IC's first 6 digits if NRIC;
                   00-30 → 2000s, 31-99 → 1900s)
    address: back of MyKad only; lines separated by \\n
    gender: NRIC last digit odd=Male even=Female; passport: read M/F field
    nationality: usually Malaysian; for passports read the cover

  Property rules:
    title_type: one of geran|hakmilik|hsd|ptd|gm|other (lowercase)
    title_number: digits + slashes ONLY (no "VALUE:", no "(unreadable)").
                  E.g. "564662", "564662/M1C/30/710". Set "" if unreadable.
    lot_number: digits ONLY.
    mukim/daerah/negeri: as printed on the title. Common: Plentong, Pulai,
                         Tebrau, Senai (mukim), Johor Bahru/Kulai (daerah),
                         Johor (negeri). NEVER guess if not visible.
    owner_name: the registered owner UPPERCASE. Multiple owners → join
                with " & ".
    owner_ic: 12-digit NRIC of the owner if printed.
    property_address: STREET ADDRESS — usually NOT on title docs (§10ha).
                      Only fill if the doc actually shows street/postcode.
                      DO NOT hallucinate based on mukim.
    property_description: any extra description (unit number, level, etc.)
    title_type_confidence: high if logo/header explicit; medium if
                           inferred from layout; low if guessed.

  Bank rules:
    bank_name: standardised bank label e.g. "Maybank", "POSB", "CIMB"
    account_number: digits only, with dashes if printed (e.g. 030-25917-3)
    currency: ISO code e.g. SGD, MYR, USD; default MYR if Malaysian bank
    account_type: savings | current | fixed_deposit | unit_trust | epf

  Insurance rules:
    insurer: company name e.g. "AIA", "Allianz Malaysia"
    policy_number: as printed
    policyholder_name: name on the schedule

  Vehicle rules:
    registration_number: e.g. "WLM 8888"
    make / model: e.g. Toyota / Camry

  Will / death cert:
    testator_name / deceased_name as printed
    date_of_death: DD-MM-YYYY

STEP 3 — Set:
  • confidence: "high" if every key field is unambiguous; "medium" if
                one or two fields uncertain; "low" otherwise.
  • manual_review: true if the image is too blurred / partial / non-doc
                   to extract anything meaningful.
  • reason: 1-line description of what you saw (≤80 chars).

STEP 4 — OUTPUT one JSON object containing EVERY key from the schema
above (use empty strings for fields that don't apply to this kind).
Output JSON only — no preamble, no markdown fences."""


def extract_all(file_path: str, *, call_site: Optional[str] = None) -> dict:
    """Single-pass classify-AND-extract.

    Returns the FULL schema dict (every key, empty for inapplicable
    fields). Cached via §10x.67 — second call on the same file returns
    instantly.

    Honours kill switch §10x.65: returns the sentinel
    {'_disabled_by_kill_switch': True, 'kind': 'other', ...} when
    DISABLE_VISION_CALLS=1.
    """
    if os.environ.get('DISABLE_VISION_CALLS', '').strip() == '1':
        out = dict(_BLANK_RESULT)
        out['_disabled_by_kill_switch'] = True
        out['reason'] = 'kill switch active'
        return out

    if not file_path or not os.path.isfile(file_path):
        out = dict(_BLANK_RESULT)
        out['reason'] = 'file not found'
        return out

    try:
        from services.vision_cache import cached_vision
        return cached_vision(
            file_path=file_path,
            call_kind='unified_v1',
            fn=lambda: _extract_inner(file_path, call_site=call_site),
        )
    except Exception as e:
        log.warning(f"unified_vision cache layer error, falling back direct: {e}")
        return _extract_inner(file_path, call_site=call_site)


def _extract_inner(file_path: str, *, call_site: Optional[str] = None) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    with open(file_path, 'rb') as f:
        data_b64 = base64.standard_b64encode(f.read()).decode('utf-8')
    media_type = _media_type_for(file_path)
    block_type = 'document' if media_type == 'application/pdf' else 'image'
    content_block = {
        'type': block_type,
        'source': {'type': 'base64', 'media_type': media_type, 'data': data_b64},
    }

    try:
        msg = client.messages.create(
            model=CLAUDE_MODEL_FAST,   # Haiku 4.5 — cheaper than Sonnet
            max_tokens=2048,
            messages=[{
                'role': 'user',
                'content': [content_block, {'type': 'text', 'text': _PROMPT}],
            }],
        )
    except Exception as e:
        log.warning(f"unified_vision API call failed: {e}")
        out = dict(_BLANK_RESULT)
        out['reason'] = f'api_error: {type(e).__name__}'
        return out

    # §10x.70 — log every Anthropic call.
    try:
        from ai.cost_tracker import log_usage
        log_usage(msg, call_site=call_site or 'ai.unified_vision.extract_all')
    except Exception:
        pass

    text = (msg.content[0].text or '').strip() if msg.content else ''
    parsed = _parse_json(text)
    if parsed is None:
        out = dict(_BLANK_RESULT)
        out['reason'] = 'json_parse_failed'
        out['_raw'] = text[:500]
        return out

    # Merge into the canonical schema — guarantees every key exists,
    # so callers can do `.get('lot_number')` without checks.
    out = dict(_BLANK_RESULT)
    for k, v in parsed.items():
        if k in out:
            out[k] = v
        else:
            # extra keys preserved with underscore prefix for debugging
            out[f'_extra_{k}'] = v
    return out


def _parse_json(text: str):
    """Tolerant JSON extraction — handles markdown fences and stray text."""
    if not text:
        return None
    # Strip ```json fences
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else text[3:]
    if text.endswith('```'):
        text = text[:-3].rstrip()
    text = text.strip()
    # Find first { and last } if there's preamble
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        text = m.group(0)
    try:
        return json.loads(text)
    except Exception:
        return None


# ── Compat shims — let legacy callers route to unified pass ──────────────────
#
# These are NOT used unless UNIFIED_VISION=1. When the flag is on, the
# legacy functions in ai.file_classifier / ai.ocr / ai.property_extractor
# can call into these shims instead of making their own vision calls,
# saving 4-5× on cost per image.

def as_classify_result(unified: dict) -> dict:
    """Adapt unified output to ai.file_classifier.classify_file's shape."""
    return {
        'kind': unified.get('kind') or 'other',
        'confidence': unified.get('confidence') or 'low',
        'reason': unified.get('reason') or '',
        'manual_review': bool(unified.get('manual_review')),
        'will_relevant': True,
        'custom_type': '', 'person_name': unified.get('full_name') or '',
        'purpose': '',
        'property_hint': unified.get('property_address') or '',
        'lot_number': unified.get('lot_number') or '',
        'title_number': unified.get('title_number') or '',
        'property_address': unified.get('property_address') or '',
        'bank_name': unified.get('bank_name') or '',
        'mukim': unified.get('mukim') or '',
        'daerah': unified.get('daerah') or '',
        'negeri': unified.get('negeri') or '',
        'owner_name': unified.get('owner_name') or '',
        'ic_number': unified.get('owner_ic') or unified.get('nric_number') or '',
        'name_match': None, 'ic_match': None,
    }


def as_nric_result(unified: dict) -> dict:
    """Adapt unified output to ai.ocr.extract_nric_data's shape."""
    return {
        'doc_type': 'nric' if unified.get('kind') == 'nric' else 'other',
        'full_name': unified.get('full_name') or '',
        'nric_number': unified.get('nric_number') or '',
        'date_of_birth': unified.get('date_of_birth') or '',
        'address': unified.get('address') or '',
        'gender': unified.get('gender') or '',
        'nationality': unified.get('nationality') or 'Malaysian',
        'passport_expiry': unified.get('passport_expiry') or '',
    }


def as_property_result(unified: dict) -> dict:
    """Adapt unified output to ai.property_extractor.extract_property_data's shape."""
    return {
        'property_address': unified.get('property_address') or '',
        'title_type': unified.get('title_type') or '',
        'lot_number': unified.get('lot_number') or '',
        'title_number': unified.get('title_number') or '',
        'bandar_pekan': '',
        'mukim': unified.get('mukim') or '',
        'daerah': unified.get('daerah') or '',
        'negeri': unified.get('negeri') or '',
        'property_description': unified.get('property_description') or '',
        'owner_name': unified.get('owner_name') or '',
        'title_type_confidence': unified.get('title_type_confidence') or 'low',
    }


def as_asset_result(unified: dict, asset_type: str) -> dict:
    """Adapt unified output to ai.ocr.extract_asset_document's shape."""
    if asset_type == 'bank':
        return {
            'institution': unified.get('bank_name') or '',
            'account_number': unified.get('account_number') or '',
            'currency': unified.get('currency') or 'MYR',
            'account_type': unified.get('account_type') or '',
            'holder_name': unified.get('full_name') or '',
        }
    if asset_type == 'insurance':
        return {
            'institution': unified.get('insurer') or '',
            'policy_number': unified.get('policy_number') or '',
            'holder_name': unified.get('policyholder_name') or '',
        }
    if asset_type == 'vehicle':
        return {
            'registration_number': unified.get('registration_number') or '',
            'make': unified.get('make') or '',
            'model': unified.get('model') or '',
        }
    return {}
