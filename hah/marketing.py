"""Marketing preferences (PECR): who has asked for news by email or text.

Kept apart from service messages (booking confirmations, reminders), which
families can't switch off. Every opt-in records the wording it was given
under and where it came from. Unsubscribing is one click from any marketing
email (RFC 8058 List-Unsubscribe-Post), and a browser GET never unsubscribes
on its own, so link scanners can't do it by accident.

The website newsletter used to live in data/subscribers.json; those people
are moved here as already consented (source 'legacy_newsletter', keeping
their sign-up date) — no re-permission email."""
import base64
import hmac
import os

from . import audit, config, db, ratelimit, security, validate
from .web import route, site_url

WORDING_VERSION = "2026-1"  # "Keep me updated about future events and activities"
LEGACY = "subscribers.json"


def opt_in(c, email, *, source, name=None, account_id=None, guest_contact_id=None, sms=False, phone=None, at=None):
    """Record an opt-in (idempotent). Returns the row id."""
    now = db.now()
    at = at or now
    row = c.execute("SELECT * FROM marketing_preferences WHERE email=?", (email,)).fetchone()
    if row:
        c.execute("UPDATE marketing_preferences SET email_opt_in=1, email_opt_in_at=COALESCE(email_opt_in_at, ?),"
                  " unsubscribed_email_at=NULL, sms_opt_in=CASE WHEN ? THEN 1 ELSE sms_opt_in END,"
                  " sms_opt_in_at=CASE WHEN ? THEN ? ELSE sms_opt_in_at END, phone=COALESCE(?, phone),"
                  " account_id=COALESCE(account_id, ?), guest_contact_id=COALESCE(guest_contact_id, ?),"
                  " name=COALESCE(name, ?), wording_version=?, updated_at=? WHERE id=?",
                  (at, 1 if sms else 0, 1 if sms else 0, now, phone, account_id, guest_contact_id, name,
                   WORDING_VERSION, now, row["id"]))
        return row["id"]
    return c.execute("INSERT INTO marketing_preferences(email, phone, name, account_id, guest_contact_id, email_opt_in,"
                     " email_opt_in_at, sms_opt_in, sms_opt_in_at, wording_version, source, created_at, updated_at)"
                     " VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?)",
                     (email, phone, name, account_id, guest_contact_id, at, 1 if sms else 0, now if sms else None,
                      WORDING_VERSION if source != "legacy_newsletter" else "legacy", source, now, now)).lastrowid


def unsubscribe(c, email, channel="email", h=None):
    col = "unsubscribed_email_at" if channel == "email" else "unsubscribed_sms_at"
    opt = "email_opt_in" if channel == "email" else "sms_opt_in"
    n = c.execute("UPDATE marketing_preferences SET %s=0, %s=?, updated_at=? WHERE email=? AND %s=1" % (opt, col, opt),
                  (db.now(), db.now(), email)).rowcount
    if n:
        audit.record(c, h, "marketing.unsubscribe", details={"channel": channel})
    return n


# ---------------------------------------------------------------- unsubscribe links


def token_for(email):
    e = base64.urlsafe_b64encode(email.encode()).decode().rstrip("=")
    return "%s.%s" % (e, security.signed("unsubscribe:" + email))


def email_from_token(token):
    try:
        e, sig = (token or "").rsplit(".", 1)
        email = base64.urlsafe_b64decode(e + "=" * (-len(e) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    return email if hmac.compare_digest(sig, security.signed("unsubscribe:" + email)) else None


def unsubscribe_url(email, h=None):
    return "%s/unsubscribe/%s" % (site_url(h), token_for(email))


def headers_for(email, h=None):
    """RFC 8058 one-click unsubscribe headers for a marketing email."""
    return {"List-Unsubscribe": "<%s>" % unsubscribe_url(email, h), "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


@route("GET", "/unsubscribe/<token>")
def unsubscribe_page(h, token):
    from .portal_pages import shell
    return shell(h, "unsubscribe", "Unsubscribe")


@route("POST", "/unsubscribe/<token>", csrf=False)
def one_click(h, token):
    """Mail programs' one-click unsubscribe (and the page's button)."""
    email = email_from_token(token)
    if not email:
        return h.json({"error": "This unsubscribe link isn't valid."}, 400)
    with db.tx() as c:
        unsubscribe(c, email, "email", h)
    return h.json({"ok": True, "message": "You've been unsubscribed from our news emails."})


# ---------------------------------------------------------------- the website newsletter form


@route("POST", "/api/newsletter")
def newsletter(h):
    d = h.json_body()
    if not d:
        return h.json({"error": "invalid body"}, 400)
    if d.get("website"):
        return h.json({"ok": True})
    if not ratelimit.hit("public_form", h.client_ip()):
        return h.json({"error": "Too many requests — please try again later."}, 429)
    email = validate.email(d.get("email"))
    if not email:
        return h.json({"error": "Please enter a valid email address."}, 400)
    name = validate.text(d.get("name"), 200) or None
    with db.tx() as c:
        row = c.execute("SELECT email_opt_in FROM marketing_preferences WHERE email=?", (email,)).fetchone()
        if row and row["email_opt_in"]:
            return h.json({"ok": True, "note": "already subscribed"})
        opt_in(c, email, source="newsletter_form", name=name)
    return h.json({"ok": True})


def subscribers(c):
    return [{"email": r["email"], "name": r["name"] or "", "date": (r["email_opt_in_at"] or r["created_at"])[:10],
             "source": r["source"]}
            for r in c.execute("SELECT * FROM marketing_preferences WHERE email_opt_in=1 ORDER BY email_opt_in_at DESC")]


def migrate_subscribers_json():
    """One-off at start-up: data/subscribers.json → marketing_preferences."""
    from .storage import load_json
    path = os.path.join(config.DATA, LEGACY)
    if not os.path.exists(path):
        return 0
    old = load_json(LEGACY, [])
    n = 0
    with db.tx() as c:
        for s in old if isinstance(old, list) else []:
            email = validate.email(s.get("email"))
            if not email:
                continue
            date = validate.date(s.get("date"))
            at = (date.isoformat() + "T12:00:00Z") if date else db.now()
            opt_in(c, email, source="legacy_newsletter", name=validate.text(s.get("name"), 200) or None, at=at)
            n += 1
    os.replace(path, path + ".migrated")
    return n


# ---------------------------------------------------------------- a family's own preferences


@route("GET", "/api/account/preferences", auth="account")
def my_preferences(h):
    who = h.principal("account")
    with db.read() as c:
        r = c.execute("SELECT * FROM marketing_preferences WHERE email=? OR account_id=?", (who["email"], who["id"])).fetchone()
    return h.json({"email_news": bool(r and r["email_opt_in"]), "sms_news": bool(r and r["sms_opt_in"]),
                   "has_mobile": bool(who["mobile"])})


@route("POST", "/api/account/preferences", auth="account")
def set_preferences(h):
    who = h.principal("account")
    d = h.json_body() or {}
    with db.tx() as c:
        email_news, sms_news = bool(d.get("email_news")), bool(d.get("sms_news")) and bool(who["mobile"])
        if email_news or sms_news:
            opt_in(c, who["email"], source="account_settings", name="%s %s" % (who["first_name"], who["last_name"]),
                   account_id=who["id"], sms=sms_news, phone=who["mobile"])
        if not email_news:
            unsubscribe(c, who["email"], "email", h)
        if not sms_news:
            unsubscribe(c, who["email"], "sms", h)
        audit.record(c, h, "marketing.preferences", account_id=who["id"],
                     details={"email": email_news, "sms": sms_news})
    return h.json({"ok": True})
