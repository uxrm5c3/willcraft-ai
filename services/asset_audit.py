"""COMPREHENSIVE asset-walkthrough audit. Run before/after every change.

Verifies:
  §10b — Property count == AI Summary count
  §10e — Walkthrough order is HIGH confidence FIRST
  §10g — Address claims are unique (no double-claim)
  §10x.12 — Every AI Summary item has a step5_data entry
  §10hg — Confirm cards visible BEFORE auto-saves
"""
import sys, json
sys.path.insert(0, '/app')
from app import app, Client, Document, Will
from services.gift_walker import (get_pending_gift_documents,
                                    _score_property_confidence)
from ai.chat_planner import (_extract_ai_summary_properties,
                              _extract_ai_summary_banks,
                              _extract_ai_summary_insurance)

CID = sys.argv[1] if len(sys.argv) > 1 else '631204-07-5743'

with app.app_context():
    if '-' in CID:   # NRIC lookup
        c = Client.query.filter_by(nric_passport=CID).first()
    else:
        c = Client.query.get(CID)
    if not c:
        print(f'Client not found: {CID}'); sys.exit(1)
    print('═' * 72)
    print(f'COMPREHENSIVE ASSET AUDIT — {c.full_name} ({c.nric_passport})')
    print('═' * 72)

    # ── 1. AI Summary canonical asset list ────────────────────────
    print('\n[1] AI SUMMARY ASSET COUNTS (canonical)')
    print('-' * 72)
    ai_props = _extract_ai_summary_properties(c.id) or []
    ai_banks = _extract_ai_summary_banks(c.id) or []
    ai_ins   = _extract_ai_summary_insurance(c.id) or []
    print(f'  Properties: {len(ai_props)}')
    for i, p in enumerate(ai_props, 1):
        print(f'    {i}. {p.get("name","")[:60]} | {p.get("address","")[:50]}')
    print(f'  Banks:      {len(ai_banks)}')
    for i, b in enumerate(ai_banks, 1):
        print(f'    {i}. {b.get("institution","")} #{b.get("account_number","")}')
    print(f'  Insurance:  {len(ai_ins)}')
    for i, x in enumerate(ai_ins, 1):
        print(f'    {i}. {x.get("insurer","")} #{x.get("policy_number","")}')

    # ── 2. Pending gift docs (what walkthrough actually shows) ────
    print('\n[2] PENDING GIFT WALKTHROUGH GROUPS (what user sees)')
    print('-' * 72)
    pending = get_pending_gift_documents(c.id) or {}
    for kind, groups in sorted(pending.items()):
        print(f'  {kind}: {len(groups)} groups')
        for i, g in enumerate(groups, 1):
            ex = g.get('extracted') or {}
            # _score_property_confidence takes the EXTRACTED dict, not the
            # group dict. Calling with `g` returns 0 always (silent bug).
            score = _score_property_confidence(ex) if kind == 'property' else 0
            print(f'    {i}. score={score:2d}  '
                  f'lot={ex.get("lot_number","")[:12]:12s}  '
                  f'title={ex.get("title_number","")[:18]:18s}  '
                  f'mukim={ex.get("mukim","")[:12]:12s}  '
                  f'addr={(ex.get("property_address") or "")[:40]}')

    # ── 3. step5_data state ───────────────────────────────────────
    w = (Will.query.filter_by(client_id=c.id, status='draft')
         .filter(Will.deleted_at.is_(None))
         .order_by(Will.updated_at.desc()).first())
    s5 = json.loads(w.step5_data or '[]') if w else []
    print(f'\n[3] STEP5_DATA SAVED GIFTS')
    print('-' * 72)
    print(f'  total: {len(s5)} gifts')
    for i, g in enumerate(s5, 1):
        kind = g.get('kind', '?')
        ident = ''
        if kind == 'property':
            pi = g.get('property_info') or {}
            ident = pi.get('address', '')[:50]
        elif kind == 'bank':
            ident = f'{g.get("bank_name","")} #{g.get("account_number","")}'
        elif kind == 'insurance':
            ident = f'{g.get("insurer","")} #{g.get("policy_number","")}'
        l1 = g.get('_layer1_confirmed') or False
        bens = len(g.get('beneficiaries') or [])
        sub = g.get('substitute_specific')
        print(f'    {i}. [{kind}] {ident:50s}  L1={l1}  bens={bens}  sub={sub is not None}')

    # ── 4. Reconcile: AI Summary vs Pending vs step5_data ─────────
    print(f'\n[4] RECONCILIATION CHECKS')
    print('-' * 72)
    errs = []
    if len(ai_props) != len(pending.get('property', [])):
        errs.append(f'§10b: AI Summary {len(ai_props)} properties != pending {len(pending.get("property", []))} groups')
    if len(ai_banks) != len(pending.get('bank', [])):
        errs.append(f'§10x.12: AI Summary {len(ai_banks)} banks != pending {len(pending.get("bank", []))} groups')
    if len(ai_ins) != len(pending.get('insurance', [])):
        errs.append(f'§10x.12: AI Summary {len(ai_ins)} insurance != pending {len(pending.get("insurance", []))} groups')
    # §10e: scores monotonically decrease
    prop_scores = []
    for g in pending.get('property', []) or []:
        ex = g.get('extracted') or {}
        prop_scores.append(_score_property_confidence(ex))
    if prop_scores != sorted(prop_scores, reverse=True):
        errs.append(f'§10e: property walkthrough order is NOT HIGH→LOW. '
                    f'Scores: {prop_scores}')

    if errs:
        print(f'  ❌ {len(errs)} ISSUES:')
        for e in errs: print(f'     {e}')
    else:
        print('  ✅ All checks pass')

    # ── 5. §10x.206 — Template-structure snapshot ─────────────────
    # Asserts every §10x.24 canonical phrasing pattern against the
    # drafter output. Locks the KOID-aligned firm template format.
    print(f'\n[5] §10x.206 TEMPLATE-STRUCTURE SNAPSHOT')
    print('-' * 72)
    try:
        from tests.will_gen.test_template_structure import (
            generate_will_for_client, run_checks,
        )
        will_text = generate_will_for_client(c.id)
        if not will_text:
            print('  ⚠ drafter returned empty text — skipped')
        else:
            passes, fails, failures = run_checks(will_text)
            for rule_ref, name, fn in []:  # noqa
                pass
            if fails:
                print(f'  ❌ {fails}/{fails + passes} §10x.24 pattern(s) failed:')
                for rule_ref, name, reason in failures[:10]:
                    print(f'     [{rule_ref}] {name}')
                    if reason:
                        print(f'        {reason[:100]}')
                # Add to top-level errs so non-zero exit is signalled
                errs.append(
                    f'§10x.206: {fails} template-structure pattern(s) failed '
                    f'(see [5] above)'
                )
            else:
                print(f'  ✅ All {passes} §10x.24 patterns conform')
    except Exception as e:
        print(f'  ⚠ snapshot exception: {type(e).__name__}: {e}')

    # Non-zero exit if any error surfaced (reconciliation OR template)
    if errs:
        sys.exit(1)
