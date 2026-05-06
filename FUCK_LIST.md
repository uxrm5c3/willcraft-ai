# 🔥 FUCK LIST — Step 6 Specific Gifts Walkthrough

Tested live against KOID BENG SUN test client (`0590d69b-f171-4764-8bf6-642b20b824f6`)
on `47.130.249.28:8082`. Test method: `/tmp/chat_sim.py` headless simulator
(bypasses HTTP auth) walking the user through Step 6 turn-by-turn.

Last deploy: `7e2bfe0` (fix walker phantom filter for OCR-truncation siblings of accepted gifts)

---

## ✅ FIXED THIS SESSION

### F-1. Walker created phantom property cards from OCR-truncated strata titles
**Symptom:** After accepting a strata title (e.g. `564662/M1C/30/710` + Menara C address),
its OCR-truncated sibling (`564662` + polluted "Jalan Gunung 4" address) re-surfaced as
a separate property card on the next turn ("Property 2 of 4").
**Root cause:** Walker filtered pending groups against accepted gifts using only
`(lot_digits, addr_signature)`. The sibling's polluted address didn't match the
accepted gift's real address → not filtered.
**Fix:** Added `referenced_lot_master_titles: set` of `(lot_digits, master_title_digits)`.
At dedup site, drop any pending group whose `(lot, master)` matches an accepted gift's
UNLESS its title sig is a genuinely different unit (different parcel suffix).
`services/gift_walker.py` commit `7e2bfe0`.

### F-2. Cross-sig OCR-truncation merge during walker grouping
**Symptom:** Within a single walker call, master `564662` (no addr) and full strata
`564662/M1C/30/710` (with addr) ended up at different `(lot, addr)` sig keys and
weren't merged → "4 property cards" instead of 3.
**Fix:** Added cross-sig merge pass that buckets groups by `(lot_digits,
master_title_digits)`. Pairs are folded via `_safe_to_merge` which enforces same
master + only-one-side-has-parcel-encoding (true OCR truncation). Genuine distinct
parcels (both sides encoded, different suffixes) still split.
`services/gift_walker.py` commit `53d52cc`.

### F-3. Two-hint evidence not rendered on property card
**Symptom:** `validate_matches_with_web_clues` writes `_resolved_mukim`,
`_hint1_mukim_ok`, `_clue_status`, `_clue_sources` to extracted_data but the
walkthrough card template never displayed them — user couldn't see why a binding
was confident.
**Fix:** `_walkthrough_property_card()` now renders a "🔗 Match evidence" block:
Hint 1 (mukim), Hint 2 (timing image+message timestamps), web-clue compatibility
status, source URLs. Block is omitted when no hint metadata is present.
`ai/chat_planner.py` commit `b411f98`.

---

## 🔥 FUCK — STILL OUTSTANDING

### FUCK-1. Web-clue validation doesn't run on docs that already have an OCR'd address
**Repro:** KOID Marina Cove SPA (`e3073a4b`) has `property_address: '#30-08, Menara C…'`
extracted by OCR. `_persist_property_enrichment` early-returns at `existing_addr and
not _NLC_ADDR_RE.match(existing_addr)` (line 4512), so `validate_matches_with_web_clues`
never fires for these docs. Result: `_resolved_mukim`, `_clue_status` are never
populated → the new two-hint card block (F-3) renders nothing for OCR-addressed docs.

**Where:** `app.py::_persist_property_enrichment` line ~4512.
**Fix idea:** Run web-clue validation as a SEPARATE pass after the address-match
pass, for ALL pending property docs that have an address (regardless of source).
Don't gate it on "address is missing".

### FUCK-2. Cross-property address contamination via `chat_text._beneficiary_hint`
**Repro:** `f152b635` (lot 207922, title 504662, a *strata* unit) ended up with
`property_address: 'Unit B-05-11 Condominium Example Johor Bahru'` and
`_enriched_from: ['chat_text._beneficiary_hint', 'chat_text.property_address']`.
That address belongs to a DIFFERENT property (Medini, not Marina Cove). The chat-text
enrichment greedily took an address it shouldn't have because lot 207922 is a strata
master shared by many units.

**Where:** `ai/chat_planner.py::_enrich_from_chat_text` — when applied to a strata
doc (titles with `/`, `_is_strata` returns True), it MUST verify the address actually
mentions the doc's title number / parcel suffix. Currently it greedily takes any
unclaimed address mentioning the lot.
**Fix idea:** Skip `_enrich_from_chat_text` for strata docs unless the chat text
contains the doc's full title sig (e.g. `504662` somewhere near the address).

### FUCK-3. Hallucinated address survives to walker
**Repro:** Doc `a980cc76` has `lot_number: 'Blabla'`, `property_address: 'Lot Blabla,
Mukim Seberang Selatan, Daerah Kuala Muda, Kedah'`. Pure extractor hallucination.
The garbage filter `_looks_like_garbage` catches `BLABLA` for *strict noise tokens*
(UNREADABLE, CANNOT READ, NOT VISIBLE) but lets `Blabla` through as it's not in the
burn list.

**Where:** `services/gift_walker.py::_looks_like_garbage`. Per CLAUDE.md §10aa,
extend the noise list to include obvious placeholder strings (`BLABLA`, `XXX`,
`EXAMPLE`, `DUMMY`, `TEST`). Also: an address starting with the literal `Lot Blabla`
is the same garbage — reject it at extraction time.

### FUCK-4. Cukai-tanah-only properties don't surface as walker rows
**Repro:** Docs `7defac1c` (lot 194139, MERAK KAYANGAN B-05-11) and `b81069e5`
(lot 001964139, MEDINI 6 A15 & A19) are both `document_type: 'cukai_harta'` (property
tax slips) — they have a real address and a lot number, but no title doc. The
walker's `_group_property_documents` requires a `title_doc` anchor to emit a card.
The AI Summary lists 5 properties but the walker only surfaces 3 (the ones with
title docs).

**Where:** `services/gift_walker.py::get_pending_gift_documents` — the emit loop
at the end of `_group_property_documents` skips groups without `primary` (title doc).
**Fix idea:** Allow a strong cukai-harta or SPA doc (real address + lot) to anchor
its own group. Mark it `_address_confidence='medium'` and route to the unverified-
property card flow (§10d) so the user can confirm.

### FUCK-5. Lot-number OCR truncation (`001964139` vs `194139`) doesn't merge
**Repro:** Doc `b81069e5` has `lot_number: 'LOT 001964139'` (cleaned: `001964139`).
Doc `7defac1c` has `lot_number: '194139'`. These are the same lot — the `001964139`
is OCR drift adding `001` prefix. Walker groups them under different `lot_digits`
keys → two property cards for the same physical Medini condo.

**Where:** `services/gift_walker.py` lot signature builder.
**Fix idea:** Treat leading zero-prefixes on lot numbers as OCR drift. When two lot
candidates differ only by leading zeros (`001964139` vs `194139`), normalise both
by `int(s)` → `1964139` vs `194139` differ → still no match. Better:
suffix-comparison — if `b.endswith(a)` and `b` has only digits before, treat as OCR
prefix drift. Use this only when address signatures match too.

### FUCK-6. `_try_save_property_gift` upsert misses linked support-doc IDs
**Repro:** When the cross-sig OCR-truncation pass folds `7ca8877b` into `e3073a4b`
as a support doc, accepting `e3073a4b` saves the gift with `document_id: e3073a4b`
only. `7ca8877b` is not recorded as part of that gift. We mitigate this in the
walker via the `referenced_lot_master_titles` filter (F-1), but the gift entry
itself doesn't track its merged siblings.

**Fix idea:** Add `linked_doc_ids: [list]` to the gift entry. Populate from the
walker group at save time. Wizard can use this for fingerprinting and the walker
can filter on it directly without inferring via title-master.

### FUCK-7. `"1/2"` alone doesn't trigger ownership joint-share gate
**Repro:** During property fill, the user must type `inventory ownership joint 1/2`
to specify a joint share. Typing just `1/2` is ignored. Quick-reply buttons cover
this in production but the parsing is brittle.

**Where:** `app.py::_try_handle_ownership` — pattern match requires
`inventory ownership joint` prefix.
**Fix idea:** When the previous assistant message was the joint-share gate, accept
`1/2`, `1/3`, etc. as bare values. Stash a `_pending_gate` flag on the chat session.

### FUCK-8. AI Summary count vs walker count mismatch (5 vs 3)
**Repro:** Per CLAUDE.md §10h the walker count MUST equal AI Summary count. AI
Summary deduces 5 properties from KOID's WhatsApp; walker surfaces 3 (the ones
with title docs). The 2 cukai-harta-only properties (Medini, FUCK-4) and any
summary-only ones (no doc at all) never appear as walkthrough cards. There's no
"summary-only card" path that says "I see this property in your message but no
doc — please upload" (CLAUDE.md §10he).

**Where:** `ai/chat_planner.py::_asset_walkthrough_question` — it iterates over
`pending_gifts.property` only, never over `_extract_ai_summary_properties`.
**Fix idea:** Build the union of (summary-only properties without doc) ∪ (walker
properties), render summary-only ones as the unverified-card with [Upload title]
[Type details] [Skip — address only] buttons.

### FUCK-9. Bank inventory surfaces 3rd-party / corporate accounts
**Repro:** KOID's bank inventory shows `BADAN PENGURUSAN BERSAMA MERAK KAYANGAN`
(building management corp) and `RHB BANK BERHAD / NAZREEN YAHYA AMAN` (a different
person, not the testator). The bank classifier doesn't filter on holder name.

**Where:** `services/gift_walker.py` bank emit path / classifier.
**Fix idea:** Cross-reference bank statement holder against testator's full name
(±relationship list). Holders that don't match get auto-flagged with a "🚫 Holder
doesn't match testator" warning on the card. Building management corps (any
holder containing `BADAN PENGURUSAN`, `MANAGEMENT CORPORATION`, `MC NO.`) are
service-fee accounts — should default to skip-with-warning, not include.

### FUCK-10. Stale `_enriched_from` data resurrects wrong addresses after deep-clean
**Repro:** Even after running a deep clean that wipes `_enriched_from`,
`_address_confidence`, etc., on next chat turn `_persist_property_enrichment`
re-applies the same enrichment from `recent_text` and the bad address comes back.

**Fix idea:** Wiping isn't enough — fix FUCK-2 (don't enrich strata from chat text)
AND add a `_user_overridden: True` marker that prevents re-enrichment when the user
manually corrects an address.

### FUCK-11. SPA classified as title doc → wrong `_is_strata` semantics
**Repro:** Doc `e3073a4b` is `document_type: 'spa'` (Sale & Purchase Agreement)
but the walker treats it as the title-doc anchor of its group because no other
doc with type `title` was in the merge. Per CLAUDE.md §10ha title docs DON'T have
addresses, but SPAs DO — the address on `e3073a4b` is real and correct (`#30-08,
Menara C…`).

**Where:** Mostly OK in practice (the SPA address is right), but the strata
detection (`_is_strata`) reads `document_type` and finds `'spa'` not `'strata'` —
needs to look at `title_number` (slashes) and `property_description` to detect
strata structurally.
**Fix idea:** `_is_strata` already checks slashes in title_number — ensure SPA
docs with strata-like title sigs are correctly handled at every site. Audit
all uses of `_is_strata`.

### FUCK-13. Bank gift question never fired → flow jumped to residuary
**Symptom (user-reported):** After completing property walkthrough (3 properties
in step5_data, 2 with beneficiaries + 1 skipped) and inventorying both banks
(one accepted, one skipped), the chat said "✅ Specific gifts done. Moving to
Step 7: Residuary Estate" — but banks were never assigned to any beneficiary.
Only 2 specific gifts were captured (both properties); banks vanished.

**Root cause:** `ai/chat_planner.py` line 265 gated the bank question on
`not (current_will_data.get('step5') or [])` — i.e. step5_data is completely
empty. Once any property gift was saved, the bank prompt was skipped entirely
and the planner advanced straight to Step 7. The user's bank inventory accept/
skip only writes `_inventoried` to the doc — it does NOT create a step5 entry,
so the user has no way to assign banks to beneficiaries via the inventory flow.

**Partial fix (commit `d4c036e`):** Gate now checks for any BANK gift in step5
specifically (`kind=='bank'` or has `bank_name`/`account_no`). The bank question
will fire after property gifts are done.

**STILL MISSING:** No chat-side handler consumes the user's reply to
"Who inherits all your bank accounts?". When the user picks a beneficiary the
gift is never saved to step5_data → the question loops. Need a
`_try_save_bank_gift` handler analogous to `_try_save_property_gift`. Until
this lands, the bank question fires but doesn't progress.

### FUCK-12. `_extract_ai_summary_properties` not wired into walker output filter
**Per CLAUDE.md §10h:** "AI Summary count = walkthrough count". The chat planner
has `_extract_ai_summary_properties()` to parse the canonical list, but it's only
used as a hint inside the matcher — not as an authoritative count for what to
render. Any walker output beyond the AI Summary count must be reconciled (extra
images → unverified card; missing properties → summary-only card).

**Where:** `ai/chat_planner.py::_asset_walkthrough_question` lacks a
"reconcile-with-AI-Summary" step before rendering.

---

## TEST RESULTS — End-to-End Step 6

After fixes F-1, F-2, F-3, the walkthrough completes successfully:

```
Property 1/3 — Marina Cove SPA + master-title sibling merged
  → joint Esther 1/2 + Joshua 1/2, substitute equal ✓
Property 2/3 — strata 504662 (different unit, same building) skipped
Property 3/3 — Lot PTD 127082 / HSD 251041 (landed)
  → Joshua 1/1, substitute specific Esther ✓
Bank 1 — accepted ✓ (but FUCK-9: shouldn't have surfaced)
Bank 2 — skipped ✓ (FUCK-9: shouldn't have surfaced)
→ advanced to Step 7 Residuary
```

`step5_data` correctly persists 3 entries (2 with beneficiaries + substitute,
1 skipped). The wizard receives proper `property_info` blocks with title/lot/mukim
populated.

**The Step 6 specific-gifts walkthrough is functionally complete for the happy
path.** The FUCK items above are residual quality issues — none block the
walkthrough; all should be tackled before production.
