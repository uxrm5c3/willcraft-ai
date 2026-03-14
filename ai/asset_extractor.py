"""Financial document extraction using Claude Vision API."""
import base64
import json
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_FAST


def extract_asset_data(file_path: str) -> dict:
    """Extract asset data from bank/investment statements.

    Returns dict with keys: assets (list), account_holder_name, account_holder_nric
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    with open(file_path, 'rb') as f:
        file_data = base64.standard_b64encode(f.read()).decode('utf-8')

    ext = file_path.rsplit('.', 1)[-1].lower()
    if ext == 'pdf':
        media_type = 'application/pdf'
    else:
        media_type = {
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'png': 'image/png', 'gif': 'image/gif',
        }.get(ext, 'image/jpeg')

    content_block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": file_data,
        }
    }
    if ext == 'pdf':
        content_block = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": file_data,
            }
        }

    message = client.messages.create(
        model=CLAUDE_MODEL_FAST,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                content_block,
                {
                    "type": "text",
                    "text": """Extract financial asset information from this bank statement, investment statement, or financial document.

Return ONLY a JSON object with these fields:
{
    "account_holder_name": "Account holder's full name",
    "account_holder_nric": "NRIC or ID number if visible",
    "assets": [
        {
            "type": "savings/current/fixed_deposit/investment/unit_trust/shares/insurance/epf/other",
            "institution": "Bank/institution name",
            "account_number": "Account/policy number",
            "description": "Brief description including any relevant details"
        }
    ]
}

For Malaysian financial documents, look for:
- Bank name and branch
- Account type and number
- Investment portfolio details
- Unit trust fund names
- Insurance policy numbers
- EPF/KWSP account numbers

Return ONLY the JSON, no explanation."""
                }
            ]
        }]
    )

    response_text = message.content[0].text.strip()
    if response_text.startswith('```'):
        response_text = response_text.split('\n', 1)[1].rsplit('```', 1)[0].strip()

    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        return {"error": "Could not parse extraction results", "raw": response_text}
