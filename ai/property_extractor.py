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
Document type: {doc_type}

This could be one of:
- Land Title (Hakmilik Tanah) / Geran - look for No. Hakmilik, No. Lot, Mukim, Daerah, Negeri
- Cukai Harta (Property Assessment Tax / Cukai Taksiran) - look for property address, assessment number
- Cukai Pintu (Door Tax) - look for property address
- Sale & Purchase Agreement (SPA) - look for property details in the schedule

Return ONLY a JSON object with these fields (use empty string if not found):
{{
    "property_address": "Full property address including number, street, area, city, state",
    "title_type": "HAKMILIK, HSD, HSM, GRN, EMR, PM, PN, PAJAKAN, GERAN or other title type",
    "lot_number": "Lot/PT number only (digits)",
    "title_number": "Title/Hakmilik number only (digits)",
    "bandar_pekan": "Bandar/Pekan/Township name",
    "mukim": "Mukim name",
    "daerah": "Daerah/District name",
    "negeri": "State name (e.g., JOHOR, SELANGOR, PERAK)",
    "property_description": "Brief description (residential, commercial, agricultural)"
}}

Malaysian title types:
- HAKMILIK = General ownership title
- HSD = Hakmilik Sementara Daerah (Interim Title - District)
- HSM = Hakmilik Sementara Mukim (Interim Title - Mukim)
- GRN = Geran (Final Title - Freehold)
- EMR = Pajakan Mukim (Leasehold)

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
