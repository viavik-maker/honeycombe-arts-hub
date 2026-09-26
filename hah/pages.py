"""Public pages, event pages, staff uploads and static files."""
import mimetypes
import os

from . import config
from .site import PRETTY, render_page
from .web import csp, csp_header, route, set_get_fallback


def page(h, filename, canonical=None, status=200, private=False, subs=None):
    """Render a page. PRIVATE pages (sign-in, account, booking) aren't
    indexed by search engines and carry no sharing/SEO tags."""
    nonce = h.new_nonce()
    try:
        body = render_page(filename, canonical, nonce=nonce, seo=not private, subs=subs)
    except OSError:
        return h.send(404, b"Not found", "text/plain")
    headers = {"Cache-Control": "no-store", csp_header(): csp(nonce)}
    if private:
        headers["X-Robots-Tag"] = "noindex, nofollow"
    return h.send(status, body, "text/html; charset=utf-8", headers)


def static(h, path):
    safe = os.path.normpath(path).lstrip("/\\")
    full = os.path.join(config.PUBLIC, safe)
    if not os.path.abspath(full).startswith(os.path.abspath(config.PUBLIC)):
        return h.send(403, b"Forbidden", "text/plain")
    if os.path.isdir(full):
        return h.send(404, b"Not found", "text/plain")
    if not os.path.isfile(full):
        # html fallback: /foo -> foo.html
        alt = full + ".html"
        if os.path.isfile(alt):
            return page(h, safe + ".html", canonical="/" + safe)
        # genuine miss: serve the 404 page with a real 404 status (no soft-404)
        return page(h, "404.html", status=404) if os.path.isfile(os.path.join(config.PUBLIC, "404.html")) \
            else h.send(404, b"Not found", "text/plain")
    ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
    cache = "public, max-age=86400" if safe.startswith(("img/", "uploads/", "docs/")) \
        else "no-cache"
    with open(full, "rb") as f:
        body = f.read()
    if full.endswith(".html"):
        return page(h, safe, canonical="/" + safe[:-5])
    return h.send(200, body, ctype, {"Cache-Control": cache})


set_get_fallback(static)


def _pretty(path, filename):
    route("GET", path)(lambda h: page(h, filename, canonical=path))


for _path, _file in PRETTY.items():
    _pretty(_path, _file)


@route("GET", "/whats-on/<slug>")
def event_page(h, slug):
    return page(h, "event.html", canonical="/whats-on/" + slug)


@route("GET", "/uploads/<path:name>")
def upload_file(h, name):
    """Staff-uploaded images (stored under data/uploads)."""
    full = os.path.join(config.UPLOADS, os.path.basename(name))
    if os.path.isfile(full):
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        headers = {"Cache-Control": "public, max-age=86400"}
        if full.lower().endswith(".svg"):
            # older uploads may be SVG, which can carry script: never run it
            headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        with open(full, "rb") as f:
            return h.send(200, f.read(), ctype, headers)
    return h.send(404, b"Not found", "text/plain")
