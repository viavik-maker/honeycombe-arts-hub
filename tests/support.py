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
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import server as app  # noqa: E402

app.Handler.log_message = lambda *args: None  # keep test output readable
_httpd = None


def reset_data():
    """Empty the data folder and re-seed it, as on a fresh persistent disk."""
    for name in os.listdir(DATA_DIR):
        path = os.path.join(DATA_DIR, name)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    os.makedirs(app.UPLOADS, exist_ok=True)
    app.bootstrap_seed()
    app.init_auth()


def address():
    """(host, port) of the shared test server, starting it on first use."""
    global _httpd
    if _httpd is None:
        reset_data()
        _httpd = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
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

    @property
    def origin(self):
        return "http://%s:%d" % (self.host, self.port)

    def request(self, method, path, body=None, headers=None, origin=True):
        h = dict(headers or {})
        if self.cookies:
            h["Cookie"] = "; ".join("%s=%s" % kv for kv in self.cookies.items())
        if origin and method == "POST" and "Origin" not in h:
            h["Origin"] = self.origin
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

    def login_admin(self, password=ADMIN_PASSWORD):
        return self.post_json("/api/admin/login", {"password": password})


class ServerTestCase(unittest.TestCase):
    """Each test class starts from freshly seeded data."""

    @classmethod
    def setUpClass(cls):
        address()
        reset_data()

    def client(self):
        return Client()

    def admin(self):
        c = Client()
        r = c.login_admin()
        self.assertEqual(r.status, 200, r.text)
        return c
