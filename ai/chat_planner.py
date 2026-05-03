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
        _intake_card = _intake_email_card(artifacts, user_text or '')
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
        # Walk one un-reviewed property at a time. When all properties
        # are reviewed, walk banks, then vehicles. _asset_walkthrough_*
        # picks the FIRST item where extracted._inventoried is not True.
        wt = _asset_walkthrough_question(pending_gifts, recent_text)
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
        reply_parts.append(_step5_beneficiaries_question(current_will_data))
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
            "One bullet per asset/account. For EACH property, bank account, insurance policy mentioned:\n"
            "• **Property / Asset:** address or description\n"
            "• **Ownership:** sole / joint (with whom, what share)\n"
            "• **Beneficiary:** full name(s) and share\n"
            "• ❓ Flag anything ambiguous (ownership unclear, beneficiary not named, etc.)\n"
            "Group bank accounts and insurance together at the end if no specific property link.\n\n"
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


def _intake_email_card(artifacts: List[Dict[str, Any]], user_text: str) -> str:
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

    lines = [
        f"## 📋 {n} exhibit{'s' if n != 1 else ''} received{warn_note}",
    ]
    if has_text:
        lines.append("_Analysing your message and documents…_")
    else:
        # No message text — skip loading state, go straight to instructions
        lines.append(
            "_No message text — only attachments received. "
            "Tap **▶️ Start matching** when ready._"
        )
        quick = [{'label': '▶️ Start matching', 'value': 'inbox start'}]
        return '\n'.join(lines) + f'<!--quickreplies:{json.dumps(quick)}-->'

    return '\n'.join(lines)


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
        parts.append("**Relationship to testator?**")
        quick = [
            {'label': 'Spouse', 'value': 'spouse'},
            {'label': 'Son', 'value': 'son'},
            {'label': 'Daughter', 'value': 'daughter'},
            {'label': 'Father', 'value': 'father'},
            {'label': 'Mother', 'value': 'mother'},
            {'label': 'Brother', 'value': 'brother'},
            {'label': 'Sister', 'value': 'sister'},
            {'label': 'Executor', 'value': 'executor'},
            {'label': 'Witness', 'value': 'witness'},
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


def _step5_beneficiaries_question(will_data):
    """Confirm the universe of beneficiaries (people who'll inherit anything).
    Filters identities: drops testator + witnesses; auto-suggests spouse +
    children + anyone explicitly tagged Beneficiary."""
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

    parts = [
        "### 👨‍👩‍👧 Step 5: Beneficiaries",
        "Proposed beneficiary list:",
    ]
    quick: List[Dict[str, str]] = []
    if likely:
        for i in likely:
            parts.append(f"- **{i['full_name']}** ({i.get('relationship') or 'unknown'})")
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
        # Pull every property-ish doc for this client
        sibs = (Document.query.filter(
                    Document.client_id == client_id,
                    Document.id != (p.get('document_id') or ''),
                    Document.category.in_([
                        'property_title', 'property_spa', 'property_tax',
                        'utility_bill', 'bank_letter', 'other',
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
            # Back-fill blank fields from the matching sibling
            for k in ('property_address', 'address', 'title_number',
                      'lot_number', 'mukim', 'daerah', 'negeri',
                      'title_type', 'area'):
                if not (ex.get(k) or '').strip() and (sex.get(k) or '').strip():
                    ex[k] = sex[k]
                    ex.setdefault('_enriched_from', []).append(
                        f"{sib.original_filename or sib.id[:8]}.{k}")
    except Exception:
        pass
    return ex


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
            lo = max(0, idx - 500)
            hi = min(len(text), idx + len(needle) + 500)
            windows.append(text[lo:hi])
        if not windows:
            return ex  # needles not found in this text at all

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

    return ex


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
                                 recent_text: str) -> Optional[Dict[str, Any]]:
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
    props = [p for p in (pending_gifts.get('property') or []) if not _is_inventoried(p)]
    banks = [b for b in (pending_gifts.get('bank') or []) if not _is_inventoried(b)]
    vehicles = [v for v in (pending_gifts.get('vehicle') or []) if not _is_inventoried(v)]

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
        return _walkthrough_property_card(target, len(props), recent_text,
                                           total_remaining=len(props) + len(banks) + len(vehicles))
    if banks:
        return _walkthrough_bank_card(banks[0], len(banks))
    if vehicles:
        return _walkthrough_vehicle_card(vehicles[0], len(vehicles))
    return None


def _walkthrough_property_card(p: Dict[str, Any], n_left: int,
                                recent_text: str,
                                total_remaining: int) -> Dict[str, Any]:
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
        f"### 🏠 Reviewing property ({n_left} of {total_remaining} left)",
        doc_type_line,
        formatted,
    ]
    if _title_wrong and _title_wrong_reason:
        parts.append(
            f"**⚠️ Possible wrong upload on main document:**\n"
            f"  _{_title_wrong_reason}_\n"
            f"  Please verify this is the correct document. Tap 🗑 Remove if uploaded by mistake."
        )

    # Identifiers grid — surface every field so writer can spot OCR errors
    fields = []
    for label, key in (('Title type', 'title_type'),
                       ('Title no.', 'title_number'),
                       ('Lot no.', 'lot_number'),
                       ('Mukim', 'mukim'),
                       ('Daerah', 'daerah'),
                       ('Negeri', 'negeri'),
                       ('Area', 'area')):
        v = (ex.get(key) or '').strip()
        if v:
            fields.append(f"  • **{label}:** {v}")
    if fields:
        parts.append("**📋 Identifiers (read from geran):**\n" + '\n'.join(fields))

    # ── Address verification badge (from Nominatim lookup at OCR time) ──
    _addr_note = (ex.get('address_note') or '').strip()
    _addr_canon = (ex.get('address_canonical') or '').strip()
    _addr_level = (ex.get('address_level') or '').strip()
    _addr_verified = ex.get('address_verified')  # True / False / None
    if _addr_note:
        canon_line = f"\n  _{_addr_canon[:180]}_" if _addr_canon and _addr_level == 'street' else ''
        parts.append(f"{_addr_note}{canon_line}")

    # Show enrichment provenance so the writer trusts the back-filled
    # fields: "Mukim & Daerah back-filled from cukai_tanah_2024.pdf".
    enriched_from = ex.get('_enriched_from') or []
    manually_edited = ex.get('_manually_edited') or []
    if enriched_from or manually_edited:
        srcs = {}
        for ent in enriched_from:
            try:
                fname, _, key = ent.rpartition('.')
            except Exception:
                fname, key = ent, ''
            # Pretty-print source name
            src_label = ('💬 client message' if fname == 'chat_text'
                         else f'📄 _{fname}_' if fname else '📎 sibling doc')
            srcs.setdefault(src_label, []).append(key)
        bullets = [f"  • {', '.join(keys)} ← {src}" for src, keys in srcs.items()]
        if manually_edited:
            bullets.append(f"  • ✏️ manually entered: {', '.join(manually_edited[:5])}")
        parts.append("**🔗 Auto-filled from:**\n" + '\n'.join(bullets))

    # Supporting docs grouped under this property.
    # Index i here matches thumbnail position i+1 in the image carousel
    # (thumbnail 1 = title doc, thumbnails 2..N = support docs in order).
    support = p.get('support_docs') or []
    _unrelated_warnings = []
    if support:
        sup_lines = [f"**📎 {len(support)} supporting doc{'s' if len(support) != 1 else ''} grouped under this property:**"]
        for i, s in enumerate(support, 1):
            kind = s.get('category', '')
            kind_label = {
                'property_spa':      '📝 SPA',
                'property_tax':      '🧾 Cukai Tanah / Property Tax',
                'property_title':    '📜 Geran (extra page)',
                'property_transfer': '📋 Memorandum of Transfer (Borang 14A/16A)',
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
            display_text = custom_type or purpose
            # Thumbnail position: title doc is #1, support docs are #2, #3, ...
            thumb_num = i + 1
            sup_lines.append(f"  {i}. {kind_label} — _{display_text[:140]}_ _(image {thumb_num})_")
            # Flag wrong uploads: death cert / unrelated, OR any doc where
            # the named person doesn't match the testator (_wrong_upload_suspected)
            _sup_ex = s.get('extracted') or {}
            _sup_wrong = _sup_ex.get('_wrong_upload_suspected') or kind in ('death_certificate', 'unrelated')
            _sup_wrong_reason = (_sup_ex.get('_wrong_reason') or '').strip()
            if _sup_wrong:
                if not _sup_wrong_reason:
                    _sup_wrong_reason = (
                        'Death certificate — likely uploaded by mistake.' if kind == 'death_certificate'
                        else 'Does not appear related to this property.'
                    )
                _unrelated_warnings.append((i, thumb_num, kind, _sup_wrong_reason))
        parts.append('\n'.join(sup_lines))
    if _unrelated_warnings:
        warn_lines = []
        for idx, thumb, kind, reason in _unrelated_warnings:
            warn_lines.append(
                f"  • Doc {idx} (image {thumb}): _{reason}_\n"
                f"    Please verify and tap 🗑 Remove if it was uploaded by mistake."
            )
        parts.append("**⚠️ Possibly wrong upload(s):**\n" + '\n'.join(warn_lines))

    # Beneficiary hint from batch group analysis — client said "give to Sarah"
    # in their WhatsApp text; surface it prominently so the writer can
    # pre-fill the gift assignment instead of asking again.
    ben_hint = (ex.get('_beneficiary_hint') or '').strip()
    if ben_hint:
        parts.append(f"**🎁 Intended beneficiary (from client's message):** _{ben_hint}_")

    # Intent quote from client's messages (needle-matched snippet)
    if intent:
        parts.append(f"**💬 Client's message about this property:**\n{intent}")
    elif not fields:
        # No identifiers AND no intent match — show raw message context so
        # the writer can see what the client said alongside this image.
        msg_ctx = (ex.get('_message_context') or '').strip()
        if msg_ctx:
            preview = msg_ctx[:400] + ('…' if len(msg_ctx) > 400 else '')
            parts.append(
                f"**💬 Text sent with this image:**\n"
                f"  > _{preview}_\n"
                f"_(No lot/title match found — please confirm if this text "
                f"describes this property, or type `address:`, `lot:`, etc. to fill in manually.)_"
            )

    # ── Ownership: one-line summary (guided confirm handles the questions) ──
    num_owners   = ex.get('num_owners') or 1
    try:
        num_owners = int(num_owners)
    except (TypeError, ValueError):
        num_owners = 1
    owner_names      = ex.get('owner_names') or []
    ownership_shares = (ex.get('ownership_shares') or '').strip()
    ownership_type   = (ex.get('ownership_type') or '').strip().lower()
    ownership_share  = (ex.get('ownership_share')  or '').strip()

    if not ownership_type:
        ownership_type = 'joint' if (num_owners > 1 or ownership_shares) else 'sole'

    if ownership_type == 'joint':
        share_display = ownership_share or ownership_shares or 'TBC'
        parts.append(f"**🤝 Joint ownership** — {share_display} undivided share")
    else:
        parts.append("**👤 Sole ownership**")

    # ── Encumbrance: one-line summary ──
    encumbrance      = (ex.get('encumbrance') or '').strip()
    encumbrance_type = (ex.get('encumbrance_type') or '').strip().lower()
    enc_confirmed    = ex.get('encumbrance_confirmed')

    if enc_confirmed is False:
        parts.append("**✅ Clean title** — no loan or caveat")
    elif enc_confirmed is True or encumbrance or encumbrance_type:
        enc_icon  = '🏦' if encumbrance_type == 'charge' else '🚩'
        enc_label = ('Bank charge / mortgage' if encumbrance_type == 'charge'
                     else 'Private caveat' if encumbrance_type == 'caveat'
                     else 'Encumbrance')
        detail    = f" — _{encumbrance[:120]}_" if encumbrance else ''
        parts.append(f"**{enc_icon} {enc_label} detected**{detail}")
    else:
        parts.append("**✅ No encumbrance detected**")

    # ── NLC completeness checklist ──────────────────────────────────────
    _COMPULSORY = [
        ('title_number', 'Title number (Geran/HSD/Hakmilik no.)'),
        ('lot_number',   'Lot / PTD number'),
        ('mukim',        'Mukim'),
        ('daerah',       'Daerah / District'),
        ('negeri',       'Negeri / State'),
    ]
    missing_fields = [(key, lbl) for key, lbl in _COMPULSORY
                      if not (ex.get(key) or '').strip()]
    if missing_fields or not (ex.get('property_address') or '').strip():
        status_lines = []
        for key, lbl in _COMPULSORY:
            tick = '✅' if (ex.get(key) or '').strip() else '❌'
            status_lines.append(f"  {tick} {lbl}")
        addr_tick = '✅' if (ex.get('property_address') or '').strip() else '⚠️'
        status_lines.append(f"  {addr_tick} Property address (recommended)")
        parts.append("**📝 Will clause fields:**\n" + '\n'.join(status_lines))

    if warnings:
        parts.append("**🚨 Validation:**\n" + '\n'.join(f"  {w}" for w in warnings))

    parts.append("_Tap **Accept** to add this property to your will, or **Skip** to come back later._")

    # ── Data source summary (helps spot phantom assets from email text) ──
    _enriched_from = ex.get('_enriched_from') or []
    _msg_ctx = (ex.get('_message_context') or '').strip()
    if _enriched_from or _msg_ctx:
        _src_lines = []
        if _enriched_from:
            _src_lines.append(
                f"  🗂 Fields auto-filled from: `{'`, `'.join(set(s.split('.')[0] for s in _enriched_from))}`"
            )
        if _msg_ctx:
            _preview = _msg_ctx[:300].replace('\n', ' ').strip()
            if len(_msg_ctx) > 300:
                _preview += '…'
            _src_lines.append(f"  📨 Message context used for enrichment:\n  > _{_preview}_")
        parts.append("**🔍 Data sources (how fields were filled):**\n" + '\n'.join(_src_lines))

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
    """Walk one property at a time. ONLY asks what goes into the will:
    who inherits + share.

    UX: deduce the LIKELY answer from email text and present that as the
    primary highlighted button with a "📧 from email" rationale. Other
    options follow as smaller secondary buttons.
    """
    p = pending_props[0]
    ex = p.get('extracted') or {}
    formatted = _format_property_description(ex)

    # Build an "evidence" footnote that lists what each uploaded image for
    # THIS property actually proves. Users dump multiple images per
    # property (front + back of geran, SPA, cukai tanah); without this,
    # they can't tell which image the bot is talking about.
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
    evidence_block = ('\n'.join(evidence_lines)) if evidence_lines else ''

    ident_names = [i.get('full_name','').strip()
                   for i in (will_data.get('identities') or [])
                   if i.get('full_name')]
    s1_name = ((will_data.get('step1') or {}).get('full_name') or '').strip().upper()
    candidates = [n for n in ident_names if n.upper() != s1_name]

    # ── Deduce from email text: find share patterns near each name ──────
    # Strategy: normalise the email text first ("50percent" / "50 percent" /
    # "50 %" → "50%"), then find ALL percent occurrences with their byte
    # offsets, and ALL name occurrences with theirs. For each name pick the
    # CLOSEST percent within 80 chars. This avoids greedy-regex pitfalls
    # where `[^.]{0,60}` could "eat" the leading "5" of "50%" and leave
    # "0%" for the digit-capture (which is exactly the bug we just shipped).
    deduced = []  # [{name, share, evidence}]
    if recent_text and candidates:
        import re as _re
        # 1. Normalise the text so every percent token looks like "<n>%"
        norm = _re.sub(r'(\d+)\s*(?:percent|pct|per\s*cent)\b',
                       r'\1%', recent_text, flags=_re.IGNORECASE)
        norm = _re.sub(r'(\d+)\s+%', r'\1%', norm)  # collapse "50 %" → "50%"
        # 2. Collect every (offset, "<n>%") — anchored so we never capture
        #    a partial number ((?<!\d) lookbehind) and the trailing % must
        #    follow with no digits in between.
        percent_hits = [(m.start(), m.group(0))
                        for m in _re.finditer(r'(?<!\d)(\d{1,3}%)', norm)]
        if percent_hits:
            for name in candidates:
                # Find every occurrence of the name (case-insensitive)
                name_hits = [m.start() for m in
                             _re.finditer(_re.escape(name), norm, _re.IGNORECASE)]
                if not name_hits:
                    continue
                # For each name occurrence, find the nearest percent within 80c
                best = None  # (distance, share_str, snippet)
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
                    deduced.append({'name': name, 'share': best[1],
                                    'evidence': best[2]})
        # 3. Sanity check: if the deduced shares don't add to 100, drop the
        #    suggestion entirely rather than show nonsense like "Esther 50%,
        #    Joshua 0%". The user can still tap a per-name button.
        if deduced:
            try:
                total = sum(int(d['share'].rstrip('%')) for d in deduced)
            except Exception:
                total = 0
            if total != 100:
                deduced = []

    # Build the primary suggestion (first button, large/highlighted)
    quick: List[Dict[str, str]] = []
    parts = [
        f"### 🏠 Step 6 — Property ({len(pending_props)} left)",
        formatted,
    ]
    if evidence_block:
        parts.append(f"**📎 Based on these uploads:**\n{evidence_block}")
    parts.append("**Who inherits this property?**")

    if deduced:
        # Primary suggestion = combine all deduced shares into one string
        primary_value = ', '.join(f"{d['name']} {d['share']}" for d in deduced)
        primary_label = '✓ ' + ', '.join(f"{d['name'].title()} {d['share']}" for d in deduced)
        evidence_lines = '\n'.join(f"  • _{d['evidence']}_" for d in deduced)
        parts.append(f"📧 **Suggested from email:**\n{evidence_lines}")
        quick.append({'label': primary_label, 'value': primary_value})

    # Secondary single-beneficiary options
    for n in candidates[:4]:
        if deduced and any(d['name'].upper() == n.upper() for d in deduced):
            continue  # already covered by primary
        quick.append({'label': f"{n.title()} 100%", 'value': f"{n} 100%"})
    if len(candidates) >= 2 and not deduced:
        a, b = candidates[0], candidates[1]
        quick.append({'label': f"{a.title()} 50% + {b.title()} 50%",
                      'value': f"{a} 50%, {b} 50%"})
    quick.append({'label': 'Skip', 'value': 'skip'})
    quick.append({'label': 'Delete', 'value': 'delete'})

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
