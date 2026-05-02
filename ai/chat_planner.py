"""Orchestrator for the client chat — turns extracted artifacts into a
proposed patch against the wizard step JSON, plus a reply, clarifying
questions, and advice.

Emits ONE aggregated summary per artifact kind (not per file) so a 39-
attachment email gets a tidy 5-bullet reply instead of a 39-paragraph
monologue. The chat UI handles the per-thumbnail role assignment for
multi-IC cases.
"""
from typing import List, Dict, Any, Optional


def _ddmmyyyy_to_iso(dob: str) -> str:
    """Convert 'DD-MM-YYYY' (from ai/ocr.py) to 'YYYY-MM-DD'."""
    if not dob:
        return ''
    parts = dob.split('-')
    if len(parts) == 3 and len(parts[2]) == 4 and parts[2].isdigit():
        return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
    return dob


def _ic_to_step1_patch(ic: Dict[str, Any]) -> Dict[str, Any]:
    """Map an extract_nric_data result to the step1 (testator) JSON shape."""
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
) -> Dict[str, Any]:
    """Take this turn's input and return what to say + propose."""
    current_will_data = current_will_data or {}
    reply_parts: List[str] = []
    questions: List[str] = []
    advice: List[Dict[str, str]] = []
    patch: Dict[str, Any] = {}

    # ── Bucket artifacts by kind ────────────────────────────────────────
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for art in artifacts:
        buckets.setdefault(art.get('kind', 'other'), []).append(art)

    ic_arts = buckets.get('nric', [])
    title_arts = buckets.get('property_title', [])
    tax_arts = buckets.get('property_tax', [])
    bank_arts = buckets.get('bank_statement', [])
    insur_arts = buckets.get('insurance', [])
    epf_arts = buckets.get('epf_kwsp', [])
    vehicle_arts = buckets.get('vehicle', [])
    will_arts = buckets.get('will', [])
    voice_arts = buckets.get('voice', [])
    other_arts = buckets.get('other', [])

    if artifacts:
        reply_parts.append(f"📥 Received **{len(artifacts)} attachment{'s' if len(artifacts)!=1 else ''}**:")

    # ── ICs: aggregated, with names if extracted ────────────────────────
    if ic_arts:
        # Names we managed to read from the ICs
        named = []
        unnamed = 0
        for a in ic_arts:
            ex = a.get('extracted') or {}
            name = (ex.get('full_name') or '').strip()
            nric = (ex.get('nric_number') or '').strip()
            if name and nric:
                named.append({'name': name, 'nric': nric, 'document_id': a.get('document_id'),
                              'extracted': ex})
            elif name:
                named.append({'name': name, 'nric': '', 'document_id': a.get('document_id'),
                              'extracted': ex})
            else:
                unnamed += 1

        line = f"📇 **{len(ic_arts)} IC{'s' if len(ic_arts)!=1 else ''}**"
        if named:
            line += " — read as: " + ", ".join(
                f"{n['name']}" + (f" ({n['nric']})" if n['nric'] else "")
                for n in named[:6]
            )
            if len(named) > 6:
                line += f", and {len(named)-6} more"
        if unnamed:
            line += f". {unnamed} couldn't be read clearly."
        reply_parts.append(line)

        # Pick a testator candidate: first IC with both name + NRIC
        testator = next((n for n in named if n['name'] and n['nric']), None)
        existing_s1 = current_will_data.get('step1') or {}
        existing_nric = (existing_s1.get('nric_passport') or '').strip()

        if testator and not existing_nric:
            # No testator yet — propose using first readable IC
            s1 = _ic_to_step1_patch(testator['extracted'])
            patch.setdefault('step1', {}).update(s1)
            patch['_persons'] = patch.get('_persons', []) + [{
                'role': 'Testator',
                'name': s1['full_name'], 'nric': s1['nric_passport'],
                'address': s1['residential_address'], 'dob': s1['date_of_birth'],
                'nationality': s1['nationality'],
                'document_id': testator['document_id'], 'link_to_step1': True,
            }]
            reply_parts.append(
                f"  ↳ Proposing **{s1['full_name']}** as Testator. "
                "Click ✓ Apply on the diff card to confirm."
            )
        elif testator and existing_nric and testator['nric'] != existing_nric:
            advice.append({
                'section': 'Step 1 — Testator',
                'severity': 'info',
                'text': (f"Testator already set to NRIC {existing_nric}. "
                         f"This batch contains an IC for {testator['name']} ({testator['nric']}) "
                         "— probably a different person (executor/beneficiary). "
                         "Use the per-IC role picker to file each one."),
            })

        if len(ic_arts) > 1:
            questions.append(
                f"You uploaded {len(ic_arts)} ICs. The first becomes the Testator (if not set yet). "
                f"For the others, tell me each role: Spouse, Son, Daughter, Executor, Guardian, Witness, Beneficiary."
            )

    # ── Property titles ─────────────────────────────────────────────────
    if title_arts:
        reply_parts.append(
            f"🏠 **{len(title_arts)} property title{'s' if len(title_arts)!=1 else ''}** — filed under property/. "
            "These need to be added as Specific Gifts (Step 6). Tell me who gets each property "
            "(e.g. \"condo at Marina Cove → daughter Esther\")."
        )

    # ── Property tax / cukai ────────────────────────────────────────────
    if tax_arts:
        reply_parts.append(
            f"🧾 **{len(tax_arts)} property tax notice{'s' if len(tax_arts)!=1 else ''}** "
            "(cukai harta / cukai pintu) — filed under property/. Useful for matching to titles."
        )

    # ── Bank statements ─────────────────────────────────────────────────
    if bank_arts:
        reply_parts.append(
            f"🏦 **{len(bank_arts)} bank statement{'s' if len(bank_arts)!=1 else ''}** — filed under bank/. "
            "Tell me if any specific account goes to a specific beneficiary; otherwise it falls under residuary."
        )

    # ── Insurance / EPF / Vehicle: just acknowledge ─────────────────────
    if insur_arts:
        reply_parts.append(f"🛡 **{len(insur_arts)} insurance** doc{'s' if len(insur_arts)!=1 else ''} filed.")
    if epf_arts:
        reply_parts.append(f"💼 **{len(epf_arts)} EPF/KWSP** statement{'s' if len(epf_arts)!=1 else ''} filed (note: EPF goes via nominee, not the will).")
    if vehicle_arts:
        reply_parts.append(f"🚗 **{len(vehicle_arts)} vehicle** doc{'s' if len(vehicle_arts)!=1 else ''} filed.")

    # ── Existing wills ─────────────────────────────────────────────────
    if will_arts:
        reply_parts.append(
            f"📄 **{len(will_arts)} existing will document{'s' if len(will_arts)!=1 else ''}** detected. "
            "If you want to import it as the starting point, use [Upload Will](/upload-will). "
            "Otherwise it'll just live in the chat history for reference."
        )

    # ── Voice transcripts ──────────────────────────────────────────────
    # (Already merged into user_text by the caller — don't repeat the
    # transcript here, but note the count.)
    if voice_arts:
        reply_parts.append(
            f"🎙 **{len(voice_arts)} voice note{'s' if len(voice_arts)!=1 else ''}** transcribed and added to the message text above."
        )

    # ── Unclassified — ask for help, structured ─────────────────────────
    if other_arts:
        reply_parts.append(
            f"❓ **{len(other_arts)} attachment{'s' if len(other_arts)!=1 else ''}** I couldn't classify. "
            "Tap each thumbnail in the message above to view it, then reply with what they are "
            "(e.g. \"the third photo is a property title\")."
        )

    # ── Pure-text turn (no artifacts) ──────────────────────────────────
    if not artifacts:
        if user_text and user_text.strip():
            # If the user text mentions specific intent words, hint at next
            # action; otherwise generic ack.
            t = user_text.lower()
            if any(k in t for k in ('ignore', 'wrong', 'mistake', 'delete')):
                reply_parts.append(
                    "Got it. Use the **× delete** button on a message to remove it, or **Clear Chat** to wipe everything."
                )
            else:
                reply_parts.append(
                    "Noted. I work best with attached IC photos, property titles, or short voice notes saying what to do with each."
                )
        else:
            reply_parts.append("Drop a file or tap 🎤 to start.")

    # ── Advice (basic checks) ──────────────────────────────────────────
    advice.extend(_basic_will_checks(current_will_data, patch))

    return {
        'reply': '\n\n'.join(reply_parts).strip(),
        'clarifying_questions': questions,
        'proposed_patch': patch if patch else {},
        'advice': advice,
    }


def _basic_will_checks(current: Dict[str, Any], patch: Dict[str, Any]) -> List[Dict[str, str]]:
    """Tiny set of sanity checks. Real advice engine comes in slice 4."""
    out: List[Dict[str, str]] = []
    combined = dict(current)
    for k, v in patch.items():
        if k.startswith('_'):
            continue
        if isinstance(v, dict) and isinstance(combined.get(k), dict):
            merged = dict(combined[k]); merged.update(v); combined[k] = merged
        else:
            combined[k] = v

    s1 = combined.get('step1') or {}
    if s1.get('full_name') and not s1.get('nric_passport'):
        out.append({
            'section': 'Step 1 — Testator',
            'severity': 'error',
            'text': "Testator NRIC / passport is required before generating the will. "
                    "Add it in Step 1 or upload a clearer IC photo.",
        })
    return out
