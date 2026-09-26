"""Admin → Reports: the attendance dashboard.

Counts come from registers (attendance rows), using the snapshots taken
when each booking was confirmed (age, HAF, SEND, category), so reports stay
right even after a family has been erased. Periods: year (the charity's
reporting year), quarter, month, week or day."""
import datetime

from . import audit, booking_settings, catalogue, db, validate
from .web import route

PERIODS = ("year", "quarter", "month", "week", "day")


def period_range(period, day, start_month=1):
    """(first, last, label, bucket) for the period containing DAY."""
    if period == "day":
        return day, day, day.strftime("%A %-d %B %Y"), "day"
    if period == "week":
        first = day - datetime.timedelta(days=day.weekday())
        return first, first + datetime.timedelta(days=6), "Week of %s" % first.strftime("%-d %B %Y"), "day"
    if period == "month":
        first = day.replace(day=1)
        nxt = (first + datetime.timedelta(days=32)).replace(day=1)
        return first, nxt - datetime.timedelta(days=1), first.strftime("%B %Y"), "day"
    # reporting year / quarter, starting in START_MONTH
    year = day.year if day.month >= start_month else day.year - 1
    ystart = datetime.date(year, start_month, 1)
    if period == "year":
        yend = datetime.date(year + 1, start_month, 1) - datetime.timedelta(days=1)
        label = str(year) if start_month == 1 else "%d–%s" % (year, str(year + 1)[2:])
        return ystart, yend, label, "month"
    months = (day.year - ystart.year) * 12 + day.month - ystart.month
    q = months // 3
    qstart = _add_months(ystart, q * 3)
    qend = _add_months(qstart, 3) - datetime.timedelta(days=1)
    return qstart, qend, "Q%d %s" % (q + 1, qstart.strftime("%b %Y") + "–" + qend.strftime("%b %Y")), "month"


def _add_months(d, n):
    m = d.month - 1 + n
    return datetime.date(d.year + m // 12, m % 12 + 1, 1)


def attendance(c, first, last, bucket):
    rows = c.execute(
        "SELECT s.date, COALESCE(a.snap_category, cat.report_group) AS cat, a.status, a.snap_haf, a.snap_send,"
        " a.participant_id, b.places, b.kind, b.party_children, act.title, act.id AS activity_id"
        " FROM attendance a JOIN bookings b ON b.id=a.booking_id JOIN activity_sessions s ON s.id=a.session_id"
        " JOIN activities act ON act.id=s.activity_id JOIN activity_categories cat ON cat.id=act.category_id"
        " WHERE s.date BETWEEN ? AND ? AND b.status='confirmed'", (first.isoformat(), last.isoformat())).fetchall()
    totals = {"expected": 0, "present": 0, "absent": 0, "absent_notified": 0, "haf": 0, "send": 0}
    by_cat, by_act, series, children = {}, {}, {}, set()
    for r in rows:
        n = r["party_children"] or r["places"] if r["kind"] == "party" else 1  # people, for group bookings
        st = r["status"]
        totals[st] = totals.get(st, 0) + n
        key = r["date"][:7] if bucket == "month" else r["date"]
        cat = r["cat"] or "Other"
        if st == "present":
            if r["snap_haf"]:
                totals["haf"] += n
            if r["snap_send"]:
                totals["send"] += n
            if r["participant_id"]:
                children.add(r["participant_id"])
            series.setdefault(key, {}).setdefault(cat, 0)
            series[key][cat] += n
            by_act.setdefault(r["activity_id"], {"title": r["title"], "present": 0, "absent": 0})["present"] += n
        elif st in ("absent", "absent_notified"):
            by_act.setdefault(r["activity_id"], {"title": r["title"], "present": 0, "absent": 0})["absent"] += n
        c_ = by_cat.setdefault(cat, {"category": cat, "present": 0, "absent": 0, "expected": 0})
        c_["present" if st == "present" else "absent" if st.startswith("absent") else "expected"] += n
    new = 0
    if children:
        ids = sorted(children)
        new = c.execute("SELECT COUNT(*) FROM (SELECT a.participant_id, MIN(s.date) AS first FROM attendance a"
                        " JOIN activity_sessions s ON s.id=a.session_id WHERE a.status='present' AND a.participant_id IN"
                        " (%s) GROUP BY a.participant_id) WHERE first>=?" % ",".join("?" * len(ids)),
                        ids + [first.isoformat()]).fetchone()[0]
    ran_to = min(last, catalogue.uk_today())  # "sessions run" means ones that have happened
    sessions = c.execute("SELECT COUNT(*) FROM activity_sessions WHERE date BETWEEN ? AND ? AND status='scheduled'",
                         (first.isoformat(), ran_to.isoformat())).fetchone()[0]
    # every bucket in the period, so the chart has no gaps
    keys, d = [], first
    while d <= last:
        k = d.isoformat()[:7] if bucket == "month" else d.isoformat()
        if k not in keys:
            keys.append(k)
        d += datetime.timedelta(days=1)
    return {"totals": dict(totals, sessions=sessions, children=len(children), new_children=new,
                           no_shows=totals.get("absent", 0)),
            "series": [{"key": k, "by_category": series.get(k, {}), "present": sum(series.get(k, {}).values())}
                       for k in keys],
            "categories": sorted(by_cat.values(), key=lambda x: -x["present"]),
            "activities": sorted(by_act.values(), key=lambda x: -x["present"])[:20]}


@route("GET", "/api/staff/reports/attendance", auth="staff", perm="reports.view")
def attendance_api(h):
    q = h.query()
    period = q.get("period") if q.get("period") in PERIODS else "month"
    day = validate.date(q.get("date")) or catalogue.uk_today()
    with db.read() as c:
        start_month = booking_settings.get("reporting_year_start_month", c)
        first, last, label, bucket = period_range(period, day, start_month)
        data = attendance(c, first, last, bucket)
        booked_ahead = c.execute("SELECT COALESCE(SUM(b.places),0) FROM bookings b JOIN activity_sessions s ON"
                                 " s.id=b.session_id WHERE b.status='confirmed' AND s.date>?",
                                 (catalogue.uk_today().isoformat(),)).fetchone()[0]
        cats = [r[0] for r in c.execute("SELECT DISTINCT report_group FROM activity_categories ORDER BY sort")]
    return h.json(dict(data, period=period, label=label, first=first.isoformat(), last=last.isoformat(),
                       bucket=bucket, booked_ahead=booked_ahead, category_order=cats))


@route("GET", "/api/staff/reports/attendance.csv", auth="staff", perm="reports.view")
def attendance_csv(h):
    """Daily breakdown by session category for the chosen period."""
    q = h.query()
    period = q.get("period") if q.get("period") in PERIODS else "month"
    day = validate.date(q.get("date")) or catalogue.uk_today()
    with db.tx() as c:
        first, last, label, _ = period_range(period, day, booking_settings.get("reporting_year_start_month", c))
        data = attendance(c, first, last, "day")
        cats = sorted({k for s in data["series"] for k in s["by_category"]})
        rows = [[s["key"]] + [s["by_category"].get(k, 0) for k in cats] + [s["present"]] for s in data["series"]]
        audit.record(c, h, "report.attendance_export", details={"from": first.isoformat(), "to": last.isoformat()})
    return h.csv("attendance-%s-to-%s.csv" % (first, last), ["Date"] + cats + ["Total attended"], rows)


# ---------------------------------------------------------------- trials

CONVERT_DAYS = 90


def trials(c, first, last):
    """Trial sessions in the period: did they come, and did they book again
    (a paid or funded, non-trial booking made after the trial, within 90 days)?"""
    today = catalogue.uk_today().isoformat()
    rows = c.execute(
        "SELECT b.id, b.participant_id, b.activity_id, b.status, b.created_at, s.date, act.title, a.status AS att"
        " FROM bookings b JOIN activity_sessions s ON s.id=b.session_id JOIN activities act ON act.id=b.activity_id"
        " LEFT JOIN attendance a ON a.booking_id=b.id WHERE b.is_trial=1 AND b.participant_id IS NOT NULL"
        " AND s.date BETWEEN ? AND ? AND b.status IN ('confirmed','cancelled')",
        (first.isoformat(), last.isoformat())).fetchall()
    totals = {"trials": 0, "attended": 0, "no_shows": 0, "cancelled": 0, "upcoming": 0, "converted": 0,
              "converted_same": 0}
    by_act = {}
    for r in rows:
        act = by_act.setdefault(r["activity_id"], {"title": r["title"], "trials": 0, "attended": 0, "converted": 0})
        if r["status"] == "cancelled":
            totals["cancelled"] += 1
            continue
        totals["trials"] += 1
        act["trials"] += 1
        if r["date"] > today:
            totals["upcoming"] += 1
            continue
        if r["att"] == "present":
            totals["attended"] += 1
            act["attended"] += 1
        elif r["att"] in ("absent", "absent_notified"):
            totals["no_shows"] += 1
        until = (datetime.date.fromisoformat(r["date"]) + datetime.timedelta(days=CONVERT_DAYS)).isoformat()
        later = c.execute(
            "SELECT b.activity_id FROM bookings b JOIN activity_sessions s ON s.id=b.session_id"
            " WHERE b.participant_id=? AND b.is_trial=0 AND b.status IN " + catalogue.HOLDING_SQL +
            " AND b.id<>? AND b.created_at>=? AND s.date>=? AND b.created_at<=?",
            (r["participant_id"], r["id"], r["created_at"], r["date"], until + "T23:59:59Z")).fetchall()
        if later:
            totals["converted"] += 1
            act["converted"] += 1
            if any(x["activity_id"] == r["activity_id"] for x in later):
                totals["converted_same"] += 1
    past = totals["trials"] - totals["upcoming"]
    totals["rate"] = round(100 * totals["converted"] / past) if past else None
    return {"totals": totals, "activities": sorted((a for a in by_act.values() if a["trials"]),
                                                   key=lambda a: -a["trials"])}


@route("GET", "/api/staff/reports/trials", auth="staff", perm="reports.view")
def trials_api(h):
    q = h.query()
    period = q.get("period") if q.get("period") in PERIODS else "month"
    day = validate.date(q.get("date")) or catalogue.uk_today()
    with db.read() as c:
        first, last, label, _ = period_range(period, day, booking_settings.get("reporting_year_start_month", c))
        data = trials(c, first, last)
    return h.json(dict(data, label=label, first=first.isoformat(), last=last.isoformat(), convert_days=CONVERT_DAYS))
