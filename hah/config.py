"""Paths and settings, read once at start-up."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC = os.path.join(ROOT, "public")
# HAH_DATA_DIR lets tests (and staging) point the server at a throwaway folder
DATA = os.environ.get("HAH_DATA_DIR") or os.path.join(ROOT, "data")
UPLOADS = os.path.join(DATA, "uploads")  # all editable state lives under data/
SEED = os.path.join(ROOT, "seed")  # bundled defaults, copied into DATA on first boot
PARTIALS = os.path.join(ROOT, "partials")

# Initial admin password for the very first login. Set ADMIN_PASSWORD in the
# host environment (e.g. a Render secret) so it is never committed to the repo.
# Only used to seed auth.json on first run; change it in the CMS afterwards.
DEFAULT_PASSWORD = os.environ.get("ADMIN_PASSWORD", "honeycomb2026")
SESSION_TTL = 60 * 60 * 24 * 7  # 7 days
MAX_UPLOAD = 15 * 1024 * 1024
