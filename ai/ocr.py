"""OCR extraction using Claude Vision API — strict accuracy mode."""
import base64
import json
import re
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_FAST


def extract_nric_data(image_path: str) -> dict:
    """Extract personal data from a Malaysian NRIC or passport image.

    STRICT MODE: Only returns data that can be read with high confidence.
    Any unclear field is returned as empty string rather than guessed.

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
        model=CLAUDE_MODEL_FAST,
        max_tokens=1024,
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
                    "text": """Extract personal data from this Malaysian IC (MyKad) or Passport image.

CRITICAL RULE: ACCURACY OVER COMPLETENESS.
- If you CANNOT clearly read a field, return "" (empty string). NEVER guess or infer.
- It is far better to return an empty field than wrong data.
- Only output characters you can see clearly in the image.
- If the image is blurry, dark, or partially obscured, return "" for affected fields.

STEP 1 — DOCUMENT TYPE:
- ID number starts with a LETTER (e.g. A12345678) → "passport"
- ID number is 12 digits YYMMDD-SS-NNNN → "nric"

STEP 2 — Return ONLY this JSON (no markdown, no explanation):
{
    "doc_type": "nric" or "passport",
    "full_name": "",
    "nric_number": "",
    "date_of_birth": "",
    "address": "",
    "gender": "",
    "nationality": "",
    "passport_expiry": ""
}

FIELD RULES:

full_name: EXACTLY as printed. UPPERCASE. If any character is unclear, return "".

nric_number: For NRIC, format YYMMDD-SS-NNNN (12 digits with dashes). Read each digit one by one. If ANY digit is unclear, return "".

date_of_birth: Format DD-MM-YYYY.
  For NRIC: derive from IC number (first 6 digits = YYMMDD). 00-30 → 2000s, 31-99 → 1900s.
  For passport: read from "Date of Birth" field.
  If unclear, return "".

address: ONLY for NRIC (back of MyKad). For passport, return "".
  Read EXACTLY as printed, line by line, top to bottom.
  Separate each line with \\n.
  Do NOT add any text not printed on the card (no "MALAYSIA", no reformatting).
  If the address is blurry or partially unreadable, return "".
  Every character matters — unit numbers, block numbers, street numbers, postcodes.

gender: For NRIC: last digit of IC — odd=Male, even=Female. For passport: M=Male, F=Female. If IC number is unclear, return "".

nationality: Usually "Malaysian". Only change if clearly stated otherwise. If unclear, return "Malaysian".

passport_expiry: For passport only (DD-MM-YYYY). For NRIC, return "".

REMEMBER: "" is always acceptable. Wrong data is NEVER acceptable."""
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

    # =====================================================================
    # POST-PROCESSING VALIDATION — reject bad data rather than pass it through
    # =====================================================================

    # 1. Auto-detect doc_type from nric_number format
    nric_num = result.get('nric_number', '').strip()
    if nric_num and re.match(r'^[A-Za-z]', nric_num):
        result['doc_type'] = 'passport'
    elif nric_num and re.match(r'^\d{6}-?\d{2}-?\d{4}$', nric_num):
        result['doc_type'] = 'nric'
        result['passport_expiry'] = ''

    # 2. Validate NRIC format — must be exactly NNNNNN-NN-NNNN
    if result.get('doc_type') == 'nric' and nric_num:
        # Remove dashes for validation, then reformat
        digits_only = nric_num.replace('-', '')
        if len(digits_only) == 12 and digits_only.isdigit():
            # Reformat to standard YYMMDD-SS-NNNN
            result['nric_number'] = f"{digits_only[:6]}-{digits_only[6:8]}-{digits_only[8:]}"
        else:
            # Invalid NRIC format — clear it
            result['nric_number'] = ''

    # 3. Cross-validate DOB against NRIC
    if result.get('doc_type') == 'nric' and result.get('nric_number'):
        ic_digits = result['nric_number'].replace('-', '')[:6]  # YYMMDD
        dob = result.get('date_of_birth', '')
        if dob and ic_digits:
            # DOB should be DD-MM-YYYY; IC is YYMMDD
            try:
                dob_parts = dob.split('-')
                if len(dob_parts) == 3:
                    dd, mm, yyyy = dob_parts
                    yy = yyyy[2:]  # last 2 digits
                    ic_yy, ic_mm, ic_dd = ic_digits[:2], ic_digits[2:4], ic_digits[4:6]
                    if yy != ic_yy or mm != ic_mm or dd != ic_dd:
                        # DOB doesn't match IC — derive from IC instead
                        year = int('20' + ic_yy) if int(ic_yy) <= 30 else int('19' + ic_yy)
                        result['date_of_birth'] = f"{ic_dd}-{ic_mm}-{year}"
            except (ValueError, IndexError):
                pass
        elif not dob and ic_digits:
            # Derive DOB from IC
            ic_yy, ic_mm, ic_dd = ic_digits[:2], ic_digits[2:4], ic_digits[4:6]
            try:
                year = int('20' + ic_yy) if int(ic_yy) <= 30 else int('19' + ic_yy)
                result['date_of_birth'] = f"{ic_dd}-{ic_mm}-{year}"
            except ValueError:
                result['date_of_birth'] = ''

    # 4. Cross-validate gender against NRIC last digit
    if result.get('doc_type') == 'nric' and result.get('nric_number'):
        ic_digits = result['nric_number'].replace('-', '')
        if len(ic_digits) == 12:
            last_digit = int(ic_digits[-1])
            correct_gender = 'Male' if last_digit % 2 == 1 else 'Female'
            if result.get('gender') and result['gender'] != correct_gender:
                # Gender doesn't match IC — use IC-derived gender
                result['gender'] = correct_gender
            elif not result.get('gender'):
                result['gender'] = correct_gender

    # 5. Validate name — should be mostly uppercase letters, spaces, slashes, dots
    name = result.get('full_name', '').strip()
    if name:
        # Check if name looks valid (at least 3 chars, mostly letters)
        letter_count = sum(1 for c in name if c.isalpha())
        if letter_count < 3:
            result['full_name'] = ''  # Too short or garbled — clear it

    # 6. Handle address: combine old 3-line format if model still returns it
    if 'address_line1' in result or 'address_line2' in result or 'address_line3' in result:
        lines = []
        for key in ('address_line1', 'address_line2', 'address_line3'):
            val = result.pop(key, '').strip()
            if val:
                lines.append(val)
        if lines:
            result['address'] = '\n'.join(lines)

    # 7. Clean up address formatting
    if result.get('address'):
        addr = result['address']
        addr = addr.replace('\\n', '\n')  # literal \n to real newline
        addr = '\n'.join(line.strip() for line in addr.split('\n') if line.strip())
        result['address'] = addr
    elif 'address' not in result:
        result['address'] = ''

    # 8. Ensure all expected fields exist (empty string if missing)
    for field in ('doc_type', 'full_name', 'nric_number', 'date_of_birth',
                  'address', 'gender', 'nationality', 'passport_expiry'):
        if field not in result:
            result[field] = ''

    # 9. Default nationality
    if not result.get('nationality'):
        result['nationality'] = 'Malaysian'

    return result
