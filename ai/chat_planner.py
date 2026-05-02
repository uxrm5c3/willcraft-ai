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
    if just_assigned:
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

    # Announce Step 1 completion if we just finished it
    if just_assigned and not pending_ics:
        reply_parts.append(
            "🎉 **Step 1: Identities — COMPLETE.** All ICs assigned. "
            "Now moving to **Step 2: Testator Info**."
        )

    # ── 3. STEP 2: confirm Testator details ─────────────────────────────
    if s1.get('full_name') and not _is_confirmed(current_will_data, 'testator'):
        reply_parts.append(_step2_question(s1))
        return _wrap(reply_parts, questions, patch, advice)

    # ── 4. STEP 3: Executor (main + substitute) ─────────────────────────
    # Walk through main first, then substitute. Only stop after both are
    # set OR user typed `skip` for substitute (handled by _try_save_executor).
    if n_executors < 2:
        q = _step3_executor_question(current_will_data, recent_text=recent_text)
        reply_parts.append(q['text'])
        focus = [q['focus_doc_id']] if q.get('focus_doc_id') else []
        return _wrap(reply_parts, questions, patch, advice, focus_attachments=focus)

    # ── 5. STEP 5: Beneficiaries ───────────────────────────────────────
    if n_beneficiaries == 0:
        reply_parts.append(
            "✅ **Step 3 complete.** Moving to **Step 5: Beneficiaries**.\n\n"
            "Who should inherit the estate? List each beneficiary and their share "
            "(e.g. \"Wife 100%\" or \"Joshua 50%, Esther 50%\")."
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
        f"### 👤 Step 1: Identity walk-through ({len(pending_ics)} remaining)",
        f"**{name}** — {nric}",
    ]
    if deduction:
        parts.append(
            f"📌 From the email this looks like **{deduction['role']}** "
            f"(evidence: _\"{deduction['evidence']}\"_)."
        )
        parts.append(
            "Reply: **`yes`** to confirm · a different role to correct · "
            "**`skip`** to defer · **`delete`** if this IC was uploaded by mistake."
        )
    else:
        parts.append(
            "What's their relationship to the testator?\n"
            "Reply with: **Spouse · Son · Daughter · Father · Mother · "
            "Brother · Sister · Executor · Guardian · Witness · Beneficiary · Trustee**\n"
            "Or **`skip`** to defer · **`delete`** if uploaded by mistake."
        )
    return '\n\n'.join(parts)


def _step2_question(s1: Dict[str, Any]) -> str:
    """Confirm testator details once identities are in."""
    return (
        "### 👔 Step 2: Confirm Testator\n\n"
        f"- **Name:** {s1.get('full_name','?')}\n"
        f"- **NRIC:** {s1.get('nric_passport','?')}\n"
        f"- **DOB:** {s1.get('date_of_birth','?')}\n"
        f"- **Address:** {s1.get('residential_address','?')}\n\n"
        "Reply `confirm` to lock this in, or correct any field "
        "(e.g. `address: <new address>`)."
    )


def find_executor_candidate(identities, executors, role, recent_text=''):
    """Pick the best candidate for main / substitute executor.
    Returns {'person_id', 'name', 'evidence', 'document_id'} or None.
    Used by both _step3_executor_question (to suggest in prompt) and the
    chat-message route (to apply when user replies 'yes')."""
    # 1) Already marked Executor in identities
    already = next((i for i in identities
                    if 'executor' in (i.get('relationship') or '').lower()), None)
    if already and not _is_already_executor(already, executors):
        return {'person_id': already.get('id'),
                'name': already.get('full_name'),
                'evidence': 'marked Executor in identities',
                'document_id': already.get('document_id') or None}

    # 2) Email-text deduction (Claude)
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
        parts.append(
            "Who should be the **main executor** — the person who carries out "
            "your wishes when you pass on?"
        )
    else:
        # Show who the main is for context
        m = executors[0] if executors else {}
        parts.append(
            f"✓ Main executor: **{m.get('full_name','?')}**.\n\n"
            "Now choose a **substitute (backup) executor** — they take over if "
            "the main predeceases you or declines."
        )

    if candidate:
        parts.append(
            f"📌 Suggestion: **{candidate['name']}** "
            f"(_{candidate['evidence']}_)."
        )
        parts.append(
            "Reply **`yes`** to confirm, name someone else from your identities, "
            "or **`skip`** to skip the substitute (only valid for substitute)."
        )
    else:
        names = ', '.join(i['full_name'] for i in identities[:6])
        parts.append(
            f"Reply with a name from your identities ({names}{', …' if len(identities)>6 else ''})."
        )
        if role == 'substitute':
            parts.append("Or **`skip`** if you don't want a substitute.")

    # Tailored note — only if relevant
    if minors and role == 'main':
        parts.append(
            f"⚠️ {len(minors)} minor beneficiary(ies) detected ({', '.join(n for n,_ in minors)}). "
            "Joint executors strongly recommended; you'll also need a guardian (Step 4)."
        )

    return {'text': '\n\n'.join(parts), 'focus_doc_id': candidate.get('document_id') if candidate else None}


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
