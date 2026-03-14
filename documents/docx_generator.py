"""Generate a Word document (.docx) from will text using python-docx."""

import os
import tempfile
from docx import Document
from docx.shared import Pt, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH


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

    # -- Page setup -----------------------------------------------------------
    section = doc.sections[0]
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.18)
    section.right_margin = Cm(3.18)

    # -- Default font style ---------------------------------------------------
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(12)
    style.paragraph_format.space_after = Pt(6)
    style.paragraph_format.line_spacing = 1.5

    # -- Parse and add content ------------------------------------------------
    lines = will_text.split('\n')
    for line in lines:
        stripped = line.strip()

        if not stripped:
            # Blank line -- add an empty paragraph
            doc.add_paragraph('')
            continue

        # Detect title-like lines (all uppercase and short, or known headings)
        is_heading = (
            stripped.isupper()
            and len(stripped) < 80
            and not stripped.startswith('(')
            and not stripped.startswith('-')
        )

        # Detect numbered clause headings: "1. SOMETHING" or "1.  SOMETHING"
        is_clause_heading = False
        if len(stripped) > 2 and stripped[0].isdigit():
            dot_pos = stripped.find('.')
            if 0 < dot_pos <= 3:
                rest = stripped[dot_pos + 1:].strip()
                if rest and rest.isupper():
                    is_clause_heading = True

        if is_heading:
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

    # -- Save to temp file ----------------------------------------------------
    tmp_dir = tempfile.mkdtemp()
    filepath = os.path.join(tmp_dir, f'{filename_base}_Will.docx')
    doc.save(filepath)
    return filepath
