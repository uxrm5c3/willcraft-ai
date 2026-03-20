# WillCraft AI - Integration Guide for NestedEggAdvisor

## Overview

WillCraft AI is a Malaysian will drafting platform. This guide documents how to integrate it with NestedEggAdvisor (https://nesteggadvisor.org) so client data flows from NestedEggAdvisor into WillCraft.

**Live instances:**
- https://will.lifa.com.my (LIFA tenant)
- https://will.alantanjb.com (AlanTanJB tenant)

**Tech stack:** Python/Flask, SQLite, Jinja2 templates, Tailwind CSS, Claude AI (Anthropic API)

---

## Integration Options

### Option A: REST API Integration (Recommended)

NestedEggAdvisor calls WillCraft API endpoints to create clients, populate will data, and retrieve generated documents. WillCraft handles all will drafting UI and AI generation.

**Flow:**
```
NestedEggAdvisor                        WillCraft AI
─────────────────                       ────────────
1. POST /api/integration/auth     →     Returns session token
2. POST /api/integration/create-will  →  Creates client + will + populates all steps
3. Redirect user to /wizard/step/10   →  User reviews & generates will
4. GET /api/integration/will-status   →  Poll for generated/approved status
5. GET /download/docx or /download/pdf → Download final document
```

### Option B: iframe Embed

Embed WillCraft in an iframe inside NestedEggAdvisor. Pass client data via URL params or postMessage.

```html
<iframe src="https://will.lifa.com.my/wizard/new?token=JWT_TOKEN" width="100%" height="800px"></iframe>
```

### Option C: Deep Link with JWT Token

NestedEggAdvisor generates a signed JWT containing client data. User clicks a link and lands in WillCraft with data pre-filled.

```
https://will.lifa.com.my/api/integration/prefill?token=eyJhbGci...
```

---

## API Endpoints to Build

The following endpoints need to be built on WillCraft for integration. They don't exist yet — this documents the spec for implementation.

### 1. Authentication

```
POST /api/integration/auth
Content-Type: application/json

{
  "api_key": "shared-secret-key",
  "tenant": "lifa"
}

Response:
{
  "ok": true,
  "session_token": "abc123...",
  "expires_in": 3600
}
```

### 2. Create Will with Pre-filled Data

```
POST /api/integration/create-will
Authorization: Bearer <session_token>
Content-Type: application/json

{
  "external_ref": "NEA-12345",        // NestedEggAdvisor reference ID

  "testator": {
    "full_name": "JOHN DOE",
    "nric_passport": "880515-01-5678",
    "residential_address": "NO. 1, JALAN BUKIT BINTANG, 55100 KUALA LUMPUR",
    "nationality": "Malaysian",
    "date_of_birth": "15-05-1988",
    "gender": "Male",
    "marital_status": "Married",
    "occupation": "Engineer",
    "email": "john@example.com",
    "phone": "+60123456789"
  },

  "identities": [
    {
      "full_name": "JANE DOE",
      "nric_passport": "900101-01-1234",
      "address": "NO. 1, JALAN BUKIT BINTANG, 55100 KUALA LUMPUR",
      "nationality": "Malaysian",
      "date_of_birth": "01-01-1990",
      "gender": "Female",
      "relationship": "Wife",
      "email": "jane@example.com",
      "phone": "+60198765432"
    },
    {
      "full_name": "JOHNNY DOE",
      "nric_passport": "K12345678",
      "address": "NO. 1, JALAN BUKIT BINTANG, 55100 KUALA LUMPUR",
      "nationality": "Malaysian",
      "date_of_birth": "15-06-2015",
      "gender": "Male",
      "relationship": "Son"
    }
  ],

  "executors": [
    {
      "full_name": "JANE DOE",
      "nric_passport": "900101-01-1234",
      "address": "NO. 1, JALAN BUKIT BINTANG, 55100 KUALA LUMPUR",
      "relationship": "Wife",
      "role": "Primary",
      "nationality": "Malaysian"
    }
  ],

  "beneficiaries": [
    {
      "full_name": "JANE DOE",
      "nric_passport_birthcert": "900101-01-1234",
      "relationship": "Wife",
      "nationality": "Malaysian"
    },
    {
      "full_name": "JOHNNY DOE",
      "nric_passport_birthcert": "K12345678",
      "relationship": "Son",
      "nationality": "Malaysian"
    }
  ],

  "gifts": [
    {
      "gift_type": "property",
      "property_details": {
        "property_address": "NO. 1, JALAN BUKIT BINTANG, 55100 KUALA LUMPUR",
        "title_type": "HSD",
        "title_number": "12345",
        "lot_number": "101",
        "bandar_pekan": "KUALA LUMPUR",
        "daerah": "KUALA LUMPUR",
        "negeri": "WILAYAH PERSEKUTUAN"
      },
      "ownership_type": "sole",
      "encumbrance_status": "clean",
      "allocations": [
        {
          "beneficiary_name": "JANE DOE",
          "share": "1/1"
        }
      ],
      "substitute_mode": "equal"
    },
    {
      "gift_type": "financial",
      "financial_details": {
        "institution": "Maybank",
        "account_number": "1234567890",
        "asset_type": "Savings Account"
      },
      "account_ownership": "individual",
      "allocations": [
        {
          "beneficiary_name": "JANE DOE",
          "share": "5/10"
        },
        {
          "beneficiary_name": "JOHNNY DOE",
          "share": "5/10"
        }
      ],
      "substitute_mode": "equal"
    }
  ],

  "residuary_estate": {
    "main_beneficiaries": [
      { "beneficiary_name": "JANE DOE", "share": "6/10" },
      { "beneficiary_name": "JOHNNY DOE", "share": "4/10" }
    ],
    "substitute_groups": [],
    "substitute_mode": "survivorship"
  },

  "other_matters": {
    "joint_account_clause_enabled": false,
    "commorientes_enabled": false
  }
}

Response:
{
  "ok": true,
  "will_id": "uuid",
  "client_id": "uuid",
  "redirect_url": "/wills/uuid/load?goto=step10"
}
```

### 3. Get Will Status

```
GET /api/integration/will-status?will_id=uuid
Authorization: Bearer <session_token>

Response:
{
  "ok": true,
  "will_id": "uuid",
  "status": "draft|generated|pending_approval|approved|rejected",
  "title": "Last Will and Testament of JOHN DOE",
  "created_at": "2026-03-20T20:35:00+08:00",
  "updated_at": "2026-03-20T20:35:00+08:00",
  "has_document": true,
  "download_urls": {
    "docx": "/download/docx",
    "pdf": "/download/pdf"
  }
}
```

### 4. Get Will Document

```
GET /download/docx
GET /download/pdf
Authorization: Bearer <session_token> (or cookie session)

Response: Binary file download
```

---

## Existing API Endpoints (Already Built)

These endpoints exist and can be used directly with cookie-based session auth:

### Person/Identity Management
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/persons` | List all persons for current client |
| POST | `/api/persons` | Create new person identity |
| PUT | `/api/persons/<id>` | Update person |
| DELETE | `/api/persons/<id>` | Delete person |

### Document/File Management
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/upload` | Upload file (NRIC, property doc, etc.) |
| GET | `/api/documents` | List all documents |
| GET | `/api/documents/<id>` | Download document |
| DELETE | `/api/documents/<id>` | Delete document |

### OCR (AI Document Scanning)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/ocr/nric` | Extract data from NRIC/passport image |
| POST | `/api/ocr/property` | Extract data from property document |
| POST | `/api/ocr/asset` | Extract data from bank statement |
| POST | `/api/ocr/death-cert` | Extract data from death certificate |

### Will Management
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/will/save` | Save current will to DB |
| POST | `/api/will/delete-generated` | Delete generated text, reset to draft |
| POST | `/api/will/version/<id>/delete` | Delete a specific version |
| POST | `/api/will/<id>/edit-text` | Edit will text directly |
| POST | `/api/will/<id>/redraft` | Regenerate will from data |
| POST | `/api/will/<id>/send-email` | Email will to client |

---

## Data Models Reference

### Testator (Step 2)
```json
{
  "full_name": "JOHN DOE",                       // Required, UPPERCASE
  "nric_passport": "880515-01-5678",              // Required
  "residential_address": "...",                    // Required, UPPERCASE
  "nationality": "Malaysian",                     // Default: "Malaysian"
  "date_of_birth": "15-05-1988",                  // DD-MM-YYYY format
  "gender": "Male|Female",
  "marital_status": "Single|Married|Divorced|Widow/Widower",
  "occupation": "Engineer",
  "email": "john@example.com",
  "phone": "+60123456789",
  "religion": "Buddhist",
  "property_coverage": "Malaysia|Overseas|Both",
  "contemplation_of_marriage": false,
  "fiance_name": null,
  "fiance_nric": null,
  "signing_method": "Signature|Thumbprint",
  "special_circumstances": ["Translator"],         // Array
  "translator_name": null,
  "translator_nric": null,
  "translator_language": "Bahasa Malaysia"
}
```

### Executor (Step 3)
```json
{
  "full_name": "JANE DOE",
  "nric_passport": "900101-01-1234",
  "address": "...",
  "relationship": "Wife",
  "role": "Primary|Joint|Substitute",
  "nationality": "Malaysian"
}
```

### Guardian (Step 4) — Optional
```json
{
  "full_name": "GUARDIAN NAME",
  "nric_passport": "...",
  "address": "...",
  "relationship": "Sister",
  "role": "Primary|Joint|Substitute",
  "nationality": "Malaysian"
}
```

### Guardian Allowance — Optional
```json
{
  "payment_mode": "Monthly|Quarterly|Yearly|One-Off|Discretion of Executor/Trustee|Other",
  "amount": "RM 500",
  "until_age": 21,
  "source_of_payment": "From the residuary estate"
}
```

### Beneficiary (Step 5)
```json
{
  "full_name": "JANE DOE",
  "nric_passport_birthcert": "900101-01-1234",
  "relationship": "Wife",
  "nationality": "Malaysian"
}
```

### Gift (Step 6)
```json
{
  "gift_type": "property|financial|other",

  "property_details": {
    "property_address": "NO. 1, JALAN BUKIT BINTANG, 55100 KUALA LUMPUR",
    "title_type": "HSD|HSM|GRN|EMR|PM|PN|PAJAKAN|Strata",
    "title_number": "12345",
    "lot_number": "101",
    "bandar_pekan": "KUALA LUMPUR",
    "daerah": "KUALA LUMPUR",
    "negeri": "WILAYAH PERSEKUTUAN"
  },

  "financial_details": {
    "institution": "Maybank",
    "account_number": "1234567890",
    "asset_type": "Savings Account|Fixed Deposit|Unit Trust|Shares|EPF|Insurance",
    "description": ""
  },

  "description": "My Rolex watch",              // For "other" gift type

  "ownership_type": "sole|joint",
  "testator_share": "1/2",                      // For joint ownership
  "joint_owners": "Jane Doe",                   // For joint ownership
  "account_ownership": "individual|joint",       // For financial gifts
  "encumbrance_status": "clean|encumbered",
  "debt_source": "residuary|sale|insurance",     // If encumbered
  "sell_property": false,
  "subject_to_trust": false,

  "allocations": [
    {
      "beneficiary_name": "JANE DOE",
      "share": "1/1",                           // Use fractions: 1/2, 3/10, etc.
      "substitutes": [                           // Only for substitute_mode="specific"
        { "beneficiary_name": "JOHNNY DOE", "share": "1/1" }
      ]
    }
  ],

  "substitute_mode": "equal|prorata|specific"
}
```

### Residuary Estate (Step 7)
```json
{
  "main_beneficiaries": [
    { "beneficiary_name": "JANE DOE", "share": "6/10" },
    { "beneficiary_name": "JOHNNY DOE", "share": "4/10" }
  ],
  "substitute_groups": [
    [
      { "beneficiary_name": "PARENT A", "share": "1/2" },
      { "beneficiary_name": "PARENT B", "share": "1/2" }
    ]
  ],
  "substitute_mode": "survivorship|individual",
  "additional_notes": ""
}
```

### Testamentary Trust (Step 8) — Optional
```json
{
  "beneficiaries": [
    { "beneficiary_name": "JOHNNY DOE", "share": "1/1", "role": "MB" }
  ],
  "purposes": ["Education", "Health", "Maintenance"],
  "duration": "Until age 25",
  "payment_mode": "Monthly|Quarterly|Yearly|Discretion of Trustee",
  "payment_amount": "RM 1000",
  "balance_beneficiaries": [
    { "beneficiary_name": "JOHNNY DOE", "share": "1/1" }
  ]
}
```

### Other Matters (Step 9) — Optional
```json
{
  "commorientes_enabled": false,
  "commorientes_days": 30,
  "joint_account_clause_enabled": false,
  "exclusion_enabled": false,
  "exclusion_name": "",
  "exclusion_nric": "",
  "exclusion_relationship": "",
  "exclusion_reason": "",
  "additional_instructions": ""
}
```

---

## Database Schema (SQLite)

### clients
| Column | Type | Description |
|--------|------|-------------|
| id | VARCHAR(36) PK | UUID |
| full_name | VARCHAR(200) | Client name |
| nric_passport | VARCHAR(50) | IC/passport |
| email | VARCHAR(120) | |
| phone | VARCHAR(50) | |
| created_at | DATETIME | |
| updated_at | DATETIME | |

### wills
| Column | Type | Description |
|--------|------|-------------|
| id | VARCHAR(36) PK | UUID |
| client_id | VARCHAR(36) FK | References clients |
| title | VARCHAR(200) | |
| status | VARCHAR(20) | draft/generated/pending_approval/approved/rejected |
| generated_will_text | TEXT | Final will document text |
| step1_data ... step8_data | TEXT (JSON) | Wizard step data |
| identities_data | TEXT (JSON) | Person registry snapshot |
| completed_steps | TEXT (JSON) | Array of step numbers |
| created_by | VARCHAR(36) FK | User who created |
| submitted_by | VARCHAR(36) FK | |
| approved_by | VARCHAR(36) FK | |
| approved_at | DATETIME | |
| include_logo | BOOLEAN | |

### persons
| Column | Type | Description |
|--------|------|-------------|
| id | VARCHAR(36) PK | UUID |
| client_id | VARCHAR(36) FK | |
| full_name | VARCHAR(200) | |
| nric_passport | VARCHAR(50) | |
| address | TEXT | |
| nationality | VARCHAR(100) | |
| date_of_birth | VARCHAR(20) | DD-MM-YYYY |
| gender | VARCHAR(10) | |
| email | VARCHAR(120) | |
| phone | VARCHAR(50) | |
| relationship | VARCHAR(50) | |

### users
| Column | Type | Description |
|--------|------|-------------|
| id | VARCHAR(36) PK | UUID |
| email | VARCHAR(120) | Unique |
| password_hash | VARCHAR(200) | pbkdf2:sha256 |
| name | VARCHAR(100) | |
| role | VARCHAR(20) | admin/advisor/approver |
| is_active | BOOLEAN | |

---

## Role Permissions

| Permission | Admin | Advisor | Approver |
|-----------|-------|---------|----------|
| Draft wills | Yes | Yes | Yes |
| Submit for approval | Yes | Yes | Yes |
| Approve/Reject | No | No | Yes |
| Download DOCX/PDF | No | No | Yes |
| Email will | No | No | Yes |
| Manage users | Yes | No | No |

---

## Multi-Tenant Setup

WillCraft supports multiple tenants via hostname. Each tenant has:
- Custom branding (colors, logo)
- Separate user accounts
- Shared codebase and database

To add NestedEggAdvisor as a tenant, add to `TENANT_CONFIG` in `app.py`:

```python
TENANT_CONFIG['will.nesteggadvisor.org'] = {
    'brand': 'NestedEgg',
    'subtitle': 'Will Writing',
    'theme': 'blue',
    'gradient_from': '#1e3a5f',
    'gradient_via': '#2c5282',
    'gradient_to': '#3182ce',
    'accent': '#ecc94b',
    'accent_light': '#fefcbf',
    'btn_bg': '#3182ce',
    'btn_hover': '#2b6cb0',
    'email_domain': '@nesteggadvisor.org',
    'email_from': 'noreply@nesteggadvisor.org',
    'email_cc': [],
    'default_users': [
        {'email': 'admin@nesteggadvisor.org', 'password': 'ChangeMe123#', 'name': 'Admin', 'role': 'admin'},
    ],
}
```

---

## Deployment

### Current setup
- **Server**: AWS EC2 `i-05de038faf5163f3c` (ap-southeast-1)
- **IP**: 52.221.231.214
- **Docker**: `willcraft-web` container on port 8080
- **Reverse proxy**: nginx → Docker container
- **SSL**: Cloudflare

### Adding a new tenant
1. Point DNS (e.g., `will.nesteggadvisor.org`) to 52.221.231.214
2. Add Cloudflare SSL proxy
3. Add nginx server block for the new domain
4. Add tenant config in `app.py`
5. Deploy: `git push origin main && ssh ubuntu@52.221.231.214 "bash ~/deploy.sh"`

---

## Share Format (Fractions)

All shares must be expressed as **fractions**, not percentages:
- `1/1` (100%)
- `1/2` (50%)
- `4/10` (40%)
- `3/10` (30%)
- `1/3` (33.33%)
- `31/100` (31%)

---

## Contact

- **Repository**: https://github.com/uxrm5c3/willcraft-ai
- **Support**: support@lifa.com.my
