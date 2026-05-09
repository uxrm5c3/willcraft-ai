"""§10x.48 Canonical asset-matching pipeline.

This module implements the six-stage flow burned in to CLAUDE.md §10x.48.
Every previous matching rule (§10g/§10ha/§10hb/§10he/§10hf/§10hg/§10i/
§10x.18/§10x.43/§10x.46) is a CONSTRAINT on a specific stage below.

NEVER add cross-stage shortcuts. NEVER mix stage logic. If a callsite
needs to know "was this gift bound to a real Document?", read the
binding object — don't recompute matching inline.

Stages:
  0. parse_canonical_assets(cid)        → AssetItem[]   (message ∪ AI Summary)
  1. group_documents(cid)               → DocGroup[]    (sibling clusters)
  2. bind_assets(asset_items, groups)   → Binding[]     (Tier A/B/C/D, one-claim-only)
  3. residuals(asset_items, bindings, groups) → DocGroup[]  (unbound)
  4. build_gift(asset_item, binding)    → Gift dict     (field-source priority)

Stage 5 (walkthrough) lives in ai/chat_planner.py — this module returns
data, not chat replies. Stage 6 (replay) is just calling Stages 0-4
again on every chat turn.
"""
from __future__ import annotations
import re
import json
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict


# ─────────────────────────────────────────────────────────────────────
# §10hc compliant mukim resolver — uses services.geo_resolver which
# enforces citation per entry + falls back to web-search. NEVER trusts
# training-memory shortcuts.
# ─────────────────────────────────────────────────────────────────────
# Backward-compat surface: callers still use resolve_mukim_from_address
# but the implementation now delegates to the curated+web-search resolver.
# `_GEO_BRIDGE` is intentionally REMOVED from this module. Per §10hc rule
# "NEVER assert mukim from memory" — having a hardcoded table here was
# the §10hc violation the user caught (Marina Cove → Plentong was wrong).

_WEB_SEARCH_FN = None  # lazily initialised on first call
_WEB_CLUES_FN = None
_WEB_CLUES_CACHE: Dict[str, Optional[Dict[str, Any]]] = {}


def _get_web_search_fn():
    """Build (or reuse) the Claude+web-search resolver from
    services.geo_resolver. None if Anthropic API isn't configured."""
    global _WEB_SEARCH_FN
    if _WEB_SEARCH_FN is not None:
        return _WEB_SEARCH_FN
    try:
        import anthropic
        from config import ANTHROPIC_API_KEY
        from services.geo_resolver import make_web_resolver
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        _WEB_SEARCH_FN = make_web_resolver(client)
        return _WEB_SEARCH_FN
    except Exception:
        return None


def _get_web_clues_fn():
    """Build (or reuse) the §10hf property-clues searcher. Returns a
    callable(address) → dict|None. Caches results per-process."""
    global _WEB_CLUES_FN
    if _WEB_CLUES_FN is not None:
        return _WEB_CLUES_FN
    try:
        import anthropic
        from config import ANTHROPIC_API_KEY
        from services.web_property_clues import search_property_clues
    except Exception:
        return None
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def _query(address: str) -> Optional[Dict[str, Any]]:
        if not address or len(address.strip()) < 5:
            return None
        key = address.strip().lower()[:200]
        if key in _WEB_CLUES_CACHE:
            return _WEB_CLUES_CACHE[key]
        try:
            clues = search_property_clues(address, client)
        except Exception:
            clues = None
        result = None
        if clues is not None:
            result = {
                'type':          clues.type,
                'tenure':        clues.tenure,
                'locality':      clues.locality,
                'mukim':         clues.mukim,
                'daerah':        clues.daerah,
                'negeri':        clues.negeri,
                'building_name': clues.building_name,
                'postcode':      clues.postcode,
                'sources':       list(clues.sources),
            }
        _WEB_CLUES_CACHE[key] = result
        return result

    _WEB_CLUES_FN = _query
    return _WEB_CLUES_FN


# Per-process web-search result cache (key=lowercased addr → tuple).
# Successful web-search results live here for the lifetime of the process,
# so a chat with N polls × 5 properties only does 5 web searches total
# (not 5N). Process restarts drop the cache; ok.
_RESOLVER_CACHE: Dict[str, Optional[Tuple[str, str, str]]] = {}


def resolve_mukim_from_address(addr: str) -> Optional[Tuple[str, str, str]]:
    """§10hc compliant — resolves via services.geo_resolver which:
      1. Checks curated cache (every entry has a citation)
      2. Falls back to live Claude web-search with anti-memory prompt
      3. Raises GeoUnknown if nothing trustworthy found

    Returns (mukim, daerah, negeri) or None. The caller treats None as
    "unknown — ask the user" per §10hc step 6.
    """
    if not addr:
        return None
    key = addr.strip().lower()[:200]
    if key in _RESOLVER_CACHE:
        return _RESOLVER_CACHE[key]
    try:
        from services.geo_resolver import resolve_mukim, GeoUnknown
    except Exception:
        return None
    web_fn = _get_web_search_fn()
    try:
        gr = resolve_mukim(addr, web_search_fn=web_fn)
        result = (gr.mukim, gr.daerah, gr.negeri)
    except GeoUnknown:
        result = None
    except Exception:
        result = None
    _RESOLVER_CACHE[key] = result
    return result


# ─────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────
@dataclass
class AssetItem:
    """§10x.48 Stage 0 — canonical asset record from message ∪ AI Summary."""
    kind: str                          # 'property' | 'bank' | 'insurance' | 'vehicle'
    ai_index: int
    fields: Dict[str, Any] = field(default_factory=dict)
    message_line: str = ''
    message_ts: Optional[str] = None
    beneficiary_text: str = ''
    conflicts_flagged: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DocGroup:
    """§10x.48 Stage 1 — cluster of Documents representing one physical asset."""
    group_id: str
    document_ids: List[str] = field(default_factory=list)
    kind: str = ''
    merged_extracted: Dict[str, Any] = field(default_factory=dict)
    msg_id: Optional[str] = None
    created_at_min: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Binding:
    """§10x.48 Stage 2 — one AssetItem ↔ at-most-one DocGroup."""
    ai_index: int
    group_id: Optional[str]
    tier: str          # 'A' | 'B' | 'C' | 'D' (D = no binding / H3)
    match_via: str     # 'lot_match' | 'title_match' | 'mukim_token' | 'temporal' | 'h3'
    confidence: str    # 'high' | 'medium-high' | 'medium' | 'h3'
    evidence: str = '' # human-readable for the card

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────
# Helpers (cleaning, tokenisation)
# ─────────────────────────────────────────────────────────────────────
_AI_NOISE_PREFIXES = re.compile(
    r'^\s*(?:VALUE\s*[:\-]?\s*|LOT\s+|PTD\s+|TITLE\s+|GERAN\s+|HAKMILIK\s+|HSD\s+|HSM\s+)',
    re.IGNORECASE,
)
_AI_NOISE_GARBAGE = re.compile(
    r'(?:UNREADABLE|CANNOT\s+READ|NOT\s+VISIBLE|BLURRED|UNKNOWN|N/?A)',
    re.IGNORECASE,
)


def clean_id(s: str) -> str:
    """§10aa — strip 'VALUE:', 'LOT', '(unreadable)' etc. from an OCR field."""
    if not s:
        return ''
    s = str(s).strip()
    s = _AI_NOISE_PREFIXES.sub('', s)
    s = re.sub(r'\([^)]*\)', '', s).strip()  # drop parenthetical comments
    if _AI_NOISE_GARBAGE.search(s):
        return ''
    return s.strip()


def digits(s: str) -> str:
    return re.sub(r'\D', '', clean_id(s))


_STOPWORDS = {
    'unit', 'condominium', 'condo', 'apartment', 'house', 'shop', 'street',
    'jalan', 'lorong', 'taman', 'bandar', 'pangsapuri', 'kawasan', 'no',
    'block', 'level', 'floor', 'storey', 'lot', 'ptd', 'hsd', 'geran',
    'hakmilik', 'mukim', 'daerah', 'negeri', 'state', 'district',
    'malaysia', 'singapore', 'johor', 'bahru', 'sole', 'owner', 'joint',
    'with', 'share', 'and', 'or', 'the', 'of', 'in', 'a', 'an',
}


def distinctive_tokens(text: str) -> set:
    """Return the lowercase tokens worth matching on — drops stopwords,
    keeps unit-like patterns ('c-30-08'), building names, street names,
    postcodes.

    🔥 §10x.50 Bug F — drop pure short-number tokens like '10' that
    cause false positives ('10 Jalan Sri Laguna' wrongly matching
    '10 Marsiling Lane'). Pure numeric tokens must be ≥4 digits to
    qualify as distinctive (postcodes, lot numbers, unit serials).
    """
    if not text:
        return set()
    raw = re.findall(r'[a-z0-9]+(?:[-/][a-z0-9]+)*', text.lower())
    out = set()
    for t in raw:
        if t in _STOPWORDS:
            continue
        if len(t) < 2:
            continue
        # Pure-number short tokens are too generic
        if re.fullmatch(r'\d{1,3}', t):
            continue
        out.add(t)
    return out


# ─────────────────────────────────────────────────────────────────────
# STAGE 0 — Parse canonical AssetItem list
# ─────────────────────────────────────────────────────────────────────
def parse_canonical_assets(client_id: str) -> List[AssetItem]:
    """§10x.48 Stage 0 — read AI Summary + raw forward text and return
    the merged AssetItem list. Fields are the UNION of both sources;
    AI-Summary loses info, raw text doesn't."""
    if not client_id:
        return []

    # Reuse the existing AI-Summary parsers from chat_planner — they
    # already handle both the AI Summary card and raw_forward_text fallback.
    try:
        from ai.chat_planner import (_extract_ai_summary_properties,
                                      _extract_ai_summary_banks,
                                      _extract_ai_summary_insurance)
    except Exception:
        return []

    items: List[AssetItem] = []

    # 🔥 §10x.50 Stage 0 web-clue enrichment — per §10hf, web-search every
    # property address to derive verified (building_name, type, mukim,
    # postcode). These become additional Tier B tokens that bridge
    # message-vocabulary (street format) ↔ OCR-vocabulary (land-registry).
    # Results cached per-process so 5 props × N polls = 5 searches total.
    web_clues_fn = _get_web_clues_fn()

    for i, p in enumerate(_extract_ai_summary_properties(client_id) or []):
        # §10x.48 Stage 0 invariant — `fields` is the union. The current
        # parsers already fold raw text into AI Summary, so what we get
        # IS the union (with the §10x.46 R5 block-parser fix). We still
        # apply §10ha bridge here so callers don't have to.
        fields = dict(p)
        bridged = resolve_mukim_from_address(fields.get('address') or fields.get('name') or '')
        if bridged and not fields.get('mukim'):
            fields['mukim'], fields['daerah'], fields['negeri'] = bridged
        elif bridged and not fields.get('daerah'):
            fields['daerah'] = bridged[1]
            fields.setdefault('negeri', bridged[2])

        # §10hf web-clues: building name, type, postcode, etc.
        # 🔥 §10x.107 — only call web_search when it's actually NEEDED.
        # Web search costs ~$0.063/call (token + $0.01 web_search fee).
        # SKIP when we already have enough signal to bind:
        #   - mukim known (via §10ha bridge or AI Summary) AND
        #   - either lot OR title number present (Tier A will succeed)
        # In those cases Tier A/B can match without web_clues, so calling
        # web_search is pure cost waste. Cache hits are free (§10x.104)
        # but cache misses pay the full price.
        _addr_for_clues = fields.get('address') or fields.get('name') or ''
        _has_mukim = bool(fields.get('mukim'))
        _has_lot_or_title = bool(fields.get('lot') or fields.get('title'))
        _need_web_clues = bool(_addr_for_clues) and not (_has_mukim and _has_lot_or_title)
        if web_clues_fn and _need_web_clues:
            clues = web_clues_fn(_addr_for_clues)
            if clues:
                if clues.get('building_name'):
                    fields['_web_building'] = clues['building_name']
                if clues.get('locality'):
                    fields['_web_locality'] = clues['locality']
                if clues.get('postcode'):
                    fields['_web_postcode'] = clues['postcode']
                if clues.get('type'):
                    fields['_web_type'] = clues['type']
                # Web mukim trumps AI Summary mukim ONLY if cited (always
                # is — search_property_clues requires citation per §10hf)
                if clues.get('mukim') and not fields.get('mukim'):
                    fields['mukim'] = clues['mukim']
                if clues.get('daerah') and not fields.get('daerah'):
                    fields['daerah'] = clues['daerah']
                if clues.get('negeri') and not fields.get('negeri'):
                    fields['negeri'] = clues['negeri']
                if clues.get('sources'):
                    fields['_web_sources'] = list(clues['sources'])

        items.append(AssetItem(
            kind='property',
            ai_index=i,
            fields=fields,
            message_line=fields.get('address') or fields.get('name') or '',
            beneficiary_text=fields.get('beneficiary') or '',
        ))

    base = len(items)
    for i, b in enumerate(_extract_ai_summary_banks(client_id) or []):
        items.append(AssetItem(
            kind='bank',
            ai_index=base + i,
            fields=dict(b),
            beneficiary_text=b.get('beneficiary') or '',
        ))

    base = len(items)
    for i, ins in enumerate(_extract_ai_summary_insurance(client_id) or []):
        items.append(AssetItem(
            kind='insurance',
            ai_index=base + i,
            fields=dict(ins),
            beneficiary_text=ins.get('beneficiary') or '',
        ))

    return items


# ─────────────────────────────────────────────────────────────────────
# STAGE 1 — Group documents
# ─────────────────────────────────────────────────────────────────────
def group_documents(client_id: str) -> List[DocGroup]:
    """§10x.48 Stage 1 — cluster Documents into DocGroups via the seven
    cluster signals. Stage 2 binds GROUPS, never individual Documents.
    """
    if not client_id:
        return []
    try:
        from database import Document
    except Exception:
        return []

    docs = Document.query.filter_by(client_id=client_id).all()
    docs = [d for d in docs if (d.category or '') not in ('deleted',)]
    if not docs:
        return []

    # Build a parsed-once view of each doc
    parsed = []
    for d in docs:
        try:
            ex = json.loads(d.extracted_data or '{}') or {}
        except Exception:
            ex = {}
        parsed.append({
            'id': d.id,
            'category': d.category,
            'created_at': d.created_at,
            'content_hash': getattr(d, 'content_hash', None),
            'lot': digits(ex.get('lot_number') or ''),
            'title': digits(ex.get('title_number') or ''),
            'addr': (ex.get('property_address') or '').strip().lower(),
            'acct': digits(ex.get('account_number') or ''),
            'policy': digits(ex.get('policy_number') or ''),
            'is_strata': bool(re.search(r'strata|/', (ex.get('title_number') or ''))) or
                         'strata' in (ex.get('title_type') or '').lower(),
            'msg_id': None,  # filled below if attachments_json links it
            'extracted': ex,
        })

    # Map doc → delivering ChatMessage (sibling rule)
    try:
        from database import ChatMessage, ChatSession
        sess_ids = [s.id for s in ChatSession.query.filter_by(client_id=client_id).all()]
        if sess_ids:
            for m in ChatMessage.query.filter(ChatMessage.session_id.in_(sess_ids),
                                              ChatMessage.role == 'user').all():
                try:
                    aids = json.loads(m.attachments_json or '[]') or []
                except Exception:
                    aids = []
                for did in aids:
                    for p in parsed:
                        if p['id'] == did and not p['msg_id']:
                            p['msg_id'] = m.id
                            break
    except Exception:
        pass

    # Union-find by signal
    parent = {p['id']: p['id'] for p in parsed}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            a, b = parsed[i], parsed[j]
            # 1. content_hash equal
            if a['content_hash'] and a['content_hash'] == b['content_hash']:
                union(a['id'], b['id'])
                continue
            # 7. strata exception — same lot but different title → DO NOT merge
            if a['lot'] and a['lot'] == b['lot']:
                if (a['is_strata'] or b['is_strata']) and a['title'] and b['title'] \
                   and a['title'] != b['title']:
                    continue
                # 2. same lot+title (or same lot, no title conflict)
                if not a['title'] or not b['title'] or a['title'] == b['title']:
                    union(a['id'], b['id'])
                    continue
            # 3. same address (non-strata)
            if a['addr'] and a['addr'] == b['addr'] and not (a['is_strata'] or b['is_strata']):
                union(a['id'], b['id'])
                continue
            # 4. same account
            if a['acct'] and a['acct'] == b['acct'] and len(a['acct']) >= 6:
                union(a['id'], b['id'])
                continue
            # 5. same policy
            if a['policy'] and a['policy'] == b['policy'] and len(a['policy']) >= 4:
                union(a['id'], b['id'])
                continue
            # 6. sibling rule — same msg_id AND no conflicting identifiers
            if a['msg_id'] and a['msg_id'] == b['msg_id']:
                conflict = (
                    (a['lot'] and b['lot'] and a['lot'] != b['lot']) or
                    (a['title'] and b['title'] and a['title'] != b['title']) or
                    (a['acct'] and b['acct'] and a['acct'] != b['acct']) or
                    (a['policy'] and b['policy'] and a['policy'] != b['policy'])
                )
                if not conflict and a['category'] == b['category']:
                    union(a['id'], b['id'])

    # Build DocGroup objects from union-find
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for p in parsed:
        r = find(p['id'])
        groups.setdefault(r, []).append(p)

    out: List[DocGroup] = []
    for root, members in groups.items():
        # Merge extracted fields, preferring non-empty, flagging conflicts
        merged: Dict[str, Any] = {}
        for key in ('lot_number', 'title_number', 'mukim', 'daerah', 'negeri',
                    'property_address', 'account_number', 'policy_number',
                    'institution', 'insurer', 'title_type', 'description',
                    'building_name', 'township', 'owner_name'):
            vals = [(m['extracted'].get(key) or '').strip() for m in members]
            vals = [v for v in vals if v]
            if vals:
                merged[key] = vals[0]

        # 🔥 §10x.50 Bug E — Stage 1 geo-bridge: when OCR mukim is empty
        # but OCR address contains a known township (e.g. 'BANDAR MEDINI
        # ISKANDAR' → Pulai), fill it. Without this, Tier B can't match
        # an image-bound DocGroup to an AssetItem because OCR mukim is
        # blank. Real example: B-05-11 Paradisonuava image had address
        # 'BANDAR MEDINI ISKANDAR' but mukim=''. Now it gets mukim='Pulai'
        # and Tier B mukim_token fires.
        if not merged.get('mukim'):
            blob = ' '.join([merged.get('property_address') or '',
                              merged.get('description') or '',
                              merged.get('building_name') or '',
                              merged.get('township') or ''])
            bridged = resolve_mukim_from_address(blob)
            if bridged:
                merged['mukim'] = bridged[0]
                if not merged.get('daerah'):
                    merged['daerah'] = bridged[1]
                if not merged.get('negeri'):
                    merged['negeri'] = bridged[2]

        # 🔥 §10x.50 Bug C — typo-tolerant mukim canonicalisation. OCR
        # frequently emits 'Pientong', 'Plentongy', 'Plentong, Johor Bahru'.
        # If OCR mukim is close to a known mukim name (substring or one-edit
        # distance), normalise to canonical form so Tier B equality holds.
        if merged.get('mukim'):
            cm = merged['mukim'].lower().strip()
            cm = re.sub(r'^mukim\s+', '', cm).strip()
            cm = re.sub(r'[,;].*$', '', cm).strip()  # 'Plentong, Johor Bahru' → 'Plentong'
            canonical_mukims = {'plentong', 'pulai', 'tebrau', 'senai', 'johor bahru'}
            if cm in canonical_mukims:
                merged['mukim'] = cm.title()
            else:
                # Fuzzy: edit distance ≤ 2 from any canonical mukim, OR
                # shares ≥ 60% chars at same position. Catches OCR typos
                # like 'pientong' (1 substitution from 'plentong') and
                # 'plentongy' (1 insertion).
                import difflib
                for canon in canonical_mukims:
                    if len(cm) >= 4 and difflib.SequenceMatcher(
                            None, cm, canon).ratio() >= 0.80:
                        merged['mukim'] = canon.title()
                        break

        kind = members[0]['category'] or ''
        if kind in ('property_title', 'property_spa', 'property_tax', 'loan_agreement'):
            kind = 'property'
        elif kind == 'bank_statement':
            kind = 'bank'
        elif kind == 'insurance':
            kind = 'insurance'
        elif kind == 'vehicle':
            kind = 'vehicle'

        msg_id = next((m['msg_id'] for m in members if m['msg_id']), None)
        cmin = min((m['created_at'] for m in members if m['created_at']), default=None)
        out.append(DocGroup(
            group_id=str(root),
            document_ids=[m['id'] for m in members],
            kind=kind,
            merged_extracted=merged,
            msg_id=msg_id,
            created_at_min=cmin.isoformat() if cmin else None,
        ))
    return out


def _claude_semantic_match(unbound_props: List[AssetItem],
                            free_groups: List[DocGroup]) -> Dict[int, Optional[str]]:
    """🔥 §10x.50 Tier C — Claude-semantic property matching.

    Given remaining unbound AssetItems (property kind, after Tier A/B failed)
    and remaining unclaimed DocGroups, ask Claude to pair them. Strict prompt:
    must cite reasoning from doc content, ambiguous → null, training memory
    forbidden.

    Returns {ai_index: group_id_or_None}. Caller is responsible for honouring
    one-claim-only — this function returns Claude's suggestion per AssetItem
    but does NOT enforce uniqueness across results.
    """
    if not unbound_props or not free_groups:
        return {}
    try:
        import anthropic
        from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_CHEAP
    except Exception:
        return {}

    # Build the prompt
    prop_lines = []
    for ai in unbound_props:
        f = ai.fields
        prop_lines.append(
            f"[ai_index={ai.ai_index}] "
            f"address={(f.get('address') or '')[:120]!r} | "
            f"mukim={(f.get('mukim') or '')!r} | "
            f"daerah={(f.get('daerah') or '')!r} | "
            f"ownership={(f.get('ownership') or '')[:80]!r} | "
            f"lot={(f.get('lot') or '')!r} | title={(f.get('title') or '')!r}"
        )
    grp_lines = []
    for g in free_groups:
        ge = g.merged_extracted
        grp_lines.append(
            f"[group_id={g.group_id[:8]}] "
            f"ocr_address={(ge.get('property_address') or '')[:120]!r} | "
            f"mukim={(ge.get('mukim') or '')!r} | "
            f"daerah={(ge.get('daerah') or '')!r} | "
            f"lot={(ge.get('lot_number') or '')!r} | "
            f"title={(ge.get('title_number') or '')!r} | "
            f"owner={(ge.get('owner_name') or '')!r} | "
            f"description={(ge.get('description') or '')[:120]!r}"
        )

    prompt = (
        "You are matching properties from a will-writing client's WhatsApp "
        "message to OCR-extracted property documents they uploaded.\n\n"
        "PROPERTIES from the client's message (each is a real property the "
        "user described):\n" + '\n'.join(prop_lines) + "\n\n"
        "OCR'd UPLOADED DOCUMENTS (each is a candidate match):\n"
        + '\n'.join(grp_lines) + "\n\n"
        "For each property [ai_index=N], pick the SINGLE best matching "
        "[group_id] from the uploaded documents — or null if no doc clearly "
        "matches.\n\n"
        "HARD RULES — VIOLATING ANY INVALIDATES YOUR ANSWER:\n"
        "1. Reasoning MUST come from doc content alone. Do NOT use training "
        "data memory about Malaysian property names / locations.\n"
        "2. Ambiguous = null. If two docs are equally plausible, return null "
        "for that ai_index — better to skip than guess wrong.\n"
        "3. Lot+title agreement (same lot OR same title between message and "
        "OCR) is the strongest signal.\n"
        "4. Same mukim + testator-as-owner is supportive but NOT sufficient "
        "alone — multiple props may share both. Need additional distinctive "
        "signal (lot, title, building name, unit number).\n"
        "5. One group_id must NOT match two ai_indices. If two properties "
        "compete for the same group, pick the better one and return null "
        "for the other.\n"
        "6. Strata exception: same lot but different title = different units, "
        "OK to match different ai_indices to different groups with same lot.\n\n"
        "Return JSON ONLY, no preamble:\n"
        '{"matches": [{"ai_index": 2, "group_id": "abc12345" | null, '
        '"reasoning": "<one sentence>"}]}\n'
    )

    # 🔥 §10x.104 — DB cache for Tier C semantic match. Even with
    # temperature=0 and stable inputs, repeated runs were flipping the
    # AI[2]/AI[3] binding. Hashing the prompt and caching the result
    # locks the verdict for as long as the inputs don't change.
    import hashlib
    prompt_hash = hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:32]
    try:
        from database import db, VisionExtractCache
        row = db.session.query(VisionExtractCache).filter_by(
            content_hash=f'semantic:{prompt_hash}',
            call_kind='asset_pipeline_semantic_v1',
        ).first()
        if row:
            cached_text = row.extracted_json or ''
            if cached_text:
                # Skip the LLM call — feed the cached text into the
                # JSON parser below.
                msg = None
                text = cached_text
                _from_cache = True
            else:
                _from_cache = False
        else:
            _from_cache = False
    except Exception:
        _from_cache = False

    if not _from_cache:
        try:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model=CLAUDE_MODEL_CHEAP,
                max_tokens=2000,
                # 🔥 §10x.99 — deterministic matching. Default temperature 1.0
                # produced different bindings across runs for ambiguous cases
                # (e.g. C-30-08 vs C-05-01 in the same building, or Sri Laguna
                # with sparse OCR evidence). temperature=0 makes Claude pick
                # the highest-probability answer every time — same prompt →
                # same answer. Required by §10x.48/§10x.49 determinism contract.
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            try:
                from ai.cost_tracker import log_usage
                log_usage(msg, call_site='services.asset_pipeline._claude_semantic_match')
            except Exception:
                pass
        except Exception:
            return {}
        text = (msg.content[0].text or '').strip() if msg.content else ''
        # Persist to cache so next call with same prompt returns same result
        try:
            from database import db, VisionExtractCache
            row = VisionExtractCache(
                content_hash=f'semantic:{prompt_hash}',
                call_kind='asset_pipeline_semantic_v1',
                extracted_json=text,
            )
            db.session.merge(row)
            db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
    # Extract JSON
    import re as _re
    m = _re.search(r'\{[\s\S]*\}', text)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {}

    matches = data.get('matches') or []
    by_id = {g.group_id[:8]: g.group_id for g in free_groups}
    out: Dict[int, Optional[str]] = {}
    for entry in matches:
        try:
            ai_idx = int(entry.get('ai_index'))
            short_gid = entry.get('group_id')
        except Exception:
            continue
        if not short_gid:
            continue
        # Resolve short id (8 chars) back to full
        full_gid = by_id.get(short_gid) or next(
            (g.group_id for g in free_groups if g.group_id == short_gid), None)
        if full_gid:
            # Enforce one-claim-only: skip if this group already assigned
            if full_gid in out.values():
                continue
            out[ai_idx] = full_gid
    return out


# ─────────────────────────────────────────────────────────────────────
# §10x.51 — Unified multi-signal candidate scorer
# ─────────────────────────────────────────────────────────────────────
# Replaces the rigid Tier A→B→C→D cascade with a single fused score that
# combines: lot/title direct match + web-verified mukim/daerah + OCR
# token overlap + owner name + temporal proximity + WhatsApp-text
# references-OCR-fragment. Output is ranked candidates per AssetItem so
# the chat can either auto-bind (HIGH score) or surface candidate-with-
# confirm card (MEDIUM score, §10he Step 4) instead of silent H3.

# Score weights (out of 100). Tune over time.
_WEIGHTS = {
    'lot_match':         50,
    'title_match':       50,
    'lot_AND_title':     20,   # bonus when both match
    'account_match':     50,
    'policy_match':      50,
    'mukim_match':       20,
    'daerah_match':       5,
    'unit_token':         8,   # per unit-like token (e.g. b-05-11)
    'generic_token':      3,   # per generic distinctive token
    'token_max':         20,
    'owner_testator':    10,
    'temporal_close':    15,   # ≤ 5 min
    'temporal_med':       7,   # ≤ 30 min
    'msg_text_ref':      15,   # OCR address fragment appears in raw text
    'web_building_in_ocr': 15,
    # Penalties
    'daerah_conflict':  -50,
    'foreign_owner':     -8,   # owner_name has someone NOT in family
}

# Decision thresholds
AUTO_BIND_THRESHOLD = 50   # ≥ HIGH — auto-bind without asking
CANDIDATE_THRESHOLD = 30   # MEDIUM — surface as candidate-with-confirm
# Plus: a candidate MUST have ≥ 2 distinct positive signals (mukim alone
# is not enough; many properties share a mukim).
# Below CANDIDATE_THRESHOLD or with only 1 signal → no candidate, H3


def _score_pair(ai: 'AssetItem',
                g: 'DocGroup',
                raw_forward_text: str = '',
                ai_msg_ts: Optional[Any] = None,
                testator_name: str = '',
                family_names: Optional[set] = None) -> Dict[str, Any]:
    """Score a single (AssetItem, DocGroup) pair across ALL signals.

    Returns:
        {'score': int, 'components': {weight_key: value, ...}, 'evidence': str}

    Components are kept verbose so the candidate card can show the user
    exactly WHY this is a likely match.
    """
    components: Dict[str, int] = {}
    evidence: List[str] = []

    af = ai.fields or {}
    ge = g.merged_extracted or {}

    # ── Direct identifier matches (Tier A signals) ──
    ai_lot   = digits(af.get('lot') or '')
    ai_title = digits(af.get('title') or '')
    ai_acct  = digits(af.get('account_number') or '')
    ai_pol   = digits(af.get('policy_number') or '')
    g_lot    = digits(ge.get('lot_number') or '')
    g_title  = digits(ge.get('title_number') or '')
    g_acct   = digits(ge.get('account_number') or '')
    g_pol    = digits(ge.get('policy_number') or '')

    lot_hit = bool(ai_lot and g_lot and len(ai_lot) >= 3 and ai_lot == g_lot)
    title_hit = bool(ai_title and g_title and len(ai_title) >= 4 and ai_title == g_title)
    if lot_hit:
        components['lot_match'] = _WEIGHTS['lot_match']
        evidence.append(f'lot {ai_lot} matches OCR')
    if title_hit:
        components['title_match'] = _WEIGHTS['title_match']
        evidence.append(f'title {ai_title} matches OCR')
    if lot_hit and title_hit:
        components['lot_AND_title'] = _WEIGHTS['lot_AND_title']
    if ai_acct and g_acct and len(ai_acct) >= 6 and ai_acct == g_acct:
        components['account_match'] = _WEIGHTS['account_match']
        evidence.append(f'account {ai_acct} matches OCR')
    if ai_pol and g_pol and len(ai_pol) >= 4 and ai_pol == g_pol:
        components['policy_match'] = _WEIGHTS['policy_match']
        evidence.append(f'policy {ai_pol} matches OCR')

    # ── Mukim / daerah agreement ──
    ai_mukim = (af.get('mukim') or '').strip().lower()
    ai_daerah = (af.get('daerah') or '').strip().lower()
    g_mukim = (ge.get('mukim') or '').strip().lower()
    g_daerah = (ge.get('daerah') or '').strip().lower()
    if ai_mukim and g_mukim and ai_mukim == g_mukim:
        components['mukim_match'] = _WEIGHTS['mukim_match']
        evidence.append(f'Mukim {ai_mukim.title()} agrees')
    if ai_daerah and g_daerah:
        # 'johor bahru' may appear with extra junk like 'johor bahru, johor'
        ai_d = re.sub(r'[,;].*$', '', ai_daerah).strip()
        g_d  = re.sub(r'[,;].*$', '', g_daerah).strip()
        if ai_d and g_d and ai_d == g_d:
            components['daerah_match'] = _WEIGHTS['daerah_match']
        elif ai_d and g_d and ai_d != g_d and 'johor' in (ai_d + g_d):
            # Different daerah within Johor — might be wrong region
            components['daerah_conflict'] = _WEIGHTS['daerah_conflict']
            evidence.append(f'⚠ daerah conflict ({ai_d} vs {g_d})')

    # ── Token overlap (web clues + AI Summary fields ↔ OCR blob) ──
    web_blob = ' '.join([
        af.get('_web_building') or '',
        af.get('_web_locality') or '',
        af.get('_web_postcode') or '',
    ])
    ai_tokens = distinctive_tokens(
        (af.get('address') or '') + ' ' + (af.get('name') or '') + ' ' + web_blob
    )
    g_blob = ' '.join([
        ge.get('property_address') or '',
        ge.get('description') or '',
        ge.get('building_name') or '',
        ge.get('township') or '',
    ])
    g_tokens = distinctive_tokens(g_blob)
    overlap = ai_tokens & g_tokens
    if overlap:
        unit_re = re.compile(r'^[a-z]?-?\d+(?:[-/]\d+)+$')
        unit_hits = [t for t in overlap if unit_re.match(t)]
        score_add = (len(unit_hits) * _WEIGHTS['unit_token'] +
                     (len(overlap) - len(unit_hits)) * _WEIGHTS['generic_token'])
        score_add = min(score_add, _WEIGHTS['token_max'])
        components['token_overlap'] = score_add
        evidence.append(f'tokens: {sorted(overlap)[:3]}')

    # ── Web building name appears verbatim in OCR ──
    web_building = (af.get('_web_building') or '').strip().lower()
    if web_building and web_building in g_blob.lower():
        components['web_building_in_ocr'] = _WEIGHTS['web_building_in_ocr']
        evidence.append(f'web building "{web_building}" in OCR')

    # ── Owner name (testator) ──
    g_owner = (ge.get('owner_name') or '').upper()
    if testator_name and testator_name.upper() in g_owner:
        components['owner_testator'] = _WEIGHTS['owner_testator']
        evidence.append(f'owner contains testator')
    elif g_owner and family_names:
        # Owner contains a non-family name → possible different person's property
        owner_tokens = re.findall(r'[A-Z][A-Z\']+', g_owner)
        if owner_tokens and not any(
            any(t in fn.upper() for t in owner_tokens) for fn in family_names
        ):
            components['foreign_owner'] = _WEIGHTS['foreign_owner']

    # ── Temporal proximity ──
    if ai_msg_ts and g.created_at_min:
        try:
            from datetime import datetime
            t_msg = ai_msg_ts if hasattr(ai_msg_ts, 'timestamp') else \
                    datetime.fromisoformat(str(ai_msg_ts).replace('Z', '+00:00'))
            t_grp = datetime.fromisoformat(g.created_at_min.replace('Z', '+00:00')) \
                    if isinstance(g.created_at_min, str) else g.created_at_min
            if t_msg and t_grp:
                gap = abs((t_msg - t_grp).total_seconds())
                if gap <= 300:
                    components['temporal_close'] = _WEIGHTS['temporal_close']
                    evidence.append(f'image within 5 min of message')
                elif gap <= 1800:
                    components['temporal_med'] = _WEIGHTS['temporal_med']
                    evidence.append(f'image within 30 min of message')
        except Exception:
            pass

    # ── Message-text-references-OCR-fragment ──
    # Window is FORWARD-ONLY from the AssetItem's address anchor (the
    # user's pattern is "Address, ownership, share, beneficiary" — info
    # follows the address). Window ends at the next property mention.
    if raw_forward_text and g_blob:
        rt_lc = raw_forward_text.lower()
        ai_addr_lc = (af.get('address') or '').lower()
        anchor_idx = -1
        anchor_len = 0
        if ai_addr_lc:
            for probe_len in (50, 30, 20, 15):
                if len(ai_addr_lc) >= probe_len:
                    probe = ai_addr_lc[:probe_len]
                    pos = rt_lc.find(probe)
                    if pos >= 0:
                        anchor_idx = pos
                        anchor_len = probe_len
                        break
            if anchor_idx < 0:
                for tok in re.findall(r'[a-z]?-?\d+(?:[-/]\d+)+', ai_addr_lc):
                    pos = rt_lc.find(tok)
                    if pos >= 0:
                        anchor_idx = pos
                        anchor_len = len(tok)
                        break
        if anchor_idx >= 0:
            # Window includes the anchor (so unit numbers like 'b-05-11'
            # which ARE the anchor still count) and extends forward 250
            # chars, ending at the next property boundary marker.
            window_start = anchor_idx
            window_end = min(len(rt_lc), window_start + anchor_len + 250)
            # Find next property boundary marker — but only AFTER the
            # anchor probe ends (we don't want to truncate the anchor
            # itself out of the window).
            search_from = anchor_idx + anchor_len
            for m in re.finditer(
                r'\b(?:unit\s+[a-z\d]|unit,\s*[a-z\d]|our\s+(?:house|shop|condo)|'
                r'property\s+\d+\s*[:\-]|apartment\s+[a-z\d])',
                rt_lc[search_from:window_end]
            ):
                window_end = search_from + m.start()
                break
            window = rt_lc[window_start:window_end]
            for frag in re.findall(r'[a-z][a-z0-9\s/\-]{5,40}', g_blob.lower()):
                f = frag.strip()
                if len(f) >= 6 and f in window and f not in (
                        'mukim plentong', 'mukim pulai', 'mukim tebrau',
                        'johor bahru', 'lot lot', 'no. lot'):
                    components['msg_text_ref'] = _WEIGHTS['msg_text_ref']
                    evidence.append(f'OCR fragment "{f[:30]}" near message line')
                    break

    score = sum(components.values())
    return {
        'score': score,
        'components': components,
        'evidence': '; '.join(evidence)[:240],
    }


def rank_candidates(asset_items: List[AssetItem],
                     doc_groups: List[DocGroup],
                     *,
                     raw_forward_text: str = '',
                     ai_msg_timestamps: Optional[Dict[int, Any]] = None,
                     testator_name: str = '',
                     family_names: Optional[set] = None) -> Dict[int, List[Dict[str, Any]]]:
    """For each AssetItem, return a list of (group_id, score, evidence)
    sorted by score DESC. The top candidate is the most likely match;
    subsequent ones may still be plausible.

    Caller decides binding via thresholds:
      - score ≥ AUTO_BIND_THRESHOLD → auto-bind
      - score ≥ CANDIDATE_THRESHOLD → surface as candidate-with-confirm
      - else                        → no signal
    """
    out: Dict[int, List[Dict[str, Any]]] = {}
    ai_msg_timestamps = ai_msg_timestamps or {}
    family_names = family_names or set()
    for ai in asset_items:
        ai_ts = ai_msg_timestamps.get(ai.ai_index)
        cands: List[Dict[str, Any]] = []
        for g in doc_groups:
            if g.kind and ai.kind and g.kind != ai.kind:
                continue
            res = _score_pair(ai, g,
                              raw_forward_text=raw_forward_text,
                              ai_msg_ts=ai_ts,
                              testator_name=testator_name,
                              family_names=family_names)
            if res['score'] > 0:
                cands.append({
                    'group_id': g.group_id,
                    'score': res['score'],
                    'evidence': res['evidence'],
                    'components': res['components'],
                })
        cands.sort(key=lambda c: c['score'], reverse=True)
        out[ai.ai_index] = cands
    return out


def _gather_assetitem_msg_timestamps(client_id: str,
                                       asset_items: List[AssetItem]
                                       ) -> Dict[int, Any]:
    """For each AssetItem, find the timestamp of the user message line
    that names it (used for temporal proximity scoring).

    Heuristic: scan recent user ChatMessages, find the one whose content
    contains the AssetItem's address (or distinctive token), use its
    created_at as the message timestamp for that AssetItem. Fall back
    to the Will record's most-recent inbound timestamp.
    """
    out: Dict[int, Any] = {}
    if not client_id:
        return out
    try:
        from database import ChatMessage, ChatSession
        sess_ids = [s.id for s in ChatSession.query.filter_by(client_id=client_id).all()]
        if not sess_ids:
            return out
        msgs = ChatMessage.query.filter(
            ChatMessage.session_id.in_(sess_ids),
            ChatMessage.role == 'user',
        ).all()
        # Find timestamp of message that mentions this AssetItem.
        # Strategy: try multiple probes (full address, distinctive token,
        # building/street name) — first hit wins. Loosened from rigid
        # 30-char prefix to catch user typos and run-on paragraphs.
        for ai in asset_items:
            f = ai.fields or {}
            addr_lc = (f.get('address') or '').lower()
            name_lc = (f.get('name') or '').lower()
            # Build probes ranked most-distinctive first
            probes: List[str] = []
            # 1. Unit-like tokens (b-05-11, c-30-08)
            for tok in re.findall(r'[a-z]?-?\d+(?:[-/]\d+)+', addr_lc + ' ' + name_lc):
                if len(tok) >= 5:
                    probes.append(tok)
            # 2. Web-derived locality / building name (citation-backed)
            for k in ('_web_building', '_web_locality'):
                v = (f.get(k) or '').lower().strip()
                if v and len(v) >= 5:
                    probes.append(v)
            # 3. First 20 chars of address
            if addr_lc and len(addr_lc) >= 5:
                probes.append(addr_lc[:20])
            # 4. Distinctive multi-word fragments from address
            for m in re.finditer(r'(?:jalan|taman|lorong|bandar|kampung)\s+[a-z][a-z\s\-]{2,30}',
                                   addr_lc):
                probes.append(m.group(0))
            best_ts = None
            for probe in probes:
                p = probe.strip()
                if not p or len(p) < 5:
                    continue
                for m in msgs:
                    mc = (m.content or '').lower()
                    if p in mc:
                        if best_ts is None or m.created_at < best_ts:
                            best_ts = m.created_at
                if best_ts:
                    break  # first probe that hits is enough
            if best_ts:
                out[ai.ai_index] = best_ts
    except Exception:
        pass
    return out


def _gather_match_context(client_id: str) -> Dict[str, Any]:
    """Pull raw_forward_text, testator name, family names from DB once
    so rank_candidates doesn't keep re-fetching."""
    ctx: Dict[str, Any] = {
        'raw_forward_text': '',
        'testator_name': '',
        'family_names': set(),
    }
    if not client_id:
        return ctx
    try:
        from database import Will, Person
        w = (Will.query.filter_by(client_id=client_id, status='draft')
             .filter(Will.deleted_at.is_(None))
             .order_by(Will.updated_at.desc()).first())
        if w and w.step6_data:
            try:
                s6 = json.loads(w.step6_data)
                ctx['raw_forward_text'] = (s6.get('_raw_forward_text') or '')[:8000]
            except Exception:
                pass
        for p in Person.query.filter_by(client_id=client_id).all():
            if (p.relationship or '').lower() == 'testator':
                ctx['testator_name'] = p.full_name or ''
            ctx['family_names'].add((p.full_name or '').upper())
    except Exception:
        pass
    return ctx


# ─────────────────────────────────────────────────────────────────────
# STAGE 2 — Bind AssetItem ↔ DocGroup
# ─────────────────────────────────────────────────────────────────────
def bind_assets(asset_items: List[AssetItem],
                doc_groups: List[DocGroup],
                *,
                client_id: str = '') -> List[Binding]:
    """§10x.48 Stage 2 + §10x.51 — unified multi-signal scorer + greedy
    global one-claim-only.

    Algorithm:
      1. For each AssetItem, score every DocGroup via _score_pair (combines
         lot/title direct + mukim/daerah + token overlap + owner + temporal
         + msg-text-reference + web-building-in-OCR).
      2. Build a flat list of (ai_index, group_id, score) triples.
      3. Sort DESC by score.
      4. Greedy assign: walk the list; bind each pair if neither side is
         already claimed. Tier:
            A = score ≥ AUTO_BIND_THRESHOLD AND has direct identifier match
            B = score ≥ AUTO_BIND_THRESHOLD without direct identifier
            C = CANDIDATE_THRESHOLD ≤ score < AUTO_BIND_THRESHOLD
                (rendered as candidate-with-confirm card per §10he Step 4)
            D = no signal — H3 placeholder
    """
    bindings: Dict[int, Binding] = {}
    claimed: set = set()

    # 🔥 §10x.108 Tier 0 — user-assigned override. The user picked an AI
    # Summary slot for an orphan group via the §10x.108 disambiguation
    # card. Each doc in that group has `_user_assigned_ai_idx=N` in its
    # extracted_data. Honour it BEFORE running the normal cascade so the
    # user's choice can never be overridden by a noisy LLM match.
    for grp in doc_groups:
        ex_merged = grp.merged_extracted or {}
        # The merged extraction picks one doc's fields; check ALL docs
        # in the group for the user-assigned tag.
        try:
            from database import db as _db, Document as _Doc
            user_idx_set = set()
            for did in (grp.document_ids or []):
                _d = _db.session.get(_Doc, did)
                if not _d or not _d.extracted_data:
                    continue
                import json as _json
                try:
                    _ex = _json.loads(_d.extracted_data) or {}
                except Exception:
                    continue
                v = _ex.get('_user_assigned_ai_idx')
                if isinstance(v, int):
                    user_idx_set.add(v)
            if len(user_idx_set) == 1:
                forced_idx = user_idx_set.pop()
                if (forced_idx not in bindings
                    and grp.group_id not in claimed
                    and 0 <= forced_idx < len(asset_items)):
                    bindings[forced_idx] = Binding(
                        ai_index=forced_idx,
                        group_id=grp.group_id,
                        tier='A',
                        match_via='user_assigned',
                        confidence='high',
                        evidence='User explicitly assigned this doc group via §10x.108 card',
                    )
                    claimed.add(grp.group_id)
        except Exception:
            pass

    # Gather context (raw text, testator name, family names) once
    ctx = _gather_match_context(client_id) if client_id else {
        'raw_forward_text': '', 'testator_name': '', 'family_names': set()
    }

    # Build per-AssetItem msg timestamp map (when the user mentioned this
    # AssetItem in the chat — used for temporal proximity)
    ai_ts = _gather_assetitem_msg_timestamps(client_id, asset_items) if client_id else {}

    # Compute ranked candidates per AssetItem
    ranked = rank_candidates(
        asset_items, doc_groups,
        raw_forward_text=ctx['raw_forward_text'],
        ai_msg_timestamps=ai_ts,
        testator_name=ctx['testator_name'],
        family_names=ctx['family_names'],
    )

    # Flatten to (score, ai_idx, group_id, evidence) triples and sort DESC
    flat = []
    for ai_idx, cands in ranked.items():
        for c in cands:
            flat.append((c['score'], ai_idx, c['group_id'], c['evidence'], c['components']))
    flat.sort(key=lambda x: x[0], reverse=True)

    # Greedy assign — only AUTO_BIND_THRESHOLD scores actually bind.
    # MEDIUM-confidence candidates stay in ranked_candidates and surface
    # to the user as candidate-with-confirm cards (§10he Step 4 / Path Y).
    # The user's click on "Yes — this is the property" creates the binding
    # via _try_handle_h3_user_match, NOT here. Auto-binding ambiguous
    # matches violates §10he Step 5 ("NEVER guess").
    for score, ai_idx, group_id, evidence, components in flat:
        if ai_idx in bindings:
            continue
        if group_id in claimed:
            continue
        if score < AUTO_BIND_THRESHOLD:
            break  # below auto-bind — rest are candidates only
        # Reject single-signal "matches" — bare-mukim agreement is too
        # weak even at high score. Need ≥ 2 distinct positive components.
        positive_count = sum(1 for k, v in components.items() if v > 0)
        if positive_count < 2:
            continue
        has_direct_id = any(k in components for k in
                             ('lot_match', 'title_match', 'account_match', 'policy_match'))
        tier = 'A' if has_direct_id else 'B'
        match_via = ('lot_match' if 'lot_match' in components else
                      'title_match' if 'title_match' in components else
                      'account_match' if 'account_match' in components else
                      'policy_match' if 'policy_match' in components else
                      'mukim_token')
        bindings[ai_idx] = Binding(
            ai_index=ai_idx, group_id=group_id,
            tier=tier, match_via=match_via, confidence='high',
            evidence=f'score={score} | {evidence}',
        )
        claimed.add(group_id)

    # Filter ranked candidates → moved BELOW Claude fallback so candidates
    # for AssetItems Claude binds aren't surfaced. (See "filter pass" near
    # end of bind_assets.)

    # Stash ranked candidates so Stage 5 (chat) can render candidate-with-
    # confirm cards for AssetItems that DIDN'T auto-bind (score < AUTO).
    # The chat planner reads this dict via run_pipeline()['ranked_candidates'].
    bind_assets._last_ranked_candidates = ranked

    # Legacy Tier-C Claude semantic fallback — kept for AssetItems still
    # unbound after the multi-signal scoring (in case the scorer missed
    # something Claude can spot). Strict prompt unchanged.
    free_property_groups = [g for g in doc_groups
                             if g.group_id not in claimed and g.kind == 'property']
    # For each unbound AssetItem of property kind, give Claude the
    # AssetItem fields and the unclaimed property DocGroups, ask which
    # one (if any) is the match. Strict prompt: must cite reasoning,
    # ambiguous → null, lot+title agreement strongly preferred.
    # This is the SEMANTIC bridge §10x.46 R4 demands — message uses
    # street format, OCR uses land-registry format, lexical matchers
    # can't bridge them.
    unbound_props = [ai for ai in asset_items
                      if ai.ai_index not in bindings and ai.kind == 'property']
    free_property_groups = [g for g in doc_groups
                             if g.group_id not in claimed and g.kind == 'property']
    if unbound_props and free_property_groups:
        try:
            semantic_results = _claude_semantic_match(unbound_props, free_property_groups)
        except Exception:
            semantic_results = {}
        for ai_idx, group_id in semantic_results.items():
            if group_id and group_id not in claimed:
                # Find the group + AssetItem to compose the binding
                g = next((gg for gg in free_property_groups
                          if gg.group_id == group_id), None)
                if g:
                    bindings[ai_idx] = Binding(
                        ai_index=ai_idx, group_id=group_id,
                        tier='C', match_via='claude_semantic',
                        confidence='medium',
                        evidence='Claude-inferred match (cite kept on gift)',
                    )
                    claimed.add(group_id)

    # ── Tier D: H3 (no binding) ───────────────────────────────────────
    for ai in asset_items:
        if ai.ai_index not in bindings:
            bindings[ai.ai_index] = Binding(
                ai_index=ai.ai_index, group_id=None,
                tier='D', match_via='h3', confidence='h3',
                evidence='No matching DocGroup — text-only / H3 placeholder',
            )

    # ── Filter ranked candidates AFTER all binding paths complete ─────
    # Skip AssetItems that are auto-bound; skip groups already claimed.
    # Surface top-3 score≥CANDIDATE_THRESHOLD candidates with ≥2 signals.
    filtered_ranked: Dict[int, List[Dict[str, Any]]] = {}
    for ai_idx, cands in ranked.items():
        b = bindings.get(ai_idx)
        if b and b.tier != 'D':
            continue   # auto-bound; no candidate question needed
        kept = []
        for c in cands:
            if c['score'] < CANDIDATE_THRESHOLD:
                continue
            pos_count = sum(1 for k, v in (c.get('components') or {}).items() if v > 0)
            if pos_count < 2:
                continue
            if c['group_id'] in claimed:
                continue
            kept.append(c)
        if kept:
            filtered_ranked[ai_idx] = kept[:3]

    # ── §10x.52 — Elimination signal (user directive May 2026) ────────
    # "Use the elimination. The lowest confidence address with unmatch
    # images that has PTD and HSD and close time proximity to the address."
    #
    # For each unbound AssetItem, find unclaimed property DocGroups that:
    #   • have a non-empty PTD/Lot OR HSD/Title number
    #   • were uploaded within 30 minutes of the user's message about
    #     this AssetItem (using the timestamp from _gather_assetitem_msg_timestamps)
    # Surface them as candidates with elevated score (40) so they pass the
    # CANDIDATE_THRESHOLD and reach the user as candidate-with-confirm.
    for ai in asset_items:
        if ai.kind != 'property':
            continue
        b = bindings.get(ai.ai_index)
        if b and b.tier != 'D':
            continue
        if filtered_ranked.get(ai.ai_index):
            continue
        ai_ts_for_ai = ai_ts.get(ai.ai_index)
        if not ai_ts_for_ai:
            continue
        candidates_temporal: List[Dict[str, Any]] = []
        for g in doc_groups:
            if g.kind != 'property':
                continue
            if g.group_id in claimed:
                continue
            ge = g.merged_extracted
            has_id = bool(digits(ge.get('lot_number') or '') or
                          digits(ge.get('title_number') or ''))
            if not has_id:
                continue
            if not g.created_at_min:
                continue
            try:
                from datetime import datetime
                t_msg = (ai_ts_for_ai if hasattr(ai_ts_for_ai, 'timestamp')
                         else datetime.fromisoformat(str(ai_ts_for_ai).replace('Z', '+00:00')))
                t_grp = (datetime.fromisoformat(g.created_at_min.replace('Z', '+00:00'))
                         if isinstance(g.created_at_min, str) else g.created_at_min)
                # Strip tz if mixed
                if t_msg.tzinfo is not None and t_grp.tzinfo is None:
                    t_msg = t_msg.replace(tzinfo=None)
                elif t_grp.tzinfo is not None and t_msg.tzinfo is None:
                    t_grp = t_grp.replace(tzinfo=None)
                gap = abs((t_msg - t_grp).total_seconds())
            except Exception:
                continue
            if gap > 1800:   # > 30 min — too far
                continue
            candidates_temporal.append({
                'group_id': g.group_id,
                'score': 40 if gap <= 300 else 30,
                'evidence': (
                    f"Has lot/title (lot {ge.get('lot_number','?')}, title "
                    f"{ge.get('title_number','?')}) and uploaded "
                    f"{int(gap)}s from message about this property"
                ),
                'components': {
                    'temporal_close' if gap <= 300 else 'temporal_med':
                        15 if gap <= 300 else 7,
                    'has_id': 25,
                },
            })
        if candidates_temporal:
            candidates_temporal.sort(key=lambda c: c['score'], reverse=True)
            filtered_ranked[ai.ai_index] = candidates_temporal[:3]

    # ── §10x.51 — "lone in mukim, lot not used elsewhere" suggestion ──
    # For AssetItems still H3 with no score-based candidates, find
    # unclaimed property DocGroups in the same mukim whose lot/title
    # ALSO doesn't appear in any other bound group OR higher-confidence
    # candidate. Real example: Jalan Gunung 4 (mukim Plentong) — three
    # unclaimed Plentong groups exist, two share lot 207922 with the
    # C-05-01 candidate (so they're strata units of THAT building, not
    # Jalan Gunung). The third (85b34a44, lot 127082) has a unique lot
    # not seen anywhere else in the fixture — surface as candidate.

    # Collect lots/titles already accounted for (bound or other candidates)
    accounted_lots: set = set()
    accounted_titles: set = set()
    for b in bindings.values():
        if b.group_id:
            for g in doc_groups:
                if g.group_id == b.group_id:
                    ge = g.merged_extracted
                    accounted_lots.add(digits(ge.get('lot_number') or ''))
                    accounted_titles.add(digits(ge.get('title_number') or ''))
    for cs in filtered_ranked.values():
        for c in cs:
            for g in doc_groups:
                if g.group_id == c['group_id']:
                    ge = g.merged_extracted
                    accounted_lots.add(digits(ge.get('lot_number') or ''))
                    accounted_titles.add(digits(ge.get('title_number') or ''))
    accounted_lots.discard('')
    accounted_titles.discard('')

    for ai in asset_items:
        if ai.kind != 'property':
            continue
        b = bindings.get(ai.ai_index)
        if b and b.tier != 'D':
            continue
        if filtered_ranked.get(ai.ai_index):
            continue
        ai_mukim = (ai.fields.get('mukim') or '').strip().lower()
        if not ai_mukim:
            continue
        eligible = []
        for g in doc_groups:
            if g.kind != 'property':
                continue
            if g.group_id in claimed:
                continue
            ge = g.merged_extracted
            g_mukim = (ge.get('mukim') or '').strip().lower()
            if g_mukim != ai_mukim:
                continue
            g_lot = digits(ge.get('lot_number') or '')
            g_title = digits(ge.get('title_number') or '')
            if not (g_lot or g_title):
                continue
            # Skip groups whose lot/title is already accounted for
            if g_lot and g_lot in accounted_lots:
                continue
            if g_title and g_title in accounted_titles:
                continue
            eligible.append(g)
        if eligible:
            ge_lots = [(g, digits(g.merged_extracted.get('lot_number') or ''),
                         digits(g.merged_extracted.get('title_number') or ''))
                       for g in eligible]
            cands_out = []
            for g, gl, gt in ge_lots[:3]:
                cands_out.append({
                    'group_id': g.group_id,
                    'score': 25,
                    'evidence': (f'Same mukim ({ai_mukim.title()}) + unique lot/title '
                                 f'(lot {gl or "?"}, title {gt or "?"}) — please confirm'),
                    'components': {'lone_in_mukim': 25, 'unique_id': 5},
                })
            filtered_ranked[ai.ai_index] = cands_out

    bind_assets._last_filtered_candidates = filtered_ranked

    # Return in ai_index order
    return [bindings[i] for i in sorted(bindings.keys())]


# ─────────────────────────────────────────────────────────────────────
# STAGE 3 — Residuals
# ─────────────────────────────────────────────────────────────────────
def residuals(asset_items: List[AssetItem],
              bindings: List[Binding],
              doc_groups: List[DocGroup]) -> List[DocGroup]:
    """§10x.48 Stage 3 — DocGroups not consumed by Stage 2. The chat
    surfaces these as §10d unverified cards. NEVER auto-creates an
    AssetItem from a residual."""
    claimed = {b.group_id for b in bindings if b.group_id}
    return [g for g in doc_groups if g.group_id not in claimed]


# ─────────────────────────────────────────────────────────────────────
# STAGE 4 — Build merged Gift record
# ─────────────────────────────────────────────────────────────────────
def build_gift(asset_item: AssetItem,
               binding: Binding,
               doc_group: Optional[DocGroup]) -> Dict[str, Any]:
    """§10x.48 Stage 4 — apply field-source priority and produce a Gift
    dict ready to drop into step5_data. Caller adds layer-tracking flags
    and links to the original AssetItem ai_index.

    Field source priority (per §10x.48 Stage 4 table):
      address    → AssetItem.message > AI Summary > DocGroup OCR (lowest)
      lot/title  → AssetItem (message) > DocGroup OCR > AI Summary
      mukim/daerah/negeri → DocGroup OCR > §10ha bridge > web-search
      ownership  → AssetItem.message_line ONLY
      testator_share → derived from ownership idiom (§10x.13)
      co_owners  → AssetItem.message ownership clause; never Person table
    """
    af = asset_item.fields or {}
    de = (doc_group.merged_extracted if doc_group else {}) or {}

    # Address: message > AI Summary > DocGroup OCR
    address = (af.get('address') or '').strip() or (de.get('property_address') or '').strip()

    # Lot/title: message > OCR > AI Summary
    lot = (af.get('lot') or '').strip() or clean_id(de.get('lot_number') or '')
    title = (af.get('title') or '').strip() or clean_id(de.get('title_number') or '')

    # Mukim/daerah/negeri: OCR > geo bridge > (Stage 0 already did the bridge)
    mukim = (de.get('mukim') or '').strip() or (af.get('mukim') or '').strip()
    daerah = (de.get('daerah') or '').strip() or (af.get('daerah') or '').strip()
    negeri = (de.get('negeri') or '').strip() or (af.get('negeri') or '').strip()
    if not mukim:
        bridged = resolve_mukim_from_address(address)
        if bridged:
            mukim, daerah, negeri = bridged[0], (daerah or bridged[1]), (negeri or bridged[2])

    # Ownership / testator_share / co-owners — message text only
    ownership_text = (af.get('ownership') or '').strip()
    testator_share, co_owners = _parse_ownership(ownership_text)

    variant = 'h3'
    if binding.tier == 'A':
        variant = 'h1' if (doc_group and doc_group.kind == 'property') else 'h1'
    elif binding.tier == 'B':
        variant = 'h2'

    if asset_item.kind == 'property':
        return {
            'kind': 'property',
            'asset_type': 'property',
            'ai_summary_idx': asset_item.ai_index,
            '_ai_summary_idx': asset_item.ai_index,
            'document_id': (doc_group.document_ids[0] if (doc_group and doc_group.document_ids)
                            else f'_h3_synth_{asset_item.ai_index}'),
            '_h3_placeholder': binding.tier == 'D',
            '_match_via': binding.match_via,
            '_match_tier': binding.tier,
            '_match_evidence': binding.evidence,
            'variant': variant,
            'property_info': {
                'property_address': address,
                'title_number': title,
                'lot_number': lot,
                'mukim': mukim,
                'daerah': daerah,
                'negeri': negeri,
                'co_owners': co_owners,
                'testator_share': testator_share,
            },
            'property_address': address,
            'title_number': title,
            'lot_number': lot,
            'mukim': mukim,
            'daerah': daerah,
            'negeri': negeri,
            'co_owners': co_owners,
            'testator_share': testator_share,
            'beneficiary_text': asset_item.beneficiary_text,
        }

    if asset_item.kind == 'bank':
        return {
            'kind': 'bank',
            'asset_type': 'bank',
            'ai_summary_idx': asset_item.ai_index,
            '_ai_summary_idx': asset_item.ai_index,
            'document_id': (doc_group.document_ids[0] if (doc_group and doc_group.document_ids)
                            else f'_h3_synth_{asset_item.ai_index}'),
            '_h3_placeholder': binding.tier == 'D',
            '_match_via': binding.match_via,
            '_match_tier': binding.tier,
            'bank_name': af.get('bank_name') or de.get('institution') or '',
            'account_number': af.get('account_number') or de.get('account_number') or '',
            'country': af.get('country') or '',
            'account_type': af.get('account_type') or '',
            'beneficiary_text': asset_item.beneficiary_text,
        }

    if asset_item.kind == 'insurance':
        return {
            'kind': 'insurance',
            'asset_type': 'insurance',
            'ai_summary_idx': asset_item.ai_index,
            '_ai_summary_idx': asset_item.ai_index,
            'document_id': (doc_group.document_ids[0] if (doc_group and doc_group.document_ids)
                            else f'_h3_synth_{asset_item.ai_index}'),
            '_h3_placeholder': binding.tier == 'D',
            '_match_via': binding.match_via,
            '_match_tier': binding.tier,
            'insurer': af.get('insurer') or de.get('insurer') or '',
            'policy_number': af.get('policy_number') or de.get('policy_number') or '',
            'beneficiary_text': asset_item.beneficiary_text,
        }

    return {'kind': asset_item.kind, '_match_via': binding.match_via}


def _parse_ownership(ownership_text: str) -> Tuple[str, List[str]]:
    """Parse the 'Ownership:' or 'I share with ...' clause and return
    (testator_share fraction, co_owner_names)."""
    if not ownership_text:
        return '1/1', []
    t = ownership_text.lower()
    if 'sole' in t and 'joint' not in t:
        return '1/1', []
    share = '1/2'  # default for joint (two-party)
    m = re.search(r'(\d+)\s*/\s*(\d+)', ownership_text)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        # "50/50" idiom — N==D means each party gets equal share. Two-party
        # is the overwhelmingly common case for joint property → 1/2 each.
        if num == den and den >= 2:
            share = '1/2'
        # "1/2", "1/3", "2/3" — explicit fraction
        elif num < den and den <= 10:
            share = f'{num}/{den}'
        # else: default 1/2 (the regex matched something unusable)
    co_owners: List[str] = []
    for m in re.finditer(
        r'\bwith\s+(?:my\s+(?:wife|husband|spouse|son|daughter|father|mother|brother|sister)\s+)?'
        r'((?:[A-Z][A-Za-z]+\s*){1,5})'
        r'(?=,|\s+\(|\s+share|\s+\d|\s*$)',
        ownership_text, re.IGNORECASE,
    ):
        nm = m.group(1).strip()
        nm = re.sub(r'\s+(share|with)\s*$', '', nm, flags=re.IGNORECASE).strip()
        if nm and len(nm) > 2 and nm.lower() not in ('share', 'with'):
            co_owners.append(nm[:80])
    seen = set()
    co_owners = [c for c in co_owners if not (c.lower() in seen or seen.add(c.lower()))]
    return share, co_owners


# ─────────────────────────────────────────────────────────────────────
# §10x.49 — Self-validating pipeline: contract violations raise loudly
# ─────────────────────────────────────────────────────────────────────
class ContractViolation(Exception):
    """Raised when a §10x.48 stage produces output that violates its
    invariants. NEVER catch and swallow this — surface it to the user/
    test/audit. Silent contract violations are how we got into the FUCK
    LIST in the first place."""


def _assert_stage0(asset_items: List[AssetItem]) -> None:
    """Stage 0: every AssetItem has unique ai_index, valid kind."""
    valid_kinds = {'property', 'bank', 'insurance', 'vehicle'}
    seen = set()
    for a in asset_items:
        if a.kind not in valid_kinds:
            raise ContractViolation(
                f'Stage 0: AssetItem[{a.ai_index}] kind={a.kind!r} not in {valid_kinds}'
            )
        if a.ai_index in seen:
            raise ContractViolation(
                f'Stage 0: duplicate ai_index {a.ai_index}'
            )
        seen.add(a.ai_index)


def _assert_stage1(doc_groups: List[DocGroup], all_doc_ids: List[str]) -> None:
    """Stage 1: every Document appears in exactly one DocGroup. No double-membership.
    No empty DocGroup. group_ids unique."""
    seen_doc_ids = set()
    seen_group_ids = set()
    for g in doc_groups:
        if g.group_id in seen_group_ids:
            raise ContractViolation(f'Stage 1: duplicate group_id {g.group_id!r}')
        seen_group_ids.add(g.group_id)
        if not g.document_ids:
            raise ContractViolation(f'Stage 1: empty DocGroup {g.group_id!r}')
        for did in g.document_ids:
            if did in seen_doc_ids:
                raise ContractViolation(
                    f'Stage 1: Document {did} appears in multiple groups'
                )
            seen_doc_ids.add(did)
    # Every input Document should be in some group (no orphans)
    expected = set(all_doc_ids)
    missing = expected - seen_doc_ids
    if missing:
        raise ContractViolation(
            f'Stage 1: {len(missing)} Document(s) not in any group: {sorted(missing)[:5]}'
        )


def _assert_stage2(asset_items: List[AssetItem],
                    bindings: List[Binding]) -> None:
    """Stage 2: one Binding per AssetItem; no group_id bound twice;
    every Binding has a valid tier and match_via."""
    valid_tiers = {'A', 'B', 'C', 'D'}
    valid_via = {'lot_match', 'title_match', 'account_match', 'policy_match',
                  'mukim_token', 'temporal', 'claude_semantic',
                  'multi_signal', 'user_confirmed', 'h3'}
    if len(bindings) != len(asset_items):
        raise ContractViolation(
            f'Stage 2: {len(bindings)} bindings for {len(asset_items)} AssetItems'
        )
    seen_ai = set()
    seen_grp = set()
    for b in bindings:
        if b.ai_index in seen_ai:
            raise ContractViolation(f'Stage 2: duplicate binding for ai_index {b.ai_index}')
        seen_ai.add(b.ai_index)
        if b.tier not in valid_tiers:
            raise ContractViolation(f'Stage 2: invalid tier {b.tier!r}')
        if b.match_via not in valid_via:
            raise ContractViolation(f'Stage 2: invalid match_via {b.match_via!r}')
        if b.tier == 'D' and b.group_id is not None:
            raise ContractViolation(
                f'Stage 2: tier D (H3) must have group_id=None, got {b.group_id!r}'
            )
        if b.tier != 'D' and b.group_id is None:
            raise ContractViolation(
                f'Stage 2: tier {b.tier} must have a group_id'
            )
        if b.group_id is not None:
            if b.group_id in seen_grp:
                raise ContractViolation(
                    f'Stage 2: ONE-CLAIM-ONLY violation — group_id {b.group_id!r} bound twice'
                )
            seen_grp.add(b.group_id)


def _assert_stage4(asset_items: List[AssetItem], gifts: List[Dict[str, Any]]) -> None:
    """Stage 4: every Gift has required fields; address non-empty when
    AssetItem stated one; lot/title preserved if AssetItem had them."""
    if len(gifts) != len(asset_items):
        raise ContractViolation(
            f'Stage 4: {len(gifts)} gifts for {len(asset_items)} AssetItems'
        )
    by_idx = {a.ai_index: a for a in asset_items}
    for g in gifts:
        idx = g.get('_ai_summary_idx')
        if idx is None or idx not in by_idx:
            raise ContractViolation(
                f'Stage 4: gift missing/invalid _ai_summary_idx={idx!r}'
            )
        ai = by_idx[idx]
        if ai.kind == 'property':
            ai_addr = (ai.fields.get('address') or '').strip()
            pi = g.get('property_info') or {}
            gift_addr = (pi.get('property_address') or g.get('property_address') or '').strip()
            if ai_addr and not gift_addr:
                raise ContractViolation(
                    f'Stage 4: property gift[{idx}] address dropped — '
                    f'AssetItem had {ai_addr!r}, gift has empty'
                )
            ai_lot = digits(ai.fields.get('lot') or '')
            ai_title = digits(ai.fields.get('title') or '')
            gift_lot = digits(pi.get('lot_number') or g.get('lot_number') or '')
            gift_title = digits(pi.get('title_number') or g.get('title_number') or '')
            if ai_lot and ai_lot != gift_lot:
                raise ContractViolation(
                    f'Stage 4: property gift[{idx}] lot dropped — '
                    f'AssetItem={ai_lot} gift={gift_lot}'
                )
            if ai_title and ai_title != gift_title:
                raise ContractViolation(
                    f'Stage 4: property gift[{idx}] title dropped — '
                    f'AssetItem={ai_title} gift={gift_title}'
                )
        if not g.get('_match_via'):
            raise ContractViolation(
                f'Stage 4: gift[{idx}] missing _match_via — silent guess (§10he Step 5)'
            )


# ─────────────────────────────────────────────────────────────────────
# Top-level orchestration — run all stages and return the result
# ─────────────────────────────────────────────────────────────────────
def run_pipeline(client_id: str) -> Dict[str, Any]:
    """§10x.48 Stages 0→4 in order. Returns a dict with everything the
    walker / saver / verifier needs.

    🔥 §10x.49 — every stage's output is validated via _assert_stageN
    before the next stage runs. ContractViolation raised on any breach.
    Set `validate=False` only inside unit tests that intentionally
    exercise broken inputs.
    """
    asset_items = parse_canonical_assets(client_id)
    _assert_stage0(asset_items)

    # Collect doc IDs for Stage 1 invariant check
    try:
        from database import Document
        all_doc_ids = [d.id for d in Document.query.filter_by(client_id=client_id).all()
                        if (d.category or '') not in ('deleted',)]
    except Exception:
        all_doc_ids = []

    doc_groups = group_documents(client_id)
    _assert_stage1(doc_groups, all_doc_ids)

    bindings = bind_assets(asset_items, doc_groups, client_id=client_id)
    _assert_stage2(asset_items, bindings)

    res = residuals(asset_items, bindings, doc_groups)
    group_by_id = {g.group_id: g for g in doc_groups}
    gifts = []
    for ai in asset_items:
        b = next((bb for bb in bindings if bb.ai_index == ai.ai_index), None)
        if not b:
            continue
        dg = group_by_id.get(b.group_id) if b.group_id else None
        gifts.append(build_gift(ai, b, dg))
    _assert_stage4(asset_items, gifts)

    # Pull ranked candidates that bind_assets stashed for the chat
    # walker to render candidate-with-confirm cards (Path Y / §10he Step 4).
    ranked = getattr(bind_assets, '_last_ranked_candidates', {}) or {}
    filtered = getattr(bind_assets, '_last_filtered_candidates', {}) or {}
    return {
        'asset_items': [a.to_dict() for a in asset_items],
        'doc_groups': [g.to_dict() for g in doc_groups],
        'bindings': [b.to_dict() for b in bindings],
        'residuals': [g.to_dict() for g in res],
        'gifts': gifts,
        'ranked_candidates': ranked,
        'candidates_for_confirm': filtered,
    }
