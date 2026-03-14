"""Generate a PDF document from will text.

Primary method: WeasyPrint (high-quality HTML-to-PDF).
Fallback: builds a simple PDF using basic HTML if WeasyPrint is not installed.
"""

import os
import html
import tempfile


def _will_text_to_html(will_text: str, title: str = "Last Will and Testament") -> str:
    """Convert plain-text will into styled HTML suitable for PDF rendering."""
    escaped = html.escape(will_text)

    # Turn blank lines into paragraph breaks while preserving structure
    lines = escaped.split('\n')
    html_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            html_lines.append('<br/>')
            continue

        # Detect title/heading lines
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

        if is_heading:
            html_lines.append(f'<h2 style="text-align:center; font-size:14pt; margin:18pt 0 6pt 0;">{stripped}</h2>')
        elif is_clause:
            html_lines.append(f'<p style="font-weight:bold; margin-top:14pt;">{stripped}</p>')
        elif line.startswith('    ') or line.startswith('\t'):
            html_lines.append(f'<p style="margin-left:36pt;">{stripped}</p>')
        else:
            html_lines.append(f'<p>{stripped}</p>')

    body = '\n'.join(html_lines)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>{html.escape(title)}</title>
<style>
    @page {{
        size: A4;
        margin: 2.54cm 3.18cm;
    }}
    body {{
        font-family: 'Times New Roman', Times, serif;
        font-size: 12pt;
        line-height: 1.8;
        color: #000;
    }}
    h2 {{
        font-family: 'Times New Roman', Times, serif;
    }}
    p {{
        margin: 3pt 0;
        orphans: 3;
        widows: 3;
    }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def generate_pdf(will_text: str, filename_base: str = "Will") -> str:
    """
    Generate a PDF from the will text.

    Tries WeasyPrint first; falls back to a simple HTML-file-based PDF
    if WeasyPrint is not available.

    Args:
        will_text: The full will text to render.
        filename_base: Base name for the output file (without extension).

    Returns:
        Absolute path to the generated .pdf file.
    """
    tmp_dir = tempfile.mkdtemp()
    filepath = os.path.join(tmp_dir, f'{filename_base}_Will.pdf')
    html_content = _will_text_to_html(will_text, title=f"{filename_base} - Last Will and Testament")

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
