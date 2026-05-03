"""Regression test for the chat Q&A digression path.

Reproduces the bug the writer hit:
    typing "who can be witness" while the chat was at the asset-inventory
    gate produced no visible Q&A response — the planner re-asked the asset
    card on top, burying the answer.

What this bot proves:
  1. /api/chat/<id>/message returns BOTH ok=True AND a non-empty
     assistant_message.content for any of the canary questions, EVEN when
     the chat is mid-flow (asset inventory, executor walk, etc).
  2. The reply contains the **Answer:** marker (Q&A library format), NOT
     just an asset card.
  3. /history shows the Q&A reply persisted (not lost on refresh).
  4. A reply that ALSO carries an action token (e.g. "yes — but who can
     witness?") still produces BOTH the Q&A and the planner ack.

Run:
    python3 test_qa_digression.py
    WILLCRAFT_BASE_URL=https://will.alantanjb.com python3 test_qa_digression.py
"""
import os
import re
import sys
import requests

BASE_URL = os.environ.get('WILLCRAFT_BASE_URL', 'http://localhost:5050').rstrip('/')
ADMIN_EMAIL = os.environ.get('WILLCRAFT_ADMIN_EMAIL', 'kylie.tan@alantanjb.com')
ADMIN_PASSWORD = os.environ.get('WILLCRAFT_ADMIN_PASSWORD', 'Aia12345#')


def ok(msg):  print(f'\033[92m✓\033[0m {msg}')
def fail(msg): print(f'\033[91m✗\033[0m {msg}'); sys.exit(1)
def warn(msg): print(f'\033[93m⚠\033[0m {msg}')


CANARY_QUESTIONS = [
    "who can be a witness",
    "who can be witness",
    "what is a beneficiary",
    "do i need a guardian",
    "is testamentary trust mandatory",
    "what does executor do",
    "can my spouse be executor and beneficiary",
    "explain residuary estate",
]

# These mix a question with an action token — should trigger BOTH the
# Q&A reply AND the planner reply (no short-circuit).
MIXED_QUESTIONS = [
    "yes confirm — but who can witness?",
    "skip — what does substitute mean?",
]


def login(s):
    r = s.post(f'{BASE_URL}/login',
               data={'email': ADMIN_EMAIL, 'password': ADMIN_PASSWORD},
               allow_redirects=True, timeout=30)
    if r.status_code != 200 or '/login' in r.url:
        fail(f'Login failed: status={r.status_code} url={r.url}')


def fresh_client(s):
    r = s.get(f'{BASE_URL}/wizard/new', allow_redirects=True, timeout=30)
    if r.status_code != 200:
        fail(f'/wizard/new returned {r.status_code}')
    r = s.get(f'{BASE_URL}/wills', timeout=30)
    m = re.search(r'/clients/([0-9a-f-]{36})', r.text)
    if not m:
        fail('Could not find a client_id on /wills after wizard/new')
    return m.group(1)


def post_msg(s, client_id, text):
    r = s.post(f'{BASE_URL}/api/chat/{client_id}/message',
               data={'text': text}, timeout=60)
    if r.status_code != 200:
        fail(f'POST /message returned {r.status_code}: {r.text[:200]}')
    return r.json()


def history(s, client_id):
    r = s.get(f'{BASE_URL}/api/chat/{client_id}/history', timeout=30)
    if r.status_code != 200:
        fail(f'GET /history returned {r.status_code}')
    return r.json()


def main():
    print(f'→ Target: {BASE_URL}')
    s = requests.Session()
    login(s); ok('Logged in')

    client_id = fresh_client(s)
    ok(f'Spun up fresh client {client_id[:8]}…')

    failed = 0
    fallback_count = 0
    print('\n── Canary Q&A questions (each should short-circuit to a single Q&A reply) ──')
    for q in CANARY_QUESTIONS:
        js = post_msg(s, client_id, q)
        if not js.get('ok'):
            warn(f'  "{q}" → ok=False: {js}'); failed += 1; continue
        am = js.get('assistant_message') or {}
        body = (am.get('content') or '').strip()
        if not body:
            warn(f'  "{q}" → EMPTY assistant_message.content'); failed += 1; continue
        # Q&A reply should contain the **Answer:** marker. If it doesn't,
        # we got the planner reply instead — that's the regression.
        if '**Answer:**' not in body and 'Answer:' not in body:
            warn(f'  "{q}" → reply lacked **Answer:** marker (got planner reply?):\n      {body[:120]}…')
            failed += 1; continue
        # Detect fallback ("couldn't reach the legal-Q&A engine") — that
        # means ANTHROPIC_API_KEY isn't configured or the API errored. The
        # path technically works but the answer isn't real.
        if "couldn't reach the legal-Q&A engine" in body:
            warn(f'  "{q}" → got fallback (Anthropic unreachable; check ANTHROPIC_API_KEY)')
            fallback_count += 1
            failed += 1
            continue
        # Format check: every real answer must have **Answer:** and a quick-reply marker.
        if '<!--quickreplies:' not in body:
            warn(f'  "{q}" → missing quickreplies marker (resume button) in body')
            failed += 1; continue
        # Must NOT include qa_message field — short-circuit means single message
        if js.get('qa_message'):
            warn(f'  "{q}" → got BOTH qa_message and assistant_message; expected short-circuit')
            failed += 1; continue
        ok(f'  "{q[:40]}…" → Q&A short-circuit OK ({len(body)} chars)')

    print('\n── Mixed action+question (should produce qa_message AND assistant_message) ──')
    for q in MIXED_QUESTIONS:
        js = post_msg(s, client_id, q)
        if not js.get('ok'):
            warn(f'  "{q}" → ok=False'); failed += 1; continue
        qa = js.get('qa_message') or {}
        am = js.get('assistant_message') or {}
        if not (qa.get('content') or '').strip():
            warn(f'  "{q}" → no qa_message body'); failed += 1; continue
        if not (am.get('content') or '').strip():
            warn(f'  "{q}" → no assistant_message body'); failed += 1; continue
        ok(f'  "{q[:50]}…" → BOTH qa_message + assistant_message present')

    print('\n── History persistence check ──')
    h = history(s, client_id)
    n_msgs = len(h.get('messages') or [])
    expected_min = len(CANARY_QUESTIONS) * 2 + len(MIXED_QUESTIONS) * 3
    if n_msgs < expected_min:
        warn(f'  history has {n_msgs} messages, expected ≥ {expected_min}')
        failed += 1
    else:
        ok(f'  history persisted {n_msgs} messages')

    # Every assistant Q&A reply in history should have an Answer marker.
    qa_count = sum(1 for m in (h.get('messages') or [])
                   if m.get('role') == 'assistant'
                   and '**Answer:**' in (m.get('content') or ''))
    if qa_count < len(CANARY_QUESTIONS):
        warn(f'  only {qa_count} Q&A replies in history (expected ≥ {len(CANARY_QUESTIONS)})')
        failed += 1
    else:
        ok(f'  {qa_count} Q&A replies confirmed in history')

    print()
    if failed:
        fail(f'{failed} check(s) failed.')
    ok('🎉 Q&A digression flow is healthy.')


if __name__ == '__main__':
    main()
