"""Reading the catalogue: places left, prices, booking windows, ages.

Shared by the public Book page, the booking engine, registers and the
admin. Times on sessions are UK local; *_at columns are UTC."""
import datetime
import zoneinfo

from . import config

UK = zoneinfo.ZoneInfo(config.TIMEZONE)

# bookings in these states hold a place
HOLDING = ("pending_payment", "pending_approval", "offered", "confirmed", "pending_confirmation")
HOLDING_SQL = "('pending_payment','pending_approval','offered','confirmed','pending_confirmation')"
FEW_LEFT = 3


def uk_now():
    return datetime.datetime.now(UK)


def uk_today():
    return uk_now().date()


def utc_iso(dt):
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def nice_time(dt):
    """'9:30am on Monday 3 August' (UK time)."""
    dt = dt.astimezone(UK)
    return dt.strftime("%-I:%M%p").lower().replace(":00", "") + dt.strftime(" on %A %-d %B")


def nice_date(iso):
    return datetime.date.fromisoformat(iso).strftime("%a %-d %b")


def session_start(s):
    """A session row's start as an aware datetime."""
    return datetime.datetime.fromisoformat("%s %s" % (s["date"], s["start_time"])).replace(tzinfo=UK)


def places_taken(c, session_id):
    return c.execute("SELECT COALESCE(SUM(places),0) FROM bookings WHERE session_id=? AND status IN " + HOLDING_SQL,
                     (session_id,)).fetchone()[0]


def waitlist_count(c, session_id):
    return c.execute("SELECT COUNT(*) FROM bookings WHERE session_id=? AND status='waitlisted'",
                     (session_id,)).fetchone()[0]


def price_of(activity, session):
    return session["price_pence"] if session["price_pence"] is not None else activity["price_pence"]


def centre_of(activity, session):
    return session["centre_id"] or activity["centre_id"]


def window(activity, session, now=None):
    """(state, opens_at) for booking this session right now: 'open',
    'not_open_yet' (with the UTC time it opens), 'closed' (too close to the
    start or already started) or 'cancelled'."""
    now = now or uk_now()
    if session["status"] != "scheduled":
        return "cancelled", None
    if activity["booking_opens_at"] and parse_utc(activity["booking_opens_at"]) > now:
        return "not_open_yet", activity["booking_opens_at"]
    if session_start(session) - datetime.timedelta(hours=activity["booking_closes_hours"]) <= now:
        return "closed", None
    return "open", None


def availability(c, activity, session, now=None):
    """What the Book page shows for a session."""
    state, opens = window(activity, session, now)
    taken = places_taken(c, session["id"])
    left = max(session["capacity"] - taken, 0)
    waiting = waitlist_count(c, session["id"])
    if state == "open":
        if left == 0 or waiting:
            state = "waitlist" if activity["waitlist_enabled"] else "full"
        elif left <= FEW_LEFT:
            state = "few"
    return {"state": state, "opens_at": opens, "places_left": left if not waiting else 0, "taken": taken,
            "waiting": waiting}


def first_session_date(c, activity_id, today=None):
    """The first session still to come (sessions already past don't count: a baby born since a class began
    can still join it)."""
    r = c.execute("SELECT MIN(date) FROM activity_sessions WHERE activity_id=? AND status='scheduled' AND date>=?",
                  (activity_id, (today or uk_today()).isoformat())).fetchone()
    return r[0]


def age_on(activity, session, first_date):
    """The date a child's age is checked on for this session. By school year, it's the 31 August before that
    school year starts (so a whole class is in or out together)."""
    if activity["age_basis"] == "first_session" and first_date and first_date < session["date"]:
        day = datetime.date.fromisoformat(first_date)
    else:
        day = datetime.date.fromisoformat(session["date"])
    if activity["age_by_school_year"]:
        return datetime.date(day.year if day.month >= 9 else day.year - 1, 8, 31)
    return day


def months_between(dob, on):
    m = (on.year - dob.year) * 12 + (on.month - dob.month)
    if on.day < dob.day:
        m -= 1
    return m


def age_range_text(activity):
    """Ages as the charity lists them: 0-23 months is "0–1", 24-59 is "2–4"."""
    lo, hi = activity["min_age_months"], activity["max_age_months"]
    if hi >= 1200:
        return "%s+" % _age_label(lo)
    if lo % 12 == 0 and (hi + 1) % 12 == 0:
        return "%d–%d" % (lo // 12, hi // 12)
    return "%s to %s" % (_age_label(lo), _age_label(hi))


def _age_label(months):
    if months < 24 and months % 12:
        return "%d months" % months
    return str(months // 12) if months % 12 == 0 else "%dy %dm" % (months // 12, months % 12)


def derived_status(activity, last_date, today=None):
    """The status tab an activity appears under ('past' isn't stored)."""
    today = today or uk_today()
    if activity["status"] in ("published", "unpublished", "scheduled") and last_date and \
            datetime.date.fromisoformat(last_date) < today:
        return "past"
    return activity["status"]


def is_bookable(activity):
    return activity["status"] == "published"
