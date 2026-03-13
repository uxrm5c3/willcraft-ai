"""File upload handling utilities for WillCraft AI."""

import os
import uuid
from werkzeug.utils import secure_filename
from config import UPLOAD_DIR

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'docx', 'doc'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_uploaded_file(file, client_id, category='general'):
    """Save an uploaded file to the client's document folder.
    Returns (saved_filename, relative_path, file_size).
    """
    if not allowed_file(file.filename):
        raise ValueError(f"File type not allowed: {file.filename}")

    ext = file.filename.rsplit('.', 1)[1].lower()
    safe_name = f"{uuid.uuid4().hex[:12]}.{ext}"

    folder = os.path.join(UPLOAD_DIR, client_id, 'documents', category)
    os.makedirs(folder, exist_ok=True)

    filepath = os.path.join(folder, safe_name)
    file.save(filepath)
    file_size = os.path.getsize(filepath)

    if file_size > MAX_FILE_SIZE:
        os.remove(filepath)
        raise ValueError(f"File too large: {file_size} bytes (max {MAX_FILE_SIZE})")

    relative_path = os.path.join(client_id, 'documents', category, safe_name)
    return safe_name, relative_path, file_size
