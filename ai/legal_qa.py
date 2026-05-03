"""Answer side-quest legal/process questions from the chat user without
advancing the directed-flow stage. Always nudges them back to the step
they were on.
"""
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_FAST


_QUESTION_STARTERS = (
    'what', 'why', 'how', 'when', 'where', 'who', 'which',
    'is', 'are', 'am', 'was', 'were',
    'can', 'should', 'do', 'does', 'did', 'will', 'would',
    'must', 'may', 'might', 'could', 'shall',
    # contractions
    "what's", "how's", "why's", "where's", "who's", "when's",
    "isn't", "aren't", "doesn't", "don't", "didn't",
    "can't", "couldn't", "wouldn't", "shouldn't", "won't",
    'whats', 'hows', 'whys', 'wheres', 'whos', 'whens',  # apostrophe-stripped
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
    words = t.split()
    if not words:
        return False
    # Question-word start. We accept 3+ words so "what's a witness" qualifies,
    # but a short blunt reply like "is wife" doesn't get mis-classed.
    if len(words) >= 3 and words[0] in _QUESTION_STARTERS:
        return True
    # Common help intents (substring match)
    if any(p in t for p in (
        'explain', 'tell me about', 'what is', 'what does', 'what s ',
        "what's", 'meaning of', 'definition of',
        'difference between', 'diff between', 'vs ', ' vs.',
        'why do', 'why must', 'why is', 'why are',
        'how do', 'how does', 'how can', 'how should',
        'can i ', 'can we ', 'should i', 'do i need', 'is it ok',
        'is it required', 'is it mandatory',
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
        # Prompt rule: ONLY emit a Citation line when the answer can point
        # to a real Act + section. If nothing fits, omit the line entirely.
        # We strip any "General knowledge / not in library / not specified"
        # cop-out citations during post-processing — never show them to the
        # client.
        msg = client.messages.create(
            model=CLAUDE_MODEL_FAST,
            max_tokens=500,
            messages=[{
                'role': 'user',
                'content': f"""You help a Malaysian advisor draft a non-Muslim will.{excerpts_block}

USER QUESTION: {user_text}

Reply in this EXACT format (no preface, no filler, no disclaimer, no apologies):

**Answer:** <one or two short plain-English sentences — maximum 40 words. Direct, no hedging. Use everyday Malaysian terms (Geran, Strata Title, etc.) when relevant.>

**Citation:** <ONLY include this line if you can cite a SPECIFIC Malaysian Act + section number from the excerpts above (e.g. "Wills Act 1959 s.5(2)"). If you cannot cite a specific section, OMIT this entire line — do NOT write "General knowledge", "not in library", "N/A", "not specified", or any cop-out. Just leave it out.>

**Example:** <one short concrete example — maximum 25 words — illustrating the answer in a typical Malaysian situation. Skip this line ENTIRELY if no useful example fits.>

That's it. No "I hope this helps", no "feel free to ask", no extra paragraphs."""
            }]
        )
        body = (msg.content[0].text or '').strip() if msg.content else ''

        # ── Post-process: strip cop-out Citation lines ─────────────────────
        # Even with explicit instructions, Claude sometimes still writes
        # "Citation: General knowledge — not in library" or similar. Detect
        # and remove. Also detect whether a real citation survived so we
        # know whether to emit the footer.
        import re as _re_post
        _COPOUT_PATTERNS = (
            r'general knowledge', r'not in (the )?library', r'not (specified|applicable|available)',
            r'^n/?a$', r'no (specific )?citation', r'none', r'not provided',
        )
        lines = body.split('\n')
        kept = []
        real_citation = False
        for ln in lines:
            stripped_ln = ln.strip()
            # Detect a citation line (with or without ** **)
            cite_match = _re_post.match(r'^\*?\*?citation\*?\*?\s*:\s*(.*)$',
                                        stripped_ln, _re_post.IGNORECASE)
            if cite_match:
                cite_value = cite_match.group(1).strip().rstrip('.').lower()
                # Empty value or matches a cop-out → drop the whole line
                if not cite_value or any(_re_post.search(p, cite_value)
                                         for p in _COPOUT_PATTERNS):
                    continue  # skip this line
                real_citation = True
            kept.append(ln)
        # Collapse 3+ consecutive newlines down to 2 after pruning
        body = _re_post.sub(r'\n{3,}', '\n\n', '\n'.join(kept)).strip()

        # Footer ONLY when we kept a real citation. No real cite → no noise.
        if real_citation and matched_titles:
            footer = f"\n\n<sub>📚 _Cited from library: {', '.join(matched_titles)}_</sub>"
        else:
            footer = ''

        out = body + footer
        if resume_quick:
            out += f"\n\n<!--quickreplies:{_json.dumps(resume_quick)}-->"
        return out
    except Exception as e:
        # NEVER return empty silently — that makes the question look "ignored"
        # to the user. Surface a friendly fallback (with the same resume
        # button) so the chat keeps moving, and log the underlying cause.
        try:
            import logging
            logging.getLogger(__name__).warning(
                "legal_qa.answer_question failed: %s", e, exc_info=True)
        except Exception:
            pass
        fallback = (
            "**Answer:** I couldn't reach the legal-Q&A engine just now — "
            "please retry in a moment, or check the [Legal Library](/library) "
            "for the relevant Act.\n\n"
            "**Citation:** _(engine error: " + str(e)[:120] + ")_"
        )
        if resume_quick:
            fallback += f"\n\n<!--quickreplies:{_json.dumps(resume_quick)}-->"
        return fallback
