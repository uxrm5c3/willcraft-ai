"""Render a will as a Microsoft Word .docx using the Alan Tan & Associates
established format.

Mirrors the same format spec as documents/pdf_generator.py — Times New Roman
11pt body, 1.5 line spacing, justified body, bold+underlined section headings,
hanging indent for numbered clauses, 3-line signature footer (Testator / Witness 1
/ Witness 2), no header/footer on the cover page.

Used by the admin Will Format Preview so the user can:
  - download the sample as Word
  - open in Microsoft Word / Pages
  - track changes / red-line edit the format
  - send feedback back to refine the templates

Public API: build_will_docx(will_text, output_path, firm_info=None) -> str
"""

import os
import re
import tempfile
from typing import Optional

from docx import Document
from docx.shared import Pt, Cm, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.section import WD_SECTION
from docx.oxml.ns import qn, nsmap
from docx.oxml import OxmlElement


# ── Format constants (mirror Alan & Tan PHEK YI TING sample) ──────────
FONT_FAMILY = 'Times New Roman'
FONT_SIZE_BODY = Pt(11)
FONT_SIZE_HEADING = Pt(11)
FONT_SIZE_PAGE_HEADER = Pt(10)
FONT_SIZE_PAGE_NUMBER = Pt(9)
FONT_SIZE_SIG_LABEL = Pt(8)
FONT_SIZE_COVER_TITLE = Pt(16)
FONT_SIZE_COVER_OF = Pt(14)
FONT_SIZE_COVER_NAME = Pt(16)
FONT_SIZE_COVER_FIRM_ADDR = Pt(10)


# ── Helpers ───────────────────────────────────────────────────────────
def _set_font(run, *, size=None, bold=False, italic=False, underline=False):
    run.font.name = FONT_FAMILY
    # Word needs both ascii and east-asia explicit
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn('w:rFonts'))
    if rFonts is None:
        rFonts = OxmlElement('w:rFonts')
        rPr.insert(0, rFonts)
    rFonts.set(qn('w:ascii'), FONT_FAMILY)
    rFonts.set(qn('w:hAnsi'), FONT_FAMILY)
    if size is not None:
        run.font.size = size
    run.font.bold = bold
    run.font.italic = italic
    run.font.underline = underline


def _set_paragraph_format(paragraph, *, alignment=None, space_before_pt=0,
                           space_after_pt=0, line_spacing=1.5,
                           left_indent_pt=None, first_line_indent_pt=None):
    pf = paragraph.paragraph_format
    if alignment is not None:
        paragraph.alignment = alignment
    pf.space_before = Pt(space_before_pt)
    pf.space_after = Pt(space_after_pt)
    pf.line_spacing = line_spacing
    if left_indent_pt is not None:
        pf.left_indent = Pt(left_indent_pt)
    if first_line_indent_pt is not None:
        pf.first_line_indent = Pt(first_line_indent_pt)


def _add_page_number_field(paragraph):
    """Insert a Word PAGE field that auto-numbers."""
    run = paragraph.add_run()
    fldChar1 = OxmlElement('w:fldChar')
    fldChar1.set(qn('w:fldCharType'), 'begin')
    run._element.append(fldChar1)
    instrText = OxmlElement('w:instrText')
    instrText.text = ' PAGE '
    run._element.append(instrText)
    fldChar2 = OxmlElement('w:fldChar')
    fldChar2.set(qn('w:fldCharType'), 'end')
    run._element.append(fldChar2)
    _set_font(run, size=FONT_SIZE_PAGE_NUMBER)


def _add_horizontal_line(paragraph):
    """Add a horizontal line below a paragraph (used under page header)."""
    pPr = paragraph._element.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), '6')
    bottom.set(qn('w:space'), '1')
    bottom.set(qn('w:color'), '000000')
    pBdr.append(bottom)
    pPr.append(pBdr)


def _set_section_margins(section, *, top_in=1.0, bottom_in=1.25,
                         left_in=1.0, right_in=1.0,
                         header_in=0.5, footer_in=0.6):
    section.top_margin = Inches(top_in)
    section.bottom_margin = Inches(bottom_in)
    section.left_margin = Inches(left_in)
    section.right_margin = Inches(right_in)
    section.header_distance = Inches(header_in)
    section.footer_distance = Inches(footer_in)


def _build_body_header(section, testator_name: str):
    """Build the running header used on every body page:
    'LAST WILL AND TESTAMENT OF / {TESTATOR_NAME}' bold + horizontal rule.
    """
    header = section.header
    # Clear any default paragraph
    p1 = header.paragraphs[0]
    p1.text = ''
    r1 = p1.add_run('LAST WILL AND TESTAMENT OF')
    _set_font(r1, size=FONT_SIZE_PAGE_HEADER, bold=True)
    _set_paragraph_format(p1, alignment=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=1.0)

    p2 = header.add_paragraph()
    r2 = p2.add_run(testator_name.upper())
    _set_font(r2, size=FONT_SIZE_PAGE_HEADER, bold=True)
    _set_paragraph_format(p2, alignment=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=1.0)

    # Horizontal rule under second header line
    _add_horizontal_line(p2)


def _build_body_footer(section):
    """Build the running footer: 'Page X' on the left + 3 signature lines
    (Testator / Witness 1 / Witness 2) using a 4-column table.
    """
    footer = section.footer
    # Clear default paragraph
    p0 = footer.paragraphs[0]
    p0.text = ''

    # 4-column table: [Page X] [Testator line] [Witness 1 line] [Witness 2 line]
    table = footer.add_table(rows=2, cols=4, width=Inches(6.5))
    table.autofit = True
    # Distribute columns: page-num narrower, signatures equal
    widths = [Inches(0.8), Inches(1.9), Inches(1.9), Inches(1.9)]
    for row in table.rows:
        for i, cell in enumerate(row.cells):
            cell.width = widths[i]

    # Row 1: top borders only (signature lines)
    row_lines = table.rows[0]
    # Page X cell — text only, no border
    p_page = row_lines.cells[0].paragraphs[0]
    r_page = p_page.add_run('Page ')
    _set_font(r_page, size=FONT_SIZE_PAGE_NUMBER)
    _add_page_number_field(p_page)
    _set_paragraph_format(p_page, alignment=WD_ALIGN_PARAGRAPH.LEFT, line_spacing=1.0)

    # Signature line cells: top border to draw the signature line
    for i in range(1, 4):
        cell = row_lines.cells[i]
        cell.paragraphs[0].text = ''
        tcPr = cell._element.get_or_add_tcPr()
        tcBorders = OxmlElement('w:tcBorders')
        top = OxmlElement('w:top')
        top.set(qn('w:val'), 'single')
        top.set(qn('w:sz'), '6')
        top.set(qn('w:color'), '000000')
        tcBorders.append(top)
        tcPr.append(tcBorders)

    # Row 2: labels under each signature line
    row_labels = table.rows[1]
    row_labels.cells[0].text = ''
    labels = ['Testator', 'Witness 1', 'Witness 2']
    for i, label in enumerate(labels):
        cell = row_labels.cells[i + 1]
        p = cell.paragraphs[0]
        p.text = ''
        r = p.add_run(label)
        _set_font(r, size=FONT_SIZE_SIG_LABEL)
        _set_paragraph_format(p, alignment=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=1.0)


def _add_run_with_emphasis(paragraph, text: str):
    """Add text to a paragraph, applying bold to UPPERCASE NAME (NRIC ...) tokens.
    This is a heuristic to emulate the bold-name styling in the sample without
    needing structured input.
    """
    # Pattern matches: "NAME (MALAYSIA NRIC No. xxx-xx-xxxx)" or similar
    # Bold the name + parenthesised ID together.
    pattern = re.compile(
        r'([A-Z][A-Z\s/\'\.&]+?)\s*(\((?:MALAYSIA\s+NRIC|NRIC|Identification|Passport)\s*(?:No\.?|NO\.?)\s*[\w\-/\s]+?\))'
    )

    pos = 0
    for m in pattern.finditer(text):
        # Text before the match
        if m.start() > pos:
            r = paragraph.add_run(text[pos:m.start()])
            _set_font(r, size=FONT_SIZE_BODY)
        # Bold name + parenthesised ID
        r = paragraph.add_run(text[m.start():m.end()])
        _set_font(r, size=FONT_SIZE_BODY, bold=True)
        pos = m.end()
    # Tail
    if pos < len(text):
        r = paragraph.add_run(text[pos:])
        _set_font(r, size=FONT_SIZE_BODY)


# Section headings used in Alan & Tan format
_SECTION_HEADINGS = {
    'Revocation', 'Appointment of Executor(s)', 'Appointment of Guardian(s)',
    'Non-Residuary Gift(s)', 'Non Residuary Gift(s)',
    'Residuary Estate', 'Declaration',
    'Testamentary Trust', 'Guardian Allowance', 'Contemplation of Marriage',
}


def _split_signing(will_text: str):
    markers = ['Signature of the Testator', 'SIGNATURE OF THE TESTATOR']
    for m in markers:
        idx = will_text.find(m)
        if idx >= 0:
            return will_text[:idx].rstrip(), will_text[idx:]
    return will_text, ''


def _extract_testator_name(will_text: str) -> str:
    lines = will_text.strip().split('\n')
    for i, line in enumerate(lines):
        if 'LAST WILL AND TESTAMENT OF' in line.upper():
            after = line.upper().replace('LAST WILL AND TESTAMENT OF', '').strip()
            if after:
                return after
            for j in range(i + 1, min(i + 3, len(lines))):
                cand = lines[j].strip()
                if cand:
                    return cand.upper()
    return 'THE TESTATOR'


def build_will_docx(will_text: str, output_path: Optional[str] = None,
                     firm_info: Optional[dict] = None) -> str:
    """Render the will text as .docx and return the path."""
    doc = Document()

    # ── Default style ──
    style = doc.styles['Normal']
    style.font.name = FONT_FAMILY
    style.font.size = FONT_SIZE_BODY
    style.paragraph_format.line_spacing = 1.5

    testator_name = _extract_testator_name(will_text)
    main_text, signing_text = _split_signing(will_text)

    # ── COVER PAGE — section 1: no header, no footer ──
    cover_section = doc.sections[0]
    _set_section_margins(cover_section, top_in=1.2, bottom_in=1.2)
    cover_section.different_first_page_header_footer = False
    # Empty header/footer for the cover
    cover_section.header.paragraphs[0].text = ''
    cover_section.footer.paragraphs[0].text = ''

    # Firm address at top center (small)
    if firm_info and firm_info.get('firm_address'):
        p = doc.add_paragraph()
        r = p.add_run(firm_info['firm_address'])
        _set_font(r, size=FONT_SIZE_COVER_FIRM_ADDR)
        _set_paragraph_format(p, alignment=WD_ALIGN_PARAGRAPH.CENTER,
                              line_spacing=1.0, space_before_pt=0, space_after_pt=12)

    # Push title block down with blank paragraphs
    for _ in range(10):
        p = doc.add_paragraph()
        _set_paragraph_format(p, line_spacing=1.0)

    # Title block — centered, bold
    for line, fs in [
        ('The Last Will & Testament', FONT_SIZE_COVER_TITLE),
        ('of', FONT_SIZE_COVER_OF),
        (testator_name, FONT_SIZE_COVER_NAME),
    ]:
        p = doc.add_paragraph()
        r = p.add_run(line)
        _set_font(r, size=fs, bold=True)
        _set_paragraph_format(p, alignment=WD_ALIGN_PARAGRAPH.CENTER,
                              line_spacing=1.4, space_before_pt=4, space_after_pt=4)

    # NRIC line — extract from will text
    nric_match = re.search(r'NRIC\s*No\.?\s*[:\s]*(\d{6}-\d{2}-\d{4})', will_text)
    if nric_match:
        p = doc.add_paragraph()
        r = p.add_run(f'(NRIC No. {nric_match.group(1)})')
        _set_font(r, size=FONT_SIZE_COVER_OF, bold=True)
        _set_paragraph_format(p, alignment=WD_ALIGN_PARAGRAPH.CENTER,
                              line_spacing=1.4, space_before_pt=4)

    # ── BODY PAGES — new section with header + footer ──
    body_section = doc.add_section(WD_SECTION.NEW_PAGE)
    _set_section_margins(body_section)
    body_section.different_first_page_header_footer = False
    # Critical: unlink from previous section so cover stays blank
    body_section.header.is_linked_to_previous = False
    body_section.footer.is_linked_to_previous = False

    _build_body_header(body_section, testator_name)
    _build_body_footer(body_section)

    # ── Body content ──
    # Skip the first occurrence of "LAST WILL AND TESTAMENT OF" + testator
    # name lines (already in the running header).
    lines = main_text.split('\n')
    skip_count = 0
    for i, ln in enumerate(lines):
        if 'LAST WILL AND TESTAMENT' in ln.upper():
            skip_count = i + 1
            # Also skip a following testator-name line and any blank
            j = skip_count
            while j < len(lines) and (
                lines[j].strip() == '' or lines[j].strip().upper() == testator_name.upper()
            ):
                j += 1
                skip_count = j
            break

    body_lines = lines[skip_count:]

    i = 0
    blank_streak = 0
    while i < len(body_lines):
        line = body_lines[i]
        stripped = line.strip()
        if not stripped:
            blank_streak += 1
            # Allow only one blank-line spacer
            if blank_streak == 1:
                p = doc.add_paragraph()
                _set_paragraph_format(p, line_spacing=1.0, space_before_pt=0, space_after_pt=0)
            i += 1
            continue
        blank_streak = 0

        # Section heading?
        if stripped in _SECTION_HEADINGS or stripped.rstrip(':') in _SECTION_HEADINGS:
            p = doc.add_paragraph()
            r = p.add_run(stripped.rstrip(':'))
            _set_font(r, size=FONT_SIZE_HEADING, bold=True, underline=True)
            _set_paragraph_format(p, alignment=WD_ALIGN_PARAGRAPH.LEFT,
                                  space_before_pt=14, space_after_pt=8, line_spacing=1.5)
            i += 1
            continue

        # Numbered clause "1. ", "2. ", etc.
        clause_match = re.match(r'^(\d{1,2}\.)\s+(.+)$', stripped)
        if clause_match:
            num = clause_match.group(1)
            body_text = clause_match.group(2)
            # Collect continuation lines (until blank or next clause/heading)
            j = i + 1
            while j < len(body_lines):
                next_stripped = body_lines[j].strip()
                if not next_stripped:
                    break
                if next_stripped in _SECTION_HEADINGS:
                    break
                if re.match(r'^\d{1,2}\.\s', next_stripped):
                    break
                if re.match(r'^\([a-z]\)\s', next_stripped):
                    break
                body_text += ' ' + next_stripped
                j += 1
            i = j

            p = doc.add_paragraph()
            _set_paragraph_format(p, alignment=WD_ALIGN_PARAGRAPH.JUSTIFY,
                                  space_before_pt=0, space_after_pt=12,
                                  left_indent_pt=28, first_line_indent_pt=-28,
                                  line_spacing=1.5)
            # Number prefix
            r_num = p.add_run(f'{num}\t')
            _set_font(r_num, size=FONT_SIZE_BODY)
            # Body with bold-name emphasis
            _add_run_with_emphasis(p, body_text)
            continue

        # Sub-clause (a), (b) ...
        sub_match = re.match(r'^(\([a-z]\))\s+(.+)$', stripped)
        if sub_match:
            letter = sub_match.group(1)
            body_text = sub_match.group(2)
            j = i + 1
            while j < len(body_lines):
                next_stripped = body_lines[j].strip()
                if not next_stripped:
                    break
                if next_stripped in _SECTION_HEADINGS:
                    break
                if re.match(r'^\d{1,2}\.\s', next_stripped):
                    break
                if re.match(r'^\([a-z]\)\s', next_stripped):
                    break
                body_text += ' ' + next_stripped
                j += 1
            i = j
            p = doc.add_paragraph()
            _set_paragraph_format(p, alignment=WD_ALIGN_PARAGRAPH.JUSTIFY,
                                  space_before_pt=0, space_after_pt=10,
                                  left_indent_pt=64, first_line_indent_pt=-32,
                                  line_spacing=1.5)
            r_num = p.add_run(f'{letter}\t')
            _set_font(r_num, size=FONT_SIZE_BODY)
            _add_run_with_emphasis(p, body_text)
            continue

        # Blank-page marker line
        if 'remaining page is intentionally left blank' in stripped.lower():
            p = doc.add_paragraph()
            r = p.add_run(stripped)
            _set_font(r, size=FONT_SIZE_BODY)
            _set_paragraph_format(p, alignment=WD_ALIGN_PARAGRAPH.LEFT,
                                  space_before_pt=12, space_after_pt=6, line_spacing=1.5)
            i += 1
            continue

        # Plain paragraph (e.g. opening statement, charge note, etc.)
        # Collect continuation lines
        body_text = stripped
        j = i + 1
        while j < len(body_lines):
            next_stripped = body_lines[j].strip()
            if not next_stripped:
                break
            if next_stripped in _SECTION_HEADINGS:
                break
            if re.match(r'^\d{1,2}\.\s', next_stripped):
                break
            if re.match(r'^\([a-z]\)\s', next_stripped):
                break
            body_text += ' ' + next_stripped
            j += 1
        i = j

        p = doc.add_paragraph()
        _set_paragraph_format(p, alignment=WD_ALIGN_PARAGRAPH.JUSTIFY,
                              space_before_pt=0, space_after_pt=12, line_spacing=1.5)
        _add_run_with_emphasis(p, body_text)

    # ── SIGNATURE PAGE — same section, just continue but force a page break ──
    if signing_text.strip():
        # Force a page break before signing block
        last = doc.add_paragraph()
        run = last.add_run()
        run.add_break()
        from docx.enum.text import WD_BREAK
        # Insert proper page break
        p_break = doc.add_paragraph()
        rb = p_break.add_run()
        rb.add_break(WD_BREAK.PAGE)

        for raw in signing_text.split('\n'):
            line = raw.rstrip()
            stripped = line.strip()
            p = doc.add_paragraph()
            if stripped:
                r = p.add_run(line)
                _set_font(r, size=Pt(11))
            _set_paragraph_format(p, alignment=WD_ALIGN_PARAGRAPH.LEFT,
                                  space_before_pt=2, space_after_pt=4, line_spacing=1.4)

    # ── Save ──
    if output_path is None:
        tmp_dir = tempfile.mkdtemp()
        output_path = os.path.join(tmp_dir, 'will.docx')
    doc.save(output_path)
    return output_path
