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
            reply_parts.append(
                f"✅ **{just_assigned.get('name','')}** queued for the wizard. "
                "Next asset:"
            )
        elif just_kind.startswith('inventory_skipped_'):
            reply_parts.append(
                f"⏭ Skipped **{just_assigned.get('name','')}**. Next asset:"
            )
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
        reply_parts.append(_intake_summary(artifacts))

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
        reply_parts.append(
            "✅ Specific gifts done. Moving to **Step 7: Residuary Estate**.\n\n"
            "After the specific gifts above, who should inherit **the rest of your estate** "
            "(everything not specifically given away)?\n\n"
            "Reply with name + optional share, e.g. `Wife 100%` or `Joshua 50%, Esther 50%`."
        )
        return _wrap(reply_parts, questions, patch, advice)

    # ── 6. Beyond — defer to wizard for now ─────────────────────────────
    reply_parts.append(
        "✅ Identities, Testator, Executor, Beneficiaries all set.\n\n"
        "Specific Gifts (Step 6), Residuary (Step 7), and Trust (Step 8) "
        "auto-fill from the email is coming next slice. For now, open the wizard "
        "(top-right of this page) to fill those in."
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


def _intake_summary(artifacts: List[Dict[str, Any]]) -> str:
    """Aggregated by-kind summary for fresh artifacts."""
    buckets = {}
    for a in artifacts:
        buckets.setdefault(a.get('kind', 'other'), []).append(a)
    lines = [f"📥 Received **{len(artifacts)} attachment{'s' if len(artifacts)!=1 else ''}**:"]

    if buckets.get('nric'):
        ics = buckets['nric']
        names = [(a.get('extracted') or {}).get('full_name','').strip() for a in ics]
        named = [n for n in names if n]
        line = f"📇 **{len(ics)} IC{'s' if len(ics)!=1 else ''}**"
        if named:
            line += " — read as: " + ", ".join(named[:6])
            if len(named) > 6:
                line += f", and {len(named)-6} more"
        lines.append(line)
    if buckets.get('property_title'):
        lines.append(f"🏠 **{len(buckets['property_title'])} property title{'s' if len(buckets['property_title'])!=1 else ''}** — filed under property/.")
    if buckets.get('property_tax'):
        lines.append(f"🧾 **{len(buckets['property_tax'])} property tax notice{'s' if len(buckets['property_tax'])!=1 else ''}**.")
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
        lines.append(f"❓ **{len(buckets['other'])}** I couldn't classify (tap thumbnails to view, then tell me).")
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


def _deduce_intent_from_messages(p: Dict[str, Any], recent_text: str) -> str:
    """Pull any messages from the client that mention this specific
    property by lot/title number/address. The will writer needs to see
    'what did the client actually say about this one?' next to the
    auto-grouped doc evidence."""
    if not recent_text:
        return ''
    ex = p.get('extracted') or {}
    needles = []
    for k in ('title_number', 'lot_number', 'property_address',
              'description', 'mukim', 'property_hint'):
        v = (ex.get(k) or '').strip()
        if v and len(v) >= 3:
            needles.append(v)
    if not needles:
        return ''
    # Find any sentence in recent_text that mentions one of the needles.
    import re as _re
    text_l = recent_text.lower()
    matches = []
    for needle in needles:
        n = needle.lower()
        idx = text_l.find(n)
        if idx == -1:
            continue
        # Grab a window of ±120 chars around the mention
        lo = max(0, idx - 120)
        hi = min(len(recent_text), idx + len(needle) + 120)
        snippet = recent_text[lo:hi].strip()
        # Trim to sentence bounds if possible
        snippet = _re.sub(r'\s+', ' ', snippet)
        if len(snippet) > 200:
            snippet = '…' + snippet[-200:]
        matches.append(snippet)
        if len(matches) >= 2:
            break
    return '\n'.join(f"  > _{m}_" for m in matches)


def _validate_property_format(ex: Dict[str, Any]) -> List[str]:
    """Surface obvious formatting / completeness issues per the National
    Land Code. The will writer sees these inline so they can correct
    BEFORE the gift goes into the wizard. Cheap heuristic checks — not
    a substitute for legal review."""
    warnings = []
    title_no = (ex.get('title_number') or '').strip()
    title_type = (ex.get('title_type') or '').strip()
    lot_no = (ex.get('lot_number') or '').strip()
    mukim = (ex.get('mukim') or '').strip()
    daerah = (ex.get('daerah') or '').strip()
    negeri = (ex.get('negeri') or '').strip()
    addr = (ex.get('property_address') or ex.get('description') or '').strip()
    if not addr and not title_no:
        warnings.append("⚠️  Address AND title number both blank — re-OCR or ask client for a clearer scan.")
    if title_no and not any(p in title_no.upper()
                            for p in ('GERAN', 'HSD', 'HSM', 'HS(D)', 'HS(M)',
                                      'PT', 'PN', 'GM', 'PM')):
        warnings.append(
            f"⚠️  Title `{title_no}` doesn't match common Malaysian formats "
            "(Geran / HS(D) / HS(M) / PT / PN / GM / PM) — verify with client."
        )
    if not lot_no:
        warnings.append("⚠️  Lot number missing — National Land Code requires it for the gift clause.")
    if not mukim and not daerah:
        warnings.append("⚠️  Mukim AND daerah both blank — needed to draft the property description.")
    # Quick spelling sanity for common Mukim/Daerah patterns. Anything
    # ending in obviously wrong characters likely an OCR error.
    for label, val in (('Mukim', mukim), ('Daerah', daerah), ('Negeri', negeri)):
        if val and (any(ch.isdigit() for ch in val) or len(val) > 60):
            warnings.append(f"⚠️  {label} `{val}` looks suspicious (digits or unusually long) — likely OCR error, verify.")
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

    if props:
        target = props[0]
        # If the writer just pressed "Wrong supporting docs" on this
        # property, the app handler stamped _unlink_pending. Render the
        # support-doc picker instead of the normal card.
        if (target.get('extracted') or {}).get('_unlink_pending'):
            return _walkthrough_unlink_picker(target)
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
    warnings = _validate_property_format(ex)
    intent = _deduce_intent_from_messages(p, recent_text)

    parts = [
        f"### 🏠 Reviewing property ({n_left} of {total_remaining} left)",
        formatted,
    ]

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

    # Supporting docs grouped under this property
    support = p.get('support_docs') or []
    if support:
        sup_lines = [f"**📎 {len(support)} supporting doc{'s' if len(support) != 1 else ''} grouped under this property:**"]
        for i, s in enumerate(support, 1):
            kind = s.get('category', '')
            kind_label = {
                'property_spa': '📝 SPA',
                'property_tax': '🧾 Cukai Tanah',
                'property_title': '📜 Geran (extra page)',
                'utility_bill': '⚡ Utility bill',
                'bank_letter': '🏦 Bank letter',
            }.get(kind, '📄')
            purpose = (s.get('purpose') or s.get('original_filename') or '').strip()
            sup_lines.append(f"  {i}. {kind_label} — _{purpose[:140]}_")
        parts.append('\n'.join(sup_lines))

    # Intent quote from client's messages
    if intent:
        parts.append(f"**💬 Client's message about this property:**\n{intent}")

    # NLC warnings
    if warnings:
        parts.append("**🚨 Validation:**\n" + '\n'.join(f"  {w}" for w in warnings))

    parts.append(
        "**Does this property look correctly grouped & formatted?**\n"
        "Confirm, and I'll add it to the wizard's gift list. The "
        "beneficiary assignment happens AFTER all assets are reviewed."
    )

    quick = [
        {'label': '✅ Looks right — add to wizard', 'value': 'inventory confirm'},
        {'label': '✂️ Wrong supporting docs', 'value': 'inventory unlink'},
        {'label': '🗑 Remove this property', 'value': 'delete'},
        {'label': '⏭ Skip for now', 'value': 'inventory skip'},
    ]

    # Focus the title image plus first 3 supporting docs as inline previews
    focus_ids = [p.get('document_id')]
    for s in support[:3]:
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
    focus_ids = [p.get('document_id')] + [s.get('document_id') for s in support[:5]]
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
            'utility_bill': '⚡ Utility bill',
            'bank_letter': '🏦 Bank letter',
        }.get(kind, '📄 Doc')
        sp = (s.get('purpose') or s.get('original_filename') or 'supporting doc').strip()
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
