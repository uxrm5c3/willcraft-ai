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
# §10ha geographic bridge — kept in sync with ai/chat_planner.py
# ─────────────────────────────────────────────────────────────────────
_GEO_BRIDGE: Dict[str, Tuple[str, str, str]] = {
    # Plentong (Daerah Johor Bahru)
    'seri alam':        ('Plentong', 'Johor Bahru', 'Johor'),
    'bandar seri alam': ('Plentong', 'Johor Bahru', 'Johor'),
    'taman laguna':     ('Plentong', 'Johor Bahru', 'Johor'),
    'sri laguna':       ('Plentong', 'Johor Bahru', 'Johor'),
    'marina cove':      ('Plentong', 'Johor Bahru', 'Johor'),
    'tepian bayu':      ('Plentong', 'Johor Bahru', 'Johor'),
    'pasir gudang':     ('Plentong', 'Johor Bahru', 'Johor'),
    'permas jaya':      ('Plentong', 'Johor Bahru', 'Johor'),
    'masai':            ('Plentong', 'Johor Bahru', 'Johor'),
    # Pulai (Daerah Johor Bahru)
    'medini':           ('Pulai', 'Johor Bahru', 'Johor'),
    'bandar medini':    ('Pulai', 'Johor Bahru', 'Johor'),
    'iskandar puteri':  ('Pulai', 'Johor Bahru', 'Johor'),
    'paradiso nuova':   ('Pulai', 'Johor Bahru', 'Johor'),
    'paradisonuava':    ('Pulai', 'Johor Bahru', 'Johor'),
    'merak kayangan':   ('Pulai', 'Johor Bahru', 'Johor'),
    'nusajaya':         ('Pulai', 'Johor Bahru', 'Johor'),
    # Tebrau (Daerah Johor Bahru)
    'mount austin':     ('Tebrau', 'Johor Bahru', 'Johor'),
    'taman austin':     ('Tebrau', 'Johor Bahru', 'Johor'),
}


def resolve_mukim_from_address(addr: str) -> Optional[Tuple[str, str, str]]:
    """§10ha — return (mukim, daerah, negeri) if any known township appears
    in the address. Memory-free — only consults the curated table.
    Longest key first so 'bandar seri alam' beats 'seri alam'.
    """
    if not addr:
        return None
    al = addr.lower()
    for key in sorted(_GEO_BRIDGE.keys(), key=len, reverse=True):
        if key in al:
            return _GEO_BRIDGE[key]
    return None


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
    postcodes."""
    if not text:
        return set()
    raw = re.findall(r'[a-z0-9]+(?:[-/][a-z0-9]+)*', text.lower())
    out = set()
    for t in raw:
        if t in _STOPWORDS:
            continue
        if len(t) < 2:
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


# ─────────────────────────────────────────────────────────────────────
# STAGE 2 — Bind AssetItem ↔ DocGroup
# ─────────────────────────────────────────────────────────────────────
def bind_assets(asset_items: List[AssetItem],
                doc_groups: List[DocGroup]) -> List[Binding]:
    """§10x.48 Stage 2 — Tier A → B → C → D priority cascade. One-claim-only.
    Greedy by confidence: ALL Tier-A bindings resolve before any Tier-B,
    etc. Returns one Binding per AssetItem (Tier D = no binding / H3)."""
    bindings: Dict[int, Binding] = {}
    claimed: set = set()

    def free_groups(kind: str) -> List[DocGroup]:
        return [g for g in doc_groups
                if g.group_id not in claimed
                and (not kind or g.kind == kind or not g.kind)]

    # ── Tier A: direct identifier match ───────────────────────────────
    for ai in asset_items:
        if ai.ai_index in bindings:
            continue
        ai_lot = digits(ai.fields.get('lot') or '')
        ai_title = digits(ai.fields.get('title') or '')
        ai_acct = digits(ai.fields.get('account_number') or '')
        ai_pol = digits(ai.fields.get('policy_number') or '')
        for g in free_groups(ai.kind):
            ge = g.merged_extracted
            g_lot = digits(ge.get('lot_number') or '')
            g_title = digits(ge.get('title_number') or '')
            g_acct = digits(ge.get('account_number') or '')
            g_pol = digits(ge.get('policy_number') or '')
            tier_a_via = None
            if ai_lot and g_lot and len(ai_lot) >= 3 and ai_lot == g_lot:
                tier_a_via = 'lot_match'
            elif ai_title and g_title and len(ai_title) >= 4 and ai_title == g_title:
                tier_a_via = 'title_match'
            elif ai_acct and g_acct and len(ai_acct) >= 6 and ai_acct == g_acct:
                tier_a_via = 'account_match'
            elif ai_pol and g_pol and len(ai_pol) >= 4 and ai_pol == g_pol:
                tier_a_via = 'policy_match'
            if tier_a_via:
                bindings[ai.ai_index] = Binding(
                    ai_index=ai.ai_index, group_id=g.group_id,
                    tier='A', match_via=tier_a_via, confidence='high',
                    evidence=f'{tier_a_via}: {ai_lot or ai_title or ai_acct or ai_pol}',
                )
                claimed.add(g.group_id)
                break

    # ── Tier B: mukim + token (property only) ─────────────────────────
    for ai in asset_items:
        if ai.kind != 'property' or ai.ai_index in bindings:
            continue
        ai_addr = (ai.fields.get('address') or '').lower()
        ai_mukim = (ai.fields.get('mukim') or '').strip().lower()
        if not ai_mukim:
            bridged = resolve_mukim_from_address(ai_addr) or resolve_mukim_from_address(
                (ai.fields.get('name') or '').lower())
            if bridged:
                ai_mukim = bridged[0].lower()
        if not ai_mukim:
            continue
        ai_tokens = distinctive_tokens(ai_addr + ' ' + (ai.fields.get('name') or ''))
        for g in free_groups('property'):
            ge = g.merged_extracted
            g_mukim = (ge.get('mukim') or '').strip().lower()
            if g_mukim != ai_mukim:
                continue
            g_blob = ' '.join([ge.get('property_address') or '',
                                ge.get('description') or '',
                                ge.get('building_name') or '',
                                ge.get('township') or '']).lower()
            g_tokens = distinctive_tokens(g_blob)
            overlap = ai_tokens & g_tokens
            if overlap:
                bindings[ai.ai_index] = Binding(
                    ai_index=ai.ai_index, group_id=g.group_id,
                    tier='B', match_via='mukim_token', confidence='medium-high',
                    evidence=f'Mukim {ai_mukim.title()} + tokens: {sorted(overlap)[:3]}',
                )
                claimed.add(g.group_id)
                break

    # ── Tier C: temporal proximity ────────────────────────────────────
    # For each unbound AssetItem, find the unclaimed DocGroup whose msg_id
    # matches the message line that names this asset (or whose timestamp
    # is closest, when explicit linkage is missing). Skip if multiple
    # AssetItems are equidistant — that's a guess, not a binding.
    # NOTE: explicit message-line timestamps require the inbound parser
    # to record them per-line. For now we use msg_id linkage only (the
    # ChatMessage that delivered the doc). Refinement to per-line ts can
    # come in a later commit without changing this stage's contract.
    for ai in asset_items:
        if ai.ai_index in bindings:
            continue
        # Pure temporal binding only fires for assets that have a clear
        # message_line and exactly one free group from the same delivery.
        # Without explicit per-line timestamps we cannot do better than
        # "exactly one unclaimed group of the right kind" — and that
        # would be guessing. Defer to Tier D unless we have stronger
        # signal in a later iteration.
        # Reserved for §10i full implementation.
        pass

    # ── Tier D: H3 (no binding) ───────────────────────────────────────
    for ai in asset_items:
        if ai.ai_index not in bindings:
            bindings[ai.ai_index] = Binding(
                ai_index=ai.ai_index, group_id=None,
                tier='D', match_via='h3', confidence='h3',
                evidence='No matching DocGroup — text-only / H3 placeholder',
            )

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
                  'mukim_token', 'temporal', 'h3'}
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

    bindings = bind_assets(asset_items, doc_groups)
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

    return {
        'asset_items': [a.to_dict() for a in asset_items],
        'doc_groups': [g.to_dict() for g in doc_groups],
        'bindings': [b.to_dict() for b in bindings],
        'residuals': [g.to_dict() for g in res],
        'gifts': gifts,
    }
