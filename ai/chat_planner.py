"""Orchestrator for the client chat — turns extracted artifacts into a
proposed patch against the wizard step JSON, plus a reply, clarifying
questions, and advice.

Slice 1 scope: IC images only. The pipeline is deterministic — no LLM
call here, just mapping the IC fields into step1 (testator) shape and
flagging missing fields. Later slices (property titles, WhatsApp text)
will add an LLM-driven branch for free-form intents.

Patch shape (subset of WillData):
    {
        "step1": {full_name, nric_passport, residential_address, date_of_birth, nationality, gender, person_link: {name, ic, ...}},
        ...
    }
The applier (chat_apply route) deep-merges this into the target Will's
step JSON, and uses person_link entries to call ensure_person.
"""
from typing import List, Dict, Any, Optional


def _ddmmyyyy_to_iso(dob: str) -> str:
    """Convert 'DD-MM-YYYY' (from ai/ocr.py) to 'YYYY-MM-DD'."""
    if not dob:
        return ''
    parts = dob.split('-')
    if len(parts) == 3 and len(parts[2]) == 4 and parts[2].isdigit():
        # Looks like DD-MM-YYYY
        return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
    return dob  # already ISO or unknown — leave as-is


def _ic_to_step1_patch(ic: Dict[str, Any]) -> Dict[str, Any]:
    """Map an extract_nric_data result to the step1 (testator) JSON shape."""
    return {
        'full_name': ic.get('full_name', '').strip(),
        'nric_passport': ic.get('nric_number', '').strip(),
        'residential_address': ic.get('address', '').strip(),
        'date_of_birth': _ddmmyyyy_to_iso(ic.get('date_of_birth', '')),
        'nationality': ic.get('nationality') or 'Malaysian',
        'gender': ic.get('gender', ''),
    }


def _missing_fields(s1: Dict[str, Any]) -> List[str]:
    out = []
    if not s1.get('full_name'):
        out.append('full name')
    if not s1.get('nric_passport'):
        out.append('NRIC / passport number')
    if not s1.get('residential_address'):
        out.append('residential address')
    return out


def plan_turn(
    user_text: str,
    artifacts: List[Dict[str, Any]],
    current_will_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Take this turn's input and return what to say + propose.

    artifacts: list of dicts shaped like
        {"document_id": str, "kind": str, "confidence": str,
         "extracted": dict | None,        # extractor output, kind-specific
         "original_filename": str}
    current_will_data: dict of step1..step8 already in the active Will
        (parsed JSON). Used to detect duplicates and produce advice.

    Returns:
        {
          "reply": str,
          "clarifying_questions": [str],
          "proposed_patch": dict,         # may be empty if nothing to propose
          "advice": [{"section": str, "severity": str, "text": str}],
        }
    """
    current_will_data = current_will_data or {}
    reply_parts: List[str] = []
    questions: List[str] = []
    advice: List[Dict[str, str]] = []
    patch: Dict[str, Any] = {}

    # ── Process artifacts (slice 1: IC only) ────────────────────────────
    ic_artifacts = [a for a in artifacts if a.get('kind') == 'nric']
    other_artifacts = [a for a in artifacts if a.get('kind') != 'nric']

    if ic_artifacts:
        # If multiple ICs in one turn, we only auto-propose for the first
        # (typically the testator). Others are noted for the user.
        primary = ic_artifacts[0]
        ic = primary.get('extracted') or {}
        if ic.get('error'):
            reply_parts.append(
                f"I couldn't read the IC clearly. Reason: {ic.get('error')}. "
                "Try a clearer photo or enter the details manually in Step 1."
            )
        else:
            s1 = _ic_to_step1_patch(ic)
            existing_s1 = current_will_data.get('step1') or {}
            existing_name = (existing_s1.get('full_name') or '').strip()
            existing_nric = (existing_s1.get('nric_passport') or '').strip()

            # Duplicate / overwrite check
            if existing_nric and s1['nric_passport'] and existing_nric != s1['nric_passport']:
                advice.append({
                    'section': 'Step 1 — Testator',
                    'severity': 'warning',
                    'text': (
                        f"Testator NRIC is currently {existing_nric}. This IC shows "
                        f"{s1['nric_passport']}. Applying will overwrite the existing testator. "
                        "If this IC belongs to a different person (e.g. an executor or beneficiary), "
                        "tell me their role instead of clicking Apply."
                    ),
                })
            elif existing_name and s1['full_name'] and existing_name.upper() != s1['full_name'].upper():
                advice.append({
                    'section': 'Step 1 — Testator',
                    'severity': 'info',
                    'text': f"Testator name will change from \"{existing_name}\" to \"{s1['full_name']}\".",
                })

            patch.setdefault('step1', {}).update(s1)
            # Mark for the applier to create / link a Person row
            patch['_persons'] = patch.get('_persons', []) + [{
                'role': 'Testator',
                'name': s1['full_name'],
                'nric': s1['nric_passport'],
                'address': s1['residential_address'],
                'dob': s1['date_of_birth'],
                'nationality': s1['nationality'],
                'document_id': primary.get('document_id'),
                'link_to_step1': True,
            }]

            reply_parts.append(
                f"I read this as a Malaysian {ic.get('doc_type', 'IC').upper()} for "
                f"**{s1['full_name'] or '(name unreadable)'}** "
                f"({s1['nric_passport'] or 'NRIC unreadable'}). "
                "I've drafted an update to Step 1 (Testator) — review the diff card and click Apply to confirm."
            )

            missing = _missing_fields(s1)
            if missing:
                questions.append(
                    f"I couldn't read the {', '.join(missing)} from this IC. "
                    "Could you provide it, or upload a clearer photo of the back of the card?"
                )

        if len(ic_artifacts) > 1:
            count = len(ic_artifacts) - 1
            reply_parts.append(
                f"I see {count} more IC{'s' if count > 1 else ''} in this upload. "
                "Tell me whose they are (executor, beneficiary, guardian, witness?) "
                "and I'll file each one to the right section."
            )

    # ── Other kinds: acknowledge, defer to later slices ─────────────────
    for art in other_artifacts:
        kind = art.get('kind', 'other')
        fname = art.get('original_filename', 'file')
        if kind == 'property_title':
            reply_parts.append(
                f"I can see **{fname}** is a property title. "
                "Property gifts (Step 6) aren't auto-filled yet in this version — "
                "I've saved the document under property/ and you can add it from the wizard."
            )
        elif kind == 'will':
            reply_parts.append(
                f"**{fname}** looks like an existing Will. To re-import it, use "
                "[Upload Will](/upload-will) — the chat doesn't replace that flow yet."
            )
        elif kind == 'other':
            reply_parts.append(
                f"I'm not sure what **{fname}** is. Could you tell me — IC, property title, "
                "bank statement, or something else?"
            )
        else:
            reply_parts.append(
                f"I see **{fname}** is a {kind.replace('_', ' ')}. "
                "Filing it under the matching folder. Auto-fill for this kind is coming soon."
            )

    # ── Pure-text turn (no artifacts) ────────────────────────────────────
    if not artifacts:
        if user_text.strip():
            reply_parts.append(
                "Got it — I've noted your message. Right now I can auto-fill the Testator section "
                "from an IC photo. Try uploading a MyKad image or use the wizard directly for "
                "executors, beneficiaries, and gifts."
            )
        else:
            reply_parts.append(
                "Drop an IC photo or paste a WhatsApp message and I'll start sorting it out."
            )

    # ── Advice: missing-executor / share sums (basic checks) ────────────
    advice.extend(_basic_will_checks(current_will_data, patch))

    return {
        'reply': '\n\n'.join(reply_parts).strip(),
        'clarifying_questions': questions,
        'proposed_patch': patch if patch else {},
        'advice': advice,
    }


def _basic_will_checks(current: Dict[str, Any], patch: Dict[str, Any]) -> List[Dict[str, str]]:
    """A few sanity checks that are useful to surface in chat.

    Kept tiny on purpose — the real advice engine comes in slice 4.
    """
    out: List[Dict[str, str]] = []
    # Combined view: current state with patch applied (shallow per step)
    combined = dict(current)
    for k, v in patch.items():
        if k.startswith('_'):
            continue
        if isinstance(v, dict) and isinstance(combined.get(k), dict):
            merged = dict(combined[k])
            merged.update(v)
            combined[k] = merged
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
