"""HTTP plumbing: the request handler, response helpers and the route registry.

Features register their endpoints with the @route decorator; the handler
matches the method and path and calls fn(handler, **path_params). Anything a
GET route doesn't claim falls through to the static-file handler.

Every response carries the security headers below; HTML pages also get a
Content-Security-Policy with a per-request nonce (see csp())."""
import json
import re
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import auth, config

# ---------------------------------------------------------------- routes


class Route:
    __slots__ = ("method", "regex", "fn", "auth", "body_limit")

    def __init__(self, method, regex, fn, auth, body_limit):
        self.method, self.regex, self.fn, self.auth, self.body_limit = method, regex, fn, auth, body_limit


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


def route(method, pattern, *, auth=None, body_limit=None):
    """Register fn(handler, **params) for METHOD pattern.

    auth="admin" needs a staff login; body_limit caps the request body in
    bytes (default config.BODY_LIMIT)."""
    def register(fn):
        ROUTES.append(Route(method, compile_pattern(pattern), fn, auth,
                            body_limit or config.BODY_LIMIT))
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
        super().end_headers()

    def send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=None):
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

    def cookie_attrs(self):
        """Attributes every session cookie gets (Secure once we're on HTTPS)."""
        return "Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if self.is_https() else "")

    def is_admin(self):
        tok = self.cookie("hah_session")
        return bool(tok and tok in auth.sessions())

    def same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host") or ""
        return urllib.parse.urlparse(origin).netloc == host

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
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        path = urllib.parse.unquote(path)
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        r, params = match("GET", path)
        if r is None:
            return _get_fallback(self, path)
        if r.auth == "admin" and not self.is_admin():
            return self.json({"error": "unauthorised"}, 401)
        return r.fn(self, **params)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if not self.same_origin():
            return self.json({"error": "bad origin"}, 403)
        path = urllib.parse.urlparse(self.path).path
        try:
            r, params = match("POST", path)
            if r is None or r.auth == "admin":
                # unknown paths answer like protected ones until you're logged in
                if not self.is_admin():
                    return self.json({"error": "unauthorised"}, 401)
                if r is None:
                    return self.json({"error": "not found"}, 404)
            self.body_limit = r.body_limit
            return r.fn(self, **params)
        except ValueError as e:
            return self.json({"error": str(e)}, 400)
