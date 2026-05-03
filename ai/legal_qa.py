"""Answer side-quest legal/process questions from the chat user without
advancing the directed-flow stage. Always nudges them back to the step
they were on.
"""
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_FAST


_QUESTION_STARTERS = (
    'what', 'why', 'how', 'when', 'where', 'who',
    'is', 'are', 'can', 'should', 'do', 'does', 'will',
    'must', 'may', 'could',
)


def is_question(text: str) -> bool:
    """Heuristic detector — does this look like a side-quest question
    rather than an answer to the current step?"""
    if not text:
        return False
    t = text.strip().lower()
    if not t:
        return False
    # Direct ? mark
    if t.endswith('?'):
        return True
    # Question-word start, but only if message is at least 4 words long
    # (so 'is wife the spouse' looks like a question, while a short reply
    # like 'is wife' could go either way — we err toward NOT treating as Q).
    words = t.split()
    if len(words) >= 4 and words[0] in _QUESTION_STARTERS:
        return True
    # Common help intents
    if any(p in t for p in (
        'explain', 'tell me about', 'what is', 'what does', 'meaning of',
        'difference between', 'why do', 'why must',
    )):
        return True
    return False


def answer_question(user_text: str, current_stage_summary: str = '',
                    client_id: str = None, user_id: str = None) -> str:
    """Short Claude answer + nudge back to the active step. Returns ''
    on any failure (caller can fall back to ignoring)."""
    if not user_text:
        return ''
    # Build a "↩ Resume" quick-reply for the current step so the user can
    # one-tap back to the workflow after the digression. Stage label is
    # passed in by the caller (e.g. "Step 6: property gift", "Step 1: Identity").
    import json as _json
    resume_quick = []
    if current_stage_summary:
        # The literal string 'continue' triggers the planner to re-emit
        # whatever question was active (it doesn't match any save-keyword).
        resume_quick = [
            {'label': f"↩ Resume {current_stage_summary}", 'value': 'continue'},
            {'label': 'Stay here, I have more questions', 'value': 'not yet'},
        ]
    matched_titles = []
    # Snapshot of which Acts the library currently holds (for transparency)
    library_titles = []
    try:
        from services.legal_library import list_available_acts
        library_titles = [a['title'] for a in list_available_acts()]
    except Exception:
        pass
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        nudge = (f"\n\nNow back to **{current_stage_summary}** — please continue."
                 if current_stage_summary else "")
        # MANDATORY library lookup first
        excerpts_block = ''
        ex = []
        try:
            from services.legal_library import relevant_excerpts
            ex = relevant_excerpts(user_text)
            if ex:
                matched_titles = [e['title'] for e in ex]
                excerpts_block = ("\n\nRELEVANT ACT PROVISIONS — cite the Act + section when "
                                  "you use them, and prefer these over general knowledge:\n")
                for e in ex:
                    excerpts_block += f"\n--- {e['title']} ---\n{e['excerpt']}\n"
        except Exception:
            pass

        # Log the gap if no library match (so tech team can enhance the library)
        if not ex:
            try:
                from database import db, LegalQAGap
                gap = LegalQAGap(question=user_text[:1000], client_id=client_id,
                                 user_id=user_id, matched_acts='[]',
                                 answered_from='general')
                db.session.add(gap)
                db.session.commit()
            except Exception:
                pass
        else:
            try:
                import json as _json
                from database import db, LegalQAGap
                # Still log so we can see what's frequently asked
                gap = LegalQAGap(question=user_text[:1000], client_id=client_id,
                                 user_id=user_id,
                                 matched_acts=_json.dumps(matched_titles),
                                 answered_from='library')
                db.session.add(gap)
                db.session.commit()
            except Exception:
                pass
        msg = client.messages.create(
            model=CLAUDE_MODEL_FAST,
            max_tokens=500,
            messages=[{
                'role': 'user',
                'content': f"""You help a Malaysian advisor draft a non-Muslim will.{excerpts_block}

USER QUESTION: {user_text}

Reply in this EXACT format (no preface, no filler, no disclaimer):

**Answer:** <one or two short plain-English sentences — maximum 40 words. Direct, no hedging.>

**Citation:** <Act name + section, e.g. "Wills Act 1959 s.5(2)". If the excerpts above cover it, cite from there. If not, write "General knowledge — not in library".>

**Example:** <one short concrete example — maximum 25 words — illustrating the answer in a typical Malaysian situation. Skip this line ENTIRELY if no useful example fits.>

That's it. No "I hope this helps", no extra paragraphs."""
            }]
        )
        body = (msg.content[0].text or '').strip() if msg.content else ''

        # Tiny one-line citation footer below the body — minimal, italic, gray.
        # The body itself already contains "Answer:" + "Citation:" sections.
        if matched_titles:
            footer = f"\n\n<sub>📚 _Cited from library: {', '.join(matched_titles)}_</sub>"
        elif library_titles:
            footer = f"\n\n<sub>⚠️ _Not in library — answered from general knowledge ({len(library_titles)} Acts loaded)_</sub>"
        else:
            footer = "\n\n<sub>⚠️ _Library empty — general knowledge only_</sub>"

        out = body + footer
        if resume_quick:
            out += f"\n\n<!--quickreplies:{_json.dumps(resume_quick)}-->"
        return out
    except Exception:
        return ''
