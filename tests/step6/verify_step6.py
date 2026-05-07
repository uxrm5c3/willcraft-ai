"""verify_step6.py — §10x.47 + §10x.48 enforced.

Branches on fixture_mode:
  text_only:  no Documents → H3-only is valid IFF every text-stated
              detail (address, lot, title, mukim, co-owners) lands.
  mixed:      Documents present → matcher MUST have bound at least one
              real Document. Every binding has _match_via reason.
  empty:      no AI Summary, no Documents → nothing to test.

Rules (R1-R12):
  R1.  Property count: each AI-Summary property has a step5 gift.
  R2.  Bank count.
  R3.  Insurance count.
  R4.  Beneficiary populated on every saved gift.
  R5.  Substitute clause populated on every saved gift.
  R6.  Residuary gate refuses while above incomplete.
  R7.  Property Layer 1 confirmed (H3 OR image-bound).
  R8.  Property address present on every gift (Stage 4 invariant).
  R9.  §10x.47 fixture sanity — fail if AI Summary > 0 and 0 Docs and 0 gifts.
  R10. §10x.48 Stage 4 — lot/title preserved when message line stated them.
  R11. §10x.48 Stage 4 — mukim resolved via §10ha geo bridge when address
       contains a known township and DocGroup didn't override.
  R12. §10x.48 Stage 2 — when Documents exist, ≥1 gift has _match_via
       and a real document_id (not _h3_synth_*).
"""
import sys, json, re
sys.path.insert(0, '/app')
from app import app, db, Will, Document, _try_handle_residuary_skip
from services.gift_walker import get_pending_gift_documents
from ai.chat_planner import (_extract_ai_summary_properties,
                              _extract_ai_summary_banks,
                              _extract_ai_summary_insurance)

CID = sys.argv[1] if len(sys.argv) > 1 else None
if not CID:
    print('USAGE: verify_step6.py <client_id>'); sys.exit(2)

FAIL = []
def fail(rule, msg): FAIL.append(f'[{rule}] {msg}')

# §10ha geo bridge (mirrored — keep in sync with ai/chat_planner.py::_GEO_BRIDGE)
_GEO_BRIDGE = {
    'seri alam':'Plentong','bandar seri alam':'Plentong','taman laguna':'Plentong',
    'sri laguna':'Plentong','marina cove':'Plentong','tepian bayu':'Plentong',
    'pasir gudang':'Plentong','permas jaya':'Plentong','masai':'Plentong',
    'medini':'Pulai','bandar medini':'Pulai','iskandar puteri':'Pulai',
    'paradiso nuova':'Pulai','paradisonuava':'Pulai','merak kayangan':'Pulai',
    'nusajaya':'Pulai','mount austin':'Tebrau','taman austin':'Tebrau',
}

def _expected_mukim_from_addr(addr):
    al = (addr or '').lower()
    for k in sorted(_GEO_BRIDGE.keys(), key=len, reverse=True):
        if k in al: return _GEO_BRIDGE[k]
    return None

with app.app_context():
    w = (Will.query.filter_by(client_id=CID, status='draft')
         .filter(Will.deleted_at.is_(None))
         .order_by(Will.updated_at.desc()).first())
    if not w:
        print('NO_DRAFT'); sys.exit(2)

    try:
        s5 = json.loads(w.step5_data or '[]')
        if isinstance(s5, dict): s5 = s5.get('gifts') or []
        if not isinstance(s5, list): s5 = []
    except Exception as e:
        fail('PARSE', f'step5_data invalid: {e}'); s5 = []

    prop_gifts = [g for g in s5 if isinstance(g, dict) and (
        g.get('kind') == 'property' or g.get('asset_type') == 'property'
        or g.get('property_address')
        or (g.get('property_info') or {}).get('property_address')
        or (g.get('property_details') or {}).get('property_address'))]
    bank_gifts = [g for g in s5 if isinstance(g, dict) and (
        g.get('kind') == 'bank' or g.get('asset_type') == 'bank' or g.get('bank_name'))]
    ins_gifts = [g for g in s5 if isinstance(g, dict) and (
        g.get('kind') == 'insurance' or g.get('asset_type') == 'insurance' or g.get('insurer'))]

    ai_props = _extract_ai_summary_properties(CID) or []
    ai_banks = _extract_ai_summary_banks(CID) or []
    ai_ins = _extract_ai_summary_insurance(CID) or []

    doc_count = Document.query.filter_by(client_id=CID).count()
    ai_total = len(ai_props) + len(ai_banks) + len(ai_ins)

    if doc_count == 0 and ai_total == 0:
        fixture_mode = 'empty'
    elif doc_count == 0:
        fixture_mode = 'text_only'
    else:
        fixture_mode = 'mixed'

    print('=' * 64)
    print(f'VERIFY STEP 6 — client {CID}')
    print(f'§10x.47 fixture_mode = {fixture_mode}  (docs={doc_count}, ai_total={ai_total})')
    print('=' * 64)
    print(f'AI Summary properties: {len(ai_props)}')
    print(f'AI Summary banks:      {len(ai_banks)}')
    print(f'AI Summary insurance:  {len(ai_ins)}')
    print(f'Step5 property gifts:  {len(prop_gifts)}')
    print(f'Step5 bank gifts:      {len(bank_gifts)}')
    print(f'Step5 insurance gifts: {len(ins_gifts)}')

    if fixture_mode == 'empty':
        print('\nRESULT: SKIP — empty fixture, nothing to test'); sys.exit(0)

    # R1-R3 counts
    saved_prop_idxs = set()
    for g in prop_gifts:
        idx = g.get('_ai_summary_idx')
        if isinstance(idx, int): saved_prop_idxs.add(idx)
    for i, p in enumerate(ai_props):
        matched = i in saved_prop_idxs
        if not matched:
            tokens = set(re.findall(r'[a-z0-9]+', (p.get('address') or p.get('name') or '').lower()))
            distinctive = {t for t in tokens if re.match(r'^[a-z]?-?\d+[-\d/]*$', t) and len(t) >= 4}
            for g in prop_gifts:
                addr = ((g.get('property_info') or {}).get('property_address')
                        or (g.get('property_details') or {}).get('property_address')
                        or g.get('property_address') or '').lower()
                if distinctive & set(re.findall(r'[a-z0-9]+', addr)):
                    matched = True; break
        if not matched:
            fail('R1', f'AI Summary property [{i}] not in step5: '
                       f"{(p.get('name') or p.get('address'))!r}")

    saved_acct = {re.sub(r'\W+', '', (g.get('account_number') or '')) for g in bank_gifts}
    saved_acct.discard('')
    for b in ai_banks:
        ack = re.sub(r'\W+', '', b.get('account_number') or '')
        if ack and ack not in saved_acct:
            fail('R2', f'AI Summary bank not in step5: {b.get("bank_name")} {b.get("account_number")}')

    saved_pol = {re.sub(r'\W+', '', (g.get('policy_number') or '')) for g in ins_gifts}
    saved_pol.discard('')
    for ins in ai_ins:
        pol = re.sub(r'\W+', '', ins.get('policy_number') or '')
        if pol and pol not in saved_pol:
            fail('R3', f'AI Summary insurance not in step5: {ins.get("insurer")} {ins.get("policy_number")}')

    # R4 beneficiaries, R5 substitute
    for gi, g in enumerate(s5):
        if not isinstance(g, dict): continue
        if g.get('skipped') or g.get('_user_rejected') or g.get('_ai_summary_skipped'): continue
        if not (g.get('beneficiaries') or []):
            fail('R4', f'gift[{gi}] {g.get("kind")} has no beneficiaries')
        sub = g.get('substitute_specific'); mode = g.get('substitute_mode')
        if not sub and mode in (None, '', 'none'):
            fail('R5', f'gift[{gi}] {g.get("kind")} missing substitute clause')

    # R6 residuary gate
    blocked = _try_handle_residuary_skip(CID, 'residuary skip')
    if (isinstance(blocked, dict) and blocked.get('kind') == 'residuary_skipped' and FAIL):
        fail('R6', 'residuary gate let it through despite incomplete gifts')

    # R7 layer1 confirm, R8 address present
    for gi, g in enumerate(prop_gifts):
        addr = ((g.get('property_info') or {}).get('property_address')
                or (g.get('property_details') or {}).get('property_address')
                or g.get('property_address') or '').strip()
        if not addr: fail('R8', f'property gift[{gi}] missing address')
        if g.get('_h3_placeholder') and not g.get('_layer1_confirmed'):
            fail('R7', f'property gift[{gi}] H3 placeholder not Layer-1 confirmed')

    # R9 §10x.47 fixture sanity
    if ai_total > 0 and len(s5) == 0:
        fail('R9', f'§10x.47 — AI Summary has {ai_total} items but step5 is empty')

    # R10 §10x.48 Stage 4 — lot/title preserved when message had them
    for i, p in enumerate(ai_props):
        ai_lot = (p.get('lot') or '').strip()
        ai_title = (p.get('title') or '').strip()
        if not (ai_lot or ai_title): continue
        # Find the matching gift
        matching = None
        for g in prop_gifts:
            if g.get('_ai_summary_idx') == i:
                matching = g; break
        if not matching:
            tokens = set(re.findall(r'[a-z0-9]+', (p.get('address') or '').lower()))
            distinctive = {t for t in tokens if re.match(r'^[a-z]?-?\d+[-\d/]*$', t) and len(t) >= 4}
            for g in prop_gifts:
                addr = ((g.get('property_info') or {}).get('property_address')
                        or (g.get('property_details') or {}).get('property_address')
                        or g.get('property_address') or '').lower()
                if distinctive & set(re.findall(r'[a-z0-9]+', addr)):
                    matching = g; break
        if not matching: continue
        gift_lot = ((matching.get('property_info') or {}).get('lot_number')
                    or (matching.get('property_details') or {}).get('lot_number')
                    or matching.get('lot_number') or '').strip()
        gift_title = ((matching.get('property_info') or {}).get('title_number')
                      or (matching.get('property_details') or {}).get('title_number')
                      or matching.get('title_number') or '').strip()
        if ai_lot and re.sub(r'\D', '', ai_lot) != re.sub(r'\D', '', gift_lot):
            fail('R10', f'AI prop[{i}] lot {ai_lot!r} not on gift (gift has {gift_lot!r})')
        if ai_title and re.sub(r'\D', '', ai_title) != re.sub(r'\D', '', gift_title):
            fail('R10', f'AI prop[{i}] title {ai_title!r} not on gift (gift has {gift_title!r})')

    # R11 §10x.48 Stage 4 — mukim resolved via geo bridge
    for i, p in enumerate(ai_props):
        addr = (p.get('address') or '').strip()
        expected_mukim = _expected_mukim_from_addr(addr)
        if not expected_mukim: continue
        matching = None
        for g in prop_gifts:
            if g.get('_ai_summary_idx') == i: matching = g; break
        if not matching:
            tokens = set(re.findall(r'[a-z0-9]+', addr.lower()))
            distinctive = {t for t in tokens if re.match(r'^[a-z]?-?\d+[-\d/]*$', t) and len(t) >= 4}
            for g in prop_gifts:
                gaddr = ((g.get('property_info') or {}).get('property_address')
                         or (g.get('property_details') or {}).get('property_address')
                         or g.get('property_address') or '').lower()
                if distinctive & set(re.findall(r'[a-z0-9]+', gaddr)):
                    matching = g; break
        if not matching: continue
        gift_mukim = ((matching.get('property_info') or {}).get('mukim')
                      or (matching.get('property_details') or {}).get('mukim')
                      or matching.get('mukim') or '').strip()
        if not gift_mukim:
            fail('R11', f'AI prop[{i}] address {addr[:40]!r} should have '
                        f'mukim={expected_mukim!r} via §10ha bridge but gift has none')

    # R12 §10x.48 Stage 2 — mixed mode requires real bindings
    if fixture_mode == 'mixed':
        bound = []
        for g in s5:
            did = g.get('document_id')
            if did and not str(did).startswith('_h3_synth_'):
                d = Document.query.get(did)
                if d: bound.append(g)
        if not bound:
            fail('R12', f'§10x.48 Stage 2 — {doc_count} Documents exist but '
                        f'NO gift is bound to a real Document. Matcher never ran.')
        for g in bound:
            mv = g.get('_match_via')
            if not mv:
                fail('R12', f'gift bound to doc {g.get("document_id")} has no '
                            f'_match_via reason — silent guess (§10he Step 5)')

    print()
    if FAIL:
        print(f'FAILURES ({len(FAIL)}):')
        for f in FAIL: print(f'  ❌ {f}')
        print(f'\nRESULT: FAIL ({len(FAIL)} failures)')
        sys.exit(1)
    else:
        print(f'RESULT: PASS — Step 6 complete ({fixture_mode} mode)')
        sys.exit(0)
