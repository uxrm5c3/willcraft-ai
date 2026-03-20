"""
WillCraft AI - Malaysian AI Will Writing System
Flask application with multi-step wizard for will drafting.
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify, g
from functools import wraps
import difflib
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from config import FLASK_SECRET_KEY, ANTHROPIC_API_KEY, SQLALCHEMY_DATABASE_URI, DATA_DIR, UPLOAD_DIR, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
from database import db, Client, Will, WillEditLog, WillVersion, Person, Document, User, ROLE_PERMS, ROLE_LABELS, ProbateApplication, ProbateFormTemplate, ProbateGeneratedForm

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

db.init_app(app)

# Accepted file formats for OCR scanning
OCR_ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'pdf'}

def _validate_ocr_file(file):
    """Validate uploaded file is an accepted format for OCR. Returns error message or None."""
    if not file or not file.filename:
        return 'No file selected'
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in OCR_ALLOWED_EXTENSIONS:
        return f'Unsupported file format: .{ext}. Accepted formats: JPG, PNG, PDF'
    return None


# ---------------------------------------------------------------------------
# Jinja2 filters
# ---------------------------------------------------------------------------
from fractions import Fraction
from datetime import timezone, timedelta

MYT = timezone(timedelta(hours=8))

@app.template_filter('myt')
def myt_filter(dt, fmt='%d %b %Y, %I:%M %p'):
    """Convert UTC datetime to Malaysia Time (UTC+8) and format."""
    if not dt:
        return ''
    return dt.replace(tzinfo=timezone.utc).astimezone(MYT).strftime(fmt)

@app.template_filter('to_fraction')
def to_fraction_filter(value):
    """Convert a share value to fraction display. '40' -> '4/10', '31' -> '31/100'."""
    if not value or value == '-':
        return value
    s = str(value).strip().rstrip('%')
    if '/' in s:
        return s
    try:
        num = float(s)
        n = int(num)
        if n != num:
            return s
        if n == 100:
            return "1/1"
        # Use /10 if divisible by 10, otherwise /100
        if n % 10 == 0:
            return f"{n // 10}/10"
        else:
            return f"{n}/100"
    except (ValueError, ZeroDivisionError):
        return s


# ---------------------------------------------------------------------------
# Multi-tenant configuration
# ---------------------------------------------------------------------------
TENANT_CONFIG = {
    'will.lifa.com.my': {
        'brand': 'LIFA',
        'subtitle': 'WillCraft AI',
        'theme': 'emerald',
        'gradient_from': '#064e3b',
        'gradient_via': '#065f46',
        'gradient_to': '#0f766e',
        'accent': '#d4a745',
        'accent_light': '#fef3c7',
        'btn_bg': '#059669',
        'btn_hover': '#047857',
        'email_domain': '@lifa.com.my',
        'email_from': '',
        'email_cc': [],
        'default_users': [
            {'email': 'admin@lifa.com.my', 'password': 'Admin2026#', 'name': 'Admin', 'role': 'admin'},
            {'email': 'advisor@lifa.com.my', 'password': 'Advisor2026#', 'name': 'Advisor', 'role': 'advisor'},
            {'email': 'approver@lifa.com.my', 'password': 'Approver2026#', 'name': 'Approver', 'role': 'approver'},
        ],
    },
    'will.alantanjb.com.my': {
        'brand': 'alantanjb',
        'subtitle': 'WillCraft AI',
        'theme': 'indigo',
        'gradient_from': '#1e1b4b',
        'gradient_via': '#312e81',
        'gradient_to': '#4338ca',
        'accent': '#94a3b8',
        'accent_light': '#e2e8f0',
        'btn_bg': '#4f46e5',
        'btn_hover': '#4338ca',
        'email_domain': '@alantanjb.com',
        'email_from': 'enquiry@alantanjb.com',
        'email_cc': ['kylie.tan@alantanjb.com'],
        'default_users': [
            {'email': 'accounts@alantanjb.com', 'password': 'Finance88#', 'name': 'Accounts', 'role': 'admin'},
            {'email': 'enquiry@alantanjb.com', 'password': 'Enquiry88#', 'name': 'Enquiry', 'role': 'advisor'},
            {'email': 'kylie.tan@alantanjb.com', 'password': 'Aia12345#', 'name': 'Kylie Tan', 'role': 'approver'},
        ],
    },
}
# Also map without .my for Cloudflare routing
TENANT_CONFIG['will.alantanjb.com'] = TENANT_CONFIG['will.alantanjb.com.my']

DEFAULT_TENANT = {
    'brand': 'LIFA',
    'subtitle': 'WillCraft AI',
    'theme': 'emerald',
    'gradient_from': '#064e3b',
    'gradient_via': '#065f46',
    'gradient_to': '#0f766e',
    'accent': '#d4a745',
    'accent_light': '#fef3c7',
    'btn_bg': '#059669',
    'btn_hover': '#047857',
    'email_domain': '@lifa.com.my',
    'email_from': '',
    'email_cc': [],
    'default_users': [
        {'email': 'admin@lifa.com.my', 'password': 'Admin2026#', 'name': 'Admin', 'role': 'admin'},
        {'email': 'advisor@lifa.com.my', 'password': 'Advisor2026#', 'name': 'Advisor', 'role': 'advisor'},
        {'email': 'approver@lifa.com.my', 'password': 'Approver2026#', 'name': 'Approver', 'role': 'approver'},
    ],
}


def get_tenant():
    """Get tenant config based on request hostname."""
    host = request.host.split(':')[0] if request else 'localhost'
    return TENANT_CONFIG.get(host, DEFAULT_TENANT)


# ---------------------------------------------------------------------------
# Authentication decorators
# ---------------------------------------------------------------------------

def login_required(f):
    """Require login for a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def role_required(*roles):
    """Require specific role(s) for a route."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if session.get('user_role') not in roles:
                flash('You do not have permission to access this page.', 'error')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@app.before_request
def load_current_user():
    """Load current user into g for template access."""
    g.user = None
    g.perms = {}
    g.tenant = get_tenant() if request else DEFAULT_TENANT
    user_id = session.get('user_id')
    if user_id:
        g.user = db.session.get(User, user_id)
        if g.user:
            g.perms = ROLE_PERMS.get(g.user.role, {})
        else:
            # User deleted, clear session
            session.pop('user_id', None)
            session.pop('user_role', None)


@app.context_processor
def inject_global_context():
    """Make user, permissions, tenant, and testator_person_id available to all templates."""
    # Count pending approvals for approvers
    pending_count = 0
    if g.user and g.perms.get('canApprove'):
        pending_count = Will.query.filter_by(status='pending_approval').count()
    # Check if current will has been generated
    has_generated_will = False
    if session.get('will_id'):
        wr = db.session.get(Will, session['will_id'])
        if wr and (wr.generated_will_text or wr.status in ('generated', 'pending_approval', 'approved')):
            has_generated_will = True
    return {
        'testator_person_id': session.get('step1', {}).get('person_id', ''),
        'current_user': g.user,
        'perms': g.perms,
        'tenant': g.tenant,
        'role_labels': ROLE_LABELS,
        'pending_approval_count': pending_count,
        'has_generated_will': has_generated_will,
    }


with app.app_context():
    os.makedirs(DATA_DIR, exist_ok=True)
    db.create_all()
    # Migrate: add document_id column to persons if not exists
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("ALTER TABLE persons ADD COLUMN document_id VARCHAR(36)"))
            conn.commit()
    except Exception:
        pass  # Column already exists
    # Migrate: add approval columns to wills if not exists
    for col_def in [
        ("created_by", "VARCHAR(36)"),
        ("submitted_by", "VARCHAR(36)"),
        ("submitted_at", "DATETIME"),
        ("approved_by", "VARCHAR(36)"),
        ("approved_at", "DATETIME"),
        ("approval_remarks", "TEXT"),
        ("text_edited_by", "VARCHAR(36)"),
        ("text_edited_at", "DATETIME"),
        ("include_logo", "BOOLEAN DEFAULT 1"),
    ]:
        try:
            with db.engine.connect() as conn:
                conn.execute(db.text(f"ALTER TABLE wills ADD COLUMN {col_def[0]} {col_def[1]}"))
                conn.commit()
        except Exception:
            pass
    # Create new tables (WillEditLog, Probate etc.) if they don't exist
    db.create_all()
    # Migrate: add new probate columns if not exists
    for col_def in [
        ("probate_applications", "application_type", "VARCHAR(20) DEFAULT 'probate'"),
        ("probate_applications", "deceased_name", "VARCHAR(200)"),
        ("probate_applications", "deceased_nric", "VARCHAR(50)"),
        ("probate_applications", "deceased_address", "TEXT"),
        ("probate_applications", "applicant_name", "VARCHAR(200)"),
        ("probate_applications", "applicant_nric", "VARCHAR(50)"),
        ("probate_applications", "applicant_address", "TEXT"),
        ("probate_applications", "applicant_relationship", "VARCHAR(100)"),
        ("probate_applications", "assets_data", "TEXT DEFAULT '[]'"),
        ("probate_applications", "beneficiaries_data", "TEXT DEFAULT '[]'"),
        ("probate_applications", "will_document_id", "VARCHAR(36)"),
    ]:
        try:
            with db.engine.connect() as conn:
                conn.execute(db.text(f"ALTER TABLE {col_def[0]} ADD COLUMN {col_def[1]} {col_def[2]}"))
                conn.commit()
        except Exception:
            pass
    # Seed default users if none exist — detect tenant from WILLCRAFT_DOMAIN env var
    try:
        if User.query.count() == 0:
            domain = os.environ.get('WILLCRAFT_DOMAIN', '')
            tenant = TENANT_CONFIG.get(domain, DEFAULT_TENANT)
            for u in tenant['default_users']:
                user = User(email=u['email'], name=u['name'], role=u['role'])
                user.set_password(u['password'])
                db.session.add(user)
            db.session.commit()
            print(f"[Auth] Seeded {len(tenant['default_users'])} default users for {domain or 'default'}.")
    except Exception as e:
        db.session.rollback()
        print(f"[Auth] User seeding skipped (may already exist): {e}")

    # Seed default probate form templates if table is empty
    try:
        if ProbateFormTemplate.query.count() == 0:
            PROBATE_FORM_DEFAULTS = [
                {'form_code': 'doc01', 'form_name': 'Originating Summons', 'form_name_malay': 'Saman Pemula',
                 'description': 'The main court application to start the probate process. This tells the court you want to be officially recognized as the executor of the will.',
                 'file_path': 'probate_templates/doc01_saman_pemula.docx', 'category': 'core', 'sort_order': 1},
                {'form_code': 'doc02', 'form_name': 'Affidavit under Probate Act', 'form_name_malay': 'Afidavit Menurut Akta Probet',
                 'description': "The executor's sworn statement about the deceased person, their will, and their estate. Includes exhibit references for supporting documents.",
                 'file_path': 'probate_templates/doc02_afidavit_probet.docx', 'category': 'core', 'sort_order': 2},
                {'form_code': 'doc03', 'form_name': 'Oath of Administration', 'form_name_malay': 'Sumpah Pentadbiran',
                 'description': "The executor's oath promising to honestly and faithfully manage the deceased person's estate according to the law.",
                 'file_path': 'probate_templates/doc03_sumpah_pentadbiran.docx', 'category': 'core', 'sort_order': 3},
                {'form_code': 'doc04', 'form_name': 'Witness 1 Affidavit', 'form_name_malay': 'Afidavit Saksi 1',
                 'description': 'A sworn statement from the first person who witnessed the will being signed. Confirms they saw the testator sign the will.',
                 'file_path': 'probate_templates/doc04_afidavit_saksi_1.docx', 'category': 'witness',
                 'requires_witnesses': True, 'sort_order': 4},
                {'form_code': 'doc05', 'form_name': 'Witness 2 Affidavit', 'form_name_malay': 'Afidavit Saksi 2',
                 'description': 'A sworn statement from the second person who witnessed the will being signed.',
                 'file_path': 'probate_templates/doc05_afidavit_saksi_2.docx', 'category': 'witness',
                 'requires_witnesses': True, 'sort_order': 5},
                {'form_code': 'doc06', 'form_name': 'Assets & Liabilities Schedule', 'form_name_malay': 'Jadual Aset & Liabiliti',
                 'description': "A detailed list of everything the deceased person owned (houses, cars, bank accounts, investments) and any debts they owed.",
                 'file_path': 'probate_templates/doc06_jadual_aset.docx', 'category': 'core', 'sort_order': 6},
                {'form_code': 'doc07', 'form_name': 'Beneficiary List', 'form_name_malay': 'Senarai Benefisiari',
                 'description': "A list of all people who will inherit from the deceased person's estate, including their names, ID numbers, and relationship.",
                 'file_path': 'probate_templates/doc07_senarai_benefisiari.docx', 'category': 'core', 'sort_order': 7},
                {'form_code': 'doc08', 'form_name': 'Notice of Solicitor Appointment', 'form_name_malay': 'Notis Perlantikan Peguamcara',
                 'description': 'A formal notice telling the court that a lawyer has been hired to handle this probate case.',
                 'file_path': 'probate_templates/doc08_notis_peguamcara.docx', 'category': 'core', 'sort_order': 8},
                {'form_code': 'form14a', 'form_name': 'Land Transfer (Form 14A)', 'form_name_malay': 'Borang 14A - Pindah Milik',
                 'description': 'Transfers property (land/house) from the deceased to the beneficiary named in the will. One form is needed for each property.',
                 'file_path': 'probate_templates/form14a_land_transfer.docx', 'category': 'property',
                 'requires_property': True, 'sort_order': 9},
                {'form_code': 'form346', 'form_name': 'Personal Representative (Form 346)', 'form_name_malay': 'Borang 346 - Pendaftaran Wakil Diri',
                 'description': 'Registers the executor as the legal representative at the land office so they can handle property transfers.',
                 'file_path': 'probate_templates/form346_personal_rep.docx', 'category': 'property',
                 'requires_property': True, 'sort_order': 10},
            ]
            for tpl in PROBATE_FORM_DEFAULTS:
                t = ProbateFormTemplate(**tpl)
                db.session.add(t)
            db.session.commit()
            print(f"[Probate] Seeded {len(PROBATE_FORM_DEFAULTS)} default form templates.")
    except Exception as e:
        db.session.rollback()
        print(f"[Probate] Template seeding skipped: {e}")


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def get_completed_steps():
    """Return list of completed wizard step numbers."""
    return session.get('completed_steps', [])


def mark_step_complete(step):
    """Mark a wizard step as completed in the session."""
    steps = get_completed_steps()
    if step not in steps:
        steps.append(step)
    session['completed_steps'] = steps


def ensure_client():
    """Ensure a Client record exists for the current session. Returns client_id."""
    client_id = session.get('client_id')
    if client_id:
        existing = db.session.get(Client, client_id)
        if existing:
            return client_id
    step1 = session.get('step1', {})
    client = Client(
        full_name=step1.get('full_name', 'New Client'),
        nric_passport=step1.get('nric_passport', ''),
        email=step1.get('email'),
        phone=step1.get('phone'),
    )
    db.session.add(client)
    db.session.commit()
    session['client_id'] = client.id
    return client.id


def get_client_folder_name(client_id):
    """Get the friendly folder name for a client, or fall back to client_id."""
    client = db.session.get(Client, client_id)
    return client.folder_name if client else client_id


def save_will_to_db():
    """Persist current session data to the database."""
    client_id = ensure_client()
    will_id = session.get('will_id')
    step1 = session.get('step1', {})

    if will_id:
        will_record = db.session.get(Will, will_id)
    else:
        will_record = None

    if not will_record:
        will_record = Will(client_id=client_id, created_by=session.get('user_id'))
        db.session.add(will_record)
        db.session.flush()
        session['will_id'] = will_record.id

    will_record.identities_data = json.dumps(session.get('person_registry', []))
    will_record.step1_data = json.dumps(session.get('step1', {}))
    will_record.step2_data = json.dumps({
        'executors': session.get('step2_executors', []),
        'executor_type': session.get('step3_executor_type', 'single'),
        'trustee_data': session.get('step3_trustees', {'same_as_executor': True}),
    })
    will_record.step3_data = json.dumps({
        'guardians': session.get('step3_guardians', []),
        'guardian_allowance': session.get('step3_guardian_allowance', {}),
    })
    will_record.step4_data = json.dumps(session.get('step4_beneficiaries', []))
    will_record.step5_data = json.dumps(session.get('step5_gifts', []))
    will_record.step6_data = json.dumps(session.get('step6_residuary', {}))
    will_record.step7_data = json.dumps(session.get('step7_trust', {}))
    will_record.step8_data = json.dumps(session.get('step8_others', {}))
    will_record.completed_steps = json.dumps(session.get('completed_steps', []))
    # Only update generated_will_text when explicitly present in session
    # (it's popped after generation to keep cookie small — don't overwrite DB with None)
    if 'generated_will_text' in session:
        will_record.generated_will_text = session['generated_will_text']
        if session['generated_will_text']:
            will_record.status = 'generated'
    will_record.title = f"Will of {step1.get('full_name', 'Unknown')}"

    # Update client info from step1
    client = db.session.get(Client, client_id)
    if client and step1.get('full_name'):
        client.full_name = step1['full_name']
        client.nric_passport = step1.get('nric_passport', '')
        client.email = step1.get('email')
        client.phone = step1.get('phone')

    db.session.commit()
    return will_record


def load_will_to_session(will_record):
    """Restore a saved will into the current session."""
    session['will_id'] = will_record.id
    session['client_id'] = will_record.client_id
    session['step1'] = json.loads(will_record.step1_data or '{}')
    # Load executor data (handle both old array and new object format)
    step2_raw = json.loads(will_record.step2_data or '[]')
    if isinstance(step2_raw, list):
        # Old format: plain array of executors
        session['step2_executors'] = step2_raw
        session['step3_executor_type'] = 'joint' if len(step2_raw) > 1 else 'single'
        session['step3_trustees'] = {'same_as_executor': True, 'trustees': [{}]}
    else:
        # New format: object with executors, executor_type, trustee_data
        session['step2_executors'] = step2_raw.get('executors', [])
        session['step3_executor_type'] = step2_raw.get('executor_type', 'single')
        session['step3_trustees'] = step2_raw.get('trustee_data', {'same_as_executor': True, 'trustees': [{}]})
    step3 = json.loads(will_record.step3_data or '{}')
    session['step3_guardians'] = step3.get('guardians', [])
    session['step3_guardian_allowance'] = step3.get('guardian_allowance', {})
    session['step4_beneficiaries'] = json.loads(will_record.step4_data or '[]')
    session['step5_gifts'] = json.loads(will_record.step5_data or '[]')
    session['step6_residuary'] = json.loads(will_record.step6_data or '{}')
    session['step7_trust'] = json.loads(will_record.step7_data or '{}')
    session['step8_others'] = json.loads(will_record.step8_data or '{}')
    session['completed_steps'] = json.loads(will_record.completed_steps or '[]')
    # Ensure step 10 is marked complete if will has been generated
    if will_record.status in ('generated', 'pending_approval', 'approved') and 10 not in session['completed_steps']:
        session['completed_steps'].append(10)
    # Don't load generated_will_text into session — it makes the cookie too large.
    # preview() and download() now read from DB directly.
    session.pop('generated_will_text', None)
    # Refresh identity registry from DB (preferred) or from saved snapshot
    _refresh_session_person_registry(will_record.client_id)
    if not session.get('person_registry'):
        session['person_registry'] = json.loads(will_record.identities_data or '[]')
    session.modified = True


def upsert_person(client_id, full_name, nric_passport, address=None,
                  date_of_birth=None, nationality=None, gender=None,
                  passport_expiry=None, email=None, phone=None,
                  relationship=None, document_id=None):
    """Add or update a person identity in the registry."""
    if not full_name or not nric_passport:
        return None
    # Try exact match first, then normalized match (strip dashes/spaces)
    existing = Person.query.filter_by(client_id=client_id, nric_passport=nric_passport).first()
    if not existing:
        normalized = nric_passport.replace('-', '').replace(' ', '').upper()
        all_persons = Person.query.filter_by(client_id=client_id).all()
        for p in all_persons:
            p_norm = (p.nric_passport or '').replace('-', '').replace(' ', '').upper()
            if p_norm == normalized:
                existing = p
                break
    if existing:
        existing.full_name = full_name.upper()
        if address:
            existing.address = address
        if date_of_birth:
            existing.date_of_birth = date_of_birth
        if nationality:
            existing.nationality = nationality
        if gender:
            existing.gender = gender
        if passport_expiry:
            existing.passport_expiry = passport_expiry
        if email:
            existing.email = email
        if phone:
            existing.phone = phone
        if relationship is not None:
            existing.relationship = relationship
        if document_id is not None:
            existing.document_id = document_id
        db.session.commit()
        _refresh_session_person_registry(client_id)
        return existing
    else:
        person = Person(
            client_id=client_id,
            full_name=full_name.upper(),
            nric_passport=nric_passport,
            address=address or '',
            date_of_birth=date_of_birth,
            nationality=nationality or 'Malaysian',
            gender=gender,
            passport_expiry=passport_expiry,
            email=email,
            phone=phone,
            relationship=relationship or '',
            document_id=document_id or None,
        )
        db.session.add(person)
        db.session.commit()
        _refresh_session_person_registry(client_id)
        return person


def _refresh_session_person_registry(client_id):
    """Refresh the session person registry from DB.
    Sort: Testator first, then by DOB ascending (oldest first), then by name."""
    persons = Person.query.filter_by(client_id=client_id).all()
    # Sort: Testator first, then by DOB (oldest first, None last), then name
    def _sort_key(p):
        is_testator = 0 if (p.relationship or '').lower() == 'testator' else 1
        dob = p.date_of_birth or ''
        # Normalize DOB to YYYY-MM-DD for sorting (handle DD-MM-YYYY format)
        if dob and len(dob) == 10 and dob[2] == '-' and dob[5] == '-':
            dob = f"{dob[6:10]}-{dob[3:5]}-{dob[0:2]}"
        return (is_testator, dob if dob else '9999-99-99', p.full_name)
    persons.sort(key=_sort_key)
    session['person_registry'] = [
        {'id': p.id, 'full_name': p.full_name, 'nric_passport': p.nric_passport,
         'address': p.address or '', 'date_of_birth': p.date_of_birth or '',
         'nationality': p.nationality or 'Malaysian', 'gender': p.gender or '',
         'passport_expiry': p.passport_expiry or '',
         'email': p.email or '', 'phone': p.phone or '',
         'relationship': p.relationship or '',
         'document_id': p.document_id or ''}
        for p in persons
    ]
    session.modified = True


def _propagate_identity_changes(person_id, new_name, new_nric, old_name=None):
    """When an identity is updated, propagate name/NRIC/relationship/address changes across all step session data."""
    person_data = _get_person_from_registry(person_id) or {}
    new_rel = person_data.get('relationship', '')
    new_addr = person_data.get('address', '')
    new_nationality = person_data.get('nationality', 'Malaysian')
    has_name_change = old_name and old_name.upper() != new_name.upper()

    # Always propagate by person_id (even without name change — catches relationship/address updates)
    # Step 1 (Testator)
    step1 = session.get('step1', {})
    if step1.get('person_id') == person_id:
        step1['full_name'] = new_name
        step1['nric_passport'] = new_nric
        step1['residential_address'] = new_addr
        step1['nationality'] = new_nationality
        session['step1'] = step1

    # Step 2 (Executors)
    for ex in session.get('step2_executors', []):
        if ex.get('person_id') == person_id or (has_name_change and ex.get('full_name', '').upper() == old_name.upper()):
            ex['full_name'] = new_name
            ex['nric_passport'] = new_nric
            ex['address'] = new_addr
            ex['relationship'] = new_rel
            if 'nationality' in ex:
                ex['nationality'] = new_nationality

    # Step 4 (Beneficiaries)
    for ben in session.get('step4_beneficiaries', []):
        if ben.get('person_id') == person_id or (has_name_change and ben.get('full_name', '').upper() == old_name.upper()):
            ben['full_name'] = new_name
            ben['nric_passport_birthcert'] = new_nric
            ben['relationship'] = new_rel
            if 'nationality' in ben:
                ben['nationality'] = new_nationality

    # Step 5 (Gift allocations — match by name)
    for gift in session.get('step5_gifts', []):
        for alloc in gift.get('allocations', []):
            if has_name_change and alloc.get('beneficiary_name', '').upper() == old_name.upper():
                alloc['beneficiary_name'] = new_name

    # Step 6 (Residuary estate)
    res = session.get('step6_residuary', {})
    for mb in res.get('main_beneficiaries', []):
        if mb.get('person_id') == person_id or (has_name_change and mb.get('beneficiary_name', '').upper() == old_name.upper()):
            mb['beneficiary_name'] = new_name

    session.modified = True


def _get_person_from_registry(person_id):
    """Look up a person from session['person_registry'] by ID."""
    for p in session.get('person_registry', []):
        if p['id'] == person_id:
            return p
    return None


def build_will_data():
    """Build WillData model from session data."""
    from models import (
        Testator, Executor, Guardian, GuardianAllowance,
        Beneficiary, Gift, GiftAllocation,
        ResiduaryEstate, ResiduaryBeneficiary,
        TestamentaryTrust, TrustBeneficiary,
        OtherMatters, WillData, Trustee,
    )

    # -- Section A: Testator --------------------------------------------------
    s1 = session.get('step1', {})
    testator = Testator(
        full_name=s1.get('full_name', ''),
        nric_passport=s1.get('nric_passport', ''),
        residential_address=s1.get('residential_address', ''),
        nationality=s1.get('nationality', 'Malaysian'),
        country_of_residence=s1.get('country_of_residence', 'Malaysia'),
        date_of_birth=s1.get('date_of_birth', '01-01-2000'),
        occupation=s1.get('occupation', ''),
        religion=s1.get('religion') or None,
        email=s1.get('email') or None,
        phone=s1.get('phone') or None,
        gender=s1.get('gender', 'Male'),
        marital_status=s1.get('marital_status', 'Single'),
        has_prior_will=s1.get('has_prior_will', False),
        property_coverage=s1.get('property_coverage', 'Malaysia'),
        contemplation_of_marriage=s1.get('contemplation_of_marriage', False),
        fiance_name=s1.get('fiance_name') or None,
        fiance_nric=s1.get('fiance_nric') or None,
        signing_method=s1.get('signing_method', 'Signature'),
        special_circumstances=s1.get('special_circumstances', []),
        translator_name=s1.get('translator_name') or None,
        translator_nric=s1.get('translator_nric') or None,
        translator_relationship=s1.get('translator_relationship') or None,
        translator_language=s1.get('translator_language') or None,
    )

    # -- Section B: Executors --------------------------------------------------
    executors = [Executor(**e) for e in session.get('step2_executors', [])]

    # -- Section B2: Trustees (separate from executors) -----------------------
    trustee_session = session.get('step3_trustees', {'same_as_executor': True})
    trustee_same_as_executor = trustee_session.get('same_as_executor', True)
    trustees = None
    substitute_trustee = None
    substitute_trustees = None
    if not trustee_same_as_executor:
        trustees_raw = trustee_session.get('trustees', [])
        if trustees_raw:
            trustees = [Trustee(**t) for t in trustees_raw if t.get('full_name')]
            if not trustees:
                trustees = None
        # Support multiple substitute trustees (new format)
        sub_trustees_raw = trustee_session.get('substitute_trustees', [])
        if sub_trustees_raw:
            substitute_trustees = [Trustee(**st) for st in sub_trustees_raw if st.get('full_name')]
            if substitute_trustees:
                substitute_trustee = substitute_trustees[0]  # backward compat
            else:
                substitute_trustees = None
        else:
            # Backward compat: single substitute_trustee
            sub_trustee_raw = trustee_session.get('substitute_trustee', {})
            if sub_trustee_raw and sub_trustee_raw.get('full_name'):
                substitute_trustee = Trustee(**sub_trustee_raw)
                substitute_trustees = [substitute_trustee]

    # -- Section C: Guardians (optional) --------------------------------------
    guardians_data = session.get('step3_guardians', [])
    guardians = [Guardian(**g) for g in guardians_data] if guardians_data else None

    ga_data = session.get('step3_guardian_allowance', {})
    guardian_allowance = (
        GuardianAllowance(**ga_data)
        if ga_data and ga_data.get('payment_mode')
        else None
    )

    # -- Section D: Beneficiaries ---------------------------------------------
    beneficiaries = [Beneficiary(**b) for b in session.get('step4_beneficiaries', [])]

    # -- Section E: Gifts (optional) ------------------------------------------
    gifts_data = session.get('step5_gifts', [])
    gifts = None
    if gifts_data:
        from models.gift import PropertyDetails, FinancialDetails
        gifts = []
        for gd in gifts_data:
            allocations = [GiftAllocation(**a) for a in gd.get('allocations', [])]
            prop_details = None
            fin_details = None
            gift_type = gd.get('gift_type', 'other')
            if gift_type == 'property' and gd.get('property_details'):
                prop_details = PropertyDetails(**gd['property_details'])
            if gift_type == 'financial' and gd.get('financial_details'):
                fin_details = FinancialDetails(**gd['financial_details'])
            gifts.append(Gift(
                gift_type=gift_type,
                description=gd.get('description', ''),
                property_details=prop_details,
                financial_details=fin_details,
                allocations=allocations,
                subject_to_trust=gd.get('subject_to_trust', False),
                subject_to_guardian_allowance=gd.get('subject_to_guardian_allowance', False),
                sell_property=gd.get('sell_property', False),
                substitute_mode=gd.get('substitute_mode', 'equal'),
                ownership_type=gd.get('ownership_type', 'sole'),
                testator_share=gd.get('testator_share'),
                joint_owners=gd.get('joint_owners'),
                encumbrance_status=gd.get('encumbrance_status', 'clean'),
                debt_source=gd.get('debt_source'),
                account_ownership=gd.get('account_ownership', 'individual'),
            ))

    # -- Section F: Residuary Estate ------------------------------------------
    res_data = session.get('step6_residuary', {})
    main_bens = [ResiduaryBeneficiary(**mb) for mb in res_data.get('main_beneficiaries', [])]
    sub_groups = []
    for sg in res_data.get('substitute_groups', []):
        sub_groups.append([ResiduaryBeneficiary(**sb) for sb in sg])
    residuary_estate = ResiduaryEstate(
        main_beneficiaries=main_bens,
        substitute_groups=sub_groups,
        additional_notes=res_data.get('additional_notes') or None,
    )

    # -- Section G: Testamentary Trust (optional) -----------------------------
    trust_data = session.get('step7_trust', {})
    testamentary_trust = None
    if trust_data and trust_data.get('beneficiaries'):
        trust_bens = [TrustBeneficiary(**tb) for tb in trust_data.get('beneficiaries', [])]
        balance_bens = [TrustBeneficiary(**bb) for bb in trust_data.get('balance_beneficiaries', [])]
        testamentary_trust = TestamentaryTrust(
            beneficiaries=trust_bens,
            purposes=trust_data.get('purposes', []),
            duration=trust_data.get('duration') or None,
            assets_from_gifts=trust_data.get('assets_from_gifts', []),
            payment_mode=trust_data.get('payment_mode') or None,
            payment_amount=trust_data.get('payment_amount') or None,
            balance_beneficiaries=balance_bens,
        )

    # -- Section H/I: Other Matters (optional) --------------------------------
    om_data = session.get('step8_others', {})
    other_matters = None
    if om_data:
        other_matters = OtherMatters(**om_data)

    return WillData(
        testator=testator,
        executors=executors,
        trustee_same_as_executor=trustee_same_as_executor,
        trustees=trustees,
        substitute_trustee=substitute_trustee,
        substitute_trustees=substitute_trustees,
        guardians=guardians,
        guardian_allowance=guardian_allowance,
        beneficiaries=beneficiaries,
        gifts=gifts,
        residuary_estate=residuary_estate,
        testamentary_trust=testamentary_trust,
        other_matters=other_matters,
        identities=session.get('person_registry', []),
    )


# ---------------------------------------------------------------------------
# Authentication Routes
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page and handler."""
    if 'user_id' in session:
        return redirect(url_for('index'))

    tenant = get_tenant()
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == '1'

        if not email or not password:
            return render_template('login.html', error='Please enter email and password.', tenant=tenant)

        user = User.query.filter(db.func.lower(User.email) == email, User.is_active == True).first()
        if not user or not user.check_password(password):
            return render_template('login.html', error='Invalid email or password.', tenant=tenant)

        session['user_id'] = user.id
        session['user_role'] = user.role
        session['user_name'] = user.name
        session.permanent = remember
        return redirect(url_for('index'))

    # Get quick-login users for display
    quick_users = User.query.filter_by(is_active=True).order_by(User.role, User.name).all()
    return render_template('login.html', tenant=tenant, quick_users=quick_users)


@app.route('/logout')
def logout():
    """Logout and redirect to login page."""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# User Management Routes (Admin only)
# ---------------------------------------------------------------------------

@app.route('/admin/users')
@role_required('admin')
def admin_users():
    """User management page (admin only)."""
    users = User.query.order_by(User.role, User.name).all()
    return render_template('admin/users.html', users=users)


@app.route('/admin/users/add', methods=['POST'])
@role_required('admin')
def admin_user_add():
    """Add a new user."""
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()
    name = request.form.get('name', '').strip()
    contact = request.form.get('contact', '').strip()
    role = request.form.get('role', 'advisor')

    if not email or not password or not name:
        flash('Name, email, and password are required.', 'error')
        return redirect(url_for('admin_users'))

    if len(password) < 6:
        flash('Password must be at least 6 characters.', 'error')
        return redirect(url_for('admin_users'))

    if role not in ROLE_PERMS:
        flash('Invalid role.', 'error')
        return redirect(url_for('admin_users'))

    existing = User.query.filter(db.func.lower(User.email) == email).first()
    if existing:
        flash('A user with this email already exists.', 'error')
        return redirect(url_for('admin_users'))

    user = User(email=email, name=name, contact=contact, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f'User "{name}" ({role}) created successfully.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<user_id>/update', methods=['POST'])
@role_required('admin')
def admin_user_update(user_id):
    """Update user details."""
    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_users'))

    name = request.form.get('name', '').strip()
    contact = request.form.get('contact', '').strip()
    role = request.form.get('role', '').strip()
    password = request.form.get('password', '').strip()

    if name:
        user.name = name
    if contact is not None:
        user.contact = contact
    if role and role in ROLE_PERMS:
        user.role = role
    if password and len(password) >= 6:
        user.set_password(password)

    db.session.commit()
    flash(f'User "{user.name}" updated.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<user_id>/toggle', methods=['POST'])
@role_required('admin')
def admin_user_toggle(user_id):
    """Enable/disable a user."""
    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_users'))
    if user.id == session.get('user_id'):
        flash('You cannot disable your own account.', 'error')
        return redirect(url_for('admin_users'))
    user.is_active = not user.is_active
    db.session.commit()
    status = 'enabled' if user.is_active else 'disabled'
    flash(f'User "{user.name}" {status}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<user_id>/delete', methods=['POST'])
@role_required('admin')
def admin_user_delete(user_id):
    """Delete a user."""
    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('admin_users'))
    if user.id == session.get('user_id'):
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('admin_users'))
    name = user.name
    db.session.delete(user)
    db.session.commit()
    flash(f'User "{name}" deleted.', 'success')
    return redirect(url_for('admin_users'))


# ---------------------------------------------------------------------------
# Admin: Firm Settings (logo upload)
# ---------------------------------------------------------------------------

def _logo_dir():
    """Return the logos directory path, creating it if needed."""
    d = os.path.join(DATA_DIR, 'logos')
    os.makedirs(d, exist_ok=True)
    return d


def _get_logo_path():
    """Return the absolute path to the current tenant's logo, or None."""
    tenant = get_tenant()
    domain = tenant.get('brand', 'default').lower()
    logo_dir = _logo_dir()
    for ext in ('png', 'jpg', 'jpeg', 'webp'):
        p = os.path.join(logo_dir, f'{domain}_logo.{ext}')
        if os.path.isfile(p):
            return p
    return None


@app.route('/admin/settings')
@role_required('admin')
def admin_settings():
    """Firm settings page — logo upload etc."""
    logo_path = _get_logo_path()
    logo_url = url_for('admin_serve_logo') if logo_path else None
    return render_template('admin/settings.html', logo_url=logo_url)


@app.route('/admin/settings/logo')
@login_required
def admin_serve_logo():
    """Serve the tenant's logo image."""
    logo_path = _get_logo_path()
    if not logo_path:
        return '', 404
    return send_file(logo_path)


@app.route('/admin/settings/upload-logo', methods=['POST'])
@role_required('admin')
def admin_upload_logo():
    """Handle firm logo upload."""
    f = request.files.get('logo')
    if not f or not f.filename:
        flash('No file selected.', 'error')
        return redirect(url_for('admin_settings'))

    # Validate extension
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in ('png', 'jpg', 'jpeg', 'webp'):
        flash('Only PNG, JPG, or WebP images are allowed.', 'error')
        return redirect(url_for('admin_settings'))

    # Validate size (2MB)
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > 2 * 1024 * 1024:
        flash('Logo file must be under 2MB.', 'error')
        return redirect(url_for('admin_settings'))

    # Remove any existing logo for this tenant
    tenant = get_tenant()
    domain = tenant.get('brand', 'default').lower()
    logo_dir = _logo_dir()
    for old_ext in ('png', 'jpg', 'jpeg', 'webp'):
        old = os.path.join(logo_dir, f'{domain}_logo.{old_ext}')
        if os.path.isfile(old):
            os.remove(old)

    # Save new logo
    save_path = os.path.join(logo_dir, f'{domain}_logo.{ext}')
    f.save(save_path)
    flash('Firm logo uploaded successfully.', 'success')
    return redirect(url_for('admin_settings'))


@app.route('/admin/settings/remove-logo', methods=['POST'])
@role_required('admin')
def admin_remove_logo():
    """Remove the firm logo."""
    tenant = get_tenant()
    domain = tenant.get('brand', 'default').lower()
    logo_dir = _logo_dir()
    for ext in ('png', 'jpg', 'jpeg', 'webp'):
        p = os.path.join(logo_dir, f'{domain}_logo.{ext}')
        if os.path.isfile(p):
            os.remove(p)
    flash('Logo removed.', 'success')
    return redirect(url_for('admin_settings'))


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """User profile page."""
    user = db.session.get(User, session['user_id'])
    if not user:
        return redirect(url_for('logout'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        contact = request.form.get('contact', '').strip()
        password = request.form.get('password', '').strip()
        password2 = request.form.get('password2', '').strip()

        if name:
            user.name = name
            session['user_name'] = name
        if contact is not None:
            user.contact = contact
        if password:
            if len(password) < 6:
                flash('Password must be at least 6 characters.', 'error')
                return render_template('profile.html', user=user)
            if password != password2:
                flash('Passwords do not match.', 'error')
                return render_template('profile.html', user=user)
            user.set_password(password)

        db.session.commit()
        flash('Profile updated.', 'success')
        return redirect(url_for('profile'))

    return render_template('profile.html', user=user)


# ---------------------------------------------------------------------------
# Approval Workflow Routes
# ---------------------------------------------------------------------------

@app.route('/wills/<will_id>/submit-for-approval', methods=['POST'])
@login_required
def will_submit_for_approval(will_id):
    """Submit a generated will for approval."""
    will_record = db.session.get(Will, will_id)
    if not will_record:
        flash('Will not found.', 'error')
        return redirect(url_for('will_list'))

    if will_record.status not in ('generated', 'rejected'):
        flash('Only generated or rejected wills can be submitted for approval.', 'error')
        return redirect(url_for('preview'))

    will_record.status = 'pending_approval'
    will_record.submitted_by = session['user_id']
    will_record.submitted_at = datetime.utcnow()
    will_record.approval_remarks = None
    db.session.commit()
    flash('Will submitted for approval.', 'success')
    return redirect(url_for('preview'))


@app.route('/approvals')
@role_required('approver')
def approval_list():
    """List wills pending approval."""
    pending = Will.query.filter_by(status='pending_approval').order_by(Will.submitted_at.desc()).all()
    approved = Will.query.filter_by(status='approved').order_by(Will.approved_at.desc()).limit(20).all()
    rejected = Will.query.filter_by(status='rejected').order_by(Will.updated_at.desc()).limit(20).all()
    return render_template('approvals.html', pending=pending, approved=approved, rejected=rejected)


@app.route('/wills/<will_id>/approve', methods=['POST'])
@role_required('approver')
def will_approve(will_id):
    """Approve a will."""
    will_record = db.session.get(Will, will_id)
    if not will_record:
        flash('Will not found.', 'error')
        return redirect(url_for('approval_list'))

    if will_record.status not in ('pending_approval', 'generated'):
        flash('This will is not pending approval.', 'error')
        return redirect(url_for('approval_list'))

    remarks = request.form.get('remarks', '').strip()
    will_record.status = 'approved'
    will_record.approved_by = session['user_id']
    will_record.approved_at = datetime.utcnow()
    will_record.approval_remarks = remarks or None
    db.session.commit()
    flash(f'Will "{will_record.title}" approved.', 'success')
    # If approving from preview page, redirect back there
    if request.referrer and '/preview' in request.referrer:
        return redirect(url_for('preview'))
    return redirect(url_for('approval_list'))


@app.route('/wills/<will_id>/reject', methods=['POST'])
@role_required('approver')
def will_reject(will_id):
    """Reject a will."""
    will_record = db.session.get(Will, will_id)
    if not will_record:
        flash('Will not found.', 'error')
        return redirect(url_for('approval_list'))

    if will_record.status != 'pending_approval':
        flash('This will is not pending approval.', 'error')
        return redirect(url_for('approval_list'))

    remarks = request.form.get('remarks', '').strip()
    will_record.status = 'rejected'
    will_record.approved_by = session['user_id']
    will_record.approved_at = datetime.utcnow()
    will_record.approval_remarks = remarks or 'No reason provided.'
    db.session.commit()
    flash(f'Will "{will_record.title}" rejected.', 'info')
    return redirect(url_for('approval_list'))


@app.route('/api/will/<will_id>/edit-text', methods=['POST'])
@login_required
def api_will_edit_text(will_id):
    """Save edits to the will text and log the change."""
    will_record = db.session.get(Will, will_id)
    if not will_record:
        return jsonify({'ok': False, 'error': 'Will not found'}), 404

    if not will_record.generated_will_text:
        return jsonify({'ok': False, 'error': 'No generated will text to edit'}), 400

    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'ok': False, 'error': 'No text provided'}), 400

    new_text = data['text'].strip()
    if not new_text:
        return jsonify({'ok': False, 'error': 'Will text cannot be empty'}), 400

    # Compute diff summary with specific change details
    old_lines = (will_record.generated_will_text or '').splitlines()
    new_lines = new_text.splitlines()
    added = removed = changed = 0
    change_details = []  # Specific descriptions of what changed
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes():
        if tag == 'insert':
            added += (j2 - j1)
            for line in new_lines[j1:j2]:
                snippet = line.strip()[:80]
                if snippet:
                    change_details.append(f'+ "{snippet}"')
        elif tag == 'delete':
            removed += (i2 - i1)
            for line in old_lines[i1:i2]:
                snippet = line.strip()[:80]
                if snippet:
                    change_details.append(f'- "{snippet}"')
        elif tag == 'replace':
            changed += max(i2 - i1, j2 - j1)
            # Show first replaced line
            old_snippet = old_lines[i1].strip()[:60] if i1 < len(old_lines) else ''
            new_snippet = new_lines[j1].strip()[:60] if j1 < len(new_lines) else ''
            if old_snippet and new_snippet:
                change_details.append(f'"{old_snippet}" → "{new_snippet}"')
    parts = []
    if changed:
        parts.append(f"{changed} line{'s' if changed != 1 else ''} changed")
    if added:
        parts.append(f"{added} line{'s' if added != 1 else ''} added")
    if removed:
        parts.append(f"{removed} line{'s' if removed != 1 else ''} removed")
    summary = ', '.join(parts) or 'Minor formatting changes'
    # Append first few change details for specificity
    if change_details:
        detail_str = '; '.join(change_details[:3])
        if len(change_details) > 3:
            detail_str += f' (+{len(change_details) - 3} more)'
        summary += f' — {detail_str}'

    # Get editor name
    editor = db.session.get(User, session['user_id'])
    editor_name = editor.name if editor else 'Unknown'

    # Save edited text
    will_record.generated_will_text = new_text
    will_record.text_edited_by = session['user_id']
    will_record.text_edited_at = datetime.utcnow()

    # Create edit log entry
    log_entry = WillEditLog(
        will_id=will_id,
        edited_by=session['user_id'],
        edited_by_name=editor_name,
        edited_at=datetime.utcnow(),
        summary=summary,
    )
    db.session.add(log_entry)
    db.session.commit()

    # Save change log file in client folder
    try:
        client = db.session.get(Client, will_record.client_id)
        if client:
            log_dir = os.path.join(UPLOAD_DIR, client.folder_name, 'edit_logs')
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, f'edit_log_{will_record.id[:8]}.txt')
            timestamp = will_record.text_edited_at.strftime('%d %b %Y, %I:%M %p')
            with open(log_file, 'a') as f:
                f.write(f"[{timestamp}] {editor_name} — {summary}\n")

            # Also write a unified diff file for this edit
            diff_lines = list(difflib.unified_diff(
                old_lines, new_lines,
                fromfile='before', tofile='after',
                lineterm='',
            ))
            if diff_lines:
                diff_file = os.path.join(log_dir, f'diff_{will_record.text_edited_at.strftime("%Y%m%d_%H%M%S")}.txt')
                with open(diff_file, 'w') as f:
                    f.write(f"Edit by {editor_name} at {timestamp}\n")
                    f.write(f"Summary: {summary}\n")
                    f.write('=' * 60 + '\n')
                    f.write('\n'.join(diff_lines) + '\n')
    except Exception:
        pass  # Don't fail the API call if file write fails

    return jsonify({
        'ok': True,
        'edited_at': will_record.text_edited_at.strftime('%d %b %Y, %I:%M %p'),
        'summary': summary,
        'editor_name': editor_name,
    })


# ---------------------------------------------------------------------------
# AI Redraft (clean up edited will text)
# ---------------------------------------------------------------------------

@app.route('/api/will/<will_id>/redraft', methods=['POST'])
@login_required
def api_will_redraft(will_id):
    """Send edited will text to Claude AI for cleanup: renumber clauses, fix cross-references."""
    will_record = db.session.get(Will, will_id)
    if not will_record:
        return jsonify({'ok': False, 'error': 'Will not found'}), 404

    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'ok': False, 'error': 'No text provided'}), 400

    current_text = data['text'].strip()
    if not current_text:
        return jsonify({'ok': False, 'error': 'Will text is empty'}), 400

    # Call Claude to clean up
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-20250514'),
            max_tokens=8000,
            system="""You are a Malaysian will drafting assistant. Your task is to clean up an edited will document.
Rules:
- Renumber all clauses sequentially (1, 2, 3...)
- Fix any cross-references to match new numbering (e.g. "clause 3 above" → correct clause number)
- Fix grammar issues caused by clause removal or reordering
- Preserve ALL remaining text exactly as written — do not add, rewrite, or remove any content
- Keep the exact same formatting style (spacing, capitalization, structure)
- Output ONLY the cleaned-up will text, nothing else — no preamble, no explanation""",
            messages=[{
                'role': 'user',
                'content': f"Clean up this edited will document. Renumber clauses, fix cross-references, fix grammar from any removed clauses. Output only the will text:\n\n{current_text}"
            }],
        )
        cleaned_text = response.content[0].text.strip()
    except Exception as e:
        return jsonify({'ok': False, 'error': f'AI redraft failed: {str(e)}'}), 500

    return jsonify({'ok': True, 'text': cleaned_text})


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_will_email(to_email, subject, body_html, attachments=None, tenant=None):
    """Send email via Google Workspace SMTP Relay (IP-based auth) with tenant-specific FROM/CC."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    tenant = tenant or get_tenant()
    from_email = tenant.get('email_from')
    if not from_email:
        raise ValueError('No email_from configured for this tenant')
    cc_list = tenant.get('email_cc', [])

    msg = MIMEMultipart()
    msg['From'] = from_email
    msg['To'] = to_email
    msg['Subject'] = subject
    if cc_list:
        msg['Cc'] = ', '.join(cc_list)

    msg.attach(MIMEText(body_html, 'html'))

    # Attach files (list of dicts: {'filename': ..., 'data': bytes, 'mime': ...})
    for att in (attachments or []):
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(att['data'])
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename="{att["filename"]}"')
        msg.attach(part)

    all_recipients = [to_email] + cc_list
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        # IP-based relay: login only if credentials are configured
        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(from_email, all_recipients, msg.as_string())

    return True


@app.route('/api/will/<will_id>/send-email', methods=['POST'])
@login_required
def api_will_send_email(will_id):
    """Email the approved will (PDF) to the client."""
    will_record = db.session.get(Will, will_id)
    if not will_record:
        return jsonify({'ok': False, 'error': 'Will not found'}), 404

    if will_record.status != 'approved':
        return jsonify({'ok': False, 'error': 'Only approved wills can be emailed'}), 403

    # Check permission (approver always can, admin/advisor for approved wills)
    user_perms = ROLE_PERMS.get(session.get('user_role', ''), {})
    if not user_perms.get('canEmail') and will_record.status != 'approved':
        return jsonify({'ok': False, 'error': 'You do not have permission to email wills'}), 403

    # Get client email
    client = db.session.get(Client, will_record.client_id)
    if not client or not client.email:
        return jsonify({'ok': False, 'error': 'Client email address not found. Please update the client profile.'}), 400

    # Generate PDF attachment
    will_text = will_record.generated_will_text or ''
    if not will_text:
        return jsonify({'ok': False, 'error': 'No will text to send'}), 400

    testator_name = client.full_name or 'Client'
    safe_name = "".join(c for c in testator_name if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_') or 'Will'

    try:
        from documents.pdf_generator import generate_pdf
        # Respect will's include_logo preference
        logo = None
        if will_record.include_logo:
            logo = _get_logo_path()
        filepath = generate_pdf(will_text, safe_name, logo_path=logo)
        with open(filepath, 'rb') as f:
            pdf_data = f.read()
    except Exception as e:
        app.logger.error(f'PDF generation failed: {e}')
        return jsonify({'ok': False, 'error': 'Failed to generate PDF'}), 500

    # Build email
    tenant = get_tenant()
    brand = tenant.get('brand', 'WillCraft AI')
    subject = f"Your Last Will and Testament - {testator_name}"
    body_html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #1a365d;">Your Last Will and Testament</h2>
        <p>Dear {testator_name},</p>
        <p>Please find attached your Last Will and Testament document in PDF format.</p>
        <p>Kindly review the document carefully. If you have any questions or require
        any amendments, please do not hesitate to contact us.</p>
        <br>
        <p>Best regards,<br><strong>{brand}</strong></p>
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
        <p style="font-size: 12px; color: #718096;">
            This email and its attachment are confidential and intended solely for the addressee.
        </p>
    </div>
    """

    try:
        send_will_email(
            to_email=client.email,
            subject=subject,
            body_html=body_html,
            attachments=[{
                'filename': f'{safe_name}_Will.pdf',
                'data': pdf_data,
            }],
            tenant=tenant,
        )
    except Exception as e:
        app.logger.error(f'Email sending failed: {e}')
        return jsonify({'ok': False, 'error': f'Failed to send email: {str(e)}'}), 500

    # Log the email send
    sender_name = session.get('user_name', 'Unknown')
    app.logger.info(f'Will {will_id} emailed to {client.email} by {sender_name}')

    return jsonify({
        'ok': True,
        'sent_to': client.email,
        'cc': tenant.get('email_cc', []),
        'message': f'Will emailed to {client.email}',
    })


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
@login_required
def index():
    """Landing page."""
    wills_query = Will.query
    # Approvers see all wills; admin/advisor see only their own
    if session.get('user_role') != 'approver':
        wills_query = wills_query.filter_by(created_by=session.get('user_id'))
    saved_wills = wills_query.order_by(Will.updated_at.desc()).all()
    return render_template('index.html', saved_wills=saved_wills)


# -- Save / Load / Delete Wills ------------------------------------------------

@app.route('/api/will/save', methods=['POST'])
@login_required
def api_will_save():
    """AJAX endpoint to save current session to DB."""
    try:
        will_record = save_will_to_db()
        return jsonify({'ok': True, 'will_id': will_record.id})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/will/toggle-logo', methods=['POST'])
@login_required
def api_will_toggle_logo():
    """Toggle include_logo flag on the current will."""
    will_id = session.get('will_id')
    if not will_id:
        return jsonify({'ok': False, 'error': 'No will in session'}), 400
    wr = db.session.get(Will, will_id)
    if not wr:
        return jsonify({'ok': False, 'error': 'Will not found'}), 404
    data = request.get_json(silent=True) or {}
    wr.include_logo = bool(data.get('include_logo', True))
    db.session.commit()
    return jsonify({'ok': True, 'include_logo': wr.include_logo})


@app.route('/api/will/delete-generated', methods=['POST'])
@login_required
def api_will_delete_generated():
    """Delete the generated will text (not the will record itself)."""
    will_id = session.get('will_id')
    if not will_id:
        return jsonify({'ok': False, 'error': 'No will in session'}), 400
    wr = db.session.get(Will, will_id)
    if not wr:
        return jsonify({'ok': False, 'error': 'Will not found'}), 404
    wr.generated_will_text = None
    wr.status = 'draft'
    wr.submitted_by = None
    wr.submitted_at = None
    wr.approved_by = None
    wr.approved_at = None
    wr.approval_remarks = None
    session.pop('generated_will_text', None)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/will/version/<int:version_id>/delete', methods=['POST'])
@login_required
def api_will_delete_version(version_id):
    """Delete a specific version from version history."""
    will_id = session.get('will_id')
    if not will_id:
        return jsonify({'ok': False, 'error': 'No will in session'}), 400
    version = db.session.get(WillVersion, version_id)
    if not version or version.will_id != will_id:
        return jsonify({'ok': False, 'error': 'Version not found'}), 404
    # Don't allow deleting if it's the only version
    total = WillVersion.query.filter_by(will_id=will_id).count()
    if total <= 1:
        return jsonify({'ok': False, 'error': 'Cannot delete the only version'}), 400
    # Check if deleting the latest version
    latest = WillVersion.query.filter_by(will_id=will_id).order_by(
        WillVersion.version_number.desc()
    ).first()
    is_latest = (version.id == latest.id)
    db.session.delete(version)
    db.session.flush()
    # If we deleted the latest, update will's generated_will_text to the new latest
    if is_latest:
        new_latest = WillVersion.query.filter_by(will_id=will_id).order_by(
            WillVersion.version_number.desc()
        ).first()
        if new_latest:
            wr = db.session.get(Will, will_id)
            if wr:
                wr.generated_will_text = new_latest.will_text
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/feedback', methods=['POST'])
@login_required
def api_feedback():
    """Send feedback/issue report to support@lifa.com.my."""
    client_name = request.form.get('client_name', '').strip()
    description = request.form.get('description', '').strip()
    if not description:
        return jsonify({'ok': False, 'error': 'Description is required'}), 400

    user = session.get('user', {})
    user_name = user.get('name', 'Unknown')
    user_email = user.get('email', 'Unknown')
    tenant = get_tenant()
    tenant_host = tenant.get('host', 'unknown')

    subject = f"[WillCraft] Issue Report — {client_name or 'No client'}"
    body_html = f"""
    <h3>Issue Report from WillCraft AI</h3>
    <p><strong>Client Name:</strong> {client_name}</p>
    <p><strong>Reported by:</strong> {user_name} ({user_email})</p>
    <p><strong>Site:</strong> {tenant_host}</p>
    <hr>
    <p><strong>Problem Description:</strong></p>
    <p style="white-space: pre-wrap;">{description}</p>
    """

    attachments = []
    screenshot = request.files.get('screenshot')
    if screenshot and screenshot.filename:
        att_data = screenshot.read()
        attachments.append({
            'filename': screenshot.filename,
            'data': att_data,
            'mime': screenshot.content_type or 'image/png',
        })

    try:
        send_will_email(
            to_email='support@lifa.com.my',
            subject=subject,
            body_html=body_html,
            attachments=attachments,
            tenant=tenant,
        )
        return jsonify({'ok': True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/wills')
@login_required
def will_list():
    """Unified client+wills page: list all clients with their wills grouped."""
    q = request.args.get('q', '').strip()
    if q:
        all_clients = Client.query.filter(
            db.or_(
                Client.full_name.ilike(f'%{q}%'),
                Client.nric_passport.ilike(f'%{q}%'),
            )
        ).order_by(Client.updated_at.desc()).all()
    else:
        all_clients = Client.query.order_by(Client.updated_at.desc()).all()

    # Build grouped data: each client with their wills and stats
    user_role = session.get('user_role', '')
    user_id = session.get('user_id', '')
    client_groups = []
    for c in all_clients:
        wills_query = Will.query.filter_by(client_id=c.id)
        # Approvers see all wills; admin/advisor see only their own
        if user_role != 'approver':
            wills_query = wills_query.filter_by(created_by=user_id)
        wills = wills_query.order_by(Will.updated_at.desc()).all()
        if user_role != 'approver' and not wills:
            continue  # Skip clients with no wills for this user
        doc_count = Document.query.filter_by(client_id=c.id).count()
        # Count generated files on disk
        draft_count = 0
        generated_count = 0
        folder_path = os.path.join(UPLOAD_DIR, c.folder_name)
        drafts_dir = os.path.join(folder_path, 'drafts')
        gen_dir = os.path.join(folder_path, 'generated')
        if os.path.isdir(drafts_dir):
            draft_count = len([f for f in os.listdir(drafts_dir) if os.path.isfile(os.path.join(drafts_dir, f))])
        if os.path.isdir(gen_dir):
            generated_count = len([f for f in os.listdir(gen_dir) if os.path.isfile(os.path.join(gen_dir, f))])
        client_groups.append({
            'client': c,
            'wills': wills,
            'doc_count': doc_count,
            'draft_count': draft_count,
            'generated_count': generated_count,
        })
    return render_template('will_list.html', client_groups=client_groups, search_query=q)


@app.route('/wills/<will_id>/load')
@login_required
def will_load(will_id):
    """Load a saved will into the session."""
    will_record = db.session.get(Will, will_id)
    if not will_record:
        flash('Will not found.', 'error')
        return redirect(url_for('index'))
    load_will_to_session(will_record)
    flash(f'Loaded: {will_record.title}', 'info')
    # If ?goto=preview and will has generated text, go directly to preview
    if request.args.get('goto') == 'preview' and (will_record.generated_will_text or will_record.status in ('generated', 'pending_approval', 'approved')):
        return redirect(url_for('preview'))
    return redirect(url_for('wizard_step_identities'))


@app.route('/wills/<will_id>/delete', methods=['POST'])
@login_required
def will_delete(will_id):
    """Delete a saved will."""
    will_record = db.session.get(Will, will_id)
    if will_record:
        db.session.delete(will_record)
        db.session.commit()
        # Clear session if we deleted the currently loaded will
        if session.get('will_id') == will_id:
            session.pop('will_id', None)
        flash('Will deleted.', 'info')
    return redirect(url_for('will_list'))


@app.route('/clients/<client_id>/delete', methods=['POST'])
@login_required
def client_delete(client_id):
    """Delete a client and ALL associated data (wills, persons, documents, disk files)."""
    import shutil
    client = db.session.get(Client, client_id)
    if not client:
        flash('Client not found.', 'error')
        return redirect(url_for('will_list'))

    # Delete associated documents from disk
    folder_path = os.path.join(UPLOAD_DIR, client.folder_name)
    if os.path.isdir(folder_path):
        shutil.rmtree(folder_path, ignore_errors=True)

    # Delete DB records (cascade: documents, wills, persons)
    Document.query.filter_by(client_id=client_id).delete()
    Will.query.filter_by(client_id=client_id).delete()
    Person.query.filter_by(client_id=client_id).delete()
    db.session.delete(client)
    db.session.commit()

    # Clear session if we deleted the currently loaded client
    if session.get('client_id') == client_id:
        session.clear()

    flash(f'Client "{client.full_name}" and all associated data deleted.', 'info')
    return redirect(url_for('will_list'))


# -- Person Registry API -------------------------------------------------------

@app.route('/api/persons', methods=['GET'])
@login_required
def api_persons_list():
    """Return JSON list of persons for the current client."""
    client_id = session.get('client_id')
    if not client_id:
        return jsonify([])
    persons = Person.query.filter_by(client_id=client_id).order_by(Person.full_name).all()
    return jsonify([
        {'id': p.id, 'full_name': p.full_name, 'nric_passport': p.nric_passport,
         'address': p.address or '', 'date_of_birth': p.date_of_birth or '',
         'nationality': p.nationality or 'Malaysian', 'gender': p.gender or '',
         'passport_expiry': p.passport_expiry or '',
         'email': p.email or '', 'phone': p.phone or '',
         'relationship': p.relationship or '',
         'document_id': p.document_id or ''}
        for p in persons
    ])


@app.route('/api/persons', methods=['POST'])
@login_required
def api_persons_create():
    """Create a new person identity."""
    client_id = session.get('client_id')
    if not client_id:
        client_id = ensure_client()
    data = request.get_json() or {}
    full_name = (data.get('full_name') or '').strip()
    nric_passport = (data.get('nric_passport') or '').strip()
    if not full_name or not nric_passport:
        return jsonify({'ok': False, 'error': 'Name and NRIC/Passport are required'}), 400
    try:
        person = upsert_person(
            client_id, full_name, nric_passport,
            address=(data.get('address') or '').strip(),
            date_of_birth=(data.get('date_of_birth') or '').strip() or None,
            nationality=(data.get('nationality') or 'Malaysian').strip(),
            gender=(data.get('gender') or '').strip() or None,
            passport_expiry=(data.get('passport_expiry') or '').strip() or None,
            email=(data.get('email') or '').strip() or None,
            phone=(data.get('phone') or '').strip() or None,
            relationship=(data.get('relationship') or '').strip() or None,
            document_id=(data.get('document_id') or '').strip() or None,
        )
    except Exception as e:
        app.logger.error(f'Failed to save identity: {e}')
        return jsonify({'ok': False, 'error': f'Failed to save: {str(e)}'}), 500
    return jsonify({'ok': True, 'person': {
        'id': person.id, 'full_name': person.full_name,
        'nric_passport': person.nric_passport, 'address': person.address or '',
        'nationality': person.nationality or 'Malaysian',
        'date_of_birth': person.date_of_birth or '',
        'gender': person.gender or '',
        'email': person.email or '', 'phone': person.phone or '',
        'passport_expiry': person.passport_expiry or '',
        'relationship': person.relationship or '',
        'document_id': person.document_id or '',
    }})


@app.route('/api/persons/<person_id>', methods=['PUT'])
@login_required
def api_persons_update(person_id):
    """Update an existing person identity."""
    person = db.session.get(Person, person_id)
    if not person:
        return jsonify({'ok': False, 'error': 'Person not found'}), 404
    data = request.get_json() or {}
    old_name = person.full_name  # Capture before update
    if data.get('full_name'):
        person.full_name = data['full_name'].strip().upper()
    if data.get('nric_passport'):
        person.nric_passport = data['nric_passport'].strip()
    if 'address' in data:
        person.address = (data['address'] or '').strip()
    if 'nationality' in data:
        person.nationality = (data['nationality'] or 'Malaysian').strip()
    if 'passport_expiry' in data:
        person.passport_expiry = (data['passport_expiry'] or '').strip() or None
    if 'date_of_birth' in data:
        person.date_of_birth = (data['date_of_birth'] or '').strip() or None
    if 'gender' in data:
        person.gender = (data['gender'] or '').strip() or None
    if 'email' in data:
        person.email = (data['email'] or '').strip() or None
    if 'phone' in data:
        person.phone = (data['phone'] or '').strip() or None
    if 'relationship' in data:
        person.relationship = (data['relationship'] or '').strip() or None
    if 'document_id' in data:
        person.document_id = (data['document_id'] or '').strip() or None
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Failed to update identity: {e}')
        return jsonify({'ok': False, 'error': f'Failed to update: {str(e)}'}), 500
    _refresh_session_person_registry(person.client_id)
    _propagate_identity_changes(person.id, person.full_name, person.nric_passport, old_name)
    save_will_to_db()  # Persist propagated changes
    return jsonify({'ok': True, 'person': {
        'id': person.id, 'full_name': person.full_name,
        'nric_passport': person.nric_passport, 'address': person.address or '',
        'nationality': person.nationality or 'Malaysian',
        'date_of_birth': person.date_of_birth or '',
        'gender': person.gender or '',
        'email': person.email or '', 'phone': person.phone or '',
        'passport_expiry': person.passport_expiry or '',
        'relationship': person.relationship or '',
        'document_id': person.document_id or '',
    }})


@app.route('/api/persons/<person_id>', methods=['DELETE'])
@login_required
def api_persons_delete(person_id):
    """Delete a person identity after checking for references."""
    person = db.session.get(Person, person_id)
    if not person:
        return jsonify({'ok': False, 'error': 'Person not found'}), 404

    # Check if this person is referenced anywhere in the will
    refs = _get_person_references(person_id, person.full_name)
    if refs:
        msg = f"Cannot delete {person.full_name}. This person is assigned as:\n"
        msg += "\n".join(f"• {r}" for r in refs)
        msg += "\n\nPlease remove them from these roles first."
        return jsonify({'ok': False, 'error': msg}), 400

    client_id = person.client_id
    db.session.delete(person)
    db.session.commit()
    _refresh_session_person_registry(client_id)
    return jsonify({'ok': True})


def _get_person_references(person_id, full_name):
    """Find all references to a person across will wizard session data."""
    refs = []

    # Step 2 - Testator
    step1 = session.get('step1', {})
    if step1.get('person_id') == person_id:
        refs.append('Testator')

    # Step 3 - Executors
    for ex in session.get('step2_executors', []):
        if ex.get('person_id') == person_id:
            role = ex.get('role', 'Primary')
            refs.append(f'Executor ({role})')

    # Step 3 - Trustees
    trustee_data = session.get('step3_trustees', {})
    for tr in trustee_data.get('trustees', []):
        if tr.get('person_id') == person_id:
            refs.append('Trustee')
    for st in trustee_data.get('substitute_trustees', []):
        if st.get('person_id') == person_id:
            refs.append('Substitute Trustee')

    # Step 4 - Guardians
    for gdn in session.get('step3_guardians', []):
        if gdn.get('person_id') == person_id:
            role = gdn.get('role', 'Primary')
            refs.append(f'Guardian ({role})')

    # Step 5 - Beneficiaries
    for ben in session.get('step4_beneficiaries', []):
        if ben.get('person_id') == person_id:
            refs.append('Beneficiary')

    # Step 6 - Gift allocations (matched by name)
    for gi, gift in enumerate(session.get('step5_gifts', [])):
        gift_num = gi + 1
        for alloc in gift.get('allocations', []):
            if alloc.get('beneficiary_name') == full_name:
                refs.append(f'Beneficiary of Gift No. {gift_num}')
            for sub in alloc.get('substitutes', []):
                if sub.get('beneficiary_name') == full_name:
                    refs.append(f'Substitute Beneficiary of Gift No. {gift_num}')

    # Step 7 - Residuary Estate
    res = session.get('step6_residuary', {})
    for mb in res.get('main_beneficiaries', []):
        if mb.get('person_id') == person_id:
            refs.append('Residuary Beneficiary')
    for sg in res.get('substitute_groups', []):
        for sb in (sg if isinstance(sg, list) else []):
            if sb.get('person_id') == person_id:
                refs.append('Residuary Substitute Beneficiary')

    # Step 8 - Trust
    trust = session.get('step7_trust', {})
    if trust.get('trustee_person_id') == person_id:
        refs.append('Trust Trustee')

    return refs


# -- Upload & Document API ----------------------------------------------------

@app.route('/api/upload', methods=['POST'])
@login_required
def api_upload():
    """Generic file upload endpoint."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
    category = request.form.get('category', 'general')
    client_id = session.get('client_id')
    if not client_id:
        client_id = ensure_client()
    try:
        from uploads import save_uploaded_file
        folder_name = get_client_folder_name(client_id)
        saved_name, rel_path, file_size = save_uploaded_file(file, client_id, category, folder_name=folder_name)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    doc = Document(
        client_id=client_id,
        will_id=session.get('will_id'),
        filename=saved_name,
        original_filename=file.filename,
        file_path=rel_path,
        file_type=file.content_type,
        file_size=file_size,
        category=category,
    )
    db.session.add(doc)
    db.session.commit()
    return jsonify({'ok': True, 'document_id': doc.id, 'filename': saved_name})


@app.route('/api/documents')
@login_required
def api_documents_list():
    """List documents for current client."""
    client_id = session.get('client_id')
    if not client_id:
        return jsonify([])
    docs = Document.query.filter_by(client_id=client_id).order_by(Document.created_at.desc()).all()
    return jsonify([
        {'id': d.id, 'filename': d.original_filename, 'category': d.category,
         'file_size': d.file_size, 'created_at': d.created_at.isoformat()}
        for d in docs
    ])


@app.route('/api/documents/<doc_id>')
@login_required
def api_document_view(doc_id):
    """View/download a specific document."""
    doc = db.session.get(Document, doc_id)
    if not doc:
        return jsonify({'error': 'Document not found'}), 404
    from config import UPLOAD_DIR
    abs_path = os.path.join(UPLOAD_DIR, doc.file_path)
    if not os.path.exists(abs_path):
        return jsonify({'error': 'File not found on disk'}), 404
    return send_file(abs_path, download_name=doc.original_filename)


@app.route('/api/documents/<doc_id>', methods=['DELETE'])
@login_required
def api_document_delete(doc_id):
    """Delete a specific document."""
    doc = db.session.get(Document, doc_id)
    if not doc:
        return jsonify({'ok': False, 'error': 'Document not found'}), 404
    # Remove file from disk
    from config import UPLOAD_DIR
    abs_path = os.path.join(UPLOAD_DIR, doc.file_path)
    if os.path.exists(abs_path):
        os.remove(abs_path)
    # Clear document_id from any linked persons
    linked_persons = Person.query.filter_by(document_id=doc_id).all()
    for p in linked_persons:
        p.document_id = None
    db.session.delete(doc)
    db.session.commit()
    if linked_persons:
        _refresh_session_person_registry(linked_persons[0].client_id)
    return jsonify({'ok': True})


@app.route('/api/documents/<doc_id>/translate', methods=['POST'])
@login_required
def api_document_translate(doc_id):
    """Translate a document image from Bahasa Malaysia to English."""
    doc = db.session.get(Document, doc_id)
    if not doc:
        return jsonify({'ok': False, 'error': 'Document not found'}), 404
    from config import UPLOAD_DIR
    abs_path = os.path.join(UPLOAD_DIR, doc.file_path)
    if not os.path.isfile(abs_path):
        return jsonify({'ok': False, 'error': 'File not found on disk'}), 404
    try:
        from ai.ocr import translate_document
        translation = translate_document(abs_path)
        return jsonify({'ok': True, 'translation': translation})
    except Exception as e:
        app.logger.error(f'Document translate error: {e}')
        return jsonify({'ok': False, 'error': 'Translation failed. Please try again.'}), 500


# -- OCR Extraction API -------------------------------------------------------

@app.route('/api/ocr/nric', methods=['POST'])
@login_required
def api_ocr_nric():
    """Upload NRIC/passport image, extract data via Claude Vision."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
    fmt_err = _validate_ocr_file(file)
    if fmt_err:
        return jsonify({'ok': False, 'error': fmt_err}), 400
    client_id = session.get('client_id')
    if not client_id:
        client_id = ensure_client()
    try:
        from uploads import save_uploaded_file
        folder_name = get_client_folder_name(client_id)
        saved_name, rel_path, file_size = save_uploaded_file(file, client_id, 'nric', folder_name=folder_name)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    abs_path = os.path.join(UPLOAD_DIR, rel_path)
    extracted = None
    ocr_warning = None
    try:
        from ai.ocr import extract_nric_data
        extracted = extract_nric_data(abs_path)
    except Exception as e:
        app.logger.error(f'OCR NRIC error: {e}')
        ocr_warning = 'Image unclear — could not scan automatically. File saved. Please fill in the details manually.'

    if extracted:
        # Address fallback: if OCR couldn't read address clearly (empty),
        # check if the same NRIC already has an address on file and use that.
        if not extracted.get('address') and extracted.get('nric_number'):
            nric_norm = extracted['nric_number'].replace('-', '').replace(' ', '').upper()
            existing_persons = Person.query.filter_by(client_id=client_id).all()
            for p in existing_persons:
                p_norm = (p.nric_passport or '').replace('-', '').replace(' ', '').upper()
                if p_norm == nric_norm and p.address:
                    extracted['address'] = p.address
                    extracted['_address_from_existing'] = True
                    break
        # Remove internal confidence field (not needed in response)
        extracted.pop('confidence', None)

    # Always save Document record (file is saved regardless of OCR success)
    doc = Document(
        client_id=client_id, will_id=session.get('will_id'),
        filename=saved_name, original_filename=file.filename,
        file_path=rel_path, file_type=file.content_type,
        file_size=file_size, category='nric',
        extracted_data=json.dumps(extracted) if extracted else None,
    )
    db.session.add(doc)
    db.session.commit()
    result = {'ok': True, 'document_id': doc.id}
    if extracted:
        result['extracted'] = extracted
    if ocr_warning:
        result['warning'] = ocr_warning
    return jsonify(result)


@app.route('/api/ocr/nric/<document_id>', methods=['POST'])
@login_required
def api_ocr_nric_scan(document_id):
    """Run OCR on an already-uploaded NRIC/passport document."""
    doc = db.session.get(Document, document_id)
    if not doc:
        return jsonify({'ok': False, 'error': 'Document not found'}), 404
    abs_path = os.path.join(UPLOAD_DIR, doc.file_path)
    if not os.path.isfile(abs_path):
        return jsonify({'ok': False, 'error': 'File not found on disk'}), 404

    client_id = doc.client_id or session.get('client_id')
    extracted = None
    ocr_warning = None
    try:
        from ai.ocr import extract_nric_data
        extracted = extract_nric_data(abs_path)
    except Exception as e:
        app.logger.error(f'OCR NRIC scan error: {e}')
        ocr_warning = 'Image unclear — could not scan automatically. Please fill in the details manually.'

    if extracted:
        if not extracted.get('address') and extracted.get('nric_number'):
            nric_norm = extracted['nric_number'].replace('-', '').replace(' ', '').upper()
            existing_persons = Person.query.filter_by(client_id=client_id).all()
            for p in existing_persons:
                p_norm = (p.nric_passport or '').replace('-', '').replace(' ', '').upper()
                if p_norm == nric_norm and p.address:
                    extracted['address'] = p.address
                    extracted['_address_from_existing'] = True
                    break
        extracted.pop('confidence', None)
        doc.extracted_data = json.dumps(extracted)
        db.session.commit()

    result = {'ok': True, 'document_id': doc.id}
    if extracted:
        result['extracted'] = extracted
    if ocr_warning:
        result['warning'] = ocr_warning
    return jsonify(result)


@app.route('/api/ocr/property', methods=['POST'])
@login_required
def api_ocr_property():
    """Upload cukai tanah/cukai pintu, extract property data."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
    fmt_err = _validate_ocr_file(file)
    if fmt_err:
        return jsonify({'ok': False, 'error': fmt_err}), 400
    client_id = session.get('client_id')
    if not client_id:
        client_id = ensure_client()
    try:
        from uploads import save_uploaded_file
        folder_name = get_client_folder_name(client_id)
        saved_name, rel_path, file_size = save_uploaded_file(file, client_id, 'property', folder_name=folder_name)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    abs_path = os.path.join(UPLOAD_DIR, rel_path)
    extracted = None
    ocr_warning = None
    try:
        from ai.property_extractor import extract_property_data
        doc_type = request.form.get('doc_type', 'general')
        extracted = extract_property_data(abs_path, doc_type=doc_type)
    except Exception as e:
        app.logger.error(f'OCR property error: {e}')
        ocr_warning = 'Image unclear — could not scan automatically. File saved. Please fill in the details manually.'
    doc = Document(
        client_id=client_id, will_id=session.get('will_id'),
        filename=saved_name, original_filename=file.filename,
        file_path=rel_path, file_type=file.content_type,
        file_size=file_size, category='property',
        extracted_data=json.dumps(extracted) if extracted else None,
    )
    db.session.add(doc)
    db.session.commit()
    result = {'ok': True, 'document_id': doc.id, 'document_url': f'/api/documents/{doc.id}'}
    if extracted:
        result['extracted'] = extracted
    if ocr_warning:
        result['warning'] = ocr_warning
    return jsonify(result)


@app.route('/api/ocr/asset', methods=['POST'])
@login_required
def api_ocr_asset():
    """Upload bank/investment statement, extract asset data."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
    fmt_err = _validate_ocr_file(file)
    if fmt_err:
        return jsonify({'ok': False, 'error': fmt_err}), 400
    client_id = session.get('client_id')
    if not client_id:
        client_id = ensure_client()
    try:
        from uploads import save_uploaded_file
        folder_name = get_client_folder_name(client_id)
        saved_name, rel_path, file_size = save_uploaded_file(file, client_id, 'financial', folder_name=folder_name)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    abs_path = os.path.join(UPLOAD_DIR, rel_path)
    extracted = None
    ocr_warning = None
    try:
        from ai.asset_extractor import extract_asset_data
        extracted = extract_asset_data(abs_path)
    except Exception as e:
        app.logger.error(f'OCR asset error: {e}')
        ocr_warning = 'Image unclear — could not scan automatically. File saved. Please fill in the details manually.'
    doc = Document(
        client_id=client_id, will_id=session.get('will_id'),
        filename=saved_name, original_filename=file.filename,
        file_path=rel_path, file_type=file.content_type,
        file_size=file_size, category='financial',
        extracted_data=json.dumps(extracted) if extracted else None,
    )
    db.session.add(doc)
    db.session.commit()
    result = {'ok': True, 'document_id': doc.id, 'document_url': f'/api/documents/{doc.id}'}
    if extracted:
        result['extracted'] = extracted
    if ocr_warning:
        result['warning'] = ocr_warning
    return jsonify(result)


@app.route('/client/documents')
@login_required
def client_documents():
    """Client document browser page (legacy — redirects to new client files page)."""
    client_id = session.get('client_id')
    if client_id:
        return redirect(url_for('client_files', client_id=client_id))
    documents = []
    return render_template('client_documents.html', documents=documents)


@app.route('/clients')
@login_required
def clients_list():
    """Redirect to unified /wills page (backward compatibility)."""
    q = request.args.get('q', '')
    if q:
        return redirect(url_for('will_list', q=q))
    return redirect(url_for('will_list'))


@app.route('/clients/<client_id>/files')
@login_required
def client_files(client_id):
    """Browse all files for a specific client: documents, drafts, generated wills."""
    client = db.session.get(Client, client_id)
    if not client:
        flash('Client not found.', 'error')
        return redirect(url_for('will_list'))

    # Uploaded documents from DB
    documents = Document.query.filter_by(client_id=client_id).order_by(Document.created_at.desc()).all()

    # Group docs by category
    doc_groups = {}
    for doc in documents:
        cat = doc.category or 'general'
        if cat not in doc_groups:
            doc_groups[cat] = []
        doc_groups[cat].append(doc)

    # Scan client folder for drafts and generated wills
    drafts = []
    generated = []
    folder_path = os.path.join(UPLOAD_DIR, client.folder_name)
    drafts_dir = os.path.join(folder_path, 'drafts')
    gen_dir = os.path.join(folder_path, 'generated')

    if os.path.isdir(drafts_dir):
        for fname in sorted(os.listdir(drafts_dir), reverse=True):
            fpath = os.path.join(drafts_dir, fname)
            if os.path.isfile(fpath):
                drafts.append({
                    'filename': fname,
                    'size': os.path.getsize(fpath),
                    'modified': os.path.getmtime(fpath),
                    'rel_path': os.path.join(client.folder_name, 'drafts', fname),
                })

    if os.path.isdir(gen_dir):
        for fname in sorted(os.listdir(gen_dir), reverse=True):
            fpath = os.path.join(gen_dir, fname)
            if os.path.isfile(fpath):
                generated.append({
                    'filename': fname,
                    'size': os.path.getsize(fpath),
                    'modified': os.path.getmtime(fpath),
                    'rel_path': os.path.join(client.folder_name, 'generated', fname),
                })

    # Wills in DB for this client
    wills = Will.query.filter_by(client_id=client_id).order_by(Will.updated_at.desc()).all()

    total_docs = sum(len(docs) for docs in doc_groups.values())
    return render_template('client_files.html',
                           client=client, doc_groups=doc_groups,
                           drafts=drafts, generated=generated, wills=wills,
                           total_docs=total_docs)


@app.route('/clients/<client_id>/files/download/<path:rel_path>')
@login_required
def client_file_download(client_id, rel_path):
    """Download a file from the client's folder."""
    client = db.session.get(Client, client_id)
    if not client:
        return jsonify({'error': 'Client not found'}), 404
    # Security: ensure path starts with client's folder name
    if not rel_path.startswith(client.folder_name):
        return jsonify({'error': 'Access denied'}), 403
    abs_path = os.path.join(UPLOAD_DIR, rel_path)
    if not os.path.exists(abs_path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(abs_path, download_name=os.path.basename(rel_path))


# -- Upload Existing Will ----------------------------------------------------

@app.route('/upload-will')
@login_required
def upload_will():
    """Page to upload an existing will for parsing."""
    return render_template('upload_will.html')


@app.route('/api/parse-will', methods=['POST'])
@login_required
def api_parse_will():
    """Upload and parse an existing will document, populate session."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ('pdf', 'docx'):
        return jsonify({'ok': False, 'error': 'Only PDF and DOCX files are supported'}), 400

    client_id = session.get('client_id')
    if not client_id:
        client_id = ensure_client()
    try:
        from uploads import save_uploaded_file
        folder_name = get_client_folder_name(client_id)
        saved_name, rel_path, file_size = save_uploaded_file(file, client_id, 'wills', folder_name=folder_name)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

    abs_path = os.path.join(UPLOAD_DIR, rel_path)

    # Save document record
    doc = Document(
        client_id=client_id, will_id=session.get('will_id'),
        filename=saved_name, original_filename=file.filename,
        file_path=rel_path, file_type=file.content_type,
        file_size=file_size, category='wills',
    )
    db.session.add(doc)
    db.session.commit()

    try:
        from ai.will_parser import parse_will_document
        parsed = parse_will_document(abs_path)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Parsing failed: {e}'}), 500

    if parsed.get('error'):
        return jsonify({'ok': False, 'error': parsed['error']}), 500

    # Populate session with parsed data
    if 'step1_testator' in parsed:
        session['step1'] = parsed['step1_testator']
    if 'step2_executors' in parsed:
        session['step2'] = parsed['step2_executors']
    if 'step3_guardians' in parsed:
        session['step3'] = parsed['step3_guardians']
    if 'step4_beneficiaries' in parsed:
        session['step4'] = parsed['step4_beneficiaries']
    if 'step5_gifts' in parsed:
        session['step5'] = parsed['step5_gifts']
    if 'step6_residuary' in parsed:
        session['step6'] = parsed['step6_residuary']
    if 'step7_trust' in parsed:
        session['step7'] = parsed['step7_trust']
    if 'step8_other_matters' in parsed:
        session['step8'] = parsed['step8_other_matters']

    session['will_title'] = f"Imported: {file.filename}"
    session.modified = True

    # Auto-save to DB
    save_will_to_db()

    return jsonify({'ok': True})


# -- Step 1: Identity Management ---------------------------------------------

@app.route('/wizard/step/1', methods=['GET', 'POST'])
@login_required
def wizard_step_identities():
    if request.method == 'GET':
        client_id = session.get('client_id')
        if client_id:
            _refresh_session_person_registry(client_id)
        return render_template(
            'wizard/step1_identities.html',
            current_step=1,
            completed_steps=get_completed_steps(),
            persons=session.get('person_registry', []),
        )

    # POST -- validate at least 1 identity exists, then proceed
    persons = session.get('person_registry', [])
    if not persons:
        flash('Please add at least one identity before proceeding.', 'error')
        return redirect(url_for('wizard_step_identities'))
    mark_step_complete(1)
    save_will_to_db()
    if request.form.get('_save_draft'):
        return jsonify({'ok': True, 'step': 1})
    return redirect(url_for('wizard_step_testator'))


# -- Step 2: Testator Info (simplified - select identity) --------------------

@app.route('/wizard/step/2', methods=['GET', 'POST'])
@login_required
def wizard_step_testator():
    if request.method == 'GET':
        return render_template(
            'wizard/step2_testator.html',
            current_step=2,
            completed_steps=get_completed_steps(),
            data=session.get('step1', {}),
            persons=session.get('person_registry', []),
        )

    # POST -- merge selected identity with testator-specific fields
    person_id = request.form.get('testator_person_id', '')
    person = _get_person_from_registry(person_id)

    dob_raw = request.form.get('date_of_birth', '')
    if dob_raw and '-' in dob_raw and len(dob_raw) == 10:
        parts = dob_raw.split('-')
        if len(parts) == 3 and len(parts[0]) == 4:
            dob_raw = f"{parts[2]}-{parts[1]}-{parts[0]}"

    special = request.form.getlist('special_circumstances')

    session['step1'] = {
        'person_id': person_id,
        'full_name': person['full_name'] if person else request.form.get('full_name', '').strip(),
        'nric_passport': person['nric_passport'] if person else request.form.get('nric_passport', '').strip(),
        'residential_address': person['address'] if person else request.form.get('residential_address', '').strip(),
        'nationality': person.get('nationality', 'Malaysian') if person else request.form.get('nationality', 'Malaysian').strip(),
        'country_of_residence': request.form.get('country_of_residence', 'Malaysia').strip(),
        'date_of_birth': dob_raw or (person.get('date_of_birth', '') if person else ''),
        'occupation': request.form.get('occupation', '').strip(),
        'religion': request.form.get('religion', '').strip() or None,
        'email': request.form.get('email', '').strip() or (person.get('email') if person else None),
        'phone': request.form.get('phone', '').strip() or (person.get('phone') if person else None),
        'gender': request.form.get('gender', 'Male'),
        'marital_status': request.form.get('marital_status', 'Single'),
        'has_prior_will': bool(request.form.get('has_prior_will')),
        'property_coverage': request.form.get('property_coverage', 'Malaysia'),
        'contemplation_of_marriage': bool(request.form.get('contemplation_of_marriage')),
        'fiance_name': request.form.get('fiance_name', '').strip() or None,
        'fiance_nric': request.form.get('fiance_nric', '').strip() or None,
        'signing_method': request.form.get('signing_method', 'Signature'),
        'special_circumstances': special,
        'translator_name': request.form.get('translator_name', '').strip() or None,
        'translator_nric': request.form.get('translator_nric', '').strip() or None,
        'translator_relationship': request.form.get('translator_relationship', '').strip() or None,
        'translator_language': request.form.get('translator_language', '').strip() or None,
    }
    session.modified = True
    mark_step_complete(2)
    save_will_to_db()
    if request.form.get('_save_draft'):
        return jsonify({'ok': True, 'step': 2})
    return redirect(url_for('wizard_step_executors'))


# -- Step 3: Executors (select from identities) -----------------------------

@app.route('/wizard/step/3', methods=['GET', 'POST'])
@login_required
def wizard_step_executors():
    if request.method == 'GET':
        return render_template(
            'wizard/step3_executors.html',
            current_step=3,
            completed_steps=get_completed_steps(),
            data={
                'executors': session.get('step2_executors', [{}]),
                'executor_type': session.get('step3_executor_type', 'single'),
                'trustee_data': session.get('step3_trustees', {'same_as_executor': False, 'trustees': [{}]}),
            },
            persons=session.get('person_registry', []),
            beneficiaries=session.get('step4_beneficiaries', []),
        )

    # POST -- parse executor and trustee data
    executor_type = request.form.get('executor_type', 'single')
    count = int(request.form.get('executor_count', 1))
    executors = []
    for i in range(count):
        exec_entry_type = request.form.get(f'exec_type_{i}', 'individual').strip()
        role = request.form.get(f'exec_role_{i}', 'Primary')
        if executor_type == 'joint':
            role = 'Joint'
        elif executor_type == 'single':
            role = 'Primary'

        if exec_entry_type == 'corporate':
            corp_name = request.form.get(f'exec_corp_name_{i}', '').strip()
            if corp_name:
                executors.append({
                    'is_corporate': True,
                    'corp_name': corp_name,
                    'corp_reg': request.form.get(f'exec_corp_reg_{i}', '').strip(),
                    'corp_address': request.form.get(f'exec_corp_address_{i}', '').strip(),
                    'full_name': corp_name,
                    'nric_passport': request.form.get(f'exec_corp_reg_{i}', '').strip(),
                    'address': request.form.get(f'exec_corp_address_{i}', '').strip(),
                    'relationship': 'Corporate Trustee',
                    'role': role,
                })
        else:
            person_id = request.form.get(f'exec_person_id_{i}', '').strip()
            person = _get_person_from_registry(person_id)
            if not person:
                continue
            executors.append({
                'person_id': person_id,
                'full_name': person['full_name'],
                'nric_passport': person['nric_passport'],
                'address': person['address'],
                'relationship': request.form.get(f'exec_relationship_{i}', '').strip(),
                'role': role,
                'nationality': person.get('nationality', 'Malaysian'),
            })

    # Substitute executor(s) - supports individual persons or corporate trustees
    sub_exec_count = int(request.form.get('sub_executor_count', 1))
    for i in range(sub_exec_count):
        sub_type = request.form.get(f'sub_exec_type_{i}', 'individual').strip()
        if sub_type == 'corporate':
            corp_name = request.form.get(f'sub_exec_corp_name_{i}', '').strip()
            if corp_name:
                executors.append({
                    'is_corporate': True,
                    'corp_name': corp_name,
                    'corp_reg': request.form.get(f'sub_exec_corp_reg_{i}', '').strip(),
                    'corp_address': request.form.get(f'sub_exec_corp_address_{i}', '').strip(),
                    'full_name': corp_name,  # for display compatibility
                    'nric_passport': request.form.get(f'sub_exec_corp_reg_{i}', '').strip(),
                    'address': request.form.get(f'sub_exec_corp_address_{i}', '').strip(),
                    'relationship': 'Corporate Trustee',
                    'role': 'Substitute',
                })
        else:
            sub_exec_pid = request.form.get(f'sub_exec_person_id_{i}', '').strip()
            if sub_exec_pid:
                sub_person = _get_person_from_registry(sub_exec_pid)
                if sub_person:
                    executors.append({
                        'person_id': sub_exec_pid,
                        'full_name': sub_person['full_name'],
                        'nric_passport': sub_person['nric_passport'],
                        'address': sub_person['address'],
                        'relationship': request.form.get(f'sub_exec_relationship_{i}', '').strip(),
                        'role': 'Substitute',
                        'nationality': sub_person.get('nationality', 'Malaysian'),
                    })

    session['step2_executors'] = executors
    session['step3_executor_type'] = executor_type

    # Parse trustees
    trustee_same = bool(request.form.get('trustee_same_as_executor'))
    trustee_data = {'same_as_executor': trustee_same, 'trustees': [], 'substitute_trustee': {}, 'substitute_trustees': []}

    if not trustee_same:
        trustee_count = int(request.form.get('trustee_count', 1))
        for i in range(trustee_count):
            pid = request.form.get(f'trustee_person_id_{i}', '').strip()
            person = _get_person_from_registry(pid)
            if not person:
                continue
            trustee_data['trustees'].append({
                'person_id': pid,
                'full_name': person['full_name'],
                'nric_passport': person['nric_passport'],
                'address': person['address'],
                'relationship': request.form.get(f'trustee_relationship_{i}', '').strip(),
                'nationality': person.get('nationality', 'Malaysian'),
            })

        # Substitute trustee(s) - now supports multiple joint substitutes
        sub_tr_count = int(request.form.get('sub_trustee_count', 1))
        sub_trustees = []
        for i in range(sub_tr_count):
            sub_tr_pid = request.form.get(f'sub_trustee_person_id_{i}', '').strip()
            if sub_tr_pid:
                sub_tr = _get_person_from_registry(sub_tr_pid)
                if sub_tr:
                    sub_trustees.append({
                        'person_id': sub_tr_pid,
                        'full_name': sub_tr['full_name'],
                        'nric_passport': sub_tr['nric_passport'],
                        'address': sub_tr['address'],
                        'relationship': request.form.get(f'sub_trustee_relationship_{i}', '').strip(),
                        'nationality': sub_tr.get('nationality', 'Malaysian'),
                    })
        trustee_data['substitute_trustees'] = sub_trustees
        # Keep backward compat: set substitute_trustee to first one if any
        if sub_trustees:
            trustee_data['substitute_trustee'] = sub_trustees[0]

    session['step3_trustees'] = trustee_data
    session.modified = True
    mark_step_complete(3)
    save_will_to_db()
    if request.form.get('_save_draft'):
        return jsonify({'ok': True, 'step': 3})
    return redirect(url_for('wizard_step_guardians'))


# -- Step 4: Guardians (select from identities, optional) -------------------

@app.route('/wizard/step/4', methods=['GET', 'POST'])
@login_required
def wizard_step_guardians():
    if request.method == 'GET':
        return render_template(
            'wizard/step4_guardians.html',
            current_step=4,
            completed_steps=get_completed_steps(),
            data={
                'guardians': session.get('step3_guardians', []),
                'guardian_allowance': session.get('step3_guardian_allowance', {}),
                'exclude_spouse_guardian': session.get('step3_exclude_spouse', False),
                'exclude_spouse_guardian_reason': session.get('step3_exclude_spouse_reason', ''),
            },
            persons=session.get('person_registry', []),
        )

    # POST -- parse guardian selections from identities
    count = int(request.form.get('guardian_count', 0))
    guardians = []
    for i in range(count):
        person_id = request.form.get(f'guardian_person_id_{i}', '').strip()
        person = _get_person_from_registry(person_id)
        if not person:
            continue
        guardians.append({
            'person_id': person_id,
            'full_name': person['full_name'],
            'nric_passport': person['nric_passport'],
            'address': person['address'],
            'relationship': request.form.get(f'guardian_relationship_{i}', '').strip(),
            'role': request.form.get(f'guardian_role_{i}', 'Primary'),
            'nationality': person.get('nationality', 'Malaysian'),
        })

    # Guardian allowance
    ga = {}
    payment_mode = request.form.get('allowance_payment_mode', '').strip()
    if payment_mode:
        ga = {
            'payment_mode': payment_mode,
            'other_mode': request.form.get('allowance_other_mode', '').strip() or None,
            'amount': request.form.get('allowance_amount', '').strip() or None,
            'until_age': int(request.form.get('allowance_until_age', 0) or 0) or None,
            'source_of_payment': request.form.get('allowance_source_of_payment', '').strip() or None,
        }

    session['step3_guardians'] = guardians
    session['step3_guardian_allowance'] = ga
    session['step3_exclude_spouse'] = bool(request.form.get('exclude_spouse_guardian'))
    session['step3_exclude_spouse_reason'] = request.form.get('exclude_spouse_guardian_reason', '').strip() or None
    session.modified = True
    mark_step_complete(4)
    save_will_to_db()
    if request.form.get('_save_draft'):
        return jsonify({'ok': True, 'step': 4})
    return redirect(url_for('wizard_step_beneficiaries'))


# -- Step 5: Beneficiaries (select from identities) -------------------------

@app.route('/wizard/step/5', methods=['GET', 'POST'])
@login_required
def wizard_step_beneficiaries():
    if request.method == 'GET':
        return render_template(
            'wizard/step5_beneficiaries.html',
            current_step=5,
            completed_steps=get_completed_steps(),
            data={'beneficiaries': session.get('step4_beneficiaries', [{}])},
            persons=session.get('person_registry', []),
            executor_type=session.get('step3_executor_type', 'single'),
            executors=session.get('step2_executors', []),
        )

    # POST -- parse beneficiary selections from identities
    count = int(request.form.get('beneficiary_count', 1))
    beneficiaries = []
    for i in range(count):
        person_id = request.form.get(f'ben_person_id_{i}', '').strip()
        person = _get_person_from_registry(person_id)
        if not person:
            continue
        beneficiaries.append({
            'person_id': person_id,
            'full_name': person['full_name'],
            'nric_passport_birthcert': person['nric_passport'],
            'relationship': request.form.get(f'ben_relationship_{i}', '').strip(),
            'nationality': person.get('nationality', 'Malaysian'),
        })

    session['step4_beneficiaries'] = beneficiaries
    session.modified = True
    mark_step_complete(5)
    save_will_to_db()
    if request.form.get('_save_draft'):
        return jsonify({'ok': True, 'step': 5})
    return redirect(url_for('wizard_step_gifts'))


# -- Step 6: Gifts (optional) ------------------------------------------------

@app.route('/wizard/step/6', methods=['GET', 'POST'])
@login_required
def wizard_step_gifts():
    if request.method == 'GET':
        return render_template(
            'wizard/step6_gifts.html',
            current_step=6,
            completed_steps=get_completed_steps(),
            data={'gifts': session.get('step5_gifts', [])},
            beneficiaries=session.get('step4_beneficiaries', []),
            persons=session.get('person_registry', []),
        )

    # POST -- parse gifts with nested allocations and structured details
    gift_count = int(request.form.get('gift_count', 0))
    gifts = []
    for gi in range(gift_count):
        gift_type = request.form.get(f'gift_type_{gi}', 'other').strip()
        desc = request.form.get(f'gift_desc_{gi}', '').strip()

        # Parse structured property details
        property_details = {}
        if gift_type == 'property':
            # Ownership type and share
            ownership = request.form.get(f'gift_prop_ownership_{gi}', 'sole').strip()
            share_select = request.form.get(f'gift_prop_share_{gi}', '').strip()
            share_custom = request.form.get(f'gift_prop_share_custom_{gi}', '').strip()
            testator_share = share_custom if share_select == 'other' else share_select

            # Encumbrance
            encumbrance = request.form.get(f'gift_prop_encumbrance_{gi}', 'clean').strip()
            debt_source = request.form.get(f'gift_prop_debt_source_{gi}', 'residuary').strip() if encumbrance == 'encumbered' else ''

            # Split address fields
            prop_addr = request.form.get(f'gift_prop_address_{gi}', '').strip()
            postcode = request.form.get(f'gift_prop_postcode_{gi}', '').strip()
            city = request.form.get(f'gift_prop_city_{gi}', '').strip()
            state = request.form.get(f'gift_prop_state_{gi}', '').strip()
            country = request.form.get(f'gift_prop_country_{gi}', 'Malaysia').strip()

            # Combine address for backward compat
            full_addr_parts = [prop_addr]
            if postcode or city:
                full_addr_parts.append(f"{postcode} {city}".strip())
            if state:
                full_addr_parts.append(state)
            if country and country != 'Malaysia':
                full_addr_parts.append(country)
            full_address = ', '.join(p for p in full_addr_parts if p)

            property_details = {
                'property_address': full_address or prop_addr,
                'title_type': request.form.get(f'gift_prop_title_type_{gi}', '').strip(),
                'title_number': request.form.get(f'gift_prop_title_number_{gi}', '').strip(),
                'lot_number': request.form.get(f'gift_prop_lot_number_{gi}', '').strip(),
                'bandar_pekan': request.form.get(f'gift_prop_bandar_{gi}', '').strip(),
                'daerah': request.form.get(f'gift_prop_daerah_{gi}', '').strip(),
                'negeri': state or request.form.get(f'gift_prop_negeri_{gi}', '').strip(),
                'state': state,
                'postcode': postcode,
                'city': city,
                'country': country,
                'ownership_type': ownership,
                'testator_share': testator_share if ownership == 'joint' else '',
                'encumbrance_status': encumbrance,
                'debt_source': debt_source,
            }
            if not property_details['property_address']:
                continue

        # Parse structured financial details
        financial_details = {}
        if gift_type == 'financial':
            account_ownership = request.form.get(f'gift_fin_ownership_{gi}', 'individual').strip()
            financial_details = {
                'institution': request.form.get(f'gift_fin_institution_{gi}', '').strip(),
                'account_number': request.form.get(f'gift_fin_account_{gi}', '').strip(),
                'asset_type': request.form.get(f'gift_fin_type_{gi}', '').strip(),
                'description': request.form.get(f'gift_fin_desc_{gi}', '').strip(),
                'account_ownership': account_ownership,
            }
            if not financial_details['institution'] and not financial_details['description']:
                continue

        # For "other" type, skip if no description
        if gift_type == 'other' and not desc:
            continue

        subject_to_trust = bool(request.form.get(f'gift_trust_{gi}'))
        subject_to_guardian_allowance = bool(request.form.get(f'gift_guardian_allowance_{gi}'))
        sell_property = bool(request.form.get(f'gift_sell_property_{gi}'))
        substitute_mode = request.form.get(f'gift_{gi}_sub_mode', 'equal')

        alloc_count = int(request.form.get(f'gift_{gi}_alloc_count', 0))
        allocations = []
        for ai_idx in range(alloc_count):
            ben_name = request.form.get(f'gift_{gi}_alloc_name_{ai_idx}', '').strip()
            if not ben_name:
                continue
            # Parse per-MB substitutes (only used when substitute_mode == 'specific')
            subs = []
            if substitute_mode == 'specific':
                sub_count = int(request.form.get(f'gift_{gi}_mb_{ai_idx}_sub_count', 0))
                for si in range(sub_count):
                    sub_name = request.form.get(f'gift_{gi}_mb_{ai_idx}_sub_name_{si}', '').strip()
                    sub_share = request.form.get(f'gift_{gi}_mb_{ai_idx}_sub_share_{si}', '').strip()
                    if sub_name:
                        subs.append({'beneficiary_name': sub_name, 'share': sub_share or '100%'})
            allocations.append({
                'beneficiary_name': ben_name,
                'share': request.form.get(f'gift_{gi}_alloc_share_{ai_idx}', '').strip(),
                'role': request.form.get(f'gift_{gi}_alloc_role_{ai_idx}', 'MB'),
                'substitutes': subs,
            })

        # Parse uploaded document references
        gift_docs_json = request.form.get(f'gift_docs_{gi}', '[]')
        try:
            gift_docs = json.loads(gift_docs_json) if gift_docs_json else []
        except (json.JSONDecodeError, TypeError):
            gift_docs = []

        gifts.append({
            'gift_type': gift_type,
            'description': desc,
            'property_details': property_details,
            'financial_details': financial_details,
            'allocations': allocations,
            'subject_to_trust': subject_to_trust,
            'subject_to_guardian_allowance': subject_to_guardian_allowance,
            'sell_property': sell_property,
            'substitute_mode': substitute_mode,
            'documents': gift_docs,
        })

    # Reorder gifts if user changed order via drag-and-drop or sort
    gift_order_str = request.form.get('gift_order', '')
    if gift_order_str:
        order = [int(x) for x in gift_order_str.split(',') if x.strip().isdigit()]
        gift_map = {i: g for i, g in enumerate(gifts)}
        reordered = [gift_map[i] for i in order if i in gift_map]
        # Include any gifts not in order list (safety)
        for i, g in enumerate(gifts):
            if i not in order:
                reordered.append(g)
        gifts = reordered

    session['step5_gifts'] = gifts
    session.modified = True
    mark_step_complete(6)
    save_will_to_db()
    if request.form.get('_save_draft'):
        return jsonify({'ok': True, 'step': 6})
    return redirect(url_for('wizard_step_residuary'))


# -- Step 7: Residuary Estate ------------------------------------------------

@app.route('/wizard/step/7', methods=['GET', 'POST'])
@login_required
def wizard_step_residuary():
    if request.method == 'GET':
        return render_template(
            'wizard/step7_residuary.html',
            current_step=7,
            completed_steps=get_completed_steps(),
            data=session.get('step6_residuary', {}),
            beneficiaries=session.get('step4_beneficiaries', []),
            persons=session.get('person_registry', []),
            gifts=session.get('step5_gifts', []),
        )

    # POST -- parse main beneficiaries and substitute groups
    main_count = int(request.form.get('main_beneficiary_count', 0))
    main_beneficiaries = []
    for i in range(main_count):
        # Support both person_id (dropdown) and name (text fallback)
        person_id = request.form.get(f'main_ben_person_id_{i}', '').strip()
        name = request.form.get(f'main_ben_name_{i}', '').strip()
        if person_id:
            person = _get_person_from_registry(person_id)
            if person:
                name = person['full_name']
        if not name:
            continue
        entry = {
            'beneficiary_name': name,
            'share': request.form.get(f'main_ben_share_{i}', '').strip(),
            'group': 'main',
        }
        if person_id:
            entry['person_id'] = person_id
        main_beneficiaries.append(entry)

    # Substitute groups
    sub_group_count = int(request.form.get('substitute_group_count', 0))
    substitute_groups = []
    for gi in range(sub_group_count):
        sub_count = int(request.form.get(f'sub_group_{gi}_count', 0))
        group = []
        for si in range(sub_count):
            person_id = request.form.get(f'sub_group_{gi}_person_id_{si}', '').strip()
            name = request.form.get(f'sub_group_{gi}_name_{si}', '').strip()
            if person_id:
                person = _get_person_from_registry(person_id)
                if person:
                    name = person['full_name']
            if not name:
                continue
            entry = {
                'beneficiary_name': name,
                'share': request.form.get(f'sub_group_{gi}_share_{si}', '').strip(),
                'group': f'substitute_{gi + 1}',
            }
            if person_id:
                entry['person_id'] = person_id
            group.append(entry)
        if group:
            substitute_groups.append(group)

    additional_notes = request.form.get('additional_notes', '').strip() or None

    session['step6_residuary'] = {
        'main_beneficiaries': main_beneficiaries,
        'substitute_groups': substitute_groups,
        'additional_notes': additional_notes,
    }
    session.modified = True
    mark_step_complete(7)
    save_will_to_db()
    if request.form.get('_save_draft'):
        return jsonify({'ok': True, 'step': 7})
    return redirect(url_for('wizard_step_trust'))


# -- Step 8: Testamentary Trust (optional) ------------------------------------

@app.route('/wizard/step/8', methods=['GET', 'POST'])
@login_required
def wizard_step_trust():
    if request.method == 'GET':
        return render_template(
            'wizard/step8_trust.html',
            current_step=8,
            completed_steps=get_completed_steps(),
            data=session.get('step7_trust', {}),
            beneficiaries=session.get('step4_beneficiaries', []),
            gifts=session.get('step5_gifts', []),
            persons=session.get('person_registry', []),
        )

    # POST -- parse trust data
    ben_count = int(request.form.get('trust_beneficiary_count', 0))
    trust_bens = []
    for i in range(ben_count):
        name = request.form.get(f'trust_ben_name_{i}', '').strip()
        if not name:
            continue
        trust_bens.append({
            'beneficiary_name': name,
            'share': request.form.get(f'trust_ben_share_{i}', '').strip(),
            'role': request.form.get(f'trust_ben_role_{i}', 'MB'),
        })

    purposes = request.form.getlist('purposes')
    other_purpose = request.form.get('purposes_other_text', '').strip() or None

    trust_data = {}
    if trust_bens:
        trust_data = {
            'beneficiaries': trust_bens,
            'purposes': purposes,
            'other_purpose': other_purpose,
            'duration': request.form.get('trust_duration', '').strip() or None,
            'assets_from_gifts': request.form.getlist('gift_references'),
            'property_actions': {},
            'property_residents': {},
            'payment_mode': request.form.get('payment_mode', '').strip() or None,
            'payment_amount': request.form.get('payment_amount', '').strip() or None,
            'other_payment_mode': request.form.get('payment_mode_other', '').strip() or None,
            'balance_of_trust': request.form.get('balance_of_trust', '').strip() or None,
            'separate_trustee': bool(request.form.get('separate_trustee')),
            'trustee_person_id': request.form.get('trustee_person_id', '').strip() or None,
            'trustee_relationship': request.form.get('trustee_relationship', '').strip() or None,
        }
        # Parse per-property actions (reside/lease/sell) and resident selections
        gift_refs = trust_data['assets_from_gifts']
        for ref in gift_refs:
            # Extract gift number from "Gift 1", "Gift 2", etc.
            try:
                gift_num = int(ref.replace('Gift ', ''))
            except (ValueError, AttributeError):
                continue
            action = request.form.get(f'prop_action_{gift_num}', '').strip()
            if action:
                trust_data['property_actions'][ref] = action
            resident = request.form.get(f'prop_resident_{gift_num}', '').strip()
            if resident:
                trust_data['property_residents'][ref] = resident

        # Look up trustee identity
        trustee_pid = trust_data.get('trustee_person_id')
        if trustee_pid:
            trustee_person = _get_person_from_registry(trustee_pid)
            if trustee_person:
                trust_data['trustee_name'] = trustee_person['full_name']
                trust_data['trustee_nric'] = trustee_person['nric_passport']
                trust_data['trustee_address'] = trustee_person['address']

    session['step7_trust'] = trust_data
    session.modified = True
    mark_step_complete(8)
    save_will_to_db()
    if request.form.get('_save_draft'):
        return jsonify({'ok': True, 'step': 8})
    return redirect(url_for('wizard_step_others'))


# -- Step 9: Other Matters (optional) ----------------------------------------

@app.route('/wizard/step/9', methods=['GET', 'POST'])
@login_required
def wizard_step_others():
    if request.method == 'GET':
        return render_template(
            'wizard/step9_others.html',
            current_step=9,
            completed_steps=get_completed_steps(),
            data=session.get('step8_others', {}),
        )

    # POST -- parse other matters
    om_data = {}

    terms = request.form.get('terms_of_endearment', '').strip()
    if terms:
        om_data['terms_of_endearment'] = terms

    # Commorientes
    om_data['commorientes_enabled'] = bool(request.form.get('commorientes_enabled'))
    if om_data['commorientes_enabled']:
        om_data['commorientes_days'] = int(request.form.get('commorientes_days', 0) or 0) or None

    # Exclusion
    om_data['exclusion_enabled'] = bool(request.form.get('exclusion_enabled'))
    if om_data['exclusion_enabled']:
        om_data['exclusion_name'] = request.form.get('exclusion_name', '').strip() or None
        om_data['exclusion_nric'] = request.form.get('exclusion_nric', '').strip() or None
        om_data['exclusion_relationship'] = request.form.get('exclusion_relationship', '').strip() or None
        om_data['exclusion_reason'] = request.form.get('exclusion_reason', '').strip() or None

    # Unnamed children
    om_data['unnamed_children_enabled'] = bool(request.form.get('unnamed_children_enabled'))
    if om_data['unnamed_children_enabled']:
        om_data['unnamed_children_spouse_name'] = request.form.get('unnamed_children_spouse_name', '').strip() or None
        om_data['unnamed_children_spouse_nric'] = request.form.get('unnamed_children_spouse_nric', '').strip() or None

    # Joint bank account clause
    om_data['joint_account_clause_enabled'] = bool(request.form.get('joint_account_clause_enabled'))

    additional = request.form.get('additional_instructions', '').strip()
    if additional:
        om_data['additional_instructions'] = additional

    session['step8_others'] = om_data
    session.modified = True
    mark_step_complete(9)
    save_will_to_db()
    if request.form.get('_save_draft'):
        return jsonify({'ok': True, 'step': 9})
    return redirect(url_for('wizard_step_review'))


# -- Step 10: Review ---------------------------------------------------------

@app.route('/wizard/step/10', methods=['GET'])
@login_required
def wizard_step_review():
    # Build the will data model from session
    try:
        will_data = build_will_data()
    except Exception as e:
        flash(f'Error building will data: {e}', 'error')
        return redirect(url_for('wizard_step_identities'))

    # Run validation
    from validation.legal_rules import validate_will_data, get_errors, get_warnings
    validation_results = validate_will_data(will_data)
    errors = get_errors(validation_results)
    warnings = get_warnings(validation_results)
    infos = [r for r in validation_results if r.severity == 'INFO']

    # Build summary data dict for template
    summary = {
        'identities': session.get('person_registry', []),
        'testator': session.get('step1', {}),
        'executors': session.get('step2_executors', []),
        'executor_type': session.get('step3_executor_type', 'single'),
        'trustee_data': session.get('step3_trustees', {'same_as_executor': True}),
        'guardians': session.get('step3_guardians', []),
        'guardian_allowance': session.get('step3_guardian_allowance', {}),
        'beneficiaries': session.get('step4_beneficiaries', []),
        'gifts': session.get('step5_gifts', []),
        'residuary': session.get('step6_residuary', {}),
        'trust': session.get('step7_trust', {}),
        'others': session.get('step8_others', {}),
        'other_matters': session.get('step8_others', {}),
    }

    # Check if a firm logo exists for this tenant
    has_logo = _get_logo_path() is not None

    # Get current include_logo setting from will record (default True)
    include_logo = True
    will_id = session.get('will_id')
    if will_id:
        wr = db.session.get(Will, will_id)
        if wr and wr.include_logo is not None:
            include_logo = wr.include_logo

    return render_template(
        'wizard/step10_review.html',
        current_step=10,
        completed_steps=get_completed_steps(),
        summary=summary,
        will_data=summary,
        validation_results=validation_results,
        validation_errors=errors,
        validation_warnings=warnings,
        validation_infos=infos,
        has_errors=len(errors) > 0,
        has_logo=has_logo,
        include_logo=include_logo,
    )


# -- Generate Will -----------------------------------------------------------

@app.route('/wizard/generate', methods=['POST'])
@login_required
def wizard_generate():
    try:
        will_data = build_will_data()
    except Exception as e:
        flash(f'Error building will data: {e}', 'error')
        return redirect(url_for('wizard_step_review'))

    # Run validation -- block on errors
    from validation.legal_rules import validate_will_data, get_errors
    validation_results = validate_will_data(will_data)
    errors = get_errors(validation_results)
    if errors:
        for err in errors:
            flash(f'Validation Error: {err.message}', 'error')
        return redirect(url_for('wizard_step_review'))

    # Draft will using AI (or mock)
    try:
        if ANTHROPIC_API_KEY and ANTHROPIC_API_KEY != 'your-api-key-here':
            from ai.drafter import draft_will
            will_text = draft_will(will_data)
        else:
            from ai.drafter import draft_will_mock
            will_text = draft_will_mock(will_data)
    except Exception as e:
        flash(f'Error generating will: {e}', 'error')
        traceback.print_exc()
        return redirect(url_for('wizard_step_review'))

    # Store generated will text in DB (not session — session cookie too large)
    session['generated_will_text'] = will_text  # temporary for save_will_to_db
    session.modified = True
    mark_step_complete(10)
    save_will_to_db()

    # Save include_logo preference
    include_logo = '1' in request.form.getlist('include_logo')
    will_id = session.get('will_id')
    if will_id:
        wr = db.session.get(Will, will_id)
        if wr:
            wr.include_logo = include_logo
            db.session.commit()

    # Save version history
    will_id = session.get('will_id')
    if will_id:
        # Determine version number
        latest_version = WillVersion.query.filter_by(will_id=will_id).order_by(
            WillVersion.version_number.desc()
        ).first()
        next_version = (latest_version.version_number + 1) if latest_version else 1
        user_name = ''
        if session.get('user_id'):
            u = db.session.get(User, session['user_id'])
            user_name = u.name if u else ''
        note = 'Initial generation' if next_version == 1 else f'Re-generated (version {next_version})'
        version = WillVersion(
            will_id=will_id,
            version_number=next_version,
            will_text=will_text,
            generated_by=session.get('user_id'),
            generated_by_name=user_name,
            note=note,
        )
        db.session.add(version)
        db.session.commit()

    # If approver generated the will, auto-approve it (no submission step needed)
    will_id = session.get('will_id')
    if will_id:
        user = db.session.get(User, session.get('user_id'))
        if user and ROLE_PERMS.get(user.role, {}).get('canApprove'):
            wr = db.session.get(Will, will_id)
            if wr:
                wr.status = 'approved'
                wr.approved_by = user.id
                wr.approved_at = datetime.utcnow()
                wr.approval_remarks = 'Auto-approved (generated by approver)'
                db.session.commit()

    # Remove from session to keep cookie small
    session.pop('generated_will_text', None)
    session.modified = True
    flash('Will generated successfully! You can now view, edit, or download it.', 'info')
    return redirect(url_for('preview'))


# -- Preview -----------------------------------------------------------------

@app.route('/preview')
@login_required
def preview():
    # Read will text from DB (not session) to avoid oversized cookies
    will_text = ''
    will_record = None
    versions = []
    viewing_version = None  # which version is being displayed
    if session.get('will_id'):
        will_record = db.session.get(Will, session['will_id'])
        if will_record:
            # Load version history
            versions = WillVersion.query.filter_by(will_id=will_record.id).order_by(
                WillVersion.version_number.desc()
            ).all()

            # Check if a specific version is requested
            ver_num = request.args.get('version', type=int)
            if ver_num and versions:
                for v in versions:
                    if v.version_number == ver_num:
                        will_text = v.will_text
                        viewing_version = v
                        break

            # Default: show current (latest) will text
            if not will_text:
                will_text = will_record.generated_will_text or ''
    if not will_text:
        # Fallback to session for backward compat
        will_text = session.get('generated_will_text', '')
    if not will_text:
        # If will was previously generated but text was lost, direct to Step 10 to re-generate
        if will_record and will_record.status in ('generated', 'pending_approval', 'approved'):
            flash('The will text needs to be re-generated. Please click "Generate My Will" below.', 'warning')
        else:
            flash('No will has been generated yet. Please complete the wizard first.', 'warning')
        return redirect(url_for('wizard_step_review'))

    testator_name = session.get('step1', {}).get('full_name', 'Unknown')

    # Look up last editor name and edit logs
    editor_name = None
    edit_logs = []
    client_email = None
    if will_record:
        if will_record.text_edited_by:
            editor = db.session.get(User, will_record.text_edited_by)
            if editor:
                editor_name = editor.name
        edit_logs = WillEditLog.query.filter_by(will_id=will_record.id).order_by(WillEditLog.edited_at.desc()).all()
        # Get client email for the send-email button
        if will_record.client_id:
            client = db.session.get(Client, will_record.client_id)
            if client:
                client_email = client.email

    return render_template(
        'preview.html',
        will_text=will_text,
        testator_name=testator_name,
        will_record=will_record,
        editor_name=editor_name,
        edit_logs=edit_logs,
        client_email=client_email,
        versions=versions,
        viewing_version=viewing_version,
        has_logo=_get_logo_path() is not None,
        include_logo=will_record.include_logo if will_record and will_record.include_logo is not None else True,
    )


# -- Download -----------------------------------------------------------------

@app.route('/download/<fmt>')
@login_required
def download(fmt):
    # Read will text from DB (not session) to avoid oversized cookies
    will_text = ''
    if session.get('will_id'):
        will_record = db.session.get(Will, session['will_id'])
        if will_record:
            will_text = will_record.generated_will_text or ''
    if not will_text:
        will_text = session.get('generated_will_text', '')
    if not will_text:
        flash('No will has been generated yet.', 'warning')
        return redirect(url_for('preview'))

    # Check download permissions based on role and approval status
    user_role = session.get('user_role', '')
    will_id = session.get('will_id')
    if user_role in ('admin', 'advisor') and will_id:
        will_record = db.session.get(Will, will_id)
        if will_record and will_record.status != 'approved':
            flash('This will must be approved before it can be downloaded.', 'warning')
            return redirect(url_for('preview'))

    testator_name = session.get('step1', {}).get('full_name', 'Will')
    safe_name = "".join(c for c in testator_name if c.isalnum() or c in (' ', '-', '_')).strip()
    safe_name = safe_name.replace(' ', '_') or 'Will'

    if fmt == 'docx':
        from documents.docx_generator import generate_docx
        filepath = generate_docx(will_text, safe_name)
    elif fmt == 'pdf':
        from documents.pdf_generator import generate_pdf
        # Check will's include_logo flag
        logo = None
        if will_id:
            wr = db.session.get(Will, will_id)
            if wr and wr.include_logo:
                logo = _get_logo_path()
        else:
            logo = _get_logo_path()
        filepath = generate_pdf(will_text, safe_name, logo_path=logo)
    else:
        flash('Unsupported download format.', 'error')
        return redirect(url_for('preview'))

    # Save persistent copy to client folder
    try:
        client_id = session.get('client_id')
        if client_id:
            client = db.session.get(Client, client_id)
            if client:
                from uploads import save_generated_will
                with open(filepath, 'rb') as f:
                    file_bytes = f.read()
                will_record = db.session.get(Will, session.get('will_id'))
                is_draft = will_record.status == 'draft' if will_record else True
                saved_name, rel_path = save_generated_will(
                    client.folder_name, file_bytes, fmt, is_draft=is_draft
                )
                # Update will status
                if will_record:
                    will_record.status = 'generated'
                    db.session.commit()
    except Exception as e:
        app.logger.warning(f'Could not save persistent copy: {e}')

    mime = {
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'pdf': 'application/pdf',
    }.get(fmt, 'application/octet-stream')

    return send_file(
        filepath,
        as_attachment=True,
        download_name=f'{safe_name}_Will.{fmt}',
        mimetype=mime,
    )


# -- Reset Session ------------------------------------------------------------

@app.route('/reset')
@login_required
def reset():
    # Preserve auth keys during session reset
    user_id = session.get('user_id')
    user_role = session.get('user_role')
    user_name = session.get('user_name')
    session.clear()
    if user_id:
        session['user_id'] = user_id
        session['user_role'] = user_role
        session['user_name'] = user_name
    flash('Your session has been reset. You can start a new will.', 'info')
    return redirect(url_for('index'))


@app.route('/wizard/new')
@login_required
def wizard_new():
    """Start a brand-new will by clearing the current session."""
    # Preserve auth keys during session reset
    user_id = session.get('user_id')
    user_role = session.get('user_role')
    user_name = session.get('user_name')
    session.clear()
    if user_id:
        session['user_id'] = user_id
        session['user_role'] = user_role
        session['user_name'] = user_name
    return redirect(url_for('wizard_step_identities'))


# ---------------------------------------------------------------------------
# Probate Application Module
# ---------------------------------------------------------------------------

MALAYSIAN_COURTS = [
    'JOHOR BAHRU', 'KUALA LUMPUR', 'SHAH ALAM', 'PUTRAJAYA', 'GEORGE TOWN',
    'IPOH', 'KUANTAN', 'KOTA BHARU', 'KUALA TERENGGANU', 'MELAKA',
    'SEREMBAN', 'ALOR SETAR', 'KANGAR', 'KOTA KINABALU', 'KUCHING',
    'MUAR', 'BATU PAHAT', 'SEGAMAT', 'KLANG', 'TAIPING',
]

MALAYSIAN_STATES = [
    'JOHOR DARUL TAKZIM', 'SELANGOR DARUL EHSAN', 'WILAYAH PERSEKUTUAN KUALA LUMPUR',
    'WILAYAH PERSEKUTUAN PUTRAJAYA', 'PULAU PINANG', 'PERAK DARUL RIDZUAN',
    'PAHANG DARUL MAKMUR', 'KELANTAN DARUL NAIM', 'TERENGGANU DARUL IMAN',
    'MELAKA', 'NEGERI SEMBILAN DARUL KHUSUS', 'KEDAH DARUL AMAN', 'PERLIS INDERA KAYANGAN',
    'SABAH', 'SARAWAK',
]


def _validate_probate_data(probate, will_record, recommendations):
    """Check for missing required data per form and return warnings."""
    warnings = {}  # form_code -> list of missing field descriptions
    rec_codes = {r['form_code'] for r in recommendations if r.get('recommended')}

    for code in rec_codes:
        missing = []
        # Common: death details
        if code in ('doc01', 'doc02', 'doc03'):
            if not probate.date_of_death:
                missing.append('Date of Death (Step 1)')
            if not probate.place_of_death:
                missing.append('Place of Death (Step 1)')
        if code == 'doc02':
            if not probate.death_cert_number:
                missing.append('Death Certificate Number (Step 1)')
        # Court/firm
        if code in ('doc01', 'doc08'):
            if not probate.court_location:
                missing.append('Court Location (Step 2)')
            if not probate.firm_name:
                missing.append('Firm Name (Step 2)')
        # Witnesses
        if code == 'doc04':
            if not probate.witness1_name:
                missing.append('Witness 1 Name (Step 3)')
            if not probate.witness1_nric:
                missing.append('Witness 1 NRIC (Step 3)')
            if not probate.witness1_address:
                missing.append('Witness 1 Address (Step 3)')
        if code == 'doc05':
            if not probate.witness2_name:
                missing.append('Witness 2 Name (Step 3)')
            if not probate.witness2_nric:
                missing.append('Witness 2 NRIC (Step 3)')
            if not probate.witness2_address:
                missing.append('Witness 2 Address (Step 3)')
        # Beneficiaries
        if code == 'doc07':
            bens = json.loads(probate.beneficiaries_data or '[]') if probate.beneficiaries_data else []
            if not bens:
                missing.append('No beneficiaries entered (Step 4)')
        # Assets
        if code == 'doc06':
            assets = json.loads(probate.assets_data or '[]')
            if not assets:
                missing.append('No assets entered (Step 5)')
        if missing:
            warnings[code] = missing
    return warnings


def _get_probate_context(probate_id):
    """Load probate app + will data for template context."""
    probate = db.session.get(ProbateApplication, probate_id)
    if not probate:
        return None, None, {}

    is_la = probate.application_type == 'la'
    will_record = db.session.get(Will, probate.will_id) if probate.will_id else None

    if is_la:
        # LA: deceased/applicant from probate fields
        testator = {
            'full_name': probate.deceased_name or '',
            'nric_passport': probate.deceased_nric or '',
            'residential_address': probate.deceased_address or '',
        }
        executor = {
            'full_name': probate.applicant_name or '',
            'nric_passport': probate.applicant_nric or '',
            'address': probate.applicant_address or '',
            'relationship': probate.applicant_relationship or '',
        }
        will_title = f'LA — {probate.deceased_name or "Unnamed"}'
        client_name = probate.applicant_name or ''
    else:
        # Probate: from will data
        if not will_record:
            return probate, None, {'probate': probate, 'probate_id': probate_id,
                                   'is_la': False, 'will_title': 'Unknown', 'client_name': '',
                                   'testator': {}, 'executor': None}
        testator = json.loads(will_record.step1_data or '{}')
        step2 = json.loads(will_record.step2_data or '{}')
        executors = step2.get('executors', []) if isinstance(step2, dict) else step2
        executor = executors[0] if executors else {}
        will_title = will_record.title
        client_name = will_record.client.full_name if will_record.client else ''

    # Determine max completed step based on data presence
    max_step = 0
    if probate.date_of_death or probate.place_of_death or (is_la and probate.deceased_name):
        max_step = 1
    if probate.court_location or probate.firm_name:
        max_step = max(max_step, 2)
    if probate.witness1_name or probate.witness2_name:
        max_step = max(max_step, 3)
    if probate.beneficiaries_data and probate.beneficiaries_data != '[]':
        max_step = max(max_step, 4)
    if probate.assets_data and probate.assets_data != '[]':
        max_step = max(max_step, 5)
    if probate.status == 'generated':
        max_step = 6

    return probate, will_record, {
        'probate': probate,
        'probate_id': probate_id,
        'is_la': is_la,
        'will_title': will_title,
        'client_name': client_name,
        'testator': testator,
        'executor': type('Obj', (), executor) if executor else None,
        'max_completed_step': max_step,
    }


@app.route('/probate')
@login_required
def probate_list():
    role = session.get('user_role')
    if role not in ('admin', 'approver'):
        flash('Access denied.', 'error')
        return redirect(url_for('index'))
    q = request.args.get('q', '').strip()
    if q:
        applications = ProbateApplication.query.filter(
            db.or_(
                ProbateApplication.deceased_name.ilike(f'%{q}%'),
                ProbateApplication.deceased_nric.ilike(f'%{q}%'),
                ProbateApplication.applicant_name.ilike(f'%{q}%'),
                ProbateApplication.case_number.ilike(f'%{q}%'),
            )
        ).order_by(ProbateApplication.created_at.desc()).all()
    else:
        applications = ProbateApplication.query.order_by(ProbateApplication.created_at.desc()).all()
    approved_wills = Will.query.filter_by(status='approved').order_by(Will.approved_at.desc()).all()
    return render_template('probate/list.html', applications=applications, approved_wills=approved_wills, search_query=q)


@app.route('/probate/<probate_id>/delete', methods=['POST'])
@login_required
def probate_delete(probate_id):
    """Delete a probate application and its generated forms."""
    role = session.get('user_role')
    if role not in ('admin', 'approver'):
        flash('Access denied.', 'error')
        return redirect(url_for('probate_list'))
    probate = db.session.get(ProbateApplication, probate_id)
    if not probate:
        flash('Application not found.', 'error')
        return redirect(url_for('probate_list'))
    # Delete generated form files from disk
    gen_forms = ProbateGeneratedForm.query.filter_by(probate_id=probate_id).all()
    for gf in gen_forms:
        if gf.file_path and os.path.exists(gf.file_path):
            try:
                os.remove(gf.file_path)
            except OSError:
                pass
    ProbateGeneratedForm.query.filter_by(probate_id=probate_id).delete()
    deceased = probate.deceased_name or 'Unknown'
    db.session.delete(probate)
    db.session.commit()
    flash(f'Probate application for "{deceased}" has been deleted.', 'success')
    return redirect(url_for('probate_list'))


@app.route('/probate/new-la')
@login_required
def probate_new_la():
    """Create a new Letters of Administration application (no will)."""
    role = session.get('user_role')
    if role not in ('admin', 'approver'):
        flash('Access denied.', 'error')
        return redirect(url_for('index'))
    probate = ProbateApplication(
        application_type='la',
        filing_year=str(datetime.now().year),
        created_by=session.get('user_id'),
        firm_name='Tetuan Alan Tan & Associates',
        firm_address='24-01 & 24-02, Jalan Kempas Utama 2/4, Taman Kempas Utama, 81300 Johor Bahru, Johor',
        firm_phone='07-588 5979',
    )
    db.session.add(probate)
    db.session.commit()
    return redirect(f'/probate/{probate.id}/step/1')


@app.route('/probate/new/<will_id>')
@login_required
def probate_new(will_id):
    role = session.get('user_role')
    if role not in ('admin', 'approver'):
        flash('Access denied.', 'error')
        return redirect(url_for('index'))
    will_record = db.session.get(Will, will_id)
    if not will_record:
        flash('Will not found.', 'error')
        return redirect(url_for('wills_list'))
    # Check if probate already exists for this will
    existing = ProbateApplication.query.filter_by(will_id=will_id).first()
    if existing:
        return redirect(f'/probate/{existing.id}/step/1')
    # Create new probate application
    probate = ProbateApplication(
        will_id=will_id,
        client_id=will_record.client_id,
        filing_year=str(datetime.now().year),
        created_by=session.get('user_id'),
        firm_name='Tetuan Alan Tan & Associates',
        firm_address='24-01 & 24-02, Jalan Kempas Utama 2/4, Taman Kempas Utama, 81300 Johor Bahru, Johor',
        firm_phone='07-588 5979',
    )
    db.session.add(probate)
    db.session.commit()
    return redirect(f'/probate/{probate.id}/step/1')


@app.route('/probate/<probate_id>/step/1', methods=['GET', 'POST'])
@login_required
def probate_step1(probate_id):
    probate, will_record, ctx = _get_probate_context(probate_id)
    if not probate:
        flash('Probate application not found.', 'error')
        return redirect(url_for('probate_list'))

    if request.method == 'POST':
        probate.death_cert_number = request.form.get('death_cert_number', '').strip()
        probate.date_of_death = request.form.get('date_of_death', '').strip()
        probate.time_of_death = request.form.get('time_of_death', '').strip()
        probate.place_of_death = request.form.get('place_of_death', '').strip()
        probate.estate_value_estimate = request.form.get('estate_value_estimate', '').strip()
        doc_id = request.form.get('death_cert_document_id', '').strip()
        if doc_id:
            probate.death_cert_document_id = doc_id
        # LA-specific fields
        if probate.application_type == 'la':
            probate.deceased_name = request.form.get('deceased_name', '').strip()
            probate.deceased_nric = request.form.get('deceased_nric', '').strip()
            probate.deceased_address = request.form.get('deceased_address', '').strip()
            probate.applicant_name = request.form.get('applicant_name', '').strip()
            probate.applicant_nric = request.form.get('applicant_nric', '').strip()
            probate.applicant_address = request.form.get('applicant_address', '').strip()
            probate.applicant_relationship = request.form.get('applicant_relationship', '').strip()
        # Will document upload (for LA with external will)
        will_doc_id = request.form.get('will_document_id', '').strip()
        if will_doc_id:
            probate.will_document_id = will_doc_id
        # Store extracted will data for auto-populating later steps
        will_extracted = request.form.get('will_extracted_data', '').strip()
        if will_extracted:
            try:
                ext = json.loads(will_extracted)
                # Pre-populate witnesses from extracted will data
                witnesses = ext.get('witnesses', [])
                if witnesses and not probate.witness1_name:
                    if len(witnesses) >= 1:
                        probate.witness1_name = witnesses[0].get('full_name', '')
                        probate.witness1_nric = witnesses[0].get('nric_number', '')
                    if len(witnesses) >= 2:
                        probate.witness2_name = witnesses[1].get('full_name', '')
                        probate.witness2_nric = witnesses[1].get('nric_number', '')
                # Pre-populate beneficiaries from extracted will data
                bens = ext.get('beneficiaries', [])
                existing_bens = json.loads(probate.beneficiaries_data or '[]')
                if bens and not existing_bens:
                    ben_list = []
                    for b in bens:
                        ben_list.append({
                            'full_name': b.get('full_name', ''),
                            'nric_passport': b.get('nric_number', ''),
                            'relationship': b.get('relationship', ''),
                            'address': '',
                        })
                    probate.beneficiaries_data = json.dumps(ben_list)
                # Pre-populate assets from extracted will data
                assets = ext.get('assets', [])
                existing_assets = json.loads(probate.assets_data or '[]')
                if assets and not existing_assets:
                    asset_list = []
                    for a in assets:
                        atype = a.get('type', 'other')
                        asset_list.append({
                            'asset_type': atype if atype in ('property', 'bank', 'vehicle') else 'other',
                            'description': a.get('description', ''),
                        })
                    probate.assets_data = json.dumps(asset_list)
            except (json.JSONDecodeError, Exception):
                pass  # Silently skip if parsing fails
        probate.updated_at = datetime.utcnow()
        db.session.commit()
        return redirect(f'/probate/{probate_id}/step/2')

    ctx['probate_step'] = 1
    return render_template('probate/step1_death.html', **ctx)


@app.route('/probate/<probate_id>/step/2', methods=['GET', 'POST'])
@login_required
def probate_step2(probate_id):
    probate, will_record, ctx = _get_probate_context(probate_id)
    if not probate:
        flash('Probate application not found.', 'error')
        return redirect(url_for('probate_list'))

    if request.method == 'POST':
        probate.court_location = request.form.get('court_location', '').strip()
        probate.court_state = request.form.get('court_state', '').strip()
        probate.case_number = request.form.get('case_number', '').strip()
        probate.filing_year = request.form.get('filing_year', '').strip()
        probate.firm_name = request.form.get('firm_name', '').strip()
        probate.firm_address = request.form.get('firm_address', '').strip()
        probate.firm_phone = request.form.get('firm_phone', '').strip()
        probate.firm_fax = request.form.get('firm_fax', '').strip()
        probate.firm_reference = request.form.get('firm_reference', '').strip()
        probate.lawyer_name = request.form.get('lawyer_name', '').strip()
        probate.lawyer_nric = request.form.get('lawyer_nric', '').strip()
        probate.lawyer_bar_number = request.form.get('lawyer_bar_number', '').strip()
        probate.updated_at = datetime.utcnow()
        db.session.commit()
        return redirect(f'/probate/{probate_id}/step/3')

    ctx['probate_step'] = 2
    ctx['courts'] = MALAYSIAN_COURTS
    ctx['states'] = MALAYSIAN_STATES
    ctx['current_year'] = str(datetime.now().year)
    return render_template('probate/step2_court.html', **ctx)


@app.route('/probate/<probate_id>/step/3', methods=['GET', 'POST'])
@login_required
def probate_step3(probate_id):
    probate, will_record, ctx = _get_probate_context(probate_id)
    if not probate:
        flash('Probate application not found.', 'error')
        return redirect(url_for('probate_list'))

    if request.method == 'POST':
        probate.witness1_name = request.form.get('witness1_name', '').strip()
        probate.witness1_nric = request.form.get('witness1_nric', '').strip()
        probate.witness1_address = request.form.get('witness1_address', '').strip()
        probate.witness2_name = request.form.get('witness2_name', '').strip()
        probate.witness2_nric = request.form.get('witness2_nric', '').strip()
        probate.witness2_address = request.form.get('witness2_address', '').strip()
        probate.updated_at = datetime.utcnow()
        db.session.commit()
        return redirect(f'/probate/{probate_id}/step/4')

    ctx['probate_step'] = 3
    return render_template('probate/step3_witnesses.html', **ctx)


@app.route('/probate/<probate_id>/step/4', methods=['GET', 'POST'])
@login_required
def probate_step4(probate_id):
    """Step 4: Beneficiaries list."""
    probate, will_record, ctx = _get_probate_context(probate_id)
    if not probate:
        flash('Probate application not found.', 'error')
        return redirect(url_for('probate_list'))

    if request.method == 'POST':
        bens_json = request.form.get('beneficiaries_json', '[]')
        try:
            bens = json.loads(bens_json)
        except json.JSONDecodeError:
            bens = []
        probate.beneficiaries_data = json.dumps(bens)
        probate.updated_at = datetime.utcnow()
        db.session.commit()
        if request.headers.get('X-Save-Only'):
            return jsonify(ok=True)
        return redirect(f'/probate/{probate_id}/step/5')

    # Pre-populate from will data if beneficiaries_data is empty
    existing_bens = json.loads(probate.beneficiaries_data or '[]')
    if not existing_bens and will_record:
        will_bens = json.loads(will_record.step4_data or '[]')
        for b in will_bens:
            existing_bens.append({
                'full_name': b.get('full_name', b.get('beneficiary_name', '')),
                'nric_passport': b.get('nric_passport_birthcert', b.get('nric_passport', '')),
                'relationship': b.get('relationship', ''),
                'address': '',
            })

    ctx['probate_step'] = 4
    ctx['beneficiaries_json'] = json.dumps(existing_bens)
    return render_template('probate/step4_beneficiaries.html', **ctx)


@app.route('/probate/<probate_id>/step/5', methods=['GET', 'POST'])
@login_required
def probate_step5(probate_id):
    """Step 5: Assets & Liabilities schedule."""
    probate, will_record, ctx = _get_probate_context(probate_id)
    if not probate:
        flash('Probate application not found.', 'error')
        return redirect(url_for('probate_list'))

    if request.method == 'POST':
        assets_json = request.form.get('assets_json', '[]')
        try:
            assets = json.loads(assets_json)
        except json.JSONDecodeError:
            assets = []
        probate.assets_data = json.dumps(assets)
        probate.updated_at = datetime.utcnow()
        db.session.commit()
        if request.headers.get('X-Save-Only'):
            return jsonify(ok=True)
        return redirect(f'/probate/{probate_id}/step/6')

    # Pre-populate from will gifts if assets_data is empty and will exists
    existing_assets = json.loads(probate.assets_data or '[]')
    if not existing_assets and will_record:
        gifts = json.loads(will_record.step5_data or '[]')
        for g in gifts:
            if g.get('gift_type') == 'property':
                details = g.get('property_details', {})
                existing_assets.append({
                    'asset_type': 'property',
                    'description': details.get('address', g.get('description', '')),
                    'title_number': details.get('title_number', ''),
                    'lot_number': details.get('lot_number', ''),
                    'mukim': details.get('mukim', ''),
                    'value': '',
                })
            elif g.get('gift_type') == 'financial':
                fin = g.get('financial_details', {})
                existing_assets.append({
                    'asset_type': 'bank',
                    'bank_name': fin.get('institution', ''),
                    'account_number': fin.get('account_number', ''),
                    'value': '',
                })

    # Build exhibit prefix
    exec_data = ctx.get('executor')
    exec_name = exec_data.full_name if exec_data and hasattr(exec_data, 'full_name') else ''
    exhibit_prefix = ''.join(w[0] for w in exec_name.split() if w) if exec_name else 'APP'

    ctx['probate_step'] = 5
    ctx['assets_json'] = json.dumps(existing_assets)
    ctx['exhibit_prefix'] = exhibit_prefix
    return render_template('probate/step5_assets.html', **ctx)


@app.route('/probate/<probate_id>/step/6', methods=['GET'])
@login_required
def probate_step6(probate_id):
    probate, will_record, ctx = _get_probate_context(probate_id)
    if not probate:
        flash('Probate application not found.', 'error')
        return redirect(url_for('probate_list'))

    from documents.probate_generator import recommend_forms, FORM_FIELDS
    recommendations = recommend_forms(will_record, probate)

    # Build actual values lookup for form field display
    exec_data = ctx.get('executor')
    testator = ctx.get('testator') or {}
    field_values = {
        'Deceased name & NRIC': f"{testator.get('full_name', '')} ({testator.get('nric_passport', '')})" if testator.get('full_name') else '',
        'Deceased name': testator.get('full_name', ''),
        'Deceased address': testator.get('residential_address', ''),
        'Date of death': probate.date_of_death or '',
        'Time of death': probate.time_of_death or '',
        'Place of death': probate.place_of_death or '',
        'Death certificate number': probate.death_cert_number or '',
        'Applicant (Executor) name & NRIC': f"{exec_data.full_name} ({exec_data.nric_passport})" if exec_data and exec_data.full_name else '',
        'Applicant name & NRIC': f"{exec_data.full_name} ({exec_data.nric_passport})" if exec_data and exec_data.full_name else '',
        'Applicant address': (exec_data.address if exec_data and hasattr(exec_data, 'address') else '') or '',
        'Applicant relationship': (exec_data.relationship if exec_data and hasattr(exec_data, 'relationship') else '') or '',
        'Court location': probate.court_location or '',
        'Court location & case number': f"{probate.court_location or ''} — {probate.case_number or ''}",
        'Court case number': probate.case_number or '',
        'Case number': probate.case_number or '',
        'Firm name & address': f"{probate.firm_name or ''}, {probate.firm_address or ''}" if probate.firm_name else '',
        'Firm phone & fax': f"Tel: {probate.firm_phone or ''}, Fax: {probate.firm_fax or ''}",
        'Firm reference': probate.firm_reference or '',
        'Witness 1 name & NRIC': f"{probate.witness1_name or ''} ({probate.witness1_nric or ''})" if probate.witness1_name else '',
        'Witness 1 address': probate.witness1_address or '',
        'Witness 2 name & NRIC': f"{probate.witness2_name or ''} ({probate.witness2_nric or ''})" if probate.witness2_name else '',
        'Witness 2 address': probate.witness2_address or '',
        'Estate value': probate.estate_value_estimate or '',
        'Exhibit references': 'Auto-generated',
    }
    # Assets summary values
    _assets = json.loads(probate.assets_data or '[]')
    _props = [a for a in _assets if a.get('asset_type') == 'property']
    _banks = [a for a in _assets if a.get('asset_type') == 'bank']
    _vehicles = [a for a in _assets if a.get('asset_type') == 'vehicle']
    _others = [a for a in _assets if a.get('asset_type') == 'other']
    _liabs = [a for a in _assets if a.get('asset_type') == 'liability']
    field_values['Properties (title, lot, mukim, address)'] = f'{len(_props)} properties' if _props else ''
    field_values['Bank accounts (bank, account no., value)'] = f'{len(_banks)} accounts' if _banks else ''
    field_values['Vehicles (desc, reg no., engine, chassis)'] = f'{len(_vehicles)} vehicles' if _vehicles else ''
    field_values['Other assets (description, value)'] = f'{len(_others)} items' if _others else ''
    field_values['Liabilities (description, value)'] = f'{len(_liabs)} items' if _liabs else ''
    # Prefer probate.beneficiaries_data (populated in step 4), fall back to will data
    _bens = json.loads(probate.beneficiaries_data or '[]') if probate.beneficiaries_data and probate.beneficiaries_data != '[]' else (json.loads(will_record.step4_data or '[]') if will_record else [])
    field_values['Beneficiary names & NRIC'] = ', '.join(b.get('full_name', b.get('beneficiary_name', '')) for b in _bens[:5]) if _bens else ''
    field_values['Beneficiary relationships'] = ', '.join(b.get('relationship', '') for b in _bens[:5]) if _bens else ''
    if _props:
        p0 = _props[0]
        field_values['Property title number'] = p0.get('title_number', '')
        field_values['Property lot number'] = p0.get('lot_number', '')
        field_values['Property mukim'] = p0.get('mukim', '')

    # Merge with template info and field mapping
    templates = ProbateFormTemplate.query.order_by(ProbateFormTemplate.sort_order).all()
    tpl_map = {t.form_code: t for t in templates}
    for rec in recommendations:
        tpl = tpl_map.get(rec['form_code'])
        if tpl:
            rec['form_name'] = tpl.form_name
            rec['form_name_malay'] = tpl.form_name_malay
            rec['description'] = tpl.description
        ff = FORM_FIELDS.get(rec['form_code'])
        if ff:
            rec['fields'] = [(name, source, field_values.get(name, '')) for name, source in ff['fields']]
        else:
            rec['fields'] = []

    # Check for previously generated forms
    generated_forms = ProbateGeneratedForm.query.filter_by(probate_id=probate_id).all()
    gen_list = []
    import re as _re
    for gf in generated_forms:
        tpl = tpl_map.get(gf.form_code)
        # Scan for unfilled placeholders in generated file
        missing_fields = []
        if gf.file_path and os.path.exists(gf.file_path):
            try:
                from docx import Document as _DocxDoc
                _doc = _DocxDoc(gf.file_path)
                _seen = set()
                for _p in _doc.paragraphs:
                    for _m in _re.findall(r'\{\{(\w+)\}\}', _p.text):
                        if _m not in _seen:
                            _seen.add(_m)
                            missing_fields.append(_m.replace('_', ' ').title())
                for _t in _doc.tables:
                    for _r in _t.rows:
                        for _c in _r.cells:
                            for _m in _re.findall(r'\{\{(\w+)\}\}', _c.text):
                                if _m not in _seen:
                                    _seen.add(_m)
                                    missing_fields.append(_m.replace('_', ' ').title())
            except Exception:
                pass
        gen_list.append({
            'form_code': gf.form_code,
            'form_name': tpl.form_name if tpl else gf.form_code,
            'file_path': gf.file_path,
            'missing_fields': missing_fields,
        })

    # Build exhibit prefix from applicant initials
    exec_data = ctx.get('executor')
    exec_name = exec_data.full_name if exec_data and hasattr(exec_data, 'full_name') else ''
    exhibit_prefix = ''.join(w[0] for w in exec_name.split() if w) if exec_name else 'APP'

    # Validation: check for missing required info per form
    validation_warnings = _validate_probate_data(probate, will_record, recommendations)

    # Load invoices & receipts for this probate
    receipt_docs = Document.query.filter(
        Document.description.like(f'probate:{probate_id}|%'),
        Document.category.in_(['probate_invoice', 'probate_receipt'])
    ).order_by(Document.created_at.desc()).all()
    receipts = [{
        'id': d.id,
        'filename': d.original_filename,
        'category': d.category.replace('probate_', ''),
        'description': (d.description or '').split('|', 1)[-1],
        'file_size': d.file_size,
        'created_at': d.created_at.strftime('%d %b %Y, %I:%M %p') if d.created_at else '',
    } for d in receipt_docs]

    # Build filing checklist — check actual data completeness for form generation
    form_data = json.loads(probate.form_data_json or '{}')
    manual_checks = form_data.get('filing_checklist', {})
    assets_list = json.loads(probate.assets_data or '[]')
    has_property = any(a.get('asset_type') == 'property' for a in assets_list)
    property_assets = [a for a in assets_list if a.get('asset_type') == 'property']
    has_property_docs = all(a.get('_doc_id') for a in property_assets) if property_assets else False
    exec_nric = (exec_data.nric_passport if exec_data and hasattr(exec_data, 'nric_passport') else '') or ''
    testator = ctx.get('testator')

    # Death cert info complete? (cert number optional — nice to have)
    death_info_ok = bool(probate.date_of_death and probate.place_of_death)
    death_missing = []
    if not probate.date_of_death:
        death_missing.append('Date of death')
    if not probate.place_of_death:
        death_missing.append('Place of death')
    death_warnings = []
    if not probate.death_cert_number:
        death_warnings.append('Death cert number (optional)')

    # Assets info complete?
    assets_ok = len(assets_list) > 0
    assets_missing = [] if assets_ok else ['No assets entered']

    # Will info complete?
    will_ok = bool(will_record)
    will_missing = [] if will_ok else ['No approved will linked']

    # Beneficiary info? (prefer probate.beneficiaries_data from step 4)
    beneficiaries = json.loads(probate.beneficiaries_data or '[]') if probate.beneficiaries_data and probate.beneficiaries_data != '[]' else (json.loads(will_record.step4_data or '[]') if will_record else [])
    ben_ok = len(beneficiaries) > 0
    ben_missing = [] if ben_ok else ['No beneficiaries entered']

    # Executor info?
    exec_ok = bool(exec_nric and exec_data and exec_data.full_name)
    exec_missing = []
    if not exec_data or not exec_data.full_name:
        exec_missing.append('Executor name')
    if not exec_nric:
        exec_missing.append('Executor NRIC')

    # Property title info? Manual key-in (title_number) is sufficient
    prop_missing = []
    if has_property:
        for i, p in enumerate(property_assets):
            if not p.get('title_number'):
                prop_missing.append(f'Property {i+1}: Title number missing')
            if not p.get('description'):
                prop_missing.append(f'Property {i+1}: Description missing')
    prop_ok = has_property and not prop_missing

    # Build detail data for each checklist item
    _testator = ctx.get('testator') or {}
    death_details = [
        ('Deceased Name', _testator.get('full_name', '')),
        ('NRIC', _testator.get('nric_passport', '')),
        ('Date of Death', probate.date_of_death or ''),
        ('Time of Death', probate.time_of_death or ''),
        ('Place of Death', probate.place_of_death or ''),
        ('Death Cert No.', probate.death_cert_number or ''),
        ('Estate Value', probate.estate_value_estimate or ''),
    ]

    assets_details = []
    for a in assets_list:
        atype = a.get('asset_type', '')
        if atype == 'property':
            assets_details.append(('Property', f"{a.get('description', '')} — Title: {a.get('title_number', 'N/A')}"))
        elif atype == 'bank':
            assets_details.append(('Bank', f"{a.get('bank_name', '')} — Acc: {a.get('account_number', '')} — RM {a.get('value', '')}"))
        elif atype == 'vehicle':
            assets_details.append(('Vehicle', f"{a.get('description', '')} — {a.get('reg_number', '')}"))
        elif atype == 'other':
            assets_details.append(('Other', f"{a.get('description', '')} — RM {a.get('value', '')}"))
        elif atype == 'liability':
            assets_details.append(('Liability', f"{a.get('description', '')} — RM {a.get('value', '')}"))
    if not assets_details:
        assets_details.append(('', 'No assets entered'))

    will_details = []
    if will_record:
        will_details.append(('Will Title', will_record.title or ''))
        will_details.append(('Status', will_record.status or ''))
        will_details.append(('Approved', will_record.approved_at.strftime('%d %b %Y') if will_record.approved_at else 'N/A'))
    else:
        will_details.append(('', 'No approved will linked'))

    ben_details = []
    for b in beneficiaries:
        bname = b.get('full_name', b.get('beneficiary_name', b.get('name', '')))
        bnric = b.get('nric_passport_birthcert', b.get('nric_passport', ''))
        brel = b.get('relationship', '')
        detail = f"{brel} — NRIC: {bnric}" if bnric else brel
        ben_details.append((bname, detail))
    if not ben_details:
        ben_details.append(('', 'No beneficiaries in will'))

    exec_details = [
        ('Name', exec_data.full_name if exec_data and hasattr(exec_data, 'full_name') else ''),
        ('NRIC', exec_nric),
        ('Relationship', exec_data.relationship if exec_data and hasattr(exec_data, 'relationship') else ''),
        ('Address', exec_data.address if exec_data and hasattr(exec_data, 'address') else ''),
    ]

    prop_details = []
    for p in property_assets:
        prop_details.append(('Description', p.get('description', '')))
        prop_details.append(('Title No.', p.get('title_number', '')))
        prop_details.append(('Lot No.', p.get('lot_number', '')))
        prop_details.append(('Mukim', p.get('mukim', '')))
    if not prop_details:
        prop_details.append(('', 'No property in estate'))

    filing_checklist = [
        {'key': 'death_cert', 'label': '<strong>Death Certificate</strong> — date, place, cert number',
         'exhibit': f'{exhibit_prefix}-1',
         'complete': death_info_ok,
         'missing': death_missing,
         'warnings': death_warnings,
         'details': death_details,
         'checked': manual_checks.get('death_cert', death_info_ok)},
        {'key': 'assets_schedule', 'label': '<strong>Schedule of Assets &amp; Liabilities</strong>',
         'exhibit': f'{exhibit_prefix}-2',
         'complete': assets_ok,
         'missing': assets_missing,
         'details': assets_details,
         'checked': manual_checks.get('assets_schedule', assets_ok)},
        {'key': 'original_will', 'label': '<strong>Original Will</strong> (certified true copy)',
         'exhibit': f'{exhibit_prefix}-3',
         'complete': will_ok,
         'missing': will_missing,
         'details': will_details,
         'checked': manual_checks.get('original_will', will_ok)},
        {'key': 'beneficiary_list', 'label': '<strong>Beneficiary List</strong>',
         'exhibit': f'{exhibit_prefix}-4',
         'complete': ben_ok,
         'missing': ben_missing,
         'details': ben_details,
         'checked': manual_checks.get('beneficiary_list', ben_ok)},
        {'key': 'executor_nric', 'label': 'Executor NRIC &amp; details',
         'exhibit': None,
         'complete': exec_ok,
         'missing': exec_missing,
         'details': exec_details,
         'checked': manual_checks.get('executor_nric', exec_ok)},
        {'key': 'property_titles', 'label': '<strong>Property title documents</strong> (Hakmilik)',
         'exhibit': None, 'conditional': True, 'condition_met': has_property,
         'complete': prop_ok,
         'missing': prop_missing,
         'details': prop_details,
         'checked': manual_checks.get('property_titles', prop_ok)},
    ]

    ctx['probate_step'] = 6
    ctx['recommendations'] = recommendations
    ctx['generated_forms'] = gen_list
    ctx['exhibit_prefix'] = exhibit_prefix
    ctx['validation_warnings'] = validation_warnings
    ctx['receipts'] = receipts
    ctx['filing_checklist'] = filing_checklist
    ctx['has_property'] = has_property
    ctx['beneficiaries'] = beneficiaries
    return render_template('probate/step6_review.html', **ctx)


@app.route('/probate/<probate_id>/generate', methods=['POST'])
@login_required
def probate_generate(probate_id):
    probate, will_record, ctx = _get_probate_context(probate_id)
    if not probate:
        flash('Probate application not found.', 'error')
        return redirect(url_for('probate_list'))

    selected_codes = request.form.getlist('forms')
    if not selected_codes:
        flash('Please select at least one form to generate.', 'error')
        return redirect(f'/probate/{probate_id}/step/6')

    # Build template paths map
    templates = ProbateFormTemplate.query.all()
    tpl_map = {t.form_code: os.path.join(os.path.dirname(__file__), t.file_path) for t in templates}
    tpl_name_map = {t.form_code: t.form_name for t in templates}

    # Output directory
    if probate.client_id:
        client = db.session.get(Client, probate.client_id)
        folder = client.folder_name if client else 'unknown'
    else:
        # LA without client — use probate ID as folder
        folder = f'la_{probate.id[:8]}'
    output_dir = os.path.join(DATA_DIR, 'clients', folder, 'probate')
    os.makedirs(output_dir, exist_ok=True)

    from documents.probate_generator import generate_probate_forms
    results = generate_probate_forms(probate, will_record, selected_codes, tpl_map, output_dir)

    # Delete old generated forms for this probate
    ProbateGeneratedForm.query.filter_by(probate_id=probate_id).delete()

    # Save generated form records
    for r in results:
        gf = ProbateGeneratedForm(
            probate_id=probate_id,
            form_code=r['form_code'],
            form_name=tpl_name_map.get(r['form_code'], r['form_code']),
            file_path=r['file_path'],
        )
        db.session.add(gf)

    probate.status = 'generated'
    probate.selected_forms = json.dumps(selected_codes)
    probate.updated_at = datetime.utcnow()
    db.session.commit()

    flash(f'Successfully generated {len(results)} probate form(s).', 'success')
    return redirect(f'/probate/{probate_id}/step/6')


@app.route('/probate/<probate_id>/preview/<form_code>')
@login_required
def probate_preview(probate_id, form_code):
    """Serve generated form as inline PDF for browser preview."""
    gf = ProbateGeneratedForm.query.filter_by(probate_id=probate_id, form_code=form_code).first()
    if not gf or not os.path.exists(gf.file_path):
        flash('Form not found.', 'error')
        return redirect(f'/probate/{probate_id}/step/6')
    from documents.probate_generator import convert_to_pdf
    import shutil
    tmp_dir = tempfile.mkdtemp()
    tmp_copy = os.path.join(tmp_dir, os.path.basename(gf.file_path))
    shutil.copy2(gf.file_path, tmp_copy)
    pdf_path = convert_to_pdf(tmp_copy)
    if pdf_path and os.path.exists(pdf_path):
        resp = send_file(pdf_path, mimetype='application/pdf',
                         download_name=f'{gf.form_name or form_code}.pdf')
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    flash('PDF conversion failed.', 'error')
    return redirect(f'/probate/{probate_id}/step/6')


@app.route('/probate/<probate_id>/form-content/<form_code>')
@login_required
def probate_form_content(probate_id, form_code):
    """Return DOCX content as structured paragraphs + tables for inline editing."""
    gf = ProbateGeneratedForm.query.filter_by(probate_id=probate_id, form_code=form_code).first()
    if not gf or not os.path.exists(gf.file_path):
        return jsonify(ok=False, error='Form not found'), 404
    from docx import Document as DocxDocument
    doc = DocxDocument(gf.file_path)
    content = []
    for i, p in enumerate(doc.paragraphs):
        content.append({
            'type': 'paragraph',
            'index': i,
            'text': p.text,
            'bold': any(r.bold for r in p.runs if r.bold),
            'alignment': str(p.alignment) if p.alignment else None,
        })
    for ti, table in enumerate(doc.tables):
        rows = []
        for ri, row in enumerate(table.rows):
            cells = []
            for ci, cell in enumerate(row.cells):
                cells.append(cell.text)
            rows.append(cells)
        content.append({'type': 'table', 'table_index': ti, 'rows': rows})
    return jsonify(ok=True, content=content)


@app.route('/probate/<probate_id>/form-content/<form_code>', methods=['POST'])
@login_required
def probate_form_content_save(probate_id, form_code):
    """Save edited paragraph text back to the DOCX file."""
    gf = ProbateGeneratedForm.query.filter_by(probate_id=probate_id, form_code=form_code).first()
    if not gf or not os.path.exists(gf.file_path):
        return jsonify(ok=False, error='Form not found'), 404
    data = request.get_json()
    if not data or 'edits' not in data:
        return jsonify(ok=False, error='No edits provided'), 400
    from docx import Document as DocxDocument
    doc = DocxDocument(gf.file_path)
    edits = data['edits']  # list of {index, text} for paragraphs, or {table_index, row, col, text} for cells
    for edit in edits:
        if edit.get('type') == 'table':
            ti, ri, ci = edit['table_index'], edit['row'], edit['col']
            if ti < len(doc.tables) and ri < len(doc.tables[ti].rows) and ci < len(doc.tables[ti].rows[ri].cells):
                cell = doc.tables[ti].rows[ri].cells[ci]
                # Preserve formatting: update first paragraph text, clear rest
                if cell.paragraphs:
                    for run in cell.paragraphs[0].runs:
                        run.text = ''
                    if cell.paragraphs[0].runs:
                        cell.paragraphs[0].runs[0].text = edit['text']
                    else:
                        cell.paragraphs[0].text = edit['text']
        else:
            idx = edit.get('index', -1)
            if 0 <= idx < len(doc.paragraphs):
                p = doc.paragraphs[idx]
                new_text = edit['text']
                # Preserve formatting: distribute text across existing runs
                if p.runs:
                    # Put all text in first run, clear others
                    p.runs[0].text = new_text
                    for run in p.runs[1:]:
                        run.text = ''
                else:
                    p.text = new_text
    doc.save(gf.file_path)
    gf.generated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(ok=True)


@app.route('/probate/<probate_id>/download/<form_code>')
@login_required
def probate_download(probate_id, form_code):
    fmt = request.args.get('format', 'docx')  # docx or pdf
    gf = ProbateGeneratedForm.query.filter_by(probate_id=probate_id, form_code=form_code).first()
    if not gf or not os.path.exists(gf.file_path):
        flash('Form not found.', 'error')
        return redirect(f'/probate/{probate_id}/step/6')

    # Use proper form name for download filename
    safe_name = (gf.form_name or form_code).replace(' ', '_').replace('/', '_')

    if fmt == 'pdf':
        from documents.probate_generator import convert_to_pdf
        pdf_path = convert_to_pdf(gf.file_path)
        if pdf_path and os.path.exists(pdf_path):
            return send_file(pdf_path, as_attachment=True, download_name=f'{safe_name}.pdf')
        flash('PDF conversion failed. Downloading .docx instead.', 'error')

    return send_file(gf.file_path, as_attachment=True, download_name=f'{safe_name}.docx')


@app.route('/probate/<probate_id>/reupload/<form_code>', methods=['POST'])
@login_required
def probate_reupload(probate_id, form_code):
    """Replace a generated form with an edited DOCX upload."""
    gf = ProbateGeneratedForm.query.filter_by(probate_id=probate_id, form_code=form_code).first()
    if not gf:
        return jsonify(ok=False, error='Form not found'), 404
    if 'file' not in request.files:
        return jsonify(ok=False, error='No file uploaded'), 400
    file = request.files['file']
    if not file.filename.endswith('.docx'):
        return jsonify(ok=False, error='Only .docx files are accepted'), 400
    # Overwrite the existing generated file
    file.save(gf.file_path)
    gf.generated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(ok=True)


@app.route('/probate/<probate_id>/download-all')
@login_required
def probate_download_all(probate_id):
    fmt = request.args.get('format', 'docx')  # docx or pdf
    forms = ProbateGeneratedForm.query.filter_by(probate_id=probate_id).all()
    if not forms:
        flash('No generated forms found.', 'error')
        return redirect(f'/probate/{probate_id}/step/6')

    from documents.probate_generator import create_zip
    zip_path = os.path.join(tempfile.gettempdir(), f'probate_{probate_id[:8]}.zip')
    form_files = [{'form_code': f.form_code, 'file_path': f.file_path, 'form_name': f.form_name} for f in forms]
    create_zip(form_files, zip_path, as_pdf=(fmt == 'pdf'))
    return send_file(zip_path, as_attachment=True, download_name=f'probate_forms_{probate_id[:8]}.zip')


@app.route('/api/ocr/death-cert', methods=['POST'])
@login_required
def api_ocr_death_cert():
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
    fmt_err = _validate_ocr_file(file)
    if fmt_err:
        return jsonify({'ok': False, 'error': fmt_err}), 400

    from uploads import save_uploaded_file
    client_id = session.get('client_id', 'temp')
    try:
        saved_name, rel_path, file_size = save_uploaded_file(file, client_id, category='death_certificate')
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

    abs_path = os.path.join(UPLOAD_DIR, rel_path)

    from ai.ocr import extract_death_cert_data
    try:
        extracted = extract_death_cert_data(abs_path)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'OCR failed: {str(e)}'}), 500

    if 'error' in extracted:
        return jsonify({'ok': False, 'error': extracted['error'], 'extracted': extracted})

    # Save document record
    doc = Document(
        client_id=client_id,
        filename=saved_name,
        original_filename=file.filename,
        file_path=rel_path,
        file_type=file.filename.rsplit('.', 1)[-1].lower(),
        file_size=file_size,
        category='death_certificate',
        extracted_data=json.dumps(extracted),
    )
    db.session.add(doc)
    db.session.commit()

    return jsonify({'ok': True, 'document_id': doc.id, 'extracted': extracted})


@app.route('/api/ocr/will-document', methods=['POST'])
@login_required
def api_ocr_will_document():
    """Upload a will document (PDF/image) and OCR extract testator, executors, witnesses, beneficiaries, assets."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
    fmt_err = _validate_ocr_file(file)
    if fmt_err:
        return jsonify({'ok': False, 'error': fmt_err}), 400

    from uploads import save_uploaded_file
    client_id = session.get('client_id', 'temp')
    try:
        saved_name, rel_path, file_size = save_uploaded_file(file, client_id, category='will_document')
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

    abs_path = os.path.join(UPLOAD_DIR, rel_path)

    from ai.ocr import extract_will_data
    try:
        extracted = extract_will_data(abs_path)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'OCR failed: {str(e)}'}), 500

    if 'error' in extracted:
        return jsonify({'ok': False, 'error': extracted['error'], 'extracted': extracted})

    # Save document record
    doc = Document(
        client_id=client_id,
        filename=saved_name,
        original_filename=file.filename,
        file_path=rel_path,
        file_type=file.filename.rsplit('.', 1)[-1].lower(),
        file_size=file_size,
        category='will_document',
        extracted_data=json.dumps(extracted),
    )
    db.session.add(doc)
    db.session.commit()

    return jsonify({'ok': True, 'document_id': doc.id, 'extracted': extracted})


@app.route('/api/ocr/asset-doc', methods=['POST'])
@login_required
def api_ocr_asset_doc():
    """Upload an asset document (title, bank statement, vehicle card, etc.) and OCR it."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
    fmt_err = _validate_ocr_file(file)
    if fmt_err:
        return jsonify({'ok': False, 'error': fmt_err}), 400

    asset_type = request.form.get('asset_type', 'other')

    from uploads import save_uploaded_file
    client_id = session.get('client_id', 'temp')
    try:
        saved_name, rel_path, file_size = save_uploaded_file(file, client_id, category=f'asset_{asset_type}')
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

    abs_path = os.path.join(UPLOAD_DIR, rel_path)

    from ai.ocr import extract_asset_document
    try:
        extracted = extract_asset_document(abs_path, asset_type)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'OCR failed: {str(e)}'}), 500

    if 'error' in extracted:
        return jsonify({'ok': False, 'error': extracted['error']})

    doc = Document(
        client_id=client_id,
        filename=saved_name,
        original_filename=file.filename,
        file_path=rel_path,
        file_type=file.filename.rsplit('.', 1)[-1].lower(),
        file_size=file_size,
        category=f'asset_{asset_type}',
        extracted_data=json.dumps(extracted),
    )
    db.session.add(doc)
    db.session.commit()

    return jsonify({'ok': True, 'document_id': doc.id, 'extracted': extracted})


@app.route('/api/probate/<probate_id>/checklist', methods=['POST'])
@login_required
def api_probate_checklist(probate_id):
    """Save filing checklist state."""
    probate = db.session.get(ProbateApplication, probate_id)
    if not probate:
        return jsonify(ok=False, error='Not found'), 404
    data = request.get_json(silent=True) or {}
    form_data = json.loads(probate.form_data_json or '{}')
    form_data['filing_checklist'] = data.get('checklist', {})
    probate.form_data_json = json.dumps(form_data)
    probate.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(ok=True)


@app.route('/api/probate/<probate_id>/upload-receipt', methods=['POST'])
@login_required
def api_probate_upload_receipt(probate_id):
    """Upload an invoice or payment receipt for a probate application."""
    probate = db.session.get(ProbateApplication, probate_id)
    if not probate:
        return jsonify({'ok': False, 'error': 'Probate application not found'}), 404

    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'ok': False, 'error': 'No file selected'}), 400

    doc_category = request.form.get('category', 'invoice')  # invoice or receipt
    description = request.form.get('description', '')

    from uploads import save_uploaded_file
    client_id = probate.client_id or 'temp'
    try:
        saved_name, rel_path, file_size = save_uploaded_file(file, client_id, category=f'probate_{doc_category}')
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

    doc = Document(
        client_id=client_id,
        filename=saved_name,
        original_filename=file.filename,
        file_path=rel_path,
        file_type=file.filename.rsplit('.', 1)[-1].lower(),
        file_size=file_size,
        category=f'probate_{doc_category}',
        description=f'probate:{probate_id}|{description}',
    )
    db.session.add(doc)
    db.session.commit()

    return jsonify({
        'ok': True,
        'document': {
            'id': doc.id,
            'filename': doc.original_filename,
            'category': doc_category,
            'description': description,
            'file_size': doc.file_size,
            'created_at': doc.created_at.isoformat(),
        }
    })


@app.route('/api/probate/<probate_id>/receipts')
@login_required
def api_probate_receipts(probate_id):
    """List invoices and payment receipts for a probate application."""
    docs = Document.query.filter(
        Document.description.like(f'probate:{probate_id}|%'),
        Document.category.in_(['probate_invoice', 'probate_receipt'])
    ).order_by(Document.created_at.desc()).all()

    return jsonify([{
        'id': d.id,
        'filename': d.original_filename,
        'category': d.category.replace('probate_', ''),
        'description': (d.description or '').split('|', 1)[-1],
        'file_size': d.file_size,
        'created_at': d.created_at.isoformat(),
    } for d in docs])


# Admin: Probate Template Management
@app.route('/admin/probate-templates')
@role_required('admin')
def admin_probate_templates():
    templates = ProbateFormTemplate.query.order_by(ProbateFormTemplate.sort_order).all()
    flash_msg = request.args.get('msg')
    return render_template('admin/probate_templates.html', templates=templates, flash_msg=flash_msg)


@app.route('/admin/probate-templates/<form_code>/upload', methods=['POST'])
@role_required('admin')
def admin_probate_template_upload(form_code):
    tpl = ProbateFormTemplate.query.filter_by(form_code=form_code).first()
    if not tpl:
        flash('Template not found.', 'error')
        return redirect(url_for('admin_probate_templates'))

    file = request.files.get('template')
    if not file or not file.filename:
        flash('No file selected.', 'error')
        return redirect(url_for('admin_probate_templates'))

    # Save custom template
    custom_dir = os.path.join(os.path.dirname(__file__), 'probate_templates', 'custom')
    os.makedirs(custom_dir, exist_ok=True)
    ext = file.filename.rsplit('.', 1)[-1].lower()
    custom_path = os.path.join(custom_dir, f'{form_code}.{ext}')
    file.save(custom_path)

    tpl.file_path = f'probate_templates/custom/{form_code}.{ext}'
    tpl.is_default = False
    tpl.updated_at = datetime.utcnow()
    db.session.commit()

    return redirect(url_for('admin_probate_templates', msg=f'Template for {tpl.form_name} updated.'))


@app.route('/admin/probate-templates/<form_code>/reset', methods=['POST'])
@role_required('admin')
def admin_probate_template_reset(form_code):
    tpl = ProbateFormTemplate.query.filter_by(form_code=form_code).first()
    if not tpl:
        flash('Template not found.', 'error')
        return redirect(url_for('admin_probate_templates'))

    # Map form_code back to default file
    default_files = {
        'doc01': 'doc01_saman_pemula.docx', 'doc02': 'doc02_afidavit_probet.docx',
        'doc03': 'doc03_sumpah_pentadbiran.docx', 'doc04': 'doc04_afidavit_saksi_1.docx',
        'doc05': 'doc05_afidavit_saksi_2.docx', 'doc06': 'doc06_jadual_aset.docx',
        'doc07': 'doc07_senarai_benefisiari.docx', 'doc08': 'doc08_notis_peguamcara.docx',
        'form14a': 'form14a_land_transfer.docx', 'form346': 'form346_personal_rep.docx',
    }
    default_file = default_files.get(form_code, f'{form_code}.docx')
    tpl.file_path = f'probate_templates/{default_file}'
    tpl.is_default = True
    tpl.updated_at = datetime.utcnow()
    db.session.commit()

    return redirect(url_for('admin_probate_templates', msg=f'Template for {tpl.form_name} reset to default.'))


@app.route('/probate/template/<form_code>/view')
@login_required
def probate_template_view(form_code):
    """Convert template to PDF and serve inline for browser viewing."""
    tpl = ProbateFormTemplate.query.filter_by(form_code=form_code).first()
    if not tpl:
        flash('Template not found.', 'error')
        return redirect(url_for('probate_list'))
    template_path = os.path.join(os.path.dirname(__file__), tpl.file_path)
    if not os.path.exists(template_path):
        flash('Template file not found on disk.', 'error')
        return redirect(url_for('probate_list'))
    # Detect actual file extension for correct download names
    actual_ext = os.path.splitext(template_path)[1].lstrip('.') or 'docx'
    fmt = request.args.get('format', 'pdf')
    if fmt in ('docx', 'doc'):
        return send_file(template_path, as_attachment=True,
                         download_name=f'{form_code}_template.{actual_ext}')
    # Convert to PDF for in-browser viewing
    from documents.probate_generator import convert_to_pdf
    import shutil
    tmp_dir = tempfile.mkdtemp()
    tmp_copy = os.path.join(tmp_dir, os.path.basename(template_path))
    shutil.copy2(template_path, tmp_copy)
    pdf_path = convert_to_pdf(tmp_copy)
    if pdf_path and os.path.exists(pdf_path):
        resp = send_file(pdf_path, mimetype='application/pdf',
                         download_name=f'{form_code}_template.pdf')
        # Prevent browser caching stale PDFs
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    # Fallback: download in original format
    return send_file(template_path, as_attachment=True,
                     download_name=f'{form_code}_template.{actual_ext}')


@app.route('/probate/template/<form_code>/translate', methods=['POST'])
@login_required
def probate_template_translate(form_code):
    """Translate a probate form template from Malay to English using AI."""
    tpl = ProbateFormTemplate.query.filter_by(form_code=form_code).first()
    if not tpl:
        return jsonify(ok=False, error='Template not found'), 404
    template_path = os.path.join(os.path.dirname(__file__), tpl.file_path)
    if not os.path.exists(template_path):
        return jsonify(ok=False, error='File not found'), 404
    # Convert to PDF first, then translate using vision
    from documents.probate_generator import convert_to_pdf
    import shutil
    tmp_dir = tempfile.mkdtemp()
    tmp_docx = os.path.join(tmp_dir, os.path.basename(template_path))
    shutil.copy2(template_path, tmp_docx)
    pdf_path = convert_to_pdf(tmp_docx)
    if not pdf_path or not os.path.exists(pdf_path):
        return jsonify(ok=False, error='Could not convert template to PDF'), 500
    try:
        from ai.ocr import translate_document
        translation = translate_document(pdf_path)
        return jsonify(ok=True, translation=translation)
    except Exception as e:
        app.logger.error(f'Template translate error: {e}')
        return jsonify(ok=False, error='Translation failed. Please try again.'), 500


@app.route('/probate/template/<form_code>/replace', methods=['POST'])
@login_required
def probate_template_replace(form_code):
    """Upload a replacement template for a form (admin/approver only)."""
    role = session.get('user_role')
    if role not in ('admin', 'approver'):
        return jsonify(ok=False, error='Access denied'), 403
    tpl = ProbateFormTemplate.query.filter_by(form_code=form_code).first()
    if not tpl:
        return jsonify(ok=False, error='Template not found'), 404
    file = request.files.get('template')
    if not file or not file.filename:
        return jsonify(ok=False, error='No file selected'), 400
    custom_dir = os.path.join(os.path.dirname(__file__), 'probate_templates', 'custom')
    os.makedirs(custom_dir, exist_ok=True)
    ext = file.filename.rsplit('.', 1)[-1].lower()
    custom_path = os.path.join(custom_dir, f'{form_code}.{ext}')
    file.save(custom_path)
    tpl.file_path = f'probate_templates/custom/{form_code}.{ext}'
    tpl.is_default = False
    tpl.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(ok=True, message=f'Template for {tpl.form_name} updated.')


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    app.run(debug=debug, port=port)
