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
# <!--#block name--> markers are filled from the editable page copy in content.json
BLOCK_RE = re.compile(r"<!--#block\s+([\w-]+)\s*-->")


SITE = "https://honeycombeartshub.org.uk"
SHARE_IMG = SITE + "/img/og-image.jpg"
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
DESC_RE = re.compile(r'<meta\s+name="description"\s+content="(.*?)"', re.S | re.I)


_seed_cache = {}


def seed_content():
    """The bundled defaults (seed/content.json), read once."""
    if not _seed_cache:
        try:
            with open(os.path.join(SEED, "content.json"), encoding="utf-8") as f:
                _seed_cache.update(json.load(f))
        except (OSError, ValueError):
            _seed_cache["pages"] = {}
    return _seed_cache


def with_page_defaults(c):
    """Fill in any page copy the stored content.json doesn't have yet.

    Sites that went live before the Contact / Get Involved pages became
    editable keep their content.json on a persistent disk, so it has no
    "pages" section — fall back to the bundled copy until staff save."""
    pages = c.get("pages")
    pages = dict(pages) if isinstance(pages, dict) else {}
    for key, default in (seed_content().get("pages") or {}).items():
        if not isinstance(pages.get(key), dict):
            pages[key] = default
    c["pages"] = pages
    return c


def site_content():
    """content.json as the site and the admin see it (page defaults filled in)."""
    return with_page_defaults(dict(load_json("content.json", {})))


def public_content():
    """content.json with sensitive settings (SMTP credentials) stripped, for public use."""
    c = site_content()
    s = dict(c.get("settings", {}))
    s.pop("smtp", None)
    c["settings"] = s
    return c


def _attr(s):
    return (s or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


_html = _attr  # same escaping; separate name for readability at call sites

BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")


def _safe_url(url):
    """Only let staff copy link to places a link can sensibly go."""
    u = (url or "").strip()
    return u if u.startswith(("https://", "http://", "mailto:", "tel:", "/", "#")) else ""


def _ext(url):
    return ' target="_blank" rel="noopener"' if url.startswith(("http://", "https://")) else ""


def _rich(text):
    """Staff copy -> HTML paragraphs.

    Everything is escaped first; only a tiny, safe subset is then allowed:
    a blank line starts a new paragraph, **bold**, and [label](link)."""
    out = []
    for para in re.split(r"\n\s*\n", (text or "").strip()):
        if not para.strip():
            continue
        body = _html(para.strip()).replace("\n", "<br>")
        body = BOLD_RE.sub(lambda m: "<strong>%s</strong>" % m.group(1), body)

        def link(m):
            url = _safe_url(m.group(2))
            # a link we can't allow is left exactly as typed, so staff can see why
            return '<a href="%s"%s>%s</a>' % (url, _ext(url), m.group(1)) if url else m.group(0)

        out.append("<p>%s</p>" % LINK_RE.sub(link, body))
    return "\n".join(out)


def _page(content, name):
    p = (content.get("pages") or {}).get(name)
    return p if isinstance(p, dict) else (seed_content().get("pages") or {}).get(name, {})


def _meta_block(name, fallback_title):
    """<title> + description for a page whose copy staff can edit."""
    def build(content):
        p = _page(content, name)
        return '<title>%s</title>\n  <meta name="description" content="%s">' % (
            _html(p.get("metaTitle") or fallback_title), _attr(p.get("metaDescription") or ""))
    return build


def _hero(eyebrow, heading, intro, intro_style=""):
    style = ' style="%s"' % intro_style if intro_style else ""
    return "\n        ".join(bit for bit in (
        '<span class="eyebrow">%s</span>' % _html(eyebrow) if eyebrow else "",
        "<h1>%s</h1>" % _html(heading) if heading else "",
        "<p%s>%s</p>" % (style, _html(intro)) if intro else "",
    ) if bit)


# ---------------- contact page

def _contact_link(card, settings):
    """The email / phone / address a contact card points at — taken from
    Settings so the cards can never drift out of step with the real details."""
    kind = card.get("link") or "none"
    if kind in ("emailGeneral", "emailTrustees", "emailSupport"):
        v = settings.get(kind, "")
        return '<a href="mailto:%s">%s</a>' % (_attr(v), _html(v)) if v else ""
    if kind == "phone":
        v = settings.get("phone", "")
        return '<a href="tel:%s">%s</a>' % (_attr(v.replace(" ", "")), _html(v)) if v else ""
    if kind == "address":
        return _html(settings.get("address", ""))
    if kind == "custom":
        url = _safe_url(card.get("linkUrl"))
        if url:
            return '<a href="%s"%s>%s</a>' % (_attr(url), _ext(url), _html(card.get("linkLabel") or url))
    return ""


def _b_contact_hero(content):
    p = _page(content, "contact")
    return _hero(p.get("eyebrow"), p.get("heading"), p.get("intro"),
                 "max-width:36em; color:var(--muted); font-size:1.12rem")


def _b_contact_cards(content):
    settings = content.get("settings", {})
    cards = []
    for card in _page(content, "contact").get("cards", []):
        body = "<br>".join(bit for bit in (_html(card.get("text", "")),
                                           _contact_link(card, settings)) if bit)
        cards.append("""<div class="contact-card reveal">
                <span class="ico">%s</span>
                <div>
                  <h3>%s</h3>
                  <p>%s</p>
                </div>
              </div>""" % (_html(card.get("icon", "")), _html(card.get("title", "")), body))
    return "\n              ".join(cards)


def _b_contact_opening(content):
    p = _page(content, "contact")
    heading = p.get("openingHeading") or ""
    rows = "".join(
        '<div class="event-side__row"><span>%s</span><strong>%s</strong></div>'
        % (_html(o.get("label", "")), _html(o.get("value", "")))
        for o in content.get("settings", {}).get("openingTimes", []))
    if not heading and not rows:
        return ""
    return """<div class="event-side__card reveal" style="margin-top:1.1rem">
              <div class="event-side__body">
                %s
                <div id="openingTimes">%s</div>
              </div>
            </div>""" % ('<h3 style="margin-bottom:.6em">%s</h3>' % _html(heading) if heading else "", rows)


def _b_contact_form_head(content):
    p = _page(content, "contact")
    return "\n              ".join(bit for bit in (
        '<h2 style="font-size:1.6rem">%s</h2>' % _html(p.get("formHeading")) if p.get("formHeading") else "",
        '<p class="form-note">%s</p>' % _html(p.get("formNote")) if p.get("formNote") else "",
    ) if bit)


def _b_contact_map(content):
    p = _page(content, "contact")
    settings = content.get("settings", {})
    address = settings.get("address") or ""
    if not p.get("showMap", True) or not address:
        return ""
    return """<div class="map-frame reveal">
          <iframe title="Map showing where to find %s" loading="lazy" referrerpolicy="no-referrer-when-downgrade"
                  src="https://www.google.com/maps?q=%s&amp;output=embed"></iframe>
        </div>""" % (_html(settings.get("siteName") or "us"),
                     urllib.parse.quote(address, safe=""))


# ---------------- get involved page

SETTING_LINKS = ("bookingUrl", "donateUrl", "volunteerUrl", "seesawUrl",
                 "facebook", "instagram", "twitter", "youtube")


def _section_href(section, settings):
    kind = section.get("buttonLink") or ""
    if kind == "custom":
        return _safe_url(section.get("buttonUrl"))
    if kind == "contact":
        return "/contact"
    if kind in SETTING_LINKS:
        return _safe_url(settings.get(kind))
    return ""


def _b_get_involved_hero(content):
    p = _page(content, "getInvolved")
    img = _safe_url(p.get("heroImage"))
    style = ' style="--ph-img:url(\'%s\')"' % _attr(img) if img else ""
    return """<section class="page-hero%s"%s>
      <div class="container">
        %s
      </div>
    </section>""" % (" page-hero--img" if img else "", style,
                     _hero(p.get("eyebrow"), p.get("heading"), p.get("intro")))


def _b_get_involved_sections(content):
    settings = content.get("settings", {})
    out = []
    for sec in _page(content, "getInvolved").get("sections", []):
        anchor = re.sub(r"[^a-z0-9-]+", "-", (sec.get("id") or "").lower()).strip("-")
        img = _safe_url(sec.get("image"))
        href = _section_href(sec, settings)
        label = sec.get("buttonLabel") or ""
        style = sec.get("buttonStyle") or "orange"
        if style not in ("orange", "honey", "navy", "ghost"):
            style = "orange"
        body = "\n            ".join(bit for bit in (
            '<span class="eyebrow">%s</span>' % _html(sec.get("eyebrow")) if sec.get("eyebrow") else "",
            "<h2>%s</h2>" % _html(sec.get("heading")) if sec.get("heading") else "",
            _rich(sec.get("body")),
            '<a class="btn btn--%s" href="%s"%s>%s</a>' % (style, _attr(href), _ext(href), _html(label))
            if href and label else "",
        ) if bit)
        out.append("""<div class="feature-row reveal"%s>
          <div class="feature-row__media"><img src="%s" alt="%s"></div>
          <div>
            %s
          </div>
        </div>""" % (' id="%s"' % anchor if anchor else "", _attr(img),
                     _attr(sec.get("imageAlt", "")), body))
    return "\n\n        ".join(out)


BLOCKS = {
    "contact-meta": _meta_block("contact", "Contact Us — Honeycombe Arts Hub"),
    "contact-hero": _b_contact_hero,
    "contact-cards": _b_contact_cards,
    "contact-opening": _b_contact_opening,
    "contact-form-head": _b_contact_form_head,
    "contact-map": _b_contact_map,
    "get-involved-meta": _meta_block("getInvolved", "Get Involved — Honeycombe Arts Hub"),
    "get-involved-hero": _b_get_involved_hero,
    "get-involved-sections": _b_get_involved_sections,
}


def _org_jsonld(s):
    data = {
        "@context": "https://schema.org", "@type": ["NGO", "LocalBusiness"],
        "name": "Honeycombe Arts Hub", "url": SITE,
        "logo": SITE + "/img/logo.png", "image": SHARE_IMG,
        "description": "A youth-focused community arts centre in Boscombe, Bournemouth — art, drama, music, film and friendship for children and young people aged 5–25.",
        "email": s.get("emailGeneral", "info@honeycombeartshub.org.uk"),
        "telephone": "+447932772905",
        "address": {"@type": "PostalAddress",
                    "streetAddress": "Units 4 & 5, The Sovereign Shopping Centre, 600 Christchurch Road",
                    "addressLocality": "Boscombe", "addressRegion": "Bournemouth",
                    "postalCode": "BH1 4SX", "addressCountry": "GB"},
        "sameAs": [s[k] for k in ("facebook", "instagram", "twitter", "youtube") if s.get(k)],
        "identifier": {"@type": "PropertyValue", "propertyID": "UK Registered Charity Number",
                       "value": s.get("charityNumber", "1127371")},
    }
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _seo_head(html, canonical, settings):
    tm = TITLE_RE.search(html); title = tm.group(1).strip() if tm else "Honeycombe Arts Hub"
    dm = DESC_RE.search(html); desc = dm.group(1).strip() if dm else ""
    url = _attr(SITE + (canonical or "/"))
    t, d = _attr(title), _attr(desc)
    return "\n".join([
        f'<link rel="canonical" href="{url}">',
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="Honeycombe Arts Hub">',
        f'<meta property="og:title" content="{t}">',
        f'<meta property="og:description" content="{d}">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:image" content="{SHARE_IMG}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta property="og:image:alt" content="Honeycombe Arts Hub — supporting young and emerging creatives">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{t}">',
        f'<meta name="twitter:description" content="{d}">',
        f'<meta name="twitter:image" content="{SHARE_IMG}">',
        f'<script type="application/ld+json">{_org_jsonld(settings)}</script>',
    ])


def render_page(filename, canonical=None):
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
    content = public_content()
    if "<!--#block" in html:
        html = BLOCK_RE.sub(lambda m: BLOCKS.get(m.group(1), lambda _c: "")(content), html)
    if "</head>" in html:
        html = html.replace("</head>", _seo_head(html, canonical, content.get("settings", {})) + "\n</head>", 1)
    if "<!--#data-->" in html:
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
            return self._json(public_content())

        if path == "/api/admin/overview":
            if not self._is_admin():
                return self._json({"error": "unauthorised"}, 401)
            return self._json({
                "content": site_content(),
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
            return self._page("event.html", canonical=path)

        if path in PRETTY:
            return self._page(PRETTY[path], canonical=path)

        # static files
        return self._static(path)

    def _page(self, filename, canonical=None, status=200):
        try:
            body = render_page(filename, canonical)
        except OSError:
            return self._send(404, b"Not found", "text/plain")
        return self._send(status, body, "text/html; charset=utf-8",
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
                return self._page(safe + ".html", canonical="/" + safe)
            # genuine miss: serve the 404 page with a real 404 status (no soft-404)
            return self._page("404.html", status=404) if os.path.isfile(os.path.join(PUBLIC, "404.html")) \
                else self._send(404, b"Not found", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        cache = "public, max-age=86400" if safe.startswith(("img/", "uploads/", "docs/")) \
            else "no-cache"
        with open(full, "rb") as f:
            body = f.read()
        if full.endswith(".html"):
            return self._page(safe, canonical="/" + safe[:-5])
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
        if "pages" in d and not isinstance(d["pages"], dict):
            return self._json({"error": "invalid content: pages"}, 400)
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
