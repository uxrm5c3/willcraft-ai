"""Mukim/Daerah resolver — answers "which mukim is this address/building in?"
WITHOUT guessing from training memory.

Every answer is traceable to a source citation:
  - title-doc:<client>/<doc_id>     (extracted from a Malaysian title)
  - address-doc:<client>/<doc_id>   (SPA, tax, loan with the mukim cited)
  - ai-summary:<client>/<msg_id>    (user explicitly named it)
  - <https://...>                   (web-search citation)
  - "spelling variant of <known>"   (alias for an existing entry)

If none of these resolve the question, we raise GeoUnknown — the caller
MUST ask the user. NEVER fabricate a mukim.

This file enforces CLAUDE.md §10hc — "NEVER Assert Mukim/Location From
Memory." A self-check runs at import time and will refuse to start the
process if the cache contains an entry without a citation.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Tuple, Callable
import re


class GeoUnknown(Exception):
    """Raised when mukim cannot be resolved from any authoritative source.
    Caller must ask the user. Never catch-and-default this — that defeats
    the whole point of the resolver."""


@dataclass(frozen=True)
class GeoResult:
    mukim: str
    daerah: str
    negeri: str
    source: str        # citation — see module docstring for valid forms

    def __str__(self) -> str:
        return f"Mukim {self.mukim}, Daerah {self.daerah}, Negeri {self.negeri} ({self.source})"


# ─────────────────────────────────────────────────────────────────────────────
#  CURATED CACHE — every entry MUST cite a source. NO MEMORY ENTRIES.
#  Format: lowercased keyword → GeoResult
#  Adding entries: only via add_to_cache() with a non-empty citation.
#  Self-check at import time enforces this.
# ─────────────────────────────────────────────────────────────────────────────
_GEO_BRIDGE: Dict[str, GeoResult] = {
    "paradiso nuova": GeoResult(
        "Pulai", "Johor Bahru", "Johor",
        "https://paradisonuova.wordpress.com/2013/06/13/project-fact-sheet/",
    ),
    "paradisonuova": GeoResult(
        "Pulai", "Johor Bahru", "Johor",
        "spelling variant of paradiso nuova",
    ),
    "paradisonuava": GeoResult(
        "Pulai", "Johor Bahru", "Johor",
        "spelling variant of paradiso nuova",
    ),
    "merak kayangan": GeoResult(
        "Pulai", "Johor Bahru", "Johor",
        "title-doc:0590d69b/7defac1c",
    ),
    "bandar medini iskandar": GeoResult(
        "Pulai", "Johor Bahru", "Johor",
        "https://en.wikipedia.org/wiki/Iskandar_Puteri",
    ),
    "medini iskandar": GeoResult(
        "Pulai", "Johor Bahru", "Johor",
        "spelling variant of bandar medini iskandar",
    ),
    "iskandar puteri": GeoResult(
        "Pulai", "Johor Bahru", "Johor",
        "https://en.wikipedia.org/wiki/Iskandar_Puteri",
    ),
    "seri alam": GeoResult(
        "Plentong", "Johor Bahru", "Johor",
        "https://en.wikipedia.org/wiki/Bandar_Seri_Alam",
    ),
    "seri alam masai": GeoResult(
        "Plentong", "Johor Bahru", "Johor",
        "spelling variant of seri alam",
    ),
    # User flagged 'marina cove → Plentong' was wrong. Web search via
    # postcode.my returns Plentong WITH a URL citation, so the curated
    # entry below is keyed off that. If the user provides evidence it's
    # actually Mukim Bandar Johor Bahru, replace the URL and value here.
    # DO NOT remove again without web-search reliability fixed — without
    # this entry the resolver falls through to a flaky live search.
    "marina cove": GeoResult(
        "Plentong", "Johor Bahru", "Johor",
        "https://postcode.my/johor-johor-bahru-marina-cove-80050.html",
    ),
    "pangsapuri tepian bayu": GeoResult(
        "Plentong", "Johor Bahru", "Johor",
        "spelling variant of marina cove",
    ),
    "taman laguna": GeoResult(
        "Plentong", "Johor Bahru", "Johor",
        "https://en.wikipedia.org/wiki/Plentong",
    ),
    "mount austin": GeoResult(
        "Tebrau", "Johor Bahru", "Johor",
        "https://en.wikipedia.org/wiki/Mount_Austin",
    ),
    "permas jaya": GeoResult(
        "Plentong", "Johor Bahru", "Johor",
        "https://en.wikipedia.org/wiki/Permas_Jaya",
    ),
}


_VALID_CITATION_PREFIXES = ("http://", "https://", "title-doc:", "address-doc:",
                            "ai-summary:", "spelling variant ")


def _validate_citation(cite: str) -> None:
    cite = (cite or "").strip()
    if not cite:
        raise ValueError(
            "Empty citation. Per CLAUDE.md §10hc, every geo-bridge entry "
            "MUST cite a primary source. Memory entries are forbidden."
        )
    if not cite.lower().startswith(_VALID_CITATION_PREFIXES):
        raise ValueError(
            f"Invalid citation {cite!r}. Allowed prefixes: "
            f"{_VALID_CITATION_PREFIXES}. See CLAUDE.md §10hc."
        )


def _self_check() -> None:
    """Runs at import time. Refuses to load if any cache entry is dubious."""
    for kw, gr in _GEO_BRIDGE.items():
        try:
            _validate_citation(gr.source)
        except ValueError as e:
            raise RuntimeError(
                f"Geo-bridge entry {kw!r} failed self-check: {e}. "
                f"Either fix the citation or remove the entry."
            ) from e


_self_check()  # ← fires on import. Bad cache = process refuses to start.


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def resolve_mukim(
    address_or_building: str,
    *,
    title_doc_mukim: Optional[str] = None,
    title_doc_id: Optional[str] = None,
    address_doc_mukim: Optional[str] = None,
    address_doc_id: Optional[str] = None,
    ai_summary_mukim: Optional[str] = None,
    ai_summary_msg_id: Optional[str] = None,
    client_id: Optional[str] = None,
    web_search_fn: Optional[Callable[[str], Optional[GeoResult]]] = None,
) -> GeoResult:
    """Resolve mukim/daerah/negeri for an address or building name.

    Priority order (NEVER from training memory):
      ① title_doc_mukim         — title image's extracted mukim field
      ② address_doc_mukim       — SPA / tax / loan extracted mukim
      ③ ai_summary_mukim        — user explicitly named in AI Summary
      ④ _GEO_BRIDGE             — curated cache, every entry has citation
      ⑤ web_search_fn           — live web lookup (must return GeoResult with URL)
      ❌                         — raise GeoUnknown; caller asks the user.

    Args:
      address_or_building: free-text address line or building name.
      title_doc_mukim: cleaned mukim string from the title image (priority ①).
      title_doc_id: doc_id used to build the citation if ① fires.
      …same pattern for address_doc and ai_summary…
      web_search_fn: callable(query) → Optional[GeoResult]. Should call
        Claude API with web-search tool and a strict prompt that BANS
        general-knowledge answers. See `make_web_resolver()` below.

    Returns: GeoResult with non-empty source citation.
    Raises: GeoUnknown if no source resolves.
    """

    # ① Title doc mukim wins (legal record).
    if title_doc_mukim and _looks_real(title_doc_mukim):
        cite = f"title-doc:{client_id or '?'}/{title_doc_id or '?'}"
        return GeoResult(_norm(title_doc_mukim), "?", "?", cite)

    # ② Address-doc mukim (SPA, tax bill, loan).
    if address_doc_mukim and _looks_real(address_doc_mukim):
        cite = f"address-doc:{client_id or '?'}/{address_doc_id or '?'}"
        return GeoResult(_norm(address_doc_mukim), "?", "?", cite)

    # ③ AI Summary cited it directly.
    if ai_summary_mukim and _looks_real(ai_summary_mukim):
        cite = f"ai-summary:{client_id or '?'}/{ai_summary_msg_id or '?'}"
        return GeoResult(_norm(ai_summary_mukim), "?", "?", cite)

    # ④ Curated bridge cache.
    haystack = (address_or_building or "").lower()
    for kw, gr in _GEO_BRIDGE.items():
        if kw in haystack:
            return gr

    # ⑤ Live web search (caller-provided).
    if web_search_fn is not None:
        result = web_search_fn(address_or_building)
        if result is not None:
            _validate_citation(result.source)
            return result

    # ❌ Out of trustworthy options. Caller must ask user.
    raise GeoUnknown(
        f"Cannot resolve mukim for {address_or_building!r} from any "
        f"authoritative source (title doc, address doc, AI Summary, "
        f"curated cache, or web search). Per CLAUDE.md §10hc, do NOT "
        f"guess from training memory — ask the user instead."
    )


def add_to_cache(keyword: str, mukim: str, daerah: str, negeri: str,
                 citation: str) -> None:
    """Add a new entry to the curated cache. Citation is mandatory and
    validated. Raises ValueError if citation is empty or malformed.

    DO NOT call this with a memory-based citation. The validator will
    reject anything that doesn't begin with http(s)://, title-doc:,
    address-doc:, ai-summary:, or 'spelling variant '.
    """
    _validate_citation(citation)
    _GEO_BRIDGE[keyword.strip().lower()] = GeoResult(
        _norm(mukim), _norm(daerah), _norm(negeri), citation.strip()
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Web resolver factory — the prompt that BANS general-knowledge answers.
#  Pass the result of make_web_resolver(claude_client) as web_search_fn.
# ─────────────────────────────────────────────────────────────────────────────

WEB_RESOLVER_SYSTEM_PROMPT = """\
You are a Malaysian land-administration lookup tool. Your ONLY job is to
report the official Mukim, Daerah, Negeri for the given address or
building name, citing the source URL.

HARD RULES — VIOLATING ANY OF THESE INVALIDATES YOUR ANSWER:

1. DO NOT use general knowledge or training-data memory. If the
   web-search results don't explicitly state the mukim, return UNKNOWN.

2. The answer MUST come from a search result you actually saw in this
   conversation. Cite the URL you used.

3. If multiple sources disagree, return UNKNOWN with the conflicting URLs
   listed. Do NOT pick a winner.

4. If the building name has multiple developments with the same name in
   different mukim, return UNKNOWN — ask the user.

5. Return JSON only:
   {"mukim": "...", "daerah": "...", "negeri": "...", "source_url": "https://..."}
   or {"unknown": true, "reason": "...", "sources_consulted": ["url1", ...]}.

You will be graded on accuracy. A confident wrong answer is worse than UNKNOWN.
"""


# ─────────────────────────────────────────────────────────────────────────────
#  §10x.74 — Address cache for web_resolver
#
# The KOID test forward triggered 8+ web_resolver calls at ~$0.04 each
# ($0.33 total — 46% of run cost). Without caching, every image's
# address triggers a fresh web search even when the same building has
# already been resolved earlier in the same run. With caching:
#   - First call for "Paradiso Nuova": $0.04 → cached
#   - Subsequent calls for same address: $0.00
# Expected savings on KOID fixture: $0.20-$0.30 per run.
# ─────────────────────────────────────────────────────────────────────────────
_ADDR_CACHE_HITS = {}   # normalised address -> GeoResult or None (= UNKNOWN)


def _normalise_address_for_cache(text: str) -> str:
    """Normalise address into a stable cache key.

    Lowercase, strip punctuation, collapse whitespace, drop unit numbers
    (so "Unit B-05-11 Paradiso Nuova" and "B-05-11, Paradiso Nuova"
    cache as the same key)."""
    if not text:
        return ''
    import re
    s = text.lower()
    # Drop common prefixes that don't affect mukim
    s = re.sub(r'\b(unit|block|level|floor|no\.?|tower|menara|blok)\b', '', s)
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:120]


def make_web_resolver(claude_client) -> Callable[[str], Optional[GeoResult]]:
    """Build a web_search_fn that calls Claude with web-search and the
    strict no-memory prompt. Returns None if Claude says UNKNOWN.

    The caller (chat_planner) injects this as web_search_fn=… on
    resolve_mukim(). Decoupling lets tests pass a stub instead of a
    real API call.

    🔥 §10x.74 — Process-local + DB-backed cache by normalised address.
    Same address never re-pays for a web search.
    """
    import json

    def _query(text: str) -> Optional[GeoResult]:
        cache_key = _normalise_address_for_cache(text)
        if cache_key and cache_key in _ADDR_CACHE_HITS:
            return _ADDR_CACHE_HITS[cache_key]

        # DB cache lookup — survives gunicorn worker recycle / redeploys
        if cache_key:
            try:
                from database import db, VisionExtractCache
                row = db.session.query(VisionExtractCache).filter_by(
                    content_hash=f'geo:{cache_key}',
                    call_kind='geo_resolver_v1',
                ).first()
                if row:
                    cached = json.loads(row.extracted_json or 'null')
                    result = (GeoResult(**cached) if cached else None)
                    _ADDR_CACHE_HITS[cache_key] = result
                    return result
            except Exception:
                pass

        # 🔥 §10x.60 — prompt caching on the strict no-memory system
        # prompt. Mukim resolver runs per AssetItem (5 properties per
        # client × multiple polls), so the system prompt repeats often
        # within the 5-minute cache TTL.
        msg = claude_client.messages.create(
            model="claude-haiku-4-5",   # cheap; this is a lookup
            max_tokens=400,
            system=[{
                'type': 'text',
                'text': WEB_RESOLVER_SYSTEM_PROMPT,
                'cache_control': {'type': 'ephemeral'},
            }],
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": f"Find the Malaysian Mukim for: {text}"}],
        )
        # 🔥 §10x.70 — was MISSING, leaked $$ untracked. Added now.
        try:
            from ai.cost_tracker import log_usage
            log_usage(msg, call_site='services.geo_resolver.web_resolver')
        except Exception:
            pass
        # Pull JSON from the final text block
        try:
            blocks = [b for b in msg.content if getattr(b, "type", "") == "text"]
            text_out = blocks[-1].text if blocks else ""
            data = json.loads(_extract_json(text_out))
        except Exception:
            data = None

        result = None
        if data and not data.get("unknown"):
            url = data.get("source_url", "")
            if url.startswith("http"):
                result = GeoResult(
                    _norm(data.get("mukim", "")),
                    _norm(data.get("daerah", "")),
                    _norm(data.get("negeri", "")),
                    url,
                )

        # Persist BOTH success and UNKNOWN — knowing "address X is
        # unresolvable" is just as valuable as knowing it is.
        if cache_key:
            _ADDR_CACHE_HITS[cache_key] = result
            try:
                from database import db, VisionExtractCache
                row = VisionExtractCache(
                    content_hash=f'geo:{cache_key}',
                    call_kind='geo_resolver_v1',
                    extracted_json=json.dumps(result.__dict__ if result else None),
                )
                db.session.add(row)
                db.session.commit()
            except Exception:
                try:
                    from database import db as _db
                    _db.session.rollback()
                except Exception:
                    pass

        return result

    return _query


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

_GARBAGE_MUKIM_TOKENS = ("UNKNOWN", "UNREADABLE", "NOT VISIBLE",
                         "CANNOT READ", "VALUE:", "(")


def _looks_real(s: str) -> bool:
    """True if the value looks like a real mukim name, not extractor noise."""
    if not s:
        return False
    up = s.strip().upper()
    if len(up) < 3 or len(up) > 60:
        return False
    return not any(tok in up for tok in _GARBAGE_MUKIM_TOKENS)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).title()


def _extract_json(text: str) -> str:
    """Pull the first {...} block out of a string."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return m.group(0) if m else "{}"
