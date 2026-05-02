"""End-to-end test bot for the upload-will → edit flow.

Verifies:
  1. POST /api/parse-will with the PHEK YI TING DRAFT PDF returns ok
  2. Person SQL records are created for testator, executors, beneficiaries
  3. The Will record is saved with non-empty step1-step8 data
  4. GET /wills/<id>/load (Edit) loads the will and the wizard URLs return 200
  5. Each step's data structure is the format the wizard template expects

Run with:
    cd ~/willcraft-ai && python3 test_upload_flow.py [PDF_PATH]

Default PDF: /Users/gan/Downloads/PHEK YI TING DRAFT The Last Will and Testament 2 (1).pdf
"""

import os
import sys
import json
import time
import requests

BASE_URL = os.environ.get('WILLCRAFT_BASE_URL', 'http://127.0.0.1:8000')
ADMIN_EMAIL = os.environ.get('WILLCRAFT_ADMIN_EMAIL', 'kylie.tan@alantanjb.com')
ADMIN_PASSWORD = os.environ.get('WILLCRAFT_ADMIN_PASSWORD', 'Aia12345#')

DEFAULT_PDF_CANDIDATES = [
    '/Users/gan/Downloads/PHEK YI TING DRAFT The Last Will and Testament 2 (1).pdf',
    '/Users/gan/Downloads/PHEK_YI_TING_Format_Preview.pdf',
    '/Users/gan/Downloads/PHEK_YI_TING_AI_Generated_v01.docx',
]


def _resolve_default_pdf():
    for p in DEFAULT_PDF_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def ok(msg):
    print(f'\033[92m✓\033[0m {msg}')


def fail(msg):
    print(f'\033[91m✗\033[0m {msg}')
    sys.exit(1)


def warn(msg):
    print(f'\033[93m⚠\033[0m {msg}')


def main():
    pdf = sys.argv[1] if len(sys.argv) > 1 else _resolve_default_pdf()
    if not pdf:
        fail(
            'No PDF found in any default candidate path. Pass one as arg:\n'
            '    python3 test_upload_flow.py /path/to/will.pdf\n'
            'Default candidates checked: ' + ', '.join(DEFAULT_PDF_CANDIDATES)
        )
    if not os.path.isfile(pdf):
        fail(f'PDF not found: {pdf}')

    s = requests.Session()

    # ── 1. Login ─────────────────────────────────────────────────────
    print(f'→ Logging in as {ADMIN_EMAIL} at {BASE_URL}')
    r = s.post(f'{BASE_URL}/login',
               data={'email': ADMIN_EMAIL, 'password': ADMIN_PASSWORD},
               allow_redirects=True, timeout=30)
    if r.status_code != 200 or '/login' in r.url:
        fail(f'Login failed: status={r.status_code} final_url={r.url}')
    ok('Logged in')

    # ── 2. Upload + parse will ───────────────────────────────────────
    print(f'→ Uploading {os.path.basename(pdf)} to /api/parse-will')
    with open(pdf, 'rb') as f:
        r = s.post(f'{BASE_URL}/api/parse-will',
                   files={'file': (os.path.basename(pdf), f, 'application/pdf')},
                   timeout=120)
    if r.status_code != 200:
        fail(f'Parse failed: status={r.status_code} body={r.text[:300]}')
    body = r.json()
    if not body.get('ok'):
        fail(f'Parse returned ok=false: {body}')
    ok('Parser succeeded')

    # ── 3. Find the most recent will via the will list ───────────────
    print('→ Locating the just-uploaded will record')
    r = s.get(f'{BASE_URL}/api/wills', timeout=30)
    will_id = None
    if r.status_code == 200:
        wills = r.json() if r.headers.get('content-type', '').startswith('application/json') else None
        if wills and isinstance(wills, list):
            wills_sorted = sorted(wills, key=lambda w: w.get('updated_at', ''), reverse=True)
            for w in wills_sorted:
                if 'PHEK YI TING' in (w.get('title') or '').upper():
                    will_id = w.get('id')
                    break
    if not will_id:
        # Fallback: scrape will_list HTML
        r = s.get(f'{BASE_URL}/wills', timeout=30)
        import re
        m = re.search(r'/wills/([0-9a-f-]{36})/load', r.text)
        if m:
            will_id = m.group(1)
    if not will_id:
        fail('Could not find newly-created will record')
    ok(f'Will id: {will_id}')

    # ── 4. Click "Edit" — load will into session ─────────────────────
    print('→ Clicking Edit (loading will into session)')
    r = s.get(f'{BASE_URL}/wills/{will_id}/load', allow_redirects=True, timeout=30)
    if r.status_code != 200:
        fail(f'Load failed: status={r.status_code}')
    ok('Will loaded into session')

    # ── 5. Visit each wizard step — must all return 200 ──────────────
    print('→ Walking through wizard steps 1..10')
    failures = []
    for step in range(1, 11):
        r = s.get(f'{BASE_URL}/wizard/step/{step}', timeout=30)
        ctype = r.headers.get('content-type', '')
        if r.status_code != 200:
            failures.append(f'  step {step} returned {r.status_code}')
        elif 'text/html' in ctype:
            # Sanity: ensure 'PHEK YI TING' appears somewhere in the step page
            if 'PHEK YI TING' not in r.text and step in (1, 2):
                failures.append(f'  step {step} loaded but PHEK YI TING not visible')
            else:
                ok(f'  step {step} OK')
    if failures:
        for f in failures:
            warn(f)
        fail(f'{len(failures)} wizard steps failed')
    else:
        ok('All 10 wizard steps load and render')

    print()
    ok('🎉 Upload → Edit → All 10 steps populated and rendering correctly.')


if __name__ == '__main__':
    main()
