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
    # Beyond that we'd hit API limits; remaining images fall back to
    # individual classify_file() calls without batch context.
    paths_to_analyse = file_paths[:12]
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

    prompt = f"""You are analysing {n} images sent together in one WhatsApp/email message to a Malaysian will-writing consultant.{ctx_section}

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

    content_blocks.append({"type": "text", "text": prompt})

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=CLAUDE_MODEL_CHEAP,
            max_tokens=1000,
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
            model=CLAUDE_MODEL_FAST,   # Sonnet — haiku misses headings on complex docs
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": f"""You are a Malaysian legal document expert with strong vision. Your job: read this image carefully and identify what the document IS — even if you have never seen this exact format before.{group_section}

━━━ STEP 1: READ FIRST ━━━
Before classifying, scan the image for these signals (in priority order):
1. **Main heading / title** (largest text at top — e.g. "GERAN", "SIJIL KEMATIAN", "PERJANJIAN PINJAMAN")
2. **Issuing authority logo or name** (government department, bank name, company)
3. **Form number** (e.g. BORANG 14A, Form JPN.DP01)
4. **Key phrases** (lot number, IC number, account number, amount, date of death)
5. **Language** (BM headings are common in Malaysian govt docs)

━━━ STEP 2: CLASSIFY (best-effort — always pick closest match) ━━━

Standard categories (use one):
• **nric** — MyKad (photo + IC no. ######-##-####) or Malaysian passport
• **property_title** — Land title: "HAKMILIK", "GERAN", "STRATA TITLE", "INDIVIDUAL TITLE". PTD seal. Has lot no. + "TUAN PUNYA BERDAFTAR"
• **property_spa** — Sale & Purchase Agreement / Perjanjian Jual Beli.
  Covers ALL pages of an SPA — not just the cover page. Classify as property_spa if you see ANY of:
  - Heading: "PERJANJIAN JUAL BELI", "SALE AND PURCHASE AGREEMENT", "S&P", "SPA"
  - Schedule pages: tables listing property details, purchase price, conditions, payment schedule within an SPA
  - Signing/execution pages: signature blocks for Vendor + Purchaser + witnesses, stamp duty section, solicitor stamp — IF the surrounding context (other pages, text) indicates it is an SPA
  - References to "Penjual" / "Pembeli" (Vendor / Purchaser) with a property address or lot number
• **property_tax** — Cukai Tanah / Cukai Harta / Cukai Pintu / quit rent. Local council (MBJB, DBKL, MBPJ, MPJBT, MPS, MPK, MPPG, etc.)
• **property_transfer** — BORANG 14A / 16A. "MEMORANDUM PINDAHMILIK". Transferor + Transferee sections
• **utility_bill** — TNB / Air Selangor / SAJ / PBA / Indah Water / unifi / Maxis. Service address shown
• **loan_agreement** — Loan/charge/mortgage document. Covers ALL pages — not just the cover page. Classify as loan_agreement if you see ANY of:
  - Heading: "LOAN AGREEMENT", "PERJANJIAN PINJAMAN", "DEED OF ASSIGNMENT", "CHARGE", "BEBANAN", "FACILITY AGREEMENT", "LETTER OF OFFER"
  - Bank name (RHB, Maybank, CIMB, Public Bank, HLB, AmBank, Alliance, BSN, Bank Islam, Bank Rakyat, UOB, OCBC, Affin, HSBC, Standard Chartered) present on any page with legal language
  - Signing/execution pages: signature block labelled "BORROWER" / "PEMINJAM" / "CHARGOR" / "ASSIGNOR" + bank representative + witness — these are loan agreement pages even without the main heading
  - References to "facility", "principal sum", "interest rate", "charge", "security" in a banking context
  - Schedules listing repayment amounts, interest rates, property charged as security
• **bank_letter** — Brief bank correspondence (welcome letter, account confirmation, redemption statement). NOT a statement or loan agreement
• **bank_statement** — Transaction list with dates + amounts + running balance. Passbook, FD cert, e-statement
• **insurance** — Policy schedule / takaful cert. Prudential, AIA, Great Eastern, Etiqa, Takaful Malaysia
• **epf_kwsp** — EPF/KWSP logo. Member no. + contribution history. i-Akaun screenshot
• **vehicle** — JPJ registration card (Kad Pendaftaran Kenderaan). Plate no. + chassis + engine cc
• **will** — The document IS a will: titled "WASIAT TERAKHIR", "LAST WILL AND TESTAMENT", or "MY LAST WILL". Contains BOTH testator declaration AND executor appointment AND witness attestation clause. A DRAFT will also counts. Do NOT classify as will: letters about wills, probate orders, grant of probate, letters of administration, legal opinions about wills, redemption letters, discharge documents, loan signing pages.
• **death_certificate** — Official government death registration. Look for ANY of:
  - Headings: "SIJIL KEMATIAN", "CERTIFICATE OF DEATH", "DEATH CERTIFICATE"
  - Fields: "TARIKH KEMATIAN" / "DATE OF DEATH", "SEBAB KEMATIAN" / "CAUSE OF DEATH", "TEMPAT KEMATIAN" / "PLACE OF DEATH"
  - JPN (Jabatan Pendaftaran Negara) logo or stamp on a document listing a deceased person's name + death date
  - Any form where a "date of death" field is filled in with a specific date
  Set will_relevant=false, person_name=deceased full name.
• **unrelated** — Clearly not an asset: birth cert, marriage cert, medical record, receipt, photo of people/objects, court order unrelated to property

⚡ INTERIOR PAGE RULE (very important):
Many documents span multiple pages. The heading "LOAN AGREEMENT" or "PERJANJIAN JUAL BELI" only appears on page 1. Interior pages — schedules, conditions, signing/execution pages — may have NO heading at all, only signatures, tables, or legal text. If you see:
- Signature blocks with "Borrower / Bank representative / Witness" → loan_agreement
- Signature blocks with "Vendor / Purchaser / Witness / Solicitor" + property address → property_spa
- Tabular schedules referencing "facility amount", "interest", "security" → loan_agreement
- Tabular schedules referencing "purchase price", "property", "conditions" → property_spa
Use the batch group context if provided — it tells you what type the cover page was classified as.

⚡ BEST-EFFORT RULE: If the document does NOT match any standard category, still provide:
  - `kind`: the CLOSEST standard category (never default to "other" unless truly unreadable)
  - `custom_type`: the document's actual name/title as you read it from the image (e.g. "Redemption Statement", "Letter of Undertaking", "Discharge of Charge", "Strata Title Application", "Developer's Progress Billing")
  - `purpose`: what this document proves or is used for
  - Only use `kind: "other"` + `confidence: "low"` when the image is so blurry/dark that you cannot read ANY text

━━━ RULES ━━━
- Document heading is the STRONGEST signal — trust what you read
- Interior/signing pages with no heading: use visual cues (signature labels, table headers) — see INTERIOR PAGE RULE above
- Bank name + legal language on ANY page → loan_agreement (even signing/schedule pages)
- "Vendor/Purchaser" + property reference on ANY page → property_spa
- "SIJIL KEMATIAN" / "TARIKH KEMATIAN" / "SEBAB KEMATIAN" / date of death field filled in → death_certificate (will_relevant=false)
- Truly unreadable (black/blank/photo of scenery/selfie) → other, confidence=low, custom_type="Unreadable image"
- property_hint: copy verbatim the property lot number, title number, or address (NOT owner's home address)
- person_name: the PRIMARY person named in the document (owner, borrower, deceased, IC holder). Full name as printed. Empty if no clear name visible.
- For death_certificate: set will_relevant=false, person_name=deceased full name
- For will: ONLY classify as will if you can clearly see both "LAST WILL AND TESTAMENT" / "WASIAT" AND the executor/witness structure. When in doubt, use other + custom_type with the actual document title.

Return ONLY this JSON (no other text):
```json
{{
  "kind": "<standard category>",
  "custom_type": "<document's own title as read from image, or empty if matches standard category exactly>",
  "confidence": "high|medium|low",
  "reason": "<one sentence: exactly what heading/text/logo you saw>",
  "purpose": "<what this document is for, max 20 words>",
  "property_hint": "<lot/title/address if visible, else empty>",
  "person_name": "<primary person named in document, or empty>",
  "will_relevant": true
}}
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
    result.setdefault('custom_type', '')
    result.setdefault('person_name', '')
    result.setdefault('will_relevant', True)
    if result['kind'] in ('death_certificate', 'unrelated'):
        result['will_relevant'] = False
    elif result['kind'] == 'other' and result['confidence'] == 'low':
        result.setdefault('will_relevant', False)
    # If model gave a custom_type but no standard kind, keep kind='other'
    # but surface custom_type as the display label everywhere.
    # Normalise: strip whitespace
    result['custom_type'] = (result.get('custom_type') or '').strip()
    result['person_name'] = (result.get('person_name') or '').strip()
    return result
