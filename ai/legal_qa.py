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
            max_tokens=700,
            messages=[{
                'role': 'user',
                'content': f"""You're an assistant helping a Malaysian advisor draft a non-Muslim will.{excerpts_block}
{('When citing, prefer the EXCERPTS above (cite as e.g. \"Wills Act 1959 s.5\") '
  'over general training-data recall. If the excerpts do not cover the question, '
  'say so plainly: \"Not covered in our library — answering from general knowledge.\"'
  if excerpts_block else
  'No matching Act excerpts found in our library. Answer from general knowledge '
  'and start your reply with: \"⚠️ Not in library — general knowledge only:\"')}
The user paused the workflow to ask a question. Answer concisely (max 3 short
paragraphs, 200 words). Cite the relevant Malaysian act + section when
useful: Wills Act 1959, Probate and Administration Act 1959, Distribution
Act 1958. Be plain-language.

End with a one-line disclaimer: 'This is general guidance, not legal advice
— consult a qualified lawyer for your specific situation.'

USER QUESTION: {user_text}

Answer the question, then end with the disclaimer. Do NOT preface with
'Great question' or similar filler.{nudge if False else ''}"""
            }]
        )
        body = (msg.content[0].text or '').strip() if msg.content else ''

        # Transparency header: show what we searched + what matched
        if library_titles:
            header = f"🔍 Searched **{len(library_titles)} Act{'s' if len(library_titles)!=1 else ''}** in the library: " \
                     f"_{', '.join(library_titles[:5])}{', …' if len(library_titles)>5 else ''}_\n\n"
            if matched_titles:
                header += f"📚 Matched citations in: **{', '.join(matched_titles)}**\n\n"
            else:
                header += "⚠️ No direct match in library — answering from general knowledge.\n\n"
        else:
            header = "⚠️ Library is empty (no Acts uploaded yet) — answering from general knowledge.\n\n"

        return header + body + nudge
    except Exception:
        return ''
