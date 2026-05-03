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


def list_available_acts() -> List[Dict[str, str]]:
    """Return [{'slug', 'title', 'path', 'size_kb'}] for every PDF found."""
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
        out.append({'slug': slug, 'title': _slug_to_title(slug),
                    'path': path, 'size_kb': size_kb})
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
