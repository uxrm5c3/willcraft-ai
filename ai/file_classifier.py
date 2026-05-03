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
    # Property — five distinct kinds. ONLY property_title is evidence of
    # ownership and counts toward the will's gift list. The rest are
    # SUPPORTING context (tied to a property by address / lot) and get
    # clustered under the matching title in the chat.
    'property_title',  # Geran/Hakmilik/HSD/HSM/Pajakan Negeri/Strata Title — OWNERSHIP
    'property_spa',    # Sale & Purchase Agreement — contract, transfer pending
    'property_tax',    # Cukai Tanah / Cukai Pintu / quit rent / assessment
    'utility_bill',    # TNB electric / Air Selangor / SAJ / Indah Water / unifi — ties to a property address
    'bank_letter',     # Letter from bank confirming account / loan statement (NOT a statement itself)
    # Financial assets
    'bank_statement',  # bank statement / passbook
    'insurance',       # insurance policy
    'epf_kwsp',        # EPF / KWSP statement
    'vehicle',         # JPJ vehicle grant / road tax
    'will',            # an existing Last Will and Testament
    'other',
]


def classify_file(file_path: str) -> dict:
    """Return {kind, confidence, reason}. Falls back to 'other' on any error.

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

    try:
        msg = client.messages.create(
            model=CLAUDE_MODEL_CHEAP,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": """Classify this document into ONE category. Think about what the image actually IS and what it PROVES — not what's near it. A Cukai Tanah receipt is NOT a title even if it lists the same lot; an SPA is NOT a title even if it describes one.

- nric: MyKad IC card (front or back) or Malaysian passport
- property_title: LAND TITLE document — Geran, Hakmilik, HSD, HSM, Pajakan Negeri, Strata Title, individual or qualified title. PROVES ownership. Has lot number, registered owner name, issued by Pejabat Tanah dan Daerah (PTD). NOT the same as a tax receipt or contract.
- property_spa: Sale & Purchase Agreement (SPA / Perjanjian Jual Beli). A CONTRACT to buy — does NOT prove current ownership; transfer may still be pending. Usually has buyer/seller signatures, purchase price, completion date.
- property_tax: Cukai Tanah / Cukai Harta / Cukai Pintu / quit rent receipt / property assessment notice. Tax document — does NOT prove ownership. Issued by local council or PTD as a payment demand/receipt.
- utility_bill: TNB electricity, Air Selangor / SAJ / PBA water, Indah Water sewerage, unifi/Maxis/Celcom internet. Bill or invoice tied to a service address — useful as evidence the testator lives at / occupies that address but does NOT prove ownership.
- bank_letter: a LETTER from a bank (welcome letter, loan offer letter, account confirmation, mortgage letter). NOT a periodic statement showing transactions or balances — that's bank_statement.
- bank_statement: periodic bank statement listing transactions / balance, passbook, FD certificate, e-statement screenshot
- insurance: insurance policy, certificate, or schedule (life, takaful, etc.)
- epf_kwsp: KWSP / EPF statement, contribution slip, i-Akaun screenshot
- vehicle: JPJ vehicle registration card, road tax (cukai jalan), grant
- will: a signed Last Will and Testament (Wasiat Terakhir)
- other: anything that doesn't fit above (including property docs from outside Malaysia, e.g. Singapore strata, since this tool only drafts Malaysian wills)

Be precise: the `purpose` field should describe what THIS specific image proves (e.g. "Geran for Lot 207922 Mukim Plentong proving Koid Beng Sun's individual title", not just "land document").

If the document mentions a property address, lot number, or title number, copy it verbatim into `property_hint`. This is used to cluster multiple uploads (geran + SPA + cukai + electric bill) that all refer to the SAME property under one card. Leave `property_hint` empty for documents that aren't tied to a specific property (e.g. bank statement of a savings account, EPF, vehicle).

Return ONLY this JSON (no other text):
```json
{"kind": "<one above>", "confidence": "high|medium|low", "reason": "<one short sentence>", "purpose": "<what this image proves, max 25 words>", "property_hint": "<address or lot/title no, or empty>"}
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
    return result
