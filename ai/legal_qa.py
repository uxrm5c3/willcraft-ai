"""Answer side-quest legal/process questions from the chat user without
advancing the directed-flow stage. Always nudges them back to the step
they were on.
"""
import re
import time
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_CHEAP

# ── Two-tier Q&A cache ──────────────────────────────────────────────────
# L1: in-process RAM dict — sub-millisecond, but per-worker and wiped on
#     restart.
# L2: LegalQACache table — shared across users, testators, workers, and
#     deploys. Survives restarts. Keyed by a normalised question key.
#
# Lookup order: L1 → L2 → Anthropic.
# On L2 hit we backfill L1 so subsequent asks in the same process are
# zero-DB. On Anthropic hit we write through to BOTH tiers.
_ANSWER_CACHE: dict = {}     # {normalised_q: (timestamp, answer_body)}
_CACHE_TTL = 60 * 60 * 24    # 24h L1 freshness; L2 doesn't expire
_CACHE_MAX = 200


def _cache_key(text: str) -> str:
    """Normalise so 'Who can be witness?' and 'who can be witness' hit
    the same cache slot."""
    t = (text or '').strip().lower()
    t = re.sub(r'[^\w\s%]', '', t)   # strip punctuation
    t = re.sub(r'\s+', ' ', t)
    return t[:500]  # match LegalQACache.question_key column width


def _cache_get(key: str):
    """Two-tier read: RAM first, DB second. Returns answer text or None."""
    if not key:
        return None
    # L1
    hit = _ANSWER_CACHE.get(key)
    if hit:
        ts, body = hit
        if time.time() - ts <= _CACHE_TTL:
            return body
        _ANSWER_CACHE.pop(key, None)
    # L2 — DB
    try:
        from database import db, LegalQACache
        from datetime import datetime as _dt
        row = LegalQACache.query.filter_by(question_key=key).first()
        if row and row.answer_text:
            # Bump usage stats + backfill L1
            try:
                row.hits = (row.hits or 0) + 1
                row.last_used_at = _dt.utcnow()
                db.session.commit()
            except Exception:
                db.session.rollback()
            _ANSWER_CACHE[key] = (time.time(), row.answer_text)
            return row.answer_text
    except Exception:
        pass
    return None


def _cache_set(key: str, body: str, *, question_text: str = '',
               cited_act: str = None, mode: str = 'library'):
    """Two-tier write: RAM + DB upsert."""
    if not key or not body:
        return
    # L1 with eviction
    if len(_ANSWER_CACHE) >= _CACHE_MAX:
        oldest = min(_ANSWER_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _ANSWER_CACHE.pop(oldest, None)
    _ANSWER_CACHE[key] = (time.time(), body)
    # L2 — DB upsert
    try:
        from database import db, LegalQACache
        from datetime import datetime as _dt
        row = LegalQACache.query.filter_by(question_key=key).first()
        now = _dt.utcnow()
        if row:
            # Refresh stored answer if it changed (e.g. library updated)
            row.answer_text = body
            row.cited_act = cited_act or row.cited_act
            row.mode = mode or row.mode
            row.last_used_at = now
        else:
            row = LegalQACache(
                question_key=key,
                question_text=(question_text or '')[:5000],
                answer_text=body,
                cited_act=cited_act,
                mode=mode,
                hits=0,            # 0 because this counts cache HITS, not initial save
                created_at=now,
                last_used_at=now,
            )
            db.session.add(row)
        db.session.commit()
    except Exception:
        try:
            from database import db as _db
            _db.session.rollback()
        except Exception:
            pass


def cache_clear_all() -> int:
    """Admin: purge both cache tiers (e.g. after a library update). Returns
    the number of DB rows removed."""
    _ANSWER_CACHE.clear()
    try:
        from database import db, LegalQACache
        n = LegalQACache.query.delete()
        db.session.commit()
        return int(n or 0)
    except Exception:
        try:
            from database import db as _db
            _db.session.rollback()
        except Exception:
            pass
        return 0


def _parse_suggested_quickreplies(body: str) -> list:
    """Mode E parser: pull the 2-3 reformulated questions out of a
    ``**Suggested:**`` bullet list and turn them into chat quickreply
    buttons. Returns ``[]`` if the body isn't in Mode E.

    Used both on fresh answers AND on cache hits so the buttons survive
    caching."""
    if not body or 'suggested' not in body.lower():
        return []
    # Match "Suggested:", "Suggested questions:", "Suggested reformulations:"
    # — anything that starts with "Suggested" and ends in ":".
    sugg_re = re.compile(
        r'\*?\*?suggested[^\n:]*\*?\*?\s*:\s*\n+((?:\s*[-*]\s+.+\n?)+)',
        re.IGNORECASE)
    m = sugg_re.search(body + '\n')
    if not m:
        return []
    bullets = re.findall(r'^\s*[-*]\s+(.+?)\s*$', m.group(1), re.MULTILINE)
    out = []
    for q in bullets[:3]:
        q = q.strip().strip('"').strip("'")
        if q:
            label = q if len(q) <= 80 else q[:77] + '…'
            out.append({'label': label, 'value': q})
    if out:
        out.append({'label': 'Skip — none of these', 'value': 'continue'})
    return out


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

    def _attach_resume(body: str, extra_quick: list = None) -> str:
        # Merge any extra quickreplies (e.g. Mode E suggested questions)
        # in front of the resume buttons so they're the obvious next tap.
        # If extra_quick wasn't provided, auto-extract from the body so
        # cache hits keep their suggestion buttons.
        if extra_quick is None:
            extra_quick = _parse_suggested_quickreplies(body)
        combined = list(extra_quick or []) + list(resume_quick or [])
        if combined:
            return body + f"\n\n<!--quickreplies:{_json.dumps(combined)}-->"
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
        # Prompt — five response modes. EVERY answer (except Mode E) must end
        # with a **Source:** line so the user knows where the answer came
        # from — Act + section, well-known Malaysian practice, or a web URL.
        #
        # Mode A — Library Act excerpt directly answers the question.
        # Mode B — Well-established Malaysian legal/property practice.
        # Mode C — Partial / unsure.
        # Mode D — Answered via web_search server tool (cite URL).
        # Mode E — Truly cannot answer → 2-3 alternative questions as
        #          quick-reply buttons. NO "consult a lawyer / Legal Library"
        #          dead-end — give the user concrete next-tap options.
        prompt_text = f"""You help a Malaysian advisor draft a non-Muslim will.{excerpts_block}

USER QUESTION: {user_text}

EVERY answer (except Mode E) MUST end with a single **Source:** line so the user trusts where it came from. NO bare assertions.

FIVE RESPONSE MODES — pick exactly ONE:

MODE A — Library Act excerpt above DIRECTLY answers the question:
  **Answer:** <plain-English, ≤50 words>
  **Example:** <one concrete Malaysian example, ≤25 words>
  **Source:** <ONE specific section, e.g. "Wills Act 1959 s.5(2)">

MODE B — Well-established Malaysian legal / property / probate / banking
  practice (the kind of thing a practising Malaysian lawyer or licensed
  estate planner states without checking — e.g. "Geran is the land
  title", "executor administers the estate", "land titles can be
  subdivided by applying to the Pejabat Tanah under the National Land
  Code", "EPF nomination overrides the will for EPF moneys"):
  **Answer:** <plain-English, ≤70 words>
  **Example:** <one concrete Malaysian example, ≤25 words>
  **Source:** Established Malaysian estate-planning practice<optional: " (broadly under <Act name>)">
  → BE GENEROUS with this mode. If it's standard practice knowledge,
    answer it. Do NOT punt just because the library excerpt didn't fire.

MODE C — Partial confidence (you know the gist but not the exact rule):
  **Answer:** Probably <answer> — but please verify with <Act name if known> or a lawyer.
  **Source:** Partial knowledge — needs verification

MODE D — Web search (you used the web_search tool to find a current,
  authoritative source on a Malaysian site like agc.gov.my / lawnet /
  iproperty / star / nst / a known law-firm blog):
  **Answer:** <plain-English, ≤70 words, based on what the search returned>
  **Example:** <one concrete Malaysian example, ≤25 words> (optional)
  **Source:** <Page title> — <URL>
  → Only ONE source URL. Pick the most authoritative.

MODE E — Truly outside scope, OR question is ambiguous and you'd be
  guessing what they meant:
  **Answer:** I'm not certain what you're asking. Did you mean one of these?
  **Suggested:**
  - <complete reformulated question 1>
  - <complete reformulated question 2>
  - <complete reformulated question 3, optional>
  → DO NOT say "consult a lawyer" or "check the legal library".
  → DO NOT include a Source line.
  → Suggestions must be complete questions the user can re-ask verbatim.

Decision order: try A → B → (call web_search if helpful) D → C → E.
Use web_search proactively for procedural / fee / form questions where
you don't have firm knowledge — better to fetch a real source than to
write Mode E.

Anti-hallucination: NEVER invent a section number or URL. NEVER write
"General knowledge", "not in library", "N/A", "not specified" — banned.

No preface ("I'll search…", "Let me check…"), no filler, no apologies, no "I hope this helps", no mode label ("MODE B"). Output ONLY the **Answer:** / **Example:** / **Source:** lines (or **Suggested:** for Mode E)."""

        # Try with the web_search server tool enabled. Falls back to a
        # tool-less retry if the account / model doesn't have web_search
        # access.
        def _call(use_web: bool):
            kwargs = dict(
                model=CLAUDE_MODEL_CHEAP,
                max_tokens=900,
                messages=[{'role': 'user', 'content': prompt_text}],
            )
            if use_web:
                kwargs['tools'] = [{
                    'type': 'web_search_20250305',
                    'name': 'web_search',
                    'max_uses': 2,
                }]
            return client.messages.create(**kwargs)

        try:
            msg = _call(use_web=True)
        except Exception as web_err:
            try:
                import logging
                logging.getLogger(__name__).info(
                    "web_search disabled / unsupported, retrying without: %s", web_err)
            except Exception:
                pass
            msg = _call(use_web=False)

        # Log cost (best-effort — never raises into caller).
        try:
            from ai.cost_tracker import log_usage
            log_usage(msg, call_site='ai.legal_qa.answer_question')
        except Exception:
            pass

        # Concatenate all text blocks (skip server tool_use / tool_result).
        body_parts = []
        for block in (msg.content or []):
            if getattr(block, 'type', None) == 'text':
                t = getattr(block, 'text', '') or ''
                if t.strip():
                    body_parts.append(t)
        body = '\n'.join(body_parts).strip()

        # ── Post-process: strip cop-out Source/Citation lines ─────────────
        import re as _re_post
        _COPOUT_PATTERNS = (
            r'^general knowledge\.?$', r'not in (the )?library',
            r'^not (specified|applicable|available)\.?$',
            r'^n/?a\.?$', r'^no (specific )?citation\.?$',
            r'^none\.?$', r'^not provided\.?$',
        )

        # Detect Mode E (suggested-questions fallback) so we don't strip
        # an Example line from a real answer just because the words "I'm
        # not certain" appear.
        body_lower = body.lower()
        is_suggest = ('**suggested:**' in body_lower
                      or '\nsuggested:' in body_lower)

        cited_act_value = None
        real_source = False
        kept = []
        # Strip leading preamble lines like "I'll search the web for..." or
        # bare "MODE B" / "**MODE D**" labels that sometimes leak through.
        _PREAMBLE_RE = _re_post.compile(
            r"^\s*(?:i['’]ll|i will|let me|searching|i need to)\b",
            _re_post.IGNORECASE)
        _MODE_LABEL_RE = _re_post.compile(
            r"^\s*\*?\*?mode\s+[a-e]\*?\*?\s*[:.\-—]?\s*$",
            _re_post.IGNORECASE)
        for ln in body.split('\n'):
            stripped_ln = ln.strip()
            if _MODE_LABEL_RE.match(stripped_ln):
                continue
            # Drop preamble ONLY if no answer line yet (preserves narrative
            # bodies that legitimately start with "I" later on).
            if not kept and _PREAMBLE_RE.match(stripped_ln):
                continue
            # Source/Citation line detection (accept either keyword)
            src_match = _re_post.match(
                r'^\*?\*?(source|citation)\*?\*?\s*:\s*(.*)$',
                stripped_ln, _re_post.IGNORECASE)
            if src_match:
                src_value = src_match.group(2).strip().rstrip('.')
                src_lower = src_value.lower()
                if (not src_value
                    or any(_re_post.search(p, src_lower) for p in _COPOUT_PATTERNS)):
                    continue
                real_source = True
                cited_act_value = src_value[:200]
                # Normalise label to "Source:"
                kept.append(f"**Source:** {src_value}")
                continue
            kept.append(ln)
        body = _re_post.sub(r'\n{3,}', '\n\n', '\n'.join(kept)).strip()

        # Telemetry mode label
        if is_suggest:
            cache_mode = 'suggest'
        elif real_source:
            cache_mode = 'sourced'
        elif matched_titles:
            cache_mode = 'unsure'
        else:
            cache_mode = 'general'

        out = body
        _cache_set(ck, out, question_text=user_text,
                   cited_act=cited_act_value, mode=cache_mode)
        # _attach_resume re-parses Mode E bullets so cache hits also get
        # the suggestion buttons — no need to thread them through here.
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
            "please retry in a moment.\n\n"
            "_(engine error: " + str(e)[:120] + ")_"
        )
        return _attach_resume(fallback)
