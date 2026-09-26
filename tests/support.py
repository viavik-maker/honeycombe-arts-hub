"""Shared test harness.

Runs the real web server on a random local port against a throwaway data
folder (via HAH_DATA_DIR), plus a tiny HTTP client that keeps cookies and
sends a same-origin Origin header the way a browser would.

Stdlib only — run everything with:  python3 -m unittest -v
"""
import atexit
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = tempfile.mkdtemp(prefix="hah-test-")
atexit.register(shutil.rmtree, DATA_DIR, ignore_errors=True)

# must be set before the app is imported: it reads them at import time
os.environ["HAH_DATA_DIR"] = DATA_DIR
os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD = "test-admin-password"
os.environ["HAH_PBKDF2_ITERATIONS"] = "1000"  # real hashing is deliberately slow
os.environ["MAIL_BACKEND"] = "memory"          # emails are kept in hah.mail.SENT
os.environ["SMS_PROVIDER"] = "log"              # texts are kept in hah.sms.SENT
for _k in [k for k in os.environ if k.startswith(("SMTP_", "TWILIO_", "BACKUP_S3_"))]:
    del os.environ[_k]
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import itertools  # noqa: E402

from hah import app, db, ratelimit, security, web  # noqa: E402
from hah import config  # noqa: E402

assert config.DATA == DATA_DIR, "the app was imported before tests.support set HAH_DATA_DIR"

web.Handler.log_message = lambda *args: None  # keep test output readable
_httpd = None


def reset_data():
    """Empty the data folder and re-seed it, as on a fresh persistent disk."""
    for name in os.listdir(DATA_DIR):
        path = os.path.join(DATA_DIR, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except FileNotFoundError:  # a -wal/-shm file SQLite removed as a connection closed
                pass
    app.prepare_data()
    ratelimit.reset()


def address():
    """(host, port) of the shared test server, starting it on first use."""
    global _httpd
    if _httpd is None:
        reset_data()
        _httpd = app.make_server(("127.0.0.1", 0))
        threading.Thread(target=_httpd.serve_forever, daemon=True).start()
    return _httpd.server_address[:2]


def data_path(name):
    return os.path.join(DATA_DIR, name)


class Response:
    def __init__(self, resp, body):
        self.status = resp.status
        self.headers = resp.msg
        self.body = body

    def header(self, name, default=None):
        return self.headers.get(name, default)

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body.decode("utf-8"))


class Client:
    """A browser-ish client: remembers cookies, sends Origin on POST."""

    def __init__(self):
        self.host, self.port = address()
        self.cookies = {}
        self.csrf = None

    @property
    def origin(self):
        return "http://%s:%d" % (self.host, self.port)

    def request(self, method, path, body=None, headers=None, origin=True):
        h = dict(headers or {})
        if self.cookies:
            h["Cookie"] = "; ".join("%s=%s" % kv for kv in self.cookies.items())
        if origin and method == "POST" and "Origin" not in h:
            h["Origin"] = self.origin
        if method == "POST" and self.csrf and "X-CSRF-Token" not in h:
            h["X-CSRF-Token"] = self.csrf
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=h)
            resp = conn.getresponse()
            data = resp.read()
        finally:
            conn.close()
        for raw in resp.msg.get_all("Set-Cookie") or []:
            name, _, rest = raw.partition("=")
            value = rest.split(";", 1)[0]
            if value and "max-age=0" not in raw.lower():
                self.cookies[name.strip()] = value
            else:
                self.cookies.pop(name.strip(), None)
        return Response(resp, data)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post_json(self, path, obj, **kw):
        headers = {"Content-Type": "application/json", **kw.pop("headers", {})}
        return self.request("POST", path, json.dumps(obj).encode(), headers=headers, **kw)

    def post_file(self, path, field, filename, payload, ctype="application/octet-stream"):
        boundary = uuid.uuid4().hex
        body = (("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                 "Content-Type: %s\r\n\r\n" % (boundary, field, filename, ctype)).encode()
                + payload + ("\r\n--%s--\r\n" % boundary).encode())
        return self.request("POST", path, body,
                            headers={"Content-Type": "multipart/form-data; boundary=" + boundary})

    def sign_in(self, email, password, totp_secret=None):
        """Staff sign-in: password, then the 2FA code if asked. Returns the
        last response; on success the client holds the session and CSRF token."""
        r = self.post_json("/api/staff/login", {"email": email, "password": password})
        if r.status == 200:
            self.csrf = r.json()["csrf"]
            if r.json().get("step") == "totp":
                r = self.post_json("/api/staff/totp/verify", {"code": next_code(totp_secret)})
                if r.status == 200:
                    self.csrf = r.json()["csrf"]
        return r


_codes_used = {}


def next_code(secret):
    """A valid TOTP code for SECRET that hasn't been used yet in this run
    (the server refuses to accept the same code twice)."""
    import time
    step = max(int(time.time() // 30) - 1, _codes_used.get(secret, -1) + 1)
    _codes_used[secret] = step
    return security.hotp(security._b32decode(secret), step)


_staff_ids = itertools.count(1)
STAFF_PASSWORD = "correct horse battery staple"


def make_staff(roles=("owner",), email=None, name=None, password=STAFF_PASSWORD, totp=True, status="active"):
    """Create a staff account directly in the database. Returns a dict with
    id, email, password and totp_secret."""
    n = next(_staff_ids)
    email = email or "staff%d@example.org" % n
    secret = security.new_totp_secret() if totp else None
    with db.tx() as c:
        cur = c.execute("INSERT INTO staff_users(email, name, password_hash, totp_secret, totp_enabled, status, created_at)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (email, name or "Staff %d" % n, security.hash_password(password), secret,
                         1 if totp else 0, status, db.now()))
        c.executemany("INSERT INTO staff_roles VALUES (?,?)", [(cur.lastrowid, r) for r in roles])
    return {"id": cur.lastrowid, "email": email, "password": password, "totp_secret": secret}


class ServerTestCase(unittest.TestCase):
    """Each test class starts from freshly seeded data."""

    @classmethod
    def setUpClass(cls):
        address()
        reset_data()

    def client(self):
        return Client()

    def admin(self, roles=("owner",)):
        """A client signed in (with 2FA) as a new staff member with ROLES."""
        u = make_staff(roles)
        c = Client()
        r = c.sign_in(u["email"], u["password"], u["totp_secret"])
        self.assertEqual(r.status, 200, r.text)
        c.staff = u
        return c
