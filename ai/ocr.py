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
                    "text": """You are an expert document reader. Extract all personal information from this Malaysian identity document image.

STEP 1 — IDENTIFY THE DOCUMENT TYPE:
- **Malaysian NRIC (MyKad)**: A PLASTIC CARD (credit-card sized) with the Malaysian coat of arms ("MALAYSIA" printed at top). It has a photo on the left, name, IC number, and address. The IC number is a 12-digit number in YYMMDD-SS-NNNN format (e.g., 781117-01-6247). Set doc_type = "nric".
- **Malaysian Passport**: A BOOKLET page (larger than a card) with "MALAYSIA" and "PASSPORT" printed. It has a passport number starting with a LETTER followed by digits (e.g., A12345678 or H12345678). It contains an MRZ (Machine Readable Zone) — two lines of characters at the bottom. Set doc_type = "passport".

STEP 2 — EXTRACT THE DATA:

Return ONLY a JSON object with these fields (use empty string "" if not visible/readable):
{
    "doc_type": "nric" or "passport",
    "full_name": "FULL NAME IN UPPERCASE",
    "nric_number": "the ID number (see format rules below)",
    "date_of_birth": "DD-MM-YYYY",
    "address": "full address as printed",
    "gender": "Male or Female",
    "nationality": "Malaysian or as shown",
    "passport_expiry": "DD-MM-YYYY (passport ONLY, empty string for NRIC)"
}

=== RULES FOR NRIC (MyKad) ===
- doc_type MUST be "nric"
- IC NUMBER FORMAT: exactly 12 digits as YYMMDD-SS-NNNN
  * First 6 digits = date of birth (YYMMDD). E.g., person born 17 Nov 1978 → 781117
  * Next 2 digits = state/country code (01-16 for states, or 60-99 for foreign born)
  * Last 4 digits = sequential number
  * CRITICAL: Read each digit ONE BY ONE very carefully. Do NOT guess or transpose digits.
  * The IC number is the large number printed prominently on the card, usually below or next to the name.
  * Double-check: the first 6 digits MUST match the date_of_birth (YYMMDD = DD-MM-YYYY reversed).
- FULL NAME: Read the name EXACTLY as printed, in UPPERCASE. It is usually the largest text on the front.
- ADDRESS: The address is printed on the BACK of the MyKad (or sometimes visible on front of older versions). It includes multiple lines: house number, street, taman/kampung, postcode, city, and state. Extract ALL lines and join them with ", ". Example: "NO 12 JALAN MERBAU 3, TAMAN MERBAU, 47100 PUCHONG, SELANGOR". If you can see address text anywhere on the card, extract it completely.
- GENDER: Determined from the LAST digit of the IC number. Odd number (1,3,5,7,9) = Male. Even number (0,2,4,6,8) = Female.
- passport_expiry: MUST be empty string "" for NRIC.
- date_of_birth: Derive from first 6 digits of IC. E.g., IC starts with 781117 → DOB is 17-11-1978. For years: 00-30 → 2000-2030, 31-99 → 1931-1999.

=== RULES FOR PASSPORT ===
- doc_type MUST be "passport"
- PASSPORT NUMBER: Starts with a LETTER (usually A or H for Malaysian passports) followed by 8 digits. E.g., "A12345678". This is NOT in YYMMDD-SS-NNNN format. Put the passport number in the "nric_number" field.
- FULL NAME: Read exactly as printed in the passport data page.
- DATE OF BIRTH: Read from the "Date of Birth" field on the passport data page. Format as DD-MM-YYYY.
- GENDER: Read from the "Sex" field (M = Male, F = Female).
- NATIONALITY: Read from the passport (usually "WARGANEGARA MALAYSIA" or "MALAYSIAN").
- passport_expiry: Read from "Date of Expiry" or "Tarikh Luput". Format as DD-MM-YYYY. This field MUST be filled for passports.
- ADDRESS: Passports typically do not show address. Use empty string "".

IMPORTANT ACCURACY NOTES:
- Read numbers DIGIT BY DIGIT. Do not guess. If a digit is unclear, look at the context.
- For NRIC: Cross-check that the first 6 digits of the IC match the date of birth.
- For Passport: The passport number starts with a LETTER, not a digit.
- If both sides of an NRIC are visible, get the name and IC from the FRONT, and the address from the BACK.

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
