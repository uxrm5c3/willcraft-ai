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

    # Limit to first 8 images — sufficient for any normal document batch;
    # beyond that we'd hit API limits and diminishing returns.
    paths_to_analyse = file_paths[:8]
    n = len(paths_to_analyse)

    content_blocks = []
    for i, fp in enumerate(paths_to_analyse):
        try:
            cb = _make_content_block(fp)
            content_blocks.append({"type": "text", "text": f"**Image {i+1} of {n}:**"})
            content_blocks.append(cb)
        except Exception:
            content_blocks.append({"type": "text",
                                    "text": f"**Image {i+1} of {n}:** (could not load)"})

    ctx_section = ''
    if message_context:
        ctx_section = (
            f"\n\n**Text sent with these images (WhatsApp/email message):**\n"
            f"```\n{message_context[:600]}\n```\n"
            f"This text is the strongest signal: if the client wrote 'this is my property lot 127082, "
            f"give to Sarah' before sending the images, all the photos very likely belong to that "
            f"one property. Also look for a beneficiary named in the text."
        )

    prompt = f"""You are analysing {n} images sent together in one WhatsApp/email message to a will-writing consultant.{ctx_section}

Your task: group the images by ASSET. Images are the same asset if they are pages of the same document (e.g. front + back of a geran, multiple pages of an SPA) OR if they visually overlap in content (same lot number, same title number, same account number).

The client's text message (above) is the STRONGEST clue — if they described one property/asset and then sent all the images, treat them as the same asset unless you have clear visual evidence of a different document.

For EACH group, identify:
- Which image numbers belong to it (1-based)
- The asset kind (use ONLY: nric / property_title / property_spa / property_tax / property_transfer / utility_bill / bank_letter / bank_statement / insurance / epf_kwsp / vehicle / will / other)
- Key identifiers visible across the group: lot_number, title_number, mukim, daerah, negeri, property_address, bank_name, account_number, reg_number (leave blank if not found)
- A short summary of what this group represents
- Any beneficiary mentioned in the client's text for this asset (e.g. "give to daughter")

Return ONLY this JSON (no other text):
```json
{{
  "groups": [
    {{
      "image_indices": [1, 2, 3, 4, 5],
      "asset_kind": "property_title",
      "identifiers": {{
        "lot_number": "127082",
        "title_number": "H.S.(D) 251041",
        "mukim": "Plentong",
        "daerah": "Johor Bahru",
        "negeri": "Johor",
        "property_address": "",
        "bank_name": "",
        "account_number": "",
        "reg_number": ""
      }},
      "summary": "5 pages of same Geran for Lot 127082 Mukim Plentong",
      "beneficiary_hint": "give to daughter Sarah"
    }}
  ]
}}
```"""

    content_blocks.append({"type": "text", "text": prompt})

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=CLAUDE_MODEL_CHEAP,
            max_tokens=600,
            messages=[{"role": "user", "content": content_blocks}]
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


def classify_file(file_path: str, group_context: dict = None) -> dict:
    """Return {kind, confidence, reason}. Falls back to 'other' on any error.

    group_context: optional dict from classify_batch() for this image's group.
    Injected into the prompt so the classifier knows upfront what asset this
    image belongs to (e.g. "This image is page 3 of 5 for a Geran, Lot 127082").

    Cost telemetry: if the caller wraps this in `cost_tracker.track_context(...)`,
    each Anthropic call is logged to ApiCallLog with client_id/will_id/user_id
    auto-attached. No-op outside a tracked context.
    """
    fallback = {"kind": "other", "confidence": "low", "reason": "Could not classify"}
    try:
        client_api = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        content_block = _make_content_block(file_path)
    except Exception as e:
        return {**fallback, "reason": f"Could not open file: {e}"}

    group_section = ''
    if group_context:
        kind_lbl = group_context.get('asset_kind', '')
        summary  = group_context.get('summary', '')
        idents   = group_context.get('identifiers') or {}
        ident_parts = [f"{k}: {v}" for k, v in idents.items() if v]
        ident_str = ', '.join(ident_parts) if ident_parts else ''
        n_images  = len(group_context.get('image_indices', []))
        group_section = (
            f"\n\n**BATCH GROUP CONTEXT (established by analysing all images together):**\n"
            f"This image is one of {n_images} image(s) in a group identified as: "
            f"`{kind_lbl}` — {summary}"
            + (f"\nKnown identifiers: {ident_str}" if ident_str else '') +
            f"\n\nUse this as strong prior context. Classify consistently with the group "
            f"unless you have clear visual evidence this image is a DIFFERENT document type."
        )

    try:
        from config import CLAUDE_MODEL_FAST
        msg = client_api.messages.create(
            model=CLAUDE_MODEL_FAST,   # Use sonnet — haiku misses document headings
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": f"""You are a Malaysian legal document expert. Look carefully at this image — read ALL visible headings, logos, bank names, form numbers, and text to classify it precisely.{group_section}

**STEP 1 — What do you see?** Read every heading, logo, form number, and key phrase visible.
**STEP 2 — Match to category below.**

CATEGORIES (pick the SINGLE best match):

**nric** — Malaysian MyKad IC (front: photo + IC number like 700101-01-1234, back: thumbprint grid) OR Malaysian passport. IC number format: ######-##-####.

**property_title** — Official LAND TITLE issued by Pejabat Tanah. Header says "HAKMILIK", "GERAN", "INDIVIDUAL TITLE", "STRATA TITLE". Has PTD official seal, lot number, land area, "TUAN PUNYA BERDAFTAR" (registered owner) section. Proves current ownership.

**property_spa** — Sale & Purchase Agreement / Perjanjian Jual Beli. CONTRACT to buy property — has buyer+seller names, purchase price, completion date. Lawyer-drafted, signed by both parties.

**property_tax** — Cukai Tanah, Cukai Harta, Cukai Pintu, Bil Cukai Taksiran. Tax bill/receipt from local council (MBJB, DBKL, MBPJ, MPKj, etc.) or PTD. Does NOT prove ownership.

**property_transfer** — Memorandum of Transfer / Memorandum Pindahmilik. Form number "BORANG 14A" or "BORANG 16A" visible. Has TRANSFEROR + TRANSFEREE sections.

**utility_bill** — Bills from: TNB (Tenaga Nasional), Air Selangor, SAJ (Syarikat Air Johor), PBA, Indah Water (IWK), unifi, Maxis, Celcom, TIME, Astro. Shows service address.

**loan_agreement** — Loan / mortgage / charge document signed with a bank. Look for: bank name (RHB, Maybank, CIMB, Public Bank, Hong Leong, AmBank, Alliance, BSN, Bank Islam, Bank Rakyat, UOB, OCBC, Standard Chartered) + words like "LOAN AGREEMENT", "PERJANJIAN PINJAMAN", "DEED OF ASSIGNMENT", "CHARGE", "BEBANAN", "FACILITIES AGREEMENT", signing pages with borrower signature + bank stamp. This proves the property has a bank loan/mortgage (encumbrance).

**bank_letter** — Short letter FROM a bank (not a loan agreement) — account confirmation letter, offer letter, correspondence. Bank letterhead + brief content.

**bank_statement** — Periodic transaction listing with dates, amounts, running balance. OR passbook, FD certificate, e-statement screenshot, i-Invest statement.

**insurance** — Insurance policy schedule, takaful certificate, cover note. Issuer: Prudential, AIA, Great Eastern, Etiqa, Takaful Malaysia, etc.

**epf_kwsp** — KWSP / EPF statement or i-Akaun screenshot. Has EPF logo, member number, contribution history.

**vehicle** — JPJ vehicle registration card (Kad Pendaftaran Kenderaan), road tax (Cukai Jalan) renewal, grant. Has plate number, engine cc, chassis number.

**will** — Signed Last Will and Testament (Wasiat Terakhir / Last Will). Has testator name, executor appointment, witness signatures.

**death_certificate** — Sijil Kematian / Certificate of Death issued by JPN (Jabatan Pendaftaran Negara) or hospital. Has deceased's name, IC, date/place of death. This is about someone who has DIED — not an asset of the testator.

**unrelated** — Document clearly unrelated to the testator's assets: birth certificate, marriage certificate, court order, photos, medical records, receipts, or any document about a DIFFERENT person that is not an asset.

**other** — Everything else that doesn't clearly fit the above.

RULES:
- Read the HEADING / TITLE of the document first — it's the strongest signal
- Bank name (RHB, Maybank, CIMB, etc.) visible on a signing page with loan terms → loan_agreement
- "SIJIL KEMATIAN" or "CERTIFICATE OF DEATH" → death_certificate, will_relevant=false
- Blurry/dark image where nothing is readable → other, confidence=low
- For property_hint: copy the PROPERTY lot number, title number, or address verbatim if visible (NOT the owner's home address)

Return ONLY this JSON (no other text):
```json
{{"kind": "<category>", "confidence": "high|medium|low", "reason": "<one sentence: what you saw that determined the category>", "purpose": "<what this document proves, max 20 words>", "property_hint": "<lot/title/address if visible, else empty>", "will_relevant": true}}
```"""}
                ]
            }]
        )
    except Exception as e:
        return {**fallback, "reason": f"API error: {e}"}

    try:
        from ai.cost_tracker import log_usage
        log_usage(msg, call_site='ai.file_classifier.classify_file')
    except Exception:
        pass

    text = (msg.content[0].text or "").strip() if msg.content else ""
    js = _extract_json(text)
    if not js:
        return fallback
    try:
        result = json.loads(js)
    except json.JSONDecodeError:
        return fallback
    if result.get('kind') not in KINDS:
        result['kind'] = 'other'
    result.setdefault('confidence', 'low')
    result.setdefault('reason', '')
    result.setdefault('will_relevant', True)
    if result['kind'] == 'other' and result['confidence'] == 'low':
        result.setdefault('will_relevant', False)
    return result
