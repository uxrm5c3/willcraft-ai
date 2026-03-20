import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

# SMTP settings (Google Workspace — SMTP AUTH with app password)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"
CLAUDE_MODEL_FAST = "claude-sonnet-4-20250514"  # Same as main model (Haiku not available on this API key)
MAX_TOKENS = 8000

# Database & file storage
BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(DATA_DIR, 'willcraft.db')}"
UPLOAD_DIR = os.path.join(DATA_DIR, 'clients')
