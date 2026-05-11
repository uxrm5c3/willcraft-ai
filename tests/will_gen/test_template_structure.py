"""§10x.206 — Snapshot regression for the canonical Alan & Tan firm template.

Asserts every phrasing pattern from CLAUDE.md §10x.24 against the drafter's
output for the KOID fixture (2a2b527e-d870-447b-b386-8d97b21bb849).

If the drafter ever drifts from §10x.24, this test fails — printing
the failing pattern + a snippet of the generated will so the fix is
obvious. The test is the gate that prevents the recurring "AI chat
missed this" / "compare line by line and missed out details" regression.

Runnable two ways (both must work — bug_checklist.py and run_audit.py
both invoke us):

  # Direct (no pytest):
  docker exec willcraft-web python /app/tests/will_gen/test_template_structure.py

  # Via pytest:
  pytest tests/will_gen/test_template_structure.py

Override the fixture client via CLI arg or WILL_GEN_FIXTURE_CLIENT env var.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Callable, List, Tuple

# Make repo importable when invoked standalone
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = _HERE
for _ in range(4):
    _REPO = os.path.dirname(_REPO)
    if os.path.isfile(os.path.join(_REPO, 'app.py')) and os.path.isdir(
        os.path.join(_REPO, 'services')
    ):
        if _REPO not in sys.path:
            sys.path.insert(0, _REPO)
        break
if os.path.isdir('/app') and '/app' not in sys.path:
    sys.path.insert(0, '/app')


KOID_CLIENT_ID = '2a2b527e-d870-447b-b386-8d97b21bb849'


def _resolve_fixture_client() -> str:
    """CLI arg > env var > default KOID."""
    if len(sys.argv) > 1 and not sys.argv[1].startswith('-'):
        return sys.argv[1]
    return os.environ.get('WILL_GEN_FIXTURE_CLIENT', KOID_CLIENT_ID)


def generate_will_for_client(client_id: str) -> str:
    """Call the same drafter path the wizard's Generate button uses.
    Returns the will text, or '' on failure (with reason printed)."""
    try:
        from app import app, db, Will  # noqa: F401
        from app import _refresh_wizard_session_from_db, build_will_data
        # Use the PRODUCTION drafter path (draft_will, NOT draft_will_mock).
        # `draft_will()` calls `documents/template_filler.fill_will()` which
        # is the deterministic template-fill path the wizard's Generate
        # button uses. `draft_will_mock` is a legacy code path with
        # different clause shape and would not exercise the locked format.
        from ai.drafter import draft_will
    except ImportError as e:
        print(f'❌ Cannot import drafter ({e}). Run inside the willcraft-web '
              'container or with the repo root on sys.path.')
        return ''

    with app.app_context():
        # Per §10x.120 — accept ANY active status, not just 'draft'. The
        # KOID fixture is post-approval; the snapshot test must still run.
        ACTIVE_STATUSES = ('draft', 'generated', 'pending_approval', 'approved')
        w = (Will.query.filter_by(client_id=client_id)
             .filter(Will.status.in_(ACTIVE_STATUSES))
             .filter(Will.deleted_at.is_(None))
             .order_by(Will.updated_at.desc()).first())
        if not w:
            print(f'❌ No active will found for client {client_id} '
                  f'(searched {ACTIVE_STATUSES})')
            return ''
        with app.test_request_context('/'):
            from flask import session
            session['client_id'] = client_id
            session['will_id'] = w.id
            session['user_id'] = 'template-structure-test'
            try:
                _refresh_wizard_session_from_db()
                will_data = build_will_data()
                return draft_will(will_data)
            except Exception as e:
                print(f'❌ Drafter raised: {type(e).__name__}: {e}')
                import traceback
                traceback.print_exc()
                return ''


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def _snip(text: str, needle: str, ctx: int = 60) -> str:
    """Return a small snippet around `needle` for failure context."""
    idx = text.find(needle)
    if idx < 0:
        return '<NOT FOUND>'
    start = max(0, idx - ctx)
    end = min(len(text), idx + len(needle) + ctx)
    return '...' + text[start:end].replace('\n', ' ⏎ ') + '...'


def _find_clause_n_for(text: str, body_match: str) -> int:
    """Return the clause number for a numbered line containing `body_match`.
    Returns -1 if not found."""
    for line in text.splitlines():
        m = re.match(r'(\d+)\.\s+(.*)', line)
        if m and body_match in m.group(2):
            return int(m.group(1))
    return -1


# Each check returns (ok, reason). `reason` only shown when ok is False.
PatternCheck = Tuple[str, str, Callable[[str], Tuple[bool, str]]]


def _check_no_malaysia_suffix(t: str) -> Tuple[bool, str]:
    """§10x.24: address suffix — never ', MALAYSIA' on MY addresses."""
    if ', MALAYSIA\n' in t or ', MALAYSIA.' in t:
        return False, _snip(t, ', MALAYSIA')
    return True, ''


def _check_negeri_of_form(t: str) -> Tuple[bool, str]:
    """§10x.24: state word `Negeri of Johor` (NOT `State of JOHOR`)."""
    if 'Negeri of Johor' not in t:
        return False, 'missing `Negeri of Johor`'
    if re.search(r'State of [A-Z]{2,}', t):
        m = re.search(r'State of [A-Z]{2,}', t)
        return False, f'found legacy form: {m.group(0) if m else "?"}'
    return True, ''


def _check_title_no_period_prefix(t: str) -> Tuple[bool, str]:
    """§10x.24: bare title `Geran 528881` — NOT `Geran No. 528881`."""
    if re.search(r'\bGeran No\.\s*\d', t):
        m = re.search(r'\bGeran No\.\s*\d+', t)
        return False, f'legacy "Geran No." prefix: {m.group(0) if m else "?"}'
    if not re.search(r'\bGeran \d', t):
        return False, 'no bare "Geran <digits>" found'
    return True, ''


def _check_hsd_format(t: str) -> Tuple[bool, str]:
    """§10x.24: `H.S.(D) 251041` bare format for HSD titles."""
    # KOID Shop should have H.S.(D)
    if 'H.S.(D)' not in t:
        return False, 'no H.S.(D) found (KOID Shop should have it)'
    if re.search(r'H\.S\.\(D\) No\.\s*\d', t):
        return False, 'legacy "H.S.(D) No." prefix'
    return True, ''


def _check_lot_no_period_prefix(t: str) -> Tuple[bool, str]:
    """§10x.24: bare `Lot <N>` — NOT `Lot No. <N>`."""
    if re.search(r'\bLot No\.\s*\d', t):
        m = re.search(r'\bLot No\.\s*\d+', t)
        return False, f'legacy "Lot No." prefix: {m.group(0) if m else "?"}'
    if not re.search(r'\bLot \d', t):
        return False, 'no bare "Lot <digits>" found'
    return True, ''


def _check_leading_zeros_stripped(t: str) -> Tuple[bool, str]:
    """§10x.24: leading zeros stripped from titles and lots."""
    # Find any 6-digit title/lot number starting with 0 (e.g. 00528881)
    if re.search(r'\b(?:Geran|HSD|H\.S\.\(D\)|Lot|PTD)\s+0\d{4,}', t):
        m = re.search(r'\b(?:Geran|HSD|H\.S\.\(D\)|Lot|PTD)\s+0\d{4,}', t)
        return False, f'leading-zero not stripped: {m.group(0) if m else "?"}'
    return True, ''


def _check_equal_share_singular(t: str) -> Tuple[bool, str]:
    """§10x.24: SINGULAR `in equal share` — NOT plural `in equal shares`."""
    if 'in equal shares' in t:
        return False, _snip(t, 'in equal shares')
    if 'in equal share' not in t:
        return False, 'no `in equal share` found at all'
    return True, ''


def _check_beneficiary_uppercase(t: str) -> Tuple[bool, str]:
    """§10x.24: beneficiary names UPPERCASE in clause body."""
    # KOID wife is LIM BEE YAN — must appear UPPER, not Title Case in clauses
    if 'LIM BEE YAN' not in t:
        return False, 'wife not UPPERCASE (LIM BEE YAN missing)'
    # Title-case form should not appear in clause bodies
    if re.search(r'unto my (?:wife|son|daughter|sister|brother|mother|father)\s+'
                 r'[A-Z][a-z]', t):
        m = re.search(r'unto my [A-Za-z-]+\s+[A-Z][a-z][A-Za-z ]+', t)
        return False, f'beneficiary in Title Case: {m.group(0) if m else "?"}'
    return True, ''


def _check_revocation_no_hereby(t: str) -> Tuple[bool, str]:
    """§10x.24: `I revoke all earlier Wills` — NO `hereby`."""
    if 'I revoke all earlier Wills' not in t:
        return False, 'revocation phrase missing'
    if 'I hereby revoke all earlier Wills' in t:
        return False, 'legacy "I hereby revoke" form present'
    return True, ''


def _check_executor_substitute_wording(t: str) -> Tuple[bool, str]:
    """§10x.24: `In the event that my <relationship> is unwilling or unable
    to act for whatsoever reason, then I appoint …`."""
    if not re.search(r'In the event that my [\w-]+ is unwilling or unable '
                     r'to act for whatsoever reason, then I appoint', t):
        return False, 'substitute-executor wording missing or drifted'
    return True, ''


def _check_sole_property_with_building_type(t: str) -> Tuple[bool, str]:
    """§10x.24: sole property w/ building-type → `all my shares in the
    Single Storey Medium Low Cost Shop known as` (KOID Shop)."""
    needle = 'all my shares in the Single Storey Medium Low Cost Shop known as'
    if needle not in t:
        # Could be that the property exists but building-type wasn't set.
        # Check whether the Shop is sole and present.
        if 'JALAN GUNUNG' in t and 'all my shares in the' not in t:
            return False, 'Shop present but missing `all my shares in the` prefix'
        # If Shop not in fixture, skip silently
        if 'JALAN GUNUNG' not in t:
            return True, '(fixture has no shop — skipped)'
        return False, 'missing building-type prefix on Shop'
    return True, ''


def _check_sole_property_without_building_type(t: str) -> Tuple[bool, str]:
    """§10x.24: sole property w/o building-type → `all my shares in the
    property known as`. KOID C-30-08 + C-05-01 are sole, no building-type."""
    # At minimum one sole property without building-type prefix should use
    # the bare `all my shares in the property known as` form.
    if 'all my shares in the property known as' not in t:
        return False, 'no `all my shares in the property known as` form found'
    # Legacy form `the property known as` (alone, without `all my shares in`)
    # is forbidden for sole props per §10x.24.
    # Note: joint props use `all my X/Y undivided shares in the property known as`
    # — that's fine. The bug is `the property known as` with no `shares` lead.
    if re.search(r'I hereby devise and bequeath the property known as', t):
        return False, '`I hereby devise and bequeath the property known as` (no shares lead)'
    return True, ''


def _check_joint_property_undivided_shares(t: str) -> Tuple[bool, str]:
    """§10x.24: joint property → `all my <fraction> undivided shares in the
    property known as`. KOID has 3 joint props (B-05-11, C-30-08 wait no
    C-30-08 is wife-sole, B-05-11 + Sri Laguna 1/2 joint)."""
    if not re.search(r'all my ½ undivided shares in the property known as', t):
        return False, 'no `all my ½ undivided shares in the property known as` found'
    # No `1/1 undivided` form (would be probate-awkward)
    if 'all my 1/1 undivided shares' in t:
        return False, 'legacy `all my 1/1 undivided shares` (sole, awkward)'
    return True, ''


def _check_bank_singapore_prefix(t: str) -> Tuple[bool, str]:
    """§10x.24 + §10x.152b: SG banks have `Singapore <Bank> Account No.`
    KOID has POSB + Maybank both Singapore."""
    if 'the monies in my Singapore POSB bank Account No.' not in t:
        return False, 'POSB SG-prefix missing'
    if 'the monies in my Singapore Maybank Account No.' not in t:
        return False, 'Maybank SG-prefix missing'
    return True, ''


def _check_bank_my_no_country_prefix(t: str) -> Tuple[bool, str]:
    """§10x.24 + §10x.152b: MY banks have NO country prefix. KOID has
    Public Bank Berhad — should be `the monies in my Public Bank Berhad
    Current Account No.` not `Malaysia Public Bank`."""
    if 'the monies in my Public Bank Berhad' not in t:
        return False, 'Public Bank Berhad missing'
    if 'the monies in my Malaysia Public Bank' in t:
        return False, 'MY bank wrongly got country prefix'
    return True, ''


def _check_bank_type_no_plus(t: str) -> Tuple[bool, str]:
    """§10x.24: bank type marketing prefix `Plus` stripped.
    `Public Bank Berhad Saving Account` not `Plus Saving Account`."""
    if 'Plus Saving Account' in t:
        return False, '`Plus Saving Account` marketing prefix not stripped'
    return True, ''


def _check_insurance_combined_clause(t: str) -> Tuple[bool, str]:
    """§10x.24: ONE combined insurance clause with Insurance Act 1996 s.130
    fallback wording + roman-numeral policy list."""
    if 'If any nomination under my insurance policies below fails' not in t:
        return False, 'insurance fallback clause wording missing'
    if not re.search(r'\(i\) Policy No\.\s+\d', t):
        return False, 'roman-numeral policy list missing'
    return True, ''


def _check_insurance_after_properties(t: str) -> Tuple[bool, str]:
    """§10x.24: insurance combined clause AFTER all property clauses."""
    ins_idx = t.find('If any nomination under my insurance policies')
    last_prop_idx = t.rfind('the property known as')
    if ins_idx == -1:
        return True, '(no insurance in fixture)'
    if last_prop_idx == -1:
        return True, '(no property in fixture)'
    if ins_idx < last_prop_idx:
        return False, 'insurance clause appears BEFORE last property clause'
    return True, ''


def _check_bank_substitute_position(t: str) -> Tuple[bool, str]:
    """§10x.24: bank substitute IMMEDIATELY after bank clauses, NOT after
    properties. The phrase `my monies bequeathed to her in Clause` should
    appear BEFORE the first property `the property known as`."""
    bank_sub_idx = t.find('my monies bequeathed to her in Clause')
    first_prop_idx = t.find('the property known as')
    if bank_sub_idx == -1:
        return True, '(no bank substitute in fixture)'
    if first_prop_idx == -1:
        return True, '(no property in fixture)'
    if bank_sub_idx > first_prop_idx:
        return False, 'bank substitute appears AFTER first property clause'
    return True, ''


def _check_residuary_absolutely(t: str) -> Tuple[bool, str]:
    """§10x.24: residuary main has `absolutely` between NRIC paren and
    next sentence."""
    if not re.search(
        r"To give the residue \('my residuary estate'\) to my [\w-]+ "
        r"[A-Z]+(?: [A-Z]+)* \(MALAYSIA NRIC No\. [\d-]+\) absolutely\.", t):
        return False, 'residuary main missing `absolutely` after NRIC paren'
    return True, ''


def _check_property_substitute_inline(t: str) -> Tuple[bool, str]:
    """§10x.24: property substitute INLINE within the clause, NOT as a
    separate post-loop clause. Pattern: `… in equal share. If either of my
    children does not survive me, then the benefit which that child would
    have received shall be given to and vest in the surviving child
    absolutely.` OR `… absolutely. If my <relation> does not survive me…`"""
    inline_patterns = [
        r'If either of my children does not survive me, then the benefit '
        r'which that child would have received shall be given to and vest '
        r'in the surviving child absolutely\.',
        r'If my (?:daughter|son|wife|husband|sister|brother) does not survive '
        r'me, then the benefit (?:he|she|they) would have received shall be '
        r'given to my',
    ]
    if not any(re.search(p, t) for p in inline_patterns):
        return False, 'no inline substitute pattern found'
    # Confirm there's no separate post-loop "Pursuant to Clause N above..."
    # for property substitutes (would indicate non-inline).
    if 'Pursuant to Clause' in t:
        return False, 'legacy `Pursuant to Clause N above` separate substitute'
    return True, ''


def _check_signing_page_parenthetical(t: str) -> Tuple[bool, str]:
    """§10x.24: signing page has the `(appeared thoroughly to understand
    this WILL and approve it)` parenthetical."""
    if 'signed by the Testator (appeared thoroughly to understand this WILL ' \
       'and approve it) in the presence of us both' not in t:
        return False, 'signing-page parenthetical missing or drifted'
    return True, ''


def _check_witness_blocks_numbered(t: str) -> Tuple[bool, str]:
    """§10x.24: witness blocks are numbered — `First Witness Full Name:`,
    `Second Witness Full Name:`."""
    if 'First Witness Full Name:' not in t:
        return False, 'First Witness Full Name: missing'
    if 'Second Witness Full Name:' not in t:
        return False, 'Second Witness Full Name: missing'
    # Legacy unnumbered form
    if re.search(r'^Full Name:\s', t, re.MULTILINE):
        return False, 'legacy unnumbered `Full Name:` line found'
    return True, ''


def _check_discharge_charge_boilerplate(t: str) -> Tuple[bool, str]:
    """§10x.24: every property clause is followed by the discharge-charge
    boilerplate paragraph."""
    if 'I direct that any sums required to discharge a charge or to ' \
       'withdraw a private caveat or lien attached to this property shall ' \
       'be paid out of my residuary estate.' not in t:
        return False, 'discharge-charge boilerplate missing'
    return True, ''


def _check_historical_title_inline(t: str) -> Tuple[bool, str]:
    """§10x.24: historical-title parenthetical rendered inline in lot line.
    KOID Sri Laguna has `Lot 135402 (Formerly known as HS(D) 431161 PTD 143086)`."""
    if 'JALAN SRI LAGUNA' not in t:
        return True, '(no Sri Laguna in fixture — skipped)'
    if 'Formerly known as HS(D) 431161 PTD 143086' not in t:
        return False, 'Sri Laguna historical-title parenthetical missing'
    return True, ''


def _check_clause_structure_order(t: str) -> Tuple[bool, str]:
    """§10x.206: locked clause structure. Banks → bank substitute →
    properties → insurance → residuary. Verify the LAST bank-clause
    number < bank-substitute number < first property-clause number."""
    last_bank_clause = -1
    bank_sub_clause = -1
    first_prop_clause = -1
    ins_clause = -1
    residuary_clause = -1
    for line in t.splitlines():
        m = re.match(r'(\d+)\.\s+(.*)', line)
        if not m:
            continue
        n, body = int(m.group(1)), m.group(2)
        if body.startswith('I hereby devise and bequeath the monies in my'):
            last_bank_clause = n
        elif body.startswith('In the event my') and 'bequeathed to her in Clause' in body:
            bank_sub_clause = n
        elif first_prop_clause == -1 and (
            'the property known as' in body
            or 'in the Single Storey Medium Low Cost Shop known as' in body
        ):
            first_prop_clause = n
        elif body.startswith('If any nomination under my insurance policies'):
            ins_clause = n
        elif body.startswith('Unless specifically stated to the contrary in '
                             'this Will, my Trustee(s) shall hold the rest'):
            residuary_clause = n

    if last_bank_clause > 0 and bank_sub_clause > 0:
        if bank_sub_clause <= last_bank_clause:
            return False, (f'bank substitute clause {bank_sub_clause} not '
                           f'AFTER last bank clause {last_bank_clause}')
    if bank_sub_clause > 0 and first_prop_clause > 0:
        if bank_sub_clause >= first_prop_clause:
            return False, (f'bank substitute clause {bank_sub_clause} not '
                           f'BEFORE first property clause {first_prop_clause}')
    if first_prop_clause > 0 and ins_clause > 0:
        if ins_clause <= first_prop_clause:
            return False, (f'insurance clause {ins_clause} not AFTER first '
                           f'property clause {first_prop_clause}')
    if ins_clause > 0 and residuary_clause > 0:
        if residuary_clause <= ins_clause:
            return False, (f'residuary clause {residuary_clause} not AFTER '
                           f'insurance clause {ins_clause}')
    return True, ''


def _check_koid_residuary_full(t: str) -> Tuple[bool, str]:
    """§10x.24: residuary for KOID — `… to my wife LIM BEE YAN (MALAYSIA
    NRIC No. 661126-04-5182) absolutely.` exactly."""
    needle = ("to my wife LIM BEE YAN (MALAYSIA NRIC No. 661126-04-5182) "
              "absolutely.")
    if needle not in t:
        return False, _snip(t, "'my residuary estate'") if "'my residuary estate'" in t else 'residuary main full-NRIC form missing'
    return True, ''


def _check_strata_title_format(t: str) -> Tuple[bool, str]:
    """§10x.24: strata title `Strata Title Geran 564662/M1C/30/710`."""
    if 'Strata Title Geran 564662/M1C/30/710' not in t:
        return False, 'C-30-08 Strata Title Geran <suffix> missing'
    if 'Strata Title Geran 564662/M1C/5/517' not in t:
        return False, 'C-05-01 Strata Title Geran <suffix> missing'
    return True, ''


# ---------------------------------------------------------------------------
# Pattern table — each (rule_ref, name, check_fn)
# Order: structural → phrasing → punctuation. Each check independent.
# ---------------------------------------------------------------------------
PATTERN_CHECKS: List[PatternCheck] = [
    ('§10x.24 address suffix',         'no `, MALAYSIA` suffix',                _check_no_malaysia_suffix),
    ('§10x.24 state word',             '`Negeri of <TitleCase>` (NOT `State of JOHOR`)', _check_negeri_of_form),
    ('§10x.24 title prefix',           '`Geran <N>` (NO `No.` between)',        _check_title_no_period_prefix),
    ('§10x.24 HSD format',             '`H.S.(D) <N>` bare',                    _check_hsd_format),
    ('§10x.24 lot prefix',             '`Lot <N>` (NO `No.` between)',          _check_lot_no_period_prefix),
    ('§10x.24 leading zeros',          'leading zeros stripped',                _check_leading_zeros_stripped),
    ('§10x.24 equal share',            'SINGULAR `in equal share`',             _check_equal_share_singular),
    ('§10x.24 beneficiary case',       'beneficiary names UPPERCASE',           _check_beneficiary_uppercase),
    ('§10x.24 revocation',             '`I revoke` (NO `hereby`)',              _check_revocation_no_hereby),
    ('§10x.24 executor substitute',    '`is unwilling or unable to act for whatsoever reason`', _check_executor_substitute_wording),
    ('§10x.24 sole prop w/ building',  '`all my shares in the <building> known as`', _check_sole_property_with_building_type),
    ('§10x.24 sole prop w/o building', '`all my shares in the property known as`', _check_sole_property_without_building_type),
    ('§10x.24 joint prop',             '`all my ½ undivided shares in the property known as`', _check_joint_property_undivided_shares),
    ('§10x.24 bank SG prefix',         '`Singapore POSB bank Account No.`',     _check_bank_singapore_prefix),
    ('§10x.24 bank MY no prefix',      '`Public Bank Berhad <Type> Account No.`', _check_bank_my_no_country_prefix),
    ('§10x.24 bank type strip',        '`Plus Saving Account` stripped',        _check_bank_type_no_plus),
    ('§10x.24 strata title',           'Strata Title Geran <master>/<sub>',     _check_strata_title_format),
    ('§10x.24 insurance combined',     'ONE combined Insurance Act 1996 s.130 clause + roman list', _check_insurance_combined_clause),
    ('§10x.24 insurance position',     'insurance AFTER all property clauses',  _check_insurance_after_properties),
    ('§10x.24 bank substitute pos',    'bank substitute right after banks',     _check_bank_substitute_position),
    ('§10x.24 residuary absolutely',   '`absolutely.` between NRIC paren and survivor cascade', _check_residuary_absolutely),
    ('§10x.24 property sub inline',    'property substitute INLINE',            _check_property_substitute_inline),
    ('§10x.24 historical title',       '`(Formerly known as HS(D) ... PTD ...)`', _check_historical_title_inline),
    ('§10x.24 discharge boilerplate',  'discharge-charge paragraph after property clauses', _check_discharge_charge_boilerplate),
    ('§10x.24 signing parenthetical',  '`(appeared thoroughly to understand this WILL and approve it)`', _check_signing_page_parenthetical),
    ('§10x.24 witness numbered',       '`First Witness Full Name:` / `Second Witness Full Name:`', _check_witness_blocks_numbered),
    ('§10x.24 KOID residuary',         'residuary main is wife LIM BEE YAN absolutely', _check_koid_residuary_full),
    ('§10x.206 clause order',          'banks → bank-sub → properties → insurance → residuary', _check_clause_structure_order),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_checks(will_text: str) -> Tuple[int, int, List[Tuple[str, str, str]]]:
    """Run every pattern check. Returns (passes, fails, failure_details).
    Failure tuple is (rule_ref, name, reason)."""
    failures: List[Tuple[str, str, str]] = []
    passes = 0
    for rule_ref, name, fn in PATTERN_CHECKS:
        try:
            ok, reason = fn(will_text)
        except Exception as e:
            ok, reason = False, f'check threw: {type(e).__name__}: {e}'
        if ok:
            passes += 1
        else:
            failures.append((rule_ref, name, reason))
    return passes, len(failures), failures


def test_koid_template_structure() -> None:
    """pytest entry — fails the test on any pattern violation."""
    client_id = _resolve_fixture_client()
    will_text = generate_will_for_client(client_id)
    assert will_text, f'drafter returned empty text for {client_id}'

    passes, fails, failures = run_checks(will_text)
    if failures:
        msg = '\n'.join(
            f'  ❌ [{ref}] {name}\n       {reason}'
            for ref, name, reason in failures
        )
        # Show ±200 chars around the first failure for context
        ref, name, reason = failures[0]
        ctx = ''
        if reason and reason != '<NOT FOUND>' and len(reason) < 80:
            # Maybe reason is a snippet already — skip extra ctx
            ctx = ''
        raise AssertionError(
            f'{fails}/{fails + passes} §10x.24 / §10x.206 pattern(s) '
            f'violated against client {client_id}:\n{msg}\n\n'
            f'First failure context:\n{will_text[:300]}\n...'
        )


def main() -> int:
    client_id = _resolve_fixture_client()
    print('═' * 72)
    print(f'§10x.206 template-structure snapshot — client {client_id}')
    print('═' * 72)
    will_text = generate_will_for_client(client_id)
    if not will_text:
        print('❌ drafter returned empty text — cannot run checks')
        return 2

    passes, fails, failures = run_checks(will_text)
    for rule_ref, name, fn in PATTERN_CHECKS:
        ok, reason = fn(will_text)
        mark = '✅' if ok else '❌'
        print(f'  {mark} [{rule_ref}] {name}')
        if not ok:
            print(f'         why: {reason}')

    print()
    print(f'PASS: {passes}/{passes + fails}')
    if fails:
        print(f'FAIL: {fails}')
        print()
        print('Template drift detected. Fix at source:')
        print('  - documents/template_filler.py (clause-shape)')
        print('  - models/gift.py (per-asset descriptor)')
        print('  - documents/sample_will_koid.py (canonical sample)')
        print('  - CLAUDE.md §10x.24 (canonical phrasing rules)')
        print()
        print('Snapshot of generated will (first 800 chars):')
        print(will_text[:800])
        print('...')
        return 1
    print('ALL CHECKS PASSED — drafter conforms to §10x.24 / §10x.206')
    return 0


if __name__ == '__main__':
    sys.exit(main())
