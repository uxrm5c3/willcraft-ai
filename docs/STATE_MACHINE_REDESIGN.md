# Holistic State-Machine Redesign — §10x.137 Plan

## Why this exists

The chat planner has shipped **20+ patches** in this session
(§10x.117 → §10x.136) for what's fundamentally **one bug class**:

> The system tries to derive "what step is the user on" from a stew of
> booleans (`completed_steps`, `step5_data` length, `_layer1_confirmed`,
> `_h3_placeholder`, `assets_confirmed`, …). When the same input
> arrives, different handlers may claim it, garbage placeholders create
> false dedups, and the planner re-renders the same card silently when
> nothing changed.

Every patch fixed a symptom. None fixed the architecture. The user has
asked, repeatedly, for a holistic fix:

> *"the card not responding again, This bug keeps happening / go through
> the code thoroughly to find the root cause / specific gift is not
> registering anything"* — §10x.134
> *"There is serious code broken and not responding to the fix. Need to
> relook at how to solve this in a different and holistic manner"* — present

This doc is that fix.

---

## Current architecture (the mess)

```
USER INPUT ─────────────────────────────────────────┐
                                                    │
                          ┌─────────────────────────▼──┐
                          │  ~30 chained _try_* handlers │
                          │  Each guesses if input is    │
                          │  theirs via pattern matching │
                          │  + ad-hoc gating.            │
                          │  Order matters. Drift.       │
                          └─────────────────────────┬───┘
                                                    │
                          ┌─────────────────────────▼───┐
                          │  Planner re-derives state    │
                          │  from booleans + lengths +   │
                          │  flag combinations.          │
                          │  No canonical "current step".│
                          └─────────────────────────┬───┘
                                                    │
                          ┌─────────────────────────▼───┐
                          │  Emit card for derived state │
                          │  No verification state       │
                          │  actually changed → silent   │
                          │  re-render on null save.    │
                          └──────────────────────────────┘
```

### Symptoms this produces (all observed this session)

| Symptom | Root cause | Patches that didn't extinct it |
|---|---|---|
| Card keeps re-rendering same question | No state-change check after handler | §10x.117 §10x.124 §10x.125 §10x.126 §10x.127 |
| Wrong handler claims input | Chain order + ad-hoc gating | §10x.130 §10x.134 |
| Step skipping (Step 2 → Step 6) | Planner derives current step from booleans → mis-derives | §10x.130 |
| Gift count wrong | Multiple representations (pending, step5, AI Summary) | §10x.129 §10x.131 §10x.132 |
| Gift not registered in wizard | Saves land on wrong step5 entry; garbage placeholders block | §10x.122 §10x.131 §10x.132 |
| Garbage placeholders accumulate | No invariant enforced | §10x.105 §10x.108 §10x.131 |

---

## Target architecture (the redesign)

```
USER INPUT ────────────────────────────────────────┐
                                                   │
                         ┌─────────────────────────▼────────────┐
                         │  state = will.current_state          │
                         │  (canonical, single source of truth) │
                         └─────────────────────────┬────────────┘
                                                   │
                         ┌─────────────────────────▼────────────┐
                         │  HANDLER_REGISTRY[state] = handler   │
                         │  ONE handler per state. Determined   │
                         │  by current_state, not by chain      │
                         │  ordering.                           │
                         └─────────────────────────┬────────────┘
                                                   │
                         ┌─────────────────────────▼────────────┐
                         │  result = handler(input, will)       │
                         │  Returns one of:                     │
                         │    • TransitionResult(next_state, ack) │
                         │    • RejectResult(error_msg)         │
                         │    • SaveResult(...)                 │
                         └─────────────────────────┬────────────┘
                                                   │
                         ┌─────────────────────────▼────────────┐
                         │  if Reject → emit recovery card      │
                         │  if Transition → save + emit card    │
                         │    for next_state                    │
                         │  Invariants asserted at every save.  │
                         └──────────────────────────────────────┘
```

### Canonical states (the finite list)

```python
# services/state_machine.py
class WillState(Enum):
    # Pre-flow
    S0_INTAKE              = 'S0_INTAKE'              # Awaiting WhatsApp forward / files
    S0_PROCESSING          = 'S0_PROCESSING'          # Vision/OCR running

    # Step 1: Identity
    S1_IDENTITY            = 'S1_IDENTITY'            # Walking pending IC docs

    # Step 2: Testator
    S2_TESTATOR_CONFIRM    = 'S2_TESTATOR_CONFIRM'    # Confirm name/NRIC/DOB
    S2_TESTATOR_ADDRESS    = 'S2_TESTATOR_ADDRESS'    # Awaiting residential address

    # Step 3: Executor
    S3_EXECUTOR_PICK       = 'S3_EXECUTOR_PICK'       # Choose primary
    S3_EXECUTOR_SUBSTITUTE = 'S3_EXECUTOR_SUBSTITUTE' # Choose substitute
    S3_EXECUTOR_CONFIRM    = 'S3_EXECUTOR_CONFIRM'    # Confirm auto-populated

    # Step 4: Guardian (only if minors)
    S4_GUARDIAN_PICK       = 'S4_GUARDIAN_PICK'
    S4_GUARDIAN_SUB        = 'S4_GUARDIAN_SUB'

    # Step 5: Beneficiaries
    S5_BENEFICIARY_PICK    = 'S5_BENEFICIARY_PICK'
    S5_BENEFICIARY_CONFIRM = 'S5_BENEFICIARY_CONFIRM'

    # Step 6: Specific Gifts (per AI Summary index)
    S6_GIFT_LAYER_1        = 'S6_GIFT_LAYER_1'        # Confirm asset (current_ai_idx)
    S6_GIFT_LAYER_2        = 'S6_GIFT_LAYER_2'        # Main beneficiary
    S6_GIFT_LAYER_3        = 'S6_GIFT_LAYER_3'        # Substitute beneficiary

    # Step 7: Residuary
    S7_RESIDUARY_MAIN      = 'S7_RESIDUARY_MAIN'
    S7_RESIDUARY_SUB       = 'S7_RESIDUARY_SUB'

    # Step 8: Trust
    S8_TRUST               = 'S8_TRUST'

    # Step 9: Other matters
    S9_OTHER               = 'S9_OTHER'

    # Step 10: Generate
    S10_GENERATE           = 'S10_GENERATE'
    S10_DONE               = 'S10_DONE'
```

State `S6_GIFT_LAYER_*` carries an additional `current_ai_idx` (which AI
Summary asset is being walked). Stored as `will.current_ai_idx INT`.

### Database changes

```sql
ALTER TABLE wills ADD COLUMN current_state TEXT DEFAULT 'S0_INTAKE';
ALTER TABLE wills ADD COLUMN current_ai_idx INT DEFAULT NULL;
-- Index for speed
CREATE INDEX idx_wills_state ON wills(current_state) WHERE deleted_at IS NULL;
```

Backfill (one-time): for existing wills, derive `current_state` from
the legacy boolean stew using the same logic the planner uses today.
Run `data/backfill_state.py` once. After that, the legacy derivation is
DELETED — `current_state` is canonical.

### Gift identity invariant (extincts the "garbage placeholder" class)

```python
# services/gift_repository.py
def save_gift(will_id, ai_idx, fields) -> GiftSaveResult:
    """🔥 §10x.137 — UPSERT keyed by ai_idx. NEVER append blindly.

    Invariants enforced:
      • ai_idx must be in [0, len(_extract_ai_summary_properties())-1]
      • At most ONE gift per ai_idx in step5_data
      • ai_idx=None gifts are FORBIDDEN — fail-loud
      • Gift must have at least one of: address, lot, title, account_number
        — pure-empty placeholders are rejected
    """
    will = Will.query.get(will_id)
    if ai_idx is None:
        raise InvariantViolation('§10x.137: ai_idx is required')
    if not (fields.get('address') or fields.get('lot') or
            fields.get('title') or fields.get('account_number')):
        raise InvariantViolation('§10x.137: gift has no identifying fields')
    s5 = json.loads(will.step5_data or '[]')
    # UPSERT
    existing = next((i for i, g in enumerate(s5)
                     if g.get('_ai_summary_idx') == ai_idx), None)
    if existing is not None:
        s5[existing] = {**s5[existing], **fields, '_ai_summary_idx': ai_idx}
    else:
        s5.append({**fields, '_ai_summary_idx': ai_idx})
    will.step5_data = json.dumps(s5)
    db.session.commit()
    return GiftSaveResult(...)
```

Garbage placeholders cannot exist after this rule — every save MUST
have an `ai_idx` and at least one identifier.

### Single dispatcher

```python
# services/dispatcher.py
HANDLER_REGISTRY = {
    WillState.S1_IDENTITY:            handle_identity,
    WillState.S2_TESTATOR_CONFIRM:    handle_testator_confirm,
    WillState.S2_TESTATOR_ADDRESS:    handle_testator_address,
    WillState.S3_EXECUTOR_PICK:       handle_executor_pick,
    WillState.S3_EXECUTOR_CONFIRM:    handle_executor_confirm,
    # ...
    WillState.S6_GIFT_LAYER_1:        handle_gift_layer_1,
    WillState.S6_GIFT_LAYER_2:        handle_gift_layer_2,
    WillState.S6_GIFT_LAYER_3:        handle_gift_layer_3,
    WillState.S7_RESIDUARY_MAIN:      handle_residuary_main,
    # ...
}

def dispatch(will, input_text, attachments):
    state = WillState(will.current_state)
    handler = HANDLER_REGISTRY[state]
    result = handler(will, input_text, attachments)
    if isinstance(result, TransitionResult):
        will.current_state = result.next_state.value
        if result.next_ai_idx is not None:
            will.current_ai_idx = result.next_ai_idx
        db.session.commit()
        return result.ack, planner.emit_card(will)
    elif isinstance(result, RejectResult):
        return None, planner.emit_recovery_card(state, result.reason, input_text)
```

No more chain. No more ordering. No more wrong-handler claims. The
state determines the handler. Period.

### Card emitter

```python
# services/state_planner.py
CARD_BUILDERS = {
    WillState.S1_IDENTITY:        build_identity_card,
    WillState.S2_TESTATOR_CONFIRM:build_testator_confirm_card,
    # ...
}

def emit_card(will) -> str:
    state = WillState(will.current_state)
    return CARD_BUILDERS[state](will)
```

One function maps state → card. No more 600-line `plan_turn` that
re-derives everything.

---

## Migration plan (4 phases, ~4 days)

### Phase 1 — State machine core (1.5 days)

1. Create `services/state_machine.py` with `WillState` enum + valid
   transitions table.
2. Add `Will.current_state` + `Will.current_ai_idx` columns + Alembic
   migration.
3. Write `data/backfill_state.py` — derives current_state from existing
   booleans for every existing will.
4. Add the runtime invariant: at every save, assert `state` is in the
   valid_transitions of the previous state. Raise `StateMachineViolation`
   otherwise.

**Deliverable:** every will has a canonical `current_state` field. No
behaviour change yet — the legacy `plan_turn` still runs.

### Phase 2 — Gift-identity invariant (0.5 day)

1. Create `services/gift_repository.py` with `save_gift(will_id, ai_idx, fields)`.
2. Migration: scan every existing will's step5_data; remove rows that
   violate the invariant (no ai_idx + no identifier). Log to
   audit table.
3. Replace every `_try_save_property_gift` / `_try_save_bank_*_gift`
   site to delegate to `gift_repository.save_gift`.

**Deliverable:** garbage placeholders cannot exist. step5_data is
keyed by ai_idx.

### Phase 3 — Dispatcher (1 day)

1. Create `services/dispatcher.py` with `HANDLER_REGISTRY` and
   `dispatch()` function.
2. Port the 5 most-used handlers to the new contract:
   `(will, input, attachments) → TransitionResult | RejectResult`
   - `handle_gift_layer_1`
   - `handle_gift_layer_2`
   - `handle_gift_layer_3`
   - `handle_executor_confirm`
   - `handle_residuary_main`
3. Wire `api_chat_message` to call `dispatch()` BEFORE the legacy
   chain. If dispatch returns a result, skip the chain. Else fall back
   to legacy.

**Deliverable:** the 5 most-failing flows go through the dispatcher.
Legacy still runs for everything else.

### Phase 4 — Invariants + tests (1 day)

1. Add `assert_singleton_will` (already exists per §10x.120) +
   `assert_no_garbage_step5` + `assert_gift_count_matches_ai_summary`.
2. Add `tests/state_machine_e2e.py` — Selenium/Playwright walks
   KOID end-to-end via Chrome MCP, asserts:
   - 5 property cards rendered (one per AI Summary prop)
   - Each click changes `will.current_state`
   - step5 has exactly 5 property gifts at end
   - Generated will text contains all 5 property clauses
3. Wire into the `tests/step6/run_audit.py` pre-deploy gate.

**Deliverable:** structural fixes are caught at deploy time, not by
user reports.

---

## What ships TODAY (Option A — already done)

- ✅ Cleaned KOID state (step5/step6 wiped, doc flags reset)
- ✅ §10x.135 — hardcoded SARAH BT ALI removed
- ✅ §10x.136 — typo-tolerant web search retry (Paradisonuava → Paradiso Nuova verified)

User can keep testing immediately.

## What ships next (Option B Phase 1, ~1.5 days)

Phase 1 of the state machine. Nothing in the chat behaviour changes
yet — but `Will.current_state` becomes canonical. Legacy code can
read it. Then Phase 2 hits the gift invariant. Then Phase 3 ports
the 5 hottest flows.

Each phase is self-contained and ship-able. After each, the system
is at least as good as before. After Phase 4, the bug class is
extinct.
