"""Marketing preferences (PECR): who has asked for news by email or text.

Kept apart from service messages (booking confirmations, reminders), which
families can't switch off. Every opt-in records the wording it was given
under and where it came from; unsubscribing is always one click."""
from . import db

WORDING_VERSION = "2026-1"  # "Keep me updated about future events and activities"


def opt_in(c, email, *, source, name=None, account_id=None, guest_contact_id=None, sms=False, phone=None):
    now = db.now()
    row = c.execute("SELECT * FROM marketing_preferences WHERE email=?", (email,)).fetchone()
    if row:
        c.execute("UPDATE marketing_preferences SET email_opt_in=1, email_opt_in_at=?, unsubscribed_email_at=NULL,"
                  " sms_opt_in=CASE WHEN ? THEN 1 ELSE sms_opt_in END, phone=COALESCE(?, phone),"
                  " account_id=COALESCE(account_id, ?), guest_contact_id=COALESCE(guest_contact_id, ?),"
                  " name=COALESCE(name, ?), wording_version=?, updated_at=? WHERE id=?",
                  (now, 1 if sms else 0, phone, account_id, guest_contact_id, name, WORDING_VERSION, now, row["id"]))
        return row["id"]
    return c.execute("INSERT INTO marketing_preferences(email, phone, name, account_id, guest_contact_id, email_opt_in,"
                     " email_opt_in_at, sms_opt_in, sms_opt_in_at, wording_version, source, created_at, updated_at)"
                     " VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?)",
                     (email, phone, name, account_id, guest_contact_id, now, 1 if sms else 0, now if sms else None,
                      WORDING_VERSION, source, now, now)).lastrowid
