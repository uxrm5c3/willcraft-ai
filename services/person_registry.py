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


def _address_looks_like_property(addr: str) -> bool:
    """🔥 §10x.231 — Detect when an address payload is actually a property
    legal description (LOT/PTD/HSD/GERAN/MUKIM tokens) rather than a
    residential address. Property docs (property_title / property_spa
    etc.) sometimes leak through writebacks into Person.address; this
    pollutes the will and the wizard. Refuse to write such payloads."""
    if not addr:
        return False
    upper = addr.upper()
    PROP_TOKENS = ('LOT ', 'PTD ', 'HSD ', 'HS(D)', 'GERAN', 'MUKIM',
                   'HAKMILIK', 'PERSIARAN MEDINI UTARA',
                   'NO. PETAK', 'NO. TINGKAT', 'PARCEL')
    return any(t in upper for t in PROP_TOKENS)


def ensure_person(client_id, name, nric='', address='', relationship='',
                  dob='', nationality='Malaysian', document_id=None,
                  source_category='nric'):
    """Create or update a Person for this client and return its id.

    🔥 §10x.41 — RELATIONSHIP IS REQUIRED FOR NEW PERSONS.
    A Person must have a role (Testator / Spouse / Son / Daughter /
    Father / Mother / Brother / Sister / Sister-in-law / Brother-in-law
    / etc.). Saving a Person with `relationship=None` produces ghost
    identities the chat / wizard / will can't bind to anything.

    For NEW rows: if relationship is empty, the function REFUSES to
    create the Person and returns None. Caller must ask the user for
    the role first.
    For EXISTING rows: opportunistic fill remains — empty fields get
    populated, populated fields stay (never overwrite).
    """
    if not name or not name.strip():
        return None
    nric = (nric or '').strip()
    clean_name = name.strip()
    rel = (relationship or '').strip()
    # 🔥 §10x.226 — collapse whitespace/newlines in address. Vision OCR
    # routinely returns IC addresses with embedded \n that pollute every
    # downstream consumer (will clause, wizard form, "Same as X" buttons).
    addr_clean = ' '.join((address or '').split()) if address else ''
    # 🔥 §10x.231 — REFUSE property-style addresses unless the source is
    # explicitly an NRIC document. Property gift saves / asset pipelines
    # MUST NOT write owner addresses to Person — the address on a property
    # doc is the PROPERTY's address, not the owner's residence.
    if addr_clean and source_category != 'nric' and _address_looks_like_property(addr_clean):
        try:
            from flask import current_app
            current_app.logger.warning(
                f'§10x.231 REFUSED property-style address for Person={clean_name!r} '
                f'source={source_category!r}: {addr_clean[:80]!r}'
            )
        except Exception:
            pass
        addr_clean = ''
    existing = Person.query.filter_by(client_id=client_id, full_name=clean_name).first()
    if existing:
        if not existing.nric_passport and nric:
            existing.nric_passport = nric
        if not existing.address and addr_clean:
            existing.address = addr_clean
        if not existing.relationship and rel:
            existing.relationship = rel
        if not existing.date_of_birth and dob:
            existing.date_of_birth = normalise_dob(dob)
        if document_id and not existing.document_id:
            existing.document_id = document_id
        db.session.flush()
        return existing.id
    # 🔥 §10x.41 — REFUSE to create a new Person without a role.
    # Returning None forces the caller to surface a role-question card
    # to the user instead of silently creating a ghost identity.
    if not rel:
        try:
            from flask import current_app
            current_app.logger.warning(
                f"§10x.41 ensure_person REFUSED: no relationship for {clean_name!r} "
                f"(client_id={client_id}). Caller must ask user for role first."
            )
        except Exception:
            pass
        return None
    new_p = Person(
        client_id=client_id,
        full_name=clean_name,
        nric_passport=nric,
        address=addr_clean or None,
        relationship=rel,
        date_of_birth=normalise_dob(dob) or None,
        nationality=nationality or 'Malaysian',
        document_id=document_id,
    )
    db.session.add(new_p)
    db.session.flush()
    return new_p.id
