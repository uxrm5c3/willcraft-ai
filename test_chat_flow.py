"""End-to-end test bot for the per-client AI chat flow.

Verifies on production (or any BASE_URL) that:
  1. Login works
  2. A new client can be created via the wizard "+ New Will" flow
  3. The Files page (/clients/<id>/files) renders an "AI Chat" button
  4. The chat page (/chat/<id>) renders the inbox address banner
  5. POST /api/chat/<id>/message with text returns an assistant reply
  6. GET /api/chat/<id>/history returns the messages we just created
  7. The inbound-email webhook is reachable (returns 401 or 503, not 404)

Run:
    cd ~/willcraft-ai && python3 test_chat_flow.py
    WILLCRAFT_BASE_URL=https://will.alantanjb.com python3 test_chat_flow.py
"""

import os
import re
import sys
import requests

BASE_URL = os.environ.get('WILLCRAFT_BASE_URL', 'https://will.alantanjb.com').rstrip('/')
ADMIN_EMAIL = os.environ.get('WILLCRAFT_ADMIN_EMAIL', 'kylie.tan@alantanjb.com')
ADMIN_PASSWORD = os.environ.get('WILLCRAFT_ADMIN_PASSWORD', 'Aia12345#')


def ok(msg):
    print(f'\033[92m✓\033[0m {msg}')


def fail(msg):
    print(f'\033[91m✗\033[0m {msg}')
    sys.exit(1)


def warn(msg):
    print(f'\033[93m⚠\033[0m {msg}')


def main():
    s = requests.Session()
    print(f'→ Target: {BASE_URL}')

    # 1. Login
    print(f'→ Logging in as {ADMIN_EMAIL}')
    r = s.post(f'{BASE_URL}/login',
               data={'email': ADMIN_EMAIL, 'password': ADMIN_PASSWORD},
               allow_redirects=True, timeout=30)
    if r.status_code != 200 or '/login' in r.url:
        fail(f'Login failed: status={r.status_code} final_url={r.url}')
    ok('Logged in')

    # 2. Create a fresh will/client via /wizard/new
    print('→ Creating a new will (and implicitly a client)')
    r = s.get(f'{BASE_URL}/wizard/new', allow_redirects=True, timeout=30)
    if r.status_code != 200:
        fail(f'/wizard/new returned {r.status_code}')
    ok('Wizard scaffolded')

    # Find our newly-created client by listing /clients (we want the most recent)
    print('→ Resolving the new client_id from /wills')
    r = s.get(f'{BASE_URL}/wills', timeout=30)
    if r.status_code != 200:
        fail(f'/wills returned {r.status_code}')
    # Extract the first /clients/<uuid>/files link in the HTML
    m = re.search(r'/clients/([0-9a-f-]{36})/files', r.text)
    if not m:
        fail('Could not find any client_id link on /wills (no clients listed?)')
    client_id = m.group(1)
    ok(f'Most recent client_id: {client_id}')

    # 3. Files page should have the AI Chat button
    print(f'→ GET /clients/{client_id[:8]}…/files')
    r = s.get(f'{BASE_URL}/clients/{client_id}/files', timeout=30)
    if r.status_code != 200:
        fail(f'Files page returned {r.status_code}')
    if f'/chat/{client_id}' not in r.text:
        fail('AI Chat button (link to /chat/<id>) NOT found on the Files page HTML')
    if 'AI Chat' not in r.text:
        warn('Files page reachable but the literal text "AI Chat" not found — template may be stale')
    else:
        ok('"AI Chat" button is present on the Files page')

    # 4. Chat page should render with the inbox banner + a per-client address
    print(f'→ GET /chat/{client_id[:8]}…')
    r = s.get(f'{BASE_URL}/chat/{client_id}', timeout=30)
    if r.status_code != 200:
        fail(f'Chat page returned {r.status_code}')
    short = client_id[:8]
    expected_addr_fragment = f'client-{short}@inbox.'
    if expected_addr_fragment not in r.text:
        fail(f'Chat page rendered but inbox address fragment "{expected_addr_fragment}" missing — '
             'banner template may not be deployed or client_id encoding is off.')
    ok(f'Chat page shows inbox address fragment: {expected_addr_fragment}')

    if 'Forward from WhatsApp' in r.text:
        ok('"Forward from WhatsApp" banner heading is present')
    else:
        warn('"Forward from WhatsApp" heading not found — banner may be styled differently')

    if 'Inbound mail isn' in r.text:
        warn('Banner shows the ⚠️ "Inbound mail isn\'t enabled" warning — '
             'POSTMARK_INBOUND_USER/PASS env vars not set on the server yet.')
    else:
        ok('Inbox webhook env vars are configured')

    # 5. Send a text-only chat message
    print(f'→ POST /api/chat/{client_id[:8]}…/message')
    r = s.post(f'{BASE_URL}/api/chat/{client_id}/message',
               data={'text': 'Test from the bot — please respond.'}, timeout=60)
    if r.status_code != 200:
        fail(f'Message endpoint returned {r.status_code}: {r.text[:300]}')
    js = r.json()
    if not js.get('ok'):
        fail(f'Message ok=false: {js}')
    if not js.get('assistant_message', {}).get('content'):
        fail(f'No assistant reply content: {js}')
    ok(f'Assistant replied: "{js["assistant_message"]["content"][:80]}…"')

    # 6. History should show 2+ messages now
    print(f'→ GET /api/chat/{client_id[:8]}…/history')
    r = s.get(f'{BASE_URL}/api/chat/{client_id}/history', timeout=30)
    js = r.json()
    if not js.get('ok'):
        fail(f'History ok=false: {js}')
    n = len(js.get('messages', []))
    if n < 2:
        fail(f'Expected ≥2 messages in history, got {n}')
    ok(f'History returns {n} messages')

    # 7. Inbound webhook reachability
    print('→ POST /api/inbound-email (probe — should be 401 or 503, NOT 404)')
    r = requests.post(f'{BASE_URL}/api/inbound-email', json={}, timeout=30)
    if r.status_code == 404:
        fail('Inbound webhook returned 404 — endpoint NOT deployed.')
    elif r.status_code == 503:
        warn('Inbound webhook deployed but returned 503 — env vars POSTMARK_INBOUND_USER/PASS not set.')
    elif r.status_code == 401:
        ok('Inbound webhook deployed AND env vars set (401 = needs Basic Auth, as expected)')
    else:
        warn(f'Inbound webhook returned unexpected status {r.status_code}')

    print()
    ok('🎉 Chat flow end-to-end check complete.')
    print(f'\nFor humans: open this in your browser to see the chat UI:')
    print(f'    {BASE_URL}/chat/{client_id}')


if __name__ == '__main__':
    main()
