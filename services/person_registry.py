"""Shared helpers for the central Person registry.

Both the upload-will flow (api_parse_will) and the chat flow create
Person rows from extracted data. Centralising here keeps the matching
rules consistent — wizard pickers in steps 4/5 rely on Person.full_name
matches, so divergent creation logic causes empty pickers and step
submission errors.
"""
from database import db, Person


def normalise_dob(dob):
    """Accept DD/MM/YYYY or YYYY-MM-DD. Return YYYY-MM-DD."""
    if not dob:
        return ''
    if '/' in dob:
        parts = dob.split('/')
        if len(parts) == 3 and len(parts[-1]) == 4:
            return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
    return dob


def ensure_person(client_id, name, nric='', address='', relationship='',
                  dob='', nationality='Malaysian', document_id=None):
    """Create or update a Person for this client and return its id.

    Matches existing persons by exact full_name (per client). Empty fields
    on the existing row are filled in opportunistically; existing values
    are never overwritten.
    """
    if not name or not name.strip():
        return None
    nric = (nric or '').strip()
    clean_name = name.strip()
    existing = Person.query.filter_by(client_id=client_id, full_name=clean_name).first()
    if existing:
        if not existing.nric_passport and nric:
            existing.nric_passport = nric
        if not existing.address and address:
            existing.address = address
        if not existing.relationship and relationship:
            existing.relationship = relationship
        if not existing.date_of_birth and dob:
            existing.date_of_birth = normalise_dob(dob)
        if document_id and not existing.document_id:
            existing.document_id = document_id
        db.session.flush()
        return existing.id
    new_p = Person(
        client_id=client_id,
        full_name=clean_name,
        nric_passport=nric,
        address=address or None,
        relationship=relationship or None,
        date_of_birth=normalise_dob(dob) or None,
        nationality=nationality or 'Malaysian',
        document_id=document_id,
    )
    db.session.add(new_p)
    db.session.flush()
    return new_p.id
