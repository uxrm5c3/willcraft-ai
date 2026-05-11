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
  # §10x.156 — non-subject addresses (chargor / purchaser / lawyer)
  # captured separately so the matcher doesn't mistake them for the
  # subject property's location.
  "_party_addresses": [{"role": str, "address": str}],

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
    # 🔥 §10x.156 — party addresses captured separately so the matcher
    # never confuses a chargor's residence with the subject property.
    '_party_addresses': [],
    # 🔥 §10x.161 — doc-type-specific sub-schemas. Empty dict when the
    # doc isn't of that kind. Matcher trusts these over flat fields.
    '_title_doc':  {},   # filled when kind == 'property_title'
    '_spa':        {},   # filled when kind == 'property_spa'
    '_charge':     {},   # filled when kind == 'loan_agreement'
    '_cukai':      {},   # filled when kind == 'property_tax'
    '_transfer':   {},   # filled when kind == 'property_transfer'
    # 🔥 §10x.161 — granularity stamped at extraction time so matcher
    # never has to re-derive it. One of: strata | sub_parcel | master |
    # unknown. Populated by services.property_granularity classifier
    # if sub-schemas don't declare granularity explicitly.
    '_doc_level':  'unknown',
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
  • property_tax        — Cukai Tanah / Cukai Pintu / quit rent /
                          assessment / STRATA MAINTENANCE BILL
                          (JMB / Badan Pengurusan Bersama / Management
                          Corporation Statement of Account; service
                          charge; sinking fund; water bill for a unit)
  • loan_agreement      — bank loan, mortgage, charge document
  • bank_statement      — REAL bank statement only: ISSUER must be a
                          recognised bank (Maybank, CIMB, Public Bank,
                          POSB, DBS, OCBC, UOB, RHB, Hong Leong, etc.)
                          AND show a deposit account number. A strata
                          "Statement of Account" from a JMB / Badan
                          Pengurusan Bersama / Management Corporation
                          is NOT a bank_statement — classify as
                          property_tax instead, even if the layout
                          says "Statement of Account".
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

  🔥 §10x.161 — DOC-TYPE-SPECIFIC SUB-SCHEMAS (typed extraction).
  When `kind` falls into one of the property categories below, also
  emit the matching sub-schema. These sub-schemas force you to put
  fields in their CORRECT slot (subject property vs party residence)
  and to label granularity at extraction time. The matcher downstream
  trusts these sub-schemas — they're the source of truth.

  • property_title (Geran / Hakmilik Strata / HSD / PTD):
      _title_doc: {
        is_strata: bool,           # has /M1?/N/MMM parcel sub-token
        master_title_no: str,      # parent Geran No. (always present)
        master_lot: str,           # parent NLC lot No.
        strata_block: str,         # e.g. "A", "B", "M1C" (strata only)
        strata_parcel_no: str,     # e.g. "B-05-11" (strata only)
        full_title: str,           # raw printed string e.g. "564662/M1C/30/710"
        mukim, daerah, negeri: str,
        registered_proprietors: [
          {name, ic, share_fraction}, ...
        ],
        granularity: 'strata' | 'sub_parcel'
      }

  • property_spa (Sale & Purchase Agreement):
      _spa: {
        subject_property: {       # from FIRST SCHEDULE / DESCRIPTION
          building_name, unit, parcel_no, master_lot,
          mukim, daerah, negeri, address_in_schedule
        },
        vendor: {name, ic, residence_address},
        purchaser: {name, ic, residence_address},  # ← NOT subject property
        sale_price, completion_date
      }

  • loan_agreement (Charge / Borang 16A / facility agreement):
      _charge: {
        borang_no: str,            # 16A / 16D / etc
        secured_property: {        # from SCHEDULE / SECURITY block
          strata_title_no, parcel_no, master_lot,
          mukim, daerah, negeri, address_in_schedule
        },
        chargor: {name, ic, residence_address},   # ← NOT subject property
        chargee_bank: str,
        loan_amount
      }

  • property_tax (Cukai Tanah / Cukai Pintu / assessment):
      _cukai: {
        assessment_account_no: str,
        master_parcel_lot: str,     # the parent NLC lot — NOT strata sub-parcel
        parcel_postal_address: str, # bill-to address; may be owner mailing
        registered_proprietors: [  # often DEVELOPER + buyers for pre-strata
          {name, ic}, ...
        ],
        granularity: 'master'       # Cukai is master-level by default
      }

  • property_transfer (Memorandum of Transfer / Borang 14A):
      _transfer: {
        property: {strata_title_no, parcel_no, master_lot, mukim, ...},
        transferor: {...},
        transferee: {...}
      }

  RULES for filling sub-schemas:
    1. Use the AUTHORITATIVE section of the doc for the property:
       SCHEDULE / SECURITY / CHARGED LAND / OBJECT OF CHARGE /
       DESCRIPTION OF PROPERTY / PERIHAL TANAH / HARTANAH.
    2. Party addresses go in their typed slots (vendor/purchaser/
       chargor residence). NEVER copy a party address into
       subject_property.address_in_schedule.
    3. If a field is not visible in the doc, set "" — do not invent.
    4. After filling sub-schemas, ALSO fill the top-level flat
       fields (property_address, title_number, lot_number, mukim,
       daerah, negeri, owner_name) from the sub-schemas' subject
       property — for backward compat with existing consumers.

  Property rules:
    title_type: one of geran|hakmilik|hsd|ptd|gm|other (lowercase)
    title_number: digits + slashes ONLY (no "VALUE:", no "(unreadable)").
                  E.g. "564662", "564662/M1C/30/710". Set "" if unreadable.
                  Malaysian titles are typically 4-7 digits; strata sub-tokens
                  follow as "/M1?/<floor>/<parcel>". A "Folio N" or "Vol N"
                  reference is a folio-location WITHIN a register, NOT a title
                  number — set title_number="" and put it in property_description.
    lot_number: digits ONLY.
    mukim/daerah/negeri: as printed on the title. Common: Plentong, Pulai,
                         Tebrau, Senai (mukim), Johor Bahru/Kulai (daerah),
                         Johor (negeri). NEVER guess if not visible.
    owner_name: the registered owner UPPERCASE. Multiple owners → join
                with " & ".
    owner_ic: 12-digit NRIC of the owner if printed.
    property_address: STREET ADDRESS of the SUBJECT PROPERTY only.
                      🔥 §10x.156 CRITICAL — On SPA / Charge (Borang 16A) /
                      Loan / Cukai docs, the address printed on the doc is
                      OFTEN a party's RESIDENCE, NOT the subject property:
                        • SPA: "Purchaser's address" = where the buyer lives
                          (often a different city, even Singapore). The
                          SUBJECT property is in the FIRST SCHEDULE /
                          DESCRIPTION OF PROPERTY block — read THAT.
                        • Charge / Loan: "Chargor's address" = where the
                          borrower lives. The SECURITY / CHARGED LAND /
                          SCHEDULE block describes the subject property.
                        • Lawyer attestation page: only the lawyer's office
                          address is visible — no subject property data.
                        • Cukai Tanah: bill-to address may be the owner's
                          mailing address, not the property.
                      RULE: Only fill property_address from a section that
                      explicitly identifies the SUBJECT PROPERTY (heading
                      contains: SCHEDULE / DESCRIPTION / SECURITY / CHARGED
                      LAND / PROPERTY ADDRESS / PERIHAL TANAH / HARTANAH /
                      OBJECT OF CHARGE). If you cannot find such a section,
                      leave property_address EMPTY and route any other
                      addresses you see into _party_addresses below.
                      Title docs (Geran/HSD) usually have NO street address
                      at all — leave empty per §10ha.
    property_description: any extra description (unit number, level,
                          building name, "Folio 5 reference", etc.)
    title_type_confidence: high if logo/header explicit; medium if
                           inferred from layout; low if guessed.

  🔥 §10x.156 — Party addresses (NEW field, capture but separate):
    _party_addresses: list of {role, address} for every non-subject address
                      visible on the doc. Roles you may see:
                        • "purchaser_residence" / "vendor_residence" (SPA)
                        • "chargor_residence" / "chargee_office" (Charge)
                        • "borrower_residence" / "lender_office" (Loan)
                        • "lawyer_office" / "attestor" (any legal doc)
                        • "owner_mailing" (Cukai bill-to address)
                      Format each entry as {"role": "...", "address": "..."}.
                      If a role is unclear but the address is clearly NOT
                      the subject property, use role="party_unknown".
                      LEAVE EMPTY ([]) if doc is a pure title (Geran/HSD) —
                      those don't have party addresses.

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

    # 🔥 §10x.161 — Phase 3: run the canonical granularity classifier
    # right at extraction time and persist `_doc_level` on the result.
    # The matcher downstream reads this tag without re-classifying.
    # Sub-schemas may already declare granularity explicitly (e.g.
    # _cukai.granularity = 'master'); honour that, else fall back to
    # the classifier.
    try:
        from services.property_granularity import classify_doc_level
        # Sub-schema explicit declaration wins
        explicit = (out.get('_title_doc', {}) or {}).get('granularity') \
                   or (out.get('_cukai',    {}) or {}).get('granularity') \
                   or ''
        if explicit in ('strata', 'sub_parcel', 'master', 'unknown'):
            out['_doc_level'] = explicit
        else:
            out['_doc_level'] = classify_doc_level(out, category=out.get('kind', ''))
    except Exception:
        out['_doc_level'] = 'unknown'

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
