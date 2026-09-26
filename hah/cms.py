"""The content API, the public forms (contact, newsletter) and the staff CMS."""
import os
import re
import secrets
import threading
import time

from . import auth, config
from .content import public_content, site_content
from .mail import try_send_email
from .storage import load_json, save_json
from .web import route

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
    name = (d.get("name") or "").strip()[:200]
    email = (d.get("email") or "").strip()[:200]
    phone = (d.get("phone") or "").strip()[:50]
    message = (d.get("message") or "").strip()[:5000]
    if not name or not email or not message or "@" not in email:
        return h.json({"error": "Please fill in your name, email and message."}, 400)
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
    return h.json({"ok": True})


@route("POST", "/api/newsletter")
def newsletter(h):
    d = h.json_body()
    if not d:
        return h.json({"error": "invalid body"}, 400)
    if d.get("website"):
        return h.json({"ok": True})
    email = (d.get("email") or "").strip().lower()[:200]
    name = (d.get("name") or "").strip()[:200]
    if "@" not in email or "." not in email:
        return h.json({"error": "Please enter a valid email address."}, 400)
    subs = load_json("subscribers.json", [])
    if any(s.get("email") == email for s in subs):
        return h.json({"ok": True, "note": "already subscribed"})
    subs.insert(0, {"email": email, "name": name,
                    "date": time.strftime("%Y-%m-%d")})
    save_json("subscribers.json", subs)
    return h.json({"ok": True})


# ---------------------------------------------------------------- staff login


@route("POST", "/api/admin/login")
def login(h):
    d = h.json_body() or {}
    time.sleep(0.4)  # soft brute-force throttle
    if auth.check_password(d.get("password") or ""):
        tok = auth.new_session()
        return h.json({"ok": True}, headers={
            "Set-Cookie": ("hah_session=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d"
                           % (tok, config.SESSION_TTL))})
    return h.json({"error": "Incorrect password"}, 401)


@route("POST", "/api/admin/logout")
def logout(h):
    tok = h.cookie("hah_session")
    if tok:
        auth.drop_session(tok)
    return h.json({"ok": True}, headers={"Set-Cookie": "hah_session=; Path=/; Max-Age=0"})


@route("POST", "/api/admin/password", auth="admin")
def change_password(h):
    d = h.json_body() or {}
    if not auth.check_password(d.get("current") or ""):
        return h.json({"error": "Current password is incorrect"}, 400)
    new = d.get("new") or ""
    if len(new) < 8:
        return h.json({"error": "New password must be at least 8 characters"}, 400)
    auth.set_password(new)
    return h.json({"ok": True})


# ---------------------------------------------------------------- staff CMS


@route("GET", "/api/admin/overview", auth="admin")
def overview(h):
    return h.json({
        "content": site_content(),
        "messages": load_json("messages.json", []),
        "subscribers": load_json("subscribers.json", []),
    })


@route("GET", "/api/admin/subscribers.csv", auth="admin")
def subscribers_csv(h):
    subs = load_json("subscribers.json", [])
    rows = ["email,name,date"] + [
        '"%s","%s","%s"' % (s.get("email", "").replace('"', '""'),
                             s.get("name", "").replace('"', '""'),
                             s.get("date", ""))
        for s in subs
    ]
    return h.send(200, "\n".join(rows).encode(), "text/csv; charset=utf-8",
                  {"Content-Disposition": "attachment; filename=newsletter-subscribers.csv"})


@route("POST", "/api/admin/content", auth="admin")
def save_content(h):
    d = h.json_body()
    if not isinstance(d, dict) or "settings" not in d:
        return h.json({"error": "invalid content payload"}, 400)
    for key in ("events", "pastEvents", "gallery", "testimonials", "impact", "values"):
        if not isinstance(d.get(key), list):
            return h.json({"error": f"invalid content: {key}"}, 400)
    if "pages" in d and not isinstance(d["pages"], dict):
        return h.json({"error": "invalid content: pages"}, 400)
    # keep a rolling backup before overwrite
    cur = load_json("content.json", None)
    if cur:
        save_json("content.backup.json", cur)
    save_json("content.json", d)
    return h.json({"ok": True})


@route("POST", "/api/admin/upload", auth="admin")
def upload(h):
    ctype = h.headers.get("Content-Type") or ""
    m = re.search(r"boundary=([^;]+)", ctype)
    if "multipart/form-data" not in ctype or not m:
        return h.json({"error": "expected multipart upload"}, 400)
    boundary = m.group(1).strip('"').encode()
    body = h.body()
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
            return h.json({"error": "File type not allowed (images or PDF only)."}, 400)
        stem = re.sub(r"[^a-z0-9-]+", "-", os.path.splitext(name)[0].lower()).strip("-") or "file"
        os.makedirs(config.UPLOADS, exist_ok=True)
        final = f"{stem}-{secrets.token_hex(4)}{ext}"
        with open(os.path.join(config.UPLOADS, final), "wb") as f:
            f.write(payload)
        return h.json({"ok": True, "url": f"/uploads/{final}"})
    return h.json({"error": "no file found in upload"}, 400)


@route("POST", "/api/admin/messages", auth="admin")
def messages(h):
    d = h.json_body() or {}
    msgs = load_json("messages.json", [])
    if d.get("action") == "read":
        for msg in msgs:
            if msg["id"] == d.get("id"):
                msg["read"] = bool(d.get("read", True))
    elif d.get("action") == "delete":
        msgs = [msg for msg in msgs if msg["id"] != d.get("id")]
    save_json("messages.json", msgs)
    return h.json({"ok": True, "messages": msgs})


@route("POST", "/api/admin/subscribers", auth="admin")
def subscribers(h):
    d = h.json_body() or {}
    subs = load_json("subscribers.json", [])
    if d.get("action") == "delete":
        subs = [s for s in subs if s.get("email") != d.get("email")]
    save_json("subscribers.json", subs)
    return h.json({"ok": True, "subscribers": subs})
