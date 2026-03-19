"""Generate a PDF document from will text.

Primary method: WeasyPrint (high-quality HTML-to-PDF).
Fallback: builds a simple PDF using basic HTML if WeasyPrint is not installed.

Professional Malaysian will format:
- Running header: "LAST WILL AND TESTAMENT OF [TESTATOR NAME]" on every page
- Running footer: Page number + separate Testator/Witness 1/Witness 2 signature spaces
- Proper signing/attestation page layout
"""

import os
import re
import html
import tempfile


def _extract_testator_name(will_text: str) -> str:
    """Extract testator name from will text (line after 'LAST WILL AND TESTAMENT OF')."""
    lines = will_text.strip().split('\n')
    for i, line in enumerate(lines):
        if 'LAST WILL AND TESTAMENT OF' in line.upper():
            # Name might be on the same line or the next line
            after = line.upper().replace('LAST WILL AND TESTAMENT OF', '').strip()
            if after:
                return after
            # Check next non-empty line
            for j in range(i + 1, min(i + 3, len(lines))):
                candidate = lines[j].strip()
                if candidate:
                    return candidate.upper()
    return "THE TESTATOR"


def _split_signing_page(will_text: str):
    """Split will text into main content and signing page section.

    Returns (main_content, signing_section) where signing_section starts
    from 'Signature of the Testator' line.
    """
    # Look for the signing page marker
    markers = [
        'Signature of the Testator',
        'SIGNATURE OF THE TESTATOR',
    ]
    for marker in markers:
        idx = will_text.find(marker)
        if idx >= 0:
            return will_text[:idx].rstrip(), will_text[idx:]

    # If no signing page found, return all as main content
    return will_text, ""


def _build_content_html(text: str) -> str:
    """Convert main will content (before signing page) into HTML paragraphs."""
    escaped = html.escape(text)
    lines = escaped.split('\n')
    html_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            html_lines.append('<div class="spacer"></div>')
            continue

        # Skip the title lines (they're in the running header)
        if 'LAST WILL AND TESTAMENT OF' in stripped.upper():
            continue

        # Skip per-page footer content (already in running footer)
        if 'Continued on' in stripped and ('next page' in stripped.lower() or 'Page' in stripped):
            continue
        if stripped.startswith('Page|') or stripped.startswith('Page |'):
            continue
        if ('Testator' in stripped and 'Witness 1' in stripped and 'Witness 2' in stripped):
            continue
        if stripped.replace('_', '').replace(' ', '').replace('|', '') == '':
            continue

        # Detect the testator name line (all caps, short, right after title)
        # We skip it since it's in the header
        # But we need to keep it if it's part of the preamble

        # Detect "THE REST OF THE PAGE IS INTENTIONALLY LEFT BLANK"
        if 'REST OF THE PAGE IS INTENTIONALLY LEFT BLANK' in stripped.upper():
            html_lines.append(f'<p class="blank-notice">{stripped}</p>')
            continue

        # Detect title-like headings (all uppercase, short)
        is_heading = (
            stripped.isupper()
            and len(stripped) < 80
            and not stripped.startswith('(')
            and not stripped.startswith('-')
        )

        # Detect clause headings: "1. REVOCATION" etc.
        is_clause = False
        if len(stripped) > 2 and stripped[0].isdigit():
            dot_pos = stripped.find('.')
            if 0 < dot_pos <= 3:
                rest = stripped[dot_pos + 1:].strip()
                if rest and rest.isupper():
                    is_clause = True

        # Detect section headings like "Revocation", "Appointment of Executor(s)"
        is_section_heading = stripped in (
            'Revocation', 'Appointment of Executor(s)', 'Appointment of Guardian(s)',
            'Non Residuary Gift(s)', 'Residuary Estate', 'Declaration',
            'Testamentary Trust', 'Guardian Allowance', 'Contemplation of Marriage',
        )

        if is_section_heading:
            html_lines.append(f'<h3 class="section-heading">{stripped}</h3>')
        elif is_heading:
            html_lines.append(f'<h2 class="will-heading">{stripped}</h2>')
        elif is_clause:
            html_lines.append(f'<p class="clause-heading">{stripped}</p>')
        elif line.startswith('    ') or line.startswith('\t'):
            html_lines.append(f'<p class="indented">{stripped}</p>')
        else:
            html_lines.append(f'<p>{stripped}</p>')

    return '\n'.join(html_lines)


def _build_signing_page_html(signing_text: str) -> str:
    """Build the attestation/signing page HTML — 2-column layout matching professional will format."""
    if not signing_text.strip():
        return ""

    return """
<div class="signing-page">

    <!-- Testator Signature -->
    <table class="sign-layout">
        <tr>
            <td class="sign-label">Signature of the Testator:</td>
            <td class="sign-field">&nbsp;</td>
        </tr>
        <tr><td colspan="2" style="height:10pt;"></td></tr>
        <tr>
            <td class="sign-label">Date of this Will:</td>
            <td class="sign-field"><span class="date-hint">(dd/mm/yyyy)</span></td>
        </tr>
    </table>

    <div style="height:20pt;"></div>

    <p class="attestation-clause">This Last Will and Testament was signed by the Testator in the presence of us both and attested by us in the presence of both Testator and of each other:</p>

    <div style="height:16pt;"></div>

    <!-- First Witness -->
    <table class="sign-layout">
        <tr>
            <td class="sign-label">Signature of First Witness:</td>
            <td class="sign-field">&nbsp;</td>
        </tr>
        <tr><td colspan="2" style="height:6pt;"></td></tr>
        <tr>
            <td class="sign-label">Full Name:</td>
            <td class="sign-field">&nbsp;</td>
        </tr>
        <tr>
            <td class="sign-label">NRIC / Passport No.:</td>
            <td class="sign-field">&nbsp;</td>
        </tr>
        <tr>
            <td class="sign-label">Address:</td>
            <td class="sign-field">&nbsp;</td>
        </tr>
        <tr>
            <td class="sign-label"></td>
            <td class="sign-field">&nbsp;</td>
        </tr>
        <tr>
            <td class="sign-label">Contact No.:</td>
            <td class="sign-field">&nbsp;</td>
        </tr>
    </table>

    <div style="height:16pt;"></div>

    <!-- Second Witness -->
    <table class="sign-layout">
        <tr>
            <td class="sign-label">Signature of Second Witness:</td>
            <td class="sign-field">&nbsp;</td>
        </tr>
        <tr><td colspan="2" style="height:6pt;"></td></tr>
        <tr>
            <td class="sign-label">Full Name:</td>
            <td class="sign-field">&nbsp;</td>
        </tr>
        <tr>
            <td class="sign-label">NRIC / Passport No.:</td>
            <td class="sign-field">&nbsp;</td>
        </tr>
        <tr>
            <td class="sign-label">Address:</td>
            <td class="sign-field">&nbsp;</td>
        </tr>
        <tr>
            <td class="sign-label"></td>
            <td class="sign-field">&nbsp;</td>
        </tr>
        <tr>
            <td class="sign-label">Contact No.:</td>
            <td class="sign-field">&nbsp;</td>
        </tr>
    </table>

    <div class="end-marker">
        <p>- End of Document -</p>
    </div>

</div>
"""


def _logo_to_data_uri(logo_path: str) -> str:
    """Convert a logo file to a base64 data URI for embedding in HTML."""
    import base64
    ext = logo_path.rsplit('.', 1)[-1].lower()
    mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'webp': 'image/webp'}.get(ext, 'image/png')
    with open(logo_path, 'rb') as f:
        data = base64.b64encode(f.read()).decode('ascii')
    return f'data:{mime};base64,{data}'


def _will_text_to_html(will_text: str, title: str = "Last Will and Testament",
                       logo_path: str = None) -> str:
    """Convert plain-text will into styled HTML suitable for PDF rendering.

    Follows Rockwills Trustee Berhad professional format with:
    - Running header with optional firm logo and testator name on every page
    - Running footer with page number and Testator/Witness signature spaces
    - Proper signing/attestation page layout
    """
    testator_name = _extract_testator_name(will_text)
    main_content, signing_section = _split_signing_page(will_text)

    content_html = _build_content_html(main_content)
    signing_html = _build_signing_page_html(signing_section)

    escaped_testator = html.escape(testator_name)
    escaped_title = html.escape(title)

    # Build logo HTML if logo file exists
    logo_html = ''
    if logo_path and os.path.isfile(logo_path):
        data_uri = _logo_to_data_uri(logo_path)
        logo_html = f'<img src="{data_uri}" class="header-logo" alt="">'

    # Adjust top margin when logo is present (needs more space)
    top_margin = '4.2cm' if logo_html else '3.5cm'

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>{escaped_title}</title>
<style>
    /* === Page Setup === */
    @page {{
        size: A4;
        margin: {top_margin} 3.18cm 4.8cm 3.18cm;

        /* Running header via element() */
        @top-left {{ content: none; }}
        @top-center {{
            content: element(pageHeader);
            width: 100%;
        }}
        @top-right {{ content: none; }}

        /* Clear left/right so @bottom-center spans full width */
        @bottom-left {{ content: none; }}
        @bottom-right {{ content: none; }}

        /* Running footer: placed via element() */
        @bottom-center {{
            content: element(pageFooter);
            width: 100%;
        }}
    }}

    /* Signing page: NO footer boxes, smaller bottom margin */
    @page signing {{
        size: A4;
        margin: {top_margin} 3.18cm 2.54cm 3.18cm;

        @top-left {{ content: none; }}
        @top-center {{
            content: element(pageHeader);
            width: 100%;
        }}
        @top-right {{ content: none; }}

        /* No footer on signing page */
        @bottom-left {{ content: none; }}
        @bottom-center {{ content: none; }}
        @bottom-right {{ content: none; }}
    }}

    /* Running header element — placed into @top-center via element() */
    .page-header {{
        position: running(pageHeader);
        font-family: 'Times New Roman', Times, serif;
        width: 100%;
        text-align: center;
        padding-bottom: 8pt;
        border-bottom: 0.5pt solid #000;
    }}
    .page-header .header-logo {{
        display: block;
        max-height: 40pt;
        max-width: 180pt;
        margin: 0 auto 4pt auto;
    }}
    .page-header .header-text {{
        font-size: 9pt;
        font-weight: bold;
        line-height: 1.4;
    }}

    /* Running footer element — placed into @bottom-center via element() */
    .page-footer {{
        position: running(pageFooter);
        font-family: 'Times New Roman', Times, serif;
        width: 100%;
    }}
    .page-footer .footer-info {{
        font-size: 8pt;
        text-align: center;
        padding: 0 0 2pt 0;
    }}
    .page-footer .footer-line {{
        border-top: 0.5pt solid #000;
        height: 0;
        margin: 0;
        padding: 0;
    }}
    .page-footer .sig-boxes {{
        width: 100%;
        border-collapse: collapse;
    }}
    .page-footer .sig-boxes td {{
        width: 33.33%;
        border: 0.5pt solid #000;
        height: 45pt;
        text-align: center;
        vertical-align: bottom;
        font-size: 8pt;
        padding: 2pt 4pt;
        font-family: 'Times New Roman', Times, serif;
    }}
    /* Page counter works inside running() elements */
    .page-footer .page-num::after {{
        content: counter(page);
    }}

    /* === Body Styles === */
    body {{
        font-family: 'Times New Roman', Times, serif;
        font-size: 12pt;
        line-height: 1.8;
        color: #000;
    }}

    /* Will title (first occurrence in content - hide since it's in header) */
    h2.will-heading {{
        text-align: center;
        font-size: 14pt;
        margin: 12pt 0 6pt 0;
        font-family: 'Times New Roman', Times, serif;
    }}

    /* Section headings: Revocation, Appointment of Executor(s), etc. */
    h3.section-heading {{
        font-size: 12pt;
        font-weight: bold;
        margin: 18pt 0 6pt 0;
        font-family: 'Times New Roman', Times, serif;
        text-decoration: underline;
    }}

    /* Numbered clause headings */
    p.clause-heading {{
        font-weight: bold;
        margin-top: 14pt;
    }}

    /* Indented sub-clauses */
    p.indented {{
        margin-left: 36pt;
    }}

    /* Regular paragraphs */
    p {{
        margin: 3pt 0;
        orphans: 3;
        widows: 3;
    }}

    /* Spacer between paragraphs */
    div.spacer {{
        height: 6pt;
    }}

    /* "THE REST OF THE PAGE IS INTENTIONALLY LEFT BLANK" */
    p.blank-notice {{
        text-align: center;
        margin-top: 24pt;
        font-size: 10pt;
        letter-spacing: 0.5pt;
    }}

    /* === Signing Page Styles === */
    .signing-page {{
        page: signing;
        page-break-before: always;
    }}

    /* Signing page: 2-column layout (labels left, fields right) */
    .sign-layout {{
        width: 100%;
        border-collapse: collapse;
    }}
    .sign-layout td {{
        font-family: 'Times New Roman', Times, serif;
        font-size: 11pt;
        padding: 4pt 0;
        vertical-align: bottom;
    }}
    .sign-label {{
        width: 35%;
        text-align: left;
        white-space: nowrap;
    }}
    .sign-field {{
        width: 65%;
        border-bottom: 0.5pt solid #000;
        height: 20pt;
    }}

    .date-hint {{
        font-size: 9pt;
        color: #666;
    }}

    .attestation-clause {{
        font-size: 11pt;
        line-height: 1.5;
        text-align: justify;
    }}

    .end-marker {{
        margin-top: 20pt;
        text-align: center;
        font-size: 10pt;
        font-weight: bold;
    }}
</style>
</head>
<body>
<!-- Running header: automatically placed into @top-center on each page -->
<div class="page-header">
    {logo_html}
    <div class="header-text">LAST WILL AND TESTAMENT OF<br>{escaped_testator}</div>
</div>

<!-- Running footer: automatically placed into @bottom-center on each page -->
<div class="page-footer">
    <div class="footer-info">Page <span class="page-num"></span></div>
    <div class="footer-line"></div>
    <table class="sig-boxes">
        <tr>
            <td>Testator</td>
            <td>Witness 1</td>
            <td>Witness 2</td>
        </tr>
    </table>
</div>

{content_html}
{signing_html}
</body>
</html>"""


def generate_pdf(will_text: str, filename_base: str = "Will",
                 logo_path: str = None) -> str:
    """
    Generate a PDF from the will text.

    Tries WeasyPrint first; falls back to a simple HTML-file-based PDF
    if WeasyPrint is not available.

    Args:
        will_text: The full will text to render.
        filename_base: Base name for the output file (without extension).
        logo_path: Optional path to a firm logo image to embed in the header.

    Returns:
        Absolute path to the generated .pdf file.
    """
    tmp_dir = tempfile.mkdtemp()
    filepath = os.path.join(tmp_dir, f'{filename_base}_Will.pdf')
    html_content = _will_text_to_html(
        will_text, title=f"{filename_base} - Last Will and Testament",
        logo_path=logo_path)

    try:
        from weasyprint import HTML
        HTML(string=html_content).write_pdf(filepath)
    except ImportError:
        # Fallback: save as HTML-based PDF using a subprocess call to
        # wkhtmltopdf if available, otherwise save as HTML and rename.
        _fallback_pdf(html_content, filepath)

    return filepath


def _fallback_pdf(html_content: str, filepath: str):
    """
    Fallback PDF generation when WeasyPrint is not installed.

    Tries wkhtmltopdf via subprocess. If that is also unavailable, writes
    an HTML file (browsers can print-to-PDF from it).
    """
    import subprocess
    import shutil

    tmp_html = filepath.replace('.pdf', '.html')
    with open(tmp_html, 'w', encoding='utf-8') as f:
        f.write(html_content)

    wkhtmltopdf = shutil.which('wkhtmltopdf')
    if wkhtmltopdf:
        try:
            subprocess.run(
                [wkhtmltopdf, '--quiet', '--page-size', 'A4',
                 '--margin-top', '25mm', '--margin-bottom', '25mm',
                 '--margin-left', '32mm', '--margin-right', '32mm',
                 tmp_html, filepath],
                check=True, timeout=30,
            )
            os.remove(tmp_html)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

    # Last resort: rename the HTML as the PDF (user can open in browser)
    os.rename(tmp_html, filepath)
