"""Admin → People: advanced search, family and child records, edits,
duplicates and merging.

Search is a POST so that names never appear in URLs or server logs. Records
are split into tabs by permission: basic details for anyone with
people.view_basic, health for people.view_health, family safeguarding
information for the DSL only. Opening health or safeguarding information is
written to the audit log.

Searches can be saved (for yourself or shared), and the results used for
bulk actions: message the families, export, or add children to a waiting
list (see bookings_staff.waitlist_add)."""
import json

from . import audit, bookings, catalogue, db, family, incidents, money, validate
from .validate import Invalid
from .web import route

FLAG_FILTERS = ("allergy", "anaphylaxis", "medical", "dietary", "send", "semh")
MAX_REFS = 500


def _refs(d):
    """Only these rows (the ones ticked for a bulk action), or None for all matches."""
    refs = d.get("refs")
    if refs is None:
        return None
    if not isinstance(refs, list):
        raise ValueError("Choose some rows first.")
    return [r for r in refs if isinstance(r, str)][:MAX_REFS]


def _like(text):
    return "%" + text.replace("%", "").replace("_", "")[:60] + "%"


def search_rows(c, h, d):
    q = validate.text(d.get("q"), 80)
    scope = d.get("scope") if d.get("scope") in ("families", "children", "staff") else "children"
    f = d.get("filters") or {}
    if scope == "staff":
        if not h.has_perm("staff.manage"):
            raise ValueError("You don't have permission to search staff.")
        rows = c.execute("SELECT id, name, email, status FROM staff_users WHERE name LIKE ? OR email LIKE ? ORDER BY name"
                         " LIMIT 200", (_like(q), _like(q))).fetchall()
        return scope, [dict(r) for r in rows]
    if scope == "families":
        where, args = ["a.status NOT IN ('anonymised')"], []
        if q:
            where.append("(a.first_name || ' ' || a.last_name LIKE ? OR a.email LIKE ? OR a.mobile LIKE ? OR a.postcode"
                         " LIKE ? OR a.ref LIKE ? OR EXISTS (SELECT 1 FROM participants p WHERE p.account_id=a.id AND"
                         " (p.first_name || ' ' || p.last_name) LIKE ?))")
            digits = "".join(ch for ch in q if ch.isdigit())
            args += [_like(q), _like(q), _like(digits[-9:]) if len(digits) >= 6 else _like(q), _like(q), _like(q), _like(q)]
        if f.get("status") in ("active", "pending_activation", "pending_verification", "closed"):
            where.append("a.status=?")
            args.append(f["status"])
        if f.get("source") in ("self", "import", "staff", "walkin"):
            where.append("a.source=?")
            args.append(f["source"])
        refs = _refs(d)
        if refs is not None:
            where.append("a.ref IN (%s)" % ",".join("?" * len(refs)) if refs else "0")
            args += refs
        rows = c.execute("SELECT a.* FROM accounts a WHERE " + " AND ".join(where) + " ORDER BY a.last_name, a.first_name"
                         " LIMIT 200", args).fetchall()
        out = []
        for a in rows:
            kids = c.execute("SELECT first_name, dob FROM participants WHERE account_id=? AND status='active'"
                             " ORDER BY dob", (a["id"],)).fetchall()
            out.append({"ref": a["ref"], "name": "%s %s" % (a["first_name"], a["last_name"]), "email": a["email"],
                        "mobile": a["mobile"], "postcode": a["postcode"], "status": a["status"], "source": a["source"],
                        "children": ["%s (%d)" % (k["first_name"], family.age_years(k["dob"])) for k in kids]})
        return scope, out
    where, args = ["p.status='active'"], []
    if q:
        where.append("((p.first_name || ' ' || p.last_name) LIKE ? OR p.ref LIKE ? OR p.school_name LIKE ?"
                     " OR (a.first_name || ' ' || a.last_name) LIKE ?)")
        args += [_like(q)] * 4
    for k in FLAG_FILTERS:
        if f.get(k):
            if not h.has_perm("people.view_health"):
                raise ValueError("You don't have permission to search by health needs.")
            where.append("p.f_%s=1" % k)
    if f.get("haf") in ("claimed_eligible", "verified", "not_sure", "not_eligible"):
        where.append("p.haf_status=?")
        args.append(f["haf"])
    if f.get("level") in ("none", "short", "full", "adult"):
        where.append("p.level=?")
        args.append(f["level"])
    if f.get("needs_review"):
        where.append("p.needs_review=1")
    if (f.get("min_age") or "") != "" or (f.get("max_age") or "") != "":
        today = catalogue.uk_today()
        if str(f.get("min_age") or "").isdigit():
            where.append("p.dob<=?")
            args.append(today.replace(year=today.year - int(f["min_age"])).isoformat())
        if str(f.get("max_age") or "").isdigit():
            where.append("p.dob>?")
            args.append(today.replace(year=today.year - int(f["max_age"]) - 1).isoformat())
    refs = _refs(d)
    if refs is not None:
        where.append("p.ref IN (%s)" % ",".join("?" * len(refs)) if refs else "0")
        args += refs
    if str(f.get("activity") or "").isdigit():
        where.append("EXISTS (SELECT 1 FROM bookings b WHERE b.participant_id=p.id AND b.activity_id=? AND"
                     " b.status NOT IN ('cancelled','expired'))")
        args.append(int(f["activity"]))
    rows = c.execute("SELECT p.*, a.ref AS aref, a.first_name AS af, a.last_name AS al, a.mobile, a.email FROM participants p"
                     " JOIN accounts a ON a.id=p.account_id WHERE " + " AND ".join(where) +
                     " ORDER BY p.last_name, p.first_name LIMIT 300", args).fetchall()
    show_flags = h.has_perm("people.view_health") or h.has_perm("registers.view")
    return scope, [{"ref": p["ref"], "name": "%s %s" % (p["first_name"], p["last_name"]),
                    "age": family.age_years(p["dob"]), "dob": p["dob"], "level": p["level"],
                    "needs_review": bool(p["needs_review"]), "haf": p["haf_status"] if h.has_perm("bookings.manage") else None,
                    "flags": [k for k in FLAG_FILTERS if p["f_" + k]] if show_flags else [],
                    "family": {"ref": p["aref"], "name": "%s %s" % (p["af"], p["al"]), "mobile": p["mobile"],
                               "email": p["email"]}} for p in rows]


@route("POST", "/api/staff/search", auth="staff", perm="people.view_basic")
def search(h):
    with db.read() as c:
        scope, rows = search_rows(c, h, h.json_body() or {})
    return h.json({"scope": scope, "results": rows})


@route("POST", "/api/staff/search/export", auth="staff", perm="people.view_basic")
def search_export(h):
    d = h.json_body() or {}
    with db.tx() as c:
        scope, rows = search_rows(c, h, d)
        audit.record(c, h, "people.export", details={"scope": scope, "rows": len(rows)})
    if scope == "families":
        return h.csv("families.csv", ["Ref", "Name", "Email", "Mobile", "Postcode", "Status", "Children"],
                     [[r["ref"], r["name"], r["email"] or "", r["mobile"] or "", r["postcode"] or "", r["status"],
                       ", ".join(r["children"])] for r in rows])
    if scope == "staff":
        return h.csv("staff.csv", ["Name", "Email", "Status"], [[r["name"], r["email"], r["status"]] for r in rows])
    return h.csv("children.csv", ["Ref", "Name", "Age", "Form level", "Parent", "Mobile", "Email"],
                 [[r["ref"], r["name"], r["age"], r["level"], r["family"]["name"], r["family"]["mobile"] or "",
                   r["family"]["email"] or ""] for r in rows])


# ---------------------------------------------------------------- saved searches


def _saved_json(r, me):
    return {"id": r["id"], "name": r["name"], "scope": r["scope"], "q": r["q"], "filters": json.loads(r["filters"]),
            "shared": bool(r["shared"]), "mine": r["staff_id"] == me, "by": r["by"]}


@route("GET", "/api/staff/searches", auth="staff", perm="people.view_basic")
def saved_searches(h):
    me = h.staff()["id"]
    with db.read() as c:
        rows = c.execute("SELECT x.*, s.name AS by FROM saved_searches x JOIN staff_users s ON s.id=x.staff_id"
                         " WHERE x.staff_id=? OR x.shared=1 ORDER BY x.name COLLATE NOCASE", (me,)).fetchall()
    return h.json({"searches": [_saved_json(r, me) for r in rows]})


@route("POST", "/api/staff/searches", auth="staff", perm="people.view_basic")
def save_search(h):
    d = h.json_body() or {}
    name = validate.text(d.get("name"), 80)
    if not name:
        raise Invalid({"name": "Give the search a name."})
    scope = d.get("scope") if d.get("scope") in ("children", "families") else None
    if not scope:
        raise Invalid({"scope": "Only searches for children or families can be saved."})
    filters = d.get("filters") if isinstance(d.get("filters"), dict) else {}
    filters = {k: v for k, v in filters.items() if isinstance(k, str) and isinstance(v, (str, int, bool))}
    me = h.staff()["id"]
    with db.tx() as c:
        if c.execute("SELECT COUNT(*) FROM saved_searches WHERE staff_id=?", (me,)).fetchone()[0] >= 50:
            raise ValueError("You've saved 50 searches — delete some first.")
        sid = c.execute("INSERT INTO saved_searches(staff_id, name, scope, q, filters, shared, created_at)"
                        " VALUES (?,?,?,?,?,?,?)", (me, name, scope, validate.text(d.get("q"), 80),
                                                    json.dumps(filters), 1 if d.get("shared") else 0,
                                                    db.now())).lastrowid
        audit.record(c, h, "people.search_saved", entity_type="saved_search", entity_id=sid)
    return h.json({"ok": True, "id": sid})


@route("POST", "/api/staff/searches/<sid>/delete", auth="staff", perm="people.view_basic")
def delete_search(h, sid):
    with db.tx() as c:
        r = c.execute("SELECT * FROM saved_searches WHERE id=?", (int(sid) if sid.isdigit() else 0,)).fetchone()
        if not r or (r["staff_id"] != h.staff()["id"] and not h.has_perm("staff.manage")):
            raise LookupError
        c.execute("DELETE FROM saved_searches WHERE id=?", (r["id"],))
    return h.json({"ok": True})


# ---------------------------------------------------------------- records


def _account(c, ref):
    a = c.execute("SELECT * FROM accounts WHERE ref=? AND status<>'anonymised'", (ref,)).fetchone()
    if not a:
        raise LookupError
    return a


def _participant(c, ref):
    p = c.execute("SELECT * FROM participants WHERE ref=? AND status<>'anonymised'", (ref,)).fetchone()
    if not p:
        raise LookupError
    return p


@route("GET", "/api/staff/people/accounts/<ref>", auth="staff", perm="people.view_basic")
def account_record(h, ref):
    with db.tx() as c:
        a = _account(c, ref)
        audit.record(c, h, "account.view", entity_type="account", entity_id=a["id"], account_id=a["id"])
        kids = [dict(family.participant_summary(c, p), haf=p["haf_status"] if h.has_perm("bookings.manage") else None)
                for p in c.execute("SELECT * FROM participants WHERE account_id=? AND status<>'anonymised'"
                                   " ORDER BY is_account_holder DESC, dob", (a["id"],)).fetchall()]
        if not h.has_perm("people.view_health"):
            for k in kids:
                k["flags"] = {}
        out = {"account": {k: a[k] for k in ("ref", "kind", "email", "email_verified_at", "status", "first_name",
                                             "last_name", "mobile", "address_line1", "address_line2", "town", "postcode",
                                             "source", "pay_later_allowed", "staff_notes", "created_at", "last_login_at",
                                             "activated_at", "reconfirmed_at")},
               "participants": kids, "contacts": family.contacts(c, a["id"])}
        if h.has_perm("bookings.view"):
            today = catalogue.uk_today().isoformat()
            rows = c.execute("SELECT b.* FROM bookings b JOIN activity_sessions s ON s.id=b.session_id WHERE b.account_id=?"
                             " AND b.status<>'expired' ORDER BY s.date DESC LIMIT 100", (a["id"],)).fetchall()
            out["bookings"] = [dict(bookings.booking_json(c, b), id=b["id"]) for b in rows]
            out["upcoming"] = sum(1 for b in out["bookings"] if b["date"] >= today and b["status"] == "confirmed")
            out["invoices"] = [money.invoice_json(c, i, with_lines=False) for i in c.execute(
                "SELECT * FROM invoices WHERE account_id=? ORDER BY id DESC LIMIT 50", (a["id"],))]
            out["credit_pence"] = money.credit_balance(c, a["id"])
        m = c.execute("SELECT email_opt_in, sms_opt_in, unsubscribed_email_at FROM marketing_preferences WHERE account_id=?"
                      " OR email=?", (a["id"], a["email"] or "")).fetchone()
        out["marketing"] = dict(m) if m else None
        out["messages"] = [dict(r) for r in c.execute(
            "SELECT id, channel, template_key, subject, status, created_at FROM message_deliveries WHERE account_id=?"
            " AND secret IS NULL ORDER BY id DESC LIMIT 30", (a["id"],))] if h.has_perm("messaging.service") else []
        return h.json(out)


@route("GET", "/api/staff/people/participants/<ref>", auth="staff", perm="people.view_basic")
def participant_record(h, ref):
    tab = h.query().get("tab") or "basic"
    with db.tx() as c:
        p = _participant(c, ref)
        a = c.execute("SELECT ref, first_name, last_name FROM accounts WHERE id=?", (p["account_id"],)).fetchone()
        out = {"participant": dict(family.participant_summary(c, p), school_name=p["school_name"], education=p["education"],
                                   gender=p["gender"], photo=p["photo_consent"], collection_alert=p["collection_alert"],
                                   has_collection_password=bool(p["collection_pw_hash"]),
                                   support_plan=p["support_plan"],
                                   haf=p["haf_status"] if h.has_perm("bookings.manage") else None),
               "family": {"ref": a["ref"], "name": "%s %s" % (a["first_name"], a["last_name"])},
               "tabs": ["basic"] + (["health"] if h.has_perm("people.view_health") else [])
               + (["safeguarding"] if h.has_perm("safeguarding.view") else [])}
        if not h.has_perm("people.view_health"):
            out["participant"]["flags"] = {}
        out["consents"] = family.current_consents(c, p["account_id"], p["id"])
        if tab == "health":
            if not h.has_perm("people.view_health"):
                return h.json({"error": "You don't have permission to see health details."}, 403)
            hl = c.execute("SELECT * FROM participant_health WHERE participant_id=?", (p["id"],)).fetchone()
            gp = c.execute("SELECT * FROM participant_gp WHERE participant_id=?", (p["id"],)).fetchone()
            out["health"] = dict(hl) if hl else None
            out["gp"] = dict(gp) if gp else None
            audit.record(c, h, "participant.view_health", entity_type="participant", entity_id=p["id"],
                         participant_id=p["id"])
        elif tab == "safeguarding":
            if not h.has_perm("safeguarding.view"):
                return h.json({"error": "Only the Designated Safeguarding Lead can see this."}, 403)
            sg = c.execute("SELECT * FROM participant_safeguarding WHERE participant_id=?", (p["id"],)).fetchone()
            out["safeguarding"] = dict(sg) if sg else None
            out["flag_safeguarding"] = bool(p["f_safeguarding"])
            audit.record(c, h, "participant.view_safeguarding", entity_type="participant", entity_id=p["id"],
                         participant_id=p["id"], restricted=True)
        else:
            audit.record(c, h, "participant.view", entity_type="participant", entity_id=p["id"], participant_id=p["id"])
        if h.has_perm("bookings.view"):
            out["bookings"] = [dict(bookings.booking_json(c, b), id=b["id"]) for b in c.execute(
                "SELECT b.* FROM bookings b JOIN activity_sessions s ON s.id=b.session_id WHERE b.participant_id=?"
                " AND b.status<>'expired' ORDER BY s.date DESC LIMIT 100", (p["id"],)).fetchall()]
            present = c.execute("SELECT COUNT(*) FROM attendance WHERE participant_id=? AND status='present'",
                                (p["id"],)).fetchone()[0]
            out["attended"] = present
        if h.has_perm("incidents.view"):
            rows = c.execute("SELECT i.* FROM incidents i JOIN incident_people ip ON ip.incident_id=i.id WHERE"
                             " ip.participant_id=? AND (i.restricted=0 OR ?) ORDER BY i.occurred_at DESC",
                             (p["id"], 1 if h.has_perm("safeguarding.view") else 0)).fetchall()
            out["incidents"] = [incidents.incident_json(c, i) for i in rows]
        return h.json(out)


@route("POST", "/api/staff/people/participants/<ref>/update", auth="staff", perm="people.edit")
def update_participant(h, ref):
    d = h.json_body() or {}
    with db.tx() as c:
        p = _participant(c, ref)
        fields = {}
        for k, n in (("first_name", 60), ("last_name", 60), ("school_name", 120)):
            if k in d:
                v = validate.text(d[k], n)
                if not v and k != "school_name":
                    raise Invalid({k: "This can't be empty."})
                fields[k] = v or None
        if "dob" in d:
            dob = validate.date(d["dob"])
            if not dob or dob > catalogue.uk_today():
                raise Invalid({"dob": "Enter a real date of birth."})
            fields["dob"] = dob.isoformat()
        if "haf_status" in d:
            if not h.has_perm("bookings.manage") or d["haf_status"] not in ("unknown", "claimed_eligible", "not_eligible",
                                                                            "not_sure", "verified"):
                raise ValueError("You can't change HAF status.")
            fields["haf_status"] = d["haf_status"]
            if d["haf_status"] == "verified":
                fields.update(haf_verified_at=db.now(), haf_verified_by=h.staff()["id"])
        if "collection_alert" in d:
            fields["collection_alert"] = validate.long_text(d["collection_alert"], 500) or None
        if "needs_review" in d:
            fields["needs_review"] = 1 if d["needs_review"] else 0
        if "f_safeguarding" in d:
            if not h.has_perm("safeguarding.view"):
                raise ValueError("Only the DSL can set the safeguarding flag.")
            fields["f_safeguarding"] = 1 if d["f_safeguarding"] else 0
            c.execute("UPDATE participant_safeguarding SET dsl_reviewed_at=?, dsl_reviewed_by=? WHERE participant_id=?",
                      (db.now(), h.staff()["id"], p["id"]))
        if fields:
            fields["updated_at"] = db.now()
            c.execute("UPDATE participants SET %s WHERE id=?" % ",".join("%s=?" % k for k in fields), [*fields.values(), p["id"]])
            family.compute_level(c, p["id"])
            audit.record(c, h, "participant.staff_edit", entity_type="participant", entity_id=p["id"], participant_id=p["id"],
                         restricted="f_safeguarding" in fields, details={"changed": sorted(k for k in fields if k != "updated_at")})
    return h.json({"ok": True})


@route("POST", "/api/staff/people/accounts/<ref>/update", auth="staff", perm="people.edit")
def update_account(h, ref):
    d = h.json_body() or {}
    with db.tx() as c:
        a = _account(c, ref)
        fields = {}
        for k, n in (("first_name", 60), ("last_name", 60), ("address_line1", 120), ("address_line2", 120), ("town", 80)):
            if k in d:
                fields[k] = validate.text(d[k], n) or None
        if "mobile" in d:
            m = validate.uk_mobile(d["mobile"]) if d["mobile"] else None
            if d["mobile"] and not m:
                raise Invalid({"mobile": "Enter a UK mobile number."})
            fields["mobile"] = m
        if "postcode" in d:
            pc = validate.postcode(d["postcode"]) if d["postcode"] else None
            if d["postcode"] and not pc:
                raise Invalid({"postcode": "Enter a UK postcode."})
            fields["postcode"] = pc
        if "email" in d:
            if a["password_hash"]:
                raise ValueError("The family changes their own email address (it's how they sign in).")
            e = validate.email(d["email"]) if d["email"] else None
            if d["email"] and not e:
                raise Invalid({"email": "Check the email address."})
            if e and c.execute("SELECT 1 FROM accounts WHERE email=? AND id<>? AND status<>'anonymised'", (e, a["id"])).fetchone():
                raise Invalid({"email": "Another account uses this email — merge them instead."})
            fields["email"] = e
        if "staff_notes" in d:
            fields["staff_notes"] = validate.long_text(d["staff_notes"], 3000) or None
        for k in ("first_name", "last_name"):
            if k in fields and not fields[k]:
                raise Invalid({k: "This can't be empty."})
        if fields:
            fields["updated_at"] = db.now()
            c.execute("UPDATE accounts SET %s WHERE id=?" % ",".join("%s=?" % k for k in fields), [*fields.values(), a["id"]])
            audit.record(c, h, "account.staff_edit", entity_type="account", entity_id=a["id"], account_id=a["id"],
                         details={"changed": sorted(k for k in fields if k != "updated_at")})
    return h.json({"ok": True})


@route("POST", "/api/staff/people/accounts/<ref>/activation", auth="staff", perm="people.edit")
def resend_activation(h, ref):
    from . import accounts
    with db.tx() as c:
        a = _account(c, ref)
        if a["status"] != "pending_activation" or not a["email"]:
            raise ValueError("Only accounts waiting to be activated (with an email address) can be sent a link.")
        accounts.send_activation(c, h, a, reminder=True)
        audit.record(c, h, "account.activation_sent", entity_type="account", entity_id=a["id"], account_id=a["id"])
    return h.json({"ok": True})


# ---------------------------------------------------------------- duplicates and merging


@route("GET", "/api/staff/people/duplicates", auth="staff", perm="people.edit")
def duplicates(h):
    with db.read() as c:
        kids = [dict(r) for r in c.execute(
            "SELECT lower(p.first_name) || ' ' || lower(p.last_name) AS name, p.dob, GROUP_CONCAT(a.ref, ' ') AS families,"
            " COUNT(*) AS n FROM participants p JOIN accounts a ON a.id=p.account_id WHERE p.status='active'"
            " AND a.status NOT IN ('closed','anonymised') GROUP BY 1, 2 HAVING COUNT(DISTINCT p.account_id)>1 LIMIT 100")]
        phones = [dict(r) for r in c.execute(
            "SELECT mobile, GROUP_CONCAT(ref, ' ') AS families, COUNT(*) AS n FROM accounts WHERE mobile IS NOT NULL"
            " AND status NOT IN ('closed','anonymised') GROUP BY mobile HAVING COUNT(*)>1 LIMIT 100")]
    return h.json({"children": kids, "mobiles": phones})


MOVE = [("participants", "account_id"), ("bookings", "account_id"), ("invoices", "account_id"),
        ("payments", "account_id"), ("refunds", "account_id"), ("credit_notes", "account_id"),
        ("consents", "account_id"), ("incident_people", "account_id"), ("message_deliveries", "account_id"),
        ("guest_contacts", "account_id"), ("checkouts", "account_id")]


@route("POST", "/api/staff/people/accounts/<ref>/merge", auth="staff", perm="people.edit")
def merge(h, ref):
    """Move everything from this account into another (a family registered
    twice, or an import that matched an existing sign-up), then close it."""
    d = h.json_body() or {}
    if not h.has_perm("bookings.manage"):
        raise ValueError("Merging needs the bookings permission too.")
    with db.tx() as c:
        src, dst = _account(c, ref), _account(c, d.get("into_ref") or "")
        if src["id"] == dst["id"]:
            raise ValueError("Choose a different account to merge into.")
        moved = {}
        for table, col in MOVE:
            moved[table] = c.execute("UPDATE %s SET %s=? WHERE %s=?" % (table, col, col), (dst["id"], src["id"])).rowcount
        start = c.execute("SELECT COALESCE(MAX(priority),0) FROM emergency_contacts WHERE account_id=?", (dst["id"],)).fetchone()[0]
        for i, x in enumerate(c.execute("SELECT id FROM emergency_contacts WHERE account_id=? ORDER BY priority",
                                        (src["id"],)).fetchall()):
            c.execute("UPDATE emergency_contacts SET account_id=?, priority=? WHERE id=?", (dst["id"], start + i + 1, x["id"]))
        c.execute("UPDATE accounts SET status='closed', closed_at=?, staff_notes=COALESCE(staff_notes,'') || ? WHERE id=?",
                  (db.now(), "\nMerged into %s on %s." % (dst["ref"], catalogue.uk_today().isoformat()), src["id"]))
        c.execute("DELETE FROM account_sessions WHERE account_id=?", (src["id"],))
        if not dst["pay_later_allowed"] and src["pay_later_allowed"]:
            c.execute("UPDATE accounts SET pay_later_allowed=1 WHERE id=?", (dst["id"],))
        family.recompute_account(c, dst["id"])
        audit.record(c, h, "account.merge", entity_type="account", entity_id=dst["id"], account_id=dst["id"],
                     details={"from": src["ref"], "moved": moved})
    return h.json({"ok": True, "moved": moved})
