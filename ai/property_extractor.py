"""Property document extraction using Claude Vision API."""
import base64
import json
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL


def extract_property_data(file_path: str) -> dict:
    """Extract property data from cukai tanah/cukai pintu or title document.

    Returns dict with keys: property_address, title_type, lot_number,
    title_number, mukim, daerah, negeri, property_description
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
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                content_block,
                {
                    "type": "text",
                    "text": """Extract property information from this Malaysian property document (cukai tanah, cukai pintu, land title, or property assessment).

Return ONLY a JSON object with these fields (use empty string if not found):
{
    "property_address": "Full property address",
    "title_type": "HSD, HSM, GRN, EMR, PM, PN, or other title type",
    "lot_number": "Lot/PT number",
    "title_number": "Title number/reference",
    "mukim": "Mukim name",
    "daerah": "Daerah/District name",
    "negeri": "State name",
    "property_description": "Brief description of the property type (e.g., residential, commercial, agricultural)"
}

For Malaysian property documents:
- HSD = Hakmilik Sementara Daerah (Interim Title - District)
- HSM = Hakmilik Sementara Mukim (Interim Title - Mukim)
- GRN = Geran (Final Title - Freehold)
- EMR = Pajakan Mukim (Leasehold)
- Look for "No. Hakmilik", "No. Lot", "Mukim", "Daerah", "Negeri"

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
