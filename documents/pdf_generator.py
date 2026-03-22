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


def _is_numbered_clause(stripped: str) -> bool:
    """Check if a line starts a numbered clause like '4. I direct...' or '10. Unless...'."""
    if len(stripped) > 2 and stripped[0].isdigit():
        dot_pos = stripped.find('.')
        if 0 < dot_pos <= 3:
            return True
    return False


_SECTION_HEADINGS = {
    'Revocation', 'Appointment of Executor(s)', 'Appointment of Guardian(s)',
    'Non Residuary Gift(s)', 'Residuary Estate', 'Declaration',
    'Testamentary Trust', 'Guardian Allowance', 'Contemplation of Marriage',
}


def _build_content_html(text: str) -> str:
    """Convert main will content (before signing page) into HTML paragraphs.

    Groups section headings with their first clause, and groups each numbered
    clause with its continuation paragraphs to prevent page-break splits.
    """
    escaped = html.escape(text)
    lines = escaped.split('\n')

    # First pass: classify each line (max 1 consecutive blank line — minimal spacing)
    classified = []  # list of (type, html_str) tuples
    blank_count = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank_count += 1
            if blank_count <= 1:  # Max 1 spacer between content
                classified.append(('spacer', '<div class="spacer"></div>'))
            continue
        blank_count = 0

        # Skip header/footer content
        if 'LAST WILL AND TESTAMENT' in stripped.upper():
            continue
        if 'Continued on' in stripped and ('next page' in stripped.lower() or 'Page' in stripped):
            continue
        if stripped.startswith('Page|') or stripped.startswith('Page |'):
            continue
        if ('Testator' in stripped and 'Witness 1' in stripped and 'Witness 2' in stripped):
            continue
        if stripped.replace('_', '').replace(' ', '').replace('|', '') == '':
            continue

        if 'REST OF THE PAGE IS INTENTIONALLY LEFT BLANK' in stripped.upper():
            classified.append(('blank', f'<p class="blank-notice">{stripped}</p>'))
            continue

        is_section = stripped in _SECTION_HEADINGS
        is_numbered = _is_numbered_clause(stripped)
        is_heading = (
            stripped.isupper() and len(stripped) < 80
            and not stripped.startswith('(') and not stripped.startswith('-')
        )
        is_indented = line.startswith('    ') or line.startswith('\t')

        if is_section:
            classified.append(('section', f'<h3 class="section-heading">{stripped}</h3>'))
        elif is_numbered and is_heading:
            # Uppercase numbered heading like "1. REVOCATION"
            classified.append(('clause-start', f'<p class="clause-heading">{stripped}</p>'))
        elif is_heading:
            classified.append(('heading', f'<h2 class="will-heading">{stripped}</h2>'))
        elif is_numbered:
            # Numbered clause like "4. I direct my Executor..."
            classified.append(('clause-start', f'<p class="clause-start">{stripped}</p>'))
        elif is_indented:
            classified.append(('indented', f'<p class="indented">{stripped}</p>'))
        else:
            classified.append(('text', f'<p>{stripped}</p>'))

    # Second pass: group into clause blocks to prevent page-break splits
    html_lines = []
    i = 0
    while i < len(classified):
        typ, htm = classified[i]

        if typ == 'section':
            # Section heading: wrap with the next clause/content to keep together
            group = [htm]
            i += 1
            # Skip spacers after section heading
            while i < len(classified) and classified[i][0] == 'spacer':
                group.append(classified[i][1])
                i += 1
            # Include the first clause block after the section heading
            if i < len(classified) and classified[i][0] in ('clause-start', 'text', 'heading'):
                group.append(classified[i][1])
                i += 1
                # Include continuation lines (non-numbered, non-heading, non-section)
                while i < len(classified) and classified[i][0] in ('text', 'indented'):
                    group.append(classified[i][1])
                    i += 1
            # Section groups flow naturally — no break-inside:avoid to prevent big gaps
            html_lines.append('\n'.join(group))

        elif typ == 'clause-start':
            # Numbered clause: group with continuation paragraphs
            # But limit group size to prevent large empty spaces
            group = [htm]
            i += 1
            line_count = 1
            MAX_GROUP_LINES = 8  # Max lines to keep together — prevents big gaps
            while i < len(classified) and line_count < MAX_GROUP_LINES:
                next_typ = classified[i][0]
                if next_typ in ('text', 'indented'):
                    group.append(classified[i][1])
                    i += 1
                    line_count += 1
                elif next_typ == 'spacer':
                    j = i + 1
                    while j < len(classified) and classified[j][0] == 'spacer':
                        j += 1
                    if j < len(classified) and classified[j][0] in ('text', 'indented'):
                        while i < j:
                            group.append(classified[i][1])
                            i += 1
                    else:
                        break
                else:
                    break
            html_lines.append(f'<div class="clause-group">\n' + '\n'.join(group) + '\n</div>')

        else:
            html_lines.append(htm)
            i += 1

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
            <td class="sign-field" style="text-align:right;"><span class="date-hint">(dd/mm/yyyy)</span></td>
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


def _build_cover_page_html(testator_name: str, will_text: str, logo_html: str, firm_info: dict = None) -> str:
    """Build cover page HTML with firm logo, testator name, and NRIC."""
    if not firm_info:
        return ''  # No cover page if no firm info

    # Extract NRIC from will text
    import re
    nric_match = re.search(r'NRIC\s*No\.?\s*[:\s]*(\d{6}-\d{2}-\d{4})', will_text)
    nric = nric_match.group(1) if nric_match else ''

    firm_name = html.escape(firm_info.get('firm_name', ''))
    firm_address = html.escape(firm_info.get('firm_address', ''))

    cover_logo = logo_html.replace('header-logo', 'cover-logo') if logo_html else ''

    return f"""
    <div class="cover-page">
        <div class="cover-spacer-top"></div>
        {cover_logo}
        <div class="cover-firm-address">{firm_address}</div>
        <hr class="cover-line">
        <div class="cover-title">The Last Will &amp; Testament</div>
        <div class="cover-title">of</div>
        <div class="cover-testator">{testator_name}</div>
        {'<div class="cover-nric">(NRIC No. ' + nric + ')</div>' if nric else ''}
    </div>"""


def _build_prepared_by_html(firm_info: dict = None) -> str:
    """Build 'Prepared By' last page HTML."""
    if not firm_info:
        return ''

    firm_name = html.escape(firm_info.get('firm_name', ''))
    firm_address = html.escape(firm_info.get('firm_address', ''))
    firm_phone = html.escape(firm_info.get('firm_phone', ''))
    firm_email = html.escape(firm_info.get('firm_email', ''))

    details = []
    if firm_address:
        details.append(firm_address)
    if firm_phone:
        details.append(f'TEL NO: {firm_phone}')
    if firm_email:
        details.append(f'EMAIL: {firm_email}')

    return f"""
    <div class="prepared-page">
        <hr class="prepared-line">
        <div class="prepared-heading">PREPARED BY:</div>
        <hr class="prepared-line">
        <div class="prepared-firm">{firm_name}</div>
        <div class="prepared-subtitle">ADVOCATES &amp; SOLICITORS</div>
        <div class="prepared-detail">{'<br>'.join(details)}</div>
        <hr class="prepared-line">
    </div>"""


def _will_text_to_html(will_text: str, title: str = "Last Will and Testament",
                       logo_path: str = None, firm_info: dict = None) -> str:
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

    /* Cover page: no header, no footer */
    @page cover {{
        size: A4;
        margin: 3cm 3.18cm 3cm 3.18cm;
        @top-center {{ content: none; }}
        @bottom-center {{ content: none; }}
    }}

    /* Prepared-by page: no header, no footer */
    @page prepared {{
        size: A4;
        margin: 3cm 3.18cm 3cm 3.18cm;
        @top-center {{ content: none; }}
        @bottom-center {{ content: none; }}
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

        /* Signing page footer: page number + line only, no signature boxes */
        @bottom-left {{ content: none; }}
        @bottom-right {{ content: none; }}
        @bottom-center {{
            content: element(signingFooter);
            width: 100%;
        }}
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
    .page-footer .footer-top-line {{
        border-top: 0.5pt solid #000;
        height: 0;
        margin: 0;
        padding: 0;
    }}
    .page-footer .footer-info {{
        font-size: 8pt;
        text-align: left;
        padding: 2pt 0 2pt 0;
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

    /* Signing page footer: page number + line only, no signature boxes */
    .signing-footer {{
        position: running(signingFooter);
        font-family: 'Times New Roman', Times, serif;
        width: 100%;
    }}
    .signing-footer .footer-top-line {{
        border-top: 0.5pt solid #000;
        height: 0;
        margin: 0;
        padding: 0;
    }}
    .signing-footer .footer-info {{
        font-size: 8pt;
        text-align: left;
        padding: 2pt 0 2pt 0;
    }}
    .signing-footer .page-num::after {{
        content: counter(page);
    }}

    /* === Body Styles === */
    body {{
        font-family: 'Times New Roman', Times, serif;
        font-size: 12pt;
        line-height: 1.4;
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
        margin: 10pt 0 4pt 0;
        font-family: 'Times New Roman', Times, serif;
        text-decoration: underline;
        break-after: avoid;
        page-break-after: avoid;
    }}

    /* Section group: heading + first clause kept together, but CAN span pages */
    .section-group {{
        /* Sections CAN span 2 pages — don't force avoid */
    }}

    /* Clause group: keep paragraphs together to avoid mid-paragraph breaks */
    .clause-group {{
        break-inside: avoid;
        page-break-inside: avoid;
    }}

    /* Numbered clause headings (all-uppercase like "1. REVOCATION") */
    p.clause-heading {{
        font-weight: bold;
        margin-top: 8pt;
        break-after: avoid;
        page-break-after: avoid;
    }}

    /* Numbered clause start (regular case like "4. I direct...") */
    p.clause-start {{
        margin-top: 2pt;
    }}

    /* Indented sub-clauses */
    p.indented {{
        margin-left: 36pt;
    }}

    /* Regular paragraphs */
    p {{
        margin: 3pt 0;
        orphans: 4;
        widows: 3;
    }}

    /* Spacer between paragraphs — minimal to prevent content alteration */
    div.spacer {{
        height: 3pt;
    }}

    /* "THE REST OF THE PAGE IS INTENTIONALLY LEFT BLANK" */
    p.blank-notice {{
        text-align: center;
        margin-top: 24pt;
        font-size: 10pt;
        letter-spacing: 0.5pt;
        break-before: auto;
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

    /* === Cover Page === */
    .cover-page {{
        page: cover;
        text-align: center;
        page-break-after: always;
    }}
    .cover-spacer-top {{
        height: 120pt;
    }}
    .cover-logo {{
        max-height: 160pt;
        max-width: 260pt;
        margin: 0 auto 16pt auto;
        display: block;
    }}
    .cover-firm-address {{
        font-size: 10pt;
        color: #333;
        margin-bottom: 20pt;
        line-height: 1.5;
    }}
    .cover-line {{
        width: 100%;
        border: none;
        border-top: 0.5pt solid #000;
        margin: 16pt auto;
    }}
    .cover-title {{
        font-size: 16pt;
        font-weight: bold;
        line-height: 2.0;
    }}
    .cover-testator {{
        font-size: 16pt;
        font-weight: bold;
    }}
    .cover-nric {{
        font-size: 14pt;
        font-weight: bold;
        margin-top: 4pt;
    }}

    /* === Prepared By Page === */
    .prepared-page {{
        page: prepared;
        page-break-before: always;
        text-align: center;
        padding-top: 120pt;
    }}
    .prepared-heading {{
        font-size: 14pt;
        font-weight: bold;
        text-decoration: underline;
        margin-bottom: 30pt;
    }}
    .prepared-firm {{
        font-size: 14pt;
        font-weight: bold;
        margin-bottom: 4pt;
    }}
    .prepared-subtitle {{
        font-size: 12pt;
        font-weight: bold;
        margin-bottom: 20pt;
    }}
    .prepared-detail {{
        font-size: 11pt;
        line-height: 1.8;
    }}
    .prepared-line {{
        width: 40%;
        border: none;
        border-top: 1pt solid #000;
        margin: 20pt auto;
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
    <div class="footer-top-line"></div>
    <div class="footer-info">Page| <span class="page-num"></span></div>
    <table class="sig-boxes">
        <tr>
            <td>Testator</td>
            <td>Witness 1</td>
            <td>Witness 2</td>
        </tr>
    </table>
</div>

<!-- Signing page footer: page number only, no signature boxes -->
<div class="signing-footer">
    <div class="footer-top-line"></div>
    <div class="footer-info">Page| <span class="page-num"></span></div>
</div>

{_build_cover_page_html(escaped_testator, will_text, logo_html, firm_info)}
{content_html}
{signing_html}
{_build_prepared_by_html(firm_info)}
</body>
</html>"""


def generate_pdf(will_text: str, filename_base: str = "Will",
                 logo_path: str = None, firm_info: dict = None) -> str:
    """
    Generate a PDF from the will text.

    Tries WeasyPrint first; falls back to a simple HTML-file-based PDF
    if WeasyPrint is not available.

    Args:
        will_text: The full will text to render.
        filename_base: Base name for the output file (without extension).
        logo_path: Optional path to a firm logo image to embed in the header.
        firm_info: Optional dict with firm details for cover page and last page:
            {firm_name, firm_address, firm_phone, firm_email}

    Returns:
        Absolute path to the generated .pdf file.
    """
    tmp_dir = tempfile.mkdtemp()
    filepath = os.path.join(tmp_dir, f'{filename_base}_Will.pdf')
    html_content = _will_text_to_html(
        will_text, title=f"{filename_base} - Last Will and Testament",
        logo_path=logo_path, firm_info=firm_info)

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
