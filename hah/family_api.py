"""What a signed-in parent (or young adult) can see and change about their
family. Every query is limited to the signed-in account; a reference that
belongs to another family simply isn't found."""
import datetime
import json

from . import audit, db, family, formspec, intray, outbox, security
from .validate import Invalid
from .web import route

ACCOUNT_FIELDS = ("ref", "kind", "email", "first_name", "last_name", "mobile", "address_line1", "address_line2",
                  "town", "postcode", "status", "reconfirmed_at", "source")


def _who(h):
    return h.principal("account")


def _own(c, h, ref):
    return c.execute("SELECT * FROM participants WHERE ref=? AND account_id=? AND status='active'",
                     (ref, _who(h)["id"])).fetchone()


def _level_for_account(c, account_id, kind):
    if kind == "adult":
        return "adult"
    targets = {r[0] for r in c.execute("SELECT target_level FROM participants WHERE account_id=? AND status='active'",
                                       (account_id,))}
    return "full" if "full" in targets else "short"


def _needs_reauth(h):
    """Changing who can collect a child needs the password again (10 min)."""
    from .accounts import recently_reauthenticated
    if not recently_reauthenticated(_who(h)):
        h.json({"error": "Please enter your password to change collection details.", "reauth": True}, 403)
        return True
    return False


def _collection_changed(c, h, p, what):
    """Tell the family (email + text) and staff if the child is booked soon."""
    who = _who(h)
    ctx = {"first_name": who["first_name"], "child": p["first_name"], "what": what}
    outbox.email(c, who["email"], "collection_changed", ctx, account_id=who["id"], participant_id=p["id"])
    if who["mobile"]:
        outbox.text_message(c, who["mobile"], "collection_changed", ctx, account_id=who["id"], participant_id=p["id"])
    if booked_within(c, p["id"], days=7):
        intray.add(c, "collection_changed", "Collection details changed for %s (booked this week)" % p["first_name"],
                   perm="registers.view", participant_id=p["id"], account_id=who["id"], dedupe=False)


def booked_within(c, pid, days):
    """Does the child have a live booking in the next DAYS days? (0 before bookings exist.)"""
    try:
        return c.execute(
            "SELECT 1 FROM bookings b JOIN activity_sessions s ON s.id=b.session_id WHERE b.participant_id=?"
            " AND b.status IN ('confirmed','pending_payment','pending_approval') AND s.date BETWEEN ? AND ?",
            (pid, datetime.date.today().isoformat(),
             (datetime.date.today() + datetime.timedelta(days=days)).isoformat())).fetchone() is not None
    except Exception:  # bookings tables not created yet
        return False


# ---------------------------------------------------------------- overview


@route("GET", "/api/account/formspec")
def formspec_api(h):
    with db.read() as c:
        questions = {r["key"]: {"label": r["label"], "help": r["help_text"], "options": json.loads(r["options"]),
                                "kind": r["kind"]}
                     for r in c.execute("SELECT * FROM consent_types WHERE id IN (SELECT MAX(id) FROM consent_types"
                                        " WHERE active=1 GROUP BY key)")}
    return h.json(dict(formspec.public_spec(), consent_questions=questions))


@route("GET", "/api/account/me", auth="account")
def me(h):
    who = _who(h)
    with db.read() as c:
        people = [family.participant_summary(c, p) for p in family.account_participants(c, who["id"])]
        acks = family.current_consents(c, who["id"], None)
        return h.json({
            "account": {k: who[k] for k in ACCOUNT_FIELDS},
            "participants": people,
            "contacts": family.contacts(c, who["id"]),
            "acknowledged": all(k in acks for k in formspec.ACKNOWLEDGEMENTS),
            "csrf": who["csrf"],
        })


# ---------------------------------------------------------------- your details and contacts


@route("POST", "/api/account/details", auth="account")
def save_details(h):
    who = _who(h)
    d = h.json_body() or {}
    with db.tx() as c:
        level = _level_for_account(c, who["id"], who["kind"])
        you = formspec.clean("you", d, level)
        c.execute("UPDATE accounts SET first_name=?, last_name=?, mobile=?, address_line1=?, address_line2=?, town=?,"
                  " postcode=?, updated_at=? WHERE id=?",
                  (you["first_name"], you["last_name"], you["mobile"], you["address_line1"], you["address_line2"],
                   you["town"], you["postcode"], db.now(), who["id"]))
        if who["kind"] == "adult":
            c.execute("UPDATE participants SET first_name=?, last_name=? WHERE account_id=? AND is_account_holder=1",
                      (you["first_name"], you["last_name"], who["id"]))
        audit.record(c, h, "account.details_changed", entity_type="account", entity_id=who["id"], account_id=who["id"],
                     details={"fields": sorted(k for k in you if you[k] != who[k])})
        family.recompute_account(c, who["id"])
    return me(h)


@route("POST", "/api/account/contacts", auth="account")
def save_contacts(h):
    """Replace the family's emergency contacts (shared by all the children)."""
    who = _who(h)
    d = h.json_body() or {}
    rows = d.get("contacts") or []
    if not isinstance(rows, list) or len(rows) > formspec.CONTACTS["max"]:
        raise Invalid({"contacts": "Add up to %d contacts." % formspec.CONTACTS["max"]})
    cleaned, errors = [], {}
    from . import validate
    for i, r in enumerate(rows):
        name, rel = validate.text(r.get("full_name"), 80), validate.text(r.get("relationship"), 40)
        phone = validate.uk_phone(r.get("phone"))
        if not name:
            errors["contacts.%d.full_name" % i] = "Enter their full name."
        if not rel:
            errors["contacts.%d.relationship" % i] = "How are they related to the child?"
        if not phone:
            errors["contacts.%d.phone" % i] = "Enter a UK phone number."
        cleaned.append((name, rel, phone, 1 if r.get("can_collect") else 0))
    if errors:
        raise Invalid(errors)
    with db.tx() as c:
        before = family.contacts(c, who["id"])
        collectors_before = sorted((x["full_name"], x["phone"]) for x in before if x["can_collect"])
        collectors_after = sorted((n, p) for n, _, p, cc in cleaned if cc)
        if before and collectors_before != collectors_after:
            if _needs_reauth(h):
                return
        c.execute("DELETE FROM emergency_contacts WHERE account_id=?", (who["id"],))
        now = db.now()
        c.executemany("INSERT INTO emergency_contacts(account_id, full_name, relationship, phone, can_collect, priority,"
                      " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                      [(who["id"], n, r, p, cc, i + 1, now, now) for i, (n, r, p, cc) in enumerate(cleaned)])
        if before and collectors_before != collectors_after:
            for p in family.account_participants(c, who["id"]):
                _collection_changed(c, h, p, "who may collect")
        audit.record(c, h, "account.contacts_changed", entity_type="account", entity_id=who["id"], account_id=who["id"],
                     details={"count": len(cleaned)})
        family.recompute_account(c, who["id"])
    return me(h)


@route("POST", "/api/account/acknowledge", auth="account")
def acknowledge(h):
    who = _who(h)
    d = h.json_body() or {}
    if not all(d.get(k) for k in formspec.ACKNOWLEDGEMENTS):
        raise Invalid({k: "Please tick to confirm." for k in formspec.ACKNOWLEDGEMENTS if not d.get(k)})
    with db.tx() as c:
        for k in formspec.ACKNOWLEDGEMENTS:
            family.record_consent(c, who["id"], None, k, "yes", "profile", ip=h.client_ip())
        family.recompute_account(c, who["id"])
    return me(h)


# ---------------------------------------------------------------- children


@route("POST", "/api/account/participants", auth="account")
def add_participant(h):
    who = _who(h)
    d = h.json_body() or {}
    target = d.get("target_level") if d.get("target_level") in ("short", "full") else "full"
    child = formspec.clean("child", d, "short")  # name + DOB now; the rest comes in later steps
    dob = datetime.date.fromisoformat(child["dob"])
    if formspec.age_months(dob) >= 18 * 12:
        raise Invalid({"dob": "They're 18 or over, so they register themselves as a young adult."})
    with db.tx() as c:
        n = c.execute("SELECT COUNT(*) FROM participants WHERE account_id=? AND status='active'", (who["id"],)).fetchone()[0]
        if n >= 12:
            raise Invalid({"first_name": "That's a lot of children! Please call us to add more."})
        now = db.now()
        ref = family.new_ref("P")
        pid = c.execute("INSERT INTO participants(ref, account_id, first_name, last_name, dob, gender, target_level,"
                        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (ref, who["id"], child["first_name"], child["last_name"], child["dob"], child.get("gender"),
                         target, now, now)).lastrowid
        audit.record(c, h, "participant.added", entity_type="participant", entity_id=pid, participant_id=pid,
                     account_id=who["id"])
        family.compute_level(c, pid)
    return h.json({"ok": True, "ref": ref})


@route("GET", "/api/account/participants/<ref>", auth="account")
def get_participant(h, ref):
    with db.read() as c:
        p = _own(c, h, ref)
        if not p:
            return h.json({"error": "Not found"}, 404)
        health = c.execute("SELECT * FROM participant_health WHERE participant_id=?", (p["id"],)).fetchone()
        sg = c.execute("SELECT family_info FROM participant_safeguarding WHERE participant_id=?", (p["id"],)).fetchone()
        gp = c.execute("SELECT * FROM participant_gp WHERE participant_id=?", (p["id"],)).fetchone()
        level = p["target_level"] if not p["is_account_holder"] else "adult"
        months = formspec.age_months(datetime.date.fromisoformat(p["dob"]))
        data = {
            "summary": family.participant_summary(c, p),
            "child": {k: p[k] for k in ("first_name", "last_name", "dob", "gender", "education", "school_name")}
            | {"haf_status": p["haf_status"] if p["haf_status"] != "unknown" else None},
            "haf_verified": p["haf_status"] == "verified",
            "gp": {k: gp[k] for k in ("surgery_name", "doctor_name", "surgery_phone", "surgery_postcode")} if gp else None,
            "health": {k: health[k] for k in health.keys() if k not in ("participant_id", "updated_at", "updated_by_account",
                                                                          "updated_by_staff")} if health else None,
            "safeguarding": {"family_info": sg["family_info"] if sg else None, "collection_alert": p["collection_alert"],
                             "saved": sg is not None},
            "consents": family.current_consents(c, p["account_id"], p["id"]),
            "consent_questions": formspec.consent_keys(level, months),
            "collection": {"set": bool(p["collection_pw_hash"])},
        }
    return h.json(data)


def _save_section(c, h, p, name, d):
    who = _who(h)
    level = "adult" if p["is_account_holder"] else p["target_level"]
    now = db.now()
    changed = []
    if name == "child":
        if p["is_account_holder"]:
            raise Invalid({"first_name": "Change your name under Your details."})
        v = formspec.clean("child", d, level)
        haf = v.get("haf_status") or p["haf_status"]
        if p["haf_status"] == "verified" and haf != "verified":
            haf = "verified"  # staff verified it; the parent can't undo that from here
        c.execute("UPDATE participants SET first_name=?, last_name=?, dob=?, gender=?, education=?, school_name=?,"
                  " haf_status=?, updated_at=? WHERE id=?",
                  (v["first_name"], v["last_name"], v["dob"], v.get("gender"), v.get("education"), v.get("school_name"),
                   haf, now, p["id"]))
        if v.get("haf_status") == "not_sure" and p["haf_status"] != "not_sure":
            intray.add(c, "haf_verify", "Check HAF eligibility for %s" % v["first_name"], perm="bookings.manage",
                       participant_id=p["id"], account_id=who["id"])
        changed = sorted(v)
    elif name == "gp":
        v = formspec.clean("gp", d, level)
        c.execute("INSERT INTO participant_gp(participant_id, surgery_name, doctor_name, surgery_phone, surgery_postcode,"
                  " updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT(participant_id) DO UPDATE SET surgery_name=excluded.surgery_name,"
                  " doctor_name=excluded.doctor_name, surgery_phone=excluded.surgery_phone,"
                  " surgery_postcode=excluded.surgery_postcode, updated_at=excluded.updated_at",
                  (p["id"], v["surgery_name"], v["doctor_name"], v["surgery_phone"], v["surgery_postcode"], now))
        changed = sorted(v)
    elif name == "health":
        v = formspec.clean("health", d, level)
        cols = [f["key"] for f in formspec.SPEC["health"]["fields"]]
        old = c.execute("SELECT * FROM participant_health WHERE participant_id=?", (p["id"],)).fetchone()
        values = [v.get(k) for k in cols]
        c.execute("INSERT INTO participant_health(participant_id, %s, updated_at, updated_by_account) VALUES (?, %s, ?, ?)"
                  " ON CONFLICT(participant_id) DO UPDATE SET %s, updated_at=excluded.updated_at,"
                  " updated_by_account=excluded.updated_by_account"
                  % (", ".join(cols), ", ".join("?" * len(cols)), ", ".join("%s=excluded.%s" % (k, k) for k in cols)),
                  [p["id"]] + values + [now, who["id"]])
        family.refresh_flags(c, p["id"])
        changed = [k for k in cols if (old[k] if old else None) != v.get(k)] if old else sorted(k for k in cols if v.get(k))
        if old and changed and booked_within(c, p["id"], days=7):
            intray.add(c, "health_changed", "Health details changed for %s (booked this week)" % p["first_name"],
                       perm="people.view_health", participant_id=p["id"], account_id=who["id"], dedupe=False)
    elif name == "safeguarding":
        v = formspec.clean("safeguarding", d, "full")
        old = c.execute("SELECT family_info FROM participant_safeguarding WHERE participant_id=?", (p["id"],)).fetchone()
        alert_changed = (v.get("collection_alert") or None) != (p["collection_alert"] or None)
        if alert_changed and p["collection_alert"] and _needs_reauth(h):
            return None
        c.execute("INSERT INTO participant_safeguarding(participant_id, family_info, updated_at, updated_by_account)"
                  " VALUES (?,?,?,?) ON CONFLICT(participant_id) DO UPDATE SET family_info=excluded.family_info,"
                  " updated_at=excluded.updated_at, updated_by_account=excluded.updated_by_account",
                  (p["id"], v.get("family_info"), now, who["id"]))
        c.execute("UPDATE participants SET collection_alert=?, updated_at=? WHERE id=?",
                  (v.get("collection_alert"), now, p["id"]))
        if v.get("family_info") and (not old or old["family_info"] != v.get("family_info")):
            c.execute("UPDATE participant_safeguarding SET dsl_reviewed_at=NULL, dsl_reviewed_by=NULL WHERE participant_id=?",
                      (p["id"],))
            intray.add(c, "safeguarding_info_submitted", "Safeguarding information to review (a child's record)",
                       perm="safeguarding.view", participant_id=p["id"], account_id=who["id"])
        if alert_changed:
            _collection_changed(c, h, p, "the “must not collect” note")
        changed = ["family_info", "collection_alert"]
    elif name == "consents":
        months = formspec.age_months(datetime.date.fromisoformat(p["dob"]))
        asked = formspec.consent_keys(level, months)
        answers = d.get("answers") or {}
        errors = {}
        for q in asked:
            if q["required"] and not answers.get(q["key"]):
                errors[q["key"]] = "Please choose an answer."
        if errors:
            raise Invalid(errors)
        current = family.current_consents(c, p["account_id"], p["id"])
        gha = answers.get("go_home_alone")
        if gha and current.get("go_home_alone") and gha != current["go_home_alone"] and _needs_reauth(h):
            return None  # checked before anything is written
        for q in asked:
            value = answers.get(q["key"])
            if value and current.get(q["key"]) != value:
                family.record_consent(c, p["account_id"], p["id"], q["key"], value, "profile", ip=h.client_ip())
                changed.append(q["key"])
                if q["key"] == "go_home_alone" and current.get("go_home_alone"):
                    _collection_changed(c, h, p, "going home alone")
    elif name == "collection":
        v = formspec.clean("collection", d, "full")
        if p["collection_pw_hash"] and _needs_reauth(h):
            return None
        if (d.get("confirm") or "") != (d.get("password") or ""):
            raise Invalid({"confirm": "The two passwords don't match."})
        family.set_collection_password(c, p["id"], v["password"])
        if p["collection_pw_hash"]:
            _collection_changed(c, h, p, "the collection password")
        changed = ["collection_password"]
    elif name == "target":
        target = d.get("target_level")
        if p["is_account_holder"] or target not in ("short", "full"):
            raise Invalid({"target_level": "Choose what they'll come to."})
        c.execute("UPDATE participants SET target_level=? WHERE id=?", (target, p["id"]))
        changed = ["target_level"]
    elif name == "reviewed":
        c.execute("UPDATE participants SET needs_review=0 WHERE id=?", (p["id"],))
        c.execute("UPDATE accounts SET reconfirmed_at=COALESCE(reconfirmed_at, ?) WHERE id=?", (now, who["id"]))
        changed = ["reviewed"]
    else:
        return h.json({"error": "Unknown section"}, 404)
    audit.record(c, h, "participant.%s_changed" % name, entity_type="participant", entity_id=p["id"],
                 participant_id=p["id"], account_id=who["id"], details={"fields": changed},
                 restricted=(name == "safeguarding"))
    family.compute_level(c, p["id"])
    return True


@route("POST", "/api/account/participants/<ref>/archive", auth="account")
def archive(h, ref):
    with db.tx() as c:
        p = _own(c, h, ref)
        if not p or p["is_account_holder"]:
            return h.json({"error": "Not found"}, 404)
        c.execute("UPDATE participants SET status='archived', updated_at=? WHERE id=?", (db.now(), p["id"]))
        audit.record(c, h, "participant.archived", entity_type="participant", entity_id=p["id"], participant_id=p["id"],
                     account_id=p["account_id"])
    return me(h)


@route("POST", "/api/account/participants/<ref>/<section>", auth="account")
def save_section(h, ref, section):
    d = h.json_body() or {}
    with db.tx() as c:
        p = _own(c, h, ref)
        if not p:
            return h.json({"error": "Not found"}, 404)
        result = _save_section(c, h, p, section, d)
        if result is not True:
            return result  # a response (reauth needed / unknown section) was already sent
    return get_participant(h, ref)
