"""The injury and incident log.

Parents are told in one of three ways, chosen when the incident is logged:
now (an email saying there's a note to read — never the details), at
collection (the register won't let the child be signed out until it's been
talked through), or not at all (a reason is required). Safeguarding concerns
are restricted to the DSL role and never sent to parents automatically."""
import datetime

from . import audit, db, family, intray, outbox, validate
from .validate import Invalid
from .web import route, site_url

KINDS = {"injury": "Injury / accident", "illness": "Illness", "behaviour": "Behaviour", "safeguarding": "Safeguarding concern",
         "near_miss": "Near miss", "other": "Other"}
NOTIFY = {"now": "Tell the parent now (email)", "at_collection": "Talk it through at collection",
          "not_notified": "Don't tell the parent (give a reason)"}


def _visible(h, inc):
    return not inc["restricted"] or h.has_perm("safeguarding.view")


def _retain_until(c, pids):
    dobs = [c.execute("SELECT dob FROM participants WHERE id=?", (pid,)).fetchone()[0] for pid in pids]
    if not dobs:
        return None
    youngest = max(datetime.date.fromisoformat(d) for d in dobs)
    try:
        return youngest.replace(year=youngest.year + 25).isoformat()
    except ValueError:  # 29 February
        return youngest.replace(year=youngest.year + 25, day=28).isoformat()


def _people(c, raw, session_id):
    """[{participant_id, booking_id, account_id, person_name, role}] from the form."""
    out = []
    for x in raw or []:
        role = x.get("role") if x.get("role") in ("injured", "involved", "witness") else "involved"
        if str(x.get("booking_id") or "").isdigit():
            b = c.execute("SELECT * FROM bookings WHERE id=?", (int(x["booking_id"]),)).fetchone()
            if not b:
                continue
            if b["participant_id"]:
                out.append({"participant_id": b["participant_id"], "booking_id": b["id"], "account_id": b["account_id"],
                            "guest_contact_id": None, "person_name": None, "role": role})
            else:
                out.append({"participant_id": None, "booking_id": b["id"], "account_id": b["account_id"],
                            "guest_contact_id": b["guest_contact_id"],
                            "person_name": validate.text(x.get("person_name"), 100) or b["party_name"] or "Guest",
                            "role": role})
        elif x.get("participant_ref"):
            p = c.execute("SELECT * FROM participants WHERE ref=?", (x["participant_ref"],)).fetchone()
            if p:
                out.append({"participant_id": p["id"], "booking_id": None, "account_id": p["account_id"],
                            "guest_contact_id": None, "person_name": None, "role": role})
        elif validate.text(x.get("person_name"), 100):
            out.append({"participant_id": None, "booking_id": None, "account_id": None, "guest_contact_id": None,
                        "person_name": validate.text(x["person_name"], 100), "role": role})
    return out


def _notify_now(c, h, inc_id):
    """Tell each family there's a note to read (no details in the email)."""
    inc = c.execute("SELECT * FROM incidents WHERE id=?", (inc_id,)).fetchone()
    told = set()
    for r in c.execute("SELECT ip.*, p.first_name FROM incident_people ip LEFT JOIN participants p ON p.id=ip.participant_id"
                       " WHERE ip.incident_id=? AND ip.role<>'witness'", (inc_id,)).fetchall():
        target = None
        if r["account_id"]:
            target = c.execute("SELECT email, first_name, id FROM accounts WHERE id=?", (r["account_id"],)).fetchone()
        elif r["guest_contact_id"]:
            target = c.execute("SELECT email, name AS first_name, NULL AS id FROM guest_contacts WHERE id=?",
                               (r["guest_contact_id"],)).fetchone()
        if not target or not target["email"] or target["email"] in told:
            continue
        told.add(target["email"])
        outbox.email(c, target["email"], "incident_notice",
                     {"first_name": target["first_name"] or "there", "child": r["first_name"] or "your child",
                      "account_url": site_url(h) + "/account/bookings"},
                     account_id=target["id"], participant_id=r["participant_id"])
    c.execute("UPDATE incidents SET parent_notified_at=?, updated_at=? WHERE id=?", (db.now(), db.now(), inc["id"]))
    return len(told)


def clean(d, current=None):
    errors, out = {}, {}
    get = (lambda k: d[k] if k in d else (current[k] if current else None))
    kind = get("kind")
    if kind not in KINDS:
        errors["kind"] = "Choose what kind of incident this is."
    out["kind"] = kind
    out["severity"] = get("severity") if get("severity") in ("minor", "moderate", "serious") else "minor"
    when = (get("occurred_at_local") or get("occurred_at") or "").strip()[:16]
    try:
        datetime.datetime.fromisoformat(when)
        out["occurred_at"] = when
    except ValueError:
        errors["occurred_at_local"] = "Enter when it happened."
    out["description"] = validate.long_text(get("description"), 5000)
    if not out["description"]:
        errors["description"] = "Describe what happened."
    for k, n in (("location", 200), ("first_aider", 100)):
        out[k] = validate.text(get(k), n) or None
    for k in ("action_taken", "witnesses", "follow_up"):
        out[k] = validate.long_text(get(k), 3000) or None
    out["first_aid_given"] = 1 if get("first_aid_given") else 0
    out["riddor_reportable"] = 1 if get("riddor_reportable") else 0
    mode = get("notify_mode")
    if kind == "safeguarding":
        mode, out["not_notified_reason"] = "not_notified", "Safeguarding concern — the DSL decides who is told."
    else:
        out["not_notified_reason"] = validate.text(get("not_notified_reason"), 300) or None
        if mode not in NOTIFY:
            errors["notify_mode"] = "Choose how the parent will be told."
        elif mode == "not_notified" and not out["not_notified_reason"]:
            errors["not_notified_reason"] = "Say why the parent won't be told."
    out["notify_mode"] = mode
    out["restricted"] = 1 if kind == "safeguarding" else 0
    if errors:
        raise Invalid(errors)
    return out


@route("POST", "/api/staff/incidents", auth="staff", perm="incidents.log")
def create(h):
    d = h.json_body() or {}
    v = clean(d)
    with db.tx() as c:
        sid = int(d["session_id"]) if str(d.get("session_id") or "").isdigit() else None
        s = c.execute("SELECT s.*, a.centre_id AS a_centre FROM activity_sessions s JOIN activities a ON a.id=s.activity_id"
                      " WHERE s.id=?", (sid,)).fetchone() if sid else None
        people = _people(c, d.get("people"), sid)
        if not people:
            raise Invalid({"people": "Add at least one person involved."})
        now = db.now()
        v.update(ref=family.new_ref("I"), session_id=s["id"] if s else None,
                 centre_id=(s["centre_id"] or s["a_centre"]) if s else None, created_by=h.staff()["id"],
                 created_at=now, updated_at=now,
                 retain_until=_retain_until(c, [p["participant_id"] for p in people if p["participant_id"]]))
        cols = sorted(v)
        iid = c.execute("INSERT INTO incidents(%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                        [v[k] for k in cols]).lastrowid
        for p in people:
            c.execute("INSERT INTO incident_people(incident_id, participant_id, booking_id, guest_contact_id, person_name,"
                      " role, account_id) VALUES (?,?,?,?,?,?,?)", (iid, p["participant_id"], p["booking_id"],
                                                                   p["guest_contact_id"], p["person_name"], p["role"],
                                                                   p["account_id"]))
        told = _notify_now(c, h, iid) if v["notify_mode"] == "now" else 0
        if v["restricted"]:
            intray.add(c, "safeguarding_concern", "A safeguarding concern has been logged — please review",
                       perm="safeguarding.view", entity_type="incident", entity_id=iid, dedupe=False)
        elif v["severity"] == "serious" or v["riddor_reportable"]:
            intray.add(c, "incident_follow_up", "Serious incident logged (%s) — follow up" % KINDS[v["kind"]],
                       perm="incidents.manage", entity_type="incident", entity_id=iid, session_id=v["session_id"])
        audit.record(c, h, "incident.create", entity_type="incident", entity_id=iid, restricted=bool(v["restricted"]),
                     details={"kind": v["kind"], "notify": v["notify_mode"], "people": len(people)})
        ref = v["ref"]
    return h.json({"ok": True, "ref": ref, "id": iid, "notified": told})


def incident_json(c, inc, full=False):
    people = []
    for r in c.execute("SELECT ip.*, p.first_name, p.last_name, p.ref AS pref FROM incident_people ip LEFT JOIN"
                       " participants p ON p.id=ip.participant_id WHERE ip.incident_id=?", (inc["id"],)):
        people.append({"name": ("%s %s" % (r["first_name"], r["last_name"])) if r["first_name"] else r["person_name"],
                       "ref": r["pref"], "role": r["role"], "acknowledged_at": r["acknowledged_at"]})
    out = {"id": inc["id"], "ref": inc["ref"], "occurred_at": inc["occurred_at"], "kind": inc["kind"],
           "kind_text": KINDS[inc["kind"]], "severity": inc["severity"], "status": inc["status"],
           "notify_mode": inc["notify_mode"], "parent_notified_at": inc["parent_notified_at"],
           "discussed_at": inc["discussed_at"], "restricted": bool(inc["restricted"]), "people": people,
           "session_id": inc["session_id"]}
    if full:
        out.update({k: inc[k] for k in ("location", "description", "action_taken", "first_aid_given", "first_aider",
                                        "witnesses", "follow_up", "not_notified_reason", "riddor_reportable",
                                        "created_at", "retain_until")})
        who = c.execute("SELECT name FROM staff_users WHERE id=?", (inc["created_by"],)).fetchone()
        out["created_by"] = who["name"] if who else None
    return out


@route("GET", "/api/staff/incidents", auth="staff", perm="incidents.view")
def list_incidents(h):
    q = h.query()
    where, args = ["1=1"], []
    if q.get("status") in ("open", "closed"):
        where.append("status=?")
        args.append(q["status"])
    if q.get("kind") in KINDS:
        where.append("kind=?")
        args.append(q["kind"])
    if not h.has_perm("safeguarding.view"):
        where.append("restricted=0")
    with db.read() as c:
        rows = c.execute("SELECT * FROM incidents WHERE " + " AND ".join(where) + " ORDER BY occurred_at DESC LIMIT 200",
                         args).fetchall()
        return h.json({"incidents": [incident_json(c, r) for r in rows], "kinds": KINDS, "notify": NOTIFY})


@route("GET", "/api/staff/incidents/<iid>", auth="staff", perm="incidents.view")
def get_incident(h, iid):
    with db.tx() as c:
        inc = c.execute("SELECT * FROM incidents WHERE id=?", (int(iid),)).fetchone()
        if not inc or not _visible(h, inc):
            raise LookupError
        audit.record(c, h, "incident.view", entity_type="incident", entity_id=inc["id"], restricted=bool(inc["restricted"]))
        return h.json({"incident": incident_json(c, inc, full=True)})


@route("POST", "/api/staff/incidents/<iid>/update", auth="staff", perm="incidents.manage")
def update(h, iid):
    d = h.json_body() or {}
    with db.tx() as c:
        inc = c.execute("SELECT * FROM incidents WHERE id=?", (int(iid),)).fetchone()
        if not inc or not _visible(h, inc):
            raise LookupError
        fields = {}
        for k in ("follow_up", "action_taken"):
            if k in d:
                fields[k] = validate.long_text(d[k], 3000) or None
        if "riddor_reportable" in d:
            fields["riddor_reportable"] = 1 if d["riddor_reportable"] else 0
        if d.get("status") in ("open", "closed"):
            fields["status"] = d["status"]
            fields["closed_at"] = db.now() if d["status"] == "closed" else None
        if fields:
            fields["updated_at"] = db.now()
            c.execute("UPDATE incidents SET %s WHERE id=?" % ",".join("%s=?" % k for k in fields), [*fields.values(), inc["id"]])
        told = 0
        if d.get("notify_now") and not inc["restricted"]:
            told = _notify_now(c, h, inc["id"])
        if fields.get("status") == "closed":
            intray.resolve(c, "incident_follow_up", entity_type="incident", entity_id=inc["id"], staff_id=h.staff()["id"])
        audit.record(c, h, "incident.update", entity_type="incident", entity_id=inc["id"],
                     restricted=bool(inc["restricted"]), details={"changed": sorted(fields), "notified": told})
    return h.json({"ok": True, "notified": told})


@route("GET", "/api/staff/sessions/<sid>/people", auth="staff", perm="incidents.log")
def session_people(h, sid):
    """Who's booked on a session, for picking people when logging an incident."""
    with db.read() as c:
        rows = c.execute("SELECT b.id, b.kind, b.party_name, p.first_name, p.last_name FROM bookings b LEFT JOIN"
                         " participants p ON p.id=b.participant_id WHERE b.session_id=? AND b.status='confirmed'"
                         " ORDER BY p.last_name, p.first_name", (int(sid),)).fetchall()
    return h.json({"people": [{"booking_id": r["id"], "name": ("%s %s" % (r["first_name"], r["last_name"]))
                               if r["first_name"] else (r["party_name"] or "Group booking")} for r in rows]})


# ---------------------------------------------------------------- parents


@route("GET", "/api/account/incidents", auth="account")
def my_incidents(h):
    who = h.principal("account")
    with db.read() as c:
        rows = c.execute(
            "SELECT i.*, ip.id AS ipid, ip.acknowledged_at, p.first_name FROM incidents i JOIN incident_people ip"
            " ON ip.incident_id=i.id LEFT JOIN participants p ON p.id=ip.participant_id WHERE ip.account_id=?"
            " AND i.restricted=0 AND i.notify_mode<>'not_notified' AND ip.role<>'witness'"
            " AND (i.parent_notified_at IS NOT NULL OR i.discussed_at IS NOT NULL) ORDER BY i.occurred_at DESC LIMIT 50",
            (who["id"],)).fetchall()
    return h.json({"incidents": [{
        "ref": r["ref"], "child": r["first_name"], "occurred_at": r["occurred_at"], "kind": KINDS[r["kind"]],
        "description": r["description"], "action_taken": r["action_taken"], "first_aid_given": bool(r["first_aid_given"]),
        "acknowledged": bool(r["acknowledged_at"])} for r in rows]})


@route("POST", "/api/account/incidents/<ref>/acknowledge", auth="account")
def acknowledge(h, ref):
    who = h.principal("account")
    with db.tx() as c:
        n = c.execute("UPDATE incident_people SET acknowledged_at=? WHERE account_id=? AND acknowledged_at IS NULL AND"
                      " incident_id=(SELECT id FROM incidents WHERE ref=? AND restricted=0)",
                      (db.now(), who["id"], ref)).rowcount
        if not n and not c.execute("SELECT 1 FROM incident_people ip JOIN incidents i ON i.id=ip.incident_id WHERE"
                                   " i.ref=? AND ip.account_id=? AND i.restricted=0", (ref, who["id"])).fetchone():
            raise LookupError
        audit.record(c, h, "incident.acknowledged", entity_type="incident", details={"ref": ref})
    return h.json({"ok": True})
