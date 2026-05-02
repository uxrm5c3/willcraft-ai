"""Classify an uploaded file so the chat pipeline knows which extractor to run.

This is a lightweight vision call — keep max_tokens small. The kinds are
chosen to match the existing folder categories under data/clients/{id}/documents/
plus a few extras the chat pipeline needs.
"""
import json
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_FAST
from ai.ocr import _make_content_block, _extract_json


KINDS = [
    'nric',            # Malaysian MyKad / passport
    'property_title',  # Geran, Hakmilik, HSD, HSM, Pajakan Negeri
    'property_tax',    # Cukai Harta / Cukai Pintu
    'bank_statement',  # bank statement / passbook
    'insurance',       # insurance policy
    'epf_kwsp',        # EPF / KWSP statement
    'vehicle',         # JPJ vehicle grant / road tax
    'will',            # an existing Last Will and Testament
    'other',
]


def classify_file(file_path: str) -> dict:
    """Return {kind, confidence, reason}. Falls back to 'other' on any error."""
    fallback = {"kind": "other", "confidence": "low", "reason": "Could not classify"}
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        content_block = _make_content_block(file_path)
    except Exception as e:
        return {**fallback, "reason": f"Could not open file: {e}"}

    try:
        msg = client.messages.create(
            model=CLAUDE_MODEL_FAST,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": """Classify this Malaysian document into ONE category:

- nric: MyKad IC card (front or back) or Malaysian passport
- property_title: land title — Geran, Hakmilik, HSD, HSM, Pajakan Negeri, strata title
- property_tax: Cukai Harta / Cukai Pintu / property assessment / quit rent notice
- bank_statement: bank statement, passbook, account opening doc, FD certificate
- insurance: insurance policy, certificate, or schedule (life, takaful, etc.)
- epf_kwsp: KWSP / EPF statement, contribution slip, i-Akaun screenshot
- vehicle: JPJ vehicle registration card, road tax (cukai jalan), grant
- will: a signed Last Will and Testament (Wasiat Terakhir)
- other: anything that doesn't fit above

Return ONLY this JSON (no other text):
```json
{"kind": "<one above>", "confidence": "high|medium|low", "reason": "<one short sentence>"}
```"""}
                ]
            }]
        )
    except Exception as e:
        return {**fallback, "reason": f"API error: {e}"}

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
