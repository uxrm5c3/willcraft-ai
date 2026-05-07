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
