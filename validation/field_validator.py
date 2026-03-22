"""
Field-level validation for identity and gift data.
Returns errors that can be displayed inline on forms.
"""
import re
from typing import List, Dict, Optional


def validate_nric(nric: str) -> Optional[str]:
    """Validate Malaysian NRIC format: YYMMDD-SS-NNNN."""
    if not nric:
        return "NRIC is required"
    nric = nric.strip().replace(' ', '')

    # Check format
    pattern = r'^\d{6}-\d{2}-\d{4}$'
    if not re.match(pattern, nric):
        return f"Invalid NRIC format. Expected YYMMDD-SS-NNNN (e.g., 850115-01-1234)"

    # Validate date portion
    yy, mm, dd = nric[:2], nric[2:4], nric[4:6]
    month = int(mm)
    day = int(dd)
    if month < 1 or month > 12:
        return f"Invalid month '{mm}' in NRIC"
    if day < 1 or day > 31:
        return f"Invalid day '{dd}' in NRIC"

    # Validate state code
    state_code = nric[7:9]
    valid_states = {
        '01', '21', '22', '23', '24',  # Johor
        '02', '25', '26', '27',        # Kedah
        '03', '28', '29',              # Kelantan
        '04', '30',                    # Malacca
        '05', '31', '59',              # Negeri Sembilan
        '06', '32', '33',              # Pahang
        '07', '34', '35',              # Penang
        '08', '36', '37', '38', '39',  # Perak
        '09', '40',                    # Perlis
        '10', '41', '42', '43', '44',  # Selangor
        '11', '45', '46',              # Terengganu
        '12', '47', '48', '49',        # Sabah
        '13', '50', '51', '52', '53',  # Sarawak
        '14', '54', '55', '56', '57',  # KL
        '15', '58',                    # Labuan
        '16',                          # Putrajaya
        '82',                          # Unknown state
        '60', '61', '62', '63', '64', '65', '66', '67', '68',  # Foreign born
        '71', '72', '74',              # Foreign country
        '98', '99',                    # Stateless / refugee
    }
    if state_code not in valid_states:
        return f"Invalid state code '{state_code}' in NRIC"

    return None  # Valid


def validate_person(person: Dict) -> List[Dict]:
    """Validate a person's data. Returns list of {field, message, severity}."""
    errors = []

    # Full name
    name = (person.get('full_name') or '').strip()
    if not name:
        errors.append({'field': 'full_name', 'message': 'Full name is required', 'severity': 'error'})
    elif len(name) < 3:
        errors.append({'field': 'full_name', 'message': 'Name seems too short', 'severity': 'warning'})

    # NRIC / Passport
    nric = (person.get('nric_passport') or '').strip()
    if not nric:
        errors.append({'field': 'nric_passport', 'message': 'NRIC/Passport is required', 'severity': 'error'})
    else:
        nric_error = validate_nric(nric)
        # Only validate as NRIC if it looks like one (has dashes and digits only)
        if re.match(r'^\d[\d-]+\d$', nric) and nric_error:
            errors.append({'field': 'nric_passport', 'message': nric_error, 'severity': 'error'})

    # Address
    address = (person.get('address') or '').strip()
    if not address:
        errors.append({'field': 'address', 'message': 'Address is required for will drafting', 'severity': 'warning'})
    elif len(address) < 10:
        errors.append({'field': 'address', 'message': 'Address seems incomplete', 'severity': 'warning'})

    return errors


def validate_property_details(prop: Dict) -> List[Dict]:
    """Validate property gift details. Returns list of {field, message, severity}."""
    errors = []

    # Property address
    addr = (prop.get('property_address') or '').strip()
    if not addr:
        errors.append({'field': 'property_address', 'message': 'Property address is required', 'severity': 'error'})

    # Title type
    title_type = (prop.get('title_type') or '').strip()
    if not title_type:
        errors.append({'field': 'title_type', 'message': 'Title type is required for will drafting', 'severity': 'warning'})

    # Title number
    title_num = (prop.get('title_number') or '').strip()
    if not title_num:
        errors.append({'field': 'title_number', 'message': 'Title number is required', 'severity': 'warning'})

    # Lot number
    lot_num = (prop.get('lot_number') or '').strip()
    if not lot_num:
        errors.append({'field': 'lot_number', 'message': 'Lot/PT number is required', 'severity': 'warning'})

    # Mukim
    mukim = (prop.get('bandar_pekan') or prop.get('mukim') or '').strip()
    if not mukim:
        errors.append({'field': 'bandar_pekan', 'message': 'Mukim is required', 'severity': 'warning'})

    # Daerah
    daerah = (prop.get('daerah') or '').strip()
    if not daerah:
        errors.append({'field': 'daerah', 'message': 'Daerah (District) is required', 'severity': 'warning'})

    # Negeri (State)
    negeri = (prop.get('negeri') or prop.get('state') or '').strip()
    if not negeri:
        errors.append({'field': 'negeri', 'message': 'Negeri (State) is required for probate', 'severity': 'error'})

    # Check for duplicate data in address
    if addr and negeri:
        addr_upper = addr.upper()
        # Count occurrences of postcode+city pattern
        postcode = (prop.get('postcode') or '').strip()
        city = (prop.get('city') or '').strip()
        if postcode and city:
            pattern = f"{postcode}.*{city}"
            matches = re.findall(pattern, addr_upper, re.IGNORECASE)
            if len(matches) > 1:
                errors.append({'field': 'property_address', 'message': 'Address contains duplicate postcode/city data', 'severity': 'warning'})

    return errors
