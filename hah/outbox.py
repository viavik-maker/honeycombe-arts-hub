"""The outbox: every email and text the site sends goes through here.

Queue a message inside the same transaction as the change that causes it
(so a rolled-back booking never sends a confirmation); the background worker
sends it shortly after, retrying with back-off if the mail server or SMS
provider is unavailable. The table doubles as the message archive.

One-time links (password resets, invites…) are passed as SECRET: the stored
message says {{SECRET}} and the real link is added only at the moment of
sending, then wiped — so the archive, backups and data exports never hold a
link someone could use."""
import datetime
import html as _html
import json

from . import db, mail, sms, templating, validate, worker

BATCH = 20
RETRY_AFTER = [60, 300, 1800, 7200, 43200]  # seconds; then give up
LOCK_SECONDS = 300


def _at(seconds=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def email(c, to, template, context=None, *, kind="service", to_name=None, secret=None, headers=None,
          account_id=None, staff_id=None, participant_id=None, booking_id=None, campaign_id=None):
    """Queue an email (inside the caller's db.tx()). Returns the delivery id."""
    to = validate.email(to)
    if not to:
        return None
    intro = c.execute("SELECT intro FROM email_intros WHERE template_key=?", (template,)).fetchone() \
        if kind != "staff" else None
    subject, text, html_body = templating.render_email(template, context or {}, intro[0] if intro else None)
    cur = c.execute(
        "INSERT INTO message_deliveries(campaign_id, template_key, channel, kind, to_address, to_name, subject,"
        " body_text, body_html, headers, secret, account_id, staff_id, participant_id, booking_id,"
        " next_attempt_at, created_at) VALUES (?,?, 'email', ?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (campaign_id, template, kind, to, to_name, subject, text, html_body,
         json.dumps(headers) if headers else None, secret, account_id, staff_id, participant_id, booking_id,
         db.now(), db.now()))
    return cur.lastrowid


def email_body(c, to, subject, text, html_body, *, kind="service", to_name=None, headers=None, account_id=None,
               campaign_id=None):
    """Queue an email whose words staff wrote (Messages). TEXT/HTML_BODY are final."""
    to = validate.email(to)
    if not to:
        return None
    cur = c.execute(
        "INSERT INTO message_deliveries(campaign_id, template_key, channel, kind, to_address, to_name, subject,"
        " body_text, body_html, headers, account_id, next_attempt_at, created_at)"
        " VALUES (?, 'campaign', 'email', ?,?,?,?,?,?,?,?,?,?)",
        (campaign_id, kind, to, to_name, subject, text, html_body, json.dumps(headers) if headers else None,
         account_id, db.now(), db.now()))
    return cur.lastrowid


def text_message(c, to, template, context=None, *, kind="service", account_id=None, participant_id=None,
                 booking_id=None, campaign_id=None, body=None):
    """Queue an SMS to a UK mobile (inside the caller's db.tx()). Returns the
    delivery id, or None if the number isn't a UK mobile."""
    to = validate.uk_mobile(to)
    if not to:
        return None
    text = body if body is not None else templating.render_sms(template, context or {})
    blocked = c.execute("SELECT 1 FROM sms_blocks WHERE phone=?", (to,)).fetchone()
    cur = c.execute(
        "INSERT INTO message_deliveries(campaign_id, template_key, channel, kind, to_address, body_text,"
        " account_id, participant_id, booking_id, next_attempt_at, created_at, status, error)"
        " VALUES (?,?, 'sms', ?,?,?,?,?,?,?,?,?,?)",
        (campaign_id, template, kind, to, text, account_id, participant_id, booking_id, db.now(), db.now(),
         "suppressed" if blocked else "queued", "They replied STOP to our texts" if blocked else None))
    return cur.lastrowid


def block_sms(c, phone, source):
    """No more texts to PHONE (they replied STOP, or the provider says they opted out)."""
    c.execute("INSERT OR IGNORE INTO sms_blocks(phone, blocked_at, source) VALUES (?,?,?)", (phone, db.now(), source))
    for r in c.execute("SELECT id, phone FROM marketing_preferences WHERE sms_opt_in=1 AND phone IS NOT NULL").fetchall():
        if validate.uk_mobile(r["phone"]) == phone:
            c.execute("UPDATE marketing_preferences SET sms_opt_in=0, unsubscribed_sms_at=?, updated_at=? WHERE id=?",
                      (db.now(), db.now(), r["id"]))
    c.execute("UPDATE message_deliveries SET status='suppressed', error='They replied STOP to our texts',"
              " locked_until=NULL WHERE channel='sms' AND to_address=? AND status='queued'", (phone,))


def _claim():
    """Mark up to BATCH due messages as 'sending' and return them."""
    now = db.now()
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM message_deliveries WHERE (status='queued' AND next_attempt_at<=?)"
            " OR (status='sending' AND locked_until<=?) ORDER BY id LIMIT ?", (now, now, BATCH))]
        if not mail.configured():
            rows = [r for r in rows if r["channel"] != "email"]
        if not sms.configured():
            rows = [r for r in rows if r["channel"] != "sms"]
        for r in rows:
            c.execute("UPDATE message_deliveries SET status='sending', locked_until=? WHERE id=?",
                      (_at(LOCK_SECONDS), r["id"]))
    return rows


def _finish(row, ok, provider_id=None, error=None, permanent=False, suppressed=False):
    attempts = row["attempts"] + 1
    with db.tx() as c:
        if ok:
            c.execute("UPDATE message_deliveries SET status='sent', attempts=?, provider_id=?, sent_at=?,"
                      " secret=NULL, error=NULL, locked_until=NULL WHERE id=?",
                      (attempts, provider_id, db.now(), row["id"]))
        elif suppressed or permanent or attempts > len(RETRY_AFTER):
            c.execute("UPDATE message_deliveries SET status=?, attempts=?, error=?, secret=NULL, locked_until=NULL"
                      " WHERE id=?", ("suppressed" if suppressed else "failed", attempts, (error or "")[:500], row["id"]))
        else:
            c.execute("UPDATE message_deliveries SET status='queued', attempts=?, error=?, next_attempt_at=?,"
                      " locked_until=NULL WHERE id=?",
                      (attempts, (error or "")[:500], _at(RETRY_AFTER[attempts - 1]), row["id"]))


def _with_secret(row):
    """(text, html) with the one-time link put in for sending."""
    text, html_body = row["body_text"], row["body_html"]
    if row["secret"]:
        text = text.replace(templating.SECRET, row["secret"])
        if html_body:
            value = _html.escape(row["secret"], quote=True)
            if row["secret"].startswith(("https://", "http://")):
                value = '<a href="%s">%s</a>' % (value, value)
            html_body = html_body.replace(templating.SECRET, value)
    return text, html_body


def send_due():
    """Send what's due. Returns a summary for the job log."""
    rows = _claim()
    sent = failed = 0
    emails = [r for r in rows if r["channel"] == "email"]
    if emails:
        try:
            with mail.Connection() as conn:
                for r in emails:
                    text, html_body = _with_secret(r)
                    try:
                        mid = conn.send(mail.build(r["to_address"], r["subject"], text, html_body, r["to_name"],
                                                   json.loads(r["headers"]) if r["headers"] else None, conn.cfg))
                        _finish(r, True, mid)
                        sent += 1
                    except mail.MailError as e:
                        _finish(r, False, error=str(e), permanent=e.permanent)
                        failed += 1
        except mail.MailError as e:  # couldn't connect: retry the whole batch later
            for r in emails:
                _finish(r, False, error=str(e))
            failed += len(emails)
    for r in (r for r in rows if r["channel"] == "sms"):
        try:
            _finish(r, True, sms.send(r["to_address"], r["body_text"]))
            sent += 1
        except sms.SmsError as e:
            _finish(r, False, error=str(e), permanent=e.permanent, suppressed=e.opted_out)
            if e.opted_out:
                with db.tx() as c:
                    block_sms(c, r["to_address"], "provider_opt_out")
            failed += 1
    return "sent %d, failed %d" % (sent, failed) if rows else None


@worker.job("outbox", every=10, timeout=300)
def outbox_job():
    return send_due()


def archived_body(row):
    """What staff see in the archive: never the one-time link."""
    return (row["body_text"] or "").replace(templating.SECRET, "[one-time link — not kept]")


def stats():
    with db.read() as c:
        rows = c.execute("SELECT channel, status, COUNT(*) n FROM message_deliveries GROUP BY channel, status").fetchall()
        recent_failures = [dict(r) for r in c.execute(
            "SELECT id, channel, template_key, error, created_at FROM message_deliveries"
            " WHERE status='failed' ORDER BY id DESC LIMIT 5")]
    out = {}
    for r in rows:
        out.setdefault(r["channel"], {})[r["status"]] = r["n"]
    return {"counts": out, "recent_failures": recent_failures}
