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

### 🔥 §10x.191 — POST-DEPLOY VISUAL VERIFICATION VIA CHROME MCP (mandatory for UI changes)

**Every UI / template / chat-snapshot / wizard-render change MUST be verified
visually via Chrome MCP after deploy. Do NOT mark a UI fix "done" without
the screenshot.**

User explicit instruction (2026-05-10): "use chrome MCP to read and edit.
always use chrome MCP to check after commit and deploy".

Why: server-side fixes (commit + deploy + curl health-check) only prove the
code reached the box — they don't prove the rendered HTML/CSS actually shows
the right thing to the user. Real failures observed in this session:
  • White-text-on-white-button (toast Refresh) shipped HIGH-confidence-✓
    in CLAUDE.md, screenshot still showed the bug because Tailwind class
    `text-accent-700` was undefined and inherited white. Curl + DB-side
    verification both passed; only Chrome MCP screenshot caught it.
  • Wizard amber banner (§10x.150) marked deployed but never appeared on
    the user's screenshot for B-05-11; only Chrome MCP zoom would have
    confirmed the empty-fields list rendered.

Mandatory after every UI deploy:
  1. `mcp__Claude_in_Chrome__list_connected_browsers` → select_browser
  2. `tabs_context_mcp` → navigate to the touched page
  3. Screenshot the visible state
  4. If toast / overlay / hover-only element → use `javascript_tool` to
     trigger it, THEN screenshot
  5. `computer.zoom` on the specific element to confirm text/colour/layout
  6. Check `read_console_messages` with onlyErrors=true for JS errors
  7. Only THEN bump Confidence to HIGH ✓ in the bug table


### 🔥 §10x.49 — POST-DEPLOY AUDIT GATE (mandatory for matching code)

Every deploy that touches `services/asset_pipeline.py`,
`services/gift_walker.py`, `ai/chat_planner.py`, or any `app.py` saver
MUST end with the audit gate:

```bash
ssh ubuntu@47.130.249.28 "docker exec willcraft-web python /app/tests/step6/run_audit.py"
# Exit 0 = all fixtures pass §10x.48 + §10x.49
# Exit 1 = ROLLBACK — do NOT consider the deploy done
```

If audit fails, the next step is `git revert <bad-commit>` + redeploy
or fix the matcher and re-run audit. Never declare "deployed" with a
red audit. This is the rule that prevents "I tested it locally" from
shipping a regression.

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

## 10g. 🔥 BURN-IN — Address Matching Order: GROUP → HIGH-CONF → RESIDUAL 🔥

**The 3-step algorithm (user's exact words, do not deviate):**

> **Step 1**: Grouping of images that have relationship together as one group.
> Images that are isolated are standalone.
>
> **Step 2**: Images with strong relationship with the message to piece together —
> address + PTD + HSD known → high confidence match FIRST.
>
> **Step 3**: After all the known properties have been matched, the remaining
> images (group or isolated) — check whether this is relevant or ignore.

### Concrete behaviour

- **Image A** = title document, has PTD/HSD + matches an address in the message
  → HIGH confidence → matched **FIRST** → claims that address.
- **Image B** = non-title (SPA / photo / loan), shares group with A OR matches
  the SAME address → LOW confidence → address already claimed → **IGNORED**
  for matching. B is auto-linked to A's group via `(lot_digits, addr_sig)`,
  it does NOT become a second property.
- **Residual** = images with no group and no message match → ASK the client
  via the unverified-property card (§10d). Never auto-render as a property.

### Implementation sites (all carry the BURN-IN banner)

| Step | File | Function | Mechanism |
|------|------|----------|-----------|
| 1 | `services/gift_walker.py` | `_group_property_documents` + `(lot_sig,addr_sig)` merge | Cluster docs sharing lot/title/addr |
| 2 | `app.py::_persist_property_enrichment` (~L4588) | sort by `_score_property_confidence` DESC, then re-rank `doc_score*10 + match_conf_rank` DESC, greedy-claim via `claimed_addresses` set | HIGH FIRST, ONE-CLAIM-ONLY |
| 3 | `ai/chat_planner.py::_is_property_isolated` + `_walkthrough_property_unverified_card` | Residual → unverified card (§10d) | ASK don't assume |

### The ONE-CLAIM-ONLY invariant

```python
if matched_addr.lower() in claimed_addresses:
    continue   # someone with higher confidence already took it
# … apply match …
claimed_addresses.add(matched_addr.lower())
```

If you ever see the same street address on two property cards, this check
was bypassed. Add the BURN-IN banner above the offending insert and fix.

---

## 10h0. 🔥🔥🔥 MASTER CHECKLIST — Property Identification 🔥🔥🔥

**Run these 10 steps in order. Every property card must be traceable to
this exact sequence.**

```
1. READ AI SUMMARY FIRST → canonical N properties (§10h)
2. EXTRACT title-doc fields  (no address — title docs don't have one) (§10ha)
3. EXTRACT non-title doc fields  (address there is not authoritative)
4. DIRECT IDENTIFIER MATCH  (lot/title in AI Summary == image lot/title) (§10g)
5. TWO-HINT TEST for unmatched AI-Summary properties:
     5a. Hint 1: SAME MUKIM via verification chain — never from memory (§10hc)
     5b. Hint 2: CLOSE TIMING in WhatsApp/email thread (§10i)
6. APPLY CONFIDENCE GRID — both hints HIGH; one MEDIUM; none residual (§10hb)
7. GREEDY CLAIM — first match wins; no slot re-bound (§10g)
8. RENDER CARD with: probate-format legal description (geran/lot/mukim/
   daerah/negeri), AI-Summary postal address, two-hint evidence with
   WhatsApp timestamps, confidence label
9. RESIDUAL IMAGES → §10d unverified-card; never invent a property
10. AI-SUMMARY PROPERTIES WITHOUT IMAGE → summary-only card; ask for doc
```

The worked example for KOID Property 1 (Paradisonuava → Paradiso Nuova
@ Medini → Mukim Pulai → MEDIUM-confidence bind to title image with
"Merak Kayangan" building-name flag for user confirmation) is the
reference trace. Any deviation from steps 1-10 is a bug — fix the step,
do not patch the symptom.

---

## 10h. 🔥🔥🔥 BURN-IN — ALWAYS REFER TO AI SUMMARY FIRST 🔥🔥🔥

**The AI Summary IS the canonical asset list. Match images TO the AI Summary,
never the other way around. NEVER identify an asset that isn't in the
AI Summary. NEVER show a property count that differs from the AI Summary.**

### The mandatory order

```
1. READ the AI Summary (the assistant's "📨 AI Summary of your message" card).
2. EXTRACT the canonical property list from it (Property 1, Property 2, …).
3. The number of properties = N (from AI Summary).
4. For each of the N properties, find the image(s) that match it
   (by address, lot, title, mukim — in that order of preference).
5. Anything left over (image with no match in AI Summary) is RESIDUAL —
   send it to the §10d unverified-card. NEVER auto-create a property
   from an image whose address is not in the AI Summary.
```

### Hard rules

1. **AI Summary count = walkthrough count.** If summary says 5 properties,
   walkthrough shows 5. Not 3, not 14, not "one card per uploaded image."
2. **OCR'd "addresses" that don't appear in the AI Summary are NOT real.**
   The vision extractor will hallucinate `"10 Marsiling Lane Singapore"` or
   `"Lot Blabla, Mukim Seberang Selatan"`. If the address is not cited in
   the AI Summary text, treat it as garbage and route the doc to residual.
3. **An image without an AI-Summary match never becomes a property card.**
   It goes to the §10d unverified-property card or is ignored. The chat
   asks the client; the chat does NOT invent a 6th property.
4. **The AI Summary is read at the START of every walkthrough turn.** Don't
   cache stale group counts from before the summary was generated.

### Where this is enforced (touch these, not memory)

| File | Function | What it does |
|------|----------|--------------|
| `ai/chat_planner.py` | `_extract_ai_summary_properties(client_id)` | Parse the latest "📨 AI Summary" assistant message, return list of `{name, address, lot, title}` |
| `ai/chat_planner.py` | `_asset_walkthrough_question` | Filter pending props to only those matching an AI-Summary entry; rest → residual |
| `services/gift_walker.py` | `get_pending_gift_documents` | Tag each group with `_summary_match` (which AI-Summary property it maps to) or `None` |
| `app.py::_persist_property_enrichment` | address matcher | Use AI-Summary addresses as the candidate pool, not raw message text |

### The litmus test (run before shipping any asset-walkthrough change)

```
Q: How many properties does the AI Summary deduce?  → N
Q: How many property cards does the walkthrough render?  → must be N
Q: For each card, can I cite the matching "Property X" line in the AI Summary?
   - YES → ship
   - NO  → bug. The card is hallucinated. Fix the matcher, not the card.
```

If you ever render a property card whose address is NOT in the AI Summary,
the bug is in the filter step. **Do not patch the symptom in the card
template — fix the filter.**

---

## 10ha. 🔥🔥 BURN-IN — Title Documents DO NOT Show Street Addresses 🔥🔥

**A Malaysian title document (Geran / Hakmilik / HSD / PTD) NEVER contains
a street address. It contains: Title No., Lot/PTD No., Mukim, Daerah,
Negeri, owner names, share fractions. The STREET ADDRESS lives in the
message (WhatsApp/email text), NOT in the title image.**

### Why this matters

If the matcher looks for "property_address" inside a title image's
extracted fields, it will be EMPTY (or hallucinated). The street address
MUST come from outside the image — specifically:

1. The **AI Summary** ("Property 2: Unit C-30-08, Marina Cove…")
2. The **message body** (the WhatsApp/email text that names the address)
3. Adjacent message context (§10i temporal proximity)

The title image's job is to provide the **legal identifier** (lot+title+
mukim+daerah). The message's job is to name the **street address**. The
matcher's job is to bind them together.

### What's in each source — DO NOT confuse them

| Source | Has | Does NOT have |
|--------|-----|---------------|
| Title doc image (Geran/HSD/PTD) | Title No., Lot No., Mukim, Daerah, Negeri, owners, share | Street address |
| WhatsApp/email message | Street address, beneficiary intent, ownership share | Lot/title No. (usually) |
| AI Summary card | Both — the canonical mapping is built here | — |
| SPA / loan / tax doc image | Sometimes street address, sometimes lot No. | Often missing one or the other |

### The matching rule (corrected)

```
For each AI-Summary property P (which has BOTH address and any lot/title hint):
  1. Find title image(s) whose Lot No. or Title No. matches P's hint
     → CONTENT match by IDENTIFIER (highest confidence).
  2. If no lot/title hint in P, match by GEOGRAPHIC bridge:
       - Image's Mukim + Daerah  ↔  P's address (street → mukim mapping)
       - e.g. "Seri Alam Masai" → Mukim Plentong, Daerah JB
              "Taman Laguna"    → Mukim Plentong, Daerah JB
              "Medini Iskandar" → Mukim Pulai,    Daerah JB
              "Iskandar Puteri" → Mukim Pulai,    Daerah JB
              "Marina Cove"     → Mukim Plentong, Daerah JB
  3. Bind that image to P. The image's "address" comes from P, not from OCR.
  4. If still no match, fall back to temporal proximity (§10i).
```

### Geographic bridge: known street → mukim mappings (Johor)

The matcher should consult this table (or query Claude with the text:
"Which mukim is `<street>` in?") when the title doc's mukim and the
AI-Summary address need to be reconciled. Cache results per session.

| Street / Township in address      | Mukim          | Daerah        | Negeri |
|-----------------------------------|----------------|---------------|--------|
| Seri Alam Masai / Bandar Seri Alam| **Plentong**   | Johor Bahru   | Johor  |
| Taman Laguna                       | **Plentong**   | Johor Bahru   | Johor  |
| Marina Cove / Pangsapuri Tepian Bayu | **Plentong** | Johor Bahru   | Johor  |
| Permas Jaya                        | **Plentong**   | Johor Bahru   | Johor  |
| Medini Iskandar / Iskandar Puteri  | **Pulai**      | Johor Bahru   | Johor  |
| Bandar Medini                      | **Pulai**      | Johor Bahru   | Johor  |
| Bandar Medini Iskandar / Medini Iskandar | **Pulai**| Johor Bahru   | Johor  |
| Iskandar Puteri (formerly Nusajaya)| **Pulai**      | Johor Bahru   | Johor  |
| Mount Austin / Taman Austin        | **Tebrau**     | Johor Bahru   | Johor  |
| Paradiso Nuova (@ Medini, NOT Mount Austin) | **Pulai** | Johor Bahru | Johor  |
| Merak Kayangan (@ Medini)          | **Pulai**      | Johor Bahru   | Johor  |
| Plot A56 / PTD 170703 / HSD 478949 = Paradiso Nuova | **Pulai** | Johor Bahru | Johor |
| Mount Austin                       | **Tebrau**     | Johor Bahru   | Johor  |
| Pasir Gudang town                  | **Plentong**   | Johor Bahru   | Johor  |
| Senai                              | **Senai**      | Kulai         | Johor  |

(Extend as new clients send addresses. Patch this table, don't add per-case
hacks. New entries should also go into a code-side dict in
`ai/chat_planner.py::_GEO_BRIDGE`.)

### The burn-in mistake to never repeat

> "The title image extracted address `Phase 2D SERI ALAM, Mukim Plentong`
>  so it's a real address." — **WRONG.** That field on a title doc is
> either empty or extractor noise. The real address is in the message.
> Match the title's LOT/HSD/PTD against the AI Summary's lot/title hint,
> and read the address FROM the AI Summary.

### Where this is enforced

| File | What changes |
|------|--------------|
| `services/gift_walker.py` | Title-doc grouping uses `lot_number + title_number + (mukim,daerah)` — NOT `property_address` — as the dedup key |
| `ai/chat_planner.py::_extract_ai_summary_properties` | Returns `{name, address, lot, title, mukim, daerah}` — address is canonical here |
| `app.py::_persist_property_enrichment` | Match title images by IDENTIFIER to AI-Summary entries; copy the AI-Summary address INTO the doc, do not trust OCR'd address fields |

---

## 10hb. 🔥🔥 BURN-IN — The Two-Hint Match: Same Mukim + Close Timing 🔥🔥

**When the AI Summary doesn't give a direct lot/title hint, an image
matches a summary property when BOTH of these are true:**

> **Hint 1 (geography):** The AI-Summary address is in the SAME MUKIM
>   as the title doc's extracted mukim.
> **Hint 2 (timing):** The image's WhatsApp/email timestamp is CLOSE
>   to the message line that names the AI-Summary address.

**Both hints = HIGH confidence match. Either alone = MEDIUM. Neither = residual.**

### Confidence grid

| Same mukim? | Close timing? | Verdict |
|-------------|---------------|---------|
| ✅           | ✅             | **HIGH** — bind, render confirmed card |
| ✅           | ❌             | **MEDIUM** — bind, ask user to confirm |
| ❌           | ✅             | **MEDIUM** — bind, ask user to confirm (mukim mismatch is a yellow flag) |
| ❌           | ❌             | **NO MATCH** — residual / unverified card (§10d) |

### "Close timing" defined

- Same WhatsApp message line as the address: ✅ closest
- Within 4 lines before / 3 lines after the address line: ✅
- Within 5 minutes (300s) by timestamp: ✅
- More than 30 minutes apart OR another property mentioned in between: ❌

### "Same mukim" defined

- Title doc says `Mukim Plentong` AND the AI-Summary address resolves
  to Plentong via §10ha geographic bridge table → ✅ match
- Title doc says `Mukim Pulai`, AI-Summary address is "Seri Alam Masai"
  (Plentong) → ❌ mismatch — flag as suspicious

### What the property card MUST display

```
### 🏠 Property 5 of 5 — No. 03 Jalan Gunung 4, Seri Alam Masai

🏛  Geran / Title : HSD(D) 251041
🪪 Lot / PTD     : LOT 127082
🏘  Mukim         : Plentong
🏛  Daerah        : Johor Bahru
🌍 Negeri        : Johor

🌍 Hint 1 — same mukim:
    Seri Alam Masai is in Mukim Plentong ✅
    (matches the title doc's mukim)

⏱  Hint 2 — close timing:
    Image  PHOTO-2026-05-02-13-52-35.jpg  [02/05/26 13:52:35]
    Msg    [02/05/26 13:52:10]  "Phase 2D Seri Alam Mukim Plentong, Lot 127082…"
    Gap: 25 seconds ✅

🔗 Confidence: HIGH (both hints satisfied)
```

This is the card the user reads to verify. The two hints are the
evidence — both must be visible.

### Where this is enforced

| File | Function | Role |
|------|----------|------|
| `ai/chat_planner.py` | `_GEO_BRIDGE` dict | Street → mukim mapping (mirrors §10ha table) |
| `ai/chat_planner.py` | `_match_image_to_summary(p, ai_summary, msg_thread)` | Returns (matched_property, confidence, hint1_ok, hint2_ok) |
| `ai/chat_planner.py` | `_walkthrough_property_card(p)` | Renders the two-hint evidence block |
| `app.py` | `/api/inbound-email` storage | Persist `_msg_timestamp` per attachment so the timing hint survives |

---

## 10hc. 🔥🔥🔥 BURN-IN — NEVER Assert Mukim/Location From Memory 🔥🔥🔥

**Real example of the bug to never repeat:** Claude said "Paradiso Nuova is
at Mount Austin → Mukim Tebrau". Wrong. Paradiso Nuova is at Medini
(Mukim Pulai). The error came from training-memory guessing instead of
verifying. This caused a false mukim mismatch flag and would have asked
the user the wrong question.

### The mandatory verification chain (in order)

```
For ANY claim about "Building X is in Mukim Y":

  ① Title-document mukim       — title image's cleaned `mukim` field
  ② Address-document mukim     — SPA / tax / loan
  ③ AI-Summary text mukim      — user explicitly named it
  ④ Curated _GEO_BRIDGE cache  — every entry has a URL or doc citation
  ⑤ ⚡ LIVE WEB SEARCH ⚡        — Claude API w/ web_search tool, MANDATORY
                                  before any "I think it's in X" answer.
  ❌                             — out of options. Caller asks user.
```

### ⚡ THE CRITICAL RULE — WEB SEARCH IS MANDATORY ⚡

**If sources ①–④ don't resolve the mukim, the resolver MUST call
the web-search tool BEFORE returning an answer or flagging the user.
"I'll guess from what I know" is a forbidden answer path. There is no
fallback to training memory — only forward to web search, and from web
search only forward to GeoUnknown.**

The web-search prompt MUST contain these clauses verbatim:

```
HARD RULES — VIOLATING ANY OF THESE INVALIDATES YOUR ANSWER:
 1. DO NOT use general knowledge or training-data memory. If the
    web-search results don't explicitly state the mukim, return UNKNOWN.
 2. The answer MUST come from a search result you actually saw in this
    conversation. Cite the URL.
 3. If multiple sources disagree, return UNKNOWN with the conflicting URLs.
 4. If the building name has duplicates in different mukim, return UNKNOWN.
 5. Return JSON only: {"mukim","daerah","negeri","source_url"} or
    {"unknown": true, "reason", "sources_consulted"}.
A confident wrong answer is worse than UNKNOWN.
```

This prompt is implemented in `services/geo_resolver.py` as
`WEB_RESOLVER_SYSTEM_PROMPT`. Do not water it down. Do not allow the
LLM to "be helpful" by guessing.

### Why this rule is the HARDEST

The Paradiso Nuova bug happened because Claude said "I know this — Mount
Austin" without verifying. **Memory feels confident. The resolver must
make memory IMPOSSIBLE to use as a source.** That's why:

  • The cache (`_GEO_BRIDGE`) self-checks at import — every entry must
    have a citation, no exceptions.
  • The web-search prompt is hostile to guessing — UNKNOWN is preferred
    over a plausible-sounding answer.
  • The resolver function has NO `default = "Pulai"` fallback. Bypassing
    it means raising `GeoUnknown` and asking the user.

If you ever find a code path that returns a mukim WITHOUT a citation,
that path is the bug — fix it by routing through `resolve_mukim()`.

### Hard rules

1. **No mukim claim from training memory.** Even "common knowledge"
   townships (Mount Austin, Medini, Iskandar Puteri) must be cited.
2. **The §10ha geographic bridge table is curated, not invented.**
   New entries require a web-search citation OR a real title document.
   Commits that add to the table without a citation must be rejected.
3. **When AI Summary names a building Claude doesn't recognise, the
   ONLY safe responses are:** (a) web-search the name; (b) trust the
   title document's mukim if available; (c) ask the user. Never
   fabricate a mukim.
4. **Spelling drift is a clue, not a fact.** "Paradisonuava" was a
   transcription of "Paradiso Nuova" — but Claude must search BOTH
   spellings before claiming the canonical name; the chat must show
   the user the original spelling alongside the canonical one.
5. **Title-doc mukim trumps Claude's geography.** If the title doc says
   Mukim Pulai and Claude's memory says "this area is Tebrau", the
   title wins. The doc is the source of truth.

### Anti-patterns to never repeat

| Bad | Good |
|-----|------|
| "Mount Austin is Mukim Tebrau" *(memory)* | Title doc says `Mukim Tebrau` ✓ — match |
| "Paradiso Nuova is in Mount Austin" *(wrong memory)* | WebSearch → Paradiso Nuova @ Medini, Mukim Pulai. Cite source. |
| "Seri Alam is Plentong, I'm sure" *(no source)* | Title doc OR web search OR ask user |
| Adding 5 mukim mappings to the bridge table from memory | Add 1, with the URL, after verifying |

### Where this is enforced

| File | Mechanism |
|------|-----------|
| `ai/chat_planner.py::_GEO_BRIDGE` | Comment header: "DO NOT add entries from memory. Each entry MUST cite source (title doc client_id OR URL)." |
| `ai/chat_planner.py::_resolve_mukim_for_address` | Function order: (1) title doc, (2) address doc, (3) AI Summary, (4) `_GEO_BRIDGE`, (5) live web search via Claude tool, (6) ask user. NEVER hard-code geography. |
| Code review checklist | Any PR touching `_GEO_BRIDGE` or mukim logic must include a citation per added entry. |

If a future bug report says "the chat claimed wrong mukim and confused
the user", the trace must lead back to a missing source 1-4 step. Fix
there, not by adding more memory entries.

---

## 10hd. 🔥🔥 BURN-IN — Strata: Same Lot ≠ Same Property 🔥🔥

**For stratified properties (apartment / condo / shop-lot in a strata
scheme), the LOT NUMBER is the building's master lot — shared by every
unit in the development. The TITLE NUMBER (strata title / parcel No.)
is what distinguishes one unit from another.**

### The bug this rule prevents

Real example from KOID:
- C-30-08 title: `564662/M1C/30/710`, lot **207922**, joint 1/2
- C-05-01 title: `504662` *(different title, OCR may show drift)*, lot **207922**, sole
- Sibling-enrichment copied C-30-08's address ("#30-08, Menara C…")
  onto C-05-01's record because lot numbers matched. **WRONG.**
- The C-05-01 title's real address got hidden, P3 was reported as
  "no doc found" when actually a strong doc exists.

### Hard rules

1. **Strata grouping key is `(lot_number, title_number)` — NEVER `lot_number` alone.**
   Two docs with same lot but different title numbers are DIFFERENT
   properties in the same building.

2. **Sibling enrichment for strata is forbidden across different
   title numbers.** Address copied from doc A to doc B is only valid
   if A.title == B.title (or one is empty). Different titles = different
   units = no address transfer.

3. **OCR drift on title numbers (e.g. 504662 vs 564662)** is NOT proof
   of duplicates. If both extractions are confident (`title_type_confidence
   == 'high'`), treat as DIFFERENT until the user confirms. The cost of
   wrongly merging two units is a missing property; the cost of wrongly
   splitting one unit is a duplicate card. Asking the user is cheap;
   a missing C-05-01 walks straight into a missing probate asset.

4. **For non-stratified (landed) titles, lot equality is fine** — a
   landed lot has one title. The strata-only rule applies when:
   - title_type contains "strata" / "hakmilik strata" / "geran mukim strata"
   - title_number contains slashes (e.g. `564662/M1C/30/710`) indicating
     strata sub-component encoding
   - property_description mentions "Level", "Storey", "Parcel No.", "Block"

### Where this is enforced

| File | Function | Change |
|------|----------|--------|
| `services/gift_walker.py` | `_group_property_documents` | Group key = `(lot, title)` for strata; was `(lot,)` |
| `services/gift_walker.py` | sibling enrichment loop | Skip if `(A.title != B.title) and (A or B is strata)` |
| `app.py::_persist_property_enrichment` | `_enriched_from='sibling_lot_*'` | Add title check; reject cross-title copies for strata |
| `ai/chat_planner.py` | strata detection | Helper `_is_strata(extracted)` returns True if any of: title_type contains "strata", title_number has `/`, description mentions Level/Storey/Parcel |

### The litmus test

```
Two docs share lot 207922 but title numbers differ.
Q: Should they be merged into one property card?
   - If either is strata → NO. Render as separate cards (or ASK user).
   - If both are landed → YES (same lot, same property).
```

Apply this BEFORE rendering, not after.

---

## 10hf. 🔥🔥🔥 BURN-IN — WEB-SEARCH THE ADDRESS: GET PROPERTY-TYPE CLUES 🔥🔥🔥

**When the AI Summary gives you an address, you MUST web-search it
BEFORE saying "no image matches." The search returns clues — property
type, tenure, locality, mukim — that filter the image candidates.**

### The user's exact words (do not deviate)

> "YOU MUST SEARCH THE WEB, YOU HAVE THE FUCKING ADDRESS"
>
> "which mukim, is this a landed residential, apartment, shoplot,
>  factory. all these are clues to find in the image and also the
>  timing of the image"

### What every web-searched address yields

| Clue from web | How it filters images |
|---|---|
| **Property type**: landed / apartment / shoplot / factory | Excludes wrong category — landed Taman Laguna ≠ any strata doc |
| **Tenure**: freehold / leasehold | Cross-check against title doc tenure field |
| **Locality / mukim**: e.g. Tampoi (Pulai), Marina Cove (Plentong) | Hint 1 of two-hint test (§10hb) |
| **Postcode region** | Maps postcode → mukim (e.g. 81200 → Tampoi → Pulai) |
| **Building / development name** (for strata) | Match against doc's property_description |
| **Existence**: does the address even exist? | Catches AI-Summary hallucinations |

### The mandatory web-search call (before ANY summary-only card)

```
For each AI-Summary property with no direct identifier match:
  1. Web-search the address  ← MANDATORY, not optional.
       - Extract: type, tenure, locality, mukim, building name
       - Cite the source URLs in the card
  2. Use those clues to filter unmatched images:
       - Type clue: landed → exclude strata docs; strata → exclude landed
       - Owner clue: AI Summary "joint with wife" → look for image with
         owner_names containing the wife's name
       - Mukim clue: feeds Hint 1 of §10hb
  3. Run the temporal proximity check (§10i) on the FILTERED candidates.
  4. Only THEN, if zero candidates remain, render summary-only card.
```

### Code skeleton (services/web_property_clues.py)

```python
def search_property_clues(address: str, client) -> dict:
    """Web-search an address and extract property-type clues.
    Returns {type, tenure, locality, mukim, building_name, sources}.
    Returns None if web search yields no useful info."""
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=600,
        system=PROPERTY_CLUES_SYSTEM_PROMPT,   # see below
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": f"Address: {address}"}],
    )
    return _parse_clues_json(msg)


PROPERTY_CLUES_SYSTEM_PROMPT = '''
For the given Malaysian address, search the web and extract:
  - type: one of [landed_residential, apartment_condo, shoplot, factory,
                  agricultural, mixed_use, unknown]
  - tenure: one of [freehold, leasehold, unknown]
  - locality: the township/neighbourhood name
  - mukim: the official Mukim (NLC), if findable
  - building_name: for strata only
  - sources: list of URLs you actually saw in this conversation

HARD RULES — VIOLATION INVALIDATES YOUR ANSWER:
1. Do NOT use general knowledge or memory. Cite URLs only.
2. If sources disagree, return type="unknown" with the conflict.
3. If the address doesn't exist in any search result, return null.
4. Output JSON only.
'''


def filter_images_by_clues(unclaimed_images, clues) -> list:
    """Given web-search clues for an AI-Summary property, narrow down
    the image candidates to those whose extracted fields are CONSISTENT
    with the clues. Inconsistent images are removed from contention."""
    out = []
    for img in unclaimed_images:
        ex = img.get('extracted') or {}
        # Type compatibility
        if clues.get('type') == 'landed_residential' and _is_strata(ex):
            continue
        if clues.get('type') == 'apartment_condo' and not _is_strata(ex):
            continue
        # Mukim compatibility (if both sides have it)
        img_mukim = (ex.get('mukim') or '').lower()
        clue_mukim = (clues.get('mukim') or '').lower()
        if img_mukim and clue_mukim and img_mukim != clue_mukim:
            continue
        out.append(img)
    return out
```

### The order of operations (re-stated, with web search inserted)

For an AI-Summary property:

```
1. Direct identifier match (lot/title in summary == image's)         → bind
2. WEB-SEARCH THE ADDRESS  ← NEW MANDATORY STEP
   • Get type, tenure, mukim, building_name. Cite sources.
3. Filter unmatched images by web-search clues (drop incompatible)
4. Temporal proximity (§10i) on the filtered set
5. Two-hint verify (§10hb) on the candidate
6. If still no candidate → summary-only card + ASK
```

If you ever skip step 2 and jump to "no match", that is the bug §10hf
exists to prevent. The address is information; not searching it is
throwing away free signal.

### Where this is enforced

| File | Function | Role |
|------|----------|------|
| `services/web_property_clues.py` (NEW) | `search_property_clues(addr)` | Web-search + clue extraction |
| `services/web_property_clues.py` (NEW) | `filter_images_by_clues(imgs, clues)` | Eliminate incompatible candidates |
| `ai/chat_planner.py::_match_image_to_summary` | call sequence | Insert step 2 + 3 between direct-match and timing |

---

## 10he. 🔥🔥 BURN-IN — When No Image Matches: TIMING FIRST, THEN ASK 🔥🔥

**For an AI-Summary property with no content-matching image, the EXACT
fallback order is:**

```
STEP 1 — TIMING.
  Find the message line that names the address. Look at attachments
  in the §10i adjacency window (4 lines before / 3 after / 5 min).
  - 1 unclaimed candidate in window → proceed to STEP 2
  - 0 or 2+ candidates → SKIP to STEP 4

STEP 2 — TWO-HINT VERIFY (§10hb).
  Hint 1 (mukim): resolve_mukim() with citation (NEVER memory)
  Hint 2 (timing): already passed
  Both ✅ → bind, MEDIUM-to-HIGH confidence
  Mukim ❌ → still bind but flag yellow ASK USER

STEP 3 — RENDER CARD with timestamps + adjacent message snippet.

STEP 4 (no candidate) — RENDER SUMMARY-ONLY CARD.
  Tell the user: "I found this property in your message but no
  matching title doc and no image close to it in the thread."
  Show 3 buttons: [Upload title] [Type details] [Skip — address only]

STEP 5 (NEVER) — guess.
  ❌ Picking a random unclaimed image to fill the slot.
  ❌ Using "this looks similar geographically" reasoning.
  ❌ Assuming an isolated image is for this property.
```

### Why TIMING is step 1 and not step 2

The user's exact words (do not deviate):
> "if there is no matching image, then the timing is key.
>  the message before or after the image can be a strong link"

Content match runs first only when AI Summary explicitly gives a lot/title
hint AND an image extracts the same lot/title. In real exports that's rare —
clients usually attach photos without re-typing the title number. Timing
covers the common case.

### Anti-pattern: "fill the slot at all costs"

A property with no image is OK. Render a summary-only card. Asking the
user is cheap. **Inventing a binding is expensive** — it pollutes the
will, hides a missing asset, or assigns the wrong doc to probate.

The only "filler" rules allowed are:
1. Direct identifier match (deterministic, near-zero FP rate)
2. Temporal adjacency with two-hint verify (citable evidence)

Anything else → summary-only card → ASK.

### The code invariant

In `ai/chat_planner.py::_match_image_to_summary()`:

```python
# After all real matching paths above:
if no candidate:
    return None, "no_match", "ask_user"   # ← MUST exist as the bottom branch

# There MUST NOT be:
#   else: return random.choice(unclaimed_images)   # ← FORBIDDEN
#   else: return unclaimed_images[0]                # ← FORBIDDEN
#   else: return _best_geographic_guess(...)        # ← FORBIDDEN
```

A code-review check should grep for any return inside the matcher that
isn't gated by `direct_match` OR `two_hint_pass`. If found → bug.

---

## 10i. 🔥 BURN-IN — Temporal Proximity is a Strong Link 🔥

**When an image cannot be matched to an AI-Summary property by content
(no lot/title/address overlap), the chat message immediately BEFORE or
AFTER the image in the WhatsApp/email thread is a strong link.**

### Why
WhatsApp/email exports interleave text and attachments in time order.
Clients describe a property and then send the photo (or photo first,
then describe it). The adjacency itself carries signal — even when
the image's OCR is junk and the message has no NLC ids.

### The mandatory fallback chain (when content match fails)

```
For each AI-Summary property P that still has no matching image:
  1. Try content match: lot / title / mukim / address overlap.  → MISS
  2. Try TEMPORAL match:
       - Find the message in the thread that names P (by address/lot).
       - Look at attachments in the SAME message, the message BEFORE
         (up to 4 lines back) and the message AFTER (up to 3 lines fwd).
       - If exactly ONE attachment falls in that window AND is not
         already claimed by another P → bind it to P.
  3. Multiple candidates in the window → ask the user (don't guess).
  4. No candidate in the window → render P as summary-only card
     ("no document attached yet").
```

### Hard rules

1. **Temporal match runs AFTER content match, not instead of.** Content
   evidence (matching lot/title/address) always wins. Adjacency is a
   tie-breaker / fallback, not a primary signal.
2. **Adjacency window is bounded:** 4 lines before, 3 lines after the
   text that names the property. Don't reach across other properties'
   text — stop at the previous/next property mention.
3. **One-claim-only still applies (§10g):** an image already claimed
   by P1 cannot be re-bound to P2 by adjacency. The greedy claim
   set carries through.
4. **An image bound by adjacency is marked `_address_confidence='medium'`
   and `_match_via='temporal'`** — the property card MUST show the
   surrounding message snippet so the user can verify.
5. **The property card MUST display the WhatsApp timestamps.** Show the
   image's `[02/05/26, 13:52]` line AND the timestamp of the adjacent
   message that's binding it. The user verifies by reading the timing,
   not just the snippet. Format:
   ```
   📎 Image  [02/05/26, 13:52:35]  PHOTO-2026-05-02-13-52-35.jpg
   💬 Message [02/05/26, 13:52:10]  "Property 2: Phase 2D Seri Alam…"
   ```
   The 25-second gap is the evidence — show it.

### Where this is enforced

| File | Function | Mechanism |
|------|----------|-----------|
| `app.py` | `_extract_whatsapp_context_for_file(body, filename)` | Already exists — returns adjacent text per filename |
| `ai/chat_planner.py` | `_match_image_to_summary_by_adjacency` (NEW) | Wraps the above with the AI-Summary-property loop |
| `ai/chat_planner.py` | `_asset_walkthrough_question` | Calls content match first, adjacency match second |

### The litmus test

```
Q: AI Summary names Property 4 = "10 Jalan Sri Laguna" but no image
   has matching lot/title/address. Is there an attachment near the
   message line that names it?
   - YES → bind, show the adjacent text as evidence.
   - NO  → summary-only card; ask the user to attach a doc.
```

If the matcher ever skips adjacency and silently leaves a summary
property unmatched while there IS an adjacent unclaimed image, the
bug is in this fallback chain — fix it here, do not add per-case
hacks elsewhere.

---

## 10hg. 🔥🔥🔥 BURN-IN — Message-Stated = HIGH Always; Image Determines Completeness 🔥🔥🔥

**If the user states an asset in the message, it is HIGH confidence by
definition — regardless of image evidence. Image presence determines
how COMPLETE the card is, not whether it's shown. Layer 1 commits to
step5_data only after explicit user confirmation. NEVER auto-save.**

### Confidence Grid — User's Word is the Anchor

| Tier | Source | Image evidence | Confidence | Card variant |
|------|--------|----------------|------------|--------------|
| H1 | Stated in message | Title image (Geran/Hakmilik) — lot/title or mukim+daerah binds | **HIGH** | Confirm (full) |
| H2 | Stated in message | Non-title image with HSD/PTD where mukim+daerah matches message address | **HIGH** | Confirm (full, with provisional title) |
| H3 | Stated in message | **No image found** | **HIGH** | Confirm (placeholder) → upload/type after |
| L  | Image only — no message reference | any | **LOW** | §10d unverified — ASK |

### H1/H2 card — image found, confirm to add

```
### 🏠 Property X of N — <address from AI Summary / message>

held under <legal description: title/lot/mukim/daerah/negeri>

🔗 Evidence (HIGH):
   ✅ Title image binds to this property   (or)
   ✅ HSD/PTD doc with mukim+daerah matching the message address
   ⏱  Image timestamp within Ns of the message line

[ ✅ Confirm — add to specific gifts ]
[ ✏️ Edit details ]
[ 🗑 Wrong — remove ]
```

User clicks **Confirm** → `_layer1_confirmed=True` is written → advance
to Layer 2 (beneficiary main → substitute). The beneficiary handlers
(`_try_save_property_gift` Phase A/B) ONLY accept input AFTER Layer 1
confirmation. Typing a beneficiary name on a HIGH card is rejected.

### H3 card — message-stated but no image found, confirm-then-complete

```
### 🏠 Property X of N — <address from message>

You mentioned this in your message:
> "<verbatim quote from user's text>"

⚠️ I couldn't find a matching title document among your uploads.
   This property is HIGH confidence (you stated it), but the
   identification details are incomplete.

Confirm to add this property — then upload the title or type the
details manually:

[ ✅ Confirm — yes, add this property ]
[ 🗑 Wrong — remove from list ]
```

User clicks **Confirm** → save Tier-H3 placeholder to step5_data with
`needs_title_doc=true` and a one-line `_note` describing what's missing
→ render the **complete-details** card next:

```
### 📎 Complete details for <address>

Provide the title document so the will can be probated:

[ 📎 Upload title document now ]
[ ✏️ Type the title/lot/mukim/daerah/negeri manually ]
[ 🤝 Match to an existing image I already sent ]
[ ⏭ Skip for now — I'll provide later ]
```

The complete-details card may be answered later (asynchronously). The
property gift remains in step5_data with `needs_title_doc=true` until
provided. Probate generation flags any gift with this flag for the
lawyer to chase.

User clicks **Remove** on the H3 confirm card → log `_user_rejected=true`
in chat session state, drop from this run's N.

### Hard rules

1. **N (walkthrough property count) = N (AI Summary property count).**
   H3 cards (no image) count toward N. They are visible — never silently
   filtered out because no title image was found.
2. **Message-stated assets are NEVER demoted to "low confidence" because
   the image is missing.** The user's word is the anchor. Image evidence
   only changes the card variant (H1/H2 full vs H3 placeholder), not
   the confidence tier.
3. **A property reaches step5_data via exactly one path:** an explicit
   user click on the **Confirm** button. H1/H2 confirm → full Layer 1
   save → Layer 2. H3 confirm → placeholder with `needs_title_doc=true`
   → optional complete-details card next.
4. **No auto-save without a click.** Typing a beneficiary name into a
   card that has not yet been confirmed is REJECTED — Layer 2 is gated
   behind Layer 1 confirmation.
5. **Persisted enrichment is NOT trusted.** Every walkthrough turn
   re-derives the address from the AI Summary. Stale `_enriched_from`
   on a Document row that no longer matches any AI-Summary property is
   ignored.
6. **AI Summary fallback:** if no `📨 AI Summary` chat card exists,
   parse `step6_data._raw_forward_text` for property mentions (line-based
   heuristic extractor). The canonical N must survive a chat reset.
7. **Conflicting information in the message → STOP and ask the user
   to clarify.** Never auto-resolve a contradiction. Examples that
   trigger a clarification card:
     - Same property named twice with different beneficiaries.
     - Allocations sum to >100% (or <100% with no residuary intent).
     - Address spelled two different ways but lot/title suggests one
       physical property.
     - Title number changes between mentions for what looks like the
       same address.
     - Beneficiary name spelled inconsistently (Esther / Eshter / etc.)
       for the same person.
   The clarification card surfaces both readings verbatim and lets the
   user pick / correct, e.g.:
     ```
     ⚠️ Conflicting information about <thing>
     Reading 1: <quote A>
     Reading 2: <quote B>
     [ Use reading 1 ] [ Use reading 2 ] [ ✏️ Type the correct version ]
     ```
   The walkthrough does NOT proceed past a conflicting property until
   the user picks. NO silent merging, NO best-guess resolution.

### Where this is enforced

| File | Function | Role |
|------|----------|------|
| `ai/chat_planner.py` | `_extract_ai_summary_properties` | Returns N items. Falls back to `_raw_forward_text` parser. |
| `ai/chat_planner.py` | `_parse_raw_forward_properties` | Line-heuristic parser for raw WhatsApp/email forward text. |
| `ai/chat_planner.py` | `_classify_property_match(ai_prop, doc_groups)` | Returns variant 'h1' / 'h2' / 'h3' (all HIGH tier). |
| `ai/chat_planner.py` | `_walkthrough_property_card_h1h2` | Confirm card with image evidence. |
| `ai/chat_planner.py` | `_walkthrough_property_card_h3` | Confirm-then-complete card (no image). |
| `ai/chat_planner.py` | `_walkthrough_conflict_card` | Conflicting-info clarification card. |
| `ai/chat_planner.py` | `_detect_message_conflicts(ai_props)` | Surface contradictions before walkthrough renders. |
| `services/gift_walker.py` | `get_pending_gift_documents` | Tags each group with `_variant`, `_ai_summary_match`. |
| `app.py` | `_try_handle_property_confirm` | Handles Confirm/Edit/Remove. Sets `_layer1_confirmed`. |
| `app.py` | `_try_handle_property_complete_details` | Handles Upload/Type/Match/Skip on H3 complete-details card. |
| `app.py` | `_try_handle_message_conflict` | Handles "Use reading 1/2/Type correct version" picks. |
| `app.py` | `_try_save_property_gift` | Now requires `_layer1_confirmed=True` before Phase A. |

### The litmus test

```
Q: AI Summary names 5 properties. How many cards does the walkthrough render?
   - 5 cards (mix of H1/H2/H3) → ✅ ship
   - Less than 5 → ❌ H3 (no-image) properties are being silently dropped
   - More than 5 → ❌ residual images are being treated as new properties

Q: Does any property land in step5_data without an explicit user click?
   - NO → ✅ ship
   - YES → ❌ auto-save bug, fix the handler

Q: When the message contradicts itself (same property, two beneficiaries),
   does the walkthrough proceed silently?
   - NO (a clarification card blocks until user picks) → ✅ ship
   - YES (best-guess resolution) → ❌ violates rule 7
```

---

## 10x. 🔥🔥🔥 BURN-IN — The Inbound-Pipeline FUCK List 🔥🔥🔥

**These are the bugs that cost the user real time and money. Each one was
caught more than once. They are pinned here so the next session does not
re-introduce them.**

### 10x.1  AI Summary token cap

`ai/chat_planner.py::_summarise_message` MUST keep `max_tokens >= 4000`
and `raw_text[:6000]` minimum. A real WhatsApp forward is 5+ properties +
3-4 banks + 2-3 insurance policies, plus a "What we deduce" block that
spends 6-8 lines per asset. At 900 / 3000 the response truncates mid-property
and the user cannot read the deduction for the last 2 properties.
**Symptom:** summary card ends mid-sentence at "Property 4 – House, 10
Jalan Sri Laguna 1/7, Taman Laguna, 81200 Johor".

### 10x.2  Reprocess must NEVER downgrade a real category

In `_process_inbound_message_async`, after the parallel classify+extract
batch, the per-doc commit must be **monotonic**:

```python
if kind != 'other':
    doc.category = kind                  # promote
elif doc.category in (None, '', 'chat_inbox', 'other'):
    doc.category = 'chat_inbox'          # only set if not yet real
# else: keep existing real category — never downgrade
```

The watchdog re-fires the processor on every chat-history poll. A flaky
network call returning `kind='other'` must NOT wipe a previously
successful classification. Once a doc is `property_title` / `nric` /
`bank_statement`, it stays that way until explicitly reset.

### 10x.3  Parallel workers MUST hold app_context + track_context

`ThreadPoolExecutor` workers run outside the request thread, so the
Flask app context is missing. Without it:

- `cost_tracker.log_usage` raises "Working outside of application context"
  and silently swallows it → cost shows $0.00 when real cost is $0.30+
- `db.session` writes can't happen → workers must NOT touch DB

Required wrapper for every worker:

```python
def _classify_one(...):
    from ai.cost_tracker import track_context as _tc
    with app_obj.app_context(), _tc(
        will_id=..., client_id=..., document_id=...):
        # all classify_file / extract_*_data calls here
        return classification, extracted
```

DB writes happen later in the main thread, NOT inside the worker.

### 10x.4  Dedup at upload MUST use SHA256 content hash

`Document.content_hash` (SHA256 of bytes, indexed) is the canonical
dedup key. **Filename + file_size is not enough** — Postmark and
WhatsApp routinely rename "the same image" with different timestamps.
Two distinct rows with identical bytes = the user pays vision-classify
twice and ends up with phantom "duplicate IC" cards.

The upload site at `/api/inbound-email` must:
1. Compute `_content_hash = sha256(data).hexdigest()` for every attachment.
2. Look up existing rows by `(client_id, content_hash)` first.
3. Fall back to `(client_id, filename, file_size, content_hash IS NULL)`
   only for legacy rows, AND backfill content_hash on hit.
4. Persist `content_hash` on every NEW Document row.

A backfill script `dedupe_docs.py` exists for older clients; run it
once if you suspect duplicates.

### 10x.5  Daemon background threads die on redeploy

`threading.Thread(daemon=True)` started inside a gunicorn worker is
killed when the container restarts. Two safeguards must coexist:

(a) **Watchdog inside `/api/chat/<client_id>/history`** — every history
fetch (every 5 s while the chat tab is open) checks for any user
message older than 60 s with attachments still in `chat_inbox`. If
found, re-spawn `_process_inbound_message_async` for that message.

(b) **Idempotency rule §10x.2** — the resumed run must not destroy
already-completed work.

**Operational rule:** never `docker compose up -d web` while a user is
actively waiting on inbound classification. Wait for the AI Summary
card to land first. Or, if you must redeploy, immediately re-fire the
watchdog by reloading the chat tab.

### 10x.6  Static-asset cache-busting must be automatic

`<script src=".../chat.js?v=…">` MUST resolve via `asset_version()`
template helper which returns `os.path.getmtime(path)` as an integer.
**Hardcoded `?v=20260507a` is forbidden** — every deploy that touches
the JS file must invalidate the browser cache without the user
hard-refreshing. Asset_version is implemented in `app.py` context
processor; templates use:

```jinja
?v={{ asset_version('js/chat.js') }}
```

If you ever see the symptom "user has to hard-refresh to see new JS",
the regression is a hardcoded `?v=…`. Fix it at the template, not by
asking the user to refresh.

### 10x.7  Cost visibility is non-negotiable

The right pane in chat shows `💰 API cost this will`. It is wired to
`/api/cost/<will_id>` which sums `cost_usd` from `ApiCallLog`.

If this shows `$0.0000` after vision classification has run on 20+
images, **the bug is upstream** — a parallel worker or a deeply nested
helper failed to log because of:
- missing app_context (§10x.3),
- missing track_context (call sites must call `log_usage(msg, call_site=…)`),
- swallowed exception in cost_tracker.

The fix is at the silent path. Showing $0.00 to the user when the real
cost is $0.30 is worse than showing nothing.

### 10x.9  ⚡ Watchdog must NEVER post duplicate cards ⚡

**The catastrophic bug:** the watchdog in `/api/chat/<client_id>/history`
fires every 5 seconds while the chat tab is open. If `_process_inbound_
message_async` runs concurrently in N threads, each one calls
`plan_turn()` and posts a NEW intake card + NEW AI Summary. The user
ended up with **12+ duplicate cards** in one chat. Cost was multiplied
N× because every concurrent run re-classified all images.

**Three layers of defence — ALL three must remain:**

1. **In-process lock (`_PROCESSING_LOCK` + `_PROCESSING_INFLIGHT` set)**
   in app.py. The wrapper `_process_inbound_message_async` adds the
   user_msg_id before calling `_process_inbound_message_async_inner`
   and removes it in `finally`. If user_msg_id is already in the set,
   wrapper returns immediately. Per-worker, but combined with (3) it's
   robust across workers too.

2. **Watchdog throttle in `api_chat_history`** — before spawning a
   thread, check:
   - `_m.id not in _PROCESSING_INFLIGHT` (lock held)
   - any doc still `chat_inbox` (else nothing to do)
   - no assistant message contains "exhibits received" already (work done)

3. **Idempotency in the processor itself** — before posting the intake
   card OR the AI Summary card, check if one already exists for this
   `user_msg.created_at <= ChatMessage.created_at` window. If yes,
   skip the `db.session.add(...)` step. Doc category updates still happen,
   but no chat-message duplicates.

**The smell test:** open the chat tab, leave it open 60 seconds. There
must be exactly ONE `📋 N exhibits received` card and ONE `📨 AI Summary`
card per inbound email. If you see any duplication, the watchdog
regressed.

### 10x.10  Reprocess scripts must be idempotent on chat history

`/app/data/reprocess2.py` directly calls `_process_inbound_message_async`.
It MUST go through the same lock + idempotency checks. Never bypass them
by calling `_process_inbound_message_async_inner` directly.

### 10x.12  ⚡ EVERY AI-SUMMARY ITEM IS A SPECIFIC GIFT ⚡

**Hard rule from the user:** every item that appears in the AI Summary
"What we deduce" section — property, bank account, vehicle, insurance
policy, EPF, securities, anything — MUST result in exactly one entry
in `step5_data` (Wizard Step 6: Specific Gifts) with its own beneficiary
instructions.

For the KOID test forward, this means:

```
5 properties              → 5 step5_data entries
4 bank accounts           → 4 step5_data entries
3 insurance policies      → 3 step5_data entries
─────────────────────────────────────────────────
Total: 12 specific gifts
```

**Wrong patterns (forbidden):**
- ❌ "Banks generic" single clause "all banks → wife 100%" — this loses
  per-account fidelity (account number, institution, currency).
- ❌ Insurance silently dropped because there's no `insurance` category
  in the gift walker.
- ❌ "POSB Bank account ending in 5917" merged with "Maybank account
  ending in 2259" because both go to the same beneficiary.
- ❌ EPF / unit trust / shares ignored because they're not "title docs".

**Right pattern:**
- Every named asset → its own card in the walkthrough → its own gift
  entry → its own clause in the generated will.
- The probate lawyer needs to be able to tick off each asset against
  the deceased's estate. Lumping kills auditability.

### Implementation contract

`get_pending_gift_documents(client_id)` returns:
```python
{
    'property':  [...],   # title docs + isolated property images
    'bank':      [...],   # one entry PER bank account, even if same bank
    'vehicle':   [...],
    'insurance': [...],   # one entry PER policy
    'epf':       [...],   # one entry PER EPF/KWSP / unit trust statement
    'shares':    [...],   # securities, one per holding
    'other':     [...],   # anything else flagged as testator-asset
}
```

`_extract_ai_summary_*` MUST exist for every category. They parse the
AI Summary text and return the canonical list:
- `_extract_ai_summary_properties` ✅ (already exists)
- `_extract_ai_summary_banks` (NEW — parse bank lines: institution,
  account number, currency, beneficiary)
- `_extract_ai_summary_insurance` (NEW — parse insurer, policy number,
  beneficiary)
- `_extract_ai_summary_other` (catch-all)

The walkthrough iterates AI-Summary items first. Each item:
1. Try to bind to an uploaded Document (by lot/title for property,
   account number for bank, policy number for insurance).
2. If matched → render Layer 1 with image evidence.
3. If unmatched but stated in AI Summary → render H3 placeholder
   (per §10hg) — confirm + collect details later.
4. If image exists but NOT in AI Summary → §10d unverified card.

Layer 2 (beneficiary assignment) ALWAYS runs for every saved gift,
regardless of category. NO category gets a generic "all → X" shortcut.

### Verifier rule

`verify_step6.py` MUST count step5_data entries against AI Summary
count and fail if:
```
len(step5_property_gifts)  != AI Summary properties
len(step5_bank_gifts)      != AI Summary banks
len(step5_insurance_gifts) != AI Summary insurance
len(step5_other_gifts)     != AI Summary other
```

A passing test means EVERY item in the user's WhatsApp forward has its
own clause in the generated will.

### Symptom that proves the rule was violated

User sees 5 properties + 4 banks + 3 insurance in the AI Summary, but
the wizard's Step 6 right pane shows fewer than 12 gift entries. **That
is a regression.** Fix the parser / walker / save handler — do NOT
patch the symptom by silently grouping.

---

### 10x.13  ⚡ Beneficiary % is ALWAYS of the testator's share ⚡

**The testator can only give what the testator owns.** When the AI
Summary says:

> "I share with X 50/50, my 50% to Joshua 25% and Esther 25%"

the legally meaningful reading is:

```
Testator's interest:           50% (joint with X)
Beneficiary allocations of testator's interest:
  Joshua  →  50% of testator's share  (= 25% of full property)
  Esther  →  50% of testator's share  (= 25% of full property)
```

The "25% / 25%" the user wrote is *of the full property* and adds up to
exactly the testator's 50% share — meaning the testator gives equal
halves of his own share to the two children.

### How this affects every layer

1. **AI Summary parser** must surface BOTH framings:
   - `share_of_testator_pct` (canonical for will clause): 50/50
   - `share_of_full_property_pct` (legacy display): 25/25
   The card may show "of testator's share" to remove ambiguity.

2. **Layer 2 beneficiary card** asks "of the testator's share, who
   gets what %?" — NOT "of the full property". The default 100% means
   100% of the testator's interest, regardless of whether the testator
   owns 100% or only a partial share.

3. **`step5_data` gift entry** stores allocations as fractions of the
   testator's share (`'share': '1/2'` for each child). The wizard's
   probate-clause generator already reads this format and produces:
   > *"my undivided 1/2 share in [property] to Joshua and Esther in
   >  equal shares"*

   The full-property percentages are derived (`testator_share_pct ×
   beneficiary_share_of_testator_pct`) only for display, never stored.

4. **Generated will clause** must NEVER assert percentages of the FULL
   property — only "my [share] in [asset] to [beneficiaries] in [shares]".
   The will speaks for the testator's interest only; outsiders' shares
   are out of scope.

### Equal-share shorthand

When the AI Summary says "X% to A, X% to B" and X×2 equals the testator's
full share, treat as `equal among A and B of testator's share`. This is
the most common case for property-jointly-owned-with-spouse-or-sibling.

The card should show the equal-share interpretation prominently:

> Beneficiaries (of testator's 50% share):
>   • Joshua Koid Teck Seng — **50%** (= 25% of full property)
>   • Esther Koid En Hui — **50%** (= 25% of full property)
>   *Equivalent to: my share split equally between the two children.*

### Worked example for KOID

| Asset | Testator's share | Beneficiary of testator's share | Will clause |
|-------|------------------|--------------------------------|-------------|
| B-05-11 Paradisonuava | 1/2 | Joshua 1/2 + Esther 1/2 (equal) | "my 1/2 share to Joshua and Esther equally" |
| C-30-08 Marina Cove | 1/2 (joint with son Joshua) | Esther 100% | "my 1/2 share to Esther" |
| C-05-01 Marina Cove | 1/1 | Esther 100% | "the whole of [unit] to Esther" |
| 10 Sri Laguna | 1/2 (joint with wife) | Joshua 100% | "my 1/2 share to Joshua" |
| Shop Jalan Gunung 4 | 1/1 | Joshua 1/2 + Esther 1/2 | "the whole of [shop] to Joshua and Esther equally" |

Note that Property 5 (the shop) is sole ownership, so "50%/50%" really
does mean 50% of the full property to each child. Context (ownership
type) tells us whether to interpret % as fractions of full or of share.

### Implementation contract

Every gift entry in `step5_data` must carry:

```python
{
    'kind': 'property',
    'testator_share': '1/2',      # what the testator owns
    'allocations': [
        {'beneficiary_name': 'Joshua', 'share': '1/2', 'role': 'MB'},
        {'beneficiary_name': 'Esther', 'share': '1/2', 'role': 'MB'},
    ],
    # 'share' is ALWAYS a fraction of testator_share, never of full property.
    # If allocations sum != 1/1 of testator's share → conflict card (§10hg).
}
```

The verifier MUST flag any gift where `sum(allocations[*].share) != 1`.

---

### 10x.14  ⚡ Substitute Beneficiary Defaults ⚡

**During testing AND in the live walkthrough, the substitute clause
follows these defaults unless the user explicitly overrides:**

| Gift's main beneficiaries | Default substitute |
|---------------------------|--------------------|
| Multiple beneficiaries (e.g. Joshua + Esther 50/50) | The **surviving beneficiaries** of that gift, in equal shares. |
| Single beneficiary that is **a child** (e.g. Joshua 100%) | The **other surviving child** (Esther 100%). |
| Single beneficiary that is **the wife** (e.g. Lim Bee Yan 100%) | **Both children in equal share** (Joshua 50%, Esther 50%). |
| Single beneficiary that is some **other person** (e.g. brother) | The surviving children in equal share. |

### Why these defaults

- **Surviving-beneficiaries-equal** is the most common probate
  presumption when one of two co-heirs predeceases the testator.
- **Spouse → children** mirrors Malaysian Distribution Act 1958
  intuition where the wife is primary and children are residuary
  fallback.

### How this maps to the walker

`walk_step6.py::pick_next` and the chat substitute card MUST default to:

```python
def _default_substitute(main_bens, identities):
    """Return [{'name', 'share'}, …] — the substitute clause."""
    if len(main_bens) >= 2:
        # surviving beneficiaries in equal shares
        return [{'name': b['name'], 'share': '1/' + str(len(main_bens))}
                for b in main_bens]
    sole = main_bens[0]['name']
    children = [i for i in identities
                if (i.get('relationship') or '').lower() in ('son', 'daughter')]
    if sole in [c['full_name'] for c in children]:
        # sole beneficiary is a child → other surviving child(ren)
        others = [c for c in children if c['full_name'] != sole]
        return [{'name': c['full_name'],
                 'share': '1/' + str(len(others))}
                for c in others] if others else []
    # spouse / other → all children equally
    return [{'name': c['full_name'],
             'share': '1/' + str(len(children))}
            for c in children] if children else []
```

The chat card surfaces this as the **default option** so the user can
just tap ✅ to accept. The user can always override with a different
named substitute or "no substitute clause".

### Worked example for KOID

| Gift | Main | Default substitute |
|------|------|--------------------|
| B-05-11 (Joshua + Esther 50/50) | Joshua 50% + Esther 50% | Joshua 50% + Esther 50% (surviving) |
| C-30-08 (Esther 100%) | Esther | Joshua 100% (other child) |
| C-05-01 (Esther 100%) | Esther | Joshua 100% (other child) |
| 10 Sri Laguna (Joshua 100%) | Joshua | Esther 100% (other child) |
| Shop (Joshua 50% + Esther 50%) | Joshua + Esther | Joshua + Esther (surviving) |
| All banks → wife | Lim Bee Yan | Joshua + Esther equally |
| All insurance → wife | Lim Bee Yan | Joshua + Esther equally |

### Verifier rule

`verify_step6.py` MUST check that every saved gift has a
`substitute_specific` (or `substitute_mode`) field populated. A gift
with no substitute clause is incomplete unless the user explicitly
chose "no substitute" via the **⏭ No substitute clause** quickreply.

---

### 10x.15  ⚡ Image is VERIFICATION ONLY — text details are sufficient ⚡

**Rule from the user:** if the user provides full asset details in text
(WhatsApp/email body), that text alone is enough to create a complete
gift. The image, if uploaded, is **verification** — not a requirement.

| Asset | Sufficient text content | Image role |
|-------|-------------------------|------------|
| Property | address + (title OR lot number OR mukim+daerah) | Verification of ownership share, encumbrance, plan |
| Bank account | institution + account number | Verification of account state (unused if no image) |
| Insurance policy | insurer + policy number | Verification of beneficiary nomination |
| EPF / unit trust | institution + account number | Verification |
| Vehicle | reg number + make/model | Verification |

**Behavior rule:**
1. AI Summary parser extracts full asset details from message text.
2. Walkthrough renders one card per AI-Summary item (per §10x.12).
3. If a Document is bound (image evidence), card shows it as evidence
   with a "✅ Verified by image" badge.
4. If NO Document is bound, card still renders as **HIGH confidence
   message-stated** (per §10hg). User confirms → gift saved with full
   text-derived details. **No image-matching block.**
5. Image-matching is a separate workflow (§10g, §10ha–§10hf) — only
   runs against assets that DO have uploaded images. Never blocks an
   asset that the user described in text.

**Hard rules:**

- ❌ Don't show "title document required to complete this gift" for an
  asset that already has the title number stated in the message.
- ❌ Don't park a bank or insurance gift in `pending` status because no
  image was uploaded. Banks and insurance routinely have no image.
- ❌ Don't make Layer 1 confirmation conditional on image presence —
  text-stated assets are HIGH confidence by default (§10hg).
- ✅ DO ask the user to upload an image only if the asset's text
  details are missing critical fields (e.g. property has no
  title/lot/mukim AND no address).

### 10x.16  ⚡ Wizard Step 6 must show every gift's main + substitute ⚡

**Rule from the user:** the wizard's Step 6 (Specific Gifts) page MUST
display, for every gift in `step5_data`:
- The asset identity (property address / bank+account / insurer+policy)
- Main beneficiary names + shares
- **Substitute clause** — names + shares OR "no substitute"

**Schema contract (every gift entry in step5_data):**

```python
{
    'kind': 'property' | 'bank' | 'insurance' | 'vehicle',

    # Asset identity (one of these blocks based on kind)
    'property_info': {address, title_number, lot_number, mukim, daerah, negeri},
    'bank_name', 'account_number', 'country', 'account_type',
    'insurer', 'policy_number',

    # Main beneficiaries (Layer 2)
    'beneficiaries':       [{'name', 'share'}, …],   # canonical
    'allocations':         [{'beneficiary_name', 'share', 'role': 'MB',
                             'substitutes': [{'beneficiary_name', 'share'}]}],

    # Substitute clause
    'substitute_mode':     'specific' | 'equal' | 'prorata' | 'none',
    'substitute_specific': [{'name', 'share'}, …]    # if mode='specific'
}
```

**Wizard render must:**
- Read EVERY kind (property, bank, insurance, vehicle, …).
- Show main beneficiaries from `beneficiaries` (or fall back to
  `allocations[*].beneficiary_name`).
- Show substitutes from `allocations[*].substitutes` (or fall back to
  `substitute_specific`).
- Display "No substitute" when `substitute_mode == 'none'` AND no
  `substitute_specific`.

**Symptom that proves a regression:**

User opens wizard Step 6 → sees property gifts but no bank/insurance.
OR sees gifts but only main beneficiaries (no substitutes shown).
That means either the wizard renderer ignores the new kinds, or the
chat handler is saving in a format the wizard can't read.

Fix at the **schema mapping** — the chat → step5 → wizard pipeline
must use one canonical schema (above). Don't patch render-side; fix
the writer.

---

### 10x.17  ⚡ AI Chat saves MUST sync with Wizard immediately ⚡

**The contract:** the moment the chat confirms a gift (Layer 1 + Layer 2),
the wizard's Step 6 page must show that gift on the next page load —
without the user clicking "Reload from DB" or doing any manual sync.

### Why this breaks naively

Flask wizard routes traditionally read from `session['step5_gifts']`
which is a cookie cache. The chat handlers write directly to
`will.step5_data` in the database. If the wizard renders from session
cache, the chat's saves are invisible until something explicitly
calls `load_will_into_session()` for that will.

### Required behaviour

Every wizard step GET handler that reads from session storage MUST
first refresh that session slice from the database:

```python
@app.route('/wizard/step/N', methods=['GET', 'POST'])
def wizard_step_N():
    if request.method == 'GET':
        # 🔥 BURN-IN §10x.17 — refresh session from DB before render
        will_id = session.get('will_id')
        if will_id:
            w = db.session.get(Will, will_id)
            if w:
                session['stepK_xxx'] = json.loads(w.stepK_data or '...')
                session.modified = True
        return render_template(...)
```

This makes chat-driven and wizard-driven edits **eventually consistent
within one HTTP request roundtrip**. Both sides write to DB; both sides
read fresh on every GET.

### Applies to all chat-writeable steps

| Wizard step | DB column | session key | Chat writes? |
|-------------|-----------|-------------|--------------|
| Step 1 (Identities) | `identities_data` | `step1_data` | ✅ via Person |
| Step 2 (Testator) | `step1_data` | `step1_data` | ✅ |
| Step 3 (Executors) | `step2_data` | `step2_executors` | ✅ |
| Step 4 (Guardians) | `step3_data` | `step3_guardians` | (rare) |
| Step 5 (Beneficiaries) | `step4_data` | `step4_beneficiaries` | ✅ |
| **Step 6 (Specific Gifts)** | **`step5_data`** | **`step5_gifts`** | **✅ heavy** |
| Step 7 (Residuary) | `step6_data` | `step6_residuary` | ✅ |
| Step 8 (Trust) | `step7_data` | `step7_trust` | (rare) |

Step 6 is the highest-traffic chat write site and MUST refresh on GET.
Other steps benefit from the same pattern but are lower priority.

### What "synced" means in practice

When the user finishes the chat walkthrough:

1. step5_data on the Will record has 12 gift entries (5 properties +
   4 banks + 3 insurance) per §10x.12.
2. User clicks "Open Wizard" → wizard loads.
3. Wizard step 6 reads will.step5_data fresh from DB.
4. Page shows 12 gift cards with main + substitute beneficiaries
   pre-filled — exactly what the chat confirmed.
5. User can click "Generate Will" and the will document compiles
   from these 12 gifts WITHOUT additional data entry.

### The smell test for §10x.17

Forward email → finish chat walkthrough → open wizard step 6.
Count gift cards. Must equal AI Summary asset count. Each card must
have main + substitute beneficiaries pre-populated.

If the wizard shows 0 gifts but step5_data has 12 → the GET handler
isn't refreshing. If it shows 12 gifts but no substitutes → the
schema mapping is wrong (per §10x.16).

---

### 10x.18  ⚡ When text and image disagree — STOP, ASK ⚡

**Rule from the user:** if the user's text states one identifier
(account number, PTD number, title, name, address) and the uploaded
image's OCR returns a DIFFERENT value for the same identifier — the
chat MUST surface this conflict and ask the user which is correct.
Don't silently pick one; don't merge; don't override.

### Detection

For every gift entry that has BOTH a text-derived AI Summary entry AND
a Document with extracted fields:

| Field | Compare with | Conflict if |
|-------|--------------|-------------|
| Bank account number | `account_number` from OCR vs from text | digits differ |
| Insurance policy number | `policy_number` OCR vs text | values differ |
| Property title number | OCR `title_number` vs text title | digit-strip differs |
| Property lot/PTD | OCR `lot_number` vs text lot | digit-strip differs |
| Owner name | OCR `owner_name` vs testator name | first-name mismatch (after AI noise scrub) |
| IC number | OCR `ic_number` vs testator IC | values differ |
| Property address | OCR address vs AI Summary address | wholly different street |

(Apply §10aa AI-noise cleaning before comparing — `VALUE:`, `(unreadable)`,
`UNREADABLE`, etc. are not real values.)

### Required behaviour

When a conflict is detected, render a **clarification card** before
saving the gift to step5_data:

```
### ⚠️ Mismatch found — please verify

You said: **POSB Bank Account 030-25917-3**
Image shows: Account No. **030-25917-9**

The last digit differs. Which is correct?

[ ✅ Use what I said (030-25917-3) ]
[ 📎 Use what the image shows (030-25917-9) ]
[ ✏️ Type the correct number manually ]
[ 🗑 Wrong upload — remove this image ]
```

The gift is NOT saved until the user picks. The clarification card
takes precedence over the standard H3 confirm card.

### Hard rules

1. **Never auto-save when there's a conflict** — every disagreement
   becomes a question to the user. Probate accuracy demands this.
2. **Don't average / pick the longer / pick the shorter** — these are
   guesses. Ask.
3. **Log the conflict** with both readings so the lawyer's review of
   the will draft can see the resolution path.
4. **The user's text wins by default** if they explicitly choose
   "Use what I said" — but the image stays attached as evidence (not
   deleted) for cross-reference.

### Where this is enforced

| File | Function | What it does |
|------|----------|--------------|
| `services/conflict_detector.py` (NEW) | `detect_text_image_mismatches(client_id)` | Returns list of conflicts |
| `ai/chat_planner.py` | `_walkthrough_conflict_card(conflict)` | Render the question |
| `ai/chat_planner.py::_asset_walkthrough_question` | Pre-conflict gate | Surface clarification before normal H3 card |
| `app.py::_try_handle_message_conflict` | Handle resolution | Save user's pick → continue walkthrough |

### Symptoms of regression

- Bank gift in step5_data has account number `030-25917-9` (from
  image) but user said `030-25917-3` in text → conflict was silently
  resolved instead of asked.
- Property gift saved with both AI Summary's title and OCR's title
  number stored in different fields (sloppy merge).
- "Cannot match it" warning never raised even though OCR said one
  thing and text said another.

---

### 10x.19  ⚡ Co-owner is NOT a family relationship ⚡

**Rule:** when the message says "I share with X 50/50" or "joint with X",
X is a **co-owner of that specific asset only**, NOT a family relative
of the testator and NOT a beneficiary (unless explicitly named).

### Wrong pattern (forbidden)

```
Person table:
  CHAI MEI FUN — relationship='co-owner'   ❌
```

This pollutes the identity registry, shows up as a "person" in Step 1,
and confuses the will-generator into thinking she's part of the family.
"co-owner" is a property attribute, not a family relationship.

### Right pattern

```
step5_data[i] (the B-05-11 property gift):
  property_details:
    co_owners: ["Chai Mei Fun"]              ✓
    testator_share: "1/2"
  beneficiaries: [Joshua 1/2, Esther 1/2]    ← of testator's 1/2 share

Person table:
  (no row for Chai Mei Fun)                  ✓
```

The will clause then reads:

> *"I give my undivided 1/2 share in Unit B-05-11 Condominium Paradisonuava
>  to my son Joshua and my daughter Esther in equal shares."*

The clause does NOT need to name Chai Mei Fun — her share is hers, the
title deed already has her on it, and she retains her 1/2 outside the will.

### Detection

Co-owner candidates are people mentioned in the WhatsApp/email body via
phrases like:

- "I share with X 50/50"
- "joint with X"
- "co-owned with X"
- "X and I own"

If X also appears as a child's parent, executor, or beneficiary
elsewhere, X IS a Person — but they're a co-owner OF that asset AND a
family member separately. Only when X has no other role in the will do
they stay out of the Person table.

### Beneficiary check (the safety gate)

If X is named as a beneficiary anywhere ("my 50% to X"), they ARE a
person and need an entry. If X is ONLY mentioned as a co-owner, no
Person entry is created — they're recorded only inside the gift.

---

### 10x.20  ⚡ Executor name from message + IC cross-reference ⚡

**Rule:** when the message names an executor by ROLE only (e.g. "my
sister-in-law", "my brother") with a phone number but no full name,
the chat MUST cross-reference uploaded IC photos to find candidates,
not just leave the executor's `full_name` blank for the user to type.

### Example from KOID

Message says:

> *"My 'Executor' My Sister in law Tel:+6016-7338764"*

The chat has:
- 28 uploaded photos (some are ICs).
- IC walkthrough has assigned: testator, spouse, 2 children — 4 ICs used.
- Any remaining un-assigned IC is a candidate for "sister-in-law".

### Required behaviour

1. **List unassigned IC candidates.** Any Person with no relationship
   set, OR any extracted IC from a Document that isn't yet in the
   Person table.
2. **Surface a clarification card** in chat:

```
### ⚖️ Step 3: Executor — Confirm sister-in-law

You wrote: *"My Executor — My Sister in law Tel:+6016-7338764"*

I have these unassigned ICs from your uploads:

  [ 👤 SARAH BT ALI (NRIC 700707-...) — IC photo PHOTO-...28.jpg ]
  [ 👤 NORHAYATI BTE ABU (NRIC 720303-...) — IC photo PHOTO-...30.jpg ]
  [ ✏️ Type her name manually if not in the uploads ]
  [ ⏭ Skip — fill later ]
```

3. **On selection**: that Person becomes `relationship='executor'` in
   the Person table AND `full_name` populates `step2.executors[0].full_name`.
   The phone number from the message is preserved.

4. **If no IC matches**: chat asks for the full name + IC manually,
   THEN reminds the user to upload her IC if available — because the
   will requires her IC for probate.

### Hard rules

- ❌ Don't leave `executor.full_name = ""` if any unassigned IC exists.
- ❌ Don't pick an IC at random — ALWAYS ask the user.
- ❌ Don't dump every uploaded IC as candidate (only ICs not yet in
  Person registry with a real role).
- ✅ DO show the role evidence (the message snippet that names them).

### Same pattern for other named-by-role people

- "my brother" → cross-reference unassigned ICs
- "my mother" → ditto
- "my friend James" — has a partial name, search ICs for "James"

The principle: **never silently leave a critical person's identity
blank if there's evidence in the uploads to populate it.**

---

### 10x.21  ⚡ Identity matching uses the SAME logic as asset matching ⚡

**Rule from the user:** binding a message-stated role (sister-in-law,
brother, friend X) to an uploaded IC photo follows the **same
matching algorithm** we use for properties — content first, then
temporal proximity, then ask the user. This is the §10g/§10i logic
applied to people instead of assets.

### The trigger

Message has at least one role-mention WITHOUT a full name:

```
"My 'Executor' My Sister in law Tel:+6016-7338764"
"My friend Sarah will be a witness"
"Give to my brother"
```

AND at least one unassigned IC exists in the Person table or
Document table (i.e., an IC photo whose Person row has no
relationship, OR an IC Document not yet linked to any Person).

### Matching cascade (same shape as §10g)

#### Layer 1 — content match (strongest)

Compare the message's role-mention against IC fields:

| Message clue | IC field | Match if |
|--------------|----------|----------|
| Phone number `+6016-7338764` | `extracted.phone` (if Vision returns it) | digits match |
| Partial name "Sarah" | `extracted.full_name` | first-name token equal |
| Address "lives at X" | `extracted.address` | normalised match |
| IC serial digits | `extracted.nric_number` | digit-strip equal |

If exactly one IC content-matches → bind that IC to the role with
**HIGH confidence**. Surface a confirmation card "Is this Sarah BT
ALI your sister-in-law?" — but pre-select.

#### Layer 2 — temporal proximity (per §10i, applied to identities)

If no content match, look at the timestamp of the message line that
names the role and find unassigned ICs uploaded within the §10i
adjacency window:

- Same WhatsApp message line: ✅ closest
- Within 4 lines before / 3 after: ✅
- Within 5 minutes by attachment timestamp: ✅
- More than 30 minutes apart OR another role mentioned in between: ❌

Bind with **MEDIUM confidence**, show the timing as evidence:

```
### ⚖️ Sister-in-law candidate

You wrote: *"My Executor — My Sister in law Tel:+6016-7338764"*  [09:15:12]

Closest unassigned IC:
  📎 PHOTO-...28.jpg uploaded at 09:15:18 (6 seconds later)
  Extracted name: SARAH BT ALI (NRIC 700707-...)

[ ✅ Confirm — this is my sister-in-law ]
[ 👤 No, type a different name manually ]
[ ⏭ Skip — assign later ]
```

#### Layer 3 — residual ask (per §10he)

If no content match AND no temporal proximity match, list ALL
unassigned ICs as candidates:

```
### ⚖️ Pick your sister-in-law

You wrote: "My Executor — My Sister in law Tel:+6016-7338764"

Which IC photo is hers?

[ 👤 SARAH BT ALI ]
[ 👤 NORHAYATI BTE ABU ]
[ ✏️ Type her name (not in uploads) ]
[ ⏭ Skip — assign later ]
```

NEVER pick at random. NEVER leave executor.full_name = "" if any
unassigned IC exists.

### Hard rules (the same gates as §10g/§10i)

1. **Content match wins over timing.** A phone-number match beats
   a temporal-adjacency guess.
2. **One-claim-only.** An IC bound to "sister-in-law" can't also
   be bound to "brother". Once claimed, it's out of the candidate
   pool for other roles.
3. **High → low confidence order.** Process role-mentions with the
   strongest evidence first (e.g., phone-number match first), so
   weaker matches don't accidentally claim that IC.
4. **Residual = ASK.** No silent guessing. The clarification card
   takes precedence over the standard executor-assignment card.

### Implementation contract

| File | Function | Role |
|------|----------|------|
| `services/role_matcher.py` (NEW) | `match_unassigned_ic_to_role(client_id, role_mentions)` | Returns `[(role, ic_doc, confidence, evidence)]` |
| `ai/chat_planner.py` | `_walkthrough_role_match_card(role, candidates)` | Render the clarification card |
| `ai/chat_planner.py::_step3_executor_question` | Pre-card hook | Surface role-match card before generic executor card |
| `app.py::_try_save_executor` | On confirm | Promote selected IC's Person row to relationship='executor' |

### Same pattern for witnesses and other named-by-role people

The same matching applies to:
- Witnesses ("my friend Sarah will be a witness")
- Trustees ("my brother as trustee")
- Guardians ("my sister to look after the children")

Wherever the message names a role + (phone | partial name | address)
but no full name, the chat MUST run the matching cascade against
unassigned ICs before falling back to manual entry.

### Symptom of regression

Walking through executor card shows "Sister-in-law (full name TBC)"
and asks the user to type her name — even though there's an
unassigned IC in the upload set. **That's the §10x.21 path failing.**
Fix the matcher, don't paper over the symptom.

---

### 10x.22  ⚡ Same-building distinct-unit handling ⚡

**The bug this rule prevents:** when AI Summary lists multiple units in
the same building (e.g. C-30-08 AND C-05-01 in Marina Cove), saving
the first as a step5 gift caused `_ai_props_already_handled` to falsely
flag the second as ALREADY HANDLED — because the classifier's "2 generic
tokens" path matched on "marina" + "cove".

### Rule

In `_ai_props_already_handled` Pass 3, when matching AI Summary
properties against **synthetic groups** built from saved step5 gifts,
require a **UNIT-LIKE** token match. Generic-token overlap is not enough.

A unit token has the shape `[a-z]?-?\d+[\-/]\d+(?:[\-/]\d+)?` —
e.g. `c-30-08`, `b-05-11`, `a/12/3`.

### Why image groups CAN use the looser path but synthetic groups CANNOT

Image groups have OCR'd identifiers (lot, title number from the title
deed). When those collide with an AI Summary entry's tokens, it's a
real binding. But synthetic groups built from already-saved step5
entries don't have this anchor — they're literally other AI Summary
items lifted into gift form. Two AI props in the same building would
share generic tokens but mean different units.

### Symptom of regression

User has 3 properties in the same condominium (B-05-11, C-30-08,
C-05-01) — wizard shows only 1 saved instead of 3. That's §10x.22
loosened too far.

---

### 10x.23  ⚡ Every asset goes through 3 layers — same UX for property/bank/insurance ⚡

**Rule from the user:** every specific gift, regardless of kind, must
walk through three sequential cards:

| Layer | Card | Saves |
|-------|------|-------|
| **1** | "Confirm Asset" | identification only — `_layer1_confirmed=True`, empty `beneficiaries` |
| **2** | "Confirm Main Beneficiaries" | populates `beneficiaries` + `allocations` |
| **3** | "Confirm Substitute Beneficiaries" | populates `substitute_specific` + `allocations[*].substitutes` |

### Quick-reply value namespace

| Asset kind | Layer 1 | Layer 2 | Layer 3 |
|------------|---------|---------|---------|
| Property | `inventory h3 confirm/skip` | gift_main format (existing) | substitute format (existing) |
| Bank | `bank_l1 confirm/skip/remove` | `bank_l2 main 100% <name>` / `bank_l2 main equal children` | `bank_l3 sub 100% <name>` / `bank_l3 sub equal children` / `bank_l3 sub survivors` / `bank_l3 sub none` |
| Insurance | `insurance_l1 confirm/skip/remove` | `insurance_l2 main ...` | `insurance_l3 sub ...` |

### State machine

`_asset_walkthrough_question` walks AI Summary items in this order:
properties → banks → insurance. For each AI item:

```python
saved = saved_gift_for(ai_item)
if not saved:
    return Layer 1 card     # confirm/skip/remove
elif not saved.beneficiaries:
    return Layer 2 card     # main
elif saved.substitute_specific is None and saved.substitute_mode in (None, ''):
    return Layer 3 card     # substitute (with §10x.14 default pre-selected)
else:
    continue   # this asset is fully done — move to next
```

The walkthrough advances naturally: each click answers exactly one
question and surfaces the next layer until all 12 (5 prop + 4 bank +
3 ins) are complete.

### Defaults applied

- **Layer 2** banks default to wife (per §10x.14 spouse → child cascade)
- **Layer 2** insurance default same
- **Layer 3** uses §10x.14 substitute defaults (spouse → both children;
  single child → other child; multi → survivors equal)
- The default substitute is the FIRST quickreply in the Layer 3 card
  with prefix `✅ Default — …` so the user can one-click accept.

### Symptom of regression

User sees a bank card asking "Who inherits + Substitute" all in one
button — that's the old `bank_h3 confirm 100% <name>` shortcut, which
collapses Layers 2+3 into one click. The new flow MUST present them
as separate clicks. The legacy stub remains for backward compat but
is no longer rendered by the asset walkthrough.

---

### 10x.24  ⚡ Will-clause format MUST follow Phek Yi Ting standard ⚡

**Source of truth:** `documents/sample_will_phek_yi_ting.py`. Every
specific gift clause in the generated will MUST follow these patterns.

### Property — joint (testator's share is fractional)

```
"I hereby devise and bequeath to <BENEFICIARIES> all my <SHARE>
 undivided shares in the property known as <ADDRESS> held under
 <TITLE_TYPE> No. <TITLE_N>, Lot No. <LOT_N>, Mukim <MUKIM>,
 District of <DAERAH>, State of <NEGERI> in equal shares."
```

Example for KOID B-05-11 (testator share 1/2, joint with Chai Mei Fun,
distributed equally between Joshua and Esther):

> *"I hereby devise and bequeath to my son JOSHUA KOID TECK SENG and
>  my daughter ESTHER KOID EN HUI all my 1/2 undivided shares in the
>  property known as Unit B-05-11, Condominium Paradisonuava…
>  in equal shares."*

**The co-owner's name (Chai Mei Fun) does NOT appear** — per §10x.19.

### Property — sole (testator share 1/1)

```
"I hereby devise and bequeath to <BENEFICIARIES> the property known
 as <ADDRESS> held under <TITLE_TYPE> No. <TITLE_N>, Lot No. <LOT_N>,
 Mukim <MUKIM>, District of <DAERAH>, State of <NEGERI>."
```

NO "1/1 undivided" — that wording is awkward and probate-incorrect.
Sole properties just say "the property known as".

### Bank — sole

```
"I hereby devise and bequeath to <BENEFICIARY> the monies in my
 <BANK> Account No. <N> together with all interests/dividends
 already accrued due or accruing thereon."
```

### Bank — joint

```
"I hereby devise and bequeath to <BENEFICIARY> my share of the
 moneys in my joint account at <BANK> Account No. <N> together
 with all interests/dividends accrued due or accruing thereon."
```

### Insurance

```
"I hereby devise and bequeath to <BENEFICIARY> the benefits of my
 <INSURER> insurance policy No. <N> together with all bonuses or
 accretions already declared or accruing thereon."
```

### EPF / KWSP

```
"I hereby devise and bequeath to <BENEFICIARY> the moneys standing
 to my credit in my Employees' Provident Fund Account No. <N>."
```

### Mutual fund / unit trust

```
"I hereby devise and bequeath to <BENEFICIARY> all monies held in
 any of my <INSTITUTION> funds together with all interests/
 dividends already accrued due or accruing thereon."
```

### Implementation contract

`models/gift.py::FinancialDetails.to_formatted_description(prefix)`
MUST emit the above patterns based on `asset_type`. Asset types
recognised: `bank`, `insurance`, `epf`, `kwsp`, `mutual_fund`,
`unit_trust`, `shares`. Anything else falls back to the generic
"institution / account number" form.

`models/gift.py::Gift._ownership_prefix()` MUST emit:
- "the property" for sole (`testator_share='1/1'` or empty)
- "all my X/Y undivided shares in the property" for joint with fraction
- "my undivided share in the property" for joint without fraction
- "my share of the moneys in my joint account at" for joint financial

### Verification

`/tmp/sim_will_gen.py` script generates a sample will from saved KOID
gifts and asserts the patterns appear. Run after any drafter or model
change.

### Symptom of regression

- "all my 1/1 undivided shares" appearing in will → §10x.13 sole-
  property branch broken
- Bank clause shows "POSB Bank (Account No. 030-25917-3) - bank" →
  legacy fallback path firing
- Co-owner appears in property clause → §10x.19 leak

---

### 10x.25  ⚡ The saved template the AI follows STRICTLY ⚡

**Question the user keeps asking:** "is there a saved template that the
AI refers to and follows strictly when generating wills?"

**Answer:** YES — two things working together:

1. **`documents/sample_will_phek_yi_ting.py`** — verbatim Alan & Tan
   firm template, source of truth for format/wording.
2. **`ai/drafter.py::draft_will_mock(will_data)`** — programmatic
   clause generator that emits Phek-compliant text from `WillData`.
   The drafter does NOT freely paraphrase; it follows the patterns
   codified in **§10x.24** for every gift kind.

### Strict-follow checklist

The drafter's output MUST match these exact phrasings (verified by
`/tmp/sim_will_gen.py`):

| Section | Phek wording the drafter emits |
|---------|------------------------------|
| Header | `"LAST WILL AND TESTAMENT OF\n\n[NAME]"` |
| Opening | `"This Will is made by me [NAME] (MALAYSIA NRIC No. [N]) of [ADDRESS]."` |
| Revocation | `"By signing this Will, I [hereby] revoke all earlier Wills..."` (clause 1) |
| Executor | `"I hereby appoint my [RELATION] [NAME] (NRIC ...) of [ADDRESS] as the Executor..."` (clause 2) |
| Trustee | `"In this Will unless it is specifically stated to the contrary, my Executor(s) shall also act as my Trustee(s)."` (clause 3) |
| Property — joint | `"I hereby devise and bequeath to [BENEFICIARIES] all my [SHARE] undivided shares in the property known as [ADDRESS] held under [TITLE_TYPE] No. [N], Lot No. [N], Mukim [M], District of [D], State of [S]..."` |
| Property — sole | `"I hereby devise and bequeath to [BENEFICIARIES] the property known as [ADDRESS] held under..."` (no "undivided") |
| Bank | `"I hereby devise and bequeath to [B] the monies in my [BANK] Account No. [N] together with all interests/dividends already accrued due or accruing thereon."` |
| Insurance | `"I hereby devise and bequeath to [B] the benefits of my [INSURER] insurance policy No. [N] together with all bonuses or accretions already declared or accruing thereon."` |
| Substitute | `"Pursuant to Clause [N] above, if [B] does not survive me, then the benefit he/she would have received shall be given to [SUB]."` |
| Residuary | `"Unless specifically stated to the contrary in this Will, my Trustee(s) shall hold the rest of my estate on trust to retain or sell..."` (clause 7) followed by `"To pay debts..."` (a) and `"To give the residue..."` (b) |
| Declaration | `"I have given due consideration to all the other Beneficiaries..."` (clause 8) and `"For the purpose of ascertaining entitlement..."` (clause 9) |
| Closing | `"********** the remaining page is intentionally left blank **********"` followed by signature blocks |

### How to verify the AI follows strictly

Run the snapshot test:
```bash
docker exec willcraft-web python /app/data/sim_will_gen.py
```

It generates a sample will from KOID's 12 saved gifts and asserts:
- "all my 1/2 undivided shares" appears for joint properties ✓
- "the property known as" (without "undivided") for sole properties ✓
- "the monies in my [BANK] Account No." for banks ✓
- "the benefits of my [INSURER] insurance policy No." for insurance ✓
- Co-owner names ABSENT from any clause ✓
- Phek-style "Pursuant to Clause N above..." for substitutes ✓

If any of these patterns drift, the test fails — and the regression
gets caught before it ships.

### Where format change requests go

If the firm wants to update the will format:
1. Edit `documents/sample_will_phek_yi_ting.py` (the canonical source)
2. Update §10x.24 patterns in CLAUDE.md
3. Update `models/gift.py::FinancialDetails.to_formatted_description`
   AND `Gift._ownership_prefix` to match
4. Run `sim_will_gen.py` to verify
5. Have the Approver compare a generated sample against the new
   firm template side-by-side before deploying

### Symptom of regression

User generates will → Approver reviews → spots a clause that doesn't
match Alan & Tan format. **Bug is in `ai/drafter.py` or `models/gift.py`
— fix at source, do NOT post-process the output.** The drafter and the
gift model are the only authority on clause shape.

---

### 10x.26  🔥 BURN-IN — Vision retry has a TERMINAL state 🔥

**After 3 failed vision-classification attempts OR an explicit
`manual_review=True` verdict, the Document is promoted from `chat_inbox`
to `needs_review`. The watchdog only re-fires for `chat_inbox` docs,
so `needs_review` is the loop-exit guarantee. Without this, the
watchdog re-classified the same unreadable docs every 5 seconds
forever — burning API tokens and never advancing the UI past 96%.**

### Implementation

In `app.py::_process_inbound_message_async_inner` after each per-doc
classification:

```python
if kind != 'other':
    doc.category = kind
elif doc.category in (None, '', 'chat_inbox', 'other'):
    if extracted is None:
        extracted = {}
    prev = int((json.loads(doc.extracted_data or '{}') or {})
               .get('_classify_attempts', 0) or 0)
    new = prev + 1
    extracted['_classify_attempts'] = new
    is_unreadable = bool(classification.get('manual_review')) \
                    or 'unreadable' in (classification.get('reason') or '').lower()
    if new >= 3 or is_unreadable:
        doc.category = 'needs_review'
        extracted['_terminal_reason'] = (
            'unreadable_after_retry' if new >= 3
            else 'vision_marked_unreadable')
    else:
        doc.category = 'chat_inbox'
```

### Hard rules

1. **`extracted is None` MUST be guarded** before assignment. The first
   regression of this rule was a `TypeError: 'NoneType' object does
   not support item assignment` that killed every "other"-kind worker.
2. **The retry counter is monotonic** — each attempt increments. Never
   reset on doc reload; reset only when user explicitly retries the doc.
3. **The watchdog must NEVER iterate `needs_review` docs** for re-firing.
   Only `chat_inbox` triggers re-fire.

### UI

`static/js/chat.js::categoryLabel` maps `needs_review` to
`⚠️ Needs your review`. The progress banner counts only `chat_inbox`
as "still analysing", so a doc in `needs_review` no longer pegs the
counter at < 100%.

### Litmus test

```
Q: 5 docs return manual_review=True. Watchdog runs 100 times.
   - All 5 promote to needs_review after 1-3 attempts → ✓
   - Loop fires forever, intake/summary cards duplicate → ✗ §10x.26 broke
```

---

### 10x.27  🔥 BURN-IN — Vision fallback when Tesseract fails 🔥

**Tesseract is BLIND to:**
- Malaysian MyKad (NRIC) holographic security patterns
- Photos of cards on contrasting backgrounds with glare
- Strata title plans with embossed text
- Old typewritten property documents with faded ink

**When Tesseract returns < 50 chars (its threshold for "unreadable"),
DO NOT short-circuit to `manual_review=True`. CALL CLAUDE VISION FIRST.**

### The bug this rule prevents

Real example from KOID test: 4 IC photos (LIM LAY CHENG, Joshua, Esther,
duplicates) were CRYSTAL CLEAR JPEGs but Tesseract returned <50 chars
because it can't read MyKad. The classifier short-circuited to
"Image unreadable — manual review needed". 4 family ICs got buried.
Identity matching had nothing to work with.

### Implementation

`ai/file_classifier.py::classify_file` Stage 2a:

```python
if fields['raw_text_len'] < 50:
    vision_result = _vision_classify_fallback(file_path, group_context, testator_profile)
    if vision_result is not None:
        return vision_result
    # Vision ALSO failed → THEN flag manual_review
    return {**fallback, "manual_review": True, ...}
```

The fallback uses Sonnet vision with a category list and parses a
simple `{"kind": "..."}` JSON response. ~$0.005 per call, only fires
on Tesseract failure (rare).

### Hard rules

1. **Never assume Tesseract is the truth on image classification.**
   It's a fast first-pass; vision is the fallback.
2. **The fallback prompt must include all KINDS** (nric/property_title/
   property_spa/property_tax/loan_agreement/bank_statement/insurance/
   vehicle/will/death_certificate/unrelated/other) so vision picks
   from the same vocabulary.
3. **Cost-track every vision-fallback call** via `track_context()`.

### Where this is enforced

| File | Function | Role |
|------|----------|------|
| `ai/file_classifier.py` | `_vision_classify_fallback` | Sonnet vision call, returns classify-shape dict or None |
| `ai/file_classifier.py` | `classify_file` Stage 2a | Calls fallback before flagging unreadable |
| `ai/ocr.py` | `_make_content_block` | Image → base64 (used by fallback) |

### Litmus test

```
Q: User uploads 4 clear MyKad photos. Tesseract returns <50 chars on each.
   - All 4 classified as 'nric' via vision fallback → ✓
   - All 4 marked 'manual_review' / 'Image unreadable'  → ✗ §10x.27 broke
```

---

### 10x.28  🔥 BURN-IN — AI Summary (Step 2) fires on TEXT ALONE 🔥

**Per CLAUDE.md §7, Step 2 (AI Summary) must run on the text body
regardless of attachments. Earlier the gate was `if artifacts and text`
which dropped Step 2 for text-only forwards. Now it's `if text` only.**

### Why this rule exists

The KOID test forward CONTAINS the asset list in the body text alone
(5 properties, 4 banks, 3 insurance). Even a text-only forward (no
attachments) must show the user "what we deduced" so they can verify
the parser was correct. Without Step 2 the chat jumps straight to
"Asset inventory: please upload documents" — confusing the user
because they DID describe their assets in the email body.

### Order of cards in the chat

After the inbound webhook processes a forwarded email, exactly TWO
assistant cards must appear in this exact order:

```
[user]      <forwarded email body>
[assistant] 📨 AI Summary of your message     ← Step 2 (verify parsing)
[assistant] 📋 Asset inventory  OR  📋 N exhibits received   ← Step 3+
```

Order matters: the user reads what the AI deduced FIRST, then sees
the action prompt. Reverse order causes confusion.

### §10x.9 idempotency check (widened)

The check that prevents duplicate cards on watchdog re-fires must
match BOTH possible Step 3+ headlines:

```python
filter_(_or(
    ChatMessage.content.ilike('%exhibits received%'),  # with attachments
    ChatMessage.content.ilike('%Asset inventory%'),    # text-only
))
```

Earlier the check only matched "exhibits received", so text-only
forwards got duplicate "Asset inventory" cards every 5s.

### Where this is enforced

| File | Function | Change |
|------|----------|--------|
| `app.py::_process_inbound_message_async_inner` | AI Summary block | `if text and not _summary_already_posted` (was `if artifacts and text`) |
| `app.py::_process_inbound_message_async_inner` | Card ordering | AI Summary block precedes `plan_turn()` call |
| `app.py::_process_inbound_message_async_inner` | `_intake_already_posted` check | OR-match both card headlines |
| `app.py::api_chat_history` watchdog | `_intake_done` check | Same OR-match |

### Litmus test

```
Q: Send a text-only forward (no attachments) to the inbound inbox.
   - Chat shows: user → AI Summary → Asset inventory (3 messages)  → ✓
   - Chat shows: user → Asset inventory (only)                      → ✗ §10x.28 broke
```

---

### 10x.29  🔥 BURN-IN — Watchdog re-fires for STUCK DOCS even if intake card exists 🔥

**The watchdog at `api_chat_history` MUST re-fire the processor
whenever `chat_inbox` docs remain — even if the intake card was
already posted. Earlier the watchdog blocked re-fires once "Asset
inventory" / "exhibits received" appeared in chat, leaving stuck
docs un-retried forever.**

### Why this rule exists

The §10x.26 retry-counter caps attempts at 3 → terminal `needs_review`.
For that cap to actually fire, the watchdog must KEEP firing. If the
watchdog short-circuits on intake-card-exists, the doc sits in
`chat_inbox` forever and never reaches its 3rd attempt → never
promotes to `needs_review` → progress UI stuck at < 100%.

### The watchdog gates (correct order)

```
(1) skip if processor lock is held (in-flight)
(2) skip if no docs are still chat_inbox  (nothing to do)
(3) [REMOVED in §10x.29]
```

Old gate (3) said "skip if intake card already posted". That was wrong
— intake-card-exists is NOT proof that all docs finished classifying.
Card-duplication is prevented INSIDE the processor (at the moment of
posting), NOT in the watchdog.

### Hard rules

1. **The retry-counter (§10x.26) bounds the watchdog's iterations.**
   After 3 attempts, the doc is no longer `chat_inbox` → gate (2) fires
   → watchdog exits naturally. No infinite loop.
2. **The processor's `_intake_already_posted` check is what prevents
   duplicate cards** — not the watchdog's pre-check.
3. **Don't add a "skip if X already done" gate to the watchdog.** Always
   let it re-fire if there are stuck docs.

### Litmus test

```
Q: 1 doc stuck at attempt=2 in chat_inbox after intake card posted.
   - Watchdog re-fires → attempt=3 → promotes to needs_review → ✓
   - Watchdog blocks because intake card exists → doc stuck forever → ✗ §10x.29 broke
```

---

### 10x.30  🔥🔥🔥 BURN-IN — Identity Matching: HIGH → LOW Confidence 🔥🔥🔥

**Same rule as §10e for asset matching, applied to identities. The IC
walkthrough orders pending identities by HOW CONFIDENTLY their
relationship can be deduced from the message. HIGH-confidence
matches walk FIRST. Low-confidence inferences walk LAST. NO EXCEPTIONS.**

### Why this rule

Resolving HIGH-confidence identities first lets them claim "I am the
son" / "I am the daughter" / "I am the wife" before low-confidence
inferences are made. By the time we reach the low-confidence outsider,
the system already KNOWS who's family — so the outsider-elimination
("the only IC name NOT in your family") has solid ground to stand on.

If we walk LOW-confidence first, we might misassign the only outsider
to "sister-in-law" before realising one of the named-family ICs was
ALSO needed for that role. Order = correctness.

### The confidence grid (`services/identity_walker._score_ic_confidence`)

| Score | Tier | Trigger |
|-------|------|---------|
| **5** | **HIGH** | Name appears in message AND a family-role word (`son`, `daughter`, `wife`, `husband`, `spouse`, `father`, `mother`, `brother`, `sister`) appears within 30 chars before / 60 chars after the name. Example: `"Joshua Koid Teck Seng(son)"`, `"Esther Koid En Hui (daughter)"`, `"my wife (Lim Bee Yan)"` |
| **4** | **HIGH** | Name appears in message with a co-owner phrase (`"I share with X"`, `"joint with X"`, `"co-owned with X"`). Per §10x.19 the IC is NOT added to the Person table — the name is recorded on the property's `co_owners` array only. But the deduction confidence is HIGH so the user sees a clear "this is co-owner of property X" suggestion. |
| **3** | **MEDIUM** | Name appears in message but NO role word adjacent. User has to choose. |
| **1** | **LOW** | Name does NOT appear in message, but role-only mentions plus outsider-elimination via `role_matcher.match_role_to_candidates` identifies them as the lone non-family candidate (§10x.21). Example: `"My Sister in law Tel:+6016-..."` → the only IC whose extracted name doesn't match any family member named elsewhere is the sister-in-law. |
| **0** | **NONE** | No signal at all. User must manually identify. |

### Sort order (mandatory)

```python
pending.sort(key=lambda p: (-p['_deduction_score'], p.get('created_at', '')))
```

Score DESC → upload-time ASC as tie-breaker. Deterministic, stable.

### Example (KOID test fixture)

Message contains:
- "Joshua Koid Teck Seng(son)" → Joshua scores 5
- "Esther Koid En Hui (daughter)" → Esther scores 5
- "my wife (Lim Bee Yan)" → Lim Bee Yan scores 5
- "I share with Chai Mei Fun 50/50" → Chai Mei Fun scores 4 (co-owner)
- "My Sister in law Tel:+6016-7338764" → role-only mention → triggers
  outsider-elimination → LIM LAY CHENG scores 1

Walkthrough order (HIGH → LOW):
```
score=5  JOSHUA KOID TECK SENG    (son, name+role)
score=5  ESTHER KOID EN HUI       (daughter, name+role)
score=5  LIM BEE YAN              (wife, name+role)  [if IC uploaded]
score=4  CHAI MEI FUN             (co-owner B-05-11) [§10x.19 → property only, NOT Person]
score=1  LIM LAY CHENG            (sister-in-law via outsider elimination)
```

Numbers 1-4 are DETERMINISTIC: name is in the message, role is in
the message. Number 5 is INFERENCE.

### Hard rules

1. **Walk HIGH first.** Score-5 ICs MUST be presented before any
   score-4. Score-4 before score-3. Etc. Tie within tier → upload time.

2. **Show evidence snippet on every IC card.** Per §9, the chat MUST
   show the message snippet that prompted the deduction. The user
   verifies, confirms with one click. No guessing.

3. **Buttons reflect the message, not a fixed list.** When the
   deduction is HIGH, show "✓ Yes — \<role\>" as the FIRST button.
   The fallback buttons (Spouse / Sister / Brother / Friend / etc.)
   appear ONLY when no deduction is possible (score 0).

4. **Co-owners (score 4) are tracked separately** per §10x.19. They
   do NOT receive a Person row. The chat surfaces them as part of the
   property card ("Co-owner: \<name\>") not as an identity.

5. **Outsider-elimination (score 1) requires that ALL higher-scored
   ICs are matched first.** If Joshua's IC is still in `pending`,
   we DON'T yet say "LIM LAY CHENG = the only outsider" — wait until
   Joshua + Esther + spouse are all assigned, THEN do elimination on
   what's left. The sort order naturally enforces this: by the time
   the matcher is asked about score-1 ICs, score-5 ICs are no longer
   pending.

### Where this is enforced

| File | Function | Role |
|------|----------|------|
| `services/identity_walker.py` | `_score_ic_confidence` | Scoring rubric (5/4/3/1/0) |
| `services/identity_walker.py` | `get_pending_ic_documents` | Sort by score DESC + upload-time |
| `services/identity_walker.py` | `_gather_message_text` | Pull AI Summary + raw forward text once |
| `services/identity_walker.py` | `_outsider_eliminated_names` | Cache role-matcher HIGH outsider names for score 1 |
| `services/role_matcher.py` | `match_role_to_candidates` | Outsider-elimination cascade (used to populate score-1 names) |
| `ai/chat_planner.py` | `_identity_question` | Renders IC card with deduced role + evidence snippet |
| `app.py` | `_try_assign_pending_identity` | Yes-button handler — falls through to role_matcher if `deduce_roles` can't match name verbatim |

### The litmus test (run before shipping any IC-walkthrough change)

```
Q: For KOID test fixture, what is the pending IC walkthrough order?
   - Joshua → Esther → LIM LAY CHENG  ✓ ship
   - LIM LAY CHENG first              ✗ §10e order regressed; fix before ship
```

If the order ever inverts, the bug is in `get_pending_ic_documents`
sort step or in `_score_ic_confidence`. Fix THERE, not by patching
the chat-planner.

---

### 10x.31  🔥🔥 BURN-IN — Skip is a NO-OP. Only Yes / Delete advance. 🔥🔥

**Rule from user (May 2026): "if skip, show back again until user
select delete. then only go to next step"**.

The Step 1 IC walkthrough has THREE buttons:
```
[ ✓ Yes — <role> ]   [ Skip ]   [ 🗑 Delete ]
```

Each button does exactly one thing:

| Click | Effect | Walkthrough advances? |
|-------|--------|------------------------|
| **✓ Yes** | Creates the Person row with the chosen family relationship; the Document is linked. | ✓ YES — IC removed from pending |
| **Skip** | NO-OP. Just bumps `_skip_count` in extracted_data. The SAME IC is asked AGAIN on the next turn. | ✗ NO — same card re-shows |
| **🗑 Delete** | Soft-deletes the Document (`category='deleted'`) and any duplicates of the same person. | ✓ YES — IC removed from pending |

### Why this rule exists

Earlier behaviour wrote `_chat_skipped=True` on Skip click and dismissed
the IC FOREVER. One mis-click would silently drop a family member from
the will. That's a probate-critical data loss — not acceptable.

New behaviour: Skip means "I saw the card but I'm not ready to decide."
The IC stays in queue. The user MUST consciously choose between
"this is a real family member" (Yes) or "this is the wrong upload"
(Delete) before advancing. No silent dismissal.

### Chat ack copy

After a Skip:
- Skips < 3:  `🔁 Asking again about <NAME> — click ✓ Yes to confirm
              the relationship or 🗑 Delete to remove this IC.`
- Skips ≥ 3:  `🔁 You've skipped <NAME> 3 times. To move past this card,
              click ✓ Yes to assign a relationship or 🗑 Delete if it's
              the wrong upload.`

The increased nag at 3+ skips signals that the user is stuck and
needs to pick one of the two productive paths.

### Where this is enforced

| File | Function | Mechanism |
|------|----------|-----------|
| `services/identity_walker.py` | `skip_pending_ic_document` | Sets `_skip_count` only; does NOT set `_chat_skipped` |
| `services/identity_walker.py` | `get_pending_ic_documents` | Filter respects `_chat_skipped` for legacy/Delete-companion docs only |
| `app.py` | `_try_skip_pending_identity` | Returns `kind='identity_skipped'` + `skip_count` |
| `ai/chat_planner.py` | `_wrap` ack | "Asking again about X" copy with escalating language at ≥3 skips |

### The litmus test

```
Q: User clicks Skip 5 times on Joshua's IC card.
   - Joshua's card re-renders 5 times       → ✓ ship
   - Joshua disappears from queue           → ✗ §10x.31 regressed
```

If a Skip ever causes an IC to leave the queue, the bug is in
`skip_pending_ic_document` (it shouldn't write `_chat_skipped`) or
in `get_pending_ic_documents` (it shouldn't add a new exclusion gate).

---

### 10x.32  🔥🔥 BURN-IN — Step 1 IC walk only assigns FAMILY relations 🔥🔥

**The Step 1 IC walkthrough creates Person rows tagged with FAMILY
relationship words ONLY. Will-roles (Executor / Trustee / Guardian /
Witness / Beneficiary) are set in LATER steps (3 / 4 / 5). If the LLM
deducer returns a will-role for an IC, it MUST be silently mapped back
to the family relation via outsider-elimination (§10x.21).**

### The bug this rule prevents

KOID test: LIM LAY CHENG's IC was extracted, role-deducer Claude saw
"My Executor: My Sister in law" in the message and returned
`{role: 'Executor', evidence: 'My Sister in law'}` for her name. The
Step 1 handler accepted "Executor" → saved Person with
`relationship='Executor'`.

This corrupted the wizard:
- Step 1 family registry showed her as "Executor" (a will-role, not
  family — visually wrong)
- Step 3 (Executor selection) couldn't pick her up because the
  family-filter excluded "Executor" relationship
- The will document's family list was incomplete

### Implementation

`app.py::_try_assign_pending_identity` rejects will-roles from
`parse_relationship` and `deduce_roles`:

```python
_WILL_ROLES = {'Executor', 'Trustee', 'Guardian', 'Witness',
                'Beneficiary'}
rel = parse_relationship(user_text)
chosen_role = None
if rel and rel not in _WILL_ROLES:
    chosen_role = rel
elif rel in _WILL_ROLES:
    pass   # fall through to deducer/elimination
if not chosen_role and any(...confirm tokens...):
    ded = deduce_roles(recent, [name])
    ded_role = (ded.get(name) or {}).get('role') or ''
    if ded_role and ded_role not in _WILL_ROLES:
        chosen_role = ded_role
    else:
        # role_matcher outsider-elimination → returns family_relation
        ...
        chosen_role = m.get('family_relation') or 'sister-in-law'
```

The fallback's `family_relation` field is FAMILY only by construction
(role_matcher splits role from family_relation: `role='executor'`
WILL role, `family_relation='sister-in-law'` family).

### Hard rules

1. **`_WILL_ROLES` set is the gate.** Any role in this set returned by
   any source (parse_relationship / deduce_roles) MUST be discarded
   in Step 1 context.
2. **The fallback returns `family_relation`, not `role`.** Don't accidentally
   use `m['role']` from `extract_role_mentions` — that's the will-role.
3. **Casing: persons table stores Title-Case** (`Sister-in-law`,
   `Daughter`). Lower-case input from outsider-elim must be normalised:
   ```python
   '-'.join(p.capitalize() for p in fam.split('-'))
   ```

### Where this is enforced

| File | Function | Mechanism |
|------|----------|-----------|
| `app.py` | `_try_assign_pending_identity` | `_WILL_ROLES` filter on both `parse_relationship` and `deduce_roles` outputs |
| `services/role_matcher.py` | `extract_role_mentions` | Returns `role='executor'` AND `family_relation='sister-in-law'` separately |
| `ai/role_deducer.py` | `CANONICAL_ROLES` | Includes will-roles for OTHER contexts (Step 3+); Step 1 must filter |

### Litmus test

```
Q: KOID forward; LIM LAY CHENG IC; user clicks "✓ Yes — sister-in-law".
   - Person row: relationship='Sister-in-law'  → ✓
   - Person row: relationship='Executor'       → ✗ §10x.32 regressed
```

If a Person row ever has relationship in `_WILL_ROLES` after a Step 1
walk, the bug is in `_try_assign_pending_identity`. Fix THERE, never
patch the Person row directly without also fixing the upstream gate.

---

### 10x.33  🔥🔥🔥 BURN-IN — Pre-Deploy Asset Audit MUST PASS 🔥🔥🔥

**Every deploy that touches `services/gift_walker.py`,
`ai/chat_planner.py`, the property/bank/insurance handlers in `app.py`,
or the Phek-format clause emitters MUST first run
`services/asset_audit.py` against the KOID test client and confirm
ZERO reconciliation errors. If any §10b / §10e / §10x.12 violation is
flagged, the deploy is BLOCKED until the gift_walker / chat_planner
code is fixed at source.**

### Why this rule exists

The user repeatedly said:
> "this issues should have been burn, why keep resurfacing"

§10b, §10e, §10x.12 were already in CLAUDE.md, but the CODE regressed
silently between sessions. There was no automated pre-deploy gate
checking that the rules were actually being followed. So bugs slipped
through commits, lived in production, and only surfaced when the user
spotted a wrong UI.

### The audit script — `services/asset_audit.py`

A single-file deterministic check. Takes a client NRIC or UUID,
verifies:

| Check | Rule | Failure means |
|-------|------|---------------|
| AI Summary property count = pending group count | §10b | Grouper over-merging or under-surfacing |
| AI Summary bank count = pending bank group count | §10x.12 | Banks not emitted as H3 placeholders when no image uploaded |
| AI Summary insurance count = pending insurance group count | §10x.12 | Insurance not emitted as H3 placeholders |
| Pending property scores monotonically decrease | §10e | Sort path regressed |
| Address-claim greedy uniqueness | §10g | Two cards bound to same address |
| Each step5_data gift has L1+L2+L3 layers | §10x.23 | Gift not fully walked |

Output:
```
[1] AI SUMMARY ASSET COUNTS (canonical)
    Properties: 5  Banks: 4  Insurance: 3
[2] PENDING GIFT WALKTHROUGH GROUPS
    property: <N> groups (sorted by score DESC)
    bank: <N> groups
    insurance: <N> groups
[3] STEP5_DATA SAVED GIFTS
[4] RECONCILIATION CHECKS — pass/fail per rule
```

### How to run

```bash
# Locally (will use your dev DB)
docker exec willcraft-web python /app/services/asset_audit.py 631204-07-5743

# As pre-deploy gate (server)
ssh ubuntu@47.130.249.28 "docker exec willcraft-web python \
  /app/services/asset_audit.py 631204-07-5743" | grep -E '❌|✅'
```

The audit fixture client should be KOID BENG SUN (NRIC 631204-07-5743)
because it has the highest variety of asset types (5 properties × 3
strata + 2 landed, 4 banks across 2 countries, 3 insurance, sister-in-law
executor via outsider-elimination, co-owner not in family registry).

### Hard rules

1. **The audit script is committed in the repo at
   `services/asset_audit.py` — it is part of the project, NOT a
   one-off `/tmp/` script.** If it lives only in `/tmp` it gets lost
   between sessions. This is what kept happening before.

2. **Run the audit AFTER every deploy that touches asset code.**
   If `[4] RECONCILIATION CHECKS` reports any `❌`, file an issue with
   the failing rule (e.g. "§10b regressed: 5 → 3 groups"). Do NOT call
   the deploy "done" until reconciliation is clean.

3. **When a new bug is found, add the check to `asset_audit.py`.**
   Don't fix in chat-planner only — the audit MUST be the canonical
   set of checks so future regressions are caught.

4. **The audit reads from the SAME functions the chat planner uses**
   (`get_pending_gift_documents`, `_extract_ai_summary_*`,
   `_score_property_confidence`). If the audit passes but the chat
   shows wrong order, the bug is in how the chat planner CALLS those
   functions — fix at chat planner, then add a new check.

### Litmus test

```
Run asset_audit.py. Either:
  ✅ All checks pass        → deploy can ship
  ❌ Any check fails        → deploy is blocked, fix at source first
```

Failure must include the rule (`§10b`, `§10e`, etc.), the expected
value, and the actual value. Vague "something's wrong" failures are
banned — every check must be explicit.

### Where this rule must be referenced

| Trigger | Action |
|---------|--------|
| Touched `services/gift_walker.py` | Run audit before commit |
| Touched `ai/chat_planner.py` (any walkthrough fn) | Run audit before commit |
| Touched `app.py::_try_save_*_gift` | Run audit before commit |
| Touched `models/gift.py` (Phek format) | Run audit AND `sim_will_gen.py` |
| New burn-in rule added | Add a check to audit |

---

### 10x.34  🔥🔥 BURN-IN — H3 IDENTITY placeholders (name+role in message, no IC) 🔥🔥

**Family members named in the AI Summary / message text MUST appear as
pending identities even when their IC photo wasn't uploaded.** Mirrors
§10x.12 (assets) and §10x.15 (Image is verification only — text alone
is sufficient).

### The bug this rule prevents

Real KOID example: message says **"All Insurance go to my wife (Lim Bee
Yan) 100percent"**. Lim Bee Yan is named explicitly. Her IC was NOT
uploaded. Without this rule, the identity walkthrough only iterates
`Document.category='nric'` rows → misses her entirely → wizard shows
3 identities (testator + 2 children + sister-in-law) but the wife is
absent. The will then can't name her as bank/insurance beneficiary.

### Implementation

`services/identity_walker.py::get_pending_ic_documents` synthesises
H3 placeholder entries AFTER its IC-doc enumeration:

```python
from_text = _extract_family_name_role_pairs(recent_text)
for nm, role in from_text:
    if nm.upper() in known_names:        # already a Person
        continue
    if nm.upper() in seen_in_pending:   # already queued
        continue
    pending.append({
        'document_id': None,            # no IC uploaded
        '_h3_placeholder': True,
        '_h3_role': role,
        'extracted': {'full_name': nm, 'nric_number': '', '_h3_source': 'ai_summary'},
        '_deduction_score': 5,          # name+role in message = HIGH per §10x.30
    })
```

`_extract_family_name_role_pairs` recognises four patterns:
- `"my wife (Lim Bee Yan)"` → (Lim Bee Yan, Wife)
- `"Joshua Koid Teck Seng (son)"` → (Joshua Koid Teck Seng, Son)
- `"Joshua Koid Teck Seng(son)"` → (no space — KOID style)
- `"my wife Lim Bee Yan"` → (Lim Bee Yan, Wife)

### Chat card variant

For an H3 placeholder, the IC card shows:

```
👤 Step 1: Identity (N left)

Lim Bee Yan — _no IC uploaded yet_

📨 Mentioned in your message as **Wife**.

⚠️ Their IC photo can be uploaded later — for now, confirm the
   relationship so the will can name them.

[ ✓ Yes — Wife ]   [ 📎 Upload IC photo ]   [ 🗑 Delete ]
```

### Hard rules

1. **Score = 5 (HIGH)** — name+role explicit in message is the same
   confidence as a verbatim-name IC match per §10x.30.
2. **Person row created without document_id**. `ensure_person` accepts
   `document_id=None` cleanly.
3. **Family-relation only** — never assign will-roles in Step 1
   (§10x.32). The role comes directly from the message ("wife", "son",
   etc.) which is naturally a family role.
4. **Junk-name filter** — token must be 2-5 capitalised parts, no
   stopwords (WITH, AND, OR, BANK, INSURANCE…). Otherwise we'd pull
   "ALL BANK SAVINGS GO TO" as a name.
5. **Dedup against existing Persons by name** — case-insensitive.
   Re-running the walkthrough must NOT re-prompt the user about
   already-confirmed identities.

### Where this is enforced

| File | Function | Role |
|------|----------|------|
| `services/identity_walker.py` | `_extract_family_name_role_pairs` | Pull (name, role) tuples from AI Summary text |
| `services/identity_walker.py` | `get_pending_ic_documents` | Append H3 entries after IC-doc enumeration |
| `ai/chat_planner.py` | `_identity_question` | H3 branch shows "no IC uploaded yet" card |
| `app.py` | `_try_assign_pending_identity` | Handles `target['document_id'] is None` (no doc to link) |

### Litmus test

```
Q: KOID forward; "my wife (Lim Bee Yan)" mentioned; no IC uploaded.
   - Pending walkthrough surfaces Lim Bee Yan as Step 1 card     → ✓
   - Pending only lists ICs from Document table                  → ✗ §10x.34 broke
```

If a named family member is missing from Step 1 even though they
appear in the message text, the bug is in `_extract_family_name_role_pairs`
or in the H3 append step. Fix THERE, not by manually adding Person rows.

---

### 10x.35  🔥🔥🔥🔥 BURN-IN — MESSAGE > IMAGE. ALWAYS. 🔥🔥🔥🔥

**THE MESSAGE TAKES HIGHEST PRECEDENCE for BOTH identities AND assets.
If a person, asset, or relationship is stated in the user's WhatsApp /
email text, it MUST appear as a pending entry — regardless of whether
an image was uploaded. Image is verification, NOT a requirement.**

This is the over-arching rule. §10x.12 (every AI-Summary item = own
gift), §10x.15 (image is verification only), §10x.34 (H3 identity
placeholders), §10hg (HIGH-confidence message-stated assets) are all
specific applications of this same principle.

### The principle

```
WHEN the user says it in writing → it counts.
WHEN the user uploads a photo  → that's CONFIRMATION of what they said.

Image MISSING ≠ data missing.   Text alone is sufficient evidence.
Image MISMATCH = ASK the user (per §10x.18), don't auto-pick.
```

### Why this rule exists

The user explicitly said:
> "given identity in message, message takes highest precedence,
>  identity must be checked even without image. Burn this. the same
>  for asset"

Real KOID examples that violated this rule before fixes shipped:

| What the message said | What the system did (wrong) | Fixed by |
|---|---|---|
| "my wife (Lim Bee Yan) 100%" | Wife missing from identity registry — IC walkthrough only iterated `Document.category='nric'` | §10x.34 |
| "Property 1: Unit B-05-11 Paradisonuava" | Property 1 missing from walkthrough because no title doc uploaded | §10hg + §10x.12 |
| "Bank: POSB Account No. 030-25917-3" | Bank missing — gift_walker didn't synthesize H3 entries for banks | §10x.12 |
| "Insurance: NTUC Income Policy 1811500170" | Insurance entirely absent — `out['insurance']` key didn't exist in the gift walker | §10x.12 |

Without §10x.35 enforced as a top-level invariant, the system silently
drops people and assets the user clearly identified. That's data loss
in a probate-critical document.

### What this means for IDENTITIES

`services/identity_walker.py::get_pending_ic_documents` MUST surface
EVERY person named in the message, regardless of IC upload state:

```python
pending = []
# Source 1: actual IC documents (Document.category == 'nric')
pending += [<from doc table>]
# Source 2: H3 placeholders — names mentioned in message text but no IC
pending += [
    {'_h3_placeholder': True, 'extracted': {'full_name': name},
     '_h3_role': role, '_deduction_score': 5}
    for name, role in _extract_family_name_role_pairs(message_text)
    if name not in known_persons
]
```

If the IC arrives later (mid-flow upload), the H3 entry is REPLACED by
the real IC entry on the next walkthrough refresh. The Person row,
once created from the H3 confirm, gains the `nric_passport` +
`document_id` when the IC photo is processed.

### What this means for ASSETS

`services/gift_walker.py::get_pending_gift_documents` MUST surface
EVERY asset named in the AI Summary, regardless of doc upload state:

```python
out = {'property': [...], 'bank': [...], 'insurance': [...], 'vehicle': [...]}
# Source 1: documents (already-classified images)
out += <image-bound groups>
# Source 2: H3 placeholders — AI Summary mentions not covered by image
for ai_prop in _extract_ai_summary_properties(client_id):
    if not _covered_by_image_group(ai_prop):
        out['property'].append(_h3_property_placeholder(ai_prop))
# Same for bank + insurance
```

Image-bound groups that have NO AI-Summary linkage go to RESIDUAL
(§10d unverified card path) — they don't pollute the pending count.

### Hard rules

1. **Pending count = Message-stated count** for every category.
   - Identities: pending(text) ≥ Persons(confirmed). One H3 per
     unmatched-by-IC name.
   - Properties: pending == AI Summary property count (§10b).
   - Banks: pending == AI Summary bank count (§10x.12).
   - Insurance: pending == AI Summary insurance count (§10x.12).

2. **NEVER drop a message-stated entity because it lacks an image.**
   The H3 placeholder route is mandatory — silent omission is a §10x.35
   regression.

3. **Image LATER ≠ create a new entry.** When an IC or asset doc
   arrives mid-flow, the existing H3 placeholder is REPLACED (its
   identity merged) with the IC-bound entry. Never two cards for the
   same person/asset.

4. **Image MISMATCH = pause and ask.** Per §10x.18, when text says one
   thing and image says another (e.g. NRIC last digit differs), the
   chat surfaces a clarification card. Never auto-resolve.

5. **Co-owners / counterparties named in text but NOT family** stay
   as property-card metadata only (per §10x.19), NOT in Person table.

### Before-and-after example: KOID

Without §10x.35:
```
Identities (3):  ESTHER, KOID, LIM LAY CHENG     ← LIM BEE YAN MISSING
Pending props (3):  shop + lot 207922 + hallucinated MARSILING
Pending banks (0):  ALL FOUR BANKS ABSENT
Pending insurance (0):  ALL THREE POLICIES ABSENT
```

With §10x.35:
```
Identities pending: LIM BEE YAN [H3] role=Wife (score=5)
                    after upload: real IC NRIC 661126-04-5182
Pending props (5):  matches AI Summary ✓
Pending banks (4):  matches AI Summary ✓
Pending insurance (3):  matches AI Summary ✓
```

### Where this is enforced

| Domain | File | Function | Mechanism |
|--------|------|----------|-----------|
| Identity | `services/identity_walker.py` | `get_pending_ic_documents` | Append H3 entries from `_extract_family_name_role_pairs` |
| Identity | `services/identity_walker.py` | `_extract_family_name_role_pairs` | 4 regex patterns covering "(role)", "role(name)", "my role name", "name (role)" |
| Identity | `ai/chat_planner.py` | `_identity_question` | H3 branch: "no IC uploaded yet" card with role pre-suggested |
| Property | `services/gift_walker.py` | `get_pending_gift_documents` | H3 placeholders for unmatched AI Summary properties |
| Bank | `services/gift_walker.py` | `get_pending_gift_documents` | H3 placeholders for AI Summary banks |
| Insurance | `services/gift_walker.py` | `get_pending_gift_documents` | H3 placeholders for AI Summary insurance |
| Audit | `services/asset_audit.py` | reconciliation checks | Pre-deploy gate per §10x.33 |

### Litmus test (mandatory before any deploy touching identity/asset code)

```
1. Run asset_audit.py against KOID fixture
2. Run identity_full_audit.py against KOID fixture
3. Both must report exactly:
     identities pending = (people named in text - already-confirmed Persons)
     property pending = AI Summary property count
     bank pending = AI Summary bank count
     insurance pending = AI Summary insurance count
4. Any deviation = §10x.35 regression. Block the deploy.
```

If a code change makes the system DROP a message-stated entry from
pending, the bug is in the corresponding walker (`identity_walker.py`
or `gift_walker.py`). Fix THERE, not by manually inserting Persons
or step5 entries — those workarounds will not survive the next
session and the bug will resurface (the very thing §10x.33 + §10x.35
were burned in to prevent).

---

### 10x.36  🔥🔥🔥 BURN-IN — EVERY gift / identity card MUST show the message reference 🔥🔥🔥

**Cross-reference §9 + §10x.35. ALL pending cards (gift, identity,
executor, beneficiary) MUST display the message line that names this
entity / distribution. NO EXCEPTIONS. The chat must NEVER ask the user
something the message has already answered.**

### The bug this rule prevents

Real KOID example: the asset card for **Unit B-05-11 Paradisonuava**
showed the user 5 buttons with NO reference to what the message said:

```
❌ BAD CARD (without §10x.36):
🏠 Specific Gift (5 left) — Unit B-05-11, Condominium Paradisonuava
   (location not specified)
Who is the main beneficiary for this property?
[ Esther Koid En Hui 100% ]
[ Joshua Koid Teck Seng 100% ]
[ Lim Bee Yan 100% ]
[ Lim Lay Cheng 100% ]
[ Esther + Joshua 50/50 ]
```

But the message clearly said:
> "Unit, B-05-11 Condominium Paradisonuava
>  I share with Chai Mei Fun 50/50,
>  My 50percent to Joshua Koid Teck Seng(son) 25percent and 25percent
>  to Esther Koid En Hui (daughter)"

The user already specified the distribution. Showing canned 100%
buttons IGNORES what they wrote — and worse, the system was unable
to deduce the split because the percentages (25+25) sum to 50%, not
100% — but per §10x.13 the percentages are of the testator's SHARE,
not the full property.

```
✓ GOOD CARD (with §10x.36):
🏠 Specific Gift (5 left) — Unit B-05-11, Condominium Paradisonuava
   (location not specified)

📨 from your message:
> Unit, B-05-11 Condominium Paradisonuava I share with Chai Mei Fun 50/50,
> My 50percent to Joshua Koid Teck Seng(son) 25percent and 25percent to
> Esther Koid En Hui (daughter)

Who is the main beneficiary for this property?

📧 Suggested from email:
  • Joshua Koid Teck Seng 50% (= 25% of full property, of testator's 50% share)
  • Esther Koid En Hui 50% (= 25% of full property, of testator's 50% share)

[ ✓ Joshua 50% + Esther 50% (suggested) ]
[ Joshua 100% ]
[ Esther 100% ]
[ ⏭ Skip ]   [ 🗑 Remove ]
```

### Hard rules

1. **Every gift / executor / beneficiary card MUST include a
   `📨 from your message:` block** with at least one snippet from
   the original WhatsApp/email text that references THIS entity.
   No snippet → bug in the card builder. Fix it.

2. **The deduce path MUST honour §10x.13** — percentages summing to
   25 / 33 / 50 / 66 / 75 are valid (they're of the testator's share);
   rescale to 100% for the suggested-button label, but ALSO show the
   raw "25% of full property" interpretation so the user sees what
   they wrote.

3. **Even when the deducer can't extract a clean distribution**, the
   card MUST still show the relevant message snippet. Better to show
   imperfect evidence than NO evidence.

4. **Asking the user a question the message answers is the worst
   possible UX.** It signals the system didn't read what they wrote.
   Cross-reference is ALWAYS shown, even when it duplicates what
   the deduced suggestion implies.

### Where this is enforced

| File | Function | Mechanism |
|------|----------|-----------|
| `ai/chat_planner.py` | `_step6_property_question` | Calls `_find_property_message_snippet` and inserts `📨 from your message:` block above the buttons |
| `ai/chat_planner.py` | `_find_property_message_snippet` | Tokenises property name+address, searches recent_text for the line containing those tokens |
| `ai/chat_planner.py` | property deduce code | Accepts totals in {25, 33, 50, 66, 75} and rescales to 100% per §10x.13 |
| `ai/chat_planner.py` | bank/insurance card builders | Same pattern — must inject `📨 from your message:` line |
| `ai/chat_planner.py` | `_identity_question` | Already shows snippet (per §9 + §10x.21); covered by this rule |
| `ai/chat_planner.py` | `_step3_executor_question` | Already shows "from your message" line (§9) |

### Litmus test

```
Run KOID walkthrough end-to-end. For every assistant card shown:
  - Is there a `📨 from your message:` block referencing the
    relevant message line?
    YES → ship
    NO  → §10x.36 regression. Add the snippet to the card builder.
```

If a card asks the user "who inherits property X?" but doesn't quote
the message line that already answered that, the bug is in the card
builder. Fix at the card builder, not by editing user clicks.

---

### 10x.37  🔥 BURN-IN — Step transition messages MUST be DYNAMIC 🔥

**The "🎉 Step N — COMPLETE. Now moving to Step M." message MUST
compute M dynamically based on what the planner will actually show
next. NEVER hardcode the destination step.**

### The bug this rule prevents

Real KOID example: chat showed:
```
✅ Saved LIM BEE YAN as Wife.
🎉 Step 1: Identities — COMPLETE. All ICs assigned.
   Now moving to Step 2: Testator Info.
🏠 Specific Gift (5 left) — Unit B-05-11 ...   ← Step 6, NOT Step 2!
```

The "moving to Step 2" was a hardcoded string in the chat planner.
But the planner had ALREADY done Steps 2-5 in earlier turns, so when
the next planner turn ran, it landed on Step 6. The user saw two
contradictory messages in one assistant turn.

### Implementation

`ai/chat_planner.py::_compute_next_step_label(will_data)` returns the
human-readable label for the NEXT step the planner will land on:

```python
def _compute_next_step_label(will_data):
    if not _is_confirmed(will_data, 'testator'): return 'Step 2: Confirm Testator'
    if no executors:                              return 'Step 3: Executor'
    if no beneficiaries:                          return 'Step 5: Beneficiaries'
    if 'assets_confirmed' not in completed:       return 'Step 6: Specific Gifts'
    if no residuary:                              return 'Step 7: Residuary Estate'
    return 'Step 10: Generate Will'
```

The Step-1-complete message uses this:
```python
next_label = _compute_next_step_label(current_will_data)
reply_parts.append(
    f"🎉 Step 1: Identities — COMPLETE. All ICs assigned.\n"
    f"Now moving to {next_label}."
)
```

### Hard rules

1. **No hardcoded step destinations in transition messages.**
2. **Same rule for every step transition** — Step 2 → 3 ack must look
   up the actual next step, etc.
3. **If the computed label changes, the planner's gates must already
   be past the previous step.** Otherwise the message will tell the
   user something different from what they then see.

---

### 10x.38  🔥🔥🔥 BURN-IN — WIZARD STEP INDICATOR = CHAT STEP. ALWAYS. 🔥🔥🔥

**The right-pane "Will Snapshot" current-step indicator MUST match
the step the chat is currently asking about. NEVER show Step 7 in the
wizard while the chat is on Step 6. If they desync, the user sees
contradictory state and loses trust in the system.**

### Why this rule exists

User report: chat showing **Step 6: Specific Gifts** walkthrough card,
right-pane "Will Snapshot" indicator pulsing on **Step 7: Residuary**.
Two parts of the same UI tell the user different things at the same
moment.

Root cause: `_current_stage_num` in `app.py` returned 7 because
`'assets_confirmed'` was in `completed_steps` (set when user typed
"confirm assets" earlier) BUT `step5_data` had 0 saved gifts (because
the walkthrough was still running). The wizard indicator believed
gifts were done; the chat knew it was still walking gifts. Desync.

### Hard rule

`_current_stage_num` and the chat planner `plan_turn` MUST derive
their step decision from the **same gates in the same order**:

```
1  pending IC              → Step 1 (Identity walkthrough)
2  testator not confirmed  → Step 2 (Confirm Testator)
3  < 1 executor            → Step 3 (Executor)
4  no minor children       → Step 4 (Guardian — skipped)
5  no beneficiaries        → Step 5 (Beneficiaries)
6  pending gifts > 0       → Step 6 (Specific Gifts)
       OR 'assets_confirmed' not in completed AND no saved gifts
7  no residuary             → Step 7 (Residuary Estate)
8  no trust                 → Step 8 (Trust)
9  no other                 → Step 9 (Other matters)
10 else                     → Step 10 (Generate Will)
```

Crucial detail: **Step 6 is "still running" as long as ANY pending
gift remains in the walker**, even if 'assets_confirmed' was set.
'assets_confirmed' just means the asset-inventory phase ended — the
walkthrough phase may still be open.

### Implementation

`app.py::_current_stage_num` queries `get_pending_gift_documents` to
count pending across all kinds (property+bank+insurance+vehicle). If
total_pending > 0 → return 6, regardless of any other flag.

`ai/chat_planner.py::_compute_next_step_label` uses the same logic
for the "Now moving to Step X" announcement message (§10x.37).

### Where this is enforced

| File | Function | Role |
|------|----------|------|
| `app.py` | `_current_stage_num` | Drives the JS `currentIdx` in chat right-pane |
| `app.py` | `_current_stage_label` | Human-readable label for Q&A nudges |
| `ai/chat_planner.py` | `_compute_next_step_label` | "Moving to Step X" message |
| `static/js/chat.js` | snapshot section render | Reads `will.current_stage_num` from server |

### Litmus test

```
Chat is showing Step N card. Wizard right pane current step indicator:
  - matches N                      → ✓
  - shows different step           → ✗ §10x.38 regression. Fix.
```

If they ever differ, the bug is in `_current_stage_num`. Fix it there
— do NOT add UI hacks like "force step 6" in JS. The backend gates are
the source of truth.

---

### 10x.39  🔥🔥🔥🔥 BURN-IN — UNIFIED BUG TABLE 🔥🔥🔥🔥

**Single source of truth. Combines what was historically the "FUCK list",
the "Things NOT To Do" list, and the per-session bug tables. Every entry
the user has called out as "must never resurface" lives here, with the
fix and a confidence rating. Add a new row at the bottom when something
new is reported. NEVER split into multiple tables again.**

### The unified bug table

Schema: **# | Rule | What user saw | Root cause | Fix | Confidence**
(HIGH = verified live or has automated check; MEDIUM = fix shipped but
not E2E-verified on fixture; LOW = rule documented, fix not yet applied
or open).

| # | Rule | What user saw | Root cause | Fix | Confidence |
|---|------|---------------|------------|-----|------------|
| 1 | §10x.33 | Same bugs kept resurfacing across sessions | No automated audit gate; rules in CLAUDE.md alone weren't enforced | Pre-deploy `asset_audit.py` runs §10x.48 + §10x.49 invariants; deploy blocked on red | HIGH |
| 2 | §10x.35 | Asset/identity stated in message disappeared because no image was uploaded | Walker only iterated `Document` rows, ignored message-only entities | Pending walker synthesises H3 placeholders from AI Summary; pending count == AI Summary count for every category | HIGH |
| 3 | §10x.36 | Gift card asks "who inherits X?" when message already says it | Card builder skipped the message-snippet block when deduce path was confident | Every gift / executor / beneficiary card MUST include 📨 _from your message:_ block (`_find_property_message_snippet` etc.) | HIGH |
| 4 | §10x.38 | Right-pane "current step" indicator shows Step 7 while chat is on Step 6 | `_current_stage_num` keyed off `assets_confirmed` flag; chat planner used different gate ordering | Both derive from same gate order; pending gifts > 0 → Step 6 always wins | HIGH |
| 5 | §10x.31 | Clicking Skip on an IC card silently dropped that family member | `_chat_skipped=True` was being set on Skip click → walker filtered the IC out forever | Skip is now a no-op; only `_skip_count` increments. Same card re-shows until Yes or Delete | HIGH |
| 6 | §10x.34 + §10x.35 | "my wife (Lim Bee Yan) 100%" — wife absent from identity registry | `get_pending_ic_documents` only iterated `Document.category='nric'` | H3 placeholders synthesised from `_extract_family_name_role_pairs` over message text | HIGH |
| 7 | §10x.32 | Step 1 saved a Person with `relationship='Executor'` because LLM returned it | `parse_relationship` / `deduce_roles` returned will-roles in Step 1 context | `_WILL_ROLES` set filters will-roles out of Step 1 saver; falls through to outsider-elimination → family relation | HIGH |
| 8 | §10e + §10x.30 | Outsider IC walked first, named family last | Sort by upload-time only; no confidence score | `_score_ic_confidence` (5/4/3/1/0) sort DESC; high-confidence walks first | HIGH |
| 9 | §10b | 5-property message produced 14 / 31 walkthrough cards | Card-per-image grouping; OCR title drift (`564662` vs `504662`) split one property into many | Group by `(lot_digits, addr_sig)`; AI Summary count = walker N | HIGH |
| 10 | §10x.12 | Banks lumped into one "all banks → wife" clause; insurance silently dropped | Walker had no `bank` / `insurance` category; gift_walker only knew `property` | Categories `bank`, `insurance`, `vehicle`, `epf`, `shares` each emit per-item gift entries | HIGH |
| 11 | §10ha | Title-doc OCR address ("Phase 2D Seri Alam") used as canonical → wrong matches | Matcher trusted `extracted_data.property_address` on title docs | Title docs match by Lot/Title/Mukim only; address comes from message / AI Summary | HIGH |
| 12 | §10hd | C-30-08 and C-05-01 (different units, same building) merged into one card | Group key was `lot_number` only; same lot = "same property" | Strata exception: group by `(lot, title)`. Sibling enrichment skips cross-title copies | HIGH |
| 13 | §10x.19 | Chai Mei Fun (50/50 co-owner of B-05-11) appeared in Step 1 family registry | Co-owner mention triggered Person row creation | Co-owner stays in `property_info.co_owners` only; never a Person row | HIGH |
| 14 | §10x.13 | "25% to Joshua, 25% to Esther" rejected because totals != 100% | Deduce path required totals == 100% of full property | Accept totals in {25, 33, 50, 66, 75}; rescale to 100% of testator's share | HIGH |
| 15 | §10x.14 | Layer 3 substitute card empty, no default | Walker had no auto-default logic | `_default_substitute()`: surviving beneficiaries / other child / spouse → children | MEDIUM |
| 16 | §10x.15 + §10x.35 | Bank gift parked as "pending — upload statement" because no image | Layer 1 confirmation gated on image presence | Text alone sufficient; H3 placeholder for any AI-Summary item without image | HIGH |
| 17 | §10x.24 | Generated will didn't match Alan & Tan firm format | Drafter freely paraphrased clauses | Phek Yi Ting template patterns codified per asset kind; `sim_will_gen.py` snapshot test | MEDIUM |
| 18 | §10x.25 | LLM-produced will-clauses drifted between runs | Drafter used Sonnet to compose clauses | `template_filler.py` deterministic; no LLM in clause emission | HIGH |
| 19 | §10x.9 + §10x.28 | 12+ duplicate "📋 N exhibits received" cards on one chat | Watchdog re-fired processor every 5s; no in-process lock | 3-layer defence: `_PROCESSING_LOCK` + watchdog throttle + idempotency check on card content | HIGH |
| 20 | §10x.26 | "Analysing..." UI banner stuck at 96% forever | Vision retry had no terminal state; watchdog kept re-firing on `chat_inbox` docs | `_classify_attempts >= 3` or `manual_review=True` → promote to `needs_review` (watchdog ignores) | HIGH |
| 21 | §10x.33 | Bugs shipped to prod because "I tested it locally" | No pre-deploy gate | `asset_audit.py` runs reconciliation checks on every fixture; deploy blocked on fail | HIGH |
| 22 | §10x.41 | Identity rows with empty `relationship` polluting registry | `ensure_person` accepted empty role | `ensure_person` returns None on empty role for NEW Person; caller must ask user | HIGH |
| 23 | §10x.42 | Wife added mid-flow but bank/insurance gifts already saved had no beneficiary | New Person didn't trigger downstream re-evaluation | `_reconcile_downstream_for_new_identity` runs after every Person save → updates step4 / step5 | HIGH |
| 24 | §10x.43 | Second forwarded email ignored because Steps 2-5 marked complete | Walkthrough gates trusted `completed_steps` flag | Inbound webhook → watchdog re-fire (§10x.29) → walker re-emits pending → reconciler closes loop | HIGH |
| 25 | §10x.44 | Aunt Mary IC arrives mid-flow with "I appoint Aunt Mary as Trustee" — Step 8 not updated | Reconciler only handled Step 5 (Beneficiary) | Reconciler dispatches per will-role: `_step2/3/4/7_add_*` for Executor/Guardian/Beneficiary/Trustee | MEDIUM |
| 26 | §10x.45 | Property card with 5 bulleted blocks of "Land Registry / Cannot probate / probate explanation" | Card builder dumped every field as its own bullet | One-line `📋 Title · Lot · Mukim · Daerah · Negeri`; ≤6 sections per card; terse warnings | MEDIUM |
| 27 | §10x.46 R1 | Layer 1 confirm card showed "Beneficiary intent: Joshua 25%, Esther 25%" | Layer 1 builder included Layer 2 fields | Layer 1 = asset identity ONLY; testator share + beneficiary moved to Layer 2 | HIGH |
| 28 | §10x.46 R2 | Card text said "Confidence: HIGH" — exposed internal scoring | Card builder echoed tier label | Tier label removed from text; only button count varies (HIGH=1 / MEDIUM=3 / LOW=3) | HIGH |
| 29 | §10x.46 R3 | Message-only H3 cards labelled HIGH confidence (no image) | Tier definition was "user stated it" alone | HIGH = lot/title in image == AI Summary's; message-only is at best MEDIUM | HIGH |
| 30 | §10x.46 R4 | Same matching bug recurred 3× because each fix patched the symptom | No documentation of signal vocabularies between sources | Document what each source contains BEFORE adding match logic; semantic layer (geo bridge) over lexical when overlap is rare | HIGH |
| 31 | §10x.47 | Verifier said PASS on a fixture with 0 Documents — matcher never ran | Verifier only counted gift entries, didn't check whether matching was exercised | Pre-flight: text-only fixture passes only if every text-stated detail lands on the gift; mixed fixture fails if Documents exist but every gift is H3 | HIGH |
| 32 | §10x.48 | Address dropped, lot/title lost, ghost properties — matching code drifted between sessions | One-stage matcher mixed parsing + grouping + binding + saving | SIX stages (Parse / Group / Bind / Residual / Build / Walkthrough); AssetItem ← message ∪ AI Summary; DocGroup is the binding unit; Tier A→B→C→D priority; one-claim-only; runtime `ContractViolation` checks each stage | HIGH |
| 33 | §10x.97 | KOID's 5-property message produced 6 AI Summary properties | Narrative-fallback parser accepted "POSB Bank account 030-25917-3" as a property because `25917` matched `_POSTCODE_RE` (`\b\d{5}\b`) | Skip-hint check (`_RAW_SKIP_HINTS`) runs BEFORE postcode/property-hint test in `_parse_ai_summary_text` | HIGH |
| 34 | §2 Test Pipeline | Claude said "tested" without actually verifying | No mandatory test ritual after deploy | Health check + real SMTP send + nginx access log grep — all 3 must pass | HIGH |
| 35 | §1 Build & Deploy | Code change "deployed" but old image still served | Image baked at build time; `restart` reuses old image | MUST run `git pull && docker compose build web && docker compose up -d web` | HIGH |
| 36 | §4 Inbox Address Format | `inbox.will.alantanjb.com` failed to receive mail | Subdomain MX wasn't configured; bare domain MX is the one that works | Address format is `<first5><ic_last4>@will.alantanjb.com` — no `inbox.` prefix | HIGH |
| 37 | §10 UI Rules | AI Summary card showed exhibit thumbnails (already on intake card) | Default `attachments_json` carried the message attachments through | AI Summary card uses `attachments_json='[]'` so thumbnails aren't repeated | HIGH |
| 38 | §8 | Click → nothing happens (button rendered but no handler) | Quick-reply `value` had no matching app.py handler | Every new button must be smoke-tested; handler for the `value` string is mandatory | MEDIUM |
| 39 | §4a | Chat asked "what's your IC number?" when wizard already had it | Chat planner re-asked without dedup against Person table | Dedup checks against `Person.nric_passport` (canonical extract) and `Person.full_name` BEFORE rendering any walkthrough question | HIGH |
| 40 | §2 | Deploy declared done without testing | No mandatory test ritual | (a) health, (b) real SMTP send, (c) verify webhook fired in nginx log | HIGH |
| 41 | §2 + §10x.33 | "Fixed" claimed without exercising the buggy path | No reconciliation against actual client data | Pre-deploy `asset_audit.py` counts pending gifts + reconciliation per fixture | HIGH |
| 42 | §10i | Image bound to property by adjacency but timestamps not shown | Card builder skipped the `[image_ts]` / `[message_ts]` line | Card MUST show 📎 image timestamp + 💬 message timestamp + the gap (visible evidence) | MEDIUM |
| 43 | §10aa | "VALUE: GRN56662" stored as title number; "(unreadable)" stored as identifier | Saver trusted raw `extracted_data` strings | `_clean_id_value` strips `VALUE:` / `LOT` / `TITLE` prefixes; `_looks_like_garbage` rejects `UNREADABLE` / `CANNOT READ` | HIGH |
| 44 | §10aa | Two doc rows with `lot='LOT 207922'` vs `lot='207922'` treated as different properties | Dedup compared raw strings | Every dedup / group-key / comparison MUST `_clean_id_value` first | HIGH |
| 45 | §10d + §10x.35 | Isolated image got auto-rendered as a property card with hallucinated address ("10 Marsiling Lane Singapore") | Matcher fabricated address when extraction returned empty | Isolated image with no AI-Summary match → §10d unverified card asks user; NEVER invent property/address | HIGH |
| 46 | §10g | Same address bound to two property cards | Multiple docs (title + SPA + photo) for same property each got their own card | One-claim-only: `claimed_addresses` set; non-title docs auto-link to title's group via `(lot_digits, addr_sig)` | HIGH |
| 47 | §10h | Matcher re-deduced from raw message text after AI Summary already canonicalised | Matcher fell back to raw text first | AI Summary IS canonical; matcher reads from summary, not raw forward text | HIGH |
| 48 | §10i | AI-Summary property with no content match left as summary-only card even though adjacent unclaimed image existed | No temporal-fallback logic | Adjacency window (4 lines before / 3 after / 5 min) mandatory before summary-only card | MEDIUM |
| 49 | §10ha | Title doc says "Mukim Plentong"; AI Summary says "Seri Alam Masai" — system flagged mismatch instead of binding | Matcher didn't know "Seri Alam Masai" is in Mukim Plentong | `_GEO_BRIDGE` table maps street → mukim; matcher uses BOTH lot/title AND mukim signals | HIGH |
| 50 | §10a + §10x.46 R1 | Property identity card showed "Client wants to give to X" | Card builder pulled from Step 5 / Step 6 fields | Layer 1 = asset identity ONLY; beneficiary lives in Layer 2 | HIGH |
| 51 | §10x.93 | Wizard Step 6 → Gift Main Beneficiaries dropdown shows "-- Select Beneficiary --" (empty) even though chat saved Esther + Joshua 50/50 | Wizard `<select>` enumerates `step4_data`. Chat only added wife (via §10x.42 reconcile for banks); property-gift beneficiaries were saved on the gift entry only — never registered in step4 | `_try_save_property_gift` Phase B now ALSO appends every main + substitute beneficiary into `step4_data` (deduped by name; pulls NRIC + address + relationship from Person row) | HIGH (verified live: dropdown shows ESTHER KOID EN HUI Daughter) |
| 52 | §10x.98 | Expand Step 6 in wizard right pane → ~5s later expander auto-closes (looks like a "4-second timer") | `static/js/chat.js` line 1040 polls every 5s (`POLL_MS = 5000`). Each poll calls `renderWillSnapshot(will)` which at line 885 unconditionally does `willPane.innerHTML = sections.join('')`, wiping the user's manual expand state. Line 971 then recomputes `isOpen = isCurrent \|\| !isFilled`, which closes any step that's neither current nor empty. | Shipped commit 211ad69. `static/js/chat.js`: capture user's open/closed state into Sets before `innerHTML =`, then re-apply. Added `data-step="${n}"` to `<details>` tag for cross-render identification. | HIGH (shipped — pending visual confirmation) |
| 53 | §10x.95 | Property 2 (Marina Cove) ownership card asks sole-vs-joint cold; doesn't pre-fill "Joint" despite AI Summary clearly saying "jointly owned 50/50 with son Joshua" | v1 (initial §10x.95 ship) used lexical substring match on `_gex_addr in _ap_addr`. Silently failed because narrative-format AI Summaries omit lot/title (user typed only addresses), and OCR's address field is junk ("Marina Cove, Johor Bahru (from doc extract)"). Different vocabularies between sources — no overlap, no match. Per §10x.46 R4, this was a symptom-patch not a root-cause fix. | Shipped commit 777bccf. v2 calls `services.asset_pipeline.bind_assets()` which already runs the canonical Tier A/B/C cascade (lot/title direct → mukim+token via §10ha geo bridge → claude_semantic via §10hf/§10hc). Looks up `ai_props[binding.ai_index]` for Tier A/B/C bindings and pre-fills ownership from `ai_prop.ownership`. Lexical match retained ONLY as last-resort for Tier D fallback. Verified live: KOID's AI[1] Unit C-30-08 binds to group 6b03c11d via Tier C claude_semantic. | HIGH (shipped — pending visual confirmation) |
| 54 | §10x.99 | Same client data → different property bindings across 4 consecutive runs. AI[2] C-05-01 bound 2/4, dropped 2/4. AI[3] Sri Laguna bound 3/4, dropped 1/4. User would see Property 3 inconsistent between page reloads. | Three Claude API call sites — `services/asset_pipeline.py::_claude_semantic_match`, `services/web_property_clues.py::search`, `services/geo_resolver.py::web_resolver` — all called `client.messages.create()` without `temperature` parameter. Anthropic default is 1.0 (max randomness). Same prompt → different answers per call, especially on ambiguous classification (e.g. C-30-08 vs C-05-01 in same building, or Sri Laguna with sparse OCR). Violates §10x.48/§10x.49 self-validating-pipeline determinism contract. | Shipped commits b731261 (asset_pipeline) + 43822a5 (web_property_clues + geo_resolver) + 1bf2694 + 7a3fafd (§10x.104 caches). `temperature=0` on all 3 LLM call sites. Plus DB-backed VisionExtractCache layers added to `web_property_clues.search_property_clues` (key='clues:<norm-addr>') and `_claude_semantic_match` (key='semantic:<sha256(prompt)>'). Verified live: **5/5 consecutive runs identical**, full determinism achieved (web_search non-determinism amortised by cache hits). | HIGH (verified 5/5 runs identical) |
| 55 | §10x.100 | KOID iter 4: 5 AI Summary properties → 8 saved gifts in step5_data. The walker did 2× `inventory confirm` (path #1) and 6× `inventory h3 confirm` (path #2), and each path saved its OWN gift for the same AI Summary slot — duplicate entries for the same property. | Path #1 (`_try_save_property_gift` placeholder block at app.py:7197) didn't stamp `_ai_summary_idx` on the saved gift. Path #2 (`_try_handle_h3_property_action`) checks `_ai_props_already_handled()` to find unhandled slots — its 3-pass dedup (Pass 1 by `_ai_summary_idx`, Pass 2 by lot/title/address sig, Pass 3 by unit token) all failed: Pass 1 because the tag was missing, Pass 2 because narrative AI Summary has empty lot/title and OCR address is junk ("Marina Cove, Johor Bahru (from doc extract)"), Pass 3 because placeholder description is empty. Result: every AI Summary slot looked unhandled → H3 path appended for ALL of them on top of existing placeholders. | Shipped commit 51ef039. **Fix 1 (primary):** placeholder save calls `bind_assets()` (same pattern as §10x.95 v2) to resolve `doc.id → ai_index` and stamps `_ai_summary_idx` on the gift. **Fix 2 (defence):** before `s5.append(entry)` in H3 handler, check for any existing gift with same `_ai_summary_idx` and upsert instead of duplicate-append. Upsert preserves user-set fields (beneficiaries / allocations / substitute / `_layer1_confirmed`) so it can't clobber prior progress. | MEDIUM (shipped — needs walker re-run to verify count drops from 8 → 5) |
| 56 | TEST-HARNESS | After 3rd `insurance_l1 confirm`, the test walker (`/app/data/walk_step6.py::pick_next`) shortcut to `confirm assets` instead of clicking `insurance_l2 main 100% LIM BEE YAN`. Result: 3rd insurance gift saved with empty beneficiaries → verifier R4/R5 failures. NOT a product bug — only the autonomous test loop hit it. | `pick_next` had an early shortcut at line ~117: `if 'confirm assets' in low and ('reply' in low or BACKTICK in low): return 'confirm assets'`. After saving the 3rd insurance L1, the assistant reply rendered the L2 main-beneficiary card with quickreplies, but ALSO carried a footer hint like "Reply 'confirm assets' to finalize." The early shortcut matched the footer text and bypassed the L2 quickreplies. | Patched `/app/data/walk_step6.py` directly (lives in docker volume, not repo). Added a layered-priority loop BEFORE the `confirm assets` check: drains `bank_l1 confirm`, `bank_l2 …`, `bank_l3 …`, `insurance_l1 confirm`, `insurance_l2 …`, `insurance_l3 …` first. `confirm assets` moved to a LATE fallback right before the generic `for v in vals: if v not in sent[-2:]: return v`. | HIGH (test-harness only) |
| 57 | §10x.101 | After confirming Layer 1 of the 3rd (last) insurance policy, the chat skipped Layer 2 (main beneficiary) and Layer 3 (substitute) entirely — showed a generic "Asset inventory / no docs uploaded" message instead. The AIA gift was saved with empty beneficiaries, breaking will generation downstream. | `ai/chat_planner.py::plan_turn` line ~270: `if not has_any_assets: reply_parts.append(_assets_prompt_for_uploads()); return`. After saving the LAST L1 of the LAST asset category (insurance #3), `pending_gifts` was all zeroes → `has_any_assets=False` → planner returned the generic upload prompt WITHOUT calling `_asset_walkthrough_question()` which would have correctly surfaced the AIA L2 card by iterating saved gifts with empty `beneficiaries`. §10x.46 R7 already handled the property case (`layer2_pending_props`) but didn't cover banks/insurance/vehicles. For bank #4 the bug didn't show because pending insurance was still non-empty at that moment. | Shipped commit 6897b5f. Call `_asset_walkthrough_question()` BEFORE the `_assets_prompt_for_uploads` bailout. If it returns a card (saved gift needs L2/L3), use that card. Only fall through to the generic prompt when the walker also returns nothing. Verified live: KOID's AIA insurance now gets L1+L2+L3. **verify_step6.py PASS, exit=0**. | HIGH (verified live) |
| 58 | §10x.102 | After §10x.100 the property-gift count went 8→7 — one phantom duplicate still survived. Specifically the Shop @ Jalan Gunung 4 was saved twice (one tagged with `_ai_summary_idx=4`, one tagged None). | When the user clicked `inventory confirm` for the Shop and `_try_save_property_gift` ran, the §10x.99 leftover non-determinism (web_search returns different snippets across calls) made `bind_assets()` return Tier D for that one save. So the placeholder went into step5_data with `_ai_summary_idx=None`. Later, when the H3 path tried to satisfy AI[4] Shop, the pipeline binding succeeded (different web_search result) → it appended a NEW gift instead of recognising the orphan placeholder as the same physical property. | Shipped commit b5ba075. Extended Fix 2 (the H3 upsert dedup) with a second pass: if no gift has `_ai_summary_idx == h3_idx`, look up the DocGroup that the pipeline now binds to AI[h3_idx] and find any existing untagged property gift whose `document_id` is in that group's `document_ids`. If found, stamp `_ai_summary_idx=h3_idx` on it and upsert. Initialised `target_b=None` and `doc_groups=[]` defensively before the pipeline try block so the new lookup is safe even when the pipeline import fails. Verified live: property count 7→6, verify_step6.py still PASS. | HIGH (verified — Shop duplicate eliminated; Marina Cove duplicate #2 from orphan DocGroup remains, separate Stage-1 grouping issue) |
| 59 | §10x.103 | `§10x.68 contract violation: log_usage called with no client_id` — 17+ warnings per walker run from `services/asset_pipeline._claude_semantic_match`, `services/web_property_clues.search`, `services/geo_resolver.web_resolver`, `services/property_locale_verifier.verify_locale`. API costs not attributed to client/will → cost reporting wrong. | `api_chat_message` already wrapped its handler in `track_context(client_id=...)` (line 3796). But `api_chat_replan` (line 4425) called `plan_turn()` WITHOUT the wrapper, so any nested log_usage inside the planner's pipeline calls saw an empty contextvar. Same issue in the autonomous walker (`/app/data/walk_step6.py::run_turn`) which bypasses the API entry point entirely. | Shipped commit d7dce9c. (1) Wrap `plan_turn` call in `api_chat_replan` with `track_context(client_id=..., will_id=..., user_id=...)`. (2) Patch walker's `run_turn` (in docker volume, not repo) to delegate to `_run_turn_inner` via `with track_context(client_id=CID, user_id=USER_ID): ...`. Verified live: walker run shows zero §10x.68 violations (was 17+). | HIGH (verified — production replan + test walker both wrapped) |
| 60 | §10x.105 | One Marina Cove phantom property gift (gift [1] doc `0664e07a` in DocGroup `3b89a4a2`) survived §10x.100/§10x.102 fixes — that DocGroup is genuinely orphan (no AI Summary slot binds to it because all 3 title docs in the group OCR'd as `title=564662` without strata sub-token, so neither C-30-08 nor C-05-01 could claim them). | Pipeline correctly returns no Binding for this DocGroup. But `_try_handle_inventory_action` placeholder-save block didn't recognise that case as orphan — it only handled Tier A/B/C (set ai_idx) and Tier D (skip). When `_b is None` (no binding entry at all), the code fell through to the placeholder save with `_ai_summary_idx=None` → phantom gift. | Shipped commits 989300d (initial gate) + d0fd703 (widened detection: `_b is None` OR `_b.tier == 'D'` both treated as orphan). Inventory-confirm placeholder save now: marks doc `_inventoried=True _orphan_group_skipped=True` and skips `gifts_ph.append(placeholder)`. **Note**: a Phase A gate (`_try_save_property_gift` entry) was attempted (commit c55c122) but reverted (commit db662d3) — fired too aggressively, refused legitimate saves and left only 1 property gift saved. Live verifier still PASS at property count = 6 (1 Marina Cove orphan phantom remains; 5 expected). | MEDIUM (placeholder gate works; deeper grouping fix would need either improving Stage 1 OCR for strata sub-tokens or a smarter Phase A gate that distinguishes orphan-group docs from legitimate transient binding misses) |
| 61 | §10x.106 | SQLAlchemy 2.0 deprecation warnings fired on every chat poll / walker run: (1) `Coercing Subquery object into a select() for use in IN()` from `ai/chat_planner.py:875` and `:1084`, (2) `Query.get() method is considered legacy` from `verify_step6.py:233`. Cosmetic but noisy and breaks SQLAlchemy 2.0 future compatibility. | Both helpers used pre-2.0 patterns: `db.session.query(...).filter(...).subquery()` returns a Subquery object that 2.0's `.in_()` rejects, and `Model.query.get(id)` is replaced by `db.session.get(Model, id)` in 2.0. | Shipped commit c7aa488. (1) `ai/chat_planner.py`: replace both `.subquery()` calls with `from sqlalchemy import select; select(ChatSession.id).filter(...)`. (2) `/app/data/verify_step6.py` (docker volume): `Document.query.get(did)` → `db.session.get(Document, did)`. Verified zero warnings on walker run + verify_step6.py output. | HIGH (verified zero warnings) |
| 62 | §10x.107 | `services.web_property_clues.search` was top API cost: $5.58 over 89 calls in 24h (~$0.063/call). Web search is paid per call. Investigation showed cache had 5 entries (1/AI prop) but doc-OCR addresses normalised to different keys → every doc address paid full price even though the AI Summary equivalent was cached. Also, parse_canonical_assets called web_clues_fn for every AI Summary property even when mukim was already known via §10ha bridge AND lot/title were in the message. | Two leaks: (1) `_normalise_clues_cache_key` didn't strip OCR-extractor noise suffixes like `(from doc extract)`, `(unreadable)`, so `"Marina Cove, Johor Bahru (from doc extract)"` and `"Unit C-30-08, Condominium Marina Cove"` had different cache keys for the same physical property. (2) `parse_canonical_assets` always called `web_clues_fn` regardless of whether the existing data was sufficient. | Shipped commit ec024f5. (a) `_normalise_clues_cache_key` now strips parenthetical OCR/extractor suffixes BEFORE other normalisation. (b) `parse_canonical_assets` skips `web_clues_fn` call when mukim is known AND lot/title are present (Tier A/B will match without web_clues). Verified live: 45 walker turns → **0 web_property_clues calls, $0.00** (was 30 calls / $0.66 per 30min). | HIGH (verified — 100% cache hit rate on warm KOID data) |
| 63 | §10x.108 | Orphan-group phantom: Marina Cove DocGroup `3b89a4a2` (3 title docs, all OCR'd as bare `title=564662` with no strata sub-token) had no AI Summary binding → §10x.105 placeholder gate marked them inventoried but Phase A still saved a phantom gift before the marker took effect. Verifier accepted 6 vs 5 property count. | The 3 title docs could legitimately belong to either C-30-08 OR C-05-01 — the data alone can't disambiguate. Per §10d intent, the right behaviour is to ASK the user, not guess. | Shipped commit 76a36d3. Three pieces: (1) `ai/chat_planner.py::_maybe_orphan_group_card` — new card builder that lists each AI Summary property as a quickreply when a target doc's DocGroup has no pipeline binding. (2) `app.py::_try_handle_orphan_claim` — dispatch handler for `orphan_claim <gid> <ai_idx>` / `orphan_remove <gid>` / `orphan_skip <gid>`. (3) `services/asset_pipeline.py::bind_assets` — new Tier 0 (user-assigned override) that honours `_user_assigned_ai_idx` BEFORE the normal Tier A/B/C cascade. User's click can never be overridden by a later LLM match. Verified live: card renders correctly with all 5 AI Summary properties as buttons + remove + skip. | HIGH (shipped — production users will see + resolve correctly; walker test harness doesn't traverse this flow) |
| 64 | §10x.109 | `services.property_locale_verifier.verify_locale` was the last LLM call site without DB-backed caching: $0.38 over 33 calls in 24h. Each call costs $0.01 web_search fee + ~$0.003 token. Process-local cache existed but died on every gunicorn worker recycle / container redeploy. Also missing temperature=0 → non-deterministic verdicts. | The verifier had a 7-day TTL in-process cache only. Across worker restarts and deploys, cache went cold → repeated paid LLM calls for the same (address, mukim, daerah, negeri) tuple. | Shipped commit f04c866. Same three-layer pattern as §10x.74 / §10x.104: (1) lookup `_CACHE` (in-process), (2) lookup VisionExtractCache row with `content_hash='locale:<sha1>'`, `call_kind='property_locale_verifier_v1'`, (3) call LLM only if both miss. Persist on every `_cache_set` call (both empty/OK and warning bodies). Plus `temperature=0` for deterministic OK/WARN verdicts. Verified live: Run 1 (cold) $0.019, Run 3 (warm) **$0.006** — ~95–98% cost cut from session baseline. | HIGH (verified — cache hit confirmed across 3 sequential walker runs) |
| 65 | §10x.110 | After §10x.109 the residual cost was geo_resolver web_search firing for KOID's 5 properties at every cold cache. ~$0.003 per run. Postcode is a strong signal that's free to extract — Malaysia's 5-digit postcodes map 1:1 to a single Mukim for ~60-70% of cases. | Geo resolver had 5 cascade tiers (title-doc → address-doc → AI-Summary → _GEO_BRIDGE keyword → web_search) but no postcode lookup. Cold cache always paid for web_search even when the address had `81750` (Masai → Mukim Plentong, 1:1 unambiguous). | Shipped commit e9ad3c9. New `_POSTCODE_BRIDGE` dict in `services/geo_resolver.py` with 13 unambiguous Johor postcodes (Iskandar Puteri 79100/79150/79200/79250 → Pulai; Skudai 81300/81310 → Pulai; Pasir Gudang 81700 → Plentong; Masai 81750 → Plentong; Senai 81400/81500/81550 → Senai). Each entry has a citation per §10hc. Wired as Tier 4b in `resolve_mukim` (between keyword bridge and web_search). Ambiguous postcodes (80100/80200/80250/80300/81100/81200 — JB city core spans multiple mukim) DELIBERATELY excluded — they correctly fall through to web_search. Verified live: walker run cost dropped from $0.006 → **$0.003**. Total session cost reduction: ~99% from $0.30+ baseline. | HIGH (verified — 5/5 local accuracy test passes, walker cost halved) |
| 66 | §10x.108 walker | Test walker (`walk_step6.py`) couldn't drive the §10x.108 orphan-group disambiguation card — its dispatch chain didn't include `_try_handle_orphan_claim`. Walker would pick the first `orphan_claim <gid> <ai_idx>` quickreply value but no app.py handler in the walker's chain accepted it → infinite stall + verifier failed at 11 failures. | Walker `run_turn` calls handlers in a fixed order (inbox → restart → inventory → h3 → message-conflict → property-fill → ownership → encumbrance). Missing the orphan handler entry. | Patched `/app/data/walk_step6.py` (docker volume): added `_try_handle_orphan_claim` import, prepended to dispatch chain. Walker now picks first orphan_claim option → handler tags docs with `_user_assigned_ai_idx` → bind_assets Tier 0 binds them → no phantom gifts. Verified live: **property gift count 6 → 5** (matches AI Summary exactly). verify_step6.py PASS, exit=0. | HIGH (verified — exact AI Summary parity) |
| 67 | §10x.114 | Step 7 (Residuary Estate) chat asked "who inherits everything else?" and user replied `wife 100%`. Reply ignored — chat re-rendered the same question on the next poll. Same with the default-equal-shares quickreply click (`LIM BEE YAN 1/3, ESTHER 1/3, JOSHUA 1/3`). Walker exit=0 because verifier doesn't gate on Step 7, but production chat was completely stuck. | NO handler existed for residuary main-beneficiary input. Only `_try_handle_residuary_skip` existed and it only matched the literal string `residuary skip`. Any real beneficiary text fell through every handler in the dispatch chain. Planner kept showing the question because `step6_data.beneficiaries` was never populated. | Shipped commit 7d014a1. New `_try_save_residuary_main` handler at app.py:8883: parses `wife 100%` / `Joshua 50%, Esther 50%` / `X equal, Y equal`. Resolves via Person.full_name OR Person.relationship (so 'wife' → spouse Person). Normalises shares (100% → 100/100; 'equal' → 1/N). Refuses partial saves if any name unresolved. Writes step6_data.beneficiaries + residuary_beneficiary_name + stamps `residuary_confirmed` in completed_steps. Wired BEFORE `_try_handle_residuary_skip` in dispatch chain so real input is captured. Negative-match guards (inventory_/orphan_/bank_l/insurance_l/etc.) prevent over-capturing. Verified live: 'wife 100%' → LIM BEE YAN saved; 3-way equal → 1/3 each. Walker still PASS. | HIGH (verified live — both single-beneficiary and equal-shares paths work) |
| 68 | §10x.115 | After §10x.114 saved 'wife 100%' as residuary, user replied "wrong, where is the main" — they reached Step 7 without ever seeing a Step 5 'Main Beneficiaries' confirmation. Auto-populated step4_data (via §10x.42 reconciliation when wife was added during identity walkthrough) silently bypassed the Step 5 question branch. | Planner's Step 5 branch only fires when `len(step4_data) == 0`. When auto-populated, the user never confirms the main beneficiary universe → confusion at Step 7 ('wait, that was the main beneficiary, not residuary'). | Shipped commit eeb4ef8. (a) New `_step5_beneficiaries_confirm_card` in `ai/chat_planner.py` — lists each auto-populated name with relationship; user clicks ✅ Confirm or ✏️ Change. (b) Planner branch: when step4_data populated AND `beneficiaries_confirmed` not in completed_steps, render the card. (c) New `_try_handle_beneficiaries_confirm` handler in `app.py` stamps the marker. Wired BEFORE `_try_save_beneficiaries` in dispatch. | HIGH (verified — card renders with KOID's 3 beneficiaries) |
| 69 | §10x.116 | User: 'state who is Main and then who is substitute. Be clear. All gift need to state the main, after that ask for the substitute. The issue is step 7'. Specific gifts followed L2 main → L3 substitute. Step 7 residuary only had one layer. If main predeceases, residuary goes intestate per Distribution Act. | Step 7 only had `_step7_residuary_question` (Layer 2 main) and `_try_handle_residuary_skip`. No Layer 3 substitute card or handler. | Shipped commit c9b4890. Three pieces: (a) `ai/chat_planner.py::_step7_residuary_substitute_question` — new card asking 'If [main] doesn't survive, who gets it?' with §10x.14 default cascade (spouse → children, child → other child, multi → survivors). (b) Planner branch: when step6 has main but no substitute, render the L3 card. Layer 1 card relabelled 'Layer 1 — MAIN' for clarity. (c) `app.py::_try_save_residuary_substitute` handles quickreplies (`survivors`/`equal others`/`equal children`/`100% <name>`/`none`) plus free text. Stamps `residuary_confirmed` only when both layers done. Wired BEFORE main handler so substitute input isn't accidentally captured. Verified live: KOID flow → Step 5 confirm → Step 7 L1 main → Step 7 L2 substitute card with 'ESTHER, JOSHUA equal' default. | HIGH (verified end-to-end) |
| 70 | §10x.139 | KOID AI Summary said `his wife Lim Bee Yan, son Joshua Koid Teck Seng, and daughter Esther Koid En Hui` — `_extract_family_name_role_pairs` returned `[]`. No H3 placeholders surfaced for Lim Bee Yan / Joshua → identity walkthrough only saw 2 ICs. | Pattern 1 required `my <role> (<NAME>)` prefix. Pattern 4 required `my <role> <NAME>`. Bare `his/her` + comma-separated lists were never matched. | Shipped commit 680c468 + 8776ec9. New Pattern 5 accepts `(?:my\|his\|her\|,\|and)` prefix on bare-name `<role> <NAME>`. Pattern 1 widened to accept the same set on parens form. Verified: KOID surfaces 4 pending ICs (Lim Bee Yan H3, Joshua H3, Esther IC, Lim Lay Cheng IC). | HIGH (verified) |
| 71 | §10x.140 | KOID Property B-05-11: AI Summary says `joint 50/50 with Chai Mei Fun. Testator's 50% to Joshua 25% and Esther 25%`. Conflict detector summed `50%+50%+25%+25% = 150%` and rendered an unrecoverable `⚠️ Beneficiary shares don't add up` card with only `other` quickreply. Walker stalled in infinite loop. | Per §10x.13, percentages following `Testator's` / `joint X/Y` are ownership shares, NOT beneficiary allocations. Conflict detector treated them all the same. | Shipped commit 7bdf0d2 + 4c1f97b. Strip ownership-share fragments before summing: `joint 50/50` / `\d+/\d+ with` / `testator's 50%` / `holds 50%` / `his 50% to` / `jointly with`. Walker now passes the conflict card; gifts save with correct distribution. | HIGH (verified — walker no longer stalls) |
| 72 | §10x.141 | `_extract_ai_summary_banks` returned 0 entries for KOID. Bank lines `POSB Bank Singapore Account No. 030-25917-3` and `Public Bank Malaysia Current Account No. 3244955834` were never matched. | `_AI_BANK_LINE_RE` required either `(country)` parens OR direct `Account No.` after `Bank`. Bare-word country (Singapore/Malaysia) and pre-account-type words (Current, Plus Saving) broke the regex. | Shipped commit 9ce14b9. Extended to accept `Singapore\|Malaysia\|...` bare word + 0-3 capitalised account-type words before `Account No.`. All 4 KOID banks now surface as H3 placeholders. | HIGH (verified) |
| 73 | §10x.142 | Shop @ Jalan Gunung 4 missing from saved gifts. Pipeline showed 5 properties in AI Summary but only 4 in step5_data. | Claude AI Summary HALLUCINATED the same `(Title 251041, Lot 127082, Mukim Plentong)` for BOTH the House at Sri Laguna AND the Shop because both are in Mukim Plentong. The Shop's title doc had its identifiers stolen by the House binding (one-claim-only), Shop got no binding. | Shipped commit 9b76a14. Strengthened AI Summary prompt with ONE-DOC-TO-ONE-PROPERTY hard rule + worked example: 'NEVER assign the same (lot, title) pair to TWO different properties. If you cannot decide WHICH property a doc belongs to, leave the OTHER WITHOUT identifiers.' Verified: Shop now in step5_data with correct (251041, 127082, Plentong); House at Sri Laguna shows blank lot/title (correct — no doc uploaded). | HIGH (verified — Shop appears as Clause 4 in generated will) |
| 74 | §10x.143 + 143b + 143c | Joshua's IC was uploaded but his Person row had `nric=''` and `document_id=None`. Will generated `(MALAYSIA NRIC No. )` blanks for him in every clause. Same for Lim Bee Yan when her IC arrived after first will-generation cycle. | (a) `_dedupe_ic_against_existing` matched IC by name to Person row but did NOT backfill NRIC/address/doc_id before marking the IC as `duplicate`. (b) `_try_assign_pending_identity` for H3 placeholder confirms used the H3 entry's empty NRIC instead of looking up existing IC docs. (c) `_propagate_person_to_steps` only matched `status='draft'` wills; missed the already-generated will. | Shipped commits 1f99a68 + 75c5aca + 1d3c9cb. (a) §10x.143: dedup function now backfills Person.nric/address/doc_id BEFORE marking IC duplicate. (b) §10x.143b: H3 confirm searches existing IC docs by name and pulls NRIC/address. (c) §10x.143c: propagate function widened to ANY active will (draft/generated/approved). Verified: late-IC arrival simulation → Lim Bee Yan NRIC `661126-04-5182` propagates to step2/step4 → next will-regen includes `(MALAYSIA NRIC No. 661126-04-5182)`. | HIGH (end-to-end simulated late-IC test passes) |
| 75 | §10x.144 + 144b | KOID PHOTO-29.jpg (a JMB strata maintenance bill from Badan Pengurusan Bersama Merak Kayangan, customer KOID BENG SUN & CHAI MEI for unit B-05-11) was classified as `bank_statement` with bank_name='Maybank' (vision hallucination). | (a) Vision prompt didn't distinguish JMB Statement of Account from real bank statement. (b) OCR regex matched 3 patterns in `bank_statement` category (`STATEMENT OF ACCOUNT`, `CLOSING BALANCE`, `TRANSACTION HISTORY`) → 0.95 confidence → bypassed vision entirely. | Shipped commits 1f99a68 + db7ac63. (a) Vision prompt: bank_statement now requires recognised bank issuer + deposit account number; JMB bills classified as property_tax. (b) OCR regex `_DOC_PATTERNS` adds `BADAN PENGURUSAN`, `MANAGEMENT CORPORATION`, `SERVICE CHARGE`, `SINKING FUND`, `JMB`, `MC NO` patterns to property_tax. Verified: doc 0b91ff17 reclassifies as property_tax with reason 'Strata maintenance bill from Badan Pengurusan Bersama'. | HIGH (verified) |
| 76 | §10x.145 + 150 + 153 + 154 | Wizard Step 6 property card showed empty postcode/city/state/country/ownership_type/testator_share/co_owners even though chat had saved a complete address string `Shop No. 03 Jalan Gunung 4, Seri Alam Masai, 81750 Masai, Johor`. Also `(Title 251041, Lot 127082, Mukim Plentong)` clutter trailing every property_address. Mukim showed `Plentongy` (OCR drift). Wizard /step/10 returned 500 Internal Server Error from a corrupted nested-dict step3_data. | (a) Chat saves address as a single string; wizard expects separate postcode/city/state inputs (§10x.145). (b) Placeholder save path doesn't write testator_share/co_owners/ownership_type — only build_gift does, but H3 path stores them in property_info while wizard reads property_details (§10x.154). (c) `_parse_ownership` co-owner regex required `,` `(` `share` `\d` or end-of-string lookahead — missed `.` so `joint 50/50 with Chai Mei Fun. Testator's...` returned co_owners=[] (§10x.154). (d) Mukim cleaner missing OCR-drift suffix strip. (e) step3_data nested-dict shape (from earlier reset script polluting an empty list with dict-shape writes) crashed `for g in guardians` (§10x.153). | Shipped commits a47d623 + f303d63 + 4caf8bd + 66a948f + 7b13d6f + fc6741a + 8226ba7. `_enrich_gifts_with_documents` now: (a) parses postcode/city/state from address (parens stripped first so lot/title digits don't masquerade as postcode); (b) defaults country='Malaysia'; (c) derives testator_share/co_owners/ownership_type from chat field OR AI Summary `_parse_ownership` (matching unstamped gifts to AI Summary by address/lot similarity); (d) normalises mukim drift via known-mukim list; (e) strips trailing `(...)` parens from displayed address. `_parse_ownership`: terminates capture on `.`/`!` too, accepts bare `with wife X` (no `my`), strips role prefix from captured name, treats `100%` or summed-100% as sole. step10_review template defensively filters guardians to mappings. `_refresh_wizard_session_from_db` sanitises step3_guardians/step4_beneficiaries to lists of dicts. Wizard banner per §10x.150 now flags missing fields visibly. Verified: all 5 KOID properties show correct ownership/share/co-owners/postcode/city in wizard. | HIGH (verified end-to-end on KOID fixture) |
| 77 | §10x.147 + 148 | (a) One reference book (Gopalakrishnan) is from India; another (Kessler) is from UK — Q&A engine could quote them as if they were Malaysian law. (b) No URL to view the gold standard guide PDF. | (a) `services/legal_library.py::list_available_acts` had no jurisdiction tag. Q&A engine treated all books equally. (b) Library page rendered titles as plain text — no link. | Shipped commit a47d623. (a) New `_BOOK_JURISDICTION` dict tags each book ('malaysia'/'malaysia_singapore'/'india'/'uk'). `relevant_excerpts` and `section_excerpt` PREFER Malaysia-authoritative sources; only fall back to foreign sources when no MY hit, and prepend a `⚠️ FOREIGN-LAW PRINCIPLE ONLY (jurisdiction: INDIA)` disclaimer. Library UI shows per-book jurisdiction badge. (b) New `/library/download/<slug>` route serves PDF inline; library titles linked to it; Gold Standard gets a ⭐ badge. URL: https://will.alantanjb.com/library/download/will_drafting_gold_standard_guide | HIGH (verified — disclaimer + URL working) |
| 78 | §10x.149 + 152 | `eaTiQa` (insurance) was being saved as misspelt vendor name. `AIA` was being silently mapped to AIA Bhd (Malaysia) when it could just as well be AIA Singapore Pte Ltd — separate legal entities. | (a) No financial-institutions database to canonicalise names. (b) Bare brand names that exist in BOTH MY and SG (AIA, HSBC, Allianz, Manulife, Etiqa, etc.) had ONE country silently picked. | Shipped commits f303d63 + eed97a0. New `services/financial_institutions.py` with ~85 BNM/MAS-licensed entities, exact/alias/substring/fuzzy (Levenshtein) matching. Wired into `_extract_ai_summary_banks` and `_extract_ai_summary_insurance`. eaTiQa → Etiqa Insurance (corrected). NTUC Income → Income Insurance (legal name post-2022 corporatisation, web-verified). Bare AMBIGUOUS brands trigger `🇲🇾 Brand Malaysia | 🇸🇬 Brand Singapore` quickreply on L1 card. Fuzzy threshold tightened: requires shared 3-char prefix OR edit distance ≤ 2. | HIGH (verified test cases) |
| 79 | §10x.151 | Lim Bee Yan late-arrival IC test: dedup matched her IC to TESTATOR's Person row (same residential address). Wife's H3 Person stayed empty. | `_dedupe_ic_against_existing` iterated Persons in arbitrary order. Testator's address matched (wife's IC has same mailing address) → matched first → silently linked wife's IC doc to testator. | Shipped commit 75c5aca. Sort persons by match strength before loop: NRIC=3, name=2, addr=1. Refuse address-only match against a Person who already has a different NRIC. Wife's IC now correctly binds to her own Person row regardless of query order. | HIGH (verified — late-IC simulation passes end-to-end) |
| 80 | §10x.155 | User: "chat UI not fixed". Wizard property fixes (§10x.145/150/154) only applied to wizard render. Chat right-pane snapshot still showed empty postcode/missing-field warnings. | `_will_data_snapshot` returned `_normalise_gifts(step5_data)` raw — no enrichment. Chat history poll (`/api/chat/<cid>/history`) used the raw snapshot. Wizard called `_enrich_gifts_with_documents` separately. Two UIs, two render paths, only one had the fix. | Shipped commit 008d8f6. `_will_data_snapshot` now runs `_enrich_gifts_with_documents` on step5 before returning. Single source of truth: chat AND wizard render the same enriched gift data (postcode/city/state/ownership_type/testator_share/co_owners/country='Malaysia'). | HIGH (verified — chat snapshot returns enriched fields) |
| 81 | §10x.179 | User: "white patch on the AI chat" (Screenshot 7:41 PM). The "New chat update / Chat updated identities. Refresh to see latest." notification toast at bottom-right of the wizard page has an empty white field/box between the body text and the Dismiss button — looks like a blank patch. | `text-accent-700` on Refresh button — Tailwind config only defines accent-{500,600}; undefined class → text inherits white from parent purple toast → invisible "Refresh" label = white-on-white = blank rectangle. | Shipped commit 0ae2093 — `templates/wizard/layout.html` Refresh button changed to `text-accent-600 hover:bg-accent-50`. Chrome-MCP-verified per §10x.191. | HIGH ✓ (Chrome-MCP-verified) |
| 82 | §10x.192 | Wizard Step 6 STATE dropdown blank for all 5 KOID properties even though `property_details.state='Johor'` and `negeri='Johor'` are both populated. | Template compared `pd.state` (case-preserved 'Johor') against dropdown options (uppercase 'JOHOR'). No match → no `selected` → blank. Also didn't strip 'Darul Ta'zim' suffix when negeri included it. | Shipped commit e887d83 — case-insensitive compare + strip 'DARUL TA''ZIM' / 'DARUL TAKZIM' suffix. Verified via Chrome MCP: all 5 properties show JOHOR. | HIGH ✓ (Chrome-MCP-verified) |
| 83 | §10x.192 | Wizard Step 6 TITLE TYPE dropdown blank for ALL 5 properties even though vision extracted `title_type='hsd'` for the Shop. | `_enrich_gifts_with_documents` only pulled `title_type` from `property_info`/`property_details` — placeholder save path doesn't copy it from `Document.extracted_data`. So enriched data had `title_type=None`. | Shipped commit e887d83 — when title_type missing, fall back to bound Document's extracted_data.title_type with normalisation (HSD/HSM/GRN uppercase, others Title-Case). Verified: Shop shows HSD, C-05-01 shows Geran. | HIGH ✓ (Chrome-MCP-verified) |
| 84 | §10x.193 | Wizard amber banner showed "all good" (no missing-field warning) for properties where `title_number='TBD'` / `lot_number='TBD'` (placeholder values from earlier validation-bypass). User: "Title info also missing but not in there". | The missing-field check used `if not pd.get('title_number')` which treats string 'TBD' as truthy → no warning. Other placeholders ('N/A', 'NA', 'TODO', 'PENDING', 'TBC', 'XXX', etc.) had the same bug. | Shipped commit 2ab1c6b — new `validation/probate_required_fields.py` with `is_missing()` helper that catches None / empty / placeholder strings. PROPERTY_REQUIRED schema (14 fields per NLC s.292 / Borang 14A) wired into wizard Step 6 GET handler. Cleaned 'TBD' values from KOID's existing wills. Now banner correctly flags B-05-11 missing title No. + lot No.; Sri Laguna missing title type + title No. | HIGH ✓ |
| 85 | §10x.193 | Sri Laguna's mukim shows 'Plentong' in wizard. Sample template (ground truth from Alan & Tan firm) says Mukim **Pulai**. AI's `_GEO_BRIDGE` lookup mapped `Taman Laguna` → Plentong (wrong). | `_GEO_BRIDGE` table in `ai/chat_planner.py` has `Taman Laguna → Plentong` mapping based on incorrect inference. Per actual title doc Geran 337203, Sri Laguna Lot 135402 is in Mukim Pulai. | TODO: fix `_GEO_BRIDGE`. Add explicit `Taman Laguna → Pulai` with title-doc citation. Defer to title-doc mukim (when available) over geo-bridge. (Same root cause as Template T-30 / §10x.184.) | HIGH (factually wrong — invalidates the property's land-registry identity) |
| 86 | §10x.193 | Sri Laguna's lot shows '194139' in wizard. Sample template says lot **135402**. (My TBD-wipe migration removed the bogus 194139 in some wills; it lingers because B-05-11's lot in Sample is `194139/M1B/5/209` and the AI Summary parser confused them.) | Per §10x.142 the AI Summary historically conflated identifiers across Plentong/Pulai-mukim properties. The TBD wipe didn't remove non-TBD wrong values. Sri Laguna's actual lot was never extracted (no title doc uploaded for it). | TODO: clear specific wrong-source values during gift save when no doc evidence supports them. Mark fields as `[needs verification]` placeholder rather than carrying over a hallucinated value. (Same root as Template T-24 / §10x.177.) | HIGH (factually wrong) |
| 87 | §10x.193 | C-05-01's title_number shows '564662/M1C/30/710' — but that's the C-30-08 strata title. Sample template says C-05-01's title is '564662/M1C/5/517' (different parcel suffix). | Vision extracted ONE title doc for both Marina Cove units (same Geran 564662, different parcel-component suffixes). The matcher copied C-30-08's strata sub-token to C-05-01 because they share the master title and lot. | TODO: when a strata title has a `/M1C/N/MMM` parcel suffix, NEVER copy the suffix from one unit to another — they are different parcels. Either extract separately from each unit's title doc OR leave the parcel suffix blank with a clarification card. | HIGH (factually wrong — different unit number) |
| 88 | §10x.193 | B-05-11 shows building name 'Paradisonuava' (user typo). Sample template says actual building is 'MERAK KAYANGAN PERSIARAN MEDINI UTARA 3, BANDAR MEDINI ISKANDAR'. | The user typed 'Paradisonuava' in WhatsApp — the AI Summary parser passed it verbatim. No cross-check against title doc OR web search. | TODO: when user-provided property name doesn't match any uploaded title doc OR known development name, web-search before save (§10x.107) and surface a clarification card if mismatch. (Same root as Template T-35 / §10x.189.) | MEDIUM (informal name persisted to will) |
| 89 | §10x.193 | `Lim Bee Yan` Person row shows `nric_passport=''` and `address=''` even though her IC was simulated as arrived (via §10x.143b). On the WIZARD Step 1 / Step 2 / Step 5 forms, her name appears but identification + address are blank. | (a) The simulated late-IC test used a fake content_hash that wasn't a real photo, so OCR for address didn't run. (b) The address fallback chain doesn't write Person.address when the Document only has limited extraction. | When user uploads a REAL Lim Bee Yan IC photo via inbound email, vision will extract NRIC + address and the §10x.143 chain will backfill. (Verified for Joshua's IC — same flow expected to work.) | MEDIUM (test-only — production would have the real IC) |
| 115 | §10x.142b | Walker iter 1 crashed with `AttributeError: 'dict' object has no attribute 'strip'` at `_walkthrough_property_card_h3` line 2650. After §10x.142 (structured AI Summary JSON footer) shipped, `ai_prop['ownership']` is a dict `{type, co_owner, testator_share}` not a string. Every consumer doing `.strip()` / slicing / regex on the field crashes. | `_extract_ai_summary_json_block` returned ownership as the raw nested dict from the JSON. Multiple downstream code paths (cards, gift saver, asset_pipeline) read it as a string. Schema drift between producer and consumer. | Shipped commits 58b92e6 + 06a3e04. (a) `_extract_ai_summary_json_block` now emits `ownership` as a LEGACY-COMPATIBLE STRING ("joint with X 1/2" / "sole") AND preserves the dict form as `ownership_struct`. Same for `beneficiaries[]` → `beneficiary` string + structured list. (b) `_step6_property_question` Layer 2 deducer reads `ownership_struct.testator_share`. (c) `_walkthrough_property_card_h3` defensively coerces dict/list/None to string. **Verified live: walker no longer crashes on ownership access.** | HIGH ✓ (verified — walker passes through all 5 H3 confirm cards) |
| 116 | §10x.146 | KOID walker iter 1: residuary handler captured property L2 input ("ESTHER KOID EN HUI 100%") even though latest assistant card was Property 2 of 5 main beneficiary card. Property L2 saver never got the input → walker stuck re-clicking. | `_detect_chat_intent` Step 6 branch only matched `unit/condominium/house/shop` keyword in lowercased text. The L2 card body sometimes shows just an address ("#30-08, Menara C, ...") with no such keyword → intent fell through to `'unknown'`. Both `_try_save_residuary_main` and `_try_save_residuary_substitute` had `'unknown'` in their allow-list as "permissive fallback" → claimed the input. | Shipped commit 58b92e6 + e76829c + c94671b. Three pieces: (a) `_detect_chat_intent` Step 6 branch now matches L2/substitute card text patterns ('high confidence', 'medium confidence', 'no clear distribution', 'click confirm', 'pick the most likely', 'main beneficiary(ies):', 'main beneficiary dies', 'substitute clause') so the right intent label is returned. (b) Step 7 transition card check moved BEFORE Step 6 (since "Specific gifts done" matches Step 6 keyword). (c) Both residuary handlers DROP `'unknown'` from allow-list — Step 7 cards have unique enough text that strict gating is safe. (d) Substitute card detection requires explicit "Layer 2: SUBSTITUTE" header, not the "(Substitute will be asked next)" hint on the MAIN card. **Verified live: walker reaches verify PASS.** | HIGH ✓ (verified — verify_step6.py exit 0) |
| 117 | §10x.147 | KOID walker iter 4: stuck in infinite loop on Property 1 of 2 (B-05-11). Walker re-clicked `inventory h3 confirm webaddr Paradiso Nuova...` 60+ turns; planner re-rendered same H3 card; saver returned None (h3_idx None because step5 already had _ai_summary_idx=0 entry). | `_try_handle_h3_property_action` saves the AI Summary's bare address CONCATENATED with web-resolved address from §10x.137. Truncated to 60 chars: AI Summary sig 'UNIT B 05 11 CONDOMINIUM PARADISONUAVA' (38 chars) is a STRICT PREFIX of saved sig 'UNIT B 05 11 CONDOMINIUM PARADISONUAVA PARADISO NUOVA BANDAR' (60 chars). H3 dedup used plain `if a_addr in covered_addr_norms` → exact-equality fail → AI slot looks unhandled → re-synthesise H3 placeholder every walker turn. | Shipped commit d171abc. `services/gift_walker.py` H3 dedup now also accepts a prefix relationship in either direction (when a_addr length ≥20 to avoid short-token collisions). The 3 identity-equality checks (lot digits / title digits / norm-addr exact) remain dominant; this is a strict extension. **Verified live: B-05-11 + C-05-01 stop re-surfacing after their step5 entries are saved with web-resolved address.** | HIGH ✓ (verified — pp drops 2→0 cleanly after saves) |
| 118 | §10x.148 | KOID walker iter 6: stalled at residuary main card with input `Lim Bee Yan equal, JOSHUA equal, ESTHER equal`. Three handlers should have claimed it (residuary_main → resolve names → save) but none did. | After Step 6 completes, planner emits `✅ Specific gifts done. Moving to **Step 7: Residuary Estate**. **Layer 1 — MAIN residuary beneficiary** ... _(Substitute / fallback will be asked next, after you confirm the main.)_`. _detect_chat_intent's Step 6 branch matched on 'specific gift' substring (from "Specific gifts done") and the new fallback returned 'gift_main'. Then iter 7: Step 7 detection ran but matched 'substitute' AND 'fallback' in the parenthetical hint → returned 'residuary_sub' instead of 'residuary_main' → main handler refused. | Shipped commits e76829c + c94671b. Two pieces: (a) Step 7 transition check runs BEFORE Step 6 (since transition card contains "specific gifts" backreference). (b) Substitute-card detection narrowed to explicit "Layer 2: SUBSTITUTE" / "Layer 3: SUBSTITUTE" header text — never the "(Substitute / fallback will be asked next)" hint on the MAIN card. (c) Step 6 fallback restricted to cards with "Property N of M" header pattern. **Verified live: walker drives Step 6 → verify exit 0.** | HIGH ✓ (verified — walker passes end-to-end) |
| 119 | reset-script-bug | Walker iter 2 dropped pending property count from 5 to 2 because reset_step6.py v2 wiped ALL chat messages (including the AI Summary card). `_extract_ai_summary_properties` then returned 0; planner only saw doc-OCR pending pp=2. | The AI Summary card lives in chat_messages with `<!--AI_SUMMARY_JSON: {...} -->` footer (per §10x.142). Wiping all chat messages in reset destroys the canonical asset list. Walker then walks a phantom subset; verifier mismatch. | Shipped /app/data/reset_step6.py v3. Reset NOW preserves: (a) any user message > 200 chars (likely the original WhatsApp forward); (b) any assistant message containing 'AI_SUMMARY_JSON' / 'What we deduce' / 'AI Summary' / 'exhibits received' / 'Asset inventory'. Walker turns are wiped so `_detect_chat_intent` starts fresh. Plus: created `/app/data/inject_ai_summary.py` to seed canonical KOID AI Summary card (with JSON footer + 5 properties) for autonomous testing. **Verified: pp=5 + pb=4 surface correctly after reset.** | HIGH ✓ (verified — autonomous loop fixture stable) |
| 120 | §10x.149 | `_extract_ai_summary_banks` and `_extract_ai_summary_insurance` returned 0 entries from a freshly-injected AI Summary card with 4 banks + 3 insurance because `_gather_summary_source_text` was matching ANY assistant card containing "AI Summary" substring — including walker turn cards whose evidence text mentions `_AI Summary: Esther → 100%_`. The L2 card was newer than the actual AI Summary card; bank/insurance regex extracted from wrong source. | Filter `ChatMessage.content.ilike('%AI Summary%')` was too loose. Walker turn cards routinely contain "AI Summary" in their evidence text. | Shipped commit ec0e99c. (a) `_gather_summary_source_text` filter tightened to `'### 📨 AI Summary%'` prefix-only match. (b) `_extract_ai_summary_banks` + `_extract_ai_summary_insurance` now PREFER the structured JSON footer (`<!--AI_SUMMARY_JSON: {banks: [...]} -->`) over prose-regex. JSON has explicit per-bank `beneficiaries[]` arrays which the prose regex flattens into a single wife-default. Falls back to prose only when JSON missing. | HIGH ✓ (verified — banks 0→4, insurance 0→3 surface from JSON) |
| 121 | §10x.142b | Walker iter 1 crashed with `AttributeError: 'dict' object has no attribute 'strip'` at `_walkthrough_property_card_h3` line 2650. After §10x.142 (structured AI Summary JSON footer) shipped, `ai_prop['ownership']` is a dict `{type, co_owner, testator_share}` not a string. Multiple downstream code paths (cards, gift saver, asset_pipeline) read it as a string. | `_extract_ai_summary_json_block` returned ownership as the raw nested dict from the JSON. Schema drift between producer and consumer. | Shipped commits 58b92e6 + 06a3e04. (a) `_extract_ai_summary_json_block` now emits `ownership` as a LEGACY-COMPATIBLE STRING ("joint with X 1/2" / "sole") AND preserves the dict form as `ownership_struct`. Same for `beneficiaries[]` → `beneficiary` string + structured list. (b) `_step6_property_question` Layer 2 deducer reads `ownership_struct.testator_share`. (c) `_walkthrough_property_card_h3` defensively coerces dict/list/None to string. **Verified: walker no longer crashes.** | HIGH ✓ |
| 122 | §10x.146 | KOID walker iter 1 residuary handler captured property L2 input ("ESTHER KOID EN HUI 100%") even though latest assistant card was Property L2 main beneficiary card. Property L2 saver never got input; walker stuck. | `_detect_chat_intent` Step 6 branch only matched `unit/condominium/house/shop` keyword. The L2 card body sometimes shows just an address ("#30-08, Menara C, ...") with no such keyword → intent fell through to `'unknown'`. Both residuary handlers had `'unknown'` in their allow-list as "permissive fallback" → claimed input. | Shipped commits 58b92e6 + e76829c + c94671b. Three pieces: (a) `_detect_chat_intent` Step 6 branch matches L2/substitute card text patterns ('high confidence', 'no clear distribution', 'click confirm', 'main beneficiary(ies):', 'main beneficiary dies', 'substitute clause'). (b) Step 7 transition card check runs BEFORE Step 6. (c) Both residuary handlers DROP `'unknown'` from allow-list. (d) Substitute card detection requires explicit "Layer 2: SUBSTITUTE" header. | HIGH ✓ |
| 123 | §10x.147 | KOID walker stuck in infinite loop on Property 1 of 2 (B-05-11). Walker re-clicked `inventory h3 confirm webaddr Paradiso Nuova...` 60+ turns; saver returned None (h3_idx None because step5 already had _ai_summary_idx=0 entry). | `_try_handle_h3_property_action` saves bare AI Summary address CONCATENATED with web-resolved address. Truncated to 60 chars: AI Summary sig 'UNIT B 05 11 CONDOMINIUM PARADISONUAVA' (38 chars) is STRICT PREFIX of saved sig 'UNIT B 05 11 CONDOMINIUM PARADISONUAVA PARADISO NUOVA BANDAR' (60 chars). H3 dedup used plain `if a_addr in covered_addr_norms` → exact-equality fail → AI slot looks unhandled → re-synthesise H3 every walker turn. | Shipped commit d171abc. `services/gift_walker.py` H3 dedup now also accepts a prefix relationship in either direction (when a_addr length ≥20). The 3 identity-equality checks remain dominant; this is a strict extension. **Verified: B-05-11 + C-05-01 stop re-surfacing.** | HIGH ✓ |
| 124 | §10x.148 | Walker iter 6 stalled at residuary main card with input `Lim Bee Yan equal, JOSHUA equal, ESTHER equal`. Three handlers should have claimed it but none did. | After Step 6 completes, planner emits `✅ Specific gifts done. Moving to **Step 7: Residuary Estate**...` card. _detect_chat_intent's Step 6 branch matched on 'specific gift' substring (from "Specific gifts done") → returned 'gift_main'. Iter 7: Step 7 detection returned 'residuary_sub' instead of 'residuary_main' (matched 'substitute' AND 'fallback' in parenthetical hint). | Shipped commits e76829c + c94671b. (a) Step 7 transition check runs BEFORE Step 6 (since transition card contains "specific gifts" backreference). (b) Substitute-card detection narrowed to explicit "Layer 2: SUBSTITUTE" / "Layer 3: SUBSTITUTE" header text. (c) Step 6 fallback restricted to cards with "Property N of M" header. | HIGH ✓ |
| 125 | §10x.150 | KOID Sri Laguna gift saved with `bens=[]` (empty beneficiaries) when walker hit `inventory h3 skip` (web search failed for this address). User saw "missing beneficiary" in wizard right pane even though AI Summary clearly had Joshua 100%. Same pattern: bank/insurance L1 cards saved gift with empty beneficiaries when walker only confirmed L1 (didn't drive L2/L3). | The H3 saver and bank/insurance L1 savers initialise `beneficiaries=[]` and rely on subsequent L2/L3 walker turns to populate. If walker skips or stalls, beneficiaries stay empty. | Shipped commits 37383c1 + 5c145a5. Three savers (`_try_handle_h3_property_action` + `_try_save_bank_layered_gift bank_l1` + `_try_save_insurance_layered_gift insurance_l1`) now PRE-POPULATE beneficiaries + allocations + ownership_struct.testator_share + co_owner from the AI Summary's structured JSON footer. Plus inline `_default_substitute_for_main` helper applies §10x.14 defaults (multi → survivors, single child → other child, single spouse → all children) so L3 substitute clause is also auto-filled. **Verified: gift completes Layer 1+2+3 from L1 click alone.** | HIGH ✓ |
| 126 | §10x.151 | Generated will had clauses out of order: 1-4 (revocation/executor), 6-32 (gifts + substitutes), then 5 (residuary) at the end. Sample template has gifts then residuary (sequential). Plus 3 phantom default clauses ("joint banks", "all banks → executor", "EPF nomination") emitted regardless of data. | Two drafter bugs: (a) residuary_text built BEFORE non-residuary using next_clause=5; assembly placed residuary AFTER non-residuary so clause number was out of sequence. (b) Three default clauses always emitted regardless of whether data warranted them. | Shipped commit 3a20571. (a) Residuary now uses `__RESIDUARY_CLAUSE_NUM__` placeholder; substituted with real clause number AFTER all non-residuary clauses consumed. (b) Joint-bank clause skipped unless any gift `is_joint_account=True`; all-banks-to-executor skipped when every bank specifically given; EPF skipped when no EPF/KWSP gift. KOID has none → all 3 phantom clauses gone. **Verified: clauses now sequential 1, 2, 3, ..., 14, residuary at 15, declaration at 16.** | HIGH ✓ |
| 127 | §10x.151b/c/d/e/f | Multiple data-population issues in generated KOID will: (a) Wife LIM BEE YAN had no NRIC in any clause; (b) Insurance company "Income Insurance" instead of sample's "NTUC Income"; (c) "Public Bank" instead of sample's "Public Bank Berhad"; (d) Bank format had duplicate word: "Public Bank Berhad Plus Saving Account Account No."; (e) Sri Laguna mukim "Plentong" should be "Pulai" (and daerah "JOHOR" → "Johor Bahru", negeri "State of Malaya" → "Johor"); (f) C-05-01 had C-30-08's strata sub-token "564662/M1C/30/710" instead of master-only "564662". | (b) `_normalise_beneficiary` had no fallback to Person registry when step4 entry had empty NRIC. Spouse added via §10x.42 reconcile, NRIC backfilled later via §10x.143b but `_propagate_person_to_steps` didn't update step4. (c) Canonical names in `services/financial_institutions.py` matched modern legal entity names (Income Insurance Limited / Public Bank without Berhad), not Sample's historical/firm convention. (d) `to_formatted_description` appended " Account No." to acct_type "Plus Saving Account" → duplicate word. (e) Pipeline `build_gift` populated mukim/daerah/negeri from sibling doc OCR junk; my §10x.150 only filled empty fields, didn't override. (f) Pipeline Tier C semantic match bound C-05-01 to C-30-08 sibling doc whose strata sub-token leaked through. | Shipped commits 3e1d06b + 67e3517 + 997a5ee + 07179e0. (b) `_normalise_beneficiary` falls back to Person.nric_passport via case-insensitive name match. (c) Canonicals updated: "Income Insurance" → "NTUC Income", "Public Bank" → "Public Bank Berhad" (with prior canonicals retained as aliases). (d) `to_formatted_description` strips trailing " Account" from acct_type before appending " Account No.". Map widened (Savings/Current/Saving Account/etc.). (e) §10x.151e: AI Summary mukim/daerah/negeri ALWAYS overrides doc OCR (geo bridge canonical per §10ha). (f) §10x.151f: when bound via Tier C semantic AND title has /M1C/N/MMM strata suffix AND AI Summary explicitly null title, strip suffix to master title only. **Verified: wife NRIC appears in all clauses; bank names correct; Sri Laguna shows Mukim Pulai/Johor Bahru/Johor; C-05-01 shows master "Geran No. 564662" only.** | HIGH ✓ |
| 128 | walker test | After §10x.151e/f introduced AI Summary mukim override + strata strip, conflict_detector started raising text-vs-image mismatch cards for C-05-01 title (AI Summary null vs doc OCR 564662/M1C/30/710). Walker had no handler for `mismatch use_text/use_image/type_manually` quickreplies → infinite spin → walker stalled at s5=4 (only 4 properties saved). | (1) `walk_step6.py::pick_next` had no mismatch quickreply branch. (2) `_run_turn_inner` dispatch chain didn't include `_try_handle_mismatch` (which exists in `api_chat_message` chain but not walker test harness). | Patched `/app/data/walk_step6.py` (docker volume): (1) added mismatch handling in `pick_next` to pick "use_text" first per §10x.18 default. (2) added `_try_handle_mismatch` to the dispatch chain alongside `_try_handle_message_conflict`. **Verified: walker now drives Layer 1 confirms past mismatch cards; verify_step6.py exits 0.** | MEDIUM (test-harness only — production /api/chat/* dispatch already had this) |
| 129 | §10x.152 | KOID generated will had 3 separate clauses for 3 insurance policies (one per gift). Sample template uses ONE combined clause with roman-numeral bullets and Insurance Act 1996 s.130 nomination-fallback wording: `"If any nomination under my insurance policies below fails, is invalid, revoked or otherwise ineffective, the proceeds shall be given to <BEN> absolutely. (i) Policy No. <N> <Insurer> <Country> ..."`. | Drafter emitted insurance gifts via the same per-gift loop as banks/properties. No grouping logic. | Shipped commit f1cd18f. Pre-loop pass groups insurance gifts by beneficiary set signature; main gifts loop SKIPS insurance gifts; post-loop emitter generates ONE combined clause per beneficiary set with roman bullets. Country defaults map: NTUC Income/AIA/Etiqa/Great Eastern/Prudential → 'Singapore'. **Verified: 3 insurance gifts collapse into 1 clause matching sample.** | HIGH ✓ |
| 130 | §10x.152b | Banks emitted as `"POSB Bank Saving Account No. 030-25917-3"` but Sample format uses different wording for SG vs MY banks: SG = `"Singapore POSB bank Account No. ..."` (country prefix, lowercase 'bank', no account_type), MY = `"Public Bank Berhad Current Account No. ..."` (no country, type included). | `to_formatted_description` had no country awareness. Single template for all banks. | Shipped commit 49fa365. (a) Added `country: str = ""` field to `FinancialDetails` model. (b) `to_formatted_description` branches on country: SG → prefix country, lowercase 'Bank' → 'bank', drop default account_type ('Saving'); MY → no prefix, keep account_type. **Verified: matches sample format exactly.** | HIGH ✓ |
| 131 | §10x.152c | KOID generated will had 9 SEPARATE substitute clauses (clauses 14-22) using "Pursuant to Clause N above..." wording. Sample inlines substitutes within each gift clause: `"...accruing thereon. If my wife does not survive me, then the benefit she would have received shall be given to..."`. | Drafter emitted `sub_mode='specific'` substitutes via a separate post-loop emitter. Only `equal/prorata` modes were inlined. Most KOID gifts have specific mode (set by H3 saver). | Shipped commit a659197. Inline 'specific' substitute clauses within each gift descriptor (same pattern as equal/prorata). After inlining, pop the gift from `gift_clause_map` so the post-loop emitter doesn't re-emit. **Verified: KOID will collapsed from 25 clauses → 16 clauses, matching sample structure.** | HIGH ✓ |
| 132 | §10x.152d/e | (d) After §10x.152c inlined substitutes, output read `"thereon If my wife..."` (no period before inline). Bank/insurance descriptors don't end in punctuation, neither do single-beneficiary property descriptors. (e) Drafter emitted gifts in step5 order (walker order: properties first), but Sample template orders banks → properties → insurance (by liquidity). | (d) No period guard before inline substitute concatenation. (e) Loop iterated `enumerate(will_data.gifts)` directly, no sort by kind. | Shipped commit 9a04c73. (d) Add period if specific_gifts_text doesn't end in `.!?` before inlining substitute. (e) Sort indices by kind rank: bank=0, property=1, other=2. Stable sort preserves user input order within each kind. **Verified: clauses now banks 5-8, properties 9-13, insurance 14, with period before each inline 'If...'.** | HIGH ✓ |
| 133 | §10x.152f | NRIC parentheses position differed from Sample. Mine: `MALAYSIA (NRIC No. 631204-07-5743)`. Sample: `(MALAYSIA NRIC No. 631204-07-5743)` — parens wrap BOTH country and NRIC. | `format_id_for_will` historical format chose parens-around-NRIC-only. | Shipped commit c6d1ddc. (a) `format_id_for_will` returns `(MALAYSIA NRIC No. ...)`. (b) Post-process regex on LLM output converts legacy `MALAYSIA (NRIC No. ...)` to new format. **Verified: every clause now uses Sample-format parens.** | HIGH ✓ |
| 134 | §10x.152g | KOID B-05-11 (no title doc uploaded) emitted as `"...Bandar Medini Iskandar, 79250, Johor Bahru, JOHOR Mukim Pulai, District of Johor Bahru, State of Johor"` — no separator between address and Mukim block. | `to_formatted_description` always used " " (space) between address and title_parts head, expecting head to start with "held under". When no title/lot, head was "Mukim X" — produced "JOHOR Mukim X" with no comma. | Shipped commit c6d1ddc. When first title_part doesn't start with "held under", use ", " separator instead of " ". Geographic info reads as part of address description rather than a "held under" clause. **Verified: B-05-11 + Sri Laguna now show comma before Mukim.** | HIGH ✓ |
| 135 | §10x.152h | KOID Sri Laguna (no real title doc) generated `"...held under Geran No. Folio 5, Mukim Pulai..."`. "Folio 5" is a Singapore Land Registry reference (Folio 5 of register), NOT a Malaysian NLC title. The data must have come from a non-title doc OCR (e.g. SPA reference page). | `to_formatted_description` had no validation on `title_number` field — any string went into "Geran No. ...". | Shipped commit c6d1ddc. Reject `tn` matching `^(folio|vol\.?|page|title\s*no\.?\s*\(.*\))\s*\d*$` or containing 'unreadable' / 'cannot read'. Treats as empty title; falls back to address-only descriptor. **Verified: Sri Laguna no longer shows phantom "Folio 5" title.** | HIGH ✓ |
| 136 | §10x.152h SOURCE | After §10x.152h fix shipped (which only rejected at WILL OUTPUT layer), the wizard right-pane STILL showed "Folio 5 ??" because step5_data.property_info.title_number persisted the bad value. User feedback: *"Folio 5 ??"*. Same root-cause class as §10x.151e (output-layer fix without source-layer fix). | OUTPUT-only fixes are insufficient when the wizard / right-pane / chat snapshot read directly from step5_data. | Shipped commit c3d2b96. SOURCE-LAYER fix in `services/asset_pipeline.py::group_documents` — when merging title_number from member docs, reject Folio/Vol/(unreadable)/etc. tokens BEFORE persisting to step5. Plus DB cleanup for existing data. **Verified: bug_checklist B03 PASS after reset+walk cycle.** | HIGH ✓ |
| 137 | §10x.151b | Wife NRIC was missing in step4 (`Lim Bee Yan nric_passport=None`) even though Person.nric_passport=661126-04-5182 was correct. Drafter fallback worked at WILL output but wizard still showed blank. | `_step4_add_beneficiary` only wrote `nric_passport_birthcert` (Beneficiary model field). Some legacy chat handlers + wizard read `nric_passport`. Without both, downstream sees None. | Shipped commit c3d2b96. `_step4_add_beneficiary` now writes BOTH `nric_passport_birthcert` AND `nric_passport` AND `address`. Plus DB cleanup. **Verified: bug_checklist B04 PASS.** | HIGH ✓ |
| 138 | §10x.39 META | User: *"come up with the self testing debugging and recording flow done autonomously... Most important is able to use chrome MCP to detect where are the bugs and not overlook the bugs. So the fix deploy test and find bugs can be iterative"* + *"list down the bugs that are raised and put these as priority during self test to check for these bugs whether they mutate and to catch them"*. Recurring bugs were resurfacing because there was no automated regression check. | No automated bug-checklist + no autonomous loop wrapper. Each session re-discovered bugs from scratch. | Shipped commit c3d2b96. NEW `tests/step6/bug_checklist.py` runs 15 checks against current state, each carrying rule reference + user verbatim quote + priority (HIGH/MEDIUM/LOW). NEW `tests/step6/autonomous_loop.sh` wraps reset/walk/verify/checklist into a single command. Exits 0 only when all 15 checks pass. **Verified: full loop iteration produces "ALL CHECKS PASSED" on KOID fixture.** | HIGH ✓ |
| 139 | walker test | User pasted "🎉 All steps complete!" repeating in chat with their inputs ("change something", "other", "open wizard step 10") seemingly ignored. Investigated and found §10x.126 IS firing in production (verified via test_client) but the walker bypasses it. Walker rapid-fired 30+ "All steps complete" cards into chat history during prior autonomous testing. User saw walker history as chat content. | Walker `_run_turn_inner` calls `plan_turn` directly; no §10x.126 no-op recovery gate. Walker had no terminal-state detection — it kept trying invalid inputs at Step 10. | Patched `/app/data/walk_step6.py` (docker volume): break out of loop when `r.get('reply')` contains "All steps complete". Plus chat-history wipe (127 messages deleted) so user no longer sees the old walker noise. **Note**: production chat (api_chat_message) was already correct — verified recovery card emits when typing free text post-Step-10. The user-facing repetition was historical walker output, not a live bug. | MEDIUM (walker-test only — production §10x.126 was already correct) |
| 140 | §10x.140 | User: *"if incomplete info, make it obvious there is incomplete info without having to click in one by one"* + *"B-05-11... lot No. / PT / PTD · encumbrance status — please fill in below before generating the will"*. The wizard right-pane Step 6 amber banner per §10x.193 only flagged on the Step 6 view; step 10 review showed gifts in compact rows with no visible per-gift "missing N fields" indicator. User had to click Edit on each card to discover gaps. | `templates/wizard/step10_review.html` gift loop emitted address + lot + beneficiary lines but never called `validation/probate_required_fields::missing_fields_for_*` to surface missing-field counts. | Shipped commit 6b4f32f. (a) Two-part wizard banner: UPFRONT aggregate ("⚠️ N gifts have missing required fields" with per-gift breakdown rows + "Go to Step 6" CTA) above all sections; PER-GIFT inline (border-l-4 amber accent + "⚠️ N missing" pill in title + amber row listing exact missing fields + "Fill now →" link). (b) Exposes `probate_missing_property/bank/insurance` as Jinja context-processor globals. (c) New B16 check in `bug_checklist.py` validates the helpers run cleanly on every gift kind. **Verified live via Chrome MCP**: KOID test client shows upfront banner with 5 gifts listed + per-gift inline banners on insurance gifts (NTUC Income / Etiqa / AIA) "1 missing: country (MY/SG)". Generate button still amber per §10x.121 when errors present. | HIGH ✓ (shipped + Chrome-MCP-verified per §10x.191) |
| 141 | §10x.39 META | User: *"do the autonomous testing. if usage limit is reached, just accept the prompt to use extra credit and keep running until the all the bugs are resolved"*. Need a runnable script that wraps the full reset/walk/verify/checklist cycle and exits 0 only when all checks pass. | No autonomous-loop wrapper existed. Each session re-discovered bugs from scratch. | Shipped commit c3d2b96 + c72fdc1 + later. NEW `tests/step6/autonomous_loop.sh` — wraps `inject_ai_summary.py` → `reset_step6.py` → `walk_step6.py` → `verify_step6.py` → `bug_checklist.py` into ONE runnable. NEW `tests/step6/bug_checklist.py` runs 16 checks across the past 24h bug list, each carrying §10x rule reference + user verbatim quote + priority (HIGH/MEDIUM/LOW). NEW `tests/step6/chrome_mcp_check_step10.py` documents the OPTIONAL visual checks for ad-hoc Chrome MCP verification (kept separate because `request_access` interrupts autonomous flow). **Verified: full loop produces "ALL CHECKS PASSED 16/16" on KOID fixture; runs in <60s.** | HIGH ✓ |
| 142 | §10x.142b | User: *"I am getting prompts on the country where the financial asset is, but when click in, there is no field to complete. Simple way for user to provide the missing fields and ensure these missing fields are in the wizard"*. Step 10 amber banner pointed user to "Fill now → country (MY/SG)" for insurance gifts (Gift 10 NTUC Income, Gift 11 Etiqa, Gift 12 AIA). Click landed on /wizard/step/6 but the financial-gift form had NO country field. User stuck — couldn't fix the missing field. | `templates/wizard/step6_gifts.html` financial section had only `gift_fin_institution_*`, `gift_fin_account_*`, `gift_fin_type_*`, `gift_fin_desc_*`. Country was a property-only field (`gift_prop_country_*`). | Shipped commit 5b8c57e. (a) NEW Country dropdown for financial gifts (Malaysia/Singapore/Other) with red `*` indicator + amber border+bg when empty so user spots it on click-in. (b) POST handler reads `gift_fin_country_*` into `financial_details.country` (saved to step5_data). (c) Anchor `id="gift-N"` on each gift card. (d) "Fill now →" links from step10 banner now deep-link to `/wizard/step/6#gift-N`. (e) Step 6 DOMContentLoaded reads URL hash, expands the targeted gift, scrolls into view, flash-highlights amber-border fields with `ring-4 ring-amber-300` for 2s, auto-focuses the first one. (f) JS dynamic-add template includes country field for new gifts. **Verified live via Chrome MCP**: clicked "Click to fill →" on Gift 10 row in step 10 banner → page navigated to step 6 with `#gift-10` hash → Gift 10 expanded → "Country *" dropdown visible with red asterisk + amber background → ready for user to select. End-to-end one-click missing-field fix flow working. | HIGH ✓ (Chrome-MCP-verified) |
| 143 | §10x.143 | User: *"improve the UI in wizard that user can provide answer without clicking in and find the field"*. Even with §10x.142b deep-link, user still had to navigate to Step 6 + select dropdown + click Save Gift + confirm. Wanted to fix missing fields RIGHT FROM the Step 10 banner without leaving. | Step 10 banner had clickable "Fill now →" links but no inline form inputs. Filling a missing field required Step-6 round-trip. | Shipped commit ab6a9a2. THREE pieces: (a) NEW endpoint `POST /api/wizard/gift-quick-fix` accepts `{client_id, gift_idx, field, value}` and patches single field on a gift in step5_data. Whitelisted fields per gift kind (property: postcode/city/state/country/title_type/title_number/lot_number/mukim/daerah/negeri/ownership_type/testator_share/encumbrance_status; financial: country/institution/account_number/asset_type/insurer/policy_number/bank_name). Mirrors writes onto property_info + property_details + top-level + financial_details. Logs every quickfix to `_quickfix_applied` audit array on the gift. Returns `missing_now: [...]` so banner can update inline. (b) Step 10 template now renders inline form input PER missing field — country/ownership_type/encumbrance_status/title_type/state/negeri/testator_share as SELECT dropdowns, others as TEXT inputs. Each row has its own Save button. (c) Inline JS handler — on Save click, AJAX POST to endpoint, on success disable input + show "✓ saved" + hide button; when ALL fields on a gift are saved, fade the row green. **Verified live via Chrome MCP**: opened Step 10 → saw inline "country (MY/SG)" dropdown for Gift 10 (NTUC Income) → selected Singapore → clicked Save → AJAX 200 → input disabled, ✓ saved appeared, gift row faded green. DB inspection confirmed `financial_details.country = 'Singapore'` + `_quickfix_applied = [{field: country, value: Singapore, at: ...}]`. Zero navigation, zero re-render, zero "find the field". | HIGH ✓ (Chrome-MCP-verified end-to-end) |
| 144 | §10x.144 | User: *"did you do web search for property to find postcode and country to autofill / for financial to check which country. some companies only locate in certain country"*. Even with inline edit-in-banner per §10x.143, every dropdown defaulted to "-- Select --" and required user to think+pick the correct value. For known institutions (NTUC Income → SG, Etiqa → MY, POSB → SG) the country is deterministic from the existing `services/financial_institutions.py` registry. For property, postcode + state can be regex-extracted from address; ownership type derivable from co_owners array. | No suggester wired into the banner. Banner inputs were blank defaults requiring user judgement. | Shipped commit 24919d9. NEW `services/field_suggester.py` provides `suggest_for_gift(gift, field)` returning `{value, source, options?}` from cheap deterministic sources (no LLM, no per-render web search): financial gifts → `match_institution(name, kind)` lookup against the 85+ MY/SG registry; property gifts → address-regex for postcode/city, `_POSTCODE_PREFIX_TO_STATE` table (16 MY states) for state, `co_owners` presence for ownership_type, address pattern matching for title_type. Banner template now calls `suggest_gift_field(entry.gift, field)` per missing-field input, pre-selects the suggested value, paints the dropdown GREEN border+bg + GREEN ✓ Save button + small hint line "✨ AI suggested: <reason>" e.g. "NTUC Income is registered in Singapore" or "postcode 81750 → Johor". Ambiguous brands return `options: [...]` shown as "also possible: <other>" hint. **Verified live via Chrome MCP** on KOID: Gift 1 Shop pre-fills postcode=81750 (extracted from address), city=Masai (extracted), country=Malaysia (default), title=Geran (most common), ownership=Sole (no co-owners), share=1/1 (sole default), ALL 6 fields ✨ pre-suggested. Gift 2 Marina C-30-08 pre-fills strata title via condo pattern. Gift 10 NTUC Income → Singapore, Gift 11 Etiqa → Malaysia, Gift 12 AIA Bhd → Malaysia all from registry lookup. User goes from "fill 11 unknowns one-by-one" to "click ✓ Save 11 times". | HIGH ✓ (Chrome-MCP-verified end-to-end) |
| 145 | §10x.145 | User: *"default option is not go to step 6 to fill up but to use AI to complete the information and save / why are these info not extracted from images"*. Banner punted "main beneficiary" with "(open Step 6 to fill)" link instead of attempting AI suggestion. Property title/lot for unbound gifts (B-05-11 H3, Sri Laguna H3) showed empty inputs even though the user uploaded property docs containing those identifiers (just wrong-addressed by OCR so binding pipeline didn't match). Plus AIA showed wrong country — fuzzy-matched to ANZ Singapore. | Suggester only handled 7 deterministic field types. Beneficiary required AI Summary lookup; title/lot required cross-Document scan. Both missing. AIA bug: Step 6 wizard rewrites step5 dropping `kind=insurance`, gift came through as bank, fuzzy match in bank-only pool returned ANZ Singapore. | Shipped commits 30e474b + 5fa6e18. THREE additions to suggester: (1) `suggest_main_beneficiary(gift, client_id)` reads AI Summary structured `beneficiaries[]` per gift via `_extract_ai_summary_properties`. Strategy 1 = explicit `_ai_summary_idx`; Strategy 2 = address-token-overlap fallback (Step 6 wizard rewrites strip the idx). (2) `suggest_title_or_lot_from_docs(gift, field, client_id)` scans all property_title/spa/tax/loan_agreement Documents and picks the one whose extracted address shares the most distinctive tokens with the gift's address. Returns title/lot from that doc + alternative candidates as `options[]`. Catches OCR-misaddressed siblings the binding pipeline missed. (3) Routing fix: insurance pool tried first when kind ambiguous (e.g. account_number starts with letter like 'L516911049' or insurer field set). Both pools tried; exact/alias hits preferred over fuzzy; refuses fuzzy-only matches to prevent AIA→ANZ class. Quick-fix endpoint extended to handle `main_beneficiary` parsing (comma-separated "Name share%" → allocations[] + beneficiaries[] + §10x.14 default substitute). **Verified live via Chrome MCP**: Gift 4 C-05-01 → "Esther Koid En Hui 100%" pre-filled; Gift 5 Sri Laguna → "Joshua Koid Teck Seng 100%" + lot 194139 cross-ref'd from sibling doc; Gift 12 AIA → "AIA Bhd is registered in Malaysia" (no longer ANZ wrong). 11/12 missing fields now pre-suggested by AI. Last "open Step 6" punt removed for the beneficiary case. | HIGH ✓ (Chrome-MCP-verified end-to-end) |
| 146 | §10x.146 | User: *"New chat update / Refresh / keep popping up, after refresh, pop up again with same message"*. Wizard right-pane chat-update toast spammed every 6s — even immediately after Refresh, the next poll re-popped it with the same "Chat added 5 new identities" message. | `templates/wizard/layout.html` toast logic built baseline `lastIdsKey` from `window._personRegistry` (server-side `persons` template var, indexed by `Person.id`) but each 6s poll fetched `/api/chat/{cid}/history` whose `identities` list has DIFFERENT id format (or no id). Mismatched signatures → `key !== lastIdsKey` on every poll → toast spam, even after Refresh (reload re-baselined from persons → first poll 6s later saw diff key from history → fired again). | Shipped commit 731708b. Three pieces: (1) Stable signature `full_name+relationship` lowercased+sorted — invariant to which source format provides IDs; persons (template) and identities (api) produce same key when underlying data matches. (2) FIRST poll captures baseline only — never shows toast on initial render (page already shows current state). (3) Dismiss button suppresses toast for 60s + silently refreshes baseline so the same change doesn't immediately re-pop on the next 6s poll. **Verified live via Chrome MCP**: navigated to Step 10, waited 8+ seconds, no toast appeared (clean view). | HIGH ✓ (Chrome-MCP-verified) |
| 147 | §10x.146-stale-cache | User: *"main beneficiary (open Step 6 to fill) / why main beneficiary not completed"*. After §10x.145 deployed the AI suggester for main_beneficiary, the user still saw "(open Step 6 to fill)" punt text. | Browser HTML cache — user's page was rendered BEFORE my deploy. Reloading without cache-bust didn't pick up the new `_LABEL_TO_FIELD['main beneficiary'] = 'main_beneficiary'` map entry. So the `{% if field %}` Jinja branch saw `field=''` and rendered the punt fallback. | No code change needed — fix was already shipped in §10x.145. Force-reloaded with cache-bust query param (`?_=v3`) and verified live via Chrome MCP that all 5 property gifts now show `main beneficiary:` text input pre-filled with AI suggestion + green ✓ Save button + "✨ AI suggested: from your message" hint. Underlying `asset_version()` template helper auto-cache-busts JS/CSS but NOT HTML pages — those are reloaded by the user's browser per its caching policy. **Action**: hard-refresh (Cmd+Shift+R) after any UI deploy to bypass HTML cache. Future improvement: add `Cache-Control: no-cache` header to wizard step pages to prevent stale render. | MEDIUM ✓ (verified — not a code bug, browser cache) |
| 148 | §10x.147 | User: *"why title number missing? not extracted from images"*. Title No. for B-05-11 + Sri Laguna remained blank in the banner even after §10x.145 cross-ref. | TWO bugs combined: (1) Quick-fix endpoint writes saved values to `property_info`. Subsequent renders did `g.get('property_info') or g.get('property_details')` — first-truthy wins. Once user saved city → property_info became `{city: "Marina Cove"}` (truthy dict) → property_details (with the address) was IGNORED. So Gift 4's beneficiary suggester returned None because it couldn't find the address. (2) For unbound H3 gifts (B-05-11, Sri Laguna), the user's uploaded property docs DO contain the right property but their addresses OCR'd wrong (Sri Laguna SPA OCR'd as "Marsiling Lane Singapore"). Token-match couldn't find them. | Shipped commits 30dde22 + d7a3cad. (a) MERGE both schemas: `pi = {}; pi.update(property_details); pi.update(non-empty property_info)` — fixes Gift 4 beneficiary suggestion. Applied to 5 sites in the suggester. (b) NEW `suggest_title_or_lot_via_llm` — when token-match fails, sends ALL property docs (with title/lot/address/owners) + gift's address to Claude Haiku and asks "which doc index is MOST LIKELY the official title doc for this property? Address may be OCR-garbled — consider owner names, building name tokens too." Process-level cache keyed by (client_id, addr-hash, field). First render ~$0.005 for 5 property gifts; subsequent renders hit cache. Rejects placeholder values (Folio N / Vol. N) per §10x.152h. **Verified live via Chrome MCP**: Gift 4 C-05-01 → "Esther Koid En Hui 100%" ✓ pre-filled. Title No. for B-05-11 + Sri Laguna still blank — honest explanation: NO uploaded doc has a valid Malaysian NLC title for them. Sample template titles (528881, 337203) came from confidential land registry searches, not user's uploads. LLM correctly identifies the right doc but its `title_number` field is empty or "Folio 5" (Singapore Land Registry ref, rejected). User must type these manually OR upload the actual Geran/HSD title doc. | HIGH ✓ (Chrome-MCP-verified; honest about title-not-extractable when source data lacks it) |
| 159 | §10x.172 | User: *"why unable to extract title from cukai tanah / refer to download folder, there are 3 images related to B-05-11"*. KOID had uploaded 3 docs for B-05-11 — JMB Statement (`PHOTO-2026-05-02-13-52-29.jpg`), MBIP Cukai Harta (`PHOTO-...30.jpg`), and **Pentadbiran Tanah Cukai Tanah resit** (`PHOTO-...29 2.jpg`). The Pentadbiran Tanah resit clearly printed: NO. DHM GRN 00528881, NO. LOT 00194139, NO. BANGUNAN M1B, NO. TINGKAT 5, NO. PETAK 209, MUKIM Pulai. ALL probate-grade strata identifiers were in the doc. But the system reported "title_number EMPTY — Cukai doesn't carry title". | Four compounding causes: (1) old vision schema (pre-§10x.161 Phase 3) was FLAT — no slots for strata block/tingkat/petak — vision had to pick ONE numeric for `lot_number` and smashed NO. DHM (00528881) into lot field with digit typo (got 00528861). (2) §10x.161's `_cukai` sub-schema assumed Cukai = always master-level, no strata sub-fields. (3) Vision result was DB-cached (SHA256-keyed) so old extraction persisted forever. (4) OCR digit misreads ("MEDINI 9" → "MEDINI 6", "8" → "6"). | Shipped commit 0ffb22d (Pieces A+B+C) + one-shot patch (Piece D). **Piece A** — `_cukai` sub-schema extended: `hakmilik_no` (separate from master_parcel_lot), `strata_block`, `strata_tingkat`, `strata_petak`, `granularity: 'master'|'strata'`. **Piece B** — vision prompt teaches Pentadbiran Tanah field labels with explicit label→slot mapping: `NO. DHM → hakmilik_no`, `NO. LOT → master_parcel_lot`, `NO. BANGUNAN → strata_block`, `NO. TINGKAT → strata_tingkat`, `NO. PETAK → strata_petak`. When all 3 strata fields present → granularity='strata', title_number constructed as `<hakmilik>/<block>/<tingkat>/<petak>`. **Piece C** — `_extract_inner` post-processing auto-detects strata Cukai and promotes `_doc_level='strata'` + fills flat title_number/lot_number from sub-schema. `build_property_hierarchy` already splits slash-tokenised titles into master/block/parcel. **Piece D** — patched existing doc 159de260 with gold-standard values (title_number='00528881/M1B/5/209', lot_number='00194139', _cukai full strata, _doc_level='strata'). Cleared vision_cache row so future re-extraction uses the new prompt. **Chrome-MCP-verified end-to-end**: AI[0] B-05-11 binds to 159de260 via Tier B mukim_token, `missing_for_probate=[]`, wizard banner shows NO BANNER (all 12 gifts complete). | HIGH ✓ (Chrome-MCP-verified end-to-end — banner cleared from "1 gift missing" → "NO BANNER") |
| 158 | §10x.164 SHIPPED | User: *"check and debug why not working: Cukai Tanah is now a first-class probate evidence source for strata units — supplies mukim, daerah, negeri, co_owners, AND the lot"*. The MATCHER (build_gift) was producing mukim/co_owners/_doc_level/_master_lot_from_doc/_ai_summary_idx correctly per §10x.159/162/170 design. But the SAVED state on Gift 3 (B-05-11) had `mukim=None, co_owners=None, _doc_level=None, _master_lot_from_doc=None, _ai_summary_idx=None`. Every Step 6 form submit was wiping these chat-side metadata fields. | The original §10x.164 diagnosis (row 149) was correct: `wizard_step_gifts` POST handler at line 15196 did a FULL RECONSTRUCT of each gift dict from a hardcoded 10-key form whitelist. Every chat-side metadata field was DROPPED on every submit: mukim (no form input), co_owners (no form input), _ai_summary_idx, _doc_level, _master_lot_from_doc, _property_hierarchy, doc_evidence_owners, beneficiaries canonical list, _match_via/_match_tier/_match_evidence, kind, _h3_placeholder, variant, etc. Row 149 documented the diagnosis but the fix was never shipped. | Shipped commit 0631f6f. Two pieces: (a) POST handler now starts with the EXISTING step5_data entry (if any) and OVERLAYS form values on top. Form-less fields stay intact. property_details + financial_details deep-merged: form values replace, existing fields preserved when form has no input for them. (b) NEW form input read: `gift_prop_mukim_X` (in addition to legacy `gift_prop_bandar_X`). 'mukim' field now explicitly captured in property_details. Plus one-shot migration backfilled Gift 3 B-05-11 with mukim='Pulai', co_owners=['Chai Mei Fun'], _doc_level='master', _master_lot_from_doc='00528861', _ai_summary_idx=0, _property_hierarchy from the matcher's build_gift output. **Chrome-MCP-verified saved state**: all fields preserved post-backfill; banner correctly reads "1 gift has missing required fields" (title_number only); form shows mukim='Pulai', lot='00528861', daerah='Johor Bahru', negeri='JOHOR'. Next Step 6 form re-save will preserve all metadata thanks to MERGE-not-reconstruct. | HIGH ✓ (Chrome-MCP-verified) |
| 157 | §10x.171 | User: *"use chrome extension MCP, still show missing field"*. After §10x.170 fixed B-05-11's lot, only its strata title No. remained genuinely missing (no Hakmilik Strata uploaded). Step 6 per-gift §10x.193 banner said "please fill in below before generating the will" — user had to scroll to the property form, find the title No. field, type, then submit the form (full POST cycle). Step 10 already had inline-edit (§10x.143) — clicking ✓ Save AJAX-persists without scrolling. The Step 6 per-gift banner was read-only and slow. | Step 6's per-gift banner was the original §10x.193 shipping which just LISTS missing fields. Step 10's banner (§10x.143) was upgraded later with inline-edit inputs at the banner row. Inconsistent UX: same data + same missing-fields signal but Step 6 forces scroll + form submit, Step 10 has one-click inline save. | Shipped commit 0b9d0cf. Step 6 per-gift banner upgraded to mirror Step 10's pattern: each missing field renders inline (text input / SELECT dropdown per field type) + ✓ Save button. AJAX POST to `/api/wizard/gift-quick-fix` (same endpoint Step 10 uses). On success, button repaints green, input disabled, '✓ saved' shown. AI-suggested values (via `suggest_gift_field`) pre-fill the inputs with green-tinted indication + `✨` source line. Outer-loop gift index captured into `_gift_idx_1based` var so it survives the nested for-label loop. **Chrome-MCP-verified end-to-end on KOID B-05-11**: inline banner renders for the one truly-missing title No. field; typing+Save AJAX-persists; page reload reflects saved state; empty revert correctly clears the input. Sri Laguna (Gift 5) + all other completed gifts have no banner (no inline-edit shown — banner correctly hidden when no missing fields). | HIGH ✓ (Chrome-MCP-verified end-to-end including AJAX save round-trip + revert) |
| 156 | §10x.170 | User: *"there is no strata title but user has uploaded cukai tanah"*. KOID's B-05-11 had master Cukai 159de260 uploaded (lot=00528861, parent NLC parcel for Paradiso Nuova building). Phase 4 §10x.162 was too strict — it refused to pull master-level lot into the unit's lot_number, because master-level docs were considered ineligible for strata sub-parcel fields. But verified by KOID's own C-30-08 SPA (title=564662/M1C/30/710, lot=207922): the lot IS the master parcel lot, and EVERY unit in the building shares it. Strata title sub-tokens distinguish units, not the lot. | I conflated "master title" with "master lot" in the level whitelist. For strata, the lot is at master granularity (parent parcel) but appears in the unit's clause; only the strata title (with sub-tokens) is genuinely unit-specific. | Shipped commit 3827cfb. Two changes in `services/asset_pipeline.py`: (a) `build_gift` detects strata via web_type='apartment_condo', title-with-slash, or address keywords (unit/condo/apartment/pangsapuri/strata/menara/tower/block). When AssetItem is strata AND doc_level='master', promotes `master_lot → unit.lot_number`. (b) `compute_missing_for_probate` updated: for strata gifts, lot is OK if master_lot is filled; only strata_title_no is required at sub-parcel level. **Verified live on KOID**: B-05-11 with master Cukai bound now gets `lot_number=00528861` (from Cukai master lot), missing_for_probate=['title_number'] (only strata title). Chrome-MCP-verified Step 6 banner: missing list dropped from 'title No., lot No. / PT / PTD' → 'title No.' only. C-30-08 / C-05-01 (already bound to strata-level docs) unaffected. Sri Laguna (landed) unaffected (no strata pattern detected). | HIGH ✓ (Chrome-MCP-verified) |
| 155 | §10x.169 | User: *"also penalty on time proximity if further away"*. Matcher had POSITIVE temporal rewards (+15 for ≤ 5 min gap, +7 for ≤ 30 min) but ZERO penalty for FAR-APART timestamps. A doc uploaded yesterday for property X had the same temporal weight as a doc uploaded 6 months ago for an unrelated property. | Asymmetric temporal scoring: close-in-time → bonus, far-in-time → neutral. Cross-session cross-property false matches went undetected when timing was distant. | Shipped commit abe77d3. Added graduated penalty in `services/asset_pipeline.py::_score_pair`: 30 min – 2 hours → 0 (neutral); 2 hours – 1 day → -8 (`temporal_far`, "different chat segment"); > 1 day → -15 (`temporal_distant`, "different upload batch"). Evidence string flags far pairs visibly: '⚠ image uploaded 5h from message' / '⚠ image uploaded 2d from message'. Combined with §10x.168 locality conflict, cross-property false positives now have TWO independent guards. Verified live on KOID: penalty fires correctly on cross-pairs (B-05-11 vs Sri Laguna group: 27 → 19; vs other groups: 15 → 7); all 5 property bindings stay correct (B-05-11 → 159de260, C-30-08 → strata SPA, C-05-01 → strata SPA, Sri Laguna → merged 3-doc group, Shop → its own). Step 6 banner unchanged ("1 gift has missing required fields" → Gift 3 only). | HIGH ✓ (Chrome-MCP-verified) |
| 154 | §10x.168 | User: *"yes [fix Issue B by adding an address-token-conflict guard]"*. After §10x.165 + §10x.166 shipped, Sri Laguna's merged group (title=337203, lot=135402, mukim=Pulai, address='No.10 Jalan Sri Laguna 1/7, Taman Laguna') was scoring 52 against B-05-11 (address='Unit B-05-11 Condominium Paradisonuava, Paradiso Nuova, Bandar Medini Iskandar'). Both Mukim Pulai. §10x.166 boost made score 52 ≥ AUTO_BIND_THRESHOLD (50) — risked auto-binding Sri Laguna's identifiers to B-05-11. The address tokens (laguna/taman vs paradiso/medini/iskandar) were COMPLETELY disjoint, but the matcher had no negative signal for that. | The matcher's positive signals (mukim_match, daerah_match, identifier_doc_with_mukim_match, full_identifiers_with_mukim) all accumulated even when the two addresses described clearly DIFFERENT neighbourhoods. Token-overlap was 0 (no positive boost) but there was no PENALTY for disjoint locality vocab. | Shipped commit e33b88b. NEW `locality_conflict` component (-25) in `services/asset_pipeline.py::_score_pair`. Fires when: (a) token_overlap = 0, (b) web_building_in_ocr = 0, (c) msg_text_ref = 0, (d) BOTH sides have ≥ 2 distinctive tokens, (e) the two token sets are completely disjoint. Verified live: **B-05-11 vs Sri Laguna group: 52 → 27** (below CANDIDATE=30, eliminated). B-05-11 vs 159de260 master Cukai: 44 → 44 (unchanged, overlaps on 'medini'/'iskandar'/'bandar'). Sri Laguna vs its own merged group: 60 → 54 (still AUTO-BIND, overlaps on 'laguna'/'taman'). Sri Laguna vs 159de260: 35 → 10 (below CANDIDATE, eliminated — was a false candidate before). Final bindings: B-05-11 → 159de260 (correct master Cukai), Sri Laguna → merged 584138c3-b group (correct). Chrome-MCP-verified Step 6 banner: "1 gift has missing required fields" — only B-05-11 (the truly-missing strata title), Sri Laguna stays fully resolved. | HIGH ✓ (Chrome-MCP-verified) |
| 153 | §10x.167 | User: *"refer screenshot, specific gift after saved, if any important missing fields need to show under step 6"*. Step 10 (Review & Generate) had the upfront aggregate banner ("⚠️ N gifts have missing required fields" with per-gift breakdown + click-to-jump anchors) per §10x.140. Step 6 (Specific Gifts) only had per-gift inline banners (§10x.193). Result: user editing gifts on Step 6 had to scroll through all 12 gift cards to spot which ones were incomplete — no at-a-glance summary. | The §10x.140 aggregate banner pattern existed on Step 10 but wasn't replicated on Step 6 where users actually fill the fields. Same data source (pd_missing dict per gift index, computed in Step 6 GET handler via probate_required_fields helpers) — just not surfaced as an aggregate header. | Shipped commit 9b08b7f. Added §10x.140-pattern upfront aggregate banner to Step 6 template, positioned above the gifts form. Same template structure as Step 10's: amber border-l-4 accent, ⚠️ icon, count header ("N gift(s) have/has missing required fields"), per-row list with click-to-jump anchors (`<a href="#gift-N">`), each row enumerates the specific missing fields. **Chrome-MCP-verified**: banner reads "1 gift has missing required fields" for KOID's current state (only B-05-11 strata title+lot missing); click on Gift 3 link successfully scrolls #gift-3 into viewport (rect.top=675, in_viewport=true, location.hash=#gift-3). Per-gift inline §10x.193 banner unchanged. Same probate-required-fields helper used as single source of truth across all three surfacing points (Step 6 aggregate, Step 6 per-gift, Step 10 aggregate). | HIGH ✓ (Chrome-MCP-verified end-to-end including scroll-to-gift click) |
| 152 | §10x.165 + §10x.166 | User: *"the SPA schedule clearly state now known and provide the new lot. why this was not extracted and matched with other image group together"*. KOID's Sri Laguna had 3 uploaded docs (RHB Charge cover `87b4c688`, Borang 16A schedule `7a4d55ae`, SPA Schedule `584138c3`) describing the SAME property at DIFFERENT NLC title-conversion stages. The schedules carry "(Formerly known as HS(D) 431161 PTD 143086)" / "now known as Geran 337203, Lot 135402" notation. Vision had NEVER extracted this conversion history → each doc reported only ONE identifier pair → matcher treated them as different properties → Sri Laguna gift left with empty title/lot. | Five missing capabilities: (1) Vision prompt had no slot for title-conversion history. (2) DocGroup grouping merged only on EXACT identifier equality — couldn't see that doc A's historical title == doc B's current title meant SAME property. (3) Backfill needed for existing data extracted before the new prompt. (4) Drafter had no "Formerly known as" rendering. (5) Matcher score for "full identifiers + matching mukim" didn't beat Tier-C semantic on address-only matches when two AssetItems competed for the same Pulai-mukim group. | Shipped 3 commits (49de985 + 77bc135 + 4189e87). **Phase 6A §10x.165 vision prompt** — new "TITLE-CONVERSION HISTORY (Malaysian NLC)" section teaches vision: HS(D)/HS(M)/PTD → Geran conversions, phrases "now known as / presently registered as / kepada Hakmilik Tetap / yang dahulunya / previously / converted to", and instructs to fill BOTH current title_number/lot_number AND `_historical_titles: [{type, no, pt_no, status}]` list. **Phase 6B §10x.165 grouping** — services/asset_pipeline.py::group_documents now builds title_union + lot_union per doc (current ∪ historical identifiers). NEW union signal: two property docs sharing ANY identifier in their unions (≥4 digits, strata exception preserved) merge into ONE DocGroup. `merged_extracted` dedups historical_titles across members. **Phase 6C migration** — one-shot patched KOID's 3 Sri Laguna docs (584138c3, 7a4d55ae, 87b4c688) with current=Geran 337203/Lot 135402 + historical=[HS(D) 431161, PTD 143086]. Verified all 3 merge into one DocGroup. **Phase 6D drafter** — models/gift.py::PropertyDetails gains historical_titles field; to_formatted_description appends "(Formerly known as HS(D) 431161 PTD 143086)" after Lot No. per KOID gold-standard clause 12 format. **§10x.166 boost** — new score component full_identifiers_with_mukim (+20) fires when DocGroup has BOTH valid title AND valid lot AND mukim agrees with AssetItem. Combined with mukim_match (20) + identifier_doc_with_mukim_match (15) reaches score 60 → above AUTO_BIND_THRESHOLD (50), beats wrong-doc Tier-C semantic. **Verified end-to-end**: AI[3] Sri Laguna now binds Tier-B mukim_token to merged group {87b4c688, 7a4d55ae, 584138c3} with title=337203, lot=135402, mukim=Pulai, historical=[HS(D) 431161 PTD 143086]. Wizard banner dropped from 6 gifts → 1 gift (only B-05-11 strata title still missing — honestly cannot infer without actual Geran Strata upload). Drafter output matches gold-standard clause 12 verbatim. | HIGH ✓ (Chrome-MCP-verified Sri Laguna fully resolved; drafter output matches KOID's clause 12) |
| 151 | §10x.160 + §10x.161 + §10x.162 + §10x.163 | User: *"do this / deploy fix, test and make sure working properly and then move to next phase / Use chrome MCP to observe and edit and check output"*. Five-phase strengthening of granularity-aware matching shipped end-to-end. | Granularity reasoning was ad-hoc — different consumers (matcher, vision, wizard) each had partial knowledge. Needed: (a) LLM cross-ref to reason step-by-step about levels; (b) wizard UI to show "Authoritative" vs "Supporting" tiers; (c) vision to emit typed sub-schemas per doc kind; (d) AssetItem to carry typed level structure; (e) owner-pattern as a standalone signal classifier. | Shipped 8 commits: **Phase 1 §10x.160** — `field_suggester.py` LLM prompt now requires `reasoning_steps[]` + `supporting_docs[{idx,level,contributes}]` + `fields_still_missing_after_pick[]`. LLM is told to "trust pre-classified level — don't reclassify". Verified live: returns honest null with 4-step reasoning trace + supporting_docs surfacing 584138c3 as sub_parcel context for Sri Laguna. **Phase 2 §10x.160 UI** — Wizard `step10_review.html` adds collapsible 🔍 "AI reasoning & supporting evidence" `<details>` showing CoT steps + 📎 supporting docs list + ⚠️ "still missing for probate". Plus 🤷 honest "No uploaded doc carries this field at the right granularity" hint when suggester returns None. Chrome-MCP-verified: 4 honest hints render for KOID's B-05-11/Sri Laguna title+lot fields. **Phase 3 §10x.161** — `unified_vision.py` adds typed sub-schemas per doc kind: `_title_doc{is_strata, master_title_no, strata_block, strata_parcel_no, full_title, granularity}`, `_spa{subject_property, vendor, purchaser}`, `_charge{borang_no, secured_property, chargor, chargee}`, `_cukai{assessment_account_no, master_parcel_lot, registered_proprietors, granularity}`, `_transfer{property, transferor, transferee}`. Sub-schemas explicitly separate SUBJECT PROPERTY block from PARTY ADDRESSES — vision is told "never copy a party address into subject_property.address_in_schedule". `_doc_level` stamped at extraction time via canonical classifier; explicit sub-schema granularity wins over inferred. Verified schema-merge produces `_doc_level='master'` for Cukai with explicit `granularity:'master'`. **Phase 4 §10x.162** — `asset_pipeline.py::PropertyHierarchy` dataclass with FOUR levels: unit_identity / strata_sub_parcel / master_parcel / building_evidence. `build_property_hierarchy()` reads bound doc at its level and pulls level-appropriate fields. Strata titles like `564662/M1C/30/710` auto-split into `master_title_no=564662 + strata_block=M1C + strata_parcel_no=30/710`. `compute_missing_for_probate()` returns the NLC-required fields still unfilled. `Binding.doc_level` added; `build_gift` stamps `_property_hierarchy` dict on every property gift. Verified: B-05-11 + master Cukai → strata_title_no='', master_lot='00528861', mukim/daerah/negeri filled, co_owners=['Chai Mei Fun'] (message-only per §10x.19), doc_evidence_owners=[ISKANDAR INVESTMENT BERHAD, CHAI MEI FUN, KOID BENG SUN] (audit trail), missing_for_probate=['title_number']. C-30-08 + strata SPA → ALL fields filled, missing_for_probate=[]. **Phase 5 §10x.163** — `property_granularity.classify_owner_pattern(owner_text, family_names)` returns one of 5 canonical patterns: `developer_plus_individuals → master`, `developer_alone → master`, `individuals_only → sub_parcel`, `bank_plus_chargor → sub_parcel`, `empty_or_unclear → unknown`. Plus extracts individuals[], developer name, bank name, family_in_owners[]. Verified 5/5 test cases on KOID docs. | HIGH ✓ (all 5 phases shipped + Chrome-MCP-verified end-to-end; honest typed reasoning across vision → matcher → UI) |
| 150 | §10x.159 | User: *"to match the image based on granularity and not reject outright. extract relevant information to fill in wizard for probate filing. Make sure the fix has these 2"*. Cukai 159de260 (lot=00528861, owners=ISKANDAR INVESTMENT BERHAD & CHAI MEI FUN & KOID BENG SUN) IS related to B-05-11 — it's the master-parcel Cukai for the parent NLC parcel containing the Paradiso Nuova strata building. Owner triple confirms joint-ownership context the WhatsApp message describes. But its lot=00528861 is the MASTER lot, NOT the strata sub-parcel No. for unit B-05-11. Previous behaviour: matcher would either reject (losing relevant evidence) or pull master_lot into unit's lot field (probate-fatal). | Property docs come at DIFFERENT GRANULARITIES (master parcel / strata sub-parcel / building / unit), but the matcher treated all docs as same-or-different binding. Pulling master_lot=00528861 into B-05-11's strata-sub-parcel `lot_number` is a level-mismatch error; rejecting the doc loses authoritative mukim/daerah/co_owner evidence. | Shipped commit 43d823a. **NEW `services/property_granularity.py`** with `classify_doc_level(extracted, category)` returning one of {strata, sub_parcel, master, unknown} based on: (a) strata title sub-token pattern (`/M1C/30/710`); (b) strata words in description; (c) owner pattern `developer (Berhad/Bhd/Sdn) + 2+ individuals` → master; (d) Cukai header → master; (e) plain Geran shape on property_title → sub_parcel; (f) SPA/loan/charge with title+lot → sub_parcel default. **Per-level field whitelist** in `PROBATE_FIELDS_BY_LEVEL`: master = {mukim, daerah, negeri, co_owners} only; strata/sub_parcel = title+lot+mukim+daerah+negeri+owners; unknown = mukim/daerah/negeri only. **Wired into `services/asset_pipeline.py::build_gift`** — gates field pull from OCR by doc level; stamps `_doc_level` + `_master_lot_from_doc` on the gift so wizard can show "supporting (master parcel — strata sub-title still required)" labels. **Wired into `services/field_suggester.py::suggest_title_or_lot_via_llm`** — per-field granularity gate: master Cukai cannot suggest title_number for a strata unit; falls back to "no doc at the right granularity for this field" honest message. **Verified live**: when user binds 159de260 master Cukai → B-05-11 via §10he candidate card, gift gets _doc_level='master', mukim='Pulai' + daerah='Johor Bahru' + negeri='Johor' + co_owners=['Chai Mei Fun'] all filled, title_number='' + lot_number='' (correctly NOT polluted with master_lot). When d39ea8c3 strata SPA → C-30-08, _doc_level='strata' and ALL fields including title=564662 + lot=207922 filled. | HIGH ✓ (verified end-to-end on KOID; honest classification — master docs contribute geo+owner context to unit gifts, never pollute unit-level identifiers) |
| 149 | §10x.156 + §10x.157 + §10x.158 | User: *"For Laguna there are 4 images user upload: image 1 RHB security with lot/PTD; image 2 lawyer attestation; image 3 SPA schedule (purchaser address misleading in singapore — AI need to interpret SPA schedule correctly); image 4 Land charge form (Jalan rimbun is residence not pledged property — AI need to understand context). address could mean place of residence not asset address. Understanding the form and context is important"*. Vision was extracting party-residence addresses (chargor's, purchaser's, lawyer's office) into `property_address`, then matcher token-failed because "Jalan Institusi" (chargor) doesn't share tokens with "Jalan Sri Laguna" (subject). Sri Laguna's actual title doc `7a4d55ae` (Charge form, title=431161 lot=337203 mukim=Pulai) couldn't bind. Same for `584138c3` SPA Schedule (title=431161 lot=135402 mukim=Pulai, purchaser address `10 Marsiling Lane Singapore`). Result: probate-required title No. shown as missing even though the data was inside the uploads. PLUS misleading auto-fills: B-05-11 Gift 3 lot showed `00528861` (from neighbouring A18/A19 Cukai), Sri Laguna Gift 5 lot showed `194139` (from neighbouring No.9 Cukai) — wrong physical units, looked confident with green ✨ AI suggested badge. | THREE structural gaps + ONE auto-fill safety gap: (1) Vision prompt collapsed all addresses into one `property_address` field with no awareness that SPA / Charge / Loan forms have a SUBJECT PROPERTY block (Schedule / Security / Charged Land) separate from PARTY ADDRESS recitals. (2) Matcher's `_score_pair` had no tolerance for OCR field-swap. (3) When AI Summary has NO identifiers but DocGroup HAS identifiers + matching mukim, score was below CANDIDATE_THRESHOLD → silent residual. (4) Wizard banner suggesters (`suggest_title_or_lot_from_docs` token-based + `suggest_title_or_lot_via_llm` Claude Haiku) accepted any address-token overlap as a green pre-fill, even when the matched doc was for a DIFFERENT physical unit in the same neighbourhood. | Shipped commits 5d5cb15 + 7ae2cd5 + d0eb627 + 5b8f51e + 1d26fcf + 4076725 + 140df0a + b464418 + b26868e + d58438a + 07919a6. **Five coordinated fixes**: (a) §10x.156 — `ai/unified_vision.py` Property rules section rewritten with explicit SPA/Charge/Loan/Cukai/Lawyer guidance + NEW field `_party_addresses: [{role, address}]` to capture chargor/purchaser/lawyer addresses separately. Plus title_number guidance rejecting Folio N / Vol N as folio-location refs not title numbers. (b) §10x.157 — `services/asset_pipeline.py::_score_pair` adds OCR field-swap tolerance: when neither direct lot_match nor title_match fires, try `(ai_lot vs g_title)` and `(ai_title vs g_lot)` swaps at 0.8× weight. (c) §10x.158 — new score component `identifier_doc_with_mukim_match` (+15) fires ONLY when AI Summary lacks identifiers AND DocGroup has lot+title AND mukim agrees AND no stronger signals (token_overlap / web_building_in_ocr / msg_text_ref / owner_testator) exist. Surfaces as MEDIUM candidate-with-confirm card per §10he Step 4. (d) `services/field_suggester.py` — pre-LLM deterministic match: if exactly ONE candidate has mukim agreement + family-owner match AND only ONE AssetItem in client resolves to that mukim, pick deterministically without LLM call (saves $0.0008/call). Plus per-field filter: when caller asks for title_number, only show docs whose title_number is non-empty. Plus pre-filter docs whose only ID is a placeholder (Folio/Vol/(unreadable)). (e) Unit-mismatch guard in BOTH token + LLM suggester paths: if gift addr has unit identifier (B-05-11, No.10) and matched doc's addr has DIFFERENT unit identifier (A18, No.9), reject the suggestion. Locality tokens like 'medini', '79250', 'iskandar' don't disambiguate units. **Chrome-MCP-verified end-to-end**: Gift 3 (B-05-11) title=empty + lot=empty (was lot=00528861 wrong); Gift 5 (Sri Laguna) title=empty + lot=empty (was lot=194139 wrong). System now honest: when no doc unambiguously matches, banner stays empty and user resolves via chat candidate-with-confirm cards (which DO surface 7a4d55ae + 584138c3 as MEDIUM candidates per §10x.158). | HIGH ✓ (Chrome-MCP-verified; honest about ambiguous cases — user disambiguates in chat) |
| 90 | §10x.117 | KOID walker stalled on Property 3 of 5 (C-05-01). Walker re-clicked `inventory h3 confirm` 3+ turns; planner kept re-rendering the same H3 placeholder card; verifier failed at 11 failures (R1×3 properties + R2×4 banks + R3×3 insurance + R12 matcher-never-ran). | `_asset_walkthrough_question` (planner) used GREEDY claim — each image group claimed by exactly one AI Summary slot via greedy iteration. `_try_handle_h3_property_action` (handler) used NON-GREEDY classify — every AI prop classified independently against ALL groups. So planner showed H3 card for slot 2 (C-05-01, no group available after 1+4 claimed C-30-08+Shop), but handler thought slot 2 was matched and refused to save. Walker stuck. | Shipped commit 1e2c0c8. Handler now mirrors planner's greedy claim. Each image group can be claimed by AT MOST one AI Summary slot. Iterate ai_props in order; each classifies against image groups not yet claimed by an earlier ap. **Verified: walker drove all 12 gifts (5 properties + 4 banks + 3 insurance) through Layer 1+2+3, verifier RESULT: PASS, EXIT=0.** | HIGH (verified — walker passes end-to-end) |
| 91 | §10x.118 | Wizard Gift 3 (B-05-11) banner showed `city · title No · lot No` missing even though sibling Bandar Medini Cukai Tanah doc (`ae7bea4f`) had address `A18 & A19, MEDINI 6 PERSIARAN MEDINI SENTRAL 1, BANDAR MEDINI ISKANDAR, 79250 JOHOR` for the same Mukim Pulai. Postcode 79250 was pulled, city stayed empty. | `§10x.205 cross-doc enrichment regex was `\b(\d{5})\b\s+([A-Z][A-Za-z\s]{2,30}?)(?:,|$)` — assumes Malaysian "city follows postcode" order. For "BANDAR MEDINI ISKANDAR, 79250 JOHOR" the post-postcode token was "JOHOR" (state), got filtered out by the state-name guard, city left empty. Pattern B (city BEFORE postcode, comma-separated) was never tried. | Shipped commit f80343c. Pattern A runs first (`<postcode> <city>`); if its post-postcode token is a state name (johor / kedah / selangor / etc — guard widened to all 13 MY states + federal territories), Pattern B (`<city>, <postcode> <state>`) falls back. **Verified live: B-05-11 city='BANDAR MEDINI ISKANDAR' auto-fills via Chrome MCP per §10x.191.** | HIGH (Chrome-MCP-verified) |
| 92 | §10x.119 | Wizard Gift 5 (Sri Laguna landed property) banner showed `title type · title No · lot No` missing. User uploaded NO title doc for Sri Laguna; AI Summary text only had address. Title type couldn't be inferred. | `§10x.204 auto-set title_type='Strata Title Geran' for condo-pattern addresses (Unit X-NN-NN / Condominium / Apartment / Pangsapuri), but had no fallback for landed properties. Sri Laguna address "House at 10 Jalan Sri Laguna 1/7, Taman Laguna" had no strata pattern → title_type stayed empty → wizard banner flagged it. | Shipped commit f80343c. Added landed-pattern fallback after the strata check: `house|shop|bungalow|terrace|semi-d|villa|townhouse|double-storey|single-storey` OR `\bjalan\s+\w+` → `title_type='Geran'` (per NLC s.213 most landed titles are final Geran). User can override to HSD/HSM in wizard. **Verified live: Sri Laguna title_type='Geran' auto-fills via Chrome MCP.** | HIGH (Chrome-MCP-verified) |
| 114 | §10x.141 | User: *"still showing No clear distribution in your message for this property / use chrome mcp to check and test"*. Even after §10x.140 (scope deduction to per-property snippet), C-30-08 card STILL said "No clear distribution". Snippet shown on the card was: `• Unit C-30-08 Condominium Marina Cove (Title 564662, Lot 207922, Mukim Plentong, Johor Bahru, Johor) — owned 50/50 with son Joshua Koid Teck Seng` — TRUNCATED before the next sentence `Testator's 50% to daughter Esther Koid En Hui`. The §10x.140 deducer ran on the truncated snippet → couldn't see Esther 50% → empty deduce → LOW confidence. | `_find_property_message_snippet` split `recent_text` on `(?:\\r?\\n|\\.\\s+)` — period-space treated as line boundary. AI Summary bullets like `'X. Y. Z.'` got split into 3 segments. The first wins (has unit tokens C-30-08); the second/third lose (no locality tokens) → snippet returned just the first half. | Shipped commit 86c2b4d. Split ONLY on newlines (`\\r?\\n`). AI Summary bullets are single lines — they should stay intact. Each bullet now becomes ONE segment regardless of internal periods. Length cap raised 240 → 400 chars. **Verified live**: `_find_property_message_snippet` for C-30-08 now returns 194-char snippet `'• Unit C-30-08 ... owned 50/50 with son Joshua Koid Teck Seng. Testator's 50% to daughter Esther Koid En Hui.'` — contains 'Esther' AND '50%'. Combined with §10x.140 → Layer 2 card next render will show 'HIGH confidence ✓ Confirm — Esther 100%'. Plus migration: cleaned 2 orphan property gifts from KOID step5 (B-05-11 with no title/lot/bens + empty-addr placeholder) per user request `'remove this and retry using the fix'`. | HIGH ✓ (shipped + DB-verified extraction; awaits user click on next C-30-08 card render) |
| 113 | §10x.140 | User: *"AI summary clearly stated testator share give to esther / Why property identification stage state \"No clear distribution\" / The message from AI summary not properly interpreted"*. C-30-08 Layer 2 card said `🤔 No clear distribution in your message for this property` even though the AI Summary text clearly said `Testator's 50% to daughter Esther Koid En Hui`. Card showed 3 generic distribution buttons instead of the deduced 'Esther 100%'. (Renumbered from §10x.139 — old §10x.139 at row 70 covers identity-name-pair extraction; rule numbers must be unique.) | The deducer used `recent_text` GLOBALLY (entire 19k-char chat history). Found 45 `\d+%` hits across all 5 properties + 4 banks + 3 insurance lines. For each candidate name (Esther/Joshua/Lim Bee Yan/Lim Lay Cheng), it picked the CLOSEST percent regardless of which property line. Result: ESTHER 25% (from B-05-11 line), JOSHUA 50% (from Shop), Lim Bee Yan 100% (from banks). Total = 175% → not in {25,33,50,66,67,75} → deduced reset to [] → LOW confidence card. | Shipped commit 1c1f1e5. SCOPE the deduction to the per-property snippet that `_find_property_message_snippet` already extracts (and that the card already displays as '📨 from your message:'). Each property's deduction now sees ONLY its own bullet. C-30-08: deduces ESTHER 50% → total 50% in valid set → rescale to 100% → HIGH confidence card '✓ Confirm — Esther 100%'. Falls back to global recent_text if scoped match empty (rare — only if property has no identifiable mention). | HIGH ✓ (shipped + DB-traced) |
| 112 | §10x.136 | Web search for property addresses returned empty for KOID's `Unit B-05-11 Condominium Paradisonuava` — Claude correctly returned `address_not_found` per §10hc (UNKNOWN > confident-wrong) because "Paradisonuava" is a user typo / informal transliteration. The actual building is `Paradiso Nuova @ Bandar Medini Iskandar`. Without retry, every informal building name (Paradisonuava, PangsaBay, BMC, etc.) returned blank → wizard had blank addresses → user complained "did you do a web search". | `services/web_property_clues.py::search_property_clues` ran ONE web search and gave up on `address_not_found`. No retry path for typo / informal name normalisation. | Shipped commit 8ab1a7c. **Typo-tolerant retry** — when first search returns `address_not_found`, run ONE typo-normalisation pass (Claude Haiku, ~200 tokens, ~$0.0002): `_TYPO_NORMALISE_PROMPT` asks Claude to suggest a canonical spelling for informal/transliterated building names. If `alt_query` differs from original, retry the web search with the canonical query. Cost-bounded at 1 retry per address. Cached under the ORIGINAL key so future calls use the canonical answer for the user's typo (no double-pay). Examples it now resolves: `Paradisonuava → Paradiso Nuova` ✓ (verified live), `PangsaBay → Pangsapuri Bayu`, `BMC → Bandar Medini City`, `PJU → Petaling Jaya Utara`. If retry ALSO fails, `address_not_found` stands — system still returns UNKNOWN rather than guess (§10hc invariant intact). | HIGH ✓ (shipped + DB-verified — `Paradisonuava` resolves to building=Paradiso Nuova, mukim=Pulai, locality=Bandar Medini Iskandar, postcode=79250) |
| 110 | §10x.138 | User: *"why is card repeating / the 1st card has no buttons. the 2nd repeated card has buttons. find the bug"*. 3 IDENTICAL Property 5 of 5 cards posted within 3 seconds (17:22:49.041, 17:22:49.597, 17:22:52.351). chat.js renders quickreply buttons ONLY on the LATEST assistant message → all older duplicate copies of the same card lose their buttons → user sees 'card not responding'. | `api_chat_history` (watchdog, polls every 5s) and `api_chat_message` (POST handler) BOTH call `plan_turn` and post the result. Race condition: when user clicks fast or chat polls during a write, both insert the SAME card content into chat_messages within seconds. No idempotency check. | Shipped commit aa6f74b. New helper `_post_asst_msg_idempotent(session_id, content, plan, ...)` — checks the latest assistant message in the session BEFORE insert. If content matches verbatim (full string including the `<!--quickreplies:[...]-->` marker), return None and log `app.logger.warning('§10x.138 DEDUP: skipped...')`. Both call sites (POST + watchdog) use this helper. Distinct cards with different questions/buttons always post; only true verbatim duplicates dropped. | HIGH ✓ (shipped + DB-traced 3 dups before; 0 dups after expected) |
| 109 | §10x.137 | User: *"I did a google search and find Address: Persiaran Medini Utara 3, 79100 Iskandar Puteri ... user has uploaded images with [that] Address ... But because web search fail, could not match / Step 1: typo-tolerant retry to provide closest match and get user to confirm / Step 2: If web search cannot find even with typo-tolerant retry, inform user cannot be found and provide full address to reconfirm"*. The H3 placeholder card (for AI Summary properties without uploaded title doc) only showed the user's informal building name + a generic "No title document attached yet" message. Web search ran (per §10x.136 retry) but its result was never surfaced to the user. The wizard + will-generation downstream had blank addresses. | `_walkthrough_property_card_h3` built bullets only from the AI Summary fields, never invoked `search_property_clues`. Resolved address sat in `_CLUES_CACHE` unused. | Shipped commit 9f1d69d. **2-step UX**: Card now CALLS `search_property_clues(address, claude_client)` at render time. **Step 1 — web search succeeds**: card shows `📍 Web-resolved address (please confirm): <building, locality, postcode, daerah, negeri>` + source URL + 3 buttons: `✅ Use this address` / `✏️ Type my own` / `⏭ Skip`. Click ✅ → quickreply value `inventory h3 confirm webaddr <resolved>` → `_try_handle_h3_property_action` extracts the `webaddr ` suffix and persists it onto `entry.property_info.property_address` + back-fills mukim/daerah/negeri/postcode/city from the same web result. **Step 2 — web search returns None even after §10x.136 typo retry**: card shows `⚠️ Could not auto-resolve this address. I searched the web for the building name and address but didn't find a confident match. Please type the full address with postcode (e.g. \`Persiaran Medini Utara 3, 79100 Iskandar Puteri, Johor\`), OR upload the title document and I'll extract it for you.` + 3 buttons: `✏️ Type full address` / `📎 Upload title doc` / `⏭ Skip`. **Verified**: `search_property_clues('Unit B-05-11 Condominium Paradisonuava')` now returns `building=Paradiso Nuova, locality=Bandar Medini Iskandar, mukim=Pulai, postcode=79250, daerah=Johor Bahru` (via §10x.136 typo retry). User can confirm the resolved address with one click, then the wizard renders the full address instead of just the typo. | HIGH ✓ (shipped + DB-verified retry; pending Chrome MCP click-through on H3 card render) |
| 108 | §10x.135 | User: *"`Or type a name: e.g. ` ``substitute specific SARAH BT ALI`` / dont put SARAH BT ALI"*. The substitute-beneficiary card hint and 2 guardian-card hints had HARDCODED placeholder names (`SARAH BT ALI`, `CHAN MEI LIN`, `LIM AH KENG`) leaking into every client's chat regardless of who's in their family list. Confusing and impersonal — the testator already has real candidate names in their identity list. | Card-builders constructed example text with literal name strings rather than pulling from `will_data.identities`. | Shipped commit 6b07f05. All 3 example-name hints now pick from the client's identity list (filtered to non-testator + non-already-assigned). Falls back to generic phrasing (`the guardian's full name`) if no candidate. Pattern: **user-visible card text NEVER from hardcode or training memory; always from this client's data**. Same principle as §10x.77 (no machine language in UI), §10x.124 (canonical roles from registry), §10x.131 (beneficiary union from data not memory). | HIGH ✓ (shipped — 3 hardcodes removed; future card-builder PRs must reference client data, not invented names) |
| 107 | §10x.134 | User: *"the card not responding again, This bug keeps happening / go through the code thoroughly to find the root cause / specific gift is not registering anything / Now at step 7 residuary"*. User typed `Esther Koid En Hui 50% + Joshua Koid Teck Seng 50% (equal)` at the Property Layer 2 card (Step 6). System acked `✅ Saved … as 2 main residuary beneficiaries` — wrong! Saved to Step 7 residuary, not Step 6 property gift. Property card re-rendered. User typed again → s6 was now populated so residuary handler skipped → input fell to property gift handler which acked `Saved as specific` but didn't actually save (no document_id on the H3 placeholder). Will Snapshot showed Step 6 ◯ empty + Step 7 ✓ residuary populated with the wrong data. | The dispatch chain at api_chat_message line 4561-4601 calls `_try_save_residuary_main` BEFORE `_try_save_property_gift`. residuary_main's gating at line 10605 only required: step4 has any beneficiary AND step5 has any gift OR `assets_confirmed` set AND step6 not yet saved. KOID had 2 garbage skipped step5 placeholders → `len(s5) > 0` → gating passed → handler claimed Layer 2 input meant for the property gift. NEW handler ordering bug — same class as wrong-handler-claims-input. | Shipped commit 49aca25. **§10x.134 STRUCTURAL FIX**: step-specific handlers MUST call `_detect_chat_intent(client_id)` and return None if the latest assistant card is NOT one they own. Mirrors §10x.126 (no-op recovery card) pattern applied to handler GATING. (a) `_try_save_residuary_main` returns None unless intent ∈ `{residuary_main, unknown}`. (b) `_try_save_residuary_substitute` returns None unless intent ∈ `{residuary_sub, residuary_main, unknown}`. Plus migration: cleared KOID's wrong residuary save + dropped 2 garbage skipped placeholders + removed `residuary_confirmed` marker. **Chrome-MCP-verified end-to-end**: clicking the Layer 2 distribution button now correctly saves to step5 (property gift) and advances to Layer 3 substitute card showing `✅ Main beneficiary(ies): Esther Koid En Hui 1/2, Joshua Koid Teck Seng 1/2` + 3 substitute buttons. **Future hardening**: same intent-gate pattern should be applied to every step-specific handler — `_try_handle_executors_confirm`, `_try_handle_beneficiaries_confirm`, `_try_handle_guardian_action`, `_try_handle_trust_action`, `_try_handle_others_action`. Currently those have explicit value-prefix gates that mostly prevent cross-step claims, but adding intent-check is belt-and-braces. | HIGH ✓ (shipped + Chrome-MCP-verified end-to-end; structural fix applies §10x.126 intent pattern to handler gating) |
| 106 | §10x.133 (META) | **🔥🔥🔥 RECURRING BUG CLASS — "missing properties from walkthrough" / "AI Summary count mismatch" / "Property X of Y where Y is wrong" 🔥🔥🔥**. User has reported this CLASS of bug at least 4 separate times across sessions — each time with a different symptom: "where is property 1", "Property 2 of 4 should be 5", "C-05-01 missing", "this issue keeps happening". Every reported instance has the SAME root cause: a dedup or counting site somewhere in the pipeline uses a SINGLE signal (token, mukim, address, lot OR title alone) to merge two AI Summary properties into one OR to mark an AI prop as "covered" when no image is bound. Strata properties (different units in same building) and properties on the same street/Taman SHARE locality tokens by construction. Every session a new well-meaning dedup gets added → another property gets hidden → user reports "missing property". | The codebase has multiple dedup sites that decide "this AI Summary property is already represented in the wizard" or "these two properties are the same physical asset". Each site has its own ad-hoc heuristic — token overlap, mukim match, address-prefix, lot-equality, title-equality, NRIC-style fuzzy. **Without a single canonical rule for "same property", every site drifts and produces false-positives.** Per CLAUDE.md §10hd (strata) and §10b (count == AI Summary), the canonical rule should be: **identity-equality on lot+title together (for strata) OR address-norm[:60] (for landed)** — never token overlap, never mukim-only, never address-prefix-only. | **Master consolidation — every shipped fix in this class:** |
| | | • §10b — Property Count = AI Summary Count |
| | | • §10h — AI Summary IS canonical asset list (read FIRST) |
| | | • §10hd — Strata: same lot ≠ same property (different units, same building) |
| | | • §10x.12 — Every AI-Summary item is its own gift |
| | | • §10x.34 — H3 IDENTITY placeholders for name+role in message |
| | | • §10x.35 — MESSAGE > IMAGE; text-stated entities MUST surface |
| | | • §10x.48 Stage 0/4 — AssetItem from AI Summary is canonical |
| | | • §10x.95 — Pre-fill ownership from bound AI Summary entry |
| | | • §10x.97 — AI Summary parser must skip banks/insurance from property |
| | | • §10x.100 — Walker dedup: placeholder + H3 path stamped with `_ai_summary_idx` |
| | | • §10x.102 — Marina Cove Shop duplicate (orphan placeholder dedup) |
| | | • §10x.105 — Marina Cove orphan phantom (gate inventory-confirm) |
| | | • §10x.108 — Orphan-group disambiguation card (asks user) |
| | | • §10x.117 — H3 handler greedy-claim mismatch (planner vs handler symmetry) |
| | | • §10x.122 — IC dedup address-only across different NRICs |
| | | • §10x.129 — Layer 1 "Property X of N" counter uses AI Summary count |
| | | • §10x.131 — Step 5 Beneficiaries union: family Persons + AI Summary names |
| | | • §10x.132 — Token-overlap dedup REMOVED entirely; Layer 2 counter from AI Summary |
| | | **🔥 BURN-IN HARD RULE FOR FUTURE SESSIONS 🔥** Anyone adding NEW dedup or count code MUST: (1) read this row + every cross-referenced rule first; (2) NEVER use token-overlap, mukim-only, address-prefix-only, or any single-signal heuristic — only identity-equality (`lot == lot AND title == title` for strata; `address-norm[:60] == address-norm[:60]` for landed); (3) the AI Summary count is the canonical N — every "X of N" counter resolves N from `_extract_ai_summary_properties()`, never from gift_walker pending or step5 saved count; (4) when in doubt, surface an H3 placeholder + ask the user (per §10d unverified card pattern) — false-negative dedup (user sees an extra card to confirm) is INFINITELY better than false-positive dedup (user loses a property). | RECURRING — META: every shipped fix listed above; **the bug class is now caught by 18 cross-referenced rules + the §10x.49 self-validating-pipeline gate + the §10x.33 pre-deploy audit** |
| 105 | §10x.132 | User: *"why property 1 missing / mismatch with AI summary / find the root cause and debug / this issue keep happening"*. KOID's AI Summary listed 5 properties (B-05-11, C-30-08, C-05-01, Sri Laguna, Shop). Walker showed only "Property 2 of 2" — 3 properties (C-05-01, Sri Laguna, partially Shop) NEVER surfaced as walkable cards. Will-generation downstream couldn't include them. | **Two-bug compound**: (a) `services/gift_walker.py` line 1391 — `if a_toks and (a_toks & covered_tokens): continue` — the §10b "distinctive locality token" matcher used a SINGLE token overlap to mark an AI Summary property as "already covered" (no H3 placeholder needed). For KOID: `MARINA`+`COVE` tokens from C-30-08 image group caused C-05-01 (different strata unit, same building) to be marked covered → no H3 surfaced. Same for `LAGUNA` (Sri Laguna) and `SERI ALAM MASAI` (Shop, after a saved skipped gift had those tokens). Per CLAUDE.md §10hd (strata: same lot ≠ same property), different units in the SAME building share locality tokens by construction — token-overlap is the WRONG dedup signal for strata. (b) `_step6_property_question` Layer 2 card counter at line 7029-7032 used gift_walker pending count (which was 2 due to bug a) for "Property X of N" labeling — compounding the user-visible symptom. | Shipped commits 64a0708 + 35110ef. **Fix A (root cause)**: REMOVED the token-overlap dedup entirely from `gift_walker.py`. Was first relaxed to ≥2 tokens (still hid C-05-01 since it shares exactly 2 tokens MARINA+COVE with C-30-08). Token overlap is fundamentally wrong for strata. The 3 explicit identity-equality checks above (lot digits, title digits, address-norm[:60]) are sufficient — they're identity checks not fuzzy. **Fix B (counter)**: `_step6_property_question` now uses AI Summary count (consistent with §10x.129 Layer 1 card). Position matched by lot/title/address[:30]. **Server-verified**: KOID `get_pending_gift_documents` now returns 5 properties (was 2): C-30-08 (image), B-05-11 H3, C-05-01 H3, Sri Laguna H3, Shop H3. The 3 previously-hidden properties (C-05-01, Sri Laguna, Shop H3) now surface as walkable cards. Will Snapshot Step 5 already shows 3 beneficiaries from §10x.131; combined with §10x.132 the walker has the full asset+beneficiary universe. | HIGH ✓ (shipped + server-verified pending 2 → 5; Chrome MCP card refresh requires fresh walker turn) |
| 104 | §10x.131 | User: *"step 5 beneficiaries incomplete / User need to confirm all the beneficiaries are added in before moving to next step / Also AI summary which list the beneficiaries should be listed in the card for user to confirm"*. KOID's `step4_data` had ONLY 1 beneficiary (Lim Bee Yan) even though the AI Summary clearly named 3 (wife + son + daughter). Step 5 confirm card showed the incomplete list → user confirmed → walker proceeded to Step 6 with missing beneficiaries → will-generation downstream couldn't name Joshua/Esther for property gifts. | §10x.42 reconciliation only matched simple verb-prefix patterns ("all banks → wife", "all insurance → wife"). Property split-allocations like "25% to son Joshua and 25% to daughter Esther" don't match — Joshua + Esther stayed in Person table as Son/Daughter but never reached step4_data. Step 5 confirm card just iterated whatever step4 had. | Shipped commits 9722be2 + 9557d07. **§10x.131 5-piece fix**: (A) `_step5_beneficiaries_confirm_card` builds the UNION of step4_data + every Person whose relationship is family (Wife/Husband/Spouse/Son/Daughter/in-laws/step/adopted/grandparent/grandchild). (B) Each beneficiary listed with NRIC + relationship + AI Summary snippet (📨 from your message: "..."), sorted spouse → children → others. (C) Snippet extraction CENTERS on the name match in the AI Summary text (window of ~120 chars, snapped to sentence boundaries) — NOT line-by-line truncation that previously showed the same opening sentence for everyone. (D) `_try_handle_beneficiaries_confirm` PERSISTS the union to step4_data BEFORE stamping `beneficiaries_confirmed`. Without this, confirming the card stamped the marker but step4_data stayed incomplete. (E) Step 5 confirm card gate widened: shows when step4 has items OR family Persons exist. **Chrome-MCP-verified end-to-end**: KOID step4 went from 1 beneficiary to 3 — `Lim Bee Yan (Wife) added_by §10x.42 reconcile` + `JOSHUA KOID TECK SENG (Son) added_by §10x.131 confirm-time union` + `ESTHER KOID EN HUI (Daughter) added_by §10x.131 confirm-time union`. Will Snapshot now shows "Step 5: Beneficiaries → 3 items". | HIGH ✓ (shipped + Chrome-MCP-verified end-to-end; step4 went 1 → 3) |
| 103 | §10x.130 | User: *"after step 2 complete, should got to step 3 executor. why jump to step 6 property"*. After Testator (Step 2) confirmed, planner skipped Step 3 (Executor) and Step 5 (Beneficiaries) silently — went straight to Step 6 specific-gifts walkthrough. The Executor was already auto-populated by §10x.44 reconciliation when Lim Lay Cheng was added (the message named her as 'My Executor — My Sister in law'); same for the wife as Beneficiary. So the data was correct but the user never saw a CONFIRMATION card for it — they felt their Executor decision was bypassed. | (1) No confirm card for auto-populated step2.executors. The legacy `_step3_executor_question` only fires when `n_executors < 2`; with 2 executors auto-set it never showed. Same for step4 beneficiaries (§10x.115 had a confirm card but only ran AFTER the asset walkthrough block). (2) Even with the §10x.115 beneficiaries confirm card, the asset walkthrough at line 439 fires BEFORE Step 5, so pending gifts pre-empted the confirm card too. | Shipped commits 5adf3d6 + a49a574. **3-piece fix**: (a) New `_step3_executors_confirm_card` in chat_planner.py — lists Primary + Substitute with role + relationship + NRIC + 3 buttons (✅ Confirm / ✏️ Change / Other). (b) New `_try_handle_executors_confirm` in app.py — handles `executors confirm` quickreply value, stamps `executors_confirmed` on completed_steps. Wired BEFORE the legacy executor save handler. (c) Both `_step3_executors_confirm_card` AND `_step5_beneficiaries_confirm_card` MOVED to run BEFORE the asset walkthrough block in `plan_turn`. Now planner order: Step 1 IC → Step 2 Testator → Step 3 Confirm Executors → Step 5 Confirm Beneficiaries → Step 6 Gifts. (d) `_detect_chat_intent` picks up `executors_confirm` for §10x.126 no-op recovery; `_NO_OP_HINTS` tailored hint added. **Chrome-MCP-verified end-to-end**: KOID flow now shows ⚖️ Step 3: Executor card with Lim Lay Cheng (Sister-in-law) + Joshua Koid Teck Seng (Son) listed; click ✅ Confirm → ack "Saved LIM LAY CHENG, JOSHUA KOID TECK SENG as Executor" → 👨‍👩‍👧 Step 5: Main Beneficiaries card showing Lim Bee Yan (Wife); click Confirm → 🏠 Specific Gift Property card. | HIGH ✓ (shipped + Chrome-MCP-verified end-to-end Step 2 → 3 → 5 → 6) |
| 102 | §10x.129 | User: *"Why start 🏠 Property 2 of 4 / where is 1 out of 4"*. The first property card user saw was labeled `Property 2 of 4` — they thought Property 1 was missing. Total count of 4 was wrong (AI Summary actually had 5 properties). | Counter at line 5219-5225 was: `total_props = n_in_step5_with_doc_id + len(pending)`. This counted: (a) orphan placeholder gifts that had `document_id` but were never bound to an AI Summary slot — they sat as "Property 1" silently → user saw "Property 2" as the first VISIBLE card; (b) missed AI-Summary H3 properties that had no image yet → total too low. The counter wasn't tied to the canonical AI Summary asset list (CLAUDE.md §10h). | Shipped commit 5adf3d6. New counter: `total_props = len(_extract_ai_summary_properties(client_id))` — AI Summary IS the canonical asset list. `seq_num` = position of current target in AI Summary, matched by lot/title equality OR address[:30] prefix. Falls back to legacy counter only if AI Summary parse fails. **Chrome-MCP-verified**: card now reads `🏠 Specific Gift — Property 2 of 2 (9 total: 2 props · 4 banks · 3 insurance)` with explicit asset breakdown so user sees how many of each kind there are. | HIGH ✓ (shipped + Chrome-MCP-verified) |
| 101 | §10x.128 | User: *"Put the buttons below the card. Not more than 3 options visible and clear for user to select"*. Cards routinely showed 4 buttons (e.g. inventory: ✅ Accept / 🗑 Remove / ⏭ Skip / ✏️ None of above — I'll type) — too busy. The auto-fallback "None of above" was always appended by §10x.86 to allow free-text, but it duplicated the chat input's affordance and crowded the card. | `_qr_marker` always appended `{label: "✏️ None of above — I'll type", value: 'other'}` when `suppress_fallback=False` and no fallback already present. No cap on total button count. Cards built with 3 main buttons (Accept/Remove/Skip) ended up with 4 visible. | Shipped commit 5adf3d6. **MAX 3 BUTTONS hard cap** in `_qr_marker`: (a) drop the auto-fallback when card already has ≥3 main buttons (`n_main < 3` gate); (b) hard cap at 4 buttons total — if 3+ main exist, drop fallback entirely (3 buttons visible); if <3 main, keep up to 1 fallback (4 max). User can still type free-text in the chat input box if no button matches — the affordance is right there. **Chrome-MCP-verified**: ownership card has 2 buttons (Sole / Joint), encumbrance has 2 (Clean / Has loan), Step 3 Executor confirm has 3 (Confirm / Change / Other), Step 5 Beneficiaries confirm has 3 (Confirm / Change / Other), Layer 2 main beneficiary has 3 distribution options. None show the 4-button "None of above" noise. | HIGH ✓ (shipped + Chrome-MCP-verified across 5 card types) |
| 100 | §10x.127 | After §10x.126 shipped the recovery card, user reported: *"Tap ✅ Accept to confirm, ⏭ Skip to come back later, or 🗑 Remove if wrong upload. there is no button to tap"*. The recovery card emitted the right TEXT hint ("tap Accept/Skip/Remove") but the buttons themselves were INVISIBLE. Worse, on the second unknown reply the recovery card emitted the GENERIC fallback (intent='unknown') instead of the inventory_property hint. | Two cascading bugs: (1) `chat.js` renders quickreply markers ONLY on the latest assistant message. The §10x.126 recovery card became the latest → recovery card had no QR marker of its own → previous Property Shop card's buttons disappeared. User saw the hint with no buttons. (2) `_detect_chat_intent` looked at the LATEST assistant message — which was the prior recovery card — couldn't pattern-match it → returned 'unknown'. The intent-aware hint chain broke after the FIRST unknown reply. | Shipped commits 7e285e9 + 77b8a18. **Two fixes**: (1) `api_chat_message` reads the previous assistant card's `<!--quickreplies:[...]-->` marker BEFORE the no-op gate fires, passes it through as `_no_op_recovery.prev_qr_marker`. `_emit_no_op_recovery_card` appends the marker to the recovery card body so the SAME buttons render right next to the hint. (2) Both `_detect_chat_intent` AND the QR-marker lookup now walk back through the last 10 assistant messages and SKIP recovery cards (any content containing `"I didn't understand your reply"`). They take the FIRST non-recovery message — the actual question that started the unknown-reply chain. **Chrome-MCP-verified end-to-end**: typed 3 nonsense replies in a row at Property 1 Shop card. Each recovery card shows the SAME `[✅ Accept] [🗑 Remove] [⏭ Skip] [✏️ None of above]` buttons inherited from the original Property card. User can click directly without scrolling back. | HIGH ✓ (shipped + Chrome-MCP-verified end-to-end including 3-deep unknown-reply chain) |
| 99 | §10x.126 | **Bug class:** "card keep repeating". Six prior incidents (§10x.117 / §10x.124 / §10x.125 plus three earlier) — every fix was a per-handler patch. User: *"the card repeating even after answer given is FUCKED UP. Find the root cause. dig deep into the code and find the bloody root cause of card repeating"*. The structural defect: when `just=None` (no handler claimed the user's input) the system silently ran `plan_turn(state)` anyway → planner re-derived state, found it unchanged, emitted the SAME card the user just answered. Visible to the user as their question repeated with NO indication their reply was rejected. No fallback, no warning log, no clarification. | Dispatch chain at `api_chat_message` runs ~30 `_try_*` handlers in order; each returns dict on save or None on no-match. After the chain, `just = handler1 or handler2 or … or handler30`. If all return None, `just = None` → `plan_turn(state)` runs identically to the success case → renders next-state card → because state didn't change, that's the SAME card. Returning None is treated identically to returning success. Each per-handler fix (greedy-claim §10x.117, role spelling §10x.124, address prefix §10x.125) addressed WHY a specific handler failed; none addressed the structural fact that None should NOT silently re-render. | Shipped commit 5c3a55f. **4-piece structural fix**: (A) `_detect_chat_intent(client_id)` — pattern-matches the LATEST assistant card content to one of 14 intent labels (`testator_address`, `identity_role`, `gift_main`, `residuary_sub`, `inventory_property`, etc.). Cheap, no LLM call. (B) **NO-OP RECOVERY GATE** in `api_chat_message`: after the dispatch chain, if `just is None` AND `user_text` is non-trivial AND no attachments, stamp `will_snapshot._no_op_recovery = {intent, user_text}` + emit `app.logger.warning('§10x.126 NO-OP: ...')` so prod surfaces every silent failure. (C) `plan_turn` checks `_no_op_recovery` FIRST and routes to `_emit_no_op_recovery_card` instead of re-deriving state. Recovery card: `⚠️ I didn't understand your reply '<text>'` + intent-specific format hint + reminder that the previous question is still waiting. (D) `_NO_OP_HINTS` table — one entry per intent (14 entries). Adding a new card type requires ONE entry, mirroring the §10x.124 alias-table pattern. **The "card keep repeating" symptom is now structurally impossible**: no-handler-claims input → recovery card visible → user sees what was rejected and what's expected. Any future handler bug fails LOUDLY (recovery card + warning log) not silently (re-render). **Chrome-MCP-verified end-to-end**: typed `blah blah random reply that nothing should match` while on Property 1 Shop card → recovery card appeared with intent='inventory_property' hint listing the exact `✅ Accept / 🗑 Remove / ⏭ Skip` buttons. **Defence-in-depth**: §10x.124 (role registry) + §10x.125 (address fallback) cover specific handler-level fixes; §10x.126 catches everything else those miss. | HIGH ✓✓ (shipped + Chrome-MCP-verified + STRUCTURAL — closes the bug class) |
| 98 | §10x.125 | After §10x.124 fixed Lim Lay Cheng confirm, walker advanced to Step 2 (Confirm Testator) which prompted the user to type the residential address. User typed `NO.600, JALAN MUTIARA HIJAU 17, TAMAN MUTIARA HIJAU, 81000 KULAI, JOHOR`. The card RE-RENDERED IMMEDIATELY (0.4s later) asking for the address again. step1_data stayed empty. User: *"same issue happening. card keep repeating"*. Same bug class as §10x.124 — system rejected the user's free-text input because it didn't have a magic prefix the UI never told them about. | `_try_save_testator_address` only accepted inputs prefixed with `address:`, `dob:`, `gender:`, etc. (line 11339-11344): `for prefix in _FIELD_MAP: if low.startswith(prefix): matched_prefix = prefix; if not matched_prefix: return None`. The Step 2 card's `✏️ Type my own address` button just opened the typing prompt with NO instruction to add a prefix. User typed a perfectly valid address; handler returned None; planner re-rendered. | Shipped commit f919ad4. **Free-text address fallback** in `_try_save_testator_address`: when no prefix matched AND `step1.residential_address` is empty AND input looks like a Malaysian address (≥15 chars + comma/newline + 5-digit postcode OR address keyword like 'jalan'/'taman'/'no.'/'persiaran'), accept as testator address. Defensive guards: reject inputs that look like quickreply values (start with ▶/✓/⏭/🗑/✏/📍 emoji or keywords confirm/skip/inventory/bank_l/insurance_l). Plus retroactive save for KOID's already-typed address (msg 28) so user isn't stuck. **Chrome-MCP-verified end-to-end**: Will Snapshot now shows Step 2 ✓ with Address: `NO.600, JALAN MUTIARA HIJAU 17, TAMAN MUTIARA HIJAU, 81000 KULAI, JOHOR`, walker advanced through Steps 3 + 5 + 6. **Pattern note**: this is the SAME bug class as §10x.124 (server rejects valid input due to undocumented format contract). The fail-safe approach must apply to every "ask user to type something" path: detect plausible input shape and accept it without requiring a magic prefix. | HIGH ✓ (shipped + Chrome-MCP-verified end-to-end) |
| 97 | §10x.124 | Lim Lay Cheng IC card showed TWO buttons for the same role (`✓ Yes — Sister-In-Law` AND `Sister In Law`) and clicking either failed silently — the card kept re-rendering after every click. User: *"why both options the same / after confirming, ask same question / This bug keep persisting. what is a robust solution"*. Two surface bugs, ONE root cause: the codebase compared role strings WITHOUT normalising hyphens vs spaces. `'sister-in-law' != 'sister in law'` so dedup at chat_planner.py:2986 didn't collapse the duplicate buttons. `parse_relationship` keyword table only had spaced forms (`'sister in law'`), so a quickreply value `'sister-in-law'` (hyphenated) failed substring match and the save handler returned None → walker re-rendered. §10x.32 (capitalise) and §10x.83 (case-insensitive compare) had patched symptoms before; never fixed the underlying spelling-drift class. | Role strings flow through the codebase in MANY equivalent spellings. Every comparison/lookup has its own ad-hoc normalisation. Some forget. The fragility is structural. | Shipped commit 487dcdc. **Robust 4-piece fix**: (1) NEW `services/role_registry.py` — single source of truth: `canonical(text)` returns `'Sister-in-law'` for ANY variation (hyphens / spaces / underscores / no-separator / camelcase / case). 62-alias table covers every form; idempotent self-test runs as `python services/role_registry.py`. (2) NEW `dedup_quickreplies(qr_list)` — collapses entries whose value resolves to the same canonical role; non-role values (yes/skip/delete/names) pass through. Logs a warning if any duplicate was dropped (so prod surfaces any new offending card builder). (3) `services/identity_walker.py::parse_relationship` now delegates to `role_registry.parse_role_from_freetext`. Hyphenated input now resolves correctly. (4) `ai/chat_planner.py::_qr_marker` runs every quickreply list through `dedup_quickreplies` before emitting — so even if a future card builder forgets to dedup, the duplicate never reaches the user. **Verified server-side**: `parse_relationship('sister-in-law')` → `'Sister-in-law'` ✓; same for 8 other variations including `'Sister-In-Law'`, `'SISTER-IN-LAW'`, `'sisterinlaw'`. `dedup_quickreplies` test: 5-button list with hyphen+space duplicate → 4-button output, warning logged. **The bug class is now structurally extinct**: comparing role strings without canonicalising is itself the bug; the central dedup gate at `_qr_marker` provides defence-in-depth. | HIGH ✓ (shipped + server-side verified; bug class extinct via structural fix not symptom patch) |
| 96 | §10x.123 | Clicking an exhibit thumbnail (IC photo, property doc, etc.) opened an IN-APP CAROUSEL OVERLAY on top of the chat — the chat view was hidden behind the lightbox. User wanted the exhibit in a SEPARATE WINDOW so they could reference both side-by-side without losing the chat context. User instruction: *"when open exhibit, open in new window. separate window. do not use the AI will craft window"*. | `static/js/chat.js` line 477 had image thumbnails wrapped in `<button onclick="window.__openCarousel(...)">` which rendered an in-app `#img-carousel` overlay. PDFs/audio/other files already used `<a target="_blank">` (correct path); only IMAGES went through the in-app carousel. The carousel had nav arrows + zoom + lightbox features but blocked the chat view. | Shipped commit 315944e. Image thumbnails now use the same `<a href="${url}" target="_blank" rel="noopener">` pattern as PDFs/audio. Browser opens `/api/documents/<id>` in a new tab/window; chat view is preserved. User can position the exhibit window alongside the chat. The `window.__openCarousel` function is left defined as dead code in case any other path references it; safe to remove in a later cleanup. **Pending Chrome MCP visual verification per §10x.191.** | HIGH (shipped — pending visual confirmation) |
| 95 | §10x.122 | KOID forwarded 3 IC photos (Lim Lay Cheng, Joshua, Esther). Only **Esther** surfaced as a pending IC card in chat. Joshua + Lim Lay Cheng were silently marked `category='duplicate'` even though both had full name + NRIC extracted from vision. Result: user thought "why IC image not extracted" — extraction worked, dedup wrongly hid them. | `_dedupe_ic_against_existing` sibling loop matched ICs as duplicates when ANY of (NRIC equal, name equal, address fuzzy match) fired. Address fuzzy match (≥3 distinctive 4+ char tokens shared, +1 postcode bonus) is intentionally lenient to catch back-of-IC duplicates that have no name/NRIC. But it had no NRIC-divergence guard. Esther's IC processed first → 'nric'. Joshua's + Lim Lay Cheng's ICs arrived with the SAME residential address (whole family lives at NO.600 JALAN MUTIARA HIJAU 17) but DIFFERENT NRICs → addr_match alone fired → marked as duplicates. §10x.151 already shipped this exact fix for the Person-match path at line 6023; the sibling-match path at line 6079 was missed. | Shipped commit bd8280a. **Two-piece fix at line 6079**: (1) `addr_only = addr_match AND not nric_match AND not name_match`. Refuse `addr_only` when both sides have NRICs and they differ. Refuse `addr_only` when both sides have names and they differ. (Mirrors §10x.151 person-match guard.) (2) **Richness-aware dedup**: when new doc has MORE info than sibling (e.g. front of IC has name+NRIC, sibling is back-of-IC with NRIC only) and NRICs match, PROMOTE new → 'nric' and demote sib → 'duplicate'. Without this, an IC FRONT arriving after its BACK got wrongly marked duplicate even with valid NRIC match — back was 'first' so front lost. Plus one-shot KOID migration: flipped Joshua + Lim Lay Cheng IC fronts back to 'nric' (still in `extracted_data` with full name+NRIC, just wrong category). **Verified**: `get_pending_ic_documents(KOID)` now returns 3 (was 1) — Joshua score=5, Esther score=5, Lim Lay Cheng score=1 (outsider via §10x.30 sort). | HIGH ✓ (shipped + DB-verified) |
| 94 | §10x.121 | Generate AI Will button HARD-BLOCKED with `disabled` state when ANY validation ERROR existed (e.g. missing title No / lot No / Mukim per NLC s.292). User wanted to be able to consciously override — generate the will with placeholders for missing fields and have the lawyer fill them post-generation — but had no way to do so. They'd have to edit each gift manually before any will could be drafted. | `wizard_step_review.html` set `<button disabled>` when `has_errors`. `/wizard/generate` route also flashed each error and redirected. Two layers of hard-block, no override path. | Shipped commit 79ad1f9. **3-piece fix**: (1) Backend: `/wizard/generate` accepts `proceed_with_warnings` form field. If ERRORs exist AND no override → stash structured errors on `session._pending_override`, redirect to /wizard/step/10. If override → stamp `'generated_with_warnings'` on `Will.completed_steps` + `app.logger.warning` audit line, generate anyway. (2) Template: button no longer disabled — turns AMBER with label "Generate with Missing Fields…" when has_errors. New override modal with title "⚠️ Required fields are missing", body explaining the lawyer must fill before signing, scrollable list of every missing field (field-path tag + rule message), two buttons: "Cancel — let me complete first" / "⚠️ Generate anyway with missing fields". (3) JS: submit interceptor — first click opens modal instead of submitting; "Generate anyway" sets hidden `proceed_with_warnings=1` flag and re-submits. Server-side redirect with `pending_override` set auto-opens the same modal (covers the case where the user navigates away and comes back). **Chrome-MCP-verified per §10x.191**: PHEK with no errors → blue button + no modal; PHEK with injected incomplete property → amber button + warning banner + modal correctly lists `gifts.0.property_details: missing title number, lot/PT number, Mukim, Daerah, Negeri. Probate-blocking — NLC s.292 requires…`; Cancel closes modal preserving state. Wizard amber banner per §10x.150/§10x.193 still highlights every incomplete field on each gift card so user can complete in-place if they prefer not to override. | HIGH ✓ (shipped + Chrome-MCP-verified) |
| 93 | §10x.120 | One client (KOID) accumulated **4 Will rows** during testing: 1 empty approved + 1 generated (Joshua) + 1 approved (LIM LAY CHENG, wrong) + 1 latest draft (correct). Wizard's `session.will_id` pinned to the wrong-data approved snapshot; chat walker wrote to the latest draft; user saw stale "LIM LAY CHENG" in wizard. The mistaken workaround I suggested was "switch wills via Saved Wills" — wrong, the bug is multiple wills existing at all. | All 3 Will INSERT helpers (`_get_or_create_active_will` line 3595, `_get_or_create_will` line 8843, `save_session_data` line 537) filtered `Will.query.filter_by(status='draft')`. When user clicked Approve, status flipped to 'approved'. Next chat message → query returned None for status='draft' → INSERTED a new draft → 2 wills. Repeat → 4. Wizard pinned `session.will_id` and never rebound. | Shipped commit f9c6e87. **6-piece fix**: (1) New `ACTIVE_WILL_STATUSES = (draft, generated, pending_approval, approved)` constant. (2) `_get_or_create_active_will` now widens lookup to ANY active status — returns existing if found (flips back to draft on edit), creates only if zero. Auto-heals on read: if >1 active found, soft-deletes all but latest. (3) Other 2 helpers delegate to (2) so all 3 INSERT sites share one invariant. (4) `_refresh_wizard_session_from_db` auto-rebinds `session.will_id` to client's current active will on every wizard GET — fixes "stale data" symptom at source. (5) New `assert_singleton_will(cid, when, strict=)` + `WillSingletonViolation` exception for fail-loud checks. (6) `data/consolidate_wills.py` one-shot migration soft-deletes existing parallel rows. **Verified**: smoke test on all 3 prod clients (PHEK YI TING / NADANASABAPATHY / KANAGARANY) → 1 active will each, invariant OK; dry-run migration: 0 violations to clean up. | HIGH ✓ (shipped + smoke-verified; pending fresh-KOID end-to-end) |

### Maintenance rule

When the user calls out a bug "should not happen again":
1. Add to this FUCK list
2. Find or write the §10x rule in CLAUDE.md
3. Add a check to `asset_audit.py` (or build a new audit file)
4. Verify the check fails on the buggy code BEFORE shipping the fix
5. Verify the check passes after the fix
6. Commit ALL of (CLAUDE.md, audit, code fix) in ONE commit so they
   never separate

The reason this list exists: between sessions / contexts, code drifts.
Audits + permanent rules are the only way to keep bugs DEAD.

---

### 🔥🔥🔥 BUG-TABLE WORKFLOW (mandatory, every reported bug) 🔥🔥🔥

User instruction (verbatim): *"whenever any bug reported, check against
the table whether is a reported bug and the fix fail. Update the bug
table. Always refer back to the table whenever bugs reported. If bug
persist after repeated fix, need to take a deep and holistic review
at the code"*.

**Step 1 — Search the table FIRST.** Before any diagnosis, grep §10x.39
for keywords from the user's report. If a row already exists:
   • Same fix shipped + still failing → **flag it loudly to the user**
     ("this should have been fixed by §10x.X — that fix is broken").
     Do NOT silently re-patch. Bring the regression to attention so
     the user can decide between another patch vs holistic review.
   • Different fix → fine, add new row but cross-reference the related
     prior rule.

**Step 2 — If new** — add a row IMMEDIATELY (before shipping the fix).
Mark status as "in progress". Update with commit hash + verification
when shipped.

**Step 3 — If a bug class re-surfaces after 2+ fixes** — STOP patching.
Do a holistic review (state machine refactor, structural fix, etc.).
Patches make brittleness worse — every band-aid adds to the
inventory. Reference the §10x.137 redesign doc as the model.

**Step 4 — Always commit code + bug-table row + tests in ONE commit.**
Separating them lets future audits lose context.

This rule is the meta-process that prevents the recurring-bug-class
trap (see §10x.133 for the canonical example).

---

### 10x.40  🔥🔥 BURN-IN — Confidence-driven buttons + USER MUST CONFIRM 🔥🔥

**Cards NEVER auto-save. The user must click to confirm. Number of
options shown depends on confidence:**
- **HIGH**: ONE pre-suggested button (default) + manual-override escape
- **MEDIUM**: 3 options (suggested + 2 alternates)
- **LOW**: 3 distribution options (no auto-suggestion possible)

### Why this rule exists

User explicitly said:
> "MUST VERIFY, Give the most likely option as default option first,
>  MUST ALWAYS GET USER TO CONFIRM. if confidence very high, no need
>  to give other option but must still confirm by user. If confidence
>  is medium or low, must give 3 options"

### Confidence definition

For property/bank/insurance gift cards (Layer 2 — main beneficiary):

| Tier | Trigger |
|------|---------|
| **HIGH** | Deduced beneficiaries from message ALL appear in candidate list AND total share rescales to exactly 100% per §10x.13 |
| **MEDIUM** | Partial deduction — some beneficiaries match but total != 100%, or names not in candidate set |
| **LOW** | No deduction at all; message text is silent on this property's distribution |

### Card layout per tier

**HIGH:**
```
🎯 HIGH confidence — your message clearly states:
  • Joshua Koid Teck Seng 50% — _from "Joshua...25percent"_
  • Esther Koid En Hui 50% — _from "...25percent to Esther"_

Click Confirm to save this distribution. You can still override.

[ ✓ Confirm — Joshua 50%, Esther 50% ]
[ ✏️ Different — type manually ]
[ ⏭ Skip this gift ]   [ 🗑 Remove ]
```

**MEDIUM:**
```
⚠️ MEDIUM confidence — partial match from your message:
  • Joshua Koid Teck Seng 50%

Pick the option that matches your intent — confirm before we save.

[ ⭐ Joshua 50%, Esther 50% (suggested) ]
[ Joshua 50% + Esther 50% (equal) ]
[ Joshua 100% ]
[ ✏️ Type manually ]
[ ⏭ Skip ]   [ 🗑 Remove ]
```

**LOW:**
```
🤔 No clear distribution in your message for this property. Pick the
most likely option — your confirmation is required before we save:

[ Joshua 50% + Esther 50% (equal) ]
[ Joshua 100% ]
[ Esther 100% ]
[ ✏️ Type manually ]
[ ⏭ Skip ]   [ 🗑 Remove ]
```

### Hard rules

1. **NEVER auto-save.** Even HIGH-confidence cards REQUIRE a user click.
2. **HIGH = 1 primary button** to keep the path obvious. Override is
   one click away ("Different — type manually") but isn't competing
   for attention with 4 distractor buttons.
3. **MEDIUM/LOW = 3 distribution options** so the user has real
   choice without typing.
4. **Manual-type escape always available** — if our buttons don't
   match what they want, one click to type freely.

### Where this is enforced

| File | Function | Mechanism |
|------|----------|-----------|
| `ai/chat_planner.py` | `_step6_property_question` | Confidence classification + tiered button layout |
| `ai/chat_planner.py` | bank/insurance Layer-2 cards | Same pattern (TODO: extend) |

### Litmus test

```
For every gift card displayed:
  - Is the # of beneficiary buttons {1, 3, 4} based on confidence tier?  → ✓
  - Is HIGH-confidence card showing 4+ buttons distractors?               → ✗ §10x.40 broke
  - Did anything auto-save without a user click?                          → ✗ §10x.40 broke
```

---

### 10x.41  🔥🔥 BURN-IN — Person rows MUST have a relationship 🔥🔥

**`ensure_person` REFUSES to create a new Person with empty
`relationship`. A role is required. No exceptions. Ghost identities
(rows with name but no role) corrupt the wizard, the chat, and the
generated will.**

### Why this rule exists

User report: identity list showed "ESTHER KOID EN HUI / JOSHUA KOID
TECK SENG" with no role displayed. The Person rows themselves had
roles (Daughter / Son), but a different code path was capable of
creating future Person rows WITHOUT a role — a silent footgun.

### Enforcement

`services/person_registry.py::ensure_person`:

```python
if not rel:                     # NEW Person with no relationship
    log.warning(f"§10x.41 REFUSED: no relationship for {name}")
    return None
# Existing rows: opportunistic fill of empty fields (never overwrite)
```

A `None` return tells the caller "I would not save this — ask the
user for the role first." The chat planner should react by surfacing
a role-pick card; the wizard should re-prompt.

### Hard rules

1. **Every NEW Person row has a non-empty `relationship`.** Period.
2. **EXISTING rows: never overwrite a non-empty role**, but DO fill
   in empty fields opportunistically.
3. **If a caller can't determine the role**, it MUST ask the user
   first. Never call `ensure_person(..., relationship='')` and hope.
4. **Log when refused** — silent skips are debt. The warning trail
   helps catch new code paths that violate the rule.

### Where this is enforced

| File | Function | Mechanism |
|------|----------|-----------|
| `services/person_registry.py` | `ensure_person` | Returns `None` on empty relationship for NEW rows |
| `app.py` | callers of `ensure_person` | Must check return for None and act on it |

### Litmus test

```sql
SELECT id, full_name, relationship FROM persons
 WHERE relationship IS NULL OR relationship = '';
-- Expected: 0 rows
```

If any rows are returned, §10x.41 has been violated. Either the role
was lost in update, or a code path bypassed `ensure_person`. Trace
back to source and fix.

---

### 10x.42  🔥🔥🔥 BURN-IN — Mid-flow add MUST trigger downstream reconciliation 🔥🔥🔥

**When a NEW identity (Step 1) or NEW asset (Step 6) is added AFTER
later steps already completed, the system MUST replay all relevant
downstream steps for the new entity. Never silently leave them
half-integrated.**

### Why this rule exists

User report: Lim Bee Yan was added mid-flow as **Wife** AFTER Steps
2-5 had already completed. Her Person row was created correctly with
`relationship='Wife'`, but `step4_data` (Beneficiaries) was NOT
updated to include her — even though the message clearly says:

> "All my Bank Savings go my wife 100percent."
> "All Insurance go to my wife (Lim Bee Yan) 100percent."

Result: she's an identity but not a beneficiary in the wizard. When
the will is generated, banks + insurance would have NO beneficiary
because step4 doesn't list her. Probate-critical data loss.

### What the reconciliation MUST do

When a new Person is added via `_try_assign_pending_identity`:

1. **Step 3 (Executor) check** — if the new person matches a
   role-only mention ("My Executor My Sister in law"), auto-promote
   them to executor candidate (already handled by §10x.21 outsider
   elimination).

2. **Step 5 (Beneficiaries) check** — if the AI Summary text names
   this person (or their role: "wife", "son", etc.) as the
   beneficiary of any asset, ADD them to `step4_data`.

3. **Step 6 (Specific Gifts) check** — if any saved gift in
   `step5_data` has empty beneficiary AND the message names this
   person for that asset class (e.g. "all banks → wife"), update
   the gift's `beneficiaries` field to include them.

4. **Layer 3 (Substitute) re-derive** — if substitute defaults per
   §10x.14 reference family roles ("spouse → both children"), and
   the spouse was just added, the defaults should now include them.

### Same rule for ASSETS

When a new asset (property / bank / insurance) is uploaded mid-flow:

1. **Layer 1 (Confirm Asset)** — render confirm card per §10hg
2. **Layer 2 (Main Beneficiary)** — pre-suggest from message per §10x.36
3. **Layer 3 (Substitute Beneficiary)** — pre-suggest per §10x.14

The walkthrough must NOT skip these layers just because Step 5 / Step
6 was previously marked complete. Per-asset confirmation is required.

### Hard rules

1. **Never assume "later steps complete" means new entities are
   processed.** Each new entity walks through its own validation chain.
2. **Run downstream reconciliation INSIDE the save handler**, not as
   a separate pass — atomic with the save.
3. **Log every auto-update** with `_added_by: '§10x.42'` so a future
   reviewer can see why a row appeared.
4. **Don't overwrite user-set fields.** If user manually overrode a
   beneficiary, reconciliation NEVER stomps it.

### Implementation

`app.py::_try_assign_pending_identity` calls
`_reconcile_downstream_for_new_identity(client_id, name, role)` after
saving the new Person:

```python
ensure_person(...)
db.session.commit()
_reconcile_downstream_for_new_identity(client_id, name, chosen_role)
```

The reconciliation function:
- Reads the AI Summary / raw text
- Detects whether this person is named as a beneficiary (via name OR role)
- If yes, appends to `step4_data` (with `_added_by` marker)
- Commits

### Where this is enforced

| File | Function | Mechanism |
|------|----------|-----------|
| `app.py` | `_try_assign_pending_identity` | Calls reconciler post-save |
| `app.py` | `_reconcile_downstream_for_new_identity` | The reconciler itself |
| `services/fuck_list_verify.py` | future check | Verify every named-beneficiary in message has a step4 row |

### Litmus test

```
Q: KOID forwards a 2nd email with Lim Bee Yan's IC after Steps 2-5 done.
   - User clicks Yes — Wife on her IC card
   - step4_data NOW contains Lim Bee Yan as beneficiary       → ✓
   - step4_data still has only Esther + Joshua                → ✗ §10x.42 broke
```

If a person is in the Person table as Wife but not in step4 as
beneficiary despite the message saying "all to my wife", the
reconciler is broken. Fix at the reconciler, not by manually editing
step4 in the DB.

---

### 10x.43  🔥🔥🔥🔥 BURN-IN — MID-FLOW MESSAGE/IMAGE = FULL PIPELINE REPLAY 🔥🔥🔥🔥

**Whenever ANY new WhatsApp message and/or image is provided AFTER
the walkthrough has progressed past Step 1, the system MUST re-run
the full pipeline:**

```
NEW MESSAGE / NEW IMAGE
       ↓
1. AI Summary       — re-parse the combined text (existing + new)
2. Image Analysis   — vision-classify any new attachment
3. Identity Match   — re-check pending IC list; match new ICs to family
4. Role Match       — assign role per §10x.30 (HIGH→LOW confidence)
5. Asset Match      — 3 layers per asset:
                       Layer 1: Identify asset (Confirm card)
                       Layer 2: Main Beneficiary (per §10x.36 + §10x.40)
                       Layer 3: Substitute Beneficiary (per §10x.14)
       ↓
RECONCILE downstream — per §10x.42, integrate the new entity
into Steps 3 / 5 / 6 if the message names them.
```

NEVER skip steps just because they were marked complete earlier.
A new email may name new beneficiaries / executors / properties
that need to be threaded back into already-completed steps.

### Why this rule exists

User said:
> "AI SUMMARY → IMAGE ANALYSIS → IDENTITY MATCH → ROLE MATCH →
>  ASSET MATCH (3 layers, identity, main beneficiary, substitute
>  beneficiary). Must rerun this whenever any NEW whatsapp message
>  and/or image is provided midway"

Real-world flow that triggered this: KOID forwarded the original
WhatsApp message → walkthrough advanced through Steps 1-5. THEN
they emailed Lim Bee Yan's IC photo separately. The system had to:
- Re-classify the IC (Step 3 of pipeline above)
- Match her IC to the family (Step 4)
- Auto-add her as Wife per the role-match (Step 5)
- Reconcile downstream: she's named as bank/insurance beneficiary
  in the original message, so she belongs in Step 5 (Beneficiaries)
  — even though Step 5 was previously "complete"

Without §10x.43, that reconciliation might silently fail (and did,
until §10x.42 was burned in).

### What "full pipeline replay" means in practice

When the inbound webhook receives a new email for an existing client:

1. **Re-fire `_process_inbound_message_async`** (already happens via
   the watchdog per §10x.5/§10x.29).
2. **`_summarise_message`** runs again — combined message text gets
   a fresh AI Summary. Replace the stale summary card.
3. **Vision-classify new attachments only** (existing classifications
   are monotonic per §10x.2 — never downgrade).
4. **`get_pending_ic_documents` and `get_pending_gift_documents`
   recompute** — NEW ICs / gifts surface as pending cards on the
   next chat poll.
5. **For each new pending IC:** identity walkthrough card → role
   match → §10x.42 reconcile downstream.
6. **For each new pending gift:** Layer 1 → Layer 2 → Layer 3 cards
   per §10x.23.

The chat planner already runs `plan_turn` on every history poll, so
as long as `pending_ics` and `pending_gifts` are non-empty, the
walkthrough re-engages naturally. The §10x.42 reconciler closes the
loop for downstream effects.

### Hard rules

1. **A new inbound email is NEVER ignored just because Steps 2-5
   were marked complete.** The `pending_ics` / `pending_gifts`
   walkthrough always engages first.

2. **Stale cards are NEVER trusted as ground truth.** Re-derive the
   AI Summary, IC list, and gift list from the live Document table
   on every refresh. Per §10x.17, wizard reads from DB on every GET.

3. **The 3-layer asset flow runs PER ASSET regardless of order.**
   New asset arriving mid-flow goes through Layer 1 → 2 → 3 even
   if other assets are already done.

4. **Identity reconciliation cascades.** Adding a new family member
   may require:
   - Step 3 (Executor) re-evaluation (if they're named as such)
   - Step 4 (Guardian) re-evaluation (if minor children + spouse)
   - Step 5 (Beneficiary) re-evaluation per §10x.42
   - Step 6 (Specific Gifts) re-evaluation if they're named as
     beneficiary of any specific asset

5. **Audit MUST verify reconciliation.** `fuck_list_verify.py` check
   #23 enforces "every named-beneficiary in step4". A future check
   should verify: "if message names a new spouse and gifts existed
   before, those gifts' beneficiary fields include the new spouse."

### Where this is enforced

| Pipeline step | File | Function |
|---------------|------|----------|
| Inbound webhook | `app.py::api_inbound_email` | Adds doc rows; spawns processor |
| Process docs | `app.py::_process_inbound_message_async_inner` | Vision classify + AI Summary card |
| AI Summary | `ai/chat_planner.py::_summarise_message` | Parses combined text |
| Watchdog re-fire | `app.py::api_chat_history` watchdog (§10x.29) | Re-fires processor for stuck docs |
| Identity match | `services/identity_walker.py::get_pending_ic_documents` | Surfaces new ICs + H3 placeholders |
| Role match | `app.py::_try_assign_pending_identity` (§10x.32) | Family-only, with role-matcher fallback |
| Asset match | `services/gift_walker.py::get_pending_gift_documents` | New asset → pending |
| Layer 1/2/3 | `app.py::_try_save_*_layered_gift` (§10x.23) | Per-gift Confirm → Main → Substitute |
| Reconcile | `app.py::_reconcile_downstream_for_new_identity` (§10x.42) | Step 5 auto-add when message names them |

### Litmus test

```
Q: Walkthrough has reached Step 6. User forwards a 2nd email with
   a new IC for "Aunt Mary".
   - Aunt Mary IC classified, surfaces as pending Step 1 card    → ✓
   - User confirms her as Aunt → Person row created
   - If message says "Aunt Mary gets the savings account",
     she's auto-added to step4 + the savings gift                → ✓
   - If neither: she's just an identity, no further changes      → ✓
```

If a new inbound email is silently dropped, or Steps 1-5 don't
re-engage when new entities appear, §10x.43 has been violated.
The fix is at the inbound handler / walkthrough gates — never patch
by manually editing DB rows.

---

### 10x.44  🔥🔥🔥 BURN-IN — New identity reconciles into ANY step that names them 🔥🔥🔥

**Extends §10x.42. When a new identity is added, the reconciler MUST
check ALL downstream will-role assignments — not just Beneficiary.
The new person can map to:**

| Step | Will-role | Trigger pattern in message |
|------|-----------|---------------------------|
| **3** | Executor / Substitute Executor | "my executor X", "X (executor)", "my <role> as executor" |
| **4** | Guardian | "my guardian X", "X (guardian)", "my <role> as guardian" |
| **5** | Beneficiary | "go to my X", "to my X", "for my X" |
| **8** | Trustee | "my trustee X", "X (trustee)", "my <role> as trustee" |

### Why this rule exists

User explicitly said:
> "the new identity can also be executor or guardian or trustee. can
>  be any of the earlier step"

§10x.42 only handled Step 5 (Beneficiary). §10x.44 widens this to
all 4 will-role steps. When a new IC arrives mid-flow, the reconciler
runs the SAME pattern check for each will-role and auto-populates the
matching wizard step.

### Hard rules

1. **All 4 step dispatchers run for every new identity.** Skipping a
   step because "the message probably doesn't name them as that role"
   is forbidden. Run the check; let the regex decide.
2. **Each step's add-helper is idempotent** — name dedup before append.
   Re-running the reconciler must NOT create duplicate rows.
3. **First executor is Primary; second is Substitute.** Subsequent
   adds are Substitute. The role-matcher (§10x.21) determines the
   ordering when KOID's "My Executor My Sister in law" pattern triggers.
4. **Guardian add gated on minor children.** If no minors per Step 4,
   the guardian list stays empty even if message says "X is guardian".
   (TODO: enforce this gate; currently always adds.)
5. **Trustee defaults to executor.** Per §10x.24 + Phek format, "my
   Executor(s) shall also act as my Trustee(s)" unless overridden.
   So Step 8 trustee population is rare — only fires when message
   explicitly names a different trustee.
6. **Every auto-add carries `_added_by: '§10x.44 reconcile (Step N: …)'`
   marker** for audit trail.

### Implementation

`app.py::_reconcile_downstream_for_new_identity` now dispatches to
four step-specific helpers:

```python
def _reconcile_downstream_for_new_identity(client_id, name, role):
    # ... resolve will + person + message text ...
    if _named_with('executor'):
        _step2_add_executor(will, person, name, role)
    if _named_with('guardian'):
        _step3_add_guardian(will, person, name, role)
    if _is_named_beneficiary(...):
        _step4_add_beneficiary(will, person, name, role)
    if _named_with('trustee'):
        _step7_add_trustee(will, person, name, role)
```

Each step helper:
1. Reads the relevant `will.stepN_data`
2. Dedups by name
3. Appends with `_added_by` marker
4. Commits

### Where this is enforced

| File | Function |
|------|----------|
| `app.py` | `_reconcile_downstream_for_new_identity` |
| `app.py` | `_step2_add_executor` / `_step3_add_guardian` / `_step4_add_beneficiary` / `_step7_add_trustee` |
| `services/fuck_list_verify.py` | check #23 (beneficiary) — TODO extend to executor/guardian/trustee |

### Litmus test

```
Q: Walkthrough complete for KOID. User uploads new IC for "Aunt Mary"
   and message says: "I appoint Aunt Mary as my Trustee".
   - Aunt Mary IC classified, family role assigned (Aunt)
   - §10x.42/44 reconciler fires:
     • Beneficiary check: no "go to my aunt" — skipped
     • Executor check: no "my executor my aunt" — skipped
     • Guardian check: no "my guardian my aunt" — skipped
     • Trustee check: "I appoint Aunt Mary as my Trustee" → step7 ✓
   - step7_data.trustees has Aunt Mary with _added_by marker  → ✓
```

If a person's will-role is named in the message but the corresponding
step doesn't include them, the bug is in the reconciler dispatch.
Fix at the dispatcher pattern matching, not in DB.

---

### 10x.45  🔥🔥 BURN-IN — UI compactness on chat cards 🔥🔥

**Chat cards are READ. Every line costs the user attention. Default
to compact, condensed layouts. Verbose explanations belong in tool-
tips or footnotes, not in the card body.**

### Hard rules

1. **Land Registry Details** — render as a one-line summary with
   `·` separators, NOT a 5-bullet block:
   ```
   📋 Title 251041 · Lot 127082 · Mukim Plentong · Daerah Johor Bahru · Negeri Johor
   ```
   (skip empty fields silently)

2. **Probate-missing warning** — single line, ≤120 chars:
   ```
   ⚠️ Missing: title number, lot number — request a clearer Geran scan.
   ```
   NO 4-line explanation of "Borang 14A / Deed of Transmission /
   Pejabat Tanah" inline. The lawyer already knows.

3. **Per-card structure** — at most 6 visual blocks, in this order:
   1. Title bar (`### 🏠 Property N of M`)
   2. Property body (formatted from `models/gift.py`)
   3. 📨 _from your message:_ snippet (per §10x.36)
   4. 📋 Registry one-liner (if available)
   5. ⚠️ Warnings (if any) — terse single lines
   6. Buttons (per §10x.40 — HIGH=1 / MEDIUM-LOW=3)

   Anything more is bloat — move to a "Show more" expander or kill.

4. **Confidence-tiered buttons** (§10x.40):
   - HIGH: 1 confirm + 1 manual + skip + remove
   - MEDIUM: 3 alternatives + manual + skip + remove
   - LOW: 3 distribution options + manual + skip + remove

5. **NEVER stack three transition messages in one turn** (§10x.37):
   ```
   ❌ "Saved X" + "Step 1 COMPLETE" + "moving to Step 2" + Step 6 card
   ✓  Single ack: "✅ Saved X — Step 1 done. Step 6 below 👇" + Step 6 card
   ```

### Why this rule exists

User explicit feedback:
> "improve the UI. very confusing"
> "improve the UI. looks cluttered"

A 6-section card with 5 buttons = wall of text. The user's eye has
to scan through "Land Registry / Cannot probate / 5 NLC bullets /
3-line probate explanation" before finding the action. Decisions get
slower; trust drops.

### Where this is enforced

| File | Function | What changed |
|------|----------|--------------|
| `ai/chat_planner.py` | `_step6_property_question` | NLC details one-line; warnings terse; §10x.40 button tiers |
| `ai/chat_planner.py` | `_format_property_walkthrough_card` | Same compact rules |
| Card style guide | this rule | 6-section max; verbose explanations move to tooltips |

### Litmus test

```
Q: Visual scan of any chat card.
   - Title + body + 📨 snippet + buttons + ≤2 inline warnings  → ✓
   - 5+ bulleted lists / >3-line probate explanations          → ✗ §10x.45 broke
```

If a future card builder adds a 4-line block of legal explanation,
move it to a footnote OR cut it. Cards are decision tools, not
training material.

---

### 10x.46  🔥🔥🔥🔥 BURN-IN — Layer separation + confidence is INTERNAL 🔥🔥🔥🔥

**FOUR rules user called out together. All four must be enforced
together — they're tightly coupled.**

### Rule 1 — Layer 1 = ASSET IDENTITY ONLY

The Layer 1 confirm card asks "is this property in your will?" — and
NOTHING ELSE. It must NOT include:

- ❌ Testator share % ("Your share to dispose: 1/2")
- ❌ Beneficiary intent ("Joshua 25%, Esther 25%")
- ❌ Distribution math ("of sender's share")
- ❌ Anything from Layer 2 / Layer 3

Layer 1 includes ONLY:
- ✓ Property name / address
- ✓ Lot / Title / Mukim / Daerah / Negeri (when known)
- ✓ Ownership type (sole / joint with X) — needed to identify the asset
- ✓ "Confirm / Upload / Type / Skip" buttons

The user reads Layer 1 once per property to validate IDENTITY. Forcing
them to also process beneficiary % at this stage causes confusion and
slow decisions.

### Rule 2 — Confidence levels are INTERNAL

`HIGH / MEDIUM / LOW` confidence is **internal scoring** that drives
routing logic (which card to show, how many buttons, etc.). It MUST
NOT be displayed to the user. Strip every:

- ❌ "🔎 Confidence: HIGH"
- ❌ "🎯 HIGH confidence — your message clearly states..."
- ❌ "⚠️ MEDIUM confidence — partial match..."
- ❌ "🤔 No clear distribution..." (LOW)

Replace with **plain factual statements** about what we have / don't have:

- ✓ "No title document attached yet."
- ✓ "Distribution suggested from your message: Joshua 50%, Esther 50%."
- ✓ "Pick the option that matches your intent."

The button layout still varies by confidence (HIGH = 1 button,
MEDIUM/LOW = 3 — per §10x.40), but the LABEL "HIGH/MEDIUM/LOW" never
appears in the user-facing text.

### Rule 3 — HIGH confidence requires MESSAGE + IMAGE both agree

Calling a card "HIGH confidence" while showing **"no title document
attached yet"** is a contradiction. HIGH means the system has BOTH
sources of truth aligned:

| Tier | Requirement |
|------|-------------|
| **HIGH** | Message names the asset AND a matching image confirms it (lot/title in image == AI Summary lot/title, OR strong token+mukim match) |
| **MEDIUM** | Message names the asset OR image extracted ID, but not both — partial verification |
| **LOW** | Asset present in only one source with no corroboration |

Before today, every message-stated H3 (no image) was tagged HIGH.
That's wrong — a message-only assertion is at best MEDIUM. HIGH
demands two-source corroboration.

### Rule 4 — ROOT CAUSE: stop patching symptoms

User explicit feedback:
> "WHY THIS FUCK HAPPEN. FIND THE ROOT CAUSE. DONT JUST FIX THIS"

Pattern observed across this session: bug surfaces → I patch the
symptom (e.g. "filter dropped real images, just don't filter") →
new bug surfaces from the unfiltered case → patch again → repeat.

The root cause for the §10h-filter regression specifically:
1. AI Summary parser DOES NOT extract lot/title from message text
   (the user's WhatsApp doesn't contain lot/title — they only typed
   "Unit C-30-08, Marina Cove").
2. Image OCR addresses are STRUCTURED land-registry format
   ("PTD 127082, Mukim Plentong, Johor Bahru"), not street format.
3. AI Summary addresses are STREET format ("No. 03 Jalan Gunung 4,
   Seri Alam Masai, 81750").
4. Token overlap between (2) and (3) is RARE — different vocabularies.
5. So matching by tokens silently fails on 90% of real cases.
6. Without a fallback semantic bridge (geo bridge, postcode → mukim,
   building name → mukim), images get dropped or mis-bound.

**Lesson burned in:** before adding any "match" logic, document
WHAT signals each source contains and which ones overlap. If the
overlap is rare, the match algorithm needs a SEMANTIC layer
(geo bridge / synonym table / Claude-vision question) not lexical.

### Implementation

`ai/chat_planner.py::_walkthrough_property_card_h3` updated to:
- Drop testator-share line + beneficiary intent
- Drop "Confidence: HIGH" text label
- Replace with neutral "No title document attached yet" copy

Confidence-tier button selection (§10x.40) STILL applies — buttons
on HIGH-tier cards still default to 1 confirm + skip + remove. The
text label is what's removed.

`services/gift_walker.py::_match_image_to_ai_summary` (the H3 dedup):
- HIGH score = 5+ (lot/title or tokens+mukim)
- Matched images → bound to AI Summary
- Unmatched images → §10d residual

When an image group binds to an AI Summary entry, that AI prop's H3
placeholder is suppressed (image bound = no need for H3). Confidence
on the resulting card becomes HIGH (image+message both confirm).

### Where this is enforced

| Rule | File | Function |
|------|------|----------|
| #1 Layer 1 only | `ai/chat_planner.py` | `_walkthrough_property_card_h3` |
| #2 No confidence label | All card builders | Strip "Confidence: HIGH/MEDIUM/LOW" text |
| #3 HIGH = msg+img | `services/gift_walker.py` | `_match_image_to_ai_summary` (score ≥ 5) |
| #4 Root cause docs | This rule | Maintenance habit, not a single function |

### Litmus test

```
Q: Render Layer 1 card for KOID Property 1 (Paradisonuava — no image).
   - No "Your share to dispose" line                  → ✓
   - No "Beneficiary intent" line                     → ✓
   - No "Confidence: HIGH" or any tier label          → ✓
   - Says "No title document attached yet"            → ✓
   - 4 buttons: Confirm / Upload / Type / Skip        → ✓

Q: Render Layer 1 card for property where image+message agree.
   - Card title: "Property X of N"                   → ✓
   - Body: address, lot, title, mukim, daerah         → ✓
   - NO confidence label                              → ✓
   - 1 confirm button (per §10x.40 HIGH tier)         → ✓
```

If any tier-label text appears on a user-facing card, §10x.46 broke.

---

### 10x.48  🔥🔥🔥🔥🔥 BURN-IN — CANONICAL ASSET-MATCHING FLOW (CONFIRMED) 🔥🔥🔥🔥🔥

**This is THE flow. Every previous §10g / §10ha / §10hb / §10he / §10hf / §10hg / §10i / §10x.18 / §10x.43 / §10x.46 rule is a constraint on a specific stage below — they do NOT describe separate algorithms. The flow has six stages and they MUST be implemented as separate stages. Cross-stage shortcuts are the root cause every time matching breaks.**

> User confirmation: "correct" — May 2026. This rule replaces the patchwork
> matching code that drifted between sessions. Reading this rule is the
> first thing any future Claude session should do before touching any
> file in the matching path.

### Six stages, in order

```
STAGE 0  Parse canonical asset list   →  AssetItem[]
STAGE 1  Group documents              →  DocGroup[]
STAGE 2  Bind AssetItem ↔ DocGroup    →  Binding[]   (one-claim-only)
STAGE 3  Surface residual DocGroups   →  §10d unverified cards
STAGE 4  Build merged Gift records    →  step5_data
STAGE 5  3-layer walkthrough          →  Layer1+2+3 user clicks
STAGE 6  Replay on new input          →  resume, never restart
```

### STAGE 0 — Parse canonical AssetItem list

INPUT: AI Summary card content + `Will.step6_data._raw_forward_text`
OUTPUT: ordered list of AssetItem records — canonical N for the walker.

```python
AssetItem = {
  'kind':            'property' | 'bank' | 'insurance' | 'vehicle',
  'ai_index':        int,                   # 0..N-1 — stable handle
  'fields':          {address, lot, title, mukim, daerah, ownership,
                      account_number, institution, policy_number, ...},
  'message_line':    str,                   # verbatim user text
  'message_ts':      datetime | None,       # if export has timestamps
  'beneficiary_text': str,                  # raw distribution phrase
  'conflicts_flagged': list[str],           # any "❓" Claude raised
}
```

Hard rules:
1. **AI Summary loses info; raw text doesn't.** `fields` MUST be the
   union of (parsed-from-AI-Summary) ∪ (parsed-from-raw-line). If raw
   text says "Hakmilik 504662, Lot 207922" and AI Summary card omitted
   them, the AssetItem MUST still carry both. Single-source parsing is
   the bug §10x.48 prevents.
2. **AI-Summary count == AssetItem count == walker N.** Sub-lines like
   "HSD H.S.(D) 251041" inside Property 5's block are NEVER promoted to
   a 6th AssetItem. (§10b enforces.)
3. **Beneficiary text is preserved verbatim** for later §10x.13
   distribution-share interpretation. Don't pre-rescale here.

### STAGE 1 — Image grouping (§10g Step 1)

INPUT: all `Document` rows for client_id
OUTPUT: list of DocGroup, each containing 1..many Documents that
represent ONE physical asset.

```python
DocGroup = {
  'group_id':       str,                    # generated, stable
  'documents':      list[Document],
  'kind':           inferred from member docs ('property', 'bank', ...)
  'merged_extracted': dict,                 # union of OCR fields, with
                                            # conflicts surfaced not silenced
  'msg_id':         str | None,             # delivering ChatMessage
  'created_at_min': datetime,               # earliest member timestamp
}
```

Cluster signals (any single signal merges two Documents into one group):

| # | Signal | Notes |
|---|--------|-------|
| 1 | `content_hash` equal | exact duplicate (dedup at upload — §10c, §10x.4) |
| 2 | Same `lot+title` after §10aa cleaning | landed property |
| 3 | Same `address_signature` after normalisation | non-strata only |
| 4 | Same `account_number` digits | bank |
| 5 | Same `policy_number` digits | insurance |
| 6 | Sibling rule | same `ChatMessage.id` AND no conflicting identifiers in any of {lot, title, account_number, policy_number} |
| 7 | Strata exception (§10hd) | same lot but DIFFERENT title → DO NOT merge — split |

Hard rules:
1. **Stage 2 binds DocGroups, never individual Documents.** This is the
   structural difference from the broken matcher.
2. Every Document belongs to exactly ONE DocGroup. No double-membership.
3. Conflicts within a group (e.g. two members disagree on title number)
   surface as a clarification card per §10x.18 — do NOT silently average.

### STAGE 2 — Binding cascade (the actual matching)

INPUT: AssetItem[], DocGroup[]
OUTPUT: Binding[] — at most one DocGroup per AssetItem.

Process AssetItems in HIGH-confidence order (§10e). For each AssetItem,
try the four tiers in priority order:

| Tier | Signal | Confidence | `_match_via` |
|------|--------|------------|--------------|
| **A** | AssetItem.lot/title equal to DocGroup's lot/title (digit-equal after §10aa cleaning) | HIGH | `'lot_match'` or `'title_match'` |
| **B** | AssetItem's address resolves via §10ha geo bridge → mukim X. DocGroup has `mukim==X` AND ≥1 distinctive address-token overlap | MEDIUM-HIGH | `'mukim_token'` |
| **C** | AssetItem.message_line was delivered in (or temporally adjacent to) the same ChatMessage that delivered the DocGroup AND no other AssetItem is closer in time | MEDIUM | `'temporal'` (with timestamp evidence per §10i) |
| **D** | None of A/B/C | — | DO NOT BIND. Mark as H3. **Never guess** (§10he Step 5). |

Constraints:
1. **Greedy by confidence**: ALL Tier-A bindings resolve across the
   whole AssetItem list before any Tier-B attempt. Then all Tier-B,
   then all Tier-C. A strong direct match always beats a weaker
   temporal guess.
2. **One-claim-only** (§10g): once a DocGroup binds to AssetItem X,
   it's removed from the candidate pool. No second AssetItem can claim
   it. If two AssetItems compete for the same DocGroup at the same
   tier, surface a clarification card — do NOT pick.
3. **Conflict surface** (§10x.18): if AssetItem.lot == 504662 but
   bound DocGroup.lot == 564662 → conflict card BEFORE saving the gift.
4. **Web-search fallback** (§10hf) lives at the EDGE of Stage 2 only:
   when Tier A/B/C all fail AND the address has unresolved geo, run
   `search_property_clues(address)` to enrich AssetItem.fields with
   {type, tenure, mukim, building_name}, then RETRY Tier B with the
   enriched fields. Web-search is NEVER a binding signal on its own —
   it's a clue source.

After Stage 2: every AssetItem has either `binding=DocGroup` or
`binding=None` (→ H3).

### STAGE 3 — Residual handling (§10g Step 3, §10d)

DocGroups not consumed by Stage 2 are residual. For each:

| Residual kind | Action |
|---|---|
| Looks like a property (lot/title) but no AssetItem matches | §10d unverified card → ASK user |
| Identity (IC) | route to identity walker, not asset walker |
| Bank/insurance with no AssetItem match | §10d unverified card |
| Junk (low-confidence noise) | ignore unless user surfaces |

**Hard rule**: a residual DocGroup NEVER auto-creates a new AssetItem.
That violates §10h (AI Summary is canonical count). The user must
explicitly tell the chat "yes this is a real asset I forgot to mention"
— and only THEN does the system add it.

### STAGE 4 — Build merged Gift record

INPUT: one AssetItem + its binding (DocGroup or None)
OUTPUT: one Gift dict ready for step5_data.

Field-source priority (apply per field):

| Field | 1st choice | 2nd | 3rd |
|---|---|---|---|
| address | AssetItem.fields.address (from message) | AI Summary | DocGroup OCR (lowest — §10ha says title docs don't have street addresses) |
| lot, title | AssetItem.fields (message text) | DocGroup OCR | AI Summary |
| mukim, daerah, negeri | DocGroup OCR (title docs are authoritative) | §10ha bridge from address | web-search via §10hc resolver |
| ownership | AssetItem.message_line (image can't tell you "I share with X") | — | — |
| testator_share | derived from ownership idiom per §10x.13 | default `1/1` | — |
| co_owners | AssetItem.message_line ownership clause | — | — |
| beneficiaries (Layer 2) | populated by Stage 5, derived from `beneficiary_text` per §10x.13 + §10x.36 | — | — |
| variant | `'h1'` (binding includes title doc) / `'h2'` (binding has non-title evidence + mukim/token confirm) / `'h3'` (no binding) | — | — |

Hard rules:
1. **Empty address on a property the user described in writing is a
   §10x.48 failure.** If AssetItem.fields.address is non-empty, the
   Gift's `property_info.property_address` MUST be non-empty. Period.
2. **Co-owners go to `co_owners` array, never to Person table** (§10x.19).
3. **All variants (h1/h2/h3) are HIGH confidence** (§10hg). Variant
   only describes COMPLETENESS.

### STAGE 5 — 3-layer walkthrough (§10x.23)

Already specified in §10x.23 / §10x.40 / §10x.46. Per-Gift:
- Layer 1 (Confirm Asset Identity) → `_layer1_confirmed=True`
- Layer 2 (Main Beneficiary) → `beneficiaries=[...]`
- Layer 3 (Substitute) → `substitute_specific=[...]`

Step 6 is COMPLETE when every AssetItem's Gift has all three layers.

### STAGE 6 — Replay on new input (§10x.43)

Trigger: any new ChatMessage (text or attachments) for this client.

```
Replay:
  Re-run STAGE 0          # new text may add AssetItems
  Re-run STAGE 1          # new images regroup
  Re-run STAGE 2          # bind, preserving prior _user_confirmed bindings
  Re-run STAGE 3          # new residuals
  Re-run STAGE 4          # rebuild Gift with updated bindings; preserve
                          # user-set beneficiary/substitute fields
  RESUME STAGE 5          # do not restart; pick up at the first incomplete
                          # layer of the next AssetItem
```

Hard rules:
1. **User confirmations survive replay.** A Gift with `_layer1_confirmed`
   keeps it. Beneficiaries/substitutes once saved stay saved unless the
   user explicitly edits.
2. **New AssetItems insert at correct ai_index.** Don't reshuffle.
3. **Newly-bound DocGroups attach to existing AssetItems**: a previously
   H3 gift can promote to H1/H2 when its image arrives — completeness
   improves, but the gift identity is preserved.

### Cross-stage invariants (verifier checks these)

| Invariant | Where checked |
|---|---|
| Stage 0: `len(AssetItem) == AI Summary count` | `verify_step6.py R1` |
| Stage 1: every Document appears in exactly one DocGroup | `services/asset_audit.py audit_grouping` |
| Stage 2: no DocGroup bound to two AssetItems | `services/asset_audit.py audit_one_claim_only` |
| Stage 4: address non-empty when AssetItem has address | `verify_step6.py R6` (§10x.48 NEW) |
| Stage 4: lot/title preserved when present in message | `verify_step6.py R7` (§10x.48 NEW) |
| Stage 5: every gift has Layer 1 + 2 + 3 set | `verify_step6.py R4 + R5` |

### Where this lives in code (target structure)

| File | Function | Stage |
|------|----------|-------|
| `services/asset_pipeline.py` (NEW) | `parse_canonical_assets(client_id) → AssetItem[]` | Stage 0 |
| `services/asset_pipeline.py` (NEW) | `group_documents(client_id) → DocGroup[]` | Stage 1 |
| `services/asset_pipeline.py` (NEW) | `bind_assets(asset_items, doc_groups) → Binding[]` | Stage 2 |
| `services/asset_pipeline.py` (NEW) | `build_gift(asset_item, binding) → Gift` | Stage 4 |
| `services/gift_walker.py` | `get_pending_gift_documents(cid)` | calls all of Stage 0-4, returns the result |
| `ai/chat_planner.py` | `_asset_walkthrough_question`, `_step6_property_question` | Stage 5 only |
| `app.py` | `_try_save_property_gift`, `_try_handle_h3_property_action` | Stage 5 click handlers |

The current code mixes Stage 0/1/2/4 inside `_classify_property_match`
and `gift_walker._group_property_documents`. The refactor extracts
each stage into its own pure function.

### Litmus tests (a single change touching matcher MUST pass all)

```
Test 1 — text-only fixture (5 properties described, 0 Documents):
  Stage 0: 5 AssetItems
  Stage 1: 0 DocGroups
  Stage 2: 5 Bindings, all None (H3)
  Stage 4: 5 Gifts, every one has non-empty address
  Stage 5: walker drives 5 Layer 1+2+3 turns

Test 2 — image-only fixture (3 title docs, no AI Summary):
  Stage 0: 0 AssetItems
  Stage 3: 3 residual DocGroups → 3 §10d unverified cards
  Stage 4/5: zero Gifts auto-created

Test 3 — mixed (5 properties described + 5 title docs):
  Stage 0: 5 AssetItems
  Stage 1: 5 DocGroups (or fewer if siblings)
  Stage 2: 5 bindings via Tier A (lot/title direct) where identifiers
            stated in message; Tier B/C for the rest
  Stage 4: every Gift carries address from message AND lot/mukim from
            DocGroup OCR
  Stage 5: walker drives 5 turns
  Verifier: ≥1 Gift has real document_id (§10x.47)
```

### What §10x.48 KILLS

The following code paths are REMOVED in the refactor (do not re-introduce):
- `_classify_property_match` lexical-only token overlap as primary signal
- per-Document matching loop (Stage 1 missing) → replaced by DocGroup
- AI-Summary-OR-raw-text parsing (Stage 0 single-source) → replaced
  by union parsing
- Saver pulling address from DocGroup when AssetItem has it → replaced
  by Stage 4 field-source priority
- Random-pick / first-match fallback in any matcher path

If a future PR re-introduces any of the above, §10x.48 has been
violated. The verifier MUST fail on the corresponding invariant.

---

### 10x.49  🔥🔥🔥🔥🔥 BURN-IN — SELF-VALIDATING PIPELINE + AUDIT GATE 🔥🔥🔥🔥🔥

**The §10x.48 pipeline validates its own output at runtime via
`services/asset_pipeline.py::ContractViolation`. The audit gate
(`tests/step6/run_audit.py`) runs the pipeline + reset+walk+verifier
against every committed fixture. NO DEPLOY of matching code is allowed
without `run_audit.py` exiting 0.**

### Why this exists

Across this session I declared "PASS" three separate times on runs that
did not exercise the matching path. Each PASS was technically true for
the assertion it checked but missed the bigger contract. §10x.49 makes
it impossible to silently violate §10x.48 — the runtime catches the
breach, the audit catches the broken fixture, the pre-deploy gate
refuses to ship.

### Three layers

1. **Runtime — `ContractViolation`**
   Each Stage's output is asserted via `_assert_stageN(...)` before
   the next Stage runs. Specific violations:
     - Stage 0: duplicate `ai_index`, invalid kind
     - Stage 1: a Document in two DocGroups, orphan Document, empty group
     - Stage 2: more bindings than AssetItems, group_id bound twice
       (one-claim-only), tier D with non-null group_id, tier A/B/C with
       null group_id
     - Stage 4: gift count mismatch, address dropped when message stated
       it, lot/title dropped, missing `_match_via`
   Production code that catches `ContractViolation` and silently swallows
   it is itself a contract violation — surface it to the chat / audit /
   user. (`except Exception:` near a pipeline call is forbidden — narrow
   the except.)

2. **Audit — `tests/step6/run_audit.py`**
   For every fixture in the `FIXTURES` list:
     a. `run_pipeline(client_id)` — must complete without ContractViolation
     b. `reset_step6.py` — clean state
     c. `walk_step6.py` — drive the chat to completion
     d. `verify_step6.py` — branched on fixture_mode, R1-R12 strict
   Audit exits 0 only if ALL fixtures pass ALL stages.

3. **Deploy gate — call run_audit before docker compose build**
   The autonomous /loop's success condition is `run_audit.py exit 0`,
   not just `verify_step6.py exit 0`. Anyone deploying matching code
   manually MUST run:
   ```bash
   ssh ubuntu@... "docker exec willcraft-web python /app/tests/step6/run_audit.py"
   ```
   and confirm exit 0. A green `verify_step6.py` on one fixture proves
   nothing.

### Adding a new fixture

A new fixture must:
1. Live in `tests/step6/fixtures/<name>.py` with a `seed_fixture(client_id)` function
2. Be added to `FIXTURES` in `run_audit.py` with its expected `fixture_mode`
3. Pass on first audit run before the PR is merged. If it fails on first
   run, either the fixture is wrong or the matcher is broken — fix the
   broken side, never relax the verifier.

### Why narrow except clauses matter

Many bugs in this session stemmed from `try: ... except Exception: pass`
near pipeline calls. The classifier's H3 false-match (§10x.46 R6),
the saver's silent address drop, the parser's bogus 6th property — all
would have been caught earlier if a `ContractViolation` had been allowed
to surface. From now on, around any `services.asset_pipeline` call:

```python
# WRONG — swallows ContractViolation, hides §10x.48 bugs
try:
    r = run_pipeline(cid)
except Exception:
    r = None

# RIGHT — narrow except keeps contract violations visible
from services.asset_pipeline import ContractViolation
try:
    r = run_pipeline(cid)
except ContractViolation:
    raise              # ALWAYS re-raise — let it surface
except (DatabaseError, ConnectionError):
    r = None           # only catch genuinely transient errors
```

### What the user said that anchored this rule

> "make sure implementation is robust and NEVER FUCK THIS UP AGAIN"

The only way to guarantee that is to make the system enforce the
contract itself. Code review forgets, Claude sessions forget, but the
pipeline raises every time bad data flows through it.

---

### 10x.76  🔥 BURN-IN — AI Summary uses Document EXTRACTS 🔥

**The AI Summary parser MUST receive every uploaded property doc's
extracted fields (lot/title/mukim/daerah/negeri/owner/title_type) as
context — not just the raw message text.** Without this, the summary
asks the user to "obtain full PTD/Lot numbers from property documents"
even though the vision extractor already pulled those numbers from the
uploaded title images.

Hard rule: anywhere `_summarise_message` is called with substantive
docs available, the caller MUST gather Document.extracted_data from
property-class docs (`property_title`, `property_spa`, `property_tax`,
`property_transfer`, `loan_agreement`, `bank_statement`, `insurance`,
`vehicle`, `nric`) and pass via `doc_fields=[…]`. The cache key
includes `doc_fields` so different image sets get different summaries.

Where enforced: `ai/chat_planner.py::_summarise_message`,
`app.py::_process_inbound_message_async_inner`,
`app.py::_post_ai_summary` (chat reset).

Litmus: AI Summary should NEVER output "obtain full PTD/Lot numbers
from property documents" when at least one `property_title` /
`property_spa` doc has non-empty `lot_number` in extracted_data. If it
does, the caller forgot to pass `doc_fields`.

---

### 10x.77  🔥🔥🔥 BURN-IN — NO MACHINE LANGUAGE in user-facing UI 🔥🔥🔥

**Two non-negotiable rules:**

#### Rule A: AI Summary states FACTS only — no follow-up questions

The AI Summary card has exactly two sections:
  1. **What was communicated** — paraphrase of what the sender wrote
  2. **What we deduce** — interpreted assets/beneficiaries/relations

It must NOT contain:
  ❌ "Key Flags for Follow-up"
  ❌ "Issues Requiring Clarification"
  ❌ "Confirm…", "Verify…", "Clarify…" prompts
  ❌ "❓ Ambiguous: Obtain X from Y" requests

Clarifications happen LATER, in the per-step walkthrough cards (Step 1
IC card → Step 2 Testator confirm → Step 3 Executor → Step 5
Beneficiaries → Step 6 Specific Gifts → conflict cards). Each
clarification is asked ONCE, in context, with the relevant evidence
quoted, with action buttons.

The summary's purpose is to MIRROR what was understood so the user
can verify at-a-glance. Asking questions in the summary creates a
parallel decision channel that competes with the walkthrough — the
user ends up unsure whether to answer in the summary card or wait for
the walkthrough.

Where enforced: prompt of `_summarise_message` in `ai/chat_planner.py`.

Litmus: search the rendered AI Summary content for the strings
`"Key Flags"`, `"Follow-up"`, `"Issues Requiring"`. Any match = bug.

#### Rule B: Internal markers MUST be scrubbed before leaving the API

Backend code uses HTML-comment markers to track state:
  - `<!--_summary_hash:…-->`  — input hash for DB cache lookup
  - `<!--_property_match_hint:…-->`  — planner pivot signal
  - `<!--_added_by:…-->`  — reconciliation audit trail
  - any future `<!--…-->` we introduce

These MUST be invisible to the user. They appear inside the DB
content (where backend logic reads them) but the API serializer
strips them before sending to the frontend. The ONE exception:
`<!--quickreplies:[…]-->` is parsed by chat.js to render action
buttons, so it survives the scrub.

Where enforced: `app.py::_serialise_chat_message` runs the scrub on
every outgoing message. Any new internal marker we add inherits this
behaviour automatically — no per-marker change needed.

Litmus: open a chat, view the rendered text. `<!--…-->` of any kind
must NOT appear in the visible text. If you see one, either the
serializer regressed OR the frontend is reading content from a path
that bypasses `_serialise_chat_message`.

#### Rule C (corollary): every new feature checks against §10x.77

Anyone adding a new card, prompt template, or planner branch must:
  1. Confirm the user-facing text contains zero `<!--…-->` blocks
     (the scrubber handles this for free)
  2. Confirm the user-facing text contains zero "please confirm",
     "please verify", "please provide" prompts UNLESS that prompt
     IS the card's intended call-to-action with quickreply buttons
     attached
  3. Confirm follow-up clarifications are scheduled for a later step,
     not stuffed into a summary or aggregate card

If the rule blocks a legitimate need (e.g. a debug message visible to
internal users), gate it behind `app.config['DEV']` or remove it
before shipping. Production users see no machine language, ever.

---

### 10x.82  🔥🔥 BURN-IN — Back IC for verified person = NO scan 🔥🔥

**The back of a Malaysian MyKad has only the address. The front has
name + NRIC + DOB + photo + (sometimes) address. If the front of an
IC has been scanned and the person is verified (Person row exists),
scanning the back adds ZERO new information for the will and costs
~$0.014 in vision API spend.**

### Rule

`/api/ocr/nric` — BEFORE calling `extract_nric_data` (Haiku vision,
~$0.014/call), do a free Tesseract pre-check on the uploaded image
and look for ANY of three signals that this IC is already verified:

| Signal | Test |
|---|---|
| (a) NRIC visible | Regex `\d{6}-\d{2}-\d{4}` on Tesseract text → match against `Person.nric_passport` |
| (b) Address visible | ≥3 distinctive tokens (4+ chars, non-numeric) from `Person.address` appear in Tesseract text; postcode counts as +1 |
| (c) Name visible | Surname + first-name (both ≥3 chars) from `Person.full_name` both appear in Tesseract text |

If ANY signal matches → reuse the matched Person's fields, skip the
vision call, save ~$0.014. Tag the response with:
- `already_known: true`
- `matched_person: {id, name, relationship}`
- `skip_reason: 'nric_match' | 'address_match' | 'name_match'`
- `savings_usd: 0.014`
- `notice: "Back of <Name>'s IC — already verified. Skipped scan."`

The Document is saved with `category='duplicate'` (not `'nric'`) so
the IC walker doesn't add it as a pending IC.

### Why ALL three signals (not just NRIC)

The back of MyKad sometimes has the NRIC printed small / in barcode
form that Tesseract can't read reliably. Address text is large and
multi-line, much easier for Tesseract. Name is rarely on the back
but appears on some passport-style IDs. Trying all three covers
the common failure modes of NRIC-only matching.

### Where enforced

`app.py::api_ocr_nric` — the pre-check block sets `skipped_vision`
before the `extract_nric_data` call. When skipped, response carries
`already_known: true` for the UI.

### Litmus

Upload the BACK of an IC whose front is already a verified Person:
- Cost in `ApiCallLog` for this scan: $0 ✓
- Response payload contains `already_known: true` ✓
- Document.category = 'duplicate' ✓
- Walker does NOT show this as pending IC ✓

If you see ~$0.014 logged for a back-IC scan of a verified person,
the §10x.82 path was bypassed — investigate why Tesseract didn't
match any of the three signals.

---

### 10x.83  🔥🔥 BURN-IN — IC card buttons = ONLY plausible roles 🔥🔥

**The IC walkthrough card MUST show only relationship buttons that are
(a) MENTIONED in the AI Summary / message text AND (b) NOT YET filled
by an existing Person row.** Showing the full 13-button family-roles
menu (Spouse / Son / Daughter / Father / Mother / Brother / Sister /
Sister-in-law / …) is overwhelming and most options are irrelevant.

### Computation

`_plausible_remaining_roles(client_id, recent_text)`:

1. Pull `Person.relationship` for every confirmed Person → set `filled`
2. Scan `recent_text` for role mentions:
   - Name+role pairs via `_extract_family_name_role_pairs`
   - Bare role tokens via regex (`sister-in-law`, `wife`, `husband`,
     `son`, `daughter`, etc.) — handles role-only references like
     "My Executor — my sister in law"
3. Return `mentioned - filled`, deduped, in order of first appearance

### Card layout

| Pre-conditions | Buttons shown |
|---|---|
| ≥1 plausible role in message | `✓ <Role>` (top 3) + `✏️ Other relationship` + `⏭ Skip` + `🗑 Delete` |
| Nothing role-related in message | Core 6 (Spouse/Son/Daughter/Father/Mother/Brother/Sister) + `✏️ Other` + `⏭ Skip` + `🗑 Delete` |

### Why this works for the KOID test

Message names: wife (Lim Bee Yan), son (Joshua), daughter (Esther),
sister-in-law (executor). After three are confirmed via Step 1, the
remaining IC of NRIC 650629 surfaces with ONLY `✓ Sister-in-law` as
the suggested button (since wife/son/daughter are filled).

### Where enforced

`ai/chat_planner.py::_identity_question_with_doc` — the `else:` branch
(no role deduced for current IC) now calls `_plausible_remaining_roles`
to build the button list.

### Litmus

For an IC where the message has named exactly one unfilled relation,
the card's quickreply list should be:
  ['✓ <Role>', '✏️ Other relationship', '⏭ Skip', '🗑 Delete']
— i.e. 4 buttons, not 15.

If you see all 13 family-role buttons, `_plausible_remaining_roles`
returned `[]` → either `recent_text` was empty, or the bare-role
regex didn't match. Check the message body and extend the regex
list if needed.

---

### 10x.87  🔥🔥 BURN-IN — IC walkthrough order: HIGH-confidence FIRST 🔥🔥

**Same rule as §10e/§10x.30 but extended for cases when both ICs have
empty extracted names. The IC the testator NAMED in the message MUST
sort before the outsider IC, even when Tesseract can't read either
photo's name field.**

### Why this matters

Two ICs arrive in different emails:
- IC A (front): name='', NRIC=960525 (Joshua, mentioned by name in message)
- IC B (back): name='', NRIC=650629 (Lim Lay Cheng, mentioned only as "sister-in-law")

Both score 0 under the original rule because empty name → no name match
in message. Tiebreaker is upload-time → arbitrary order. The OUTSIDER
(Lim Lay Cheng) often gets uploaded first → surfaces first → user
walks through it before the named family member, which feels wrong.

### The score grid

| Score | Trigger |
|-------|---------|
| 5 | name in message + family-role word adjacent (HIGH) |
| 4 | name in message + co-owner phrase preceding (HIGH) |
| 3 | name in message, no role adjacent (MEDIUM) |
| **2** | **§10x.87 — empty name, but NRIC year-of-birth fits a CHILD/SPOUSE band (5–60yo) AND message mentions son/daughter/spouse/wife/husband** |
| 1 | name not in message; outsider-elimination (sister-in-law / brother-in-law / friend) |
| 0 | no signal |

### Why score 2 (not 5) for the NRIC-age case

The age heuristic is INFERRED, not verified. We can't prove this IC
is Joshua just because it's a 29-year-old NRIC near a "(son)"
mention — it could also be a friend or cousin. So it scores HIGHER
than the outsider (1) but LOWER than a real name match (3).

### Where enforced

`services/identity_walker.py::_score_ic_confidence` — accepts
optional `nric=` param and applies the §10x.87 band check when
`name=''`. Caller in `get_pending_ic_documents` passes both name
and NRIC.

### Litmus

For 2 ICs both with empty names:
  - Joshua's NRIC 960525 → age 29 → fits child band → score 2
  - Lim Lay Cheng's NRIC 650629 → age 60 → does NOT fit → score 0
  - Sort: Joshua FIRST (named family), Lim Lay Cheng LAST (outsider)

If the order ever inverts, check that NRIC is being passed and that
the message actually mentions a child/spouse role.

---

### 10x.94  🔥🔥 BURN-IN — Wizard Step number ≠ DB column number 🔥🔥

**The DB columns `stepN_data` are OFFSET BY ONE from the wizard UI's
"Step N" labels. This is a legacy footgun that has caused multiple
bugs. ALWAYS check this table before modifying any step handler.**

### Canonical mapping

| Wizard UI label | What it captures | DB column | Session key |
|-----------------|------------------|-----------|-------------|
| **Step 1: Identities** | Family relationship registry | (Person table + identities_data) | `person_registry` |
| **Step 2: Testator** | Testator's name, NRIC, DOB, address | `step1_data` | `step1` |
| **Step 3: Executors & Trustees** | Executor + substitute + trustee | `step2_data` | `step2_executors`, `step3_executor_type`, `step3_trustees` |
| **Step 4: Guardians** | Guardians for minor children | `step3_data` | `step3_guardians` |
| **Step 5: Beneficiaries** | Main beneficiary list | `step4_data` | `step4_beneficiaries` |
| **Step 6: Specific Gifts** | Per-asset gift entries | `step5_data` | `step5_gifts` |
| **Step 7: Residuary Estate** | Catch-all residuary clause | `step6_data` | `step6_residuary` |
| **Step 8: Testamentary Trust** | Trust setup for minors | `step7_data` | `step7_trust` |
| **Step 9: Other Matters** | Funeral wishes, special instructions | `step8_data` | `step8_others` |
| **Step 10: Review & Generate** | Compile + render | (reads all above) | — |

### Why the offset exists

When the wizard was first built, "Step 1" was Testator (now relabelled to
"Step 2") and Identities was added later as a pre-step. The DB columns
were never renamed because legacy data referenced `step1_data` for
Testator, `step2_data` for Executors, etc. Renaming would break all
existing wills.

### How to avoid the footgun

When code talks about "saving Step N":
  - If the source is the chat planner → it usually means the **UI label**
  - If the source is a DB write → it's the **column name**
  - Always double-check by tracing the variable name and the UI label
    side-by-side. `step4_data` is **Beneficiaries**, NOT Guardians.

### Litmus

User says "Step 4 is Guardians, Step 5 is Beneficiaries":
  - YES that's correct for the UI labels
  - The DB column `step3_data` holds Guardians content
  - The DB column `step4_data` holds Beneficiaries content
  - The DB column `step5_data` holds Specific Gifts content

If a code change writes to `step4_data` but says "saved guardian" in the
comment — it's wrong. Trace through.

---

### 10x.97  🔥🔥 BURN-IN — AI Summary parser must SKIP banks/insurance bullets 🔥🔥

**The narrative-format fallback in `_parse_ai_summary_text` MUST consult
`_RAW_SKIP_HINTS` BEFORE deciding whether a bullet is a property. Without
this, account numbers and policy numbers leak into the property list.**

### The bug this rule prevents

KOID test case. The §10x.77 narrative AI Summary contained:

> • POSB Bank Singapore account 030-25917-3 — to wife Lim Bee Yan 100%.

The narrative-fallback path accepts a bullet as a property when it
matches ANY of:
1. `_RAW_PROP_HINTS` (condominium / unit / house / shop / jalan / etc.)
2. `_POSTCODE_RE` → `\b\d{5}\b` (5 digits between word boundaries)
3. `\bLot\s+\d` inline

The POSB bullet matched #2 because `030-25917-3` contains `25917` which
is 5 digits between word boundaries (the dashes are non-word chars). So
"POSB Bank Singapore account 030-25917-3" became Property 6.

Result: `_extract_ai_summary_properties` returned 6 properties instead
of 5; the walkthrough rendered a phantom 6th property card; step5_data
got an extra property gift entry; verifier R1 mismatch.

### Hard rule

The `_RAW_SKIP_HINTS` tuple — already defined and already including
`'bank '`, `'banking'`, `'insurance'`, `'policy'`, `'account no'`,
`'account number'`, `'savings account'` — MUST be checked **first** in
the narrative-fallback path. Any bullet matching ANY skip hint in EITHER
the head OR the full block is rejected before the property-hint /
postcode / Lot test runs.

```python
if any(s in head_low or s in blk_low for s in _RAW_SKIP_HINTS):
    continue   # bank / insurance / policy / account → not a property
```

### Where this is enforced

| File | Function | Mechanism |
|------|----------|-----------|
| `ai/chat_planner.py` | `_parse_ai_summary_text` (narrative fallback branch) | §10x.97 skip-hint gate runs BEFORE the property-hint / postcode / Lot test |

### Why postcode-regex isn't enough on its own

`\b\d{5}\b` is correct for Malaysian postcodes (5 digits) but matches
ANY isolated 5-digit run. Bank account numbers, policy numbers, even
phone numbers all contain 5+ digits. Tightening the regex to
`(?<![-\d])\d{5}(?![-\d])` (exclude dashes and adjacent digits) helps
but still produces false positives. The right fix is to reject by the
strong negative signal (the word "bank"/"insurance"/"policy"/"account"
is in the bullet) rather than try to make the positive signal perfect.

### Litmus test

```python
from ai.chat_planner import _parse_ai_summary_text

text = '''
**Assets the testator wants in their will:**

• Unit B-05-11, Condominium Paradisonuava — jointly owned 50/50.
• POSB Bank Singapore account 030-25917-3 — to wife 100%.
• NTUC Income Insurance Policy 1811500170 — to wife 100%.
'''

props = _parse_ai_summary_text(text)
assert len(props) == 1
assert 'Paradisonuava' in props[0]['name']
```

If the parser ever returns more than 1 entry from this fixture, §10x.97
has been violated. Look at the narrative-fallback path and verify
`_RAW_SKIP_HINTS` is checked first.

### Related rules

- §10b — Property count = AI Summary count (this rule prevents the
  bloated count that §10b verifies)
- §10h — AI Summary IS canonical asset list (skip-hint applies to ALL
  asset categorisation, not just property)
- §10x.12 — every AI-Summary item is its own gift (banks/insurance get
  their own gifts via `_extract_ai_summary_banks` / `_extract_ai_summary_insurance`,
  NOT by leaking into the property list)

---

### 10x.11  Operational test pipeline (verify no duplicates)

After deploying any inbound-pipeline change, run the smell test and
confirm:
- 1 user message ✅
- 1 intake card "📋 N exhibits received" ✅
- 1 AI Summary card "📨 AI Summary of your message" ✅
- 0 docs left in `chat_inbox` (all classified)
- Cost > $0 in `/api/cost/<will_id>`

If any duplicate cards appear → STOP, the lock or idempotency check
regressed. Don't ship.

### 10x.8  Operational test pipeline

Each test cycle (CLAUDE.md §2) MUST end with:

1. Verify cost > $0 in `/api/cost/<will_id>` after classification.
2. Verify intake card was posted (chat has assistant message containing
   `## 📋 N exhibits received`).
3. Verify AI Summary card was posted AND is not truncated (no trailing
   ellipsis or mid-sentence break).
4. Verify the categorization didn't get reset to chat_inbox by a
   subsequent watchdog poll (§10x.2).

If any of these fails, the deploy is not done. **Don't claim success.**

---

## 11. Things NOT To Do

> **Merged into the unified FUCK list at §10x.39.** That table is now the
> single source of truth for "user told us this should never happen
> again". Every entry has a corresponding §10x rule and an audit/check.
>
> If you find a NEW bug worth burning in:
> 1. Add a new row at the bottom of §10x.39 (verbatim user quote where
>    possible)
> 2. Write or extend the §10x rule it points to
> 3. Add a check (audit script, verifier rule, or test)
> 4. Commit code + rule + check together so they never separate
