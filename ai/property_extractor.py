"""Property document extraction using Claude Vision API."""
import base64
import json
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_FAST


def extract_property_data(file_path: str, doc_type: str = 'general') -> dict:
    """Extract property data from cukai tanah/cukai pintu, title document, or SPA.

    Args:
        file_path: Path to the document image/PDF
        doc_type: One of 'title', 'cukai_harta', 'cukai_pintu', 'spa', 'general'

    Returns dict with keys: property_address, title_type, lot_number,
    title_number, bandar_pekan, mukim, daerah, negeri, property_description
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
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                content_block,
                {
                    "type": "text",
                    "text": f"""Extract property information from this Malaysian property document.
Document type hint: {doc_type} (if 'auto', detect the document type automatically)

This could be one of:
- Land Title (Hakmilik Tanah) / Geran - look for No. Hakmilik, No. Lot, Mukim, Daerah, Negeri
- Cukai Harta (Property Assessment Tax / Cukai Taksiran) - look for property address, assessment number
- Cukai Pintu (Door Tax) - look for property address
- Sale & Purchase Agreement (SPA) - look for property details in the schedule

CRITICAL: You must understand Malaysian land title document structure to avoid misinterpreting data:

**IMPORTANT DISTINCTIONS on Malaysian Land Titles:**
- "PERIHAL TANAH" / "BUTIR-BUTIR TANAH" section = PROPERTY details (Lot, Mukim, Daerah, Negeri, land area)
- "TUAN PUNYA BERDAFTAR" / "TUAN PUNYA" section = OWNER details (names, IC numbers, addresses)
- The address under "TUAN PUNYA" is the OWNER's residential address, NOT the property address
- The PROPERTY address is derived from: the street/area name in the title + Mukim + Daerah + Negeri
- If there is no street address on the title, use the Lot number + Mukim + Daerah as the property location

Return ONLY a JSON object with these fields (use empty string if not found):
{{
    "document_type": "Detected type: 'title' (land title/geran/hakmilik), 'cukai_harta' (property tax), 'cukai_pintu' (door tax), 'spa' (sale agreement), or 'other'",
    "property_address": "Property location/address from PERIHAL TANAH section ONLY — NOT the owner's address",
    "title_type": "Geran, Hakmilik, HSD, HSM, or Pajakan Negeri",
    "lot_number": "Lot/PT number from the title",
    "title_number": "Title/Hakmilik/Geran number",
    "bandar_pekan": "Bandar/Pekan/Township from title (without leading 'Mukim' or 'Bandar')",
    "mukim": "Mukim name from title (without leading 'Mukim')",
    "daerah": "Daerah/District from title (without leading 'Daerah')",
    "negeri": "State from title (e.g., JOHOR, SELANGOR, PERAK)",
    "property_description": "Brief description (residential, commercial, agricultural, etc.)",
    "num_owners": 1,
    "owner_names": ["Each owner name as separate item — from TUAN PUNYA section"],
    "owner_addresses": ["Each owner's residential address as separate item — from TUAN PUNYA section"],
    "ownership_shares": "Share fraction if visible (e.g., '1/2 bahagian', '1/3 share'), empty if sole owner",
    "title_type_confidence": "high if title type is clearly visible, low if uncertain"
}}

Malaysian title types (normalize to these values):
- Geran = Final Title / Grant (Geran Mukim, GRN)
- Hakmilik = General ownership title (HAKMILIK)
- HSD = Hakmilik Sementara (Interim/Temporary Title)
- HSM = Hakmilik Strata Master (Strata Title for condos/apartments)
- Pajakan Negeri = State Lease / Leasehold (PN, PAJAKAN)

Return ONLY the JSON, no explanation."""
                }
            ]
        }]
    )

    response_text = message.content[0].text.strip()
    if response_text.startswith('```'):
        response_text = response_text.split('\n', 1)[1].rsplit('```', 1)[0].strip()

    try:
        result = json.loads(response_text)
        # Clean up lot_number: strip PTD/PT/LOT prefix — just the number
        if result.get('lot_number'):
            import re as _re
            result['lot_number'] = _re.sub(r'^(PTD|PT|LOT|NO\.?)\s*', '', result['lot_number'].strip(), flags=_re.IGNORECASE).strip()
        # Clean up title_number: strip prefix
        if result.get('title_number'):
            result['title_number'] = _re.sub(r'^(NO\.?|GERAN|HAKMILIK|HSD|HSM)\s*', '', result['title_number'].strip(), flags=_re.IGNORECASE).strip()
        # Clean up mukim/bandar_pekan: strip prefix
        if result.get('mukim'):
            result['mukim'] = _re.sub(r'^(MUKIM|BANDAR)\s+', '', result['mukim'].strip(), flags=_re.IGNORECASE).strip()
        if result.get('bandar_pekan'):
            result['bandar_pekan'] = _re.sub(r'^(MUKIM|BANDAR)\s+', '', result['bandar_pekan'].strip(), flags=_re.IGNORECASE).strip()
        if result.get('daerah'):
            result['daerah'] = _re.sub(r'^(DAERAH|DISTRICT\s+OF)\s+', '', result['daerah'].strip(), flags=_re.IGNORECASE).strip()
        return result
    except json.JSONDecodeError:
        return {"error": "Could not parse extraction results", "raw": response_text}
