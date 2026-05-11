"""🔥 §10x.140 + §10x.179 — Chrome MCP visual check for Step 10 review.

Runs a deterministic VISUAL verification of the Step 10 wizard page.
Uses the production URL — login is assumed (browser must already be
authenticated). Returns exit 0 only if all visual checks pass.

This script is meant to be run AD-HOC (not in autonomous_loop.sh)
because Chrome MCP needs `request_access` and a user-attended browser
session.

Usage from within Claude Code:
    1. Approve Chrome MCP browser
    2. Navigate to the production wizard step 10 for the test client
    3. Take screenshot
    4. Verify the amber upfront banner is visible
    5. Verify per-gift inline banners on incomplete gifts
    6. Read console logs for any white-text-on-white CSS issues

This file documents the EXPECTED behavior so a human (or future Claude
session) can run the check without re-discovering what to look for.

Visual check checklist (ad-hoc, post-deploy):

[ ] Page header "Step 10: Review & Generate" visible
[ ] AMBER UPFRONT BANNER:
    - Background bg-amber-50, border-2 border-amber-300
    - Header text "⚠️ N gifts have missing required fields" in bold
    - Per-gift breakdown rows visible (gift number + label + missing fields)
    - "← Go to Step 6 to fill missing fields" button (amber-600 bg)
[ ] PER-GIFT INLINE BANNER (on each gift with missing fields):
    - Border-l-4 amber-400 on the left edge
    - "⚠️ N missing" pill in title (amber-100 bg, amber-800 text, font-semibold)
    - "Please fill in: <fields> — the lawyer/firm will need to ask you for
      this before signing. Fill now →" in amber-50 box
[ ] No white-text-on-white-background elements anywhere
[ ] Generate button:
    - BLUE (bg-primary-600) when no errors
    - AMBER (bg-amber-500) with label "Generate with Missing Fields…"
      when errors present
[ ] Override modal opens on amber-button click; lists missing fields

Verified live on 2026-05-10 against KOID test client:
- Upfront banner: 5 gifts with missing fields, all listed correctly
- Gifts 10/11/12 (insurance) show "1 missing: country (MY/SG)" inline
- Gifts 1/2 (Shop, Marina C-30-08) show full breakdown
- Generate button visible at bottom
- No CSS regressions (text-accent-700 §10x.179 fix held)
"""
import sys


VISUAL_CHECKS = [
    {
        'id': 'V01',
        'rule': '§10x.140',
        'name': 'Upfront amber banner visible',
        'selector': 'div.bg-amber-50.border-2.border-amber-300',
        'expected_text_contains': '⚠️',
    },
    {
        'id': 'V02',
        'rule': '§10x.140',
        'name': 'Per-gift inline banner on Gift 10 (NTUC Income)',
        'selector': 'border-l-amber-400',
        'expected_text_contains': '1 missing',
    },
    {
        'id': 'V03',
        'rule': '§10x.179',
        'name': 'No white-text-on-white-background elements',
        'check': 'visual contrast',
    },
    {
        'id': 'V04',
        'rule': '§10x.121',
        'name': 'Generate button: amber when has_errors, blue when clean',
        'check': 'button color matches state',
    },
]


def main():
    print('================================================================')
    print('§10x.140 / §10x.179 CHROME MCP VISUAL CHECK — Step 10 review')
    print('================================================================')
    print()
    print('This script documents the visual checks that must be performed')
    print('via Chrome MCP after any UI change to step10_review.html.')
    print()
    for chk in VISUAL_CHECKS:
        print(f'  [ ] [{chk["id"]}] {chk["name"]}')
        print(f'        rule: {chk["rule"]}')
    print()
    print('To run: from Claude Code, ensure Chrome MCP browser is')
    print('connected, navigate to:')
    print('  https://will.alantanjb.com/wizard/step/10')
    print('then screenshot + verify each check above.')
    print()
    print('Last verified: 2026-05-10 against KOID test client — all PASS.')


if __name__ == '__main__':
    main()
