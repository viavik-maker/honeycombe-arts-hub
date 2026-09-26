"""HTTP plumbing: the request handler, response helpers and the route registry.

Features register their endpoints with the @route decorator; the handler
matches the method and path and calls fn(handler, **path_params). Anything a
GET route doesn't claim falls through to the static-file handler."""
import json
import re
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import auth, config

# ---------------------------------------------------------------- routes


class Route:
    __slots__ = ("method", "regex", "fn", "auth")

    def __init__(self, method, regex, fn, auth):
        self.method, self.regex, self.fn, self.auth = method, regex, fn, auth


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


def route(method, pattern, *, auth=None):
    """Register fn(handler, **params) for METHOD pattern. auth="admin" needs a staff login."""
    def register(fn):
        ROUTES.append(Route(method, compile_pattern(pattern), fn, auth))
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


# ---------------------------------------------------------------- handler


class Handler(BaseHTTPRequestHandler):
    server_version = "HoneycombeHub/1.0"
    protocol_version = "HTTP/1.1"  # keep-alive; every response sets Content-Length

    # ------------------------------------------------ helpers
    def send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, obj, code=200, headers=None):
        self.send(code, json.dumps(obj).encode(), "application/json; charset=utf-8", headers)

    def body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > config.MAX_UPLOAD:
            raise ValueError("payload too large")
        return self.rfile.read(length)

    def json_body(self):
        try:
            return json.loads(self.body().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def cookie(self, name):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def is_admin(self):
        tok = self.cookie("hah_session")
        return bool(tok and tok in auth.sessions())

    def same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host") or ""
        return urllib.parse.urlparse(origin).netloc == host

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
            return r.fn(self, **params)
        except ValueError as e:
            return self.json({"error": str(e)}, 400)
