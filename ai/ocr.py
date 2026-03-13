"""OCR extraction using Claude Vision API."""
import base64
import json
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL


def extract_nric_data(image_path: str) -> dict:
    """Extract personal data from a Malaysian NRIC or passport image.

    Returns dict with keys: full_name, nric_number, date_of_birth, address, gender, nationality
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    with open(image_path, 'rb') as f:
        image_data = base64.standard_b64encode(f.read()).decode('utf-8')

    ext = image_path.rsplit('.', 1)[-1].lower()
    media_type = {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'png': 'image/png', 'gif': 'image/gif',
    }.get(ext, 'image/jpeg')

    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    }
                },
                {
                    "type": "text",
                    "text": """Extract all personal information from this Malaysian NRIC (identity card) or passport image.

Return ONLY a JSON object with these fields (use empty string if not visible/readable):
{
    "full_name": "FULL NAME IN UPPERCASE",
    "nric_number": "XXXXXX-XX-XXXX format",
    "date_of_birth": "DD-MM-YYYY format",
    "address": "Full address if visible",
    "gender": "Male or Female",
    "nationality": "Malaysian or the nationality shown"
}

For Malaysian NRIC:
- The 12-digit number is in format YYMMDD-SS-NNNN
- Extract the name exactly as printed
- The address is on the back of the NRIC
- Gender: last digit odd = Male, even = Female

For passport:
- Extract as shown on the data page

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
