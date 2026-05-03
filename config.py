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
# Model tiers — pick by accuracy-vs-cost tradeoff at each call site:
#   CLAUDE_MODEL       — Sonnet. Drafter, will parser, BM↔EN translation.
#                        Final-deliverable quality matters more than cost.
#   CLAUDE_MODEL_FAST  — Sonnet. Vision OCR (NRIC, property, bank, etc.) —
#                        first-pass accuracy on document numbers is critical;
#                        re-OCR fallback for low-confidence digits also runs
#                        on this tier so we keep one good vision model.
#   CLAUDE_MODEL_CHEAP — Haiku. Cheap structured calls where the answer is
#                        either an enum, short JSON, or formatted from
#                        injected context (file_classifier, role_deducer,
#                        legal_qa, asset_extractor, property_extractor).
#                        ~3× cheaper input, ~3× cheaper output vs Sonnet.
# Override via env vars on the EC2 host without code changes.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
CLAUDE_MODEL_FAST = os.getenv("CLAUDE_MODEL_FAST", "claude-sonnet-4-20250514")
CLAUDE_MODEL_CHEAP = os.getenv("CLAUDE_MODEL_CHEAP", "claude-haiku-4-5")
MAX_TOKENS = 8000

# Database & file storage
BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(DATA_DIR, 'willcraft.db')}"
UPLOAD_DIR = os.path.join(DATA_DIR, 'clients')
