"""The staff admin password and login sessions."""
import base64
import hashlib
import hmac
import secrets
import time

from . import config
from .storage import load_json, save_json, update_json


def _hash_password(password, salt, iterations=120_000):
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return base64.b64encode(dk).decode()


def init_auth():
    auth = load_json("auth.json", None)
    if not auth:
        salt = secrets.token_bytes(16)
        auth = {
            "salt": base64.b64encode(salt).decode(),
            "hash": _hash_password(config.DEFAULT_PASSWORD, salt),
            "iterations": 120_000,
        }
        save_json("auth.json", auth)
    return auth


def check_password(password):
    auth = load_json("auth.json", None) or init_auth()
    salt = base64.b64decode(auth["salt"])
    expect = auth["hash"]
    got = _hash_password(password, salt, auth.get("iterations", 120_000))
    return hmac.compare_digest(expect, got)


def set_password(password):
    salt = secrets.token_bytes(16)
    save_json("auth.json", {
        "salt": base64.b64encode(salt).decode(),
        "hash": _hash_password(password, salt),
        "iterations": 120_000,
    })


def _live(s):
    now = time.time()
    return {k: v for k, v in (s or {}).items() if v > now}


def sessions():
    s = load_json("sessions.json", {})
    live = _live(s)
    if len(live) != len(s):
        update_json("sessions.json", {}, _live)
    return live


def new_session():
    tok = secrets.token_urlsafe(32)
    update_json("sessions.json", {},
                lambda s: {**_live(s), tok: time.time() + config.SESSION_TTL})
    return tok


def drop_session(tok):
    update_json("sessions.json", {},
                lambda s: {k: v for k, v in _live(s).items() if k != tok})
