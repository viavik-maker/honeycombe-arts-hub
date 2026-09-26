"""Admin → Messages: email or text many families at once, and the archive
of everything the site has sent.

Two kinds, kept apart as PECR requires:
  * service messages — about something a family has booked (a change of
    room, a reminder, a cancelled session). They go to everyone booked,
    whatever their marketing choices.
  * news (marketing) — only to people who opted in and haven't
    unsubscribed. Every news email carries a one-click unsubscribe.
{{first_name}} in the subject or message is replaced for each person."""
import json

from . import audit, catalogue, db, marketing, outbox, templating, validate
from .markup import html as esc
from .markup import rich
from .validate import Invalid
from .web import route

MAX_SMS = 480  # three text messages' worth
LIVE = "('confirmed','pending_approval','pending_payment','offered','pending_confirmation')"


def _booked(c, where, args):
    """Accounts and guests with live bookings matching WHERE (on bookings b / sessions s)."""
    out = []
    for r in c.execute("SELECT DISTINCT a.id, a.email, a.mobile, a.first_name FROM bookings b JOIN activity_sessions s"
                       " ON s.id=b.session_id JOIN accounts a ON a.id=b.account_id WHERE b.status IN " + LIVE +
                       " AND a.status NOT IN ('closed','anonymised') AND " + where, args):
        out.append({"account_id": r["id"], "email": r["email"], "mobile": r["mobile"], "first_name": r["first_name"]})
    for r in c.execute("SELECT DISTINCT g.id, g.email, g.phone, g.name FROM bookings b JOIN activity_sessions s"
                       " ON s.id=b.session_id JOIN guest_contacts g ON g.id=b.guest_contact_id WHERE b.status IN " + LIVE +
                       " AND b.account_id IS NULL AND g.anonymised_at IS NULL AND " + where, args):
        out.append({"account_id": None, "email": r["email"], "mobile": r["phone"], "first_name": r["name"]})
    return out


def recipients(c, kind, channel, audience):
    """[(key, {email, mobile, first_name, account_id})], one per person, and a label."""
    t = audience.get("type")
    today = catalogue.uk_today().isoformat()
    if kind == "marketing":  # news only ever goes to people who opted in
        col = "email_opt_in" if channel == "email" else "sms_opt_in"
        rows = [{"account_id": r["account_id"], "email": r["email"], "mobile": r["phone"],
                 "first_name": (r["name"] or "").split(" ")[0] or None}
                for r in c.execute("SELECT * FROM marketing_preferences WHERE %s=1" % col)]
        label = "Everyone who opted in to news by %s" % channel
    elif t == "session":
        s = c.execute("SELECT s.*, a.title FROM activity_sessions s JOIN activities a ON a.id=s.activity_id WHERE s.id=?",
                      (int(audience.get("session_id") or 0),)).fetchone()
        if not s:
            raise Invalid({"audience": "Choose a session."})
        rows = _booked(c, "s.id=?", (s["id"],))
        label = "Booked on %s, %s %s" % (s["title"], catalogue.nice_date(s["date"]), s["start_time"])
    elif t == "activity":
        a = c.execute("SELECT * FROM activities WHERE id=?", (int(audience.get("activity_id") or 0),)).fetchone()
        if not a:
            raise Invalid({"audience": "Choose an activity."})
        rows = _booked(c, "s.activity_id=? AND s.date>=?", (a["id"], today))
        label = "Booked on %s (upcoming sessions)" % a["title"]
    elif t == "date":
        day = validate.date(audience.get("date"))
        if not day:
            raise Invalid({"audience": "Choose a date."})
        rows = _booked(c, "s.date=?", (day.isoformat(),))
        label = "Everyone booked on %s" % catalogue.nice_date(day.isoformat())
    elif t == "accounts":
        refs = [r for r in audience.get("refs") or [] if isinstance(r, str)][:500]
        rows = [{"account_id": r["id"], "email": r["email"], "mobile": r["mobile"], "first_name": r["first_name"]}
                for r in c.execute("SELECT * FROM accounts WHERE ref IN (%s) AND status NOT IN ('closed','anonymised')"
                                   % ",".join("?" * len(refs)), refs)] if refs else []
        label = "%d chosen famil%s" % (len(rows), "y" if len(rows) == 1 else "ies")
    else:
        raise Invalid({"audience": "Choose who it's for."})
    seen, out = set(), []
    for r in rows:
        key = (r["email"] or "").lower() if channel == "email" else validate.uk_mobile(r["mobile"] or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((key, r))
    return out, label


def _check(h, d):
    kind = d.get("kind") if d.get("kind") in ("service", "marketing") else None
    channel = d.get("channel") if d.get("channel") in ("email", "sms") else None
    if not kind or not channel:
        raise Invalid({"kind": "Choose what kind of message this is."})
    if kind == "marketing" and not h.has_perm("messaging.marketing"):
        raise ValueError("You don't have permission to send news.")
    body = validate.long_text(d.get("body"), 10000)
    subject = validate.text(d.get("subject"), 150)
    errors = {}
    if not body:
        errors["body"] = "Write the message."
    if channel == "email" and not subject:
        errors["subject"] = "Add a subject."
    if channel == "sms" and len(body) > MAX_SMS:
        errors["body"] = "Keep texts under %d characters (this is %d)." % (MAX_SMS, len(body))
    if errors:
        raise Invalid(errors)
    return kind, channel, subject, body, d.get("audience") or {}


def render(kind, channel, subject, body, person, h=None):
    """(subject, text, html, headers) for one person."""
    first = person.get("first_name") or "there"
    fill = (lambda s: templating.fill(s, {"first_name": first}))
    if channel == "sms":
        text = " ".join(fill(body).split())
        if kind == "marketing":
            text += " Opt out: " + marketing.unsubscribe_url(person["email"] or person["mobile"], h)
        return None, text, None, None
    marker = "\u0001FN\u0002"
    html_body = rich(body.replace("{{first_name}}", marker).replace("{{ first_name }}", marker)).replace(marker, esc(first))
    text = fill(body)
    headers = None
    if kind == "marketing":
        url = marketing.unsubscribe_url(person["email"], h)
        text += "\n\n—\nYou're getting this because you asked for news from Honeycombe Arts Hub. Unsubscribe: %s\n" % url
        html_body += ('<p style="color:#666;font-size:13px">You\'re getting this because you asked for news from '
                      'Honeycombe Arts Hub. <a href="%s">Unsubscribe</a></p>' % esc(url))
        headers = marketing.headers_for(person["email"], h)
    else:
        text += "\n\n—\nThis message is about your booking with Honeycombe Arts Hub.\n"
    return fill(subject), text, templating.html_layout(fill(subject), html_body), headers


@route("POST", "/api/staff/messages/preview", auth="staff", perm="messaging.service")
def preview(h):
    kind, channel, subject, body, audience = _check(h, h.json_body() or {})
    with db.read() as c:
        people, label = recipients(c, kind, channel, audience)
    sample = people[0][1] if people else {"first_name": "Sam", "email": "someone@example.org", "mobile": ""}
    subj, text, _, _ = render(kind, channel, subject, body, sample, h)
    return h.json({"count": len(people), "label": label, "subject": subj, "text": text,
                   "sample": [p[1]["first_name"] or p[0] for p in people[:12]],
                   "segments": (len(text) - 1) // 153 + 1 if channel == "sms" else None})


@route("POST", "/api/staff/messages/send", auth="staff", perm="messaging.service", body_limit=128 * 1024)
def send(h):
    d = h.json_body() or {}
    kind, channel, subject, body, audience = _check(h, d)
    with db.tx() as c:
        people, label = recipients(c, kind, channel, audience)
        if not people:
            raise ValueError("Nobody matches — nothing was sent.")
        if d.get("expected") != len(people):
            return h.json({"error": "The list has changed since you previewed it (%d people now). Preview again."
                                    % len(people), "count": len(people)}, 409)
        cid = c.execute("INSERT INTO campaigns(kind, channel, subject, body, audience, audience_label, recipients,"
                        " created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (kind, channel, subject or None, body, json.dumps(audience), label, len(people),
                         h.staff()["id"], db.now())).lastrowid
        for key, p in people:
            subj, text, html_body, headers = render(kind, channel, subject, body, p, h)
            if channel == "email":
                outbox.email_body(c, key, subj, text, html_body, kind=kind, headers=headers,
                                  account_id=p["account_id"], campaign_id=cid)
            else:
                outbox.text_message(c, key, None, kind=kind, account_id=p["account_id"], campaign_id=cid, body=text)
        audit.record(c, h, "messages.send", entity_type="campaign", entity_id=cid,
                     details={"kind": kind, "channel": channel, "recipients": len(people)})
    return h.json({"ok": True, "campaign_id": cid, "count": len(people)})


@route("GET", "/api/staff/messages", auth="staff", perm="messaging.service")
def campaigns(h):
    with db.read() as c:
        rows = c.execute("SELECT m.*, s.name AS sender FROM campaigns m LEFT JOIN staff_users s ON s.id=m.created_by"
                         " ORDER BY m.id DESC LIMIT 100").fetchall()
        out = []
        for r in rows:
            counts = {x[0]: x[1] for x in c.execute("SELECT status, COUNT(*) FROM message_deliveries WHERE campaign_id=?"
                                                    " GROUP BY status", (r["id"],))}
            out.append({"id": r["id"], "kind": r["kind"], "channel": r["channel"], "subject": r["subject"],
                        "body": r["body"], "audience": r["audience_label"], "recipients": r["recipients"],
                        "sender": r["sender"], "created_at": r["created_at"], "status": counts})
    return h.json({"campaigns": out})


@route("GET", "/api/staff/messages/archive", auth="staff", perm="messaging.service")
def archive(h):
    """Everything sent to an address (or recently). One-time links are never kept."""
    q = validate.text(h.query().get("q"), 120)
    with db.read() as c:
        if q:
            rows = c.execute("SELECT * FROM message_deliveries WHERE to_address LIKE ? ORDER BY id DESC LIMIT 100",
                             ("%" + q + "%",)).fetchall()
        else:
            rows = c.execute("SELECT * FROM message_deliveries WHERE kind<>'staff' ORDER BY id DESC LIMIT 100").fetchall()
        out = [{"id": r["id"], "channel": r["channel"], "kind": r["kind"], "to": r["to_address"], "subject": r["subject"],
                "status": r["status"], "error": r["error"], "created_at": r["created_at"], "sent_at": r["sent_at"],
                "template": r["template_key"], "body": outbox.archived_body(r)[:4000]} for r in rows]
    return h.json({"messages": out})
