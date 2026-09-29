"""Passwords, tokens and two-factor codes (stdlib only).

Passwords: PBKDF2-HMAC-SHA256, stored as pbkdf2_sha256$ITERATIONS$SALT$HASH so
the work factor can be raised later (hashes are upgraded at next login).
Tokens: random, URL-safe; only their SHA-256 is ever stored.
Two-factor: TOTP (RFC 6238, 30-second steps, 6 digits) — works with any
authenticator app (Google/Microsoft Authenticator, 1Password, etc.)."""
import base64
import hashlib
import hmac
import os
import secrets
import struct
import threading
import time
import urllib.parse

# OWASP 2023 guidance for PBKDF2-SHA256. Tests lower it via the environment.
ITERATIONS = int(os.environ.get("HAH_PBKDF2_ITERATIONS") or 600_000)

# Password hashing is deliberately slow; don't let a flood of logins starve
# the rest of the site.
_hash_slots = threading.BoundedSemaphore(2)


class Busy(Exception):
    """Too many password checks at once — answer 429 and let them retry."""


def _pbkdf2(password, salt, iterations):
    if not _hash_slots.acquire(timeout=10):
        raise Busy()
    try:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    finally:
        _hash_slots.release()


def hash_password(password, iterations=None):
    iterations = iterations or ITERATIONS
    salt = secrets.token_bytes(16)
    dk = _pbkdf2(password, salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, base64.b64encode(salt).decode(),
                                       base64.b64encode(dk).decode())


def verify_password(password, stored):
    """True if PASSWORD matches STORED. Unknown formats never match."""
    try:
        scheme, iterations, salt, expected = (stored or "").split("$")
        if scheme != "pbkdf2_sha256":
            return False
        dk = _pbkdf2(password, base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(base64.b64encode(dk).decode(), expected)


def needs_rehash(stored):
    try:
        return int(stored.split("$")[1]) < ITERATIONS
    except (AttributeError, IndexError, ValueError):
        return True


# a hash to check against when the email isn't known, so the response takes
# as long as a real check and doesn't reveal which emails have accounts
_DUMMY = None


def dummy_verify(password):
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_hex(8))
    verify_password(password, _DUMMY)
    return False


PASSWORD_MIN = 10


def password_problem(password, *, email=""):
    """A reason the password isn't acceptable, or None. (NCSC/NIST style:
    length over complexity rules, and no obvious choices.)"""
    if len(password) < PASSWORD_MIN:
        return "Use at least %d characters — three random words works well." % PASSWORD_MIN
    if len(password) > 200:
        return "That password is too long."
    low = password.lower()
    if low in _COMMON or len(set(low)) < 4:
        return "That password is too easy to guess — try three random words."
    if email and email.split("@")[0].lower() in low and len(email.split("@")[0]) > 3:
        return "Don't include your email address in your password."
    return None


_COMMON = {
    "password", "password1", "password12", "password123", "password1234", "passw0rd123",
    "1234567890", "12345678910", "0123456789", "qwertyuiop", "qwerty1234", "qwerty12345",
    "iloveyou123", "letmein123", "welcome123", "welcome1234", "football123", "baseball123",
    "honeycomb2026", "honeycombe2026", "honeycombe1", "honeycombearts", "artshub2026",
    "administrator", "admin12345", "changeme123", "trustno1234", "sunshine123", "princess123",
}


# ---------------------------------------------------------------- tokens


def new_token(nbytes=32):
    return secrets.token_urlsafe(nbytes)


def hash_token(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- TOTP (RFC 6238)

STEP = 30
DIGITS = 6


def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _b32decode(secret):
    s = secret.upper().replace(" ", "")
    return base64.b32decode(s + "=" * (-len(s) % 8))


def hotp(key, counter, digits=DIGITS, digest=hashlib.sha1):
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp(secret, at=None):
    return hotp(_b32decode(secret), int((at if at is not None else time.time()) // STEP))


def verify_totp(secret, code, last_step=None, at=None, window=1):
    """The time step CODE matched (to store as last_step), or None.
    Accepts one step either side for clock drift; a step at or before
    LAST_STEP is refused, so an overheard code can't be replayed."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    now_step = int((at if at is not None else time.time()) // STEP)
    key = _b32decode(secret)
    for step in range(now_step - window, now_step + window + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(hotp(key, step), code):
            return step
    return None


def totp_uri(secret, account, issuer="Honeycombe Arts Hub"):
    label = urllib.parse.quote("%s:%s" % (issuer, account))
    return "otpauth://totp/%s?secret=%s&issuer=%s&algorithm=SHA1&digits=%d&period=%d" % (
        label, secret, urllib.parse.quote(issuer), DIGITS, STEP)


def new_recovery_codes(n=10):
    """Human-friendly one-time codes (shown once) and their hashes (stored)."""
    codes = ["%s-%s" % (secrets.token_hex(3), secrets.token_hex(3)) for _ in range(n)]
    return codes, [hash_token(c) for c in codes]


# ---------------------------------------------------------------- app secrets

_secret_cache = {}


def app_secret(name):
    """A server secret (bytes): HAH_<NAME> from the environment if set (the
    recommended way), else one generated once and kept in data/secrets.json
    (mode 0600, included in the encrypted backups)."""
    import json
    from . import config
    if name in _secret_cache:
        return _secret_cache[name]
    env = os.environ.get("HAH_" + name.upper())
    if env:
        value = env.encode()
    else:
        path = os.path.join(config.DATA, "secrets.json")
        try:
            with open(path) as f:
                stored = json.load(f)
        except (OSError, ValueError):
            stored = {}
        if name not in stored:
            stored[name] = secrets.token_urlsafe(32)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(stored, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        value = stored[name].encode()
    _secret_cache[name] = value
    return value


def signed(message, name="secret_key"):
    """HMAC-SHA256 of MESSAGE with an app secret, URL-safe (for unsubscribe links etc.)."""
    mac = hmac.new(app_secret(name), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:18]).decode().rstrip("=")


def hash_collection_password(password):
    """Collection passwords are short words, so they're peppered with a server
    secret before hashing: a copy of the database alone can't be guessed at."""
    return hash_password(_norm_cp(password), iterations=100_000) + "$p"


def verify_collection_password(password, stored):
    if not stored or not stored.endswith("$p"):
        return False
    return verify_password(_norm_cp(password), stored[:-2])


def _norm_cp(password):
    norm = " ".join((password or "").casefold().split())
    return hmac.new(app_secret("pepper"), norm.encode(), hashlib.sha256).hexdigest()
