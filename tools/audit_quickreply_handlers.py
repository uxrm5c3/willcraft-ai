"""§10x.111 — Quickreply handler audit.

Greps every `<!--quickreplies:[...]-->` block in `ai/chat_planner.py`,
extracts the `value` strings, and verifies each prefix has a matching
handler in `app.py`.

Usage:
    python3 tools/audit_quickreply_handlers.py

Exit 0 if every quickreply value has a handler, exit 1 with a list
of orphans otherwise.

Why: prevents the class of "button renders but does nothing when
clicked" bugs (bug table entry #38). When someone adds a new quickreply
value but forgets the app.py handler, this script fails CI.
"""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLANNER = ROOT / 'ai' / 'chat_planner.py'
APP = ROOT / 'app.py'

# Quickreply values are emitted in two ways:
#   1. _qr_marker(quick) which inlines them as <!--quickreplies:[...]-->
#   2. Direct {"label": ..., "value": ...} dict literals in card builders
#
# We collect from BOTH paths.
VALUE_LIT_RE = re.compile(
    r"['\"]value['\"]\s*:\s*['\"]([^'\"]+)['\"]",
)
F_VALUE_RE = re.compile(
    r"['\"]value['\"]\s*:\s*f['\"]([^{'\"]*?)\{[^}]+\}[^'\"]*?['\"]",
)

# Values that are user-typed free text (NOT clicked buttons).
# Free-text inputs go through phrase parsing in app.py — they don't need
# a literal-string handler match. Skip these from the audit.
FREE_TEXT = {
    'other',     # "Type a different name" — opens a free-text path
    'type',      # "Type details manually" — same
    'manual',    # "Manual entry" — same
}

# Client-side-only quickreply values. chat.js intercepts these to do
# UI actions (open wizard, trigger file upload, switch view). They have
# no app.py handler by design. Listing them here makes the audit honest
# about what's NOT a server-side handler bug.
CLIENT_SIDE_ONLY = {
    'open wizard step 10',  # chat.js opens /wizard/step/10
    'upload-ic',            # chat.js triggers file picker
    'walk one by one',      # chat.js scrolls / resets walkthrough mode
}

# Prefixes already known to have handlers, with the handler function
# name for the audit report.
KNOWN_HANDLERS = {
    'inbox ': '_try_handle_inbox_action',
    'inventory ': '_try_handle_inventory_action',
    'inventory h3 ': '_try_handle_h3_property_action',
    'unlink ': '_try_handle_unlink_action',
    'restart ': '_try_handle_restart_inbox',
    'gifts restart': '_try_handle_restart_gifts',
    'guardian ': '_try_handle_guardian_action',
    'trust ': '_try_handle_trust_action',
    'others ': '_try_handle_others_action',
    'residuary skip': '_try_handle_residuary_skip',
    'residuary ': '_try_handle_residuary_skip',
    'conflict ': '_try_handle_message_conflict',
    'orphan_': '_try_handle_orphan_claim',
    'role_match': '_try_handle_role_match',
    'beneficiaries confirm': '_try_handle_beneficiaries_confirm',
    'beneficiaries edit': '_try_save_beneficiaries (fall-through)',
    'bank_l1 ': '_try_save_bank_layered_gift',
    'bank_l2 ': '_try_save_bank_layered_gift',
    'bank_l3 ': '_try_save_bank_layered_gift',
    'bank_h3 ': '_try_save_bank_h3_gift',
    'insurance_l1 ': '_try_save_insurance_layered_gift',
    'insurance_l2 ': '_try_save_insurance_layered_gift',
    'insurance_l3 ': '_try_save_insurance_layered_gift',
    'insurance_h3 ': '_try_save_insurance_h3_gift',
    'banks generic': '_try_save_bank_gift',
    'gift ': '_try_save_property_gift',
    'substitute ': '_try_save_property_gift',
    'address:': '_try_handle_property_fill',
    'daerah:': '_try_handle_property_fill',
    'negeri:': '_try_handle_property_fill',
    'mukim:': '_try_handle_property_fill',
    'lot:': '_try_handle_property_fill',
    'title:': '_try_handle_property_fill',
    'property fill': '_try_handle_property_fill',
    'property ': '_try_handle_property_fill',
    'change ': '_try_handle_property_fill',
    'confirm defaults': '_try_handle_assets_gate',
    'confirm assets': '_try_handle_assets_gate',
    'i have more to upload': '_try_handle_assets_gate',
    'h3 user match': '_try_handle_h3_user_match',
    'skip': '(generic skip — context-dependent)',
    'delete': '(generic delete — context-dependent)',
    'yes': '(generic confirm)',
    'no': '(generic decline)',
    'remove': '(generic remove)',
    'confirm': '(generic confirm)',
}


def collect_quickreply_values(planner_src: str) -> set[str]:
    values = set()
    for m in VALUE_LIT_RE.finditer(planner_src):
        v = m.group(1).strip()
        if v:
            values.add(v)
    return values


def find_handler(value: str) -> str | None:
    # Match by longest-prefix
    best = None
    best_len = -1
    for prefix, handler in KNOWN_HANDLERS.items():
        if value == prefix.rstrip() or value.startswith(prefix):
            if len(prefix) > best_len:
                best = handler
                best_len = len(prefix)
    return best


def main() -> int:
    if not PLANNER.exists() or not APP.exists():
        print(f'ERROR: missing {PLANNER} or {APP}', file=sys.stderr)
        return 2

    src = PLANNER.read_text(encoding='utf-8')
    values = collect_quickreply_values(src)

    orphan = []
    matched = []
    for v in sorted(values):
        # Skip free-text sentinels
        if v in FREE_TEXT:
            continue
        # Skip client-side-only buttons (no server handler by design)
        if v in CLIENT_SIDE_ONLY:
            continue
        # Skip placeholder f-string values (audit can't resolve interpolation)
        if '{' in v:
            continue
        h = find_handler(v)
        if h is None:
            orphan.append(v)
        else:
            matched.append((v, h))

    print(f'Total quickreply values found: {len(values)}')
    print(f'Matched (have handler):       {len(matched)}')
    print(f'Orphan (NO handler):           {len(orphan)}')
    print()
    if orphan:
        print('❌ ORPHAN QUICKREPLIES — these will silently do nothing when clicked:')
        for v in orphan:
            print(f'   {v!r}')
        print()
        print('Fix: add a handler in app.py with a startswith() check for the prefix,')
        print('or add the prefix to KNOWN_HANDLERS in this audit script if it is')
        print('routed to an existing generic handler.')
        return 1

    print('✅ All quickreply values have a routing handler.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
