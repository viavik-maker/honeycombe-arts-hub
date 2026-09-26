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


# ---------------------------------------------------------------- year on year (with MagicBooking history)

MAX_HISTORIC_ROWS = 2000


def _groups(c):
    return [r[0] for r in c.execute("SELECT DISTINCT report_group FROM activity_categories ORDER BY sort")] + ["Other"]


def parse_historic(c, text):
    """CSV text (month, category, attendances[, children]) → (rows, errors). Months as YYYY-MM or MM/YYYY."""
    import csv
    import io
    import re
    groups = {g.lower(): g for g in _groups(c)}
    rows, errors = [], []
    for n, rec in enumerate(csv.reader(io.StringIO(text or "")), 1):
        rec = [x.strip() for x in rec]
        if not any(rec):
            continue
        if n == 1 and rec[0].lower() in ("month", "date"):
            continue  # a header row
        if len(rec) < 3:
            errors.append("Line %d: needs month, category and attendances." % n)
            continue
        m = re.match(r"^(\d{4})-(\d{1,2})$", rec[0]) or re.match(r"^(\d{1,2})/(\d{4})$", rec[0])
        if not m:
            errors.append("Line %d: “%s” isn't a month (use 2025-08 or 08/2025)." % (n, rec[0][:20]))
            continue
        y, mo = (m.group(1), m.group(2)) if len(m.group(1)) == 4 else (m.group(2), m.group(1))
        if not 1 <= int(mo) <= 12 or not 2000 <= int(y) <= catalogue.uk_today().year:
            errors.append("Line %d: “%s” isn't a month we can use." % (n, rec[0][:20]))
            continue
        cat = groups.get(rec[1].lower())
        if not cat:
            errors.append("Line %d: “%s” isn't a category. Use one of: %s." % (n, rec[1][:40], ", ".join(groups.values())))
            continue
        try:
            att = int(rec[2])
            kids = int(rec[3]) if len(rec) > 3 and rec[3] != "" else None
            if att < 0 or (kids is not None and kids < 0):
                raise ValueError
        except ValueError:
            errors.append("Line %d: attendances (and children) must be whole numbers." % n)
            continue
        rows.append({"month": "%s-%02d" % (y, int(mo)), "category": cat, "attendances": att, "children": kids})
        if len(rows) > MAX_HISTORIC_ROWS:
            errors.append("That's more than %d rows — split it up." % MAX_HISTORIC_ROWS)
            break
    return rows, errors


@route("GET", "/api/staff/reports/historic", auth="staff", perm="reports.view")
def historic_list(h):
    with db.read() as c:
        rows = [dict(r) for r in c.execute("SELECT id, month, category, attendances, children, source FROM"
                                           " historic_attendance ORDER BY month DESC, category")]
        return h.json({"rows": rows, "categories": _groups(c)})


@route("POST", "/api/staff/reports/historic/import", auth="staff", perm="import.run", body_limit=512 * 1024)
def historic_import(h):
    d = h.json_body() or {}
    with db.tx() as c:
        rows, errors = parse_historic(c, d.get("csv"))
        if errors or not rows:
            return h.json({"error": "Nothing was saved — fix these first." if errors else "There's nothing to import.",
                           "problems": errors[:30]}, 422)
        existing = {(r[0], r[1]) for r in c.execute("SELECT month, category FROM historic_attendance")}
        replaced = sum(1 for r in rows if (r["month"], r["category"]) in existing)
        if not d.get("commit"):
            return h.json({"ok": True, "preview": rows[:24], "count": len(rows), "replaces": replaced,
                           "total": sum(r["attendances"] for r in rows)})
        for r in rows:
            c.execute("INSERT INTO historic_attendance(month, category, attendances, children, created_by, created_at)"
                      " VALUES (?,?,?,?,?,?) ON CONFLICT(month, category) DO UPDATE SET attendances=excluded.attendances,"
                      " children=excluded.children", (r["month"], r["category"], r["attendances"], r["children"],
                                                      h.staff()["id"], db.now()))
        audit.record(c, h, "report.historic_import", details={"rows": len(rows), "replaced": replaced})
    return h.json({"ok": True, "saved": len(rows), "replaced": replaced})


@route("POST", "/api/staff/reports/historic/<hid>/delete", auth="staff", perm="import.run")
def historic_delete(h, hid):
    with db.tx() as c:
        if not c.execute("DELETE FROM historic_attendance WHERE id=?", (int(hid) if hid.isdigit() else 0,)).rowcount:
            raise LookupError
        audit.record(c, h, "report.historic_delete", entity_type="historic_attendance", entity_id=int(hid))
    return h.json({"ok": True})


def year_on_year(c, years=3, today=None):
    """Attendances per month of each reporting year: registers plus MagicBooking history."""
    today = today or catalogue.uk_today()
    start_month = booking_settings.get("reporting_year_start_month", c)
    first, _, _, _ = period_range("year", today, start_month)
    out = []
    for i in range(years - 1, -1, -1):
        ystart = _add_months(first, -12 * i)
        yend = _add_months(ystart, 12) - datetime.timedelta(days=1)
        _, _, label, _ = period_range("year", ystart, start_month)
        keys = [_add_months(ystart, m).isoformat()[:7] for m in range(12)]
        live = dict(c.execute(
            "SELECT substr(s.date,1,7), SUM(CASE WHEN b.kind='party' THEN COALESCE(NULLIF(b.party_children,0), b.places)"
            " ELSE 1 END) FROM attendance a JOIN bookings b ON b.id=a.booking_id JOIN activity_sessions s"
            " ON s.id=a.session_id WHERE a.status='present' AND b.status='confirmed' AND s.date BETWEEN ? AND ?"
            " GROUP BY 1", (ystart.isoformat(), yend.isoformat())).fetchall())
        old = dict(c.execute("SELECT month, SUM(attendances) FROM historic_attendance WHERE month BETWEEN ? AND ?"
                             " GROUP BY month", (keys[0], keys[-1])).fetchall())
        months = [{"key": k, "registers": live.get(k, 0) or 0, "historic": old.get(k, 0) or 0} for k in keys]
        for m in months:
            m["total"] = m["registers"] + m["historic"]
            # months still to come aren't plotted; nor is this month until something's been recorded
            m["future"] = k_future(m["key"], today) or (m["key"] == today.isoformat()[:7] and not m["total"])
        out.append({"label": label, "start": ystart.isoformat(), "months": months,
                    "total": sum(m["total"] for m in months), "has_historic": any(m["historic"] for m in months)})
    return out


def k_future(key, today):
    return key > today.isoformat()[:7]


@route("GET", "/api/staff/reports/year-on-year", auth="staff", perm="reports.view")
def year_on_year_api(h):
    try:
        years = max(2, min(int(h.query().get("years") or 3), 6))
    except ValueError:
        years = 3
    with db.read() as c:
        return h.json({"years": year_on_year(c, years)})
