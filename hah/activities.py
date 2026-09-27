"""Admin → Activities: the catalogue staff build and publish.

Lifecycle: draft → scheduled (goes live at publish_at) → published ⇄
unpublished → archived. "Past" is worked out from the last session date.
Publishing is refused, with the reasons, until an activity can really be
booked (it has upcoming sessions with places, and a way to pay)."""
import datetime
import re

from . import audit, booking_settings, catalogue, db, payments_stripe, validate, worker
from .validate import Invalid
from .web import route

STATUSES = ("draft", "scheduled", "published", "unpublished", "archived")
LEVELS = {"guest": "One-off event (no account needed)", "short": "Baby & toddler (short form, parent stays)",
          "full": "Holiday club / drop-off (full form)", "adult": "Young adults 18+ (adult form)"}
TRANSITIONS = {
    "draft": {"scheduled", "published", "archived"},
    "scheduled": {"draft", "published", "unpublished", "archived"},
    "published": {"unpublished", "archived"},
    "unpublished": {"published", "scheduled", "draft", "archived"},
    "archived": {"unpublished"},
}
MAX_GENERATED = 200
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# ---------------------------------------------------------------- reading


def _activity(c, aid):
    a = c.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
    if not a:
        raise LookupError
    return a


def _editable(a):
    if a["status"] == "archived":
        raise ValueError("Archived activities can't be edited — restore it first.")
    return a


def _local(utc):
    """UTC ISO → 'YYYY-MM-DDTHH:MM' UK local, for datetime-local inputs."""
    if not utc:
        return ""
    return catalogue.parse_utc(utc).astimezone(catalogue.UK).strftime("%Y-%m-%dT%H:%M")


def _from_local(value, field):
    v = (value or "").strip()
    if not v:
        return None
    try:
        dt = datetime.datetime.fromisoformat(v)
    except ValueError:
        raise Invalid({field: "Enter a date and time."})
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=catalogue.UK)
    return catalogue.utc_iso(dt)


def session_json(c, a, s, today=None):
    today = today or catalogue.uk_today().isoformat()
    taken = catalogue.places_taken(c, s["id"])
    return {"id": s["id"], "date": s["date"], "start_time": s["start_time"], "end_time": s["end_time"],
            "theme": s["theme"], "capacity": s["capacity"], "price_pence": s["price_pence"],
            "effective_price_pence": catalogue.price_of(a, s), "centre_id": s["centre_id"], "status": s["status"],
            "cancelled_reason": s["cancelled_reason"], "staff_notes": s["staff_notes"], "taken": taken,
            "waiting": catalogue.waitlist_count(c, s["id"]), "past": s["date"] < today,
            "ever_booked": c.execute("SELECT 1 FROM bookings WHERE session_id=? LIMIT 1", (s["id"],)).fetchone()
            is not None}


def activity_json(c, a, with_sessions=True):
    last = c.execute("SELECT MAX(date) FROM activity_sessions WHERE activity_id=?", (a["id"],)).fetchone()[0]
    out = {k: a[k] for k in a.keys()}
    out.update(derived_status=catalogue.derived_status(a, last), publish_at_local=_local(a["publish_at"]),
               booking_opens_at_local=_local(a["booking_opens_at"]), age_text=catalogue.age_range_text(a),
               last_date=last)
    if with_sessions:
        out["sessions"] = [session_json(c, a, s) for s in c.execute(
            "SELECT * FROM activity_sessions WHERE activity_id=? ORDER BY date, start_time", (a["id"],))]
    return out


@route("GET", "/api/staff/activities/meta", auth="staff", perm="activities.view")
def meta(h):
    with db.read() as c:
        cats = [dict(r) for r in c.execute("SELECT * FROM activity_categories ORDER BY sort, name")]
        centres = [dict(r) for r in c.execute("SELECT * FROM centres ORDER BY active DESC, name")]
    return h.json({"categories": cats, "centres": centres, "levels": LEVELS, "statuses": STATUSES,
                   "card_payments": payments_stripe.configured(),
                   "bank_holidays": booking_settings.get("bank_holidays")})


@route("GET", "/api/staff/activities", auth="staff", perm="activities.view")
def list_activities(h):
    q = h.query()
    tab, text = q.get("tab", "current"), (q.get("q") or "").strip().lower()
    today = catalogue.uk_today().isoformat()
    rows = []
    with db.read() as c:
        for a in c.execute("SELECT a.*, cat.name AS category, cen.name AS centre FROM activities a"
                           " JOIN activity_categories cat ON cat.id=a.category_id JOIN centres cen ON cen.id=a.centre_id"
                           " ORDER BY a.title"):
            if q.get("category") and str(a["category_id"]) != q["category"]:
                continue
            if q.get("centre") and str(a["centre_id"]) != q["centre"]:
                continue
            if text and text not in a["title"].lower() and text not in a["slug"]:
                continue
            st = c.execute("SELECT MIN(CASE WHEN date>=? AND status='scheduled' THEN date END) AS next_date,"
                           " MAX(date) AS last_date, COUNT(*) AS n,"
                           " SUM(CASE WHEN date>=? AND status='scheduled' THEN capacity ELSE 0 END) AS cap"
                           " FROM activity_sessions WHERE activity_id=?", (today, today, a["id"])).fetchone()
            booked = c.execute("SELECT COALESCE(SUM(b.places),0) FROM bookings b JOIN activity_sessions s"
                               " ON s.id=b.session_id WHERE b.activity_id=? AND s.date>=? AND s.status='scheduled'"
                               " AND b.status IN " + catalogue.HOLDING_SQL, (a["id"], today)).fetchone()[0]
            waiting = c.execute("SELECT COUNT(*) FROM bookings WHERE activity_id=? AND status='waitlisted'",
                                (a["id"],)).fetchone()[0]
            derived = catalogue.derived_status(a, st["last_date"])
            if tab == "current" and derived in ("past", "archived"):
                continue
            if tab not in ("current", "all") and derived != tab:
                continue
            rows.append({"id": a["id"], "title": a["title"], "slug": a["slug"], "status": a["status"],
                         "derived_status": derived, "category": a["category"], "centre": a["centre"],
                         "next_date": st["next_date"], "last_date": st["last_date"], "sessions": st["n"],
                         "capacity": st["cap"] or 0, "booked": booked, "waiting": waiting,
                         "registration_level": a["registration_level"], "age_text": catalogue.age_range_text(a),
                         "publish_at": a["publish_at"]})
    return h.json({"activities": rows})


@route("GET", "/api/staff/activities/<aid>", auth="staff", perm="activities.view")
def get_activity(h, aid):
    if not aid.isdigit():
        return h.json({"error": "not found"}, 404)
    with db.read() as c:
        try:
            return h.json({"activity": activity_json(c, _activity(c, int(aid)))})
        except LookupError:
            return h.json({"error": "not found"}, 404)


# ---------------------------------------------------------------- editing

BOOL_FIELDS = ("requires_approval", "parent_must_stay", "haf_only", "waitlist_enabled", "allow_pay_later",
               "allow_trial", "age_by_school_year")
INT_FIELDS = {"min_age_months": (0, 1200), "max_age_months": (0, 1200), "price_pence": (0, 100000),
              "adult_price_pence": (0, 100000), "capacity_default": (0, 500), "max_party_size": (1, 30),
              "booking_closes_hours": (0, 720)}


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60].strip("-") or "activity"


def unique_slug(c, base, exclude_id=None):
    slug, n = base, 1
    while c.execute("SELECT 1 FROM activities WHERE slug=? AND id IS NOT ?", (slug, exclude_id)).fetchone():
        n += 1
        slug = "%s-%d" % (base, n)
    return slug


def clean_activity(c, d, current=None):
    """Checked values for the columns staff may set. Raises Invalid."""
    errors, out = {}, {}
    get = (lambda k: d[k] if k in d else (current[k] if current else None))
    title = validate.text(get("title"), 120)
    if not title:
        errors["title"] = "Enter a title."
    out["title"] = title
    for key, table in (("category_id", "activity_categories"), ("centre_id", "centres")):
        v = get(key)
        try:
            v = int(v)
        except (TypeError, ValueError):
            v = None
        if not v or not c.execute("SELECT 1 FROM %s WHERE id=?" % table, (v,)).fetchone():
            errors[key] = "Choose one."
        out[key] = v
    out["summary"] = validate.text(get("summary"), 300) or None
    out["description"] = validate.long_text(get("description"), 5000) or None
    image = validate.text(get("image"), 300)
    if image and not re.match(r"^(/uploads/|/img/)[\w./-]+$", image):
        errors["image"] = "Choose an image from the site's uploads."
    out["image"] = image or None
    out["event_id"] = validate.text(get("event_id"), 80) or None
    for k, (lo, hi) in INT_FIELDS.items():
        v = get(k)
        try:
            v = int(v if v not in (None, "") else (current[k] if current else {"max_age_months": 1200, "max_party_size": 8,
                                                                               "capacity_default": 20}.get(k, 0)))
        except (TypeError, ValueError):
            errors[k] = "Enter a whole number."
            continue
        if not lo <= v <= hi:
            errors[k] = "Enter a number from %d to %d." % (lo, hi)
        out[k] = v
    if "min_age_months" in out and "max_age_months" in out and out["min_age_months"] > out["max_age_months"]:
        errors["max_age_months"] = "The oldest age must be at least the youngest."
    for k in BOOL_FIELDS:
        out[k] = 1 if get(k) else 0
    level = get("registration_level") or "full"
    if level not in LEVELS:
        errors["registration_level"] = "Choose which form families fill in."
    out["registration_level"] = level
    if level == "guest":
        out["parent_must_stay"] = 1
    basis = get("age_basis")
    if basis not in ("session_date", "first_session"):
        # term blocks (Home Ed) are booked as a whole, so ages are checked once, at the start; holiday clubs and
        # weekly classes check each session's date
        basis = "first_session" if c.execute("SELECT 1 FROM activity_categories WHERE id=? AND key='home_ed'",
                                             (out["category_id"],)).fetchone() else "session_date"
    out["age_basis"] = basis
    out["capacity_counts"] = get("capacity_counts") if get("capacity_counts") in ("children", "all_people") else "children"
    out["waitlist_mode"] = get("waitlist_mode") if get("waitlist_mode") in ("auto_offer", "manual") else "auto_offer"
    allowance = get("haf_allowance_days")
    if allowance in (None, ""):
        out["haf_allowance_days"] = None
    else:
        try:
            out["haf_allowance_days"] = int(allowance)
            if not 1 <= out["haf_allowance_days"] <= 60:
                raise ValueError
        except (TypeError, ValueError):
            errors["haf_allowance_days"] = "Enter a number of days from 1 to 60, or leave it empty."
    tp = get("trial_price_pence")
    if tp in (None, ""):
        out["trial_price_pence"] = None
    else:
        try:
            out["trial_price_pence"] = int(tp)
            if not 0 <= out["trial_price_pence"] <= 100000:
                raise ValueError
        except (TypeError, ValueError):
            errors["trial_price_pence"] = "Enter a trial price, or leave it empty to charge the normal price."
    if out.get("haf_only") and out.get("price_pence"):
        errors["price_pence"] = "HAF places are free — set the price to £0."
    try:
        out["booking_opens_at"] = _from_local(d["booking_opens_at_local"], "booking_opens_at_local") \
            if "booking_opens_at_local" in d else (current["booking_opens_at"] if current else None)
    except Invalid as e:
        errors.update(e.errors)
    if errors:
        raise Invalid(errors)
    return out


@route("POST", "/api/staff/activities", auth="staff", perm="activities.manage")
def create_activity(h):
    d = h.json_body() or {}
    with db.tx() as c:
        v = clean_activity(c, d)
        v["slug"] = unique_slug(c, slugify(d.get("slug") or v["title"]))
        v.update(status="draft", created_by=h.staff()["id"], created_at=db.now(), updated_at=db.now())
        cols = sorted(v)
        aid = c.execute("INSERT INTO activities(%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                        [v[k] for k in cols]).lastrowid
        audit.record(c, h, "activity.create", entity_type="activity", entity_id=aid)
        return h.json({"ok": True, "activity": activity_json(c, _activity(c, aid))})


@route("POST", "/api/staff/activities/<aid>/update", auth="staff", perm="activities.manage")
def update_activity(h, aid):
    d = h.json_body() or {}
    with db.tx() as c:
        a = _editable(_activity(c, int(aid)))
        v = clean_activity(c, d, a)
        if d.get("slug") and a["status"] == "draft":
            v["slug"] = unique_slug(c, slugify(d["slug"]), exclude_id=a["id"])
        changed = sorted(k for k in v if v[k] != a[k])
        if changed:
            v["updated_at"] = db.now()
            c.execute("UPDATE activities SET %s WHERE id=?" % ",".join("%s=?" % k for k in v),
                      [*v.values(), a["id"]])
            audit.record(c, h, "activity.update", entity_type="activity", entity_id=a["id"],
                         details={"changed": changed})
        return h.json({"ok": True, "activity": activity_json(c, _activity(c, a["id"]))})


def publish_problems(c, a):
    """Why this activity can't go live yet (empty list = it can)."""
    today = catalogue.uk_today().isoformat()
    problems = []
    upcoming = c.execute("SELECT * FROM activity_sessions WHERE activity_id=? AND date>=? AND status='scheduled'",
                         (a["id"], today)).fetchall()
    if not upcoming:
        problems.append("Add at least one upcoming session.")
    elif not any(s["capacity"] > 0 for s in upcoming):
        problems.append("Every upcoming session has 0 places.")
    paid = any(catalogue.price_of(a, s) > 0 for s in upcoming)
    if paid and not payments_stripe.configured() and not a["allow_pay_later"]:
        problems.append("It has a price, but card payments aren't set up yet (ask your web developer to add the "
                        "Stripe keys) and paying later isn't allowed for it.")
    if a["haf_only"] and a["registration_level"] not in ("full", "adult"):
        problems.append("HAF activities need the full registration form.")
    return problems


@route("POST", "/api/staff/activities/<aid>/status", auth="staff", perm="activities.manage")
def set_status(h, aid):
    d = h.json_body() or {}
    to = d.get("to")
    with db.tx() as c:
        a = _activity(c, int(aid))
        if to not in TRANSITIONS.get(a["status"], ()):
            raise ValueError("An activity that's %s can't be made %s." % (a["status"], to))
        fields = {"status": to, "updated_at": db.now()}
        if to in ("published", "scheduled"):
            problems = publish_problems(c, a)
            if problems:
                return h.json({"error": "It can't go live yet.", "problems": problems}, 409)
        if to == "scheduled":
            at = _from_local(d.get("publish_at_local"), "publish_at_local")
            if not at or at <= db.now():
                raise Invalid({"publish_at_local": "Choose a date and time in the future."})
            fields["publish_at"] = at
        elif to == "published":
            fields["publish_at"] = db.now()
        fields["archived_at"] = db.now() if to == "archived" else None
        c.execute("UPDATE activities SET %s WHERE id=?" % ",".join("%s=?" % k for k in fields),
                  [*fields.values(), a["id"]])
        audit.record(c, h, "activity.status", entity_type="activity", entity_id=a["id"],
                     details={"from": a["status"], "to": to})
        return h.json({"ok": True, "activity": activity_json(c, _activity(c, a["id"]))})


@worker.job("publish_scheduled", every=60, timeout=60)
def publish_scheduled():
    """Scheduled activities go live at their publish time (if still ready)."""
    published = held = 0
    with db.tx() as c:
        for a in c.execute("SELECT * FROM activities WHERE status='scheduled' AND publish_at<=?",
                           (db.now(),)).fetchall():
            problems = publish_problems(c, a)
            if problems:
                from . import intray
                intray.add(c, "publish_blocked", "“%s” couldn't go live: %s" % (a["title"], problems[0]),
                           perm="activities.manage", entity_type="activity", entity_id=a["id"], activity_id=a["id"])
                held += 1
                continue
            c.execute("UPDATE activities SET status='published', updated_at=? WHERE id=?", (db.now(), a["id"]))
            audit.record(c, None, "activity.status", entity_type="activity", entity_id=a["id"],
                         details={"from": "scheduled", "to": "published"})
            published += 1
    return "published %d, held %d" % (published, held)


# ---------------------------------------------------------------- sessions


def clean_session(c, a, d, current=None):
    errors, out = {}, {}
    get = (lambda k: d[k] if k in d else (current[k] if current else None))
    day = validate.date(get("date"))
    if not day:
        errors["date"] = "Enter a date."
    out["date"] = day.isoformat() if day else None
    for k in ("start_time", "end_time"):
        v = (get(k) or "").strip()[:5]
        if not TIME_RE.match(v):
            errors[k] = "Enter a time like 09:30."
        out[k] = v
    if not errors.get("start_time") and not errors.get("end_time") and out["start_time"] >= out["end_time"]:
        errors["end_time"] = "The session must end after it starts."
    out["theme"] = validate.text(get("theme"), 120) or None
    out["staff_notes"] = validate.long_text(get("staff_notes"), 1000) or None
    try:
        out["capacity"] = int(get("capacity") if get("capacity") not in (None, "") else a["capacity_default"])
        if not 0 <= out["capacity"] <= 500:
            raise ValueError
    except (TypeError, ValueError):
        errors["capacity"] = "Enter a number of places from 0 to 500."
    price = get("price_pence")
    if price in (None, ""):
        out["price_pence"] = None
    else:
        try:
            out["price_pence"] = int(price)
            if not 0 <= out["price_pence"] <= 100000:
                raise ValueError
        except (TypeError, ValueError):
            errors["price_pence"] = "Enter a price, or leave it empty to use the activity's price."
    centre = get("centre_id")
    out["centre_id"] = int(centre) if str(centre or "").isdigit() and c.execute(
        "SELECT 1 FROM centres WHERE id=?", (int(centre),)).fetchone() else None
    if errors:
        raise Invalid(errors)
    return out


def _session(c, sid):
    s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        raise LookupError
    return s


@route("POST", "/api/staff/activities/<aid>/sessions", auth="staff", perm="activities.manage")
def add_session(h, aid):
    d = h.json_body() or {}
    with db.tx() as c:
        a = _editable(_activity(c, int(aid)))
        v = clean_session(c, a, d)
        if c.execute("SELECT 1 FROM activity_sessions WHERE activity_id=? AND date=? AND start_time=?",
                     (a["id"], v["date"], v["start_time"])).fetchone():
            raise Invalid({"date": "There's already a session at that date and time."})
        cols = sorted(v)
        sid = c.execute("INSERT INTO activity_sessions(activity_id, created_at, %s) VALUES (?,?,%s)"
                        % (",".join(cols), ",".join("?" * len(cols))), [a["id"], db.now(), *[v[k] for k in cols]]).lastrowid
        audit.record(c, h, "session.create", entity_type="session", entity_id=sid)
        return h.json({"ok": True, "session": session_json(c, a, _session(c, sid))})


@route("POST", "/api/staff/sessions/<sid>/update", auth="staff", perm="activities.manage")
def update_session(h, sid):
    d = h.json_body() or {}
    with db.tx() as c:
        s = _session(c, int(sid))
        a = _editable(_activity(c, s["activity_id"]))
        v = clean_session(c, a, d, s)
        taken = catalogue.places_taken(c, s["id"])
        if v["capacity"] < taken:
            raise Invalid({"capacity": "%d place%s already taken — cancel or move bookings first."
                           % (taken, "" if taken == 1 else "s")})
        # families booked on the old date and time would never be told (nor ages and clashes checked again)
        if (v["date"], v["start_time"], v["end_time"]) != (s["date"], s["start_time"], s["end_time"]) and c.execute(
                "SELECT 1 FROM bookings WHERE session_id=? AND status IN ('pending_payment','pending_approval','offered',"
                "'confirmed','pending_confirmation','waitlisted') LIMIT 1", (s["id"],)).fetchone():
            raise ValueError("This session has bookings, so its date and time can't be changed — move the bookings "
                             "to another session, or cancel this one (families are told) and add a new one.")
        if (v["date"], v["start_time"]) != (s["date"], s["start_time"]) and c.execute(
                "SELECT 1 FROM activity_sessions WHERE activity_id=? AND date=? AND start_time=? AND id<>?",
                (a["id"], v["date"], v["start_time"], s["id"])).fetchone():
            raise Invalid({"date": "There's already a session at that date and time."})
        changed = sorted(k for k in v if v[k] != s[k])
        if changed:
            c.execute("UPDATE activity_sessions SET %s WHERE id=?" % ",".join("%s=?" % k for k in v),
                      [*v.values(), s["id"]])
            audit.record(c, h, "session.update", entity_type="session", entity_id=s["id"], details={"changed": changed})
            if v["capacity"] > s["capacity"]:
                from . import waitlist
                waitlist.places_freed(c, s["id"])
        return h.json({"ok": True, "session": session_json(c, a, _session(c, s["id"]))})


@route("POST", "/api/staff/sessions/<sid>/delete", auth="staff", perm="activities.manage")
def delete_session(h, sid):
    with db.tx() as c:
        s = _session(c, int(sid))
        _editable(_activity(c, s["activity_id"]))
        if c.execute("SELECT 1 FROM bookings WHERE session_id=? LIMIT 1", (s["id"],)).fetchone():
            raise ValueError("This session has had bookings, so it can't be deleted — cancel it instead.")
        c.execute("DELETE FROM activity_sessions WHERE id=?", (s["id"],))
        audit.record(c, h, "session.delete", entity_type="session", entity_id=s["id"],
                     details={"activity_id": s["activity_id"], "date": s["date"]})
    return h.json({"ok": True})


def bank_holidays(c=None):
    """The bank holiday list from Booking settings, as YYYY-MM-DD (older saves may not be)."""
    return {x.isoformat() for x in (validate.date(v) for v in booking_settings.get("bank_holidays", c)) if x}


def generate_dates(start, end, weekdays, skip):
    """Dates from START to END (inclusive) on WEEKDAYS (0=Mon), minus SKIP."""
    day, out = start, []
    while day <= end and len(out) <= MAX_GENERATED:
        if day.weekday() in weekdays and day.isoformat() not in skip:
            out.append(day)
        day += datetime.timedelta(days=1)
    return out


@route("POST", "/api/staff/activities/<aid>/sessions/generate", auth="staff", perm="activities.manage")
def generate_sessions(h, aid):
    """Make a run of sessions: every chosen weekday between two dates, skipping
    listed dates (and bank holidays if asked). dry_run returns the preview."""
    d = h.json_body() or {}
    errors = {}
    start, end = validate.date(d.get("from")), validate.date(d.get("to"))
    if not start:
        errors["from"] = "Enter the first date."
    if not end:
        errors["to"] = "Enter the last date."
    if start and end and end < start:
        errors["to"] = "The last date must be on or after the first."
    if start and end and (end - start).days > 400:
        errors["to"] = "Generate at most a year at a time."
    weekdays = {int(x) for x in d.get("weekdays") or [] if str(x).isdigit() and 0 <= int(x) <= 6}
    if not weekdays:
        errors["weekdays"] = "Choose at least one day of the week."
    for k in ("start_time", "end_time"):
        if not TIME_RE.match((d.get(k) or "")[:5]):
            errors[k] = "Enter a time like 09:30."
    if not errors.get("start_time") and not errors.get("end_time") and d["start_time"][:5] >= d["end_time"][:5]:
        errors["end_time"] = "The session must end after it starts."
    if errors:
        raise Invalid(errors)
    skip = {x.isoformat() for x in (validate.date(s) for s in d.get("skip_dates") or []) if x}
    if d.get("skip_bank_holidays", True):
        skip |= bank_holidays()
    themes = [validate.text(t, 120) for t in d.get("themes") or [] if validate.text(t, 120)]
    dates = generate_dates(start, end, weekdays, skip)
    if len(dates) > MAX_GENERATED:
        raise Invalid({"to": "That's more than %d sessions — choose a shorter range." % MAX_GENERATED})
    with db.tx() as c:
        a = _editable(_activity(c, int(aid)))
        base = clean_session(c, a, {"date": start.isoformat(), "start_time": d["start_time"][:5],
                                    "end_time": d["end_time"][:5], "capacity": d.get("capacity"),
                                    "price_pence": d.get("price_pence"), "centre_id": d.get("centre_id")})
        preview, created = [], 0
        for i, day in enumerate(dates):
            exists = c.execute("SELECT 1 FROM activity_sessions WHERE activity_id=? AND date=? AND start_time=?",
                               (a["id"], day.isoformat(), base["start_time"])).fetchone() is not None
            theme = themes[i] if i < len(themes) else None
            preview.append({"date": day.isoformat(), "theme": theme, "exists": exists})
            if not exists and not d.get("dry_run"):
                c.execute("INSERT INTO activity_sessions(activity_id, date, start_time, end_time, theme, centre_id,"
                          " capacity, price_pence, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                          (a["id"], day.isoformat(), base["start_time"], base["end_time"], theme, base["centre_id"],
                           base["capacity"], base["price_pence"], db.now()))
                created += 1
        if d.get("dry_run"):
            return h.json({"ok": True, "dry_run": True, "sessions": preview,
                           "skipped": sorted(x for x in skip if start.isoformat() <= x <= end.isoformat())})
        audit.record(c, h, "session.generate", entity_type="activity", entity_id=a["id"], details={"created": created})
        return h.json({"ok": True, "created": created, "activity": activity_json(c, a)})


@route("POST", "/api/staff/activities/<aid>/duplicate", auth="staff", perm="activities.manage")
def duplicate(h, aid):
    """Copy an activity as a draft for next term, moving every session by the
    same number of whole weeks (Monday to Monday) so weekdays line up. Days
    before the new start date, and bank holidays, are left out."""
    d = h.json_body() or {}
    new_start = validate.date(d.get("new_start_date"))
    with db.tx() as c:
        a = _activity(c, int(aid))
        sessions = c.execute("SELECT * FROM activity_sessions WHERE activity_id=? AND status='scheduled'"
                             " ORDER BY date, start_time", (a["id"],)).fetchall()
        if sessions and not new_start:
            raise Invalid({"new_start_date": "Enter the date the new run starts."})
        title = validate.text(d.get("title"), 120) or a["title"] + " (copy)"
        skip = {"id", "slug", "title", "status", "publish_at", "created_by", "created_at", "updated_at", "archived_at",
                "booking_opens_at", "event_id"}
        v = {k: a[k] for k in a.keys() if k not in skip}
        v.update(title=title, slug=unique_slug(c, slugify(d.get("slug") or title)), status="draft",
                 created_by=h.staff()["id"], created_at=db.now(), updated_at=db.now())
        cols = sorted(v)
        new_id = c.execute("INSERT INTO activities(%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                           [v[k] for k in cols]).lastrowid
        shift, dropped, holidays = 0, 0, bank_holidays(c)
        if sessions:
            first = datetime.date.fromisoformat(sessions[0]["date"])
            monday = lambda day: day - datetime.timedelta(days=day.weekday())  # noqa: E731
            shift = (monday(new_start) - monday(first)).days
        for s in sessions:
            day = (datetime.date.fromisoformat(s["date"]) + datetime.timedelta(days=shift)).isoformat()
            if day < new_start.isoformat() or day in holidays:
                dropped += 1
                continue
            c.execute("INSERT OR IGNORE INTO activity_sessions(activity_id, date, start_time, end_time, theme,"
                      " centre_id, capacity, price_pence, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (new_id, day, s["start_time"], s["end_time"], s["theme"], s["centre_id"], s["capacity"],
                       s["price_pence"], db.now()))
        audit.record(c, h, "activity.duplicate", entity_type="activity", entity_id=new_id,
                     details={"from": a["id"], "shift_days": shift, "left_out": dropped})
        return h.json({"ok": True, "activity": activity_json(c, _activity(c, new_id))})


# ---------------------------------------------------------------- centres


@route("POST", "/api/staff/centres", auth="staff", perm="settings.manage")
def save_centre(h):
    d = h.json_body() or {}
    name = validate.text(d.get("name"), 120)
    if not name:
        raise Invalid({"name": "Enter the centre's name."})
    pc = validate.postcode(d.get("postcode")) if d.get("postcode") else None
    with db.tx() as c:
        if d.get("id"):
            c.execute("UPDATE centres SET name=?, address=?, postcode=?, active=? WHERE id=?",
                      (name, validate.text(d.get("address"), 300) or None, pc, 1 if d.get("active", True) else 0,
                       int(d["id"])))
            cid = int(d["id"])
        else:
            cid = c.execute("INSERT INTO centres(name, address, postcode) VALUES (?,?,?)",
                            (name, validate.text(d.get("address"), 300) or None, pc)).lastrowid
        audit.record(c, h, "centre.save", entity_type="centre", entity_id=cid)
    return h.json({"ok": True, "id": cid})


# ---------------------------------------------------------------- exports


@route("GET", "/api/staff/activities.csv", auth="staff", perm="activities.view")
def export_list(h):
    rows = []
    with db.read() as c:
        for a in c.execute("SELECT a.*, cat.name AS category, cen.name AS centre FROM activities a JOIN"
                           " activity_categories cat ON cat.id=a.category_id JOIN centres cen ON cen.id=a.centre_id"
                           " ORDER BY a.title"):
            st = c.execute("SELECT COUNT(*) AS n, MIN(date) AS first, MAX(date) AS last FROM activity_sessions"
                           " WHERE activity_id=?", (a["id"],)).fetchone()
            booked = c.execute("SELECT COALESCE(SUM(places),0) FROM bookings WHERE activity_id=? AND status IN "
                               + catalogue.HOLDING_SQL, (a["id"],)).fetchone()[0]
            rows.append([a["title"], a["slug"], catalogue.derived_status(a, st["last"]), a["category"], a["centre"],
                         catalogue.age_range_text(a), a["registration_level"], a["price_pence"] / 100, st["n"],
                         st["first"] or "", st["last"] or "", booked])
    return h.csv("activities.csv", ["Title", "Slug", "Status", "Category", "Centre", "Ages", "Form", "Price (£)",
                                    "Sessions", "First session", "Last session", "Places booked"], rows)


@route("GET", "/api/staff/activities/<aid>/export.csv", auth="staff", perm="activities.view")
def export_sessions(h, aid):
    with db.read() as c:
        try:
            a = _activity(c, int(aid))
        except (LookupError, ValueError):
            return h.json({"error": "not found"}, 404)
        rows = []
        for s in c.execute("SELECT * FROM activity_sessions WHERE activity_id=? ORDER BY date, start_time", (a["id"],)):
            counts = {r[0]: r[1] for r in c.execute(
                "SELECT status, COALESCE(SUM(places),0) FROM bookings WHERE session_id=? GROUP BY status", (s["id"],))}
            attended = c.execute("SELECT COUNT(*) FROM attendance WHERE session_id=? AND status='present'",
                                 (s["id"],)).fetchone()[0]
            rows.append([s["date"], s["start_time"], s["end_time"], s["theme"] or "", s["status"], s["capacity"],
                         catalogue.price_of(a, s) / 100, counts.get("confirmed", 0),
                         counts.get("pending_approval", 0) + counts.get("pending_payment", 0) + counts.get("offered", 0),
                         counts.get("waitlisted", 0), attended])
    return h.csv("%s-sessions.csv" % a["slug"], ["Date", "Start", "End", "Theme", "Status", "Places", "Price (£)",
                                                  "Confirmed", "Pending", "Waiting list", "Attended"], rows)
