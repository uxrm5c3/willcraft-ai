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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    documents = db.relationship('Document', backref='will', lazy=True)

    # Relationships to User
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_wills')


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
    category = db.Column(db.String(50))  # nric, property, financial, will
    description = db.Column(db.String(500), nullable=True)
    extracted_data = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
