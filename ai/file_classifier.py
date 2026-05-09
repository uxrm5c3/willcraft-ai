"""Classify an uploaded file so the chat pipeline knows which extractor to run.

This is a lightweight vision call — keep max_tokens small. The kinds are
chosen to match the existing folder categories under data/clients/{id}/documents/
plus a few extras the chat pipeline needs.
"""
import json
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_CHEAP
from ai.ocr import _make_content_block, _extract_json


KINDS = [
    'nric',            # Malaysian MyKad / passport
    # Property — six distinct kinds. ONLY property_title is evidence of
    # ownership and counts toward the will's gift list. The rest are
    # SUPPORTING context (tied to a property by address / lot) and get
    # clustered under the matching title in the chat.
    'property_title',    # Geran/Hakmilik/HSD/HSM/Pajakan Negeri/Strata Title — OWNERSHIP
    'property_spa',      # Sale & Purchase Agreement — contract, transfer pending
    'property_tax',      # Cukai Tanah / Cukai Pintu / quit rent / assessment
    'property_transfer', # Memorandum of Transfer (Borang 14A / Borang 16A) — NLC transfer form
    'utility_bill',      # TNB electric / Air Selangor / SAJ / Indah Water / unifi — ties to a property address
    'bank_letter',       # Letter from bank confirming account / loan statement (NOT a statement itself)
    'loan_agreement',    # Loan / mortgage / charge document — Perjanjian Pinjaman / Charge / Deed of Assignment
                         # Issued by bank (RHB, Maybank, CIMB, Public Bank, HLB, etc.) — proves property is encumbered
    # Financial assets
    'bank_statement',  # bank statement / passbook
    'insurance',       # insurance policy
    'epf_kwsp',        # EPF / KWSP statement
    'vehicle',         # JPJ vehicle grant / road tax
    'will',            # an existing Last Will and Testament
    # Clearly unrelated — wrong uploads, docs about other people, etc.
    'death_certificate',  # Sijil Kematian / Certificate of Death — for someone else; NOT a will asset
    'unrelated',          # Anything that clearly does not relate to this testator's assets
                          # (birth certs, marriage certs, court orders, IDs of deceased persons, etc.)
    'other',
]

# Asset kinds that can appear multiple times (different accounts / vehicles).
# For property, the "same asset" check uses lot/title overlap instead.
_MULTI_ASSET_KINDS = {'nric', 'bank_statement', 'vehicle', 'insurance', 'epf_kwsp'}


def classify_batch(file_paths: list, message_context: str = '') -> dict:
    """Analyse a batch of images TOGETHER to determine asset grouping BEFORE
    classifying each image individually.

    This is the most important first step for multi-image WhatsApp batches:
    a customer who sends 5 photos in one message almost always intends them
    as pages of the same document. We use:
      - The WhatsApp/email text (before/after the images) as primary evidence
      - Overlapping identifiers across images (same lot, same title, same account)
      - Visual similarity heuristics

    Returns a dict:
    {
        "groups": [
            {
                "image_indices": [0, 1, 3],   # 0-based, into file_paths
                "asset_kind": "property_title",
                "identifiers": {
                    "lot_number": "127082",
                    "title_number": "H.S.(D) 251041",
                    "mukim": "Plentong",
                    "daerah": "Johor Bahru",
                    "negeri": "Johor",
                    "property_address": "",
                    "bank_name": "",
                    "account_number": "",
                    "reg_number": ""
                },
                "summary": "5 pages of the same Geran for Lot 127082 Mukim Plentong",
                "beneficiary_hint": "give to daughter Sarah"  # from WhatsApp text
            },
            {
                "image_indices": [2],
                "asset_kind": "nric",
                "identifiers": {},
                "summary": "MyKad for testator",
                "beneficiary_hint": ""
            }
        ]
    }

    Falls back to {"groups": []} on error — caller processes images individually.
    Only called when there are 2+ non-audio images in the batch.
    """
    if not file_paths:
        return {"groups": []}

    # Limit to first 12 images — covers most real-world batches.
    paths_to_analyse = file_paths[:12]
    n = len(paths_to_analyse)

    # ── OCR all images first (free, Tesseract) ────────────────────────────────
    # Then send text-only to Haiku — avoids expensive image token cost.
    from ai.ocr_preprocessor import ocr_extract, regex_extract

    image_summaries = []
    for i, fp in enumerate(paths_to_analyse):
        raw = ocr_extract(fp)
        fields = regex_extract(raw)
        # Build a compact summary for each image
        parts = [f"**Image {i+1} of {n}**"]
        if fields['raw_text_len'] < 50:
            parts.append("(unreadable / blank)")
        else:
            if fields['doc_type']:
                parts.append(f"Likely type: {fields['doc_type']} "
                             f"(confidence {fields['confidence']:.0%})")
            if fields['lot_number']:
                parts.append(f"Lot: {fields['lot_number']}")
            if fields['title_number']:
                parts.append(f"Title: {fields['title_number']}")
            if fields['bank_name']:
                parts.append(f"Bank: {fields['bank_name']}")
            if fields['owner_name']:
                parts.append(f"Owner: {fields['owner_name']}")
            if fields['property_address']:
                parts.append(f"Address: {fields['property_address'][:80]}")
            if fields['mukim']:
                parts.append(f"Mukim: {fields['mukim']}")
            if fields['daerah']:
                parts.append(f"Daerah: {fields['daerah']}")
            # Include a short excerpt of the raw OCR for Haiku to reason over
            excerpt = raw[:300].replace('\n', ' ').strip()
            if excerpt:
                parts.append(f"OCR excerpt: {excerpt}")
        image_summaries.append(' | '.join(parts))

    images_block = '\n'.join(image_summaries)

    ctx_section = ''
    if message_context:
        ctx_section = (
            f"\n\n**Text sent with these images (WhatsApp/email message):**\n"
            f"```\n{message_context[:600]}\n```\n"
            f"This text is the strongest signal: if the client wrote 'this is my property lot 127082, "
            f"give to Sarah' before sending the images, all the photos very likely belong to that "
            f"one property. Also look for a beneficiary named in the text."
        )

    prompt = f"""You are analysing {n} documents sent together in one WhatsApp/email message to a Malaysian will-writing consultant.

The documents have been OCR-scanned. Here are the extracted text summaries:{ctx_section}

━━━ DOCUMENT SUMMARIES ━━━
{images_block}
━━━ END SUMMARIES ━━━



━━━ YOUR TASK ━━━
Group these images by PROPERTY / ASSET IDENTITY.

IMPORTANT — grouping is NOT about "pages of the same document".
A group = all documents that relate to the SAME PROPERTY or ASSET, regardless of document type.

Example of one group for a single property:
  - Image 2: Geran (title deed) for Lot 127082
  - Image 5: SPA (Sale & Purchase Agreement) for Lot 127082
  - Image 7: Cukai Tanah receipt for Lot 127082
  - Image 9: Bank offer letter for loan on Lot 127082
  → These all belong to ONE group: "Lot 127082"

Each image gets assigned to exactly ONE group. Create a separate group for:
  - Each distinct property (different lot number, different address, or clearly different building)
  - Each distinct financial asset (different bank account, different insurance policy, different vehicle)
  - Each IC / MyKad (one group per person)
  - Irrelevant or unrelated images (death cert, court orders, unrelated documents, images of unclear content) — put these in their own group with asset_kind = "other"

━━━ HOW TO IDENTIFY WHICH PROPERTY AN IMAGE BELONGS TO ━━━
Match images to the same property group if they share ANY of:
  - Same lot number (e.g. "Lot 127082" appears on multiple documents)
  - Same title/geran/HSD/HSM/PTD number
  - Same property address (even partially matching)
  - Same mukim + daerah combination
If none of these match, treat it as a separate asset or irrelevant.

━━━ DOCUMENT TYPES (asset_kind) ━━━
Use ONLY: nric / property_title / property_spa / property_tax / property_transfer / utility_bill / bank_letter / loan_agreement / bank_statement / insurance / epf_kwsp / vehicle / will / death_certificate / other

Notes on harder cases:
- SPA schedule or signing page (no heading visible): look for "Vendor/Purchaser/Penjual/Pembeli" + property reference → property_spa
- Loan signing or schedule page (no heading visible): look for "Borrower/Chargor/Peminjam" + bank name → loan_agreement
- Death certificate: "Sijil Kematian" / "Date of Death" / "Tarikh Kematian" → death_certificate, NOT part of any property group
- Blurry or unreadable image: assign to nearest matching group if lot/address visible, else → other

━━━ OUTPUT FORMAT ━━━
Return ONLY this JSON (no other text):
```json
{{
  "groups": [
    {{
      "image_indices": [2, 5, 7, 9],
      "asset_kind": "property_title",
      "identifiers": {{
        "lot_number": "127082",
        "title_number": "H.S.(D) 251041",
        "mukim": "Plentong",
        "daerah": "Johor Bahru",
        "negeri": "Johor",
        "property_address": "No 12 Jalan Plentong",
        "bank_name": "Maybank",
        "account_number": "",
        "reg_number": ""
      }},
      "summary": "Lot 127082, Mukim Plentong — Geran + SPA + Cukai Tanah + Maybank loan letter",
      "beneficiary_hint": "give to daughter Sarah"
    }},
    {{
      "image_indices": [3],
      "asset_kind": "death_certificate",
      "identifiers": {{}},
      "summary": "Death certificate — not related to any property",
      "beneficiary_hint": ""
    }}
  ]
}}
```"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=CLAUDE_MODEL_CHEAP,    # Haiku — text-only, no images
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
    except Exception as e:
        return {"groups": [], "_error": str(e)}

    try:
        from ai.cost_tracker import log_usage
        log_usage(msg, call_site='ai.file_classifier.classify_batch')
    except Exception:
        pass

    text = (msg.content[0].text or '').strip() if msg.content else ''
    js = _extract_json(text)
    if not js:
        return {"groups": []}
    try:
        result = json.loads(js)
    except json.JSONDecodeError:
        return {"groups": []}

    # Normalise: convert 1-based image_indices to 0-based, validate kinds
    groups = result.get('groups') or []
    normalised = []
    for g in groups:
        raw_indices = g.get('image_indices') or []
        # Accept 1-based (1..n) and convert; also accept 0-based (0..n-1)
        indices_0 = []
        for idx in raw_indices:
            if isinstance(idx, int):
                zero = idx - 1 if idx >= 1 else idx   # convert 1-based → 0-based
                if 0 <= zero < len(file_paths):
                    indices_0.append(zero)
        if not indices_0:
            continue
        kind = g.get('asset_kind', 'other')
        if kind not in KINDS:
            kind = 'other'
        idents = g.get('identifiers') or {}
        normalised.append({
            'image_indices': indices_0,
            'asset_kind': kind,
            'identifiers': {
                'lot_number':        (idents.get('lot_number') or '').strip(),
                'title_number':      (idents.get('title_number') or '').strip(),
                'mukim':             (idents.get('mukim') or '').strip(),
                'daerah':            (idents.get('daerah') or '').strip(),
                'negeri':            (idents.get('negeri') or '').strip(),
                'property_address':  (idents.get('property_address') or '').strip(),
                'bank_name':         (idents.get('bank_name') or '').strip(),
                'account_number':    (idents.get('account_number') or '').strip(),
                'reg_number':        (idents.get('reg_number') or '').strip(),
            },
            'summary':          (g.get('summary') or '').strip(),
            'beneficiary_hint': (g.get('beneficiary_hint') or '').strip(),
        })
    return {"groups": normalised}


def classify_file(file_path: str, group_context: dict = None,
                  testator_profile: dict = None) -> dict:
    """Classify a document image/PDF and extract key fields.

    Pipeline (cheapest-first, no Sonnet vision):
      1. Tesseract OCR  → raw text
      2. Regex extract  → doc_type + fields (free)
         confidence ≥ 0.70  →  return immediately (no AI call)
         confidence 0.30–0.69 → Haiku text classify (~$0.001)
         raw_text < 50 chars  → flag manual_review (no AI call)
      3. Testator match: compare owner_name / ic_number vs testator_profile

    Args:
        file_path:        absolute path to the file
        group_context:    dict from classify_batch() for this image's group
        testator_profile: {'name': str, 'ic': str} — testator identity for matching

    Returns dict with keys:
        kind, confidence, reason, purpose, property_hint, person_name,
        will_relevant, custom_type,
        lot_number, title_number, property_address, bank_name,
        mukim, daerah, negeri,
        owner_name, ic_number,
        name_match (bool|None), ic_match (bool|None),
        manual_review (bool)
    """
    from ai.ocr_preprocessor import ocr_extract, regex_extract

    fallback = {
        "kind": "other", "confidence": "low", "reason": "Could not classify",
        "manual_review": False, "will_relevant": True,
        "custom_type": "", "person_name": "", "purpose": "", "property_hint": "",
        "lot_number": "", "title_number": "", "property_address": "",
        "bank_name": "", "mukim": "", "daerah": "", "negeri": "",
        "owner_name": "", "ic_number": "", "name_match": None, "ic_match": None,
    }

    if not file_path or not __import__('os').path.isfile(file_path):
        return {**fallback, "reason": "File not found"}

    # ── Stage 1: OCR ─────────────────────────────────────────────────────────
    raw_text = ocr_extract(file_path)
    fields   = regex_extract(raw_text)

    # ── Stage 2a: Tesseract failed → fall back to VISION classify ────────────
    # 🔥 §10x.27 — Tesseract is blind to Malaysian IC holographic patterns,
    # photos taken at angle, low contrast title docs, etc. Returning
    # "Image unreadable" without ever asking Claude vision was burying real
    # ICs (LIM LAY CHENG case: clear IC photo → Tesseract <50 chars →
    # "unreadable" → never classified). Always try vision before giving up.
    if fields['raw_text_len'] < 50:
        vision_result = _vision_classify_fallback(file_path, group_context, testator_profile)
        if vision_result is not None:
            return vision_result
        # Vision also failed (network error, non-image file, etc) — only NOW
        # mark for manual review.
        return {
            **fallback,
            "kind": "other",
            "confidence": "low",
            "reason": "Image unreadable — manual review needed",
            "manual_review": True,
            "will_relevant": False,
        }

    # ── Stage 2b: High-confidence regex match → no AI needed ─────────────────
    if fields['confidence'] >= 0.70 and fields['doc_type']:
        result = _build_from_fields(fields, group_context, testator_profile)
        result = _maybe_vision_enrich_property(file_path, result)
        return result

    # ── Stage 2c: Has text but ambiguous → Haiku text-only classify (~$0.001) ─
    if fields['raw_text_len'] >= 50:
        result = _haiku_classify(raw_text, fields, group_context, testator_profile)
        result = _maybe_vision_enrich_property(file_path, result)
        return result

    return {**fallback, "manual_review": True,
            "reason": "Could not determine document type"}


def _maybe_vision_enrich_property(file_path: str, result: dict) -> dict:
    """🔥 §10x.52 — When the document is property-related but the field
    extraction is sparse (no street address, OR no lot/title), call
    vision_extract_property_fields to read directly from the image.

    The existing Tesseract+Haiku pipeline often misses critical fields
    on property docs because:
      • Title docs have street address only on cover page (small print)
      • SPA documents have address scattered across the body
      • Photos of title docs are angled/low-contrast for Tesseract
    Vision reads the image directly and fills in what's missing.

    Vision values NEVER overwrite existing non-empty fields — only fill
    empties — so trust order is: existing → vision.
    """
    if not isinstance(result, dict):
        return result
    kind = (result.get('kind') or '').strip()
    PROPERTY_KINDS = {'property_title', 'property_spa', 'property_tax',
                       'property_transfer', 'loan_agreement'}
    if kind not in PROPERTY_KINDS:
        return result
    # Sparse if street address empty OR (lot AND title both empty)
    has_address = bool((result.get('property_address') or '').strip())
    has_lot = bool((result.get('lot_number') or '').strip())
    has_title = bool((result.get('title_number') or '').strip())
    if has_address and (has_lot or has_title):
        return result   # already enough info
    try:
        vision_fields = vision_extract_property_fields(file_path)
    except Exception:
        return result
    if not vision_fields:
        return result
    # Fill empty fields only
    for k in ('property_address', 'lot_number', 'title_number',
              'mukim', 'daerah', 'negeri', 'owner_name', 'building_name',
              'postcode'):
        if not (result.get(k) or '').strip() and vision_fields.get(k):
            result[k] = vision_fields[k]
    result['_vision_enriched'] = True
    return result


# ---------------------------------------------------------------------------
# Internal helpers for classify_file
# ---------------------------------------------------------------------------

def _build_from_fields(fields: dict, group_context: dict = None,
                       testator_profile: dict = None) -> dict:
    """Build a classify_file result dict directly from regex-extracted fields."""
    kind = fields['doc_type']
    if kind not in KINDS:
        kind = 'other'

    conf_float = fields['confidence']
    conf_label = 'high' if conf_float >= 0.85 else 'medium'

    # Property hint: prefer lot number, fall back to address
    prop_hint = fields.get('lot_number') or fields.get('property_address') or ''

    # Batch group enrichment: fill missing fields from group identifiers
    grp_idents = (group_context or {}).get('identifiers') or {}
    for field in ('lot_number', 'title_number', 'mukim', 'daerah', 'negeri',
                  'property_address', 'bank_name'):
        if not fields.get(field) and grp_idents.get(field):
            fields[field] = grp_idents[field]

    will_rel = kind not in ('death_certificate', 'unrelated')
    if kind == 'other':
        will_rel = False

    result = {
        "kind":             kind,
        "confidence":       conf_label,
        "reason":           f"Identified by OCR keyword match (confidence {conf_float:.0%})",
        "manual_review":    False,
        "will_relevant":    will_rel,
        "custom_type":      '',
        "person_name":      fields.get('owner_name') or fields.get('ic_number') or '',
        "purpose":          _kind_purpose(kind),
        "property_hint":    prop_hint[:200],
        "lot_number":       fields.get('lot_number', ''),
        "title_number":     fields.get('title_number', ''),
        "property_address": fields.get('property_address', ''),
        "bank_name":        fields.get('bank_name', ''),
        "mukim":            fields.get('mukim', ''),
        "daerah":           fields.get('daerah', ''),
        "negeri":           fields.get('negeri', ''),
        "owner_name":       fields.get('owner_name', ''),
        "ic_number":        fields.get('ic_number', ''),
    }

    result.update(_testator_match(fields, testator_profile))
    return result


def _haiku_classify(raw_text: str, fields: dict, group_context: dict = None,
                    testator_profile: dict = None) -> dict:
    """Call Claude Haiku with OCR text only (no image). ~$0.001 per call."""
    fallback = {
        "kind": "other", "confidence": "low", "manual_review": False,
        "will_relevant": True, "custom_type": "", "person_name": "",
        "purpose": "", "property_hint": "",
        "lot_number": fields.get('lot_number', ''),
        "title_number": fields.get('title_number', ''),
        "property_address": fields.get('property_address', ''),
        "bank_name": fields.get('bank_name', ''),
        "mukim": fields.get('mukim', ''),
        "daerah": fields.get('daerah', ''),
        "negeri": fields.get('negeri', ''),
        "owner_name": fields.get('owner_name', ''),
        "ic_number": fields.get('ic_number', ''),
        "name_match": None, "ic_match": None,
        "reason": "Haiku classification failed — fallback",
    }

    group_section = ''
    if group_context:
        kind_lbl = group_context.get('asset_kind', '')
        summary  = group_context.get('summary', '')
        grp_idents = group_context.get('identifiers') or {}
        ident_str = ', '.join(f"{k}: {v}" for k, v in grp_idents.items() if v)
        group_section = (
            f"\n\nBATCH GROUP CONTEXT: this document is part of a group identified as "
            f"'{kind_lbl}' — {summary}"
            + (f"\nKnown identifiers: {ident_str}" if ident_str else '')
            + "\nUse this as strong prior if consistent with the OCR text."
        )

    # Pre-filled fields hint (regex found something, just not confident enough)
    hints = []
    for k in ('lot_number', 'ic_number', 'bank_name', 'owner_name'):
        if fields.get(k):
            hints.append(f"{k}: {fields[k]}")
    hints_section = ('\n\nPartially extracted fields (regex, may be inaccurate):\n'
                     + '\n'.join(hints)) if hints else ''

    prompt = f"""You are a Malaysian legal document classifier. You have been given OCR-extracted text from a document image. Classify the document and extract key fields.{group_section}{hints_section}

━━━ OCR TEXT (first 1200 chars) ━━━
{raw_text[:1200]}
━━━ END OCR TEXT ━━━

Classify into ONE of these categories:
nric / property_title / property_spa / property_tax / property_transfer /
utility_bill / bank_letter / loan_agreement / bank_statement / insurance /
epf_kwsp / vehicle / will / death_certificate / unrelated / other

Rules:
- GERAN / HAKMILIK / TUAN PUNYA BERDAFTAR → property_title
- PERJANJIAN JUAL BELI / VENDOR / PURCHASER / PENJUAL / PEMBELI → property_spa
- FACILITY AGREEMENT / BORROWER / CHARGOR / PEMINJAM / BEBANAN → loan_agreement
- CUKAI TANAH / CUKAI HARTA / QUIT RENT → property_tax
- SIJIL KEMATIAN / DATE OF DEATH / TARIKH KEMATIAN → death_certificate (will_relevant=false)
- Interior/signing pages with no heading: use party labels (Borrower→loan, Vendor→spa)
- TNB / AIR SELANGOR / SAJ / utility company → utility_bill (good for address)
- Only classify as "will" if WASIAT or LAST WILL AND TESTAMENT + executor structure visible
- Use "other" + custom_type if none fit

Return ONLY valid JSON:
```json
{{
  "kind": "<category>",
  "custom_type": "<actual document title if not a standard category, else empty>",
  "confidence": "high|medium|low",
  "reason": "<one sentence: what keywords/phrases led to this classification>",
  "purpose": "<what this document proves, max 15 words>",
  "property_hint": "<lot number or address if visible, else empty>",
  "person_name": "<primary person named — owner, borrower, deceased — or empty>",
  "lot_number": "<PTD/HSD/Lot number or empty>",
  "title_number": "<title/geran reference number or empty>",
  "property_address": "<street address if visible, else empty>",
  "bank_name": "<bank name if visible, else empty>",
  "mukim": "<mukim if visible, else empty>",
  "daerah": "<daerah if visible, else empty>",
  "negeri": "<state if visible, else empty>",
  "owner_name": "<registered owner / borrower full name if visible, else empty>",
  "ic_number": "<IC number formatted as XXXXXX-XX-XXXX if visible, else empty>",
  "will_relevant": true
}}
```"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=CLAUDE_MODEL_CHEAP,    # Haiku — text only, cheap
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
    except Exception as e:
        return {**fallback, "reason": f"Haiku API error: {e}"}

    try:
        from ai.cost_tracker import log_usage
        log_usage(msg, call_site='ai.file_classifier.classify_file.haiku')
    except Exception:
        pass

    text_out = (msg.content[0].text or '').strip() if msg.content else ''
    js = _extract_json(text_out)
    if not js:
        return fallback
    try:
        res = json.loads(js)
    except json.JSONDecodeError:
        return fallback

    kind = res.get('kind', 'other')
    if kind not in KINDS:
        kind = 'other'

    will_rel = res.get('will_relevant', True)
    if kind in ('death_certificate', 'unrelated'):
        will_rel = False

    result = {
        "kind":             kind,
        "confidence":       res.get('confidence', 'low'),
        "reason":           (res.get('reason') or '').strip()[:300],
        "manual_review":    False,
        "will_relevant":    will_rel,
        "custom_type":      (res.get('custom_type') or '').strip(),
        "person_name":      (res.get('person_name') or res.get('owner_name') or '').strip(),
        "purpose":          (res.get('purpose') or '').strip()[:200],
        "property_hint":    (res.get('property_hint') or res.get('lot_number') or
                             res.get('property_address') or '').strip()[:200],
        "lot_number":       (res.get('lot_number') or fields.get('lot_number') or '').strip(),
        "title_number":     (res.get('title_number') or fields.get('title_number') or '').strip(),
        "property_address": (res.get('property_address') or fields.get('property_address') or '').strip(),
        "bank_name":        (res.get('bank_name') or fields.get('bank_name') or '').strip(),
        "mukim":            (res.get('mukim') or fields.get('mukim') or '').strip(),
        "daerah":           (res.get('daerah') or fields.get('daerah') or '').strip(),
        "negeri":           (res.get('negeri') or fields.get('negeri') or '').strip(),
        "owner_name":       (res.get('owner_name') or fields.get('owner_name') or '').strip(),
        "ic_number":        (res.get('ic_number') or fields.get('ic_number') or '').strip(),
    }
    result.update(_testator_match(result, testator_profile))
    return result


def _vision_classify_fallback(file_path: str, group_context: dict = None,
                               testator_profile: dict = None) -> dict:
    """🔥 §10x.27 — Vision fallback when Tesseract fails (<50 chars).

    Tesseract is blind to:
      • Malaysian IC holographic security patterns (LIM LAY CHENG case)
      • Photos at angle / low contrast title docs
      • Strata title plans with embossed text
      • Old typewritten documents

    When that happens, ask Claude vision (Haiku) "what kind of document is
    this?" with the same KINDS list. ~$0.005 per call — much cheaper than
    full extraction, and only fires when Tesseract failed.

    Returns a classify_file-shape dict, or None if vision also failed.
    """
    import os as _os
    if not file_path or not _os.path.isfile(file_path):
        return None
    ext = file_path.rsplit('.', 1)[-1].lower()
    if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'pdf', 'heic', 'heif',
                    'bmp', 'tiff', 'tif'):
        return None   # not an image — vision can't help

    # 🔥 §10x.73 — one-shot vision. When UNIFIED_VISION=1 the unified pass
    # already classifies AND extracts — no need for a separate
    # classify-only fallback call. Adapt that result and return.
    if _os.environ.get('UNIFIED_VISION', '').strip() == '1':
        try:
            from ai.unified_vision import extract_all, as_classify_result
            unified = extract_all(file_path,
                                   call_site='ai.file_classifier.fallback')
            if not unified.get('_disabled_by_kill_switch'):
                return as_classify_result(unified)
        except Exception:
            pass  # fall through to legacy classify-only call

    try:
        from config import CLAUDE_MODEL_FAST as _MODEL
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        content_block = _make_content_block(file_path)
        kinds_list = ', '.join(KINDS)
        prompt = (
            "Look at this image. What single category does it best match from "
            f"this list: {kinds_list}.\n\n"
            "Categories explained briefly:\n"
            "- nric: Malaysian MyKad (identity card) — has 12-digit number, "
            "photo, holographic 'KAD PENGENALAN MALAYSIA' or 'MyKad'\n"
            "- property_title: Geran/Hakmilik/HSD/HSM/Pajakan/Strata title — "
            "ownership document\n"
            "- property_spa: Sale & Purchase Agreement\n"
            "- property_tax: Cukai Tanah / Cukai Pintu / quit rent\n"
            "- property_transfer: Memorandum of Transfer (Borang 14A/16A)\n"
            "- loan_agreement: bank loan/mortgage/charge document\n"
            "- bank_statement / insurance / epf_kwsp / vehicle / will\n"
            "- death_certificate / unrelated / other\n\n"
            "Output ONLY this JSON:\n"
            '{"kind":"<one of the categories>",'
            '"confidence":"high|medium|low",'
            '"reason":"<short reason e.g. \'Sees MyKad with NRIC and photo\'>"}'
        )
        msg = client.messages.create(
            model=_MODEL, max_tokens=400,
            messages=[{"role": "user", "content": [
                content_block,
                {"type": "text", "text": prompt}]}]
        )
        try:
            from ai.cost_tracker import log_usage
            log_usage(msg, call_site='ai.file_classifier._vision_classify_fallback')
        except Exception:
            pass
        text = msg.content[0].text if msg.content else ''
        # Don't use _extract_json — it filters for IC-specific keys
        # (full_name/nric_number/doc_type) and would drop our {"kind":...}
        # output. Parse directly.
        import re as _re
        m = _re.search(r'\{[^{}]*\}', text, _re.DOTALL)
        try:
            parsed = json.loads(m.group(0)) if m else {}
        except Exception:
            parsed = {}
        kind = (parsed.get('kind') or 'other').strip().lower()
        if kind not in KINDS:
            kind = 'other'
        conf = (parsed.get('confidence') or 'medium').strip().lower()
        reason = (parsed.get('reason') or 'Identified by vision fallback')[:200]
        return {
            "kind": kind,
            "confidence": conf,
            "reason": reason,
            "manual_review": False,
            "will_relevant": kind not in ('death_certificate', 'unrelated'),
            "custom_type": "",
            "person_name": "",
            "purpose": _kind_purpose(kind),
            "property_hint": "",
            "lot_number": "", "title_number": "", "property_address": "",
            "bank_name": "", "mukim": "", "daerah": "", "negeri": "",
            "owner_name": "", "ic_number": "",
            "name_match": None, "ic_match": None,
            "_via_vision_fallback": True,
        }
    except Exception:
        return None


def vision_extract_property_fields(file_path: str) -> dict:
    # 🔥 §10x.65 EMERGENCY KILL SWITCH
    import os as _os
    if _os.environ.get('DISABLE_VISION_CALLS', '').strip() == '1':
        return {}
    # 🔥 §10x.73 — One-shot vision: when UNIFIED_VISION=1, the unified pass
    # (cached by content_hash) already extracted ALL property fields. No
    # need for a second vision call here. The cache will return the prior
    # result without re-paying. This was the missing 5th shim from the
    # initial §10x.73 refactor — adding it eliminates ~$0.07 per 28-image
    # KOID run.
    if _os.environ.get('UNIFIED_VISION', '').strip() == '1':
        try:
            from ai.unified_vision import extract_all, as_property_result
            unified = extract_all(file_path,
                                   call_site='ai.file_classifier.vision_extract_property_fields')
            if not unified.get('_disabled_by_kill_switch'):
                # Return the same shape vision_extract_property_fields
                # historically returned, so callers see no diff.
                return as_property_result(unified)
        except Exception:
            pass  # fall through to legacy
    # 🔥 §10x.67 — DB-backed cache. Same image, same fields, $0 second time.
    try:
        from services.vision_cache import cached_vision
        return cached_vision(
            file_path=file_path,
            call_kind='vision_extract_fields',
            fn=lambda: _vision_extract_property_fields_impl(file_path),
        )
    except Exception:
        return _vision_extract_property_fields_impl(file_path)


def _vision_extract_property_fields_impl(file_path: str) -> dict:
    """🔥 §10x.52 — Vision-based property field extractor.

    Tesseract OCR routinely fails to extract critical property fields:
      • Street address (Jalan X, Taman Y) embedded in property docs
      • PTD/HSD numbers in low-contrast or rotated images
      • Mukim/Daerah/Negeri buried in structured layouts

    This sends the image directly to Claude vision with a strict prompt
    asking for ONLY the probate-relevant fields. Returns dict with same
    shape as classify_file's extracted fields. Caller merges over any
    Tesseract-extracted values (vision wins for empty fields).

    Returns {} on any error — caller falls back to existing fields.
    """
    import os as _os
    if not file_path or not _os.path.isfile(file_path):
        return {}
    ext = file_path.rsplit('.', 1)[-1].lower()
    if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'pdf', 'heic', 'heif',
                    'bmp', 'tiff', 'tif'):
        return {}
    try:
        from config import CLAUDE_MODEL_FAST as _MODEL
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        content_block = _make_content_block(file_path)
    except Exception:
        return {}

    prompt = (
        "Look at this Malaysian property document image and extract the "
        "probate-relevant fields. Read EVERY visible address line, lot "
        "number, title number, mukim, daerah, owner name from the image.\n\n"
        "HARD RULES:\n"
        "1. Read directly from the image — do NOT use general knowledge.\n"
        "2. If a field is not visible in the image, return empty string.\n"
        "3. The street address is the postal address line(s) — usually has "
        "'Jalan', 'Lorong', 'Taman', or a numbered street. NOT the legal "
        "Mukim/Daerah designation.\n"
        "4. Lot number: PTD/Lot/HSD/HSM/H.S.(D)/Hakmilik number. Strip any "
        "'VALUE:' / 'LOT' prefix the OCR may have added.\n"
        "5. Title number: Geran/Hakmilik/strata title reference.\n"
        "6. Mukim: official NLC mukim only (e.g. 'Plentong', 'Pulai', "
        "'Tebrau', 'Bandar Johor Bahru'). Strip 'Mukim ' prefix.\n"
        "7. Owner name: full registered owner name(s), uppercase.\n\n"
        "Return ONLY this JSON (no preamble, no markdown):\n"
        '{\n'
        '  "property_address": "<street address visible on doc, or empty>",\n'
        '  "lot_number": "<lot/PTD/HSD number, or empty>",\n'
        '  "title_number": "<geran/hakmilik/strata title number, or empty>",\n'
        '  "mukim": "<mukim, or empty>",\n'
        '  "daerah": "<daerah, or empty>",\n'
        '  "negeri": "<negeri/state, or empty>",\n'
        '  "owner_name": "<owner name(s), or empty>",\n'
        '  "building_name": "<for strata/condo only, or empty>",\n'
        '  "postcode": "<5-digit postcode if visible, or empty>",\n'
        '  "confidence": "high|medium|low"\n'
        '}'
    )
    try:
        msg = client.messages.create(
            model=_MODEL, max_tokens=600,
            messages=[{"role": "user", "content": [
                content_block,
                {"type": "text", "text": prompt}]}]
        )
        try:
            from ai.cost_tracker import log_usage
            log_usage(msg, call_site='ai.file_classifier.vision_extract_property_fields')
        except Exception:
            pass
        text = msg.content[0].text if msg.content else ''
        import re as _re
        m = _re.search(r'\{[\s\S]*\}', text)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except Exception:
            return {}
        # Sanitise
        out = {}
        for k in ('property_address', 'lot_number', 'title_number', 'mukim',
                  'daerah', 'negeri', 'owner_name', 'building_name', 'postcode'):
            v = (data.get(k) or '').strip()
            # Reject AI-noise tokens per §10aa
            if v and not _re.search(r'(?i)(unreadable|cannot\s+read|not\s+visible|n/?a)', v):
                # Strip §10aa prefixes
                v = _re.sub(r'^(?:VALUE\s*[:\-]?\s*|LOT\s+|PTD\s+|TITLE\s+|GERAN\s+|HAKMILIK\s+|HSD\s+|HSM\s+)',
                            '', v, flags=_re.IGNORECASE).strip()
                v = _re.sub(r'\([^)]*\)', '', v).strip()
                if v:
                    out[k] = v[:200]
        return out
    except Exception:
        return {}


def _testator_match(fields: dict, testator_profile: dict = None) -> dict:
    """Compare extracted owner_name / ic_number against the testator profile.

    Returns {'name_match': bool|None, 'ic_match': bool|None}
    None = could not compare (field missing on one side).
    """
    out = {"name_match": None, "ic_match": None}
    if not testator_profile:
        return out

    # IC match — exact after stripping dashes/spaces
    t_ic = (testator_profile.get('ic') or '').replace('-', '').replace(' ', '')
    f_ic = (fields.get('ic_number') or '').replace('-', '').replace(' ', '')
    if t_ic and f_ic:
        out['ic_match'] = (t_ic == f_ic)

    # Name match — fuzzy (SequenceMatcher ratio > 0.70)
    t_name = (testator_profile.get('name') or '').upper().strip()
    f_name = (fields.get('owner_name') or '').upper().strip()
    if t_name and f_name:
        from difflib import SequenceMatcher
        ratio = SequenceMatcher(None, t_name, f_name).ratio()
        out['name_match'] = ratio >= 0.70

    return out


def _kind_purpose(kind: str) -> str:
    """Short human-readable purpose string for a document kind."""
    return {
        'property_title':    'Proves property ownership',
        'property_spa':      'Sale & Purchase Agreement',
        'property_tax':      'Property tax / quit rent record',
        'property_transfer': 'Transfer of ownership form',
        'utility_bill':      'Confirms property address',
        'loan_agreement':    'Loan / charge on property',
        'bank_letter':       'Bank correspondence',
        'bank_statement':    'Bank account statement',
        'insurance':         'Insurance / takaful policy',
        'epf_kwsp':          'EPF / KWSP savings',
        'vehicle':           'Vehicle registration',
        'will':              'Existing will document',
        'nric':              'Identity document',
        'death_certificate': 'Death certificate (not an asset)',
        'unrelated':         'Unrelated document',
        'valuation':         'Property valuation report',
    }.get(kind, '')
