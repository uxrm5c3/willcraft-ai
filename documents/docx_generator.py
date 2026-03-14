"""Generate a Word document (.docx) from will text using python-docx.

Follows Rockwills Trustee Berhad professional format:
- Running header: "LAST WILL AND TESTAMENT OF [TESTATOR NAME]" on every page
- Running footer: Page number, Testator/Witness1/Witness2 signature spaces
- Proper signing/attestation page layout
"""

import os
import tempfile
from docx import Document
from docx.shared import Pt, Inches, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml


def _extract_testator_name(will_text: str) -> str:
    """Extract testator name from will text."""
    lines = will_text.strip().split('\n')
    for i, line in enumerate(lines):
        if 'LAST WILL AND TESTAMENT OF' in line.upper():
            after = line.upper().replace('LAST WILL AND TESTAMENT OF', '').strip()
            if after:
                return after
            for j in range(i + 1, min(i + 3, len(lines))):
                candidate = lines[j].strip()
                if candidate:
                    return candidate.upper()
    return "THE TESTATOR"


def _split_signing_page(will_text: str):
    """Split will text into main content and signing page section."""
    markers = ['Signature of the Testator', 'SIGNATURE OF THE TESTATOR']
    for marker in markers:
        idx = will_text.find(marker)
        if idx >= 0:
            return will_text[:idx].rstrip(), will_text[idx:]
    return will_text, ""


def _add_header(doc, testator_name: str):
    """Add running header with testator name to the document."""
    section = doc.sections[0]
    header = section.header
    header.is_linked_to_previous = False

    # First line: "LAST WILL AND TESTAMENT OF"
    para1 = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    para1.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para1.paragraph_format.space_after = Pt(0)
    para1.paragraph_format.space_before = Pt(0)
    run1 = para1.add_run("LAST WILL AND TESTAMENT OF")
    run1.bold = True
    run1.font.name = 'Times New Roman'
    run1.font.size = Pt(9)

    # Second line: testator name
    para2 = header.add_paragraph()
    para2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para2.paragraph_format.space_after = Pt(6)
    para2.paragraph_format.space_before = Pt(0)
    run2 = para2.add_run(testator_name)
    run2.bold = True
    run2.font.name = 'Times New Roman'
    run2.font.size = Pt(9)

    # Add bottom border to header
    pPr = para2._element.get_or_add_pPr()
    pBdr = parse_xml(f'<w:pBdr {nsdecls("w")}><w:bottom w:val="single" w:sz="4" w:space="4" w:color="000000"/></w:pBdr>')
    pPr.append(pBdr)


def _add_footer(doc):
    """Add running footer with page number and Testator/Witness signature spaces."""
    section = doc.sections[0]
    footer = section.footer
    footer.is_linked_to_previous = False

    # Create a table for the footer layout: 4 columns
    # [Page| N] [___Testator] [___Witness1] [Continued.../___Witness2]
    table = footer.add_table(rows=2, cols=4, width=Cm(14))
    table.autofit = True

    # Set table borders to none
    tbl = table._element
    tblPr = tbl.find(qn('w:tblPr'))
    if tblPr is None:
        tblPr = parse_xml(f'<w:tblPr {nsdecls("w")}></w:tblPr>')
        tbl.insert(0, tblPr)
    borders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        '<w:top w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:left w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:bottom w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:right w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:insideH w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:insideV w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '</w:tblBorders>'
    )
    tblPr.append(borders)

    # Row 1: Signature lines
    # Col 0: empty (page number area)
    cell_00 = table.cell(0, 0)
    cell_00.text = ""

    # Col 1: signature line for Testator
    cell_01 = table.cell(0, 1)
    p01 = cell_01.paragraphs[0]
    p01.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run01 = p01.add_run("_______________")
    run01.font.name = 'Times New Roman'
    run01.font.size = Pt(7)

    # Col 2: signature line for Witness 1
    cell_02 = table.cell(0, 2)
    p02 = cell_02.paragraphs[0]
    p02.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run02 = p02.add_run("_______________")
    run02.font.name = 'Times New Roman'
    run02.font.size = Pt(7)

    # Col 3: "Continued on next page" + signature line for Witness 2
    cell_03 = table.cell(0, 3)
    p03 = cell_03.paragraphs[0]
    p03.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run03_a = p03.add_run("Continued on next page")
    run03_a.font.name = 'Times New Roman'
    run03_a.font.size = Pt(6)
    p03b = cell_03.add_paragraph()
    p03b.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run03_b = p03b.add_run("_______________")
    run03_b.font.name = 'Times New Roman'
    run03_b.font.size = Pt(7)

    # Row 2: Labels
    # Col 0: Page number
    cell_10 = table.cell(1, 0)
    p10 = cell_10.paragraphs[0]
    p10.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run_page = p10.add_run("Page| ")
    run_page.font.name = 'Times New Roman'
    run_page.font.size = Pt(7)

    # Add page number field
    fldChar1 = parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>')
    run_page._element.append(fldChar1)
    instrText = parse_xml(f'<w:instrText {nsdecls("w")} xml:space="preserve"> PAGE </w:instrText>')
    run_page._element.append(instrText)
    fldChar2 = parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>')
    run_page._element.append(fldChar2)

    # Col 1: "Testator" label
    cell_11 = table.cell(1, 1)
    p11 = cell_11.paragraphs[0]
    p11.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run11 = p11.add_run("Testator")
    run11.font.name = 'Times New Roman'
    run11.font.size = Pt(7)

    # Col 2: "Witness 1" label
    cell_12 = table.cell(1, 2)
    p12 = cell_12.paragraphs[0]
    p12.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run12 = p12.add_run("Witness 1")
    run12.font.name = 'Times New Roman'
    run12.font.size = Pt(7)

    # Col 3: "Witness 2" label
    cell_13 = table.cell(1, 3)
    p13 = cell_13.paragraphs[0]
    p13.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run13 = p13.add_run("Witness 2")
    run13.font.name = 'Times New Roman'
    run13.font.size = Pt(7)


def _add_signing_page(doc):
    """Add the attestation/signing page with proper Rockwill layout."""
    # Page break before signing page
    para_break = doc.add_paragraph()
    run_break = para_break.add_run()
    run_break.add_break(WD_BREAK.PAGE)

    # Helper to add a label-field row
    def add_sig_row(label_text, with_line=True):
        table = doc.add_table(rows=1, cols=2)
        table.autofit = True
        # Remove borders
        tbl = table._element
        tblPr = tbl.find(qn('w:tblPr'))
        if tblPr is None:
            tblPr = parse_xml(f'<w:tblPr {nsdecls("w")}></w:tblPr>')
            tbl.insert(0, tblPr)
        borders = parse_xml(
            f'<w:tblBorders {nsdecls("w")}>'
            '<w:top w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:left w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:bottom w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:right w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:insideH w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '<w:insideV w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
            '</w:tblBorders>'
        )
        tblPr.append(borders)

        # Label cell
        cell0 = table.cell(0, 0)
        p0 = cell0.paragraphs[0]
        p0.paragraph_format.space_after = Pt(2)
        p0.paragraph_format.space_before = Pt(2)
        run0 = p0.add_run(label_text)
        run0.font.name = 'Times New Roman'
        run0.font.size = Pt(11)

        # Set column width for label (approx 40%)
        tc0 = cell0._element
        tcPr0 = tc0.get_or_add_tcPr()
        tcW0 = parse_xml(f'<w:tcW {nsdecls("w")} w:w="3600" w:type="dxa"/>')
        tcPr0.append(tcW0)

        # Field cell with underline
        cell1 = table.cell(0, 1)
        p1 = cell1.paragraphs[0]
        p1.paragraph_format.space_after = Pt(2)
        p1.paragraph_format.space_before = Pt(2)
        if with_line:
            # Add bottom border to the cell content
            pPr = p1._element.get_or_add_pPr()
            pBdr = parse_xml(
                f'<w:pBdr {nsdecls("w")}>'
                '<w:bottom w:val="single" w:sz="4" w:space="1" w:color="000000"/>'
                '</w:pBdr>'
            )
            pPr.append(pBdr)
            run1 = p1.add_run(" ")
            run1.font.name = 'Times New Roman'
            run1.font.size = Pt(11)

        return table

    # Testator signature
    add_sig_row("Signature of the Testator:")
    doc.add_paragraph('')  # spacer

    # Date
    t = add_sig_row("Date of this Will:")
    # Add (dd/mm/yyyy) hint in the field
    cell = t.cell(0, 1)
    p = cell.paragraphs[0]
    for run in p.runs:
        run.clear()
    hint_run = p.add_run("                                                                    (dd/mm/yyyy)")
    hint_run.font.name = 'Times New Roman'
    hint_run.font.size = Pt(9)
    hint_run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    doc.add_paragraph('')  # spacer

    # Attestation clause
    attest = doc.add_paragraph()
    attest.paragraph_format.space_before = Pt(6)
    attest.paragraph_format.space_after = Pt(12)
    run_attest = attest.add_run(
        "This Last Will and Testament was signed by the Testator in the presence "
        "of us both and attested by us in the presence of both Testator and of each other:"
    )
    run_attest.font.name = 'Times New Roman'
    run_attest.font.size = Pt(11)

    # First Witness
    add_sig_row("Signature of First Witness:")
    add_sig_row("First Witness Full Name:")
    add_sig_row("First Witness Identification:")
    add_sig_row("First Witness Address:")
    add_sig_row("")  # Address line 2
    add_sig_row("")  # Address line 3
    add_sig_row("First Witness Contact Number:")

    doc.add_paragraph('')  # spacer

    # Second Witness
    add_sig_row("Signature of Second Witness:")
    add_sig_row("Second Witness Full Name:")
    add_sig_row("Second Witness Identification:")
    add_sig_row("Second Witness Address:")
    add_sig_row("")  # Address line 2
    add_sig_row("")  # Address line 3
    add_sig_row("Second Witness Contact Number:")

    # End of Document marker
    doc.add_paragraph('')
    end_para = doc.add_paragraph()
    end_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    end_para.paragraph_format.space_before = Pt(18)
    run_end = end_para.add_run("End of Document")
    run_end.bold = True
    run_end.font.name = 'Times New Roman'
    run_end.font.size = Pt(10)


def generate_docx(will_text: str, filename_base: str = "Will") -> str:
    """
    Generate a Word document from the will text.

    Args:
        will_text: The full will text to render.
        filename_base: Base name for the output file (without extension).

    Returns:
        Absolute path to the generated .docx file.
    """
    doc = Document()
    testator_name = _extract_testator_name(will_text)
    main_content, signing_section = _split_signing_page(will_text)

    # -- Page setup -----------------------------------------------------------
    section = doc.sections[0]
    section.top_margin = Cm(3.5)
    section.bottom_margin = Cm(3.5)
    section.left_margin = Cm(3.18)
    section.right_margin = Cm(3.18)

    # -- Add running header ---------------------------------------------------
    _add_header(doc, testator_name)

    # -- Add running footer ---------------------------------------------------
    _add_footer(doc)

    # -- Default font style ---------------------------------------------------
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(12)
    style.paragraph_format.space_after = Pt(6)
    style.paragraph_format.line_spacing = 1.5

    # -- Parse and add main content -------------------------------------------
    lines = main_content.split('\n')
    for line in lines:
        stripped = line.strip()

        if not stripped:
            doc.add_paragraph('')
            continue

        # Skip title lines (already in header)
        if 'LAST WILL AND TESTAMENT OF' in stripped.upper():
            continue

        # Detect "THE REST OF THE PAGE IS INTENTIONALLY LEFT BLANK"
        if 'REST OF THE PAGE IS INTENTIONALLY LEFT BLANK' in stripped.upper():
            para = doc.add_paragraph()
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            para.paragraph_format.space_before = Pt(24)
            run = para.add_run(stripped)
            run.font.name = 'Times New Roman'
            run.font.size = Pt(10)
            continue

        # Detect title-like lines (all uppercase and short)
        is_heading = (
            stripped.isupper()
            and len(stripped) < 80
            and not stripped.startswith('(')
            and not stripped.startswith('-')
        )

        # Detect numbered clause headings
        is_clause_heading = False
        if len(stripped) > 2 and stripped[0].isdigit():
            dot_pos = stripped.find('.')
            if 0 < dot_pos <= 3:
                rest = stripped[dot_pos + 1:].strip()
                if rest and rest.isupper():
                    is_clause_heading = True

        # Detect section headings
        is_section_heading = stripped in (
            'Revocation', 'Appointment of Executor(s)', 'Appointment of Guardian(s)',
            'Non Residuary Gift(s)', 'Residuary Estate', 'Declaration',
            'Testamentary Trust', 'Guardian Allowance', 'Contemplation of Marriage',
        )

        if is_section_heading:
            para = doc.add_paragraph()
            para.alignment = WD_ALIGN_PARAGRAPH.LEFT
            para.paragraph_format.space_before = Pt(18)
            run = para.add_run(stripped)
            run.bold = True
            run.underline = True
            run.font.size = Pt(12)
            run.font.name = 'Times New Roman'
        elif is_heading:
            para = doc.add_paragraph()
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = para.add_run(stripped)
            run.bold = True
            run.font.size = Pt(14 if 'LAST WILL' in stripped else 12)
            run.font.name = 'Times New Roman'
        elif is_clause_heading:
            para = doc.add_paragraph()
            para.alignment = WD_ALIGN_PARAGRAPH.LEFT
            run = para.add_run(stripped)
            run.bold = True
            run.font.size = Pt(12)
            run.font.name = 'Times New Roman'
            para.paragraph_format.space_before = Pt(12)
        else:
            para = doc.add_paragraph()
            # Preserve leading whitespace / indentation
            if line.startswith('    ') or line.startswith('\t'):
                para.paragraph_format.left_indent = Cm(1.27)
            run = para.add_run(stripped)
            run.font.name = 'Times New Roman'
            run.font.size = Pt(12)

    # -- Add signing page -----------------------------------------------------
    if signing_section:
        _add_signing_page(doc)

    # -- Save to temp file ----------------------------------------------------
    tmp_dir = tempfile.mkdtemp()
    filepath = os.path.join(tmp_dir, f'{filename_base}_Will.docx')
    doc.save(filepath)
    return filepath
