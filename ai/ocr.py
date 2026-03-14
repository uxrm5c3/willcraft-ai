"""OCR extraction using Claude Vision API — chain-of-thought accuracy mode."""
import base64
import json
import re
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_FAST


def extract_nric_data(image_path: str) -> dict:
    """Extract personal data from a Malaysian NRIC or passport image.

    Uses chain-of-thought prompting: the model first spells out each character
    it sees, then produces the final JSON. This significantly improves accuracy
    for names and IC numbers.

    Returns dict with keys: full_name, nric_number, date_of_birth, address, gender, nationality
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    with open(image_path, 'rb') as f:
        image_data = base64.standard_b64encode(f.read()).decode('utf-8')

    ext = image_path.rsplit('.', 1)[-1].lower()
    if ext == 'pdf':
        media_type = 'application/pdf'
    else:
        media_type = {
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'png': 'image/png', 'gif': 'image/gif',
        }.get(ext, 'image/jpeg')

    if ext == 'pdf':
        content_block = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": image_data,
            }
        }
    else:
        content_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": image_data,
            }
        }

    message = client.messages.create(
        model=CLAUDE_MODEL_FAST,
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                content_block,
                {
                    "type": "text",
                    "text": """Read this Malaysian IC (MyKad) or Passport image carefully.

You MUST follow these steps IN ORDER. Do NOT skip any step.

STEP 1 — IDENTIFY DOCUMENT TYPE:
Look at the ID number. Does it start with a letter? → passport. Is it 12 digits? → nric.

STEP 2 — READ THE FULL NAME:
Spell out the name CHARACTER BY CHARACTER. Write each letter you see:
"Name characters: [letter1] [letter2] [letter3] ..."
Then write the complete name.
Watch for: I vs L, O vs 0, S vs 5, B vs 8, G vs C, U vs V.

STEP 3 — READ THE IC/PASSPORT NUMBER:
Spell out EACH DIGIT one by one:
"IC digits: [d1] [d2] [d3] [d4] [d5] [d6] - [d7] [d8] - [d9] [d10] [d11] [d12]"
Then write the complete number.

CRITICAL DATE VALIDATION for NRIC:
- First 6 digits = YYMMDD (birth date)
- Digits 3-4 = MONTH: must be 01 to 12. If you read 00, 13, 14, etc → you misread a digit. Re-read!
- Digits 5-6 = DAY: must be 01 to 31. If you read 00, 32, 33, 40, 47, etc → you misread a digit. Re-read!
- Example: 870229 = 29 Feb 1987 ✓ | 871347 = month 13 day 47 ✗ IMPOSSIBLE — re-read carefully!
- Common misreads: 0↔8, 1↔7, 3↔8, 4↔1, 5↔6, 6↔8, 9↔0
- If the date seems wrong, look again very carefully at each digit.

STEP 4 — READ THE ADDRESS (back of MyKad only, skip for passport):
Read each line of the address top to bottom. Write each line separately.

STEP 5 — OUTPUT THE FINAL JSON:
After your analysis above, output ONLY this JSON block (no extra text after it):

```json
{
    "doc_type": "nric or passport",
    "full_name": "EXACT NAME IN UPPERCASE",
    "nric_number": "YYMMDD-SS-NNNN",
    "date_of_birth": "DD-MM-YYYY",
    "address": "line1\\nline2\\nline3",
    "gender": "Male or Female",
    "nationality": "Malaysian",
    "passport_expiry": ""
}
```

RULES:
- full_name: UPPERCASE, exactly as printed. Include BIN/BINTI/A/L/A/P if present.
- nric_number: 12 digits with dashes YYMMDD-SS-NNNN for NRIC. Passport number for passport.
- date_of_birth: DD-MM-YYYY. For NRIC derive from IC (first 6 digits). 00-30→2000s, 31-99→1900s.
- address: Back of MyKad only. Separate lines with \\n. For passport return "".
- gender: NRIC last digit odd=Male, even=Female. Passport: read M/F field.
- passport_expiry: Passport only (DD-MM-YYYY). For NRIC return "".
- Return "" for any field you truly cannot read at all.

IMPORTANT: The character-by-character reading in Steps 2-3 is CRITICAL for accuracy. Do NOT skip it."""
                }
            ]
        }]
    )

    response_text = message.content[0].text.strip()

    # Extract JSON from the response (it may have chain-of-thought text before it)
    json_str = _extract_json(response_text)
    if not json_str:
        return {"error": "Could not parse extraction results", "raw": response_text[:500]}

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        # Try fixing common issues: unescaped newlines in strings
        try:
            fixed = re.sub(r'(?<!\\)\n', ' ', json_str)
            result = json.loads(fixed)
        except json.JSONDecodeError:
            return {"error": "Could not parse extraction results", "raw": response_text[:500]}

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

    # 2. Validate NRIC format — must be exactly NNNNNN-NN-NNNN with valid date
    if result.get('doc_type') == 'nric' and nric_num:
        # Remove dashes for validation, then reformat
        digits_only = nric_num.replace('-', '')
        if len(digits_only) == 12 and digits_only.isdigit():
            # Validate YYMMDD — month must be 01-12, day must be 01-31
            ic_yy = digits_only[:2]
            ic_mm = int(digits_only[2:4])
            ic_dd = int(digits_only[4:6])
            if ic_mm < 1 or ic_mm > 12:
                # Invalid month — IC number is misread, flag it
                result['nric_number'] = ''
                result['_nric_date_invalid'] = f"Month={ic_mm:02d} is invalid (must be 01-12)"
            elif ic_dd < 1 or ic_dd > 31:
                # Invalid day — IC number is misread, flag it
                result['nric_number'] = ''
                result['_nric_date_invalid'] = f"Day={ic_dd:02d} is invalid (must be 01-31)"
            else:
                # Additional check: months with 30 days max
                if ic_mm in (4, 6, 9, 11) and ic_dd > 30:
                    result['nric_number'] = ''
                    result['_nric_date_invalid'] = f"Day={ic_dd:02d} invalid for month={ic_mm:02d}"
                elif ic_mm == 2 and ic_dd > 29:
                    result['nric_number'] = ''
                    result['_nric_date_invalid'] = f"Day={ic_dd:02d} invalid for February"
                else:
                    # Valid date — reformat to standard YYMMDD-SS-NNNN
                    result['nric_number'] = f"{digits_only[:6]}-{digits_only[6:8]}-{digits_only[8:]}"
        else:
            # Invalid NRIC format — clear it
            result['nric_number'] = ''

    # 3. Cross-validate DOB against NRIC (derive DOB from IC number)
    if result.get('doc_type') == 'nric' and result.get('nric_number'):
        ic_digits = result['nric_number'].replace('-', '')[:6]  # YYMMDD
        ic_yy, ic_mm, ic_dd = ic_digits[:2], ic_digits[2:4], ic_digits[4:6]
        # Always derive DOB from IC (it's the most reliable source)
        try:
            year = int('20' + ic_yy) if int(ic_yy) <= 30 else int('19' + ic_yy)
            month = int(ic_mm)
            day = int(ic_dd)
            if 1 <= month <= 12 and 1 <= day <= 31:
                result['date_of_birth'] = f"{ic_dd}-{ic_mm}-{year}"
            else:
                result['date_of_birth'] = ''
        except ValueError:
            result['date_of_birth'] = ''
    elif result.get('doc_type') == 'nric' and not result.get('nric_number'):
        # NRIC was invalid — also clear DOB since we can't derive it reliably
        dob = result.get('date_of_birth', '')
        if dob:
            # Validate the model-provided DOB independently
            try:
                parts = dob.split('-')
                if len(parts) == 3:
                    dd, mm = int(parts[0]), int(parts[1])
                    if dd < 1 or dd > 31 or mm < 1 or mm > 12:
                        result['date_of_birth'] = ''
            except (ValueError, IndexError):
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

    # 10. Remove internal fields not needed in response
    result.pop('confidence', None)

    return result


def _extract_json(text: str) -> str:
    """Extract JSON object from text that may contain chain-of-thought reasoning.

    Tries multiple strategies:
    1. Look for ```json code block
    2. Find the last { ... } block in the text
    3. Try the whole text as JSON
    """
    # Strategy 1: Look for ```json ... ``` code block
    json_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if json_block_match:
        return json_block_match.group(1).strip()

    # Strategy 2: Find the last JSON object (the final answer after reasoning)
    # Use a greedy approach to find the last { ... } block
    last_brace = text.rfind('}')
    if last_brace >= 0:
        # Walk backwards to find the matching opening brace
        depth = 0
        for i in range(last_brace, -1, -1):
            if text[i] == '}':
                depth += 1
            elif text[i] == '{':
                depth -= 1
            if depth == 0:
                candidate = text[i:last_brace + 1]
                # Quick validation: does it look like JSON with expected keys?
                if '"full_name"' in candidate or '"nric_number"' in candidate or '"doc_type"' in candidate:
                    return candidate
                break

    # Strategy 3: Try the whole text
    text = text.strip()
    if text.startswith('{') and text.endswith('}'):
        return text

    # Strategy 4: Simple regex for any JSON-like object
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if match:
        return match.group(0)

    return None
