"""Walk-through helper for Step 6: Specific Gifts.

Finds Documents (property titles, bank statements, vehicles) that haven't
yet been turned into a Gift entry in the Will's step5_data, and parses
free-form user replies like 'Joshua 50%, Esther 50%' into beneficiary +
share assignments.
"""
import json
import re
from typing import List, Dict, Any
from database import ChatMessage, ChatSession, Document, Will


# ─────────────────────────────────────────────────────────────────────────────
#  ANTI-ASSUMPTION HELPERS — see CLAUDE.md §10hd
#  "Same lot ≠ same property" for stratified titles. NEVER merge two
#  strata docs by lot number alone. The TITLE NUMBER is what
#  distinguishes one unit from another in the same building.
# ─────────────────────────────────────────────────────────────────────────────

_STRATA_TITLE_TYPE_TOKENS = (
    'STRATA', 'HAKMILIK STRATA', 'GERAN MUKIM STRATA', 'GMS',
    'STRATA TITLE',
)

_STRATA_DESCRIPTION_TOKENS = (
    'LEVEL ', 'STOREY', 'TINGKAT', 'PARCEL NO', 'PARCEL ',
    'PETAK', 'BLOCK ', 'BLOK ', 'BUILDING M', 'BUILDING ',
    'BUILT UP AREA', 'STRATA',
)


def _is_strata(extracted: Dict[str, Any]) -> bool:
    """True if this title doc represents a strata parcel (apartment / condo
    / shop-lot in a strata scheme), not a landed lot.

    A doc is strata if ANY of:
      - title_type contains a strata token
      - title_number has slashes (e.g. '564662/M1C/30/710' encodes
        master/block/storey/parcel)
      - property_description mentions Level / Storey / Parcel / Block
      - document_type explicitly = 'strata_title'

    This is the HARD predicate. Never widen it without updating §10hd —
    a false-positive is safer (forces title check) than a false-negative
    (allows wrong merge).
    """
    if not extracted:
        return False
    tt = (extracted.get('title_type') or '').upper()
    if any(tok in tt for tok in _STRATA_TITLE_TYPE_TOKENS):
        return True
    tn = (extracted.get('title_number') or '').strip()
    if '/' in tn and any(c.isdigit() for c in tn):
        # Strata title encoding: '<master>/<block>/<storey>/<parcel>'
        return True
    desc = (extracted.get('property_description') or '').upper()
    if any(tok in desc for tok in _STRATA_DESCRIPTION_TOKENS):
        return True
    if (extracted.get('document_type') or '').lower() == 'strata_title':
        return True
    return False


def _title_signature(extracted: Dict[str, Any]) -> str:
    """Canonical title-number signature for grouping. For strata, the
    FULL title is used (including /Block/Storey/Parcel suffix). For
    landed, just the cleaned digits.

    Returns '' if the title number is missing or garbage — caller must
    handle that case (typically: don't merge by title alone).
    """
    raw = (extracted.get('title_number') or '').strip()
    if not raw:
        return ''
    cleaned = _clean_id_value(raw)
    if _looks_like_garbage(cleaned):
        return ''
    # Keep slashes for strata (they encode block/storey/parcel) but drop
    # whitespace and punctuation noise.
    sig = re.sub(r'[\s\-.]', '', cleaned).upper()
    # If strata, also collapse any 'M1C' style block prefix to 'M*C' to
    # tolerate OCR drift in the block letter — but DO NOT collapse the
    # storey/parcel digits.
    return sig


def _master_title(sig: str) -> str:
    """Extract just the master title number (digits before first slash) from
    a title signature. Used to distinguish OCR truncation (master only) from
    genuine different parcels (full /block/storey/parcel encoding).

    e.g. "564662/M1C/30/710" → "564662"
         "564662"            → "564662"
         "GMS564662"         → "564662"
    """
    if not sig:
        return ''
    head = sig.split('/')[0]
    digits = re.sub(r'\D', '', head)
    return digits


def _safe_to_merge(grp_a: Dict[str, Any], grp_b: Dict[str, Any]) -> bool:
    """Return True if two property groups can be safely merged into one.

    The hard rule (§10hd): if either group is strata, the title
    signatures must match. Same lot + different title = different units
    in the same building → DO NOT MERGE.

    BUT — distinguish OCR-truncation from genuine different units:
      • full-encoded "564662/M1C/30/710" vs master-only "564662"
        → master matches, only one side has parcel encoding → likely the
        same unit (front-page vs back-page of same geran). MERGE.
      • full-encoded "564662/M1C/30/710" vs full-encoded "564662/M1C/05/100"
        → both have parcel encoding, suffixes differ → different units. SPLIT.
      • master "564662" vs master "504662"
        → different master numbers → different units. SPLIT (per §10hd: "OCR
        drift" 504662 vs 564662 should be treated as different until user
        confirms).

    For landed properties (neither is strata), lot equality is enough.
    """
    ex_a = (grp_a.get('title_doc') or {}).get('extracted') or {}
    ex_b = (grp_b.get('title_doc') or {}).get('extracted') or {}
    a_strata = _is_strata(ex_a)
    b_strata = _is_strata(ex_b)
    if not (a_strata or b_strata):
        return True   # both landed — lot equality is sufficient
    sig_a = _title_signature(ex_a)
    sig_b = _title_signature(ex_b)
    if not sig_a or not sig_b:
        # One side has no readable title. Can't prove they're the same
        # unit; refuse to merge and let the user decide.
        return False
    if sig_a == sig_b:
        return True
    # Distinguish OCR truncation (one side master-only) from genuine split
    has_parcel_a = '/' in sig_a
    has_parcel_b = '/' in sig_b
    master_a = _master_title(sig_a)
    master_b = _master_title(sig_b)
    if has_parcel_a != has_parcel_b:
        # Only one side has parcel encoding → likely OCR truncation.
        # Treat as same unit IFF master matches.
        return bool(master_a) and master_a == master_b
    # Both have parcel encoding (or neither, but sig_a != sig_b means they
    # both lack and differ) → genuine different units.
    return False


def _is_genuinely_different_unit(sig_a: str, sig_b: str) -> bool:
    """Are these two title signatures from genuinely different strata
    units (NOT OCR truncation of the same unit)?

    Returns False when one side is master-only and shares the master with
    the other side (likely OCR truncation, same unit). Returns True when
    sigs differ in master OR both have parcel encoding with different
    suffixes. See §10hd.
    """
    if not sig_a or not sig_b:
        return False
    if sig_a == sig_b:
        return False
    a_parcel = '/' in sig_a
    b_parcel = '/' in sig_b
    master_a = _master_title(sig_a)
    master_b = _master_title(sig_b)
    if a_parcel != b_parcel:
        # OCR truncation case: same master ⇒ same unit
        return not (master_a and master_a == master_b)
    # Both encoded or neither → distinct sigs = distinct units
    return True


def _safe_to_inherit_address(src_extracted: Dict[str, Any],
                             dst_extracted: Dict[str, Any]) -> bool:
    """Return True if the source doc's address can be safely copied to
    the destination doc as a sibling enrichment.

    The rule (§10hd #2): cross-title address inheritance is forbidden
    when either side is strata. Two strata parcels in the same building
    have the SAME lot but DIFFERENT addresses — copying address by lot
    match would hide the destination's real unit.
    """
    src_strata = _is_strata(src_extracted)
    dst_strata = _is_strata(dst_extracted)
    if not (src_strata or dst_strata):
        return True   # landed — lot match implies same address
    sig_src = _title_signature(src_extracted)
    sig_dst = _title_signature(dst_extracted)
    if not sig_src or not sig_dst:
        return False  # can't prove — refuse
    return sig_src == sig_dst


def _score_property_confidence(ex: Dict[str, Any]) -> int:
    """Numeric confidence score 0-13 for a property document's extracted data.

    Higher = more certain about property identity → show first in walkthrough.

    Scoring:
      +3  title_type_confidence == "high"
      +1  title_type_confidence == "medium"
      +1  has title_number
      +1  has lot_number
      +1  bonus: has BOTH title_number and lot_number
      +2  has a real (non-NLC) street address already
      +1  has non-empty owner_names
      +3  _message_context contains this doc's NLC identifier (user explicitly said it)
      +1  _message_context exists at all (some user intent context present)
    """
    try:
        from ai.chat_planner import _NLC_ADDR_RE
    except ImportError:
        _NLC_ADDR_RE = None

    score = 0
    conf = (ex.get('title_type_confidence') or '').lower()
    if conf == 'high':
        score += 3
    elif conf == 'medium':
        score += 1

    has_title = bool((ex.get('title_number') or '').strip())
    has_lot = bool((ex.get('lot_number') or '').strip())
    if has_title:
        score += 1
    if has_lot:
        score += 1
    if has_title and has_lot:
        score += 1  # bonus for having both identifiers

    # Real (non-NLC) street address already known
    addr = (ex.get('property_address') or '').strip()
    if addr:
        if _NLC_ADDR_RE is None or not _NLC_ADDR_RE.match(addr):
            score += 2

    # Owner names extracted
    owners = [o for o in (ex.get('owner_names') or []) if (o or '').strip()]
    if owners:
        score += 1

    # Message context: user explicitly mentioned this property in WhatsApp/chat
    ctx = (ex.get('_message_context') or '').strip()
    if ctx:
        ctx_lower = ctx.lower()
        lot = (ex.get('lot_number') or '').strip().lower()
        title = (ex.get('title_number') or '').strip().lower()
        # Primary signal: NLC identifier appears in the message the user typed
        if (lot and len(lot) >= 3 and lot in ctx_lower) or \
           (title and len(title) >= 3 and title in ctx_lower):
            score += 3
        else:
            score += 1  # some context present but no explicit NLC reference

    return score


# Only `property_title` proves OWNERSHIP and triggers a "who inherits this?"
# question. `property_spa` (contract, transfer pending) and `property_tax`
# (just a payment receipt) are SUPPORTING docs — we attach them to the
# matching title as evidence, but never as standalone gifts.
_GIFT_KINDS = ('property_title', 'bank_statement', 'vehicle')
# Anything tied to a property address but NOT a title — clusters under
# the matching title in the chat for context, never as its own gift.
# loan_agreement = bank charge document (proves encumbrance, groups with the property).
_PROPERTY_SUPPORT_KINDS = ('property_spa', 'property_tax', 'property_transfer',
                           'utility_bill', 'bank_letter', 'loan_agreement')
# Clearly unrelated uploads that should be flagged and never grouped.
_UNRELATED_KINDS = ('death_certificate', 'unrelated')


def _norm_addr(s: str) -> str:
    """Normalise an address-ish string for fuzzy group matching:
    uppercase, strip punctuation, collapse whitespace."""
    if not s:
        return ''
    out = re.sub(r'[^\w\s/]', ' ', s.upper())
    return ' '.join(out.split())


def _clean_id_value(value: str) -> str:
    """Strip AI-extractor noise from a title/lot identifier.

    The vision model occasionally dumps strings like 'VALUE: GRN35662',
    'VALUE: LOT 207922', 'VALUE: (unreadable)' into structured fields.
    Pull the actual identifier out: drop 'VALUE:' / 'LOT' / 'TITLE'
    prefixes, kill bracketed commentary, collapse whitespace.
    Returns uppercased identifier or '' if nothing usable remains.
    """
    if not value:
        return ''
    v = value.strip().upper()
    # Drop leading prefixes the AI tends to emit
    v = re.sub(r'^(VALUE\s*[:\-]\s*)+', '', v)
    v = re.sub(r'^(LOT|TITLE|GERAN|TITLE\s*NO\.?)\s*[:\-]?\s*', '', v)
    # Drop parenthetical commentary like '(UNREADABLE)' or '(BLURRED)'
    v = re.sub(r'\([^)]*\)', '', v).strip()
    return v


def _looks_like_garbage(value: str) -> bool:
    """Return True for values that are clearly OCR hallucinations or
    placeholder text rather than real Malaysian lot/title identifiers.

    Real lot numbers are digits (possibly with a slash for strata), real
    title numbers are digits or alphanumeric with standard prefixes.
    Rejects things like 'Blabla', 'Unknown', 'N/A', pure-letter strings
    longer than 4 chars, AI-rambling like 'VALUE: (unreadable)', etc.
    """
    if not value:
        return True
    v = value.strip().upper()
    # AI-emitted noise → garbage
    if 'UNREADABLE' in v or 'NOT VISIBLE' in v or 'CANNOT READ' in v:
        return True
    # Common placeholder strings
    _JUNK = {'N/A', 'NA', 'UNKNOWN', 'NONE', 'NIL', 'TBD', '-', '?', 'BLABLA',
              'PLACEHOLDER', 'XXXX', 'XXXXX', 'VALUE:', 'VALUE'}
    if v in _JUNK:
        return True
    # After cleaning AI prefixes, if nothing's left it's garbage
    cleaned = _clean_id_value(v)
    if not cleaned:
        return True
    # If it's longer than 4 chars and contains ONLY letters (no digits,
    # no '/', no '-'), it's almost certainly garbled OCR output.
    # Real lot numbers have at least one digit. Title numbers too.
    stripped = re.sub(r'[\s/\-()]', '', cleaned)
    if len(stripped) > 4 and stripped.isalpha():
        return True
    return False


def _property_group_key(ex: Dict[str, Any]) -> str:
    """A loose identifier for grouping multiple uploaded images that all
    refer to the SAME property (front/back of geran, SPA, cukai tanah,
    electric bill, bank letter). Order of preference:

      1. title_number (most precise)
      2. lot_number + mukim
      3. property_address / description
      4. property_hint from the vision classifier (covers utility bills
         and bank letters that don't have title fields but DO mention
         the address)

    Garbage OCR values (all-letter strings like 'Blabla') are treated as
    empty so they don't pollute grouping.  Empty string → own bucket.
    """
    if not isinstance(ex, dict):
        return ''
    tn = (ex.get('title_number') or '').strip().upper()
    if tn and not _looks_like_garbage(tn):
        return f'TN:{tn}'
    lot = (ex.get('lot_number') or '').strip().upper()
    mukim = (ex.get('mukim') or '').strip().upper()
    if lot and not _looks_like_garbage(lot):
        return f'LOT:{lot}|{mukim}'
    addr = _norm_addr(ex.get('description') or ex.get('property_address') or '')
    if addr:
        return f'ADDR:{addr}'
    hint = _norm_addr(ex.get('property_hint') or '')
    return f'HINT:{hint}' if hint else ''


def get_pending_gift_documents(client_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Returns {'property': [...], 'bank': [...], 'vehicle': [...]} with
    Documents not yet referenced in any Gift in the active draft Will's
    step5_data.

    Property handling: users often dump multiple images per property
    (front + back of geran, SPA, cukai tanah). We GROUP these so the user
    sees ONE property card with all supporting images listed under it,
    not three separate "who inherits this?" questions.
    """
    out = {'property': [], 'bank': [], 'vehicle': []}
    will = (Will.query.filter_by(client_id=client_id, status='draft')
            .filter(Will.deleted_at.is_(None))
            .order_by(Will.updated_at.desc()).first())
    if not will:
        return out

    try:
        gifts = json.loads(will.step5_data) if will.step5_data else []
    except (json.JSONDecodeError, TypeError):
        gifts = []
    if not isinstance(gifts, list):
        gifts = []

    # ╔══════════════════════════════════════════════════════════════════════╗
    # ║  🔥 BURN-IN — NO DUPLICATE PROPERTY CARDS 🔥                          ║
    # ║  When a gift is already in step5_data, ANY pending property doc      ║
    # ║  that refers to the SAME physical property must be filtered out —    ║
    # ║  even when its OCR'd title number differs (564662 vs 504662). Match  ║
    # ║  by (lot_digits, normalised_address) signature, not just title.      ║
    # ║  See CLAUDE.md §10f.                                                 ║
    # ╚══════════════════════════════════════════════════════════════════════╝
    referenced_doc_ids = set()
    referenced_group_keys = set()       # legacy dedup by group key
    referenced_lot_addr_sigs = set()    # new dedup by (lot, address)
    # ╔══════════════════════════════════════════════════════════════════╗
    # ║  🔥 BURN-IN — STRATA-AWARE DEDUP (CLAUDE.md §10hd) 🔥              ║
    # ║  Same lot + empty addr is the strata-collision case. Two units in ║
    # ║  one building share lot 207922 but have different titles          ║
    # ║  (564662/M1C/30/710 vs 504662). The (lot, addr) key alone wrongly ║
    # ║  filters the second unit. Track the title signature alongside so  ║
    # ║  the dedup site can let strata pass when titles differ.           ║
    # ╚══════════════════════════════════════════════════════════════════╝
    referenced_sig_titles: Dict[tuple, set] = {}  # (lot, addr) → set of title sigs
    # ╔══════════════════════════════════════════════════════════════════╗
    # ║  🔥 BURN-IN — OCR-TRUNCATION FILTER FOR ACCEPTED GIFTS 🔥          ║
    # ║  When a gift was saved on the FULL strata title "564662/M1C/30/  ║
    # ║  710" but a sibling doc still carries the master-only "564662"   ║
    # ║  with a polluted/different address, the (lot,addr) sig won't     ║
    # ║  match. Also track (lot_digits, master_title_digits) so the      ║
    # ║  master-only sibling is recognised as a phantom of an accepted   ║
    # ║  property and filtered.                                          ║
    # ╚══════════════════════════════════════════════════════════════════╝
    referenced_lot_master_titles: set = set()  # {(lot_digits, master_title_digits)}
    for g in gifts:
        if isinstance(g, dict) and g.get('document_id'):
            referenced_doc_ids.add(g['document_id'])
        # If a gift was saved with the property identifier, remember it so
        # later-uploaded SPA/tax for the same lot don't resurface as new.
        if isinstance(g, dict):
            # Wizard gifts can save under property_info OR property_details
            # (the legacy upsert path uses property_details). Read both.
            pi = g.get('property_info') or g.get('property_details') or g
            gk = _property_group_key(pi)
            if gk:
                referenced_group_keys.add(gk)
            # Lot+address signature — survives OCR title drift
            def _pi_get(key):
                return (pi.get(key)
                        or (g.get('property_details') or {}).get(key)
                        or (g.get('property_info') or {}).get(key)
                        or g.get(key) or '')
            g_lot = _clean_id_value(_pi_get('lot_number'))
            if _looks_like_garbage(g_lot):
                g_lot = ''
            g_lot_digits = re.sub(r'\D', '', g_lot)
            g_addr_sig = _norm_addr(_pi_get('property_address'))[:60]
            if g_lot_digits or g_addr_sig:
                referenced_lot_addr_sigs.add((g_lot_digits, g_addr_sig))
                # Track title signature for strata-aware dedup
                g_title_ex = {
                    'title_number': _pi_get('title_number'),
                    'title_type':   _pi_get('title_type'),
                    'property_description': _pi_get('property_description'),
                    'document_type': _pi_get('document_type'),
                }
                ts = _title_signature(g_title_ex)
                if ts:
                    referenced_sig_titles.setdefault(
                        (g_lot_digits, g_addr_sig), set()).add(ts)
                    # Also remember the master-title (digits before first /)
                    # so OCR-truncated siblings of this accepted gift get
                    # filtered out, even if their address differs (because
                    # the master-only sibling never had a real address).
                    g_master = _master_title(ts)
                    if g_lot_digits and g_master:
                        referenced_lot_master_titles.add(
                            (g_lot_digits, g_master))

    # Pull title docs + supporting docs + unclassified (chat_inbox/other) in
    # one pass. Unclassified docs are needed so we can attach them to the
    # matching property group when they arrived in the same email (same
    # chat_message_id) as a geran — typical for multi-page WhatsApp forwards
    # where back-pages get classified as 'other' because OCR sees no title.
    _SIBLING_KINDS = ('chat_inbox', 'other')
    all_kinds = _GIFT_KINDS + _PROPERTY_SUPPORT_KINDS + _SIBLING_KINDS
    docs = (Document.query.filter(
        Document.client_id == client_id,
        Document.category.in_(all_kinds),
    ).order_by(Document.created_at.asc()).all())
    # 🔥 §10x.126 — drop docs the user explicitly skipped or
    # soft-deleted via the §10x.125 identify-image card. Their
    # extracted_data carries _skipped_not_in_will=True (Skip click)
    # or _user_removed=True. Without this filter, the planner
    # re-renders the same identify card forever after Skip.
    def _is_user_skipped(d):
        try:
            ex = json.loads(d.extracted_data or '{}') if d.extracted_data else {}
        except Exception:
            return False
        if not isinstance(ex, dict):
            return False
        return bool(ex.get('_skipped_not_in_will')
                    or ex.get('_user_removed')
                    or ex.get('_orphan_group_skipped'))
    docs = [d for d in docs if not _is_user_skipped(d)]

    # ── Retroactive message context lookup ────────────────────────────────
    # Old documents (processed before _message_context was added to
    # extracted_data) don't have the WhatsApp text stored in extracted_data.
    # Pre-fetch it now from the linked ChatMessage so the property card can
    # display "Client's message about this property" even for old uploads.
    #
    # We only do this for documents that:
    #   a) have a chat_message_id, AND
    #   b) their extracted_data lacks _message_context
    # One DB query fetches ALL needed messages in bulk — no N+1.
    _all_chat_msg_ids = set(
        str(d.chat_message_id) for d in docs
        if d.chat_message_id
    )
    _chat_msg_content: Dict[str, str] = {}  # chat_message_id → content text
    if _all_chat_msg_ids:
        try:
            msgs = ChatMessage.query.filter(
                ChatMessage.id.in_(_all_chat_msg_ids)
            ).all()
            for cm in msgs:
                if cm.content:
                    _chat_msg_content[str(cm.id)] = cm.content
        except Exception:
            pass  # non-fatal — retroactive context is best-effort

    # ── NLC-based retroactive message context ──────────────────────────────
    # Pre-fetch ALL user messages for the client's active session so we can
    # link messages to images using NLC identifiers (HSD/PTD/lot/title numbers)
    # as the primary criterion — stronger than chat_message_id proximity alone.
    #
    # Example: "Lot 127082 at Phase 2D Seri Alam, give to Joshua" sent as a
    # separate message from the image upload. We match the message to the doc
    # by lot_number = "127082" found in both.
    #
    # Results: nlc_key (lowercase) → best matching user message text
    _nlc_to_message: Dict[str, str] = {}  # e.g. "127082" → "Lot 127082, give to Joshua"
    try:
        cs = (ChatSession.query.filter_by(client_id=client_id)
              .order_by(ChatSession.created_at.desc()).first())
        if cs:
            _user_msgs = (ChatMessage.query
                          .filter_by(session_id=cs.id, role='user')
                          .order_by(ChatMessage.created_at.asc()).all())
            for um in _user_msgs:
                txt = (um.content or '').strip()
                if not txt or len(txt) < 4:
                    continue
                txt_lower = txt.lower()
                # Extract NLC patterns from this message
                # Matches: PTD 127082, HSD 251041, Lot 207922, H.S.(D) 251041, Title 504662…
                import re as _re_
                nlc_hits = _re_.findall(
                    r'\b(?:ptd|hsd|lot|title|geran|hs\(d\)|h\.s\.\(d\))\s*[\.:\-]?\s*(\d{3,8})\b',
                    txt_lower
                )
                for hit in nlc_hits:
                    # Use the shorter number as key (lot/PTD numbers are usually 5-7 digits)
                    if hit not in _nlc_to_message:
                        _nlc_to_message[hit] = txt
                # Also check for bare numbers that look like lot numbers (5-7 digits)
                bare_nums = _re_.findall(r'\b(\d{5,7})\b', txt)
                for num in bare_nums:
                    if num not in _nlc_to_message:
                        _nlc_to_message[num] = txt
    except Exception:
        pass  # non-critical — best-effort

    # First pass: index property-related docs (title + support) by group key.
    # Also track chat_message_id → best group key so we can later absorb
    # sibling docs (same email batch, unclassified pages) into the group.
    prop_groups: Dict[str, Dict[str, Any]] = {}
    seen_keys = set()        # for bank/vehicle dedupe
    # msg_id → best group key rank: TN=4 > LOT=3 > ADDR=2 > HINT=1 > DOC=0
    _GK_RANK = {'TN:': 4, 'LOT:': 3, 'ADDR:': 2, 'HINT:': 1}
    msg_id_to_gk: Dict[str, str] = {}  # chat_message_id str → group key

    # Sibling pool: unclassified docs keyed by chat_message_id
    sibling_pool: Dict[str, List[Dict]] = {}

    for d in docs:
        if d.id in referenced_doc_ids:
            continue
        try:
            ex = json.loads(d.extracted_data) if d.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            ex = {}

        # Retroactively inject _message_context for documents that don't
        # already have it stored (old uploads processed before the feature).
        # Priority order:
        #   1. chat_message_id-based lookup (text sent WITH this specific image)
        #   2. NLC-based lookup (user message mentioning this doc's lot/title number)
        if not ex.get('_message_context'):
            raw_ctx = ''
            if d.chat_message_id:
                raw_ctx = _chat_msg_content.get(str(d.chat_message_id), '')
            if raw_ctx:
                # Strip pure attachment lines (they're noise, not intent text)
                clean_lines = [
                    ln for ln in raw_ctx.splitlines()
                    if '<attached:' not in ln.lower()
                    and not ln.strip().lower().endswith('(file attached)')
                ]
                clean = '\n'.join(clean_lines).strip()
                if clean:
                    ex = dict(ex)  # shallow copy — don't mutate the parsed JSON
                    ex['_message_context'] = clean
            else:
                # Pass 2: NLC-identifier-based lookup — check if any user message
                # explicitly mentions this document's lot/title number.
                # This handles: message in one turn, image upload in another.
                lot_n = (ex.get('lot_number') or '').strip().lower()
                title_n = (ex.get('title_number') or '').strip().lower()
                # Strip non-digits to get the bare number for lookup
                import re as _re2_
                lot_bare = _re2_.sub(r'\D', '', lot_n)
                title_bare = _re2_.sub(r'\D', '', title_n)
                nlc_msg = None
                for key in [lot_bare, title_bare, lot_n, title_n]:
                    if key and len(key) >= 4 and key in _nlc_to_message:
                        nlc_msg = _nlc_to_message[key]
                        break
                if nlc_msg:
                    ex = dict(ex)
                    ex['_message_context'] = nlc_msg
                    ex['_context_source'] = 'nlc_message_match'

        doc_summary = {
            'document_id': d.id,
            'category': d.category,
            'extracted': ex,
            'purpose': (ex.get('purpose') or '').strip(),
            'original_filename': d.original_filename,
            'created_at': d.created_at.isoformat() if d.created_at else '',
            'chat_message_id': d.chat_message_id,
        }

        # ── Unclassified siblings ──────────────────────────────────────────
        if d.category in _SIBLING_KINDS:
            mid = str(d.chat_message_id or '')
            if mid:
                sibling_pool.setdefault(mid, []).append(doc_summary)
            continue

        # ── Property docs ──────────────────────────────────────────────────
        if d.category in ('property_title',) + _PROPERTY_SUPPORT_KINDS:
            gk = _property_group_key(ex) or f'DOC:{d.id}'
            if gk in referenced_group_keys:
                continue
            grp = prop_groups.setdefault(gk, {
                'group_key': gk,
                'title_doc': None,
                'support_docs': [],
                'all_doc_ids': [],
            })
            grp['all_doc_ids'].append(d.id)
            if d.category == 'property_title':
                if grp['title_doc'] is None:
                    grp['title_doc'] = doc_summary
                else:
                    grp['support_docs'].append(doc_summary)
            else:
                grp['support_docs'].append(doc_summary)

            # Track the best group key seen for this chat message so later
            # unclassified docs from the same email can join this group.
            mid = str(d.chat_message_id or '')
            if mid:
                cur_rank = next((v for k, v in _GK_RANK.items() if gk.startswith(k)), 0)
                ex_gk = msg_id_to_gk.get(mid, '')
                ex_rank = next((v for k, v in _GK_RANK.items() if ex_gk.startswith(k)), 0)
                if cur_rank >= ex_rank:
                    msg_id_to_gk[mid] = gk
            continue

        # ── Bank / vehicle ─────────────────────────────────────────────────
        # 🔥 BURN-IN — JMB/BPB receipts mis-classified as bank statements
        # JMB / Badan Pengurusan Bersama / Joint Management Body docs are
        # property maintenance fee receipts, NOT bank accounts. Filter out
        # before they pollute the bank list.
        if d.category == 'bank_statement':
            _bn_upper = (ex.get('bank_name') or '').upper()
            _NON_BANK_TOKENS = (
                'BADAN PENGURUSAN BERSAMA',
                'JOINT MANAGEMENT BODY',
                'PERBADANAN PENGURUSAN',
                'MANAGEMENT CORPORATION',
                ' JMB',  # leading space avoids matching 'KMBANK' etc
                ' BPB',
                'STRATA MANAGEMENT',
                'MAINTENANCE FEE',
                'SERVICE CHARGE',
            )
            if any(tok in _bn_upper for tok in _NON_BANK_TOKENS):
                continue   # not a real bank — skip
            key = ('bank', (ex.get('account_number') or '').strip())
        else:
            key = ('vehicle', (ex.get('reg_number') or '').strip().upper())
        if key[1] and key in seen_keys:
            continue
        seen_keys.add(key)
        if d.category == 'bank_statement':
            out['bank'].append(doc_summary)
        else:
            out['vehicle'].append(doc_summary)

    # ── Sibling merge ──────────────────────────────────────────────────────
    # Attach unclassified docs from the same email to the matching property
    # group. This fixes the "8 images but only 1 shown" problem: back-pages
    # of a geran that OCR couldn't read land as 'other'; we absorb them as
    # supporting docs under the geran that WAS identified.
    for mid, siblings in sibling_pool.items():
        gk = msg_id_to_gk.get(mid)
        if not gk or gk not in prop_groups:
            continue
        grp = prop_groups[gk]
        known_ids = set(grp['all_doc_ids'])
        for s in siblings:
            if s['document_id'] not in known_ids:
                grp['support_docs'].append(s)
                grp['all_doc_ids'].append(s['document_id'])
                known_ids.add(s['document_id'])

    # ── DOC:{id} merge ─────────────────────────────────────────────────────
    # Property docs that had no extractable lot/title (DOC:{id} bucket) can
    # still be merged into a named group if they share the same email batch.
    ungrouped_keys = [gk for gk in list(prop_groups.keys()) if gk.startswith('DOC:')]
    for gk in ungrouped_keys:
        grp = prop_groups[gk]
        # Find the chat_message_id for any doc in this group
        all_mids = set()
        for did in grp['all_doc_ids']:
            # Lookup from doc_summary in title_doc or support_docs
            for ds in ([grp['title_doc']] if grp['title_doc'] else []) + grp['support_docs']:
                if ds and ds.get('document_id') == did:
                    mid = str(ds.get('chat_message_id') or '')
                    if mid:
                        all_mids.add(mid)
        for mid in all_mids:
            target_gk = msg_id_to_gk.get(mid)
            if target_gk and target_gk != gk and target_gk in prop_groups:
                # Merge this DOC:{id} group into the named group
                target = prop_groups[target_gk]
                known_ids = set(target['all_doc_ids'])
                for ds in ([grp['title_doc']] if grp['title_doc'] else []) + grp['support_docs']:
                    if ds and ds['document_id'] not in known_ids:
                        target['support_docs'].append(ds)
                        target['all_doc_ids'].append(ds['document_id'])
                        known_ids.add(ds['document_id'])
                del prop_groups[gk]
                break

    # ── Same-email named-group merge ──────────────────────────────────────
    # When 5 images of the SAME property are in one email but OCR gives
    # different (or garbage) lot/title numbers, we end up with multiple
    # named groups (LOT:X, LOT:Y, DOC:Z…) for the same email batch.
    # Merge them all into the group with the best extraction quality.
    #
    # Strategy: for each chat_message_id that appears across multiple
    # different named groups, keep the BEST group (most filled fields)
    # and fold all others into it as support docs.
    mid_to_named_gks: Dict[str, List[str]] = {}
    for gk, grp in list(prop_groups.items()):
        if gk.startswith('DOC:'):
            continue  # already handled above
        # Collect all message IDs referenced by docs in this group
        all_docs_iter = (([grp['title_doc']] if grp['title_doc'] else [])
                         + grp['support_docs'])
        for ds in all_docs_iter:
            if not ds:
                continue
            mid = str(ds.get('chat_message_id') or '')
            if mid:
                mid_to_named_gks.setdefault(mid, [])
                if gk not in mid_to_named_gks[mid]:
                    mid_to_named_gks[mid].append(gk)

    def _group_identifiers(gk: str) -> dict:
        """Return the key OCR identifiers for a group (title_no, lot_no, negeri)."""
        if gk not in prop_groups:
            return {}
        td = prop_groups[gk].get('title_doc') or {}
        ex_ = td.get('extracted') or {}
        return {
            'title_number': (ex_.get('title_number') or '').strip().upper(),
            'lot_number':   (ex_.get('lot_number')   or '').strip().upper(),
            'negeri':       (ex_.get('negeri')        or '').strip().upper(),
            'daerah':       (ex_.get('daerah')        or '').strip().upper(),
        }

    def _groups_conflict(gk_a: str, gk_b: str) -> bool:
        """Return True if two groups have CONFLICTING OCR data — meaning they
        cannot possibly be the same property and must stay separate.

        Rules (strongest signal first):
          1. Different title_number (both non-empty) → CONFLICT
          2. Different lot_number (both non-empty) AND different negeri → CONFLICT
          3. Different lot_number AND different daerah (same state) → CONFLICT
        A missing value is never treated as a conflict — absence of data ≠ mismatch.
        """
        a = _group_identifiers(gk_a)
        b = _group_identifiers(gk_b)
        # Rule 1 — title numbers exist and differ
        tn_a, tn_b = a.get('title_number',''), b.get('title_number','')
        if tn_a and tn_b and tn_a != tn_b:
            return True
        # Rule 2 — lot numbers exist and differ, and states also differ
        ln_a, ln_b = a.get('lot_number',''), b.get('lot_number','')
        neg_a, neg_b = a.get('negeri',''), b.get('negeri','')
        if ln_a and ln_b and ln_a != ln_b:
            if neg_a and neg_b and neg_a != neg_b:
                return True
            # Same state but different daerah → also a conflict
            da_a, da_b = a.get('daerah',''), b.get('daerah','')
            if da_a and da_b and da_a != da_b:
                return True
        return False

    for mid, gk_list in mid_to_named_gks.items():
        if len(gk_list) < 2:
            continue  # only one group for this email → nothing to merge
        # Keep the group that exists in prop_groups and has a title_doc with
        # the most filled NLC fields. Break ties by group-key rank (TN > LOT > ADDR > HINT).
        def _group_quality(gk: str) -> int:
            if gk not in prop_groups:
                return -1
            td = prop_groups[gk].get('title_doc') or {}
            ex_ = td.get('extracted') or {}
            filled = sum(1 for k in ('title_number', 'lot_number', 'mukim', 'daerah', 'negeri')
                         if (ex_.get(k) or '').strip())
            rank = next((v for k, v in _GK_RANK.items() if gk.startswith(k)), 0)
            return filled * 10 + rank

        gk_list_alive = [gk for gk in gk_list if gk in prop_groups]
        if len(gk_list_alive) < 2:
            continue
        best_gk = max(gk_list_alive, key=_group_quality)
        target = prop_groups[best_gk]
        known_ids = set(target['all_doc_ids'])
        for gk in gk_list_alive:
            if gk == best_gk:
                continue
            # ── CONFLICT CHECK ──────────────────────────────────────────
            # If the other group has OCR data that CONTRADICTS the best
            # group (different title/lot/state), they are different
            # properties — never merge them, even if sent in the same email.
            if _groups_conflict(best_gk, gk):
                continue  # keep as a separate property card
            grp = prop_groups[gk]
            # ── STRATA SAFETY (CLAUDE.md §10hd) ─────────────────────────
            # Same email can carry two strata parcels in the same building
            # (lot shared, titles different). Refuse to merge them.
            if not _safe_to_merge(target, grp):
                continue
            for ds in (([grp['title_doc']] if grp['title_doc'] else [])
                       + grp['support_docs']):
                if ds and ds['document_id'] not in known_ids:
                    target['support_docs'].append(ds)
                    target['all_doc_ids'].append(ds['document_id'])
                    known_ids.add(ds['document_id'])
            del prop_groups[gk]

    # ── Time-proximity merge (old-data fallback) ──────────────────────────
    # For OLD uploads that have no chat_message_id, the chat_message_id-based
    # merges above are all no-ops. A typical WhatsApp batch is sent within
    # seconds; when the email arrives all attachments are processed in rapid
    # succession and end up with created_at timestamps ≤ N minutes apart.
    #
    # Strategy: for each remaining DOC:{id} group (unreadable OCR → its own
    # card), find any named group (TN:/LOT:/ADDR:/HINT:) whose docs were
    # created within PROX_MINUTES. If found, merge the DOC:{id} into the
    # nearest named group. This is a best-effort heuristic that handles the
    # "PHOTO-2026-05-02-13-52-32.jpg showing as its own blank card" case.
    PROX_MINUTES = 10  # images uploaded within 10 min of each other → same batch
    from datetime import datetime, timedelta, timezone
    ungrouped_doc_keys = [gk for gk in list(prop_groups.keys()) if gk.startswith('DOC:')]
    if ungrouped_doc_keys:
        # Build a map: named group key → list of created_at datetimes
        def _group_timestamps(grp: Dict[str, Any]):
            ts_list = []
            for ds in (([grp['title_doc']] if grp['title_doc'] else [])
                       + grp['support_docs']):
                if not ds:
                    continue
                raw = ds.get('created_at') or ''
                if raw:
                    try:
                        ts_list.append(datetime.fromisoformat(raw))
                    except (ValueError, TypeError):
                        pass
            return ts_list

        named_gks = [(gk, _group_timestamps(grp))
                     for gk, grp in prop_groups.items()
                     if not gk.startswith('DOC:') and prop_groups[gk].get('title_doc')]

        for doc_gk in ungrouped_doc_keys:
            if doc_gk not in prop_groups:
                continue
            doc_grp = prop_groups[doc_gk]
            doc_ts_list = _group_timestamps(doc_grp)
            if not doc_ts_list:
                continue

            best_named_gk = None
            best_gap_secs = PROX_MINUTES * 60 + 1  # beyond threshold

            for named_gk, named_ts_list in named_gks:
                if named_gk not in prop_groups:
                    continue
                if not named_ts_list:
                    continue
                # Minimum gap between any doc in named group and any doc in DOC group
                for dt in doc_ts_list:
                    for nt in named_ts_list:
                        gap = abs((dt - nt).total_seconds())
                        if gap < best_gap_secs:
                            best_gap_secs = gap
                            best_named_gk = named_gk

            if best_named_gk and best_named_gk in prop_groups:
                target = prop_groups[best_named_gk]
                known_ids = set(target['all_doc_ids'])
                for ds in (([doc_grp['title_doc']] if doc_grp['title_doc'] else [])
                           + doc_grp['support_docs']):
                    if ds and ds['document_id'] not in known_ids:
                        target['support_docs'].append(ds)
                        target['all_doc_ids'].append(ds['document_id'])
                        known_ids.add(ds['document_id'])
                del prop_groups[doc_gk]

    # ── FINAL PASS: address+lot merge (handles OCR title-number variation) ──
    # Two groups with the SAME lot_number AND substantially the same address
    # are the same physical property — even if their title_numbers differ
    # (OCR misread 564662 ↔ 504662, or one says 'VALUE: GRN35662' and another
    # '564662'). This catches the duplicate-property-card explosion where 6
    # different titles all point to the same Menara C / Tepian Bayu unit.
    def _addr_signature(grp: Dict[str, Any]) -> str:
        td = grp.get('title_doc') or {}
        ex_ = td.get('extracted') or {}
        return _norm_addr(ex_.get('property_address') or ex_.get('description') or '')[:60]

    def _lot_signature(grp: Dict[str, Any]) -> str:
        td = grp.get('title_doc') or {}
        ex_ = td.get('extracted') or {}
        raw = (ex_.get('lot_number') or '').strip()
        cleaned = _clean_id_value(raw)
        if _looks_like_garbage(cleaned):
            return ''
        # Only keep digits + letters that matter — drop spaces, slashes, dashes
        return re.sub(r'[\s/\-]', '', cleaned)

    # Group keys by (lot_signature, addr_signature). Same signature → same property.
    sig_to_keys: Dict[tuple, List[str]] = {}
    for gk, grp in list(prop_groups.items()):
        if not grp.get('title_doc'):
            continue
        lot_sig = _lot_signature(grp)
        addr_sig = _addr_signature(grp)
        if not lot_sig and not addr_sig:
            continue  # nothing to dedupe by
        # ── Drop if this property is ALREADY in step5_data ──────────────
        # Different OCR title than the accepted one would slip past the
        # group_key check above; the (lot, addr) sig catches it.
        # 🔥 STRATA EXCEPTION (§10hd): same lot + empty addr is the
        # collision case for two units in one building. If the pending
        # group is strata AND its title signature is genuinely different
        # (not OCR truncation) from EVERY accepted title at this (lot,
        # addr) → KEEP IT, do NOT filter.
        if (lot_sig, addr_sig) in referenced_lot_addr_sigs:
            td_ex = (grp.get('title_doc') or {}).get('extracted') or {}
            grp_strata = _is_strata(td_ex)
            grp_title_sig = _title_signature(td_ex)
            ref_title_sigs = referenced_sig_titles.get((lot_sig, addr_sig)) or set()
            different_unit = (
                grp_strata and grp_title_sig and bool(ref_title_sigs)
                and all(_is_genuinely_different_unit(grp_title_sig, rs)
                        for rs in ref_title_sigs)
            )
            if different_unit:
                pass  # keep the group, fall through to sig grouping
            else:
                del prop_groups[gk]
                continue
        # ── 🔥 OCR-TRUNCATION FILTER ─────────────────────────────────────
        # If this group's (lot, master_title) matches an accepted gift's
        # (lot, master_title), it's a phantom of that accepted gift —
        # the sibling carrying the master-only title number that didn't
        # match by (lot, addr) because its address was missing or
        # polluted by stale enrichment. Filter it out UNLESS the title
        # signatures are genuinely different (different parcel suffix).
        td_ex = (grp.get('title_doc') or {}).get('extracted') or {}
        grp_title_sig = _title_signature(td_ex)
        grp_master = _master_title(grp_title_sig)
        if lot_sig and grp_master and (lot_sig, grp_master) in referenced_lot_master_titles:
            # Check if any accepted title at this lot is genuinely a
            # different unit. If ALL accepted titles share the master AND
            # this group's title is OCR truncation of one of them, drop.
            phantom = False
            for ref_sigs in referenced_sig_titles.values():
                for rs in ref_sigs:
                    if _master_title(rs) == grp_master:
                        # Same master. Is this group OCR-truncation
                        # (master only, no parcel encoding) of that ref?
                        # _is_genuinely_different_unit returns False in
                        # that case → phantom.
                        if not _is_genuinely_different_unit(grp_title_sig, rs):
                            phantom = True
                            break
                if phantom:
                    break
            if phantom:
                del prop_groups[gk]
                continue
        sig = (lot_sig, addr_sig)
        sig_to_keys.setdefault(sig, []).append(gk)

    for sig, gks in sig_to_keys.items():
        if len(gks) < 2:
            continue
        # Pick the group with the best title_number (non-garbage, has a real value)
        # as the primary, fold the rest into it.
        def _title_quality(gk: str) -> int:
            grp = prop_groups.get(gk) or {}
            td = grp.get('title_doc') or {}
            ex_ = td.get('extracted') or {}
            tn = (ex_.get('title_number') or '').strip()
            if not tn or _looks_like_garbage(tn):
                return 0
            cleaned = _clean_id_value(tn)
            # Pure digits = best (most likely correct OCR), then alnum, then everything else
            if cleaned.isdigit():
                return 3
            if cleaned.replace('/', '').replace('-', '').replace('.', '').isalnum():
                return 2
            return 1
        best_gk = max(gks, key=_title_quality)
        target = prop_groups[best_gk]
        known_ids = set(target['all_doc_ids'])
        for gk in gks:
            if gk == best_gk or gk not in prop_groups:
                continue
            grp = prop_groups[gk]
            # ╔══════════════════════════════════════════════════════════════╗
            # ║  🔥 BURN-IN — STRATA: same lot ≠ same property 🔥              ║
            # ║  CLAUDE.md §10hd. Two strata parcels in one building share   ║
            # ║  the lot but have DIFFERENT title numbers (and addresses).   ║
            # ║  Refuse to merge if either side is strata and title          ║
            # ║  signatures don't match.                                     ║
            # ╚══════════════════════════════════════════════════════════════╝
            if not _safe_to_merge(target, grp):
                continue
            for ds in (([grp['title_doc']] if grp['title_doc'] else [])
                       + grp['support_docs']):
                if ds and ds['document_id'] not in known_ids:
                    target['support_docs'].append(ds)
                    target['all_doc_ids'].append(ds['document_id'])
                    known_ids.add(ds['document_id'])
            del prop_groups[gk]

    # ╔════════════════════════════════════════════════════════════════════╗
    # ║  🔥 BURN-IN — CROSS-SIG OCR-TRUNCATION MERGE 🔥                     ║
    # ║  CLAUDE.md §10hd. Two groups with the SAME lot AND the SAME        ║
    # ║  master title number but at DIFFERENT (lot,addr) sig keys —        ║
    # ║  typically because one side has the full strata title              ║
    # ║  encoding ("564662/M1C/30/710") + a real street address, and       ║
    # ║  the other side has only the master ("564662") and no address —    ║
    # ║  are the same unit (front page vs back page of the same geran,    ║
    # ║  or title-doc vs SPA). The (lot,addr) pass above can't merge      ║
    # ║  them because their addresses differ. Run a pairwise pass that    ║
    # ║  uses _safe_to_merge to fold OCR-truncation pairs together.       ║
    # ║                                                                    ║
    # ║  IMPORTANT: only fold when _safe_to_merge returns True (which     ║
    # ║  enforces same master + only one side has parcel encoding —      ║
    # ║  the OCR truncation case). Two genuine strata parcels in the     ║
    # ║  same building with different suffixes are NOT merged.           ║
    # ╚════════════════════════════════════════════════════════════════════╝
    def _has_lot_master(grp: Dict[str, Any]) -> tuple:
        """Return (lot_digits, master_title_digits) for grouping."""
        td = grp.get('title_doc') or {}
        ex_ = td.get('extracted') or {}
        lot_raw = (ex_.get('lot_number') or '').strip()
        lot_clean = _clean_id_value(lot_raw)
        lot_digits = re.sub(r'\D', '', lot_clean)
        if _looks_like_garbage(lot_clean):
            lot_digits = ''
        title_sig = _title_signature(ex_)
        master = _master_title(title_sig)
        return (lot_digits, master)

    # Build (lot, master) → [gk, ...] map; merge any group with len > 1.
    lot_master_map: Dict[tuple, List[str]] = {}
    for gk, grp in list(prop_groups.items()):
        if not grp.get('title_doc'):
            continue
        lm = _has_lot_master(grp)
        if not lm[0] or not lm[1]:
            continue   # need both lot AND master to risk OCR-truncation merge
        lot_master_map.setdefault(lm, []).append(gk)

    for lm, gks in lot_master_map.items():
        if len(gks) < 2:
            continue
        # Pick the highest-quality group as target (most extracted fields,
        # plus prefer ones with a real address).
        def _gq(gk):
            grp_ = prop_groups.get(gk) or {}
            td_ = grp_.get('title_doc') or {}
            ex_ = td_.get('extracted') or {}
            filled = sum(1 for k in ('title_number', 'lot_number', 'mukim',
                                      'daerah', 'negeri', 'property_address')
                         if (ex_.get(k) or '').strip())
            has_addr = 1 if (ex_.get('property_address') or '').strip() else 0
            # Prefer FULL strata encoding ("564662/M1C/30/710") over master-only
            # ("564662") — the full encoding came off a real title page.
            sig_ = _title_signature(ex_)
            has_parcel = 1 if '/' in sig_ else 0
            return filled * 10 + has_addr * 5 + has_parcel * 3
        best = max(gks, key=_gq)
        target = prop_groups[best]
        known_ids = set(target['all_doc_ids'])
        for gk in gks:
            if gk == best or gk not in prop_groups:
                continue
            grp = prop_groups[gk]
            # _safe_to_merge ensures: same master + at most one side has
            # parcel encoding (i.e. OCR-truncation case). Two distinct
            # parcels at (564662/M1C/30/710) vs (564662/M1C/05/100) would
            # have already split by sig_to_keys above and BOTH sides have
            # parcel encoding → _safe_to_merge returns False.
            if not _safe_to_merge(target, grp):
                continue
            for ds in (([grp['title_doc']] if grp['title_doc'] else [])
                       + grp['support_docs']):
                if ds and ds['document_id'] not in known_ids:
                    target['support_docs'].append(ds)
                    target['all_doc_ids'].append(ds['document_id'])
                    known_ids.add(ds['document_id'])
            del prop_groups[gk]

    # ╔════════════════════════════════════════════════════════════════════╗
    # ║  🔥 BURN-IN RULE — ASSET WALKTHROUGH ORDER 🔥                       ║
    # ║  ALWAYS start with the asset that has HIGHEST confidence.          ║
    # ║  LOWEST confidence comes LAST. No exceptions, no random order,     ║
    # ║  no "first uploaded." High → Low. See CLAUDE.md §10e.              ║
    # ║                                                                    ║
    # ║  Confidence = _score_property_confidence(extracted) — multi-image  ║
    # ║  groups with NLC ids cross-referenced to the user's WhatsApp text  ║
    # ║  score highest. Isolated single images with no chat match score    ║
    # ║  lowest and are inventoried LAST.                                  ║
    # ╚════════════════════════════════════════════════════════════════════╝
    def _group_confidence(gk_grp_pair):
        _, grp = gk_grp_pair
        td = grp.get('title_doc') or {}
        ex_ = (td.get('extracted') or {}) if isinstance(td, dict) else {}
        return _score_property_confidence(ex_)

    sorted_groups = sorted(prop_groups.items(), key=_group_confidence, reverse=True)

    # ── Emit one card per group ────────────────────────────────────────────
    # Only emit if a property_title is present (ownership evidence required).
    # Groups with only SPA/cukai tanah are orphaned — skip for now.
    for gk, grp in sorted_groups:
        primary = grp['title_doc']
        if primary:
            # Deduplicate support docs by BOTH document_id AND original_filename.
            # Two separate checks are needed:
            #   1. document_id: same DB row added twice from multiple merge passes
            #   2. original_filename: same physical file uploaded multiple times
            # Previously only deduplicated by filename, so a doc with an empty
            # filename would bypass the check entirely (if fname → False).
            seen_doc_ids: set = set()
            seen_fnames: set = set()
            primary_did = primary.get('document_id')
            primary_fname = (primary.get('original_filename') or '').strip()
            if primary_did is not None:
                seen_doc_ids.add(primary_did)
            if primary_fname:
                seen_fnames.add(primary_fname)
            deduped: list = []
            for s in grp['support_docs']:
                did   = s.get('document_id')
                fname = (s.get('original_filename') or '').strip()
                # Skip if same document_id already included
                if did is not None and did in seen_doc_ids:
                    continue
                # Skip if same non-empty filename already included
                if fname and fname in seen_fnames:
                    continue
                if did is not None:
                    seen_doc_ids.add(did)
                if fname:
                    seen_fnames.add(fname)
                deduped.append(s)
            primary['support_docs'] = deduped
            primary['group_key'] = gk
            out['property'].append(primary)

    # ╔════════════════════════════════════════════════════════════════════╗
    # ║  🔥 BURN-IN §10x.12 + §10hg + §10x.15 — AI-SUMMARY-ONLY SYNTHESIS  ║
    # ║  Every asset NAMED in the AI Summary must produce a pending entry, ║
    # ║  even when no image / statement / policy was uploaded. The user    ║
    # ║  described it in text — that's enough (per §10x.15: "Image is      ║
    # ║  verification only — text details are sufficient"). Surface as     ║
    # ║  H3 placeholders so the walkthrough can complete every gift.       ║
    # ╚════════════════════════════════════════════════════════════════════╝
    out.setdefault('insurance', [])
    try:
        from ai.chat_planner import (
            _extract_ai_summary_properties,
            _extract_ai_summary_banks,
            _extract_ai_summary_insurance,
        )
        ai_props = _extract_ai_summary_properties(client_id) or []
        ai_banks = _extract_ai_summary_banks(client_id) or []
        ai_ins   = _extract_ai_summary_insurance(client_id) or []
    except Exception:
        ai_props, ai_banks, ai_ins = [], [], []

    def _digits_only(s: str) -> str:
        return re.sub(r'\D', '', s or '')

    # ╔════════════════════════════════════════════════════════════════════╗
    # ║  🔥 §10h — AI Summary IS the canonical asset list (count = N).    ║
    # ║  Match image groups TO AI Summary entries. Image groups that      ║
    # ║  cannot be bound to any AI Summary property are RESIDUAL noise —  ║
    # ║  they get filtered out of the walkthrough (§10d unverified card  ║
    # ║  is shown for those instead).                                     ║
    # ╚════════════════════════════════════════════════════════════════════╝
    def _addr_tokens_local(addr: str) -> set:
        STOP = {'JALAN', 'TAMAN', 'BANDAR', 'KAMPUNG', 'KAMPONG',
                'UNIT', 'BLOCK', 'BLOK', 'NO', 'JOHOR', 'BAHRU',
                'KUALA', 'LUMPUR', 'SELANGOR', 'CONDOMINIUM',
                'APARTMENT', 'PERSIARAN', 'SOLOK', 'LORONG',
                'LEBUH', 'MUKIM', 'DAERAH', 'NEGERI', 'STATE',
                'DISTRICT', 'MALAYSIA', 'PHASE', 'WITH',
                'KAWASAN', 'PERUSAHAAN'}
        out_t: set = set()
        for t in re.findall(r"[A-Za-z]{4,}", (addr or '').upper()):
            if t in STOP: continue
            out_t.add(t)
        return out_t

    # Build AI Summary signature index for image-group dedup
    ai_lot_digits: set = set()
    ai_title_digits: set = set()
    ai_tokens: set = set()
    for ap in ai_props:
        ld = _digits_only(ap.get('lot', ''))
        td = _digits_only(ap.get('title', ''))
        if ld: ai_lot_digits.add(ld)
        if td: ai_title_digits.add(td)
        ai_tokens.update(_addr_tokens_local(ap.get('address', '')))
        ai_tokens.update(_addr_tokens_local(ap.get('name', '')))

    # 🔥 §10h v3 — match each image group to ONE AI Summary property
    # via lot/title/tokens/mukim (geographic bridge). Keep matched
    # images in pending; demote unmatched ones to §10d "unverified"
    # (so they DON'T inflate pending count beyond AI Summary count).
    # Then synthesize H3 placeholders ONLY for AI Summary entries
    # that no image group claimed.
    if ai_props:
        # Build per-AI-prop signature for matching
        def _addr_token_set(s: str) -> set:
            STOP = {'JALAN', 'TAMAN', 'BANDAR', 'KAMPUNG', 'UNIT',
                     'BLOCK', 'BLOK', 'NO', 'JOHOR', 'BAHRU', 'KUALA',
                     'LUMPUR', 'CONDOMINIUM', 'APARTMENT', 'PERSIARAN',
                     'LORONG', 'LEBUH', 'MUKIM', 'DAERAH', 'NEGERI',
                     'STATE', 'DISTRICT', 'MALAYSIA', 'PHASE', 'WITH',
                     'KAWASAN', 'PERUSAHAAN'}
            return {t for t in re.findall(r"[A-Za-z0-9\-]{4,}",
                                            (s or '').upper())
                    if t not in STOP}
        # Geographic bridge — map common Johor localities to mukim
        GEO_BRIDGE = {
            'SERI ALAM': 'PLENTONG', 'MARINA COVE': 'PLENTONG',
            'PERMAS JAYA': 'PLENTONG', 'TAMAN LAGUNA': 'PLENTONG',
            'TAMAN AUSTIN': 'TEBRAU', 'MEDINI': 'PULAI',
            'ISKANDAR PUTERI': 'PULAU', 'PARADISO': 'PULAI',
            'PARADISONUAVA': 'PULAI', 'NUSAJAYA': 'PULAI',
            'SENAI': 'SENAI',
        }
        ai_signatures = []
        for ap in ai_props:
            addr = (ap.get('address') or '') + ' ' + (ap.get('name') or '')
            toks = _addr_token_set(addr)
            mukim_hint = None
            for loc, mk in GEO_BRIDGE.items():
                if loc in addr.upper():
                    mukim_hint = mk; break
            ai_signatures.append({
                'lot':   _digits_only(ap.get('lot') or ''),
                'title': _digits_only(ap.get('title') or ''),
                'toks':  toks,
                'mukim': (ap.get('mukim') or mukim_hint or '').upper(),
                'matched_image_idx': None,
            })

        # Greedy match: each image to first available AI Summary slot
        kept_images = []
        for img_idx, grp in enumerate(out['property']):
            ex = (grp.get('extracted') or {}) if grp else {}
            i_lot   = _digits_only(_clean_id_value(ex.get('lot_number') or ''))
            i_title = _digits_only(_clean_id_value(ex.get('title_number') or ''))
            i_toks  = _addr_token_set(ex.get('property_address') or '')
            i_mukim = (ex.get('mukim') or '').upper().strip()
            best_ai_idx = None
            best_score = 0
            for ai_idx, sig in enumerate(ai_signatures):
                if sig['matched_image_idx'] is not None:
                    continue   # already matched
                score = 0
                if sig['lot'] and i_lot and sig['lot'] == i_lot:    score += 5
                if sig['title'] and i_title and sig['title'] == i_title: score += 5
                if sig['toks'] and i_toks and (sig['toks'] & i_toks):
                    score += 3
                if sig['mukim'] and i_mukim and sig['mukim'] in i_mukim:
                    score += 2
                if score > best_score:
                    best_score = score
                    best_ai_idx = ai_idx
            # 🔥 §10h — require STRONG match: lot+title (10), title or lot
            # alone (5), or token+mukim combined (5). Mukim alone (2) is
            # not enough — Plentong matches 4 properties for KOID. Image
            # groups with weak match go to §10d residual (manual binding).
            if best_ai_idx is not None and best_score >= 5:
                ai_signatures[best_ai_idx]['matched_image_idx'] = img_idx
                grp['_ai_summary_match'] = ai_props[best_ai_idx]
                kept_images.append(grp)
            # else: image has no strong AI Summary linkage → drop (§10d
            # residual). User can later use "Match to existing property"
            # button or upload the missing identification.
        out['property'] = kept_images
    # When ai_props is empty, leave out['property'] alone (legacy path)

    # ── Properties: add H3 for AI-Summary properties not already covered
    # ╔════════════════════════════════════════════════════════════════════╗
    # ║  🔥🔥🔥 §10x.133 META — RECURRING BUG CLASS GUARD 🔥🔥🔥             ║
    # ║                                                                     ║
    # ║  STOP. Before modifying ANY dedup logic in this section, READ:      ║
    # ║    • CLAUDE.md §10x.39 row 106 (the META consolidation row)         ║
    # ║    • CLAUDE.md §10hd (Strata: same lot ≠ same property)             ║
    # ║    • CLAUDE.md §10b (Property Count == AI Summary Count)            ║
    # ║                                                                     ║
    # ║  The "missing properties from walkthrough" bug has been reported    ║
    # ║  4+ times. Every reported instance had the SAME root cause: a new   ║
    # ║  well-meaning dedup using a single signal (token overlap, mukim     ║
    # ║  match, address prefix) caused two AI Summary properties to merge   ║
    # ║  into one OR an unbound AI prop to be marked "covered".             ║
    # ║                                                                     ║
    # ║  HARD RULES for any dedup added below:                              ║
    # ║    1. NEVER token-overlap dedup. Strata + same-Taman properties     ║
    # ║       share locality tokens by construction.                        ║
    # ║    2. NEVER mukim-only dedup. Mukim Plentong has 4 KOID properties. ║
    # ║    3. NEVER address-prefix-only dedup. Different units in same      ║
    # ║       building have similar address prefixes.                       ║
    # ║    4. ONLY identity-equality:                                       ║
    # ║       • Strata: lot == lot AND title == title (per §10hd)           ║
    # ║       • Landed: address-norm[:60] == address-norm[:60]              ║
    # ║    5. When in doubt, surface H3 placeholder + ASK USER (§10d).      ║
    # ║       False-negative dedup is INFINITELY better than                ║
    # ║       false-positive dedup.                                         ║
    # ║    6. Property count N = len(_extract_ai_summary_properties()) —    ║
    # ║       NEVER count from gift_walker pending or step5 saved.          ║
    # ║                                                                     ║
    # ║  Past offenders that re-introduced this bug class:                  ║
    # ║    • §10x.95 (lexical address substring match — superseded by       ║
    # ║      §10x.95 v2's pipeline binding)                                 ║
    # ║    • §10b token-overlap (REMOVED in §10x.132 after 4 reports)       ║
    # ║                                                                     ║
    # ║  If you find yourself writing `if X and (X & covered_X)`, STOP.     ║
    # ║  Use identity-equality. If identity-equality is genuinely too       ║
    # ║  strict for your case, add an H3 placeholder + ask the user.        ║
    # ╚════════════════════════════════════════════════════════════════════╝
    # Original §10b intent: match by lot, title, normalised address. Token
    # overlap was added later for OCR-vs-typed-address cases but caused
    # 4+ regressions and is FORBIDDEN per §10x.132 / §10x.133.
    covered_lot_digits: set = set()
    covered_title_digits: set = set()
    covered_addr_norms: set = set()
    covered_tokens: set = set()  # distinctive locality tokens

    def _addr_tokens(addr: str) -> set:
        """Return set of 4+ char alphabetic tokens (UPPER) excluding
        common stop-words, useful for fuzzy property-address matching."""
        STOP = {'JALAN', 'TAMAN', 'BANDAR', 'KAMPUNG', 'KAMPONG',
                'UNIT', 'BLOCK', 'BLOK', 'NO', 'JOHOR', 'BAHRU',
                'KUALA', 'LUMPUR', 'SELANGOR', 'CONDOMINIUM',
                'APARTMENT', 'PERSIARAN', 'SOLOK', 'LORONG',
                'LEBUH', 'MUKIM', 'DAERAH', 'NEGERI', 'STATE',
                'DISTRICT', 'MALAYSIA', 'PHASE', 'WITH',
                'KAWASAN', 'PERUSAHAAN', 'CONDOMINIUMS'}
        out_t: set = set()
        for t in re.findall(r"[A-Za-z]{4,}", (addr or '').upper()):
            if t in STOP: continue
            out_t.add(t)
        return out_t

    covered_mukims: set = set()
    for grp in out['property']:
        ex = (grp.get('extracted') or {}) if grp else {}
        ld = _digits_only(_clean_id_value(ex.get('lot_number') or ''))
        td = _digits_only(_clean_id_value(ex.get('title_number') or ''))
        addr_raw = ex.get('property_address') or ''
        an = _norm_addr(addr_raw)[:60]
        mk = (ex.get('mukim') or '').strip().upper()
        if ld:  covered_lot_digits.add(ld)
        if td:  covered_title_digits.add(td)
        if an:  covered_addr_norms.add(an)
        if mk:  covered_mukims.add(mk)
        covered_tokens.update(_addr_tokens(addr_raw))
    # Also mark properties already saved to step5_data as covered
    for sig in referenced_lot_addr_sigs:
        if sig[0]: covered_lot_digits.add(sig[0])
        if sig[1]: covered_addr_norms.add(sig[1])

    for ap in ai_props:
        a_lot = _digits_only(ap.get('lot') or '')
        a_title = _digits_only(ap.get('title') or '')
        a_addr_raw = ap.get('address') or ''
        a_addr = _norm_addr(a_addr_raw)[:60]
        a_toks = _addr_tokens(a_addr_raw)
        # Match by ANY of: lot digits, title digits, normalised address,
        # OR ≥1 distinctive locality token in common with a covered group.
        if a_lot and a_lot in covered_lot_digits:
            continue
        if a_title and a_title in covered_title_digits:
            continue
        if a_addr and a_addr in covered_addr_norms:
            continue
        # 🔥 §10x.132 — REMOVED token-overlap dedup entirely.
        # Original §10b intent was to dedup AI props vs image groups
        # when OCR addresses differ from user-typed addresses for the
        # SAME property. But token overlap is fundamentally wrong for
        # STRATA properties: C-30-08 + C-05-01 share 'MARINA'+'COVE',
        # B-05-11 + nothing in Paradiso, etc. Different units in same
        # building share locality tokens by construction (CLAUDE.md
        # §10hd). Single OR multi-token overlap both produce false
        # positives. The 3 explicit checks above (lot digits, title
        # digits, address-norm[:60]) are sufficient — they're identity-
        # equality checks, not fuzzy. If neither lot, title, nor
        # normalised address matches → it's a different property →
        # surface H3 placeholder.
        # Bug fixed: KOID had 5 AI Summary properties but only 2 ever
        # surfaced ('MARINA'+'COVE' false-matched C-05-01 against
        # C-30-08; 'SERI ALAM MASAI' false-matched Sri Laguna; etc).
        # H3 placeholder per §10hg — confirm-then-complete card
        out['property'].append({
            '_h3_placeholder': True,
            '_ai_summary_match': ap,
            'document_id': None,
            'support_docs': [],
            'all_doc_ids': [],
            'group_key': f'H3:property:{(a_addr or a_lot or ap.get("name","")).strip()[:40]}',
            'extracted': {
                'property_address': ap.get('address', ''),
                'lot_number': ap.get('lot', ''),
                'title_number': ap.get('title', ''),
                'mukim': ap.get('mukim', ''),
                'daerah': ap.get('daerah', ''),
                'negeri': ap.get('negeri', ''),
                '_h3_source': 'ai_summary',
            },
            'name': ap.get('name', ''),
        })

    # ── Banks: AI Summary entries → H3 placeholders if no statement uploaded
    covered_bank_acct: set = set()
    for b in out['bank']:
        ex = (b.get('extracted') or {}) if b else {}
        an = _digits_only(ex.get('account_number') or '')
        if an: covered_bank_acct.add(an)
    # step5_data saved banks
    for g in gifts or []:
        if isinstance(g, dict) and g.get('kind') == 'bank':
            an = _digits_only(g.get('account_number') or '')
            if an: covered_bank_acct.add(an)
    seen_h3_bank_acct: set = set()  # dedup AI Summary's own duplicates
    for ab in ai_banks:
        an = _digits_only(ab.get('account_number') or '')
        if not an:
            continue
        if an in covered_bank_acct or an in seen_h3_bank_acct:
            continue
        seen_h3_bank_acct.add(an)
        out['bank'].append({
            '_h3_placeholder': True,
            '_ai_summary_match': ab,
            'document_id': None,
            'support_docs': [],
            'all_doc_ids': [],
            'group_key': f'H3:bank:{an}',
            'extracted': {
                'institution':    ab.get('institution', ''),
                'account_number': ab.get('account_number', ''),
                'account_type':   ab.get('account_type', ''),
                'country':        ab.get('country', ''),
                '_h3_source':     'ai_summary',
            },
        })

    # ── Insurance: same pattern (always H3 unless policy doc uploaded)
    covered_ins_pol: set = set()
    for ins in out['insurance']:
        ex = (ins.get('extracted') or {}) if ins else {}
        pn = (ex.get('policy_number') or '').strip().upper()
        if pn: covered_ins_pol.add(pn)
    for g in gifts or []:
        if isinstance(g, dict) and g.get('kind') == 'insurance':
            pn = (g.get('policy_number') or '').strip().upper()
            if pn: covered_ins_pol.add(pn)
    seen_h3_ins_pol: set = set()
    for ai in ai_ins:
        pn = (ai.get('policy_number') or '').strip().upper()
        if not pn:
            continue
        if pn in covered_ins_pol or pn in seen_h3_ins_pol:
            continue
        seen_h3_ins_pol.add(pn)
        out['insurance'].append({
            '_h3_placeholder': True,
            '_ai_summary_match': ai,
            'document_id': None,
            'support_docs': [],
            'all_doc_ids': [],
            'group_key': f'H3:insurance:{pn}',
            'extracted': {
                'insurer':       ai.get('insurer', ''),
                'policy_number': ai.get('policy_number', ''),
                '_h3_source':    'ai_summary',
            },
        })

    return out


# Match phrases like "Joshua 50%", "Joshua 1/2", "Joshua equal", "Joshua and Esther equal share"
_SHARE_RE = re.compile(
    r'(\d+\s*%|\d+/\d+|equal(?:ly|\s+shares?)?)',
    re.IGNORECASE,
)


def parse_beneficiary_shares(text: str, known_names: List[str]) -> List[Dict[str, str]]:
    """Parse user text mentioning beneficiaries + their shares.
    Returns [{'name': 'JOSHUA…', 'share': '50%'}, ...].

    Strategy: find every known_name mentioned in the text (case-insensitive),
    then for each, look at adjacent text for a share token. If no share
    found and there's only one beneficiary, default to '100%'.  If multiple
    and no shares, default to 'equal'.
    """
    if not text or not known_names:
        return []
    t = text.lower()
    found = []
    for name in known_names:
        n = name.lower().strip()
        if not n:
            continue
        idx = t.find(n)
        if idx == -1:
            continue
        # Look for a share within 40 chars after the name (or before)
        window = t[idx: min(len(t), idx + len(n) + 40)] + ' ' + t[max(0, idx-25): idx]
        sh = _SHARE_RE.search(window)
        share = sh.group(1).strip() if sh else None
        found.append({'name': name, 'share': share, '_idx': idx})

    # Sort by position in text
    found.sort(key=lambda x: x['_idx'])
    for f in found:
        del f['_idx']

    if not found:
        return []
    # Defaults: single → 100%, multiple → equal
    missing = [f for f in found if not f['share']]
    if len(found) == 1 and missing:
        found[0]['share'] = '100%'
    elif missing:
        for f in missing:
            f['share'] = 'equal'
    return found
