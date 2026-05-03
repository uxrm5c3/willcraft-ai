"""Answer side-quest legal/process questions from the chat user without
advancing the directed-flow stage. Always nudges them back to the step
they were on.
"""
import re
import time
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_FAST

# ── Module-level Q&A cache ──────────────────────────────────────────────
# Same question → same answer, served from RAM. Saves a full Anthropic
# round-trip + several KB of excerpt tokens for repeat asks like
# "who can be witness", "what is geran", "what's RPGT". Bounded LRU-ish:
# expire after _CACHE_TTL seconds, evict oldest when over _CACHE_MAX.
_ANSWER_CACHE: dict = {}     # {normalised_q: (timestamp, answer_body)}
_CACHE_TTL = 60 * 60 * 24    # 24 hours
_CACHE_MAX = 200


def _cache_key(text: str) -> str:
    """Normalise so 'Who can be witness?' and 'who can be witness' hit
    the same cache slot."""
    t = (text or '').strip().lower()
    t = re.sub(r'[^\w\s%]', '', t)   # strip punctuation
    t = re.sub(r'\s+', ' ', t)
    return t


def _cache_get(key: str):
    hit = _ANSWER_CACHE.get(key)
    if not hit:
        return None
    ts, body = hit
    if time.time() - ts > _CACHE_TTL:
        _ANSWER_CACHE.pop(key, None)
        return None
    return body


def _cache_set(key: str, body: str):
    if not key or not body:
        return
    if len(_ANSWER_CACHE) >= _CACHE_MAX:
        # Evict oldest
        oldest = min(_ANSWER_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _ANSWER_CACHE.pop(oldest, None)
    _ANSWER_CACHE[key] = (time.time(), body)


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
    """Short Claude answer + nudge back to the active step. Returns a
    fallback message on failure (never silent ''). Cached by question
    text so repeats are instant + free."""
    if not user_text:
        return ''
    # Build a "↩ Resume" quick-reply for the current step so the user can
    # one-tap back to the workflow after the digression.
    import json as _json
    resume_quick = []
    if current_stage_summary:
        resume_quick = [
            {'label': f"↩ Resume {current_stage_summary}", 'value': 'continue'},
            {'label': 'Stay here, I have more questions', 'value': 'not yet'},
        ]

    def _attach_resume(body: str) -> str:
        if resume_quick:
            return body + f"\n\n<!--quickreplies:{_json.dumps(resume_quick)}-->"
        return body

    # ── Cache hit? Return instantly, no Anthropic call. ────────────────
    ck = _cache_key(user_text)
    cached = _cache_get(ck)
    if cached:
        return _attach_resume(cached)

    matched_titles = []
    library_titles = []
    try:
        from services.legal_library import list_available_acts
        library_titles = [a['title'] for a in list_available_acts()]
    except Exception:
        pass
    try:
        # Hard 25s timeout so we fail fast instead of hanging the chat.
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=25.0)
        # ── Step A: identify the SINGLE most likely Act first ────────────
        # Cheap regex topic-match. Then read only that Act and grab the
        # best section. Falls back to broad keyword search across top-3
        # Acts only if no topic hint matches. This cuts both tokens AND
        # latency dramatically vs. the old "scan all 23 Acts" approach.
        focused = None
        try:
            from services.legal_library import identify_act, section_excerpt
            focus_slug = identify_act(user_text)
            if focus_slug:
                focused = section_excerpt(focus_slug, user_text)
        except Exception:
            focused = None
        nudge = (f"\n\nNow back to **{current_stage_summary}** — please continue."
                 if current_stage_summary else "")
        # ── Step B: build excerpts_block ─────────────────────────────────
        # Prefer the focused excerpt from Step A if we found one (single
        # Act, single section — cleanest possible context). Otherwise fall
        # back to broad keyword scan across top-3 Acts.
        excerpts_block = ''
        ex = []
        if focused:
            matched_titles = [focused['title']]
            excerpts_block = (
                "\n\nRELEVANT ACT PROVISION — this is the ONLY excerpt the "
                "library returned for this question. If your answer is not "
                "directly supported by it, say you don't know.\n"
                f"\n--- {focused['title']} ---\n{focused['excerpt']}\n"
            )
            ex = [focused]
        else:
            try:
                from services.legal_library import relevant_excerpts
                ex = relevant_excerpts(user_text)
                if ex:
                    matched_titles = [e['title'] for e in ex]
                    excerpts_block = (
                        "\n\nRELEVANT ACT PROVISIONS — cite the Act + "
                        "section ONLY if the section text directly supports "
                        "the answer. Do not infer or generalise:\n"
                    )
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
        # Prompt — anti-hallucination first, format second.
        #
        # Rules (per client mandate "NO hallucination"):
        #   1. If the excerpts above directly answer the question → answer
        #      with a precise Act + section citation.
        #   2. If the excerpts are tangentially related but don't actually
        #      answer it → say "I'm not certain" and point to the Act for
        #      the user to verify. Do NOT fabricate a section number.
        #   3. If you have no excerpt and the answer is not basic Malaysian
        #      legal common knowledge → say "I don't know" upfront. Do NOT
        #      guess.
        #   4. If you DO know it as basic common knowledge (e.g. "Geran is
        #      the land title") → answer plainly with NO citation line.
        msg = client.messages.create(
            model=CLAUDE_MODEL_FAST,
            max_tokens=300,
            messages=[{
                'role': 'user',
                'content': f"""You help a Malaysian advisor draft a non-Muslim will.{excerpts_block}

USER QUESTION: {user_text}

ANTI-HALLUCINATION RULES (highest priority):
- If the excerpts above DIRECTLY answer the question → cite "<Act> s.<n>".
- If the excerpts are only tangentially related → say "I'm not 100% sure" and point the user to the Act for verification. NEVER invent a section number.
- If you have NO excerpt AND the answer is not basic terminology common knowledge → reply with **Answer:** "I don't know — please consult a lawyer or check the relevant Act in the [Legal Library](/library)." and stop. No fake example, no guessed citation.
- Confidence over completeness. A short "I don't know" beats a wrong answer.

Reply in this EXACT format (no preface, no filler, no apologies):

**Answer:** <one or two short plain-English sentences — maximum 40 words. Direct. Use everyday Malaysian terms (Geran, Strata Title) when relevant. If you don't know, say so HERE.>

**Citation:** <ONLY include this line if you cite a SPECIFIC Act + section number from the excerpts above (e.g. "Wills Act 1959 s.5(2)"). Otherwise OMIT this line entirely. NEVER write "General knowledge", "not in library", "N/A", "not specified", "based on common practice", or any cop-out — just leave the line out.>

**Example:** <one concrete Malaysian example — max 25 words. Skip this line if you said "I don't know" or no useful example fits.>

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
        # Detect "I don't know" up-front so we strip Citation+Example and
        # don't pretend we have evidence we don't.
        body_lower = body.lower()
        is_dont_know = any(p in body_lower for p in (
            "i don't know", 'i do not know', "i'm not certain",
            "i am not certain", "not 100% sure", 'not entirely sure',
            'cannot answer with certainty', 'unable to answer',
        ))
        for ln in lines:
            stripped_ln = ln.strip()
            # Detect a citation line (with or without ** **)
            cite_match = _re_post.match(r'^\*?\*?citation\*?\*?\s*:\s*(.*)$',
                                        stripped_ln, _re_post.IGNORECASE)
            if cite_match:
                cite_value = cite_match.group(1).strip().rstrip('.').lower()
                # Empty value, cop-out, OR don't-know answer → drop line
                if (not cite_value
                    or is_dont_know
                    or any(_re_post.search(p, cite_value) for p in _COPOUT_PATTERNS)):
                    continue
                real_citation = True
            # Strip Example line too if don't-know
            if is_dont_know and _re_post.match(
                    r'^\*?\*?example\*?\*?\s*:', stripped_ln, _re_post.IGNORECASE):
                continue
            kept.append(ln)
        # Collapse 3+ consecutive newlines down to 2 after pruning
        body = _re_post.sub(r'\n{3,}', '\n\n', '\n'.join(kept)).strip()

        # Footer ONLY when we kept a real citation AND we're confident.
        if real_citation and matched_titles and not is_dont_know:
            footer = f"\n\n<sub>📚 _Cited from library: {', '.join(matched_titles)}_</sub>"
        else:
            footer = ''

        out = body + footer
        # Cache the body+footer (NOT the per-call resume button) so the next
        # asker of the same question gets it instantly — no LLM round-trip.
        _cache_set(ck, out)
        return _attach_resume(out)
    except Exception as e:
        # NEVER return empty silently. Surface a friendly fallback (with the
        # same resume button) so the chat keeps moving, and log the cause.
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
            "_(engine error: " + str(e)[:120] + ")_"
        )
        return _attach_resume(fallback)
