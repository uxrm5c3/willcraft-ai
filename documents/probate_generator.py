"""Probate form generator — fills .docx templates with placeholder replacement."""
import copy
import json
import os
import re
import uuid
import zipfile
from datetime import datetime
from docx import Document


def replace_in_paragraph(paragraph, replacements):
    """Replace placeholders in a paragraph, handling split runs."""
    full_text = ''.join(run.text for run in paragraph.runs)
    if not full_text or '{{' not in full_text:
        return False

    new_text = full_text
    for placeholder, value in replacements.items():
        if placeholder in new_text:
            new_text = new_text.replace(placeholder, str(value) if value else '')

    if new_text != full_text and paragraph.runs:
        # Keep first run's formatting, clear others
        paragraph.runs[0].text = new_text
        for run in paragraph.runs[1:]:
            run.text = ""
        return True
    return False


def fill_template(template_path, replacements, output_path):
    """Open a .docx template, replace all {{PLACEHOLDER}} markers, save.

    Args:
        template_path: Path to the .docx template file
        replacements: Dict mapping {{PLACEHOLDER}} -> value
        output_path: Path to save the generated .docx file

    Returns:
        output_path on success
    """
    doc = Document(template_path)

    # Process all paragraphs in main body
    for para in doc.paragraphs:
        replace_in_paragraph(para, replacements)

    # Process tables (many court forms have tables)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    replace_in_paragraph(para, replacements)

    # Process headers and footers
    for section in doc.sections:
        if section.header:
            for para in section.header.paragraphs:
                replace_in_paragraph(para, replacements)
            for table in section.header.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            replace_in_paragraph(para, replacements)
        if section.footer:
            for para in section.footer.paragraphs:
                replace_in_paragraph(para, replacements)
            for table in section.footer.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            replace_in_paragraph(para, replacements)

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc.save(output_path)
    return output_path


def build_replacements(probate_app, will_record):
    """Build the full replacement dict from probate application + will data.

    Args:
        probate_app: ProbateApplication model instance
        will_record: Will model instance (can be None for LA)

    Returns:
        Dict of {{PLACEHOLDER}} -> value
    """
    is_la = probate_app.application_type == 'la'

    if is_la or not will_record:
        # LA: deceased/applicant from probate fields
        testator = {
            'full_name': probate_app.deceased_name or '',
            'nric_passport': probate_app.deceased_nric or '',
            'residential_address': probate_app.deceased_address or '',
        }
        primary_exec = {
            'full_name': probate_app.applicant_name or '',
            'nric_passport': probate_app.applicant_nric or '',
            'address': probate_app.applicant_address or '',
            'relationship': probate_app.applicant_relationship or '',
        }
        beneficiaries = json.loads(probate_app.beneficiaries_data or '[]')
        gifts = json.loads(probate_app.assets_data or '[]')
    else:
        # Probate: from will data
        testator = json.loads(will_record.step1_data or '{}')
        step2 = json.loads(will_record.step2_data or '{}')
        executors = step2.get('executors', []) if isinstance(step2, dict) else step2
        primary_exec = executors[0] if executors else {}
        beneficiaries = json.loads(will_record.step4_data or '[]')
        gifts = json.loads(will_record.step5_data or '[]')

    # Build applicant initials for exhibit references (e.g., TLL from TAN LI LI)
    applicant_name = primary_exec.get('full_name', probate_app.witness1_name or '')
    initials = ''.join(word[0] for word in applicant_name.split() if word) if applicant_name else 'APP'

    replacements = {
        # Deceased (testator/si mati)
        '{{DECEASED_NAME}}': testator.get('full_name', ''),
        '{{DECEASED_NRIC}}': testator.get('nric_passport', ''),
        '{{DECEASED_ADDRESS}}': testator.get('residential_address', ''),

        # Death details
        '{{DATE_OF_DEATH}}': probate_app.date_of_death or '',
        '{{TIME_OF_DEATH}}': probate_app.time_of_death or '',
        '{{PLACE_OF_DEATH}}': probate_app.place_of_death or '',
        '{{DEATH_CERT_NO}}': probate_app.death_cert_number or '',

        # Applicant (primary executor)
        '{{APPLICANT_NAME}}': primary_exec.get('full_name', ''),
        '{{APPLICANT_NRIC}}': primary_exec.get('nric_passport', ''),
        '{{APPLICANT_ADDRESS}}': primary_exec.get('address', ''),
        '{{APPLICANT_RELATIONSHIP}}': primary_exec.get('relationship', ''),
        '{{APPLICANT_CAPACITY}}': 'waris kadimnya',

        # Court details
        '{{COURT_LOCATION}}': probate_app.court_location or '',
        '{{COURT_STATE}}': probate_app.court_state or '',
        '{{CASE_NUMBER}}': probate_app.case_number or '',
        '{{FILING_YEAR}}': probate_app.filing_year or str(datetime.now().year),

        # Law firm
        '{{FIRM_NAME}}': probate_app.firm_name or '',
        '{{FIRM_NAME_UPPER}}': (probate_app.firm_name or '').upper(),
        '{{FIRM_ADDRESS}}': probate_app.firm_address or '',
        '{{FIRM_PHONE}}': probate_app.firm_phone or '',
        '{{FIRM_FAX}}': probate_app.firm_fax or '',
        '{{FIRM_REFERENCE}}': probate_app.firm_reference or '',

        # Lawyer — in Doc 08 the "appointing person" is the APPLICANT, not the lawyer
        # The lawyer name/NRIC only appears for physical attestation (Form 14A/346)
        '{{LAWYER_NAME}}': primary_exec.get('full_name', ''),
        '{{LAWYER_NRIC}}': primary_exec.get('nric_passport', ''),
        '{{LAWYER_BAR_NO}}': '',

        # Witnesses
        '{{WITNESS1_NAME}}': probate_app.witness1_name or '',
        '{{WITNESS1_NRIC}}': probate_app.witness1_nric or '',
        '{{WITNESS1_ADDRESS}}': probate_app.witness1_address or '',
        '{{WITNESS2_NAME}}': probate_app.witness2_name or '',
        '{{WITNESS2_NRIC}}': probate_app.witness2_nric or '',
        '{{WITNESS2_ADDRESS}}': probate_app.witness2_address or '',

        # Estate value
        '{{ESTATE_VALUE}}': probate_app.estate_value_estimate or '',
        '{{ESTATE_VALUE_WORDS}}': probate_app.estate_value_estimate or '',

        # Exhibit references
        '{{EXHIBIT_1}}': f'{initials}-1',
        '{{EXHIBIT_2}}': f'{initials}-2',
        '{{EXHIBIT_3}}': f'{initials}-3',
        '{{EXHIBIT_4}}': f'{initials}-4',

        # Beneficiary list (for Doc 07)
        '{{BENEFICIARY1_RELATIONSHIP}}': '',

        # Date
        '{{TODAY_DATE}}': datetime.now().strftime('%d-%m-%Y'),
    }

    # Build firm address lines for forms that use multi-line format
    if probate_app.firm_address:
        # Handle both real newlines and literal \n from form input
        addr_text = probate_app.firm_address.replace('\\n', '\n')
        addr_lines = [l.strip() for l in addr_text.split('\n') if l.strip()]
        for i, line in enumerate(addr_lines[:4], 1):
            replacements[f'{{{{FIRM_ADDRESS_LINE{i}}}}}'] = line
        # Also update the full address with proper formatting
        replacements['{{FIRM_ADDRESS}}'] = ', '.join(addr_lines)
    # Ensure all FIRM_ADDRESS_LINE placeholders have values (empty if not set)
    for i in range(1, 5):
        key = f'{{{{FIRM_ADDRESS_LINE{i}}}}}'
        if key not in replacements:
            replacements[key] = ''

    # Build beneficiary info
    if beneficiaries:
        ben_lines = []
        for i, b in enumerate(beneficiaries):
            name = b.get('beneficiary_name', b.get('full_name', ''))
            nric = b.get('nric_passport_birthcert', b.get('nric_passport', ''))
            rel = b.get('relationship', '')
            ben_lines.append(f'{name} (No. K/P: {nric}), {rel}.')
        replacements['{{BENEFICIARY1_RELATIONSHIP}}'] = ben_lines[0].split('), ')[-1].rstrip('.') if ben_lines else ''

    # Property placeholders (for Form 14A/346)
    properties = [g for g in gifts if g.get('gift_type') == 'property']
    if properties:
        p = properties[0]
        details = p.get('property_details', {})
        replacements.update({
            '{{PROPERTY1_TITLE}}': details.get('title_number', ''),
            '{{PROPERTY1_LOT}}': details.get('lot_number', ''),
            '{{PROPERTY1_MUKIM}}': details.get('mukim', ''),
            '{{PROPERTY1_ADDRESS}}': details.get('address', ''),
            '{{PROPERTY2_TITLE}}': details.get('title_number', ''),
            '{{PROPERTY2_LOT}}': details.get('lot_number', ''),
            '{{PROPERTY2_MUKIM}}': details.get('mukim', ''),
        })

    # Vehicle/bank placeholders (for Doc 06)
    replacements.update({
        '{{VEHICLE1_DESC}}': '',
        '{{VEHICLE1_REGNO}}': '',
        '{{VEHICLE1_ENGINE}}': '',
        '{{VEHICLE1_CHASSIS}}': '',
        '{{BANK1_ACCNO}}': '',
        '{{BANK2_ACCNO}}': '',
    })

    return replacements


def recommend_forms(will_record, probate_app=None):
    """Analyze will/probate data and recommend which forms are needed.

    Returns list of dicts: {form_code, recommended, reason}
    """
    is_la = probate_app and probate_app.application_type == 'la'

    if is_la:
        assets = json.loads(probate_app.assets_data or '[]')
        has_property = any(a.get('asset_type') == 'property' for a in assets)
    elif will_record:
        gifts = json.loads(will_record.step5_data or '[]')
        has_property = any(g.get('gift_type') == 'property' for g in gifts)
    else:
        has_property = False

    recommendations = [
        {
            'form_code': 'doc01',
            'recommended': True,
            'reason': 'Required — the main court filing to start the application',
        },
        {
            'form_code': 'doc02',
            'recommended': True,
            'reason': "Required — applicant's sworn statement about the estate",
        },
        {
            'form_code': 'doc03',
            'recommended': True,
            'reason': "Required — applicant's oath to manage the estate honestly",
        },
        {
            'form_code': 'doc04',
            'recommended': not is_la,
            'reason': 'Not needed — no will witnesses for LA' if is_la
                      else 'Recommended — sworn statement from the first will witness',
        },
        {
            'form_code': 'doc05',
            'recommended': not is_la,
            'reason': 'Not needed — no will witnesses for LA' if is_la
                      else 'Recommended — sworn statement from the second will witness',
        },
        {
            'form_code': 'doc06',
            'recommended': True,
            'reason': "Required — list of the deceased's assets and debts",
        },
        {
            'form_code': 'doc07',
            'recommended': True,
            'reason': 'Required — list of people who will inherit',
        },
        {
            'form_code': 'doc08',
            'recommended': True,
            'reason': 'Required — formal notice of lawyer appointment',
        },
        {
            'form_code': 'form14a',
            'recommended': has_property,
            'reason': 'Needed — transfers property from deceased to beneficiary' if has_property
                      else 'Not needed — no property in the estate',
        },
        {
            'form_code': 'form346',
            'recommended': has_property,
            'reason': 'Needed — registers executor at land office for property' if has_property
                      else 'Not needed — no property in the estate',
        },
    ]
    return recommendations


def generate_probate_forms(probate_app, will_record, selected_codes, templates_map, output_dir):
    """Generate all selected probate forms.

    Args:
        probate_app: ProbateApplication model instance
        will_record: Will model instance
        selected_codes: List of form_code strings to generate
        templates_map: Dict mapping form_code -> template file path
        output_dir: Directory to save generated files

    Returns:
        List of dicts: {form_code, form_name, file_path}
    """
    replacements = build_replacements(probate_app, will_record)
    results = []

    for code in selected_codes:
        template_path = templates_map.get(code)
        if not template_path or not os.path.exists(template_path):
            continue

        output_name = f'{code}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.docx'
        output_path = os.path.join(output_dir, output_name)

        try:
            fill_template(template_path, replacements, output_path)
            results.append({
                'form_code': code,
                'file_path': output_path,
            })
        except Exception as e:
            print(f"Error generating {code}: {e}")
            continue

    return results


def create_zip(form_files, zip_path):
    """Create a ZIP file containing all generated forms.

    Args:
        form_files: List of dicts with 'form_code' and 'file_path'
        zip_path: Path for the output ZIP file

    Returns:
        zip_path on success
    """
    os.makedirs(os.path.dirname(zip_path), exist_ok=True)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in form_files:
            if os.path.exists(f['file_path']):
                arcname = os.path.basename(f['file_path'])
                zf.write(f['file_path'], arcname)
    return zip_path
