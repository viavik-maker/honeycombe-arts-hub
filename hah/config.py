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
BODY_LIMIT = 64 * 1024  # default request-body cap; routes that need more say so

# How many proxies sit in front of us and append to X-Forwarded-For. Render
# adds one; check the real client IP arrives as expected on staging.
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS") or (1 if os.environ.get("RENDER") else 0))
MAX_CONCURRENT = int(os.environ.get("HAH_MAX_CONCURRENT") or 64)  # requests handled at once
REQUEST_TIMEOUT = 30  # seconds a client may take to send its request

# Content-Security-Policy: "report" (browsers only report violations) while we
# check nothing legitimate breaks, then "enforce".
CSP_MODE = os.environ.get("HAH_CSP", "report")
