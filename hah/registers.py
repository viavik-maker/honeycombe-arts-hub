"""Registers: who's expected, signing in and out, and the HAF export.

Who can open what:
  * session staff: registers for today and tomorrow only (they see health
    flags and allergy details there, because they need them to keep children
    safe — every register view is audited);
  * anyone with people.view_health (managers, SEND lead, DSL…): any date.
Collection passwords are never shown or printed: staff type what the adult
says and the server checks it. After too many wrong tries the child's record
isn't locked — staff switch to the phone-verification procedure."""
import datetime

from . import audit, booking_settings, catalogue, db, family, ratelimit, security, validate
from .web import route

ratelimit.LIMITS.update({"collection_pw": (5, 12 * 60 * 60)})

FLAGS = ("allergy", "anaphylaxis", "medical", "dietary", "send", "semh", "religious")
RELEASE = {"password": "Gave the collection password", "known_adult_verified": "Known adult, checked by phone",
           "parent_stayed": "Parent/carer stayed", "went_home_alone": "Went home alone (with permission)",
           "other": "Other (see notes)"}


def _session(c, sid):
    s = c.execute("SELECT s.*, a.title, a.parent_must_stay, a.registration_level, a.haf_only, cen.name AS centre"
                  " FROM activity_sessions s JOIN activities a ON a.id=s.activity_id"
                  " JOIN centres cen ON cen.id=COALESCE(s.centre_id, a.centre_id) WHERE s.id=?", (int(sid),)).fetchone()
    if not s:
        raise LookupError
    return s


def may_open(h, date):
    """Session staff: today and tomorrow only."""
    if h.has_perm("people.view_health"):
        return True
    today = catalogue.uk_today()
    return today.isoformat() <= date <= (today + datetime.timedelta(days=1)).isoformat()


def _consent(c, account_id, pid, key):
    return family.current_consents(c, account_id, pid).get(key)


def to_discuss(c, pid, session_id):
    """Incidents to talk through with whoever collects the child."""
    return [dict(ref=r["ref"], kind=r["kind"]) for r in c.execute(
        "SELECT i.ref, i.kind FROM incidents i JOIN incident_people p ON p.incident_id=i.id WHERE p.participant_id=?"
        " AND i.notify_mode='at_collection' AND i.discussed_at IS NULL AND i.restricted=0"
        " AND (i.session_id=? OR substr(i.occurred_at,1,10)=?)",
        (pid, session_id, catalogue.uk_today().isoformat()))]


def person_row(c, p, session, show_haf):
    """What a register shows about one child (or young adult)."""
    health = c.execute("SELECT * FROM participant_health WHERE participant_id=?", (p["id"],)).fetchone()
    acct = c.execute("SELECT * FROM accounts WHERE id=?", (p["account_id"],)).fetchone()
    contacts = family.contacts(c, p["account_id"])
    months = catalogue.months_between(datetime.date.fromisoformat(p["dob"]), datetime.date.fromisoformat(session["date"]))
    alone = _consent(c, acct["id"], p["id"], "go_home_alone")
    return {
        "ref": p["ref"], "first_name": p["first_name"], "last_name": p["last_name"], "age": months // 12,
        "flags": {k: bool(p["f_" + k]) for k in FLAGS},
        "health": {k: health[k] for k in ("allergies", "anaphylaxis", "adrenaline_pen", "medical_conditions",
                                          "medication", "dietary", "send_needs", "semh_needs",
                                          "religious_requirements", "access_needs")} if health else None,
        "photo": p["photo_consent"], "first_aid": _consent(c, acct["id"], p["id"], "first_aid"),
        "plasters": _consent(c, acct["id"], p["id"], "plasters"),
        "go_home_alone": alone == "yes" and months // 12 >= booking_settings.get("go_home_alone_min_age", c),
        "collection_alert": p["collection_alert"], "has_collection_password": bool(p["collection_pw_hash"]),
        "needs_review": bool(p["needs_review"]), "level": p["level"],
        "haf": p["haf_status"] if show_haf else None,
        "parent": {"name": "%s %s" % (acct["first_name"], acct["last_name"]), "mobile": acct["mobile"]},
        "collectors": [x for x in contacts if x["can_collect"]],
        "contacts": contacts,
    }


def register_rows(c, h, session):
    show_haf = h.has_perm("bookings.manage")
    rows = []
    for b in c.execute("SELECT b.*, a.status AS att, a.signed_in_at, a.signed_out_at, a.release_method,"
                       " a.collected_by_name, a.collected_by_relationship, a.late, a.notes AS att_notes"
                       " FROM bookings b LEFT JOIN attendance a ON a.booking_id=b.id WHERE b.session_id=?"
                       " AND b.status='confirmed' ORDER BY b.id", (session["id"],)).fetchall():
        row = {"booking_id": b["id"], "ref": b["ref"], "kind": b["kind"], "status": b["att"] or "expected",
               "signed_in_at": b["signed_in_at"], "signed_out_at": b["signed_out_at"],
               "release_method": b["release_method"], "collected_by": b["collected_by_name"],
               "late": bool(b["late"]), "notes": b["att_notes"], "profile_incomplete": bool(b["profile_incomplete"]),
               "funding": b["funding"] if show_haf or b["funding"] != "haf" else "paid"}
        if b["participant_id"]:
            p = c.execute("SELECT * FROM participants WHERE id=?", (b["participant_id"],)).fetchone()
            row["person"] = person_row(c, p, session, show_haf)
            row["to_discuss"] = to_discuss(c, p["id"], session["id"])
            row["sort"] = (p["last_name"].lower(), p["first_name"].lower())
        else:
            kids = [person_row(c, p, session, show_haf) for p in c.execute(
                "SELECT p.* FROM participants p JOIN booking_party_children x ON x.participant_id=p.id"
                " WHERE x.booking_id=?", (b["id"],))]
            if b["guest_contact_id"]:
                g = c.execute("SELECT * FROM guest_contacts WHERE id=?", (b["guest_contact_id"],)).fetchone()
                contact = {"name": b["party_name"] or g["name"] or g["email"], "mobile": g["phone"]}
            else:
                a = c.execute("SELECT * FROM accounts WHERE id=?", (b["account_id"],)).fetchone()
                contact = {"name": "%s %s" % (a["first_name"], a["last_name"]), "mobile": a["mobile"]}
            row["party"] = {"contact": contact, "adults": b["party_adults"], "children": b["party_children"],
                            "places": b["places"], "named": kids}
            row["to_discuss"] = []
            row["sort"] = (contact["name"].lower(), "")
        rows.append(row)
    rows.sort(key=lambda r: r.pop("sort"))
    return rows


def session_summary(c, s):
    counts = {r[0]: r[1] for r in c.execute(
        "SELECT COALESCE(a.status,'expected'), COUNT(*) FROM bookings b LEFT JOIN attendance a ON a.booking_id=b.id"
        " WHERE b.session_id=? AND b.status='confirmed' GROUP BY 1", (s["id"],))}
    places = c.execute("SELECT COALESCE(SUM(places),0) FROM bookings WHERE session_id=? AND status='confirmed'",
                       (s["id"],)).fetchone()[0]
    out = c.execute("SELECT COUNT(*) FROM attendance a JOIN bookings b ON b.id=a.booking_id WHERE b.session_id=?"
                    " AND a.signed_out_at IS NOT NULL", (s["id"],)).fetchone()[0]
    return {"id": s["id"], "date": s["date"], "start_time": s["start_time"], "end_time": s["end_time"],
            "theme": s["theme"], "title": s["title"], "centre": s["centre"], "status": s["status"],
            "activity_id": s["activity_id"], "places": places, "bookings": sum(counts.values()),
            "present": counts.get("present", 0), "absent": counts.get("absent", 0) + counts.get("absent_notified", 0),
            "expected": counts.get("expected", 0), "signed_out": out, "capacity": s["capacity"]}


# ---------------------------------------------------------------- lists


@route("GET", "/api/staff/registers", auth="staff", perm="registers.view")
def registers(h):
    q = h.query()
    day = validate.date(q.get("date")) or catalogue.uk_today()
    days = max(1, min(int(q["days"]) if (q.get("days") or "").isdigit() else 1, 14))
    end = day + datetime.timedelta(days=days - 1)
    where, args = ["s.date BETWEEN ? AND ?"], [day.isoformat(), end.isoformat()]
    if (q.get("centre") or "").isdigit():
        where.append("COALESCE(s.centre_id, a.centre_id)=?")
        args.append(int(q["centre"]))
    if (q.get("activity") or "").isdigit():
        where.append("a.id=?")
        args.append(int(q["activity"]))
    with db.read() as c:
        rows = c.execute("SELECT s.*, a.title, cen.name AS centre FROM activity_sessions s JOIN activities a"
                         " ON a.id=s.activity_id JOIN centres cen ON cen.id=COALESCE(s.centre_id, a.centre_id)"
                         " WHERE " + " AND ".join(where) + " ORDER BY s.date, s.start_time, a.title", args).fetchall()
        sessions = [dict(session_summary(c, s), allowed=may_open(h, s["date"])) for s in rows]
        centres = [dict(r) for r in c.execute("SELECT id, name FROM centres WHERE active=1 ORDER BY name")]
    return h.json({"from": day.isoformat(), "to": end.isoformat(), "sessions": sessions, "centres": centres,
                   "today": catalogue.uk_today().isoformat()})


@route("GET", "/api/staff/registers/session/<sid>", auth="staff", perm="registers.view")
def register(h, sid):
    with db.tx() as c:
        s = _session(c, sid)
        if not may_open(h, s["date"]):
            return h.json({"error": "Session staff can open today's and tomorrow's registers only."}, 403)
        rows = register_rows(c, h, s)
        audit.record(c, h, "register.view", entity_type="session", entity_id=s["id"], details={"rows": len(rows)})
        return h.json({"session": dict(session_summary(c, s), parent_must_stay=bool(s["parent_must_stay"])),
                       "rows": rows, "release_methods": RELEASE,
                       "pending": c.execute("SELECT COUNT(*) FROM bookings WHERE session_id=? AND status IN"
                                            " ('pending_approval','pending_payment','offered')", (s["id"],)).fetchone()[0]})


@route("GET", "/api/staff/registers/day/<date>", auth="staff", perm="registers.view")
def combined(h, date):
    """Every session on a day in one list (optionally one centre)."""
    if not validate.date(date):
        raise LookupError
    if not may_open(h, date):
        return h.json({"error": "Session staff can open today's and tomorrow's registers only."}, 403)
    q = h.query()
    with db.tx() as c:
        out = []
        for s in c.execute("SELECT s.id FROM activity_sessions s JOIN activities a ON a.id=s.activity_id WHERE s.date=?"
                           " AND s.status='scheduled' AND (? = '' OR COALESCE(s.centre_id, a.centre_id)=?)"
                           " ORDER BY s.start_time", (date, q.get("centre") or "", q.get("centre") or "")).fetchall():
            sess = _session(c, s["id"])
            out.append({"session": session_summary(c, sess), "rows": register_rows(c, h, sess)})
        audit.record(c, h, "register.view_day", details={"date": date, "sessions": len(out)})
    return h.json({"date": date, "sessions": out, "release_methods": RELEASE})


@route("GET", "/api/staff/registers/week", auth="staff", perm="registers.view")
def week(h):
    """A grid of children × days for one activity over a week."""
    q = h.query()
    start = validate.date(q.get("start")) or catalogue.uk_today()
    start -= datetime.timedelta(days=start.weekday())
    end = start + datetime.timedelta(days=6)
    if not (q.get("activity") or "").isdigit():
        raise ValueError("Choose an activity.")
    with db.read() as c:
        sessions = c.execute("SELECT * FROM activity_sessions WHERE activity_id=? AND date BETWEEN ? AND ?"
                             " AND status='scheduled' ORDER BY date, start_time",
                             (int(q["activity"]), start.isoformat(), end.isoformat())).fetchall()
        people = {}
        for s in sessions:
            if not may_open(h, s["date"]):
                continue
            for r in c.execute("SELECT p.ref, p.first_name, p.last_name, COALESCE(a.status,'expected') AS st"
                               " FROM bookings b JOIN participants p ON p.id=b.participant_id"
                               " LEFT JOIN attendance a ON a.booking_id=b.id WHERE b.session_id=? AND b.status='confirmed'",
                               (s["id"],)):
                person = people.setdefault(r["ref"], {"name": "%s %s" % (r["first_name"], r["last_name"]), "days": {}})
                person["days"][str(s["id"])] = r["st"]
    return h.json({"start": start.isoformat(), "sessions": [dict(id=s["id"], date=s["date"], start_time=s["start_time"])
                                                             for s in sessions],
                   "people": sorted(people.values(), key=lambda p: p["name"].split()[-1].lower())})


# ---------------------------------------------------------------- signing in and out


def _row(c, bid):
    b = c.execute("SELECT * FROM bookings WHERE id=? AND status='confirmed'", (int(bid),)).fetchone()
    if not b:
        raise LookupError
    return b


@route("POST", "/api/staff/attendance/<bid>", auth="staff", perm="registers.mark")
def mark(h, bid):
    d = h.json_body() or {}
    action = d.get("action")
    staff = h.staff()
    with db.tx() as c:
        b = _row(c, bid)
        s = _session(c, b["session_id"])
        if not may_open(h, s["date"]):
            return h.json({"error": "Session staff can mark today's and tomorrow's registers only."}, 403)
        from . import bookings
        bookings.add_attendance(c, b)
        att = c.execute("SELECT * FROM attendance WHERE booking_id=?", (b["id"],)).fetchone()
        now = db.now()
        if action == "in":
            c.execute("UPDATE attendance SET status='present', signed_in_at=COALESCE(signed_in_at, ?), signed_in_by=?,"
                      " late=?, arrived_count=?, updated_at=? WHERE id=?",
                      (now, staff["id"], 1 if d.get("late") else 0, d.get("arrived_count"), now, att["id"]))
        elif action == "absent":
            if att["signed_in_at"]:
                raise ValueError("They've been signed in — undo that first.")
            c.execute("UPDATE attendance SET status=CASE WHEN status='absent_notified' THEN status ELSE 'absent' END,"
                      " updated_at=? WHERE id=?", (now, att["id"]))
        elif action == "undo":
            if att["signed_out_at"]:
                c.execute("UPDATE attendance SET signed_out_at=NULL, signed_out_by=NULL, release_method=NULL,"
                          " collected_by_name=NULL, collected_by_relationship=NULL, updated_at=? WHERE id=?", (now, att["id"]))
            else:
                c.execute("UPDATE attendance SET status='expected', signed_in_at=NULL, signed_in_by=NULL, late=0,"
                          " updated_at=? WHERE id=?", (now, att["id"]))
        elif action == "out":
            problem = _sign_out(c, h, b, s, att, d)
            if problem:
                return h.json(problem, 409 if problem.get("to_discuss") else 400)
        elif action == "note":
            c.execute("UPDATE attendance SET notes=?, updated_at=? WHERE id=?",
                      (validate.long_text(d.get("notes"), 500) or None, now, att["id"]))
        else:
            raise ValueError("Unknown register action.")
        audit.record(c, h, "attendance.%s" % action, entity_type="booking", entity_id=b["id"],
                     participant_id=b["participant_id"],
                     details={"method": d.get("method")} if action == "out" else None)
        att = c.execute("SELECT * FROM attendance WHERE booking_id=?", (b["id"],)).fetchone()
    return h.json({"ok": True, "status": att["status"], "signed_in_at": att["signed_in_at"],
                   "signed_out_at": att["signed_out_at"], "release_method": att["release_method"]})


def _sign_out(c, h, b, s, att, d):
    """Returns a problem dict, or None once signed out."""
    if not att["signed_in_at"]:
        return {"error": "Sign them in first."}
    method = d.get("method")
    if method not in RELEASE:
        return {"error": "Choose how they're being collected."}
    p = c.execute("SELECT * FROM participants WHERE id=?", (b["participant_id"],)).fetchone() if b["participant_id"] else None
    if p:
        pending = to_discuss(c, p["id"], s["id"])
        if pending and not d.get("incident_discussed"):
            return {"error": "There's an incident to talk through with the adult collecting %s before they go."
                             % p["first_name"], "to_discuss": pending}
    name = validate.text(d.get("collected_by_name"), 100)
    if method == "password":
        if not p or not p["collection_pw_hash"]:
            return {"error": "There's no collection password for this child — check the adult by phone instead."}
        key = "p%d" % p["id"]
        if ratelimit.blocked("collection_pw", key):
            return {"error": "Too many wrong passwords. Use the phone-verification procedure: call the parent on the "
                             "number in their record, then choose “Known adult, checked by phone”."}
        if not security.verify_collection_password(d.get("password") or "", p["collection_pw_hash"]):
            ratelimit.hit("collection_pw", key)
            audit.record(c, h, "attendance.password_wrong", entity_type="booking", entity_id=b["id"],
                         participant_id=p["id"])
            return {"error": "That isn't the collection password.", "wrong_password": True}
        if not name:
            return {"error": "Enter the name of the adult collecting."}
    elif method == "known_adult_verified" and not name:
        return {"error": "Enter the name of the adult you checked."}
    elif method == "went_home_alone":
        months = catalogue.months_between(datetime.date.fromisoformat(p["dob"]), catalogue.uk_today()) if p else 0
        allowed = p and family.current_consents(c, p["account_id"], p["id"]).get("go_home_alone") == "yes" and \
            months // 12 >= booking_settings.get("go_home_alone_min_age", c)
        if not allowed:
            return {"error": "There's no permission for them to go home alone."}
    elif method == "parent_stayed" and not s["parent_must_stay"] and b["kind"] != "party":
        return {"error": "This isn't a stay-and-play session — choose another way."}
    now = db.now()
    c.execute("UPDATE attendance SET signed_out_at=?, signed_out_by=?, release_method=?, collected_by_name=?,"
              " collected_by_relationship=?, incident_discussed=?, updated_at=? WHERE id=?",
              (now, h.staff()["id"], method, name or None, validate.text(d.get("collected_by_relationship"), 60) or None,
               1 if d.get("incident_discussed") else None, now, att["id"]))
    if p and d.get("incident_discussed"):
        c.execute("UPDATE incidents SET discussed_at=?, discussed_by=?, parent_notified_at=COALESCE(parent_notified_at, ?),"
                  " updated_at=? WHERE notify_mode='at_collection' AND discussed_at IS NULL AND id IN"
                  " (SELECT incident_id FROM incident_people WHERE participant_id=?)",
                  (now, h.staff()["id"], now, now, p["id"]))
    return None


# ---------------------------------------------------------------- printing


@route("GET", "/admin/registers/<sid>/print", auth="staff", perm="registers.view")
def print_register(h, sid):
    """A paper copy for when the tablet isn't available. Never includes
    collection passwords or safeguarding information."""
    from .markup import html as esc
    from .web import csp, csp_header
    with db.tx() as c:
        s = _session(c, sid)
        if not may_open(h, s["date"]):
            return h.send(403, b"Not allowed", "text/plain")
        rows = register_rows(c, h, s)
        audit.record(c, h, "register.print", entity_type="session", entity_id=s["id"])
    body = []
    for r in rows:
        if "person" in r:
            p = r["person"]
            hl = p["health"] or {}
            needs = "; ".join(x for x in (
                ("ALLERGY: " + hl["allergies"]) if hl.get("allergies") else "",
                "ANAPHYLAXIS" + (" (pen)" if hl.get("adrenaline_pen") else "") if hl.get("anaphylaxis") else "",
                ("Medical: " + hl["medical_conditions"]) if hl.get("medical_conditions") else "",
                ("Diet: " + hl["dietary"]) if hl.get("dietary") else "",
                "SEND" if p["flags"]["send"] else "", "SEMH" if p["flags"]["semh"] else "") if x)
            who = "%s %s (%d)" % (p["first_name"], p["last_name"], p["age"])
            collect = ", ".join("%s (%s) %s" % (x["full_name"], x["relationship"], x["phone"]) for x in p["collectors"])
            extra = []
            if p["collection_alert"]:
                extra.append("<strong>COLLECTION ALERT — see manager</strong>")
            if p["go_home_alone"]:
                extra.append("May go home alone")
            photo = {"online": "Photos OK", "internal": "Photos: internal only", "none": "NO PHOTOS"}.get(p["photo"], "Photos: ?")
            body.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s<br>Parent: %s %s</td><td></td><td></td></tr>" % (
                esc(who), esc(needs) or "—", esc(photo) + ("<br>" + "<br>".join(extra) if extra else ""),
                esc(collect) or "—", esc(p["parent"]["name"]), esc(p["parent"]["mobile"] or "")))
        else:
            pa = r["party"]
            body.append("<tr><td>%s — %d place(s): %d adult(s), %d child(ren)</td><td>%s</td><td></td><td>%s</td>"
                        "<td></td><td></td></tr>" % (
                            esc(pa["contact"]["name"]), pa["places"], pa["adults"], pa["children"],
                            esc("; ".join("%s: %s" % (k["first_name"], (k["health"] or {}).get("allergies") or "—")
                                          for k in pa["named"])) or "—", esc(pa["contact"]["mobile"] or "")))
    nonce = h.new_nonce()
    page = """<!DOCTYPE html><html lang="en-GB"><head><meta charset="utf-8"><meta name="robots" content="noindex">
<title>Register — %s %s</title><style>body{font-family:system-ui,sans-serif;margin:16px;font-size:12px}
table{border-collapse:collapse;width:100%%}th,td{border:1px solid #444;padding:4px 6px;vertical-align:top;text-align:left}
th{background:#eee}h1{font-size:18px;margin:0 0 4px}.no-print{margin-bottom:10px}@media print{.no-print{display:none}}</style></head>
<body><p class="no-print"><button id="p">Print</button> Confidential — keep with you and shred after use.</p>
<h1>%s — %s %s–%s%s</h1><p>%s · %d booked · printed %s</p>
<table><thead><tr><th>Name (age)</th><th>Health &amp; needs</th><th>Permissions</th><th>Who can collect</th>
<th style="width:9%%">In</th><th style="width:14%%">Out / collected by</th></tr></thead><tbody>%s</tbody></table>
<script nonce="%s">document.getElementById("p").onclick=function(){print()}</script></body></html>""" % (
        esc(s["title"]), esc(s["date"]), esc(s["title"]), esc(catalogue.nice_date(s["date"])), esc(s["start_time"]),
        esc(s["end_time"]), (" · " + esc(s["theme"])) if s["theme"] else "", esc(s["centre"]), len(rows),
        esc(catalogue.uk_now().strftime("%-d %b %Y %H:%M")), "".join(body), nonce)
    return h.send(200, page.encode(), "text/html; charset=utf-8",
                  {"Cache-Control": "no-store", "X-Robots-Tag": "noindex", csp_header(): csp(nonce)})


# ---------------------------------------------------------------- HAF export for BCP Council


@route("GET", "/api/staff/reports/haf.csv", auth="staff", perm="reports.view")
def haf_export(h):
    """One row per HAF child: who they are and which days they attended.
    (Shared with BCP Council under the HAF grant agreement — not consent.)"""
    q = h.query()
    start = validate.date(q.get("from")) or catalogue.uk_today().replace(month=1, day=1)
    end = validate.date(q.get("to")) or catalogue.uk_today()
    with db.tx() as c:
        people = {}
        for r in c.execute(
                "SELECT p.*, s.date, COALESCE(a.status,'expected') AS att, acc.postcode, act.title"
                " FROM bookings b JOIN participants p ON p.id=b.participant_id JOIN activity_sessions s ON s.id=b.session_id"
                " JOIN activities act ON act.id=b.activity_id JOIN accounts acc ON acc.id=p.account_id"
                " LEFT JOIN attendance a ON a.booking_id=b.id WHERE b.funding='haf' AND b.status='confirmed'"
                " AND s.date BETWEEN ? AND ? ORDER BY p.last_name, p.first_name, s.date",
                (start.isoformat(), end.isoformat())):
            e = people.setdefault(r["id"], {"row": r, "booked": 0, "attended": [], "activities": set()})
            e["booked"] += 1
            e["activities"].add(r["title"])
            if r["att"] == "present":
                e["attended"].append(r["date"])
        audit.record(c, h, "report.haf_export", details={"from": start.isoformat(), "to": end.isoformat(),
                                                         "children": len(people)})
    rows = []
    for e in people.values():
        p = e["row"]
        rows.append([p["first_name"], p["last_name"], p["dob"], family.age_years(p["dob"]), p["gender"] or "",
                     p["school_name"] or ("Home educated" if p["education"] == "home_educated" else ""),
                     p["postcode"] or "", "Yes" if p["f_send"] else "No", p["haf_status"], "; ".join(sorted(e["activities"])),
                     e["booked"], len(e["attended"]), " ".join(e["attended"])])
    return h.csv("haf-%s-to-%s.csv" % (start, end), ["First name", "Last name", "Date of birth", "Age", "Gender",
                                                      "School", "Postcode", "SEND", "HAF status", "Activities",
                                                      "Days booked", "Days attended", "Dates attended"], rows)
