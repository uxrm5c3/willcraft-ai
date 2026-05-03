"""
WillCraft AI - Malaysian AI Will Writing System
Flask application with multi-step wizard for will drafting.
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify, g, make_response, Response
from functools import wraps
import base64
import difflib
import json
import os
import re
import sys
import tempfile
import threading
import traceback
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from config import FLASK_SECRET_KEY, ANTHROPIC_API_KEY, SQLALCHEMY_DATABASE_URI, DATA_DIR, UPLOAD_DIR, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
from database import db, Client, Will, WillEditLog, WillVersion, Person, Document, User, ROLE_PERMS, ROLE_LABELS, ProbateApplication, ProbateFormTemplate, ProbateGeneratedForm, ChatSession, ChatMessage, LegalQAGap

# Enable SQLite WAL mode + a 5s busy timeout on every new connection.
# Without this, any concurrent write attempt (e.g. inbound email webhook
# arriving while the chat polls or the wizard saves) raises
# "database is locked". WAL allows readers + a single writer concurrently;
# busy_timeout makes additional writers wait up to 5s instead of erroring.
from sqlalchemy import event
from sqlalchemy.engine import Engine
import sqlite3 as _sqlite3

@event.listens_for(Engine, 'connect')
def _enable_sqlite_wal(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, _sqlite3.Connection):
        cur = dbapi_connection.cursor()
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA busy_timeout=5000')
        cur.execute('PRAGMA synchronous=NORMAL')
        cur.close()

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max upload (legal Acts can be 30-40MB)

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

@app.template_filter('oneline')
def oneline_filter(value):
    """Flatten multiline text to a single line, joining with commas."""
    if not value:
        return value
    return ', '.join(line.strip() for line in str(value).splitlines() if line.strip())

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
        'firm_name': 'Tetuan Alan Tan & Associates',
        'firm_address': "24-01 & 24-02, Jln Kempas Utama 2/4, Taman Kempas Utama, 81300 Johor Bahru, Johor Darul Ta'zim",
        'firm_phone': '011-3953 2638',
        'lawyer_name': 'FAIZUL HANAFI BIN TOKIRAN',
        'lawyer_bar_number': 'BC/F/167',
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
        pending_count = Will.query.filter_by(status='pending_approval').filter(Will.deleted_at.is_(None)).count()
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
    # Migrate: add chat_message_id column to documents (links a doc to the chat message that uploaded it)
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("ALTER TABLE documents ADD COLUMN chat_message_id VARCHAR(36)"))
            conn.commit()
    except Exception:
        pass  # Column already exists
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
        ("probate_applications", "deleted_at", "DATETIME"),
        ("wills", "deleted_at", "DATETIME"),
        ("will_edit_logs", "details", "TEXT"),
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

    # Fallback: if step1 (testator) is empty but the user added someone marked
    # 'Testator' in the Identities step (Step 1), pull their details so the
    # Will title and Client name don't end up as "Will of Unknown" / "New Client".
    if not (step1 or {}).get('full_name'):
        for p in session.get('person_registry', []):
            if (p.get('relationship') or '').strip().lower() == 'testator':
                step1 = {
                    'full_name': p.get('full_name', '') or '',
                    'nric_passport': p.get('nric_passport', '') or '',
                    'residential_address': p.get('address', '') or '',
                    'date_of_birth': p.get('date_of_birth', '') or '',
                    'nationality': p.get('nationality', 'Malaysian') or 'Malaysian',
                    'gender': p.get('gender', '') or '',
                    'email': p.get('email', '') or '',
                    'phone': p.get('phone', '') or '',
                    'person_id': p.get('id', '') or '',
                }
                session['step1'] = step1
                session.modified = True
                break

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
    # Coerce all session list fields to [] in case parser left them as None
    def _list(key):
        v = session.get(key)
        return v if isinstance(v, list) else []
    executors = [Executor(**e) for e in _list('step2_executors')]

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
    guardians_data = _list('step3_guardians')
    guardians = [Guardian(**g) for g in guardians_data] if guardians_data else None

    ga_data = session.get('step3_guardian_allowance', {})
    guardian_allowance = (
        GuardianAllowance(**ga_data)
        if ga_data and ga_data.get('payment_mode')
        else None
    )

    # -- Section D: Beneficiaries ---------------------------------------------
    beneficiaries = [Beneficiary(**b) for b in _list('step4_beneficiaries')]

    # -- Section E: Gifts (optional) ------------------------------------------
    gifts_data = _list('step5_gifts')
    gifts = None
    if gifts_data:
        from models.gift import PropertyDetails, FinancialDetails
        gifts = []
        for gd in gifts_data:
            allocs_raw = gd.get('allocations') or []
            allocations = [GiftAllocation(**a) for a in allocs_raw]
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
    res_data = session.get('step6_residuary') or {}
    if not isinstance(res_data, dict):
        res_data = {}
    main_bens_raw = res_data.get('main_beneficiaries') or []
    main_bens = [ResiduaryBeneficiary(**mb) for mb in main_bens_raw]
    sub_groups = []
    for sg in (res_data.get('substitute_groups') or []):
        sub_groups.append([ResiduaryBeneficiary(**sb) for sb in (sg or [])])
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
        session['user_email'] = user.email
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


@app.route('/admin/will-format-preview')
@role_required('admin', 'approver')
def admin_will_format_preview():
    """
    Render the verbatim Alan & Tan PHEK YI TING sample through the current
    PDF/Word generator. Use this to compare against the original sample and
    iterate on the format until it matches exactly.

    Query params:
      ?format=pdf   → PDF (default)
      ?format=docx  → Microsoft Word .docx (downloads, editable in Word)
      ?download=1   → force download even for PDF
    """
    from documents.sample_will_phek_yi_ting import SAMPLE_WILL_TEXT_PHEK_YI_TING
    from documents.empty_template_will import EMPTY_TEMPLATE_WILL_TEXT

    fmt = (request.args.get('format') or 'pdf').lower()
    use_empty = request.args.get('empty') == '1'
    will_text = EMPTY_TEMPLATE_WILL_TEXT if use_empty else SAMPLE_WILL_TEXT_PHEK_YI_TING
    file_label = 'Empty_Template' if use_empty else 'PHEK_YI_TING_Format_Preview'

    logo = _get_logo_path()
    tenant = get_tenant()
    firm_info = None
    if tenant.get('firm_name'):
        firm_info = {
            'firm_name': tenant.get('firm_name', ''),
            'firm_address': tenant.get('firm_address', ''),
            'firm_phone': tenant.get('firm_phone', ''),
            'firm_email': tenant.get('email_from', ''),
        }

    if fmt == 'docx':
        from documents.will_docx import build_will_docx
        try:
            filepath = build_will_docx(
                will_text,
                firm_info=firm_info,
                logo_path=logo,
                is_draft=True,
            )
            with open(filepath, 'rb') as f:
                data = f.read()
        except Exception as e:
            app.logger.error(f'Will format preview (docx) failed: {e}')
            return f'Preview generation (docx) failed: {e}', 500
        return Response(
            data,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            headers={
                'Content-Disposition': f'attachment; filename="{file_label}.docx"',
            },
        )

    # Default: PDF
    from documents.pdf_generator import generate_pdf
    try:
        filepath = generate_pdf(
            will_text,
            file_label,
            logo_path=logo,
            firm_info=firm_info,
        )
        with open(filepath, 'rb') as f:
            pdf_data = f.read()
    except Exception as e:
        app.logger.error(f'Will format preview failed: {e}')
        return f'Preview generation failed: {e}', 500

    download = request.args.get('download') == '1'
    return Response(
        pdf_data,
        mimetype='application/pdf',
        headers={
            'Content-Disposition': (
                f'attachment; filename="{file_label}.pdf"' if download
                else f'inline; filename="{file_label}.pdf"'
            ),
        },
    )


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
    pending = Will.query.filter_by(status='pending_approval').filter(Will.deleted_at.is_(None)).order_by(Will.submitted_at.desc()).all()
    approved = Will.query.filter_by(status='approved').filter(Will.deleted_at.is_(None)).order_by(Will.approved_at.desc()).limit(20).all()
    rejected = Will.query.filter_by(status='rejected').filter(Will.deleted_at.is_(None)).order_by(Will.updated_at.desc()).limit(20).all()
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


@app.route('/api/will/<will_id>/cost', methods=['GET'])
@login_required
def api_will_cost(will_id):
    """Return total token cost for a will + per-call-site breakdown.

    Response:
      { ok: true,
        will_id: "...",
        total_usd: 0.001234,
        total_usd_fmt: "$0.001234",
        saas_budget_usd: 300,            # annual plan price
        budget_pct: 0.04,                # % of annual budget consumed
        calls: [{ call_site, calls, input_tokens, output_tokens, cost_usd }] }
    """
    will_record = db.session.get(Will, will_id)
    if not will_record:
        return jsonify({'ok': False, 'error': 'Will not found'}), 404

    from ai.cost_tracker import total_for_will, breakdown_for_will
    total = float(total_for_will(will_id))
    calls = breakdown_for_will(will_id)
    saas_budget = 300.0            # $300/yr annual plan
    budget_pct = (total / saas_budget * 100) if saas_budget else 0

    return jsonify({
        'ok': True,
        'will_id': will_id,
        'total_usd': round(total, 6),
        'total_usd_fmt': f'${total:.4f}',
        'saas_budget_usd': saas_budget,
        'budget_pct': round(budget_pct, 4),
        'calls': calls,
    })


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
        details='\n'.join(change_details) if change_details else None,
    )
    db.session.add(log_entry)

    # Update the current (latest) version's text to match the edit
    latest_ver = WillVersion.query.filter_by(will_id=will_id).order_by(
        WillVersion.version_number.desc()
    ).first()
    if latest_ver:
        latest_ver.will_text = new_text
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


@app.route('/api/will/<will_id>/ai-edit', methods=['POST'])
@login_required
def api_will_ai_edit(will_id):
    """AI-assisted will editing: user describes changes, AI identifies clause and suggests replacement."""
    will_record = db.session.get(Will, will_id)
    if not will_record:
        return jsonify({'ok': False, 'error': 'Will not found'}), 404

    data = request.get_json()
    if not data or 'instruction' not in data or 'will_text' not in data:
        return jsonify({'ok': False, 'error': 'Missing instruction or will_text'}), 400

    instruction = data['instruction'].strip()
    will_text = data['will_text'].strip()
    if not instruction or not will_text:
        return jsonify({'ok': False, 'error': 'Instruction and will text are required'}), 400

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=os.environ.get('CLAUDE_MODEL', 'claude-sonnet-4-20250514'),
            max_tokens=4000,
            system="""You are a Malaysian will editing assistant. The user wants to make changes to their will.

Your task:
1. Read the user's instruction carefully
2. Identify the specific clause(s) in the will text that need to be changed
3. Generate the suggested replacement text for that clause
4. Explain what you changed and why

Rules:
- Use proper Malaysian legal drafting language
- Use "I hereby devise and bequeath" for property gifts
- Use Malay terms for property: Mukim, Daerah, Negeri
- Use fractions (4/10) not percentages (40%)
- For Malaysian NRIC: "MALAYSIA (NRIC No. XXXXXX-XX-XXXX)"
- Include discharge/lien clause for property gifts
- Keep the same formatting style as the rest of the will
- If the instruction is unclear, ask for clarification in the explanation field

Return ONLY a JSON object:
{
    "original_text": "The exact text from the will that should be replaced (copy verbatim)",
    "suggested_text": "The new text to replace it with",
    "explanation": "Brief explanation of what was changed",
    "clause_number": "The clause number affected (e.g., '5' or '5,6' for multiple)"
}

If the instruction cannot be applied (e.g., refers to something not in the will), return:
{
    "original_text": "",
    "suggested_text": "",
    "explanation": "Explanation of why the change cannot be made",
    "clause_number": ""
}""",
            messages=[{
                'role': 'user',
                'content': f"Here is the current will text:\n\n{will_text}\n\n---\n\nUser's instruction: {instruction}"
            }],
        )
        result_text = response.content[0].text.strip()
        if result_text.startswith('```'):
            result_text = result_text.split('\n', 1)[1].rsplit('```', 1)[0].strip()

        import json as json_mod
        result = json_mod.loads(result_text)
        return jsonify({'ok': True, 'result': result})
    except json_mod.JSONDecodeError:
        return jsonify({'ok': True, 'result': {
            'original_text': '',
            'suggested_text': result_text,
            'explanation': 'AI returned a text response instead of structured edit. You may need to apply manually.',
            'clause_number': ''
        }})
    except Exception as e:
        return jsonify({'ok': False, 'error': f'AI edit failed: {str(e)}'}), 500


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

    # Use SMTP_USER as envelope sender (required for SMTP AUTH), Reply-To for the logical sender
    sender_addr = SMTP_USER or from_email
    msg = MIMEMultipart()
    msg['From'] = sender_addr
    msg['To'] = to_email
    msg['Subject'] = subject
    if from_email and from_email != sender_addr:
        msg['Reply-To'] = from_email
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
        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(sender_addr, all_recipients, msg.as_string())

    return True


@app.route('/api/support/report', methods=['POST'])
@login_required
def api_support_report():
    """Send a bug/issue report with optional screenshot to support@lifa.com.my."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    description = request.form.get('description', '').strip()
    if not description:
        return jsonify(ok=False, error='Description is required'), 400

    page = request.form.get('page', 'Unknown')
    browser = request.form.get('browser', 'Unknown')
    user_name = session.get('user_name', 'Unknown')
    user_email = session.get('user_email', 'Unknown')
    user_role = session.get('user_role', 'Unknown')
    tenant = get_tenant()
    domain = os.environ.get('WILLCRAFT_DOMAIN', 'Unknown')

    subject = f"[WillCraft Issue] {description[:80]}"

    body_html = f"""
    <h2 style="color: #dc2626;">Issue Report from WillCraft</h2>
    <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
        <tr><td style="padding: 8px; border: 1px solid #e5e7eb; font-weight: bold; background: #f9fafb; width: 140px;">Reported By</td>
            <td style="padding: 8px; border: 1px solid #e5e7eb;">{user_name} ({user_email})</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #e5e7eb; font-weight: bold; background: #f9fafb;">Role</td>
            <td style="padding: 8px; border: 1px solid #e5e7eb;">{user_role}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #e5e7eb; font-weight: bold; background: #f9fafb;">Site</td>
            <td style="padding: 8px; border: 1px solid #e5e7eb;">{domain}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #e5e7eb; font-weight: bold; background: #f9fafb;">Page</td>
            <td style="padding: 8px; border: 1px solid #e5e7eb;">{page}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #e5e7eb; font-weight: bold; background: #f9fafb;">Browser</td>
            <td style="padding: 8px; border: 1px solid #e5e7eb; font-size: 11px;">{browser}</td></tr>
    </table>
    <h3 style="margin-top: 16px;">Description</h3>
    <div style="background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 12px; white-space: pre-wrap;">{description}</div>
    <p style="color: #9ca3af; font-size: 11px; margin-top: 16px;">Sent automatically from WillCraft AI</p>
    """

    try:
        sender_addr = SMTP_USER or tenant.get('email_from', '')
        msg = MIMEMultipart()
        msg['From'] = sender_addr
        msg['To'] = 'support@lifa.com.my'
        msg['Subject'] = subject
        if tenant.get('email_from') and tenant['email_from'] != sender_addr:
            msg['Reply-To'] = tenant['email_from']

        msg.attach(MIMEText(body_html, 'html'))

        # Attach screenshot if provided
        screenshot = request.files.get('screenshot')
        if screenshot and screenshot.filename:
            part = MIMEBase('image', 'png')
            part.set_payload(screenshot.read())
            encoders.encode_base64(part)
            safe_name = screenshot.filename.replace('"', '')
            part.add_header('Content-Disposition', f'attachment; filename="{safe_name}"')
            msg.attach(part)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(sender_addr, ['support@lifa.com.my'], msg.as_string())

        return jsonify(ok=True)
    except Exception as e:
        print(f"[Support] Email failed: {e}")
        return jsonify(ok=False, error=f'Failed to send: {str(e)}'), 500


@app.route('/api/will/<will_id>/send-email', methods=['POST'])
@login_required
def api_will_send_email(will_id):
    """Email the approved will (PDF) to a recipient."""
    will_record = db.session.get(Will, will_id)
    if not will_record:
        return jsonify({'ok': False, 'error': 'Will not found'}), 404

    if will_record.status != 'approved':
        return jsonify({'ok': False, 'error': 'Only approved wills can be emailed'}), 403

    # Get recipient email — prefer custom to_email from request body, fallback to client email
    data = request.get_json(silent=True) or {}
    to_email = (data.get('to_email') or '').strip()
    client = db.session.get(Client, will_record.client_id)

    if not to_email:
        if client and client.email:
            to_email = client.email
        else:
            return jsonify({'ok': False, 'error': 'Please enter a recipient email address'}), 400

    if '@' not in to_email:
        return jsonify({'ok': False, 'error': 'Please enter a valid email address'}), 400

    # Generate PDF attachment
    will_text = will_record.generated_will_text or ''
    if not will_text:
        return jsonify({'ok': False, 'error': 'No will text to send'}), 400

    testator_name = (client.full_name if client else '') or 'Client'
    safe_name = "".join(c for c in testator_name if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_') or 'Will'

    try:
        from documents.pdf_generator import generate_pdf
        logo = None
        if will_record.include_logo:
            logo = _get_logo_path()
        # Build firm info for cover page and prepared-by page
        tenant = get_tenant()
        firm_info = None
        if tenant.get('firm_name'):
            firm_info = {
                'firm_name': tenant.get('firm_name', ''),
                'firm_address': tenant.get('firm_address', ''),
                'firm_phone': tenant.get('firm_phone', ''),
                'firm_email': tenant.get('email_from', ''),
            }
        filepath = generate_pdf(will_text, safe_name, logo_path=logo, firm_info=firm_info)
        with open(filepath, 'rb') as f:
            pdf_data = f.read()
    except Exception as e:
        app.logger.error(f'PDF generation failed: {e}')
        return jsonify({'ok': False, 'error': 'Failed to generate PDF'}), 500

    # Build email
    tenant = get_tenant()
    brand = tenant.get('brand', 'WillCraft AI')
    user_name = session.get('user_name', 'Unknown')
    user_role = session.get('user_role', '')

    # CC: merge user-provided CC, tenant CC, and auto-CC approver for admin/advisor
    cc_list = list(tenant.get('email_cc', []))
    user_cc = (data.get('cc') or '').strip()
    if user_cc:
        for addr in user_cc.split(','):
            addr = addr.strip()
            if addr and '@' in addr and addr not in cc_list:
                cc_list.append(addr)
    if user_role in ('admin', 'advisor'):
        approvers = User.query.filter_by(role='approver', is_active=True).all()
        for ap in approvers:
            if ap.email and ap.email not in cc_list:
                cc_list.append(ap.email)

    # Use user-provided subject and body, with defaults
    subject = (data.get('subject') or '').strip() or f"Last Will and Testament — {testator_name}"
    user_body = (data.get('body') or '').strip()

    if user_body:
        import html as html_mod
        body_escaped = html_mod.escape(user_body).replace('\n', '<br>')
        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <p>{body_escaped}</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="font-size: 12px; color: #718096;">
                This email and its attachment are confidential and intended solely for the addressee.
            </p>
        </div>
        """
    else:
        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <p>Dear {testator_name},</p>
            <p>Please find attached the Last Will and Testament in PDF format.</p>
            <p>Kindly review the document carefully. If you have any questions or require
            any amendments, please do not hesitate to contact us.</p>
            <br>
            <p>Best regards,<br><strong>{user_name}</strong><br>{brand}</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="font-size: 12px; color: #718096;">
                This email and its attachment are confidential and intended solely for the addressee.
            </p>
        </div>
        """

    from_email = tenant.get('email_from')
    if not from_email:
        user = db.session.get(User, session.get('user_id'))
        from_email = user.email if user else None
    if not from_email:
        return jsonify({'ok': False, 'error': 'No sender email configured. Contact admin.'}), 400

    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders

        sender_addr = SMTP_USER or from_email
        msg = MIMEMultipart()
        msg['From'] = sender_addr
        msg['To'] = to_email
        msg['Subject'] = subject
        if from_email and from_email != sender_addr:
            msg['Reply-To'] = from_email
        if cc_list:
            msg['Cc'] = ', '.join(cc_list)
        msg.attach(MIMEText(body_html, 'html'))

        part = MIMEBase('application', 'octet-stream')
        part.set_payload(pdf_data)
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename="{safe_name}_Will.pdf"')
        msg.attach(part)

        all_recipients = [to_email] + cc_list
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(sender_addr, all_recipients, msg.as_string())
    except Exception as e:
        app.logger.error(f'Email sending failed: {e}')
        return jsonify({'ok': False, 'error': f'Failed to send email: {str(e)}'}), 500

    sender_name = session.get('user_name', 'Unknown')
    app.logger.info(f'Will {will_id} emailed to {to_email} by {sender_name} (cc: {cc_list})')

    return jsonify({
        'ok': True,
        'sent_to': to_email,
        'cc': cc_list,
        'message': f'Will emailed to {to_email}',
    })


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
@login_required
def index():
    """Landing page."""
    wills_query = Will.query.filter(Will.deleted_at.is_(None))
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


@app.route('/api/will/restore-generated', methods=['POST'])
@login_required
def api_will_restore_generated():
    """Restore the generated will text from the latest version."""
    will_id = session.get('will_id')
    if not will_id:
        return jsonify({'ok': False, 'error': 'No will in session'}), 400
    wr = db.session.get(Will, will_id)
    if not wr:
        return jsonify({'ok': False, 'error': 'Will not found'}), 404
    # Find the latest version
    latest = WillVersion.query.filter_by(will_id=will_id).order_by(
        WillVersion.version_number.desc()
    ).first()
    if not latest or not latest.will_text:
        return jsonify({'ok': False, 'error': 'No version found to restore'}), 404
    wr.generated_will_text = latest.will_text
    wr.status = 'generated'
    session['generated_will_text'] = latest.will_text
    db.session.commit()
    return jsonify({'ok': True, 'version': latest.version_number})


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
        wills_query = Will.query.filter_by(client_id=c.id).filter(Will.deleted_at.is_(None))
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
    """Soft-delete a saved will (recoverable for 30 days)."""
    will_record = db.session.get(Will, will_id)
    if will_record:
        will_record.deleted_at = datetime.utcnow()
        db.session.commit()
        # Clear session if we deleted the currently loaded will
        if session.get('will_id') == will_id:
            session.pop('will_id', None)
        flash('Will moved to trash. It can be restored within 30 days.', 'info')
    return redirect(url_for('will_list'))


@app.route('/wills/<will_id>/restore', methods=['POST'])
@login_required
def will_restore(will_id):
    """Restore a soft-deleted will."""
    will_record = db.session.get(Will, will_id)
    if will_record and will_record.deleted_at:
        will_record.deleted_at = None
        db.session.commit()
        flash('Will restored successfully.', 'success')
    return redirect(url_for('trash_list'))


@app.route('/wills/<will_id>/permanent-delete', methods=['POST'])
@login_required
def will_permanent_delete(will_id):
    """Permanently delete a will (admin only, from trash)."""
    role = session.get('user_role')
    if role not in ('admin',):
        flash('Access denied.', 'error')
        return redirect(url_for('trash_list'))
    will_record = db.session.get(Will, will_id)
    if will_record:
        db.session.delete(will_record)
        db.session.commit()
        flash('Will permanently deleted.', 'success')
    return redirect(url_for('trash_list'))


@app.route('/trash')
@login_required
def trash_list():
    """Show soft-deleted wills and probate applications (recoverable for 30 days)."""
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=30)

    # Auto-purge items older than 30 days
    expired_wills = Will.query.filter(Will.deleted_at.isnot(None), Will.deleted_at < cutoff).all()
    for w in expired_wills:
        db.session.delete(w)
    expired_probates = ProbateApplication.query.filter(
        ProbateApplication.deleted_at.isnot(None), ProbateApplication.deleted_at < cutoff
    ).all()
    for p in expired_probates:
        # Clean up generated form files
        gen_forms = ProbateGeneratedForm.query.filter_by(probate_id=p.id).all()
        for gf in gen_forms:
            if gf.file_path and os.path.exists(gf.file_path):
                try:
                    os.remove(gf.file_path)
                except OSError:
                    pass
        ProbateGeneratedForm.query.filter_by(probate_id=p.id).delete()
        db.session.delete(p)
    if expired_wills or expired_probates:
        db.session.commit()

    # Fetch soft-deleted items with days_left
    now = datetime.utcnow()
    deleted_wills = Will.query.filter(Will.deleted_at.isnot(None)).order_by(Will.deleted_at.desc()).all()
    for w in deleted_wills:
        w.days_left = max(0, 30 - (now - w.deleted_at).days)
    deleted_probates = ProbateApplication.query.filter(
        ProbateApplication.deleted_at.isnot(None)
    ).order_by(ProbateApplication.deleted_at.desc()).all()
    for p in deleted_probates:
        p.days_left = max(0, 30 - (now - p.deleted_at).days)

    return render_template('trash.html',
                           deleted_wills=deleted_wills,
                           deleted_probates=deleted_probates)


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


@app.route('/api/persons/<person_id>', methods=['PATCH'])
@login_required
def api_persons_patch(person_id):
    """Partial update of a person — for inline error correction."""
    person = db.session.get(Person, person_id)
    if not person:
        return jsonify({'ok': False, 'error': 'Person not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'ok': False, 'error': 'No data provided'}), 400

    # Only allow updating specific fields
    allowed_fields = ['full_name', 'nric_passport', 'address', 'date_of_birth',
                       'gender', 'nationality', 'email', 'phone', 'relationship']
    updated = []
    for field in allowed_fields:
        if field in data:
            setattr(person, field, data[field])
            updated.append(field)

    if updated:
        db.session.commit()
        _refresh_session_person_registry(person.client_id)
        return jsonify({'ok': True, 'updated': updated})

    return jsonify({'ok': False, 'error': 'No valid fields to update'}), 400


@app.route('/api/validate/persons', methods=['GET'])
@login_required
def api_validate_persons():
    """Validate all persons in the current will session."""
    from validation.field_validator import validate_person
    registry = session.get('person_registry', [])
    results = {}
    for p in registry:
        errors = validate_person(p)
        if errors:
            results[p.get('id', '')] = {
                'name': p.get('full_name', ''),
                'errors': errors
            }
    return jsonify({'ok': True, 'results': results})


@app.route('/api/validate/gifts', methods=['GET'])
@login_required
def api_validate_gifts():
    """Validate all gift property details in the current will session."""
    from validation.field_validator import validate_property_details
    gifts = session.get('step5_gifts', [])
    results = {}
    for i, g in enumerate(gifts):
        if g.get('gift_type') == 'property':
            prop = g.get('property_details', {})
            errors = validate_property_details(prop)
            if errors:
                results[str(i)] = {
                    'description': prop.get('property_address', f'Gift {i+1}')[:50],
                    'errors': errors
                }
    return jsonify({'ok': True, 'results': results})


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
    # Clear references from probate applications
    for pa in ProbateApplication.query.filter_by(death_cert_document_id=doc_id).all():
        pa.death_cert_document_id = None
    for pa in ProbateApplication.query.filter_by(will_document_id=doc_id).all():
        pa.will_document_id = None
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
    wills = Will.query.filter_by(client_id=client_id).filter(Will.deleted_at.is_(None)).order_by(Will.updated_at.desc()).all()

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

    # The parser returns objects like {step2_executors: {executors: [...]}} —
    # unwrap the nested arrays so the session matches what the wizard expects.
    def _unwrap(obj, key):
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            return obj.get(key, []) or []
        return []

    from services.person_registry import ensure_person, normalise_dob as _normalise_dob

    def _ensure_person(name, nric, address='', relationship='', dob='',
                       nationality='Malaysian'):
        return ensure_person(client_id, name, nric=nric, address=address,
                             relationship=relationship, dob=dob, nationality=nationality)

    # ── Step 1: Testator ─────────────────────────────────────────────
    if 'step1_testator' in parsed:
        s1 = dict(parsed['step1_testator'] or {})
        s1['date_of_birth'] = _normalise_dob(s1.get('date_of_birth', ''))
        # Create / update the testator's Person record
        testator_pid = _ensure_person(
            s1.get('full_name'), s1.get('nric_passport'),
            address=s1.get('residential_address', ''),
            relationship='Testator',
            dob=s1.get('date_of_birth', ''),
            nationality=s1.get('nationality', 'Malaysian'),
        )
        if testator_pid:
            s1['person_id'] = testator_pid
        session['step1'] = s1

    # ── Step 2: Executors ────────────────────────────────────────────
    if 'step2_executors' in parsed:
        raw_execs = _unwrap(parsed['step2_executors'], 'executors')
        execs = []
        for ex in raw_execs:
            pid = _ensure_person(
                ex.get('full_name'), ex.get('nric_passport'),
                address=ex.get('address', ''),
                relationship=ex.get('relationship', ''),
                nationality=ex.get('nationality', 'Malaysian'),
            )
            ex_clean = dict(ex)
            if pid:
                ex_clean['person_id'] = pid
            execs.append(ex_clean)
        session['step2_executors'] = execs
        session['step3_executor_type'] = 'joint' if len(execs) > 1 else 'single'
        session['step3_trustees'] = {'same_as_executor': True, 'trustees': [{}]}

    # ── Step 3: Guardians ────────────────────────────────────────────
    if 'step3_guardians' in parsed:
        raw_g = _unwrap(parsed['step3_guardians'], 'guardians')
        guardians = []
        for g in raw_g:
            pid = _ensure_person(g.get('full_name'), g.get('nric_passport'),
                                  address=g.get('address', ''),
                                  relationship=g.get('relationship', ''))
            gd = dict(g)
            if pid:
                gd['person_id'] = pid
            guardians.append(gd)
        session['step3_guardians'] = guardians

    # ── Step 4: Beneficiaries ────────────────────────────────────────
    if 'step4_beneficiaries' in parsed:
        raw_b = _unwrap(parsed['step4_beneficiaries'], 'beneficiaries')
        bens = []
        for b in raw_b:
            pid = _ensure_person(
                b.get('full_name'),
                b.get('nric_passport') or b.get('nric_passport_birthcert'),
                relationship=b.get('relationship', ''),
            )
            bd = dict(b)
            if pid:
                bd['person_id'] = pid
            # Wizard expects nric_passport_birthcert
            if 'nric_passport' in bd and 'nric_passport_birthcert' not in bd:
                bd['nric_passport_birthcert'] = bd.pop('nric_passport')
            bens.append(bd)
        session['step4_beneficiaries'] = bens

    # ── Step 5: Gifts ────────────────────────────────────────────────
    if 'step5_gifts' in parsed:
        session['step5_gifts'] = _unwrap(parsed['step5_gifts'], 'gifts')
    # ── Step 6/7/8: Residuary / Trust / Others ───────────────────────
    if 'step6_residuary' in parsed:
        s6 = parsed['step6_residuary']
        session['step6_residuary'] = s6 if isinstance(s6, dict) else {}
    if 'step7_trust' in parsed:
        s7 = parsed['step7_trust']
        session['step7_trust'] = s7 if isinstance(s7, dict) else {}
    if 'step8_other_matters' in parsed:
        s8 = parsed['step8_other_matters']
        session['step8_others'] = s8 if isinstance(s8, dict) else {}

    # Commit Person creates + refresh registry so wizard pickers see them
    db.session.commit()
    _refresh_session_person_registry(client_id)

    # Mark all steps with imported data as complete so the wizard shows them
    # in green and the user can jump straight to step 10 (Review & Generate).
    completed = set(session.get('completed_steps', []))
    for n, has in [
        (1, 'step1_testator' in parsed),
        (2, 'step1_testator' in parsed),
        (3, 'step2_executors' in parsed),
        (4, 'step3_guardians' in parsed),
        (5, 'step4_beneficiaries' in parsed),
        (6, 'step5_gifts' in parsed),
        (7, 'step6_residuary' in parsed),
        (8, 'step7_trust' in parsed),
        (9, 'step8_other_matters' in parsed),
    ]:
        if has:
            completed.add(n)
    session['completed_steps'] = sorted(completed)

    session['will_title'] = f"Imported: {file.filename}"
    session.modified = True

    # Auto-save to DB
    save_will_to_db()

    return jsonify({'ok': True})


# -- Client Chat (per-client AI inbox) --------------------------------------

def _get_or_create_chat_session(client_id, user_id):
    """One ChatSession per client (the most recent). Create if missing."""
    cs = (ChatSession.query
          .filter_by(client_id=client_id)
          .order_by(ChatSession.created_at.desc())
          .first())
    if cs:
        return cs
    cs = ChatSession(client_id=client_id, created_by=user_id)
    db.session.add(cs)
    db.session.flush()
    return cs


def _get_or_create_active_will(client_id, user_id):
    """Most recently updated non-deleted draft Will for this client, or new one."""
    will = (Will.query
            .filter_by(client_id=client_id, status='draft')
            .filter(Will.deleted_at.is_(None))
            .order_by(Will.updated_at.desc())
            .first())
    if will:
        return will
    will = Will(client_id=client_id, created_by=user_id)
    db.session.add(will)
    db.session.flush()
    return will


def _normalise_gifts(gifts: list) -> list:
    """Ensure every gift in step5_data has a `kind` field that the snapshot
    JS can use to display a meaningful label.

    Two historic save paths produced gifts without `kind`:
      1. Old `_try_save_property_gift` before the `kind` field was added —
         these have `document_id` + optional `property_address`/`title_number`.
      2. Wizard-format gifts using `gift_type` instead of `kind`.

    We normalise in place so the JS only needs to check one field.
    """
    if not isinstance(gifts, list):
        return gifts
    result = []
    for g in gifts:
        if not isinstance(g, dict):
            result.append(g)
            continue
        g = dict(g)   # copy so we don't mutate the original
        # --- already has kind ---
        if g.get('kind'):
            result.append(g)
            continue
        # --- wizard format: gift_type → kind ---
        gt = (g.get('gift_type') or '').strip().lower()
        if gt == 'property':
            g['kind'] = 'property'
            # Flatten property_details to top-level so JS checks propAddr etc.
            pd = g.get('property_details') or {}
            for f in ('property_address', 'title_number', 'lot_number',
                      'mukim', 'daerah', 'negeri'):
                if not g.get(f) and pd.get(f):
                    g[f] = pd[f]
            # Flatten allocations → beneficiaries
            if not g.get('beneficiaries') and g.get('allocations'):
                g['beneficiaries'] = [
                    {'name': a.get('beneficiary_name', ''), 'share': a.get('share', '')}
                    for a in g['allocations'] if a.get('beneficiary_name')
                ]
        elif gt in ('financial', 'bank'):
            g['kind'] = 'bank'
            fd = g.get('financial_details') or {}
            if not g.get('bank_name') and fd.get('institution'):
                g['bank_name'] = fd['institution']
            if not g.get('account_number') and fd.get('account_number'):
                g['account_number'] = fd['account_number']
            if not g.get('beneficiaries') and g.get('allocations'):
                g['beneficiaries'] = [
                    {'name': a.get('beneficiary_name', ''), 'share': a.get('share', '')}
                    for a in g['allocations'] if a.get('beneficiary_name')
                ]
        elif gt == 'vehicle':
            g['kind'] = 'vehicle'
        elif gt in ('other', ''):
            g['kind'] = 'other'
        else:
            g['kind'] = gt   # pass through unknown types
        # --- old chat-format: has document_id but no kind → property gift ---
        if not g.get('kind') and g.get('document_id'):
            g['kind'] = 'property'
        result.append(g)
    return result


def _will_data_snapshot(will_record):
    """Parse a Will's step JSON columns into a single dict for the chat planner & UI.
    Also includes the live Person registry from DB so the chat's right-pane
    sees identities as they're added in chat (they don't live in stepN_data
    until the wizard saves)."""
    if not will_record:
        return {}
    def _j(s, default):
        try:
            return json.loads(s) if s else default
        except (json.JSONDecodeError, TypeError):
            return default
    # Live identities from Person table — these are the single source of truth
    # while the chat is running, before the wizard's save_will_to_db serializes
    # them into identities_data.
    persons = (Person.query.filter_by(client_id=will_record.client_id)
               .order_by(Person.full_name.asc()).all())
    identities = [{
        'id': p.id, 'full_name': p.full_name,
        'nric_passport': p.nric_passport, 'relationship': p.relationship or '',
        'date_of_birth': p.date_of_birth or '',
        'document_id': p.document_id or '',
    } for p in persons]
    # `completed_steps` carries chat-flow markers like 'assets_confirmed'
    # that the planner uses to gate ordering — collect ALL gifts before
    # asking who inherits, just like we collect ALL identities before
    # asking who's the executor.
    completed = _j(will_record.completed_steps, [])
    if not isinstance(completed, list):
        completed = []
    return {
        'will_id': will_record.id,
        'title': will_record.title,
        'status': will_record.status,
        'step1': _j(will_record.step1_data, {}),
        'step2': _j(will_record.step2_data, {}),
        'step3': _j(will_record.step3_data, {}),
        'step4': _j(will_record.step4_data, []),
        'step5': _normalise_gifts(_j(will_record.step5_data, [])),
        'step6': _j(will_record.step6_data, {}),
        'step7': _j(will_record.step7_data, {}),
        'step8': _j(will_record.step8_data, {}),
        'identities': identities,
        'completed_steps': completed,
        'current_stage_num': _current_stage_num(will_record.client_id, will_record),
    }


def _serialise_chat_message(m):
    """Turn a ChatMessage row into a JSON-friendly dict for the UI."""
    def _j(s, default):
        try:
            return json.loads(s) if s else default
        except (json.JSONDecodeError, TypeError):
            return default

    attachment_ids = _j(m.attachments_json, [])
    attachments = []
    if attachment_ids:
        docs = Document.query.filter(Document.id.in_(attachment_ids)).all()
        doc_by_id = {d.id: d for d in docs}
        for did in attachment_ids:
            d = doc_by_id.get(did)
            if not d:
                continue
            attachments.append({
                'id': d.id,
                'filename': d.original_filename,
                'category': d.category,
                'size': d.file_size,
            })
    return {
        'id': m.id,
        'role': m.role,
        'content': m.content or '',
        'attachments': attachments,
        'clarifying_questions': _j(m.clarifying_questions_json, []),
        'proposed_patch': _j(m.proposed_patch_json, None),
        'advice': _j(m.advice_json, []),
        'applied_at': m.applied_at.isoformat() if m.applied_at else None,
        'rejected_at': m.rejected_at.isoformat() if m.rejected_at else None,
        'created_at': m.created_at.isoformat() if m.created_at else None,
    }


@app.route('/chat/<client_id>')
@login_required
def chat_page(client_id):
    """Render the per-client AI chat page."""
    from services.inbound_address import address_for_client
    client = db.session.get(Client, client_id)
    if not client:
        flash('Client not found.', 'error')
        return redirect(url_for('clients_list'))
    # Derive the inbox host from the request host. Production: will.alantanjb.com → inbox.will.alantanjb.com
    host = request.host.split(':')[0] if request else 'localhost'
    inbox_host = f"inbox.{host}"
    inbox_address = address_for_client(client, inbox_host)
    inbox_enabled = bool(os.environ.get('POSTMARK_INBOUND_USER') and os.environ.get('POSTMARK_INBOUND_PASS'))
    return render_template('chat.html', client=client,
                           inbox_address=inbox_address,
                           inbox_enabled=inbox_enabled)


@app.route('/api/chat/<client_id>/history')
@login_required
def api_chat_history(client_id):
    """Return all messages in the client's chat session + current will snapshot."""
    client = db.session.get(Client, client_id)
    if not client:
        return jsonify({'ok': False, 'error': 'Client not found'}), 404

    cs = (ChatSession.query
          .filter_by(client_id=client_id)
          .order_by(ChatSession.created_at.desc())
          .first())
    messages = []
    if cs:
        for m in cs.messages:
            messages.append(_serialise_chat_message(m))

    active_will = (Will.query
                   .filter_by(client_id=client_id, status='draft')
                   .filter(Will.deleted_at.is_(None))
                   .order_by(Will.updated_at.desc())
                   .first())
    snapshot = _will_data_snapshot(active_will)
    return jsonify({'ok': True, 'messages': messages, 'will': snapshot})


@app.route('/api/chat/<client_id>/message', methods=['POST'])
@login_required
def api_chat_message(client_id):
    """Receive a chat message (text + optional file uploads) and return the assistant reply."""
    from uploads import save_uploaded_file
    from ai.file_classifier import classify_file
    from ai.ocr import extract_nric_data
    from ai.chat_planner import plan_turn
    from ai.voice_transcription import is_audio, transcribe

    client = db.session.get(Client, client_id)
    if not client:
        return jsonify({'ok': False, 'error': 'Client not found'}), 404

    user_text = (request.form.get('text') or '').strip()
    files = request.files.getlist('files') if 'files' in request.files else []

    if not user_text and not files:
        return jsonify({'ok': False, 'error': 'Empty message'}), 400

    user_id = session.get('user_id')
    cs = _get_or_create_chat_session(client_id, user_id)

    # Establish cost-tracking context for every Anthropic call made in this
    # request: will_id, client_id, user_id auto-attach to ApiCallLog rows.
    _will_for_ctx = (Will.query
                     .filter_by(client_id=client_id, status='draft')
                     .filter(Will.deleted_at.is_(None))
                     .order_by(Will.updated_at.desc())
                     .first())
    _cost_ctx_ids = {
        'client_id': client_id,
        'will_id': _will_for_ctx.id if _will_for_ctx else None,
        'user_id': user_id,
    }
    try:
        from ai.cost_tracker import track_context as _track_ctx
        _cost_tracker_cm = _track_ctx(**_cost_ctx_ids)
        _cost_tracker_cm.__enter__()
    except Exception:
        _cost_tracker_cm = None

    # 1. Persist the user message first so attachments can FK to it
    user_msg = ChatMessage(
        session_id=cs.id, role='user', content=user_text,
        attachments_json='[]',
    )
    db.session.add(user_msg)
    db.session.flush()

    folder_name = client.folder_name
    attachment_ids = []
    artifacts = []
    file_errors = []
    voice_transcripts = []  # collected to merge into user_text

    for f in files:
        if not f or not f.filename:
            continue
        # Audio files go to documents/voice/, get transcribed, and bypass the
        # vision classifier (Whisper handles them, file_classifier would just
        # error trying to send them as images).
        is_voice = is_audio(f.content_type or '') or is_audio(f.filename)
        category_initial = 'voice' if is_voice else 'chat_inbox'
        try:
            saved_name, rel_path, file_size = save_uploaded_file(
                f, client_id, category=category_initial, folder_name=folder_name)
        except ValueError as e:
            file_errors.append(f"{f.filename}: {e}")
            continue

        doc = Document(
            client_id=client_id,
            chat_message_id=user_msg.id,
            filename=saved_name, original_filename=f.filename,
            file_path=rel_path, file_type=f.content_type,
            file_size=file_size, category=category_initial,
        )
        db.session.add(doc)
        db.session.flush()
        attachment_ids.append(doc.id)

        abs_path = os.path.join(UPLOAD_DIR, rel_path)

        if is_voice:
            transcript = transcribe(abs_path)
            if transcript:
                voice_transcripts.append(transcript)
                doc.description = (transcript[:500] or None)
                try:
                    doc.extracted_data = json.dumps({'transcript': transcript})
                except (TypeError, ValueError):
                    pass
            else:
                doc.description = '(transcription failed or unavailable)'
            artifacts.append({
                'document_id': doc.id, 'kind': 'voice',
                'confidence': 'high' if transcript else 'low',
                'extracted': {'transcript': transcript} if transcript else None,
                'original_filename': f.filename,
            })
            continue  # don't run vision classifier on audio

        # 2. Classify (vision)
        classification = classify_file(abs_path)
        kind = classification.get('kind', 'other')

        # 3. Extract per kind
        extracted = None
        try:
            if kind == 'nric':
                extracted = extract_nric_data(abs_path)
            elif kind in ('property_title', 'property_spa', 'property_tax',
                           'property_transfer'):
                # Full property OCR for all four — Borang 14A/16A contains
                # lot_number and title_number which the extractor can read.
                # The KIND tells the downstream walker whether this counts
                # as registered ownership evidence (only property_title does).
                from ai.property_extractor import extract_property_data
                extracted = extract_property_data(abs_path, doc_type='general')
            elif kind in ('utility_bill', 'bank_letter'):
                # Light-touch: no per-field extraction yet — the classifier
                # has already given us `purpose` + `property_hint` which is
                # enough to cluster under a property and show in chat.
                extracted = {}
            elif kind == 'bank_statement':
                from ai.ocr import extract_asset_document
                extracted = extract_asset_document(abs_path, asset_type='bank')
            elif kind == 'vehicle':
                from ai.ocr import extract_asset_document
                extracted = extract_asset_document(abs_path, asset_type='vehicle')
            elif kind in ('insurance', 'epf_kwsp'):
                from ai.ocr import extract_asset_document
                extracted = extract_asset_document(abs_path, asset_type='other')
        except Exception as e:
            extracted = {'error': str(e)}

        # 4. Update Document with classification result
        doc.category = kind if kind != 'other' else 'chat_inbox'
        doc.description = classification.get('reason', '')[:500] if classification.get('reason') else None
        # Persist per-image `purpose` (what THIS image proves) and
        # `property_hint` (lot/address used to cluster multiple uploads
        # under one property) so the chat planner can surface and group.
        purpose = (classification.get('purpose') or '').strip()
        prop_hint = (classification.get('property_hint') or '').strip()
        will_relevant = classification.get('will_relevant', True)
        if extracted is None:
            extracted = {}
        if purpose:
            extracted['purpose'] = purpose[:300]
        if prop_hint:
            extracted['property_hint'] = prop_hint[:300]
        # ── Message context ──────────────────────────────────────────────
        # Store the text the client sent WITH this file. This is the single
        # most reliable context clue — the client typed "my house at Lot
        # 127082, give to Joshua" in the same WhatsApp/email message as
        # they attached the geran. We use it later to back-fill missing
        # OCR fields and to surface intent on the property card.
        # For direct chat uploads we also look at the previous user message
        # (back-to-back: text THEN image is common in WhatsApp-style flows).
        try:
            wa_ctx = _extract_whatsapp_context_for_file(
                user_text or '', doc.original_filename or ''
            )
            if wa_ctx:
                extracted['_message_context'] = wa_ctx[:800]
                extracted['_context_source'] = 'whatsapp_preceding'
            else:
                msg_context_parts = []
                if user_text:
                    msg_context_parts.append(user_text)
                prev_msgs = (ChatMessage.query
                             .filter_by(session_id=cs.id, role='user')
                             .filter(ChatMessage.id != user_msg.id)
                             .order_by(ChatMessage.created_at.desc())
                             .limit(3).all())
                for pm in prev_msgs:
                    txt = (pm.content or '').strip()
                    if txt:
                        msg_context_parts.append(txt)
                if msg_context_parts:
                    extracted['_message_context'] = '\n'.join(msg_context_parts)[:800]
        except Exception:
            pass
        # For low-confidence 'other' docs: cross-check recent chat messages
        # to see if the client mentioned this image (by filename keyword or
        # asset reference). If no mention found, flag as likely irrelevant.
        if kind == 'other' and not will_relevant:
            try:
                recent = _gather_recent_chat_text(client_id)
                fname_stem = doc.original_filename.rsplit('.', 1)[0].lower()
                # Rough check: any asset keywords in context?
                asset_keywords = ('property', 'house', 'land', 'lot', 'geran',
                                  'bank', 'account', 'insurance', 'car', 'vehicle',
                                  'lot', 'mukim', 'title', fname_stem)
                if not any(kw in (recent or '').lower() for kw in asset_keywords):
                    extracted['_likely_irrelevant'] = True
                    extracted['_irrelevant_reason'] = (
                        'Classified as unrecognised document type and no matching '
                        'asset was mentioned in the chat.'
                    )
            except Exception:
                pass
        if extracted:
            try:
                doc.extracted_data = json.dumps(extracted)
            except (TypeError, ValueError):
                doc.extracted_data = None

        # Dedupe: if this IC's extracted name already exists on another
        # nric Document for this client, mark the new one as duplicate
        # so it never enters the walk-through pool.
        is_duplicate = False
        if kind == 'nric' and extracted and not extracted.get('error'):
            is_duplicate = _dedupe_ic_against_existing(client_id, doc, extracted)

        if is_duplicate:
            continue  # don't emit as an artifact — it's a duplicate

        artifacts.append({
            'document_id': doc.id,
            'kind': kind,
            'confidence': classification.get('confidence', 'low'),
            'extracted': extracted,
            'original_filename': f.filename,
        })

    # Merge voice transcripts into the user-visible message text so the planner
    # treats spoken instructions the same as typed ones.
    if voice_transcripts:
        joined = '\n\n'.join(voice_transcripts)
        if user_text:
            user_text = f"{user_text}\n\n_(voice)_ {joined}"
        else:
            user_text = f"_(voice)_ {joined}"
        user_msg.content = user_text

    user_msg.attachments_json = json.dumps(attachment_ids)
    db.session.commit()  # persist user_msg before any deductions / planner

    # 5. Directed flow: try to assign the next pending IC if user replied
    #    with a relationship keyword OR confirmation. If not, see if they
    #    asked to delete the focused doc. Then refresh the pending list so
    #    the planner asks about the NEXT one.
    # Q&A digression: if user asked a side-quest question, answer it as a
    # SEPARATE assistant message. By default we SHORT-CIRCUIT and return
    # only the Q&A — the answer already includes a "↩ Resume <step>" quick
    # reply so the writer can come back. The planner only also runs if the
    # turn ALSO carried files OR the text starts with an action token (yes
    # / skip / confirm / a beneficiary share like "Joshua 50%") — in those
    # cases the user mixed a question with an action and both need handling.
    qa_msg = None
    if user_text:
        try:
            from ai.legal_qa import is_question, answer_question
            looks_like_question = is_question(user_text)
        except Exception as e:
            try:
                import logging
                logging.getLogger(__name__).warning(
                    "is_question failed: %s", e, exc_info=True)
            except Exception:
                pass
            looks_like_question = False
        if looks_like_question:
            current_will = (Will.query.filter_by(client_id=client_id, status='draft')
                            .filter(Will.deleted_at.is_(None))
                            .order_by(Will.updated_at.desc()).first())
            try:
                stage = _current_stage_label(client_id, current_will)
            except Exception:
                stage = ''
            try:
                ans = answer_question(user_text, stage, client_id=client_id,
                                      user_id=session.get('user_id'))
            except Exception as e:
                try:
                    import logging
                    logging.getLogger(__name__).warning(
                        "answer_question crashed: %s", e, exc_info=True)
                except Exception:
                    pass
                # Never let the Q&A path fail silently — emit a fallback so
                # the user always sees SOMETHING acknowledging their question.
                ans = (f"**Answer:** I couldn't reach the legal-Q&A engine just "
                       f"now — please retry in a moment.\n\n_(error: {str(e)[:100]})_")
            if not ans:
                ans = ("**Answer:** I couldn't generate an answer for that — "
                       "try rephrasing, or check the [Legal Library](/library).")
            qa_msg = ChatMessage(session_id=cs.id, role='assistant', content=ans,
                                 attachments_json='[]')
            db.session.add(qa_msg)
            db.session.commit()

            # Decide: should the planner ALSO run after the Q&A?
            #
            # Default: NO — short-circuit. The Q&A answer already includes a
            # "↩ Resume" quick-reply, and re-asking the current step on top
            # of the Q&A buries the answer in noise (this is the bug the
            # writer reported: "no response" — actually the Q&A was there
            # but a giant asset card was rendered after it).
            #
            # Run the planner ONLY when the turn also looks like an action:
            #   - files attached this turn
            #   - text starts with a confirmation/skip/delete token
            #   - text contains an explicit share assignment ("Joshua 50%")
            _kw = user_text.strip().lower()
            _action_starts = ('yes', 'skip', 'delete', 'no', 'confirm',
                              'remove', 'inventory ', 'unlink ',
                              'guardian ', 'trust ', 'trust yes', 'trust skip',
                              'others ', 'residuary skip', 'property fill',
                              'property ', 'address:', 'daerah:', 'negeri:',
                              'mukim:', 'lot:', 'title:',
                              'change ', 'confirm defaults')
            has_share = bool(re.search(r'\b\d{1,3}\s*%|\bequal\b|\b\d+/\d+\b', _kw))
            also_action = (
                bool(files) or bool(artifacts)
                or any(_kw == t.strip() or _kw.startswith(t) for t in _action_starts)
                or has_share
            )
            if not also_action:
                return jsonify({
                    'ok': True,
                    'user_message': _serialise_chat_message(user_msg),
                    'assistant_message': _serialise_chat_message(qa_msg),
                })

    just_assigned = _try_assign_pending_identity(client_id, user_text)
    just_deleted = None if just_assigned else _try_delete_pending_identity(client_id, user_text)
    # If user replied "confirm assets" / "i have more to upload" at the
    # asset-inventory gate, stamp the marker (or clear it) so the planner
    # advances past the gate to the per-asset assignment step.
    just_assets_gate = None
    just_inventory = None
    if not just_assigned and not just_deleted:
        # Walk-through actions take priority — the writer is reviewing
        # one specific asset card, so 'inventory confirm' / 'inventory
        # skip' / 'inventory unlink' must hit the per-asset handler
        # before the gate fallback.
        just_inventory = (_try_handle_restart_gifts(client_id, user_text)
                          or _try_handle_unlink_action(client_id, user_text)
                          or _try_handle_inventory_action(client_id, user_text)
                          or _try_handle_property_fill(client_id, user_text)
                          or _try_handle_ownership(client_id, user_text)
                          or _try_handle_encumbrance(client_id, user_text))
        if not just_inventory:
            just_assets_gate = _try_handle_assets_gate(client_id, user_text)
    # If past Step 1, attempt executor save then beneficiaries save then
    # gift-delete then gift-save. Delete BEFORE save so a "delete" reply
    # at a Step-6 property card removes the doc instead of trying to parse
    # it as beneficiary names (which always fails).
    just_executor = None
    just_benef = None
    just_gift_deleted = None
    just_gift = None
    just_guardian = None
    just_trust = None
    just_others = None
    just_residuary_skip = None
    if not just_assigned and not just_deleted:
        from services.identity_walker import get_pending_ic_documents as _gpid
        if not _gpid(client_id):  # Step 1 done
            just_executor = _try_save_executor(client_id, user_text)
            if not just_executor:
                just_guardian = _try_handle_guardian_action(client_id, user_text)
                if not just_guardian:
                    just_trust = _try_handle_trust_action(client_id, user_text)
                    if not just_trust:
                        just_others = _try_handle_others_action(client_id, user_text)
                        if not just_others:
                            just_residuary_skip = _try_handle_residuary_skip(client_id, user_text)
                            if not just_residuary_skip:
                                just_benef = _try_save_beneficiaries(client_id, user_text)
                                if not just_benef:
                                    just_gift_deleted = _try_delete_pending_gift(client_id, user_text)
                                    if not just_gift_deleted:
                                        just_gift = _try_save_property_gift(client_id, user_text)
    from services.identity_walker import get_pending_ic_documents
    from services.gift_walker import get_pending_gift_documents
    pending_ics = get_pending_ic_documents(client_id)
    pending_gifts = get_pending_gift_documents(client_id)
    recent_text = _gather_recent_chat_text(client_id)

    # 6. Plan the assistant turn against the current Will state
    active_will = (Will.query
                   .filter_by(client_id=client_id, status='draft')
                   .filter(Will.deleted_at.is_(None))
                   .order_by(Will.updated_at.desc())
                   .first())
    will_snapshot = _will_data_snapshot(active_will)
    # Treat any save as "just_assigned" so the planner acknowledges + advances
    just = (just_assigned or just_executor or just_benef
            or just_gift_deleted or just_gift or just_assets_gate
            or just_inventory or just_guardian or just_trust
            or just_others or just_residuary_skip)
    will_snapshot['pending_gifts'] = pending_gifts
    # If a property_fill action produced a reply_override (e.g. the "how to
    # type missing fields" prompt), inject it into the plan instead of running
    # the normal planner — it's a simple instructional message, not a full turn.
    # Any inventory action that sets reply_override wants to replace the
    # planner's normal turn (address gate, ownership gate, encumbrance gate,
    # gifts restart, etc.). The presence of reply_override is the selector —
    # only gate/fill results set it; normal confirm/skip results do not.
    _fill_override = (isinstance(just_inventory, dict)
                      and bool(just_inventory.get('reply_override')))
    plan = plan_turn(user_text, artifacts, will_snapshot,
                     pending_ics=pending_ics, recent_text=recent_text,
                     just_assigned=just, just_deleted=just_deleted)
    if _fill_override:
        plan['reply'] = just_inventory['reply_override']

    if file_errors:
        plan['reply'] = (plan.get('reply') or '') + (
            "\n\n**Some files were rejected:**\n- " + "\n- ".join(file_errors)
        )

    asst_msg = ChatMessage(
        session_id=cs.id,
        role='assistant',
        content=plan.get('reply', ''),
        attachments_json=json.dumps(plan.get('focus_attachments') or []),
        clarifying_questions_json=json.dumps(plan.get('clarifying_questions', [])),
        proposed_patch_json=json.dumps(plan['proposed_patch']) if plan.get('proposed_patch') else None,
        advice_json=json.dumps(plan.get('advice', [])),
        target_will_id=active_will.id if active_will else None,
    )
    db.session.add(asst_msg)
    db.session.commit()

    # If a side-quest Q&A reply was produced earlier in this turn, surface it
    # alongside the planner's reply so the user sees BOTH (the answer + the
    # re-asked step) immediately, without waiting on a history refresh.
    extra = {}
    if qa_msg is not None:
        extra['qa_message'] = _serialise_chat_message(qa_msg)
    try:
        if _cost_tracker_cm is not None:
            _cost_tracker_cm.__exit__(None, None, None)
    except Exception:
        pass
    return jsonify({
        'ok': True,
        'user_message': _serialise_chat_message(user_msg),
        'assistant_message': _serialise_chat_message(asst_msg),
        **extra,
    })


@app.route('/api/chat/<client_id>/apply/<message_id>', methods=['POST'])
@login_required
def api_chat_apply(client_id, message_id):
    """Apply the proposed_patch from an assistant message to the active Will."""
    from services.person_registry import ensure_person

    client = db.session.get(Client, client_id)
    if not client:
        return jsonify({'ok': False, 'error': 'Client not found'}), 404

    m = db.session.get(ChatMessage, message_id)
    if not m or m.role != 'assistant':
        return jsonify({'ok': False, 'error': 'Message not found'}), 404
    if m.applied_at:
        return jsonify({'ok': False, 'error': 'Already applied'}), 400
    if m.rejected_at:
        return jsonify({'ok': False, 'error': 'Already rejected'}), 400
    if not m.proposed_patch_json:
        return jsonify({'ok': False, 'error': 'No patch on this message'}), 400

    try:
        patch = json.loads(m.proposed_patch_json)
    except json.JSONDecodeError:
        return jsonify({'ok': False, 'error': 'Patch JSON is malformed'}), 500

    user_id = session.get('user_id')
    will = _get_or_create_active_will(client_id, user_id)

    # 1. Apply person actions first so we have person_ids to embed in step JSON
    person_id_by_role = {}
    for p in patch.pop('_persons', []):
        pid = ensure_person(
            client_id,
            p.get('name', ''),
            nric=p.get('nric', ''),
            address=p.get('address', ''),
            relationship=p.get('role', ''),
            dob=p.get('dob', ''),
            nationality=p.get('nationality', 'Malaysian'),
            document_id=p.get('document_id'),
        )
        if pid:
            person_id_by_role[p.get('role', '')] = pid

    # 2. Merge each step patch into the corresponding step JSON column
    step_attrs = {
        'step1': 'step1_data', 'step2': 'step2_data', 'step3': 'step3_data',
        'step4': 'step4_data', 'step5': 'step5_data', 'step6': 'step6_data',
        'step7': 'step7_data', 'step8': 'step8_data',
    }
    for step_key, attr in step_attrs.items():
        if step_key not in patch:
            continue
        new_partial = patch[step_key]
        try:
            current = json.loads(getattr(will, attr) or '{}')
        except json.JSONDecodeError:
            current = {}
        if isinstance(current, list) or isinstance(new_partial, list):
            # List-shaped steps: replace wholesale only if patch is a list
            merged = new_partial if isinstance(new_partial, list) else current
        else:
            merged = dict(current)
            merged.update(new_partial)
        # Embed Testator person_id if we just created/updated one
        if step_key == 'step1' and person_id_by_role.get('Testator'):
            merged['person_id'] = person_id_by_role['Testator']
        setattr(will, attr, json.dumps(merged))

    # 3. Sync Client header from step1 if testator name/nric were just set
    if 'step1' in patch:
        s1 = json.loads(will.step1_data or '{}')
        if s1.get('full_name'):
            client.full_name = s1['full_name']
        if s1.get('nric_passport'):
            client.nric_passport = s1['nric_passport']
        if s1.get('email'):
            client.email = s1['email']
        if s1.get('phone'):
            client.phone = s1['phone']
        will.title = f"Will of {s1.get('full_name', 'Unknown')}"

    m.applied_at = datetime.utcnow()
    m.applied_by = user_id
    m.target_will_id = will.id
    db.session.commit()

    return jsonify({
        'ok': True,
        'message': _serialise_chat_message(m),
        'will': _will_data_snapshot(will),
    })


@app.route('/api/chat/<client_id>/replan/<message_id>', methods=['POST'])
@login_required
def api_chat_replan(client_id, message_id):
    """Re-run the planner over an existing user message's attachments using
    already-classified Document data (no re-call to Claude vision). Deletes
    any prior assistant replies that followed this message and replaces them
    with a fresh one. Useful after planner-code upgrades."""
    from ai.chat_planner import plan_turn
    user_msg = db.session.get(ChatMessage, message_id)
    if not user_msg or user_msg.role != 'user':
        return jsonify({'ok': False, 'error': 'Not a user message'}), 404
    cs = db.session.get(ChatSession, user_msg.session_id)
    if not cs or cs.client_id != client_id:
        return jsonify({'ok': False, 'error': 'Wrong client'}), 403

    try:
        doc_ids = json.loads(user_msg.attachments_json or '[]')
    except (json.JSONDecodeError, TypeError):
        doc_ids = []
    docs = Document.query.filter(Document.id.in_(doc_ids)).all() if doc_ids else []

    KNOWN_KINDS = {'nric', 'property_title', 'property_spa', 'property_tax',
                   'property_transfer', 'utility_bill', 'bank_letter',
                   'bank_statement', 'insurance', 'epf_kwsp', 'vehicle', 'will', 'voice'}
    artifacts = []
    for doc in docs:
        try:
            extracted = json.loads(doc.extracted_data) if doc.extracted_data else None
        except (json.JSONDecodeError, TypeError):
            extracted = None
        kind = doc.category if doc.category in KNOWN_KINDS else 'other'
        artifacts.append({
            'document_id': doc.id, 'kind': kind,
            'confidence': 'high', 'extracted': extracted,
            'original_filename': doc.original_filename,
        })

    # Delete any assistant replies that came AFTER this user message
    n_deleted = (ChatMessage.query
                 .filter(ChatMessage.session_id == cs.id,
                         ChatMessage.role == 'assistant',
                         ChatMessage.created_at > user_msg.created_at)
                 .delete(synchronize_session=False))

    client = db.session.get(Client, cs.client_id)
    active_will = (Will.query.filter_by(client_id=client.id, status='draft')
                   .filter(Will.deleted_at.is_(None))
                   .order_by(Will.updated_at.desc()).first())
    from services.identity_walker import get_pending_ic_documents
    pending_ics = get_pending_ic_documents(client.id)
    recent_text = _gather_recent_chat_text(client.id)
    # On replan we want the planner to skip the "intake" stage — pass
    # empty artifacts so it goes straight to walk-through / next step.
    plan = plan_turn(user_msg.content or '', [], _will_data_snapshot(active_will),
                     pending_ics=pending_ics, recent_text=recent_text)

    asst_msg = ChatMessage(
        session_id=cs.id, role='assistant',
        content=plan.get('reply', ''),
        attachments_json=json.dumps(plan.get('focus_attachments') or []),
        clarifying_questions_json=json.dumps(plan.get('clarifying_questions', [])),
        proposed_patch_json=json.dumps(plan['proposed_patch']) if plan.get('proposed_patch') else None,
        advice_json=json.dumps(plan.get('advice', [])),
        target_will_id=active_will.id if active_will else None,
    )
    db.session.add(asst_msg)
    db.session.commit()
    return jsonify({'ok': True, 'message_id': asst_msg.id, 'replaced_replies': n_deleted})


@app.route('/api/legal-library/upload', methods=['POST'])
@login_required
def api_legal_library_upload():
    """Accept a PDF upload (multipart/form-data, field 'file' + optional 'slug')
    and save under data/legal_acts/<slug>.pdf for the legal_qa retrieval."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify({'ok': False, 'error': 'Empty file'}), 400
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({'ok': False, 'error': 'Only PDF accepted'}), 400
    slug = (request.form.get('slug') or f.filename[:-4]).lower()
    slug = re.sub(r'[^a-z0-9_]+', '_', slug).strip('_')
    if not slug:
        return jsonify({'ok': False, 'error': 'Invalid slug'}), 400
    folder = os.path.join(DATA_DIR, 'legal_acts')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{slug}.pdf")
    f.save(path)
    size_kb = os.path.getsize(path) // 1024
    return jsonify({'ok': True, 'slug': slug, 'size_kb': size_kb,
                    'path': f'data/legal_acts/{slug}.pdf'})


@app.route('/api/legal-library/list')
@login_required
def api_legal_library_list():
    """List currently-loaded acts."""
    from services.legal_library import list_available_acts
    return jsonify({'ok': True, 'acts': list_available_acts()})


@app.route('/library')
@login_required
def legal_library_page():
    """Admin UI to upload + view loaded Acts. Login required (any role)."""
    from services.legal_library import list_available_acts
    return render_template('legal_library.html', acts=list_available_acts())


@app.route('/api/legal-library/delete/<slug>', methods=['POST'])
@login_required
def api_legal_library_delete(slug):
    """Delete a single Act PDF from the library."""
    safe_slug = re.sub(r'[^a-z0-9_]+', '', slug.lower())
    if not safe_slug:
        return jsonify({'ok': False, 'error': 'Invalid slug'}), 400
    path = os.path.join(DATA_DIR, 'legal_acts', f"{safe_slug}.pdf")
    if not os.path.isfile(path):
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    try:
        os.remove(path)
        # Library content changed → cached answers may be stale. Purge.
        try:
            from ai.legal_qa import cache_clear_all
            cache_clear_all()
        except Exception:
            pass
        return jsonify({'ok': True})
    except OSError as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/legal-library/cache/stats')
@login_required
def api_legal_library_cache_stats():
    """Return how many cached answers we have, total hits served, and the
    top 10 most-asked questions. Helps quantify token savings."""
    try:
        from database import LegalQACache
        from sqlalchemy import func as _func
        total = db.session.query(_func.count(LegalQACache.id)).scalar() or 0
        total_hits = db.session.query(_func.coalesce(_func.sum(LegalQACache.hits), 0)).scalar() or 0
        top = (LegalQACache.query
               .order_by(LegalQACache.hits.desc(), LegalQACache.last_used_at.desc())
               .limit(10).all())
        return jsonify({
            'ok': True,
            'cached_questions': int(total),
            'total_cache_hits': int(total_hits),
            'estimated_tokens_saved': int(total_hits) * 1500,  # ~1.5k tokens per LLM call
            'top_questions': [{
                'question': r.question_text[:200],
                'hits': r.hits or 0,
                'mode': r.mode,
                'cited_act': r.cited_act,
                'last_used_at': r.last_used_at.isoformat() if r.last_used_at else None,
            } for r in top],
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/legal-library/cache/clear', methods=['POST'])
@login_required
def api_legal_library_cache_clear():
    """Admin: purge the legal Q&A cache (RAM + DB). Use after a big
    library update so stale answers don't keep getting served."""
    try:
        from ai.legal_qa import cache_clear_all
        n = cache_clear_all()
        return jsonify({'ok': True, 'deleted': int(n)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/chat/<client_id>/backfill-extractions', methods=['POST'])
@login_required
def api_chat_backfill_extractions(client_id):
    """Re-run the appropriate extractor on every Document for this client
    where extracted_data is empty (or only an error). Useful after fixing
    extractor bugs without re-uploading."""
    client = db.session.get(Client, client_id)
    if not client:
        return jsonify({'ok': False, 'error': 'Client not found'}), 404
    docs = Document.query.filter_by(client_id=client_id).all()
    done = []
    for doc in docs:
        if doc.category not in ('property_title', 'property_tax', 'bank_statement',
                                'vehicle', 'insurance', 'epf_kwsp', 'nric'):
            continue
        try:
            existing = json.loads(doc.extracted_data) if doc.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            existing = {}
        # Skip if already has substantive data (no error + at least one truthy field)
        if existing and not existing.get('error') and any(
            v for k, v in existing.items() if k not in ('error', 'raw') and v
        ):
            continue
        abs_path = os.path.join(UPLOAD_DIR, doc.file_path)
        if not os.path.isfile(abs_path):
            continue
        try:
            if doc.category == 'nric':
                from ai.ocr import extract_nric_data
                ext = extract_nric_data(abs_path)
            elif doc.category in ('property_title', 'property_tax'):
                from ai.property_extractor import extract_property_data
                ext = extract_property_data(abs_path, doc_type='general')
            elif doc.category == 'bank_statement':
                from ai.ocr import extract_asset_document
                ext = extract_asset_document(abs_path, asset_type='bank')
            elif doc.category == 'vehicle':
                from ai.ocr import extract_asset_document
                ext = extract_asset_document(abs_path, asset_type='vehicle')
            else:
                from ai.ocr import extract_asset_document
                ext = extract_asset_document(abs_path, asset_type='other')
            doc.extracted_data = json.dumps(ext)
            done.append({'id': doc.id, 'category': doc.category,
                         'filename': doc.original_filename})
            db.session.commit()
        except Exception as e:
            done.append({'id': doc.id, 'category': doc.category, 'error': str(e)})
    return jsonify({'ok': True, 'updated': len(done), 'details': done[:30]})


@app.route('/api/chat/<client_id>/clear', methods=['POST'])
@login_required
def api_chat_clear(client_id):
    """Delete every message in this client's chat (Documents are kept —
    they're owned by the Client, just unlinked from the gone messages)."""
    client = db.session.get(Client, client_id)
    if not client:
        return jsonify({'ok': False, 'error': 'Client not found'}), 404
    cs = (ChatSession.query
          .filter_by(client_id=client_id)
          .order_by(ChatSession.created_at.desc())
          .first())
    if not cs:
        return jsonify({'ok': True, 'deleted': 0})
    msg_ids = [m.id for m in ChatMessage.query.filter_by(session_id=cs.id).all()]
    if msg_ids:
        Document.query.filter(Document.chat_message_id.in_(msg_ids)).update(
            {Document.chat_message_id: None}, synchronize_session=False)
    n = ChatMessage.query.filter_by(session_id=cs.id).delete()
    db.session.commit()
    return jsonify({'ok': True, 'deleted': n})


@app.route('/api/chat/<client_id>/message/<message_id>', methods=['DELETE'])
@login_required
def api_chat_message_delete(client_id, message_id):
    """Delete a single chat message (and its assistant reply if it's a user
    message). Useful for cleaning up junk without nuking the whole thread."""
    m = db.session.get(ChatMessage, message_id)
    if not m:
        return jsonify({'ok': False, 'error': 'Message not found'}), 404
    cs = db.session.get(ChatSession, m.session_id)
    if not cs or cs.client_id != client_id:
        return jsonify({'ok': False, 'error': 'Wrong client'}), 403
    deleted_ids = [m.id]
    # If this is a user message, also delete the immediately following
    # assistant reply (so they remove as a pair the way they were created).
    if m.role == 'user':
        nxt = (ChatMessage.query
               .filter(ChatMessage.session_id == cs.id,
                       ChatMessage.created_at > m.created_at,
                       ChatMessage.role == 'assistant')
               .order_by(ChatMessage.created_at.asc()).first())
        if nxt:
            deleted_ids.append(nxt.id)
    Document.query.filter(Document.chat_message_id.in_(deleted_ids)).update(
        {Document.chat_message_id: None}, synchronize_session=False)
    ChatMessage.query.filter(ChatMessage.id.in_(deleted_ids)).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'ok': True, 'deleted': len(deleted_ids)})


@app.route('/api/chat/<client_id>/reject/<message_id>', methods=['POST'])
@login_required
def api_chat_reject(client_id, message_id):
    """Mark a proposed patch as rejected (no changes applied)."""
    m = db.session.get(ChatMessage, message_id)
    if not m or m.role != 'assistant':
        return jsonify({'ok': False, 'error': 'Message not found'}), 404
    if m.applied_at or m.rejected_at:
        return jsonify({'ok': False, 'error': 'Already resolved'}), 400
    m.rejected_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'message': _serialise_chat_message(m)})


# -- Directed chat helpers --------------------------------------------------

def _gather_recent_chat_text(client_id: str, max_chars: int = 8000) -> str:
    """Concat user-message content for this client's chat (oldest → newest).
    Used as context for role deduction (forwarded email bodies live here)."""
    cs = (ChatSession.query.filter_by(client_id=client_id)
          .order_by(ChatSession.created_at.desc()).first())
    if not cs:
        return ''
    msgs = (ChatMessage.query.filter_by(session_id=cs.id, role='user')
            .order_by(ChatMessage.created_at.asc()).all())
    out = []
    total = 0
    for m in msgs:
        c = m.content or ''
        if total + len(c) > max_chars:
            break
        out.append(c)
        total += len(c)
    return '\n\n'.join(out)


def _extract_whatsapp_context_for_file(body: str, filename: str) -> str:
    """Return text messages adjacent to this filename's attachment reference
    in an exported WhatsApp chat log.

    WhatsApp exports (iOS / Android) embed attachment lines like:
      [02/05/26, 13:52] Ahmad: ‎<attached: PHOTO-2026-05-02-13-52-35.jpg>
      [02/05/26, 13:52] Ahmad: IMG-20260502-WA0001.jpg (file attached)

    Clients send context BEFORE or AFTER images — both patterns are common:
      Pattern A — message then image:
        [time] Client: This is my property lot 127082, give to Sarah.
        [time] Client: <attached: photo.jpg>
      Pattern B — image then message:
        [time] Client: <attached: photo.jpg>
        [time] Client: This is my property, please give to daughter.
      Pattern C — multiple images with one surrounding message:
        [time] Client: All these are for lot 127082.
        [time] Client: <attached: photo1.jpg>
        [time] Client: <attached: photo2.jpg>   ← for this image, message is BEFORE

    We look up to 4 text lines BEFORE and up to 3 text lines AFTER, then
    return whichever side has more content (prefer before if equal).
    Returns '' if the filename isn't found (caller falls back to full body).
    """
    if not filename or not body:
        return ''
    fn_lower = filename.lower()
    body_lower = body.lower()
    idx = body_lower.find(fn_lower)
    if idx == -1:
        return ''

    def _is_attach_line(s: str) -> bool:
        sl = s.lower()
        return '<attached:' in sl or 'file attached' in sl

    # ── Look BEFORE the attachment reference ──────────────────────────────
    before_lines = body[:idx].split('\n')
    before_ctx = []
    for line in reversed(before_lines):
        stripped = line.strip()
        if not stripped:
            continue
        if _is_attach_line(stripped):
            # Skip other attachment lines but don't stop — the text we want
            # may be interspersed with other image sends (Pattern C).
            continue
        before_ctx.insert(0, stripped)
        if len(before_ctx) >= 4:
            break

    # ── Look AFTER the attachment reference ───────────────────────────────
    after_start = idx + len(filename)
    after_lines = body[after_start:].split('\n')
    after_ctx = []
    for line in after_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _is_attach_line(stripped):
            # Another attachment line — stop scanning (the text after this
            # belongs to that next image, not ours)
            break
        after_ctx.append(stripped)
        if len(after_ctx) >= 3:
            break

    # ── Pick the richer side (or combine if both non-empty) ───────────────
    if before_ctx and after_ctx:
        # Both sides have text — combine for maximum context
        combined = before_ctx + after_ctx
        return '\n'.join(combined)
    return '\n'.join(before_ctx or after_ctx)


_CONFIRM_TOKENS = ('yes', 'confirm', 'correct', 'ok ', 'okay', 'yep', 'yeah', 'true', 'right')
_SKIP_TOKENS = ('skip', 'later', 'pass')
_DELETE_TOKENS = ('delete', 'remove', 'wrong', 'discard', 'trash', 'irrelevant', 'unrelated')


def _dedupe_ic_against_existing(client_id: str, doc, extracted: dict) -> bool:
    """If `doc` is an IC whose extracted name OR NRIC matches another nric
    Document for this client, mark `doc` as 'duplicate' and return True.
    Caller should skip emitting this doc as an artifact.

    Match by NRIC (most reliable) OR by name (in case NRIC was unreadable).
    """
    if not extracted:
        return False
    name = (extracted.get('full_name') or '').strip().upper()
    nric = (extracted.get('nric_number') or '').strip()
    if not name and not nric:
        return False
    siblings = Document.query.filter(
        Document.client_id == client_id,
        Document.category == 'nric',
        Document.id != doc.id,
    ).all()
    for sib in siblings:
        try:
            sib_ex = json.loads(sib.extracted_data) if sib.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            sib_ex = {}
        sib_name = (sib_ex.get('full_name') or '').strip().upper()
        sib_nric = (sib_ex.get('nric_number') or '').strip()
        if (nric and sib_nric and nric == sib_nric) or (name and sib_name and name == sib_name):
            doc.category = 'duplicate'
            doc.description = f'(duplicate of {sib.original_filename or sib.id[:8]})'
            return True
    return False


def _try_delete_pending_identity(client_id: str, user_text: str):
    """If user replies with a delete keyword, bulk-delete every Document
    that shares the focused IC's extracted name (re-uploads of the same
    person create multiple docs; one 'delete' should remove them all so
    the walk-through doesn't loop on duplicates).
    Returns {'name', 'action': 'deleted', 'count'} or None."""
    if not user_text:
        return None
    from services.identity_walker import get_pending_ic_documents
    text_lower = user_text.lower().strip()
    words = set(re.findall(r'\b[a-z]+\b', text_lower))
    if not any(t in words for t in _DELETE_TOKENS):
        return None
    pending = get_pending_ic_documents(client_id)
    if not pending:
        return None
    target = pending[0]
    ex = target['extracted'] or {}
    name = (ex.get('full_name') or '').strip()
    nric = (ex.get('nric_number') or '').strip()

    # Bulk delete: any nric Document where EITHER name OR nric matches.
    # NRIC catches the case where one upload has unreadable name but
    # the same IC number — those would otherwise re-appear in walk-through.
    target_name = name.upper()
    all_nric = Document.query.filter_by(client_id=client_id, category='nric').all()
    count = 0
    for d in all_nric:
        try:
            exd = json.loads(d.extracted_data) if d.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            exd = {}
        d_name = (exd.get('full_name') or '').strip().upper()
        d_nric = (exd.get('nric_number') or '').strip()
        match = (target_name and d_name and target_name == d_name) or \
                (nric and d_nric and nric == d_nric)
        if match:
            d.category = 'deleted'
            d.description = '(removed by user from chat walk-through)'
            count += 1
    if count == 0:
        # No name or NRIC to match on — just delete this single document
        doc = db.session.get(Document, target['document_id'])
        if doc:
            doc.category = 'deleted'
            doc.description = '(removed by user from chat walk-through)'
            count = 1
    db.session.commit()
    label = name or target.get('original_filename', 'this document')
    return {'name': label, 'action': 'deleted', 'count': count}


def _current_stage_num(client_id: str, will) -> int:
    """Numeric counterpart of _current_stage_label — the wizard step
    number the chat planner is currently working on. Used by the chat's
    right-pane snapshot so the "current" indicator pulses on the RIGHT
    step (e.g. Step 6 Specific Gifts), not the first empty optional step
    (Step 4 Guardians is optional and often skipped — the snapshot used
    to glow it forever).

    Mirrors the planner's stage ordering in ai/chat_planner.plan_turn.
    """
    from services.identity_walker import get_pending_ic_documents
    if get_pending_ic_documents(client_id):
        return 1
    if not will:
        return 1
    try:
        s1 = json.loads(will.step1_data) if will.step1_data else {}
        s2 = json.loads(will.step2_data) if will.step2_data else {}
        s4 = json.loads(will.step4_data) if will.step4_data else []
        s5 = json.loads(will.step5_data) if will.step5_data else []
        s6 = json.loads(will.step6_data) if will.step6_data else {}
        completed = json.loads(will.completed_steps or '[]')
    except (json.JSONDecodeError, TypeError):
        s1, s2, s4, s5, s6, completed = {}, {}, [], [], {}, []
    if not isinstance(completed, list):
        completed = []
    if not (s1 or {}).get('full_name'):
        return 2  # Step 2 Testator
    n_exec = len((s2 or {}).get('executors') or [])
    # Asset inventory phase happens BEFORE executor in the new flow —
    # snapshot it as Step 6 (Specific Gifts) so the writer knows that's
    # what the chat is reviewing.
    if 'assets_confirmed' not in completed:
        return 6
    if n_exec < 2:
        return 3  # Step 3 Executors
    if not isinstance(s4, list) or len(s4) == 0:
        return 5  # Step 5 Beneficiaries
    if isinstance(s5, list) and any(isinstance(g, dict) and g.get('document_id') for g in s5):
        return 6  # Step 6 mid-assignment
    if not s6 or not (s6.get('beneficiaries') or s6.get('residuary_beneficiary_name')):
        return 7  # Step 7 Residuary
    return 10  # done — Generate


def _current_stage_label(client_id: str, will) -> str:
    """Short human-readable label for the planner's current stage. Used by
    the Q&A nudge to remind the user where to come back to."""
    from services.identity_walker import get_pending_ic_documents
    if get_pending_ic_documents(client_id):
        return "Step 1: Identity walk-through"
    if not will:
        return "Step 1: setup"
    try:
        s2 = json.loads(will.step2_data) if will.step2_data else {}
        s4 = json.loads(will.step4_data) if will.step4_data else []
        completed = json.loads(will.completed_steps or '[]')
    except (json.JSONDecodeError, TypeError):
        s2, s4, completed = {}, [], []
    if not isinstance(completed, list):
        completed = []
    n_exec = len((s2 or {}).get('executors') or [])
    if 'assets_confirmed' not in completed:
        return "Step 6: Asset inventory review"
    if n_exec < 2:
        return f"Step 3: {'main' if n_exec == 0 else 'substitute'} Executor"
    if not isinstance(s4, list) or len(s4) == 0:
        return "Step 5: Confirm Beneficiaries"
    return "Step 6: Specific Gifts"


def _try_save_beneficiaries(client_id: str, user_text: str):
    """Step 5 (Beneficiaries) handler. Accepts:
      - 'yes' / 'confirm' → save the auto-suggested likely list
      - 'remove X' / 'remove X and Y' → save likely list minus X (by name OR
        by relationship word, e.g. 'remove sister in law')
      - 'only X, Y' / 'just X and Y' → save only X and Y
    Returns {'name', 'role', 'kind'} or None."""
    if not user_text:
        return None
    will = (Will.query.filter_by(client_id=client_id, status='draft')
            .filter(Will.deleted_at.is_(None))
            .order_by(Will.updated_at.desc()).first())
    if not will:
        return None
    try:
        s4 = json.loads(will.step4_data) if will.step4_data else []
    except (json.JSONDecodeError, TypeError):
        s4 = []
    if not isinstance(s4, list):
        s4 = []
    if len(s4) > 0:
        return None
    try:
        s2 = json.loads(will.step2_data) if will.step2_data else {}
        if not isinstance(s2, dict):
            s2 = {}
    except (json.JSONDecodeError, TypeError):
        s2 = {}
    if len(s2.get('executors') or []) == 0:
        return None

    BENEFICIARY_RELS = {
        'spouse', 'wife', 'husband', 'son', 'daughter', 'father', 'mother',
        'brother', 'sister', 'grandson', 'granddaughter', 'beneficiary',
        'stepson', 'stepdaughter', 'adopted son', 'adopted daughter',
    }
    persons = Person.query.filter_by(client_id=client_id).all()
    eligible = [p for p in persons
                if (p.relationship or '').lower() not in ('testator', 'witness')]

    text_lower = user_text.lower().strip()
    words = set(re.findall(r'\b[a-z\-]+\b', text_lower))

    def _match_persons(phrase):
        """Find Persons whose name OR relationship matches the phrase."""
        out = []
        ph = phrase.strip().lower().replace('-', ' ')
        if not ph:
            return out
        for p in eligible:
            nm = (p.full_name or '').lower()
            rel = (p.relationship or '').lower().replace('-', ' ')
            if (nm and (ph in nm or any(part in nm for part in ph.split() if len(part) > 2))) \
               or (rel and ph in rel):
                out.append(p)
        return out

    def _serialize(p_list):
        return [{
            'full_name': p.full_name, 'nric_passport': p.nric_passport or '',
            'address': p.address or '', 'relationship': p.relationship or '',
            'person_id': p.id,
        } for p in p_list]

    final = None
    # 1) ONLY/JUST X — explicit list
    only_m = re.search(r'\b(?:only|just|keep)\s+(.+)', text_lower)
    if only_m:
        names_text = only_m.group(1)
        # Split by commas, 'and'
        parts = re.split(r',|\band\b', names_text)
        keep = []
        seen = set()
        for part in parts:
            for p in _match_persons(part):
                if p.id not in seen:
                    keep.append(p); seen.add(p.id)
        if keep:
            final = keep

    # 2) REMOVE X — drop named/related people from the likely list
    if final is None and 'remove' in words:
        # Extract everything after 'remove'
        rem_m = re.search(r'\bremove\b\s+(.+?)(?:\.|$)', text_lower)
        if rem_m:
            remove_text = rem_m.group(1)
            parts = re.split(r',|\band\b', remove_text)
            exclude_ids = set()
            for part in parts:
                for p in _match_persons(part):
                    exclude_ids.add(p.id)
            # Build likely list minus excluded
            likely = []
            for p in eligible:
                rel = (p.relationship or '').lower()
                if p.id in exclude_ids:
                    continue
                if rel in BENEFICIARY_RELS or not rel or 'in-law' in rel:
                    likely.append(p)
            final = likely

    # 3) Plain confirm
    if final is None and any(t in words for t in _CONFIRM_TOKENS):
        likely = []
        for p in eligible:
            rel = (p.relationship or '').lower()
            if rel in BENEFICIARY_RELS or not rel or 'in-law' in rel:
                likely.append(p)
        final = likely

    if not final:
        return None
    will.step4_data = json.dumps(_serialize(final))
    db.session.commit()
    names = ', '.join(p.full_name for p in final)
    return {'name': names, 'role': f'{len(final)} beneficiaries', 'kind': 'beneficiaries'}


def _try_handle_property_fill(client_id: str, user_text: str):
    """Let the writer manually supply missing property fields directly in chat.

    Accepted formats (all case-insensitive):
      address: No. 22, Jalan Rimbun, Seri Alam
      daerah: Johor Bahru
      negeri: Johor
      mukim: Plentong
      lot: 127082
      title: HS(D) 251041
      lot_number: 127082
      title_number: HS(D) 251041
      property fill      ← bare "property fill" → show a prompt template

    Also handles the trigger button value 'property fill' by returning a
    prompt text that tells the writer how to type the values.
    """
    if not user_text:
        return None
    t = user_text.strip().lower()

    # ── Bare trigger — show instructions ──────────────────────────────
    if t == 'property fill':
        from services.gift_walker import get_pending_gift_documents
        pend = get_pending_gift_documents(client_id)
        props = pend.get('property') or []
        # Find the first non-inventoried property
        target_ex = {}
        for p in props:
            if not (p.get('extracted') or {}).get('_inventoried'):
                target_ex = p.get('extracted') or {}
                break
        _FIELD_LABELS = [
            ('property_address', 'address'),
            ('title_number',     'title'),
            ('lot_number',       'lot'),
            ('mukim',            'mukim'),
            ('daerah',           'daerah'),
            ('negeri',           'negeri'),
        ]
        missing = [(fld, kw) for fld, kw in _FIELD_LABELS
                   if not (target_ex.get(fld) or '').strip()]
        if not missing:
            return {'name': 'all fields complete', 'role': 'property_fill_noop', 'kind': 'property_fill'}
        lines = ['Type the missing field(s) directly — one per message or all at once:']
        for fld, kw in missing:
            lines.append(f"  `{kw}: <value>`   e.g. `{kw}: ...`")
        return {
            'name': 'fill prompt shown',
            'role': 'property_fill_prompt',
            'kind': 'property_fill',
            'reply_override': '\n'.join(lines),
        }

    # ── Field assignment: "daerah: Johor Bahru" ──────────────────────
    _FIELD_MAP = {
        'address':         'property_address',
        'property_address': 'property_address',
        'title':           'title_number',
        'title_number':    'title_number',
        'lot':             'lot_number',
        'lot_number':      'lot_number',
        'mukim':           'mukim',
        'daerah':          'daerah',
        'negeri':          'negeri',
        'state':           'negeri',
        'district':        'daerah',
        'area':            'area',
    }
    # Match "keyword: value" at the start of the message
    m = re.match(r'^([a-z_]+)\s*:\s*(.+)$', t, re.IGNORECASE)
    if not m:
        return None
    kw = m.group(1).strip().lower()
    raw_val = user_text[m.start(2):].strip()  # preserve original casing
    field = _FIELD_MAP.get(kw)
    if not field:
        return None

    # Find focused property doc
    from services.gift_walker import get_pending_gift_documents
    pend = get_pending_gift_documents(client_id)
    props = pend.get('property') or []
    target = next((p for p in props if not (p.get('extracted') or {}).get('_inventoried')), None)
    if not target:
        return None

    doc = db.session.get(Document, target['document_id'])
    if not doc:
        return None
    try:
        ex = json.loads(doc.extracted_data) if doc.extracted_data else {}
    except (json.JSONDecodeError, TypeError):
        ex = {}

    old_val = (ex.get(field) or '').strip()
    ex[field] = raw_val.strip()
    ex.setdefault('_manually_edited', [])
    if isinstance(ex['_manually_edited'], list):
        ex['_manually_edited'].append(f'{field}={raw_val.strip()[:60]}')

    try:
        doc.extracted_data = json.dumps(ex)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return None

    label = raw_val.strip()[:60]
    return {
        'name': f'{field} → {label}',
        'role': f'manual edit{"" if not old_val else f" (was: {old_val[:40]})"}',
        'kind': 'property_fill',
    }


def _try_handle_ownership(client_id: str, user_text: str):
    """Handle 'ownership: sole' / 'ownership: joint 1/2' commands.

    These are typed or tapped from the property card to confirm whether
    the testator owns the property solely or jointly, and if jointly
    what their undivided share is.

    Accepted formats (case-insensitive):
      ownership: sole
      ownership: joint 1/2
      ownership: joint 50%
      ownership: 1/2         ← bare share implies joint
      ownership: 1/3
    """
    if not user_text:
        return None
    t = user_text.strip()
    m = re.match(r'^ownership\s*:\s*(.+)$', t, re.IGNORECASE)
    if not m:
        return None
    val = m.group(1).strip().lower()

    # Determine type and share from the value
    if val == 'sole' or 'sole' in val:
        new_type  = 'sole'
        new_share = ''
    else:
        new_type = 'joint'
        # Extract the fraction/percentage: "joint 1/2", "1/2", "50%", "joint 50%"
        sh_m = re.search(r'(\d+/\d+|\d+\s*%)', val)
        new_share = sh_m.group(1).strip() if sh_m else ''

    # Find the current pending property
    from services.gift_walker import get_pending_gift_documents
    pend = get_pending_gift_documents(client_id)
    props = pend.get('property') or []
    target = next((p for p in props if not (p.get('extracted') or {}).get('_inventoried')), None)
    if not target:
        return None

    doc = db.session.get(Document, target['document_id'])
    if not doc:
        return None
    try:
        ex = json.loads(doc.extracted_data) if doc.extracted_data else {}
    except (json.JSONDecodeError, TypeError):
        ex = {}

    ex['ownership_type']  = new_type
    ex['ownership_share'] = new_share
    ex.setdefault('_manually_edited', [])
    if isinstance(ex['_manually_edited'], list):
        ex['_manually_edited'].append(f'ownership={new_type}{"/" + new_share if new_share else ""}')

    try:
        doc.extracted_data = json.dumps(ex)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return None

    label = 'Sole owner' if new_type == 'sole' else f'Joint owner — {new_share or "(share TBC)"}'
    return {'name': label, 'role': 'ownership_confirmed', 'kind': 'property_fill'}


def _try_handle_encumbrance(client_id: str, user_text: str):
    """Handle 'encumbered: yes / no / charge / caveat' commands.

    The Malaysian will template needs to know whether a property is clean
    or encumbered so the correct clause can be drafted:
      - Clean: straightforward gift clause
      - Charge: add direction to Executor to discharge bank charge
      - Caveat: add direction to apply to withdraw private caveat

    Accepted formats (case-insensitive):
      encumbered: yes          ← generic 'yes' — writer still types details
      encumbered: no
      encumbered: clean
      encumbered: charge       ← bank loan / mortgage
      encumbered: caveat       ← private caveat
    """
    if not user_text:
        return None
    t = user_text.strip()
    m = re.match(r'^encumbered\s*:\s*(.+)$', t, re.IGNORECASE)
    if not m:
        return None
    val = m.group(1).strip().lower()

    if val in ('no', 'clean', 'false', 'none'):
        enc_confirmed    = False
        enc_type         = ''
    elif val == 'charge' or 'charge' in val or 'mortgage' in val or 'loan' in val:
        enc_confirmed    = True
        enc_type         = 'charge'
    elif val == 'caveat' or 'caveat' in val or 'caveatan' in val:
        enc_confirmed    = True
        enc_type         = 'caveat'
    else:
        # Generic 'yes' — mark confirmed but leave type for writer to fill
        enc_confirmed    = True
        enc_type         = 'other'

    from services.gift_walker import get_pending_gift_documents
    pend = get_pending_gift_documents(client_id)
    props = pend.get('property') or []
    target = next((p for p in props if not (p.get('extracted') or {}).get('_inventoried')), None)
    if not target:
        return None

    doc = db.session.get(Document, target['document_id'])
    if not doc:
        return None
    try:
        ex = json.loads(doc.extracted_data) if doc.extracted_data else {}
    except (json.JSONDecodeError, TypeError):
        ex = {}

    ex['encumbrance_confirmed'] = enc_confirmed
    if enc_type:
        ex['encumbrance_type'] = enc_type
    ex.setdefault('_manually_edited', [])
    if isinstance(ex['_manually_edited'], list):
        ex['_manually_edited'].append(f'encumbrance={"clean" if not enc_confirmed else enc_type or "yes"}')

    try:
        doc.extracted_data = json.dumps(ex)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return None

    label = 'Clean (no charge/caveat)' if not enc_confirmed else f'Encumbered — {enc_type or "details TBC"}'
    return {'name': label, 'role': 'encumbrance_confirmed', 'kind': 'property_fill'}


def _try_handle_restart_gifts(client_id: str, user_text: str):
    """Handle 'restart gifts' / 'restart inventory' / 'reset gifts'.

    Clears the entire Step-6 walkthrough so the writer can redo it from
    scratch with the latest document grouping improvements:

      1. Removes all gifts from the active Will's step5_data
      2. Clears 'assets_confirmed' from completed_steps so the planner
         re-enters the asset-inventory loop
      3. Clears _inventoried and _skipped from all property/bank/vehicle
         Documents so they re-appear in the walk

    Does NOT touch the Documents themselves (OCR data is preserved) or
    any other Will step (beneficiaries, executors, etc.).
    """
    if not user_text:
        return None
    t = user_text.strip().lower()
    _RESTART_TOKENS = {'restart gifts', 'restart inventory', 'reset gifts',
                       'reset inventory', 'redo gifts', 'redo inventory',
                       'restart step 6', 'restart step6'}
    if t not in _RESTART_TOKENS:
        return None

    will = (Will.query.filter_by(client_id=client_id, status='draft')
            .filter(Will.deleted_at.is_(None))
            .order_by(Will.updated_at.desc()).first())
    if not will:
        return None

    # 1. Clear all saved gifts
    will.step5_data = '[]'

    # 2. Remove 'assets_confirmed' from completed_steps so planner re-enters loop
    try:
        completed = json.loads(will.completed_steps or '[]')
        if not isinstance(completed, list):
            completed = []
        completed = [c for c in completed if c != 'assets_confirmed']
        will.completed_steps = json.dumps(completed)
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return None

    # 3. Clear _inventoried + _skipped from all property/bank/vehicle docs
    kinds = ('property_title', 'property_spa', 'property_tax', 'property_transfer',
             'utility_bill', 'bank_letter', 'bank_statement', 'vehicle',
             'chat_inbox', 'other')
    docs = Document.query.filter(
        Document.client_id == client_id,
        Document.category.in_(kinds),
        Document.deleted_at.is_(None),
    ).all()
    changed = 0
    for d in docs:
        try:
            ex = json.loads(d.extracted_data) if d.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            ex = {}
        if ex.get('_inventoried') or ex.get('_skipped'):
            ex.pop('_inventoried', None)
            ex.pop('_skipped', None)
            d.extracted_data = json.dumps(ex)
            changed += 1
    if changed:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    return {
        'name': f'Step 6 reset ({changed} docs unlocked)',
        'role': 'gifts_restarted',
        'kind': 'gifts_restart',
        'reply_override': (
            f"♻️ **Step 6 reset.** All {changed} documents have been unlocked and "
            f"the gift list has been cleared.\n\n"
            f"I'll now walk you through all your properties, bank accounts, and "
            f"vehicles again — this time with improved grouping. Just reply to each card."
        ),
    }


def _try_handle_unlink_action(client_id: str, user_text: str):
    """Handle the support-doc picker:
      • 'unlink <doc_id>' → remove that doc from its parent property's
        group by clearing its property_hint + setting category='other',
        so it pops out of the cluster and lands in chat_inbox for the
        writer to manually re-attach.
      • 'unlink done' → clear the parent's _unlink_pending flag so the
        normal property card returns next turn.
    """
    if not user_text:
        return None
    t = user_text.strip().lower()
    if not (t.startswith('unlink ') or t == 'unlink done'):
        return None

    if t == 'unlink done':
        # Find the property currently in unlink mode and clear the flag
        from services.gift_walker import get_pending_gift_documents
        pend = get_pending_gift_documents(client_id)
        for p in (pend.get('property') or []):
            if (p.get('extracted') or {}).get('_unlink_pending'):
                doc = db.session.get(Document, p.get('document_id'))
                if not doc:
                    continue
                try:
                    ex = json.loads(doc.extracted_data) if doc.extracted_data else {}
                    ex.pop('_unlink_pending', None)
                    doc.extracted_data = json.dumps(ex)
                    db.session.commit()
                    return {'name': 'support docs', 'role': 'kept all',
                            'kind': 'unlink_done'}
                except Exception:
                    db.session.rollback()
                    return None
        return None

    # 'unlink <doc_id>'
    parts = t.split(maxsplit=1)
    if len(parts) != 2:
        return None
    doc_id = parts[1].strip()
    doc = db.session.get(Document, doc_id)
    if not doc or doc.client_id != client_id:
        return None
    # Move out of the property cluster: clear identifying fields so
    # _property_group_key returns '' and gift_walker treats it as orphan.
    try:
        ex = json.loads(doc.extracted_data) if doc.extracted_data else {}
    except (json.JSONDecodeError, TypeError):
        ex = {}
    ex['_unlinked_from_property'] = True
    for k in ('title_number', 'lot_number', 'mukim', 'property_address',
              'description', 'property_hint'):
        ex.pop(k, None)
    # Drop into chat_inbox so it doesn't pollute another cluster
    doc.category = 'chat_inbox'
    try:
        doc.extracted_data = json.dumps(ex)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return None
    return {'name': doc.original_filename or 'doc', 'role': 'unlinked',
            'kind': 'unlink_one'}


def _try_handle_inventory_action(client_id: str, user_text: str):
    """Per-asset walk-through actions issued by the inventory card:

      • 'inventory confirm' → the writer approves THIS asset; mark its
        Document as inventoried (extracted_data._inventoried = True) so
        it disappears from the walk and the next un-reviewed asset
        comes up.
      • 'inventory skip'    → leave the doc untouched but mark inventoried
        anyway so the walk can progress. Writer can revisit later.
      • 'inventory unlink'  → switch into "wrong supporting docs" picker
        mode — emit a list of the support docs with individual remove
        buttons. (Picker UI in next slice; for now stamp a flag the
        planner can read.)

    Each call resolves ONE focused asset (the same priority order the
    walk-through uses: property → bank → vehicle).
    """
    if not user_text:
        return None
    t = user_text.strip().lower()
    # Accept the four explicit prefixes AND the bare "delete"/"remove"
    # tokens that the 🗑 quick-reply on the property/bank/vehicle review
    # cards emits. Without the bare-token branch the delete button was a
    # no-op (it fell through to the IC-only delete handler, which returned
    # None because the user is past Step 1).
    _is_delete = (t == 'delete' or t == 'remove'
                  or t.startswith('delete ') or t.startswith('remove ')
                  or t.startswith('inventory delete')
                  or t.startswith('inventory remove'))
    # "inventory ownership …" and "inventory encumbered …" are sub-commands
    # issued by the guided confirm gate (ownership prompt / encumbrance prompt).
    _is_ownership_gate   = t.startswith('inventory ownership')
    _is_encumbrance_gate = t.startswith('inventory encumbered')
    if not (t.startswith('inventory confirm')
            or t.startswith('inventory skip')
            or t.startswith('inventory unlink')
            or _is_delete
            or _is_ownership_gate
            or _is_encumbrance_gate):
        return None

    from services.gift_walker import get_pending_gift_documents
    pend = get_pending_gift_documents(client_id)
    target = None
    target_kind = None
    for kind in ('property', 'bank', 'vehicle'):
        items = pend.get(kind) or []
        # Pick the first NOT-yet-inventoried one (matches what the chat
        # card showed the writer this turn).
        for it in items:
            if not (it.get('extracted') or {}).get('_inventoried'):
                target = it
                target_kind = kind
                break
        if target:
            break
    if not target:
        return None

    doc = db.session.get(Document, target['document_id'])
    if not doc:
        return None

    if _is_delete:
        # 🗑 Remove this property/bank/vehicle. Soft-delete the focused
        # Document AND any auto-grouped support docs so the writer doesn't
        # have to delete each piece individually. Also stamp _inventoried
        # so the walk-through skips past this slot on the next render.
        try:
            ex = json.loads(doc.extracted_data) if doc.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            ex = {}
        ex['_inventoried'] = True
        ex['_deleted_by_user'] = True
        try:
            doc.extracted_data = json.dumps(ex)
            doc.category = 'deleted'
            doc.description = (doc.description or '') + ' (removed via chat walk-through)'
            removed_count = 1
            # Cascade-delete any support docs the planner had grouped under
            # this asset (SPA / cukai / utility / extra geran pages — all
            # tied by property_hint or filename neighbourhood).
            for s in (target.get('support_docs') or []):
                sd = db.session.get(Document, s.get('document_id'))
                if not sd:
                    continue
                try:
                    sex = json.loads(sd.extracted_data) if sd.extracted_data else {}
                except (json.JSONDecodeError, TypeError):
                    sex = {}
                sex['_inventoried'] = True
                sex['_deleted_by_user'] = True
                sd.extracted_data = json.dumps(sex)
                sd.category = 'deleted'
                sd.description = (sd.description or '') + ' (cascade-removed with parent property)'
                removed_count += 1
            db.session.commit()
        except Exception:
            db.session.rollback()
            return None
        if target_kind == 'property':
            label = (ex.get('property_address') or ex.get('title_number')
                     or ex.get('property_hint') or 'property')
        elif target_kind == 'bank':
            label = (ex.get('bank_name') or 'bank account')
        else:
            label = (ex.get('description') or ex.get('vehicle_make') or 'vehicle')
        return {'name': label[:80],
                'role': f'removed ({removed_count} doc{"s" if removed_count != 1 else ""})',
                'kind': f'inventory_deleted_{target_kind}'}

    if t.startswith('inventory unlink'):
        # Stamp a transient marker so the planner shows the support-doc
        # picker on its next render. Real picker UI lands next slice.
        try:
            ex = json.loads(doc.extracted_data) if doc.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            ex = {}
        ex['_unlink_pending'] = True
        try:
            doc.extracted_data = json.dumps(ex)
            db.session.commit()
        except Exception:
            db.session.rollback()
        label = (ex.get('property_address') or ex.get('title_number')
                 or 'this asset')
        return {'name': label[:80], 'role': 'review supporting docs',
                'kind': 'inventory_unlink_pending'}

    # ── Address gate (property confirm only) ──────────────────────────
    # A Malaysian will property clause MUST describe the property — even
    # a vague address like "No. 5, Jalan Maju, Subang" is essential so
    # the Executor knows which property to deal with.
    #
    # If the writer taps "✅ Looks right" but the title has NO address,
    # prompt them to type it rather than silently saving a blank gift.
    # They can still override with "inventory confirm no address" or by
    # typing `address: leave blank` if they intentionally want it empty.
    _is_confirm = t.startswith('inventory confirm')
    _force_no_addr = ('no address' in t or 'leave blank' in t or
                      'leave address' in t or 'skip address' in t)
    if _is_confirm and target_kind == 'property' and not _force_no_addr:
        try:
            _ex_check = json.loads(doc.extracted_data) if doc.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            _ex_check = {}
        _addr = (_ex_check.get('property_address') or '').strip()
        if not _addr:
            # Address missing — ask: type it now, or confirm without.
            # Two quick-reply buttons keep it simple; no upfront card clutter.
            import json as _json
            _qr = [
                {'label': '✏️ Type address now', 'value': 'address: '},
                {'label': '🏚 No street address — confirm anyway', 'value': 'inventory confirm no address'},
            ]
            _qr_marker = f'<!--quickreplies:{_json.dumps(_qr)}-->'
            return {
                'name': 'address missing',
                'role': 'address_required',
                'kind': 'property_fill',
                'reply_override': (
                    "**⚠️ No property address found.**\n\n"
                    "Address is needed for the will clause so the Executor knows which property to deal with.\n\n"
                    "  • Type it: `address: No. 22, Jalan Rimbun, Taman Seri Alam, Johor`\n"
                    "  • Or tap below if this is agricultural / industrial land with no street address."
                    + _qr_marker
                ),
            }

    # ── Guided confirm gates (property only) ────────────────────────────
    # Sequential 3-step flow triggered by tapping Accept:
    #
    #   Step 1 — Ownership type:  2 buttons  (Sole / Joint)
    #   Step 1b — If Joint, share: 3 buttons  (1/2 / 1/3 / type manually)
    #   Step 2 — Encumbrance:     2 buttons  (Clean / Has loan or caveat)
    #
    # Sub-commands "inventory ownership …" and "inventory encumbered …" are
    # emitted by the quick-reply buttons. They save the answer then re-enter
    # gate logic to show the next prompt or finalise.
    _is_confirm = t.startswith('inventory confirm')

    if target_kind == 'property' and not t.startswith('inventory skip'):
        try:
            _gex = json.loads(doc.extracted_data) if doc.extracted_data else {}
        except (json.JSONDecodeError, TypeError):
            _gex = {}

        # ── Parse and save ownership answer ──────────────────────────
        if _is_ownership_gate:
            _ow_rest = t[len('inventory ownership'):].strip()
            if 'sole' in _ow_rest:
                _gex['ownership_type']  = 'sole'
                _gex['ownership_share'] = ''
            elif _ow_rest == 'joint':
                # "joint" with no share yet → show share picker (Step 1b)
                # Don't save yet; return the share prompt immediately.
                _qr_share = [
                    {'label': '1/2 share',      'value': 'inventory ownership joint 1/2'},
                    {'label': '1/3 share',      'value': 'inventory ownership joint 1/3'},
                    {'label': '✏️ Other — type', 'value': 'inventory ownership joint '},
                ]
                return {
                    'name': 'joint share',
                    'role': 'ownership_gate',
                    'kind': 'property_fill',
                    'reply_override': (
                        "**🤝 Joint ownership — what is the testator's share?**\n\n"
                        "_(Select the undivided share, or tap Other and type e.g. `inventory ownership joint 2/5`)_"
                        + f'<!--quickreplies:{json.dumps(_qr_share)}-->'
                    ),
                }
            else:
                # "joint 1/2", "joint 1/3", "joint 2/5" etc.
                _sh = re.search(r'(\d+/\d+|\d+\s*%)', _ow_rest)
                _gex['ownership_type']  = 'joint'
                _gex['ownership_share'] = _sh.group(1).strip() if _sh else _ow_rest.replace('joint', '').strip()
            _gex.setdefault('_manually_edited', [])
            _ow_tag = f"ownership={_gex['ownership_type']}"
            if _gex.get('ownership_share'):
                _ow_tag += '/' + _gex['ownership_share']
            if isinstance(_gex['_manually_edited'], list):
                _gex['_manually_edited'].append(_ow_tag)
            try:
                doc.extracted_data = json.dumps(_gex)
                db.session.commit()
            except Exception:
                db.session.rollback()

        # ── Parse and save encumbrance answer ────────────────────────
        if _is_encumbrance_gate:
            _enc_rest = t[len('inventory encumbered'):].strip()
            if _enc_rest in ('clean', 'no', 'none', 'false'):
                _gex['encumbrance_confirmed'] = False
                _gex['encumbrance_type']      = ''
            elif 'charge' in _enc_rest or 'mortgage' in _enc_rest or 'loan' in _enc_rest:
                _gex['encumbrance_confirmed'] = True
                _gex['encumbrance_type']      = 'charge'
            elif 'caveat' in _enc_rest:
                _gex['encumbrance_confirmed'] = True
                _gex['encumbrance_type']      = 'caveat'
            else:
                _gex['encumbrance_confirmed'] = True
                _gex['encumbrance_type']      = 'charge'  # default to charge if ambiguous
            try:
                doc.extracted_data = json.dumps(_gex)
                db.session.commit()
            except Exception:
                db.session.rollback()

        # ── Gate 1: Ownership type not yet set ───────────────────────
        _ow_type = (_gex.get('ownership_type') or '').strip().lower()
        if not _ow_type and (_is_confirm or _is_ownership_gate):
            _num_own = _gex.get('num_owners') or 1
            try:
                _num_own = int(_num_own)
            except (TypeError, ValueError):
                _num_own = 1
            _ocr_shares = (_gex.get('ownership_shares') or '').strip()
            _ocr_hint   = ''
            if _num_own > 1 or _ocr_shares:
                _ocr_hint = (
                    f"\n\n_OCR detected {_num_own} registered owners"
                    + (f" ({_ocr_shares})" if _ocr_shares else '')
                    + " — likely joint ownership._"
                )
            _qr_ow = [
                {'label': '👤 Sole owner',  'value': 'inventory ownership sole'},
                {'label': '🤝 Joint owner', 'value': 'inventory ownership joint'},
            ]
            return {
                'name': 'ownership',
                'role': 'ownership_gate',
                'kind': 'property_fill',
                'reply_override': (
                    "**Step 1 of 2 — Ownership**\n\n"
                    "Is the testator the **sole owner**, or is it **jointly owned** with another person?"
                    + _ocr_hint
                    + f'<!--quickreplies:{json.dumps(_qr_ow)}-->'
                ),
            }

        # ── Gate 2: Encumbrance not yet confirmed ────────────────────
        _enc_confirmed = _gex.get('encumbrance_confirmed')  # None = not yet answered
        if _enc_confirmed is None and (_is_confirm or _is_ownership_gate or _is_encumbrance_gate):
            _enc_ocr      = (_gex.get('encumbrance') or '').strip()
            _enc_type_ocr = (_gex.get('encumbrance_type') or '').strip().lower()
            _enc_hint     = ''
            if _enc_ocr or _enc_type_ocr:
                _enc_icon = '🏦' if _enc_type_ocr == 'charge' else '🚩'
                _enc_hint = f"\n\n_{_enc_icon} OCR detected: {_enc_ocr[:150]}_"
            _qr_enc = [
                {'label': '✅ Clean title',       'value': 'inventory encumbered clean'},
                {'label': '🏦 Has loan or caveat', 'value': 'inventory encumbered charge'},
            ]
            _enc_marker = f'<!--quickreplies:{json.dumps(_qr_enc)}-->'
            return {
                'name': 'encumbrance',
                'role': 'encumbrance_gate',
                'kind': 'property_fill',
                'reply_override': (
                    "**Step 2 of 2 — Encumbrance**\n\n"
                    "Is there a **bank loan or caveat** registered on this property?"
                    + _enc_hint
                    + "\n\n_(The Executor will be directed to settle the loan or withdraw the caveat.)_"
                    + _enc_marker
                ),
            }

    # 'inventory confirm' or 'inventory skip' → mark inventoried
    try:
        ex = json.loads(doc.extracted_data) if doc.extracted_data else {}
    except (json.JSONDecodeError, TypeError):
        ex = {}
    ex['_inventoried'] = True
    if t.startswith('inventory skip'):
        ex['_skipped'] = True
    try:
        doc.extracted_data = json.dumps(ex)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return None

    # Friendly ack label
    if target_kind == 'property':
        label = (ex.get('property_address') or ex.get('title_number')
                 or 'property')
    elif target_kind == 'bank':
        label = (ex.get('bank_name') or 'bank account')
    else:
        label = (ex.get('description') or ex.get('vehicle_make') or 'vehicle')

    action = 'skipped' if t.startswith('inventory skip') else 'reviewed'

    # Auto-stamp `assets_confirmed` if this was the LAST un-reviewed asset.
    # Saves the writer from having to type "confirm assets" at the end —
    # walk-through completion IS the confirmation.
    pend_after = get_pending_gift_documents(client_id)
    any_left = False
    for k in ('property', 'bank', 'vehicle'):
        for it in (pend_after.get(k) or []):
            if not (it.get('extracted') or {}).get('_inventoried'):
                any_left = True
                break
        if any_left:
            break
    if not any_left:
        will = (Will.query.filter_by(client_id=client_id, status='draft')
                .filter(Will.deleted_at.is_(None))
                .order_by(Will.updated_at.desc()).first())
        if will:
            try:
                completed = json.loads(will.completed_steps or '[]')
            except (json.JSONDecodeError, TypeError):
                completed = []
            if not isinstance(completed, list):
                completed = []
            if 'assets_confirmed' not in completed:
                completed.append('assets_confirmed')
                will.completed_steps = json.dumps(completed)
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()

    return {'name': label[:80], 'role': f'{action} & queued for wizard',
            'kind': f'inventory_{action}_{target_kind}'}


_ASSETS_CONFIRM_TOKENS = (
    'confirm assets', 'thats everything', "that's everything",
    'yes thats all', "yes that's all", 'all uploaded',
    'confirm', 'done', 'looks good', 'looks right',
    "i'll skip specific gifts", 'skip specific gifts', 'no specific gifts',
)
_ASSETS_MORE_TOKENS = (
    'i have more to upload', 'more to upload', 'add more',
    'upload more', 'not done', 'wait', 'one more',
)


def _try_handle_assets_gate(client_id: str, user_text: str):
    """Handle the asset-inventory confirmation step. The planner inserts
    a confirmation gate AFTER all uploads but BEFORE per-asset gifts to
    mirror the identity flow ('collect every IC then assign roles').

    - "confirm assets" / "yes that's everything" → stamp 'assets_confirmed'
      in the will's completed_steps so the planner advances to executor →
      beneficiaries → per-asset gift assignment.
    - "i have more to upload" → just acknowledge so the planner re-asks
      the inventory question on the next turn (giving the user a beat to
      attach more files).
    """
    if not user_text:
        return None
    t = user_text.strip().lower()
    if not t:
        return None
    will = (Will.query.filter_by(client_id=client_id, status='draft')
            .filter(Will.deleted_at.is_(None))
            .order_by(Will.updated_at.desc()).first())
    if not will:
        return None
    try:
        completed = json.loads(will.completed_steps or '[]')
    except (json.JSONDecodeError, TypeError):
        completed = []
    if not isinstance(completed, list):
        completed = []
    # Skip if already confirmed — the gate's gone, this isn't the right
    # handler for whatever they typed.
    if 'assets_confirmed' in completed:
        return None
    if any(tok in t for tok in _ASSETS_MORE_TOKENS):
        return {'name': 'asset upload', 'role': 'pending more uploads',
                'kind': 'assets_more'}
    if any(tok == t or t.startswith(tok) for tok in _ASSETS_CONFIRM_TOKENS):
        completed.append('assets_confirmed')
        will.completed_steps = json.dumps(completed)
        db.session.commit()
        return {'name': 'asset inventory', 'role': 'confirmed complete',
                'kind': 'assets_confirmed'}
    return None


def _mark_completed(will, key: str) -> bool:
    """Add `key` to will.completed_steps. Returns True if added, False if
    already present or will is None."""
    if not will:
        return False
    try:
        completed = json.loads(will.completed_steps or '[]')
        if not isinstance(completed, list):
            completed = []
        if key in completed:
            return False
        completed.append(key)
        will.completed_steps = json.dumps(completed)
        db.session.commit()
        return True
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return False


def _get_or_create_will(client_id: str):
    """Return the active draft Will for client, creating one if missing."""
    will = (Will.query.filter_by(client_id=client_id, status='draft')
            .filter(Will.deleted_at.is_(None))
            .order_by(Will.updated_at.desc()).first())
    if not will:
        will = Will(client_id=client_id, status='draft',
                    title='Draft Will', completed_steps='[]')
        db.session.add(will)
        db.session.commit()
    return will


def _try_handle_guardian_action(client_id: str, user_text: str):
    """Handle guardian step quick-replies and free-text names.

    Recognised inputs:
      'guardian skip'              → mark guardians_confirmed, no guardian set
      'guardian skip substitute'   → mark guardians_confirmed (primary already set)
      'guardian <name>'            → save primary guardian
      '<name>' at guardian step    → handled separately in plan context; not here

    Returns {name, role, kind} or None.
    """
    if not user_text:
        return None
    t = user_text.strip().lower()

    if t == 'guardian skip' or t == 'guardian none':
        will = _get_or_create_will(client_id)
        _mark_completed(will, 'guardians_confirmed')
        return {'name': 'guardians', 'role': 'skipped (no minor children / will set via wizard)',
                'kind': 'guardian_skipped'}

    if t == 'guardian skip substitute':
        will = _get_or_create_will(client_id)
        _mark_completed(will, 'guardians_confirmed')
        return {'name': 'guardians', 'role': 'primary set; no substitute',
                'kind': 'guardian_confirmed'}

    if not t.startswith('guardian '):
        return None
    # 'guardian <name>'
    name = user_text.strip()[9:].strip()  # strip 'guardian ' prefix (case-preserved)
    if not name or len(name) < 2:
        return None
    will = _get_or_create_will(client_id)
    try:
        s3 = json.loads(will.step3_data or '{}') if will.step3_data else {}
        if not isinstance(s3, dict):
            s3 = {}
    except (json.JSONDecodeError, TypeError):
        s3 = {}
    guardians = s3.get('guardians') or []
    has_primary = any(not g.get('is_substitute') for g in guardians)
    new_guardian = {'full_name': name.upper(), 'is_substitute': has_primary}
    guardians.append(new_guardian)
    s3['guardians'] = guardians
    try:
        will.step3_data = json.dumps(s3)
        if has_primary:
            # Substitute added → mark confirmed
            _mark_completed(will, 'guardians_confirmed')
        db.session.commit()
    except Exception:
        db.session.rollback()
        return None
    role = 'substitute guardian' if has_primary else 'primary guardian'
    return {'name': name.upper(), 'role': role, 'kind': 'guardian_saved'}


def _try_handle_trust_action(client_id: str, user_text: str):
    """Handle testamentary trust step quick-replies.

    Recognised:
      'trust yes'                   → set wants_trust=True, ask for trustee
      'trust skip'                  → mark trust_confirmed, trust_skipped=True
      'trust trustee same as executor' → copy executor name as trustee
      'trust trustee <name>'        → save trustee name
      'trust age <n>'               → save distribution age
      'trust age none'              → no age limit
    """
    if not user_text:
        return None
    t = user_text.strip().lower()
    if not (t.startswith('trust ') or t == 'trust yes' or t == 'trust skip'):
        return None
    will = _get_or_create_will(client_id)
    try:
        s7 = json.loads(will.step7_data or '{}') if will.step7_data else {}
        if not isinstance(s7, dict):
            s7 = {}
    except (json.JSONDecodeError, TypeError):
        s7 = {}

    if t == 'trust skip':
        s7['trust_skipped'] = True
        will.step7_data = json.dumps(s7)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        _mark_completed(will, 'trust_confirmed')
        return {'name': 'trust', 'role': 'skipped', 'kind': 'trust_skipped'}

    if t == 'trust yes':
        s7['wants_trust'] = True
        will.step7_data = json.dumps(s7)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return {'name': 'trust', 'role': 'setting up trust — enter trustee name',
                'kind': 'trust_wants'}

    if t == 'trust trustee same as executor':
        s2 = json.loads(will.step2_data or '{}') if will.step2_data else {}
        executors = (s2.get('executors') or []) if isinstance(s2, dict) else []
        exec_name = (executors[0].get('full_name') or '') if executors else ''
        s7['trustee_name'] = exec_name or 'Executor'
        will.step7_data = json.dumps(s7)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return {'name': exec_name or 'Executor', 'role': 'trustee (same as executor)',
                'kind': 'trust_trustee_set'}

    if t.startswith('trust trustee '):
        name = user_text.strip()[14:].strip().upper()
        s7['trustee_name'] = name
        will.step7_data = json.dumps(s7)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return {'name': name, 'role': 'trustee saved', 'kind': 'trust_trustee_set'}

    if t.startswith('trust age '):
        age_val = user_text.strip()[10:].strip().lower()
        if age_val in ('none', 'no limit', ''):
            s7['distribution_age'] = None
            role = 'no age limit'
        else:
            try:
                s7['distribution_age'] = int(re.sub(r'[^\d]', '', age_val) or '25')
                role = f'distribute at age {s7["distribution_age"]}'
            except ValueError:
                return None
        will.step7_data = json.dumps(s7)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        # If trustee + age both set, mark confirmed
        if s7.get('trustee_name') and 'distribution_age' in s7:
            _mark_completed(will, 'trust_confirmed')
        return {'name': 'trust', 'role': role, 'kind': 'trust_age_set'}

    return None


def _try_handle_others_action(client_id: str, user_text: str):
    """Handle 'other matters' step.

    Recognised:
      'others confirm'   → mark others_confirmed with default values
      'others skip'      → same as confirm with defaults
      'change <clause>'  → prompt is handled by chat_planner; here we just
                           look for 'change <clause>: <new text>' pattern
    """
    if not user_text:
        return None
    t = user_text.strip().lower()
    will = _get_or_create_will(client_id)
    try:
        s8 = json.loads(will.step8_data or '{}') if will.step8_data else {}
        if not isinstance(s8, dict):
            s8 = {}
    except (json.JSONDecodeError, TypeError):
        s8 = {}

    if t in ('others confirm', 'others skip', 'confirm defaults — proceed to review',
             'confirm defaults', 'skip — use all defaults'):
        s8['confirmed'] = True
        will.step8_data = json.dumps(s8)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        _mark_completed(will, 'others_confirmed')
        return {'name': 'other matters', 'role': 'confirmed with defaults', 'kind': 'others_confirmed'}

    # Pattern: 'change <clause>: <new value>'  e.g. "change funeral: Cremation preferred"
    m = re.match(r'^change\s+(.+?):\s*(.+)$', user_text.strip(), re.IGNORECASE)
    if m:
        clause_key = re.sub(r'\s+', '_', m.group(1).strip().lower())
        new_val = m.group(2).strip()
        s8[clause_key] = new_val
        will.step8_data = json.dumps(s8)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return {'name': clause_key, 'role': f'updated: {new_val[:60]}', 'kind': 'others_updated'}

    return None


def _try_handle_residuary_skip(client_id: str, user_text: str):
    """If user taps 'residuary skip', mark residuary_confirmed with no
    beneficiaries so the planner advances past step 7."""
    if not user_text:
        return None
    t = user_text.strip().lower()
    if t != 'residuary skip':
        return None
    will = _get_or_create_will(client_id)
    # Write an empty step6 so the planner sees it as 'done'
    try:
        s6 = json.loads(will.step6_data or '{}') if will.step6_data else {}
        if not isinstance(s6, dict):
            s6 = {}
        s6['skipped'] = True
        s6['beneficiaries'] = []
        will.step6_data = json.dumps(s6)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return None
    return {'name': 'residuary', 'role': 'skipped', 'kind': 'residuary_skipped'}


def _try_delete_pending_gift(client_id: str, user_text: str):
    """If user types 'delete' / 'remove' / 'wrong' at a Step-6 gift question
    (property/bank/vehicle), soft-delete the focused Document so it stops
    re-appearing in the walk-through. Returns {'name','action','count'} or
    None.

    This was missing — the chat planner was offering a "Delete" button on
    each property card but no handler picked it up, so taps did nothing
    and the same property kept being asked over and over.
    """
    if not user_text:
        return None
    text_lower = user_text.lower().strip()
    words = set(re.findall(r'\b[a-z]+\b', text_lower))
    if not any(t in words for t in _DELETE_TOKENS):
        return None
    from services.gift_walker import get_pending_gift_documents
    pend = get_pending_gift_documents(client_id)
    # Pick the first pending of any kind, in the same priority the planner
    # would ask about (property → bank → vehicle).
    target = None
    target_kind = None
    for kind in ('property', 'bank', 'vehicle'):
        items = pend.get(kind) or []
        if items:
            target = items[0]
            target_kind = kind
            break
    if not target:
        return None
    doc = db.session.get(Document, target['document_id'])
    if not doc:
        return None
    doc.category = 'deleted'
    doc.description = '(removed by user from chat walk-through)'
    # Also soft-delete any support docs that were grouped with this title
    # (e.g. SPA + cukai tanah images for the same lot). Otherwise they
    # could orphan and clutter the doc library.
    if target_kind == 'property':
        for s in (target.get('support_docs') or []):
            sdoc = db.session.get(Document, s.get('document_id'))
            if sdoc:
                sdoc.category = 'deleted'
                sdoc.description = '(removed with parent property)'
    db.session.commit()
    # Friendly label so the planner can acknowledge the delete cleanly
    ex = target.get('extracted', {}) or {}
    if target_kind == 'property':
        label = ex.get('property_address') or ex.get('title_number') or 'this property'
    elif target_kind == 'bank':
        label = (f"{ex.get('bank_name', '')} {ex.get('account_number', '')}"
                 .strip() or 'this bank account')
    else:
        label = (ex.get('registration_number') or ex.get('vehicle_make')
                 or 'this vehicle')
    return {'name': label[:100], 'action': 'deleted', 'count': 1,
            'kind': f'gift_{target_kind}_delete'}


def _try_save_property_gift(client_id: str, user_text: str):
    """Step 6 (Property gift) handler. Persists a parsed gift to the active
    Will's step5_data, marked with the source document_id so gift_walker
    stops re-asking. Returns {'name', 'role', 'kind'} or None.

    Accepts replies like:
      - "Joshua 100%"
      - "Esther 50%, Joshua 50%"
      - "Wife"  (single name → defaults to 100%)
      - "skip" → mark this property as skipped (writes a sentinel gift entry
                 with no beneficiaries so it's not re-asked)
    """
    if not user_text:
        return None
    will = (Will.query.filter_by(client_id=client_id, status='draft')
            .filter(Will.deleted_at.is_(None))
            .order_by(Will.updated_at.desc()).first())
    if not will:
        return None
    # Must have at least one beneficiary saved (Step 5) before gifts make sense
    try:
        s4 = json.loads(will.step4_data or '[]')
    except (json.JSONDecodeError, TypeError):
        s4 = []
    if not s4:
        return None

    from services.gift_walker import get_pending_gift_documents, parse_beneficiary_shares
    pend = get_pending_gift_documents(client_id)
    pending_props = pend.get('property') or []
    if not pending_props:
        return None
    target = pending_props[0]
    doc_id = target['document_id']

    # Load existing gifts list
    try:
        gifts = json.loads(will.step5_data or '[]')
        if not isinstance(gifts, list):
            gifts = []
    except (json.JSONDecodeError, TypeError):
        gifts = []

    txt = user_text.strip().lower()
    # "skip" → save a sentinel so this property stops being asked
    if txt in ('skip', 'next', 'pass'):
        gifts.append({
            'document_id': doc_id,
            'kind': 'property',
            'skipped': True,
            'beneficiaries': [],
        })
        will.step5_data = json.dumps(gifts)
        db.session.commit()
        return {'name': target.get('extracted', {}).get('property_address', 'this property'),
                'role': 'skipped', 'kind': 'gift_skip'}

    # Parse beneficiary names + shares
    known_names = [p.get('full_name', '') for p in s4 if p.get('full_name')]
    # Also include relationship words → resolve to person
    parsed = parse_beneficiary_shares(user_text, known_names)
    if not parsed:
        # Try matching by relationship words (wife/son/daughter/etc.)
        REL_MAP = {
            'wife': 'spouse', 'husband': 'spouse', 'spouse': 'spouse',
            'son': 'son', 'daughter': 'daughter', 'father': 'father',
            'mother': 'mother', 'children': None, 'kids': None,
        }
        words = re.findall(r'\b[a-z\-]+\b', txt)
        matched_persons = []
        for w in words:
            target_rel = REL_MAP.get(w, w)
            if target_rel is None:
                # 'children' → all sons + daughters
                for p in s4:
                    if (p.get('relationship') or '').lower() in ('son', 'daughter'):
                        matched_persons.append(p)
            else:
                for p in s4:
                    rel = (p.get('relationship') or '').lower()
                    if rel == target_rel or rel == w:
                        matched_persons.append(p)
        # Dedupe
        seen_n = set()
        uniq = []
        for p in matched_persons:
            n = p.get('full_name', '').upper()
            if n and n not in seen_n:
                seen_n.add(n); uniq.append(p)
        if uniq:
            share = '100%' if len(uniq) == 1 else 'equal'
            parsed = [{'name': p['full_name'], 'share': share} for p in uniq]

    if not parsed:
        return None

    gifts.append({
        'document_id': doc_id,
        'kind': 'property',
        'property_address': (target.get('extracted', {}) or {}).get('property_address', ''),
        'title_number': (target.get('extracted', {}) or {}).get('title_number', ''),
        'beneficiaries': parsed,
    })
    will.step5_data = json.dumps(gifts)
    db.session.commit()

    desc = ', '.join(f"{b['name']} {b['share']}" for b in parsed)
    ex_t = (target.get('extracted', {}) or {})
    addr = ex_t.get('property_address', 'property')

    # ── Probate-critical alert ──────────────────────────────────────
    # Even though the gift is now saved, the lawyer still needs Geran +
    # lot + mukim/daerah/negeri to file Borang 14A / Deed of Transmission
    # at the Land Office. If any are blank, raise the alert NOW (during
    # gift completion) so the writer chases the client before the will
    # is finalised — not after.
    alert_parts = []
    if not (ex_t.get('title_number') or '').strip():
        alert_parts.append('title number (Geran/PTD/HSD/HSM/Hakmilik)')
    if not (ex_t.get('lot_number') or '').strip():
        alert_parts.append('lot number')
    if not (ex_t.get('mukim') or '').strip():
        alert_parts.append('Mukim')
    if not (ex_t.get('daerah') or '').strip():
        alert_parts.append('Daerah')
    if not (ex_t.get('negeri') or '').strip():
        alert_parts.append('Negeri')
    alert = ''
    if alert_parts:
        alert = (
            "🚨 **Probate alert:** this gift is missing **"
            + ', '.join(alert_parts)
            + "** — the lawyer cannot file _Borang 14A / Deed of "
            "Transmission_ at the Pejabat Tanah without these. Please "
            "ask the client for a clearer Geran/Hakmilik scan before "
            "finalising the will."
        )

    return {'name': desc, 'role': f'gift of {addr[:50]}', 'kind': 'gift',
            'alert': alert}


def _try_save_executor(client_id: str, user_text: str):
    """If chat is at the executor stage and user replied with a name OR
    'yes' (to confirm the suggested candidate), persist to the active
    Will's step2_data. Returns {'name', 'role'} or None."""
    if not user_text:
        return None
    will = (Will.query.filter_by(client_id=client_id, status='draft')
            .filter(Will.deleted_at.is_(None))
            .order_by(Will.updated_at.desc()).first())
    if not will:
        return None
    try:
        s2 = json.loads(will.step2_data or '{}')
        if not isinstance(s2, dict):
            s2 = {}
    except (json.JSONDecodeError, TypeError):
        s2 = {}
    executors = s2.get('executors') or []
    if len(executors) >= 2:
        return None
    role = 'main' if len(executors) == 0 else 'substitute'

    text_lower = user_text.lower().strip()
    words = set(re.findall(r'\b[a-z]+\b', text_lower))

    # Skip — only valid for substitute
    if role == 'substitute' and any(t in words for t in _SKIP_TOKENS):
        s2['_substitute_skipped'] = True
        will.step2_data = json.dumps(s2)
        db.session.commit()
        return {'name': '(no substitute)', 'role': 'substitute', 'action': 'skipped'}

    # Find a Person matching either a name in the text OR (if 'yes')
    # the planner's suggested candidate.
    persons = Person.query.filter_by(client_id=client_id).all()
    chosen = None
    for p in persons:
        nm = (p.full_name or '').lower()
        if nm and nm in text_lower:
            chosen = p
            break

    if not chosen and any(t in words for t in _CONFIRM_TOKENS):
        # User said yes — apply the candidate the planner suggested
        from ai.chat_planner import find_executor_candidate
        identities = [{
            'id': p.id, 'full_name': p.full_name,
            'relationship': p.relationship or '',
            'date_of_birth': p.date_of_birth or '',
            'document_id': p.document_id or '',
        } for p in persons]
        recent = _gather_recent_chat_text(client_id)
        cand = find_executor_candidate(identities, executors, role, recent)
        if cand:
            chosen = next((p for p in persons if p.id == cand['person_id']), None)

    if not chosen:
        return None

    # Don't add the same person twice
    for e in executors:
        if e.get('person_id') == chosen.id:
            return None

    executors.append({
        'full_name': chosen.full_name,
        'nric_passport': chosen.nric_passport or '',
        'address': chosen.address or '',
        'relationship': chosen.relationship or '',
        'person_id': chosen.id,
        'is_substitute': (role == 'substitute'),
    })
    s2['executors'] = executors
    s2['executor_type'] = 'joint' if len(executors) > 1 else 'single'
    s2['trustee_data'] = s2.get('trustee_data') or {'same_as_executor': True, 'trustees': [{}]}
    will.step2_data = json.dumps(s2)
    db.session.commit()
    return {'name': chosen.full_name, 'role': f'{role} executor', 'kind': 'executor'}


def _try_assign_pending_identity(client_id: str, user_text: str):
    """If user_text plausibly assigns the next pending IC's role, create
    the Person and return {'name', 'role'}. Else return None."""
    if not user_text:
        return None
    from services.identity_walker import get_pending_ic_documents, parse_relationship
    from services.person_registry import ensure_person
    from ai.role_deducer import deduce_roles

    pending = get_pending_ic_documents(client_id)
    if not pending:
        return None
    target = pending[0]
    ex = target['extracted'] or {}
    name = (ex.get('full_name') or '').strip()
    if not name:
        return None

    text_lower = ' ' + user_text.lower().strip() + ' '
    if any((' ' + s + ' ') in text_lower for s in _SKIP_TOKENS):
        return None  # user said skip — leave for next turn

    rel = parse_relationship(user_text)
    chosen_role = None
    if rel:
        chosen_role = rel
    elif any((' ' + c + ' ') in text_lower for c in _CONFIRM_TOKENS):
        # User said yes/confirm — apply the deduced role
        recent = _gather_recent_chat_text(client_id)
        ded = deduce_roles(recent, [name])
        if ded.get(name):
            chosen_role = ded[name]['role']

    if not chosen_role:
        return None

    ensure_person(
        client_id, name,
        nric=(ex.get('nric_number') or ''),
        address=(ex.get('address') or ''),
        relationship=chosen_role,
        dob=(ex.get('date_of_birth') or ''),
        nationality=ex.get('nationality') or 'Malaysian',
        document_id=target['document_id'],
    )
    db.session.commit()
    return {'name': name, 'role': chosen_role, 'kind': 'identity'}


# -- Inbound email → chat (Postmark webhook) --------------------------------

_REPLY_QUOTE_RE = re.compile(
    r'(?:^On .{0,80}wrote:.*\Z)|(?:^>+.*$)|(?:^-{2,}\s*Original Message\s*-{2,}.*\Z)',
    re.MULTILINE | re.DOTALL,
)
_INBOUND_FILE_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'heic', 'heif', 'webp', 'bmp', 'pdf',
                       # Audio (voice notes forwarded from WhatsApp etc.)
                       'mp3', 'mp4', 'm4a', 'wav', 'webm', 'ogg', 'oga', 'mpga'}


def _strip_reply_quotes(text: str) -> str:
    """Trim 'On X, Y wrote:' tails and quoted '>' lines from email bodies."""
    if not text:
        return ''
    cleaned = _REPLY_QUOTE_RE.sub('', text)
    return '\n'.join(line for line in cleaned.splitlines() if line.strip()).strip()


def _extract_inbox_to(payload: dict):
    """Find the inbox-formatted recipient in a Postmark inbound payload."""
    from services.inbound_address import short_id_from_address
    for t in payload.get('ToFull') or []:
        addr = (t.get('Email') or '').strip()
        if short_id_from_address(addr):
            return addr
    raw = payload.get('To') or ''
    for part in raw.split(','):
        part = part.strip()
        if '<' in part and '>' in part:
            part = part[part.index('<') + 1: part.index('>')]
        if short_id_from_address(part):
            return part
    return None


@app.route('/api/inbound-email', methods=['POST'])
def api_inbound_email():
    """Wrapper that surfaces the underlying exception in JSON so failures
    are debuggable end-to-end (instead of a generic 500). Postmark retries
    on 5xx, so we still raise the original status."""
    try:
        return _api_inbound_email_impl()
    except Exception as e:
        traceback.print_exc()
        return jsonify({'ok': False, 'error': f'{type(e).__name__}: {e}'}), 500


def _api_inbound_email_impl():
    """Postmark inbound webhook — turns a forwarded email into a chat message.

    Two-phase: this handler does the FAST parts synchronously (auth, parse,
    save user message + raw attachments to disk) and returns 200 to Postmark
    inside ~1 second. The slow per-attachment AI work (vision classify, IC
    extract, voice transcribe, planner reply) runs in a background thread so
    Postmark's 10s webhook timeout doesn't kill emails with many photos.

    Auth: HTTP Basic, credentials in env vars POSTMARK_INBOUND_USER /
    POSTMARK_INBOUND_PASS. If either is unset, the endpoint refuses
    everything (so a half-configured deploy can't be abused).

    Sender allowlist: opt-in via env var INBOUND_ALLOWED_DOMAINS (comma-
    separated). Default = empty = accept any sender.
    """
    from services.inbound_address import find_client_by_address
    from uploads import MAX_FILE_SIZE

    expected_user = os.environ.get('POSTMARK_INBOUND_USER', '')
    expected_pass = os.environ.get('POSTMARK_INBOUND_PASS', '')
    if not expected_user or not expected_pass:
        return jsonify({'ok': False, 'error': 'Inbound webhook not configured'}), 503
    auth = request.authorization
    if not auth or auth.username != expected_user or auth.password != expected_pass:
        return jsonify({'ok': False, 'error': 'Unauthorized'}), 401

    payload = request.get_json(silent=True) or {}

    matched_to = _extract_inbox_to(payload)
    if not matched_to:
        return jsonify({'ok': True, 'ignored': 'no inbox address in To'}), 200

    subject = (payload.get('Subject') or '').strip()
    client = find_client_by_address(matched_to, hint_subject=subject)
    if not client:
        return jsonify({'ok': True, 'ignored': 'unknown client', 'to': matched_to}), 200

    from_email = (
        ((payload.get('FromFull') or {}).get('Email')) or payload.get('From') or ''
    ).strip().lower()
    if '<' in from_email and '>' in from_email:
        from_email = from_email[from_email.index('<') + 1: from_email.index('>')]
    allowed_raw = os.environ.get('INBOUND_ALLOWED_DOMAINS', '').strip()
    if allowed_raw and allowed_raw != '*':
        allowed = [d.strip().lower() for d in allowed_raw.split(',') if d.strip()]
        if not any(from_email.endswith('@' + d) for d in allowed):
            return jsonify({'ok': True, 'ignored': 'sender not allowlisted', 'from': from_email}), 200

    text_body = (payload.get('TextBody') or '').strip()
    if not text_body and payload.get('HtmlBody'):
        text_body = re.sub(r'<[^>]+>', ' ', payload['HtmlBody'])
        text_body = re.sub(r'\s+', ' ', text_body).strip()
    text_body = _strip_reply_quotes(text_body)

    attachments = payload.get('Attachments') or []
    if not text_body and not attachments:
        return jsonify({'ok': True, 'ignored': 'empty email'}), 200

    # Postmark puts the original email Date header in 'Date' field.
    # Include it so the AI can understand message sequencing when
    # the user forwards a WhatsApp chat (the body will contain the
    # WhatsApp timestamps, and the email Date gives arrival time).
    email_date = (payload.get('Date') or '').strip()
    body_with_meta = f"_(forwarded via email from {from_email})_\n\n"
    if subject:
        body_with_meta += f"**Subject:** {subject}\n\n"
    if email_date:
        body_with_meta += f"**Email date:** {email_date}\n\n"
    body_with_meta += text_body

    cs = _get_or_create_chat_session(client.id, user_id=None)
    user_msg = ChatMessage(
        session_id=cs.id, role='user', content=body_with_meta,
        attachments_json='[]',
    )
    db.session.add(user_msg)
    db.session.flush()

    # SYNC: write each attachment to disk + create Document row.
    # Skip vision classify / IC extract / Whisper here — the background
    # thread does those. Postmark sees a 200 within ~1s.
    folder_name = client.folder_name
    attachment_ids = []
    file_errors = []

    for att in attachments:
        name = att.get('Name', 'attachment')
        b64 = att.get('Content', '')
        if not b64:
            continue
        ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
        if ext not in _INBOUND_FILE_EXTS:
            file_errors.append(f"{name}: unsupported file type")
            continue
        try:
            data = base64.b64decode(b64)
        except Exception:
            file_errors.append(f"{name}: could not decode")
            continue
        if len(data) > MAX_FILE_SIZE:
            file_errors.append(f"{name}: larger than 20MB")
            continue

        ctype = (att.get('ContentType') or '').lower()
        # Provisional category — the async worker will retag based on classify
        # (or transcribe for audio). Save under chat_inbox/ for now.
        cat = 'chat_inbox'
        safe_basename = uuid.uuid4().hex[:12] + '.' + ext
        folder = os.path.join(UPLOAD_DIR, folder_name, 'documents', cat)
        os.makedirs(folder, exist_ok=True)
        abs_path = os.path.join(folder, safe_basename)
        with open(abs_path, 'wb') as f:
            f.write(data)
        rel_path = os.path.join(folder_name, 'documents', cat, safe_basename)

        doc = Document(
            client_id=client.id, chat_message_id=user_msg.id,
            filename=safe_basename, original_filename=name,
            file_path=rel_path, file_type=ctype,
            file_size=len(data), category=cat,
        )
        db.session.add(doc)
        db.session.flush()
        attachment_ids.append(doc.id)

    user_msg.attachments_json = json.dumps(attachment_ids)

    # If the email had any rejected attachments, surface that as an immediate
    # assistant note (no AI work needed) so the user sees something right away.
    if file_errors:
        early_note = ChatMessage(
            session_id=cs.id, role='assistant',
            content=("📎 Some attachments couldn't be saved:\n- " + "\n- ".join(file_errors)),
        )
        db.session.add(early_note)

    db.session.commit()

    # Spawn the slow processing in the background. The webhook returns NOW.
    threading.Thread(
        target=_process_inbound_message_async,
        args=(app, user_msg.id),
        daemon=True,
    ).start()

    return jsonify({
        'ok': True,
        'client_id': client.id,
        'message_id': user_msg.id,
        'attachments': len(attachment_ids),
        'queued_for_processing': True,
    })


def _process_inbound_message_async(app_obj, user_msg_id):
    """Background processing — runs after the webhook has returned 200.

    For each Document attached to the user_msg:
      - audio → Whisper transcribe
      - else  → vision classify (nric/property_title/...) + extract if IC
    Then call the planner over (text + voice transcripts + extracted artifacts)
    and save the assistant ChatMessage.
    """
    with app_obj.app_context():
        from ai.file_classifier import classify_file
        from ai.ocr import extract_nric_data
        from ai.chat_planner import plan_turn
        from ai.voice_transcription import transcribe, is_audio

        try:
            user_msg = db.session.get(ChatMessage, user_msg_id)
            if not user_msg:
                return
            cs = db.session.get(ChatSession, user_msg.session_id)
            client = db.session.get(Client, cs.client_id) if cs else None
            if not client:
                return

            doc_ids = []
            try:
                doc_ids = json.loads(user_msg.attachments_json or '[]')
            except (json.JSONDecodeError, TypeError):
                doc_ids = []

            artifacts = []
            voice_transcripts = []
            docs = (Document.query.filter(Document.id.in_(doc_ids)).all()
                    if doc_ids else [])

            # ── Batch-first grouping ──────────────────────────────────────
            # STEP 1: Analyse ALL images together BEFORE classifying individually.
            # This is the key insight: a customer sending 5 photos in one
            # WhatsApp message almost always means 5 pages of the same document.
            # We ask Claude to:
            #   (a) Infer relationships from overlapping identifiers (lot, title, acct)
            #   (b) Use the WhatsApp text (before/after images) as primary evidence
            #   (c) Return asset GROUPS — {image_indices, asset_kind, identifiers}
            # Each subsequent classify_file() call gets the group verdict as context,
            # so "page 4 of a blurry geran" is classified correctly instead of 'other'.
            from ai.file_classifier import classify_batch

            image_docs = [d for d in docs
                          if not (is_audio((d.file_type or '').lower())
                                  or is_audio(d.original_filename or ''))]
            batch_group_map: dict = {}   # doc index (into docs list) → group dict

            if len(image_docs) >= 2:
                try:
                    image_paths = [os.path.join(UPLOAD_DIR, d.file_path)
                                   for d in image_docs]
                    # Use the email body as message context for the batch analysis.
                    # WhatsApp text (lot numbers, beneficiary names) lives here.
                    batch_msg_ctx = (user_msg.content or '')[:600]
                    batch_result = classify_batch(image_paths, message_context=batch_msg_ctx)
                    # Build doc → group lookup keyed by original image_docs index
                    for grp in (batch_result.get('groups') or []):
                        for img_idx in (grp.get('image_indices') or []):
                            if 0 <= img_idx < len(image_docs):
                                batch_group_map[id(image_docs[img_idx])] = grp
                except Exception:
                    pass   # batch analysis failed — fall through to individual classify

            for doc in docs:
                abs_path = os.path.join(UPLOAD_DIR, doc.file_path)
                if not os.path.isfile(abs_path):
                    continue
                ctype = (doc.file_type or '').lower()
                is_voice = is_audio(ctype) or is_audio(doc.original_filename or '')

                if is_voice:
                    # Move to voice/ folder for tidiness
                    if doc.category != 'voice':
                        doc.category = 'voice'
                    transcript = transcribe(abs_path)
                    if transcript:
                        voice_transcripts.append(transcript)
                        doc.description = transcript[:500]
                        try:
                            doc.extracted_data = json.dumps({'transcript': transcript})
                        except (TypeError, ValueError):
                            pass
                    else:
                        doc.description = '(transcription failed or unavailable)'
                    artifacts.append({
                        'document_id': doc.id, 'kind': 'voice',
                        'confidence': 'high' if transcript else 'low',
                        'extracted': {'transcript': transcript} if transcript else None,
                        'original_filename': doc.original_filename,
                    })
                    db.session.commit()
                    continue

                # STEP 2: Classify this image individually, using the batch
                # group verdict as strong context (if available).
                group_ctx = batch_group_map.get(id(doc))
                classification = classify_file(abs_path, group_context=group_ctx)

                # If batch analysis says this image is property but the
                # individual classifier said 'other', trust the batch verdict —
                # it had all images plus the WhatsApp text to reason from.
                kind = classification.get('kind', 'other')
                if group_ctx and kind == 'other':
                    batch_kind = group_ctx.get('asset_kind', '')
                    if batch_kind and batch_kind != 'other':
                        kind = batch_kind
                        classification['kind'] = kind
                        classification['confidence'] = 'medium'
                        classification['reason'] = (
                            f'Reclassified from batch group analysis '
                            f'({group_ctx.get("summary", "")})'
                        )

                extracted = None
                try:
                    if kind == 'nric':
                        extracted = extract_nric_data(abs_path)
                    elif kind in ('property_title', 'property_spa', 'property_tax',
                                   'property_transfer'):
                        from ai.property_extractor import extract_property_data
                        extracted = extract_property_data(abs_path, doc_type='general')
                    elif kind in ('utility_bill', 'bank_letter'):
                        extracted = {}
                    elif kind == 'bank_statement':
                        from ai.ocr import extract_asset_document
                        extracted = extract_asset_document(abs_path, asset_type='bank')
                    elif kind == 'vehicle':
                        from ai.ocr import extract_asset_document
                        extracted = extract_asset_document(abs_path, asset_type='vehicle')
                    elif kind in ('insurance', 'epf_kwsp'):
                        from ai.ocr import extract_asset_document
                        extracted = extract_asset_document(abs_path, asset_type='other')
                except Exception as e:
                    extracted = {'error': str(e)}
                doc.category = kind if kind != 'other' else 'chat_inbox'
                doc.description = (classification.get('reason') or '')[:500] or None
                purpose = (classification.get('purpose') or '').strip()
                prop_hint = (classification.get('property_hint') or '').strip()
                will_relevant = classification.get('will_relevant', True)
                if extracted is None:
                    extracted = {}
                if purpose:
                    extracted['purpose'] = purpose[:300]
                if prop_hint:
                    extracted['property_hint'] = prop_hint[:300]

                # STEP 3: Cross-fill missing NLC fields from the batch group
                # identifiers. The batch analysis extracted lot/title/mukim
                # from the clearest image in the group — propagate to all pages.
                if group_ctx:
                    grp_idents = group_ctx.get('identifiers') or {}
                    prop_fields = ('lot_number', 'title_number', 'mukim',
                                   'daerah', 'negeri', 'property_address',
                                   'bank_name', 'account_number', 'reg_number')
                    for field in prop_fields:
                        if not (extracted.get(field) or '').strip():
                            val = (grp_idents.get(field) or '').strip()
                            if val:
                                extracted[field] = val
                                extracted.setdefault('_enriched_from', []).append(
                                    f'batch_group.{field}'
                                )
                    # Store beneficiary hint from WhatsApp text (e.g. "give to Sarah")
                    bh = (group_ctx.get('beneficiary_hint') or '').strip()
                    if bh and not extracted.get('_beneficiary_hint'):
                        extracted['_beneficiary_hint'] = bh
                # Store the text context that came WITH this image.
                # For WhatsApp-forwarded emails the body is a chat log:
                #   [time] Client: please add this property, lot 127082 to sarah
                #   [time] Client: <attached: PHOTO-2026-05-02-13-52-35.jpg>
                # We extract the lines immediately before THIS image's attachment
                # reference — that's the most specific context for this image.
                # Fall back to the full email body if no WhatsApp format found.
                try:
                    msg_body = (user_msg.content or '').strip()
                    wa_ctx = _extract_whatsapp_context_for_file(
                        msg_body, doc.original_filename or ''
                    )
                    if wa_ctx:
                        # WhatsApp-specific context found for this image
                        extracted['_message_context'] = wa_ctx[:800]
                        extracted['_context_source'] = 'whatsapp_preceding'
                    elif msg_body:
                        # No WhatsApp format — store the whole email body
                        extracted['_message_context'] = msg_body[:800]
                        extracted['_context_source'] = 'email_body'
                except Exception:
                    pass
                if kind == 'other' and not will_relevant:
                    try:
                        recent = _gather_recent_chat_text(client.id)
                        fname_stem = doc.original_filename.rsplit('.', 1)[0].lower()
                        asset_keywords = ('property', 'house', 'land', 'lot', 'geran',
                                          'bank', 'account', 'insurance', 'car', 'vehicle',
                                          'mukim', 'title', fname_stem)
                        if not any(kw in (recent or '').lower() for kw in asset_keywords):
                            extracted['_likely_irrelevant'] = True
                            extracted['_irrelevant_reason'] = (
                                'Classified as unrecognised document type and no matching '
                                'asset was mentioned in the chat.'
                            )
                    except Exception:
                        pass
                if extracted:
                    try:
                        doc.extracted_data = json.dumps(extracted)
                    except (TypeError, ValueError):
                        doc.extracted_data = None

                # Dedupe ICs by extracted name — if a previous Document
                # for this client already has the same name, mark this
                # new one as 'duplicate' and skip emitting as artifact.
                is_dup = False
                if kind == 'nric' and extracted and not extracted.get('error'):
                    is_dup = _dedupe_ic_against_existing(client.id, doc, extracted)

                if not is_dup:
                    artifacts.append({
                        'document_id': doc.id, 'kind': kind,
                        'confidence': classification.get('confidence', 'low'),
                        'extracted': extracted,
                        'original_filename': doc.original_filename,
                    })
                # Commit per-document so partial progress is visible to chat
                # polling — useful when there are many attachments.
                db.session.commit()

            # Reload user_msg in case its content changed (it didn't, but safe)
            user_msg = db.session.get(ChatMessage, user_msg_id)

            # Merge voice transcripts into the message body for the planner
            text = user_msg.content or ''
            if voice_transcripts:
                joined = '\n\n'.join(voice_transcripts)
                user_msg.content = text + f"\n\n_(voice transcript)_\n{joined}"
                text = user_msg.content
                db.session.commit()

            active_will = (Will.query.filter_by(client_id=client.id, status='draft')
                           .filter(Will.deleted_at.is_(None))
                           .order_by(Will.updated_at.desc()).first())
            from services.identity_walker import get_pending_ic_documents
            pending_ics = get_pending_ic_documents(client.id)
            recent_text = _gather_recent_chat_text(client.id)
            plan = plan_turn(text, artifacts, _will_data_snapshot(active_will),
                             pending_ics=pending_ics, recent_text=recent_text)

            asst_msg = ChatMessage(
                session_id=cs.id, role='assistant',
                content=plan.get('reply', ''),
                attachments_json=json.dumps(plan.get('focus_attachments') or []),
                clarifying_questions_json=json.dumps(plan.get('clarifying_questions', [])),
                proposed_patch_json=json.dumps(plan['proposed_patch']) if plan.get('proposed_patch') else None,
                advice_json=json.dumps(plan.get('advice', [])),
                target_will_id=active_will.id if active_will else None,
            )
            db.session.add(asst_msg)
            db.session.commit()
        except Exception:
            traceback.print_exc()
            try:
                db.session.rollback()
            except Exception:
                pass


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
            # Undivided share and ownership
            undivided = bool(request.form.get(f'gift_prop_undivided_{gi}'))
            testator_share = request.form.get(f'gift_prop_share_{gi}', '').strip() if undivided else ''
            ownership = 'joint' if undivided else request.form.get(f'gift_prop_ownership_{gi}', 'sole').strip()

            # Encumbrance
            encumbrance = request.form.get(f'gift_prop_encumbrance_{gi}', 'clean').strip()
            debt_source = request.form.get(f'gift_prop_debt_source_{gi}', 'residuary').strip() if encumbrance == 'encumbered' else ''

            # Split address fields
            prop_addr = request.form.get(f'gift_prop_address_{gi}', '').strip()
            postcode = request.form.get(f'gift_prop_postcode_{gi}', '').strip()
            city = request.form.get(f'gift_prop_city_{gi}', '').strip()
            state = request.form.get(f'gift_prop_state_{gi}', '').strip()
            country = request.form.get(f'gift_prop_country_{gi}', 'Malaysia').strip()

            # Strip postcode/city/state from address if already in separate fields
            # This prevents duplication when user selects from existing addresses
            clean_addr = prop_addr
            if postcode and city:
                # Remove trailing duplicates like ", 81100 JOHOR BAHRU, JOHOR, 81100 JOHOR BAHRU, JOHOR..."
                import re
                # Remove any repeated postcode+city+state patterns from address
                dup_pattern = re.compile(
                    r',\s*' + re.escape(postcode) + r'\s+' + re.escape(city) + r'(?:\s*,\s*' + re.escape(state) + r')?',
                    re.IGNORECASE
                )
                # Keep only the first occurrence (part of the original address), remove the rest
                matches = list(dup_pattern.finditer(clean_addr))
                if len(matches) > 1:
                    # Remove all but first match
                    for m in reversed(matches[1:]):
                        clean_addr = clean_addr[:m.start()] + clean_addr[m.end():]
                elif len(matches) == 1:
                    # If postcode/city/state are in separate fields, strip them from address
                    clean_addr = clean_addr[:matches[0].start()].rstrip(', ')

            property_details = {
                'property_address': clean_addr or prop_addr,
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
                'undivided_share': undivided,
                'testator_share': testator_share,
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

    # Discharge / lien clause for properties (default ON)
    om_data['discharge_clause_enabled'] = bool(request.form.get('discharge_clause_enabled'))
    om_data['discharge_placement'] = request.form.get('discharge_placement', 'per_property')

    # Testator satisfaction clause (default ON)
    om_data['testator_satisfaction_enabled'] = bool(request.form.get('testator_satisfaction_enabled'))

    # Translator / Interpreter attestation
    om_data['translator_enabled'] = bool(request.form.get('translator_enabled'))
    if om_data['translator_enabled']:
        om_data['translator_name'] = request.form.get('translator_name', '').strip()
        om_data['translator_nric'] = request.form.get('translator_nric', '').strip()
        om_data['translator_language'] = request.form.get('translator_language', '').strip()
        om_data['translator_address'] = request.form.get('translator_address', '').strip()

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
        has_versions=WillVersion.query.filter_by(will_id=session.get('will_id', '')).count() > 0 if session.get('will_id') else False,
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
            # Auto-approve when an approver views a generated will (the approver is the
            # lawyer — Kylie at Alan Tan & Associates — so an explicit submit-then-approve
            # round-trip is unnecessary friction). Only fires once: status must be 'generated'.
            if (will_record.status == 'generated'
                and getattr(g, 'perms', {}).get('canApprove')
                and getattr(g, 'user', None)):
                will_record.status = 'approved'
                will_record.approved_by = g.user.id
                will_record.approved_at = datetime.utcnow()
                will_record.approval_remarks = 'Auto-approved on preview by approver'
                db.session.commit()
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

        # Attach edit logs to versions: for each version, find edits made between
        # this version's creation and the next version's creation
        if versions and edit_logs:
            for vi, v in enumerate(versions):  # versions are desc by version_number
                v_start = v.created_at
                v_end = versions[vi - 1].created_at if vi > 0 else None  # next newer version
                v.edit_logs = [
                    el for el in edit_logs
                    if el.edited_at >= v_start and (v_end is None or el.edited_at < v_end)
                ]
                # Update display time to last edit time if edits exist
                if v.edit_logs:
                    v.last_edited_at = v.edit_logs[0].edited_at  # most recent edit (already desc)
                    v.last_edited_by = v.edit_logs[0].edited_by_name
                else:
                    v.last_edited_at = None
                    v.last_edited_by = None

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

@app.route('/download/verification-pdf')
@login_required
def download_verification_pdf():
    """Generate and download verification PDF with documents + field data."""
    from documents.verification_pdf import generate_verification_pdf

    client_id = session.get('client_id')
    if not client_id:
        flash('No client loaded.', 'error')
        return redirect(url_for('wizard_step', step=1))

    # Gather persons with documents
    persons = []
    for p in session.get('person_registry', []):
        person_record = db.session.get(Person, p.get('id'))
        if person_record:
            pdata = {k: getattr(person_record, k, '') for k in
                     ['full_name', 'nric_passport', 'nationality', 'date_of_birth',
                      'gender', 'address', 'relationship', 'document_id']}
            pdata['id'] = person_record.id
            persons.append(pdata)

    # Gather gifts with documents
    gifts = session.get('step5_gifts', [])

    # Build documents map: document_id -> file_path
    documents_map = {}
    doc_ids = [p['document_id'] for p in persons if p.get('document_id')]
    for g in gifts:
        for d in g.get('documents', []):
            did = d.get('document_id', '')
            # Extract document_id from URL if not set directly
            if not did and d.get('url'):
                url = d['url']
                # URL format: /api/documents/UUID
                parts = url.rstrip('/').split('/')
                if len(parts) >= 3:
                    did = parts[-1]
            if did:
                doc_ids.append(did)
                # Also store the mapping so verification PDF can find it
                d['document_id'] = did
    for doc_id in doc_ids:
        doc = db.session.get(Document, doc_id)
        if doc and doc.file_path:
            # file_path may be relative — prepend UPLOAD_DIR if not absolute
            fp = doc.file_path
            if not os.path.isabs(fp):
                fp = os.path.join(UPLOAD_DIR, fp)
            if os.path.exists(fp):
                documents_map[doc_id] = fp

    testator_name = session.get('step1', {}).get('full_name', 'Unknown')

    filepath = generate_verification_pdf(persons, gifts, documents_map, testator_name)
    if not filepath or not os.path.exists(filepath):
        flash('Could not generate verification PDF.', 'error')
        return redirect(url_for('wizard_step', step=10))

    return send_file(filepath, as_attachment=True,
                     download_name=f"Verification_{''.join(c for c in testator_name if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_')}.pdf",
                     mimetype='application/pdf')


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
        # Locked format: Alan & Tan / WillCraft standard. Single source of truth
        # for ALL will docx generation. Format approved 2026-05-02 — see
        # documents/will_docx.py + documents/sample_will_phek_yi_ting.py.
        from documents.will_docx import build_will_docx
        tenant = get_tenant()
        firm_info = None
        logo = None
        if tenant.get('firm_name'):
            firm_info = {
                'firm_name': tenant.get('firm_name', ''),
                'firm_address': tenant.get('firm_address', ''),
                'firm_phone': tenant.get('firm_phone', ''),
                'firm_email': tenant.get('email_from', ''),
            }
        if will_id:
            wr = db.session.get(Will, will_id)
            if wr and wr.include_logo:
                logo = _get_logo_path()
        # Determine draft status from the will record (default True for safety)
        is_draft = True
        if will_id:
            wr_status = (db.session.get(Will, will_id) or None)
            if wr_status and getattr(wr_status, 'status', '') in ('approved', 'final'):
                is_draft = False
        filepath = build_will_docx(
            will_text,
            firm_info=firm_info,
            logo_path=logo,
            is_draft=is_draft,
        )
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
        # Build firm info for cover page and prepared-by page
        tenant = get_tenant()
        firm_info = None
        if tenant.get('firm_name'):
            firm_info = {
                'firm_name': tenant.get('firm_name', ''),
                'firm_address': tenant.get('firm_address', ''),
                'firm_phone': tenant.get('firm_phone', ''),
                'firm_email': tenant.get('email_from', ''),
            }
        filepath = generate_pdf(will_text, safe_name, logo_path=logo, firm_info=firm_info)
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
    user_email = session.get('user_email')
    session.clear()
    if user_id:
        session['user_id'] = user_id
        session['user_role'] = user_role
        session['user_name'] = user_name
        session['user_email'] = user_email
    flash('Your session has been reset. You can start a new will.', 'info')
    return redirect(url_for('index'))


@app.route('/wizard/new', methods=['GET', 'POST'])
@login_required
def wizard_new():
    """Start a brand-new will. Testator name is REQUIRED — collected via the
    "+ New Will" modal (POST). GET without a name falls back to /wills with
    a flash so users can't bypass the modal by deep-linking.
    """
    # Preserve auth keys during session reset
    auth_keys = {k: session.get(k) for k in
                 ('user_id', 'user_role', 'user_name', 'user_email')}

    if request.method == 'POST':
        full_name = (request.form.get('full_name') or '').strip()
        nric = (request.form.get('nric_passport') or '').strip()
        if not full_name:
            flash('Please enter the testator\'s full name to start a new will.', 'error')
            return redirect(url_for('will_list'))

        session.clear()
        for k, v in auth_keys.items():
            if v: session[k] = v
        session['step1'] = {
            'full_name': full_name,
            'nric_passport': nric,
            'nationality': 'Malaysian',
        }
        # Create Client + Person immediately so Wills list / Client folder UI
        # show the right name from the very first page load.
        client_id = ensure_client()
        from services.person_registry import ensure_person
        pid = ensure_person(client_id, full_name, nric=nric, relationship='Testator')
        if pid:
            session['step1']['person_id'] = pid
        db.session.commit()
        return redirect(url_for('wizard_step_identities'))

    # GET — was the entry point for "+ New Will" before; now require the modal.
    flash('Please use the "+ New Will" button so we can capture the testator\'s name.', 'warning')
    return redirect(url_for('will_list'))


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


def _sync_probate_from_will(probate, will_record):
    """Copy testator/executor data from linked will into probate fields if empty.
    Makes probate the single source of truth for all downstream validation/generation.
    Returns (changed: bool, errors: list[str]) — errors explain why fields couldn't be synced."""
    errors = []
    if not will_record:
        errors.append('No linked will record found')
        return False, errors
    changed = False

    # Extract testator from will step1_data
    step1 = json.loads(will_record.step1_data or '{}')
    testator = step1 if step1 else {}
    if not testator:
        errors.append(f'Will ({will_record.id}) has no testator data (step1_data is empty)')

    # Extract executor from will — try step3_data first (executors), then step2_data
    executor = {}
    step3 = json.loads(will_record.step3_data or '[]')
    if isinstance(step3, list) and step3:
        executor = step3[0]
    elif isinstance(step3, dict) and (step3.get('full_name') or step3.get('person_id')):
        executor = step3
    # If step3 didn't yield a valid executor (e.g. it's guardians data), try step2
    if not executor or not (executor.get('full_name') or executor.get('person_id')):
        step2 = json.loads(will_record.step2_data or '{}')
        executors = step2.get('executors', []) if isinstance(step2, dict) else step2
        if isinstance(executors, list) and executors:
            executor = executors[0]
    if not executor or not (executor.get('full_name') or executor.get('person_id')):
        errors.append(f'Will ({will_record.id}) has no executor data (step2_data and step3_data are empty)')

    # Resolve person details from identity registry
    identities = json.loads(will_record.identities_data or '[]')
    id_lookup = {p.get('id', ''): p for p in identities} if identities else {}

    if executor.get('person_id'):
        if executor['person_id'] in id_lookup:
            person = id_lookup[executor['person_id']]
            executor = {**executor, **person}
        else:
            errors.append(f'Executor person_id "{executor["person_id"]}" not found in will identities ({len(identities)} identities)')

    if testator.get('person_id'):
        if testator['person_id'] in id_lookup:
            person = id_lookup[testator['person_id']]
            testator = {**testator, **person}
        else:
            errors.append(f'Testator person_id "{testator["person_id"]}" not found in will identities ({len(identities)} identities)')

    # Sync deceased fields from testator
    if not probate.deceased_name and testator.get('full_name'):
        probate.deceased_name = testator['full_name']
        changed = True
    elif not probate.deceased_name and not testator.get('full_name'):
        errors.append('Cannot sync deceased name: will testator has no full_name')

    if not probate.deceased_nric and testator.get('nric_passport'):
        probate.deceased_nric = testator['nric_passport']
        changed = True
    elif not probate.deceased_nric and not testator.get('nric_passport'):
        errors.append('Cannot sync deceased NRIC: will testator has no nric_passport')

    if not probate.deceased_address and (testator.get('residential_address') or testator.get('address')):
        probate.deceased_address = testator.get('residential_address') or testator.get('address')
        changed = True

    # Sync applicant fields from executor
    if not probate.applicant_name and executor.get('full_name'):
        probate.applicant_name = executor['full_name']
        changed = True
    elif not probate.applicant_name and not executor.get('full_name'):
        errors.append('Cannot sync applicant name: will executor has no full_name')

    if not probate.applicant_nric and executor.get('nric_passport'):
        probate.applicant_nric = executor['nric_passport']
        changed = True
    elif not probate.applicant_nric and not executor.get('nric_passport'):
        errors.append('Cannot sync applicant NRIC: will executor has no nric_passport')

    if not probate.applicant_address and executor.get('address'):
        probate.applicant_address = executor['address']
        changed = True
    if not probate.applicant_relationship and executor.get('relationship'):
        probate.applicant_relationship = executor['relationship']
        changed = True

    if changed:
        db.session.commit()

    # Log errors for debugging
    if errors:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f'Probate {probate.id} sync from will {will_record.id}: {"; ".join(errors)}')

    return changed, errors


def _validate_probate_data(probate, will_record, recommendations):
    """Check for missing required data per form and return warnings.
    Always checks probate fields only — will data should already be synced."""
    warnings = {}  # form_code -> list of missing field descriptions
    rec_codes = {r['form_code'] for r in recommendations if r.get('recommended')}

    # Pre-compute common checks — probate fields are the single source of truth
    has_deceased_name = bool(probate.deceased_name)
    has_deceased_nric = bool(probate.deceased_nric)
    has_deceased_addr = bool(probate.deceased_address)
    has_applicant_name = bool(probate.applicant_name)
    has_applicant_nric = bool(probate.applicant_nric)
    has_applicant_addr = bool(probate.applicant_address)
    has_applicant_rel = bool(probate.applicant_relationship)
    has_court = bool(probate.court_location)
    has_court_state = bool(probate.court_state)
    has_firm_name = bool(probate.firm_name)
    has_firm_addr = bool(probate.firm_address)
    has_firm_ref = bool(probate.firm_reference)
    has_lawyer = bool(probate.lawyer_name)
    has_bar = bool(probate.lawyer_bar_number)
    has_dod = bool(probate.date_of_death)
    has_pod = bool(probate.place_of_death)
    has_death_cert = bool(probate.death_cert_number)

    assets = json.loads(probate.assets_data or '[]')
    props = [a for a in assets if a.get('asset_type') == 'property']
    bens = json.loads(probate.beneficiaries_data or '[]') if probate.beneficiaries_data else []

    # Fields used by all/most forms
    common_fields = []
    if not has_deceased_name: common_fields.append('Deceased Name (Step 2)')
    if not has_deceased_nric: common_fields.append('Deceased NRIC (Step 2)')
    if not has_applicant_name: common_fields.append('Applicant Name (Step 2)')
    if not has_applicant_nric: common_fields.append('Applicant NRIC (Step 2)')
    if not has_court: common_fields.append('Court Location (Step 3)')
    if not has_court_state: common_fields.append('Court State (Step 3)')

    for code in rec_codes:
        missing = list(common_fields)  # start with common missing fields

        # Death details
        if code in ('doc01', 'doc02', 'doc03'):
            if not has_dod: missing.append('Date of Death (Step 1)')
            if not has_pod: missing.append('Place of Death (Step 1)')
        if code == 'doc02':
            if not has_death_cert: missing.append('Death Certificate Number (Step 1)')

        # Firm details
        if code in ('doc01', 'doc08', 'form14a', 'form346'):
            if not has_firm_name: missing.append('Firm Name (Step 3)')
            if not has_firm_addr: missing.append('Firm Address (Step 3)')

        # Lawyer details
        if code in ('form14a', 'form346'):
            if not has_lawyer: missing.append('Lawyer Name (Step 3)')
            if not has_bar: missing.append('Bar Council Number (Step 3)')

        # Applicant details
        if code in ('doc01', 'doc02', 'doc03', 'form346'):
            if not has_applicant_addr: missing.append('Applicant Address (Step 2)')
            if not has_applicant_rel: missing.append('Applicant Relationship (Step 2)')

        # Deceased address
        if code in ('doc01', 'doc02'):
            if not has_deceased_addr: missing.append('Deceased Address (Step 2)')

        # Witnesses
        if code == 'doc04':
            if not probate.witness1_name: missing.append('Witness 1 Name (Step 4)')
            if not probate.witness1_nric: missing.append('Witness 1 NRIC (Step 4)')
            if not probate.witness1_address: missing.append('Witness 1 Address (Step 4)')
        if code == 'doc05':
            if not probate.witness2_name: missing.append('Witness 2 Name (Step 4)')
            if not probate.witness2_nric: missing.append('Witness 2 NRIC (Step 4)')
            if not probate.witness2_address: missing.append('Witness 2 Address (Step 4)')

        # Property details
        if code in ('form14a', 'form346'):
            if props:
                p0 = props[0]
                if not p0.get('title_number'): missing.append('Property Title Number (Step 6)')
                if not p0.get('lot_number'): missing.append('Property Lot Number (Step 6)')
                if not p0.get('mukim'): missing.append('Property Mukim (Step 6)')
            else:
                missing.append('No properties entered (Step 6)')

        # Beneficiaries
        if code == 'doc07':
            if not bens: missing.append('No beneficiaries entered (Step 5)')

        # Assets schedule
        if code == 'doc06':
            if not assets: missing.append('No assets entered (Step 6)')

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

    # Auto-sync will data into probate fields (single source of truth)
    sync_errors = []
    if will_record and not is_la:
        _changed, sync_errors = _sync_probate_from_will(probate, will_record)

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
        # Probate: always read from probate fields (already synced from will above)
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
        if will_record:
            will_title = will_record.title
            client_name = will_record.client.full_name if will_record.client else ''
        else:
            will_title = f'Probate — {probate.deceased_name or "Manual Entry"}'
            client_name = probate.applicant_name or ''

    # Determine which steps are complete (green tick) — require ALL essential fields
    # Step 1: Death details only
    step1_ok = all([
        probate.date_of_death,
        probate.place_of_death,
    ])
    # Step 2: Deceased & Executor details
    step2_ok = all([
        probate.deceased_name,
        probate.deceased_nric,
        probate.applicant_name,
        probate.applicant_nric,
    ])
    # Step 3: Court & Firm — case_number and firm_reference are OPTIONAL (assigned later)
    step3_ok = all([
        probate.court_location,
        probate.court_state,
        probate.firm_name,
        probate.firm_address,
        probate.lawyer_name,
        probate.lawyer_bar_number,
    ])
    # Step 4: At least one witness with name, NRIC, address
    step4_ok = all([
        probate.witness1_name,
        probate.witness1_nric,
        probate.witness1_address,
    ])
    # Step 5: At least one beneficiary
    _bens_data = json.loads(probate.beneficiaries_data or '[]') if probate.beneficiaries_data else []
    step5_ok = len(_bens_data) > 0 and all(
        b.get('full_name') or b.get('beneficiary_name') for b in _bens_data
    )
    # Step 6: At least one asset entered
    _assets_data = json.loads(probate.assets_data or '[]') if probate.assets_data else []
    step6_ok = len(_assets_data) > 0

    # Build completed steps set (non-sequential — each step independent)
    completed_steps = set()
    if step1_ok: completed_steps.add(1)
    if step2_ok: completed_steps.add(2)
    if step3_ok: completed_steps.add(3)
    if step4_ok: completed_steps.add(4)
    if step5_ok: completed_steps.add(5)
    if step6_ok: completed_steps.add(6)
    if probate.status in ('generated', 'pending_approval', 'approved', 'rejected'):
        completed_steps.add(7)

    all_steps_complete = all(s in completed_steps for s in range(1, 7))  # Steps 1-6 all done

    no_will = not is_la and not will_record  # Manual will upload (probate without linked will)

    return probate, will_record, {
        'probate': probate,
        'probate_id': probate_id,
        'is_la': is_la,
        'no_will': no_will,
        'will_title': will_title,
        'client_name': client_name,
        'testator': testator,
        'executor': type('Obj', (), executor) if executor else None,
        'completed_steps': completed_steps,
        'all_steps_complete': all_steps_complete,
        'sync_errors': sync_errors,
        # Keep max_completed_step for backward compatibility
        'max_completed_step': max(completed_steps) if completed_steps else 0,
    }


@app.route('/probate')
@login_required
def probate_list():
    role = session.get('user_role')
    if role not in ('admin', 'approver'):
        flash('Access denied.', 'error')
        return redirect(url_for('index'))
    q = request.args.get('q', '').strip()
    base_q = ProbateApplication.query.filter(ProbateApplication.deleted_at.is_(None))
    if q:
        applications = base_q.filter(
            db.or_(
                ProbateApplication.deceased_name.ilike(f'%{q}%'),
                ProbateApplication.deceased_nric.ilike(f'%{q}%'),
                ProbateApplication.applicant_name.ilike(f'%{q}%'),
                ProbateApplication.case_number.ilike(f'%{q}%'),
            )
        ).order_by(ProbateApplication.created_at.desc()).all()
    else:
        applications = base_q.order_by(ProbateApplication.created_at.desc()).all()
    approved_wills = Will.query.filter_by(status='approved').filter(Will.deleted_at.is_(None)).order_by(Will.approved_at.desc()).all()
    return render_template('probate/list.html', applications=applications, approved_wills=approved_wills, search_query=q)


@app.route('/probate/<probate_id>/delete', methods=['POST'])
@login_required
def probate_delete(probate_id):
    """Soft-delete a probate application (recoverable for 30 days)."""
    role = session.get('user_role')
    if role not in ('admin', 'approver'):
        flash('Access denied.', 'error')
        return redirect(url_for('probate_list'))
    probate = db.session.get(ProbateApplication, probate_id)
    if not probate:
        flash('Application not found.', 'error')
        return redirect(url_for('probate_list'))
    deceased = probate.deceased_name or 'Unknown'
    probate.deleted_at = datetime.utcnow()
    db.session.commit()
    flash(f'Probate application for "{deceased}" moved to trash. It can be restored within 30 days.', 'success')
    return redirect(url_for('probate_list'))


@app.route('/probate/<probate_id>/restore', methods=['POST'])
@login_required
def probate_restore(probate_id):
    """Restore a soft-deleted probate application."""
    role = session.get('user_role')
    if role not in ('admin', 'approver'):
        flash('Access denied.', 'error')
        return redirect(url_for('trash_list'))
    probate = db.session.get(ProbateApplication, probate_id)
    if probate and probate.deleted_at:
        probate.deleted_at = None
        db.session.commit()
        flash(f'Probate application restored successfully.', 'success')
    return redirect(url_for('trash_list'))


@app.route('/probate/<probate_id>/permanent-delete', methods=['POST'])
@login_required
def probate_permanent_delete(probate_id):
    """Permanently delete a probate application (admin only, from trash)."""
    role = session.get('user_role')
    if role not in ('admin',):
        flash('Access denied.', 'error')
        return redirect(url_for('trash_list'))
    probate = db.session.get(ProbateApplication, probate_id)
    if not probate:
        flash('Application not found.', 'error')
        return redirect(url_for('trash_list'))
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
    flash(f'Probate application for "{deceased}" permanently deleted.', 'success')
    return redirect(url_for('trash_list'))


@app.route('/probate/new-la')
@app.route('/probate/new-probate')  # External will (no WillCraft will linked)
@login_required
def probate_new_la():
    """Create a new LA or external-will probate application."""
    role = session.get('user_role')
    if role not in ('admin', 'approver'):
        flash('Access denied.', 'error')
        return redirect(url_for('index'))
    # Determine type from URL
    app_type = 'probate' if request.path.endswith('new-probate') else 'la'
    tenant = get_tenant()
    probate = ProbateApplication(
        application_type=app_type,
        filing_year=str(datetime.now().year),
        created_by=session.get('user_id'),
        firm_name=tenant.get('firm_name', 'Tetuan Alan Tan & Associates'),
        firm_address=tenant.get('firm_address', '24-01 & 24-02, Jalan Kempas Utama 2/4, Taman Kempas Utama, 81300 Johor Bahru, Johor'),
        firm_phone=tenant.get('firm_phone', '07-588 5979'),
        lawyer_name=tenant.get('lawyer_name', 'FAIZUL HANAFI BIN TOKIRAN'),
        lawyer_bar_number=tenant.get('lawyer_bar_number', 'BC/F/167'),
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
    existing = ProbateApplication.query.filter_by(will_id=will_id).filter(ProbateApplication.deleted_at.is_(None)).first()
    if existing:
        return redirect(f'/probate/{existing.id}/step/1')
    # Create new probate application
    tenant = get_tenant()
    probate = ProbateApplication(
        will_id=will_id,
        client_id=will_record.client_id,
        filing_year=str(datetime.now().year),
        created_by=session.get('user_id'),
        firm_name=tenant.get('firm_name', 'Tetuan Alan Tan & Associates'),
        firm_address=tenant.get('firm_address', '24-01 & 24-02, Jalan Kempas Utama 2/4, Taman Kempas Utama, 81300 Johor Bahru, Johor'),
        firm_phone=tenant.get('firm_phone', '07-588 5979'),
        lawyer_name=tenant.get('lawyer_name', 'FAIZUL HANAFI BIN TOKIRAN'),
        lawyer_bar_number=tenant.get('lawyer_bar_number', 'BC/F/167'),
    )
    db.session.add(probate)
    db.session.commit()
    # Auto-populate deceased/applicant from linked will
    _changed, _errors = _sync_probate_from_will(probate, will_record)
    return redirect(f'/probate/{probate.id}/step/1')


def _classify_asset(description):
    """Classify an asset description into: property, bank, vehicle, investment, other."""
    if not description:
        return 'other'
    desc = description.lower()
    # Property keywords
    if any(kw in desc for kw in ['land', 'house', 'apartment', 'flat', 'condo', 'bungalow',
            'terrace', 'semi-d', 'lot', 'title', 'geran', 'hakmilik', 'strata',
            'property', 'rumah', 'tanah', 'building', 'premises', 'unit',
            'jalan', 'lorong', 'taman', 'kampung', 'no.', 'no ', 'address']):
        return 'property'
    # Bank / financial account keywords
    if any(kw in desc for kw in ['bank', 'account', 'savings', 'current account', 'fixed deposit',
            'akaun', 'maybank', 'cimb', 'rhb', 'hong leong', 'public bank',
            'ambank', 'bsn', 'affin', 'hsbc', 'ocbc', 'uob', 'standard chartered',
            'alliance', 'kwsp', 'epf', 'tabung', 'amanah', 'asb', 'asp']):
        return 'bank'
    # Vehicle keywords
    if any(kw in desc for kw in ['car', 'vehicle', 'motor', 'kereta', 'toyota', 'honda',
            'proton', 'perodua', 'mercedes', 'bmw', 'nissan', 'mazda',
            'registration', 'plate number', 'nombor pendaftaran']):
        return 'vehicle'
    # Investment keywords
    if any(kw in desc for kw in ['share', 'stock', 'unit trust', 'bond', 'investment',
            'dividend', 'securities', 'saham', 'bursa', 'insurance', 'policy',
            'insurans', 'takaful', 'mutual fund']):
        return 'investment'
    return 'other'


@app.route('/probate/<probate_id>/save-ocr-data', methods=['POST'])
@login_required
def probate_save_ocr_data(probate_id):
    """Save OCR-extracted data (witnesses, beneficiaries, assets) for later steps."""
    probate = db.session.get(ProbateApplication, probate_id)
    if not probate:
        return jsonify(ok=False, error='Not found'), 404
    data = request.get_json(force=True)
    # Merge into form_data_json
    existing = json.loads(probate.form_data_json or '{}')
    existing['ocr_extracted'] = data
    probate.form_data_json = json.dumps(existing)

    # Auto-fill deceased fields (overwrite from scan — user explicitly confirmed)
    if data.get('deceased_name'):
        probate.deceased_name = data['deceased_name']
    if data.get('deceased_nric'):
        probate.deceased_nric = data['deceased_nric']
    if data.get('deceased_address'):
        probate.deceased_address = data['deceased_address']

    # Auto-fill executor/applicant fields
    if data.get('applicant_name'):
        probate.applicant_name = data['applicant_name']
    if data.get('applicant_nric'):
        probate.applicant_nric = data['applicant_nric']
    if data.get('applicant_address'):
        probate.applicant_address = data['applicant_address']
    if data.get('applicant_relationship'):
        probate.applicant_relationship = data['applicant_relationship']

    # Auto-fill witness fields
    if data.get('witness1_name'):
        probate.witness1_name = data['witness1_name']
    if data.get('witness1_nric'):
        probate.witness1_nric = data['witness1_nric']
    if data.get('witness1_address'):
        probate.witness1_address = data['witness1_address']
    if data.get('witness2_name'):
        probate.witness2_name = data['witness2_name']
    if data.get('witness2_nric'):
        probate.witness2_nric = data['witness2_nric']
    if data.get('witness2_address'):
        probate.witness2_address = data['witness2_address']

    # Auto-fill beneficiaries
    bens = []
    i = 0
    while data.get(f'beneficiary_{i}_name'):
        bens.append({
            'full_name': data.get(f'beneficiary_{i}_name', ''),
            'nric_passport': data.get(f'beneficiary_{i}_nric', ''),
            'relationship': data.get(f'beneficiary_{i}_relationship', ''),
            'address': data.get(f'beneficiary_{i}_address', ''),
        })
        i += 1
    if bens:
        probate.beneficiaries_data = json.dumps(bens)

    # Auto-fill assets
    assets = []
    i = 0
    while data.get(f'asset_{i}'):
        desc = data[f'asset_{i}']
        # Try to parse JSON if the asset was stored as JSON string
        try:
            asset_obj = json.loads(desc) if desc.startswith('{') else None
        except (json.JSONDecodeError, AttributeError):
            asset_obj = None
        if asset_obj and isinstance(asset_obj, dict):
            # Normalize: OCR uses 'type', DB uses 'asset_type'
            if asset_obj.get('type') and not asset_obj.get('asset_type'):
                asset_obj['asset_type'] = asset_obj.pop('type')
            if not asset_obj.get('asset_type'):
                asset_obj['asset_type'] = _classify_asset(asset_obj.get('description', ''))
            if 'estimated_value' not in asset_obj:
                asset_obj['estimated_value'] = ''
            assets.append(asset_obj)
        else:
            assets.append({'asset_type': _classify_asset(desc), 'description': desc, 'estimated_value': ''})
        i += 1
    if assets:
        probate.assets_data = json.dumps(assets)

    probate.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(ok=True)


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
        # Deceased/applicant fields moved to step 2
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
                        if witnesses[0].get('address'):
                            probate.witness1_address = witnesses[0]['address']
                    if len(witnesses) >= 2:
                        probate.witness2_name = witnesses[1].get('full_name', '')
                        probate.witness2_nric = witnesses[1].get('nric_number', '')
                        if witnesses[1].get('address'):
                            probate.witness2_address = witnesses[1]['address']
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
                            'address': b.get('address', ''),
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
    # Look up uploaded document objects for display
    if probate.death_cert_document_id:
        ctx['death_cert_doc'] = db.session.get(Document, probate.death_cert_document_id)
    if probate.will_document_id:
        ctx['will_doc'] = db.session.get(Document, probate.will_document_id)
    return render_template('probate/step1_death.html', **ctx)


@app.route('/probate/<probate_id>/step/2', methods=['GET', 'POST'])
@login_required
def probate_step2(probate_id):
    """Step 2: Deceased & Executor details."""
    probate, will_record, ctx = _get_probate_context(probate_id)
    if not probate:
        flash('Probate application not found.', 'error')
        return redirect(url_for('probate_list'))

    if request.method == 'POST':
        probate.deceased_name = request.form.get('deceased_name', '').strip()
        probate.deceased_nric = request.form.get('deceased_nric', '').strip()
        probate.deceased_address = request.form.get('deceased_address', '').strip()
        probate.applicant_name = request.form.get('applicant_name', '').strip()
        probate.applicant_nric = request.form.get('applicant_nric', '').strip()
        probate.applicant_address = request.form.get('applicant_address', '').strip()
        probate.applicant_relationship = request.form.get('applicant_relationship', '').strip()
        probate.updated_at = datetime.utcnow()
        db.session.commit()
        return redirect(f'/probate/{probate_id}/step/3')

    ctx['probate_step'] = 2
    return render_template('probate/step2_executor.html', **ctx)


@app.route('/probate/<probate_id>/step/3', methods=['GET', 'POST'])
@login_required
def probate_step3(probate_id):
    """Step 3: Court & Law Firm details."""
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
        return redirect(f'/probate/{probate_id}/step/4')

    ctx['probate_step'] = 3
    ctx['courts'] = MALAYSIAN_COURTS
    ctx['states'] = MALAYSIAN_STATES
    ctx['current_year'] = str(datetime.now().year)
    return render_template('probate/step2_court.html', **ctx)


@app.route('/probate/<probate_id>/step/4', methods=['GET', 'POST'])
@login_required
def probate_step4(probate_id):
    """Step 4: Witnesses."""
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
        return redirect(f'/probate/{probate_id}/step/5')

    ctx['probate_step'] = 4
    return render_template('probate/step3_witnesses.html', **ctx)


@app.route('/probate/<probate_id>/step/5', methods=['GET', 'POST'])
@login_required
def probate_step5(probate_id):
    """Step 5: Beneficiaries list."""
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
        return redirect(f'/probate/{probate_id}/step/6')

    # Pre-populate from will data if beneficiaries_data is empty
    existing_bens = json.loads(probate.beneficiaries_data or '[]')
    if not existing_bens and will_record:
        will_bens = json.loads(will_record.step4_data or '[]')
        # Build identity lookup from will's person registry for address extraction
        identity_lookup = {}
        will_identities = json.loads(will_record.identities_data or '[]')
        for person in will_identities:
            identity_lookup[person.get('id', '')] = person
        for b in will_bens:
            # Look up address from will's identity registry via person_id
            addr = b.get('address', '')
            if not addr and b.get('person_id'):
                person = identity_lookup.get(b['person_id'], {})
                addr = person.get('address', '')
            existing_bens.append({
                'full_name': b.get('full_name', b.get('beneficiary_name', '')),
                'nric_passport': b.get('nric_passport_birthcert', b.get('nric_passport', '')),
                'relationship': b.get('relationship', ''),
                'address': addr,
            })

    ctx['probate_step'] = 5
    ctx['beneficiaries_json'] = json.dumps(existing_bens)
    return render_template('probate/step4_beneficiaries.html', **ctx)


@app.route('/probate/<probate_id>/step/6', methods=['GET', 'POST'])
@login_required
def probate_step6(probate_id):
    """Step 6: Assets & Liabilities schedule."""
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
        return redirect(f'/probate/{probate_id}/step/7')

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

    ctx['probate_step'] = 6
    ctx['assets_json'] = json.dumps(existing_assets)
    ctx['exhibit_prefix'] = exhibit_prefix
    return render_template('probate/step5_assets.html', **ctx)


@app.route('/probate/<probate_id>/step/7', methods=['GET'])
@login_required
def probate_step7(probate_id):
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
        'Case number': probate.case_number or 'Not set',
        'Firm name & address': f"{probate.firm_name or ''}, {probate.firm_address or ''}" if probate.firm_name else '',
        'Firm phone & fax': f"Tel: {probate.firm_phone or ''}, Fax: {probate.firm_fax or ''}",
        'Firm reference': probate.firm_reference or '',
        'Witness 1 name & NRIC': f"{probate.witness1_name or ''} ({probate.witness1_nric or ''})" if probate.witness1_name else '',
        'Witness 1 address': probate.witness1_address or '',
        'Witness 2 name & NRIC': f"{probate.witness2_name or ''} ({probate.witness2_nric or ''})" if probate.witness2_name else '',
        'Witness 2 address': probate.witness2_address or '',
        'Estate value': probate.estate_value_estimate or '',
        'Exhibit references': 'Auto-generated',
        'Lawyer name': probate.lawyer_name or '',
        'Lawyer bar council number': probate.lawyer_bar_number or '',
        'Filing year': probate.filing_year or '',
    }
    # Assets summary values
    _assets = json.loads(probate.assets_data or '[]')
    _props = [a for a in _assets if a.get('asset_type') == 'property']
    _banks = [a for a in _assets if a.get('asset_type') == 'bank']
    _vehicles = [a for a in _assets if a.get('asset_type') == 'vehicle']
    _others = [a for a in _assets if a.get('asset_type') == 'other']
    _investments = [a for a in _assets if a.get('asset_type') == 'investment']
    _liabs = [a for a in _assets if a.get('asset_type') == 'liability']
    field_values['Properties (title, lot, mukim, address)'] = f'{len(_props)} properties' if _props else 'N/A'
    field_values['Bank accounts (bank, account no., value)'] = f'{len(_banks)} accounts' if _banks else 'N/A'
    field_values['Vehicles (desc, reg no., engine, chassis)'] = f'{len(_vehicles)} vehicles' if _vehicles else 'N/A'
    field_values['Investment accounts (CDS, unit trust, etc.)'] = f'{len(_investments)} accounts' if _investments else 'N/A'
    field_values['Other assets (description, value)'] = f'{len(_others)} items' if _others else 'N/A'
    field_values['Liabilities (description, value)'] = f'{len(_liabs)} items' if _liabs else 'N/A'
    # Beneficiaries from probate (single source of truth)
    _bens = json.loads(probate.beneficiaries_data or '[]') if probate.beneficiaries_data and probate.beneficiaries_data != '[]' else []
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

    # Beneficiary info from probate (single source of truth)
    beneficiaries = json.loads(probate.beneficiaries_data or '[]') if probate.beneficiaries_data and probate.beneficiaries_data != '[]' else []
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

    ctx['probate_step'] = 7
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

    # Block generation if steps are incomplete (approver/admin can override)
    role = session.get('user_role')
    if not ctx.get('all_steps_complete') and role not in ('admin', 'approver'):
        flash('Please complete all steps (1-6) before generating forms.', 'error')
        return redirect(f'/probate/{probate_id}/step/7')

    selected_codes = request.form.getlist('forms')
    if not selected_codes:
        flash('Please select at least one form to generate.', 'error')
        return redirect(f'/probate/{probate_id}/step/7')

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
    return redirect(f'/probate/{probate_id}/step/7')


@app.route('/probate/<probate_id>/submit-approval', methods=['POST'])
@login_required
def probate_submit_approval(probate_id):
    """Submit generated forms for approver review."""
    probate = db.session.get(ProbateApplication, probate_id)
    if not probate:
        flash('Application not found.', 'error')
        return redirect(url_for('probate_list'))
    if probate.status not in ('generated', 'rejected'):
        flash('Forms must be generated before submitting for approval.', 'error')
        return redirect(f'/probate/{probate_id}/step/7')
    probate.status = 'pending_approval'
    probate.submitted_by = session.get('user_id')
    probate.submitted_at = datetime.utcnow()
    probate.approval_notes = None  # Clear previous rejection notes
    db.session.commit()
    flash('Forms submitted for approval.', 'success')
    return redirect(f'/probate/{probate_id}/step/7')


@app.route('/probate/<probate_id>/approve', methods=['POST'])
@login_required
def probate_approve(probate_id):
    """Approve probate forms (approver only)."""
    role = session.get('user_role')
    if role not in ('admin', 'approver'):
        flash('Access denied.', 'error')
        return redirect(url_for('probate_list'))
    probate = db.session.get(ProbateApplication, probate_id)
    if not probate:
        flash('Application not found.', 'error')
        return redirect(url_for('probate_list'))
    notes = request.form.get('approval_notes', '').strip()
    probate.status = 'approved'
    probate.approved_by = session.get('user_id')
    probate.approved_at = datetime.utcnow()
    probate.approval_notes = notes or None
    db.session.commit()
    flash(f'Probate forms for {probate.deceased_name or "estate"} approved.', 'success')
    return redirect(f'/probate/{probate_id}/step/7')


@app.route('/probate/<probate_id>/reject', methods=['POST'])
@login_required
def probate_reject(probate_id):
    """Request changes on probate forms (approver only)."""
    role = session.get('user_role')
    if role not in ('admin', 'approver'):
        flash('Access denied.', 'error')
        return redirect(url_for('probate_list'))
    probate = db.session.get(ProbateApplication, probate_id)
    if not probate:
        flash('Application not found.', 'error')
        return redirect(url_for('probate_list'))
    notes = request.form.get('approval_notes', '').strip()
    probate.status = 'rejected'
    probate.approved_by = session.get('user_id')
    probate.approved_at = datetime.utcnow()
    probate.approval_notes = notes or 'Changes requested'
    db.session.commit()
    flash(f'Changes requested for {probate.deceased_name or "estate"}.', 'info')
    return redirect(f'/probate/{probate_id}/step/7')


@app.route('/probate/<probate_id>/preview/<form_code>')
@login_required
def probate_preview(probate_id, form_code):
    """Serve generated form as inline PDF for browser preview."""
    gf = ProbateGeneratedForm.query.filter_by(probate_id=probate_id, form_code=form_code).first()
    if not gf or not os.path.exists(gf.file_path):
        flash('Form not found.', 'error')
        return redirect(f'/probate/{probate_id}/step/7')
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
    return redirect(f'/probate/{probate_id}/step/7')


@app.route('/probate/<probate_id>/form-html/<form_code>')
@login_required
def probate_form_html(probate_id, form_code):
    """Convert generated DOCX to editable HTML."""
    gf = ProbateGeneratedForm.query.filter_by(probate_id=probate_id, form_code=form_code).first()
    if not gf or not os.path.exists(gf.file_path):
        return '<p>Form not found</p>', 404
    import shutil, subprocess
    tmp_dir = tempfile.mkdtemp()
    tmp_copy = os.path.join(tmp_dir, os.path.basename(gf.file_path))
    shutil.copy2(gf.file_path, tmp_copy)
    # Convert DOCX to HTML using LibreOffice
    result = subprocess.run(
        ['libreoffice', '--headless', '--convert-to', 'html', tmp_copy, '--outdir', tmp_dir],
        capture_output=True, text=True, timeout=30
    )
    html_file = os.path.splitext(tmp_copy)[0] + '.html'
    if not os.path.exists(html_file):
        return '<p>Conversion failed</p>', 500
    with open(html_file, 'r', encoding='utf-8', errors='replace') as f:
        html_content = f.read()
    # Wrap in editable page with save functionality
    page_html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{
    font-family: 'Times New Roman', serif;
    margin: 0; padding: 20px 40px;
    background: white;
  }}
  /* Highlight unfilled placeholders */
  .missing-field {{
    background: #fef2f2;
    border: 1px dashed #ef4444;
    padding: 1px 4px;
    border-radius: 3px;
    color: #dc2626;
    font-weight: bold;
  }}
  /* Toolbar */
  #toolbar {{
    position: sticky; top: 0; z-index: 100;
    background: #1f2937; color: white;
    padding: 8px 16px;
    margin: -20px -40px 20px -40px;
    display: flex; align-items: center; justify-content: space-between;
    font-family: system-ui, sans-serif;
  }}
  #toolbar button {{
    padding: 4px 12px; border: none; border-radius: 4px;
    font-size: 12px; font-weight: 600; cursor: pointer;
  }}
  #toolbar .save-btn {{ background: #16a34a; color: white; }}
  #toolbar .save-btn:hover {{ background: #15803d; }}
  #toolbar .save-btn:disabled {{ background: #6b7280; cursor: not-allowed; }}
  #toolbar .translate-btn {{ background: #2563eb; color: white; }}
  #toolbar .translate-btn:hover {{ background: #1d4ed8; }}
  #toolbar .translate-btn:disabled {{ background: #6b7280; cursor: not-allowed; }}
  #toolbar .edit-toggle {{ background: #7c3aed; color: white; }}
  #toolbar .edit-toggle:hover {{ background: #6d28d9; }}
  #toolbar .edit-toggle.active {{ background: #f59e0b; }}
  #toolbar .status {{ font-size: 11px; color: #9ca3af; margin-left: 8px; }}
  #toolbar .title {{ font-size: 13px; font-weight: 600; }}
  /* Make content editable look nice */
  #doc-content {{ outline: none; min-height: 80vh; }}
  #doc-content:focus {{ outline: none; }}
  #doc-content table {{ border-collapse: collapse; }}
  #doc-content td, #doc-content th {{ border: 1px solid #ccc; padding: 4px 8px; }}
</style>
</head>
<body>
<div id="toolbar">
  <span class="title">{gf.form_name or form_code}</span>
  <div style="display:flex;align-items:center;gap:8px;">
    <span id="status" class="status"></span>
    <button class="translate-btn" id="translate-btn" onclick="translateToEnglish()" title="Translate Bahasa Melayu to English">Translate to English</button>
    <button class="edit-toggle" id="edit-toggle" onclick="toggleEdit()" title="Toggle editing mode">Edit: ON</button>
    <button class="save-btn" id="save-btn" onclick="saveChanges()">Save</button>
  </div>
</div>
<div id="doc-content" contenteditable="true">
''' + html_content + '''
</div>
<script>
let _dirty = false;
const docEl = document.getElementById('doc-content');

// Highlight {{PLACEHOLDERS}}
function highlightMissing() {
  const walker = document.createTreeWalker(docEl, NodeFilter.SHOW_TEXT, null, false);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(node => {
    if (/\\{\\{\\w+\\}\\}/.test(node.textContent)) {
      const span = document.createElement('span');
      span.innerHTML = node.textContent.replace(/\\{\\{(\\w+)\\}\\}/g,
        '<span class="missing-field">{{$1}}</span>');
      node.parentNode.replaceChild(span, node);
    }
  });
}
highlightMissing();

docEl.addEventListener('input', () => {
  _dirty = true;
  document.getElementById('status').textContent = 'Unsaved changes';
  document.getElementById('status').style.color = '#fbbf24';
});

async function saveChanges() {
  const btn = document.getElementById('save-btn');
  const status = document.getElementById('status');
  btn.disabled = true;
  btn.textContent = 'Saving...';
  status.textContent = '';

  // Get the edited HTML content
  const html = docEl.innerHTML;
  try {
    const res = await fetch('/probate/''' + probate_id + '''/form-html/''' + form_code + '''', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ html: html })
    });
    const data = await res.json();
    if (data.ok) {
      status.textContent = 'Saved successfully!';
      status.style.color = '#4ade80';
      _dirty = false;
      // Notify parent to refresh if needed
      if (window.parent && window.parent.onFormSaved) window.parent.onFormSaved();
    } else {
      status.textContent = 'Save failed: ' + (data.error || 'Unknown');
      status.style.color = '#f87171';
    }
  } catch(e) {
    status.textContent = 'Save failed: ' + e.message;
    status.style.color = '#f87171';
  }
  btn.disabled = false;
  btn.textContent = 'Save';
}

// Toggle edit mode
let _editMode = true;
function toggleEdit() {
  _editMode = !_editMode;
  docEl.contentEditable = _editMode ? 'true' : 'false';
  const btn = document.getElementById('edit-toggle');
  btn.textContent = 'Edit: ' + (_editMode ? 'ON' : 'OFF');
  btn.classList.toggle('active', _editMode);
}

// Translate to English using Claude API
async function translateToEnglish() {
  if (!confirm('Translate this document from Bahasa Melayu to English?')) return;
  const btn = document.getElementById('translate-btn');
  const status = document.getElementById('status');
  btn.disabled = true;

  // Spinner + countdown timer
  let elapsed = 0;
  const est = 20; // estimated seconds
  btn.innerHTML = '&#9889; Translating...';
  status.innerHTML = '<span style="display:inline-flex;align-items:center;gap:4px;">' +
    '<svg style="width:14px;height:14px;animation:spin 1s linear infinite;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.49-8.49l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M6.34 6.34L3.51 3.51"/></svg>' +
    '<span id="translate-timer">~' + est + 's remaining</span></span>';
  status.style.color = '#60a5fa';

  // Add spinner keyframes if not present
  if (!document.getElementById('spin-style')) {
    const s = document.createElement('style');
    s.id = 'spin-style';
    s.textContent = '@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}';
    document.head.appendChild(s);
  }

  const timer = setInterval(() => {
    elapsed++;
    const rem = Math.max(0, est - elapsed);
    const el = document.getElementById('translate-timer');
    if (el) {
      if (rem > 0) el.textContent = '~' + rem + 's remaining';
      else el.textContent = 'Almost done...';
    }
  }, 1000);

  try {
    const res = await fetch('/probate/''' + probate_id + '''/translate-form/''' + form_code + '''', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ html: docEl.innerHTML })
    });
    clearInterval(timer);
    const data = await res.json();
    if (data.ok && data.translated_html) {
      docEl.innerHTML = data.translated_html;
      _dirty = true;
      status.innerHTML = '&#10003; Translated in ' + elapsed + 's — Review and Save';
      status.style.color = '#4ade80';
    } else {
      status.textContent = 'Translation failed: ' + (data.error || 'Unknown');
      status.style.color = '#f87171';
    }
  } catch(e) {
    clearInterval(timer);
    status.textContent = 'Translation failed: ' + e.message;
    status.style.color = '#f87171';
  }
  btn.disabled = false;
  btn.textContent = 'Translate to English';
}

window.addEventListener('beforeunload', (e) => {
  if (_dirty) { e.preventDefault(); e.returnValue = ''; }
});
</script>
</body></html>'''
    resp = make_response(page_html)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/probate/<probate_id>/form-html/<form_code>', methods=['POST'])
@login_required
def probate_form_html_save(probate_id, form_code):
    """Save edited HTML back to DOCX."""
    gf = ProbateGeneratedForm.query.filter_by(probate_id=probate_id, form_code=form_code).first()
    if not gf or not os.path.exists(gf.file_path):
        return jsonify(ok=False, error='Form not found'), 404
    data = request.get_json()
    if not data or 'html' not in data:
        return jsonify(ok=False, error='No HTML content provided'), 400
    import subprocess
    # Write edited HTML to temp file
    tmp_dir = tempfile.mkdtemp()
    html_path = os.path.join(tmp_dir, 'edited.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(f'''<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>{data['html']}</body></html>''')
    # Convert HTML back to DOCX using LibreOffice (requires explicit filter)
    result = subprocess.run(
        ['libreoffice', '--headless', '--convert-to', 'docx:MS Word 2007 XML', html_path, '--outdir', tmp_dir],
        capture_output=True, text=True, timeout=30
    )
    new_docx = os.path.join(tmp_dir, 'edited.docx')
    if not os.path.exists(new_docx):
        return jsonify(ok=False, error='Conversion failed'), 500
    # Replace the generated file
    import shutil
    shutil.copy2(new_docx, gf.file_path)
    gf.generated_at = datetime.utcnow()
    db.session.commit()
    return jsonify(ok=True)


@app.route('/probate/<probate_id>/translate-form/<form_code>', methods=['POST'])
@login_required
def probate_translate_form(probate_id, form_code):
    """Translate form HTML from Bahasa Melayu to English using Claude API."""
    data = request.get_json()
    if not data or 'html' not in data:
        return jsonify(ok=False, error='No HTML content'), 400

    html_content = data['html']
    try:
        import anthropic
        from config import ANTHROPIC_API_KEY, CLAUDE_MODEL_FAST
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        response = client.messages.create(
            model=CLAUDE_MODEL_FAST,
            max_tokens=8000,
            messages=[{
                "role": "user",
                "content": f"""Translate this Malaysian legal court document from Bahasa Melayu to English.

IMPORTANT RULES:
1. Keep ALL HTML tags, attributes, and structure EXACTLY as-is — only translate the text content
2. Keep ALL names, NRIC numbers, addresses, dates, case numbers, and form numbers unchanged
3. Keep legal citations and section references unchanged (e.g., "Seksyen 12" → "Section 12")
4. Use proper English legal terminology (e.g., "Pemohon" → "Applicant", "Si Mati" → "the Deceased")
5. Keep "PEMOHON" as "APPLICANT", "Probet" as "Probate"
6. Return ONLY the translated HTML, no explanations

Common translations:
- Dalam Mahkamah Tinggi Malaya → In the High Court of Malaya
- Dalam Perkara Mengenai Harta Pusaka → In the Matter of the Estate of
- Saman Pemula → Originating Summons
- Afidavit → Affidavit
- Senarai Benefisiari → List of Beneficiaries
- Perintah → Order
- Sumpah → Oath/Affirmation

HTML to translate:
{html_content}"""
            }]
        )
        translated = response.content[0].text.strip()
        # Remove markdown code fences if present
        if translated.startswith('```'):
            translated = translated.split('\n', 1)[1] if '\n' in translated else translated[3:]
        if translated.endswith('```'):
            translated = translated[:-3].rstrip()
        return jsonify(ok=True, translated_html=translated)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@app.route('/probate/<probate_id>/download/<form_code>')
@login_required
def probate_download(probate_id, form_code):
    fmt = request.args.get('format', 'docx')  # docx or pdf
    gf = ProbateGeneratedForm.query.filter_by(probate_id=probate_id, form_code=form_code).first()
    if not gf or not os.path.exists(gf.file_path):
        flash('Form not found.', 'error')
        return redirect(f'/probate/{probate_id}/step/7')

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
        return redirect(f'/probate/{probate_id}/step/7')

    from documents.probate_generator import create_zip
    zip_path = os.path.join(tempfile.gettempdir(), f'probate_{probate_id[:8]}.zip')
    form_files = [{'form_code': f.form_code, 'file_path': f.file_path, 'form_name': f.form_name} for f in forms]
    create_zip(form_files, zip_path, as_pdf=(fmt == 'pdf'))
    return send_file(zip_path, as_attachment=True, download_name=f'probate_forms_{probate_id[:8]}.zip')


@app.route('/api/probate/<probate_id>/send-email', methods=['POST'])
@login_required
def api_probate_send_email(probate_id):
    """Email all generated probate forms as a ZIP attachment."""
    probate = db.session.get(ProbateApplication, probate_id)
    if not probate:
        return jsonify({'ok': False, 'error': 'Probate application not found'}), 404

    forms = ProbateGeneratedForm.query.filter_by(probate_id=probate_id).all()
    if not forms:
        return jsonify({'ok': False, 'error': 'No generated forms to send'}), 400

    data = request.get_json(silent=True) or {}
    to_email = (data.get('to_email') or '').strip()
    if not to_email or '@' not in to_email:
        return jsonify({'ok': False, 'error': 'Please enter a valid email address'}), 400

    fmt = data.get('format', 'pdf')  # pdf or docx

    # Build ZIP attachment
    from documents.probate_generator import create_zip
    zip_path = os.path.join(tempfile.gettempdir(), f'probate_email_{probate_id[:8]}.zip')
    form_files = [{'form_code': f.form_code, 'file_path': f.file_path, 'form_name': f.form_name} for f in forms]
    create_zip(form_files, zip_path, as_pdf=(fmt == 'pdf'))
    with open(zip_path, 'rb') as f:
        zip_data = f.read()

    # Determine sender & CC
    tenant = get_tenant()
    user_role = session.get('user_role', '')
    user_name = session.get('user_name', 'Unknown')
    brand = tenant.get('brand', 'WillCraft AI')

    # CC: merge user-provided CC, tenant CC, and auto-CC approver for admin/advisor
    cc_list = list(tenant.get('email_cc', []))
    user_cc = (data.get('cc') or '').strip()
    if user_cc:
        for addr in user_cc.split(','):
            addr = addr.strip()
            if addr and '@' in addr and addr not in cc_list:
                cc_list.append(addr)
    if user_role in ('admin', 'advisor'):
        approvers = User.query.filter_by(role='approver', is_active=True).all()
        for ap in approvers:
            if ap.email and ap.email not in cc_list:
                cc_list.append(ap.email)

    # Use user-provided subject and body, with defaults
    deceased_name = probate.deceased_name or 'the estate'
    subject = (data.get('subject') or '').strip() or f"Probate Forms — {deceased_name}"
    user_body = (data.get('body') or '').strip()

    if user_body:
        # Convert plain text body to HTML (preserve line breaks)
        import html as html_mod
        body_escaped = html_mod.escape(user_body).replace('\n', '<br>')
        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <p>{body_escaped}</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="font-size: 12px; color: #718096;">
                This email and its attachments are confidential and intended solely for the addressee.
            </p>
        </div>
        """
    else:
        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <p>Dear Sir/Madam,</p>
            <p>Please find attached the probate court forms for the estate of <strong>{deceased_name}</strong>.</p>
            <p>The attached ZIP file contains {len(forms)} form(s) in {fmt.upper()} format.</p>
            <br>
            <p>Best regards,<br><strong>{user_name}</strong><br>{brand}</p>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="font-size: 12px; color: #718096;">
                This email and its attachments are confidential and intended solely for the addressee.
            </p>
        </div>
        """

    from_email = tenant.get('email_from')
    if not from_email:
        user = db.session.get(User, session.get('user_id'))
        from_email = user.email if user else None
    if not from_email:
        return jsonify({'ok': False, 'error': 'No sender email configured. Contact admin.'}), 400

    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders

        sender_addr = SMTP_USER or from_email
        msg = MIMEMultipart()
        msg['From'] = sender_addr
        msg['To'] = to_email
        msg['Subject'] = subject
        if from_email and from_email != sender_addr:
            msg['Reply-To'] = from_email
        if cc_list:
            msg['Cc'] = ', '.join(cc_list)
        msg.attach(MIMEText(body_html, 'html'))

        part = MIMEBase('application', 'octet-stream')
        part.set_payload(zip_data)
        encoders.encode_base64(part)
        ext = 'pdf' if fmt == 'pdf' else 'docx'
        part.add_header('Content-Disposition', f'attachment; filename="probate_forms_{probate_id[:8]}.zip"')
        msg.attach(part)

        all_recipients = [to_email] + cc_list
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(sender_addr, all_recipients, msg.as_string())

        app.logger.info(f'Probate {probate_id} forms emailed to {to_email} by {user_name} (cc: {cc_list})')
        return jsonify({
            'ok': True,
            'sent_to': to_email,
            'cc': cc_list,
            'message': f'Forms emailed to {to_email}',
        })
    except Exception as e:
        app.logger.error(f'Probate email failed: {e}')
        return jsonify({'ok': False, 'error': f'Failed to send email: {str(e)}'}), 500


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
