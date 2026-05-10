"""Legal library — loads PDFs of Malaysian acts from data/legal_acts/
and exposes their text so ai/legal_qa.py can cite specific provisions.

Drop the official PDFs into data/legal_acts/ with these filenames:
  - wills_act_1959.pdf
  - probate_and_administration_act_1959.pdf
  - distribution_act_1958.pdf
  - national_land_code_1965.pdf  (large — chapters can be split if needed)
  - strata_titles_act_1985.pdf
  - any_other_relevant_act.pdf

Sources: official copies from federalgazette.agc.gov.my (Attorney-General's
Chambers) or Practical Law Malaysia. Filename prefix becomes the citation
key. Text is loaded once at startup + cached.
"""
import os
import re
from typing import Dict, List, Optional
from config import DATA_DIR


LEGAL_DIR = os.path.join(DATA_DIR, 'legal_acts')
_TEXT_CACHE: Dict[str, str] = {}


def _slug_to_title(slug: str) -> str:
    """wills_act_1959 → Wills Act 1959"""
    return ' '.join(p.capitalize() for p in slug.split('_'))


def _classify(slug: str) -> str:
    """🔥 §10x.136 — Categorise PDFs: 'act' (statute) vs 'book' (textbook /
    drafting guide). Acts have year suffix; books are named differently.
    """
    s = slug.lower()
    # Statute pattern: ends with _<year> (4-digit) AND contains _act_ OR _code_
    if (re.search(r'_(?:act|code|enactment|ordinance)_?\d{4}$', s)
            or re.search(r'_(?:act|code|enactment|ordinance)\d{4}$', s)):
        return 'act'
    # Heuristic for books / guides
    if any(k in s for k in (
            'gopalakrishnan', 'kessler', 'shankar', '_ed_', '_11ed',
            '_3ed', '_drafting_', 'guide', 'precedent', 'handbook',
            'textbook', 'malaysia_and_singapore')):
        return 'book'
    # Default to 'act' for anything ending in a 4-digit year
    if re.search(r'_\d{4}$', s):
        return 'act'
    return 'book'


def list_available_acts() -> List[Dict[str, str]]:
    """Return [{'slug', 'title', 'path', 'size_kb', 'category'}] for every
    PDF found. category is 'act' or 'book'."""
    out = []
    if not os.path.isdir(LEGAL_DIR):
        return out
    for fn in sorted(os.listdir(LEGAL_DIR)):
        if not fn.lower().endswith('.pdf'):
            continue
        slug = fn[:-4].lower()
        path = os.path.join(LEGAL_DIR, fn)
        try:
            size_kb = os.path.getsize(path) // 1024
        except OSError:
            size_kb = 0
        out.append({
            'slug': slug, 'title': _slug_to_title(slug),
            'path': path, 'size_kb': size_kb,
            'category': _classify(slug),
        })
    return out


def _extract_pdf_text(path: str) -> str:
    """Best-effort PDF → text. Tries pypdf, falls back to empty string."""
    if path in _TEXT_CACHE:
        return _TEXT_CACHE[path]
    text = ''
    try:
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader  # type: ignore
        reader = PdfReader(path)
        text = '\n\n'.join((p.extract_text() or '') for p in reader.pages)
    except Exception:
        text = ''
    _TEXT_CACHE[path] = text
    return text


# ── Topic → Act mapping ─────────────────────────────────────────────────
# Maps a question keyword/phrase to the SINGLE most likely Act slug. This
# lets us identify the right Act first (cheap lookup) and then drill into
# its sections, instead of scoring all 23 Acts for every question.
#
# Order matters — earlier patterns win on ties. Add aggressively as we
# encounter new question types.
_TOPIC_HINTS: List[tuple] = [
    # (regex pattern in lowered question, slug to prefer)
    # 🔥 §10x.135 — TEXTBOOK references (more comprehensive than statutes
    # for "how do I draft" / "what's good practice" questions). Listed
    # FIRST so drafting/practice questions hit the textbook before falling
    # to statute. Each rule must include a citation pattern that lands on
    # actual content, not the wrong-question topic.
    (r'\bdraft|drafting|trust deed|will trust|how do i (?:draft|write)|'
     r'best practice|gold.standard|sample clause|precedent|template',
     'drafting_trusts_and_will_trusts_11ed'),
    (r'\bprobate procedure|grant application|caveat|estate administration|'
     r'contested probate|small estate|sijil faraid|distribution scheme|'
     r'singapore.*probate|family justice court|amanah raya',
     'probate_administration_malaysia_singapore_3ed'),
    (r'\bcommunity|hindu|christian|muslim|chinese custom|adat|customary',
     'law_of_wills_gopalakrishnan_11ed'),
    (r'\bwill\b|testament|testator|witness|attest|revoke|codicil',
     'wills_act_1959'),
    (r'\bprobate|grant of probate|letters of administration|administrator|executor',
     'probate_administration_act_1959'),
    (r'distribution act|intestate|intestacy|spouse and children|share of estate',
     'distribution_act_1958'),
    (r'family provision|dependant|maintenance from estate',
     'inheritance_family_provision_act_1971'),
    (r'\bgeran\b|individual title|land title|land code|nlc|qualified title|caveat',
     'national_land_code_1965'),
    (r'\bstrata\b|parcel|management corporation|sub-?divided',
     'strata_titles_act_1985'),
    (r'strata management|jmb|joint management body|maintenance fee|sinking fund',
     'strata_management_act_2013'),
    (r'\brpgt\b|real property gains|cgt|capital gains',
     'rpgt_act_1976'),
    (r'\bstamp\b|stamping|stamp duty',
     'stamp_act_1949'),
    (r'small estate|estate distribution officer|edo',
     'small_estates_distribution_act_1955'),
    (r'\binsolvenc|bankrupt',
     'insolvency_act_1967'),
    (r'\btax\b|income tax|chargeable income',
     'income_tax_act_1967'),
]


def identify_act(question: str) -> Optional[str]:
    """Return the slug of the single most-likely Act for this question,
    or None if no topic hint matches. Cheap regex lookup, no PDF reads."""
    if not question:
        return None
    q = question.lower()
    available = {a['slug'] for a in list_available_acts()}
    for pattern, slug in _TOPIC_HINTS:
        if slug not in available:
            continue
        if re.search(pattern, q):
            return slug
    return None


def section_excerpt(slug: str, question: str, max_chars: int = 1600
                    ) -> Optional[Dict[str, str]]:
    """Given a known-good Act slug, find the most relevant paragraph(s)
    inside it for the question. Returns {title, excerpt} or None if the
    Act isn't loaded / has no matching keyword."""
    if not slug:
        return None
    acts = {a['slug']: a for a in list_available_acts()}
    act = acts.get(slug)
    if not act:
        return None
    text = _extract_pdf_text(act['path'])
    if not text:
        return None
    keywords = set(re.findall(r'\b[a-z]{4,}\b', question.lower()))
    keywords -= {'what', 'when', 'where', 'which', 'this', 'that', 'with',
                 'have', 'does', 'will', 'should', 'would', 'about', 'cite',
                 'tell', 'explain', 'mean', 'meaning', 'difference', 'between',
                 'whats', 'hows', 'definition'}
    if not keywords:
        return None
    paragraphs = re.split(r'\n\s*\n', text)
    scored = []
    for i, para in enumerate(paragraphs):
        pl = para.lower()
        h = sum(1 for k in keywords if k in pl)
        if h:
            scored.append((h, i, para.strip()))
    scored.sort(reverse=True)
    if not scored:
        return None
    # Take top 2 sections, joined with separator, truncated
    top = [s[2] for s in scored[:2]]
    excerpt = '\n\n…\n\n'.join(top)[:max_chars]
    return {'title': act['title'], 'slug': slug, 'excerpt': excerpt}


def relevant_excerpts(question: str, max_chars: int = 2400,
                      max_acts: int = 3, per_act_chars: int = 800
                      ) -> List[Dict[str, str]]:
    """For a user question, return excerpts from the TOP-N acts that contain
    keywords (default 3, not all 23). Crude keyword retrieval — good enough
    until we add embeddings.

    Token-budget aware: each call sends at most `max_chars` of excerpt text
    to the LLM (was 6000 — burned tokens on every 'who can be witness' Q).
    """
    if not question:
        return []
    acts = list_available_acts()
    if not acts:
        return []
    # Pull keywords (4+ char alphanumeric tokens, lowercased)
    keywords = set(re.findall(r'\b[a-z]{4,}\b', question.lower()))
    # Drop generic words
    keywords -= {'what', 'when', 'where', 'which', 'this', 'that', 'with',
                 'have', 'does', 'will', 'should', 'would', 'about', 'cite',
                 'tell', 'explain', 'mean', 'meaning', 'difference', 'between',
                 'whats', 'hows', 'difference', 'between', 'definition'}
    if not keywords:
        return []

    # Step 1: score each Act by total keyword hits across all paragraphs.
    # Only the top-N acts get expensive paragraph-level scoring.
    act_scores = []
    for act in acts:
        text = _extract_pdf_text(act['path'])
        if not text:
            continue
        # Cheap whole-doc keyword count first
        tl = text.lower()
        total_hits = sum(tl.count(k) for k in keywords)
        if total_hits:
            act_scores.append((total_hits, act, text))
    act_scores.sort(key=lambda x: -x[0])
    top_acts = act_scores[:max_acts]

    out: List[Dict[str, str]] = []
    budget_left = max_chars
    for _hits, act, text in top_acts:
        if budget_left <= 0:
            break
        # Score paragraphs by keyword hits, take the top 2 (was 3)
        paragraphs = re.split(r'\n\s*\n', text)
        scored = []
        for i, para in enumerate(paragraphs):
            pl = para.lower()
            phits = sum(1 for k in keywords if k in pl)
            if phits:
                scored.append((phits, i, para.strip()))
        scored.sort(reverse=True)
        top = [s[2] for s in scored[:2]]
        excerpt = '\n\n…\n\n'.join(top)
        # Truncate to per_act_chars OR remaining budget, whichever's smaller
        cap = min(per_act_chars, budget_left)
        excerpt = excerpt[:cap]
        if excerpt:
            out.append({'title': act['title'], 'excerpt': excerpt})
            budget_left -= len(excerpt)
    return out
