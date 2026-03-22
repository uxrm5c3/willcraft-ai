"""
Generate a verification PDF showing uploaded documents alongside extracted/entered data.
Layout: Left = document image, Right = field data table.
"""
import os
import base64
import tempfile
from datetime import datetime


def _image_to_data_uri(file_path):
    """Convert image or PDF file to base64 data URI for embedding in HTML.
    For PDFs, converts first page to JPEG image."""
    if not file_path or not os.path.exists(file_path):
        return None
    ext = os.path.splitext(file_path)[1].lower()

    if ext == '.pdf':
        # Convert first page of PDF to image
        try:
            import subprocess
            import tempfile
            tmp = tempfile.mktemp(suffix='.jpg')
            # Try pdftoppm first (poppler)
            result = subprocess.run(
                ['pdftoppm', '-jpeg', '-r', '150', '-f', '1', '-l', '1', '-singlefile', file_path, tmp.replace('.jpg', '')],
                capture_output=True, timeout=10
            )
            out_file = tmp.replace('.jpg', '') + '.jpg'
            if os.path.exists(out_file):
                with open(out_file, 'rb') as f:
                    data = base64.b64encode(f.read()).decode('utf-8')
                os.unlink(out_file)
                return f"data:image/jpeg;base64,{data}"
        except Exception:
            pass
        # Fallback: try using WeasyPrint/PIL
        try:
            from PIL import Image
            import fitz  # PyMuPDF
            doc = fitz.open(file_path)
            page = doc[0]
            pix = page.get_pixmap(dpi=150)
            img_data = pix.tobytes("jpeg")
            doc.close()
            data = base64.b64encode(img_data).decode('utf-8')
            return f"data:image/jpeg;base64,{data}"
        except Exception:
            pass
        return None

    mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                '.gif': 'image/gif', '.webp': 'image/webp'}
    mime = mime_map.get(ext, 'image/png')
    with open(file_path, 'rb') as f:
        data = base64.b64encode(f.read()).decode('utf-8')
    return f"data:{mime};base64,{data}"


def generate_verification_pdf(persons, gifts, documents_map, testator_name=""):
    """Generate a verification PDF with documents and field data side by side.

    Args:
        persons: list of dicts with person data + document info
        gifts: list of dicts with gift data + document info
        documents_map: dict mapping document_id -> file_path
        testator_name: name for the PDF title

    Returns:
        filepath to generated PDF
    """
    html_sections = []

    # --- Header ---
    html_sections.append(f"""
    <div style="text-align:center; margin-bottom:30px; border-bottom:2px solid #333; padding-bottom:15px;">
        <h1 style="font-size:18pt; margin:0;">VERIFICATION DOCUMENT</h1>
        <p style="font-size:11pt; color:#666; margin:5px 0 0 0;">
            {testator_name} &mdash; Generated {datetime.now().strftime('%d %B %Y, %I:%M %p')}
        </p>
    </div>
    """)

    # --- Identity Section ---
    persons_with_docs = [p for p in persons if p.get('document_id')]
    if persons_with_docs:
        html_sections.append('<h2 style="font-size:14pt; color:#333; border-bottom:1px solid #ccc; padding-bottom:5px;">IDENTITIES</h2>')

        for p in persons_with_docs:
            doc_path = documents_map.get(p['document_id'])
            data_uri = _image_to_data_uri(doc_path) if doc_path else None

            html_sections.append(f"""
            <div style="display:flex; gap:20px; margin:15px 0; page-break-inside:avoid; border:1px solid #ddd; border-radius:8px; padding:15px;">
                <div style="flex:0 0 45%; max-width:45%;">
                    <p style="font-size:9pt; color:#999; margin:0 0 5px 0;">Uploaded Document</p>
                    {'<img src="' + data_uri + '" style="width:100%; border:1px solid #ccc; border-radius:4px;">' if data_uri else '<div style="background:#f5f5f5; padding:30px; text-align:center; color:#999; border-radius:4px;">Document not available</div>'}
                </div>
                <div style="flex:1;">
                    <p style="font-size:9pt; color:#999; margin:0 0 5px 0;">Entered Data</p>
                    <table style="width:100%; border-collapse:collapse; font-size:10pt;">
                        <tr style="border-bottom:1px solid #eee;"><td style="padding:4px 8px; color:#666; width:35%;">Full Name</td><td style="padding:4px 8px; font-weight:bold;">{p.get('full_name', '-')}</td></tr>
                        <tr style="border-bottom:1px solid #eee;"><td style="padding:4px 8px; color:#666;">NRIC/Passport</td><td style="padding:4px 8px; font-weight:bold;">{p.get('nric_passport', '-')}</td></tr>
                        <tr style="border-bottom:1px solid #eee;"><td style="padding:4px 8px; color:#666;">Nationality</td><td style="padding:4px 8px;">{p.get('nationality', '-')}</td></tr>
                        <tr style="border-bottom:1px solid #eee;"><td style="padding:4px 8px; color:#666;">Date of Birth</td><td style="padding:4px 8px;">{p.get('date_of_birth', '-')}</td></tr>
                        <tr style="border-bottom:1px solid #eee;"><td style="padding:4px 8px; color:#666;">Gender</td><td style="padding:4px 8px;">{p.get('gender', '-')}</td></tr>
                        <tr style="border-bottom:1px solid #eee;"><td style="padding:4px 8px; color:#666;">Relationship</td><td style="padding:4px 8px;">{p.get('relationship', '-')}</td></tr>
                        <tr><td style="padding:4px 8px; color:#666;">Address</td><td style="padding:4px 8px; font-size:9pt;">{p.get('address', '-')}</td></tr>
                    </table>
                </div>
            </div>
            """)

    # --- Property Gifts Section ---
    property_gifts = [g for g in gifts if g.get('gift_type') == 'property']
    gifts_with_docs = [g for g in property_gifts if g.get('documents')]
    if gifts_with_docs:
        html_sections.append('<div style="page-break-before:always;"></div>')
        html_sections.append('<h2 style="font-size:14pt; color:#333; border-bottom:1px solid #ccc; padding-bottom:5px;">PROPERTY GIFTS</h2>')

        for i, g in enumerate(gifts_with_docs):
            prop = g.get('property_details', {})
            docs = g.get('documents', [])
            first_doc = docs[0] if docs else {}
            doc_id = first_doc.get('document_id', '')
            # Extract document_id from URL if not set directly
            if not doc_id and first_doc.get('url'):
                url_parts = first_doc['url'].rstrip('/').split('/')
                if len(url_parts) >= 3:
                    doc_id = url_parts[-1]
            doc_path = documents_map.get(doc_id) if doc_id else None
            data_uri = _image_to_data_uri(doc_path) if doc_path else None

            html_sections.append(f"""
            <div style="display:flex; gap:20px; margin:15px 0; page-break-inside:avoid; border:1px solid #ddd; border-radius:8px; padding:15px;">
                <div style="flex:0 0 45%; max-width:45%;">
                    <p style="font-size:9pt; color:#999; margin:0 0 5px 0;">Title Document (Gift {i+1})</p>
                    {'<img src="' + data_uri + '" style="width:100%; border:1px solid #ccc; border-radius:4px;">' if data_uri else '<div style="background:#f5f5f5; padding:30px; text-align:center; color:#999; border-radius:4px;">Document not available</div>'}
                </div>
                <div style="flex:1;">
                    <p style="font-size:9pt; color:#999; margin:0 0 5px 0;">Entered Property Data</p>
                    <table style="width:100%; border-collapse:collapse; font-size:10pt;">
                        <tr style="border-bottom:1px solid #eee;"><td style="padding:4px 8px; color:#666; width:35%;">Address</td><td style="padding:4px 8px; font-size:9pt;">{prop.get('property_address', '-')}</td></tr>
                        <tr style="border-bottom:1px solid #eee;"><td style="padding:4px 8px; color:#666;">Title Type</td><td style="padding:4px 8px; font-weight:bold;">{prop.get('title_type', '-')}</td></tr>
                        <tr style="border-bottom:1px solid #eee;"><td style="padding:4px 8px; color:#666;">Title Number</td><td style="padding:4px 8px; font-weight:bold;">{prop.get('title_number', '-')}</td></tr>
                        <tr style="border-bottom:1px solid #eee;"><td style="padding:4px 8px; color:#666;">Lot Number</td><td style="padding:4px 8px; font-weight:bold;">{prop.get('lot_number', '-')}</td></tr>
                        <tr style="border-bottom:1px solid #eee;"><td style="padding:4px 8px; color:#666;">Mukim</td><td style="padding:4px 8px;">{prop.get('bandar_pekan', '-')}</td></tr>
                        <tr style="border-bottom:1px solid #eee;"><td style="padding:4px 8px; color:#666;">Daerah</td><td style="padding:4px 8px;">{prop.get('daerah', '-')}</td></tr>
                        <tr><td style="padding:4px 8px; color:#666;">Negeri</td><td style="padding:4px 8px;">{prop.get('negeri', '-')}</td></tr>
                    </table>
                </div>
            </div>
            """)

    # --- No documents notice ---
    if not persons_with_docs and not gifts_with_docs:
        html_sections.append('<p style="text-align:center; color:#999; padding:40px;">No documents have been uploaded yet. Upload IC/passport and property title documents to generate this verification report.</p>')

    # Build full HTML
    full_html = f"""<!DOCTYPE html>
    <html><head>
    <meta charset="utf-8">
    <style>
        @page {{ size: A4; margin: 2cm; }}
        body {{ font-family: 'Helvetica Neue', Arial, sans-serif; color: #333; line-height: 1.5; }}
    </style>
    </head><body>
    {''.join(html_sections)}
    </body></html>"""

    # Generate PDF
    safe_name = "".join(c for c in testator_name if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_') or 'Will'
    try:
        import weasyprint
        tmp_dir = tempfile.mkdtemp()
        filepath = os.path.join(tmp_dir, f"Verification_{safe_name}.pdf")
        weasyprint.HTML(string=full_html).write_pdf(filepath)
        return filepath
    except Exception as e:
        # Fallback: save as HTML
        tmp_dir = tempfile.mkdtemp()
        filepath = os.path.join(tmp_dir, f"Verification_{safe_name}.html")
        with open(filepath, 'w') as f:
            f.write(full_html)
        return filepath
