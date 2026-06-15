#!/usr/bin/env python3
"""
Honeycombe Arts Hub — website + built-in CMS server.
Zero dependencies: runs anywhere with Python 3.8+.

    python3 server.py [port]

Public site:  http://localhost:8000
Staff admin:  http://localhost:8000/admin
"""
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import smtplib
import sys
import threading
import time
import urllib.parse
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
DATA = os.path.join(ROOT, "data")
UPLOADS = os.path.join(DATA, "uploads")  # all editable state lives under data/
SEED = os.path.join(ROOT, "seed")  # bundled defaults, copied into DATA on first boot
PARTIALS = os.path.join(ROOT, "partials")

# Initial admin password for the very first login. Set ADMIN_PASSWORD in the
# host environment (e.g. a Render secret) so it is never committed to the repo.
# Only used to seed auth.json on first run; change it in the CMS afterwards.
DEFAULT_PASSWORD = os.environ.get("ADMIN_PASSWORD", "honeycomb2026")
SESSION_TTL = 60 * 60 * 24 * 7  # 7 days
MAX_UPLOAD = 15 * 1024 * 1024

_lock = threading.Lock()

# ---------------------------------------------------------------- storage

def _path(name):
    return os.path.join(DATA, name)

def load_json(name, default):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default

def save_json(name, obj):
    with _lock:
        tmp = _path(name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _path(name))

# ---------------------------------------------------------------- auth

def _hash_password(password, salt, iterations=120_000):
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return base64.b64encode(dk).decode()

def init_auth():
    auth = load_json("auth.json", None)
    if not auth:
        salt = secrets.token_bytes(16)
        auth = {
            "salt": base64.b64encode(salt).decode(),
            "hash": _hash_password(DEFAULT_PASSWORD, salt),
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

def sessions():
    s = load_json("sessions.json", {})
    now = time.time()
    live = {k: v for k, v in s.items() if v > now}
    if len(live) != len(s):
        save_json("sessions.json", live)
    return live

def new_session():
    tok = secrets.token_urlsafe(32)
    s = sessions()
    s[tok] = time.time() + SESSION_TTL
    save_json("sessions.json", s)
    return tok

def drop_session(tok):
    s = sessions()
    if tok in s:
        del s[tok]
        save_json("sessions.json", s)

# ---------------------------------------------------------------- email (optional)

def try_send_email(settings, subject, body):
    smtp = settings.get("smtp") or {}
    host, user = smtp.get("host"), smtp.get("user")
    to = smtp.get("notifyTo") or settings.get("emailGeneral")
    if not host or not to:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = user or to
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP(host, int(smtp.get("port") or 587), timeout=8) as s:
            s.starttls()
            if user and smtp.get("password"):
                s.login(user, smtp["password"])
            s.send_message(msg)
        return True
    except Exception as e:  # email is best-effort; message is stored regardless
        print(f"[mail] send failed: {e}")
        return False

# ---------------------------------------------------------------- routes

PRETTY = {
    "/": "index.html",
    "/whats-on": "whats-on.html",
    "/past-events": "past-events.html",
    "/about": "about.html",
    "/gallery": "gallery.html",
    "/get-involved": "get-involved.html",
    "/holiday-club": "holiday-club.html",
    "/arts-award": "arts-award.html",
    "/emerging-artists": "emerging-artists.html",
    "/testimonials": "testimonials.html",
    "/contact": "contact.html",
    "/policies": "policies.html",
    "/privacy": "privacy.html",
    "/safeguarding": "safeguarding.html",
    "/admin": "admin/index.html",
}

INCLUDE_RE = re.compile(r"<!--#include\s+([\w.-]+)\s*-->")


def render_page(filename):
    path = os.path.join(PUBLIC, filename)
    with open(path, encoding="utf-8") as f:
        html = f.read()

    def inc(m):
        p = os.path.join(PARTIALS, m.group(1))
        try:
            with open(p, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    html = INCLUDE_RE.sub(inc, html)
    if "<!--#data-->" in html:
        content = load_json("content.json", {})
        payload = json.dumps(content, ensure_ascii=False).replace("</", "<\\/")
        html = html.replace("<!--#data-->", f"<script>window.HAH={payload}</script>")
    return html.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "HoneycombeHub/1.0"
    protocol_version = "HTTP/1.1"  # keep-alive; every response sets Content-Length

    # ------------------------------------------------ helpers
    def _send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200, headers=None):
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8", headers)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            raise ValueError("payload too large")
        return self.rfile.read(length)

    def _json_body(self):
        try:
            return json.loads(self._body().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _cookie(self, name):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _is_admin(self):
        tok = self._cookie("hah_session")
        return bool(tok and tok in sessions())

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host") or ""
        return urllib.parse.urlparse(origin).netloc == host

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ------------------------------------------------ GET
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        path = urllib.parse.unquote(path)
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        if path == "/api/content":
            return self._json(load_json("content.json", {}))

        if path == "/api/admin/overview":
            if not self._is_admin():
                return self._json({"error": "unauthorised"}, 401)
            return self._json({
                "content": load_json("content.json", {}),
                "messages": load_json("messages.json", []),
                "subscribers": load_json("subscribers.json", []),
            })

        if path == "/api/admin/subscribers.csv":
            if not self._is_admin():
                return self._json({"error": "unauthorised"}, 401)
            subs = load_json("subscribers.json", [])
            rows = ["email,name,date"] + [
                '"%s","%s","%s"' % (s.get("email", "").replace('"', '""'),
                                     s.get("name", "").replace('"', '""'),
                                     s.get("date", ""))
                for s in subs
            ]
            return self._send(200, "\n".join(rows).encode(), "text/csv; charset=utf-8",
                              {"Content-Disposition": "attachment; filename=newsletter-subscribers.csv"})

        # staff-uploaded images (stored under data/uploads)
        if path.startswith("/uploads/"):
            full = os.path.join(UPLOADS, os.path.basename(path))
            if os.path.isfile(full):
                ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
                with open(full, "rb") as f:
                    return self._send(200, f.read(), ctype,
                                      {"Cache-Control": "public, max-age=86400"})
            return self._send(404, b"Not found", "text/plain")

        # event detail pretty url
        if path.startswith("/whats-on/") and path.count("/") == 2:
            return self._page("event.html")

        if path in PRETTY:
            return self._page(PRETTY[path])

        # static files
        return self._static(path)

    def _page(self, filename):
        try:
            body = render_page(filename)
        except OSError:
            return self._send(404, b"Not found", "text/plain")
        return self._send(200, body, "text/html; charset=utf-8",
                          {"Cache-Control": "no-store"})

    def _static(self, path):
        safe = os.path.normpath(path).lstrip("/\\")
        full = os.path.join(PUBLIC, safe)
        if not os.path.abspath(full).startswith(os.path.abspath(PUBLIC)):
            return self._send(403, b"Forbidden", "text/plain")
        if os.path.isdir(full):
            return self._send(404, b"Not found", "text/plain")
        if not os.path.isfile(full):
            # html fallback: /foo -> foo.html
            alt = full + ".html"
            if os.path.isfile(alt):
                return self._page(safe + ".html")
            return self._page("404.html") if os.path.isfile(os.path.join(PUBLIC, "404.html")) \
                else self._send(404, b"Not found", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        cache = "public, max-age=86400" if safe.startswith(("img/", "uploads/", "docs/")) \
            else "no-cache"
        with open(full, "rb") as f:
            body = f.read()
        if full.endswith(".html"):
            return self._page(safe)
        return self._send(200, body, ctype, {"Cache-Control": cache})

    def do_HEAD(self):
        self.do_GET()

    # ------------------------------------------------ POST
    def do_POST(self):
        if not self._same_origin():
            return self._json({"error": "bad origin"}, 403)
        path = urllib.parse.urlparse(self.path).path

        try:
            if path == "/api/contact":
                return self._contact()
            if path == "/api/newsletter":
                return self._newsletter()
            if path == "/api/admin/login":
                return self._login()
            if path == "/api/admin/logout":
                tok = self._cookie("hah_session")
                if tok:
                    drop_session(tok)
                return self._json({"ok": True}, headers={
                    "Set-Cookie": "hah_session=; Path=/; Max-Age=0"})
            # all routes below require auth
            if not self._is_admin():
                return self._json({"error": "unauthorised"}, 401)
            if path == "/api/admin/content":
                return self._save_content()
            if path == "/api/admin/upload":
                return self._upload()
            if path == "/api/admin/password":
                return self._password()
            if path == "/api/admin/messages":
                return self._messages()
            if path == "/api/admin/subscribers":
                return self._subscribers()
            return self._json({"error": "not found"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)

    def _contact(self):
        d = self._json_body()
        if not d:
            return self._json({"error": "invalid body"}, 400)
        if d.get("website"):  # honeypot field — bots fill it, humans never see it
            return self._json({"ok": True})
        name = (d.get("name") or "").strip()[:200]
        email = (d.get("email") or "").strip()[:200]
        phone = (d.get("phone") or "").strip()[:50]
        message = (d.get("message") or "").strip()[:5000]
        if not name or not email or not message or "@" not in email:
            return self._json({"error": "Please fill in your name, email and message."}, 400)
        msgs = load_json("messages.json", [])
        msgs.insert(0, {
            "id": secrets.token_hex(8),
            "name": name, "email": email, "phone": phone, "message": message,
            "date": time.strftime("%Y-%m-%d %H:%M"),
            "read": False,
        })
        save_json("messages.json", msgs)
        settings = load_json("content.json", {}).get("settings", {})
        threading.Thread(target=try_send_email, args=(
            settings,
            f"New website message from {name}",
            f"From: {name} <{email}>  {phone}\n\n{message}",
        ), daemon=True).start()
        return self._json({"ok": True})

    def _newsletter(self):
        d = self._json_body()
        if not d:
            return self._json({"error": "invalid body"}, 400)
        if d.get("website"):
            return self._json({"ok": True})
        email = (d.get("email") or "").strip().lower()[:200]
        name = (d.get("name") or "").strip()[:200]
        if "@" not in email or "." not in email:
            return self._json({"error": "Please enter a valid email address."}, 400)
        subs = load_json("subscribers.json", [])
        if any(s.get("email") == email for s in subs):
            return self._json({"ok": True, "note": "already subscribed"})
        subs.insert(0, {"email": email, "name": name,
                        "date": time.strftime("%Y-%m-%d")})
        save_json("subscribers.json", subs)
        return self._json({"ok": True})

    def _login(self):
        d = self._json_body() or {}
        time.sleep(0.4)  # soft brute-force throttle
        if check_password(d.get("password") or ""):
            tok = new_session()
            return self._json({"ok": True}, headers={
                "Set-Cookie": ("hah_session=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d"
                               % (tok, SESSION_TTL))})
        return self._json({"error": "Incorrect password"}, 401)

    def _save_content(self):
        d = self._json_body()
        if not isinstance(d, dict) or "settings" not in d:
            return self._json({"error": "invalid content payload"}, 400)
        for key in ("events", "pastEvents", "gallery", "testimonials", "impact", "values"):
            if not isinstance(d.get(key), list):
                return self._json({"error": f"invalid content: {key}"}, 400)
        # keep a rolling backup before overwrite
        cur = load_json("content.json", None)
        if cur:
            save_json("content.backup.json", cur)
        save_json("content.json", d)
        return self._json({"ok": True})

    def _upload(self):
        ctype = self.headers.get("Content-Type") or ""
        m = re.search(r"boundary=([^;]+)", ctype)
        if "multipart/form-data" not in ctype or not m:
            return self._json({"error": "expected multipart upload"}, 400)
        boundary = m.group(1).strip('"').encode()
        body = self._body()
        parts = body.split(b"--" + boundary)
        for part in parts:
            if b"filename=" not in part:
                continue
            head, _, payload = part.partition(b"\r\n\r\n")
            # each part ends with CRLF before the next boundary marker;
            # the final part may carry the closing "--" of the terminator
            if payload.endswith(b"--"):
                payload = payload[:-2]
            if payload.endswith(b"\r\n"):
                payload = payload[:-2]
            fn = re.search(rb'filename="([^"]*)"', head)
            if not fn:
                continue
            name = os.path.basename(fn.group(1).decode("utf-8", "ignore"))
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf"):
                return self._json({"error": "File type not allowed (images or PDF only)."}, 400)
            stem = re.sub(r"[^a-z0-9-]+", "-", os.path.splitext(name)[0].lower()).strip("-") or "file"
            os.makedirs(UPLOADS, exist_ok=True)
            final = f"{stem}-{secrets.token_hex(4)}{ext}"
            with open(os.path.join(UPLOADS, final), "wb") as f:
                f.write(payload)
            return self._json({"ok": True, "url": f"/uploads/{final}"})
        return self._json({"error": "no file found in upload"}, 400)

    def _password(self):
        d = self._json_body() or {}
        if not check_password(d.get("current") or ""):
            return self._json({"error": "Current password is incorrect"}, 400)
        new = d.get("new") or ""
        if len(new) < 8:
            return self._json({"error": "New password must be at least 8 characters"}, 400)
        set_password(new)
        return self._json({"ok": True})

    def _messages(self):
        d = self._json_body() or {}
        msgs = load_json("messages.json", [])
        if d.get("action") == "read":
            for msg in msgs:
                if msg["id"] == d.get("id"):
                    msg["read"] = bool(d.get("read", True))
        elif d.get("action") == "delete":
            msgs = [msg for msg in msgs if msg["id"] != d.get("id")]
        save_json("messages.json", msgs)
        return self._json({"ok": True, "messages": msgs})

    def _subscribers(self):
        d = self._json_body() or {}
        subs = load_json("subscribers.json", [])
        if d.get("action") == "delete":
            subs = [s for s in subs if s.get("email") != d.get("email")]
        save_json("subscribers.json", subs)
        return self._json({"ok": True, "subscribers": subs})


def bootstrap_seed():
    """On a fresh persistent disk the committed data/ files are shadowed by the
    mount, so copy bundled defaults from seed/ into DATA when they're missing."""
    for name in ("content.json",):
        dest, src = _path(name), os.path.join(SEED, name)
        if not os.path.exists(dest) and os.path.exists(src):
            shutil.copy(src, dest)


def main():
    port = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PORT", 8000))
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(UPLOADS, exist_ok=True)
    bootstrap_seed()
    init_auth()
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"""
  Honeycombe Arts Hub
  ──────────────────
  Public site : http://localhost:{port}
  Staff admin : http://localhost:{port}/admin
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye!")


if __name__ == "__main__":
    main()
