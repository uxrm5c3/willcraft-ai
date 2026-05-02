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

    # ── Acknowledge an assignment from the previous turn ────────────────
    if just_assigned:
        reply_parts.append(
            f"✅ Saved **{just_assigned.get('name','')}** as **{just_assigned.get('role','')}**."
        )

    # ── 1. INTAKE — fresh attachments this turn ─────────────────────────
    if artifacts:
        reply_parts.append(_intake_summary(artifacts))

    # ── 2. IDENTITY WALK-THROUGH — pending IC? ──────────────────────────
    if pending_ics:
        reply_parts.append(_identity_question(pending_ics, recent_text))
        return _wrap(reply_parts, questions, patch, advice)

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

    # ── 4. STEP 3: Executor ─────────────────────────────────────────────
    if n_executors == 0:
        reply_parts.append(_step3_executor_question(current_will_data))
        return _wrap(reply_parts, questions, patch, advice)

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

def _wrap(parts, questions, patch, advice):
    return {
        'reply': '\n\n'.join(p for p in parts if p).strip(),
        'clarifying_questions': questions,
        'proposed_patch': patch if patch else {},
        'advice': advice,
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
            "**Reply `yes` to confirm**, or correct it (e.g. `no, daughter`). "
            "Or `skip` to come back to this one later."
        )
    else:
        parts.append(
            "What's their relationship to the testator?\n"
            "Reply with one of: **Spouse, Son, Daughter, Father, Mother, "
            "Brother, Sister, Executor, Guardian, Witness, Beneficiary, Trustee, Other**.\n"
            "Or `skip` to come back later."
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


def _step3_executor_question(will_data: Dict[str, Any]) -> str:
    return (
        "### ⚖️ Step 3: Executor & Trustees\n\n"
        "Who should be the executor (the person who carries out your wishes)?\n\n"
        "- A single executor is fine if they're trusted and capable.\n"
        "- **Joint executors are recommended if any beneficiary is a minor**, "
        "or if the estate is complex.\n"
        "- The executor often doubles as the trustee.\n\n"
        "Reply with the name(s), e.g. `Joshua` or `Joshua and Esther jointly`. "
        "Pick from the identities you already added."
    )


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
