"""OCR extraction using Claude Vision API — chain-of-thought accuracy mode."""
import base64
import io
import json
import os
import re
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_FAST

# Formats that need conversion to JPEG before sending to Claude API
_NEEDS_CONVERSION = {'heic', 'heif', 'webp', 'bmp', 'tiff', 'tif'}


def _prepare_image_for_api(image_path: str):
    """Read an image file and return (base64_data, media_type) suitable for Claude API.

    Converts HEIC, HEIF, WebP, BMP, TIFF to JPEG automatically.
    Returns (base64_str, media_type_str).
    """
    ext = image_path.rsplit('.', 1)[-1].lower()

    if ext == 'pdf':
        with open(image_path, 'rb') as f:
            data = base64.standard_b64encode(f.read()).decode('utf-8')
        return data, 'application/pdf'

    if ext in _NEEDS_CONVERSION:
        try:
            from PIL import Image
            img = Image.open(image_path)
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=90)
            data = base64.standard_b64encode(buf.getvalue()).decode('utf-8')
            return data, 'image/jpeg'
        except ImportError:
            # Pillow not available — try sending raw (may fail for HEIC)
            pass

    # Standard image formats
    with open(image_path, 'rb') as f:
        data = base64.standard_b64encode(f.read()).decode('utf-8')

    media_type = {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'png': 'image/png', 'gif': 'image/gif',
        'webp': 'image/webp',
    }.get(ext, 'image/jpeg')

    return data, media_type


def _make_content_block(image_path: str):
    """Create the content block dict for Claude API from an image path."""
    data, media_type = _prepare_image_for_api(image_path)
    ext = image_path.rsplit('.', 1)[-1].lower()

    if ext == 'pdf':
        return {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": data}}
    else:
        return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def extract_nric_data(image_path: str) -> dict:
    """Extract personal data from a Malaysian NRIC or passport image.

    Uses chain-of-thought prompting: the model first spells out each character
    it sees, then produces the final JSON. This significantly improves accuracy
    for names and IC numbers.

    Returns dict with keys: full_name, nric_number, date_of_birth, address, gender, nationality
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    content_block = _make_content_block(image_path)

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
Address is on the BACK of MyKad, printed in small text. Read VERY carefully.

Spell out each line word by word:
"Address line 1: [word1] [word2] ..."
"Address line 2: [word1] [word2] ..."
"Address line 3: [word1] [word2] ..."

Malaysian address patterns to expect:
- Line 1: Unit/house number + street name (e.g., "NO 12 JALAN MAWAR 3")
- Line 2: Neighbourhood/area (e.g., "TAMAN BUKIT INDAH")
- Line 3: Postcode + City + State (e.g., "81200 JOHOR BAHRU JOHOR")
- Common street words: JALAN, LORONG, PERSIARAN, LEBUH, LENGKOK, SOLOK
- Common area words: TAMAN, KAMPUNG, FLAT, PANGSAPURI, APARTMENT, KONDOMINIUM
- Postcode: always 5 digits (e.g., 81200, 47810, 53000)
- States: JOHOR, KEDAH, KELANTAN, MELAKA, NEGERI SEMBILAN, PAHANG, PERAK, PERLIS, PULAU PINANG, SABAH, SARAWAK, SELANGOR, TERENGGANU, W.P. KUALA LUMPUR, W.P. PUTRAJAYA, W.P. LABUAN
- Watch for: 0 vs O, 1 vs I, 5 vs S, 8 vs B in address text

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

    # 7. Clean up address formatting + Malaysian address post-processing
    if result.get('address'):
        addr = result['address']
        addr = addr.replace('\\n', '\n')  # literal \n to real newline
        addr = '\n'.join(line.strip() for line in addr.split('\n') if line.strip())
        # Fix common OCR errors in Malaysian addresses
        addr = _clean_malaysian_address(addr)
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

    # 10. If address is empty and doc_type is NRIC, try a second focused OCR pass
    if result.get('doc_type') == 'nric' and not result.get('address', '').strip():
        try:
            address_result = _extract_address_only(client, content_block)
            if address_result:
                result['address'] = address_result
        except Exception:
            pass  # Address retry failed — user can enter manually

    # 11. Remove internal fields not needed in response
    result.pop('confidence', None)
    result.pop('_nric_date_invalid', None)

    return result


def _clean_malaysian_address(addr: str) -> str:
    """Fix common OCR errors in Malaysian addresses."""
    if not addr:
        return addr

    lines = addr.split('\n')
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Uppercase (MyKad addresses are always uppercase)
        line = line.upper()
        # Fix common abbreviation errors
        line = re.sub(r'\bJLN\b', 'JALAN', line)
        line = re.sub(r'\bLRG\b', 'LORONG', line)
        line = re.sub(r'\bTMN\b', 'TAMAN', line)
        line = re.sub(r'\bKG\b', 'KAMPUNG', line)
        line = re.sub(r'\bAPT\b', 'APARTMENT', line)
        line = re.sub(r'\bBLK\b', 'BLOK', line)
        # Fix O/0 confusion in postcodes: 5-digit sequences should be all digits
        postcode_match = re.search(r'\b([O0-9]{5})\b', line)
        if postcode_match:
            pc = postcode_match.group(1).replace('O', '0').replace('o', '0')
            line = line[:postcode_match.start(1)] + pc + line[postcode_match.end(1):]
        # Fix common state name misspellings
        state_fixes = {
            r'SELANC[O0]R\b': 'SELANGOR',
            r'J[O0]H[O0]R\b': 'JOHOR',
            r'MELA[KG]A\b': 'MELAKA',
            r'PERA[KG]\b': 'PERAK',
            r'PAHANG\b': 'PAHANG',
            r'KEDAH\b': 'KEDAH',
            r'KUALA\s*LUMPUR': 'KUALA LUMPUR',
            r'PU[LI]AU\s*PINANG': 'PULAU PINANG',
            r'TERENGGAN[OU]\b': 'TERENGGANU',
            r'NEGERI\s*SEMBI[LI]AN': 'NEGERI SEMBILAN',
        }
        for pattern, replacement in state_fixes.items():
            line = re.sub(pattern, replacement, line, flags=re.IGNORECASE)
        cleaned.append(line)

    return '\n'.join(cleaned)


def _extract_address_only(client, content_block) -> str:
    """Second-pass OCR focused specifically on reading the address from MyKad back.

    Uses a simpler, address-focused prompt to improve readability.
    """
    message = client.messages.create(
        model=CLAUDE_MODEL_FAST,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                content_block,
                {
                    "type": "text",
                    "text": """This is the back of a Malaysian MyKad (IC card). I need you to read ONLY the address.

The address is the multi-line text block printed on the card. It is typically 2-4 lines.

Read VERY CAREFULLY, word by word, character by character. The text may be small or blurry.

Malaysian address format:
- Line 1: House/unit number + street (e.g., "NO 12 JALAN MAWAR 3" or "B-12-3 KONDOMINIUM SERI")
- Line 2: Area/neighbourhood (e.g., "TAMAN BUKIT INDAH" or "KAMPUNG BARU")
- Line 3: Postcode (5 digits) + City + State

Common words: JALAN, LORONG, TAMAN, KAMPUNG, FLAT, PANGSAPURI, PERSIARAN, LEBUH
Postcodes: always exactly 5 digits (10000-99999)
States: JOHOR, KEDAH, KELANTAN, MELAKA, NEGERI SEMBILAN, PAHANG, PERAK, PERLIS, PULAU PINANG, SABAH, SARAWAK, SELANGOR, TERENGGANU, W.P. KUALA LUMPUR, W.P. PUTRAJAYA, W.P. LABUAN

Return ONLY the address text, each line separated by \\n. No JSON, no explanation.
If you cannot read the address at all, return exactly: UNREADABLE"""
                }
            ]
        }]
    )

    text = message.content[0].text.strip()
    if not text or text == 'UNREADABLE' or len(text) < 5:
        return ''

    # Clean up the response
    # Remove any markdown or quotes
    text = text.strip('"\'`')
    text = re.sub(r'^```\w*\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    # Convert literal \n to real newlines
    text = text.replace('\\n', '\n')

    # Clean each line
    lines = [line.strip() for line in text.split('\n') if line.strip()]

    # Validate: at least one line should look like a Malaysian address
    has_address_words = any(
        re.search(r'(JALAN|JLN|LORONG|LRG|TAMAN|TMN|KAMPUNG|KG|FLAT|NO\s*\.?\s*\d|PERSIARAN|LEBUH|PANGSAPURI|APARTMENT|APT|KONDOMINIUM|BLOK|BLK)', line, re.IGNORECASE)
        for line in lines
    )
    has_postcode = any(re.search(r'\b\d{5}\b', line) for line in lines)

    if not has_address_words and not has_postcode and len(lines) < 2:
        return ''

    return '\n'.join(lines)


def extract_death_cert_data(image_path: str) -> dict:
    """Extract data from a Malaysian death certificate (Sijil Kematian).

    Returns dict with keys: death_cert_number, full_name, nric_number,
    date_of_death, time_of_death, place_of_death, cause_of_death
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    content_block = _make_content_block(image_path)

    message = client.messages.create(
        model=CLAUDE_MODEL_FAST,
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                content_block,
                {
                    "type": "text",
                    "text": """Read this Malaysian Death Certificate (Sijil Kematian / Perakuan Kematian) carefully.

You MUST follow these steps IN ORDER. Do NOT skip any step.

STEP 1 — IDENTIFY DOCUMENT:
Look for "SIJIL KEMATIAN", "PERAKUAN KEMATIAN", or "DEATH CERTIFICATE" header.
Confirm this is a death certificate.

STEP 2 — READ THE CERTIFICATE NUMBER:
Look for the registration/reference number. Spell each character:
"Certificate number: [c1] [c2] [c3] ..."
Then write the complete number.

STEP 3 — READ THE DECEASED'S NAME:
Look for "Nama Si Mati" or "Name of Deceased". Spell CHARACTER BY CHARACTER:
"Name characters: [letter1] [letter2] ..."
Then write the complete name.
Watch for: I vs L, O vs 0, S vs 5, B vs 8, G vs C, U vs V.

STEP 4 — READ THE DECEASED'S NRIC/IC NUMBER:
Look for "No. Kad Pengenalan" or "IC Number". Spell EACH DIGIT:
"IC digits: [d1] [d2] [d3] [d4] [d5] [d6] - [d7] [d8] - [d9] [d10] [d11] [d12]"
Validate: first 6 digits = YYMMDD. Month must be 01-12. Day must be 01-31.

STEP 5 — READ DATE OF DEATH:
Look for "Tarikh Kematian" or "Date of Death".
Read the date carefully: day, month, year.
Format as DD-MM-YYYY.

STEP 6 — READ TIME OF DEATH:
Look for "Masa Kematian" or "Time of Death".
Read hours and minutes. Note AM/PM if shown.

STEP 7 — READ PLACE OF DEATH:
Look for "Tempat Kematian" or "Place of Death".
Read the full location — may be a hospital name, home address, or location.

STEP 8 — READ CAUSE OF DEATH (if visible):
Look for "Sebab Kematian" or "Cause of Death".
Read whatever is written. May be in medical terminology.

STEP 9 — OUTPUT THE FINAL JSON:
After your analysis above, output ONLY this JSON block:

```json
{
    "death_cert_number": "certificate/registration number",
    "full_name": "EXACT NAME IN UPPERCASE",
    "nric_number": "YYMMDD-SS-NNNN",
    "date_of_death": "DD-MM-YYYY",
    "time_of_death": "HH:MM AM/PM or HH:MM",
    "place_of_death": "full location text",
    "cause_of_death": "cause if readable"
}
```

RULES:
- full_name: UPPERCASE, exactly as printed
- nric_number: 12 digits with dashes YYMMDD-SS-NNNN
- date_of_death: DD-MM-YYYY format
- time_of_death: as printed (e.g., "9.58 pagi" or "14:30")
- place_of_death: full location
- Return "" for any field you truly cannot read.

IMPORTANT: The character-by-character reading is CRITICAL for accuracy. Do NOT skip it."""
                }
            ]
        }]
    )

    response_text = message.content[0].text.strip()
    json_str = _extract_json(response_text)
    if not json_str:
        return {"error": "Could not parse extraction results", "raw": response_text[:500]}

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        try:
            fixed = re.sub(r'(?<!\\)\n', ' ', json_str)
            result = json.loads(fixed)
        except json.JSONDecodeError:
            return {"error": "Could not parse extraction results", "raw": response_text[:500]}

    # Post-processing: validate NRIC format
    nric_num = result.get('nric_number', '').strip()
    if nric_num:
        digits_only = nric_num.replace('-', '')
        if len(digits_only) == 12 and digits_only.isdigit():
            result['nric_number'] = f"{digits_only[:6]}-{digits_only[6:8]}-{digits_only[8:]}"

    # Validate date format
    dod = result.get('date_of_death', '')
    if dod:
        # Try to normalize date format
        dod = dod.strip()
        # Accept various formats and normalize to DD-MM-YYYY
        date_match = re.match(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', dod)
        if date_match:
            dd, mm, yyyy = date_match.groups()
            result['date_of_death'] = f"{int(dd):02d}-{int(mm):02d}-{yyyy}"

    # Ensure all expected fields exist
    for field in ('death_cert_number', 'full_name', 'nric_number', 'date_of_death',
                  'time_of_death', 'place_of_death', 'cause_of_death'):
        if field not in result:
            result[field] = ''

    return result


def extract_asset_document(image_path: str, asset_type: str) -> dict:
    """Extract data from an asset-related document (title, bank statement, vehicle card, etc.).

    Args:
        image_path: Path to the image/PDF file
        asset_type: One of 'property', 'bank', 'vehicle', 'other', 'liability'

    Returns dict with extracted fields matching the asset type.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    content_block = _make_content_block(image_path)

    prompts = {
        'property': """Read this Malaysian property title document (Hakmilik / Geran / Land Title).

Extract these fields by reading carefully, character by character:
1. Title Number (Hakmilik) — e.g., H.S.(D) 12345, GRN 67890, PM 1234
2. Lot Number — e.g., Lot 1234, PT 5678
3. Mukim / District — the mukim or daerah name
4. Property Address — full address if shown
5. Land Area — size in sq ft or hectares if shown

Output ONLY this JSON:
```json
{
    "title_number": "",
    "lot_number": "",
    "mukim": "",
    "description": "full address or property description",
    "area": ""
}
```""",
        'bank': """Read this bank document (bank statement, passbook, or account document).

Extract these fields:
1. Bank Name — e.g., Maybank, CIMB, Public Bank, RHB
2. Account Number — the full account number
3. Account Holder Name — name on the account
4. Balance — latest balance if shown (numbers only with commas)

Output ONLY this JSON:
```json
{
    "bank_name": "",
    "account_number": "",
    "holder_name": "",
    "value": ""
}
```""",
        'vehicle': """Read this vehicle document (registration card / JPJ card / road tax / grant).

Extract these fields:
1. Vehicle Make/Model — e.g., Toyota Vios, Honda City
2. Registration Number — e.g., JQK 1234, WA 5678 B
3. Engine Number
4. Chassis Number
5. Year of Manufacture

Output ONLY this JSON:
```json
{
    "description": "make and model with year",
    "reg_number": "",
    "engine_number": "",
    "chassis_number": "",
    "year": ""
}
```""",
        'other': """Read this financial/asset document (insurance policy, EPF statement, share certificate, etc.).

Extract these fields:
1. Document Type — what kind of document is this (insurance, EPF, shares, etc.)
2. Description — policy number, account reference, or description
3. Value/Amount — monetary value if shown

Output ONLY this JSON:
```json
{
    "sub_type": "insurance or epf or shares or other",
    "description": "",
    "value": ""
}
```""",
        'liability': """Read this loan/debt document (loan statement, credit card statement, mortgage document).

Extract these fields:
1. Type — housing loan, car loan, personal loan, credit card, other
2. Description — lender name and loan/account reference
3. Outstanding Amount — amount still owed

Output ONLY this JSON:
```json
{
    "sub_type": "mortgage or car_loan or personal_loan or credit_card or other",
    "description": "",
    "value": ""
}
```"""
    }

    prompt_text = prompts.get(asset_type, prompts['other'])
    prompt_text += '\n\nRULES:\n- Read character by character for numbers and names\n- Return "" for any field you cannot read\n- UPPERCASE for names\n- Include commas in monetary amounts'

    message = client.messages.create(
        model=CLAUDE_MODEL_FAST,
        max_tokens=1024,
        messages=[{"role": "user", "content": [content_block, {"type": "text", "text": prompt_text}]}]
    )

    response_text = message.content[0].text.strip()
    json_str = _extract_json(response_text)
    if not json_str:
        return {"error": "Could not parse extraction results"}

    try:
        result = json.loads(json_str)
    except json.JSONDecodeError:
        try:
            fixed = re.sub(r'(?<!\\)\n', ' ', json_str)
            result = json.loads(fixed)
        except json.JSONDecodeError:
            return {"error": "Could not parse extraction results"}

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


def translate_document(image_path: str) -> str:
    """Translate a Bahasa Malaysia document image to English using Claude Vision.

    Returns the English translation as plain text.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    content_block = _make_content_block(image_path)

    message = client.messages.create(
        model=CLAUDE_MODEL_FAST,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                content_block,
                {
                    "type": "text",
                    "text": """Read this document image carefully.

This is a Malaysian legal/official document likely written in Bahasa Malaysia (Malay language).

Please:
1. Read ALL text visible in the document
2. Translate the entire content from Bahasa Malaysia to English
3. Preserve the document structure (headings, sections, numbered items, tables)
4. Keep proper nouns (names, places, addresses) as-is — do not translate them
5. If a document contains both Malay and English text, keep the English parts unchanged

Format the translation clearly with proper line breaks and sections.
If the document is already in English, just reproduce the text content."""
                }
            ]
        }]
    )
    return message.content[0].text
