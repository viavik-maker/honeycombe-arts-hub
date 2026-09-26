"""SEND support requests (mockup 6): "Let's plan your child's support
together".

A signed-in parent tells the SEND lead about their child's needs and uploads
any plans (EHCP, care or behaviour plans). The SEND lead gets in touch (a
call, a visit or email), then records an agreed support plan; a short
summary of it appears on registers so session staff know what helps.

Who sees what: the family sees their own requests; staff need send.view
(owner, admin, manager, DSL, SEND lead). Viewing a request or downloading a
file is written to the audit log."""
import datetime
import json

from . import (audit, booking_settings, catalogue, db, family, formspec, intray, outbox, private_files, ratelimit,
               validate, worker)
from .validate import Invalid
from .web import route, site_url

ratelimit.LIMITS.update({"send_upload": (40, 60 * 60)})

CONTACT = {"phone": "Request a phone call", "visit": "Visit the centre", "email": "Email us"}
BEST_TIME = {"any": "Any time", "morning": "Mornings", "afternoon": "Afternoons", "after_school": "After school (3–5pm)",
             "evening": "Early evening"}
NEEDS = {"autism": "Autism", "adhd": "ADHD", "slc": "Speech, language and communication", "sensory": "Sensory processing",
         "learning": "Learning difficulty", "physical": "Physical or mobility", "semh": "SEMH", "medical": "Medical needs",
         "other": "Other"}
INTERESTS = {"holiday_clubs": "Holiday clubs", "regular": "Regular events", "one_off": "One-off events",
             "not_sure": "Not sure yet"}
ONE_TO_ONE = {"yes": "Yes", "sometimes": "Sometimes", "no": "No", "not_sure": "Not sure"}
EHCP = {"yes": "Yes", "being_assessed": "Being assessed", "no": "No"}
STATUS = {"submitted": "Sent — waiting for our SEND lead", "contacted": "Our SEND lead has been in touch",
          "visit_booked": "Visit booked", "plan_agreed": "Support plan agreed", "closed": "Closed"}
MAX_PENDING_FILES = 12


def _who(h):
    return h.principal("account")


def _options():
    return {"contact": CONTACT, "best_time": BEST_TIME, "needs": NEEDS, "interests": INTERESTS,
            "one_to_one": ONE_TO_ONE, "ehcp": EHCP, "status": STATUS}


def intake_json(c, it, staff=False):
    p = c.execute("SELECT ref, first_name, last_name, dob FROM participants WHERE id=?", (it["participant_id"],)).fetchone()
    out = {"ref": it["ref"], "status": it["status"], "status_text": STATUS[it["status"]], "created_at": it["created_at"],
           "child": {"ref": p["ref"], "first_name": p["first_name"], "last_name": p["last_name"], "dob": p["dob"],
                     "age": family.age_years(p["dob"])},
           "contact_method": it["contact_method"], "best_time": it["best_time"], "needs": json.loads(it["needs"]),
           "needs_other": it["needs_other"], "interests": json.loads(it["interests"]), "good_day": it["good_day"],
           "overwhelm": it["overwhelm"], "communication": it["communication"], "current_support": it["current_support"],
           "one_to_one": it["one_to_one"], "ehcp": it["ehcp"], "consent_professionals": bool(it["consent_professionals"]),
           "visit_at": it["visit_at"], "plan_summary": it["plan_summary"], "plan_agreed_at": it["plan_agreed_at"],
           "files": [private_files.as_json(f) for f in c.execute(
               "SELECT * FROM private_files WHERE intake_id=? AND deleted_at IS NULL ORDER BY id", (it["id"],))]}
    if staff:
        a = c.execute("SELECT ref, first_name, last_name, email, mobile FROM accounts WHERE id=?", (it["account_id"],)).fetchone()
        out["family"] = {"ref": a["ref"], "name": "%s %s" % (a["first_name"], a["last_name"]), "email": a["email"],
                         "mobile": a["mobile"]}
        who = c.execute("SELECT name FROM staff_users WHERE id=?", (it["assigned_staff_id"],)).fetchone() \
            if it["assigned_staff_id"] else None
        out["assigned_to"] = who["name"] if who else None
        out["notes"] = [dict(r) for r in c.execute(
            "SELECT n.body, n.created_at, s.name AS staff FROM send_notes n LEFT JOIN staff_users s ON s.id=n.staff_id"
            " WHERE n.intake_id=? ORDER BY n.id", (it["id"],))]
    return out


# ---------------------------------------------------------------- families


@route("GET", "/api/account/send", auth="account")
def my_requests(h):
    who = _who(h)
    with db.read() as c:
        kids = [{"ref": p["ref"], "first_name": p["first_name"], "last_name": p["last_name"],
                 "age": family.age_years(p["dob"])}
                for p in family.account_participants(c, who["id"]) if not p["is_account_holder"]]
        reqs = [intake_json(c, it) for it in c.execute("SELECT * FROM send_intakes WHERE account_id=? ORDER BY id DESC",
                                                       (who["id"],)).fetchall()]
        pending = [private_files.as_json(f) for f in c.execute(
            "SELECT * FROM private_files WHERE account_id=? AND intake_id IS NULL AND deleted_at IS NULL ORDER BY id",
            (who["id"],))]
        days = booking_settings.get("send_response_days", c)
    return h.json({"you": {"first_name": who["first_name"], "last_name": who["last_name"], "email": who["email"],
                           "mobile": who["mobile"]},
                   "children": kids, "requests": reqs, "pending_files": pending, "options": _options(),
                   "response_days": days})


@route("POST", "/api/account/send/files", auth="account", body_limit=private_files.MAX_BYTES + 64 * 1024)
def upload(h):
    who = _who(h)
    if not ratelimit.hit("send_upload", "a%d" % who["id"]):
        return h.json({"error": "That's a lot of uploads — please try again later."}, 429)
    filename, data, fields = private_files.parse_upload(h)
    kind = fields.get("kind") if fields.get("kind") in ("ehcp", "other_plan") else "other_plan"
    try:
        with db.tx() as c:
            n = c.execute("SELECT COUNT(*) FROM private_files WHERE account_id=? AND intake_id IS NULL AND deleted_at IS NULL",
                          (who["id"],)).fetchone()[0]
            if n >= MAX_PENDING_FILES:
                raise private_files.Rejected("You can attach up to %d files." % MAX_PENDING_FILES)
            row = private_files.save(c, account_id=who["id"], kind=kind, filename=filename, data=data)
            audit.record(c, h, "send.file_uploaded", entity_type="private_file", entity_id=row["id"],
                         account_id=who["id"], restricted=True)
            return h.json({"ok": True, "file": private_files.as_json(row)})
    except private_files.Rejected as e:
        return h.json({"error": str(e)}, 400)


def _own_file(c, h, ref):
    f = c.execute("SELECT * FROM private_files WHERE ref=? AND account_id=? AND deleted_at IS NULL",
                  (ref, _who(h)["id"])).fetchone()
    if not f:
        raise LookupError
    return f


@route("GET", "/api/account/send/files/<ref>", auth="account")
def my_file(h, ref):
    with db.read() as c:
        f = _own_file(c, h, ref)
    return private_files.send(h, f)


@route("POST", "/api/account/send/files/<ref>/delete", auth="account")
def remove_file(h, ref):
    with db.tx() as c:
        f = _own_file(c, h, ref)
        if f["intake_id"]:
            status = c.execute("SELECT status FROM send_intakes WHERE id=?", (f["intake_id"],)).fetchone()[0]
            if status != "submitted":
                raise ValueError("Our SEND lead is already working with this — please ask them to remove it.")
        private_files.delete(c, f)
        audit.record(c, h, "send.file_removed", entity_type="private_file", entity_id=f["id"], account_id=f["account_id"],
                     restricted=True)
    return h.json({"ok": True})


def _text(d, key, n=2000):
    return validate.long_text(d.get(key), n) or None


def clean(d):
    errors = {}
    if d.get("contact_method") not in CONTACT:
        errors["contact_method"] = "Choose how you'd like to talk."
    if d.get("best_time") not in BEST_TIME:
        errors["best_time"] = "Choose the best time to contact you."
    needs = [x for x in d.get("needs") or [] if x in NEEDS]
    interests = [x for x in d.get("interests") or [] if x in INTERESTS]
    if not interests:
        errors["interests"] = "Choose at least one (or “Not sure yet”)."
    if d.get("one_to_one") not in ONE_TO_ONE:
        errors["one_to_one"] = "Choose an answer for “Would your child need 1:1 support?”."
    if d.get("ehcp") not in EHCP:
        errors["ehcp"] = "Choose an answer for “Does your child have an EHCP?”."
    if not d.get("consent_share_staff"):
        errors["consent_share_staff"] = "Please agree so we can share this with the staff supporting your child."
    if errors:
        raise Invalid(errors)
    return {"contact_method": d["contact_method"], "best_time": d["best_time"], "needs": json.dumps(needs),
            "needs_other": validate.text(d.get("needs_other"), 200) or None, "interests": json.dumps(interests),
            "good_day": _text(d, "good_day"), "overwhelm": _text(d, "overwhelm"),
            "communication": _text(d, "communication"), "current_support": _text(d, "current_support"),
            "one_to_one": d["one_to_one"], "ehcp": d["ehcp"], "consent_share_staff": 1,
            "consent_professionals": 1 if d.get("consent_professionals") else 0}


@route("POST", "/api/account/send", auth="account", body_limit=64 * 1024)
def submit(h):
    who = _who(h)
    d = h.json_body() or {}
    v = clean(d)
    with db.tx() as c:
        if d.get("child_ref"):
            p = c.execute("SELECT * FROM participants WHERE ref=? AND account_id=? AND status='active'"
                          " AND is_account_holder=0", (d["child_ref"], who["id"])).fetchone()
            if not p:
                raise Invalid({"child_ref": "Choose your child."})
            pid = p["id"]
        else:
            child = formspec.clean("child", dict(d.get("child") or {}, last_name=(d.get("child") or {}).get("last_name")
                                                 or who["last_name"]), "short")
            if formspec.age_months(datetime.date.fromisoformat(child["dob"])) >= 18 * 12:
                raise Invalid({"dob": "They're 18 or over, so they can register themselves as a young adult."})
            now = db.now()
            pid = c.execute("INSERT INTO participants(ref, account_id, first_name, last_name, dob, target_level, created_at,"
                            " updated_at) VALUES (?,?,?,?,?, 'full', ?, ?)",
                            (family.new_ref("P"), who["id"], child["first_name"], child["last_name"], child["dob"], now,
                             now)).lastrowid
            family.compute_level(c, pid)
        now = db.now()
        v.update(ref=family.new_ref("S"), account_id=who["id"], participant_id=pid, created_at=now, updated_at=now)
        cols = sorted(v)
        iid = c.execute("INSERT INTO send_intakes(%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                        [v[k] for k in cols]).lastrowid
        refs = [r for r in d.get("files") or [] if isinstance(r, str)][:MAX_PENDING_FILES]
        if refs:
            c.execute("UPDATE private_files SET intake_id=?, participant_id=? WHERE account_id=? AND intake_id IS NULL"
                      " AND deleted_at IS NULL AND ref IN (%s)" % ",".join("?" * len(refs)), [iid, pid, who["id"], *refs])
        p = c.execute("SELECT first_name FROM participants WHERE id=?", (pid,)).fetchone()
        intray.add(c, "send_intake", "New SEND support request (%s)" % p["first_name"], perm="send.view",
                   entity_type="send_intake", entity_id=iid, participant_id=pid, account_id=who["id"], dedupe=False)
        days = booking_settings.get("send_response_days", c)
        outbox.email(c, who["email"], "send_intake_received",
                     {"first_name": who["first_name"], "child": p["first_name"], "days": days,
                      "how": CONTACT[v["contact_method"]].lower()}, account_id=who["id"], participant_id=pid)
        for to in booking_settings.get("send_notify_emails", c):
            outbox.email(c, to, "send_intake_staff", {"admin_url": site_url(h) + "/admin/"}, kind="staff")
        audit.record(c, h, "send.intake_submitted", entity_type="send_intake", entity_id=iid, participant_id=pid,
                     account_id=who["id"], restricted=True, details={"files": len(refs)})
        ref = v["ref"]
    return h.json({"ok": True, "ref": ref})


# ---------------------------------------------------------------- staff


def _intake(c, ref):
    it = c.execute("SELECT * FROM send_intakes WHERE ref=?", (ref,)).fetchone()
    if not it:
        raise LookupError
    return it


@route("GET", "/api/staff/send", auth="staff", perm="send.view")
def staff_list(h):
    view = h.query().get("view") or "open"
    where = {"open": "status IN ('submitted','contacted','visit_booked')", "agreed": "status='plan_agreed'",
             "closed": "status='closed'"}.get(view, "1=1")
    with db.read() as c:
        rows = c.execute("SELECT * FROM send_intakes WHERE " + where + " ORDER BY created_at DESC LIMIT 300").fetchall()
        out = []
        for it in rows:
            p = c.execute("SELECT first_name, last_name, dob FROM participants WHERE id=?", (it["participant_id"],)).fetchone()
            a = c.execute("SELECT first_name, last_name FROM accounts WHERE id=?", (it["account_id"],)).fetchone()
            n = c.execute("SELECT COUNT(*) FROM private_files WHERE intake_id=? AND deleted_at IS NULL", (it["id"],)).fetchone()[0]
            out.append({"ref": it["ref"], "status": it["status"], "status_text": STATUS[it["status"]],
                        "created_at": it["created_at"], "child": "%s %s" % (p["first_name"], p["last_name"]),
                        "age": family.age_years(p["dob"]), "family": "%s %s" % (a["first_name"], a["last_name"]),
                        "contact_method": CONTACT[it["contact_method"]], "ehcp": EHCP[it["ehcp"]], "files": n,
                        "needs": [NEEDS[x] for x in json.loads(it["needs"])]})
    return h.json({"requests": out, "options": _options()})


@route("GET", "/api/staff/send/<ref>", auth="staff", perm="send.view")
def staff_get(h, ref):
    with db.tx() as c:
        it = _intake(c, ref)
        audit.record(c, h, "send.intake_viewed", entity_type="send_intake", entity_id=it["id"],
                     participant_id=it["participant_id"], restricted=True)
        return h.json({"request": intake_json(c, it, staff=True), "options": _options()})


@route("POST", "/api/staff/send/<ref>/update", auth="staff", perm="send.view")
def staff_update(h, ref):
    d = h.json_body() or {}
    staff = h.staff()
    with db.tx() as c:
        it = _intake(c, ref)
        fields, changed = {}, []
        if d.get("assign_to_me"):
            fields["assigned_staff_id"] = staff["id"]
        if d.get("status") and d["status"] != it["status"]:
            to = d["status"]
            if to not in STATUS:
                raise ValueError("Unknown status.")
            fields["status"] = to
            if to == "visit_booked":
                at = (d.get("visit_at") or "").strip()[:16]
                try:
                    datetime.datetime.fromisoformat(at)
                except ValueError:
                    raise Invalid({"visit_at": "Enter the date and time of the visit."})
                fields["visit_at"] = at
            if to == "plan_agreed":
                summary = validate.long_text(d.get("plan_summary"), 1500)
                if not summary:
                    raise Invalid({"plan_summary": "Write a short summary of the plan for the session staff."})
                fields.update(plan_summary=summary, plan_agreed_at=db.now(), plan_agreed_by=staff["id"])
                c.execute("UPDATE participants SET support_plan=?, support_plan_agreed_at=?, updated_at=? WHERE id=?",
                          (summary, db.now(), db.now(), it["participant_id"]))
                a = c.execute("SELECT * FROM accounts WHERE id=?", (it["account_id"],)).fetchone()
                p = c.execute("SELECT first_name FROM participants WHERE id=?", (it["participant_id"],)).fetchone()
                if a["email"]:
                    outbox.email(c, a["email"], "send_plan_agreed",
                                 {"first_name": a["first_name"], "child": p["first_name"],
                                  "book_url": site_url(h) + "/book"}, account_id=a["id"], participant_id=it["participant_id"])
            if to in ("plan_agreed", "closed"):
                intray.resolve(c, "send_intake", entity_type="send_intake", entity_id=it["id"], staff_id=staff["id"])
        elif "plan_summary" in d and it["status"] == "plan_agreed":
            summary = validate.long_text(d.get("plan_summary"), 1500)
            if summary:
                fields["plan_summary"] = summary
                c.execute("UPDATE participants SET support_plan=?, updated_at=? WHERE id=?",
                          (summary, db.now(), it["participant_id"]))
        note = validate.long_text(d.get("note"), 3000)
        if note:
            c.execute("INSERT INTO send_notes(intake_id, staff_id, body, created_at) VALUES (?,?,?,?)",
                      (it["id"], staff["id"], note, db.now()))
            changed.append("note")
        if fields:
            fields["updated_at"] = db.now()
            c.execute("UPDATE send_intakes SET %s WHERE id=?" % ",".join("%s=?" % k for k in fields),
                      [*fields.values(), it["id"]])
            changed += sorted(k for k in fields if k != "updated_at")
        audit.record(c, h, "send.intake_updated", entity_type="send_intake", entity_id=it["id"],
                     participant_id=it["participant_id"], restricted=True, details={"changed": changed})
        return h.json({"ok": True, "request": intake_json(c, _intake(c, ref), staff=True)})


@route("GET", "/api/staff/send/files/<ref>", auth="staff", perm="send.view")
def staff_file(h, ref):
    with db.tx() as c:
        f = c.execute("SELECT * FROM private_files WHERE ref=? AND deleted_at IS NULL", (ref,)).fetchone()
        if not f:
            raise LookupError
        audit.record(c, h, "send.file_downloaded", entity_type="private_file", entity_id=f["id"],
                     participant_id=f["participant_id"], restricted=True)
    return private_files.send(h, f)


# ---------------------------------------------------------------- housekeeping and erasure


@worker.job("private_files_purge", daily_at="03:50", timeout=300)
def purge_unattached():
    """Files uploaded but never sent with a request are removed after 2 days."""
    cutoff = catalogue.utc_iso(catalogue.uk_now() - datetime.timedelta(days=2))
    n = 0
    with db.tx() as c:
        for f in c.execute("SELECT * FROM private_files WHERE intake_id IS NULL AND deleted_at IS NULL AND created_at<?",
                           (cutoff,)).fetchall():
            private_files.delete(c, f)
            n += 1
    return "removed %d" % n


def erase_for_account(c, account_id):
    """Called when a family's account is erased: requests, notes and files go."""
    for f in c.execute("SELECT * FROM private_files WHERE account_id=? AND deleted_at IS NULL", (account_id,)).fetchall():
        private_files.delete(c, f)
    c.execute("UPDATE private_files SET filename='(erased)' WHERE account_id=?", (account_id,))
    for it in c.execute("SELECT id FROM send_intakes WHERE account_id=?", (account_id,)).fetchall():
        c.execute("DELETE FROM send_notes WHERE intake_id=?", (it["id"],))
    c.execute("UPDATE send_intakes SET needs='[]', needs_other=NULL, interests='[]', good_day=NULL, overwhelm=NULL,"
              " communication=NULL, current_support=NULL, plan_summary=NULL, status='closed' WHERE account_id=?",
              (account_id,))
    c.execute("UPDATE participants SET support_plan=NULL WHERE account_id=?", (account_id,))
