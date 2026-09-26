"""The old shared "team password" — kept only so the first owner account can
be created safely on a site that's already live.

Staff now sign in with their own accounts (hah/staff.py). Until the first
owner exists, whoever sets it up must prove they know the current team
password (the one in data/auth.json on the live site, or ADMIN_PASSWORD on a
brand-new install). Once the owner exists, the team password is deleted."""
import base64
import hashlib
import hmac
import os

from . import config
from .storage import load_json, path


# the old README published this as the default; it can't prove anything
PUBLISHED_DEFAULT = "honeycomb2026"


def _stored_matches(stored, password):
    salt = base64.b64decode(stored["salt"])
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode(), salt, stored.get("iterations", 120_000))
    return hmac.compare_digest(stored["hash"], base64.b64encode(dk).decode())


def _usable_stored():
    """data/auth.json, unless it still holds the published default password."""
    stored = load_json("auth.json", None)
    if stored and not _stored_matches(stored, PUBLISHED_DEFAULT):
        return stored
    return None


def _env_password():
    return config.ADMIN_PASSWORD if config.ADMIN_PASSWORD not in ("", PUBLISHED_DEFAULT) else ""


def team_password_available():
    return bool(_usable_stored() or _env_password())


def team_password_ok(password):
    stored = _usable_stored()
    if stored:
        return _stored_matches(stored, password)
    if _env_password():
        return hmac.compare_digest((password or "").encode(), _env_password().encode())
    return False


def retire_team_password():
    """The shared password and its sessions stop working for good."""
    for name in ("auth.json", "sessions.json"):
        try:
            os.remove(path(name))
        except FileNotFoundError:
            pass
