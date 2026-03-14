import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
CLAUDE_MODEL = "claude-sonnet-4-20250514"
CLAUDE_MODEL_FAST = "claude-3-5-haiku-20241022"  # Faster model for OCR tasks
MAX_TOKENS = 8000

# Database & file storage
BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(DATA_DIR, 'willcraft.db')}"
UPLOAD_DIR = os.path.join(DATA_DIR, 'clients')
