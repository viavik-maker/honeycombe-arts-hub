"""The waiting list: when a place frees up, offer it to the next family.

Entries are bookings with status 'waitlisted', ordered by priority (staff
can raise it) and then by when they joined. Brothers and sisters who joined
in one checkout share a waitlist_group and are offered together when there's
room for all of them; otherwise the offer is for as many as fit ("1 of 2").

In 'auto_offer' mode the offer is made straight away (email + text) and is
held for waitlist_offer_hours; in 'manual' mode staff get an in-tray item
and choose."""
import datetime

from . import booking_settings, catalogue, db, intray, outbox, worker
from .web import site_url


def free_places(c, session):
    return max(session["capacity"] - catalogue.places_taken(c, session["id"]), 0)


def _queue(c, session_id):
    return c.execute("SELECT * FROM bookings WHERE session_id=? AND status='waitlisted'"
                     " ORDER BY waitlist_priority DESC, created_at, id", (session_id,)).fetchall()


def offer_expiry(c, session):
    s = booking_settings.get_all(c)
    start = catalogue.session_start(session)
    now = catalogue.uk_now()
    hours = s["waitlist_offer_hours_soon"] if start - now < datetime.timedelta(hours=48) else s["waitlist_offer_hours"]
    until = min(now + datetime.timedelta(hours=hours), start - datetime.timedelta(minutes=30))
    return catalogue.utc_iso(max(until, now + datetime.timedelta(minutes=30)))


def make_offer(c, bookings, session, activity, h=None, of_group=None):
    """Offer places to BOOKINGS (same family, same session) and tell them."""
    expires = offer_expiry(c, session)
    for b in bookings:
        c.execute("UPDATE bookings SET status='offered', offer_expires_at=?, updated_at=? WHERE id=?",
                  (expires, db.now(), b["id"]))
    first = bookings[0]
    acct = c.execute("SELECT * FROM accounts WHERE id=?", (first["account_id"],)).fetchone() if first["account_id"] else None
    if not acct:
        return
    names = []
    for b in bookings:
        if b["participant_id"]:
            names.append(c.execute("SELECT first_name FROM participants WHERE id=?", (b["participant_id"],)).fetchone()[0])
    when = datetime.date.fromisoformat(session["date"]).strftime("%A %-d %B")
    until = catalogue.nice_time(catalogue.parse_utc(expires))
    partial = ""
    if of_group and of_group > len(bookings):
        partial = "There's room for %d of the %d children you asked for." % (len(bookings), of_group)
    ctx = {"first_name": acct["first_name"], "activity": activity["title"], "when": when,
           "time": "%s–%s" % (session["start_time"], session["end_time"]), "names": " and ".join(names) or "you",
           "until": until, "partial": partial,
           "bookings_url": site_url(h) + "/account/bookings"}
    outbox.email(c, acct["email"], "waitlist_offer", ctx, account_id=acct["id"], booking_id=first["id"])
    if acct["mobile"]:
        outbox.text_message(c, acct["mobile"], "waitlist_offer", ctx, account_id=acct["id"], booking_id=first["id"])


def places_freed(c, session_id, h=None):
    """Call (inside the transaction) whenever places may have become free:
    a cancellation, a decline, an expired hold or offer, more capacity."""
    session = c.execute("SELECT * FROM activity_sessions WHERE id=?", (session_id,)).fetchone()
    if not session or session["status"] != "scheduled":
        return 0
    activity = c.execute("SELECT * FROM activities WHERE id=?", (session["activity_id"],)).fetchone()
    if catalogue.session_start(session) <= catalogue.uk_now():
        return 0
    free = free_places(c, session)
    queue = _queue(c, session_id)
    if not free or not queue:
        return 0
    if activity["waitlist_mode"] == "manual":
        intray.add(c, "place_freed", "%d place%s free on %s (%s) — %d waiting" % (
            free, "" if free == 1 else "s", activity["title"], session["date"], len(queue)),
            perm="bookings.manage", entity_type="session", entity_id=session_id, session_id=session_id,
            activity_id=activity["id"])
        return 0
    offered, seen = 0, set()
    for b in queue:
        if free <= 0:
            break
        if b["id"] in seen:
            continue
        group = [x for x in queue if b["waitlist_group"] and x["waitlist_group"] == b["waitlist_group"]] or [b]
        seen.update(x["id"] for x in group)
        need = sum(x["places"] for x in group)
        if need <= free:
            make_offer(c, group, session, activity, h)
            free -= need
            offered += len(group)
        elif b["kind"] == "participant":
            # offer as many of the group as fit, saying so; the rest keep their place in the queue
            part = group[:free]
            make_offer(c, part, session, activity, h, of_group=len(group))
            free -= len(part)
            offered += len(part)
        # a party booking that doesn't fit waits; later, smaller entries may still fit
    return offered


@worker.job("waitlist_offers", every=60, timeout=120)
def expire_offers():
    """Offers nobody answered in time lapse, and the place moves on."""
    n = 0
    with db.tx() as c:
        rows = c.execute("SELECT * FROM bookings WHERE status='offered' AND offer_expires_at<=?", (db.now(),)).fetchall()
        for b in rows:
            c.execute("UPDATE bookings SET status='expired', updated_at=? WHERE id=?", (db.now(), b["id"]))
            n += 1
        for sid in {b["session_id"] for b in rows}:
            places_freed(c, sid)
    return "expired %d offer(s)" % n
