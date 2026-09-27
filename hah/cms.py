"""The content API, the public forms (contact, newsletter) and the staff CMS."""
import csv
import io
import json
import os
import re
import secrets
import sys
from email.parser import BytesParser
from email.policy import HTTP

from . import audit, config, db, images, intray, mail, outbox, ratelimit, validate
from .content import public_content, site_content
from .markup import csv_safe
from .storage import load_json, replace_json
from .web import route

TOO_MANY = "Too many attempts from your connection — please try again later."

# ---------------------------------------------------------------- public


@route("GET", "/api/content")
def content_api(h):
    return h.json(public_content())


@route("POST", "/api/contact")
def contact(h):
    d = h.json_body()
    if not d:
        return h.json({"error": "invalid body"}, 400)
    if d.get("website"):  # honeypot field — bots fill it, humans never see it
        return h.json({"ok": True})
    if not ratelimit.hit("public_form", h.client_ip()):
        return h.json({"error": TOO_MANY}, 429)
    name = (d.get("name") or "").strip()[:200]
    email = (d.get("email") or "").strip()[:200]
    phone = (d.get("phone") or "").strip()[:50]
    message = (d.get("message") or "").strip()[:5000]
    if not name or not email or not message or "@" not in email:
        return h.json({"error": "Please fill in your name, email and message."}, 400)
    with db.tx() as c:
        mid = c.execute("INSERT INTO contact_messages(ref, name, email, phone, message, created_at) VALUES (?,?,?,?,?,?)",
                        (secrets.token_hex(8), name, email, phone, message, db.now())).lastrowid
        intray.add(c, "contact_message", "Website message from %s" % name[:60], perm="site.content",
                   entity_type="contact_message", entity_id=mid)
        notify = mail.staff_notify_address()
        if notify:
            outbox.email(c, notify, "contact_notification", {"name": name, "email": email, "phone": phone or "—",
                                                             "message": message}, kind="staff",
                         headers={"Reply-To": email} if "@" in email else None)
    return h.json({"ok": True})


# ---------------------------------------------------------------- staff CMS


@route("GET", "/api/admin/overview", auth="staff", perm="site.content")
def overview(h):
    return h.json({
        "content": site_content(),
        "messages": inbox(),
        "subscribers": _subscribers(),
    })


def _subscribers():
    from . import marketing
    with db.read() as c:
        return marketing.subscribers(c)


@route("GET", "/api/admin/subscribers.csv", auth="staff", perm="site.content")
def subscribers_csv(h):
    with db.tx() as c:
        audit.record(c, h, "newsletter.exported")
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["email", "name", "date", "source"])
    for s in _subscribers():
        w.writerow([csv_safe(s[k]) for k in ("email", "name", "date", "source")])
    return h.send(200, out.getvalue().encode(), "text/csv; charset=utf-8",
                  {"Content-Disposition": "attachment; filename=newsletter-subscribers.csv",
                   "Cache-Control": "no-store"})


# Links the public site puts in href="" straight from the settings. Only web, email and phone links, or a page
# on this site, may be published: never javascript:, data: or other schemes.
LINK_SETTINGS = {"bookingUrl": "Booking link", "donateUrl": "Donate link", "volunteerUrl": "Volunteer link",
                 "seesawUrl": "Seesaw link", "facebook": "Facebook link", "twitter": "X (Twitter) link",
                 "instagram": "Instagram link", "youtube": "YouTube link"}
SAFE_SCHEMES = ("http", "https", "mailto", "tel")


def unsafe_link(value):
    """True if a settings link would run code or leave the web (javascript:, data:, vbscript:, …).
    Browsers ignore spaces, tabs and newlines inside a URL's scheme, so those are removed before checking."""
    if value in (None, ""):
        return False
    if not isinstance(value, str):
        return True
    squashed = re.sub(r"[\x00-\x20\x7f]", "", value)
    m = re.match(r"([A-Za-z][A-Za-z0-9+.-]*):", squashed)
    return bool(m) and m.group(1).lower() not in SAFE_SCHEMES


@route("POST", "/api/admin/content", auth="staff", perm="site.content", body_limit=4 * 1024 * 1024)
def save_content(h):
    d = h.json_body()
    if not isinstance(d, dict) or "settings" not in d:
        return h.json({"error": "invalid content payload"}, 400)
    for key in ("events", "pastEvents", "gallery", "testimonials", "impact", "values"):
        if not isinstance(d.get(key), list):
            return h.json({"error": f"invalid content: {key}"}, 400)
    if "pages" in d and not isinstance(d["pages"], dict):
        return h.json({"error": "invalid content: pages"}, 400)
    settings = d["settings"] if isinstance(d["settings"], dict) else {}
    for key in sorted(set(LINK_SETTINGS) | {k for k in settings if k.endswith("Url")}):
        if unsafe_link(settings.get(key)):
            return h.json({"error": "%s must be a web address starting https:// (or mailto:, tel:, or a page on this "
                                    "site like /contact)." % LINK_SETTINGS.get(key, key), "field": key}, 400)
    replace_json("content.json", d, backup="content.backup.json")  # keeps a rolling backup
    with db.tx() as c:
        audit.record(c, h, "site.published")
    return h.json({"ok": True})


@route("POST", "/api/admin/upload", auth="staff", perm="site.content", body_limit=config.MAX_UPLOAD)
def upload(h):
    """A photo for the public website. Only real JPEG/PNG/GIF/WebP images are
    accepted (checked from the file's contents, not its name), and location
    and camera details are stripped from JPEGs before they're stored."""
    ctype = h.headers.get("Content-Type") or ""
    if "multipart/form-data" not in ctype or "boundary=" not in ctype:
        return h.json({"error": "expected multipart upload"}, 400)
    body = h.body()
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + body)
    for part in msg.iter_parts() if msg.is_multipart() else ():
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        kind = images.sniff(payload)
        if not kind:
            return h.json({"error": "Only photos can be uploaded here (JPEG, PNG, GIF or WebP)."}, 400)
        if kind == "jpeg":
            try:
                payload = images.strip_jpeg_metadata(payload)
            except ValueError:
                return h.json({"error": "That photo couldn't be read — try saving it again as a JPEG."}, 400)
        name = os.path.basename(filename)
        ext = os.path.splitext(name)[1].lower()
        if not (kind == "jpeg" and ext in (".jpg", ".jpeg")):
            ext = images.TYPES[kind]
        stem = re.sub(r"[^a-z0-9-]+", "-", os.path.splitext(name)[0].lower()).strip("-") or "file"
        os.makedirs(config.UPLOADS, exist_ok=True)
        final = f"{stem}-{secrets.token_hex(4)}{ext}"
        with open(os.path.join(config.UPLOADS, final), "wb") as f:
            f.write(payload)
        with db.tx() as c:
            audit.record(c, h, "site.upload", details={"file": final})
        return h.json({"ok": True, "url": f"/uploads/{final}"})
    return h.json({"error": "no file found in upload"}, 400)


@route("POST", "/api/admin/messages", auth="staff", perm="site.content")
def messages(h):
    d = h.json_body() or {}
    with db.tx() as c:
        if d.get("action") == "read":
            c.execute("UPDATE contact_messages SET read_at=?, handled_by=? WHERE ref=?",
                      (db.now() if d.get("read", True) else None, h.staff()["id"], str(d.get("id"))))
        elif d.get("action") == "delete":
            if c.execute("DELETE FROM contact_messages WHERE ref=?", (str(d.get("id")),)).rowcount:
                audit.record(c, h, "inbox.message_deleted")
    return h.json({"ok": True, "messages": inbox()})


def inbox():
    """Contact-form messages, newest first, in the shape the admin Inbox uses."""
    with db.read() as c:
        rows = c.execute("SELECT * FROM contact_messages ORDER BY id DESC").fetchall()
    return [{"id": r["ref"], "name": r["name"], "email": r["email"], "phone": r["phone"] or "",
             "message": r["message"], "date": _uk_time(r["created_at"]), "read": r["read_at"] is not None}
            for r in rows]


def _uk_time(iso):
    import datetime
    from .worker import LOCAL
    t = datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    return t.astimezone(LOCAL).strftime("%Y-%m-%d %H:%M")


def migrate_messages_json():
    """One-off: move data/messages.json into the database (kept as
    messages.json.migrated). Safe to call on every start."""
    old = load_json("messages.json", None)
    if old is None:
        return 0
    import datetime
    from .worker import LOCAL
    with db.tx() as c:
        for m in reversed(old):
            try:
                local = datetime.datetime.strptime(m.get("date", ""), "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL)
                created = local.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                created = db.now()
            c.execute("INSERT OR IGNORE INTO contact_messages(ref, name, email, phone, message, created_at, read_at)"
                      " VALUES (?,?,?,?,?,?,?)",
                      (m.get("id") or secrets.token_hex(8), m.get("name", ""), m.get("email", ""), m.get("phone", ""),
                       m.get("message", ""), created, created if m.get("read") else None))
    os.replace(os.path.join(config.DATA, "messages.json"), os.path.join(config.DATA, "messages.json.migrated"))
    return len(old)


@route("POST", "/api/admin/subscribers", auth="staff", perm="site.content")
def subscribers(h):
    from . import marketing
    d = h.json_body() or {}
    if d.get("action") == "delete" and validate.email(d.get("email")):
        with db.tx() as c:
            marketing.unsubscribe(c, validate.email(d["email"]), "email", h)
            audit.record(c, h, "newsletter.subscriber_removed")
    return h.json({"ok": True, "subscribers": _subscribers()})


# ---------------------------------------------------------------- CSP reports


@route("POST", "/api/csp-report", body_limit=16 * 1024)
def csp_report(h):
    """Browsers report anything the Content-Security-Policy would block; we log
    one line per report so problems show up in Render's logs."""
    raw = h.body()
    if ratelimit.hit("csp_report", h.client_ip()):
        try:
            rep = (json.loads(raw.decode("utf-8")) or {}).get("csp-report") or {}
        except (ValueError, UnicodeDecodeError, AttributeError):
            rep = {}
        blocked = _printable(str(rep.get("blocked-uri") or "?").split("?")[0][:120])
        directive = _printable(str(rep.get("violated-directive") or rep.get("effective-directive") or "?")[:60])
        page = _printable(str(rep.get("document-uri") or "?").split("?")[0][:120])
        sys.stderr.write("[csp] %s blocked %s on %s\n" % (directive, blocked, page))
    return h.send(204)


def _printable(text):
    """Visitor-supplied text made safe for a one-line log entry."""
    return "".join(ch if ch.isprintable() else "?" for ch in text) or "?"


@route("GET", "/api/admin/request-info", auth="staff", perm="system.view")
def request_info(h):
    """Shows staff how the server sees their request, to confirm the visitor
    IP used for rate limits is right behind Render's proxies (it should be
    your own public IP, not an internal or proxy address)."""
    return h.json({
        "ip": h.client_ip(),
        "peer": h.client_address[0],
        "x_forwarded_for": h.headers.get("X-Forwarded-For"),
        "trusted_proxy_hops": config.TRUSTED_PROXY_HOPS,
        "https": h.is_https(),
    })
