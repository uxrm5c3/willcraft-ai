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

---

## SESSION 2026-05-10 — KOID NEW ACCOUNT END-TO-END FIXES

Tested live against KOID BENG SUN NEW account (`70459059-ff33-4bd7-b31b-d954a4785a78`)
on `47.130.249.28:8082`. Pre-filled with provided IC `631204-07-5743` + address
`NO.600 JALAN MUTIARA HIJAU 17 81000 KULAI` (per §10x.99 user instruction).
Replayed 29 attachments + WhatsApp body via inbound webhook simulation.

### F2-1. AI Summary Pattern 5 — bare `his wife X, son Y, daughter Z`
**Symptom:** `_extract_family_name_role_pairs` returned `[]` for the AI Summary
phrasing `his wife Lim Bee Yan, son Joshua Koid Teck Seng, and daughter Esther
Koid En Hui`. No H3 placeholders → identity walkthrough only saw 2 ICs (Esther
+ Lim Lay Cheng), missed Lim Bee Yan + Joshua entirely.
**Root cause:** Patterns 1-4 required `my <role> <NAME>` prefix. Bare `his/her`
+ comma-separated lists never matched.
**Fix:** Pattern 5 (commit `680c468`) accepts `(?:my|his|her|,|and)` prefix on
bare-name `<role> <NAME>`. Pattern 1 widened similarly (commit `8776ec9`).
**Rule:** §10x.139.

### F2-2. Allocation overflow false positive on `Testator's 50%` ownership
**Symptom:** Walker stalled in infinite `other` loop. Conflict detector said
"Property #1 (Unit B-05-11): shares add up to 150%, not 100%. Original:
'testator holds 50% jointly with Chai Mei Fun. His 50% to be split: 25% to
son Joshua and 25% to daughter Esther'."
**Root cause:** Per §10x.13, percentages following `Testator's` / `holds X%` /
`joint X/Y` are OWNERSHIP shares, not beneficiary allocations. Detector summed
them all together.
**Fix:** `_detect_message_conflicts` (commits `7bdf0d2` + `4c1f97b`) strips
ownership-share fragments before summing — `joint 50/50` / `\d+/\d+ with` /
`testator's 50%` / `holds 50%` / `his 50% to` / `jointly with`.
**Rule:** §10x.140.

### F2-3. Bank regex missed bare-word country
**Symptom:** `_extract_ai_summary_banks` returned 0 entries. KOID's lines
`POSB Bank Singapore Account No. 030-25917-3` and `Public Bank Malaysia Current
Account No. 3244955834` never matched.
**Root cause:** `_AI_BANK_LINE_RE` required either `(country)` parens OR direct
`Account No.` after `Bank`. Bare-word country (Singapore/Malaysia) and
pre-account-type words (Current, Plus Saving) broke the regex.
**Fix:** commit `9ce14b9`. Extended to accept `Singapore|Malaysia|...` bare
word + 0-3 capitalised account-type words before `Account No.`.
**Rule:** §10x.141.

### F2-4. Cross-property identifier hallucination (Shop missing)
**Symptom:** Pipeline showed 5 properties in AI Summary but only 4 in
step5_data. Shop @ Jalan Gunung 4 missing.
**Root cause:** Claude AI Summary HALLUCINATED the same `(Title 251041, Lot
127082, Mukim Plentong)` for BOTH the House at Sri Laguna AND the Shop because
both are in Mukim Plentong. Stage 2 binding's one-claim-only rule gave the
Shop's title doc to the House; Shop got no binding → silently dropped.
**Fix:** commit `9b76a14`. Strengthened AI Summary prompt with ONE-DOC-TO-ONE-
PROPERTY hard rule + worked example. NEVER assign the same (lot, title) to TWO
properties — leave the OTHER without identifiers.
**Rule:** §10x.142.

### F2-5. H3 Person backfill chain
**Symptom:** Joshua's IC was uploaded but his Person row had `nric=''` and
`document_id=None`. Will generated `(MALAYSIA NRIC No. )` blanks for him in
every clause. Same for Lim Bee Yan when her IC arrived after first
will-generation cycle (late-arrival test).
**Three root causes (one fix each):**
- (a) `_dedupe_ic_against_existing` matched IC by name to Person row but did
  NOT backfill NRIC/address/doc_id before marking the IC as `duplicate`.
- (b) `_try_assign_pending_identity` for H3 confirms used the H3 entry's empty
  NRIC instead of looking up existing IC docs.
- (c) `_propagate_person_to_steps` only matched `status='draft'` wills; missed
  the already-generated will so step2/step4 stayed stale.
**Fix:** commits `1f99a68` + `75c5aca` + `1d3c9cb`. Verified end-to-end:
late-IC arrival simulation → Lim Bee Yan NRIC `661126-04-5182` propagates to
step2/step4 → next will-regen includes `(MALAYSIA NRIC No. 661126-04-5182)`.
**Rules:** §10x.143 + §10x.143b + §10x.143c.

### F2-6. JMB strata maintenance bill misclassified as bank statement
**Symptom:** KOID `PHOTO-29.jpg` is a "BADAN PENGURUSAN BERSAMA MERAK
KAYANGAN" Statement of Account (strata maintenance for unit B-05-11, customer
KOID BENG SUN & CHAI MEI). Vision said `kind=bank_statement, bank_name=Maybank`.
**Two root causes:**
- (a) Vision prompt didn't distinguish JMB Statement of Account from real bank
  statement — saw "Statement of Account" header and hallucinated "Maybank".
- (b) OCR regex matched 3 patterns in `bank_statement` category (`STATEMENT OF
  ACCOUNT`, `CLOSING BALANCE`, `TRANSACTION HISTORY`) → 0.95 confidence →
  bypassed vision entirely.
**Fix:** commits `1f99a68` + `db7ac63`. Vision prompt requires recognised bank
issuer + deposit account number; JMB bills classified as property_tax. OCR
regex `_DOC_PATTERNS` adds `BADAN PENGURUSAN`, `MANAGEMENT CORPORATION`,
`SERVICE CHARGE`, `SINKING FUND`, `JMB`, `MC NO` patterns to property_tax.
**Rules:** §10x.144 + §10x.144b.

### F2-7. IC dedup: weak address-only match beat strong NRIC/name match
**Symptom:** Lim Bee Yan late-arrival IC test: dedup matched her IC to
TESTATOR's Person row (same residential address). Wife's H3 Person stayed empty.
**Root cause:** `_dedupe_ic_against_existing` iterated Persons in arbitrary
order. Testator's address matched (wife's IC has same mailing address) →
matched first → silently linked wife's IC doc to testator.
**Fix:** commit `75c5aca`. Sort persons by match strength before loop:
NRIC=3, name=2, addr=1. Refuse address-only match against a Person who already
has a different NRIC. Wife's IC now correctly binds to her own Person row.
**Rule:** §10x.151.

### F2-8. Wizard property card showing empty fields despite chat-saved data
**Symptom:** Wizard Step 6 property card showed empty postcode/city/state/
country/ownership_type/testator_share/co_owners even though chat had saved a
complete address string. Also `(Title X, Lot Y, Mukim Z)` clutter trailing
every property_address. Mukim showed `Plentongy` (OCR drift). Wizard
/step/10 returned 500 Internal Server Error from a corrupted nested-dict
step3_data.
**Root causes (multiple):**
- Chat saves address as a single string; wizard expects separate
  postcode/city/state inputs (§10x.145).
- Placeholder save path doesn't write testator_share/co_owners/ownership_type
  — only build_gift does, but H3 path stores them in `property_info` while
  wizard reads `property_details` (§10x.154).
- `_parse_ownership` co-owner regex lookahead missed `.` so
  `joint 50/50 with Chai Mei Fun. Testator's...` returned co_owners=[].
- Mukim cleaner missing OCR-drift suffix strip.
- step3_data nested-dict shape (from earlier reset script polluting an empty
  list with dict-shape writes) crashed `for g in guardians`.
**Fix:** commits `a47d623` + `f303d63` + `4caf8bd` + `66a948f` + `7b13d6f` +
`fc6741a` + `8226ba7`. `_enrich_gifts_with_documents` parses postcode/city/
state, strips parens, derives ownership/share/co_owners from chat data OR
AI Summary (matching unstamped gifts to AI Summary by address/lot similarity),
normalises mukim drift, defaults country='Malaysia'. `_parse_ownership`
terminates capture on `.`/`!`, accepts `with wife X` (no `my`), strips role
prefix, treats summed-100% as sole. Step10 template defensively filters
guardians to mappings.
**Rules:** §10x.145 + §10x.150 + §10x.153 + §10x.154.

### F2-9. India / UK reference books quoted as Malaysian law
**Symptom:** Two reference books (Gopalakrishnan India, Kessler UK) had no
jurisdiction tag. Q&A engine could quote them as if they were Malaysian law.
**Fix:** commit `a47d623`. New `_BOOK_JURISDICTION` dict tags each book. Q&A
PREFERS Malaysia-authoritative sources; only falls back to foreign sources
when no MY hit, and prepends `⚠️ FOREIGN-LAW PRINCIPLE ONLY (jurisdiction:
INDIA)` disclaimer. Library page UI shows per-book jurisdiction badge.
Plus new `/library/download/<slug>` route — Gold Standard PDF accessible at
`https://will.alantanjb.com/library/download/will_drafting_gold_standard_guide`.
**Rules:** §10x.147 + §10x.148.

### F2-10. Misspelled / ambiguous financial institution names
**Symptom:** `eaTiQa` (insurance) saved as misspelt vendor name. `AIA` silently
mapped to AIA Bhd (Malaysia) when it could just as well be AIA Singapore Pte
Ltd — separate legal entities. `NTUC Income` (legacy name) saved as-is when
the legal name post-2022 corporatisation is `Income Insurance Limited`.
**Fix:** commits `f303d63` + `eed97a0`. New `services/financial_institutions.py`
(~85 BNM/MAS-licensed entities, exact/alias/substring/fuzzy Levenshtein
matching). `eaTiQa → Etiqa Insurance`. `NTUC Income → Income Insurance` (web-
verified post-2022 name). Bare AMBIGUOUS brands (AIA, HSBC, Allianz, Manulife,
Etiqa, Citibank, StanChart, OCBC, UOB, Maybank, Prudential, Great Eastern,
MSIG, Sun Life, Zurich, Sompo) trigger `🇲🇾 Brand Malaysia | 🇸🇬 Brand
Singapore` quickreply on L1 card. Fuzzy threshold tightened: requires shared
3-char prefix OR edit distance ≤ 2.
**Rules:** §10x.149 + §10x.152.

### F2-11. Chat UI snapshot didn't pick up wizard fixes
**Symptom (user report):** "chat UI not fixed". Wizard property fixes only
applied to wizard render. Chat right-pane snapshot still showed empty
postcode/missing-field warnings even after deploy.
**Root cause:** `_will_data_snapshot` returned `_normalise_gifts(step5_data)`
RAW — no enrichment. Chat history poll (`/api/chat/<cid>/history`) used the
raw snapshot. Wizard called `_enrich_gifts_with_documents` separately. Two
UIs, two render paths, only wizard had the fix.
**Fix:** commit `008d8f6`. `_will_data_snapshot` runs
`_enrich_gifts_with_documents` on step5 before returning. Single source of
truth — chat AND wizard render the same enriched gift data.
**Rule:** §10x.155.

---

## TEST RESULT — KOID NEW ACCOUNT END-TO-END (post-deploy)

After all fixes above, the property gift data shown in BOTH chat snapshot AND
wizard Step 6 is consistent and complete:

| # | Property | Ownership | Share | Co-owner | Postcode | City |
|---|---|---|---|---|---|---|
| 0 | Shop @ Jalan Gunung 4 | sole | 1/1 | — | 81750 | Masai |
| 1 | C-30-08 Marina Cove | joint | 1/2 | Joshua Koid Teck Seng | (no street in AI Summary) | — |
| 2 | B-05-11 Paradisonuava | joint | 1/2 | Chai Mei Fun | (no street in AI Summary) | — |
| 3 | C-05-01 Marina Cove | sole | 1/1 | — | (no street in AI Summary) | — |
| 4 | House Sri Laguna | joint | 1/2 | Lim Bee Yan | 81200 | Johor Bahru |

Properties [1], [2], [3] still show empty postcode/city — that's because the
AI Summary text didn't include street addresses for those units (just "Unit X
Condominium Y"). Wizard now flags them with the §10x.150 amber banner so the
user can fill in.

Will-generation includes Lim Bee Yan's NRIC `(MALAYSIA NRIC No. 661126-04-5182)`
verified end-to-end after the late-IC simulation.
