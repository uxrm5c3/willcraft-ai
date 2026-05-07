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
from typing import List, Dict, Any, Optional, Tuple
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
        elif just_kind == 'testator_confirmed':
            # 🔥 §10x.37 — transition messages MUST be dynamic. The next
            # step depends on pending_gifts / completed flags / identities.
            # Hardcoded 'Step 3: Executor' was wrong when assets are
            # pending (planner actually renders Step 6 first).
            try:
                _next_label = _compute_next_step_label(current_will_data or {})
            except Exception:
                _next_label = 'Step 3: Executor'
            reply_parts.append(
                f"✅ Testator confirmed: **{just_assigned.get('name','')}**.\n\n"
                f"Now moving to **{_next_label}**."
            )
        elif just_kind == 'identity_skipped':
            # 🔥 §10x.31 — Skip is a no-op. Same IC reappears below.
            # Phrase the ack so the user understands they need to either
            # confirm or delete to actually move on.
            sc = int(just_assigned.get('skip_count') or 1)
            if sc >= 3:
                reply_parts.append(
                    f"🔁 You've skipped **{just_assigned.get('name','')}** "
                    f"{sc} times. To move past this card, click **✓ Yes** "
                    f"to assign a relationship or **🗑 Delete** if it's the "
                    f"wrong upload."
                )
            else:
                reply_parts.append(
                    f"🔁 Asking again about **{just_assigned.get('name','')}** "
                    f"— click **✓ Yes** to confirm the relationship or "
                    f"**🗑 Delete** to remove this IC."
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
        _cid_for_idq = (current_will_data or {}).get('client_id') or ''
        reply_parts.append(_identity_question(pending_ics, recent_text,
                                                client_id=_cid_for_idq))
        # Show the IC photo for the one being asked about so user can verify
        focus = [pending_ics[0]['document_id']] if pending_ics[0].get('document_id') else []
        return _wrap(reply_parts, questions, patch, advice, focus_attachments=focus)

    # No pending IC — Step 1 (Identities) is complete (or empty)
    s1 = current_will_data.get('step1') or {}
    s2 = current_will_data.get('step2') or {}
    n_executors = len((s2.get('executors') or []))
    n_beneficiaries = len(current_will_data.get('step4') or [])

    # 🔥 §10x.37/§10x.45 — single concise ack. Earlier we stacked
    # "Saved X" + "Step 1 COMPLETE" + "moving to Step N" + the next
    # card — three transition lines in one assistant turn confused
    # users. Keep it to ONE line so the next card stands out.
    if just_assigned and not pending_ics and just_kind == 'identity':
        next_label = _compute_next_step_label(current_will_data)
        reply_parts.append(f"🎉 Step 1 done — moving to **{next_label}** below 👇")

    # ── 3. STEP 2: confirm Testator details ─────────────────────────────
    # 🔥 §7 — must run BEFORE Step 6 (Specific Gifts) walkthrough.
    # Source the testator details from EITHER:
    #   (a) Will.step1_data.full_name (already populated by an earlier
    #       wizard write), OR
    #   (b) The Person row with relationship='Testator' (created during
    #       Step 1 IC walk)
    # Without (b) the chat skipped Step 2 whenever step1_data was empty
    # — even though a Testator Person clearly existed — and jumped
    # straight to Step 6 asset walkthrough.
    if not _is_confirmed(current_will_data, 'testator'):
        testator_info = dict(s1) if s1.get('full_name') else {}
        if not testator_info.get('full_name'):
            for ident in (current_will_data.get('identities') or []):
                if (ident.get('relationship') or '').lower() == 'testator':
                    testator_info = {
                        'full_name': ident.get('full_name', ''),
                        'nric_passport': ident.get('nric_passport', ''),
                        'date_of_birth': ident.get('date_of_birth', ''),
                        'residential_address': ident.get('address', ''),
                        'nationality': ident.get('nationality', 'Malaysian'),
                    }
                    break
        if testator_info.get('full_name'):
            reply_parts.append(_step2_question(testator_info))
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
    # 🔥 §10x.38 / §10x.43 — pending gifts include insurance now too,
    # and pending count overrides 'assets_confirmed' flag so that the
    # walkthrough doesn't prematurely advance to Step 7 while gifts
    # remain unwalked. The chat MUST stay in Step 6 as long as ANY
    # asset card is pending, regardless of any flag set earlier.
    has_any_assets = any(pending_gifts.get(k)
                          for k in ('property', 'bank', 'insurance', 'vehicle'))
    total_pending = sum(len(v) for v in pending_gifts.values()
                         if isinstance(v, list))
    if 'assets_confirmed' not in completed or total_pending > 0:
        # 🔥 §10x.46 R7 — Layer 2 MUST run before the no-pending shortcut.
        # When the last property's Layer 1 is confirmed, total_pending=0
        # and has_any_assets=False. Without this check the planner returns
        # `_assets_prompt_for_uploads()` ("Reply confirm assets to lock in")
        # and skips the last gift's Layer 2 (main beneficiary). Verifier
        # then fails R4/R5 for that gift.
        layer2_pending_early = current_will_data.get('layer2_pending_props') or []
        if layer2_pending_early:
            identities_early = current_will_data.get('identities') or []
            if identities_early:
                q = _step6_property_question(layer2_pending_early, recent_text, current_will_data)
                reply_parts.append(q['text'])
                focus = [q['focus_doc_id']] if q.get('focus_doc_id') else []
                return _wrap(reply_parts, questions, patch, advice, focus_attachments=focus)
        if not has_any_assets:
            reply_parts.append(_assets_prompt_for_uploads())
            return _wrap(reply_parts, questions, patch, advice)
        # 🔥 BURN-IN §10x.18 — text-vs-image conflict gate. If any saved
        # gift has an identifier that disagrees with its bound Document's
        # OCR, ASK the user before proceeding to walkthrough or save.
        try:
            from services.conflict_detector import detect_text_image_mismatches
            cid_for_conflict = (current_will_data or {}).get('client_id') or ''
            if cid_for_conflict:
                conflicts = detect_text_image_mismatches(cid_for_conflict)
                # Show only the FIRST unresolved conflict per turn
                resolved = set()
                for c in (current_will_data.get('completed_steps') or []):
                    if isinstance(c, str) and c.startswith('mismatch_resolved_'):
                        resolved.add(c[len('mismatch_resolved_'):])
                for cf in conflicts:
                    key = f'{cf["gift_idx"]}_{cf["field"]}'
                    if key in resolved:
                        continue
                    card = _walkthrough_text_image_conflict_card(cf)
                    reply_parts.append(card['text'])
                    focus = [card['focus_doc_id']] if card.get('focus_doc_id') else []
                    return _wrap(reply_parts, questions, patch, advice,
                                 focus_attachments=focus)
        except Exception:
            pass   # detector is best-effort
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

    # Check if a BANK gift has been saved in step5 (vs property gift / skip).
    # The gate must fire while banks are pending AND no bank-specific gift is
    # in step5 yet — previously this gated on "step5 empty" which wrongly
    # skipped bank assignment as soon as the first property gift was saved.
    _step5_list = current_will_data.get('step5') or []
    _has_bank_gift = any(
        isinstance(g, dict) and (
            g.get('kind') == 'bank'
            or g.get('asset_type') == 'bank'
            or g.get('bank_name')
            or (g.get('property_info') or {}).get('account_no')
            or (g.get('property_details') or {}).get('account_no')
        )
        for g in _step5_list
    )
    if pending_banks and not _has_bank_gift:
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


# 🔥 §10x.60 — per-process AI Summary cache by sha256(input).
# Keeps re-summarisations of unchanged text from costing $0.05 each.
# Process restart drops the cache; that's fine since the AI Summary
# message is also persisted in chat history.
_SUMMARY_CACHE: Dict[str, str] = {}


def _summarise_message(raw_text: str) -> str:
    """Use Claude Haiku to produce a two-part structured summary of a
    forwarded WhatsApp/email message in a will-writing context.

    Returns markdown with two sections:
      **What was communicated** — coherent paraphrase of the message
      **What we deduce** — interpreted will-writing intent (assets, beneficiaries, etc.)

    🔥 §10x.60 — caches by sha256(raw_text). Identical inputs return the
    cached summary instantly (saves the $0.05/call Haiku roundtrip).

    Returns empty string on failure (caller falls back to blockquote).
    """
    if not raw_text or len(raw_text.strip()) < 30:
        return ''
    import hashlib
    cache_key = hashlib.sha256(raw_text.encode('utf-8')).hexdigest()
    if cache_key in _SUMMARY_CACHE:
        return _SUMMARY_CACHE[cache_key]
    # DB-backed cache: check for a prior assistant message with this same
    # input hash. If found, return its summary content (process restart
    # won't trigger a fresh $0.05 Haiku call).
    try:
        from database import db, ChatMessage
        existing = (db.session.query(ChatMessage.content)
                    .filter(ChatMessage.role == 'assistant')
                    .filter(ChatMessage.content.like(f'%_summary_hash:{cache_key[:16]}%'))
                    .order_by(ChatMessage.created_at.desc()).first())
        if existing and existing[0]:
            # Strip our internal hash marker from cached return value
            cached = re.sub(r'<!--_summary_hash:[a-f0-9]+-->', '', existing[0]).strip()
            # Remove the AI Summary header wrapper if present
            cached = re.sub(r'^### 📨 AI Summary of your message\s*', '', cached).strip()
            cached = re.sub(r'<!--quickreplies:.*?-->', '', cached, flags=re.DOTALL).strip()
            if len(cached) > 100:   # plausible cached summary
                _SUMMARY_CACHE[cache_key] = cached
                return cached
    except Exception:
        pass
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
            f"Message:\n{raw_text[:6000]}"
        )
        msg = client.messages.create(
            model=CLAUDE_MODEL_CHEAP,
            # 🔥 BURN-IN — DO NOT LOWER THIS BELOW 4000.
            # Real WhatsApp forwards routinely list 5+ properties, 3-4 bank
            # accounts, and 2-3 insurance policies, plus the analyst's
            # "What we deduce" block expands each into 6-8 lines. At 900
            # the response truncates mid-property and the user has to
            # scroll through an incomplete list — they noticed and called
            # it out. See CLAUDE.md §10x.
            max_tokens=4000,
            timeout=60.0,   # complex messages with 5+ assets need more time
            messages=[{"role": "user", "content": prompt}]
        )
        try:
            from ai.cost_tracker import log_usage
            log_usage(msg, call_site='ai.chat_planner._summarise_message')
        except Exception:
            pass
        result = (msg.content[0].text or '').strip() if msg.content else ''
        _SUMMARY_CACHE[cache_key] = result
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
def _apply_geo_bridge_inplace(parsed_props: List[Dict[str, Any]]) -> None:
    """🔥 §10x.48 Stage 0 — apply §10ha geo bridge to fill mukim/daerah/
    negeri when address contains a known township. Mutates each prop dict
    in place. ONE source of truth for the bridge — used by both
    _extract_ai_summary_properties and the asset_pipeline.

    🔥 §10x.50 Bug B — also NORMALISE mukim when AI Summary put a township
    string into the mukim field (real example: 'Taman Laguna',
    'Seri Alam Masai' — those are townships in Mukim Plentong, not mukim
    themselves). Without this, Tier B mukim_token equality fails.
    """
    try:
        from services.asset_pipeline import resolve_mukim_from_address
    except Exception:
        return
    # Real mukim names (not townships). If AI Summary returns one of these
    # in the mukim field, treat as already canonical and don't re-bridge.
    _CANONICAL_MUKIM = {'plentong', 'pulai', 'tebrau', 'senai',
                         'bandar johor bahru', 'tanjung kupang'}
    for p in parsed_props or []:
        if not isinstance(p, dict):
            continue
        cur_mukim = (p.get('mukim') or '').strip()
        cur_mukim_norm = re.sub(r'^mukim\s+', '', cur_mukim, flags=re.IGNORECASE).strip()
        cur_mukim_norm = re.sub(r'[,;].*$', '', cur_mukim_norm).strip()
        cur_lc = cur_mukim_norm.lower()
        # If already a canonical mukim, accept as-is (no normalisation needed).
        if cur_lc in _CANONICAL_MUKIM:
            p['mukim'] = cur_mukim_norm.title()
            continue
        # If mukim is set but NOT canonical (likely a township string from
        # AI Summary like 'Taman Laguna'), resolve via the §10hc resolver
        # — curated cache + web-search, citation-backed only.
        if cur_mukim:
            bridged = resolve_mukim_from_address(cur_mukim)
            if bridged and bridged[0].lower() != cur_lc:
                p['mukim'] = bridged[0]
                if not (p.get('daerah') or '').strip():
                    p['daerah'] = bridged[1]
                if not (p.get('negeri') or '').strip():
                    p['negeri'] = bridged[2]
            # If resolver didn't recognise it, leave the original value —
            # let downstream Tier B compare verbatim. NEVER fabricate.
            continue
        # No mukim set — resolve from full address.
        bridged = resolve_mukim_from_address(
            p.get('address') or p.get('name') or ''
        )
        if bridged:
            p['mukim'] = bridged[0]
            if not (p.get('daerah') or '').strip():
                p['daerah'] = bridged[1]
            if not (p.get('negeri') or '').strip():
                p['negeri'] = bridged[2]


def _merge_raw_forward_into_props(client_id: str,
                                    ai_props: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """🔥 §10x.50 Bug A — Stage 0 union: when AI Summary card has empty
    lot/title/mukim but the raw forward text has them, merge them in.

    Real example: user wrote "Property 5: ... HSD H.S.(D) 251041, Lot 127082"
    but AI Summary card returned `lot=''` `title=''` because Claude rendered
    them as 'PTD/Lot: unknown'. Without this merge, Tier A direct-match never
    fires for Property 5 → it goes to H3 even though title 251041 sits right
    there in a residual DocGroup.

    The raw forward parser (_parse_raw_forward_properties) already extracts
    fields from message body. We pair its results to the AI Summary props
    by index OR by distinctive token overlap, then fill missing fields.
    """
    if not ai_props:
        return ai_props
    try:
        from database import Will
        _will = (Will.query.filter_by(client_id=client_id, status='draft')
                 .filter(Will.deleted_at.is_(None))
                 .order_by(Will.updated_at.desc()).first())
        if not _will or not _will.step6_data:
            return ai_props
        _s6 = _json.loads(_will.step6_data)
        raw_fwd = (_s6.get('_raw_forward_text') or '').strip()
        if not raw_fwd:
            return ai_props
        raw_props = _parse_raw_forward_properties(raw_fwd) or []
    except Exception:
        return ai_props

    # 🔥 §10x.50 Bug A.2 — locality search fallback. When raw_props yields
    # nothing usable (e.g. Gmail-mobile run-on paragraph with no Property N:
    # headers), scan raw_fwd directly per AssetItem: find a distinctive
    # token (unit number / building name) and pull lot/HSD/PTD/Hakmilik
    # numbers from the ±300-char window around it.
    if not raw_props or len(raw_props) < len(ai_props):
        _LOT_RE   = re.compile(r'\b(?:PTD|Lot)\s*(?:No\.?\s*)?([0-9]{3,})', re.IGNORECASE)
        _HSD_RE   = re.compile(r'\b(?:HSD|HS\s*\(D\)|H\.S\.\s*\(D\))\s*(?:No\.?\s*)?([0-9]{3,})', re.IGNORECASE)
        _GERAN_RE = re.compile(r'\b(?:Geran|Hakmilik|Title)\s*(?:Mukim\s*)?(?:No\.?\s*)?([0-9]{3,})', re.IGNORECASE)
        for ap in ai_props:
            ap_addr = ap.get('address') or ''
            ap_name = ap.get('name') or ''
            tokens = re.findall(r'[A-Z]?-?\d+(?:[-/]\d+)+', ap_addr + ' ' + ap_name)
            anchor_idx = None
            anchor_token = None
            for tok in tokens:
                pos = raw_fwd.find(tok)
                if pos >= 0:
                    anchor_idx = pos
                    anchor_token = tok
                    break
            if anchor_idx is None:
                continue
            window = raw_fwd[max(0, anchor_idx - 200):anchor_idx + 400]
            lot_m   = _LOT_RE.search(window)
            hsd_m   = _HSD_RE.search(window)
            ger_m   = _GERAN_RE.search(window)
            if not (ap.get('lot') or '').strip() and lot_m:
                ap['lot'] = lot_m.group(1).strip()
            if not (ap.get('title') or '').strip():
                if hsd_m:
                    ap['title'] = hsd_m.group(1).strip()
                elif ger_m:
                    ap['title'] = ger_m.group(1).strip()
        return ai_props

    # Pair by ai_index (positional) when counts match; otherwise pair by
    # distinctive token overlap (unit numbers, building names).
    def _tok(s):
        return set(re.findall(r'[a-z0-9]+(?:[-/][a-z0-9]+)*', (s or '').lower()))
    paired = [None] * len(ai_props)
    if len(raw_props) == len(ai_props):
        # 1:1 pairing — most common case for the user's "Property 1:" / 2: / 3:
        # template forwards.
        for i in range(len(ai_props)):
            paired[i] = raw_props[i]
    else:
        # Token-overlap pairing: for each ai_prop, find the raw_prop with
        # highest distinctive-token overlap.
        used = set()
        for i, ap in enumerate(ai_props):
            ap_toks = _tok(ap.get('address', '') + ' ' + ap.get('name', ''))
            distinctive = {t for t in ap_toks if re.match(r'^[a-z]?-?\d+[-\d/]*$', t)}
            best, best_score = None, 0
            for j, rp in enumerate(raw_props):
                if j in used:
                    continue
                rp_toks = _tok(rp.get('address', '') + ' ' + rp.get('name', ''))
                score = len(distinctive & rp_toks)
                if score > best_score:
                    best, best_score = j, score
            if best is not None and best_score > 0:
                paired[i] = raw_props[best]
                used.add(best)

    # Fill missing fields from the paired raw entry (raw never overwrites
    # non-empty AI Summary fields).
    for i, ap in enumerate(ai_props):
        rp = paired[i]
        if not rp:
            continue
        for k in ('lot', 'title', 'mukim', 'daerah', 'negeri', 'ownership'):
            if not (ap.get(k) or '').strip() and (rp.get(k) or '').strip():
                ap[k] = rp[k]
    return ai_props


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
        if msg and msg.content:
            parsed = _parse_ai_summary_text(msg.content)
            if parsed:
                # 🔥 §10x.50 Bug A — fill missing lot/title/mukim from raw text
                parsed = _merge_raw_forward_into_props(client_id, parsed)
                _apply_geo_bridge_inplace(parsed)
                return parsed
        # ── Fallback: parse raw forward text from step6_data ──────────────
        # Per CLAUDE.md §10hg: the canonical N must survive a chat reset.
        # When the AI Summary card is missing (or yielded zero properties),
        # re-derive the canonical list from `step6_data._raw_forward_text`
        # via a line-heuristic parser. This is the durable fallback.
        try:
            from database import Will
            _will = (Will.query.filter_by(client_id=client_id, status='draft')
                     .filter(Will.deleted_at.is_(None))
                     .order_by(Will.updated_at.desc()).first())
            if _will and _will.step6_data:
                _s6 = _json.loads(_will.step6_data)
                raw_fwd = (_s6.get('_raw_forward_text') or '').strip()
                if raw_fwd:
                    parsed = _parse_raw_forward_properties(raw_fwd)
                    _apply_geo_bridge_inplace(parsed)
                    return parsed
        except Exception:
            pass
        return []
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


# ╔══════════════════════════════════════════════════════════════════╗
# ║ 🔥 BURN-IN §10x.12 — AI Summary parsers for banks + insurance 🔥  ║
# ║ Each AI Summary item must become its own step5_data gift entry.  ║
# ║ These parsers extract every bank account and insurance policy    ║
# ║ from the raw forward text so the walkthrough can iterate them.   ║
# ╚══════════════════════════════════════════════════════════════════╝

# Bank lines look like (in raw forward or AI summary):
#   "Posb Bank (Singapore) Account No:030-25917-3"
#   "May Bank (Singapore) Account No:14200692259"
#   "Public Bank (Malaysian) Account No:3244955834(Current account)"
#   "POSB Bank, Account No. 030-25917-3 — to Lim Bee Yan 100%"
_AI_BANK_LINE_RE = re.compile(
    # Institution: 1-3 capitalized words, optionally ending with " Bank"
    # or being "Maybank". Strip a leading bare "Bank" sentence-starter
    # later (see _clean_bank_name) so "Bank POSB" → "POSB Bank".
    r'(?P<inst>(?:[A-Z][A-Za-z]*\s+){0,3}(?:Bank|BANK|Maybank|MAYBANK))\s*'
    r'(?:\((?P<country>[^)]+)\))?\s*[,\-]?\s*'
    r'(?:Account|A/C|Acct)\s*(?:No\.?|Number)\s*[:\-]?\s*'
    r'(?P<acct>[\w\-/]+)'
    r'(?:\s*\((?P<acct_type>[^)]+)\))?',
    re.IGNORECASE,
)


def _clean_bank_name(s: str) -> str:
    """Strip stray leading 'Bank' (sentence opener) so 'Bank POSB Bank'
    → 'POSB Bank'. Also collapse internal whitespace."""
    s = re.sub(r'\s+', ' ', (s or '')).strip()
    # If string starts with "Bank " AND has another "Bank" later, drop leader
    if s.lower().startswith('bank ') and 'bank' in s[5:].lower():
        s = s[5:].strip()
    return s

# Insurance lines look like:
#   "Insurance Policy number:1811500170(NTUC Income)"
#   "eaTiQa Insurance Policy number 10030125"
#   "AIA Insurance Policy number L516911049"
#   "NTUC Income, Policy No. 1811500170 — to Lim Bee Yan 100%"
_AI_INSURANCE_LINE_RE = re.compile(
    r'(?:'
    r'(?P<insurer1>(?:[A-Z][A-Za-z]*\s*){1,4})Insurance\s*'
    r'Policy\s*(?:No\.?|number)\s*[:\-]?\s*(?P<policy1>[A-Z0-9]+)'
    r'|'
    r'Insurance\s*Policy\s*(?:No\.?|number)\s*[:\-]?\s*(?P<policy2>[A-Z0-9]+)'
    r'\s*\((?P<insurer2>[^)]+)\)'
    r'|'
    r'(?P<insurer3>(?:NTUC[^,\n]*|AIA[^,\n]*|eaTiQa[^,\n]*|Great Eastern[^,\n]*|'
    r'Prudential[^,\n]*|Allianz[^,\n]*|Tokio Marine[^,\n]*|Manulife[^,\n]*))'
    r'.*?Policy\s*(?:No\.?|number)\s*[:\-]?\s*(?P<policy3>[A-Z0-9]+)'
    r')',
    re.IGNORECASE,
)


def _extract_ai_summary_banks(client_id: str) -> List[Dict[str, Any]]:
    """Return one entry per bank account mentioned in the user's
    forward (canonical AI Summary card OR fallback _raw_forward_text).

    Each entry: {bank_name, account_number, country, account_type,
                 beneficiary (string), beneficiary_share (string)}.
    """
    if not client_id:
        return []
    raw = _gather_summary_source_text(client_id)
    if not raw:
        return []
    seen = set()
    seen_acct_digits: set = set()   # 🔥 also dedup by acct digits alone
    out: List[Dict[str, Any]] = []
    for m in _AI_BANK_LINE_RE.finditer(raw):
        inst = (m.group('inst') or '').strip()
        acct = (m.group('acct') or '').strip()
        if not inst or not acct:
            continue
        # Reject false positives (e.g. "World Bank Report" without a number)
        if not re.search(r'\d', acct):
            continue
        key = (re.sub(r'\W+', '', inst).lower(), re.sub(r'\W+', '', acct))
        if key in seen:
            continue
        # 🔥 §10x.12 dedup: same account-number digits = same account, even
        # if the institution name varies between mentions (e.g. "POSB Bank
        # (Singapore)" in body vs "POSB Bank, Singapore" in summary).
        acct_digits = re.sub(r'\D', '', acct)
        if acct_digits and acct_digits in seen_acct_digits:
            continue
        seen.add(key)
        if acct_digits:
            seen_acct_digits.add(acct_digits)
        out.append({
            'bank_name':       _clean_bank_name(inst)[:80],
            'account_number':  acct[:40],
            'country':         (m.group('country') or '').strip()[:40],
            'account_type':    (m.group('acct_type') or '').strip()[:40],
            'beneficiary':     '',   # filled by sibling parser if needed
            'beneficiary_share': '',
        })
    # Heuristic: if the raw text says "All my Bank Savings go [to] my wife
    # 100percent" then default every bank's beneficiary to "wife".
    wife_default = re.search(
        r'all\s+(?:my\s+)?(?:bank\s+)?savings?\s+(?:go|to)\s+'
        r'(?:my\s+)?wife', raw, re.IGNORECASE)
    if wife_default:
        for b in out:
            b['beneficiary'] = 'wife'
            b['beneficiary_share'] = '100%'
    return out


def _extract_ai_summary_insurance(client_id: str) -> List[Dict[str, Any]]:
    """Return one entry per insurance policy mentioned. Each entry:
    {insurer, policy_number, beneficiary, beneficiary_share}."""
    if not client_id:
        return []
    raw = _gather_summary_source_text(client_id)
    if not raw:
        return []
    seen = set()
    out: List[Dict[str, Any]] = []
    for m in _AI_INSURANCE_LINE_RE.finditer(raw):
        insurer = (m.group('insurer1') or m.group('insurer2')
                   or m.group('insurer3') or '').strip()
        policy = (m.group('policy1') or m.group('policy2')
                  or m.group('policy3') or '').strip()
        # Drop trash / placeholder
        if not policy or not re.search(r'\d', policy):
            continue
        # Strip trailing junk on insurer ("eaTiQa Insurance" → "eaTiQa")
        insurer = re.sub(r'\s+Insurance\s*$', '', insurer, flags=re.IGNORECASE).strip()
        key = (re.sub(r'\W+', '', insurer).lower(), policy)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            'insurer':       insurer[:80] or 'Insurance Policy',
            'policy_number': policy[:60],
            'beneficiary':       '',
            'beneficiary_share': '',
        })
    wife_default = re.search(
        r'all\s+insurance\s+(?:go|to)\s+(?:my\s+)?wife',
        raw, re.IGNORECASE)
    if wife_default:
        for ins in out:
            ins['beneficiary'] = 'wife'
            ins['beneficiary_share'] = '100%'
    return out


def _gather_summary_source_text(client_id: str) -> str:
    """Best source of truth for asset extraction:
    1. The latest '📨 AI Summary of your message' assistant card
    2. step6_data._raw_forward_text (durable across chat clears)
    Concatenated when both exist.
    """
    parts: List[str] = []
    try:
        from database import db, ChatMessage, ChatSession
        sess_ids_subq = (db.session.query(ChatSession.id)
                         .filter(ChatSession.client_id == client_id)
                         .subquery())
        msg = (ChatMessage.query
               .filter(ChatMessage.session_id.in_(sess_ids_subq))
               .filter(ChatMessage.role == 'assistant')
               .filter(ChatMessage.content.ilike('%AI Summary%'))
               .order_by(ChatMessage.created_at.desc())
               .first())
        if msg and msg.content:
            parts.append(msg.content)
    except Exception:
        pass
    try:
        from database import Will
        _will = (Will.query.filter_by(client_id=client_id, status='draft')
                 .filter(Will.deleted_at.is_(None))
                 .order_by(Will.updated_at.desc()).first())
        if _will and _will.step6_data:
            _s6 = _json.loads(_will.step6_data)
            raw = (_s6.get('_raw_forward_text') or '').strip()
            if raw:
                parts.append(raw)
    except Exception:
        pass
    return '\n'.join(parts)


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

    # 🔥 BURN-IN §10x.18 — split ONLY on top-level property markers,
    # never on sub-field dash lines. The summary template is:
    #     • Property 1: ...
    #       - Address: ...
    #       - PTD/Lot: ...
    # Splitting on every "- " produces 15 fragments instead of 5
    # properties. Top-level markers are `•` or numeric "Property N:" or
    # "Property N — / -". Sub-bullets that begin a sub-field don't
    # qualify; they're lines that start with "-" but follow a parent
    # block.
    # Markers we recognise (any of these starts a NEW property block):
    #   "•" bullet, "*" bullet (markdown), "**Property N:" markdown-bold,
    #   "Property N:" plain, "Property N —" em-dash, "Property N –" en-dash.
    # Note: dash chars include hyphen "-", em-dash "—" U+2014, en-dash "–"
    # U+2013, minus "−" U+2212. Claude/AI summary may use any of these.
    blocks = re.split(
        r'\n\s*(?:'
        r'•\s+'
        r'|\*\*\s*Property\s+\d+\s*[:\-—–−]\s*'
        r'|\*\s+'
        r'|Property\s+\d+\s*[:\-—–−]\s+'
        r')',
        '\n' + body,
    )
    # Drop residual markdown bold markers
    blocks = [re.sub(r'\*\*', '', b).strip() for b in blocks]
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


# ╔════════════════════════════════════════════════════════════════════════╗
# ║  🔥 BURN-IN §10hg — RAW-FORWARD-TEXT FALLBACK PARSER                    ║
# ║  When the AI Summary chat card is missing (chat reset, fresh client),   ║
# ║  re-derive the canonical property list from the raw WhatsApp/email      ║
# ║  body persisted in step6_data._raw_forward_text. The canonical N must  ║
# ║  survive a chat reset.                                                  ║
# ║                                                                          ║
# ║  Heuristics (line-based):                                                ║
# ║    • Property cue words: condominium, unit, jalan, taman, house, shop,   ║
# ║      apartment, lot, plot, land at, geran, ptd, hsd                     ║
# ║    • Skip cues: bank, insurance, policy, account no, executor, witness  ║
# ║    • Postcode regex (\d{5}) is strong evidence of a street address      ║
# ║    • Beneficiary phrases: "X percent to NAME", "go to NAME", etc.       ║
# ║    • Ownership: "I share with NAME 50/50"                               ║
# ╚════════════════════════════════════════════════════════════════════════╝

_RAW_PROP_HINTS = (
    'condominium', 'unit ', 'unit,', 'unit-', 'apartment',
    'house', ' shop ', 'shoplot', 'shop-lot',
    'jalan', 'taman ', 'plot ', 'land at',
    'geran ', 'ptd ', 'hsd ', 'hs(d)',
)
_RAW_SKIP_HINTS = (
    'bank ', 'bank,', 'banking', 'insurance', 'policy', 'account no',
    'account number', 'savings account',
    'executor', 'witness', 'guardian', 'trustee',
)

_POSTCODE_RE   = re.compile(r'\b\d{5}\b')
_RAW_LOT_RE    = re.compile(r'\b(?:PTD|Lot)\s*(?:No\.?\s*)?([0-9]{3,})', re.IGNORECASE)
_RAW_HSD_RE    = re.compile(r'\b(?:HSD|HS\s*\(D\)|H\.S\.\s*\(D\))\s*(?:No\.?\s*)?([0-9]{3,})', re.IGNORECASE)
_RAW_GERAN_RE  = re.compile(r'\b(?:Geran|Hakmilik|Title)\s*(?:Mukim\s*)?(?:No\.?\s*)?([0-9]{3,})', re.IGNORECASE)
_RAW_MUKIM_RE  = re.compile(r'\bMukim\s+([A-Za-z][A-Za-z\s\-]{2,40})', re.IGNORECASE)
_RAW_DAERAH_RE = re.compile(r'\bDaerah\s+([A-Za-z][A-Za-z\s\-]{2,40})', re.IGNORECASE)


def _parse_raw_forward_properties(raw_text: str) -> List[Dict[str, Any]]:
    """Line-heuristic parser. Reads raw WhatsApp/email forward text and
    returns the same shape as `_parse_ai_summary_text` so downstream code
    sees a uniform canonical list.

    🔥 BURN-IN §10x.46 R5 — When the forward uses explicit `Property N:`
    headers (the canonical client format), parse it as BLOCKS not lines:
    the header line establishes the property; following indented/sub-
    lines (HSD/Lot/Mukim/share/beneficiary) belong to that same block,
    NOT to a new property. A previous bug treated the sub-line
    `   HSD H.S.(D) 251041, Lot 127082` (metadata for Property 5) as a
    6th property, breaking AI-Summary count == walker count.
    """
    if not raw_text:
        return []

    # ── Block mode: explicit `Property N:` headers ────────────────────
    _PROP_HEADER_RE = re.compile(r'^\s*(?:\*\*\s*)?Property\s+(\d+)\s*[:\-—–−]\s*(.+?)\s*(?:\*\*)?$',
                                  re.IGNORECASE | re.MULTILINE)
    headers = list(_PROP_HEADER_RE.finditer(raw_text))
    if headers:
        out: List[Dict[str, Any]] = []
        for i, hm in enumerate(headers):
            blk_start = hm.end()
            blk_end = headers[i + 1].start() if i + 1 < len(headers) else len(raw_text)
            header_addr = (hm.group(2) or '').strip()
            block_body  = raw_text[blk_start:blk_end]
            full_block  = header_addr + '\n' + block_body
            out.append(_parse_property_block(header_addr, block_body, full_block))
        return out

    # ── Line mode: no headers, fall back to per-line heuristics ───────
    out: List[Dict[str, Any]] = []
    for raw_line in raw_text.split('\n'):
        line = raw_line.strip()
        if not line or len(line) < 15:
            continue
        low = line.lower()

        # Skip banks / insurance / executor lines unless they ALSO carry a
        # strong property cue (rare overlap).
        if any(h in low for h in _RAW_SKIP_HINTS):
            if not any(h in low for h in _RAW_PROP_HINTS):
                continue

        # Must look property-ish: at least one cue OR a postcode.
        has_hint     = any(h in low for h in _RAW_PROP_HINTS)
        has_postcode = bool(_POSTCODE_RE.search(line))
        if not (has_hint or has_postcode):
            continue

        # Address = portion before beneficiary/ownership phrasing.
        # Trim at the EARLIEST phrasing marker (across all patterns), not
        # the first pattern that happens to match — otherwise "100percent"
        # at the tail wins over "will go to" near the start.
        addr = line
        _trim_pats = (
            r'(?:[\.,]|\s)+\s*I\s+share\s+with',
            r'(?:[\.,]|\s)+\s*(?:my\s+\w+\s+)?\d+\s*percent',
            r'(?:[\.,]|\s)+\s*\d+%',
            r'(?:[\.,]|\s)+\s*(?:my\s+\w+\s+)?(?:will\s+)?go\s+to',
            r'(?:[\.,]|\s)+\s*(?:my\s+condominium\s+)?will\s+go\s+to',
            r'(?:[\.,]|\s)+\s*all\s+my\b',
        )
        _earliest = None
        for pat in _trim_pats:
            m = re.search(pat, addr, re.IGNORECASE)
            if m and (_earliest is None or m.start() < _earliest):
                _earliest = m.start()
        if _earliest is not None:
            addr = addr[:_earliest].rstrip(' ,.;')

        # Strip leading "Unit," / "Our house" / "My shop No," etc.
        addr_clean = re.sub(
            r'^(?:Unit[,\s]+|Our\s+house\s*[,\s]*|My\s+(?:shop\s+No[,\s]*|house\s*[,\s]*)?)\s*',
            '', addr, flags=re.IGNORECASE
        ).strip(' ,.')
        if not addr_clean:
            addr_clean = addr.strip(' ,.')

        lot_m    = _RAW_LOT_RE.search(line)
        hsd_m    = _RAW_HSD_RE.search(line)
        ger_m    = _RAW_GERAN_RE.search(line)
        mukim_m  = _RAW_MUKIM_RE.search(line)
        daerah_m = _RAW_DAERAH_RE.search(line)

        # Beneficiary chunk — everything from "X percent" / "go to" onward.
        bene_chunk = ''
        m = re.search(
            r'(?:my\s+\w+\s+)?(?:\d+\s*(?:percent|%)|go\s+to|will\s+go\s+to|all\s+my\b).*$',
            line, re.IGNORECASE,
        )
        if m:
            bene_chunk = m.group(0).strip()[:200]

        # Ownership chunk — "I share with NAME 50/50".
        own_chunk = ''
        m = re.search(
            r'(?:I\s+share\s+with|joint(?:ly)?\s+with|share\s+with)\s+[^\.,]+(?:\s+(?:50/50|\d+/\d+|\d+%|\d+\s*percent))?',
            line, re.IGNORECASE,
        )
        if m:
            own_chunk = m.group(0).strip()[:120]

        # Name = address up to first comma (compact label). If that's just
        # a house number, include the next comma segment so "10, Jalan Sri
        # Laguna" beats "10".
        if addr_clean:
            segs = [s.strip() for s in addr_clean.split(',') if s.strip()]
            if segs and re.fullmatch(r'\d{1,4}', segs[0]) and len(segs) > 1:
                name = (segs[0] + ', ' + segs[1])[:120]
            else:
                name = (segs[0] if segs else addr_clean)[:120]
        else:
            name = line[:80]

        prop = {
            'name':        name,
            'address':     addr_clean[:200],
            'lot':         (lot_m.group(1).strip() if lot_m else '')[:80],
            'title':       ((hsd_m.group(1).strip() if hsd_m else '')
                            or (ger_m.group(1).strip() if ger_m else ''))[:80],
            'mukim':       (mukim_m.group(1).strip() if mukim_m else '')[:60],
            'daerah':      (daerah_m.group(1).strip() if daerah_m else '')[:60],
            'ownership':   own_chunk,
            'beneficiary': bene_chunk,
        }
        out.append(prop)
    return out


def _parse_property_block(header_addr: str, block_body: str, full_block: str) -> Dict[str, Any]:
    """Parse one Property N: block. The header line is the address; the
    body lines carry sub-fields (HSD/Lot/share/beneficiary). All sub-fields
    are merged into one property — sub-lines never spawn new properties.
    """
    addr = header_addr.strip()
    # Strip leading "Unit," / "Our house" etc. for cleaner address
    addr_clean = re.sub(
        r'^(?:Unit[,\s]+|Our\s+house\s*[,\s]*|My\s+(?:shop\s+No[,\s]*|house\s*[,\s]*)?)\s*',
        '', addr, flags=re.IGNORECASE
    ).strip(' ,.')
    if not addr_clean:
        addr_clean = addr.strip(' ,.')

    # Pull identifiers from the WHOLE block (header + body)
    lot_m    = _RAW_LOT_RE.search(full_block)
    hsd_m    = _RAW_HSD_RE.search(full_block)
    ger_m    = _RAW_GERAN_RE.search(full_block)
    mukim_m  = _RAW_MUKIM_RE.search(full_block)
    daerah_m = _RAW_DAERAH_RE.search(full_block)

    # Beneficiary chunk — first occurrence anywhere in block
    bene_chunk = ''
    m = re.search(
        r'(?:my\s+\w+\s+)?(?:\d+\s*(?:percent|%)|go\s+to|will\s+go\s+to|all\s+my\b|\bto\s+[A-Z]).*$',
        full_block, re.IGNORECASE | re.MULTILINE,
    )
    if m:
        bene_chunk = m.group(0).strip()[:200]

    # Ownership chunk
    own_chunk = ''
    m = re.search(
        r'(?:I\s+share\s+with|joint(?:ly)?\s+with|share\s+with|Sole\s+owner)'
        r'(?:\s+[^\.,\n]+)?(?:\s+(?:50/50|\d+/\d+|\d+%|\d+\s*percent))?',
        full_block, re.IGNORECASE,
    )
    if m:
        own_chunk = m.group(0).strip()[:120]

    # Name = first comma segment of address (compact label)
    if addr_clean:
        segs = [s.strip() for s in addr_clean.split(',') if s.strip()]
        if segs and re.fullmatch(r'\d{1,4}', segs[0]) and len(segs) > 1:
            name = (segs[0] + ', ' + segs[1])[:120]
        else:
            name = (segs[0] if segs else addr_clean)[:120]
    else:
        name = addr_clean[:80]

    return {
        'name':        name,
        'address':     addr_clean[:200],
        'lot':         (lot_m.group(1).strip() if lot_m else '')[:80],
        'title':       ((hsd_m.group(1).strip() if hsd_m else '')
                        or (ger_m.group(1).strip() if ger_m else ''))[:80],
        'mukim':       (mukim_m.group(1).strip() if mukim_m else '')[:60],
        'daerah':      (daerah_m.group(1).strip() if daerah_m else '')[:60],
        'ownership':   own_chunk,
        'beneficiary': bene_chunk,
    }


# ╔════════════════════════════════════════════════════════════════════════╗
# ║  🔥 BURN-IN §10hg — CLASSIFIER + CONFLICT DETECTOR                      ║
# ║                                                                          ║
# ║  Per CLAUDE.md §10hg: every AI-Summary property is HIGH confidence       ║
# ║  (the user told us about it). Image evidence only changes COMPLETENESS:  ║
# ║                                                                          ║
# ║    H1 — title image binds → confirm card with full identifiers           ║
# ║    H2 — non-title doc with mukim/daerah match → confirm provisional      ║
# ║    H3 — no image found → placeholder card asking upload/type             ║
# ║                                                                          ║
# ║  L is NOT a tier here — it's a separate path (image-only, no AI Summary  ║
# ║  reference) → handled by §10d unverified card, see _is_property_isolated.║
# ╚════════════════════════════════════════════════════════════════════════╝

# ╔════════════════════════════════════════════════════════════════════════╗
# ║  🔥 BURN-IN §10hc — _GEO_BRIDGE: street/township → mukim                 ║
# ║                                                                          ║
# ║  Curated, citation-backed only. NEVER add entries from training memory. ║
# ║  Every entry is observed in a Malaysian title document or on an official║
# ║  source (PLAN-PIPK, NLC, Iskandar Puteri / Plentong gazette).            ║
# ║  See CLAUDE.md §10ha geographic-bridge table for the full source list.   ║
# ╚════════════════════════════════════════════════════════════════════════╝
_GEO_BRIDGE = {
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
    'paradisonuava':    ('Pulai', 'Johor Bahru', 'Johor'),  # spelling drift
    'merak kayangan':   ('Pulai', 'Johor Bahru', 'Johor'),
    'nusajaya':         ('Pulai', 'Johor Bahru', 'Johor'),
    # Tebrau (Daerah Johor Bahru)
    'mount austin':     ('Tebrau', 'Johor Bahru', 'Johor'),
    'taman austin':     ('Tebrau', 'Johor Bahru', 'Johor'),
}


def _resolve_geo_from_address(addr: str) -> Optional[tuple]:
    """If `addr` contains a known township/building name, return its
    (mukim, daerah, negeri). Else None. Memory-free — only consults the
    curated _GEO_BRIDGE table.
    """
    if not addr:
        return None
    al = addr.lower()
    # Longest key first so 'bandar seri alam' beats 'seri alam'.
    for key in sorted(_GEO_BRIDGE.keys(), key=len, reverse=True):
        if key in al:
            return _GEO_BRIDGE[key]
    return None


def _digits(s: str) -> str:
    return ''.join(c for c in (s or '') if c.isdigit())


# Stop-words excluded from token matching — every property has these.
_ADDR_STOPWORDS = {
    # Generic property-type words
    'unit', 'condominium', 'condo', 'apartment', 'house', 'shop', 'street',
    'jalan', 'lorong', 'taman', 'bandar', 'pangsapuri', 'kawasan',
    'no', 'block', 'level', 'floor', 'storey',
    # Country / state / district / city — too coarse for property matching
    'johor', 'bahru', 'malaysia', 'singapore', 'selangor', 'kuala', 'lumpur',
    'penang', 'sabah', 'sarawak', 'kelantan', 'pahang', 'perak', 'perlis',
    'puteri', 'iskandar', 'mukim', 'daerah', 'negeri', 'wilayah',
    # English filler
    'and', 'the', 'of', 'at', 'in', 'on', 'to', 'with', 'my', 'our', 'share',
}


def _distinctive_address_tokens(ai_prop: Dict[str, Any]) -> List[str]:
    """Pull tokens worth matching from an AI-Summary property:
       - unit numbers like 'c-30-08', 'b-05-11', 'c30-08'
       - building names ('marina', 'cove', 'paradiso', 'nuova')
       - street/township ('laguna', 'gunung', 'medini', 'masai')
    Stopwords like 'condominium', 'unit', 'jalan' are dropped.
    """
    blob = ' '.join([
        (ai_prop.get('name') or ''),
        (ai_prop.get('address') or ''),
    ]).lower()
    tokens: List[str] = []
    # Unit-number patterns first (high signal)
    for m in re.finditer(r'\b[a-z]?-?\d+[\-/]\d+(?:[\-/]\d+)?\b', blob):
        tokens.append(m.group(0).replace(' ', ''))
    # Word tokens — alphabetic, length >= 4, not stopword
    for w in re.findall(r'[a-z]{4,}', blob):
        if w in _ADDR_STOPWORDS:
            continue
        if w not in tokens:
            tokens.append(w)
    return tokens


def _classify_property_match(ai_prop: Dict[str, Any],
                              image_groups: List[Dict[str, Any]]
                              ) -> Dict[str, Any]:
    """For one AI-Summary property, find the best image-group match.

    Returns:
      {'variant': 'h1'|'h2'|'h3',
       'group':   <matched group dict> or None,
       'reason':  short string for the card}

    h1 = direct identifier match (lot OR title digits equal)
    h2 = mukim+daerah match (when no identifier hint in summary)
    h3 = no image group matches
    """
    ai_lot   = _digits(ai_prop.get('lot') or '')
    ai_title = _digits(ai_prop.get('title') or '')
    ai_mukim = (ai_prop.get('mukim') or '').strip().lower()
    ai_daerah = (ai_prop.get('daerah') or '').strip().lower()
    ai_addr_lc = (ai_prop.get('address') or '').strip().lower()

    # 🔥 BURN-IN §10x.46 R6 — H3 synthetic placeholders MUST be skipped here.
    # The gift_walker synthesizes one h3_placeholder pending entry per
    # AI-Summary property when there is no matching image. Those entries
    # have property_address == ai_addr (so token overlap would falsely
    # fire as h1). The classifier is for REAL image evidence — placeholders
    # don't qualify.
    image_groups = [g for g in (image_groups or [])
                    if not g.get('_h3_placeholder')]

    # ── H1: direct lot/title match ─────────────────────────────────────
    for g in image_groups:
        ex = g.get('extracted') or {}
        g_lot   = _digits(ex.get('lot_number') or '')
        g_title = _digits(ex.get('title_number') or '')
        if ai_lot and g_lot and len(ai_lot) >= 3 and ai_lot == g_lot:
            return {'variant': 'h1', 'group': g,
                    'reason': f'Lot {ai_lot} matches'}
        if ai_title and g_title and len(ai_title) >= 4 and ai_title == g_title:
            return {'variant': 'h1', 'group': g,
                    'reason': f'Title {ai_title} matches'}

    # ── H1b: token overlap (building name / unit number / street) ─────
    # Clients describe in text ("Marina Cove unit C-30-08"); the title doc
    # has only OCR'd fields. We can't match identifiers, but we CAN match
    # distinctive tokens: building names, unit numbers, street names.
    if ai_addr_lc:
        ai_tokens = _distinctive_address_tokens(ai_prop)
        for g in image_groups:
            ex = g.get('extracted') or {}
            g_blob = ' '.join([
                (ex.get('property_address') or ''),
                (ex.get('description') or ''),
                (ex.get('property_description') or ''),
                (ex.get('building_name') or ''),
                (ex.get('township') or ''),
            ]).lower()
            if not g_blob.strip():
                continue
            hits = [t for t in ai_tokens if t in g_blob]
            # Strong: at least one unit-like token (e.g. "c-30-08") OR two
            # generic tokens (e.g. "marina" + "cove").
            unit_re = re.compile(r'^[a-z]?-?\d+-\d+$')
            unit_hits = [h for h in hits if unit_re.match(h)]
            if unit_hits:
                return {'variant': 'h1', 'group': g,
                        'reason': f'Unit token match: "{unit_hits[0]}"'}
            if len(hits) >= 2:
                return {'variant': 'h1', 'group': g,
                        'reason': f'Tokens match: {hits[:3]}'}

    # ── H2: mukim+daerah match (geographic, no direct id) ──────────────
    # If AI Summary doesn't state mukim explicitly, try the §10hc geo
    # bridge from the address (e.g. "Seri Alam Masai" → Mukim Plentong).
    eff_mukim = ai_mukim
    eff_daerah = ai_daerah
    if not eff_mukim:
        bridged = _resolve_geo_from_address(ai_addr_lc) or _resolve_geo_from_address(
            (ai_prop.get('name') or '').lower()
        )
        if bridged:
            eff_mukim  = bridged[0].lower()
            eff_daerah = bridged[1].lower()
    if eff_mukim:
        # H2 requires mukim match AND at least one address-token overlap
        # in the doc's blob — otherwise mukim-only match is too coarse:
        # Mukim Plentong contains Marina Cove, Seri Alam, Taman Laguna…
        # all distinct properties. Prevents prop A from stealing prop B's
        # image just because they happen to share a mukim.
        ai_tokens = _distinctive_address_tokens(ai_prop)
        for g in image_groups:
            ex = g.get('extracted') or {}
            g_mukim  = (ex.get('mukim') or '').strip().lower()
            g_daerah = (ex.get('daerah') or '').strip().lower()
            if not (g_mukim and g_mukim == eff_mukim):
                continue
            if eff_daerah and g_daerah and g_daerah != eff_daerah:
                continue
            g_blob = ' '.join([
                (ex.get('property_address') or ''),
                (ex.get('description') or ''),
                (ex.get('property_description') or ''),
                (ex.get('building_name') or ''),
                (ex.get('township') or ''),
            ]).lower()
            token_hits = [t for t in ai_tokens if t in g_blob]
            if token_hits:
                return {'variant': 'h2', 'group': g,
                        'reason': (f'Mukim {eff_mukim.title()} + token '
                                   f'{token_hits[0]!r}')}

    # ── H3: no image found ─────────────────────────────────────────────
    return {'variant': 'h3', 'group': None,
            'reason': 'No matching image — provide title doc or type details'}


def _detect_message_conflicts(ai_props: List[Dict[str, Any]]
                               ) -> List[Dict[str, Any]]:
    """Surface contradictions in the user's message that need clarification
    BEFORE the walkthrough proceeds. Per CLAUDE.md §10hg rule #7.

    Returns list of conflict descriptors, each:
      {'kind': 'duplicate_address' | 'allocation_overflow' | 'split_repeated',
       'property_idx': int, 'detail': str, 'options': [{label,value}, ...]}
    Empty list = no conflicts.
    """
    conflicts: List[Dict[str, Any]] = []
    if not ai_props:
        return conflicts

    # 1. Duplicate address: two properties pointing to the SAME street/unit
    seen: Dict[str, int] = {}
    for i, p in enumerate(ai_props):
        addr = (p.get('address') or '').strip().lower()
        if not addr or len(addr) < 8:
            continue
        # Compact key — first 40 chars normalised
        key = re.sub(r'[^a-z0-9]+', '', addr)[:40]
        if not key:
            continue
        if key in seen:
            conflicts.append({
                'kind': 'duplicate_address',
                'property_idx': i,
                'detail': (f"Properties #{seen[key]+1} and #{i+1} look like "
                           f"the same address: \"{(p.get('address') or '')[:80]}\"."),
                'options': [
                    {'label': f'They\'re the SAME property — keep #{seen[key]+1}',
                     'value': f'conflict merge {seen[key]+1} {i+1}'},
                    {'label': f'They\'re DIFFERENT — keep both',
                     'value': f'conflict keep {seen[key]+1} {i+1}'},
                    {'label': '✏️ Let me clarify in chat', 'value': 'other'},
                ],
            })
            continue
        seen[key] = i

    # 2. Allocation overflow: beneficiary shares > 100% in one property
    for i, p in enumerate(ai_props):
        b = (p.get('beneficiary') or '').lower()
        if not b:
            continue
        # Pull all "NN percent" / "NN%" tokens and sum them
        nums = [int(x) for x in re.findall(r'(\d{1,3})\s*(?:percent|%)', b) if 0 < int(x) <= 100]
        if nums and sum(nums) > 100:
            conflicts.append({
                'kind': 'allocation_overflow',
                'property_idx': i,
                'detail': (f"Property #{i+1} ({(p.get('name') or 'unknown')[:60]}): "
                           f"shares add up to {sum(nums)}%, not 100%. "
                           f"Original: \"{(p.get('beneficiary') or '')[:120]}\""),
                'options': [
                    {'label': '✏️ Restate the shares', 'value': 'other'},
                ],
            })

    return conflicts


def _walkthrough_conflict_card(conflict: Dict[str, Any]) -> Dict[str, Any]:
    """Render a clarification card. The walkthrough does NOT advance until
    the user answers — see CLAUDE.md §10hg rule #7.
    """
    kind = conflict.get('kind', 'unknown')
    detail = conflict.get('detail', '')
    icon = '⚠️' if kind == 'allocation_overflow' else '❓'
    title = {
        'duplicate_address':   'Possible duplicate property',
        'allocation_overflow': 'Beneficiary shares don\'t add up',
        'split_repeated':      'Conflicting allocation',
    }.get(kind, 'Need clarification')

    parts = [
        f"### {icon} {title}",
        detail,
        ("Tell me which reading is correct so I can put the right entry "
         "into your will."),
    ]
    quick = conflict.get('options') or [{'label': '✏️ Let me clarify', 'value': 'other'}]
    return {
        'text': '\n\n'.join(parts) + _qr_marker(quick),
        'focus_doc_ids': [],
    }


# ╔══════════════════════════════════════════════════════════════════╗
# ║ 🔥 BURN-IN §10x.23 — 3-LAYER FLOW FOR ALL ASSETS 🔥                ║
# ║ Every asset (property, bank, insurance) goes through 3 cards:     ║
# ║   Layer 1: Confirm Asset (identification only)                     ║
# ║   Layer 2: Confirm Main Beneficiaries                              ║
# ║   Layer 3: Confirm Substitute Beneficiaries                        ║
# ║ Default substitute follows §10x.14 rules.                          ║
# ╚══════════════════════════════════════════════════════════════════╝

def _walkthrough_bank_layer1_card(bank: Dict[str, Any], seq: int, total: int) -> Dict[str, Any]:
    """🔥 §10x.23 Layer 1 — confirm bank account is real / belongs to testator."""
    bn   = (bank.get('bank_name') or 'Bank').strip()
    acct = (bank.get('account_number') or '').strip()
    cty  = (bank.get('country') or '').strip()
    typ  = (bank.get('account_type') or '').strip()
    bene_hint = (bank.get('beneficiary') or '').strip()
    bene_pct  = (bank.get('beneficiary_share') or '').strip()

    parts = [
        f"### 🏦 Bank Account {seq} of {total} — Layer 1: Confirm Asset",
        f"📨 **From your message:**",
        f"• **Institution:** {bn}" + (f" ({cty})" if cty else ''),
        f"• **Account No.:** `{acct}`" + (f" _({typ})_" if typ else ''),
    ]
    if bene_hint:
        parts.append(f"• **Beneficiary intent:** {bene_hint} {bene_pct}".strip())
    parts.append("Confirm this account belongs to the testator?")
    quick = [
        {'label': '✅ Confirm — add to specific gifts',
         'value': 'bank_l1 confirm'},
        {'label': '🗑 Wrong — remove from list',
         'value': 'bank_l1 remove'},
        {'label': '⏭ Skip — handle later',
         'value': 'bank_l1 skip'},
    ]
    return {'text': '\n\n'.join(parts) + _qr_marker(quick), 'focus_doc_ids': []}


def _walkthrough_bank_layer2_card(gift: Dict[str, Any],
                                    identities: List[Dict[str, Any]]) -> Dict[str, Any]:
    """🔥 §10x.23 Layer 2 — pick main beneficiary for a confirmed bank gift."""
    bn   = (gift.get('bank_name') or 'Bank').strip()
    acct = (gift.get('account_number') or '').strip()
    spouse_name = ''
    children: List[str] = []
    for i in identities or []:
        rel = (i.get('relationship') or '').lower()
        nm  = (i.get('full_name') or '').strip()
        if not nm:
            continue
        if rel in ('spouse', 'wife', 'husband'):
            spouse_name = nm
        elif rel in ('son', 'daughter'):
            children.append(nm)

    parts = [
        f"### 🎯 Main Beneficiary — {bn} {acct}",
        "Layer 2: **Who inherits this account 100%?**",
    ]
    quick: List[Dict[str, str]] = []
    if spouse_name:
        quick.append({'label': f'💛 {spouse_name} 100% (wife — default for bank savings)',
                      'value': f'bank_l2 main 100% {spouse_name}'})
    if len(children) >= 2:
        quick.append({'label': f'👨‍👩‍👧 Both children equally',
                      'value': 'bank_l2 main equal children'})
    for ch in children[:3]:
        quick.append({'label': f'👤 {ch} 100%',
                      'value': f'bank_l2 main 100% {ch}'})
    quick.append({'label': '✏️ Type a different name',
                  'value': 'other'})
    quick.append({'label': '⏭ Skip', 'value': 'bank_l2 skip'})
    return {'text': '\n\n'.join(parts) + _qr_marker(quick), 'focus_doc_ids': []}


def _walkthrough_bank_layer3_card(gift: Dict[str, Any],
                                    identities: List[Dict[str, Any]]) -> Dict[str, Any]:
    """🔥 §10x.23 Layer 3 — pick substitute beneficiary (§10x.14 defaults)."""
    bn   = (gift.get('bank_name') or 'Bank').strip()
    acct = (gift.get('account_number') or '').strip()
    main_bens = gift.get('beneficiaries') or []
    main_names = [b.get('name', '') for b in main_bens]
    main_str = ', '.join(main_names) or '?'

    spouse_name = ''
    children: List[str] = []
    for i in identities or []:
        rel = (i.get('relationship') or '').lower()
        nm  = (i.get('full_name') or '').strip()
        if not nm: continue
        if rel in ('spouse', 'wife', 'husband'):
            spouse_name = nm
        elif rel in ('son', 'daughter'):
            children.append(nm)

    # §10x.14 default
    sole = main_names[0] if len(main_names) == 1 else None
    if sole and sole.upper() == spouse_name.upper() and len(children) >= 2:
        default = f'children equally'
        default_value = 'bank_l3 sub equal children'
    elif sole and sole in children:
        others = [c for c in children if c != sole]
        if others:
            default = f'{others[0]} 100%'
            default_value = f'bank_l3 sub 100% {others[0]}'
        else:
            default = 'no substitute'
            default_value = 'bank_l3 sub none'
    elif len(main_bens) >= 2:
        default = 'surviving beneficiaries equal'
        default_value = 'bank_l3 sub survivors'
    else:
        default = f'children equally' if len(children) >= 2 else 'no substitute'
        default_value = 'bank_l3 sub equal children' if len(children) >= 2 else 'bank_l3 sub none'

    parts = [
        f"### 🔄 Substitute Beneficiary — {bn} {acct}",
        f"Layer 3: **If {main_str} dies before you, who gets this account?**",
        f"_§10x.14 default: **{default}**_",
    ]
    quick = [
        {'label': f'✅ Default — {default}', 'value': default_value},
    ]
    if spouse_name and spouse_name not in main_names:
        quick.append({'label': f'👤 {spouse_name} 100%',
                      'value': f'bank_l3 sub 100% {spouse_name}'})
    for ch in children[:3]:
        if ch in main_names:
            continue
        quick.append({'label': f'👤 {ch} 100%',
                      'value': f'bank_l3 sub 100% {ch}'})
    if len(children) >= 2:
        quick.append({'label': f'👨‍👩‍👧 Children equally',
                      'value': 'bank_l3 sub equal children'})
    quick.append({'label': '⏭ No substitute clause',
                  'value': 'bank_l3 sub none'})
    return {'text': '\n\n'.join(parts) + _qr_marker(quick), 'focus_doc_ids': []}


def _walkthrough_insurance_layer1_card(ins: Dict[str, Any], seq: int, total: int) -> Dict[str, Any]:
    insurer = (ins.get('insurer') or 'Insurer').strip()
    policy  = (ins.get('policy_number') or '').strip()
    parts = [
        f"### 🛡 Insurance Policy {seq} of {total} — Layer 1: Confirm Asset",
        f"📨 **From your message:**",
        f"• **Insurer:** {insurer}",
        f"• **Policy No.:** `{policy}`",
        "Confirm this policy belongs to the testator?",
    ]
    quick = [
        {'label': '✅ Confirm — add to specific gifts', 'value': 'insurance_l1 confirm'},
        {'label': '🗑 Wrong — remove',                   'value': 'insurance_l1 remove'},
        {'label': '⏭ Skip',                              'value': 'insurance_l1 skip'},
    ]
    return {'text': '\n\n'.join(parts) + _qr_marker(quick), 'focus_doc_ids': []}


def _walkthrough_insurance_layer2_card(gift: Dict[str, Any],
                                         identities: List[Dict[str, Any]]) -> Dict[str, Any]:
    insurer = (gift.get('insurer') or '').strip()
    policy  = (gift.get('policy_number') or '').strip()
    spouse_name = ''
    children: List[str] = []
    for i in identities or []:
        rel = (i.get('relationship') or '').lower()
        nm  = (i.get('full_name') or '').strip()
        if not nm: continue
        if rel in ('spouse', 'wife', 'husband'):
            spouse_name = nm
        elif rel in ('son', 'daughter'):
            children.append(nm)
    parts = [
        f"### 🎯 Main Beneficiary — {insurer} Policy {policy}",
        "Layer 2: **Who is the named beneficiary?**",
        "_(For policies that pay direct to a nominee, this overrides the will, but we still record it.)_",
    ]
    quick: List[Dict[str, str]] = []
    if spouse_name:
        quick.append({'label': f'💛 {spouse_name} 100% (wife — default)',
                      'value': f'insurance_l2 main 100% {spouse_name}'})
    if len(children) >= 2:
        quick.append({'label': f'👨‍👩‍👧 Both children equally',
                      'value': 'insurance_l2 main equal children'})
    for ch in children[:3]:
        quick.append({'label': f'👤 {ch} 100%',
                      'value': f'insurance_l2 main 100% {ch}'})
    quick.append({'label': '✏️ Type a different name', 'value': 'other'})
    quick.append({'label': '⏭ Skip', 'value': 'insurance_l2 skip'})
    return {'text': '\n\n'.join(parts) + _qr_marker(quick), 'focus_doc_ids': []}


def _walkthrough_insurance_layer3_card(gift: Dict[str, Any],
                                         identities: List[Dict[str, Any]]) -> Dict[str, Any]:
    insurer = (gift.get('insurer') or '').strip()
    policy  = (gift.get('policy_number') or '').strip()
    main_bens = gift.get('beneficiaries') or []
    main_names = [b.get('name', '') for b in main_bens]
    main_str = ', '.join(main_names) or '?'

    spouse_name = ''
    children: List[str] = []
    for i in identities or []:
        rel = (i.get('relationship') or '').lower()
        nm  = (i.get('full_name') or '').strip()
        if not nm: continue
        if rel in ('spouse', 'wife', 'husband'):
            spouse_name = nm
        elif rel in ('son', 'daughter'):
            children.append(nm)

    sole = main_names[0] if len(main_names) == 1 else None
    if sole and sole.upper() == spouse_name.upper() and len(children) >= 2:
        default = 'children equally'
        default_value = 'insurance_l3 sub equal children'
    elif sole and sole in children:
        others = [c for c in children if c != sole]
        if others:
            default = f'{others[0]} 100%'
            default_value = f'insurance_l3 sub 100% {others[0]}'
        else:
            default = 'no substitute'
            default_value = 'insurance_l3 sub none'
    elif len(main_bens) >= 2:
        default = 'surviving beneficiaries equal'
        default_value = 'insurance_l3 sub survivors'
    else:
        default = 'children equally' if len(children) >= 2 else 'no substitute'
        default_value = 'insurance_l3 sub equal children' if len(children) >= 2 else 'insurance_l3 sub none'

    parts = [
        f"### 🔄 Substitute Beneficiary — {insurer} Policy {policy}",
        f"Layer 3: **If {main_str} dies before you, who gets this?**",
        f"_§10x.14 default: **{default}**_",
    ]
    quick = [{'label': f'✅ Default — {default}', 'value': default_value}]
    if spouse_name and spouse_name not in main_names:
        quick.append({'label': f'👤 {spouse_name} 100%',
                      'value': f'insurance_l3 sub 100% {spouse_name}'})
    for ch in children[:3]:
        if ch in main_names: continue
        quick.append({'label': f'👤 {ch} 100%',
                      'value': f'insurance_l3 sub 100% {ch}'})
    if len(children) >= 2:
        quick.append({'label': '👨‍👩‍👧 Children equally',
                      'value': 'insurance_l3 sub equal children'})
    quick.append({'label': '⏭ No substitute clause',
                  'value': 'insurance_l3 sub none'})
    return {'text': '\n\n'.join(parts) + _qr_marker(quick), 'focus_doc_ids': []}


# Legacy single-card stubs — kept for any caller; redirect to Layer 1.
def _walkthrough_bank_h3_card(bank, seq, total, identities):
    return _walkthrough_bank_layer1_card(bank, seq, total)


def _walkthrough_insurance_h3_card(ins, seq, total, identities):
    return _walkthrough_insurance_layer1_card(ins, seq, total)


def _walkthrough_property_card_h3(ai_prop: Dict[str, Any],
                                    seq_num: int,
                                    total: int) -> Dict[str, Any]:
    """Render a placeholder card for an AI-Summary property that has NO
    image evidence. Per CLAUDE.md §10hg, message-stated = HIGH always —
    the only thing missing is the title doc. We confirm and ask the user
    to upload OR type the missing legal-doc details after.
    """
    name = (ai_prop.get('name') or 'this property').strip()
    addr = (ai_prop.get('address') or '').strip()
    own  = (ai_prop.get('ownership') or '').strip()
    bene = (ai_prop.get('beneficiary') or '').strip()
    mukim = (ai_prop.get('mukim') or '').strip()
    daerah = (ai_prop.get('daerah') or '').strip()
    negeri = (ai_prop.get('negeri') or '').strip()
    lot = (ai_prop.get('lot') or '').strip()
    title = (ai_prop.get('title') or '').strip()

    # 🔥 §10x.46 R1 — Layer 1 = ASSET IDENTITY ONLY. Strip Claude's
    # parenthetical annotations that leak Layer-2 / internal info into
    # Layer 1: "(sender's share 1/2)", "(location not specified)",
    # "(of sender's 50% share)" etc. These belong to Layer 2 or are
    # noise. Keep only the part before the first '(' for ownership/addr.
    def _strip_parens(s: str) -> str:
        s = re.sub(r'\s*\([^)]*\)\s*', ' ', s or '').strip()
        return re.sub(r'\s+', ' ', s).strip(' ,;.')
    addr = _strip_parens(addr)
    own  = _strip_parens(own)
    # Ownership: also strip share fractions like "50/50 share" — those
    # belong to Layer 2 (testator-share %). Keep "joint with X" / "sole".
    own = re.sub(r',?\s*\d+\s*/\s*\d+(?:\s+share)?\s*$', '', own,
                 flags=re.IGNORECASE).strip(' ,;.')

    # 🔥 §10x.13 display — pre-compute testator_share so user sees what
    # they're actually disposing of ("my 1/2 share", not the whole property).
    own_lc = own.lower()
    _testator_share_display = ''
    if 'sole' in own_lc:
        _testator_share_display = '1/1 (sole owner)'
    elif own:
        _share_re = re.compile(r'(?:share\s+)?(\d+)\s*/\s*(\d+)')
        m = _share_re.search(own)
        if m:
            n, d = int(m.group(1)), int(m.group(2))
            if n == d and d >= 2:
                _testator_share_display = '1/2 (joint, splitting equally)'
            elif n < d:
                _testator_share_display = f'{n}/{d}'
        elif 'joint' in own_lc or 'with' in own_lc:
            _testator_share_display = '1/2 (joint, assumed equal split)'

    # 🔥 §10x.46 — Layer 1 = ASSET IDENTITY ONLY.
    # No beneficiary info, no testator-share %, no "Confidence: HIGH"
    # text. Those belong to Layer 2 / internal scoring respectively.
    # Layer 1's only job: confirm "this property exists in the will".
    parts = [
        f"### 🏠 Property {seq_num} of {total}",
        f"**{name[:80]}**",
    ]
    bullets = []
    if addr:   bullets.append(f"• **Address:** {addr}")
    if lot:    bullets.append(f"• **Lot/PTD:** {lot}")
    if title:  bullets.append(f"• **Title:** {title}")
    if mukim:  bullets.append(f"• **Mukim:** {mukim}")
    if daerah: bullets.append(f"• **Daerah:** {daerah}")
    if negeri: bullets.append(f"• **Negeri:** {negeri}")
    if own:    bullets.append(f"• **Ownership:** {own}")
    if bullets:
        parts.append('\n'.join(bullets))
    # Note: testator-share % and beneficiary intent were here per §10x.13
    # display, but they belong to Layer 2. Removed per user feedback.

    # No "Confidence: HIGH" label — confidence is internal scoring per
    # §10x.46. Just say what's missing in plain language:
    parts.append(
        "📝 No title document attached yet. Confirm the property — you can "
        "upload the title doc later or type the lot/title manually."
    )
    quick = [
        {'label': '✅ Confirm', 'value': 'inventory h3 confirm'},
        {'label': '📎 Upload title document', 'value': 'inbox start'},
        {'label': '✏️ Type details manually', 'value': 'other'},
        {'label': '⏭ Skip for now', 'value': 'inventory h3 skip'},
    ]
    return {
        'text': '\n\n'.join(parts) + _qr_marker(quick),
        'focus_doc_ids': [],
        '_h3_ai_prop': ai_prop,   # carry through for handler tagging
    }


def _walkthrough_property_card_candidates(ai_prop: Dict[str, Any],
                                            candidates: List[Dict[str, Any]],
                                            doc_groups_by_id: Dict[str, Dict[str, Any]],
                                            seq_num: int, total: int) -> Dict[str, Any]:
    """🔥 §10x.51 / Path Y — Candidate-with-confirm card (§10he Step 4).

    Renders for AssetItems that the matcher couldn't auto-bind but
    found ranked candidates above CANDIDATE_THRESHOLD. The user picks
    one (binds), picks "None" (free-form types), or skips.

    Click format: `inventory match h3 <ai_idx> <doc_id>` where doc_id is
    the first Document.id of the chosen DocGroup.
    """
    name = (ai_prop.get('name') or 'this property').strip()
    addr = (ai_prop.get('address') or '').strip()
    mukim = (ai_prop.get('mukim') or '').strip()
    daerah = (ai_prop.get('daerah') or '').strip()
    negeri = (ai_prop.get('negeri') or '').strip()

    # Strip Layer-2 / parenthetical leakage per §10x.46 R1
    def _strip_parens(s: str) -> str:
        s = re.sub(r'\s*\([^)]*\)\s*', ' ', s or '').strip()
        return re.sub(r'\s+', ' ', s).strip(' ,;.')
    addr = _strip_parens(addr)

    parts = [
        f"### 🏠 Property {seq_num} of {total}",
        f"**{name[:80]}**",
    ]
    bullets = []
    if addr:   bullets.append(f"• **Address:** {addr}")
    if mukim:  bullets.append(f"• **Mukim:** {mukim}")
    if daerah: bullets.append(f"• **Daerah:** {daerah}")
    if negeri: bullets.append(f"• **Negeri:** {negeri}")
    if bullets:
        parts.append('\n'.join(bullets))

    parts.append(
        f"📎 I found {len(candidates)} image(s) you uploaded that may "
        "be the title document for this property:"
    )

    ai_idx = ai_prop.get('_ai_summary_idx')
    if ai_idx is None:
        # Compute from position — caller passes _ai_summary_idx normally
        ai_idx = ai_prop.get('ai_index', 0)

    quick: List[Dict[str, str]] = []
    for i, c in enumerate(candidates[:3], start=1):
        gid = c.get('group_id', '')
        g = doc_groups_by_id.get(gid) or {}
        ge = g.get('merged_extracted') or {}
        doc_ids = g.get('document_ids') or []
        first_doc_id = doc_ids[0] if doc_ids else ''
        # Build a one-line summary of the candidate
        summary_bits = []
        if ge.get('lot_number'):
            summary_bits.append(f"Lot {ge['lot_number']}")
        if ge.get('title_number'):
            summary_bits.append(f"Title {ge['title_number'][:30]}")
        if ge.get('mukim'):
            summary_bits.append(f"Mukim {ge['mukim']}")
        if ge.get('owner_name'):
            owner = (ge['owner_name'] or '').strip()
            if owner:
                summary_bits.append(f"owner: {owner[:40]}")
        summary = ' · '.join(summary_bits) or '(sparse OCR)'
        ocr_snippet = (ge.get('property_address') or '').strip()[:80]
        parts.append(
            f"\n**Candidate {i}** — `{gid[:8]}`\n"
            f"  • {summary}\n"
            + (f"  • OCR address: _{ocr_snippet}_\n" if ocr_snippet else "")
            + f"  • Evidence: {c.get('evidence', '')[:160]}"
        )
        # Button label: short, click-friendly
        btn_label = f"✅ Yes — Candidate {i}"
        if first_doc_id:
            quick.append({
                'label': btn_label,
                'value': f'inventory match h3 {ai_idx} {first_doc_id}',
            })

    quick.append({'label': '✏️ None — type details manually', 'value': 'other'})
    quick.append({'label': '⏭ Skip for now', 'value': 'inventory h3 skip'})

    parts.append(
        "\n_Click the candidate that matches this property, or 'None' "
        "to type the title/lot manually._"
    )
    return {
        'text': '\n\n'.join(parts) + _qr_marker(quick),
        'focus_doc_ids': [],
    }


def _ai_props_already_handled(client_id: str,
                                ai_props: List[Dict[str, Any]],
                                will_data: Dict[str, Any]
                                ) -> List[bool]:
    """Mark which AI-Summary properties are already represented in the
    wizard (step5_data gift, or matched to an inventoried Document).

    Returns parallel-list of booleans len(ai_props).
    """
    out = [False] * len(ai_props)
    if not ai_props:
        return out
    s5 = (will_data or {}).get('step5') or []
    # ── Pass 1: explicit _ai_summary_idx from H3 placeholder saves ──────
    for g in s5:
        if not isinstance(g, dict):
            continue
        if g.get('_ai_summary_skipped') or g.get('_h3_placeholder') or (
            g.get('kind') == 'property' or g.get('asset_type') == 'property'
        ):
            sig = g.get('_ai_summary_idx')
            if isinstance(sig, int) and 0 <= sig < len(out):
                out[sig] = True

    # ── Pass 2: signature match (lot/title/address) ─────────────────────
    handled_sigs = set()
    for g in s5:
        if not isinstance(g, dict):
            continue
        if g.get('kind') == 'property' or g.get('asset_type') == 'property':
            pi = g.get('property_info') or g.get('property_details') or {}
            sig_lot = _digits(pi.get('lot_number') or g.get('lot_number') or '')
            sig_title = _digits(pi.get('title_number') or g.get('title_number') or '')
            sig_addr = re.sub(r'[^a-z0-9]+', '',
                              (pi.get('property_address') or
                               g.get('property_address') or
                               g.get('address') or '').lower())[:40]
            if sig_lot:
                handled_sigs.add(('lot', sig_lot))
            if sig_title:
                handled_sigs.add(('title', sig_title))
            if sig_addr:
                handled_sigs.add(('addr', sig_addr))

    for i, p in enumerate(ai_props):
        if out[i]:
            continue
        plot = _digits(p.get('lot') or '')
        ptit = _digits(p.get('title') or '')
        paddr = re.sub(r'[^a-z0-9]+', '', (p.get('address') or '').lower())[:40]
        if plot and ('lot', plot) in handled_sigs:
            out[i] = True
        elif ptit and ('title', ptit) in handled_sigs:
            out[i] = True
        elif paddr and ('addr', paddr) in handled_sigs:
            out[i] = True

    # ── Pass 3: STRICT classify match against saved gifts ──────────────
    # 🔥 BURN-IN §10x.22 — for synthetic groups (built from saved step5
    # gifts), require a UNIT-LIKE token match — NEVER mark as handled
    # via mere generic-token overlap (e.g. "marina cove" alone is not
    # enough to say C-05-01 is the same as already-saved C-30-08).
    # The H1b "2 generic tokens" path is OK for image groups but too
    # loose for synthetic groups when same building has multiple units.
    synth_groups = []
    for g in s5:
        if not isinstance(g, dict):
            continue
        if not (g.get('kind') == 'property' or g.get('asset_type') == 'property'):
            continue
        pi = g.get('property_info') or g.get('property_details') or {}
        synth_groups.append({
            'document_id': g.get('document_id') or f'_step5_synth_{id(g)}',
            'extracted': {
                'lot_number':       pi.get('lot_number') or g.get('lot_number') or '',
                'title_number':     pi.get('title_number') or g.get('title_number') or '',
                'property_address': pi.get('property_address') or g.get('property_address') or g.get('address') or '',
                'description':      pi.get('description') or g.get('description') or '',
                'property_description': pi.get('property_description') or '',
                'building_name':    pi.get('building_name') or '',
                'township':         pi.get('township') or '',
                'mukim':            pi.get('mukim') or '',
                'daerah':           pi.get('daerah') or '',
                'negeri':           pi.get('negeri') or '',
            },
        })
    claimed = set()
    # §10x.22 — require a UNIT-LIKE token match for synthetic groups.
    # The standard classifier accepts 2 generic tokens (e.g. "marina"
    # + "cove") which would falsely flag distinct units in the same
    # building. We tighten that here: only count synthetic-group H1
    # matches when a unit-number token (e.g. "c-05-01") is shared.
    _unit_re = re.compile(r'^[a-z]?-?\d+[\-/]\d+(?:[\-/]\d+)?$')

    def _ai_unit_tokens(p: Dict[str, Any]) -> set:
        blob = ' '.join([
            (p.get('name') or ''),
            (p.get('address') or ''),
        ]).lower()
        return {m.group(0) for m in re.finditer(
            r'\b[a-z]?-?\d+[\-/]\d+(?:[\-/]\d+)?\b', blob)}

    def _g_unit_tokens(g: Dict[str, Any]) -> set:
        ex = g.get('extracted') or {}
        blob = ' '.join([
            ex.get('property_address', ''),
            ex.get('description', ''),
            ex.get('property_description', ''),
        ]).lower()
        return {m.group(0) for m in re.finditer(
            r'\b[a-z]?-?\d+[\-/]\d+(?:[\-/]\d+)?\b', blob)}

    for i, p in enumerate(ai_props):
        if out[i]:
            continue
        ai_units = _ai_unit_tokens(p)
        for g in synth_groups:
            if g['document_id'] in claimed:
                continue
            g_units = _g_unit_tokens(g)
            # ONLY mark handled when at least one UNIT token matches —
            # generic-token overlap (e.g. "marina cove") is not enough.
            if ai_units and g_units and (ai_units & g_units):
                out[i] = True
                claimed.add(g['document_id'])
                break
            # Fallback: identical lot+title digit-strip
            ex = g.get('extracted') or {}
            g_lot = _digits(ex.get('lot_number') or '')
            g_title = _digits(ex.get('title_number') or '')
            ai_lot = _digits(p.get('lot') or '')
            ai_title = _digits(p.get('title') or '')
            if (ai_lot and g_lot and len(ai_lot) >= 3 and ai_lot == g_lot) \
                    or (ai_title and g_title and len(ai_title) >= 4 and ai_title == g_title):
                out[i] = True
                claimed.add(g['document_id'])
                break
    return out


def _next_step_cta(will_data: dict) -> dict:
    """Return {'label': str, 'value': str} for the ▶️ next-step button.

    🔥 §7 — STEP 1 IDENTITY ALWAYS COMES FIRST.
    🔥 §10x.53 — When ANY Document is still 'chat_inbox' (vision
    classification in progress), return a non-actionable status label
    instead of the start button. The chat watchdog will refresh and
    the real button will appear once classification completes.
    """
    # 🔥 §10x.53 — gate while analysing
    client_id = (will_data or {}).get('client_id') or ''
    if client_id:
        try:
            from database import Document as _Doc
            in_progress = _Doc.query.filter_by(
                client_id=client_id, category='chat_inbox'
            ).count()
            if in_progress > 0:
                return {
                    'label': f'🔍 Analysing {in_progress} exhibit(s) — please wait',
                    'value': 'inbox start',   # click intercepted by guard handler
                }
        except Exception:
            pass

    if not will_data:
        return {'label': '▶️ Start — verify identities', 'value': 'inbox start'}

    s1         = will_data.get('step1') or {}
    s2         = will_data.get('step2') or {}
    s4         = will_data.get('step4') or []
    completed  = will_data.get('completed_steps') or []
    pg         = will_data.get('pending_gifts') or {}
    identities = will_data.get('identities') or []
    client_id  = will_data.get('client_id') or ''

    pending_ics = [i for i in identities
                   if i.get('kind') == 'nric' and not i.get('confirmed')]

    # 🔒 Also check the REAL pending IC documents (Document.category='nric'
    # not yet linked to a Person). The will_data.identities list is only
    # populated AFTER the user confirms ICs in Step 1. Before that, it's
    # empty — but actual pending IC docs still exist in the Document
    # table. Without this check the CTA wrongly skips Step 1.
    if not pending_ics and client_id:
        try:
            from services.identity_walker import get_pending_ic_documents
            doc_pending = get_pending_ic_documents(client_id) or []
            if doc_pending:
                pending_ics = doc_pending   # treat as Step 1 still pending
        except Exception:
            pass

    # Step 1: identity documents to match
    if pending_ics:
        return {'label': '▶️ Start — verify identities', 'value': 'inbox start'}

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


def _identity_question(pending_ics: List[Dict[str, Any]], recent_text: str,
                        client_id: str = '') -> str:
    """Ask about the next pending IC, with role pre-deduced from message.

    🔥 §10x.34 — H3 identity placeholders (named in message, no IC uploaded)
    take an explicit Confirm card with the family-role suggested directly
    from the AI Summary parser. No IC photo to show.
    """
    next_ic = pending_ics[0]
    # ── §10x.34 — H3 placeholder branch (no IC uploaded)
    if next_ic.get('_h3_placeholder'):
        ex = next_ic.get('extracted') or {}
        name = (ex.get('full_name') or '').strip()
        role = next_ic.get('_h3_role') or 'family member'
        parts = [
            f"### 👤 Step 1: Identity ({len(pending_ics)} left)",
            f"**{name}** — _no IC uploaded yet_",
            f"📨 _Mentioned in your message as_ **{role}**.",
            f"⚠️ Their IC photo can be uploaded later — for now, "
            f"confirm the relationship so the will can name them.",
        ]
        quick = [
            {'label': f"✓ Yes — {role}", 'value': 'yes'},
            {'label': '📎 Upload IC photo', 'value': 'upload-ic'},
            {'label': '🗑 Delete', 'value': 'delete'},
        ]
        return '\n\n'.join(parts) + _qr_marker(quick)

    return _identity_question_with_doc(pending_ics, recent_text, client_id)


def _identity_question_with_doc(pending_ics: List[Dict[str, Any]], recent_text: str,
                                  client_id: str = '') -> str:
    """Original IC-doc walkthrough card. Signal sources:
      (a) role_deducer (name verbatim in text)
      (b) role_matcher outsider-elimination (§10x.21)
    """
    next_ic = pending_ics[0]
    ex = next_ic['extracted'] or {}
    name = (ex.get('full_name') or '').strip() or '(name unreadable)'
    nric = (ex.get('nric_number') or '').strip() or 'NRIC unreadable'

    # ── (a) Name-verbatim match ───────────────────────────────────────
    deduction = None
    snippet = ''
    if name and name != '(name unreadable)' and recent_text:
        try:
            from ai.role_deducer import deduce_roles
            deductions = deduce_roles(recent_text, [name])
            deduction = deductions.get(name)
            if deduction:
                snippet = deduction.get('evidence', '')
        except Exception:
            deduction = None

    # ── (b) Outsider-elimination via role_matcher (§10x.21) ───────────
    # If this IC is the only candidate that ISN'T a family member named
    # in the message, suggest the executor's family-relation directly.
    out_role = None
    out_snippet = ''
    if not deduction and client_id:
        try:
            from services.role_matcher import (extract_role_mentions,
                                                find_unassigned_ic_candidates,
                                                match_role_to_candidates)
            mentions = extract_role_mentions(client_id) or []
            cands = find_unassigned_ic_candidates(client_id) or []
            # Find a HIGH-confidence outsider match for THIS pending IC
            this_doc_id = next_ic.get('document_id') or next_ic.get('id') or ''
            this_nric_digits = ''.join(ch for ch in nric if ch.isdigit())
            for m in mentions:
                ranked = match_role_to_candidates(m, cands, client_id=client_id)
                for c, conf, reason in ranked:
                    if conf != 'high':
                        continue
                    cnric = ''.join(ch for ch in (c.get('nric') or '') if ch.isdigit())
                    if (c.get('document_id') == this_doc_id
                            or (cnric and this_nric_digits and cnric == this_nric_digits)):
                        out_role = m.get('family_relation') or 'sister-in-law'
                        out_snippet = m.get('evidence_snippet', '')[:200]
                        break
                if out_role:
                    break
        except Exception:
            out_role = None

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
    elif out_role:
        # 🔥 §10x.21 + §9 — show the message snippet as evidence
        evidence_block = (f"\n\n📨 _from your message:_ \"{out_snippet}\"") if out_snippet else ''
        parts.append(
            f"Looks like your **{out_role}** (the only IC name NOT in "
            f"your immediate family list).{evidence_block}\n\nConfirm?"
        )
        quick = [
            {'label': f"✓ Yes — {out_role}", 'value': 'yes'},
            {'label': 'Spouse', 'value': 'spouse'},
            {'label': 'Sister', 'value': 'sister'},
            {'label': 'Brother', 'value': 'brother'},
            {'label': 'Friend', 'value': 'friend'},
            {'label': 'Skip', 'value': 'skip'},
            {'label': 'Delete', 'value': 'delete'},
        ]
    else:
        parts.append("**Relationship to testator?**\n_(Executor / Trustee / Guardian roles are set in later steps)_")
        # 🔥 §10x.30 — buttons now include in-laws (sister-in-law /
        # brother-in-law / mother-in-law / father-in-law). Earlier list
        # forced users to use "✏️ None of above" for these very common
        # relationships.
        quick = [
            {'label': 'Spouse', 'value': 'spouse'},
            {'label': 'Son', 'value': 'son'},
            {'label': 'Daughter', 'value': 'daughter'},
            {'label': 'Father', 'value': 'father'},
            {'label': 'Mother', 'value': 'mother'},
            {'label': 'Brother', 'value': 'brother'},
            {'label': 'Sister', 'value': 'sister'},
            {'label': 'Sister-in-law', 'value': 'sister in law'},
            {'label': 'Brother-in-law', 'value': 'brother in law'},
            {'label': 'Son-in-law', 'value': 'son in law'},
            {'label': 'Daughter-in-law', 'value': 'daughter in law'},
            {'label': 'Mother-in-law', 'value': 'mother in law'},
            {'label': 'Father-in-law', 'value': 'father in law'},
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


def _walkthrough_text_image_conflict_card(conflict: Dict[str, Any]) -> Dict[str, Any]:
    """🔥 BURN-IN §10x.18 — when text-stated and image-OCR'd identifiers
    disagree, ASK the user which is correct. Don't auto-pick."""
    asset = conflict.get('asset_label', 'an asset')
    field = conflict.get('field_label', conflict.get('field', 'field'))
    text_v = conflict.get('text_value', '')
    image_v = conflict.get('image_value', '')

    parts = [
        f"### ⚠️ Mismatch — please verify {asset}",
        f"For **{field}**:",
        f"  📝 You said: **{text_v}**",
        f"  📎 Image shows: **{image_v}**",
        "These don't match. Which is correct?",
    ]
    quick = [
        {'label': f"📝 Use what I said ({text_v[:30]})",
         'value': f'mismatch use_text {conflict["gift_idx"]} {conflict["field"]}'},
        {'label': f"📎 Use what the image shows ({image_v[:30]})",
         'value': f'mismatch use_image {conflict["gift_idx"]} {conflict["field"]}'},
        {'label': '✏️ Type the correct value',
         'value': f'mismatch type_manually {conflict["gift_idx"]} {conflict["field"]}'},
        {'label': '🗑 Wrong upload — remove image',
         'value': f'mismatch remove_image {conflict["gift_idx"]}'},
    ]
    return {
        'text': '\n\n'.join(parts) + _qr_marker(quick),
        'focus_doc_id': conflict.get('document_id'),
    }


def _walkthrough_role_match_card(role_mention: Dict[str, Any],
                                   ranked_candidates: List[Tuple[Dict[str, Any], str, str]]
                                   ) -> Dict[str, Any]:
    """🔥 BURN-IN §10x.21 — render the executor/witness/etc. role-matching
    card. The user picks which uploaded IC photo is the named role-bearer
    OR types a name manually OR skips."""
    fam = role_mention.get('family_relation') or 'someone'
    fam = fam.replace('-', ' ').title()
    phone = role_mention.get('phone', '')
    evidence = role_mention.get('evidence_snippet', '')
    role_label = role_mention.get('role', 'executor').title()

    parts = [
        f"### ⚖️ Step 3: Confirm {role_label} — your {fam.lower()}",
        f"📨 **From your message:**",
        f"> _{evidence[:200]}_",
    ]
    if phone:
        parts.append(f"📞 **Phone:** {phone}")

    quick: List[Dict[str, str]] = []
    high = [(c, conf, reason) for (c, conf, reason) in ranked_candidates if conf == 'high']
    others = [(c, conf, reason) for (c, conf, reason) in ranked_candidates if conf != 'high']
    if high:
        c, _, reason = high[0]
        parts.append(
            f"🔍 **Best match:** {c['full_name']} (NRIC {c['nric']})\n"
            f"_Reason: {reason}_"
        )
        quick.append({
            'label': f"✅ Confirm — {c['full_name']}",
            'value': f"role_match confirm {c['person_id']}",
        })
        for c2, _, _ in others[:3]:
            quick.append({
                'label': f"👤 {c2['full_name']} instead",
                'value': f"role_match confirm {c2['person_id']}",
            })
    elif others:
        parts.append(
            f"📂 **Unassigned ICs in your uploads** — pick the one that's "
            f"your {fam.lower()}:"
        )
        for c, _, _ in others[:5]:
            quick.append({
                'label': f"👤 {c['full_name']}",
                'value': f"role_match confirm {c['person_id']}",
            })
    else:
        parts.append(
            "_No unassigned ICs found in your uploads. Type her full name "
            "+ NRIC manually or upload her IC photo first._"
        )
    quick.append({'label': '✏️ Type name + IC manually', 'value': 'role_match manual'})
    quick.append({'label': '⏭ Skip — fill later',        'value': 'role_match skip'})

    return {
        'text': '\n\n'.join(parts) + _qr_marker(quick),
        'focus_doc_id': (high[0][0]['document_id'] if high
                         else (others[0][0]['document_id'] if others else None)),
    }


def _step3_executor_question(will_data: Dict[str, Any], recent_text: str = '') -> Dict[str, Any]:
    """Returns {text, focus_doc_id} — the question to ask + which IC photo
    to attach. Walks main → substitute executor based on what's already
    saved in step2_data.executors.

    🔥 BURN-IN §10x.21 — if the message names an executor by ROLE only
    (e.g. "my sister-in-law Tel: +6016-...") AND there's an unassigned IC
    in the uploads, surface the role-match card BEFORE the generic picker.
    The user picks which IC corresponds to the role-mention.
    """
    identities = will_data.get('identities') or []
    s2 = will_data.get('step2') or {}
    executors = s2.get('executors') or []
    n_done = len(executors)
    role = 'main' if n_done == 0 else 'substitute'

    # ── §10x.21 role-match — applies only to MAIN executor (not substitute)
    if role == 'main':
        try:
            from services.role_matcher import (
                extract_role_mentions, find_unassigned_ic_candidates,
                match_role_to_candidates,
            )
            cid = (will_data or {}).get('client_id') or ''
            if cid:
                mentions = extract_role_mentions(cid)
                # Find an executor mention without a real name yet
                exec_mentions = [m for m in mentions
                                 if m.get('role') == 'executor'
                                 and not m.get('partial_name')]
                if exec_mentions:
                    candidates = find_unassigned_ic_candidates(cid)
                    if candidates:
                        ranked = match_role_to_candidates(exec_mentions[0], candidates)
                        if ranked:
                            return _walkthrough_role_match_card(exec_mentions[0], ranked)
        except Exception:
            pass   # fall through to standard picker

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
        # 🔥 §10x.45 UI — terse, single-line warnings. Verbose probate
        # explanations were cluttering every property card.
        if addr:
            warnings.append(
                f"⚠️ Missing: {', '.join(missing_critical)} — request a "
                f"clearer Geran/Hakmilik scan for probate filing."
            )
        else:
            warnings.append(
                "⚠️ Address AND title both blank — re-OCR or request a clearer scan."
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

    # ╔════════════════════════════════════════════════════════════════════╗
    # ║  🔥 BURN-IN §10hg — CONFLICT CARD GATE                              ║
    # ║  If the AI Summary contains contradictions (duplicate address,      ║
    # ║  shares >100%, etc.), surface the clarification card BEFORE walking ║
    # ║  any property. Walkthrough does not advance until clarified.        ║
    # ╚════════════════════════════════════════════════════════════════════╝
    _client_id = (will_data or {}).get('client_id') or ''
    _ai_props = _extract_ai_summary_properties(_client_id) if _client_id else []
    if _ai_props:
        _conflicts = _detect_message_conflicts(_ai_props)
        # Only show the FIRST unresolved conflict per turn — user clarifies
        # via chat reply, then next turn re-checks.
        _resolved_marker = (will_data or {}).get('completed_steps') or []
        _conflict_resolved = any(
            isinstance(c, str) and c.startswith('conflict_')
            for c in _resolved_marker
        )
        if _conflicts and not _conflict_resolved:
            return _walkthrough_conflict_card(_conflicts[0])

    if props:
        target = props[0]
        # 🔥 §10hg / §10x.23 — H3 placeholders MUST go to the H3 Layer 1
        # confirm card. Earlier the code always called
        # _walkthrough_property_card (image-bound layout) which rendered
        # the wrong card and skipped the Confirm-Asset semantics.
        if target.get('_h3_placeholder'):
            ai_match = target.get('_ai_summary_match') or {}
            # Compute sequence number across all property positions
            try:
                from services.gift_walker import get_pending_gift_documents as _gpd
                pg = _gpd(_client_id) or {}
                total = len(pg.get('property') or []) or 1
                # this index = first H3 in props (this `target`)
                seq = 1
                for i, pp in enumerate(pg.get('property') or []):
                    if pp is target:
                        seq = i + 1
                        break
            except Exception:
                seq = 1
                total = len(props)
            return _walkthrough_property_card_h3(ai_match, seq, total)
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
    # ╔════════════════════════════════════════════════════════════════════╗
    # ║  🔥 BURN-IN §10hg — H3 PLACEHOLDER CARDS                            ║
    # ║  After all image-derived property groups are reviewed, surface any  ║
    # ║  AI-Summary properties that have NO matching image as H3 placeholder║
    # ║  cards. The canonical N MUST be reached even if the user attached    ║
    # ║  no title doc for some properties.                                   ║
    # ╚════════════════════════════════════════════════════════════════════╝
    if _ai_props:
        _handled = _ai_props_already_handled(_client_id, _ai_props, will_data or {})
        # Greedy-claim: each image group binds to AT MOST one AI prop.
        # Iterate ai_props in order; first match wins; later props that
        # would have matched the same group fall through to H3.
        _claimed_doc_ids = set()
        _matched_to_image: List[bool] = []
        for ap in _ai_props:
            available = [g for g in all_props
                         if g.get('document_id') not in _claimed_doc_ids]
            cls = _classify_property_match(ap, available)
            if cls['variant'] in ('h1', 'h2') and cls.get('group'):
                _matched_to_image.append(True)
                _claimed_doc_ids.add(cls['group'].get('document_id'))
            else:
                _matched_to_image.append(False)
        # H3 = AI-Summary entry, not handled, not matched to any image group
        h3_idx = [i for i, ap in enumerate(_ai_props)
                  if not _handled[i] and not _matched_to_image[i]]
        if h3_idx:
            i = h3_idx[0]
            seq = sum(1 for h in _handled if h) + 1
            total = len(_ai_props)
            # 🔥 §10x.51 Path Y — before falling to H3 placeholder, check
            # whether the unified scorer found candidates for this AssetItem.
            # If yes, render a candidate-with-confirm card so the user can
            # bind a matching image instead of typing details from scratch.
            candidates = []
            doc_groups_by_id: Dict[str, Dict[str, Any]] = {}
            try:
                from services.asset_pipeline import run_pipeline
                _r = run_pipeline(_client_id) if _client_id else {}
                cfc = _r.get('candidates_for_confirm') or {}
                # Match by ai_index (pipeline's index) — same i because both
                # sources iterate _extract_ai_summary_properties in order.
                candidates = cfc.get(i) or []
                doc_groups_by_id = {g['group_id']: g for g in (_r.get('doc_groups') or [])}
            except Exception:
                candidates = []
            ap = dict(_ai_props[i])
            ap['_ai_summary_idx'] = i   # tag so card builder has it
            if candidates:
                return _walkthrough_property_card_candidates(
                    ap, candidates, doc_groups_by_id, seq, total
                )
            return _walkthrough_property_card_h3(_ai_props[i], seq, total)

    # ╔═════════════════════════════════════════════════════════════╗
    # ║ 🔥 BURN-IN §10x.12 — AI-Summary banks + insurance per item    ║
    # ║ Walk through every bank account / insurance policy mentioned ║
    # ║ in the user's WhatsApp forward. Each one = its own gift.      ║
    # ╚═════════════════════════════════════════════════════════════╝
    if _client_id:
        try:
            _ai_banks = _extract_ai_summary_banks(_client_id)
        except Exception:
            _ai_banks = []
        try:
            _ai_ins = _extract_ai_summary_insurance(_client_id)
        except Exception:
            _ai_ins = []
        s5 = (will_data or {}).get('step5') or []
        # Track which banks/insurance are already in step5_data (by acct/policy num)
        _saved_acct = set()
        _saved_policy = set()
        for g in s5:
            if not isinstance(g, dict):
                continue
            an = (g.get('account_number')
                  or (g.get('property_info') or {}).get('account_no')
                  or (g.get('property_details') or {}).get('account_no')
                  or '').strip()
            if an:
                _saved_acct.add(re.sub(r'\W+', '', an))
            pn = (g.get('policy_number') or '').strip()
            if pn:
                _saved_policy.add(re.sub(r'\W+', '', pn))
        # 🔥 §10x.23 — 3-layer flow for banks. Per asset:
        #   Layer 1 (no entry yet)        → render Layer 1 confirm card
        #   Layer 2 (no beneficiaries yet) → render main-beneficiary card
        #   Layer 3 (no substitute yet)    → render substitute card
        identities_for_l = (will_data or {}).get('identities') or []
        # Find the FIRST bank that's incomplete in any layer.
        # Build map of saved bank gifts by account number.
        saved_bank_by_acct = {}
        for g in s5:
            if not isinstance(g, dict): continue
            if g.get('kind') != 'bank': continue
            ak = re.sub(r'\W+', '', g.get('account_number') or '')
            if ak: saved_bank_by_acct[ak] = g
        for i, b in enumerate(_ai_banks):
            ak = re.sub(r'\W+', '', b.get('account_number') or '')
            done = sum(1 for x in _ai_banks
                       if re.sub(r'\W+', '', x.get('account_number') or '') in saved_bank_by_acct
                       and (saved_bank_by_acct.get(re.sub(r'\W+', '', x.get('account_number') or '')) or {}).get('substitute_specific') is not None)
            seq = i + 1
            total = len(_ai_banks)
            saved = saved_bank_by_acct.get(ak)
            if not saved or saved.get('skipped') or saved.get('_user_rejected'):
                if not saved:
                    return _walkthrough_bank_layer1_card(b, seq, total)
                continue   # already skipped/removed
            # Layer 1 done — check Layer 2
            bens = saved.get('beneficiaries') or []
            if not bens:
                return _walkthrough_bank_layer2_card(saved, identities_for_l)
            # Layer 2 done — check Layer 3
            sub = saved.get('substitute_specific')
            mode = saved.get('substitute_mode')
            if sub is None and mode in (None, ''):
                return _walkthrough_bank_layer3_card(saved, identities_for_l)
            # Fully complete — move on
            continue

        # Same 3-layer flow for insurance
        saved_ins_by_pol = {}
        for g in s5:
            if not isinstance(g, dict): continue
            if g.get('kind') != 'insurance': continue
            pn = re.sub(r'\W+', '', g.get('policy_number') or '')
            if pn: saved_ins_by_pol[pn] = g
        for i, ins in enumerate(_ai_ins):
            pn = re.sub(r'\W+', '', ins.get('policy_number') or '')
            seq = i + 1
            total = len(_ai_ins)
            saved = saved_ins_by_pol.get(pn)
            if not saved or saved.get('skipped') or saved.get('_user_rejected'):
                if not saved:
                    return _walkthrough_insurance_layer1_card(ins, seq, total)
                continue
            bens = saved.get('beneficiaries') or []
            if not bens:
                return _walkthrough_insurance_layer2_card(saved, identities_for_l)
            sub = saved.get('substitute_specific')
            mode = saved.get('substitute_mode')
            if sub is None and mode in (None, ''):
                return _walkthrough_insurance_layer3_card(saved, identities_for_l)
            continue

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

    # 🔥 §10x.45 UI — compact one-line registry summary instead of a
    # 5-bullet block. Skips fields that are empty.
    nlc_inline = []
    for label, key in (
        ('Title',  'title_number'),
        ('Lot',    'lot_number'),
        ('Mukim',  'mukim'),
        ('Daerah', 'daerah'),
        ('Negeri', 'negeri'),
    ):
        v = (ex.get(key) or '').strip()
        if v:
            nlc_inline.append(f"**{label}** {v}")
    if nlc_inline:
        parts.append("📋 " + ' · '.join(nlc_inline))

    # ── 🔥 BURN-IN — TWO-HINT EVIDENCE BLOCK (§10hb) ─────────────────────
    # When the matcher used the two-hint test (same mukim + close timing)
    # to bind this image to its AI-Summary property, surface BOTH hints
    # so the user can verify. Source citations come from
    # `validate_matches_with_web_clues` which writes `_resolved_mukim`,
    # `_hint1_mukim_ok`, `_clue_status`, `_clue_sources` onto extracted.
    _resolved_mukim = (ex.get('_resolved_mukim') or '').strip()
    _hint1_ok = ex.get('_hint1_mukim_ok')
    _clue_status = (ex.get('_clue_status') or '').strip()
    _clue_sources = ex.get('_clue_sources') or []
    _msg_ts = (ex.get('_msg_timestamp') or '').strip()
    _addr_ts = (ex.get('_address_msg_timestamp') or '').strip()
    has_hint_evidence = bool(_resolved_mukim or _hint1_ok is not None
                             or _clue_status or _msg_ts)
    if has_hint_evidence:
        hint_lines = ["🔗 **Match evidence:**"]
        # Hint 1 — mukim
        doc_mukim = (ex.get('mukim') or '').strip()
        if _hint1_ok is True:
            hint_lines.append(
                f"  • 🌍 **Hint 1 — mukim:** ✅ `{doc_mukim}` matches resolved "
                f"`{_resolved_mukim}`"
            )
        elif _hint1_ok is False:
            hint_lines.append(
                f"  • 🌍 **Hint 1 — mukim:** ⚠️ doc says `{doc_mukim}`, "
                f"resolved `{_resolved_mukim}` — please verify"
            )
        elif _resolved_mukim:
            hint_lines.append(
                f"  • 🌍 **Hint 1 — mukim:** ℹ️ resolved to `{_resolved_mukim}` "
                f"(no doc-side mukim to compare)"
            )
        # Hint 2 — timing
        if _msg_ts and _addr_ts:
            hint_lines.append(
                f"  • ⏱  **Hint 2 — timing:**\n"
                f"      📎 Image  `[{_msg_ts}]`\n"
                f"      💬 Msg   `[{_addr_ts}]`"
            )
        elif _msg_ts:
            hint_lines.append(f"  • ⏱  **Hint 2 — image timestamp:** `[{_msg_ts}]`")
        # Clue validation status
        if _clue_status == 'compatible':
            hint_lines.append("  • ✅ **Web-search clues:** type/tenure compatible")
        elif _clue_status == 'incompatible':
            hint_lines.append(
                "  • ⚠️ **Web-search clues:** doc looks INCOMPATIBLE with "
                "what the address resolves to — please verify"
            )
        elif _clue_status == 'address_not_found':
            hint_lines.append(
                "  • ℹ️ **Web-search clues:** address could not be resolved online"
            )
        if _clue_sources:
            srcs = [s for s in _clue_sources[:3] if s]
            if srcs:
                hint_lines.append(
                    "  • 🔎 _Sources:_ " + ", ".join(f"<{s}>" for s in srcs)
                )
        parts.append("\n".join(hint_lines))

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
            # 🔥 §10x.13 — beneficiary % may be of testator's SHARE (not full
            # property). For a 50/50-jointly-owned property, "Joshua 25%
            # Esther 25%" sums to 50% of FULL = 100% of testator's share.
            # Accept any total in {25, 33, 50, 66, 75, 100} as a valid
            # share-of-testator-interest; normalise to 100% relative shares.
            if total in (25, 33, 50, 66, 67, 75):
                # Rescale to 100% share-of-testator's-interest
                _scale = 100.0 / total
                for d in deduced:
                    try:
                        d['share'] = f"{int(round(int(d['share'].rstrip('%')) * _scale))}%"
                    except Exception:
                        pass
                total = 100
            if total != 100:
                deduced = []

    # 🔥 §10x.45 — compute progress across ALL gift kinds (not just
    # properties) so the user sees TRUE walkthrough position.
    _client_id_local = (will_data or {}).get('client_id') or ''
    n_total_pending = len(pending_props)
    progress_suffix = ''
    if _client_id_local:
        try:
            from services.gift_walker import get_pending_gift_documents as _gpd
            pg = _gpd(_client_id_local) or {}
            n_props_total = len(pg.get('property') or [])
            n_banks_total = len(pg.get('bank') or [])
            n_ins_total   = len(pg.get('insurance') or [])
            n_total_all = n_props_total + n_banks_total + n_ins_total
            this_idx = max(1, n_props_total - len(pending_props) + 1)
            progress_suffix = (
                f" — Property {this_idx} of {n_props_total}"
                f" ({n_total_all} total: {n_props_total} props · "
                f"{n_banks_total} banks · {n_ins_total} insurance)")
        except Exception:
            progress_suffix = f" — {len(pending_props)} property left"

    quick: List[Dict[str, str]] = []
    # 🔥 §10x.45 — header carries position; body skips re-stating address
    # (property name already in `formatted` below).
    parts = [
        f"### 🏠 Specific Gift{progress_suffix}",
        formatted,
    ]

    # 🔥 §10x.36 / §10x.35 / §9 — ALWAYS show the message line that names
    # this property so the user can see what they wrote about it.
    msg_snippet = _find_property_message_snippet(p, recent_text or '')
    if msg_snippet:
        parts.append(f"📨 _from your message:_\n> {msg_snippet}")

    if evidence_block:
        parts.append(f"**📎 Based on these uploads:**\n{evidence_block}")

    # 🔥 §10x.40 — Confidence-driven beneficiary buttons.
    # HIGH    : ONE pre-suggested button + manual override + skip/remove.
    # MEDIUM  : 3 alternative distributions (top suggestion + 2 alternates).
    # LOW     : 3 distribution options (no auto-suggestion possible).
    # User MUST always confirm — we NEVER auto-save.
    confidence = 'low'
    if deduced:
        deduced_names = {d['name'].upper() for d in deduced}
        candidate_set = {c.upper() for c in candidates}
        # HIGH: every deduced beneficiary is in our candidate list AND
        # the total share is exactly 100% (after testator-share rescale)
        all_in_candidates = deduced_names.issubset(candidate_set)
        try:
            total = sum(int(d['share'].rstrip('%')) for d in deduced)
        except Exception:
            total = 0
        if all_in_candidates and total == 100:
            confidence = 'high'
        else:
            confidence = 'medium'

    if confidence == 'high':
        primary_value = ', '.join(f"{d['name']} {d['share']}" for d in deduced)
        primary_label = '✓ Confirm — ' + ', '.join(
            f"{d['name'].title()} {d['share']}" for d in deduced)
        ev_lines = '\n'.join(f"  • _{d['evidence']}_" for d in deduced)
        parts.append(
            f"🎯 **HIGH confidence** — your message clearly states:\n"
            f"{ev_lines}\n\n"
            f"_Click Confirm to save this distribution. You can still "
            f"override with a different split if needed._")
        quick.append({'label': primary_label, 'value': primary_value})
        quick.append({'label': '✏️ Different — type manually',
                      'value': 'manual'})
        quick.append({'label': '⏭ Skip this gift', 'value': 'skip'})
        quick.append({'label': '🗑 Remove', 'value': 'delete'})

    elif confidence == 'medium':
        # Show suggested + 2 alternates
        primary_value = ', '.join(f"{d['name']} {d['share']}" for d in deduced)
        primary_label = '⭐ ' + ', '.join(
            f"{d['name'].title()} {d['share']}" for d in deduced) + ' (suggested)'
        ev_lines = '\n'.join(f"  • _{d['evidence']}_" for d in deduced)
        parts.append(
            f"⚠️ **MEDIUM confidence** — partial match from your message:\n"
            f"{ev_lines}\n\n"
            f"_Pick the option that matches your intent — confirm before "
            f"we save._")
        quick.append({'label': primary_label, 'value': primary_value})
        # Alt 1: equal split between top 2 candidates
        if len(candidates) >= 2:
            a, b = candidates[0], candidates[1]
            quick.append({'label': f"{a.title()} 50% + {b.title()} 50% (equal)",
                          'value': f"{a} 50%, {b} 50%"})
        # Alt 2: 100% to first non-deduced candidate
        deduced_upper = {d['name'].upper() for d in deduced}
        for n in candidates:
            if n.upper() not in deduced_upper:
                quick.append({'label': f"{n.title()} 100%",
                              'value': f"{n} 100%"})
                break
        quick.append({'label': '✏️ Type manually', 'value': 'manual'})
        quick.append({'label': '⏭ Skip', 'value': 'skip'})
        quick.append({'label': '🗑 Remove', 'value': 'delete'})

    else:   # LOW — no clean deduction
        parts.append(
            "🤔 **No clear distribution in your message** for this "
            "property. Pick the most likely option — your confirmation "
            "is required before we save:")
        if len(candidates) >= 2:
            a, b = candidates[0], candidates[1]
            quick.append({'label': f"{a.title()} 50% + {b.title()} 50% (equal)",
                          'value': f"{a} 50%, {b} 50%"})
            quick.append({'label': f"{a.title()} 100%",
                          'value': f"{a} 100%"})
            quick.append({'label': f"{b.title()} 100%",
                          'value': f"{b} 100%"})
        elif candidates:
            quick.append({'label': f"{candidates[0].title()} 100%",
                          'value': f"{candidates[0]} 100%"})
        quick.append({'label': '✏️ Type manually', 'value': 'manual'})
        quick.append({'label': '⏭ Skip', 'value': 'skip'})
        quick.append({'label': '🗑 Remove', 'value': 'delete'})

    text = '\n\n'.join(parts) + _qr_marker(quick)
    return {'text': text, 'focus_doc_id': p.get('document_id')}


def _compute_next_step_label(will_data: Dict[str, Any]) -> str:
    """🔥 §7 / §10x.38 — Returns the human-readable label for the NEXT
    step the planner will land on. MUST match what plan_turn actually
    shows, otherwise the "moving to Step X" message lies to the user.
    """
    if not will_data:
        return 'Step 2: Testator Info'
    s1 = will_data.get('step1') or {}
    s2 = will_data.get('step2') or {}
    s4 = will_data.get('step4') or []
    completed = will_data.get('completed_steps') or []
    client_id = will_data.get('client_id') or ''

    # Step 2 — testator confirm (skip if already confirmed)
    if not _is_confirmed(will_data, 'testator'):
        return 'Step 2: Confirm Testator'

    # 🔥 §10x.38 / §10x.55 — pending gifts run BEFORE executor in the
    # planner because the asset walkthrough fires first whenever
    # pending_gifts > 0 (line 232 in plan_turn). The label MUST match
    # what's actually rendered or the user sees a "Now moving to Step 3:
    # Executor" message followed by a Step 6 property card.
    if 'assets_confirmed' not in completed:
        # Need to check if any gifts are actually pending (not just placeholder)
        if client_id:
            try:
                from services.gift_walker import get_pending_gift_documents
                pg = get_pending_gift_documents(client_id) or {}
                if sum(len(v) for v in pg.values()
                        if isinstance(v, list)) > 0:
                    return 'Step 6: Specific Gifts'
            except Exception:
                pass

    # Step 3 — executors
    n_exec = len((s2.get('executors') or []))
    if n_exec < 1:
        return 'Step 3: Executor'
    # Step 4 — guardians (skip if no minor children declared)
    # (We don't track guardian-needed flag here — best-effort label only)
    # Step 5 — beneficiaries
    if not s4:
        return 'Step 5: Beneficiaries'
    # Step 6 — specific gifts walkthrough
    if 'assets_confirmed' not in completed:
        return 'Step 6: Specific Gifts'
    if client_id:
        try:
            from services.gift_walker import get_pending_gift_documents
            pg = get_pending_gift_documents(client_id) or {}
            if sum(len(v) for v in pg.values()
                    if isinstance(v, list)) > 0:
                return 'Step 6: Specific Gifts'
        except Exception:
            pass
    # Step 7 — residuary
    s6 = will_data.get('step6') or {}
    if not s6.get('beneficiaries'):
        return 'Step 7: Residuary Estate'
    return 'Step 10: Generate Will'


def _find_property_message_snippet(prop_dict: Dict[str, Any],
                                    recent_text: str) -> str:
    """🔥 §10x.36 — for any property card, find the message line that
    names this property's beneficiary distribution. Returns at most one
    sentence-ish snippet (≤200 chars).

    Strategy:
      1. Pull distinctive locality tokens from the property (B-05-11,
         Marina Cove, Sri Laguna, etc.)
      2. Find the FIRST line in recent_text containing any token
      3. Return that line + a few following lines (until next property
         or until 200 chars)
    """
    if not recent_text:
        return ''
    ex = (prop_dict.get('extracted') or {}) if prop_dict else {}
    addr = (ex.get('property_address') or '').strip()
    name = (prop_dict.get('name') or '').strip()
    candidates = (addr + ' ' + name).strip()
    if not candidates:
        return ''
    # Build search tokens: 4+ char tokens that aren't generic stopwords
    STOP = {'JALAN', 'TAMAN', 'BANDAR', 'KAMPUNG', 'UNIT', 'BLOCK',
             'BLOK', 'NO', 'JOHOR', 'BAHRU', 'KUALA', 'LUMPUR',
             'CONDOMINIUM', 'APARTMENT', 'PERSIARAN', 'LORONG',
             'LEBUH', 'MUKIM', 'DAERAH', 'NEGERI', 'STATE', 'MALAYSIA',
             'PHASE', 'WITH', 'KAWASAN', 'PERUSAHAAN'}
    toks = [t for t in re.findall(r"[A-Za-z0-9\-]{4,}", candidates.upper())
            if t not in STOP]
    # Also include unit numbers (B-05-11, C-30-08 etc.)
    unit_pat = re.findall(r"[A-Z]\-\d{1,3}\-\d{1,3}|\d+\-\d+\-\d+",
                           candidates.upper())
    toks.extend(unit_pat)
    if not toks:
        return ''
    # Search line by line. Newlines OR period boundaries.
    lines = re.split(r'(?:\r?\n|\.\s+)', recent_text)
    best_line = ''
    best_score = 0
    for L in lines:
        L_up = L.upper()
        score = sum(1 for t in toks if t in L_up)
        if score > best_score:
            best_score = score
            best_line = L
    if not best_line:
        return ''
    # Trim and bound length
    snippet = best_line.strip()
    if len(snippet) > 240:
        snippet = snippet[:237] + '…'
    return snippet


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
