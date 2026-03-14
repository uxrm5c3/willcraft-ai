"""File upload handling utilities for WillCraft AI."""

import os
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from config import UPLOAD_DIR

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'docx', 'doc', 'heic', 'heif', 'webp', 'bmp', 'tiff', 'tif'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_uploaded_file(file, client_id, category='general', folder_name=None):
    """Save an uploaded file to the client's document folder.

    If folder_name is provided, files are stored under that friendly name
    (e.g. 'TAN_AH_KOW_a1b2c3d4') instead of the raw client UUID.
    Returns (saved_filename, relative_path, file_size).
    """
    if not allowed_file(file.filename):
        raise ValueError(f"File type not allowed: {file.filename}")

    ext = file.filename.rsplit('.', 1)[1].lower()
    safe_name = f"{uuid.uuid4().hex[:12]}.{ext}"

    base_folder = folder_name or client_id
    folder = os.path.join(UPLOAD_DIR, base_folder, 'documents', category)
    os.makedirs(folder, exist_ok=True)

    filepath = os.path.join(folder, safe_name)
    file.save(filepath)
    file_size = os.path.getsize(filepath)

    if file_size > MAX_FILE_SIZE:
        os.remove(filepath)
        raise ValueError(f"File too large: {file_size} bytes (max {MAX_FILE_SIZE})")

    relative_path = os.path.join(base_folder, 'documents', category, safe_name)
    return safe_name, relative_path, file_size


def save_generated_will(client_folder_name, file_bytes, fmt, is_draft=True):
    """Save a generated will file to the client's persistent folder.

    Folder structure:
      data/clients/{client_folder_name}/
        drafts/YYYY-MM-DD_draft_v{N}.{fmt}
        generated/YYYY-MM-DD_will_final.{fmt}

    Returns (filename, relative_path).
    """
    date_str = datetime.now().strftime('%Y-%m-%d')
    if is_draft:
        subfolder = 'drafts'
        draft_dir = os.path.join(UPLOAD_DIR, client_folder_name, subfolder)
        os.makedirs(draft_dir, exist_ok=True)
        existing = [f for f in os.listdir(draft_dir) if f.startswith(date_str) and f.endswith(f'.{fmt}')]
        version = len(existing) + 1
        filename = f"{date_str}_draft_v{version}.{fmt}"
    else:
        subfolder = 'generated'
        filename = f"{date_str}_will_final.{fmt}"

    folder = os.path.join(UPLOAD_DIR, client_folder_name, subfolder)
    os.makedirs(folder, exist_ok=True)

    filepath = os.path.join(folder, filename)
    with open(filepath, 'wb') as f:
        f.write(file_bytes)

    relative_path = os.path.join(client_folder_name, subfolder, filename)
    return filename, relative_path
