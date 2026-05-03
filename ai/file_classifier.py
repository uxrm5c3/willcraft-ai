"""Classify an uploaded file so the chat pipeline knows which extractor to run.

This is a lightweight vision call — keep max_tokens small. The kinds are
chosen to match the existing folder categories under data/clients/{id}/documents/
plus a few extras the chat pipeline needs.
"""
import json
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_CHEAP
from ai.ocr import _make_content_block, _extract_json


KINDS = [
    'nric',            # Malaysian MyKad / passport
    # Property — six distinct kinds. ONLY property_title is evidence of
    # ownership and counts toward the will's gift list. The rest are
    # SUPPORTING context (tied to a property by address / lot) and get
    # clustered under the matching title in the chat.
    'property_title',    # Geran/Hakmilik/HSD/HSM/Pajakan Negeri/Strata Title — OWNERSHIP
    'property_spa',      # Sale & Purchase Agreement — contract, transfer pending
    'property_tax',      # Cukai Tanah / Cukai Pintu / quit rent / assessment
    'property_transfer', # Memorandum of Transfer (Borang 14A / Borang 16A) — NLC transfer form
    'utility_bill',      # TNB electric / Air Selangor / SAJ / Indah Water / unifi — ties to a property address
    'bank_letter',       # Letter from bank confirming account / loan statement (NOT a statement itself)
    # Financial assets
    'bank_statement',  # bank statement / passbook
    'insurance',       # insurance policy
    'epf_kwsp',        # EPF / KWSP statement
    'vehicle',         # JPJ vehicle grant / road tax
    'will',            # an existing Last Will and Testament
    'other',
]


def classify_file(file_path: str, sibling_hint: str = '') -> dict:
    """Return {kind, confidence, reason}. Falls back to 'other' on any error.

    sibling_hint: optional one-line description of other documents already
    classified in the same upload batch (e.g. "Other images in this batch:
    property_title — Geran for Lot 127082, Mukim Plentong"). Injected into
    the prompt so the classifier can use context from sibling pages of a
    multi-page document.

    Cost telemetry: if the caller wraps this in `cost_tracker.track_context(...)`,
    each Anthropic call is logged to ApiCallLog with client_id/will_id/user_id
    auto-attached. No-op outside a tracked context.
    """
    fallback = {"kind": "other", "confidence": "low", "reason": "Could not classify"}
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        content_block = _make_content_block(file_path)
    except Exception as e:
        return {**fallback, "reason": f"Could not open file: {e}"}

    sibling_section = ''
    if sibling_hint:
        sibling_section = (
            f"\n\n**BATCH CONTEXT — other images already classified in this upload:**\n"
            f"{sibling_hint}\n"
            f"Use this only as supporting context, not as a reason to override what you actually see. "
            f"A back-page or condition-sheet of a Geran belongs to the same batch and should be "
            f"classified as `property_title` even if it shows fewer identifying fields."
        )

    try:
        msg = client.messages.create(
            model=CLAUDE_MODEL_CHEAP,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": f"""Classify this document into ONE category. Think about what the image actually IS and what it PROVES — not what's near it. A Cukai Tanah receipt is NOT a title even if it lists the same lot; an SPA is NOT a title even if it describes one.{sibling_section}

- nric: MyKad IC card (front or back) or Malaysian passport
- property_title: LAND TITLE document — Geran, Hakmilik, HSD, HSM, Pajakan Negeri, Strata Title. PROVES registered ownership. Issued by Pejabat Tanah dan Daerah (PTD) with official seal. Header reads "HAKI MILIK", "GERAN", "HAKMILIK", or "INDIVIDUAL TITLE". Has lot number, land area, category of land use, and registered owner section ("TUAN PUNYA BERDAFTAR"). NOT a Memorandum of Transfer (which has transferor+transferee sections instead).
- property_spa: Sale & Purchase Agreement (SPA / Perjanjian Jual Beli). A CONTRACT to buy — does NOT prove current ownership; transfer may still be pending. Usually has buyer/seller signatures, purchase price, completion date.
- property_tax: Cukai Tanah / Cukai Harta / Cukai Pintu / quit rent receipt / property assessment notice. Tax document — does NOT prove ownership. Issued by local council or PTD as a payment demand/receipt.
- property_transfer: Memorandum of Transfer / Memorandum Pindahmilik — BORANG 14A (Peninsular Malaysia, National Land Code 1965) or BORANG 16A (Sabah, Sarawak). CRITICAL visual cues: the heading prominently says "MEMORANDUM PINDAHMILIK" or "MEMORANDUM OF TRANSFER". The form number "BORANG 14A", "FORM 14A", "BORANG 16A", or "FORM 16A" appears on the document. It has two separate party sections: PINDAHMILIK / TRANSFEROR (the person transferring away) and PENERIMA PINDAHMILIK / TRANSFEREE (the person receiving). Also shows BALASAN / CONSIDERATION (purchase price). Signed in front of a Solicitor or Commissioner for Oaths. Choose this over property_title if you see any of those heading/form-number cues, even if the image is blurry or partially visible.
- utility_bill: TNB electricity, Air Selangor / SAJ / PBA water, Indah Water sewerage, unifi/Maxis/Celcom internet. Bill or invoice tied to a service address — useful as evidence the testator lives at / occupies that address but does NOT prove ownership.
- bank_letter: a LETTER from a bank (welcome letter, loan offer letter, account confirmation, mortgage letter). NOT a periodic statement showing transactions or balances — that's bank_statement.
- bank_statement: periodic bank statement listing transactions / balance, passbook, FD certificate, e-statement screenshot
- insurance: insurance policy, certificate, or schedule (life, takaful, etc.)
- epf_kwsp: KWSP / EPF statement, contribution slip, i-Akaun screenshot
- vehicle: JPJ vehicle registration card, road tax (cukai jalan), grant
- will: a signed Last Will and Testament (Wasiat Terakhir)
- other: anything that doesn't fit above (including property docs from outside Malaysia, e.g. Singapore strata, since this tool only drafts Malaysian wills)

Be precise: the `purpose` field should describe what THIS specific image proves (e.g. "Geran for Lot 207922 Mukim Plentong proving Koid Beng Sun's individual title", not just "land document"). If the image is too blurry, dark, or low-quality to read, say so explicitly in `purpose` (e.g. "Image too blurry to identify — appears to be a property-related form but content unreadable").

If the document mentions a property address, lot number, or title number, copy it verbatim into `property_hint`. This is used to cluster multiple uploads (geran + SPA + cukai + electric bill) that all refer to the SAME property under one card. Leave `property_hint` empty for documents that aren't tied to a specific property (e.g. bank statement of a savings account, EPF, vehicle).

Set `will_relevant` to true if this document is relevant to a Malaysian will (i.e. it relates to an asset the testator may own — property, bank account, vehicle, EPF, insurance, identity). Set it to false for things like receipts, menus, unrelated personal photos, promotional material, or anything with no clear asset connection.

Return ONLY this JSON (no other text):
```json
{"kind": "<one above>", "confidence": "high|medium|low", "reason": "<one short sentence>", "purpose": "<what this image proves, max 25 words>", "property_hint": "<address or lot/title no, or empty>", "will_relevant": true}
```"""}
                ]
            }]
        )
    except Exception as e:
        return {**fallback, "reason": f"API error: {e}"}

    try:
        from ai.cost_tracker import log_usage
        log_usage(msg, call_site='ai.file_classifier.classify_file')
    except Exception:
        pass

    text = (msg.content[0].text or "").strip() if msg.content else ""
    js = _extract_json(text)
    if not js:
        return fallback
    try:
        result = json.loads(js)
    except json.JSONDecodeError:
        return fallback
    if result.get('kind') not in KINDS:
        result['kind'] = 'other'
    result.setdefault('confidence', 'low')
    result.setdefault('reason', '')
    result.setdefault('will_relevant', True)  # default to relevant; only low-conf 'other' is suspect
    # If classified as 'other' with low confidence, treat as potentially irrelevant
    if result['kind'] == 'other' and result['confidence'] == 'low':
        result.setdefault('will_relevant', False)
    return result
