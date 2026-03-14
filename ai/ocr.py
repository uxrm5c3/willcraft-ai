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

Return ONLY a JSON object with these fields (use empty string "" if not visible/readable):
{
    "doc_type": "nric" or "passport",
    "full_name": "FULL NAME IN UPPERCASE",
    "nric_number": "XXXXXX-XX-XXXX format for NRIC, or passport number",
    "date_of_birth": "DD-MM-YYYY format",
    "address": "Full address exactly as printed on the card",
    "gender": "Male or Female",
    "nationality": "Malaysian or the nationality shown",
    "passport_expiry": "DD-MM-YYYY format (ONLY for passport, empty string for NRIC)"
}

For Malaysian NRIC (MyKad):
- doc_type must be "nric"
- The 12-digit IC number is in format YYMMDD-SS-NNNN (e.g. 800101-14-1234)
- Extract the full name EXACTLY as printed on the card in UPPERCASE
- IMPORTANT: The address is printed on the BACK of the NRIC. It typically includes street, taman/kampung, postcode, city, and state. Extract the FULL address including all lines. If both front and back are visible, get the address from the back.
- If address is visible, extract it completely with all lines joined (e.g. "NO 12 JALAN MERBAU 3, TAMAN MERBAU, 47100 PUCHONG, SELANGOR")
- Gender: determine from last digit of IC number - odd = Male, even = Female
- passport_expiry MUST be empty string ""

For passport:
- doc_type must be "passport"
- Extract all fields as shown on the data page
- Include the passport expiry date if visible
- Address may not be available on passport - use empty string if not visible

Return ONLY the JSON, no explanation or markdown."""
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
