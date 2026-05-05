"""Orchestrator for the client chat — directed step-by-step flow.

Stages (in priority order):
  1. INTAKE — fresh attachments arrived this turn → emit aggregated summary
  2. IDENTITIES — pending ICs (no Person row linked) → walk one at a time
                  with role pre-deduced from forwarded email text
  3. EXECUTOR — identities done, no executor yet → ask
  4. (BEYOND) — beneficiaries/gifts/etc. (next slices)

Each stage knows how to advance to the next; the planner emits a
"✅ Step N complete — moving to Step N+1" line at the boundary.
"""
from typing import List, Dict, Any, Optional
import json as _json
import json
import re


def _qr_marker(quick: List[Dict[str, str]]) -> str:
    """Encode quick-reply buttons as a comment marker the chat.js renderer
    parses out and renders as a button row. Always appends a 'None — type
    in chat' fallback so the user can free-form when buttons don't fit."""
    if not quick:
        return ''
    has_fallback = any((q.get('value') or '').lower() in ('other', 'none', 'type')
                       for q in quick)
    if not has_fallback:
        quick = list(quick) + [{'label': '✏️ None of above — I\'ll type', 'value': 'other'}]
    return f"\n\n<!--quickreplies:{_json.dumps(quick)}-->"


def _ddmmyyyy_to_iso(dob: str) -> str:
    if not dob: return ''
    parts = dob.split('-')
    if len(parts) == 3 and len(parts[2]) == 4 and parts[2].isdigit():
        return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
    return dob


def _ic_to_step1_patch(ic: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'full_name': (ic.get('full_name') or '').strip(),
        'nric_passport': (ic.get('nric_number') or '').strip(),
        'residential_address': (ic.get('address') or '').strip(),
        'date_of_birth': _ddmmyyyy_to_iso(ic.get('date_of_birth') or ''),
        'nationality': ic.get('nationality') or 'Malaysian',
        'gender': ic.get('gender') or '',
    }


def plan_turn(
    user_text: str,
    artifacts: List[Dict[str, Any]],
    current_will_data: Optional[Dict[str, Any]] = None,
    pending_ics: Optional[List[Dict[str, Any]]] = None,
    recent_text: str = '',
    just_assigned: Optional[Dict[str, str]] = None,
    just_deleted: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Returns {reply, clarifying_questions, proposed_patch, advice}.

    pending_ics: list of {document_id, extracted, original_filename, ...}
                 from services.identity_walker.get_pending_ic_documents
    recent_text: concatenation of recent user messages — used to deduce
                 roles via ai.role_deducer
    just_assigned: {name, role} if the previous turn confirmed an identity
                   (so we can announce "✓ saved X as Y" before next question)
    """
    current_will_data = current_will_data or {}
    pending_ics = pending_ics or []
    reply_parts: List[str] = []
    questions: List[str] = []
    advice: List[Dict[str, str]] = []
    patch: Dict[str, Any] = {}

    # ── Acknowledge an assignment / deletion from the previous turn ─────
    just_kind = (just_assigned or {}).get('kind', 'identity')
    if just_assigned:
        # Special-case the asset-inventory gate so the ack reads as a
        # phase transition, not a "saved X as Y" line.
        if just_kind == 'assets_confirmed':
            reply_parts.append(
                "✅ **Asset inventory locked in.** Now let's assign each one "
                "to a beneficiary."
            )
        elif just_kind == 'assets_more':
            reply_parts.append(
                "👍 Got it — drop the additional documents and I'll cluster "
                "them in. Reply 'confirm' when you're done."
            )
        elif just_kind.startswith('inventory_reviewed_'):
            pass  # card for the NEXT asset IS the headline — no prefix needed
        elif just_kind.startswith('inventory_skipped_'):
            pass  # same — card follows immediately
        elif just_kind == 'gift_main':
            pass  # Phase B substitute prompt IS the headline — no ack prefix needed
        elif just_kind == 'gift':
            pass  # Next gift's Phase A card IS the headline
        elif just_kind == 'gift_skip':
            pass  # Next gift card follows immediately
        elif just_kind in ('inbox_start', 'inbox_removed', 'inbox_restart',
                           'gifts_restart'):
            pass  # inbox/restart action — reply_override handles the message
        elif just_kind == 'inventory_unlink_pending':
            reply_parts.append(
                f"✂️  Reviewing supporting docs for **{just_assigned.get('name','')}**…"
            )
        elif just_kind == 'unlink_one':
            reply_parts.append(
                f"🗑 Unlinked **{just_assigned.get('name','')}** — moved to "
                "the unclassified pool."
            )
        elif just_kind == 'unlink_done':
            reply_parts.append(
                "✅ Kept all supporting docs as-is. Back to the property:"
            )
        elif just_kind == 'identity_skipped':
            reply_parts.append(
                f"⏭ Skipped **{just_assigned.get('name','')}** — moving to next."
            )
        else:
            reply_parts.append(
                f"✅ Saved **{just_assigned.get('name','')}** as **{just_assigned.get('role','')}**."
            )
    if just_deleted:
        n = just_deleted.get('count', 1)
        suffix = f" ({n} duplicate{'s' if n != 1 else ''} removed)" if n > 1 else ''
        reply_parts.append(
            f"🗑 Removed **{just_deleted.get('name','')}** from this client's records{suffix}."
        )

    # ── 1. INTAKE — fresh attachments this turn ─────────────────────────
    if artifacts:
        # Use the rich Email Inbox Review card (shows cleaned message text,
        # numbered image list, per-image remove buttons, start-analysis CTA).
        # The plain _intake_summary is only used as fallback inside the card.
        _intake_card = _intake_email_card(
            artifacts, user_text or '', current_will_data=current_will_data
        )
        reply_parts.append(_intake_card)
        # Show ALL fresh attachment thumbnails in the carousel
        focus_ids = [a['document_id'] for a in artifacts if a.get('document_id')]
        return _wrap(reply_parts, questions, patch, advice, focus_attachments=focus_ids)

    # ── 2. IDENTITY WALK-THROUGH — pending IC? ──────────────────────────
    if pending_ics:
        reply_parts.append(_identity_question(pending_ics, recent_text))
        # Show the IC photo for the one being asked about so user can verify
        focus = [pending_ics[0]['document_id']] if pending_ics[0].get('document_id') else []
        return _wrap(reply_parts, questions, patch, advice, focus_attachments=focus)

    # No pending IC — Step 1 (Identities) is complete (or empty)
    s1 = current_will_data.get('step1') or {}
    s2 = current_will_data.get('step2') or {}
    n_executors = len((s2.get('executors') or []))
    n_beneficiaries = len(current_will_data.get('step4') or [])

    # Announce Step 1 completion only if we just finished an IDENTITY
    # assignment AND there are no more pending ICs (avoid firing on
    # executor / other-stage saves).
    if just_assigned and not pending_ics and just_kind == 'identity':
        reply_parts.append(
            "🎉 **Step 1: Identities — COMPLETE.** All ICs assigned. "
            "Now moving to **Step 2: Testator Info**."
        )

    # ── 3. STEP 2: confirm Testator details ─────────────────────────────
    if s1.get('full_name') and not _is_confirmed(current_will_data, 'testator'):
        reply_parts.append(_step2_question(s1))
        return _wrap(reply_parts, questions, patch, advice)

    # ── 3.5 ASSET INVENTORY — walk one cleaned-up property at a time ───
    # Job-to-be-done: the WILL WRITER (chat user) gets messy image+message
    # dumps from CLIENTS. This phase cleans the dump up — groups multi-
    # image uploads under one property, deduces the client's intent from
    # email text, formats per National Land Code conventions — and
    # presents one property card at a time for the writer to approve so
    # they can paste a clean inventory into the wizard.
    completed = current_will_data.get('completed_steps') or []
    pending_gifts = current_will_data.get('pending_gifts') or {}
    has_any_assets = any(pending_gifts.get(k) for k in ('property', 'bank', 'vehicle'))
    if 'assets_confirmed' not in completed:
        if not has_any_assets:
            reply_parts.append(_assets_prompt_for_uploads())
            return _wrap(reply_parts, questions, patch, advice)
        # Interleave Layer 2: if any inventoried property still needs beneficiary
        # assignment, handle that BEFORE showing the next Layer 1 card so we
        # complete both layers per property before moving to the next property.
        # Gate: require known identities (scanned ICs) so we have names to offer
        # as beneficiary candidates — no longer gated on step4 (beneficiaries)
        # because those are only collected AFTER assets_confirmed.
        layer2_pending = current_will_data.get('layer2_pending_props') or []
        if layer2_pending:
            identities = current_will_data.get('identities') or []
            if identities:
                q = _step6_property_question(layer2_pending, recent_text, current_will_data)
                reply_parts.append(q['text'])
                focus = [q['focus_doc_id']] if q.get('focus_doc_id') else []
                return _wrap(reply_parts, questions, patch, advice, focus_attachments=focus)

        # Walk one un-reviewed property at a time. When all properties
        # are reviewed, walk banks, then vehicles. _asset_walkthrough_*
        # picks the FIRST item where extracted._inventoried is not True.
        wt = _asset_walkthrough_question(pending_gifts, recent_text, current_will_data)
        if wt is None:
            # Everything reviewed — auto-stamp assets_confirmed via the
            # app handler on next turn. For now just nudge the user.
            reply_parts.append(
                "✅ All assets reviewed. Reply **`confirm assets`** to lock "
                "in the inventory and move to executor + beneficiary "
                "assignment."
            )
            return _wrap(reply_parts, questions, patch, advice)
        reply_parts.append(wt['text'])
        focus = wt.get('focus_doc_ids') or []
        return _wrap(reply_parts, questions, patch, advice, focus_attachments=focus)

    # ── 4. STEP 3: Executor (main + substitute) ─────────────────────────
    # Walk through main first, then substitute. Only stop after both are
    # set OR user typed `skip` for substitute (handled by _try_save_executor).
    if n_executors < 2:
        q = _step3_executor_question(current_will_data, recent_text=recent_text)
        reply_parts.append(q['text'])
        focus = [q['focus_doc_id']] if q.get('focus_doc_id') else []
        return _wrap(reply_parts, questions, patch, advice, focus_attachments=focus)

    # ── 4a. STEP 4: Guardians (mandatory if minor children present) ─────
    # Skip automatically when no minors. User can also tap ⏭ Skip to
    # bypass even if minors were detected (e.g. all children are adults).
    if 'guardians_confirmed' not in completed:
        identities = current_will_data.get('identities') or []
        minors = _detect_minor_children(identities)
        if minors:
            # Must appoint a guardian — offer primary + substitute prompts
            s3 = current_will_data.get('step3') or {}
            guardians = s3.get('guardians') or []
            q = _step4_guardian_question(s3, minors, recent_text)
            reply_parts.append(q['text'])
            return _wrap(reply_parts, questions, patch, advice)
        else:
            # No minors — mark confirmed silently via completed marker.
            # (The app handler stamps 'guardians_confirmed' when it gets
            # 'guardian skip' or 'guardian none'; we generate it lazily
            # here so the planner doesn't keep asking.)
            pass  # fall through — handler marks it

    # ── 4.5 STEP 5: Confirm beneficiaries (Wizard Step 5 / DB step4) ────
    s4 = current_will_data.get('step4')
    n_benef = len(s4) if isinstance(s4, list) else 0
    if n_benef == 0:
        reply_parts.append(_step5_beneficiaries_question(current_will_data, recent_text))
        return _wrap(reply_parts, questions, patch, advice)

    # ── 5. STEP 6: Specific Gifts (properties, then banks generic) ──────
    pending_gifts = current_will_data.get('pending_gifts') or {}
    pending_props = pending_gifts.get('property') or []
    pending_banks = pending_gifts.get('bank') or []

    if pending_props:
        q = _step6_property_question(pending_props, recent_text, current_will_data)
        reply_parts.append(q['text'])
        focus = [q['focus_doc_id']] if q.get('focus_doc_id') else []
        return _wrap(reply_parts, questions, patch, advice, focus_attachments=focus)

    if pending_banks and not (current_will_data.get('step5') or []):
        # No bank gift saved yet — ask the generic-clause question.
        # If user wants per-account, they can name specific accounts in reply.
        q = _step6_bank_question(pending_banks, current_will_data)
        reply_parts.append(q['text'])
        return _wrap(reply_parts, questions, patch, advice)

    # ── 6. STEP 7: Residuary ───────────────────────────────────────────
    s6 = current_will_data.get('step6') or {}
    if not s6 or not (s6.get('beneficiaries') or s6.get('residuary_beneficiary_name')):
        s4_list = current_will_data.get('step4') or []
        reply_parts.append(_step7_residuary_question(s4_list))
        return _wrap(reply_parts, questions, patch, advice)

    # ── 7. STEP 8: Testamentary Trust (optional) ──────────────────────
    if 'trust_confirmed' not in completed:
        identities = current_will_data.get('identities') or []
        minors = _detect_minor_children(identities)
        s7 = current_will_data.get('step7') or {}
        q = _step8_trust_question(s7, minors, completed)
        if q:
            reply_parts.append(q)
            return _wrap(reply_parts, questions, patch, advice)

    # ── 8. STEP 9: Other Matters (optional) ───────────────────────────
    if 'others_confirmed' not in completed:
        s8 = current_will_data.get('step8') or {}
        reply_parts.append(_step9_others_question(s8))
        return _wrap(reply_parts, questions, patch, advice)

    # ── 9. STEP 10: Review & Generate ─────────────────────────────────
    reply_parts.append(
        "🎉 **All steps complete!** The will is ready to review.\n\n"
        "Head to **[Step 10: Review & Generate](/wizard/step/10)** to preview "
        "the full draft, make any final adjustments, and generate the PDF."
        + _qr_marker([
            {'label': '📄 Go to Review & Generate', 'value': 'open wizard step 10'},
            {'label': 'I need to change something', 'value': 'change something'},
        ])
    )
    return _wrap(reply_parts, questions, patch, advice)


# ── Helpers ────────────────────────────────────────────────────────────

def _wrap(parts, questions, patch, advice, focus_attachments=None):
    return {
        'reply': '\n\n'.join(p for p in parts if p).strip(),
        'clarifying_questions': questions,
        'proposed_patch': patch if patch else {},
        'advice': advice,
        'focus_attachments': focus_attachments or [],
    }


_EMAIL_DIVIDER_RE = re.compile(
    r'-{4,}\s*(Forwarded message|Original Message|Begin forwarded message)\s*-{4,}',
    re.IGNORECASE,
)
_EMAIL_HEADER_LINE_RE = re.compile(
    r'^[ \t]*(From|To|Cc|Bcc|Date|Subject|Sent|Mailed-By|Signed-By|Reply-To)\s*:[ \t]*[^\n]*$',
    re.MULTILINE | re.IGNORECASE,
)
_EMAIL_FOOTER_RE = re.compile(
    r'(Sent from (my )?(iPhone|iPad|Android|Samsung|Gmail|Outlook|Huawei|Xiaomi'
    r'|Galaxy|MacBook|Desktop|Mobile|iOS|Google Mail)[^\n]*'
    r'|Get Outlook for (iOS|Android)[^\n]*)',
    re.IGNORECASE,
)
_QUOTE_LINE_RE  = re.compile(r'^>.*$', re.MULTILINE)
_BLANK_LINES_RE = re.compile(r'\n{3,}')
_OUR_PREAMBLE_RE = re.compile(
    r'^_\(forwarded via email from [^\)]+\)_\s*\n+'
    r'(\*\*Subject:\*\*[^\n]*\n+)?'
    r'(\*\*Email date:\*\*[^\n]*\n+)?',
    re.MULTILINE,
)


def _clean_email_body(raw: str) -> str:
    """Strip email forwarding headers, quoted lines, and device footers.

    Strategy: keep ALL body text from every layer of the forward chain,
    strip only the structural noise (From/To/Date headers, divider lines,
    "Sent from my iPhone" footers, quoted "> " lines).

    Returns the human-written content — what the lawyer/testator actually
    typed — without forwarding metadata.
    """
    if not raw:
        return ''
    # 1. Strip the preamble we added ourselves
    cleaned = _OUR_PREAMBLE_RE.sub('', raw)
    # 2. Replace forwarding dividers with a simple blank line (keep body below)
    cleaned = _EMAIL_DIVIDER_RE.sub('\n', cleaned)
    # 3. Strip individual header lines (From:, Date:, Subject:, To: etc.)
    cleaned = _EMAIL_HEADER_LINE_RE.sub('', cleaned)
    # 4. Strip "Sent from my iPhone" / "Get Outlook for iOS" footers
    cleaned = _EMAIL_FOOTER_RE.sub('', cleaned)
    # 5. Strip "> quoted" reply lines
    cleaned = _QUOTE_LINE_RE.sub('', cleaned)
    # 6. Collapse excessive blank lines
    cleaned = _BLANK_LINES_RE.sub('\n\n', cleaned).strip()
    return cleaned


def _summarise_message(raw_text: str) -> str:
    """Use Claude Haiku to produce a two-part structured summary of a
    forwarded WhatsApp/email message in a will-writing context.

    Returns markdown with two sections:
      **What was communicated** — coherent paraphrase of the message
      **What we deduce** — interpreted will-writing intent (assets, beneficiaries, etc.)

    Returns empty string on failure (caller falls back to blockquote).
    """
    if not raw_text or len(raw_text.strip()) < 30:
        return ''
    try:
        import anthropic
        from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_CHEAP
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = (
            "You are a will-writing assistant at a Malaysian law firm. "
            "A client or planner has forwarded this WhatsApp/email message along with document attachments.\n\n"
            "Produce TWO sections. IMPORTANT: complete ALL assets/items — do not truncate mid-list.\n\n"
            "**Section 1 — What was communicated:**\n"
            "Write 2–4 sentences in plain English that faithfully summarise what the sender actually said. "
            "No interpretation — just what they wrote, clearly and naturally. "
            "Skip email headers, forwarding noise, and greetings.\n\n"
            "**Section 2 — What we deduce:**\n"
            "One block per property. For each property:\n"
            "  - Address: full street address (or 'unknown' if not mentioned)\n"
            "  - PTD/Lot: lot number e.g. PTD 207922 or Lot 127082 (or 'unknown')\n"
            "  - Title: Hakmilik / HS(D) / Geran number e.g. Hakmilik 504662 (or 'unknown')\n"
            "  - Mukim/Daerah: e.g. Mukim Plentong, Daerah Johor Bahru\n"
            "  - Ownership: sole / joint (with whom, share e.g. 1/2)\n"
            "  - Beneficiary: full name(s) and share\n"
            "  - ❓ Flag anything ambiguous\n"
            "Then list bank accounts, insurance, vehicles each as a separate bullet.\n\n"
            "IMPORTANT: always include the raw PTD/Lot number AND the Hakmilik/title number "
            "exactly as they appear in the message — these are used to match documents.\n\n"
            "Format exactly:\n"
            "**What was communicated:**\n"
            "<prose>\n\n"
            "**What we deduce:**\n"
            "• <item>\n"
            "• <item>\n\n"
            f"Message:\n{raw_text[:3000]}"
        )
        msg = client.messages.create(
            model=CLAUDE_MODEL_CHEAP,
            max_tokens=900,
            timeout=20.0,   # raised — complex messages with 5+ assets need more time
            messages=[{"role": "user", "content": prompt}]
        )
        try:
            from ai.cost_tracker import log_usage
            log_usage(msg, call_site='ai.chat_planner._summarise_message')
        except Exception:
            pass
        result = (msg.content[0].text or '').strip() if msg.content else ''
        return result
    except Exception:
        return ''


# ╔════════════════════════════════════════════════════════════════════════╗
# ║  🔥 BURN-IN — AI Summary is the canonical property list 🔥               ║
# ║                                                                        ║
# ║  CLAUDE.md §10h. Walkthrough count = AI Summary count. NEVER invent a  ║
# ║  property whose address isn't in the summary. This parser reads the    ║
# ║  latest "📨 AI Summary of your message" assistant message and returns  ║
# ║  the canonical list of {name, address, lot, title, mukim, daerah,      ║
# ║  beneficiary} per property. Downstream walkthrough code filters       ║
# ║  pending property cards against this list.                             ║
# ╚════════════════════════════════════════════════════════════════════════╝
def _extract_ai_summary_properties(client_id: str) -> List[Dict[str, Any]]:
    """Parse the most recent assistant '📨 AI Summary' chat message for
    this client and return the property list as the source of truth.

    Returns: list of dicts, each with keys:
      name, address, lot, title, mukim, daerah, ownership, beneficiary
    Empty values are '' (never None). Returns [] if no summary message
    exists or it has no property bullets.

    Per CLAUDE.md §10h, callers MUST use this list as the canonical N.
    The walkthrough renders exactly len(returned_list) property cards.
    """
    if not client_id:
        return []
    try:
        from database import db, ChatMessage, ChatSession
    except Exception:
        return []
    try:
        # Find the latest assistant message starting with the AI Summary header
        sess_ids_subq = (db.session.query(ChatSession.id)
                          .filter(ChatSession.client_id == client_id)
                          .subquery())
        msg = (ChatMessage.query
               .filter(ChatMessage.session_id.in_(sess_ids_subq))
               .filter(ChatMessage.role == 'assistant')
               .filter(ChatMessage.content.ilike('%📨 AI Summary of your message%'))
               .order_by(ChatMessage.created_at.desc())
               .first())
        if not msg or not msg.content:
            return []
        return _parse_ai_summary_text(msg.content)
    except Exception:
        return []


_AI_SUMMARY_FIELD_RE = {
    'address':     re.compile(r'(?:^|[\-\s])Address\s*[:\-]\s*(.+?)(?=\n|$)', re.IGNORECASE),
    'lot':         re.compile(r'(?:^|[\-\s])(?:PTD\s*/\s*Lot|Lot\s*/\s*PTD|Lot|PTD)\s*[:\-]\s*(.+?)(?=\n|$)', re.IGNORECASE),
    'title':       re.compile(r'(?:^|[\-\s])(?:Title|Hakmilik|HSD|HS\(D\)|Geran)\s*[:\-]\s*(.+?)(?=\n|$)', re.IGNORECASE),
    'mukim':       re.compile(r'(?:^|[\-\s])Mukim(?:\s*/\s*Daerah)?\s*[:\-]\s*(.+?)(?=\n|$)', re.IGNORECASE),
    'ownership':   re.compile(r'(?:^|[\-\s])Ownership\s*[:\-]\s*(.+?)(?=\n|$)', re.IGNORECASE),
    'beneficiary': re.compile(r'(?:^|[\-\s])Beneficiary\s*[:\-]\s*(.+?)(?=\n|$)', re.IGNORECASE),
}


def _parse_ai_summary_text(text: str) -> List[Dict[str, Any]]:
    """Pure-function parser. Splits the 'What we deduce' section into
    per-property blocks (each starts with •) and pulls fields out.
    Tolerant of formatting drift — missing fields just become ''.
    """
    if not text:
        return []
    # Isolate the deduce section if present (everything after that header)
    body = text
    m = re.search(r'\*\*\s*What we deduce[^\n]*\*\*', text, re.IGNORECASE)
    if m:
        body = text[m.end():]

    # Split on bullets: '•' (preferred) or '- ' at line start.
    # Each bullet block describes ONE asset (property/bank/vehicle).
    blocks = re.split(r'(?:\n\s*[•\-]\s+|\n\s*\*\s+)', '\n' + body)
    out: List[Dict[str, Any]] = []
    for blk in blocks:
        blk = (blk or '').strip()
        if not blk:
            continue
        # Skip non-property bullets — only keep ones that look property-ish
        addr_m = _AI_SUMMARY_FIELD_RE['address'].search(blk)
        lot_m  = _AI_SUMMARY_FIELD_RE['lot'].search(blk)
        title_m = _AI_SUMMARY_FIELD_RE['title'].search(blk)
        # Heuristic: if no address AND no lot AND no title → not a property
        if not (addr_m or lot_m or title_m):
            continue
        mukim_raw = ''
        if _AI_SUMMARY_FIELD_RE['mukim'].search(blk):
            mukim_raw = _AI_SUMMARY_FIELD_RE['mukim'].search(blk).group(1).strip()
        # Split mukim/daerah if combined
        mukim, daerah = mukim_raw, ''
        if ',' in mukim_raw:
            parts = [p.strip() for p in mukim_raw.split(',', 1)]
            mukim, daerah = parts[0], parts[1] if len(parts) > 1 else ''
        elif '/' in mukim_raw:
            parts = [p.strip() for p in mukim_raw.split('/', 1)]
            mukim, daerah = parts[0], parts[1] if len(parts) > 1 else ''
        # Strip leading "Mukim "/"Daerah " keywords
        mukim  = re.sub(r'^Mukim\s+', '', mukim, flags=re.IGNORECASE).strip()
        daerah = re.sub(r'^Daerah\s+', '', daerah, flags=re.IGNORECASE).strip()
        # First non-field line of the bullet is the property name (if present)
        first_line = blk.splitlines()[0].strip()
        # Drop name if it IS a field line ("Address: …")
        if any(rx.match(first_line) for rx in _AI_SUMMARY_FIELD_RE.values()):
            first_line = ''

        prop = {
            'name':        first_line[:120],
            'address':     (addr_m.group(1).strip() if addr_m else '')[:200],
            'lot':         (lot_m.group(1).strip() if lot_m else '')[:80],
            'title':       (title_m.group(1).strip() if title_m else '')[:80],
            'mukim':       mukim[:60],
            'daerah':      daerah[:60],
            'ownership':   (_AI_SUMMARY_FIELD_RE['ownership'].search(blk).group(1).strip()
                            if _AI_SUMMARY_FIELD_RE['ownership'].search(blk) else '')[:120],
            'beneficiary': (_AI_SUMMARY_FIELD_RE['beneficiary'].search(blk).group(1).strip()
                            if _AI_SUMMARY_FIELD_RE['beneficiary'].search(blk) else '')[:200],
        }
        # Drop "unknown" placeholders so caller sees empty string, not literal 'unknown'
        for k, v in list(prop.items()):
            if v.lower() == 'unknown':
                prop[k] = ''
        out.append(prop)
    return out


def _next_step_cta(will_data: dict) -> dict:
    """Return {'label': str, 'value': str} for the ▶️ next-step button.

    Mirrors the step-gate logic in plan_turn so the button label tells the
    user exactly what happens when they tap it.
    """
    if not will_data:
        return {'label': '▶️ Start — verify testator identity', 'value': 'inbox start'}

    s1         = will_data.get('step1') or {}
    s2         = will_data.get('step2') or {}
    s4         = will_data.get('step4') or []
    completed  = will_data.get('completed_steps') or []
    pg         = will_data.get('pending_gifts') or {}
    identities = will_data.get('identities') or []

    pending_ics = [i for i in identities
                   if i.get('kind') == 'nric' and not i.get('confirmed')]

    # Step 1: identity documents to match
    if pending_ics:
        return {'label': '▶️ Match identity documents', 'value': 'inbox start'}

    # Step 2: testator details not yet confirmed
    if s1.get('full_name') and not _is_confirmed(will_data, 'testator'):
        return {'label': '▶️ Confirm testator details', 'value': 'inbox start'}

    # Asset inventory not yet done
    has_assets = any(pg.get(k) for k in ('property', 'bank', 'vehicle'))
    if 'assets_confirmed' not in completed:
        if not has_assets:
            return {'label': '▶️ Start — describe your assets', 'value': 'inbox start'}
        return {'label': '▶️ Review asset documents', 'value': 'inbox start'}

    # Step 3: executors
    n_exec = len(s2.get('executors') or [])
    if n_exec < 2:
        return {'label': '▶️ Assign executors', 'value': 'inbox start'}

    # Step 5: beneficiaries
    if not s4:
        return {'label': '▶️ Assign beneficiaries', 'value': 'inbox start'}

    # Step 6: specific gifts — properties
    if pg.get('property'):
        return {'label': '▶️ Match specific gift documents', 'value': 'inbox start'}

    # Step 6: specific gifts — bank accounts
    if pg.get('bank') and not (will_data.get('step5') or []):
        return {'label': '▶️ Assign bank accounts', 'value': 'inbox start'}

    # Step 7+: residuary / review
    return {'label': '▶️ Continue to next step', 'value': 'inbox start'}


def _intake_email_card(artifacts: List[Dict[str, Any]], user_text: str,
                       current_will_data: dict = None) -> str:
    """Email brief card — shown when new attachments arrive or on reset.

    The exhibit thumbnails are rendered by the frontend from m.attachments —
    this card provides only:
    1. Header with count + instructions
    2. Coherent message summary (key points + cleaned text)
    3. Footer CTA — tap exhibits to view/remove, then Start matching
    """
    n = len(artifacts)

    # ── Minimal loading card ──────────────────────────────────────────────────
    # Thumbnails and the full AI summary are posted together in the follow-up
    # message by the background thread (_post_ai_summary in app.py).
    # This card is intentionally lightweight — it shows instantly so the user
    # knows the upload was received while the AI works in the background.
    has_wrong = any(a.get('extracted', {}).get('_wrong_upload_suspected') for a in artifacts)
    warn_note = " ⚠️ Some may need review." if has_wrong else ""
    has_text = bool(_clean_email_body(user_text or ''))

    cta = _next_step_cta(current_will_data or {})
    quick = [cta]
    qr = f'<!--quickreplies:{json.dumps(quick)}-->'

    lines = [
        f"## 📋 {n} exhibit{'s' if n != 1 else ''} received{warn_note}",
    ]
    if has_text:
        lines.append(
            f"_Analysing your message — summary will appear below in a moment. "
            f"Review exhibits then tap **{cta['label']}** when ready._"
        )
    else:
        lines.append(
            f"_No message text — only attachments received. "
            f"Tap **{cta['label']}** when ready._"
        )

    return '\n'.join(lines) + qr


def _intake_summary(artifacts: List[Dict[str, Any]]) -> str:
    """Aggregated by-kind summary for fresh artifacts.

    Property-type documents show per-file type + the classifier's `purpose`
    sentence so the writer immediately knows what was identified (e.g. "📜
    Property Title (Geran/Hakmilik) — Lot 207922 Mukim Plentong …").
    Other kinds are grouped and counted as before.
    """
    buckets: Dict[str, list] = {}
    for a in artifacts:
        buckets.setdefault(a.get('kind', 'other'), []).append(a)
    lines = [f"📥 Received **{len(artifacts)} attachment{'s' if len(artifacts)!=1 else ''}**:"]

    # ── ICs ─────────────────────────────────────────────────────────────────
    if buckets.get('nric'):
        ics = buckets['nric']
        names = [(a.get('extracted') or {}).get('full_name', '').strip() for a in ics]
        named = [n for n in names if n]
        line = f"📇 **{len(ics)} IC{'s' if len(ics) != 1 else ''}**"
        if named:
            line += " — read as: " + ", ".join(named[:6])
            if len(named) > 6:
                line += f", and {len(named)-6} more"
        lines.append(line)

    # ── Property documents — show each one individually ─────────────────────
    _PROP_KINDS = ('property_title', 'property_spa', 'property_tax',
                   'property_transfer', 'utility_bill', 'bank_letter')
    for kind in _PROP_KINDS:
        for a in buckets.get(kind, []):
            label = _KIND_LABELS.get(kind, '📄 Document')
            ex = a.get('extracted') or {}
            purpose = (ex.get('purpose') or '').strip()
            fname = (a.get('original_filename') or '').strip()
            detail = purpose or fname
            line = f"{label}"
            if detail:
                line += f" — _{detail[:120]}_"
            lines.append(line)

    # ── Other kinds — simple counts ──────────────────────────────────────────
    if buckets.get('bank_statement'):
        lines.append(f"🏦 **{len(buckets['bank_statement'])} bank statement{'s' if len(buckets['bank_statement'])!=1 else ''}**.")
    if buckets.get('insurance'):
        lines.append(f"🛡 **{len(buckets['insurance'])} insurance** doc{'s' if len(buckets['insurance'])!=1 else ''}.")
    if buckets.get('epf_kwsp'):
        lines.append(f"💼 **{len(buckets['epf_kwsp'])} EPF/KWSP**.")
    if buckets.get('vehicle'):
        lines.append(f"🚗 **{len(buckets['vehicle'])} vehicle** doc{'s' if len(buckets['vehicle'])!=1 else ''}.")
    if buckets.get('will'):
        lines.append(f"📄 **{len(buckets['will'])} existing will document{'s' if len(buckets['will'])!=1 else ''}**.")
    if buckets.get('voice'):
        lines.append(f"🎙 **{len(buckets['voice'])} voice note{'s' if len(buckets['voice'])!=1 else ''}** transcribed.")
    if buckets.get('other'):
        for a in buckets['other']:
            fname = (a.get('original_filename') or '').strip()
            ex = a.get('extracted') or {}
            purpose = (ex.get('purpose') or '').strip()
            detail = purpose or fname
            line = "❓ **Unclassified**"
            if detail:
                line += f" — _{detail[:120]}_"
            lines.append(line)
    return '\n'.join(lines)


def _identity_question(pending_ics: List[Dict[str, Any]], recent_text: str) -> str:
    """Ask about the next pending IC, with role pre-deduced from text if possible."""
    next_ic = pending_ics[0]
    ex = next_ic['extracted'] or {}
    name = (ex.get('full_name') or '').strip() or '(name unreadable)'
    nric = (ex.get('nric_number') or '').strip() or 'NRIC unreadable'

    # Try to deduce role from the recent text
    deduction = None
    if name and name != '(name unreadable)' and recent_text:
        try:
            from ai.role_deducer import deduce_roles
            deductions = deduce_roles(recent_text, [name])
            deduction = deductions.get(name)
        except Exception:
            deduction = None

    parts = [
        f"### 👤 Step 1: Identity ({len(pending_ics)} left)",
        f"**{name}** — {nric}",
    ]
    quick: List[Dict[str, str]] = []
    if deduction:
        parts.append(
            f"Looks like **{deduction['role']}** "
            f"(_{deduction['evidence']}_). Confirm?"
        )
        quick = [
            {'label': f"✓ Yes — {deduction['role']}", 'value': 'yes'},
            {'label': 'Skip', 'value': 'skip'},
            {'label': 'Delete', 'value': 'delete'},
        ]
    else:
        parts.append("**Relationship to testator?**\n_(Executor / Trustee / Guardian roles are set in later steps)_")
        quick = [
            {'label': 'Spouse', 'value': 'spouse'},
            {'label': 'Son', 'value': 'son'},
            {'label': 'Daughter', 'value': 'daughter'},
            {'label': 'Father', 'value': 'father'},
            {'label': 'Mother', 'value': 'mother'},
            {'label': 'Brother', 'value': 'brother'},
            {'label': 'Sister', 'value': 'sister'},
            {'label': 'Son-in-law', 'value': 'son in law'},
            {'label': 'Daughter-in-law', 'value': 'daughter in law'},
            {'label': 'Skip', 'value': 'skip'},
            {'label': 'Delete', 'value': 'delete'},
        ]
    return '\n\n'.join(parts) + _qr_marker(quick)


def _step2_question(s1: Dict[str, Any]) -> str:
    """Confirm testator details once identities are in."""
    body = (
        "### 👔 Step 2: Confirm Testator\n\n"
        f"- **Name:** {s1.get('full_name','?')}\n"
        f"- **NRIC:** {s1.get('nric_passport','?')}\n"
        f"- **DOB:** {s1.get('date_of_birth','?')}\n"
        f"- **Address:** {s1.get('residential_address','?')}\n\n"
        "**All correct?**"
    )
    quick = [
        {'label': '✓ Confirm', 'value': 'confirm'},
        {'label': 'Edit address', 'value': 'address: '},
    ]
    return body + _qr_marker(quick)


def _eligible_executor_candidates(identities):
    """Filter identities down to those legally eligible to be executor under
    the Wills Act 1959 (Malaysia):
      - Cannot be the testator (s.4: testator appoints OTHERS)
      - Must be 18+ on appointment (Probate & Administration Act 1959 s.3)
      - Must not be of unsound mind (no automated check; user judgment)
    """
    from datetime import date
    today = date.today()
    eligible = []
    for i in identities:
        rel = (i.get('relationship') or '').lower()
        if rel == 'testator':
            continue
        # Compute age if DOB known; if minor → exclude
        dob = i.get('date_of_birth') or ''
        try:
            if dob and len(dob) == 10:
                y, m, d = int(dob[:4]), int(dob[5:7]), int(dob[8:10])
                age = today.year - y - ((today.month, today.day) < (m, d))
                if age < 18:
                    continue
        except (ValueError, IndexError):
            pass  # unknown DOB → keep, user judges
        eligible.append(i)
    return eligible


def find_executor_candidate(identities, executors, role, recent_text=''):
    """Pick the best candidate for main / substitute executor.
    Returns {'person_id', 'name', 'evidence', 'document_id'} or None.
    Used by both _step3_executor_question (to suggest in prompt) and the
    chat-message route (to apply when user replies 'yes').

    Filters out testator + minors per Wills Act 1959 / Probate Act 1959.
    """
    identities = _eligible_executor_candidates(identities)
    # 1) Already marked Executor in identities
    already = next((i for i in identities
                    if 'executor' in (i.get('relationship') or '').lower()), None)
    if already and not _is_already_executor(already, executors):
        return {'person_id': already.get('id'),
                'name': already.get('full_name'),
                'evidence': 'marked Executor in identities',
                'document_id': already.get('document_id') or None}

    # 2) Email-text deduction (Claude — by name)
    if recent_text:
        try:
            from ai.role_deducer import deduce_roles
            names = [i['full_name'] for i in identities if i.get('full_name')]
            if names:
                ded = deduce_roles(recent_text, names)
                for n, info in ded.items():
                    if info.get('role') == 'Executor':
                        match = next((i for i in identities if i['full_name'] == n), None)
                        if match and not _is_already_executor(match, executors):
                            return {'person_id': match.get('id'),
                                    'name': n,
                                    'evidence': info.get('evidence', ''),
                                    'document_id': match.get('document_id') or None}
        except Exception:
            pass

    # 2.5) Heuristic — text mentions "executor: <relationship>" without
    # naming the person. Cross-reference: find an identity with that
    # relationship. e.g. "My Executor: my Sister in law" → identity tagged
    # Sister-in-law.
    if recent_text:
        import re as _re
        text_lower = recent_text.lower()
        for m in _re.finditer(r'executor', text_lower):
            window = text_lower[max(0, m.start()-100): m.end()+100]
            for i in identities:
                rel = (i.get('relationship') or '').lower().replace('-', ' ').strip()
                if not rel or rel in ('testator',):
                    continue
                if rel in window and not _is_already_executor(i, executors):
                    snippet_start = max(0, m.start()-30)
                    snippet_end = min(len(recent_text), m.end()+30)
                    return {'person_id': i.get('id'),
                            'name': i.get('full_name'),
                            'evidence': f"text mentions executor near '{rel}': "
                                        f"…{recent_text[snippet_start:snippet_end].strip()}…",
                            'document_id': i.get('document_id') or None}

    # 3) Substitute heuristic — adult spouse / child not already executor
    if role == 'substitute':
        for i in identities:
            rel = (i.get('relationship') or '').lower()
            if rel in ('spouse', 'wife', 'husband', 'son', 'daughter') and \
               not _is_already_executor(i, executors):
                return {'person_id': i.get('id'),
                        'name': i.get('full_name'),
                        'evidence': f"adult {rel} — common substitute choice",
                        'document_id': i.get('document_id') or None}
    return None


def _step3_executor_question(will_data: Dict[str, Any], recent_text: str = '') -> Dict[str, Any]:
    """Returns {text, focus_doc_id} — the question to ask + which IC photo
    to attach. Walks main → substitute executor based on what's already
    saved in step2_data.executors."""
    identities = will_data.get('identities') or []
    s2 = will_data.get('step2') or {}
    executors = s2.get('executors') or []
    n_done = len(executors)
    role = 'main' if n_done == 0 else 'substitute'

    # Compute minors from DOB (only if we have DOBs)
    from datetime import date
    today = date.today()
    minors = []
    for i in identities:
        dob = i.get('date_of_birth') or ''
        try:
            if dob and len(dob) == 10:
                y, m, d = int(dob[:4]), int(dob[5:7]), int(dob[8:10])
                age = today.year - y - ((today.month, today.day) < (m, d))
                if 0 < age < 18:
                    minors.append((i['full_name'], age))
        except (ValueError, IndexError):
            pass

    candidate = find_executor_candidate(identities, executors, role, recent_text)

    parts = [f"### ⚖️ Step 3: {'Main' if role=='main' else 'Substitute'} Executor"]

    if role == 'main':
        parts.append("**Who should be your main executor?**")
    else:
        m = executors[0] if executors else {}
        parts.append(
            f"✓ Main: **{m.get('full_name','?')}**\n\n"
            "**Pick a substitute (backup) executor:**"
        )

    # ── Surface text evidence from AI Summary / forwarded message ──────
    if candidate and candidate.get('evidence'):
        parts.append(
            f"📨 **Suggested:** **{candidate['name']}**\n"
            f"_from your message:_ \"{candidate['evidence'][:160]}\""
        )

    # Build button row — eligible identity names + Skip (substitute only)
    quick: List[Dict[str, str]] = []
    if candidate:
        quick.append({'label': f"✓ {candidate['name']} (suggested)", 'value': 'yes'})
    # Other eligible identities
    for i in identities:
        n = i.get('full_name', '').strip()
        if not n: continue
        if candidate and n == candidate['name']: continue
        if _is_already_executor(i, executors): continue
        quick.append({'label': n.title(), 'value': n})
        if len(quick) >= 5: break
    if role == 'substitute':
        quick.append({'label': 'Skip — no substitute', 'value': 'skip'})

    if minors and role == 'main':
        parts.append(
            f"⚠️ {len(minors)} minor(s): {', '.join(n for n,_ in minors)}. "
            "Joint executors recommended."
        )

    return {'text': '\n\n'.join(parts) + _qr_marker(quick),
            'focus_doc_id': candidate.get('document_id') if candidate else None}


def _step5_beneficiaries_question(will_data, recent_text: str = ''):
    """Confirm the universe of beneficiaries (people who'll inherit anything).
    Filters identities: drops testator + witnesses; auto-suggests spouse +
    children + anyone explicitly tagged Beneficiary.

    Also cross-references the AI Summary / forwarded WhatsApp text so each
    suggested beneficiary shows the SPECIFIC text snippet that names them
    (e.g. "Joshua Koid Teck Seng — 25% of Unit B-05-11, my message says…").
    """
    identities = will_data.get('identities') or []
    likely = []
    BENEFICIARY_RELS = {
        'spouse', 'wife', 'husband', 'son', 'daughter', 'father', 'mother',
        'brother', 'sister', 'grandson', 'granddaughter', 'beneficiary',
        'stepson', 'stepdaughter', 'adopted son', 'adopted daughter',
    }
    EXCLUDE_RELS = {'testator', 'witness'}
    for i in identities:
        rel = (i.get('relationship') or '').lower()
        if rel in EXCLUDE_RELS:
            continue
        if rel in BENEFICIARY_RELS or not rel:
            likely.append(i)
        # Sister-in-law etc. — only include if they're also marked executor/etc.
        elif 'in-law' in rel:
            likely.append(i)

    # ── Cross-reference text/AI summary for each candidate ──────────
    # Surface the specific snippet from the WhatsApp/email that names them
    # so the user sees WHY each person is suggested.
    text_evidence: Dict[str, str] = {}
    if recent_text and likely:
        try:
            from ai.role_deducer import deduce_roles
            names = [i['full_name'] for i in likely if i.get('full_name')]
            ded = deduce_roles(recent_text, names)
            for n, info in ded.items():
                ev = (info.get('evidence') or '').strip()
                if ev:
                    text_evidence[n] = ev[:120]
        except Exception:
            pass
        # Heuristic fallback: search recent_text for each name to grab a snippet
        import re as _re
        for i in likely:
            n = i.get('full_name') or ''
            if not n or n in text_evidence:
                continue
            # Find first occurrence and grab ~80 chars around it
            try:
                pat = _re.escape(n.split()[0])  # first word of name
                m = _re.search(pat, recent_text, _re.IGNORECASE)
                if m:
                    s = max(0, m.start() - 30)
                    e = min(len(recent_text), m.end() + 80)
                    snippet = recent_text[s:e].strip().replace('\n', ' ')
                    text_evidence[n] = '…' + snippet[:120] + '…'
            except Exception:
                pass

    parts = [
        "### 👨‍👩‍👧 Step 5: Beneficiaries",
        "_Suggested from your AI Summary / forwarded message:_",
    ]
    quick: List[Dict[str, str]] = []
    if likely:
        for i in likely:
            n = i['full_name']
            rel = i.get('relationship') or 'unknown'
            line = f"- **{n}** ({rel})"
            ev = text_evidence.get(n)
            if ev:
                line += f"\n  📨 _from message:_ \"{ev}\""
            parts.append(line)
        parts.append("**Confirm this list?**")
        quick = [{'label': '✓ Yes, all of these', 'value': 'yes'}]
        # Quick-remove buttons for each so user can drop in one tap
        for i in likely:
            n = i['full_name']
            quick.append({'label': f"❌ Remove {n.title()}", 'value': f'remove {n}'})
    else:
        parts.append("⚠️ No likely beneficiaries detected. Add identities first or list names manually.")
    return '\n\n'.join(parts) + _qr_marker(quick)


def _format_property_description(ex: Dict[str, Any]) -> str:
    """Render the property in Malaysian legal-doc style, matching the
    Alan & Tan PHEK YI TING template:

      <ADDRESS> held under <Title Type> <Title No>, Lot <Lot>, Mukim <Mukim>,
      District of <Daerah>, Negeri <Negeri>

    Mirrors models.gift.PropertyDetails.to_formatted_description so the
    chat preview matches the will text the drafter will produce.
    """
    addr = (ex.get('property_address') or ex.get('description') or '').strip()
    title_type = (ex.get('title_type') or '').strip()
    title_no = (ex.get('title_number') or '').strip()
    lot_no = (ex.get('lot_number') or '').strip()
    mukim = (ex.get('mukim') or ex.get('bandar_pekan') or '').strip()
    daerah = (ex.get('daerah') or '').strip()
    negeri = (ex.get('negeri') or '').strip()

    # Normalise prefixes (drop leading "MUKIM ", "DAERAH ", etc.)
    for prefix in ('MUKIM ', 'BANDAR ', 'DAERAH ', 'NEGERI ', 'STATE OF '):
        if mukim.upper().startswith(prefix): mukim = mukim[len(prefix):]
        if daerah.upper().startswith(prefix): daerah = daerah[len(prefix):]
        if negeri.upper().startswith(prefix): negeri = negeri[len(prefix):]

    if not addr and not title_no:
        return '(address & title both unreadable)'

    parts = []
    if addr:
        parts.append(f"**{addr}**")
    held = []
    if title_type and title_no:
        # Use 'Strata Title Geran' phrasing for strata-format title numbers
        is_strata = '/' in title_no
        prefix = 'Strata Title ' if is_strata else ''
        held.append(f"held under {prefix}{title_type} {title_no}")
    elif title_no:
        held.append(f"held under title {title_no}")
    if lot_no: held.append(f"Lot {lot_no}")
    if mukim:  held.append(f"Mukim {mukim}")
    if daerah: held.append(f"District of {daerah}")
    if negeri: held.append(f"Negeri {negeri}")
    if held:
        parts.append(', '.join(held))
    return '\n\n'.join(parts)


def _is_inventoried(item: Dict[str, Any]) -> bool:
    """A doc is 'inventoried' once the will writer has reviewed it and
    pressed confirm/skip in the walk-through. Marker lives in
    extracted_data._inventoried set by the app handler."""
    ex = item.get('extracted') or {}
    return bool(ex.get('_inventoried'))


# Human-readable labels for each document kind — used in intake summary and
# on property review cards so the writer immediately knows what was uploaded.
_KIND_LABELS: Dict[str, str] = {
    'nric':               '🪪 MyKad / Passport',
    'property_title':     '📜 Property Title (Geran / Hakmilik)',
    'property_spa':       '📝 Sale & Purchase Agreement (SPA)',
    'property_tax':       '🧾 Cukai Tanah / Cukai Pintu',
    'property_transfer':  '📋 Memorandum of Transfer (Borang 14A / 16A)',
    'utility_bill':       '⚡ Utility Bill',
    'bank_letter':        '🏦 Bank Letter',
    'bank_statement':     '🏦 Bank Statement',
    'insurance':          '🛡 Insurance Policy',
    'epf_kwsp':           '💼 EPF / KWSP Statement',
    'vehicle':            '🚗 Vehicle Document',
    'will':               '📄 Existing Will',
    'other':              '❓ Unclassified',
}


# Property fields that count as a "signal" — if every one of these is
# blank AND there are no support docs, the card is just noise. Showing
# it would force the writer to dismiss an empty husk over and over.
# Fields that actually identify a Malaysian property for probate purposes.
# Deliberately EXCLUDES 'description' and 'area' — the OCR often writes
# "Property Title document" in description even when the image is unreadable,
# which would make _is_truly_empty_property think there's something to show.
_PROPERTY_IDENTIFIER_KEYS = (
    'property_address', 'address',
    'title_number', 'lot_number',
    'mukim', 'daerah', 'negeri',
    'title_type',
)

# property_hint gets special treatment: non-empty but useless values like
# "Property Title" / "unknown" should NOT count as real signals.
_HINT_NOISE = frozenset({
    'property title', 'property document', 'title document',
    'land title', 'geran', 'hakmilik', 'hsd', 'hsm',
    'unknown', 'unreadable', 'unable to read', 'n/a', '',
})


def _is_truly_empty_property(p: Dict[str, Any]) -> bool:
    """True iff a property card has NO real probate-useful identifiers AND
    no grouped support docs.

    Key insight: OCR often writes "Property Title document" into the
    `description` field even for completely unreadable images. We only
    count fields that actually identify the property at the Pejabat Tanah
    (address, title no., lot no., mukim, daerah, negeri, title type).
    """
    ex = p.get('extracted') or {}
    # Check real identifier fields
    has_identifier = any((ex.get(k) or '').strip() for k in _PROPERTY_IDENTIFIER_KEYS)
    # Check property_hint — but filter out noise phrases
    hint = (ex.get('property_hint') or '').strip().lower()
    has_hint = bool(hint and hint not in _HINT_NOISE)
    has_support = bool(p.get('support_docs'))
    return not (has_identifier or has_hint or has_support)


def _autoskip_empty_properties(props: List[Dict[str, Any]], client_id: str = None):
    """Soft-mark every truly-empty property as `_inventoried` AND
    `_auto_skipped` so the walk skips them and they don't reappear next
    turn. Persists to DB so the next request sees the same state.

    Returns the list of properties that survived (i.e. have at least one
    identifying signal — worth showing the writer)."""
    survivors = []
    for p in props:
        if _is_truly_empty_property(p):
            try:
                from database import db, Document
                doc = db.session.get(Document, p.get('document_id'))
                if doc:
                    try:
                        ex = json.loads(doc.extracted_data) if doc.extracted_data else {}
                    except (json.JSONDecodeError, TypeError):
                        ex = {}
                    ex['_inventoried'] = True
                    ex['_auto_skipped'] = 'no identifying info — nothing to ask'
                    doc.extracted_data = json.dumps(ex)
                    doc.description = (doc.description or '') + ' (auto-skipped: blank)'
                    db.session.commit()
            except Exception:
                try:
                    from database import db as _db
                    _db.session.rollback()
                except Exception:
                    pass
        else:
            survivors.append(p)
    return survivors


def _enrich_property_from_siblings(p: Dict[str, Any]) -> Dict[str, Any]:
    """When a property card has SOME signal but missing fields (e.g. only
    a PTD number, no mukim/daerah), search every OTHER property/support
    doc for the same client for overlap on title/lot/PTD/HSD/HSM/address
    and back-fill the blanks.

    Returns a NEW extracted dict — does not mutate the original or write
    to DB. The walk-through card uses the enriched copy so the writer
    sees the full picture instead of the original sparse extraction."""
    ex = dict(p.get('extracted') or {})
    # Build needles from whatever this property DOES have
    needles = set()
    for k in ('title_number', 'lot_number', 'property_hint',
              'property_address', 'address'):
        v = (ex.get(k) or '').strip()
        if len(v) >= 3:
            needles.add(v.lower())
    # Also any 5+ digit number sequence in the title/lot — captures bare
    # PTD/HSD numbers that the OCR may have stuffed into property_hint
    # without classification.
    for k in ('title_number', 'lot_number', 'property_hint'):
        v = (ex.get(k) or '')
        for m in re.findall(r'\d{4,}', v):
            needles.add(m.lower())
    if not needles:
        return ex
    try:
        from database import db, Document
        client_id = None
        own_doc = db.session.get(Document, p.get('document_id'))
        if own_doc is not None:
            client_id = own_doc.client_id
        if not client_id:
            return ex
        # Pull every property-ish doc for this client.
        # Also include soft-deleted docs (category='deleted') — they were
        # de-duped but their extracted field values are still valid and may
        # contain address/mukim/daerah info absent from the surviving copy.
        sibs = (Document.query.filter(
                    Document.client_id == client_id,
                    Document.id != (p.get('document_id') or ''),
                    Document.category.in_([
                        'property_title', 'property_spa', 'property_tax',
                        'utility_bill', 'bank_letter', 'other', 'deleted',
                    ]),
                ).all())
        for sib in sibs:
            try:
                sex = json.loads(sib.extracted_data) if sib.extracted_data else {}
            except (json.JSONDecodeError, TypeError):
                sex = {}
            haystack_parts = []
            for k in ('title_number', 'lot_number', 'property_hint',
                      'property_address', 'address', 'description',
                      'mukim', 'daerah', 'negeri'):
                v = (sex.get(k) or '').strip()
                if v:
                    haystack_parts.append(v.lower())
            haystack = ' | '.join(haystack_parts)
            if not haystack:
                continue
            # Match if ANY needle appears in the sibling's haystack
            if not any(n in haystack for n in needles):
                continue
            # ╔════════════════════════════════════════════════════════════╗
            # ║  🔥 BURN-IN — STRATA: NO CROSS-TITLE ADDRESS INHERITANCE 🔥 ║
            # ║  CLAUDE.md §10hd. Two strata parcels in the same building   ║
            # ║  share the same lot but have DIFFERENT addresses (unit      ║
            # ║  numbers). Copying an address sibling-→-self by lot match   ║
            # ║  hides the destination's real unit. Block address copy     ║
            # ║  whenever either side is strata AND title sigs differ.     ║
            # ║  Non-address fields (title_number, mukim, etc.) are still  ║
            # ║  safe to copy — they don't carry the unit-level confusion. ║
            # ╚════════════════════════════════════════════════════════════╝
            try:
                from services.gift_walker import _safe_to_inherit_address
                _addr_inherit_ok = _safe_to_inherit_address(sex, ex)
            except Exception:
                _addr_inherit_ok = True  # service helper missing → permissive
            # Back-fill blank fields from the matching sibling.
            # For address fields: also overwrite NLC-style entries (e.g.
            # "LOT 207922, Mukim Plentong…") with a real street address
            # from a sibling — the OCR often stuffs NLC refs into
            # property_address which blocks real addresses from being set.
            for k in ('property_address', 'address', 'title_number',
                      'lot_number', 'mukim', 'daerah', 'negeri',
                      'title_type', 'area'):
                # Strata gate: address fields are blocked if title sigs differ.
                if k in ('property_address', 'address') and not _addr_inherit_ok:
                    continue
                current_val = (ex.get(k) or '').strip()
                sibling_val = (sex.get(k) or '').strip()
                if not sibling_val:
                    continue
                if current_val:
                    # Address fields: allow overwrite if current is NLC-style
                    if k in ('property_address', 'address'):
                        if not _NLC_ADDR_RE.match(current_val):
                            continue  # real street address — don't overwrite
                        # NLC-style — fall through to overwrite with sibling value
                    else:
                        continue  # non-address already has a value
                ex[k] = sex[k]
                ex.setdefault('_enriched_from', []).append(
                    f"{sib.original_filename or sib.id[:8]}.{k}")
    except Exception:
        pass
    return ex


# NLC-format address detector — matches strings that are land registry
# references rather than real street addresses. Used to decide whether a
# property_address field can be overwritten with a better value.
# Also exported so app.py can import it directly.
_NLC_ADDR_RE = re.compile(
    r'^\s*(?:LOT\s+\d|LOT\s+PTD|LOT\s+HSD|LOT\s+HSM|LOT\s+NO'
    r'|PTD\s+\d|H\.?S\.?\s*\(|HSD\s+\d|HSM\s+\d'
    r'|Geran\s+No|GERAN\b|Mukim\s+\w)',
    re.IGNORECASE,
)

_MY_ADDRESS_RE = re.compile(
    r'(?:'
    # "No. 22, Jalan Rimbun…" / "No 5A, Lorong Damai 3…" / "No 22 Jalan Rimbun…"
    # (comma optional — Malaysian informal writing often omits it)
    r'No\.?\s*\d+[A-Z\-]?\s*[,\s]\s*'
    r'(?:Jalan|Jln|Lorong|Persiaran|Lebuh|Lebuhraya|Lingkaran|'
    r'Taman|Bandar|Desa|Sri|Seri|Bukit|Pandan|Damansara|'
    r'Ampang|Petaling|Cheras|Kepong|Setapak|Pudu|Wangsa|'
    r'Shah Alam|Subang|Klang|Puchong|Putrajaya)[^\n]{3,80}'
    r'|'
    # "Jalan Rimbun 2, Seri Alam…"
    r'(?:Jalan|Jln|Lorong|Persiaran|Lebuh)\s+[A-Za-z][^\n,]{3,60}'
    r'|'
    # "Apartment / Unit / Blok X, Taman…"
    r'(?:Apartment|Apt|Unit|Blok|Block|Flat|Condominium|Condo)\s+'
    r'[A-Za-z0-9\-]+[^\n,]{5,60}'
    r')',
    re.IGNORECASE,
)

_MY_NEGERI_MAP = {
    'johor': 'JOHOR', 'johor bahru': 'JOHOR', 'jb': 'JOHOR',
    'selangor': 'SELANGOR', 'shah alam': 'SELANGOR',
    'kuala lumpur': 'KUALA LUMPUR', 'kl': 'KUALA LUMPUR',
    'penang': 'PENANG', 'pulau pinang': 'PENANG', 'georgetown': 'PENANG',
    'perak': 'PERAK', 'ipoh': 'PERAK',
    'kedah': 'KEDAH', 'alor setar': 'KEDAH',
    'kelantan': 'KELANTAN', 'kota bharu': 'KELANTAN',
    'terengganu': 'TERENGGANU', 'kuala terengganu': 'TERENGGANU',
    'pahang': 'PAHANG', 'kuantan': 'PAHANG',
    'negeri sembilan': 'NEGERI SEMBILAN', 'seremban': 'NEGERI SEMBILAN',
    'melaka': 'MELAKA', 'malacca': 'MELAKA',
    'perlis': 'PERLIS', 'kangar': 'PERLIS',
    'sabah': 'SABAH', 'kota kinabalu': 'SABAH',
    'sarawak': 'SARAWAK', 'kuching': 'SARAWAK',
    'putrajaya': 'PUTRAJAYA', 'labuan': 'LABUAN',
}

_MY_POSTCODE_STATE_RE = re.compile(
    r'\b(\d{5})\s+'
    r'(Johor|Selangor|Penang|Pulau Pinang|Perak|Kedah|Kelantan|'
    r'Terengganu|Pahang|Negeri Sembilan|Melaka|Perlis|Sabah|Sarawak|'
    r'Kuala Lumpur|KL|Putrajaya|Labuan)\b',
    re.IGNORECASE,
)


def _scan_text_for_property_fields(ex: Dict[str, Any], text: str,
                                     source_tag: str,
                                     force_full: bool = False) -> Dict[str, Any]:
    """Single-pass scan of `text` for missing property fields.

    Tries to fill: property_address, negeri, daerah, ownership_type,
    ownership_share, _beneficiary_hint.
    Tags filled fields as `_enriched_from: ['<source_tag>.fieldname']`.
    Returns the (possibly mutated) ex dict.
    """
    if not text:
        return ex

    # ── Ownership type (sole / joint) from message ────────────────────
    # Only fill if not already confirmed via the guided gate
    _manually = ex.get('_manually_edited') or []
    _ow_locked = any('ownership=' in m for m in (_manually if isinstance(_manually, list) else []))
    if not _ow_locked and not (ex.get('ownership_type') or '').strip():
        _txt_l = text.lower()
        # Patterns: "sole owner", "sendiri", "myself only", "saya sahaja"
        _sole_re = re.compile(
            r'\b(sole\s*owner|sendiri|myself\s*only|saya\s*sahaja|hak\s*penuh|full\s*ownership)\b',
            re.I)
        # Patterns: "joint owner", "bersama", "co-owner", "shared"
        _joint_re = re.compile(
            r'\b(joint|bersama|co[-\s]?owner|shared\s*ownership|berkongsi)\b', re.I)
        if _sole_re.search(text):
            ex['ownership_type'] = 'sole'
            ex.setdefault('_enriched_from', []).append(f'{source_tag}.ownership_type')
        elif _joint_re.search(text):
            ex['ownership_type'] = 'joint'
            ex.setdefault('_enriched_from', []).append(f'{source_tag}.ownership_type')
            # Try to extract share (e.g. "1/2", "50%", "half share")
            _share_m = re.search(r'(\d+/\d+|\d+\s*%|half\s*share|1\s*half)', text, re.I)
            if _share_m:
                raw = _share_m.group(1).strip()
                if re.search(r'half', raw, re.I):
                    raw = '1/2'
                ex['ownership_share'] = raw
                ex.setdefault('_enriched_from', []).append(f'{source_tag}.ownership_share')

    # ── Beneficiary hint from message ─────────────────────────────────
    if not (ex.get('_beneficiary_hint') or '').strip():
        # Two-step: keyword match (case-insensitive) then title-case name scan
        _kw_re = re.compile(
            r'(?:give\s+to|kepada|beneficiary[:\s]+|to\s+be\s+given\s+to|'
            r'pass\s+to|left\s+to|bequeath\s+to)\s+', re.I)
        _kw_m = _kw_re.search(text)
        if _kw_m:
            after = text[_kw_m.end():]
            # Collect leading title-case words (ignore lowercase relationship words)
            _name_parts = []
            _stop_words = {'my', 'the', 'all', 'each', 'whom', 'any', 'her',
                           'him', 'them', 'children', 'child', 'son', 'daughter',
                           'wife', 'husband', 'spouse', 'me', 'us', 'our', 'his',
                           'their', 'beneficiaries', 'heirs', 'estate', 'a', 'an',
                           'and', 'or', 'bin', 'binte', 'binti', 'bte', 'bt'}
            _rel_words = {'my', 'his', 'her', 'our', 'their', 'the',
                          'son', 'daughter', 'wife', 'husband', 'spouse',
                          'child', 'children', 'sibling', 'brother', 'sister',
                          'father', 'mother', 'parent', 'parents',
                          'eldest', 'youngest', 'second', 'third'}
            _in_name = False  # once we've started collecting name words, stop on lowercase
            for word in re.split(r'\s+', after.strip())[:8]:
                clean = re.sub(r'[^a-zA-Z\'/\\-]', '', word)
                if not clean:
                    break
                cl = clean.lower()
                if cl in ('bin', 'binte', 'binti', 'bte', 'bt', 'a/l', 'a/p'):
                    if _name_parts:  # linking word only valid mid-name
                        _name_parts.append(clean)
                    continue
                if cl in _stop_words or cl in _rel_words:
                    if _in_name:
                        break  # already started a name, stop here
                    continue  # skip relationship word before name starts
                if clean[0].isupper():
                    _name_parts.append(clean)
                    _in_name = True
                elif _in_name:
                    break  # lowercase word after name started → stop
            if _name_parts and len(' '.join(_name_parts)) >= 3:
                ex['_beneficiary_hint'] = ' '.join(_name_parts)[:100]
                ex.setdefault('_enriched_from', []).append(f'{source_tag}._beneficiary_hint')

    need_addr = not (ex.get('property_address') or '').strip()
    need_negeri = not (ex.get('negeri') or '').strip()
    need_daerah = not (ex.get('daerah') or '').strip()
    if not (need_addr or need_negeri or need_daerah):
        return ex

    # ── Build search windows ──────────────────────────────────────────
    # If we have identifier needles, search near them.
    # If not (completely blank image), search the WHOLE text.
    needles = set()
    for k in ('title_number', 'lot_number', 'mukim', 'property_hint'):
        v = (ex.get(k) or '').strip()
        if len(v) >= 3:
            needles.add(v.lower())
    for k in ('title_number', 'lot_number'):
        for m in re.findall(r'\d{4,}', ex.get(k) or ''):
            needles.add(m)

    text_l = text.lower()
    if force_full or not needles:
        # force_full=True  → text came WITH this specific image (message_context);
        #   always scan all of it — the lot number is on the geran, not in the email.
        # no needles → blank OCR; no choice but to scan everything.
        windows = [text]
    else:
        # Narrow windows — only scan near identifier needles in global text.
        windows = []
        for needle in needles:
            idx = text_l.find(needle.lower())
            if idx == -1:
                continue
            # 1500-char window: AI summary bullet blocks can span several lines
            # with address appearing well before/after the lot number.
            lo = max(0, idx - 1500)
            hi = min(len(text), idx + len(needle) + 1500)
            windows.append(text[lo:hi])
        if not windows:
            # Lot/title numbers not found — the AI summary may have omitted them.
            # Try a tighter pass: look for both mukim AND lot/title digits together
            # within a very small window so we don't cross-contaminate properties.
            lot_digits = set()
            for k in ('title_number', 'lot_number'):
                for m in re.findall(r'\d{4,}', ex.get(k) or ''):
                    lot_digits.add(m)
            mukim_val = (ex.get('mukim') or '').strip().lower()
            if lot_digits and mukim_val and len(mukim_val) >= 3:
                mukim_idx = text_l.find(mukim_val)
                if mukim_idx != -1:
                    # 300 chars around mukim mention — tight enough to avoid
                    # cross-contaminating multiple properties in same mukim.
                    lo = max(0, mukim_idx - 300)
                    hi = min(len(text), mukim_idx + 300)
                    windows.append(text[lo:hi])
            if not windows:
                return ex  # no anchors found in this text at all

    for window in windows:
        win_l = window.lower()

        if need_addr:
            m = _MY_ADDRESS_RE.search(window)
            if m:
                candidate = m.group(0).strip()
                # Stop at sentence boundaries (. ! ?) and action phrases
                candidate = re.split(r'[.!?]|\bPlease\b|\bKindly\b|\bAttached\b',
                                     candidate, maxsplit=1)[0]
                candidate = candidate.strip().rstrip(',').rstrip('.')
                if len(candidate) >= 10:
                    ex['property_address'] = candidate[:200]
                    ex.setdefault('_enriched_from', []).append(
                        f'{source_tag}.property_address')
                    need_addr = False

        if need_negeri:
            pm = _MY_POSTCODE_STATE_RE.search(window)
            if pm:
                negeri_raw = pm.group(2).strip()
                ex['negeri'] = _MY_NEGERI_MAP.get(negeri_raw.lower(),
                                                   negeri_raw.upper())
                ex.setdefault('_enriched_from', []).append(f'{source_tag}.negeri')
                need_negeri = False

        if need_negeri:
            for kw, norm in _MY_NEGERI_MAP.items():
                if re.search(r'\b' + re.escape(kw) + r'\b', win_l):
                    ex['negeri'] = norm
                    ex.setdefault('_enriched_from', []).append(f'{source_tag}.negeri')
                    need_negeri = False
                    break

        if need_daerah:
            _DAERAH_HINTS = {
                'johor bahru': 'Johor Bahru', 'jb': 'Johor Bahru',
                'kluang': 'Kluang', 'batu pahat': 'Batu Pahat',
                'muar': 'Muar', 'segamat': 'Segamat', 'mersing': 'Mersing',
                'kota tinggi': 'Kota Tinggi', 'pontian': 'Pontian',
                'kulai': 'Kulai', 'iskandar puteri': 'Johor Bahru',
                'klang': 'Klang', 'petaling': 'Petaling Jaya',
                'sepang': 'Sepang', 'hulu langat': 'Hulu Langat',
                'subang': 'Petaling', 'puchong': 'Petaling',
                'ipoh': 'Kinta', 'taiping': 'Larut', 'teluk intan': 'Hilir Perak',
            }
            for kw, daerah_name in _DAERAH_HINTS.items():
                if re.search(r'\b' + re.escape(kw) + r'\b', win_l):
                    ex['daerah'] = daerah_name
                    ex.setdefault('_enriched_from', []).append(f'{source_tag}.daerah')
                    need_daerah = False
                    break

        if not (need_addr or need_negeri or need_daerah):
            break

    # ── Reverse-address lookup (last resort for needle-less match) ────────
    # If address is still missing after all needle-window passes, scan the
    # FULL text for every street address, then check each address's surrounding
    # context (±600 chars) for the property's known identifiers:
    #   mukim, daerah, negeri, title_number digits, lot_number digits.
    # If exactly ONE address's context matches, assign it.
    # This handles the case where the AI summary lists addresses and property
    # details in separate lines/bullets without the lot number inline.
    if need_addr:
        _prop_clues = set()
        for k in ('mukim', 'daerah'):
            v = (ex.get(k) or '').strip().lower()
            if v and len(v) >= 4:
                _prop_clues.add(v)
        for k in ('title_number', 'lot_number'):
            for _d in re.findall(r'\d{4,}', ex.get(k) or ''):
                _prop_clues.add(_d.lower())
        negeri_val = (ex.get('negeri') or '').strip().lower()

        if _prop_clues:
            _all_addr_matches = list(_MY_ADDRESS_RE.finditer(text))
            _matched_addr = None
            _match_count = 0
            for _am in _all_addr_matches:
                _ctx_lo = max(0, _am.start() - 600)
                _ctx_hi = min(len(text), _am.end() + 600)
                _ctx = text[_ctx_lo:_ctx_hi].lower()
                # Count how many clues appear in this address's context
                _hits = sum(1 for c in _prop_clues if c in _ctx)
                # Also count negeri as an extra signal (but not required)
                if negeri_val and negeri_val in _ctx:
                    _hits += 1
                # Require at least 2 distinct clues to avoid false positives
                if _hits >= 2:
                    _match_count += 1
                    _matched_addr = _am.group(0).strip()
            if _match_count == 1 and _matched_addr:
                # Single unambiguous match — clean and assign
                _matched_addr = re.split(
                    r'[.!?]|\bPlease\b|\bKindly\b|\bAttached\b',
                    _matched_addr, maxsplit=1
                )[0].strip().rstrip(',').rstrip('.')
                if len(_matched_addr) >= 10:
                    ex['property_address'] = _matched_addr[:200]
                    ex.setdefault('_enriched_from', []).append(
                        f'{source_tag}.property_address_reverse')
                    need_addr = False

    return ex


def ai_match_property_addresses(
    props: list,          # list of {extracted: {lot_number, title_number, mukim, daerah, negeri, ...}}
    raw_text: str,        # WhatsApp/email body that lists the addresses
    already_claimed: set  # addresses already matched to other properties — exclude these
) -> dict:
    """Use Claude Haiku to match each property's NLC identifiers to a street
    address mentioned in the client's forwarded message.

    The WhatsApp text lists addresses WITHOUT lot numbers; the geran documents
    have lot numbers WITHOUT addresses. This call bridges the gap by asking
    the AI to make the association using any available context clues
    (mukim, daerah, ownership share, order in list, etc.).

    Returns: {document_id: matched_address_string}  (only for confident matches)
    """
    if not props or not raw_text or len(raw_text.strip()) < 20:
        return {}

    # Build a compact property table for the prompt
    prop_lines = []
    for i, p in enumerate(props, 1):
        ex = p.get('extracted') or {}
        parts = []
        if ex.get('lot_number'):
            parts.append(f"Lot/PTD {ex['lot_number']}")
        if ex.get('title_number'):
            parts.append(f"Hakmilik/Title {ex['title_number']}")
        if ex.get('mukim'):
            parts.append(f"Mukim {ex['mukim']}")
        if ex.get('daerah'):
            parts.append(f"Daerah {ex['daerah']}")
        if ex.get('negeri'):
            parts.append(f"Negeri {ex['negeri']}")
        if ex.get('ownership_type'):
            share = ex.get('ownership_share', '')
            parts.append(f"{'Joint ' + share if share else 'Joint'}" if ex['ownership_type'] == 'joint' else 'Sole')
        prop_lines.append(f"  Property {i} (doc_id={p.get('document_id','')}): {', '.join(parts) or 'no identifiers'}")

    # Exclude already-claimed addresses from the candidate pool
    exclusion_note = ''
    if already_claimed:
        excl = [a for a in already_claimed if a and len(a) > 5]
        if excl:
            exclusion_note = (
                f"\n\nIMPORTANT: these addresses are already matched to other properties — "
                f"do NOT use them: {'; '.join(excl[:10])}"
            )

    prompt = (
        "You are a Malaysian will-writing assistant. "
        "Below are scanned property documents with their NLC (National Land Code) identifiers "
        "extracted by OCR, and a forwarded client message listing property addresses.\n\n"
        "Task: for each property document, find the best matching street address from the client's message. "
        "Use any available clues: location (mukim/daerah maps to a neighbourhood), ownership type, "
        "order of mention, or any other contextual hint.\n\n"
        "Confidence levels:\n"
        "  'high'   — strong direct match (lot/title number explicitly mentioned, or single address in text)\n"
        "  'medium' — reasonable inference from mukim/daerah/area or order of mention\n"
        "  'low'    — weak guess, multiple possibilities or very little context\n"
        "  'no_match' — cannot determine\n\n"
        "Properties (from scanned documents):\n"
        + '\n'.join(prop_lines)
        + exclusion_note
        + "\n\nClient's message:\n"
        + raw_text[:3000]
        + "\n\nOutput ONLY a JSON object. Each key is a doc_id. "
        "Each value is either 'no_match' OR an object {\"address\": \"...\", \"confidence\": \"high|medium|low\", "
        "\"reason\": \"one sentence why\"}. "
        "Example: {\"abc123\": {\"address\": \"No. 18, Jalan Rimbun, 81300 Skudai, Johor\", "
        "\"confidence\": \"high\", \"reason\": \"only one address in text\"}, "
        "\"def456\": \"no_match\"}"
    )

    try:
        import anthropic
        from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_CHEAP
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=CLAUDE_MODEL_CHEAP,
            max_tokens=800,
            timeout=15.0,
            messages=[{"role": "user", "content": prompt}]
        )
        try:
            from ai.cost_tracker import log_usage
            log_usage(msg, call_site='ai.chat_planner.ai_match_property_addresses')
        except Exception:
            pass
        raw = (msg.content[0].text or '').strip() if msg.content else ''
        # Extract JSON from response (may be wrapped in ```json ... ```)
        import re as _re
        json_m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if not json_m:
            return {}
        import json as _json
        result = _json.loads(json_m.group(0))
        # Filter out no_match entries and normalise to {address, confidence, reason} dicts
        out = {}
        for k, v in result.items():
            if not v:
                continue
            if isinstance(v, str):
                # Legacy plain-string format — treat as high confidence
                if v.lower() in ('no_match', 'no match', 'none', 'unknown', ''):
                    continue
                if len(v) >= 8:
                    out[k] = {'address': v, 'confidence': 'high', 'reason': ''}
            elif isinstance(v, dict):
                addr = (v.get('address') or '').strip()
                conf = (v.get('confidence') or 'high').lower()
                reason = (v.get('reason') or '').strip()
                if addr and len(addr) >= 8 and addr.lower() not in ('no_match', 'no match'):
                    out[k] = {'address': addr, 'confidence': conf, 'reason': reason}
        return out
    except Exception:
        return {}


# ╔════════════════════════════════════════════════════════════════════════╗
# ║  🔥 BURN-IN — WEB-SEARCH-VALIDATE EVERY ADDRESS MATCH 🔥                 ║
# ║                                                                        ║
# ║  Per CLAUDE.md §10hf: when we have a candidate (doc, address) pair,    ║
# ║  we MUST web-search the address before committing the match. The      ║
# ║  search returns property-type clues (type / tenure / mukim / building)║
# ║  that we use to VALIDATE the doc is compatible with the address.      ║
# ║                                                                        ║
# ║  If the web clues say "apartment_condo at Bandar Medini" and the doc  ║
# ║  is a clearly-landed Geran in mukim Plentong → INCOMPATIBLE. The      ║
# ║  match is downgraded to 'low' confidence and flagged for user review. ║
# ║                                                                        ║
# ║  This is the ONE LINE between auto-binding and hallucination. Don't   ║
# ║  remove it. Don't bypass it for "speed". The user said:               ║
# ║      "Web search gives PROPERTY-TYPE CLUES that filter the image      ║
# ║       search. BURN THIS BLOODY THING IN THE CODE"                     ║
# ╚════════════════════════════════════════════════════════════════════════╝
def validate_matches_with_web_clues(
    props: list,
    matches: dict,
) -> dict:
    """Run each (doc, matched_address) through web-search clues validation.

    For every match returned by `ai_match_property_addresses`:
      1. Web-search the matched address → PropertyClues (type / tenure / mukim).
      2. Check is_compatible(doc.extracted, clues).
      3. If incompatible → downgrade confidence to 'low' and set
         `_address_needs_confirm=True`, attach `_clue_reject_reason`.
      4. If compatible AND clues found → bump 'medium' to 'high', attach
         `_clue_sources` so the card can show the citations.
      5. If web search returned None (address not found, no sources) →
         leave the match alone (we don't penalise un-searchable addresses).

    Returns: a new dict with the same shape as `matches` but with
    confidence/flags adjusted by web-clue evidence.
    """
    if not props or not matches:
        return matches or {}

    try:
        from services.web_property_clues import (
            search_property_clues, is_compatible, PropertyClues,
        )
    except Exception:
        return matches  # web_property_clues unavailable → pass through

    # ── Geo resolver (CLAUDE.md §10hc — NEVER from memory) ─────────────────
    # Hint 1 of the two-hint test: same mukim. Wire resolve_mukim() in as
    # the FIRST authoritative source (title doc → address doc → AI Summary
    # → curated cache → web search). It raises GeoUnknown rather than
    # guessing — the caller (here) catches and falls through to web clues.
    try:
        from services.geo_resolver import (
            resolve_mukim, make_web_resolver, GeoUnknown,
        )
        _geo_available = True
    except Exception:
        _geo_available = False
        GeoUnknown = Exception  # type: ignore  # placeholder for except clause

    try:
        import anthropic
        from config import ANTHROPIC_API_KEY
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except Exception:
        return matches  # no API client → pass through

    # Build a web resolver bound to this client. Cached per-call so a
    # single resolve_mukim retry pass doesn't spawn a new resolver per doc.
    _web_resolver_fn = None
    if _geo_available:
        try:
            _web_resolver_fn = make_web_resolver(client)
        except Exception:
            _web_resolver_fn = None

    # Cache: avoid duplicate web searches when two docs match the same address
    clue_cache: Dict[str, Optional[Any]] = {}
    geo_cache: Dict[str, Optional[Any]] = {}

    out: Dict[str, Any] = {}
    docs_by_id = {p.get('document_id'): p for p in props if p.get('document_id')}

    for doc_id, match_val in matches.items():
        if not isinstance(match_val, dict):
            out[doc_id] = match_val
            continue

        addr = (match_val.get('address') or '').strip()
        if not addr:
            out[doc_id] = match_val
            continue

        cache_key = addr.lower()
        if cache_key not in clue_cache:
            try:
                clue_cache[cache_key] = search_property_clues(addr, client)
            except Exception:
                clue_cache[cache_key] = None
        clues = clue_cache[cache_key]

        new_match = dict(match_val)

        # ── HINT 1: same-mukim check via geo resolver (no memory) ──────────
        # Per CLAUDE.md §10hc, mukim claims must come from a citable
        # source. Try title-doc → address-doc → AI-Summary → curated
        # cache → web search, in that order. GeoUnknown means none
        # resolved — we leave the hint blank rather than guessing.
        doc = docs_by_id.get(doc_id)
        ex = (doc.get('extracted') if doc else None) or {}
        if _geo_available and cache_key not in geo_cache:
            try:
                geo = resolve_mukim(
                    addr,
                    title_doc_mukim=(ex.get('mukim') or '').strip() or None,
                    title_doc_id=(doc.get('document_id') if doc else None),
                    client_id=(doc.get('client_id') if doc else None),
                    web_search_fn=_web_resolver_fn,
                )
                geo_cache[cache_key] = geo
            except GeoUnknown:
                geo_cache[cache_key] = None
            except Exception:
                geo_cache[cache_key] = None
        geo = geo_cache.get(cache_key)
        if geo is not None:
            new_match['_resolved_mukim'] = geo.mukim
            new_match['_mukim_source'] = geo.source
            # Hint 1 verdict: does the doc's mukim match the resolved one?
            doc_mukim = (ex.get('mukim') or '').strip().lower()
            res_mukim = (geo.mukim or '').strip().lower()
            if doc_mukim and res_mukim:
                new_match['_hint1_mukim_ok'] = (doc_mukim == res_mukim)
            else:
                new_match['_hint1_mukim_ok'] = None  # unknown / one side missing

        if clues is None:
            # No web evidence available — leave match unchanged but mark
            # that the validation step ran without a verdict.
            new_match.setdefault('_clue_status', 'address_not_found')
            out[doc_id] = new_match
            continue

        # `doc` and `ex` were resolved above (Hint 1 block). Re-use here.
        ok, reason = is_compatible(ex, clues)

        if not ok:
            # Incompatible → downgrade. Do NOT silently drop the match —
            # let the user see what the web said vs what the doc says.
            new_match['confidence'] = 'low'
            new_match['_clue_status'] = 'incompatible'
            new_match['_clue_reject_reason'] = reason
            new_match['_clue_type'] = clues.type
            new_match['_clue_mukim'] = clues.mukim
            new_match['_clue_sources'] = list(clues.sources[:3])
        else:
            # Compatible. Promote medium→high if we got real clues.
            cur = (new_match.get('confidence') or 'high').lower()
            if cur == 'medium' and clues.sources:
                new_match['confidence'] = 'high'
            new_match['_clue_status'] = 'compatible'
            new_match['_clue_type'] = clues.type
            new_match['_clue_mukim'] = clues.mukim
            new_match['_clue_sources'] = list(clues.sources[:3])

        out[doc_id] = new_match

    return out


def _enrich_from_chat_text(ex: Dict[str, Any], recent_text: str) -> Dict[str, Any]:
    """Back-fill missing address/negeri/daerah from the client's chat messages.

    Two-pass strategy, highest-priority first:

    Pass 1 — Message context (text sent WITH this specific image)
      Stored at upload time as `_message_context` in extracted_data.
      For WhatsApp: the text the client typed in the same or immediately
        preceding message as the image.
      For email: the full email body (which describes ALL attachments).
      This is the most reliable source — closest to the image in time/intent.

    Pass 2 — Global recent text (all recent chat messages)
      Broader search using needle-based windows. Catches cases where the
      client mentioned the address in a separate earlier message.

    For images with NO OCR identifiers at all (completely blank), Pass 1
    searches the ENTIRE message context (no needle filter) — any address
    pattern in the accompanying text is a valid candidate.
    """
    # Pass 1: message context (text sent WITH this specific image) — force_full
    # because the lot number is on the geran image, not in the email body.
    msg_ctx = (ex.get('_message_context') or '').strip()
    if msg_ctx:
        ex = _scan_text_for_property_fields(ex, msg_ctx, 'message_context',
                                             force_full=True)

    # Pass 2: global recent text — needle-based windows only (avoid false matches
    # from unrelated properties in the same conversation).
    if recent_text:
        ex = _scan_text_for_property_fields(ex, recent_text, 'chat_text',
                                             force_full=False)

    return ex


def _deduce_intent_from_messages(p: Dict[str, Any], recent_text: str) -> str:
    """Pull any messages from the client that mention this specific
    property by lot/title number/address. The will writer needs to see
    'what did the client actually say about this one?' next to the
    auto-grouped doc evidence.

    Strategy:
    1. Needle-search `recent_text` (all chat history) for lot/title mentions.
    2. If no needles (property has no identifiers yet) but `_message_context`
       is set, show that directly — it IS the text sent with the image.
    """
    import re as _re
    ex = p.get('extracted') or {}
    needles = []
    for k in ('title_number', 'lot_number', 'property_address',
              'description', 'mukim', 'property_hint'):
        v = (ex.get(k) or '').strip()
        if v and len(v) >= 3:
            needles.append(v)

    # Also check `_message_context` if it has been set — this is the
    # WhatsApp text immediately before this image, stored per-image.
    msg_ctx = (ex.get('_message_context') or '').strip()

    if needles and recent_text:
        text_l = recent_text.lower()
        matches = []
        for needle in needles:
            n = needle.lower()
            idx = text_l.find(n)
            if idx == -1:
                continue
            lo = max(0, idx - 120)
            hi = min(len(recent_text), idx + len(needle) + 120)
            snippet = recent_text[lo:hi].strip()
            snippet = _re.sub(r'\s+', ' ', snippet)
            if len(snippet) > 200:
                snippet = '…' + snippet[-200:]
            matches.append(snippet)
            if len(matches) >= 2:
                break
        if matches:
            return '\n'.join(f"  > _{m}_" for m in matches)

    # Fallback: show the raw message_context (WhatsApp text sent WITH image)
    if msg_ctx:
        # Strip leading WhatsApp timestamp lines to keep it concise
        lines = [l.strip() for l in msg_ctx.splitlines() if l.strip()]
        # Drop pure timestamp/attachment reference lines
        clean = [l for l in lines
                 if '<attached:' not in l.lower()
                 and 'file attached' not in l.lower()]
        if clean:
            snippet = ' '.join(clean)[:300]
            return f"  > _{snippet}_"
    return ''


_TITLE_TYPE_KEYWORDS = (
    'GERAN', 'HAKMILIK', 'HS(D)', 'HSD', 'HS(M)', 'HSM',
    'PAJAKAN NEGERI', 'PN', 'PAJAKAN MUKIM', 'PM',
    'GERAN MUKIM', 'GM', 'STRATA', 'PT', 'PTD',
)


def _validate_property_format(ex: Dict[str, Any],
                               doc_kind: str = 'property_title') -> List[str]:
    """Surface obvious formatting / completeness issues per the National
    Land Code. Cheap heuristic checks — not a substitute for legal
    review. The validator is intentionally LENIENT on the title number
    itself — under NLC titles have two distinct parts:

      • Title TYPE (the prefix word): Geran / Hakmilik / HS(D) / HS(M) /
        PN / PM / GM / Strata Title — lives in `title_type`.
      • Title NUMBER: usually pure digits (e.g. "564662" for a Geran),
        or with slashes for strata ("12345/67/8/9") — lives in
        `title_number`.

    A pure-digit title number is perfectly valid for a Geran/Hakmilik.
    The earlier rule that looked for "GERAN" / "HSD" inside the NUMBER
    field was wrong and cried wolf on every legitimate title.
    """
    warnings = []
    title_no = (ex.get('title_number') or '').strip()
    title_type = (ex.get('title_type') or '').strip()
    lot_no = (ex.get('lot_number') or '').strip()
    mukim = (ex.get('mukim') or '').strip()
    daerah = (ex.get('daerah') or '').strip()
    negeri = (ex.get('negeri') or '').strip()
    addr = (ex.get('property_address') or ex.get('description') or '').strip()

    # ── TRANSFER-FORM WARNING ─────────────────────────────────────────
    # Borang 14A / 16A = Memorandum of Transfer. This IS evidence that the
    # testator acquired a property, BUT it is NOT the registered title.
    # Important nuance for the will-writer:
    #   • The address in PENERIMA PINDAHMILIK section = TRANSFEREE's residential
    #     address (where they live) — NOT the address of the property being transferred.
    #   • The actual property is identified by the LOT NUMBER + TITLE NUMBER
    #     listed in the "PERIHAL TANAH" section of the form.
    #   • Ownership confirmation requires the Geran/Hakmilik (registered title)
    #     from the Land Registry. Until registered, the transfer is equitable only.
    if doc_kind == 'property_transfer':
        if addr and not lot_no and not title_no:
            addr_short = addr[:60] + ('…' if len(addr) > 60 else '')
            warnings.append(
                "📋 **Memorandum of Transfer (Borang 14A / 16A) detected.**\n"
                f"     ⚠️ The address shown (**{addr_short}**) is likely the "
                "**transferee's residential address**, not the property being "
                "transferred.\n"
                "     • To identify the actual property, look for the **Lot number** "
                "and **Title number** in the PERIHAL TANAH section of the form.\n"
                "     • This form alone is NOT sufficient for probate — the registered "
                "**Geran / Hakmilik** (from the Land Registry) is required for the "
                "Deed of Transmission."
            )
        else:
            warnings.append(
                "📋 **Memorandum of Transfer (Borang 14A / 16A)** — confirms a "
                "property was transferred to the testator, but this is not the "
                "registered title. **Add the Geran / Hakmilik** if available for a "
                "complete probate trail."
            )

    # ── PROBATE-CRITICAL FIELD CHECK ─────────────────────────────────
    # The lawyer needs these to file Borang 14A / Deed of Transmission /
    # Pindahmilik at the Land Office. An address alone isn't enough — the
    # estate cannot be transferred to the beneficiary without the Geran
    # / PTD / HSD / HSM / Hakmilik title number AND the lot number. Flag
    # missing fields in one sharp ALERT so the writer goes back to the
    # client for a clearer Geran scan instead of pushing a half-formed
    # gift through.
    missing_critical = []
    if not title_no:
        missing_critical.append('**title number** (Geran / PTD / HSD / HSM / Hakmilik)')
    if not lot_no:
        missing_critical.append('**lot number** (Lot / PT)')
    if not mukim:
        missing_critical.append('**Mukim**')
    if not daerah:
        missing_critical.append('**Daerah**')
    if not negeri:
        missing_critical.append('**Negeri**')
    if missing_critical:
        if addr:
            # We have something to identify the property, but probate
            # cannot proceed. Prompt the writer to chase the client for
            # the Geran scan.
            warnings.append(
                "🚨  **Cannot probate without these — ask the client to "
                "provide a clearer Geran/Hakmilik scan:**\n     "
                + ', '.join(missing_critical)
                + "\n     _Reason: the lawyer needs these fields on Borang 14A "
                "/ Deed of Transmission to transfer this property to the "
                "beneficiary at the Pejabat Tanah._"
            )
        else:
            # No address either → re-OCR or get a different doc.
            warnings.append(
                "⚠️  Address AND title number both blank — re-OCR or ask "
                "client for a clearer scan."
            )

    # If we have a title NUMBER, check that EITHER title_type OR the
    # number itself indicates a recognised NLC instrument. Pure digits
    # alone are fine when title_type is set (typical Geran).
    if title_no:
        type_known = any(k in title_type.upper() for k in _TITLE_TYPE_KEYWORDS)
        no_known = any(k in title_no.upper() for k in _TITLE_TYPE_KEYWORDS)
        # Pure-digit title number AND no title_type → can't tell which
        # instrument. Most likely a Geran but worth verifying.
        if not type_known and not no_known:
            warnings.append(
                f"⚠️  Title number `{title_no}` has no instrument type "
                "(Geran / Hakmilik / HS(D) / HS(M) / PN / PM). Confirm with "
                "client whether this is a final Geran or a qualified title."
            )
        # Garbled-OCR sniff test: a real title number is alphanumeric
        # plus a small set of separators ( / - . space and the parens
        # used in HS(D) / HS(M) ). Anything else (commas, asterisks,
        # control chars) is almost certainly an OCR artefact.
        cleaned = title_no
        for ch in (' ', '/', '-', '.', '(', ')'):
            cleaned = cleaned.replace(ch, '')
        if cleaned and not cleaned.isalnum():
            warnings.append(
                f"⚠️  Title number `{title_no}` contains unexpected "
                "characters — verify OCR."
            )

    # (Lot / Mukim / Daerah individual warnings are folded into the
    # consolidated probate-critical block above so we don't double-warn.)

    # ── Web cross-check on the locale ─────────────────────────────────
    # OCR sometimes flips one daerah for a similarly-spelled neighbour
    # (e.g. "Petaling" vs "Klang"). Run a one-shot web search to confirm
    # the address actually sits in the claimed mukim/daerah. Cached, so
    # the same property never costs us twice. Best-effort — silent on
    # any failure.
    try:
        from services.property_locale_verifier import verify_locale
        web_warn = verify_locale(address=addr, mukim=mukim,
                                 daerah=daerah, negeri=negeri)
        if web_warn:
            warnings.append(web_warn)
    except Exception:
        pass

    # Quick sanity for Mukim/Daerah/Negeri — digits in these fields are
    # almost always OCR errors.
    for label, val in (('Mukim', mukim), ('Daerah', daerah), ('Negeri', negeri)):
        if val and (any(ch.isdigit() for ch in val) or len(val) > 60):
            warnings.append(
                f"⚠️  {label} `{val}` looks suspicious (digits or unusually "
                "long) — likely OCR error, verify."
            )
    return warnings


def _asset_walkthrough_question(pending_gifts: Dict[str, Any],
                                 recent_text: str,
                                 will_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """One-asset-at-a-time review for the will-writer. Picks the first
    un-reviewed property → bank → vehicle and renders a clean card with:

      • Formatted address per Malaysian legal-doc conventions
      • Full identifiers (title / lot / mukim / daerah / negeri)
      • Auto-grouped supporting docs (geran back / SPA / cukai / utility)
        with their per-image purpose so the writer knows what each is
      • Quote from the client's recent messages mentioning this property
        (intent — 'they said give to Joshua 50%')
      • NLC format warnings inline
      • Buttons: ✅ Looks right / 🗑 Remove / ✂️ Wrong support docs / ✏️ Edit

    Returns {text, focus_doc_ids} or None if everything's reviewed.
    """
    all_props = pending_gifts.get('property') or []
    props     = [p for p in all_props if not _is_inventoried(p)]
    banks     = [b for b in (pending_gifts.get('bank') or []) if not _is_inventoried(b)]
    vehicles  = [v for v in (pending_gifts.get('vehicle') or []) if not _is_inventoried(v)]

    # ╔════════════════════════════════════════════════════════════════════╗
    # ║  🔥 BURN-IN RULE — HIGH CONFIDENCE FIRST 🔥                         ║
    # ║  gift_walker.py already sorts by confidence (high → low) when it   ║
    # ║  builds prop_groups. We re-sort here defensively so any future     ║
    # ║  filter (e.g. _autoskip_empty_properties) cannot accidentally      ║
    # ║  promote a low-confidence card to position 0. Highest-confidence   ║
    # ║  asset MUST be inventoried first. Lowest confidence LAST.          ║
    # ║  See CLAUDE.md §10e.                                               ║
    # ╚════════════════════════════════════════════════════════════════════╝
    def _conf(p):
        try:
            from services.gift_walker import _score_property_confidence
            return _score_property_confidence(p.get('extracted') or {})
        except Exception:
            return 0
    props = sorted(props, key=_conf, reverse=True)

    # Silently drop properties with NOTHING worth asking about (no
    # address, no title, no lot, no support docs). Soft-marks them
    # `_inventoried` in the DB so they don't reappear next turn.
    props = _autoskip_empty_properties(props)

    if props:
        target = props[0]
        # If the writer just pressed "Wrong supporting docs" on this
        # property, the app handler stamped _unlink_pending. Render the
        # support-doc picker instead of the normal card.
        if (target.get('extracted') or {}).get('_unlink_pending'):
            return _walkthrough_unlink_picker(target)
        # Cross-reference siblings to back-fill blanks: if all we have is
        # a PTD number, scan every other property/SPA/cukai/utility doc
        # for the same client and pull mukim/daerah/address from the
        # match. The writer sees the enriched view, not the sparse one.
        enriched = _enrich_property_from_siblings(target)
        enriched = _enrich_from_chat_text(enriched, recent_text)
        target = dict(target)
        target['extracted'] = enriched
        # ── ISOLATED-PROPERTY GUARD ─────────────────────────────────────────
        # Single image, has NLC ids (HSD/PTD/title/lot), but the identifiers
        # do NOT appear anywhere in the recent chat text or AI Summary, AND
        # no sibling images share the same lot. We cannot silently render
        # this as a confirmed property — the chat must ASK the client where
        # this came from. See CLAUDE.md §10d.
        if _is_property_isolated(target, recent_text, all_props):
            return _walkthrough_property_unverified_card(target)
        # Accurate sequence counter across two accepted-property paths:
        #   Path A: accepted before placeholder fix → _inventoried=True, not in step5
        #           → still in all_props, filtered from props → counted via (all-pending)
        #   Path B: accepted after placeholder fix → document_id in step5_data
        #           → excluded from all_props entirely → counted via step5_props
        step5_props = [g for g in ((will_data or {}).get('step5') or [])
                       if (g.get('kind') == 'property' or g.get('gift_type') == 'property')
                       and g.get('document_id')]
        n_in_step5  = len(step5_props)
        n_reviewed  = n_in_step5 + (len(all_props) - len(props))
        seq_num     = n_reviewed + 1
        total_props = n_in_step5 + len(all_props)
        return _walkthrough_property_card(target, seq_num, recent_text,
                                           total_props=total_props,
                                           n_props_left=len(props))
    if banks:
        return _walkthrough_bank_card(banks[0], len(banks))
    if vehicles:
        return _walkthrough_vehicle_card(vehicles[0], len(vehicles))
    return None


# ╔════════════════════════════════════════════════════════════════════════╗
# ║  🔥 BURN-IN RULE — ISOLATED PROPERTY MUST BE VERIFIED, NOT GUESSED 🔥   ║
# ║                                                                        ║
# ║  When a single image carries NLC identifiers (HSD/PTD/title/lot) but   ║
# ║  the digits don't appear anywhere in the WhatsApp text / AI Summary,   ║
# ║  AND no sibling image shares the same lot/title — the chat MUST ASK    ║
# ║  the client. NEVER silently render a confirmed property card. NEVER    ║
# ║  fabricate an address or beneficiary. See CLAUDE.md §10d.              ║
# ║                                                                        ║
# ║  Detection:  _is_property_isolated()                                   ║
# ║  Card:       _walkthrough_property_unverified_card()                   ║
# ║  Hook:       _asset_walkthrough_question() before normal card          ║
# ║                                                                        ║
# ║  The Unverified card MUST give the client these three buttons:         ║
# ║    ✅ Yes — it is a real property of mine                              ║
# ║    🗑 Wrong upload — remove it                                         ║
# ║    ⏭ Skip for now                                                     ║
# ║                                                                        ║
# ║  No auto-create gift. No hallucinated beneficiary. Ask first.          ║
# ╚════════════════════════════════════════════════════════════════════════╝
def _is_property_isolated(target: Dict[str, Any],
                           recent_text: str,
                           all_props: List[Dict[str, Any]]) -> bool:
    """A property group is 'isolated' when:
      - it has only ONE image (no support_docs),
      - it claims NLC identifiers (HSD/PTD/title or lot number), AND
      - none of those identifiers appear in the recent chat text / AI Summary,
      - AND no other property group shares the same lot/title.

    Such a card cannot be silently rendered as a real property — the chat
    must ask the client to verify where the image came from. See CLAUDE.md §10d.
    """
    ex = (target.get('extracted') or {})
    support = target.get('support_docs') or []
    if support:  # multi-page property → trustworthy enough
        return False

    title = (ex.get('title_number') or '').strip()
    lot   = (ex.get('lot_number') or '').strip()
    # Need at least one identifier to even be a candidate for "isolated property"
    if not title and not lot:
        return False

    # Build searchable haystack from chat text + AI Summary text.
    haystack = (recent_text or '').lower()

    def _digits_only(s: str) -> str:
        return ''.join(c for c in s if c.isdigit())

    title_digits = _digits_only(title)
    lot_digits   = _digits_only(lot)

    # If either identifier (digit form) appears in chat text → not isolated
    if title_digits and len(title_digits) >= 4 and title_digits in _digits_only(haystack):
        return False
    if lot_digits and len(lot_digits) >= 3 and lot_digits in _digits_only(haystack):
        return False

    # If a sibling property in the same batch shares lot/title → not isolated
    target_id = target.get('document_id')
    for other in all_props:
        if other.get('document_id') == target_id:
            continue
        ox = other.get('extracted') or {}
        ot = (ox.get('title_number') or '').strip()
        ol = (ox.get('lot_number') or '').strip()
        if title_digits and _digits_only(ot) and title_digits == _digits_only(ot):
            return False
        if lot_digits and _digits_only(ol) and lot_digits == _digits_only(ol):
            return False

    return True


def _walkthrough_property_unverified_card(p: Dict[str, Any]) -> Dict[str, Any]:
    """Render a verification-needed card. The image has NLC identifiers
    but cannot be tied to anything in the AI Summary or other images.
    Ask the client where it came from rather than guessing.
    """
    ex = p.get('extracted') or {}
    title = (ex.get('title_number') or '').strip() or '_(not extracted)_'
    lot   = (ex.get('lot_number') or '').strip() or '_(not extracted)_'
    addr  = (ex.get('property_address') or ex.get('description') or '').strip() or '_(not extracted)_'
    fname = p.get('original_filename') or 'this image'

    parts = [
        "### ❓ Unverified property — need your help",
        f"I found an image (`{fname}`) that looks like a property document, "
        "but I **cannot match it** to anything you mentioned in your "
        "WhatsApp/email or to any other image you sent.",
        "**What I extracted from it:**",
        f"  • **Title No.:** {title}",
        f"  • **Lot No.:** {lot}",
        f"  • **Address:** {addr}",
        ("⚠️ Because it's an isolated image with no cross-reference, I won't "
         "auto-create a gift card for it. Tell me what this is so I can "
         "handle it correctly:"),
    ]
    quick = [
        {'label': '✅ Yes — it is a real property of mine', 'value': 'inventory confirm'},
        {'label': '🗑 Wrong upload — remove it',          'value': 'delete'},
        {'label': '⏭ Skip for now',                       'value': 'inventory skip'},
    ]
    return {
        'text': '\n\n'.join(parts) + _qr_marker(quick),
        'focus_doc_ids': [p.get('document_id')] if p.get('document_id') else [],
    }


def _walkthrough_property_card(p: Dict[str, Any], seq_num: int,
                                recent_text: str,
                                total_props: int,
                                n_props_left: int = 0) -> Dict[str, Any]:
    ex = p.get('extracted') or {}
    formatted = _format_property_description(ex)
    doc_kind = p.get('category', 'property_title')
    warnings = _validate_property_format(ex, doc_kind=doc_kind)
    intent = _deduce_intent_from_messages(p, recent_text)
    kind_label = _KIND_LABELS.get(doc_kind, '📜 Property Document')
    # Also show the classifier's purpose sentence if available
    purpose_str = (ex.get('purpose') or '').strip()
    doc_type_line = f"🔍 **Identified as:** {kind_label}"
    if purpose_str:
        doc_type_line += f"\n_{purpose_str[:200]}_"

    # ── Wrong-upload check on the TITLE doc itself ───────────────────────
    _title_wrong = ex.get('_wrong_upload_suspected')
    _title_wrong_reason = (ex.get('_wrong_reason') or '').strip()

    parts = [
        f"### 🏠 Property {seq_num} of {total_props}",
        formatted,
    ]

    if _title_wrong and _title_wrong_reason:
        parts.append(f"⚠️ _{_title_wrong_reason}_ — tap 🗑 Remove if wrong upload.")

    # ── Address deduction confidence notice ──────────────────────────────
    # When the AI matched an address to this title (rather than reading it
    # directly from the document), surface the reasoning and confidence so
    # the writer can confirm or reject it.
    _addr_conf = (ex.get('_address_confidence') or '').lower()
    _addr_needs_confirm = ex.get('_address_needs_confirm')
    _enriched_from = ex.get('_enriched_from') or []
    if 'ai_address_match' in _enriched_from and _addr_conf:
        addr_display = (ex.get('property_address') or '').strip()
        if _addr_conf == 'high':
            parts.append(
                f"✅ **Address deduced from chat/documents** (high confidence): "
                f"_{addr_display}_ — auto-accepted."
            )
        elif _addr_conf in ('medium', 'low'):
            conf_emoji = '🟡' if _addr_conf == 'medium' else '🔴'
            parts.append(
                f"{conf_emoji} **Address deduced** ({_addr_conf} confidence): "
                f"_{addr_display}_\n\n"
                f"_Please confirm this address is correct before accepting. "
                f"If wrong, tap ✏️ Edit to correct it._"
            )

    # Full NLC identifiers — required by National Land Code for will description
    nlc_lines = []
    for label, key in (
        ('Title No.',  'title_number'),
        ('Lot No.',    'lot_number'),
        ('Mukim',      'mukim'),
        ('Daerah',     'daerah'),
        ('Negeri',     'negeri'),
    ):
        v = (ex.get(key) or '').strip()
        if v:
            nlc_lines.append(f"  • **{label}:** {v}")
    if nlc_lines:
        parts.append("📋 **Land Registry Details:**\n" + '\n'.join(nlc_lines))

    # Supporting docs — brief list of types only
    support = p.get('support_docs') or []
    _unrelated_warnings = []
    if support:
        sup_labels = []
        for i, s in enumerate(support, 1):
            kind = s.get('category', '')
            kind_label = {
                'property_spa':      '📝 SPA',
                'property_tax':      '🧾 Cukai Tanah',
                'property_title':    '📜 Title page',
                'property_transfer': '📋 Transfer form',
                'utility_bill':      '⚡ Utility bill',
                'bank_letter':       '🏦 Bank letter',
                'loan_agreement':    '🏦 Loan / Charge document _(encumbrance)_',
                'death_certificate': '🚫 Death certificate _(possibly wrong upload)_',
                'unrelated':         '🚫 Unrelated document _(possibly wrong upload)_',
                'chat_inbox':        '📷 Unclassified',
                'other':             '📷 Unclassified',
            }.get(kind, '📄 Document')
            # custom_type = the document's own heading as read by the classifier
            # (e.g. "Redemption Statement", "Discharge of Charge"). Use it when
            # the standard kind label is too generic (chat_inbox / other).
            custom_type = (s.get('custom_type') or
                           s.get('extracted', {}).get('custom_type') or '').strip()
            purpose = (s.get('purpose') or s.get('extracted', {}).get('purpose') or
                       s.get('original_filename') or '').strip()
            # Override generic labels with the document's own title
            if custom_type and kind in ('chat_inbox', 'other'):
                kind_label = f'📄 {custom_type}'
            _sup_ex = s.get('extracted') or {}
            _sup_wrong = _sup_ex.get('_wrong_upload_suspected') or kind in ('death_certificate', 'unrelated')
            flag = ' ⚠️' if _sup_wrong else ''
            sup_labels.append(f"{kind_label}{flag}")
            if _sup_wrong:
                _unrelated_warnings.append(kind_label)
        parts.append("📎 Also attached: " + ", ".join(sup_labels))
    if _unrelated_warnings:
        parts.append(f"⚠️ Some attached docs may not belong here: {', '.join(_unrelated_warnings)}")

    # NOTE: Beneficiary hint deliberately NOT shown here.
    # Property identity step is for IDENTIFYING THE ASSET ONLY.
    # Beneficiary assignment happens later in Step 5/6 (gifts walkthrough),
    # cross-referenced against the AI Summary at that stage. See CLAUDE.md.

    # ── Ownership & encumbrance status — ONLY show after gates are confirmed ──
    # Gate 1 (ownership) and Gate 2 (encumbrance) run sequentially when the
    # writer taps Accept. `encumbrance_confirmed is not None` means BOTH gates
    # have been answered — safe to display the confirmed status on the card.
    # Before that, we show nothing (the gates will ask in sequence on Accept).
    enc_confirmed = ex.get('encumbrance_confirmed')
    if enc_confirmed is not None:
        ow_type = (ex.get('ownership_type') or '').strip().lower()
        status_parts = []
        if ow_type == 'joint':
            share = (ex.get('ownership_share') or '').strip()
            status_parts.append(f"🤝 Joint — {share}" if share else "🤝 Joint")
        elif ow_type == 'sole':
            status_parts.append("👤 Sole owner")
        if enc_confirmed is False:
            status_parts.append("✅ No encumbrance")
        elif enc_confirmed is True:
            enc_label = ('🏦 Bank charge' if (ex.get('encumbrance_type') or '') == 'charge'
                         else '🚩 Caveat')
            status_parts.append(enc_label)
        if status_parts:
            parts.append("  ".join(status_parts))

    if warnings:
        parts.append("🚨 " + " · ".join(w.lstrip('⚠️ ').strip() for w in warnings))

    parts.append("_Tap **✅ Accept** to confirm, **⏭ Skip** to come back later, or **🗑 Remove** if wrong upload._")

    # ── 3 clean action buttons — always the same, no conditionals ──────
    quick = [
        {'label': '✅ Accept', 'value': 'inventory confirm'},
        {'label': '🗑 Remove', 'value': 'delete'},
        {'label': '⏭ Skip',   'value': 'inventory skip'},
    ]

    # Focus the title image plus ALL supporting docs so every page appears in
    # the carousel. Previously capped at 3 — this meant 5-page gerens would
    # only show 4 thumbnails and the rest were invisible to the writer.
    focus_ids = [p.get('document_id')]
    for s in support:
        if s.get('document_id'):
            focus_ids.append(s['document_id'])
    focus_ids = [d for d in focus_ids if d]

    return {
        'text': '\n\n'.join(parts) + _qr_marker(quick),
        'focus_doc_ids': focus_ids,
    }


def _walkthrough_unlink_picker(p: Dict[str, Any]) -> Dict[str, Any]:
    """Show each support doc with its own 'remove from this property'
    button. Lets the writer fix AI mis-grouping (e.g. an SPA that was
    auto-attached to the wrong lot)."""
    ex = p.get('extracted') or {}
    formatted = _format_property_description(ex)
    support = p.get('support_docs') or []
    parts = [
        f"### ✂️  Wrong supporting docs?",
        formatted,
        ("Below are the docs auto-grouped under this property. Tap one "
         "to **remove** it from this group — it'll go back to the "
         "unclassified pool so you can re-group it under the correct "
         "property."),
    ]
    quick: List[Dict[str, str]] = []
    for i, s in enumerate(support, 1):
        kind = s.get('category', '')
        kind_label = {
            'property_spa': 'SPA',
            'property_tax': 'Cukai Tanah',
            'property_title': 'Geran (extra page)',
            'property_transfer': 'Memorandum of Transfer (Borang 14A/16A)',
            'utility_bill': 'Utility bill',
            'bank_letter': 'Bank letter',
        }.get(kind, 'Doc')
        purpose = (s.get('purpose') or s.get('original_filename') or '').strip()
        parts.append(f"  {i}. **{kind_label}** — _{purpose[:120]}_")
        quick.append({
            'label': f'🗑 Remove #{i} ({kind_label})',
            'value': f'unlink {s.get("document_id")}',
        })
    quick.append({'label': '✅ All correct — keep all', 'value': 'unlink done'})
    focus_ids = [p.get('document_id')] + [s.get('document_id') for s in support]
    focus_ids = [d for d in focus_ids if d]
    return {
        'text': '\n\n'.join(parts) + _qr_marker(quick),
        'focus_doc_ids': focus_ids,
    }


def _walkthrough_bank_card(b: Dict[str, Any], n_left: int) -> Dict[str, Any]:
    ex = b.get('extracted') or {}
    bank = (ex.get('bank_name') or '').strip() or 'Unnamed bank'
    acct = (ex.get('account_number') or '').strip() or '(account no unread)'
    holder = (ex.get('holder_name') or '').strip()
    parts = [
        f"### 🏦 Reviewing bank account ({n_left} left)",
        f"**{bank}**",
        f"  • **Account no.:** `{acct}`",
    ]
    if holder:
        parts.append(f"  • **Holder:** {holder}")
    purpose = (ex.get('purpose') or '').strip()
    if purpose:
        parts.append(f"  • _{purpose}_")
    parts.append("**Include this account in the will?**")
    quick = [
        {'label': '✅ Include — add to wizard', 'value': 'inventory confirm'},
        {'label': '🗑 Remove (not testator\'s)', 'value': 'delete'},
        {'label': '⏭ Skip', 'value': 'inventory skip'},
    ]
    return {
        'text': '\n\n'.join(parts) + _qr_marker(quick),
        'focus_doc_ids': [b.get('document_id')] if b.get('document_id') else [],
    }


def _walkthrough_vehicle_card(v: Dict[str, Any], n_left: int) -> Dict[str, Any]:
    ex = v.get('extracted') or {}
    desc = (ex.get('description') or ex.get('vehicle_make') or 'Vehicle').strip()
    reg = (ex.get('reg_number') or ex.get('registration_number') or '').strip()
    parts = [
        f"### 🚗 Reviewing vehicle ({n_left} left)",
        f"**{desc}** {reg}".strip(),
    ]
    purpose = (ex.get('purpose') or '').strip()
    if purpose:
        parts.append(f"  • _{purpose}_")
    parts.append("**Include this vehicle in the will?**")
    quick = [
        {'label': '✅ Include — add to wizard', 'value': 'inventory confirm'},
        {'label': '🗑 Remove', 'value': 'delete'},
        {'label': '⏭ Skip', 'value': 'inventory skip'},
    ]
    return {
        'text': '\n\n'.join(parts) + _qr_marker(quick),
        'focus_doc_ids': [v.get('document_id')] if v.get('document_id') else [],
    }


def _assets_inventory_question(pending_gifts: Dict[str, Any]) -> str:
    """Show the full clustered asset inventory and ask the user to confirm
    it's complete BEFORE we start assigning beneficiaries to each one.

    Mirrors the identity flow: collect every IC first, then assign roles.
    Here: collect every asset (clustering multi-image dumps under the same
    property), then assign beneficiaries.
    """
    props = pending_gifts.get('property') or []
    banks = pending_gifts.get('bank') or []
    vehicles = pending_gifts.get('vehicle') or []
    n_total = len(props) + len(banks) + len(vehicles)
    parts = [
        f"### 📋 Asset inventory ({n_total} item{'s' if n_total != 1 else ''})",
        ("Before we ask **who inherits what**, let's confirm the full list "
         "of assets you've uploaded. I've grouped multiple uploads of the "
         "same property together (geran + SPA + cukai tanah + utility bills "
         "all count as ONE property)."),
    ]
    if props:
        parts.append(f"**🏠 Properties ({len(props)})**")
        for i, p in enumerate(props, 1):
            ex = p.get('extracted') or {}
            label = (ex.get('property_address') or ex.get('description')
                     or ex.get('title_number') or 'Unnamed property')
            n_support = len(p.get('support_docs') or [])
            extra = f" _(+ {n_support} supporting doc{'s' if n_support != 1 else ''})_" if n_support else ''
            parts.append(f"  {i}. **{label[:80]}**{extra}")
    if banks:
        parts.append(f"**🏦 Bank accounts ({len(banks)})**")
        for i, b in enumerate(banks, 1):
            ex = b.get('extracted') or {}
            bn = ex.get('bank_name', '').strip() or 'Bank'
            an = ex.get('account_number', '').strip()
            parts.append(f"  {i}. {bn} — `{an or '(account no unread)'}`")
    if vehicles:
        parts.append(f"**🚗 Vehicles ({len(vehicles)})**")
        for i, v in enumerate(vehicles, 1):
            ex = v.get('extracted') or {}
            desc = (ex.get('description') or ex.get('vehicle_make')
                    or 'Vehicle')
            reg = ex.get('reg_number') or ex.get('registration_number') or ''
            parts.append(f"  {i}. {desc} {reg}".rstrip())

    parts.append(
        "**Is this everything you'd like to include in the will?**\n\n"
        "If yes I'll start asking who inherits each. If no, upload more "
        "documents now (drag & drop or attach) — I'll cluster them by "
        "property automatically."
    )
    quick = [
        {'label': "✅ Yes, that's everything", 'value': 'confirm assets'},
        {'label': '📎 I have more to upload', 'value': 'i have more to upload'},
    ]
    return '\n\n'.join(parts) + _qr_marker(quick)


def _assets_prompt_for_uploads() -> str:
    """No assets uploaded yet — gentle prompt to drop docs in."""
    parts = [
        "### 📋 Asset inventory",
        ("No assets uploaded yet. Please share documents for what you'd "
         "like to include in the will:"),
        ("• 🏠 **Property** — geran / land title (back + front), and any "
         "SPA, Cukai Tanah, utility bills for the same property\n"
         "• 🏦 **Bank** — statement, passbook, FD certificate\n"
         "• 🚗 **Vehicle** — JPJ grant or road tax\n"
         "• 💼 **EPF / insurance / shares** — statement or policy"),
        ("Drop multiple images of the same property in one go — I'll "
         "cluster them automatically. Tap 'Skip' if you don't have any "
         "specific assets to gift (residuary clause covers everything else)."),
    ]
    quick = [
        {'label': "I'll skip specific gifts", 'value': 'confirm assets'},
    ]
    return '\n\n'.join(parts) + _qr_marker(quick)


def _step6_property_question(pending_props, recent_text, will_data):
    """Walk one property at a time — two-phase per gift:

    Phase A: "Who inherits?" (main beneficiary)
    Phase B: "Who is the substitute?" (fires when _main_beneficiary_set: True)

    After both answered → planner moves to next property.
    """
    p = pending_props[0]
    ex = p.get('extracted') or {}
    formatted = _format_property_description(ex)

    ident_names = [i.get('full_name','').strip()
                   for i in (will_data.get('identities') or [])
                   if i.get('full_name')]
    s1_name = ((will_data.get('step1') or {}).get('full_name') or '').strip().upper()
    candidates = [n for n in ident_names if n.upper() != s1_name]

    addr_label = (ex.get('property_address') or ex.get('title_number') or 'this property')[:60]

    # ═══════════════════════════════════════════════════════════════
    # PHASE B — substitute beneficiary prompt (wizard-aligned)
    # ═══════════════════════════════════════════════════════════════
    if ex.get('_main_beneficiary_set'):
        main_bens = ex.get('_main_beneficiaries') or []
        main_desc = ', '.join(
            f"**{b.get('name','?').title()}** {b.get('share','')}" for b in main_bens
        )
        n_main = len(main_bens)
        parts = [
            f"### 🏠 Specific Gift — Substitute",
            formatted,
            f"✅ **Main beneficiary(ies):** {main_desc}",
            ("**If a main beneficiary dies before the testator, what happens to this gift?**\n\n"
             "_This is the substitute clause — strongly recommended._"),
        ]
        quick: List[Dict[str, str]] = []
        # Option 1 & 2: surviving MBs (only available when 2+ main beneficiaries)
        if n_main >= 2:
            quick.append({'label': '🔄 Surviving beneficiaries — equal shares',
                          'value': 'substitute equal'})
            quick.append({'label': '📊 Surviving beneficiaries — pro-rata shares',
                          'value': 'substitute prorata'})
        # Option 3: Name specific person(s)
        main_names_upper = {b.get('name','').upper() for b in main_bens}
        for n in candidates[:3]:
            if n.upper() not in main_names_upper:
                quick.append({'label': f'👤 {n.title()}',
                              'value': f'substitute specific {n}'})
        quick.append({'label': '⏭ No substitute clause', 'value': 'gift substitute skip'})
        parts.append(
            "_Or type a name: e.g. `substitute specific SARAH BT ALI`_"
            if n_main == 1 else
            "_Or type a name for a specific person outside this list._"
        )
        return {
            'text': '\n\n'.join(parts) + _qr_marker(quick),
            'focus_doc_id': p.get('document_id'),
        }

    # ═══════════════════════════════════════════════════════════════
    # PHASE A — main beneficiary prompt
    # ═══════════════════════════════════════════════════════════════

    # Build evidence footnote (which uploads belong to this property)
    evidence_lines = []
    primary_purpose = (p.get('purpose') or '').strip()
    if primary_purpose:
        evidence_lines.append(f"  • 📜 _{primary_purpose}_")
    for s in (p.get('support_docs') or []):
        kind = s.get('category', '')
        kind_label = {
            'property_spa': '📝 SPA',
            'property_tax': '🧾 Cukai Tanah',
            'property_title': '📜 Geran (extra page)',
            'property_transfer': '📋 Memorandum of Transfer (Borang 14A/16A)',
            'utility_bill': '⚡ Utility bill',
            'bank_letter': '🏦 Bank letter',
            'chat_inbox': '📷 Unclassified page',
            'other': '📷 Unclassified page',
        }.get(kind, '📄 Doc')
        sp = (s.get('purpose') or s.get('extracted', {}).get('purpose') or
              s.get('original_filename') or 'supporting doc').strip()
        evidence_lines.append(f"  • {kind_label} — _{sp[:120]}_")
    evidence_block = '\n'.join(evidence_lines) if evidence_lines else ''

    # ── Deduce beneficiary from email text ───────────────────────
    deduced = []
    if recent_text and candidates:
        import re as _re
        norm = _re.sub(r'(\d+)\s*(?:percent|pct|per\s*cent)\b',
                       r'\1%', recent_text, flags=_re.IGNORECASE)
        norm = _re.sub(r'(\d+)\s+%', r'\1%', norm)
        percent_hits = [(m.start(), m.group(0))
                        for m in _re.finditer(r'(?<!\d)(\d{1,3}%)', norm)]
        if percent_hits:
            for name in candidates:
                name_hits = [m.start() for m in
                             _re.finditer(_re.escape(name), norm, _re.IGNORECASE)]
                if not name_hits:
                    continue
                best = None
                for n_off in name_hits:
                    for p_off, share in percent_hits:
                        dist = abs(p_off - n_off)
                        if dist > 80:
                            continue
                        if best is None or dist < best[0]:
                            lo = max(0, min(n_off, p_off) - 5)
                            hi = min(len(norm), max(n_off, p_off) + len(share) + 5)
                            snippet = norm[lo:hi].strip()
                            if len(snippet) > 80:
                                snippet = snippet[:77] + '…'
                            best = (dist, share, snippet)
                if best:
                    deduced.append({'name': name, 'share': best[1], 'evidence': best[2]})
        if deduced:
            try:
                total = sum(int(d['share'].rstrip('%')) for d in deduced)
            except Exception:
                total = 0
            if total != 100:
                deduced = []

    quick: List[Dict[str, str]] = []
    parts = [
        f"### 🏠 Specific Gift ({len(pending_props)} left) — {addr_label}",
        formatted,
    ]
    if evidence_block:
        parts.append(f"**📎 Based on these uploads:**\n{evidence_block}")
    parts.append("**Who is the main beneficiary for this property?**")

    if deduced:
        primary_value = ', '.join(f"{d['name']} {d['share']}" for d in deduced)
        primary_label = '✓ ' + ', '.join(f"{d['name'].title()} {d['share']}" for d in deduced)
        ev_lines = '\n'.join(f"  • _{d['evidence']}_" for d in deduced)
        parts.append(f"📧 **Suggested from email:**\n{ev_lines}")
        quick.append({'label': primary_label, 'value': primary_value})

    for n in candidates[:4]:
        if deduced and any(d['name'].upper() == n.upper() for d in deduced):
            continue
        quick.append({'label': f"{n.title()} 100%", 'value': f"{n} 100%"})
    if len(candidates) >= 2 and not deduced:
        a, b = candidates[0], candidates[1]
        quick.append({'label': f"{a.title()} 50% + {b.title()} 50%",
                      'value': f"{a} 50%, {b} 50%"})
    quick.append({'label': '⏭ Skip this gift', 'value': 'skip'})
    quick.append({'label': '🗑 Remove', 'value': 'delete'})

    text = '\n\n'.join(parts) + _qr_marker(quick)
    return {'text': text, 'focus_doc_id': p.get('document_id')}


def _step6_bank_question(pending_banks, will_data):
    """One generic question for ALL bank accounts unless user specifies."""
    n = len(pending_banks)
    parts = [
        f"### 🏦 Step 6 — Bank Accounts ({n} statement{'s' if n!=1 else ''})",
        "**Who inherits all your bank accounts?**",
    ]
    # Suggest from beneficiaries
    s4 = will_data.get('step4') or []
    quick: List[Dict[str, str]] = []
    seen = set()
    for b in s4:
        n_b = (b.get('full_name') or '').strip()
        if n_b and n_b.upper() not in seen:
            seen.add(n_b.upper())
            quick.append({'label': n_b.title(), 'value': n_b})
            if len(quick) >= 4: break
    quick.append({'label': 'Walk through one by one', 'value': 'walk one by one'})
    return {'text': '\n\n'.join(parts) + _qr_marker(quick), 'focus_doc_id': None}


def _is_already_executor(identity, executors):
    """Has this identity already been added as an executor?"""
    pid = identity.get('id')
    name = (identity.get('full_name') or '').upper()
    for e in executors:
        if e.get('person_id') == pid:
            return True
        if (e.get('full_name') or '').upper() == name:
            return True
    return False


def _is_confirmed(will_data: Dict[str, Any], section: str) -> bool:
    """Has the user confirmed a section in chat? We piggyback on
    Will.completed_steps in the future; for now treat as un-confirmed
    once and re-ask if user provides corrections — keeps the flow simple."""
    # TODO: persist a "chat_confirmations" set on the chat session
    # For now: if step1 has full_name AND person_id, treat as confirmed
    s1 = will_data.get('step1') or {}
    if section == 'testator':
        return bool(s1.get('full_name') and s1.get('person_id'))
    return False


# ── Guardian / Step-8 / Step-9 helpers ──────────────────────────────────

_MINOR_RELATIONSHIPS = frozenset({
    'son', 'daughter', 'grandson', 'granddaughter',
    'stepson', 'stepdaughter', 'adopted son', 'adopted daughter',
})

def _detect_minor_children(identities: list) -> list:
    """Return the subset of identities that appear to be minor children
    (relationship is a child-type AND DOB suggests under 18, OR we can't
    tell age but relationship is clearly a child-type).

    Intentionally errs on the side of INCLUSION: if in doubt, treat as
    minor so the guardian prompt always fires and the writer confirms.
    """
    from datetime import date, datetime
    today = date.today()
    minors = []
    for p in identities:
        rel = (p.get('relationship') or '').lower().strip()
        if rel not in _MINOR_RELATIONSHIPS:
            continue
        # Try age check via date_of_birth
        dob_str = (p.get('date_of_birth') or '').strip()
        if dob_str:
            try:
                for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y'):
                    try:
                        dob = datetime.strptime(dob_str[:10], fmt).date()
                        break
                    except ValueError:
                        continue
                else:
                    dob = None
                if dob is not None:
                    age = (today - dob).days // 365
                    if age >= 18:
                        continue  # adult — skip
            except Exception:
                pass  # can't parse DOB → treat as potentially minor
        minors.append(p)
    return minors


def _step4_guardian_question(s3: dict, minors: list, recent_text: str = '') -> dict:
    """Return a chat card for the guardian step.

    Flow:
      • If no primary guardian yet → ask for primary.
      • If primary set but no substitute → offer substitute (or skip).
      Both are recorded in step3_data.guardians as a list.
    """
    guardians = s3.get('guardians') or []
    minor_names = [m.get('full_name', '').strip() or m.get('relationship', '') for m in minors]
    minor_label = ', '.join(n for n in minor_names if n) or 'minor children'

    # Determine which prompt to show
    has_primary = len([g for g in guardians if not g.get('is_substitute')]) > 0
    has_sub = len([g for g in guardians if g.get('is_substitute')]) > 0

    if not has_primary:
        parts = [
            "### 👶 Step 4: Guardian",
            f"There are minor children in this will: **{minor_label}**.",
            "The Wills Act 1959 (s.27) requires a testamentary guardian to be "
            "appointed to care for them. Who should be the **primary guardian**?",
            "_Reply with the guardian's full name (as per IC), e.g. `CHAN MEI LIN`._",
        ]
        quick = [
            {'label': '⏭ Skip — no minor children / will set later', 'value': 'guardian skip'},
        ]
    elif not has_sub:
        primary_name = next((g.get('full_name','') for g in guardians if not g.get('is_substitute')), 'primary guardian')
        parts = [
            "### 👶 Step 4: Guardian — Substitute",
            f"Primary guardian set: **{primary_name}**.",
            "Do you also want to appoint a **substitute guardian** (steps in if primary "
            "cannot act)?",
            "_Reply with name, e.g. `LIM AH KENG`, or tap Skip._",
        ]
        quick = [
            {'label': '⏭ No substitute needed', 'value': 'guardian skip substitute'},
        ]
    else:
        # Both set — this path shouldn't render (guardians_confirmed would be set)
        return {'text': ''}

    return {'text': '\n\n'.join(parts) + _qr_marker(quick)}


def _step7_residuary_question(beneficiaries: list) -> str:
    """Ask who inherits the residuary estate. Pre-fills a quick-reply
    default (equal shares among all beneficiaries) so the writer can
    one-tap it for simple wills."""
    # Build default: all beneficiaries equally
    names = [b.get('full_name', '') for b in beneficiaries if isinstance(b, dict) and b.get('full_name')]
    if names:
        if len(names) == 1:
            default_val = f"{names[0]} 100%"
        else:
            default_val = ', '.join(f"{n} equal" for n in names)
        default_label = f"Equal — {', '.join(names[:3])}" + (f" + {len(names)-3} more" if len(names) > 3 else '')
        quick_default = [{'label': f'✅ {default_label}', 'value': default_val}]
    else:
        quick_default = []

    quick = quick_default + [
        {'label': '⏭ Skip residuary clause', 'value': 'residuary skip'},
    ]
    text = (
        "✅ Specific gifts done. Moving to **Step 7: Residuary Estate**.\n\n"
        "After the specific gifts above, who should inherit **everything else** "
        "(property or money not specifically given away)?\n\n"
        "Reply with name + share, e.g. `Wife 100%` or `Joshua 50%, Esther 50%`."
    )
    return text + _qr_marker(quick)


_TRUST_DEFAULTS = {
    'distribution_age': 25,
    'note': 'Trust assets held until each beneficiary reaches the distribution age.',
}

def _step8_trust_question(s7: dict, minors: list, completed: list) -> Optional[str]:
    """Return a chat prompt for testamentary trust OR None if already handled.

    Flow:
      1. If trust hasn't been asked → ask yes/no (plus Skip).
      2. If user said yes but trustee not set → ask for trustee name.
      3. If user said yes, trustee set, but age not set → ask distribution age.
      4. All set OR user skipped → return None (mark trust_confirmed elsewhere).
    """
    # If step7 has trust_skipped → planner is past this step
    if s7.get('trust_skipped') or s7.get('trustee_name') and s7.get('distribution_age'):
        return None  # handled by app handler marking trust_confirmed

    # Has minors? Present more urgently.
    has_minors = bool(minors)
    minor_label = ', '.join(m.get('full_name','') or 'minor child' for m in minors) if has_minors else ''

    if not s7:
        # Haven't asked yet
        if has_minors:
            intro = (
                f"**Step 8: Testamentary Trust** _(optional)_\n\n"
                f"You have minor children (**{minor_label}**). A testamentary trust "
                "holds their inheritance until they reach a set age, rather than "
                "paying out immediately at probate. Do you want to set one up?"
            )
        else:
            intro = (
                "**Step 8: Testamentary Trust** _(optional)_\n\n"
                "A testamentary trust can hold assets for beneficiaries until a set age. "
                "Would you like to set one up?"
            )
        quick = [
            {'label': '✅ Yes — set up a trust', 'value': 'trust yes'},
            {'label': '⏭ No trust needed — skip', 'value': 'trust skip'},
        ]
        return intro + _qr_marker(quick)

    if s7.get('wants_trust') and not s7.get('trustee_name'):
        quick = [{'label': '⏭ Use executor as trustee', 'value': 'trust trustee same as executor'}]
        return (
            "**Step 8: Trust — Trustee**\n\n"
            "Who should be the **trustee** (manages the trust assets)?\n\n"
            "_Reply with the trustee's full name, or tap below to use the executor._"
        ) + _qr_marker(quick)

    if s7.get('wants_trust') and s7.get('trustee_name') and not s7.get('distribution_age'):
        quick = [
            {'label': '25 years old', 'value': 'trust age 25'},
            {'label': '21 years old', 'value': 'trust age 21'},
            {'label': '18 years old', 'value': 'trust age 18'},
            {'label': '⏭ No age limit', 'value': 'trust age none'},
        ]
        return (
            "**Step 8: Trust — Distribution Age**\n\n"
            "At what age should beneficiaries receive their trust share?"
        ) + _qr_marker(quick)

    return None  # All trust fields filled — fall through to trust_confirmed mark


_DEFAULT_OTHER_CLAUSES = [
    ('Funeral arrangements', 'No specific instructions — at executor\'s discretion.'),
    ('Organ donation', 'No specific instructions.'),
    ('Pets', 'No specific instructions.'),
    ('Digital assets', 'Executor to deal with as deemed appropriate.'),
    ('Debts', 'All debts and expenses to be paid from estate before distribution.'),
    ('Governing law', 'This will is governed by the laws of Malaysia.'),
]

def _step9_others_question(s8: dict) -> str:
    """Show the default 'other matters' clauses. User can confirm defaults
    or ask to change any one. Tapping 'Confirm defaults' marks
    others_confirmed in completed_steps."""
    if s8 and s8.get('confirmed'):
        return ''  # already done

    lines = ["**Step 9: Other Matters** _(optional)_\n"]
    lines.append("Here are the standard clauses included in every will. You can confirm "
                 "them or ask to change any of them:\n")
    for clause, default in _DEFAULT_OTHER_CLAUSES:
        override = (s8.get(clause.lower().replace(' ', '_')) or '').strip()
        val = override if override else f'_(default)_ {default}'
        lines.append(f"  • **{clause}:** {val}")
    text = '\n'.join(lines)
    quick = [
        {'label': '✅ Confirm defaults — proceed to review', 'value': 'others confirm'},
        {'label': 'Change funeral instructions', 'value': 'change funeral'},
        {'label': 'Change organ donation preference', 'value': 'change organ donation'},
        {'label': 'Change digital assets instructions', 'value': 'change digital assets'},
        {'label': '⏭ Skip — use all defaults', 'value': 'others skip'},
    ]
    return text + _qr_marker(quick)
