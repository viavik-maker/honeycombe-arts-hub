"""Paths and settings, read once at start-up."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC = os.path.join(ROOT, "public")
# HAH_DATA_DIR lets tests (and staging) point the server at a throwaway folder
DATA = os.environ.get("HAH_DATA_DIR") or os.path.join(ROOT, "data")
UPLOADS = os.path.join(DATA, "uploads")  # all editable state lives under data/
SEED = os.path.join(ROOT, "seed")  # bundled defaults, copied into DATA on first boot
PARTIALS = os.path.join(ROOT, "partials")

# On a brand-new install (no staff accounts, no data/auth.json), whoever sets
# up the first owner account must know this. Set it as a Render secret; it's
# never stored in the repo and there is no default.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
STAFF_SESSION_HOURS = 12      # staff are signed out after this long regardless
STAFF_IDLE_MINUTES = 60       # ... or after this long without using the admin
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

# The site's public address, used for every link in emails and texts (and by
# Stripe to send people back). Set it on Render (e.g. https://honeycombeartshub.org.uk):
# without it, links fall back to the Host header of the request, which is fine
# for local development but must not be trusted in production.
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")

# Optional separate address for staff, e.g. staff.honeycombeartshub.org.uk (host name only). When set, the admin
# and every staff API answer only there: the public site redirects /admin to it and refuses staff APIs, and staff
# sign-in cookies (host-only) are never sent to the public site. Add it as a custom domain on the same Render service.
STAFF_HOST = os.environ.get("STAFF_HOST", "").strip().lower().rstrip("/").replace("https://", "").replace("http://", "")

# Staging copies set HAH_NOINDEX=1 so search engines never index them.
NOINDEX = os.environ.get("HAH_NOINDEX") == "1"

# ---------------------------------------------------------------- booking system

DB_PATH = os.path.join(DATA, "booking.db")  # SQLite; never inside content.json
BACKUPS = os.path.join(DATA, "backups")      # local nightly snapshots (kept a week)
TIMEZONE = "Europe/London"                   # sessions, registers and job times are UK local

# Off-site backups (optional until real family data is stored — then required).
# The archive is encrypted to the trustees' certificate before it leaves the
# server; only whoever holds the matching private key (offline) can open it.
BACKUP_CERT = os.environ.get("BACKUP_CERT", "")               # PEM certificate text
BACKUP_S3_ENDPOINT = os.environ.get("BACKUP_S3_ENDPOINT", "")  # e.g. https://s3.eu-west-2.amazonaws.com
BACKUP_S3_REGION = os.environ.get("BACKUP_S3_REGION", "")      # e.g. eu-west-2
BACKUP_S3_BUCKET = os.environ.get("BACKUP_S3_BUCKET", "")
BACKUP_S3_ACCESS_KEY = os.environ.get("BACKUP_S3_ACCESS_KEY", "")
BACKUP_S3_SECRET_KEY = os.environ.get("BACKUP_S3_SECRET_KEY", "")
