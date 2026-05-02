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


def answer_question(user_text: str, current_stage_summary: str = '') -> str:
    """Short Claude answer + nudge back to the active step. Returns ''
    on any failure (caller can fall back to ignoring)."""
    if not user_text:
        return ''
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        nudge = (f"\n\nNow back to **{current_stage_summary}** — please continue."
                 if current_stage_summary else "")
        msg = client.messages.create(
            model=CLAUDE_MODEL_FAST,
            max_tokens=700,
            messages=[{
                'role': 'user',
                'content': f"""You're an assistant helping a Malaysian advisor draft a non-Muslim will.
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
        return body + nudge
    except Exception:
        return ''
