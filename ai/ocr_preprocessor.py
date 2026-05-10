"""OCR pre-processor — Tesseract + regex field extractor.

Replaces Claude Sonnet vision in classify_file() with a free/cheap pipeline:
  Image → Tesseract OCR → regex extract → structured fields

Install (one-time):
  macOS dev:  brew install tesseract && pip install pytesseract
  EC2/Ubuntu: apt install tesseract-ocr && pip install pytesseract
  (Malay language pack optional — eng alone handles most Malaysian printed docs)
"""
import re
import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Document type patterns  (order = priority, first match wins on single hit;
# on tie, more hits wins)
# ---------------------------------------------------------------------------
_DOC_PATTERNS: list[tuple[str, list[str]]] = [
    # ── Property ownership ──────────────────────────────────────────────────
    ('property_title', [
        r'\bGERAN\b',
        r'\bHAKMILIK\b',
        r'STRATA TITLE',
        r'INDIVIDUAL TITLE',
        r'TUAN PUNYA BERDAFTAR',
        r'REGISTERED PROPRIETOR',
        r'PAJAKAN NEGERI',
        r'GERAN MUKIM',
    ]),
    # ── Sale & Purchase ─────────────────────────────────────────────────────
    ('property_spa', [
        r'PERJANJIAN JUAL BELI',
        r'SALE.{0,15}PURCHASE AGREEMENT',
        r'\bPENJUAL\b',
        r'\bPEMBELI\b',
        r'\bVENDOR\b',
        r'\bPURCHASER\b',
        r'BORANG\s+14A',
        r'BORANG\s+16A',
    ]),
    # ── Loan / charge ───────────────────────────────────────────────────────
    ('loan_agreement', [
        r'FACILITY AGREEMENT',
        r'PERJANJIAN PINJAMAN',
        r'PERJANJIAN KEMUDAHAN',
        r'DEED OF ASSIGNMENT',
        r'\bCHARGOR\b',
        r'\bBORROWER\b',
        r'\bPEMINJAM\b',
        r'LETTER OF OFFER',
        r'PRINCIPAL SUM',
        r'BEBANAN',
        r'ASSIGNOR',
    ]),
    # ── Property tax / assessment ────────────────────────────────────────────
    ('property_tax', [
        r'CUKAI TANAH',
        r'CUKAI HARTA',
        r'CUKAI PINTU',
        r'QUIT RENT',
        r'PENILAIAN',          # Cukai Penilaian (council assessment)
        r'\bMBJB\b', r'\bDBKL\b', r'\bMBPJ\b', r'\bMPJBT\b',
        r'\bMPS\b',  r'\bMPK\b',  r'\bMPPG\b',  r'\bMPSP\b',
        r'PENTADBIRAN TANAH',
        # 🔥 §10x.144 — strata management bills (JMB / Badan Pengurusan
        # Bersama / Management Corporation) belong here. Common
        # invoice content: SERVICE CHARGE, SINKING FUND, WATER BILL.
        r'BADAN PENGURUSAN',
        r'PENGURUSAN BERSAMA',
        r'MANAGEMENT CORPORATION',
        r'PERBADANAN PENGURUSAN',
        r'\bJMB\b',
        r'\bMC\s+NO\b',
        r'SERVICE CHARGE',
        r'SINKING FUND',
        r'CAJ PERKHIDMATAN',
        r'KUMPULAN WANG PENJELASAN',
    ]),
    # ── Death certificate ────────────────────────────────────────────────────
    ('death_certificate', [
        r'SIJIL KEMATIAN',
        r'CERTIFICATE OF DEATH',
        r'DEATH CERTIFICATE',
        r'TARIKH KEMATIAN',
        r'SEBAB KEMATIAN',
        r'PLACE OF DEATH',
        r'TEMPAT KEMATIAN',
        r'DATE OF DEATH',
    ]),
    # ── Will ─────────────────────────────────────────────────────────────────
    ('will', [
        r'LAST WILL AND TESTAMENT',
        r'WASIAT TERAKHIR',
        r'\bWASIAT\b',
        r'I HEREBY REVOKE',
        r'EXECUTOR.*TRUSTEE',
        r'TESTATOR',
    ]),
    # ── Valuation ────────────────────────────────────────────────────────────
    ('valuation', [
        r'VALUATION REPORT',
        r'LAPORAN PENILAIAN',
        r'MARKET VALUE',
        r'NILAI PASARAN',
        r'REGISTERED VALUER',
    ]),
    # ── NRIC / MyKad ─────────────────────────────────────────────────────────
    ('nric', [
        r'MYKAD',
        r'KAD PENGENALAN',
        r'IDENTITY CARD',
        r'WARGANEGARA',
        r'MALAYSIA\b.*\bIC\b',
        r'NO\.\s*K\.?\s*P\.?',
    ]),
    # ── Utility bills (electric, water) — useful for address extraction ───────
    ('utility_bill', [
        r'TENAGA NASIONAL',
        r'\bTNB\b',
        r'AIR SELANGOR',
        r'INDAH WATER',
        r'SYARIKAT AIR',
        r'\bSAJ\b',
        r'\bPBA\b',
        r'\bUNIFI\b',
        r'\bMAXIS\b',
        r'ELECTRICITY BILL',
        r'WATER BILL',
        r'BIL ELEKTRIK',
        r'BIL AIR',
    ]),
    # ── Bank statement ───────────────────────────────────────────────────────
    ('bank_statement', [
        r'STATEMENT OF ACCOUNT',
        r'PENYATA AKAUN',
        r'RUNNING BALANCE',
        r'TRANSACTION HISTORY',
        r'CLOSING BALANCE',
    ]),
    # ── Insurance / Takaful ──────────────────────────────────────────────────
    ('insurance', [
        r'PRUDENTIAL',
        r'\bAIA\b',
        r'GREAT EASTERN',
        r'ETIQA',
        r'TAKAFUL',
        r'POLICY SCHEDULE',
        r'SIJIL TAKAFUL',
        r'ENDOWMENT',
    ]),
    # ── EPF / KWSP ───────────────────────────────────────────────────────────
    ('epf_kwsp', [
        r'\bKWSP\b',
        r'\bEPF\b',
        r'KUMPULAN WANG SIMPANAN PEKERJA',
        r'EMPLOYEES PROVIDENT FUND',
        r'I-AKAUN',
    ]),
    # ── Vehicle ──────────────────────────────────────────────────────────────
    ('vehicle', [
        r'KAD PENDAFTARAN KENDERAAN',
        r'VEHICLE REGISTRATION',
        r'NO\.\s*PENDAFTARAN',
        r'NOMBOR CASIS',
        r'NOMBOR ENJIN',
        r'ROAD TAX',
        r'PUSPAKOM',
    ]),
    # ── Bank letter ──────────────────────────────────────────────────────────
    ('bank_letter', [
        r'REDEMPTION STATEMENT',
        r'SURAT TAWARAN',
        r'LETTER OF OFFER(?!\s+LETTER)',   # avoid double-match with loan
        r'WELCOME LETTER',
    ]),
]

# ---------------------------------------------------------------------------
# Field extraction patterns
# ---------------------------------------------------------------------------

# Lot / parcel numbers: PTD 12345, HSD 6789, Lot 127082, GRN 999/2001
_LOT_NO = re.compile(
    r'(?:PTD|HSD|HSM|HS\(D\)|HS\(M\)|H\.S\.\(D\)|H\.S\.\(M\)|'
    r'Lot\b|No\.?\s*Lot\b|GRN|Geran\s*No)',
    re.IGNORECASE
)
_LOT_NO_FULL = re.compile(
    r'(?:PTD|HSD|HSM|HS\s*\(D\)|HS\s*\(M\)|H\.S\.\s*\(D\)|H\.S\.\s*\(M\)|'
    r'Lot|No\.?\s*Lot|GRN|Geran\s*No\.?)\s*[:\s]*(\d[\d/\-]*)',
    re.IGNORECASE
)

# Title / geran reference number
_TITLE_NO = re.compile(
    r'(?:H\.?S\.?\s*\(D\)|H\.?S\.?\s*\(M\)|HS\(D\)|HS\(M\)|'
    r'Pajakan\s+Negeri|STRATA TITLE\s*NO\.?|GERAN\s*NO\.?)\s*[:\s]*(\d[\d/\-]*)',
    re.IGNORECASE
)

# Malaysian IC: 123456-78-9012 or 123456789012 (12 digits)
_IC_NO = re.compile(r'\b(\d{6})[-\s]?(\d{2})[-\s]?(\d{4})\b')

# Common Malaysian bank names
_BANK = re.compile(
    r'\b(MAYBANK|MALAYAN BANKING|CIMB|RHB|AMBANK|HONG LEONG(?:\s+BANK)?|'
    r'PUBLIC BANK|BSN|AFFIN(?:\s+BANK)?|BANK ISLAM|BANK RAKYAT|'
    r'UOB|OCBC|HSBC|STANDARD CHARTERED|ALLIANCE BANK|AGRO BANK|AGROBANK)\b',
    re.IGNORECASE
)

# Street address patterns common in Malaysia
_ADDRESS = re.compile(
    r'(?:No\.?\s*\d+[A-Z]?(?:-\d+)?[,\s]+)'
    r'(?:Jalan|Jln|Lorong|Lrg|Taman|Tmn|Bandar|Bdr|Persiaran|Prsrn|Lebuh)[^,\n]{5,70}',
    re.IGNORECASE
)

# Registered owner label — stop at newline or punctuation that signals end of name
_OWNER = re.compile(
    r'(?:TUAN PUNYA BERDAFTAR|REGISTERED (?:PROPRIETOR|OWNER)|PEMILIK\s+BERDAFTAR)'
    r'[:\s]*([A-Z][A-Z @&\'\-]{2,60}?)(?=\s*\n|\s*NO\.?\s*K|\s*IC|\s*\d{6}|$)',
    re.IGNORECASE
)

# Mukim / Daerah / Negeri
_MUKIM  = re.compile(r'\bMUKIM\s+([A-Z][A-Z\s]+)', re.IGNORECASE)
_DAERAH = re.compile(r'\bDAERAH\s+([A-Z][A-Z\s]+)', re.IGNORECASE)
_NEGERI = re.compile(
    r'\b(JOHOR|SELANGOR|PERAK|PAHANG|KELANTAN|TERENGGANU|KEDAH|PERLIS|'
    r'NEGERI SEMBILAN|MELAKA|SABAH|SARAWAK|WILAYAH PERSEKUTUAN|'
    r'PUTRAJAYA|LABUAN|KUALA LUMPUR)\b',
    re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ocr_extract(image_path: str) -> str:
    """Extract raw text from image or PDF using Tesseract / pypdf.

    Returns empty string on failure (caller must handle gracefully).
    Never raises.
    """
    try:
        ext = image_path.rsplit('.', 1)[-1].lower()

        # ── PDF: try text extraction first (fast, free, works on generated PDFs)
        if ext == 'pdf':
            return _pdf_text(image_path)

        # ── Image: Pillow open → Tesseract
        return _image_ocr(image_path)

    except Exception as e:
        log.warning('ocr_extract failed for %s: %s', image_path, e)
        return ''


def regex_extract(text: str) -> dict:
    """Extract structured classification fields from OCR text.

    Returns:
      {
        doc_type:         str   — matched KINDS value or ''
        confidence:       float — 0.0 / 0.6 / 0.85 / 0.95
        lot_number:       str
        title_number:     str
        property_address: str
        owner_name:       str
        ic_number:        str   — formatted as XXXXXX-XX-XXXX
        bank_name:        str
        mukim:            str
        daerah:           str
        negeri:           str
        raw_text_len:     int
      }
    """
    result = {
        'doc_type': '',
        'confidence': 0.0,
        'lot_number': '',
        'title_number': '',
        'property_address': '',
        'owner_name': '',
        'ic_number': '',
        'bank_name': '',
        'mukim': '',
        'daerah': '',
        'negeri': '',
        'raw_text_len': len(text),
    }

    if not text or len(text.strip()) < 30:
        return result

    text_upper = text.upper()

    # ── Doc type detection ────────────────────────────────────────────────
    best_type, best_hits = '', 0
    for dtype, patterns in _DOC_PATTERNS:
        hits = sum(1 for p in patterns if re.search(p, text_upper))
        if hits > best_hits:
            best_hits = hits
            best_type = dtype

    result['doc_type'] = best_type
    if best_hits >= 3:
        result['confidence'] = 0.95
    elif best_hits == 2:
        result['confidence'] = 0.85
    elif best_hits == 1:
        result['confidence'] = 0.60
    # else confidence stays 0.0

    # ── Field extraction ──────────────────────────────────────────────────
    m = _LOT_NO_FULL.search(text)
    if m:
        result['lot_number'] = m.group(1).strip()

    m = _TITLE_NO.search(text)
    if m:
        result['title_number'] = m.group(1).strip()

    m = _IC_NO.search(text)
    if m:
        result['ic_number'] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    m = _BANK.search(text)
    if m:
        result['bank_name'] = m.group(1).upper()

    m = _ADDRESS.search(text)
    if m:
        result['property_address'] = m.group(0).strip()[:120]

    m = _OWNER.search(text)
    if m:
        # Take up to 6 words — owner name rarely exceeds that
        words = m.group(1).split()[:6]
        result['owner_name'] = ' '.join(words)

    m = _MUKIM.search(text)
    if m:
        result['mukim'] = m.group(1).strip().title()

    m = _DAERAH.search(text)
    if m:
        result['daerah'] = m.group(1).strip().title()

    m = _NEGERI.search(text)
    if m:
        result['negeri'] = m.group(1).strip().title()

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pdf_text(path: str) -> str:
    """Extract text from a (possibly text-based) PDF using pypdf."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        parts = []
        for page in reader.pages[:6]:   # first 6 pages is plenty for classification
            t = page.extract_text() or ''
            if t.strip():
                parts.append(t)
        return '\n'.join(parts)
    except Exception as e:
        log.debug('pypdf extraction failed for %s: %s', path, e)
        return ''


def _image_ocr(path: str) -> str:
    """Run Tesseract on an image file, converting exotic formats via Pillow."""
    try:
        import pytesseract
        from PIL import Image
        import io

        ext = path.rsplit('.', 1)[-1].lower()
        img = Image.open(path)

        # Convert HEIC / RGBA / palette modes that Tesseract dislikes
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')

        # Attempt Malay + English; fall back to English-only if msa pack absent
        try:
            text = pytesseract.image_to_string(img, lang='eng+msa', config='--psm 3')
        except pytesseract.pytesseract.TesseractError:
            text = pytesseract.image_to_string(img, lang='eng', config='--psm 3')

        return text

    except ImportError:
        log.warning('pytesseract not installed — skipping image OCR')
        return ''
    except Exception as e:
        log.debug('Tesseract OCR failed for %s: %s', path, e)
        return ''
