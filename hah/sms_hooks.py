"""Twilio calling us back: delivery reports for texts we sent, and replies.

Point the Twilio number's "A message comes in" webhook at
/api/twilio/inbound. Delivery reports come to /api/twilio/status by
themselves (sms.py asks for them on every text once SITE_URL is https).
Both are checked against Twilio's signature, made with TWILIO_AUTH_TOKEN.

A reply of STOP (or UNSUBSCRIBE, CANCEL, END, QUIT, STOPALL) stops all our
texts to that number, service texts included, as Twilio itself does; START
(or UNSTOP, YES) allows them again, but news texts stay off until the family
opts in again."""
import urllib.parse

from . import audit, db, outbox, sms, validate
from .web import route, site_url

STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT"}
START_WORDS = {"START", "UNSTOP", "YES", "OPTIN"}
FINAL = {"delivered", "undelivered", "failed", "sent", "queued", "sending", "accepted", "read"}


def _form(h):
    """(fields, ok) for a signed Twilio POST."""
    fields = dict(urllib.parse.parse_qsl(h.body().decode("utf-8", "replace"), keep_blank_values=True))
    url = site_url(h) + h.path
    return fields, sms.verify_twilio(url, fields, h.headers.get("X-Twilio-Signature") or "")


@route("POST", "/api/twilio/status", csrf=False, body_limit=16 * 1024)
def status(h):
    f, ok = _form(h)
    if not ok:
        return h.send(403, b"bad signature", "text/plain")
    sid, state = f.get("MessageSid") or "", (f.get("MessageStatus") or "").lower()
    if sid and state in FINAL:
        with db.tx() as c:
            row = c.execute("SELECT id, to_address FROM message_deliveries WHERE provider_id=? AND channel='sms'",
                            (sid,)).fetchone()
            if row:
                err = f.get("ErrorCode")
                c.execute("UPDATE message_deliveries SET delivery_status=?, delivered_at=CASE WHEN ?='delivered'"
                          " THEN ? ELSE delivered_at END, error=CASE WHEN ? IS NOT NULL THEN ? ELSE error END WHERE id=?",
                          (state, state, db.now(), err, "Twilio error %s" % err if err else None, row["id"]))
                if err == "21610":  # the person has replied STOP to this sender
                    outbox.block_sms(c, row["to_address"], "provider_opt_out")
    return h.send(204, b"", "text/plain")


@route("POST", "/api/twilio/inbound", csrf=False, body_limit=16 * 1024)
def inbound(h):
    f, ok = _form(h)
    if not ok:
        return h.send(403, b"bad signature", "text/plain")
    phone = validate.uk_mobile(f.get("From") or "")
    word = (f.get("Body") or "").strip().split(" ")[0].upper().strip(".!")
    if phone and word in STOP_WORDS | START_WORDS:
        with db.tx() as c:
            if word in STOP_WORDS:
                outbox.block_sms(c, phone, "reply_stop")
            else:
                c.execute("DELETE FROM sms_blocks WHERE phone=? AND source<>'staff'", (phone,))
            audit.record(c, h, "sms.reply_stop" if word in STOP_WORDS else "sms.reply_start",
                         details={"number": phone[:6] + "…" + phone[-3:]})
    # Twilio sends its own STOP/START confirmation; we don't add another reply
    return h.send(200, b'<?xml version="1.0" encoding="UTF-8"?><Response></Response>', "text/xml")
