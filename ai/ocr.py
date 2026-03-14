"""OCR extraction using Claude Vision API."""
import base64
import json
import re
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
        max_tokens=2000,
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
                    "text": """You are an expert Malaysian document reader. Extract all personal information from this image.

STEP 1 — IDENTIFY DOCUMENT TYPE (MOST IMPORTANT):

Look at the document carefully and determine what it is:

PASSPORT indicators (if ANY of these are true, it is a PASSPORT):
- The word "PASSPORT" or "PASPORT" appears on the document
- There is an MRZ zone (two lines of <<< characters at the bottom)
- The ID number starts with a LETTER like A, H, K followed by digits (e.g. A12345678, H87654321)
- It is a full page from a booklet (not a small plastic card)
- Fields like "Date of Expiry" / "Tarikh Luput" are visible

NRIC (MyKad) indicators (ALL must be true):
- It is a small PLASTIC CARD (credit card sized)
- The ID number is purely digits in YYMMDD-SS-NNNN format (12 digits, no letters)
- "KAD PENGENALAN" or just "MALAYSIA" at the top with the coat of arms

KEY TEST: Look at the ID number. If it starts with a LETTER → PASSPORT. If it is all digits like 781117-01-6247 → NRIC.

STEP 2 — EXTRACT THE DATA:

Return ONLY a JSON object (no markdown, no explanation):
{
    "doc_type": "nric" or "passport",
    "full_name": "FULL NAME IN UPPERCASE",
    "nric_number": "the ID number",
    "date_of_birth": "DD-MM-YYYY",
    "address": "full address, each line separated by comma-space",
    "gender": "Male or Female",
    "nationality": "Malaysian or as shown",
    "passport_expiry": "DD-MM-YYYY for passport, empty string for NRIC"
}

=== IF doc_type IS "nric" ===
- nric_number: 12 digits as YYMMDD-SS-NNNN. Read each digit ONE BY ONE carefully.
  The first 6 digits encode the date of birth. Cross-check: if IC starts 781117, DOB must be 17-11-1978.
- full_name: EXACTLY as printed in UPPERCASE.
- address: Read the address EXACTLY as printed. It may be on the FRONT or BACK of the card.
  Read CHARACTER BY CHARACTER. Do NOT guess or substitute words.
  Separate each line with ", " (comma space).
  Example: "02-08 BLK B1, APT MOLEK PINE 3, JALAN MOLEK 1/5, TAMAN MOLEK, 81100 JOHOR BAHRU, JOHOR"
- gender: From last digit of IC — odd = Male, even = Female.
- passport_expiry: MUST be "" (empty string).
- date_of_birth: Derive from IC first 6 digits. Years 00-30 → 2000-2030, 31-99 → 1931-1999.

=== IF doc_type IS "passport" ===
- nric_number: The PASSPORT NUMBER (letter + digits, e.g. "A12345678"). NOT in YYMMDD format.
- full_name: As printed on the data page.
- date_of_birth: From the "Date of Birth" field. Format DD-MM-YYYY.
- gender: M = Male, F = Female.
- nationality: As shown (usually "MALAYSIAN").
- passport_expiry: From "Date of Expiry" / "Tarikh Luput". Format DD-MM-YYYY. MUST be filled.
- address: Usually not on passport. Use "".

Return ONLY valid JSON. No markdown code fences. No explanation."""
                }
            ]
        }]
    )

    response_text = message.content[0].text.strip()

    # Strip markdown code fences if present
    if response_text.startswith('```'):
        response_text = response_text.split('\n', 1)[1].rsplit('```', 1)[0].strip()

    # Try to extract JSON from response even if there's extra text
    if not response_text.startswith('{'):
        match = re.search(r'\{[^{}]*\}', response_text, re.DOTALL)
        if match:
            response_text = match.group(0)

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        # Try fixing common issues: unescaped newlines in strings
        try:
            fixed = re.sub(r'(?<!\\)\n', ' ', response_text)
            result = json.loads(fixed)
        except json.JSONDecodeError:
            return {"error": "Could not parse extraction results", "raw": response_text}

    # Post-processing: auto-detect doc_type from nric_number if model got it wrong
    nric_num = result.get('nric_number', '')
    if nric_num and re.match(r'^[A-Za-z]', nric_num):
        # ID starts with a letter → definitely a passport
        result['doc_type'] = 'passport'
    elif nric_num and re.match(r'^\d{6}-?\d{2}-?\d{4}$', nric_num):
        # Pure 12-digit number → definitely NRIC
        result['doc_type'] = 'nric'
        result['passport_expiry'] = ''

    return result
