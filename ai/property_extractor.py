"""Property document extraction using Claude Vision API."""
import base64
import json
import os
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_FAST


# 🔥 §10x.56 — per-process cache so the watchdog re-firing doesn't pay
# $0.011 × 30× per doc. Keyed by file_path; shared across callers in the
# same process. DB-level idempotency lives at the callsite (Document.
# extracted_data['_property_extracted_done']) — this in-memory cache
# only protects against same-process duplicate calls.
_EXTRACT_CACHE: dict = {}


def extract_property_data(file_path: str, doc_type: str = 'general') -> dict:
    """Extract property data from cukai tanah/cukai pintu, title document, or SPA.

    Args:
        file_path: Path to the document image/PDF
        doc_type: One of 'title', 'cukai_harta', 'cukai_pintu', 'spa', 'general'

    Returns dict with keys: property_address, title_type, lot_number,
    title_number, bandar_pekan, mukim, daerah, negeri, property_description
    """
    # 🔥 §10x.56 — per-process cache. The same file_path getting extracted
    # 30× during a single chat session burned $0.30 alone for ONE doc.
    # Cache key includes doc_type so re-extraction with a different doc_type
    # hint still works. Cache lives until process restart.
    cache_key = (file_path, doc_type)
    if cache_key in _EXTRACT_CACHE:
        cached = _EXTRACT_CACHE[cache_key]
        cached['_from_cache'] = True
        return cached

    if not file_path or not os.path.isfile(file_path):
        return {}

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
    "encumbrance": "Any charge/mortgage or caveat visible in BEBANAN / ENDORSAN LAIN section (e.g., 'Charge to Maybank', 'Private caveat by ...'), empty if none",
    "encumbrance_type": "charge (bank mortgage/loan), caveat (private caveat/caveatan), or empty if none",
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

    try:
        from ai.cost_tracker import log_usage
        log_usage(message, call_site='ai.property_extractor.extract_property_data')
    except Exception:
        pass
    response_text = message.content[0].text.strip()
    if response_text.startswith('```'):
        response_text = response_text.split('\n', 1)[1].rsplit('```', 1)[0].strip()

    try:
        result = json.loads(response_text)
        # Clean up lot_number: strip PTD/PT/LOT prefix — just the number
        if result.get('lot_number'):
            import re as _re
            result['lot_number'] = _re.sub(r'^(PTD|PT|LOT|NO\.?)\s*', '', result['lot_number'].strip(), flags=_re.IGNORECASE).strip()
        # Clean up title_number: strip prefix AND infer title_type from prefix if empty
        if result.get('title_number'):
            tn = result['title_number'].strip()
            # Detect title type from prefix if title_type is empty
            if not result.get('title_type'):
                tn_upper = tn.upper()
                if tn_upper.startswith('HS(D)') or tn_upper.startswith('HSD'):
                    result['title_type'] = 'HSD'
                    result['title_type_confidence'] = 'high'
                elif tn_upper.startswith('HS(M)') or tn_upper.startswith('HSM'):
                    result['title_type'] = 'HSM'
                    result['title_type_confidence'] = 'high'
                elif tn_upper.startswith('GERAN') or tn_upper.startswith('GRN') or tn_upper.startswith('GM'):
                    result['title_type'] = 'Geran'
                    result['title_type_confidence'] = 'high'
                elif tn_upper.startswith('HAKMILIK'):
                    result['title_type'] = 'Hakmilik'
                    result['title_type_confidence'] = 'high'
                elif tn_upper.startswith('PN') or tn_upper.startswith('PAJAKAN'):
                    result['title_type'] = 'Pajakan Negeri'
                    result['title_type_confidence'] = 'high'
            result['title_number'] = _re.sub(r'^(NO\.?|HS\(D\)|HS\(M\)|HSD|HSM|GERAN|GRN|GM|HAKMILIK|PAJAKAN\s*NEGERI|PN)\s*\.?\s*(NO\.?)?\s*', '', tn, flags=_re.IGNORECASE).strip()
        # Clean up mukim/bandar_pekan: strip prefix
        if result.get('mukim'):
            result['mukim'] = _re.sub(r'^(MUKIM|BANDAR)\s+', '', result['mukim'].strip(), flags=_re.IGNORECASE).strip()
        if result.get('bandar_pekan'):
            result['bandar_pekan'] = _re.sub(r'^(MUKIM|BANDAR)\s+', '', result['bandar_pekan'].strip(), flags=_re.IGNORECASE).strip()
        if result.get('daerah'):
            result['daerah'] = _re.sub(r'^(DAERAH|DISTRICT\s+OF)\s+', '', result['daerah'].strip(), flags=_re.IGNORECASE).strip()
        # Remove owner_addresses — not needed in frontend
        result.pop('owner_addresses', None)
        # Ensure owner_names is always an array
        if isinstance(result.get('owner_names'), str):
            # Split comma-separated string into array
            result['owner_names'] = [n.strip() for n in result['owner_names'].split(',') if n.strip()]
        # Remove bandar_pekan — not needed for probate/land search
        # Keep mukim only (mukim is what matters for land office)
        if result.get('bandar_pekan') and not result.get('mukim'):
            result['mukim'] = result['bandar_pekan']
        result.pop('bandar_pekan', None)
        # Selective re-OCR: if title_number or lot_number looks blank or
        # garbled, run a tight digit-by-digit re-read on the same image.
        try:
            from ai.ocr import reocr_if_suspicious
            result = reocr_if_suspicious(file_path, result)
        except Exception:
            pass
        # Address verification via OpenStreetMap Nominatim (free, no key).
        # Only run when we have at least daerah+negeri so there's something
        # meaningful to geocode.  Wrapped in try/except so a network hiccup
        # never blocks the OCR result from saving.
        _addr  = (result.get('property_address') or '').strip()
        _mukim = (result.get('mukim') or '').strip()
        _daer  = (result.get('daerah') or '').strip()
        _neg   = (result.get('negeri') or '').strip()
        if (_daer or _neg) and result.get('document_type') in ('title', 'cukai_harta', 'cukai_pintu', 'spa', None, ''):
            try:
                from ai.address_verifier import verify_property_address
                _vr = verify_property_address(_addr, _mukim, _daer, _neg)
                result['address_verified']  = _vr.get('verified')   # True/False/None
                result['address_level']     = _vr.get('level', '')  # 'street'/'district'/'none'
                result['address_canonical'] = _vr.get('canonical', '')
                result['address_note']      = _vr.get('note', '')
            except Exception:
                pass
        # 🔥 §10x.56 — cache the result for this (file_path, doc_type) so
        # the same upload doesn't get re-extracted by the watchdog.
        _EXTRACT_CACHE[cache_key] = result
        return result
    except json.JSONDecodeError:
        err = {"error": "Could not parse extraction results", "raw": response_text}
        # Cache the failure too so we don't retry forever
        _EXTRACT_CACHE[cache_key] = err
        return err
