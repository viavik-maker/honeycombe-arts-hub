"""The content API, the public forms (contact, newsletter) and the staff CMS."""
import csv
import io
import json
import os
import re
import secrets
import sys
import threading
import time
from email.parser import BytesParser
from email.policy import HTTP

from . import audit, config, db, images, ratelimit
from .content import public_content, site_content
from .mail import try_send_email
from .markup import csv_safe
from .storage import load_json, replace_json, update_json
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
    entry = {
        "id": secrets.token_hex(8),
        "name": name, "email": email, "phone": phone, "message": message,
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "read": False,
    }
    update_json("messages.json", [], lambda msgs: [entry] + msgs)
    settings = load_json("content.json", {}).get("settings", {})
    threading.Thread(target=try_send_email, args=(
        settings,
        f"New website message from {name}",
        f"From: {name} <{email}>  {phone}\n\n{message}",
    ), daemon=True).start()
    return h.json({"ok": True})


@route("POST", "/api/newsletter")
def newsletter(h):
    d = h.json_body()
    if not d:
        return h.json({"error": "invalid body"}, 400)
    if d.get("website"):
        return h.json({"ok": True})
    if not ratelimit.hit("public_form", h.client_ip()):
        return h.json({"error": TOO_MANY}, 429)
    email = (d.get("email") or "").strip().lower()[:200]
    name = (d.get("name") or "").strip()[:200]
    if "@" not in email or "." not in email:
        return h.json({"error": "Please enter a valid email address."}, 400)
    already = []

    def add(subs):
        if any(s.get("email") == email for s in subs):
            already.append(True)
            return subs
        return [{"email": email, "name": name, "date": time.strftime("%Y-%m-%d")}] + subs

    update_json("subscribers.json", [], add)
    return h.json({"ok": True, "note": "already subscribed"} if already else {"ok": True})


# ---------------------------------------------------------------- staff CMS


@route("GET", "/api/admin/overview", auth="staff", perm="site.content")
def overview(h):
    return h.json({
        "content": site_content(),
        "messages": load_json("messages.json", []),
        "subscribers": load_json("subscribers.json", []),
    })


@route("GET", "/api/admin/subscribers.csv", auth="staff", perm="site.content")
def subscribers_csv(h):
    with db.tx() as c:
        audit.record(c, h, "newsletter.exported")
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["email", "name", "date"])
    for s in load_json("subscribers.json", []):
        w.writerow([csv_safe(s.get(k, "")) for k in ("email", "name", "date")])
    return h.send(200, out.getvalue().encode(), "text/csv; charset=utf-8",
                  {"Content-Disposition": "attachment; filename=newsletter-subscribers.csv",
                   "Cache-Control": "no-store"})


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

    def change(msgs):
        if d.get("action") == "read":
            for msg in msgs:
                if msg["id"] == d.get("id"):
                    msg["read"] = bool(d.get("read", True))
        elif d.get("action") == "delete":
            msgs = [msg for msg in msgs if msg["id"] != d.get("id")]
        return msgs

    msgs = update_json("messages.json", [], change)
    if d.get("action") == "delete":
        with db.tx() as c:
            audit.record(c, h, "inbox.message_deleted")
    return h.json({"ok": True, "messages": msgs})


@route("POST", "/api/admin/subscribers", auth="staff", perm="site.content")
def subscribers(h):
    d = h.json_body() or {}

    def change(subs):
        if d.get("action") == "delete":
            subs = [s for s in subs if s.get("email") != d.get("email")]
        return subs

    subs = update_json("subscribers.json", [], change)
    if d.get("action") == "delete":
        with db.tx() as c:
            audit.record(c, h, "newsletter.subscriber_removed")
    return h.json({"ok": True, "subscribers": subs})


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
