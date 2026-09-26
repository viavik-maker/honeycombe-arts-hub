"""Admin → Messages: email or text many families at once, and the archive
of everything the site has sent.

Two kinds, kept apart as PECR requires:
  * service messages — about something a family has booked (a change of
    room, a reminder, a cancelled session). They go to everyone booked,
    whatever their marketing choices.
  * news (marketing) — only to people who opted in and haven't
    unsubscribed. Every news email carries a one-click unsubscribe.
{{first_name}} in the subject or message is replaced for each person.

Messages can be scheduled: the audience rule is stored and worked out again
when the message goes, so families who book in the meantime get it too."""
import datetime
import json
import math

from . import audit, booking_settings, catalogue, db, intray, marketing, outbox, templating, validate, worker
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


# ---------------------------------------------------------------- text message length

GSM = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿"
          "abcdefghijklmnopqrstuvwxyzäöñüà")
GSM_EXT = set("^{}\\[~]|€\f")  # these count as two characters


def sms_info(text):
    """{segments, unicode, odd}: how many texts this is. One character outside the
    standard GSM set (a curly apostrophe, an emoji) makes the whole message Unicode,
    which fits 70 characters per text instead of 160."""
    odd = sorted({ch for ch in text if ch not in GSM and ch not in GSM_EXT})
    if odd:
        n = sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)
        return {"segments": 1 if n <= 70 else math.ceil(n / 67), "unicode": True, "odd": odd}
    n = len(text) + sum(1 for ch in text if ch in GSM_EXT)
    return {"segments": 1 if n <= 160 else math.ceil(n / 153), "unicode": False, "odd": []}


# ---------------------------------------------------------------- audiences


def _age_band(audience):
    """(youngest, oldest) in whole years, or None if no age filter."""
    lo, hi = audience.get("min_age"), audience.get("max_age")
    if lo in (None, "") and hi in (None, ""):
        return None
    try:
        lo = int(lo) if lo not in (None, "") else 0
        hi = int(hi) if hi not in (None, "") else 99
    except (TypeError, ValueError):
        raise Invalid({"audience": "Enter ages as whole years."})
    if not (0 <= lo <= hi <= 99):
        raise Invalid({"audience": "Enter an age range from 0 to 99, youngest first."})
    return lo, hi


def _dob_range(lo, hi, today):
    """Dates of birth for someone aged LO to HI (inclusive) today: (after, on_or_before)."""
    def years_ago(n):
        try:
            return today.replace(year=today.year - n)
        except ValueError:  # 29 February
            return today.replace(year=today.year - n, day=28)
    return years_ago(hi + 1).isoformat(), years_ago(lo).isoformat()


def _age_sql(band):
    after, upto = _dob_range(band[0], band[1], catalogue.uk_today())
    return "SELECT id FROM participants WHERE dob>? AND dob<=? AND status='active'", (after, upto)


def _age_text(band):
    lo, hi = band
    return "aged %d+" % lo if hi == 99 else "aged %d–%d" % (lo, hi)


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
        band = _age_band(audience)
        if band:
            sql, args = _age_sql(band)
            fams = c.execute("SELECT id, email FROM accounts WHERE status NOT IN ('closed','anonymised') AND id IN"
                             " (SELECT account_id FROM participants WHERE id IN (%s))" % sql, args).fetchall()
            ids, emails = {f["id"] for f in fams}, {(f["email"] or "").lower() for f in fams}
            rows = [r for r in rows if r["account_id"] in ids or (r["email"] or "").lower() in emails]
            label += ", with a child %s" % _age_text(band)
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
    elif t == "age":
        band = _age_band(audience)
        if not band:
            raise Invalid({"audience": "Enter an age range."})
        sql, args = _age_sql(band)
        rows = _booked(c, "s.date>=? AND b.participant_id IN (%s)" % sql, (today,) + args)
        label = "Booked on upcoming sessions, for a child %s" % _age_text(band)
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
        pence = booking_settings.get("sms_segment_pence", c)
    sample = people[0][1] if people else {"first_name": "Sam", "email": "someone@example.org", "mobile": ""}
    subj, text, _, _ = render(kind, channel, subject, body, sample, h)
    out = {"count": len(people), "label": label, "subject": subj, "text": text,
           "sample": [p[1]["first_name"] or p[0] for p in people[:12]]}
    if channel == "sms":
        info = sms_info(text)
        texts = sum(sms_info(render(kind, channel, subject, body, p, h)[1])["segments"] for _, p in people)
        out.update(segments=info["segments"], unicode=info["unicode"], odd=info["odd"], texts=texts,
                   cost_pence=texts * pence)
    return h.json(out)


def _deliver(c, cid, kind, channel, subject, body, people, h=None):
    for key, p in people:
        subj, text, html_body, headers = render(kind, channel, subject, body, p, h)
        if channel == "email":
            outbox.email_body(c, key, subj, text, html_body, kind=kind, headers=headers,
                              account_id=p["account_id"], campaign_id=cid)
        else:
            outbox.text_message(c, key, None, kind=kind, account_id=p["account_id"], campaign_id=cid, body=text)


MAX_AHEAD_DAYS = 90


def _send_at(d):
    """UTC time to send, or None for now."""
    v = (d.get("send_at_local") or "").strip()
    if not v:
        return None
    try:
        dt = datetime.datetime.fromisoformat(v)
    except ValueError:
        raise Invalid({"send_at_local": "Enter a date and time."})
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=catalogue.UK)
    now = catalogue.uk_now()
    if dt <= now + datetime.timedelta(minutes=2):
        raise Invalid({"send_at_local": "Choose a time in the future (or send it now)."})
    if dt > now + datetime.timedelta(days=MAX_AHEAD_DAYS):
        raise Invalid({"send_at_local": "Messages can be scheduled up to %d days ahead." % MAX_AHEAD_DAYS})
    return catalogue.utc_iso(dt)


@route("POST", "/api/staff/messages/send", auth="staff", perm="messaging.service", body_limit=128 * 1024)
def send(h):
    d = h.json_body() or {}
    kind, channel, subject, body, audience = _check(h, d)
    send_at = _send_at(d)
    with db.tx() as c:
        people, label = recipients(c, kind, channel, audience)
        if not people and not send_at:
            raise ValueError("Nobody matches — nothing was sent.")
        if d.get("expected") != len(people):
            return h.json({"error": "The list has changed since you previewed it (%d people now). Preview again."
                                    % len(people), "count": len(people)}, 409)
        cid = c.execute("INSERT INTO campaigns(kind, channel, subject, body, audience, audience_label, recipients,"
                        " created_by, created_at, status, send_at, sent_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (kind, channel, subject or None, body, json.dumps(audience), label, len(people),
                         h.staff()["id"], db.now(), "scheduled" if send_at else "sent", send_at,
                         None if send_at else db.now())).lastrowid
        if not send_at:
            _deliver(c, cid, kind, channel, subject, body, people, h)
        audit.record(c, h, "messages.schedule" if send_at else "messages.send", entity_type="campaign", entity_id=cid,
                     details={"kind": kind, "channel": channel, "recipients": len(people), "send_at": send_at})
    return h.json({"ok": True, "campaign_id": cid, "count": len(people), "scheduled": bool(send_at),
                   "send_at": send_at})


@route("POST", "/api/staff/messages/<cid>/cancel", auth="staff", perm="messaging.service")
def cancel(h, cid):
    with db.tx() as c:
        m = c.execute("SELECT * FROM campaigns WHERE id=?", (int(cid) if cid.isdigit() else 0,)).fetchone()
        if not m:
            raise LookupError
        if m["kind"] == "marketing" and not h.has_perm("messaging.marketing"):
            raise ValueError("You don't have permission to change news.")
        if m["status"] != "scheduled":
            raise ValueError("Only messages waiting to be sent can be cancelled.")
        c.execute("UPDATE campaigns SET status='cancelled', cancelled_by=? WHERE id=?", (h.staff()["id"], m["id"]))
        audit.record(c, h, "messages.cancel", entity_type="campaign", entity_id=m["id"])
    return h.json({"ok": True})


@worker.job("scheduled_messages", every=60, timeout=120)
def send_scheduled():
    """Send scheduled messages that are due, to whoever matches now."""
    sent = 0
    with db.tx() as c:
        for m in c.execute("SELECT * FROM campaigns WHERE status='scheduled' AND send_at<=? ORDER BY send_at",
                           (db.now(),)).fetchall():
            try:
                people, label = recipients(c, m["kind"], m["channel"], json.loads(m["audience"]))
            except Invalid as e:
                problem = "; ".join(e.errors.values())
                c.execute("UPDATE campaigns SET status='cancelled', problem=? WHERE id=?", (problem, m["id"]))
                intray.add(c, "message_failed", "A scheduled message wasn't sent: %s" % problem,
                           perm="messaging.service", entity_type="campaign", entity_id=m["id"])
                continue
            _deliver(c, m["id"], m["kind"], m["channel"], m["subject"], m["body"], people)
            c.execute("UPDATE campaigns SET status='sent', sent_at=?, recipients=?, audience_label=? WHERE id=?",
                      (db.now(), len(people), label, m["id"]))
            audit.record(c, None, "messages.send", entity_type="campaign", entity_id=m["id"],
                         details={"scheduled": True, "recipients": len(people)})
            sent += 1
    return "sent %d scheduled message(s)" % sent if sent else None


@route("GET", "/api/staff/messages", auth="staff", perm="messaging.service")
def campaigns(h):
    with db.read() as c:
        rows = c.execute("SELECT m.*, s.name AS sender FROM campaigns m LEFT JOIN staff_users s ON s.id=m.created_by"
                         " ORDER BY m.status='scheduled' DESC, m.id DESC LIMIT 100").fetchall()
        out = []
        for r in rows:
            counts = {x[0]: x[1] for x in c.execute("SELECT status, COUNT(*) FROM message_deliveries WHERE campaign_id=?"
                                                    " GROUP BY status", (r["id"],))}
            out.append({"id": r["id"], "kind": r["kind"], "channel": r["channel"], "subject": r["subject"],
                        "body": r["body"], "audience": r["audience_label"], "recipients": r["recipients"],
                        "sender": r["sender"], "created_at": r["created_at"], "status": counts,
                        "state": r["status"], "send_at": r["send_at"], "sent_at": r["sent_at"],
                        "problem": r["problem"]})
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
