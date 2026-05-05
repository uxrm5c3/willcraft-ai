# WillCraft AI — Project Instructions

**This is the single source of truth for build, deploy, and behaviour rules.**
Both the user and Claude refer to this file. If the user gives a new instruction,
EDIT THIS FILE (don't just remember it for one session).

---

## Table of Contents

1. [Build & Deploy](#1-build--deploy)
2. [Test Pipeline](#2-test-pipeline)
3. [Server & Infrastructure](#3-server--infrastructure)
4. [Inbox Address Format](#4-inbox-address-format)
5. [Architecture Overview](#5-architecture-overview)
6. [The 10-Step Wizard](#6-the-10-step-wizard)
7. [Chat Flow After Email Forward](#7-chat-flow-after-email-forward)
8. [Per-Step Behaviour Rules](#8-per-step-behaviour-rules)
9. [Cross-Reference AI Summary](#9-cross-reference-ai-summary)
10. [UI/UX Rules](#10-uiux-rules)
11. [Things NOT To Do](#11-things-not-to-do)

---

## 1. Build & Deploy

### Code lives on the dev machine, runs on the server inside Docker.

```
Dev machine (this repo) → git push → Server pulls → docker build → docker up
```

### Deploy command (RUN THIS — restart alone does NOT pick up changes)

```bash
ssh ubuntu@47.130.249.28 "cd ~/willcraft && \
  git pull && \
  docker compose build web && \
  docker compose up -d web"
```

### Why restart fails
The Docker image is **baked at build time**. `docker compose restart` reuses the existing image — your code change is not in it. You MUST rebuild.

### Cache trouble?
If old code keeps running after rebuild:
```bash
find . -name '*.pyc' -delete
find . -name '__pycache__' -type d -exec rm -rf {} +
docker compose build --no-cache web && docker compose up -d web
```

---

## 2. Test Pipeline

**Every deploy MUST end with a real end-to-end test.** Saying "deployed" without testing is a critical failure.

### Three required checks, in order:

**(a) Health check**
```bash
curl -s http://47.130.249.28:8082/api/health
# Expected: {"ok":true,"db_clients":N,"model":"...","model_cheap":"..."}
```

**(b) Send real email** (SMTP password is in server `.env` as `SMTP_PASSWORD`)
```python
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
msg = MIMEMultipart()
msg['From'] = 'kylie.tan@alantanjb.com'
msg['To']   = 'koid5743@will.alantanjb.com'
msg['Subject'] = 'Deploy test'
msg.attach(MIMEText('test', 'plain'))
with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
    s.login('kylie.tan@alantanjb.com', 'ptsg glgi ebph yjed')
    s.send_message(msg)
```

**(c) Verify webhook fired**
```bash
ssh ubuntu@47.130.249.28 "sleep 15 && grep 'inbound-email' /var/log/nginx/access.log | tail -2"
# Expected: a fresh "POST /api/inbound-email HTTP/1.1" 200 line
```

If any of (a)–(c) fails, the deploy is NOT done. Don't claim success.

---

## 3. Server & Infrastructure

| Item             | Value                                       |
|------------------|---------------------------------------------|
| Server host      | `ubuntu@47.130.249.28`                      |
| Container        | `willcraft-web`                             |
| Public port      | `8082`                                      |
| Repo path        | `~/willcraft`                               |
| Git branch       | `feat/client-chat`                          |
| Health endpoint  | `/api/health` (no auth)                     |
| Inbound webhook  | `/api/inbound-email` (Postmark, Basic auth) |
| DNS provider     | Cloudflare                                  |
| MX record        | `will.alantanjb.com → inbound.postmarkapp.com` priority 10 |

### Environment variables (in `~/willcraft/.env`)
- `POSTMARK_INBOUND_USER` / `POSTMARK_INBOUND_PASS` — Basic auth for webhook
- `SMTP_USER` / `SMTP_PASSWORD` — Gmail SMTP for outgoing test emails
- `INBOUND_ALLOWED_DOMAINS` — sender allowlist (empty = accept any)

---

## 4. Inbox Address Format

**Short. Easy to type. No subdomain.**

### Format
```
<first_name_5chars><ic_last4>@will.alantanjb.com
```

### Examples
| Client                | NRIC               | Inbox address                       |
|----------------------|--------------------|-------------------------------------|
| KOID BENG SUN         | 631204-07-5743     | `koid5743@will.alantanjb.com`       |
| KANAGARANY A/P APPU   | xxxxxx-xx-1265     | `kanag1265@will.alantanjb.com`      |
| PHEK YI TING          | 951030-01-5039     | `phek5039@will.alantanjb.com`       |

### Rules
- First word of full name, lowercase, letters only, **max 5 chars**
- Last 4 digits of NRIC (the sequence number after the last dash)
- Skip patronymics: `A/P`, `A/L`, `BIN`, `BINTI`, `BTE`, `BT`
- Skip titles: `MR`, `MRS`, `DR`, `DATO`, `DATUK`, `HAJI`, `HAJJAH`
- **NEVER use `inbox.` prefix.** MX is on bare domain.
- Backward compat: legacy `<slug>-<8hex>@…` still routes

Implemented in `services/inbound_address.py`.

---

## 4a. ⚠️ ALWAYS REFER TO THE WIZARD FIRST ⚠️

**THE WIZARD IS THE SOURCE OF TRUTH. CHECK IT BEFORE ASKING THE USER ANYTHING.**

This rule is repeated because it has been violated repeatedly. Before any
chat question — IC identity, executor, beneficiary, gift, address, anything —
the code MUST first query the wizard tables (`Person`, `Document`,
`Step1`, `Step2`, … `WillData`) and skip if the data is already there.

### Hard rules

1. **Never ask about an IC if the NRIC already exists on a `Person` row.**
   The chat must extract the canonical 12-digit `NNNNNN-NN-NNNN` pattern
   from the document's `extracted_data` (it may be embedded in a longer
   sentence, prefixed with `VALUE:`, etc.) and compare against
   `Person.nric_passport` — not raw string equality.

2. **Never ask about a person if the name already exists on a `Person` row.**
   Match on `full_name` case-insensitively. Issuing-authority strings
   (`KETUA PENGARAH PENDAFTARAN NEGARA`, `JABATAN PENDAFTARAN NEGARA`,
   `MyKad`, `KAD PENGENALAN`) are NOT person names — treat as empty.

3. **Never ask about a document already linked to a Person**
   (`Person.document_id == Document.id`).

4. **Never re-ask after Skip.** `_chat_skipped=True` in `extracted_data`
   means the user said skip — respect it.

5. **Step 2+ data**: before asking testator/executor/guardian/beneficiary
   questions, check the corresponding `step1`/`step2`/… JSON. If it's
   already populated, advance to the next step.

6. **Garbage-in-extracted-data is a wizard problem, not a chat problem.**
   When AI extraction returns rambling text in `nric_number` (e.g.
   `"This appears to be a longer reference number…"`), the dedup logic
   must still extract the 12-digit pattern and match. Don't push noise
   onto the user.

### The litmus test before posting any walkthrough question

```
Q: Is the answer already in the wizard?
   - YES → skip, advance, don't ask
   - NO  → ask, but show the evidence/snippet that prompts the question
```

If you find yourself writing code that asks the user something the wizard
already knows, STOP and add a dedup check first.

---

## 5. Architecture Overview

```
WhatsApp / Email
      ↓
Postmark Inbound Webhook
      ↓
POST /api/inbound-email   ← (sync: 200 OK in <1s)
      ↓
Save user message + attachments to disk
      ↓
Spawn background thread: _process_inbound_message_async
      ├─ Vision classify each image (IC / property_title / SPA / bank_statement / vehicle / insurance / etc.)
      ├─ OCR / extract structured data (NRIC fields, lot/title/mukim, bank details, etc.)
      ├─ Batch grouping: cluster images that belong to the same asset
      ├─ Address enrichment: match property addresses from WhatsApp text → property docs
      ├─ Post "📋 N exhibits received" intake card
      └─ Post "📨 AI Summary of your message" follow-up
      
User clicks "▶️ Start — verify identities"
      ↓
Identity walkthrough → Testator → Executor → Guardians → Beneficiaries → Gifts → ...
      ↓
Generate Will document
```

---

## 6. The 10-Step Wizard

These steps mirror `app.py` `/wizard/step/N` routes and the chat planner stages.
**The chat walkthrough MUST follow the same definitions. Do not confuse roles between steps.**

| Step | Name                  | What it captures                                   |
|------|-----------------------|----------------------------------------------------|
| 1    | **Identity**          | Family relationships ONLY (Spouse, Son, Daughter, Father, Mother, Brother, Sister, Son-in-law, Daughter-in-law, etc.) |
| 2    | **Testator**          | Confirm testator's name, NRIC, DOB, address, occupation |
| 3    | **Executor + Substitute** | Pick from identities. Cross-reference AI Summary text |
| 4    | **Guardians**         | Only required if minor children                    |
| 5    | **Beneficiaries**     | Pick from identities. Cross-reference AI Summary text |
| 6    | **Specific Gifts**    | Match property/bank/vehicle docs → beneficiaries with shares |
| 7    | **Residuary**         | What remains goes to whom                          |
| 8    | **Trust**             | Optional — testamentary trust details              |
| 9    | **Other Provisions**  | Optional — funeral wishes, special instructions    |
| 10   | **Generate / Review** | Compile and produce the will document              |

### Critical role separation
**Identity (Step 1) is for FAMILY relationships only.**
- Daughter, Son, Sister-in-law, Spouse, etc. ✓
- ~~Executor, Witness, Trustee, Guardian, Beneficiary~~ — these are LATER steps ✗

---

## 7. Chat Flow After Email Forward

User's exact wording (PRESERVE THIS ORDER):

> **Step 1**: Receive the WhatsApp the user sends
> **Step 2**: The text/words — use AI Summary to summarise and get user feedback. Make sure the AI summary is correct and understood correctly.
> **Step 3**: Decipher the images into types (IC, title, etc.)
> **Step 4**: Identity match (follow the wizard step)

### Implementation mapping
| User's step | Implementation                                                  |
|-------------|-----------------------------------------------------------------|
| 1           | `/api/inbound-email` saves user message + attachments           |
| 2           | `_summarise_message()` posts AI Summary card with confirmation CTA |
| 3           | `classify_file()` + `classify_batch()` in `_process_inbound_message_async` |
| 4           | Identity walkthrough triggered by "▶️ Start — verify identities" click |

---

## 8. Per-Step Behaviour Rules

### Skip-If-Already-In-Wizard (applies to ALL steps)

If the wizard already has the data, **don't ask again — skip past it.**

| Already in wizard                                | Behaviour              |
|--------------------------------------------------|------------------------|
| Document linked to a Person                      | Skip in Step 1         |
| IC NRIC matches existing `Person.nric_passport`  | Skip in Step 1         |
| Same name in `Person` table                      | Skip in Step 1         |
| User clicked Skip in chat (`_chat_skipped=True`) | Skip in Step 1         |
| `step1.full_name` is set                         | Skip Step 2            |
| 2 executors set                                  | Skip Step 3            |
| `step4` (beneficiaries) non-empty                | Skip Step 5            |

### Step 1 (Identity) — buttons

Show ONLY these family relationships:
```
Spouse | Son | Daughter | Father | Mother | Brother | Sister
Son-in-law | Daughter-in-law
Skip | Delete
```
**Never show** `Executor`, `Witness`, `Trustee`, `Guardian`, `Beneficiary` here.

### Skip / Yes / Delete buttons MUST work

- **Skip**: writes `_chat_skipped=True` to `Document.extracted_data`, `get_pending_ic_documents` filters it out, walkthrough advances. Verified by `_try_skip_pending_identity` in `app.py`.
- **Yes**: saves the deduced relationship, creates Person row, advances.
- **Delete**: soft-deletes the document (and any siblings with same name/NRIC), advances.
- **Buttons render only on the LATEST assistant message** so old questions don't leave stale buttons.

---

## 9. Cross-Reference AI Summary

When suggesting Executor or Beneficiary, the chat MUST show the snippet from the WhatsApp/email that names them. The user has repeated this MANY TIMES.

### Step 3 (Executor)
- Use `find_executor_candidate()` → calls `deduce_roles()` (Claude API)
- Heuristic fallback: search for "executor" near a relationship word
- Display: `📨 **Suggested:** **<name>** _from your message:_ "<snippet>"`

### Step 5 (Beneficiaries)
- For each likely beneficiary, fetch evidence via `deduce_roles()`
- Regex fallback: search for first name in the recent text, grab ~80 char window
- Display each candidate with: `📨 _from message:_ "<snippet>"`

### Address-to-Asset Matching (Step 6 prep)
WhatsApp text + property images are interrelated. Addresses MUST be deduced.
- `_extract_whatsapp_context_for_file()` — find adjacent text per filename
- `ai_match_property_addresses()` — Claude AI matches addresses → docs across the whole text
- `_persist_property_enrichment()` — runs on chat turns AND on email receipt
- Property card shows: ✅ high / 🟡 medium / 🔴 low confidence
- Low/medium asks user to confirm before accepting

---

## 10. UI/UX Rules

### Markdown formatting
- Section headings: `### 👤 Step 1: Identity (N left)` — emoji + step number + title
- Quick-reply buttons via `<!--quickreplies:[{"label":"...","value":"..."}]-->` marker

### Button styling (frontend `chat.js`)
- `skip` / `not yet` → muted gray
- `delete` → red
- everything else → primary blue

### AI Summary card — NEVER include exhibit thumbnails
The intake card already shows exhibits. The summary message uses
`attachments_json='[]'` so thumbnails aren't repeated.

### Walkthrough cards
- Show ONLY the focused IC/property/asset (one at a time)
- Show evidence/snippets from the user's own words when suggesting
- Confidence indicators on AI-deduced fields (✅ 🟡 🔴)

---

## 10a. Property Identity Card — Asset ONLY

The property card shown during the asset walkthrough IDENTIFIES THE ASSET.
That is its only job. It must NOT include:

- ❌ Beneficiary hints / "Client wants to give to X"
- ❌ Ownership share assignments
- ❌ Anything from Step 5 (Beneficiaries) or Step 6 (Specific Gifts)

Beneficiary + share assignment is a SEPARATE step that runs AFTER all
properties are identified, and it MUST cross-reference the AI Summary
that the user already confirmed.

## 10aa. AI-Extractor Noise — ALWAYS Clean Before Comparing

The vision/OCR extractor regularly dumps noise into structured fields.
Every dedup, group-key, or comparison MUST clean the value first. **NEVER
compare raw `extracted_data` strings.**

### Noise patterns observed in production (the "burn list")

| Field | Real value | Noise the extractor emits |
|-------|------------|---------------------------|
| `nric_number` | `650629-04-5308` | `"VALUE: 650629-04-5308-02-01"`, `"This appears to be a longer reference number…650629-04-5308"` |
| `title_number` | `564662` | `"VALUE: GRN56662"`, `"VALUE: GM35662"`, `"VALUE: (unreadable)"`, `"H.S.(D) 251041"` |
| `lot_number` | `207922` | `"VALUE: LOT 207922"`, `"LOT 207922"`, `"20792"` (OCR typo) |
| `full_name` | `LIM LAY CHENG` | `"KETUA PENGARAH PENDAFTARAN NEGARA"`, `"JABATAN PENDAFTARAN NEGARA"`, `"MyKad"`, empty + NRIC dumped here |
| `property_address` | (real address) | `"10 Marsiling Lane Singapore"` (hallucinated), `"(address not visible)"` |

### Hard rules

1. **Strip prefixes:** `VALUE:`, `LOT`, `TITLE`, `GERAN`, `TITLE NO.` —
   leading occurrences are extractor artefacts, not part of the ID.
2. **Drop parenthetical commentary:** `(unreadable)`, `(blurred)`,
   `(not visible)`, `(cannot read)` — never keep these as values.
3. **Reject AI-noise tokens entirely:** values containing
   `UNREADABLE`, `CANNOT READ`, `NOT VISIBLE` are GARBAGE — treat as empty.
4. **Issuing-authority text is NOT a person name:** `KETUA PENGARAH`,
   `JABATAN PENDAFTARAN`, `MYKAD`, `KAD PENGENALAN`, `WARGANEGARA`,
   `IDENTITY CARD`. If `full_name` contains any of these, treat as empty.
5. **Extract canonical NRIC** with regex `\d{6}[-\s]?\d{2}[-\s]?\d{4}`
   from anywhere in the field — the digits may be embedded in a sentence.
6. **OCR typos in lot numbers** (`20792` vs `207922`) are NOT a reason to
   create a new property card. Same address + same neighbourhood + similar
   lot digits = same property. Use lot+address signature, not raw equality.

### Where this is enforced (touch these, not memory)

| File | Function | What it does |
|------|----------|--------------|
| `services/identity_walker.py` | `_canonical_nric()` | regex-extract `NNNNNN-NN-NNNN` from any string |
| `services/identity_walker.py` | `_clean_person_name()` | reject issuing-authority names |
| `services/gift_walker.py` | `_clean_id_value()` | strip `VALUE:`, `LOT`, `TITLE`, `(…)` |
| `services/gift_walker.py` | `_looks_like_garbage()` | reject `UNREADABLE`, `CANNOT READ`, etc |

If a NEW noise pattern appears in production:
1. Add it to the cleaner function (one of the four above).
2. Add the example to the table in this section.
3. Re-run `get_pending_ic_documents()` / `get_pending_gift_documents()`
   against the affected client and verify the count drops.

**Do not patch a one-off case in chat-planner code.** Cleaners live in the
service layer so every consumer benefits.

---

## 10b. Property Count = AI Summary Count

The AI Summary deduces N distinct properties from the WhatsApp text.
The chat walkthrough MUST surface the SAME N properties — not 14, not 31,
not "one card per uploaded image." OCR will misread title numbers
(`564662` ↔ `504662`), the AI extractor will dump rambling text into
structured fields (`VALUE: (unreadable)`, `VALUE: GRN35662`). None of
that creates a new property.

### Hard rules for property grouping

1. **Same `lot_number` (cleaned) + same normalised `property_address`
   = same property.** Always merge, regardless of `title_number`
   variation. Different OCR readings of the same title (`564662`,
   `504662`, `VALUE:GRN56662`, `VALUE:(unreadable)`) all collapse.

2. **Strip AI-noise from identifiers before comparing.** `VALUE:`,
   `LOT `, `TITLE `, `(unreadable)`, `(blurred)` etc. are extractor
   prefixes/commentary, not part of the identifier. See
   `_clean_id_value()` in `services/gift_walker.py`.

3. **The count shown to the user is the count after dedup.** If you're
   about to render `Property X of N`, N must equal the number of
   physical properties — not the number of upload events or OCR groups.

4. **If the AI Summary lists 5 properties and the walkthrough shows
   14, the grouping is broken.** Fix the grouping; do not ship.

---

## 10c. Document Row Dedup at Upload Time

Same physical file uploaded twice = ONE row, not two. The user forwards
WhatsApp emails repeatedly during testing; without dedup the Document
table explodes (observed: 7 distinct files → 214 rows).

### Hard rule
At every upload site (especially `/api/inbound-email`):

```python
existing = Document.query.filter_by(
    client_id=client.id,
    original_filename=name,
    file_size=len(data),
).order_by(Document.created_at.asc()).first()
if existing:
    attachment_ids.append(existing.id)
    continue   # do NOT insert a duplicate row
```

Enforced in `app.py` inbound-email handler (search for "DEDUP: same physical file").

If a NEW upload site is added (`/api/upload`, drag-drop, etc.), it MUST
include this check — otherwise the dedup invariant breaks.

### One-time cleanup script (re-runnable)

```python
from app import app
from database import db, Document, Person
with app.app_context():
    docs = Document.query.filter_by(client_id=CID).order_by(Document.created_at.asc()).all()
    seen = {}
    for d in docs:
        key = (d.original_filename or '', d.file_size or 0)
        if key in seen:
            # Reassign Person.document_id to canonical, then delete dup
            for p in Person.query.filter_by(document_id=d.id).all():
                p.document_id = seen[key]
            db.session.delete(d)
        else:
            seen[key] = d.id
    db.session.commit()
```

---

## 10d. 🔥 BURN-IN — Isolated Property: ASK, Don't Assume 🔥

When a single image carries NLC identifiers (HSD/PTD/title/lot) but
cannot be cross-referenced to anything else, the chat MUST ASK the
client where it came from instead of silently rendering a confirmed
property card.

### Definition of "isolated"

A property group is isolated when ALL of these are true:
1. Only **one image** (no support_docs in the group)
2. Has at least one NLC identifier extracted (`title_number` or `lot_number`)
3. The digit-stripped identifiers do **not** appear in any recent
   chat message or the AI Summary
4. No other property group shares the same lot/title digits

### Required behaviour

Render the **unverified card** (not the normal property card):

> ### ❓ Unverified property — need your help
> I found an image (`PHOTO-…jpg`) that looks like a property document,
> but I **cannot match it** to anything you mentioned in your
> WhatsApp/email or to any other image you sent.
>
> **What I extracted from it:**
>   • Title No.: …
>   • Lot No.: …
>   • Address: …
>
> ⚠️ Because it's an isolated image with no cross-reference, I won't
> auto-create a gift card for it. Tell me what this is so I can handle
> it correctly:
>
> [✅ Yes — it is a real property] [🗑 Wrong upload — remove] [⏭ Skip for now]

### Where this is enforced
- `ai/chat_planner.py::_is_property_isolated()` — detection
- `ai/chat_planner.py::_walkthrough_property_unverified_card()` — render
- Hook is in `_asset_walkthrough_question()` before the normal card path

The normal card is for properties WITH evidence — multiple images, or
identifiers cross-referenced to AI Summary text. Isolated → ask first.

---

## 10e. 🔥 BURN-IN — Asset Walkthrough Order: HIGH → LOW Confidence 🔥

**ALWAYS start with the asset that has HIGHEST confidence. LOWEST
confidence comes LAST. No exceptions, no random order, no "first
uploaded." This is a hard, non-negotiable rule.**

### Why
Resolving high-confidence assets first lets them claim their addresses,
beneficiaries, and supporting docs before low-confidence ones can steal
them. It also gives the user momentum — easy/clear cases get done fast,
ambiguous ones come later when context is built up.

### Confidence scoring (`services/gift_walker.py::_score_property_confidence`)

| Signal | Points |
|---|---|
| `title_type_confidence == "high"` | +3 |
| `title_type_confidence == "medium"` | +1 |
| Has `title_number` | +1 |
| Has `lot_number` | +1 |
| Has BOTH title + lot | +1 bonus |
| Real (non-NLC) street address | +2 |
| Owner names extracted | +1 |
| User explicitly mentioned this NLC id in chat/AI Summary | +3 |
| Some message context exists | +1 |

Max ≈ 13. Higher = inventoried first.

### Where it's enforced (TWO layers — both with burn-in comment blocks)

1. `services/gift_walker.py` — final `sorted(prop_groups.items(),
   key=_group_confidence, reverse=True)`. Marked with the "🔥 BURN-IN
   RULE" banner.
2. `ai/chat_planner.py::_asset_walkthrough_question()` — defensive
   re-sort of `props` by `_score_property_confidence` after filtering.
   Marked with the same banner.

### What this guarantees

- The first asset card the user sees in any walkthrough is the one
  with the strongest evidence (multi-image group + NLC ids matched
  to AI Summary).
- Isolated/unverified single-image docs (CLAUDE.md §10d) score low
  and surface LAST, after all the easy ones are confirmed.
- If you find a future feature that needs to skip / reorder assets,
  the new logic MUST preserve high-to-low order on what remains.

If a low-confidence card is ever shown before a high-confidence one,
**the bug is in this sort path** — fix it there, do not patch it
elsewhere.

---

## 10f. 🔥 BURN-IN — NO DUPLICATE GIFTS, EVER 🔥

**Same physical property = ONE gift. Different OCR readings of the same
title number do NOT make it two properties. NO DUPLICATES IN step5_data.
NO DUPLICATE CARDS IN THE WALKTHROUGH.**

### Where duplicates were appearing (all fixed)

1. **Pending walkthrough** — same lot, two different OCR titles → two cards.
   Fixed in `services/gift_walker.py` at the `(lot_signature, addr_signature)`
   merge step. Marked with BURN-IN banner.

2. **Pending vs accepted** — once a property is in step5_data, its sibling
   (different OCR title) was still appearing as a new pending card.
   Fixed by also building `referenced_lot_addr_sigs` from step5_data and
   filtering pending groups against it.

3. **step5_data placeholder insert** — `_try_save_property_gift` only
   deduped on `document_id`. Two Document rows for the same property
   produced two placeholder gifts. Fixed in `app.py` placeholder-insert
   block: dedup on (lot_digits, addr_signature) too. Marked with BURN-IN
   banner.

### Dedup signature (the canonical key)

```python
new_lot_digits = re.sub(r'\D', '', _clean_id_value(lot_number))
new_addr_sig   = _norm_addr(property_address)[:60]
sig = (new_lot_digits, new_addr_sig)
```

If `(lot_digits, addr_sig)` matches an existing gift → it's the SAME
property, regardless of title number drift. Do not insert. Do not show.

### What to do if a duplicate ever appears again

1. Run the cleanup script for the affected client (template in §10c).
2. Find the insert site that bypassed the dedup — it's missing the
   `(lot_digits, addr_sig)` check.
3. Add the BURN-IN banner above it. Do NOT fix it without the banner —
   the banner is the trail of evidence that proves the rule was applied.

---

## 11. Things NOT To Do

These are direct quotes / paraphrases of user feedback. Do not repeat these mistakes.

- ❌ Saying **"tested"** without actually sending an email
- ❌ Saying **"deployed"** after `docker compose restart` (image not rebuilt)
- ❌ Adding `inbox.` prefix back to the email address
- ❌ Putting **Executor / Witness** in the Step 1 Identity buttons
- ❌ Showing **exhibit thumbnails** in the AI Summary card
- ❌ Buttons that **render but do nothing** when clicked
- ❌ Asking about ICs / data the wizard **already knows**
- ❌ Skipping the **address-to-asset matching** during property review
- ❌ Suggesting Executor/Beneficiary **without** showing the text evidence
- ❌ Bypassing the test pipeline — every deploy ends with a real email test
- ❌ Showing **beneficiary** ("Client wants to give to X") on the property identity card
- ❌ Property count > AI Summary count — duplicate cards from OCR title-number drift
- ❌ Treating `VALUE: GRN56662` or `VALUE: (unreadable)` as a real title number
- ❌ Trusting raw extracted_data without cleaning AI-noise prefixes first
- ❌ Saying "this is fixed" without running `get_pending_gift_documents()` against the actual client and counting properties
- ❌ **HALLUCINATING assets or beneficiaries when a document is isolated / unreadable / cannot be identified.** If the title number is unreadable, the address is missing, the lot is garbage, or the document cannot be tied to any property in the AI Summary — **STOP**. Do NOT invent a property. Do NOT invent a beneficiary. Do NOT fabricate an address (no "10 Marsiling Lane Singapore" pulled from thin air). Mark the document as **isolated / needs human review** and SKIP it from the walkthrough. The chat must say "couldn't identify this — review manually" rather than create a fictitious asset card.
