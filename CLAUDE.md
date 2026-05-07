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

### 10x.39  🔥🔥🔥🔥 BURN-IN — THE FUCK LIST 🔥🔥🔥🔥

**Bugs the user has explicitly called out as "must never resurface".
This list exists because rules in CLAUDE.md alone weren't enough — the
code regressed silently. Every entry below has a corresponding burn-in
rule + an `asset_audit.py` / `identity_full_audit.py` check.**

### The FUCK list

Each entry has:
- The exact user complaint (verbatim where possible)
- The rule that exists to prevent it
- The audit check that catches its return

| # | What user said | Rule | Audit check |
|---|---|---|---|
| 1 | "EVERY SINGLE FUCKING UPDATE, SAVE IN CLAUDE.MD PERMANENTLY. DON'T FUCKING LOSE IT AGAIN" | §10x.33 + permanent burn-ins §10x.26-10x.39 | Pre-deploy `asset_audit.py` |
| 2 | "MESSAGE > IMAGE. ALWAYS." | §10x.35 | Audit checks pending count == AI Summary count |
| 3 | "MUST REFER TO MESSAGE: HIGHEST PRECEDENCE" | §10x.36 — every gift card MUST show 📨 message snippet | Visual check + CLAUDE.md §9 cross-reference |
| 4 | "Wizard step and AI chat step MUST MATCH" | §10x.38 — `_current_stage_num` must match planner gate ordering | Side-by-side check: chat card + right-pane indicator |
| 5 | "If skip, show back again until user select delete" | §10x.31 — Skip is no-op | `_chat_skipped` flag NEVER set on Skip click |
| 6 | "Identity in message must take precedence even without image" | §10x.34 + §10x.35 | Lim Bee Yan H3 placeholder works |
| 7 | "Step 1 IC walk only assigns family relations, not Executor" | §10x.32 | `_WILL_ROLES` filter in `_try_assign_pending_identity` |
| 8 | "Asset matching: HIGH confidence first, LOW last" | §10e + §10x.30 | Audit: pending property scores monotonically decrease |
| 9 | "Property count = AI Summary count, not 14, not 31" | §10b | Audit: `len(pending_props) == len(ai_props)` |
| 10 | "Every AI-Summary item must result in a gift entry" | §10x.12 | Audit: 5 props + 4 banks + 3 ins for KOID fixture |
| 11 | "Title docs do NOT show street addresses — get from message" | §10ha | `_persist_property_enrichment` matches by lot/title, not OCR addr |
| 12 | "Strata: same lot ≠ same property — group by (lot, title)" | §10hd | `_group_property_documents` strata branch |
| 13 | "Co-owner is NOT a family relationship" | §10x.19 | Co-owner stays in `property_info.co_owners`, never Person row |
| 14 | "Beneficiary % is of testator's SHARE, not full property" | §10x.13 | Deduce path accepts totals in {25, 33, 50, 66, 75} and rescales |
| 15 | "Substitute beneficiary defaults: spouse → both children, etc." | §10x.14 | `_default_substitute()` in walker |
| 16 | "Image is verification only — text alone is sufficient" | §10x.15 + §10x.35 | H3 placeholders synthesised for AI-Summary-only items |
| 17 | "Will-clause format MUST follow Phek Yi Ting standard" | §10x.24 | `sim_will_gen.py` snapshot test |
| 18 | "AI follows the saved template STRICTLY — no creativity" | §10x.25 | `template_filler.py` is deterministic, no LLM |
| 19 | "Watchdog must NEVER post duplicate cards" | §10x.9 + §10x.28 | Idempotency: 1 intake + 1 AI Summary, no dups |
| 20 | "Vision retry has terminal state — no infinite analysing" | §10x.26 | `_classify_attempts >= 3` → `needs_review` |
| 21 | "Pre-deploy asset audit MUST pass" | §10x.33 | `asset_audit.py` reconciliation checks |

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
- ❌ Matching addresses without ordering — must be HIGH confidence FIRST, claim greedily, never reuse a claimed address (see §10g)
- ❌ Letting a non-title image (Image B) get its own property card when it shares an address with a title image (Image A) that already claimed it
- ❌ **Identifying assets without first reading the AI Summary.** AI Summary is the canonical list — N properties in summary = N cards in walkthrough. NEVER invent a property whose address isn't in the summary. See §10h.
- ❌ **Reasoning from "recent_text" / message body when an AI Summary exists.** The summary has already canonicalised the property list — use it. Don't go back to the raw text and re-deduce.
- ❌ **Ignoring temporal proximity when content match fails.** If an AI-Summary property has no content-matching image, check the messages BEFORE/AFTER each unclaimed image. Adjacency is a strong link. See §10i.
- ❌ **Treating an OCR'd "address" on a title doc as real.** Title docs (Geran/HSD/PTD) do NOT show street addresses. The address always comes from the message / AI Summary. See §10ha.
- ❌ **Ignoring the mukim/daerah on the title doc when matching.** Mukim is the geographic bridge: e.g. Mukim Plentong contains Seri Alam Masai, Marina Cove, Taman Laguna, Permas Jaya. A title doc with Mukim=Plentong matches AI-Summary properties in any of those townships. See §10ha geographic bridge table.
- ❌ **Merging two strata titles by lot number alone.** Same lot + different title number = different units in the same building. Group by `(lot, title)` for strata, never just lot. Sibling enrichment must check title equality. See §10hd.
- ❌ **Hiding the WhatsApp timing on property cards.** When an image is bound to a property by adjacency, the card MUST show the timestamp of the image and the adjacent message so the user can verify the temporal link. See §10i.
- ❌ Treating `VALUE: GRN56662` or `VALUE: (unreadable)` as a real title number
- ❌ Trusting raw extracted_data without cleaning AI-noise prefixes first
- ❌ Saying "this is fixed" without running `get_pending_gift_documents()` against the actual client and counting properties
- ❌ **HALLUCINATING assets or beneficiaries when a document is isolated / unreadable / cannot be identified.** If the title number is unreadable, the address is missing, the lot is garbage, or the document cannot be tied to any property in the AI Summary — **STOP**. Do NOT invent a property. Do NOT invent a beneficiary. Do NOT fabricate an address (no "10 Marsiling Lane Singapore" pulled from thin air). Mark the document as **isolated / needs human review** and SKIP it from the walkthrough. The chat must say "couldn't identify this — review manually" rather than create a fictitious asset card.
