"""
WillCraft AI - Malaysian AI Will Writing System
Flask application with multi-step wizard for will drafting.
"""

from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
import json
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(__file__))

from config import FLASK_SECRET_KEY, ANTHROPIC_API_KEY, SQLALCHEMY_DATABASE_URI, DATA_DIR, UPLOAD_DIR
from database import db, Client, Will, Person, Document

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

db.init_app(app)


@app.context_processor
def inject_testator_id():
    """Make testator_person_id available to all templates."""
    return {'testator_person_id': session.get('step1', {}).get('person_id', '')}


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
        will_record = Will(client_id=client_id)
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
    will_record.generated_will_text = session.get('generated_will_text')
    will_record.title = f"Will of {step1.get('full_name', 'Unknown')}"
    if session.get('generated_will_text'):
        will_record.status = 'generated'

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
    session['generated_will_text'] = will_record.generated_will_text
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
    """Refresh the session person registry from DB."""
    persons = Person.query.filter_by(client_id=client_id).order_by(Person.full_name).all()
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
    if not trustee_same_as_executor:
        trustees_raw = trustee_session.get('trustees', [])
        if trustees_raw:
            trustees = [Trustee(**t) for t in trustees_raw if t.get('full_name')]
            if not trustees:
                trustees = None
        sub_trustee_raw = trustee_session.get('substitute_trustee', {})
        if sub_trustee_raw and sub_trustee_raw.get('full_name'):
            substitute_trustee = Trustee(**sub_trustee_raw)

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
        guardians=guardians,
        guardian_allowance=guardian_allowance,
        beneficiaries=beneficiaries,
        gifts=gifts,
        residuary_estate=residuary_estate,
        testamentary_trust=testamentary_trust,
        other_matters=other_matters,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    """Landing page."""
    saved_wills = Will.query.order_by(Will.updated_at.desc()).all()
    return render_template('index.html', saved_wills=saved_wills)


# -- Save / Load / Delete Wills ------------------------------------------------

@app.route('/api/will/save', methods=['POST'])
def api_will_save():
    """AJAX endpoint to save current session to DB."""
    try:
        will_record = save_will_to_db()
        return jsonify({'ok': True, 'will_id': will_record.id})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/wills')
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
    client_groups = []
    for c in all_clients:
        wills = Will.query.filter_by(client_id=c.id).order_by(Will.updated_at.desc()).all()
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
def will_load(will_id):
    """Load a saved will into the session."""
    will_record = db.session.get(Will, will_id)
    if not will_record:
        flash('Will not found.', 'error')
        return redirect(url_for('index'))
    load_will_to_session(will_record)
    flash(f'Loaded: {will_record.title}', 'info')
    return redirect(url_for('wizard_step_identities'))


@app.route('/wills/<will_id>/delete', methods=['POST'])
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
def api_persons_update(person_id):
    """Update an existing person identity."""
    person = db.session.get(Person, person_id)
    if not person:
        return jsonify({'ok': False, 'error': 'Person not found'}), 404
    data = request.get_json() or {}
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
    db.session.commit()
    _refresh_session_person_registry(person.client_id)
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
def api_persons_delete(person_id):
    """Delete a person identity."""
    person = db.session.get(Person, person_id)
    if not person:
        return jsonify({'ok': False, 'error': 'Person not found'}), 404
    client_id = person.client_id
    db.session.delete(person)
    db.session.commit()
    _refresh_session_person_registry(client_id)
    return jsonify({'ok': True})


# -- Upload & Document API ----------------------------------------------------

@app.route('/api/upload', methods=['POST'])
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


# -- OCR Extraction API -------------------------------------------------------

@app.route('/api/ocr/nric', methods=['POST'])
def api_ocr_nric():
    """Upload NRIC/passport image, extract data via Claude Vision."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
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
    try:
        from ai.ocr import extract_nric_data
        extracted = extract_nric_data(abs_path)
    except Exception as e:
        app.logger.error(f'OCR NRIC error: {e}')
        return jsonify({'ok': False, 'error': 'Could not scan the document. Please try again with a clearer image.'}), 500
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

    doc = Document(
        client_id=client_id, will_id=session.get('will_id'),
        filename=saved_name, original_filename=file.filename,
        file_path=rel_path, file_type=file.content_type,
        file_size=file_size, category='nric',
        extracted_data=json.dumps(extracted),
    )
    db.session.add(doc)
    db.session.commit()
    # NOTE: Do NOT auto-save person here. The user must review and
    # explicitly click "Save Identity" to create/update the person record.
    return jsonify({'ok': True, 'extracted': extracted, 'document_id': doc.id})


@app.route('/api/ocr/property', methods=['POST'])
def api_ocr_property():
    """Upload cukai tanah/cukai pintu, extract property data."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
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
    try:
        from ai.property_extractor import extract_property_data
        doc_type = request.form.get('doc_type', 'general')
        extracted = extract_property_data(abs_path, doc_type=doc_type)
    except Exception as e:
        app.logger.error(f'OCR property error: {e}')
        return jsonify({'ok': False, 'error': 'Could not read the property document. Please try again with a clearer image.'}), 500
    doc = Document(
        client_id=client_id, will_id=session.get('will_id'),
        filename=saved_name, original_filename=file.filename,
        file_path=rel_path, file_type=file.content_type,
        file_size=file_size, category='property',
        extracted_data=json.dumps(extracted),
    )
    db.session.add(doc)
    db.session.commit()
    return jsonify({'ok': True, 'extracted': extracted, 'document_id': doc.id})


@app.route('/api/ocr/asset', methods=['POST'])
def api_ocr_asset():
    """Upload bank/investment statement, extract asset data."""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
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
    try:
        from ai.asset_extractor import extract_asset_data
        extracted = extract_asset_data(abs_path)
    except Exception as e:
        app.logger.error(f'OCR asset error: {e}')
        return jsonify({'ok': False, 'error': 'Could not read the financial document. Please try again with a clearer image.'}), 500
    doc = Document(
        client_id=client_id, will_id=session.get('will_id'),
        filename=saved_name, original_filename=file.filename,
        file_path=rel_path, file_type=file.content_type,
        file_size=file_size, category='financial',
        extracted_data=json.dumps(extracted),
    )
    db.session.add(doc)
    db.session.commit()
    return jsonify({'ok': True, 'extracted': extracted, 'document_id': doc.id})


@app.route('/client/documents')
def client_documents():
    """Client document browser page (legacy — redirects to new client files page)."""
    client_id = session.get('client_id')
    if client_id:
        return redirect(url_for('client_files', client_id=client_id))
    documents = []
    return render_template('client_documents.html', documents=documents)


@app.route('/clients')
def clients_list():
    """Redirect to unified /wills page (backward compatibility)."""
    q = request.args.get('q', '')
    if q:
        return redirect(url_for('will_list', q=q))
    return redirect(url_for('will_list'))


@app.route('/clients/<client_id>/files')
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
def upload_will():
    """Page to upload an existing will for parsing."""
    return render_template('upload_will.html')


@app.route('/api/parse-will', methods=['POST'])
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
    return redirect(url_for('wizard_step_testator'))


# -- Step 2: Testator Info (simplified - select identity) --------------------

@app.route('/wizard/step/2', methods=['GET', 'POST'])
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
    return redirect(url_for('wizard_step_executors'))


# -- Step 3: Executors (select from identities) -----------------------------

@app.route('/wizard/step/3', methods=['GET', 'POST'])
def wizard_step_executors():
    if request.method == 'GET':
        return render_template(
            'wizard/step3_executors.html',
            current_step=3,
            completed_steps=get_completed_steps(),
            data={
                'executors': session.get('step2_executors', [{}]),
                'executor_type': session.get('step3_executor_type', 'single'),
                'trustee_data': session.get('step3_trustees', {'same_as_executor': True, 'trustees': [{}]}),
            },
            persons=session.get('person_registry', []),
            beneficiaries=session.get('step4_beneficiaries', []),
        )

    # POST -- parse executor and trustee data
    executor_type = request.form.get('executor_type', 'single')
    count = int(request.form.get('executor_count', 1))
    executors = []
    for i in range(count):
        person_id = request.form.get(f'exec_person_id_{i}', '').strip()
        person = _get_person_from_registry(person_id)
        if not person:
            continue
        role = request.form.get(f'exec_role_{i}', 'Primary')
        if executor_type == 'joint':
            role = 'Joint'
        elif executor_type == 'single':
            role = 'Primary'
        executors.append({
            'person_id': person_id,
            'full_name': person['full_name'],
            'nric_passport': person['nric_passport'],
            'address': person['address'],
            'relationship': request.form.get(f'exec_relationship_{i}', '').strip(),
            'role': role,
        })

    # Substitute executor
    sub_exec_pid = request.form.get('sub_exec_person_id', '').strip()
    if sub_exec_pid:
        sub_person = _get_person_from_registry(sub_exec_pid)
        if sub_person:
            executors.append({
                'person_id': sub_exec_pid,
                'full_name': sub_person['full_name'],
                'nric_passport': sub_person['nric_passport'],
                'address': sub_person['address'],
                'relationship': request.form.get('sub_exec_relationship', '').strip(),
                'role': 'Substitute',
            })

    session['step2_executors'] = executors
    session['step3_executor_type'] = executor_type

    # Parse trustees
    trustee_same = bool(request.form.get('trustee_same_as_executor'))
    trustee_data = {'same_as_executor': trustee_same, 'trustees': [], 'substitute_trustee': {}}

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
            })

        # Substitute trustee
        sub_tr_pid = request.form.get('sub_trustee_person_id', '').strip()
        if sub_tr_pid:
            sub_tr = _get_person_from_registry(sub_tr_pid)
            if sub_tr:
                trustee_data['substitute_trustee'] = {
                    'person_id': sub_tr_pid,
                    'full_name': sub_tr['full_name'],
                    'nric_passport': sub_tr['nric_passport'],
                    'address': sub_tr['address'],
                    'relationship': request.form.get('sub_trustee_relationship', '').strip(),
                }

    session['step3_trustees'] = trustee_data
    session.modified = True
    mark_step_complete(3)
    save_will_to_db()
    return redirect(url_for('wizard_step_guardians'))


# -- Step 4: Guardians (select from identities, optional) -------------------

@app.route('/wizard/step/4', methods=['GET', 'POST'])
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
    return redirect(url_for('wizard_step_beneficiaries'))


# -- Step 5: Beneficiaries (select from identities) -------------------------

@app.route('/wizard/step/5', methods=['GET', 'POST'])
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
        })

    session['step4_beneficiaries'] = beneficiaries
    session.modified = True
    mark_step_complete(5)
    save_will_to_db()
    return redirect(url_for('wizard_step_gifts'))


# -- Step 6: Gifts (optional) ------------------------------------------------

@app.route('/wizard/step/6', methods=['GET', 'POST'])
def wizard_step_gifts():
    if request.method == 'GET':
        return render_template(
            'wizard/step6_gifts.html',
            current_step=6,
            completed_steps=get_completed_steps(),
            data={'gifts': session.get('step5_gifts', [])},
            beneficiaries=session.get('step4_beneficiaries', []),
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
            property_details = {
                'property_address': request.form.get(f'gift_prop_address_{gi}', '').strip(),
                'title_type': request.form.get(f'gift_prop_title_type_{gi}', '').strip(),
                'title_number': request.form.get(f'gift_prop_title_number_{gi}', '').strip(),
                'lot_number': request.form.get(f'gift_prop_lot_number_{gi}', '').strip(),
                'bandar_pekan': request.form.get(f'gift_prop_bandar_{gi}', '').strip(),
                'daerah': request.form.get(f'gift_prop_daerah_{gi}', '').strip(),
                'negeri': request.form.get(f'gift_prop_negeri_{gi}', '').strip(),
            }
            if not property_details['property_address']:
                continue

        # Parse structured financial details
        financial_details = {}
        if gift_type == 'financial':
            financial_details = {
                'institution': request.form.get(f'gift_fin_institution_{gi}', '').strip(),
                'account_number': request.form.get(f'gift_fin_account_{gi}', '').strip(),
                'asset_type': request.form.get(f'gift_fin_type_{gi}', '').strip(),
                'description': request.form.get(f'gift_fin_desc_{gi}', '').strip(),
            }
            if not financial_details['institution'] and not financial_details['description']:
                continue

        # For "other" type, skip if no description
        if gift_type == 'other' and not desc:
            continue

        subject_to_trust = bool(request.form.get(f'gift_trust_{gi}'))
        subject_to_guardian_allowance = bool(request.form.get(f'gift_guardian_allowance_{gi}'))

        alloc_count = int(request.form.get(f'gift_{gi}_alloc_count', 0))
        allocations = []
        for ai_idx in range(alloc_count):
            ben_name = request.form.get(f'gift_{gi}_alloc_name_{ai_idx}', '').strip()
            if not ben_name:
                continue
            allocations.append({
                'beneficiary_name': ben_name,
                'share': request.form.get(f'gift_{gi}_alloc_share_{ai_idx}', '').strip(),
                'role': request.form.get(f'gift_{gi}_alloc_role_{ai_idx}', 'MB'),
            })

        gifts.append({
            'gift_type': gift_type,
            'description': desc,
            'property_details': property_details,
            'financial_details': financial_details,
            'allocations': allocations,
            'subject_to_trust': subject_to_trust,
            'subject_to_guardian_allowance': subject_to_guardian_allowance,
        })

    session['step5_gifts'] = gifts
    session.modified = True
    mark_step_complete(6)
    save_will_to_db()
    return redirect(url_for('wizard_step_residuary'))


# -- Step 7: Residuary Estate ------------------------------------------------

@app.route('/wizard/step/7', methods=['GET', 'POST'])
def wizard_step_residuary():
    if request.method == 'GET':
        return render_template(
            'wizard/step7_residuary.html',
            current_step=7,
            completed_steps=get_completed_steps(),
            data=session.get('step6_residuary', {}),
            beneficiaries=session.get('step4_beneficiaries', []),
        )

    # POST -- parse main beneficiaries and substitute groups
    main_count = int(request.form.get('main_beneficiary_count', 0))
    main_beneficiaries = []
    for i in range(main_count):
        name = request.form.get(f'main_ben_name_{i}', '').strip()
        if not name:
            continue
        main_beneficiaries.append({
            'beneficiary_name': name,
            'share': request.form.get(f'main_ben_share_{i}', '').strip(),
            'group': 'main',
        })

    # Substitute groups
    sub_group_count = int(request.form.get('substitute_group_count', 0))
    substitute_groups = []
    for gi in range(sub_group_count):
        sub_count = int(request.form.get(f'sub_group_{gi}_count', 0))
        group = []
        for si in range(sub_count):
            name = request.form.get(f'sub_group_{gi}_name_{si}', '').strip()
            if not name:
                continue
            group.append({
                'beneficiary_name': name,
                'share': request.form.get(f'sub_group_{gi}_share_{si}', '').strip(),
                'group': f'substitute_{gi + 1}',
            })
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
    return redirect(url_for('wizard_step_trust'))


# -- Step 8: Testamentary Trust (optional) ------------------------------------

@app.route('/wizard/step/8', methods=['GET', 'POST'])
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
            'property_use': request.form.get('immovable_property_action', '').strip() or None,
            'duration': request.form.get('trust_duration', '').strip() or None,
            'assets_from_gifts': request.form.getlist('gift_references'),
            'payment_mode': request.form.get('payment_mode', '').strip() or None,
            'payment_amount': request.form.get('payment_amount', '').strip() or None,
            'other_payment_mode': request.form.get('payment_mode_other', '').strip() or None,
            'balance_of_trust': request.form.get('balance_of_trust', '').strip() or None,
            'separate_trustee': bool(request.form.get('separate_trustee')),
            'trustee_person_id': request.form.get('trustee_person_id', '').strip() or None,
            'trustee_relationship': request.form.get('trustee_relationship', '').strip() or None,
        }
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
    return redirect(url_for('wizard_step_others'))


# -- Step 9: Other Matters (optional) ----------------------------------------

@app.route('/wizard/step/9', methods=['GET', 'POST'])
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

    additional = request.form.get('additional_instructions', '').strip()
    if additional:
        om_data['additional_instructions'] = additional

    session['step8_others'] = om_data
    session.modified = True
    mark_step_complete(9)
    save_will_to_db()
    return redirect(url_for('wizard_step_review'))


# -- Step 10: Review ---------------------------------------------------------

@app.route('/wizard/step/10', methods=['GET'])
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
    )


# -- Generate Will -----------------------------------------------------------

@app.route('/wizard/generate', methods=['POST'])
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

    session['generated_will_text'] = will_text
    session.modified = True
    save_will_to_db()
    return redirect(url_for('preview'))


# -- Preview -----------------------------------------------------------------

@app.route('/preview')
def preview():
    will_text = session.get('generated_will_text', '')
    if not will_text:
        flash('No will has been generated yet. Please complete the wizard first.', 'warning')
        return redirect(url_for('wizard_step_review'))

    testator_name = session.get('step1', {}).get('full_name', 'Unknown')
    return render_template(
        'preview.html',
        will_text=will_text,
        testator_name=testator_name,
    )


# -- Download -----------------------------------------------------------------

@app.route('/download/<fmt>')
def download(fmt):
    will_text = session.get('generated_will_text', '')
    if not will_text:
        flash('No will has been generated yet.', 'warning')
        return redirect(url_for('preview'))

    testator_name = session.get('step1', {}).get('full_name', 'Will')
    safe_name = "".join(c for c in testator_name if c.isalnum() or c in (' ', '-', '_')).strip()
    safe_name = safe_name.replace(' ', '_') or 'Will'

    if fmt == 'docx':
        from documents.docx_generator import generate_docx
        filepath = generate_docx(will_text, safe_name)
    elif fmt == 'pdf':
        from documents.pdf_generator import generate_pdf
        filepath = generate_pdf(will_text, safe_name)
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
def reset():
    session.clear()
    flash('Your session has been reset. You can start a new will.', 'info')
    return redirect(url_for('index'))


@app.route('/wizard/new')
def wizard_new():
    """Start a brand-new will by clearing the current session."""
    session.clear()
    return redirect(url_for('wizard_step_identities'))


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    app.run(debug=debug, port=port)
