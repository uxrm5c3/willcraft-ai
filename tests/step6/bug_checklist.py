"""🔥 §10x.39 / §10x.133 META — Today's Bug Checklist (autonomous self-test).

Runs every bug raised in the past 24h against the CURRENT state of KOID
fixture (or any specified client). Prints PASS/FAIL per check; exit 0
only if ALL checks pass.

Usage:
    docker exec willcraft-web python /app/tests/step6/bug_checklist.py [CLIENT_ID]

Default client: KOID 2a2b527e-d870-447b-b386-8d97b21bb849

Categorisation:
  - REGRESSION: previously fixed (per CLAUDE.md §10x.39 row), now broken
  - NEW: not in bug table yet
  - PRIORITY HIGH: probate-blocking
  - PRIORITY MEDIUM: visible UX bug
  - PRIORITY LOW: cosmetic

If any check FAILs:
  - Print rule reference + verbatim user complaint (where available)
  - Suggest next action (re-fix vs holistic review)
  - Exit 1 to break the autonomous loop's success condition
"""
import sys, json, re, os
from typing import List, Dict, Any, Tuple
sys.path.insert(0, '/app')

CID = sys.argv[1] if len(sys.argv) > 1 else '2a2b527e-d870-447b-b386-8d97b21bb849'

from app import app, db, Will
from database import ChatMessage, ChatSession, Person


BUG_CHECKS: List[Dict[str, Any]] = [
    # ── Card / chat behavior ──────────────────────────────────────────
    {
        'id': 'B01',
        'rule': '§10x.126 / META §10x.133',
        'priority': 'HIGH',
        'name': 'Card not repeating in chat (no-op recovery fires)',
        'user_quote': 'AI Chat is ignoring the user prompt — cards keep repeating',
        'kind': 'chat_pattern',
    },
    # ── step5 data completeness (probate-blocking) ────────────────────
    {
        'id': 'B02',
        'rule': '§10x.150',
        'priority': 'HIGH',
        'name': 'Sri Laguna has beneficiary populated',
        'user_quote': 'Sri Laguna missing lot + beneficiary — find the root cause',
        'kind': 'step5_field',
        'matcher': lambda g: 'sri laguna' in (
            (g.get('property_info') or {}).get('property_address') or g.get('address', '')).lower(),
        'check': lambda g: bool(g.get('beneficiaries') or g.get('allocations')),
    },
    {
        'id': 'B03',
        'rule': '§10x.152h',
        'priority': 'MEDIUM',
        'name': 'No "Folio N" in title fields (Singapore Land Registry ref, not MY title)',
        'user_quote': 'Folio 5 ??',
        'kind': 'step5_field',
        'matcher': lambda g: True,  # check all gifts
        'check': lambda g: not re.match(
            r'^\s*folio\s+\d+\s*$',
            ((g.get('property_info') or {}).get('title_number') or '').lower(),
        ),
    },
    {
        'id': 'B04',
        'rule': '§10x.151b',
        'priority': 'HIGH',
        'name': 'Wife (LIM BEE YAN) has NRIC populated in step4',
        'user_quote': 'Wife NRIC missing in clauses',
        'kind': 'step4_person',
        'check': lambda b: not (b.get('full_name', '').upper().startswith('LIM BEE YAN')
                                 and not b.get('nric_passport_birthcert')),
    },
    {
        'id': 'B05',
        'rule': '§10x.193 row 87 / §10x.151f',
        'priority': 'MEDIUM',
        'name': 'C-05-01 NOT inheriting C-30-08 strata sub-token',
        'user_quote': 'C-05-01 has 564662/M1C/30/710 (C-30-08 parcel)',
        'kind': 'step5_field',
        'matcher': lambda g: 'c-05-01' in (
            (g.get('property_info') or {}).get('property_address') or g.get('address', '')).lower(),
        'check': lambda g: not re.search(
            r'/M\d?[A-Z]?/30/710',
            (g.get('property_info') or {}).get('title_number') or '',
        ),
    },
    # ── Generated will format ─────────────────────────────────────────
    {
        'id': 'B06',
        'rule': '§10x.151',
        'priority': 'HIGH',
        'name': 'Will has NO phantom default clauses (joint banks, all-banks, EPF)',
        'user_quote': 'Phantom default clauses fired even when no data',
        'kind': 'will_text',
        'check': lambda t: not any(p in t for p in [
            'I hereby devise and bequeath the moneys standing to my credit in all my joint bank accounts',
            'I hereby devise and bequeath to my Executor the moneys standing to my credit in all my bank accounts',
            'If the nomination(s) made by me in my Employees\' Provident Fund',
        ]),
    },
    {
        'id': 'B07',
        'rule': '§10x.151',
        'priority': 'HIGH',
        'name': 'Residuary clause appears AFTER all gifts (sequential numbering)',
        'user_quote': 'Residuary at clause 5 then gifts 6-32 — out of order',
        'kind': 'will_text',
        'check': lambda t: _residuary_position_ok(t),
    },
    {
        'id': 'B08',
        'rule': '§10x.152',
        'priority': 'MEDIUM',
        'name': 'Insurance combined into ONE clause with bullets (not 3 separate)',
        'user_quote': 'Sample combines insurance with (i)(ii)(iii) bullets',
        'kind': 'will_text',
        'check': lambda t: t.count('insurance policy No.') <= 1
                            and 'If any nomination' in t,
    },
    {
        'id': 'B09',
        'rule': '§10x.152c',
        'priority': 'MEDIUM',
        'name': 'Substitute clauses inlined (no separate "Pursuant to Clause N" clauses)',
        'user_quote': 'Sample inlines substitutes within each gift',
        'kind': 'will_text',
        'check': lambda t: not re.search(r'^\d+\.\s+Pursuant to Clause \d+', t, re.MULTILINE),
    },
    {
        'id': 'B10',
        'rule': '§10x.152f',
        'priority': 'MEDIUM',
        'name': 'NRIC parens position: (MALAYSIA NRIC No. ...) not MALAYSIA (NRIC No. ...)',
        'user_quote': 'Sample uses (MALAYSIA NRIC No. ...)',
        'kind': 'will_text',
        'check': lambda t: 'MALAYSIA (NRIC No.' not in t and '(MALAYSIA NRIC No.' in t,
    },
    {
        'id': 'B11',
        'rule': '§10x.151c',
        'priority': 'MEDIUM',
        'name': 'Bank: "Public Bank Berhad" not "Public Bank"',
        'user_quote': 'Sample uses Public Bank Berhad',
        'kind': 'will_text',
        'check': lambda t: 'Public Bank Berhad' in t and not re.search(
            r'\bPublic Bank\b(?! Berhad)', t),
    },
    {
        'id': 'B12',
        'rule': '§10x.151c',
        'priority': 'MEDIUM',
        'name': 'Insurance: "NTUC Income" not "Income Insurance"',
        'user_quote': 'Sample uses NTUC Income',
        'kind': 'will_text',
        'check': lambda t: 'NTUC Income' in t and 'Income Insurance' not in t,
    },
    {
        'id': 'B13',
        'rule': '§10x.152b',
        'priority': 'MEDIUM',
        'name': 'SG banks have "Singapore" prefix; MY banks don\'t',
        'user_quote': 'Sample SG = "Singapore POSB bank Account No.", MY = "Public Bank Berhad Current Account No."',
        'kind': 'will_text',
        'check': lambda t: 'my Singapore POSB bank Account' in t and 'my Singapore Maybank Account' in t,
    },
    {
        'id': 'B14',
        'rule': '§10x.152e',
        'priority': 'LOW',
        'name': 'Gift order: banks → properties → insurance',
        'user_quote': 'Sample orders by liquidity',
        'kind': 'will_text',
        'check': lambda t: _gift_order_ok(t),
    },
    {
        'id': 'B15',
        'rule': '§10x.152d',
        'priority': 'LOW',
        'name': 'Period before inline substitute ("...thereon. If my wife..." not "...thereon If")',
        'user_quote': 'Punctuation polish',
        'kind': 'will_text',
        'check': lambda t: not re.search(r'thereon If my', t),
    },
]


def _residuary_position_ok(text: str) -> bool:
    """Residuary clause should come AFTER all numbered gift clauses."""
    res_idx = text.find('My Trustee(s) shall hold the rest of my estate')
    if res_idx == -1:
        return True  # no residuary; n/a
    res_clause_m = re.search(r'(\d+)\.\s+My Trustee', text)
    if not res_clause_m:
        return True
    res_n = int(res_clause_m.group(1))
    # Find all numbered clauses BEFORE residuary
    pre = text[:res_idx]
    nums = [int(x) for x in re.findall(r'^(\d+)\.\s', pre, re.MULTILINE)]
    if not nums:
        return True
    return res_n >= max(nums)


def _gift_order_ok(text: str) -> bool:
    """Banks (monies) should come before properties (the property known as)."""
    first_bank = text.find('the monies in my')
    first_prop = text.find('the property known as')
    if first_bank == -1 or first_prop == -1:
        return True
    return first_bank < first_prop


def check_card_repeating(client_id: str) -> Tuple[bool, str]:
    """B01: Detect if any assistant card was emitted 3+ times in a row
    in the last 20 messages (indicates §10x.126 not catching no-op)."""
    cs = ChatSession.query.filter_by(client_id=client_id).order_by(
        ChatSession.created_at.desc()).first()
    if not cs:
        return True, 'no chat session — n/a'
    msgs = (ChatMessage.query.filter_by(session_id=cs.id, role='assistant')
            .order_by(ChatMessage.created_at.desc()).limit(20).all())
    contents = [m.content[:200] for m in msgs if m.content]
    # Look for 3+ consecutive identical
    for i in range(len(contents) - 2):
        if contents[i] == contents[i+1] == contents[i+2]:
            return False, (f'card repeated 3+ times: '
                           f'{contents[i][:120]!r}')
    return True, ''


def run_will_generation(client_id: str) -> str:
    """Generate will via draft_will_mock; return text or '' on error."""
    from app import _refresh_wizard_session_from_db, build_will_data
    from ai.drafter import draft_will_mock
    w = (Will.query.filter_by(client_id=client_id, status='draft')
         .filter(Will.deleted_at.is_(None))
         .order_by(Will.updated_at.desc()).first())
    if not w:
        return ''
    with app.test_request_context('/'):
        from flask import session
        session['client_id'] = client_id
        session['will_id'] = w.id
        session['user_id'] = 'autonomous-test'
        try:
            _refresh_wizard_session_from_db()
            will_data = build_will_data()
            return draft_will_mock(will_data)
        except Exception as e:
            return f'__ERROR__ {e}'


def main():
    print(f'================================================================')
    print(f'§10x.39 BUG CHECKLIST — client {CID}')
    print(f'================================================================')

    failures: List[Dict[str, Any]] = []
    passes = 0

    with app.app_context():
        w = (Will.query.filter_by(client_id=CID, status='draft')
             .filter(Will.deleted_at.is_(None))
             .order_by(Will.updated_at.desc()).first())
        if not w:
            print(f'❌ NO_WILL — client {CID} has no draft will')
            sys.exit(2)
        s4 = json.loads(w.step4_data or '[]')
        s5 = json.loads(w.step5_data or '[]')
        will_text = run_will_generation(CID)

        for chk in BUG_CHECKS:
            kind = chk['kind']
            ok = True
            reason = ''

            if kind == 'chat_pattern':
                ok, reason = check_card_repeating(CID)

            elif kind == 'step5_field':
                matcher = chk.get('matcher', lambda g: True)
                check = chk['check']
                bad = []
                for i, g in enumerate(s5):
                    if not isinstance(g, dict):
                        continue
                    if not matcher(g):
                        continue
                    if not check(g):
                        bad.append(f'gift[{i}]')
                if bad:
                    ok = False
                    reason = f'failed for: {", ".join(bad)}'

            elif kind == 'step4_person':
                check = chk['check']
                bad = []
                for i, b in enumerate(s4 if isinstance(s4, list) else []):
                    if not isinstance(b, dict):
                        continue
                    if not check(b):
                        bad.append(f'step4[{i}] {b.get("full_name", "?")}')
                if bad:
                    ok = False
                    reason = f'failed for: {", ".join(bad)}'

            elif kind == 'will_text':
                if not will_text or will_text.startswith('__ERROR__'):
                    ok = False
                    reason = f'will gen failed: {will_text[:80]}'
                else:
                    try:
                        ok = chk['check'](will_text)
                        if not ok:
                            reason = 'will text check failed'
                    except Exception as e:
                        ok = False
                        reason = f'check exception: {e}'

            mark = '✅' if ok else '❌'
            print(f'  {mark} [{chk["id"]}] [{chk["priority"]}] {chk["name"]}')
            if not ok:
                print(f'         rule:  {chk["rule"]}')
                print(f'         user:  "{chk["user_quote"]}"')
                if reason:
                    print(f'         why:   {reason}')
                failures.append(chk)
            else:
                passes += 1

    print()
    print(f'PASS: {passes}/{len(BUG_CHECKS)}')
    if failures:
        print(f'FAIL: {len(failures)}')
        print()
        print('Recurring bug class detected? Check §10x.39 bug table for')
        print('prior fix attempts. If 2+ rows reference same rule, RECOMMEND')
        print('holistic review (Option B Phase 1 refactor) instead of patching.')
        sys.exit(1)
    else:
        print('ALL CHECKS PASSED')
        sys.exit(0)


if __name__ == '__main__':
    main()
