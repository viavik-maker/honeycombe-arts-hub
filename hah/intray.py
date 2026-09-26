"""The staff in-tray: things that need someone to act.

Raise items with add() inside the transaction that caused them. Titles are
seen by every staff member with the item's permission, so they never hold
health or safeguarding details (a child's first name at most)."""
from . import db


def add(c, type_, title, *, perm, detail=None, entity_type=None, entity_id=None, account_id=None,
        participant_id=None, centre_id=None, activity_id=None, session_id=None, dedupe=True):
    """Raise an in-tray item (skipped if an identical open one exists)."""
    if dedupe and c.execute(
            "SELECT 1 FROM intray_items WHERE status<>'done' AND type=? AND entity_type IS ? AND entity_id IS ?"
            " AND account_id IS ? AND participant_id IS ? AND session_id IS ?",
            (type_, entity_type, entity_id, account_id, participant_id, session_id)).fetchone():
        return None
    return c.execute(
        "INSERT INTO intray_items(type, title, detail, entity_type, entity_id, account_id, participant_id, centre_id,"
        " activity_id, session_id, required_perm, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (type_, title[:200], detail, entity_type, entity_id, account_id, participant_id, centre_id, activity_id,
         session_id, perm, db.now())).lastrowid


def resolve(c, type_, *, entity_type=None, entity_id=None, staff_id=None):
    """Close open items of TYPE for an entity (e.g. when the booking is approved)."""
    c.execute("UPDATE intray_items SET status='done', resolved_at=?, resolved_by=? WHERE status<>'done' AND type=?"
              " AND entity_type IS ? AND entity_id IS ?", (db.now(), staff_id, type_, entity_type, entity_id))


# ---------------------------------------------------------------- the staff in-tray (Admin → In-tray)

from .web import route  # noqa: E402  (routes live beside the helpers they use)

TYPE_NAMES = {
    "booking_approval": "Booking to approve", "haf_verify": "HAF eligibility to check", "voucher_payment": "Voucher / TFC payment",
    "place_freed": "Place free (waiting list)", "absence_reported": "Absence reported", "parent_cancelled": "Cancelled by family",
    "new_registration_needs": "New registration with needs", "safeguarding_info_submitted": "Safeguarding information",
    "safeguarding_concern": "Safeguarding concern", "duplicate_child": "Possible duplicate child",
    "health_changed": "Health details changed", "collection_changed": "Collection details changed",
    "payment_problem": "Payment problem", "late_payment": "Late card payment", "payment_dispute": "Card dispute",
    "refund_failed": "Refund failed", "incident_follow_up": "Incident follow-up", "publish_blocked": "Activity couldn't go live",
    "activation_problem": "Activation problem", "walkin_follow_up": "Walk-in to follow up", "haf_claim": "HAF claim",
    "contact_message": "Contact message", "send_intake": "SEND support request", "import_review": "Import to review",
}


def _visible_sql(perms):
    perms = sorted(perms) or ["-"]
    return "required_perm IN (%s)" % ",".join("?" * len(perms)), perms


@route("GET", "/api/staff/intray", auth="staff")
def intray_list(h):
    q = h.query()
    staff = h.staff()
    cond, args = _visible_sql(staff["perms"])
    view = q.get("view") or "open"
    now = db.now()
    where = [cond]
    if view == "open":
        where.append("(status='open' OR (status='snoozed' AND snooze_until<=?))")
        args.append(now)
    elif view == "snoozed":
        where.append("status='snoozed' AND snooze_until>?")
        args.append(now)
    elif view == "mine":
        where.append("status<>'done' AND assigned_staff_id=?")
        args.append(staff["id"])
    else:
        where.append("status='done'")
    if q.get("type"):
        where.append("type=?")
        args.append(q["type"])
    for k in ("centre", "activity", "session"):
        if (q.get(k) or "").isdigit():
            where.append("%s_id=?" % k)
            args.append(int(q[k]))
    with db.read() as c:
        rows = c.execute("SELECT * FROM (SELECT i.*, s.name AS assignee FROM intray_items i LEFT JOIN staff_users s"
                         " ON s.id=i.assigned_staff_id) WHERE " + " AND ".join(where) + " ORDER BY created_at DESC LIMIT 300",
                         args).fetchall()
        cond2, args2 = _visible_sql(staff["perms"])
        counts = {r[0]: r[1] for r in c.execute(
            "SELECT type, COUNT(*) FROM intray_items WHERE " + cond2 + " AND (status='open' OR (status='snoozed' AND"
            " snooze_until<=?)) GROUP BY type", args2 + [now])}
        refs = {}
        for r in rows:  # links for the UI
            if r["participant_id"] and "p%d" % r["participant_id"] not in refs:
                x = c.execute("SELECT ref FROM participants WHERE id=?", (r["participant_id"],)).fetchone()
                refs["p%d" % r["participant_id"]] = x["ref"] if x else None
            if r["account_id"] and "a%d" % r["account_id"] not in refs:
                x = c.execute("SELECT ref FROM accounts WHERE id=?", (r["account_id"],)).fetchone()
                refs["a%d" % r["account_id"]] = x["ref"] if x else None
    items = [{"id": r["id"], "type": r["type"], "type_name": TYPE_NAMES.get(r["type"], r["type"].replace("_", " ")),
              "title": r["title"], "detail": r["detail"], "status": r["status"], "created_at": r["created_at"],
              "snooze_until": r["snooze_until"], "assignee": r["assignee"], "entity_type": r["entity_type"],
              "entity_id": r["entity_id"], "session_id": r["session_id"], "activity_id": r["activity_id"],
              "participant_ref": refs.get("p%d" % r["participant_id"]) if r["participant_id"] else None,
              "account_ref": refs.get("a%d" % r["account_id"]) if r["account_id"] else None} for r in rows]
    return h.json({"items": items, "counts": counts, "total_open": sum(counts.values()),
                   "types": {k: TYPE_NAMES.get(k, k) for k in counts}})


@route("POST", "/api/staff/intray/<iid>", auth="staff")
def intray_action(h, iid):
    from . import audit
    d = h.json_body() or {}
    staff = h.staff()
    with db.tx() as c:
        item = c.execute("SELECT * FROM intray_items WHERE id=?", (int(iid),)).fetchone()
        if not item or item["required_perm"] not in staff["perms"]:
            raise LookupError
        action = d.get("action")
        if action == "done":
            c.execute("UPDATE intray_items SET status='done', resolved_at=?, resolved_by=? WHERE id=?",
                      (db.now(), staff["id"], item["id"]))
        elif action == "reopen":
            c.execute("UPDATE intray_items SET status='open', resolved_at=NULL, resolved_by=NULL, snooze_until=NULL"
                      " WHERE id=?", (item["id"],))
        elif action == "snooze":
            import datetime
            try:
                days = max(1, min(int(d.get("days") or 1), 60))
            except (TypeError, ValueError):
                days = 1
            until = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            c.execute("UPDATE intray_items SET status='snoozed', snooze_until=? WHERE id=?", (until, item["id"]))
        elif action == "assign":
            to = d.get("staff_id")
            if to is not None and not c.execute("SELECT 1 FROM staff_users WHERE id=? AND status='active'", (to,)).fetchone():
                raise ValueError("Choose a member of staff.")
            c.execute("UPDATE intray_items SET assigned_staff_id=? WHERE id=?", (to, item["id"]))
        else:
            raise ValueError("Unknown action.")
        audit.record(c, h, "intray.%s" % action, entity_type="intray", entity_id=item["id"],
                     restricted=item["required_perm"] == "safeguarding.view")
    return h.json({"ok": True})
