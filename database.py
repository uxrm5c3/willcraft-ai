"""SQLAlchemy database models for WillCraft AI."""

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import uuid

db = SQLAlchemy()


class User(db.Model):
    """Application user with role-based access."""
    __tablename__ = 'users'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(200), nullable=False, unique=True)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    contact = db.Column(db.String(50), nullable=True)
    role = db.Column(db.String(20), default='advisor')  # admin, advisor, approver
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


# Role permissions mapping
# admin:    Create users, draft wills, submit for approval — cannot download/email until approved
# advisor:  Draft wills, submit for approval — cannot download/email until approved
# approver: Draft wills, approve/reject wills, download & email wills
ROLE_PERMS = {
    'admin':    {'canDraft': True, 'canSubmit': True, 'canApprove': False, 'canDownload': False, 'canEmail': False, 'canManageUsers': True},
    'advisor':  {'canDraft': True, 'canSubmit': True, 'canApprove': False, 'canDownload': False, 'canEmail': False, 'canManageUsers': False},
    'approver': {'canDraft': True, 'canSubmit': True, 'canApprove': True,  'canDownload': True,  'canEmail': True,  'canManageUsers': False},
}

ROLE_LABELS = {
    'admin': 'Admin',
    'advisor': 'Advisor',
    'approver': 'Approver',
}


class Client(db.Model):
    """A client/testator who may have multiple will drafts."""
    __tablename__ = 'clients'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    full_name = db.Column(db.String(200), nullable=False)
    nric_passport = db.Column(db.String(50), nullable=False, default='')
    email = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    wills = db.relationship('Will', backref='client', lazy=True)
    documents = db.relationship('Document', backref='client', lazy=True)
    persons = db.relationship('Person', backref='client', lazy=True)

    @property
    def folder_name(self):
        """Generate a filesystem-safe folder name: ClientName_shortid."""
        safe_name = "".join(
            c for c in self.full_name if c.isalnum() or c in (' ', '-', '_')
        ).strip().replace(' ', '_')
        short_id = self.id[:8]
        return f"{safe_name}_{short_id}"


class Will(db.Model):
    """A will draft with all wizard step data stored as JSON."""
    __tablename__ = 'wills'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_id = db.Column(db.String(36), db.ForeignKey('clients.id'), nullable=False)
    title = db.Column(db.String(200), default='Untitled Will')
    status = db.Column(db.String(20), default='draft')  # draft, generated, pending_approval, approved, rejected
    # Store each wizard step as JSON (step1=testator, step2=executors, etc.)
    identities_data = db.Column(db.Text, default='[]')  # identity snapshot
    step1_data = db.Column(db.Text, default='{}')
    step2_data = db.Column(db.Text, default='[]')
    step3_data = db.Column(db.Text, default='{}')
    step4_data = db.Column(db.Text, default='[]')
    step5_data = db.Column(db.Text, default='[]')
    step6_data = db.Column(db.Text, default='{}')
    step7_data = db.Column(db.Text, default='{}')
    step8_data = db.Column(db.Text, default='{}')
    completed_steps = db.Column(db.Text, default='[]')
    generated_will_text = db.Column(db.Text, nullable=True)
    # Approval workflow
    created_by = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=True)
    submitted_by = db.Column(db.String(36), nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    approved_by = db.Column(db.String(36), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    approval_remarks = db.Column(db.Text, nullable=True)
    # Approver edit tracking
    text_edited_by = db.Column(db.String(36), nullable=True)
    text_edited_at = db.Column(db.DateTime, nullable=True)
    include_logo = db.Column(db.Boolean, default=True)  # Include firm logo in PDF header
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = db.Column(db.DateTime, nullable=True)  # Soft delete — auto-purged after 30 days

    documents = db.relationship('Document', backref='will', lazy=True)
    edit_logs = db.relationship('WillEditLog', backref='will', lazy=True, order_by='WillEditLog.edited_at.desc()')
    versions = db.relationship('WillVersion', backref='will', lazy=True, order_by='WillVersion.version_number.desc()')

    # Relationships to User
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_wills')


class WillEditLog(db.Model):
    """Log of edits made to will text by approvers."""
    __tablename__ = 'will_edit_logs'
    id = db.Column(db.Integer, primary_key=True)
    will_id = db.Column(db.String(36), db.ForeignKey('wills.id'), nullable=False)
    edited_by = db.Column(db.String(36), nullable=False)
    edited_by_name = db.Column(db.String(100))
    edited_at = db.Column(db.DateTime, default=datetime.utcnow)
    summary = db.Column(db.Text)  # e.g. "3 lines changed, 1 line added"
    details = db.Column(db.Text)  # Full list of changes, one per line


class WillVersion(db.Model):
    """Stores each generated version of a will for history tracking."""
    __tablename__ = 'will_versions'
    id = db.Column(db.Integer, primary_key=True)
    will_id = db.Column(db.String(36), db.ForeignKey('wills.id'), nullable=False)
    version_number = db.Column(db.Integer, nullable=False, default=1)
    will_text = db.Column(db.Text, nullable=False)
    generated_by = db.Column(db.String(36), nullable=True)  # user_id who triggered generation
    generated_by_name = db.Column(db.String(100))
    note = db.Column(db.String(500), nullable=True)  # e.g. "Initial generation", "Re-generated after edits"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Person(db.Model):
    """Central registry of person identities."""
    __tablename__ = 'persons'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_id = db.Column(db.String(36), db.ForeignKey('clients.id'), nullable=False)
    # Core identity fields
    full_name = db.Column(db.String(200), nullable=False)
    nric_passport = db.Column(db.String(50), nullable=False)
    address = db.Column(db.Text, nullable=True)
    nationality = db.Column(db.String(100), default='Malaysian')
    passport_expiry = db.Column(db.String(20), nullable=True)
    # Optional fields (may be captured by OCR)
    date_of_birth = db.Column(db.String(20), nullable=True)
    gender = db.Column(db.String(10), nullable=True)
    email = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    # Linked document (NRIC/passport scan)
    document_id = db.Column(db.String(36), nullable=True)
    # Deprecated (kept for SQLite compat)
    relationship = db.Column(db.String(100), nullable=True)
    source_step = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Document(db.Model):
    """Uploaded document metadata."""
    __tablename__ = 'documents'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_id = db.Column(db.String(36), db.ForeignKey('clients.id'), nullable=False)
    will_id = db.Column(db.String(36), db.ForeignKey('wills.id'), nullable=True)
    filename = db.Column(db.String(300), nullable=False)
    original_filename = db.Column(db.String(300), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    file_type = db.Column(db.String(50))
    file_size = db.Column(db.Integer)
    category = db.Column(db.String(50))  # nric, property, financial, will, death_certificate
    description = db.Column(db.String(500), nullable=True)
    extracted_data = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ProbateApplication(db.Model):
    """A probate application linked to an approved will or standalone LA."""
    __tablename__ = 'probate_applications'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    application_type = db.Column(db.String(20), default='probate')  # probate (with will) or la (letters of administration)
    will_id = db.Column(db.String(36), db.ForeignKey('wills.id'), nullable=True)  # nullable for LA
    client_id = db.Column(db.String(36), db.ForeignKey('clients.id'), nullable=True)  # nullable for LA
    status = db.Column(db.String(20), default='draft')  # draft, generated, pending_approval, approved, rejected

    # Approval workflow
    submitted_by = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    approved_by = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    approval_notes = db.Column(db.Text, nullable=True)

    # Deceased details (for LA — manual entry; for probate — from will)
    deceased_name = db.Column(db.String(200), nullable=True)
    deceased_nric = db.Column(db.String(50), nullable=True)
    deceased_address = db.Column(db.Text, nullable=True)

    # Applicant details (for LA — manual entry; for probate — from will executor)
    applicant_name = db.Column(db.String(200), nullable=True)
    applicant_nric = db.Column(db.String(50), nullable=True)
    applicant_address = db.Column(db.Text, nullable=True)
    applicant_relationship = db.Column(db.String(100), nullable=True)

    # Assets (for LA — JSON array of asset dicts)
    assets_data = db.Column(db.Text, default='[]')
    # Beneficiaries (for LA — JSON array)
    beneficiaries_data = db.Column(db.Text, default='[]')

    # Death details
    death_cert_number = db.Column(db.String(100), nullable=True)
    date_of_death = db.Column(db.String(20), nullable=True)
    time_of_death = db.Column(db.String(20), nullable=True)
    place_of_death = db.Column(db.String(500), nullable=True)
    death_cert_document_id = db.Column(db.String(36), nullable=True)
    will_document_id = db.Column(db.String(36), nullable=True)
    estate_value_estimate = db.Column(db.String(100), nullable=True)

    # Court details
    court_location = db.Column(db.String(200), nullable=True)
    court_state = db.Column(db.String(200), nullable=True)
    case_number = db.Column(db.String(100), nullable=True)
    filing_year = db.Column(db.String(10), nullable=True)

    # Law firm details
    firm_name = db.Column(db.String(300), nullable=True)
    firm_address = db.Column(db.Text, nullable=True)
    firm_phone = db.Column(db.String(50), nullable=True)
    firm_fax = db.Column(db.String(50), nullable=True)
    firm_reference = db.Column(db.String(100), nullable=True)

    # Lawyer details
    lawyer_name = db.Column(db.String(200), nullable=True)
    lawyer_nric = db.Column(db.String(50), nullable=True)
    lawyer_bar_number = db.Column(db.String(50), nullable=True)

    # Will witnesses
    witness1_name = db.Column(db.String(200), nullable=True)
    witness1_nric = db.Column(db.String(50), nullable=True)
    witness1_address = db.Column(db.Text, nullable=True)
    witness2_name = db.Column(db.String(200), nullable=True)
    witness2_nric = db.Column(db.String(50), nullable=True)
    witness2_address = db.Column(db.Text, nullable=True)

    # Selected forms (JSON array of form codes)
    selected_forms = db.Column(db.Text, default='[]')
    # Consolidated form data (JSON)
    form_data_json = db.Column(db.Text, default='{}')

    created_by = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = db.Column(db.DateTime, nullable=True)  # Soft delete — auto-purged after 30 days

    will = db.relationship('Will', backref='probate_applications')
    client = db.relationship('Client', backref='probate_applications')
    generated_forms = db.relationship('ProbateGeneratedForm', backref='probate_application', lazy=True)
    creator = db.relationship('User', foreign_keys=[created_by])
    submitter = db.relationship('User', foreign_keys=[submitted_by])
    approver = db.relationship('User', foreign_keys=[approved_by])


class ProbateFormTemplate(db.Model):
    """Template for a probate court form."""
    __tablename__ = 'probate_form_templates'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    form_code = db.Column(db.String(20), nullable=False, unique=True)
    form_name = db.Column(db.String(200), nullable=False)
    form_name_malay = db.Column(db.String(200), nullable=True)
    description = db.Column(db.Text, nullable=True)
    file_path = db.Column(db.String(500), nullable=False)
    is_default = db.Column(db.Boolean, default=True)
    category = db.Column(db.String(20), default='core')  # core, witness, property
    requires_property = db.Column(db.Boolean, default=False)
    requires_witnesses = db.Column(db.Boolean, default=False)
    sort_order = db.Column(db.Integer, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProbateGeneratedForm(db.Model):
    """A generated probate form document."""
    __tablename__ = 'probate_generated_forms'
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    probate_id = db.Column(db.String(36), db.ForeignKey('probate_applications.id'), nullable=False)
    form_code = db.Column(db.String(20), nullable=False)
    form_name = db.Column(db.String(200), nullable=True)
    file_path = db.Column(db.String(500), nullable=False)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
