"""HTTP plumbing: the request handler, response helpers and the route registry.

Features register their endpoints with the @route decorator; the handler
matches the method and path and calls fn(handler, **path_params). Anything a
GET route doesn't claim falls through to the static-file handler.

Every response carries the security headers below; HTML pages also get a
Content-Security-Policy with a per-request nonce (see csp())."""
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import config
from .validate import Invalid

# ---------------------------------------------------------------- routes


class Route:
    __slots__ = ("method", "regex", "fn", "auth", "perm", "mfa", "csrf", "body_limit")

    def __init__(self, method, regex, fn, auth, perm, mfa, csrf, body_limit):
        self.method, self.regex, self.fn = method, regex, fn
        self.auth, self.perm, self.mfa, self.csrf, self.body_limit = auth, perm, mfa, csrf, body_limit


ROUTES = []
_PARAM_RE = re.compile(r"<(?:(path):)?(\w+)>")


def compile_pattern(pattern):
    """"/whats-on/<slug>" -> regex; <name> is one path segment, <path:name> is the rest."""
    out, pos = [], 0
    for m in _PARAM_RE.finditer(pattern):
        out.append(re.escape(pattern[pos:m.start()]))
        out.append("(?P<%s>%s)" % (m.group(2), ".+" if m.group(1) else "[^/]+"))
        pos = m.end()
    out.append(re.escape(pattern[pos:]))
    return re.compile("^%s$" % "".join(out))


def route(method, pattern, *, auth=None, perm=None, mfa=True, csrf=True, body_limit=None):
    """Register fn(handler, **params) for METHOD pattern.

    auth:  None (public), "staff" or "account" — who must be signed in.
    perm:  for staff routes, the permission needed (see permissions.py).
    mfa:   staff routes need two-factor done, except the 2FA steps themselves.
    csrf:  signed-in POSTs must carry the session's X-CSRF-Token and a
           same-site Origin header.
    body_limit: request-body cap in bytes (default config.BODY_LIMIT)."""
    if perm and auth != "staff":
        raise ValueError("perm= needs auth='staff'")

    def register(fn):
        ROUTES.append(Route(method, compile_pattern(pattern), fn, auth, perm, mfa, csrf,
                            body_limit or config.BODY_LIMIT))
        return fn
    return register


# auth kind -> fn(handler) returning the signed-in principal (a dict with at
# least "csrf"; staff also "perms" and "mfa_passed") or None
AUTHENTICATORS = {}


def authenticator(kind):
    def register(fn):
        AUTHENTICATORS[kind] = fn
        return fn
    return register


def match(method, path):
    for r in ROUTES:
        if r.method == method:
            m = r.regex.match(path)
            if m:
                return r, m.groupdict()
    return None, None


_get_fallback = None


def set_get_fallback(fn):
    """fn(handler, path) serves any GET no route claimed (static files, 404s)."""
    global _get_fallback
    _get_fallback = fn


# ---------------------------------------------------------------- helpers


def site_url(h=None):
    """Absolute base for links in emails: SITE_URL, else (local development)
    the address this request came to."""
    if config.SITE_URL:
        return config.SITE_URL
    if h is None:
        return "http://localhost:%s" % os.environ.get("PORT", "8000")
    return "%s://%s" % ("https" if h.is_https() else "http", h.headers.get("Host") or "localhost")

def staff_url(h=None):
    """Base for links staff open (invites, alerts): the staff address if there is one."""
    if config.STAFF_HOST:
        return "https://" + config.STAFF_HOST
    return site_url(h)


def is_staff_path(path):
    return path == "/admin" or path.startswith(("/admin/", "/api/staff/", "/api/admin/"))


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

# One-time secrets in these paths must never reach the logs.
_REDACT_RE = re.compile(r"^(/(?:unsubscribe|u|activate|reset-password|verify-email)/)[^/]+")


def redact_path(raw):
    """The path part of a request target, without the query string or secrets."""
    path = urllib.parse.urlsplit(raw or "").path
    return _REDACT_RE.sub(r"\1[redacted]", path)


def client_ip(forwarded_for, peer, hops):
    """The visitor's IP. With HOPS trusted proxies in front of us, it's the
    HOPS-th address from the right of X-Forwarded-For (anything further left
    could have been made up by the client)."""
    if hops and forwarded_for:
        parts = [p.strip() for p in forwarded_for.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return peer


def csp(nonce):
    return "; ".join([
        "default-src 'self'",
        "script-src 'self' 'nonce-%s'" % nonce,
        "style-src 'self' 'unsafe-inline'",  # style="" attributes in pages and the admin
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "frame-src https://www.google.com",  # the contact-page map, once asked for
        "form-action 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "report-uri /api/csp-report",
    ])


def csp_header():
    return "Content-Security-Policy" if config.CSP_MODE == "enforce" else "Content-Security-Policy-Report-Only"


class BodyError(ValueError):
    """The request body can't be accepted (too big, malformed length...). Answers 400."""


# at most MAX_CONCURRENT requests are handled at once; the rest get a 503
_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT)


# ---------------------------------------------------------------- handler


class Handler(BaseHTTPRequestHandler):
    server_version = "HoneycombeHub/1.0"
    protocol_version = "HTTP/1.1"  # keep-alive; every response sets Content-Length
    timeout = config.REQUEST_TIMEOUT

    _slot = False
    _body_read = False
    body_limit = config.BODY_LIMIT

    # ------------------------------------------------ connection handling
    def parse_request(self):
        # take a slot only once a request has actually arrived, so idle
        # keep-alive connections don't hold one
        if not super().parse_request():
            return False
        self._slot = _slots.acquire(timeout=5)
        if not self._slot:
            self.close_connection = True
            self.send_error(503, "Busy — please try again")
            return False
        return True

    def handle_one_request(self):
        self._slot = False
        self._body_read = False
        self.body_limit = config.BODY_LIMIT
        try:
            super().handle_one_request()
        finally:
            if self._slot:
                _slots.release()
                self._slot = False
            if not self._body_read and self._declared_length():
                # we answered without reading the body: don't let it be
                # mistaken for the next request on this connection
                self.close_connection = True

    def _declared_length(self):
        headers = getattr(self, "headers", None)
        return bool(headers and (headers.get("Content-Length", "0").strip() not in ("", "0")
                                 or headers.get("Transfer-Encoding")))

    # ------------------------------------------------ helpers
    def is_https(self):
        headers = getattr(self, "headers", None)
        return bool(headers) and headers.get("X-Forwarded-Proto", "").lower() == "https"

    def client_ip(self):
        return client_ip(self.headers.get("X-Forwarded-For"), self.client_address[0],
                         config.TRUSTED_PROXY_HOPS)

    def end_headers(self):
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        if self.is_https():
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        if config.NOINDEX:
            self.send_header("X-Robots-Tag", "noindex, nofollow")
        super().end_headers()

    def send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=None):
        """Send a response. While a route runs, the response is held back until
        the route returns (see _run): a route that answers from inside
        `with db.tx()` must not tell the browser "done" before the commit."""
        if getattr(self, "_holding", False):
            self._held = (code, body, ctype, headers)
            return
        self._write(code, body, ctype, headers)

    def _write(self, code, body, ctype, headers):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, obj, code=200, headers=None):
        headers = {"Cache-Control": "no-store", **(headers or {})}
        self.send(code, json.dumps(obj).encode(), "application/json; charset=utf-8", headers)

    def csv(self, filename, header, rows):
        """A spreadsheet download; every cell goes through csv_safe()."""
        import csv
        import io
        from .markup import csv_safe
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\r\n")
        w.writerow(header)
        for row in rows:
            w.writerow([csv_safe(v) for v in row])
        self.send(200, ("﻿" + out.getvalue()).encode(), "text/csv; charset=utf-8",
                  {"Content-Disposition": 'attachment; filename="%s"' % re.sub(r"[^\w.-]", "-", filename),
                   "Cache-Control": "no-store"})

    def query(self):
        """The query string as {name: first value}."""
        return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}

    def body(self):
        if self.headers.get("Transfer-Encoding"):
            raise BodyError("chunked request bodies are not supported")
        raw = (self.headers.get("Content-Length") or "0").strip()
        if not raw.isdigit():
            raise BodyError("invalid Content-Length")
        length = int(raw)
        if length > self.body_limit:
            raise BodyError("payload too large")
        data = self.rfile.read(length)
        self._body_read = True
        return data

    def json_body(self):
        raw = self.body()  # BodyError propagates -> 400
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def cookie(self, name):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def cookie_attrs(self, samesite="Lax"):
        """Attributes every session cookie gets (Secure once we're on HTTPS)."""
        return "Path=/; HttpOnly; SameSite=%s" % samesite + ("; Secure" if self.is_https() else "")

    def principal(self, kind):
        """Who is signed in as KIND ("staff"/"account") on this request, or None."""
        cache = self.__dict__.setdefault("_principals", {})
        if kind not in cache:
            fn = AUTHENTICATORS.get(kind)
            cache[kind] = fn(self) if fn else None
        return cache[kind]

    def staff(self):
        return self.principal("staff")

    def has_perm(self, perm):
        s = self.staff()
        return bool(s and s["mfa_passed"] and perm in s["perms"])

    def same_origin(self, required=False):
        origin = self.headers.get("Origin")
        if not origin:
            return not required
        host = self.headers.get("Host") or ""
        return urllib.parse.urlparse(origin).netloc == host

    def _allowed(self, r):
        """True if the request may go ahead. Otherwise the refusal has been
        sent and the route must NOT run."""
        if not r.auth:
            return True
        who = self.principal("account" if r.auth == "holder" else r.auth)
        if who is None:
            self.json({"error": "unauthorised"}, 401)
            return False
        if r.auth == "holder" and who.get("carer_id"):
            # an extra carer signed in to a family's account: only the account holder may do this
            self.json({"error": "Only %s, who holds this account, can do that." % who["first_name"],
                       "holder_only": True}, 403)
            return False
        if r.auth == "staff" and r.mfa and not who.get("mfa_passed"):
            self.json({"error": "two-factor check needed", "step": "totp"}, 401)
            return False
        if r.perm and r.perm not in who.get("perms", ()):
            self.json({"error": "You don't have permission to do that."}, 403)
            return False
        if self.command == "POST" and r.csrf:
            token = self.headers.get("X-CSRF-Token") or ""
            if not self.same_origin(required=True) or not hmac.compare_digest(token, who.get("csrf") or "-"):
                self.json({"error": "This page is out of date — please reload it and try again."}, 403)
                return False
        return True

    def new_nonce(self):
        return secrets.token_urlsafe(16)

    # ------------------------------------------------ logging
    def log_request(self, code="-", size="-"):
        if isinstance(code, int):  # HTTPStatus -> plain number
            code = int(code)
        self.log_message('"%s %s" %s', self.command, redact_path(self.path), code)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ------------------------------------------------ dispatch
    def _staff_host_gate(self, path):
        """With a separate staff address (config.STAFF_HOST), keep the admin there. True if the request was answered."""
        if not config.STAFF_HOST:
            return False
        on_staff = (self.headers.get("Host") or "").split(":")[0].lower() == config.STAFF_HOST
        if is_staff_path(path) and not on_staff:
            if self.command in ("GET", "HEAD") and not path.startswith("/api/"):
                query = urllib.parse.urlparse(self.path).query
                self.send(302, b"", "text/plain", {"Location": staff_url() + path + ("?" + query if query else "")})
            else:
                self.json({"error": "not found"}, 404)
            return True
        if on_staff and path == "/":
            self.send(302, b"", "text/plain", {"Location": "/admin"})
            return True
        return False

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        path = urllib.parse.unquote(path)
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        self._principals = {}
        if self._staff_host_gate(path):
            return
        r, params = match("GET", path)
        if r is None:
            return _get_fallback(self, path)
        if not self._allowed(r):
            return
        try:
            return self._run(r, params)
        except LookupError:
            return self.json({"error": "not found"}, 404)
        except Invalid as e:
            return self.json({"error": "Please check the highlighted boxes.", "errors": e.errors}, 422)
        except ValueError as e:
            return self.json({"error": str(e)}, 400)
        except Exception:
            return self._crashed()

    def _run(self, r, params):
        """Call the route, then send what it answered. If it raises (including
        a failed commit), its answer is dropped and the error is sent instead."""
        self._holding, self._held = True, None
        try:
            r.fn(self, **params)
        finally:
            self._holding = False
        held, self._held = self._held, None
        if held:
            self._write(*held)

    def _crashed(self):
        """An unexpected error: log it with a reference and tell the visitor."""
        import traceback
        ref = secrets.token_hex(4)
        sys.stderr.write("[error %s] %s %s\n%s" % (ref, self.command, redact_path(self.path), traceback.format_exc()))
        try:
            return self.json({"error": "Something went wrong on our side — please try again. (ref %s)" % ref}, 500)
        except OSError:
            return None

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if not self.same_origin():
            return self.json({"error": "bad origin"}, 403)
        path = urllib.parse.urlparse(self.path).path
        self._principals = {}
        if self._staff_host_gate(path):
            return
        try:
            r, params = match("POST", path)
            if r is None:
                # unknown paths answer like protected ones until you're signed in
                if self.staff() is None:
                    return self.json({"error": "unauthorised"}, 401)
                return self.json({"error": "not found"}, 404)
            if not self._allowed(r):
                return
            self.body_limit = r.body_limit
            return self._run(r, params)
        except Invalid as e:
            return self.json({"error": "Please check the highlighted boxes.", "errors": e.errors}, 422)
        except ValueError as e:
            return self.json({"error": str(e)}, 400)
        except LookupError:
            return self.json({"error": "not found"}, 404)
        except Exception:
            return self._crashed()
