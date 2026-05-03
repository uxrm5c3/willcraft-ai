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


def _step6_property_question(pending_props, recent_text, will_data):
    """Walk one property at a time. ONLY asks what goes into the will:
    who inherits + share. Joint-tenancy detection is auto-flagged as
    advisory (not a question) since the will template uses 'all my right,
    title and interest' regardless."""
    p = pending_props[0]
    ex = p.get('extracted') or {}
    formatted = _format_property_description(ex)

    # Build quick-reply buttons from existing identities (single-beneficiary
    # shortcuts) + a couple of common splits. Fewer clicks = less typing.
    ident_names = [i.get('full_name','').strip()
                   for i in (will_data.get('identities') or [])
                   if i.get('full_name')]
    # Skip the testator (step1) — they can't inherit from themselves
    s1_name = ((will_data.get('step1') or {}).get('full_name') or '').strip().upper()
    candidates = [n for n in ident_names if n.upper() != s1_name]
    quick = []
    for n in candidates[:4]:
        quick.append({'label': f"{n.title()} 100%", 'value': f"{n} 100%"})
    if len(candidates) >= 2:
        a, b = candidates[0], candidates[1]
        quick.append({'label': f"{a.title()} 50% + {b.title()} 50%",
                      'value': f"{a} 50%, {b} 50%"})
    quick.append({'label': 'Skip', 'value': 'skip'})
    quick.append({'label': 'Delete', 'value': 'delete'})

    n_left = len(pending_props)
    parts = [
        f"### 🏠 Step 6 — Property ({n_left} left)",
        formatted,
        "**Who inherits this property?** Tap a button or type a name + share.",
    ]
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
