"""Admin → Bookings and Waiting list: find bookings, approve or decline
them, cancel (with a credit note, refund or account credit), move a child to
another session, and manage the waiting list."""
from . import (audit, bookings, catalogue, db, eligibility, family, intray, invoice_page, money, payments_stripe, validate,
               waitlist)
from .validate import Invalid
from .web import route

QUICK = {
    "approval": "b.status='pending_approval'",
    "waitlist": "b.status IN ('waitlisted','offered')",
    "unpaid": "EXISTS (SELECT 1 FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id WHERE l.booking_id=b.id"
              " AND i.status IN ('issued','part_paid'))",
    "cancelled": "b.status='cancelled'",
    "trials": "b.is_trial=1",
    "upcoming": "s.date>=:today AND b.status IN ('confirmed','pending_payment','pending_approval','offered')",
}


def _booking(c, bid):
    b = c.execute("SELECT * FROM bookings WHERE id=?", (int(bid),)).fetchone()
    if not b:
        raise LookupError
    return b


def row_json(c, b):
    out = bookings.booking_json(c, b)
    out["id"] = b["id"]
    out["created_at"] = b["created_at"]
    out["created_via"] = b["created_via"]
    out["pay_later"] = bool(b["pay_later"])
    out["profile_incomplete"] = bool(b["profile_incomplete"])
    out["waitlist_priority"] = b["waitlist_priority"]
    out["session_id"] = b["session_id"]
    out["activity_id"] = b["activity_id"]
    if b["account_id"]:
        a = c.execute("SELECT ref, first_name, last_name, email, mobile FROM accounts WHERE id=?",
                      (b["account_id"],)).fetchone()
        out["family"] = {"ref": a["ref"], "name": "%s %s" % (a["first_name"], a["last_name"]), "email": a["email"],
                         "mobile": a["mobile"]}
    elif b["guest_contact_id"]:
        g = c.execute("SELECT email, phone, name FROM guest_contacts WHERE id=?", (b["guest_contact_id"],)).fetchone()
        out["family"] = {"ref": None, "name": g["name"] or b["party_name"] or "Guest", "email": g["email"],
                         "mobile": g["phone"], "guest": True}
    if b["participant_id"]:
        p = c.execute("SELECT * FROM participants WHERE id=?", (b["participant_id"],)).fetchone()
        out["who"].update(last_name=p["last_name"], age=family.age_years(p["dob"]), haf_status=p["haf_status"],
                          level=p["level"])
    return out


@route("GET", "/api/staff/bookings", auth="staff", perm="bookings.view")
def list_bookings(h):
    q = h.query()
    where, args = ["1=1"], {"today": catalogue.uk_today().isoformat()}
    if q.get("quick") in QUICK:
        where.append(QUICK[q["quick"]])
    if q.get("status"):
        where.append("b.status=:status")
        args["status"] = q["status"]
    for k in ("activity", "session"):
        if (q.get(k) or "").isdigit():
            where.append("b.%s_id=:%s" % (k, k))
            args[k] = int(q[k])
    if validate.date(q.get("from")):
        where.append("s.date>=:from")
        args["from"] = q["from"]
    if validate.date(q.get("to")):
        where.append("s.date<=:to")
        args["to"] = q["to"]
    if q.get("funding") in ("paid", "haf", "free", "staff_comp", "prepaid_legacy"):
        where.append("b.funding=:funding")
        args["funding"] = q["funding"]
    text = (q.get("q") or "").strip()
    if text:
        where.append("(b.ref LIKE :t OR p.first_name LIKE :t OR p.last_name LIKE :t OR a.last_name LIKE :t OR"
                     " a.email LIKE :t OR g.email LIKE :t OR (p.first_name || ' ' || p.last_name) LIKE :t)")
        args["t"] = "%" + text[:60] + "%"
    page = max(int(q["page"]) if (q.get("page") or "").isdigit() else 1, 1)
    sql = ("SELECT b.* FROM bookings b JOIN activity_sessions s ON s.id=b.session_id"
           " LEFT JOIN participants p ON p.id=b.participant_id LEFT JOIN accounts a ON a.id=b.account_id"
           " LEFT JOIN guest_contacts g ON g.id=b.guest_contact_id WHERE " + " AND ".join(where))
    order = " ORDER BY b.waitlist_priority DESC, b.created_at" if q.get("quick") == "waitlist" else \
        " ORDER BY s.date, s.start_time, b.id"
    with db.read() as c:
        total = c.execute("SELECT COUNT(*) FROM (%s)" % sql, args).fetchone()[0]
        rows = c.execute(sql + order + " LIMIT 100 OFFSET %d" % ((page - 1) * 100), args).fetchall()
        out = [row_json(c, b) for b in rows]
    return h.json({"bookings": out, "total": total, "page": page, "pages": max((total + 99) // 100, 1)})


@route("GET", "/api/staff/bookings/<bid>", auth="staff", perm="bookings.view")
def get_booking(h, bid):
    with db.read() as c:
        b = _booking(c, bid)
        out = row_json(c, b)
        out["notes"] = b["notes"]
        out["cancel_reason"] = b["cancel_reason"]
        out["history"] = [dict(at=r["at"], action=r["action"], by=r["actor_name"] or r["actor_type"])
                          for r in c.execute("SELECT at, action, actor_name, actor_type FROM audit_log WHERE"
                                             " (entity_type='booking' AND entity_id=?) OR (entity_type='checkout'"
                                             " AND entity_id=?) ORDER BY id", (b["id"], b["checkout_id"]))]
        inv = money.invoice_for_booking(c, b["id"])
        out["invoice_detail"] = money.invoice_json(c, inv) if inv else None
        out["cancel_terms"] = bookings.cancel_terms(c, b)
    return h.json({"booking": out})


@route("POST", "/api/staff/bookings/<bid>/approve", auth="staff", perm="bookings.manage")
def approve(h, bid):
    with db.tx() as c:
        b = bookings.approve(c, h, _booking(c, bid))
        return h.json({"ok": True, "booking": row_json(c, b)})


@route("POST", "/api/staff/bookings/<bid>/decline", auth="staff", perm="bookings.manage")
def decline(h, bid):
    d = h.json_body() or {}
    with db.tx() as c:
        bookings.decline(c, h, _booking(c, bid), validate.text(d.get("reason"), 300),
                         haf_not_eligible=bool(d.get("haf_not_eligible")))
    return h.json({"ok": True})


@route("POST", "/api/staff/bookings/approve-haf", auth="staff", perm="bookings.manage")
def approve_haf_bulk(h):
    """Verify HAF claims in bulk: approves every waiting HAF booking of the
    chosen children and marks them verified."""
    d = h.json_body() or {}
    ids = [int(x) for x in d.get("booking_ids") or [] if str(x).isdigit()][:200]
    n = 0
    with db.tx() as c:
        for bid in ids:
            b = c.execute("SELECT * FROM bookings WHERE id=? AND status='pending_approval' AND approval_reason='haf_claim'",
                          (bid,)).fetchone()
            if b:
                bookings.approve(c, h, b)
                n += 1
    return h.json({"ok": True, "approved": n})


@route("POST", "/api/staff/bookings/<bid>/cancel", auth="staff", perm="bookings.manage")
def cancel(h, bid):
    d = h.json_body() or {}
    outcome = d.get("outcome") or "none"
    if outcome not in ("none", "credit", "refund_card", "refund_offline"):
        raise ValueError("Choose what happens to the money.")
    reason = validate.text(d.get("reason"), 300)
    if not reason:
        raise Invalid({"reason": "Give a reason (the family sees it in their email)."})
    amount = d.get("amount_pence")
    amount = int(amount) if str(amount or "").isdigit() else None
    with db.tx() as c:
        b = _booking(c, bid)
        if outcome == "refund_card" and not payments_stripe.configured():
            raise ValueError("Card refunds need Stripe set up — choose account credit or an offline refund.")
        refunded = bookings.cancel(c, h, b, reason=reason, money_outcome=outcome, by_staff=h.staff()["id"],
                                   notify=d.get("notify", True), amount=amount)
    return h.json({"ok": True, "refunded_pence": refunded})


@route("POST", "/api/staff/bookings/<bid>/move", auth="staff", perm="bookings.manage")
def move(h, bid):
    """Move a child to another session of the same price (Phase 1)."""
    d = h.json_body() or {}
    with db.tx() as c:
        b = _booking(c, bid)
        if b["status"] not in ("confirmed", "pending_approval"):
            raise ValueError("Only confirmed or pending bookings can be moved.")
        target = c.execute("SELECT * FROM activity_sessions WHERE id=?", (int(d.get("session_id") or 0),)).fetchone()
        if not target or target["status"] != "scheduled":
            raise ValueError("Choose a session that's going ahead.")
        if target["id"] == b["session_id"]:
            raise ValueError("That's the session it's already on.")
        a2 = c.execute("SELECT * FROM activities WHERE id=?", (target["activity_id"],)).fetchone()
        price = 0 if a2["haf_only"] else (bookings.trial_price(a2, target) if b["is_trial"] and a2["id"] == b["activity_id"]
                                          else catalogue.price_of(a2, target))
        was = b["price_pence"] + b["discount_pence"]  # compare list prices; any discount moves with the booking
        if price != was:
            raise ValueError("That session costs %s, not %s — cancel and rebook instead." % (
                money.pounds(price), money.pounds(was)))
        if waitlist.free_places(c, target) < b["places"] and not d.get("override"):
            raise ValueError("That session is full.")
        if d.get("override") and not h.has_perm("bookings.override"):
            raise ValueError("You don't have permission to overbook a session.")
        if b["participant_id"] and c.execute("SELECT 1 FROM bookings WHERE session_id=? AND participant_id=? AND status"
                                             " NOT IN ('cancelled','expired')", (target["id"], b["participant_id"])).fetchone():
            raise ValueError("They're already booked on that session.")
        old = b["session_id"]
        c.execute("UPDATE bookings SET session_id=?, activity_id=?, updated_at=? WHERE id=?",
                  (target["id"], a2["id"], db.now(), b["id"]))
        c.execute("UPDATE attendance SET session_id=? WHERE booking_id=?", (target["id"], b["id"]))
        c.execute("UPDATE invoice_lines SET description=?, service_date=? WHERE booking_id=?",
                  (money.line_for(c, c.execute("SELECT * FROM bookings WHERE id=?", (b["id"],)).fetchone())[0],
                   target["date"], b["id"]))
        audit.record(c, h, "booking.move", entity_type="booking", entity_id=b["id"], account_id=b["account_id"],
                     participant_id=b["participant_id"], details={"from": old, "to": target["id"],
                                                                  "override": bool(d.get("override"))})
        waitlist.places_freed(c, old, h)
        return h.json({"ok": True, "booking": row_json(c, _booking(c, b["id"]))})


@route("POST", "/api/staff/bookings/<bid>/resend", auth="staff", perm="bookings.manage")
def resend(h, bid):
    with db.tx() as c:
        b = _booking(c, bid)
        acct = c.execute("SELECT * FROM accounts WHERE id=?", (b["account_id"],)).fetchone() if b["account_id"] else None
        if not acct or not acct["email"]:
            raise ValueError("This family has no email address.")
        bookings.send_summary(c, h, acct, [b], money.invoice_for_booking(c, b["id"]))
        audit.record(c, h, "booking.resend", entity_type="booking", entity_id=b["id"])
    return h.json({"ok": True})


@route("POST", "/api/staff/bookings/<bid>/notes", auth="staff", perm="bookings.manage")
def notes(h, bid):
    d = h.json_body() or {}
    with db.tx() as c:
        b = _booking(c, bid)
        c.execute("UPDATE bookings SET notes=?, updated_at=? WHERE id=?",
                  (validate.long_text(d.get("notes"), 1000) or None, db.now(), b["id"]))
        audit.record(c, h, "booking.notes", entity_type="booking", entity_id=b["id"])
    return h.json({"ok": True})


@route("POST", "/api/staff/bookings/<bid>/trial", auth="staff", perm="bookings.manage")
def mark_trial(h, bid):
    """Mark (or unmark) a booking as a trial, for the conversion report. The price doesn't change."""
    d = h.json_body() or {}
    on = d.get("is_trial") is True
    with db.tx() as c:
        b = _booking(c, bid)
        if b["kind"] != "participant":
            raise ValueError("Only a child's or young adult's booking can be a trial.")
        if on and c.execute("SELECT 1 FROM bookings WHERE activity_id=? AND participant_id=? AND is_trial=1 AND id<>?"
                            " AND status IN " + catalogue.HOLDING_SQL,
                            (b["activity_id"], b["participant_id"], b["id"])).fetchone():
            raise ValueError("They already have a trial of this activity.")
        c.execute("UPDATE bookings SET is_trial=?, updated_at=? WHERE id=?", (1 if on else 0, db.now(), b["id"]))
        audit.record(c, h, "booking.trial", entity_type="booking", entity_id=b["id"], details={"is_trial": on})
    return h.json({"ok": True})


# ---------------------------------------------------------------- waiting list


@route("GET", "/api/staff/sessions/<sid>/waitlist", auth="staff", perm="bookings.view")
def session_waitlist(h, sid):
    with db.read() as c:
        s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (int(sid),)).fetchone()
        if not s:
            raise LookupError
        rows = c.execute("SELECT * FROM bookings WHERE session_id=? AND status IN ('waitlisted','offered')"
                         " ORDER BY status='offered' DESC, waitlist_priority DESC, created_at, id", (s["id"],)).fetchall()
        return h.json({"free": waitlist.free_places(c, s), "entries": [row_json(c, b) for b in rows]})


@route("POST", "/api/staff/bookings/waitlist-add", auth="staff", perm="bookings.manage")
def waitlist_add(h):
    """Bulk action from a People search: put the chosen children on a session's waiting list. Brothers and sisters
    are grouped, so they're offered places together. If there's room now, it's offered straight away."""
    from .staff_bookings import OVERRIDE
    d = h.json_body() or {}
    refs = [r for r in (d.get("participant_refs") or []) if isinstance(r, str)][:200]
    override = bool(d.get("override"))
    if override and not h.has_perm("bookings.override"):
        raise ValueError("You don't have permission to override booking rules.")
    if not refs:
        raise ValueError("Choose some children first.")
    with db.tx() as c:
        s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (int(d.get("session_id") or 0),)).fetchone()
        if not s or s["status"] != "scheduled" or s["date"] < catalogue.uk_today().isoformat():
            raise ValueError("Choose an upcoming session.")
        a = c.execute("SELECT * FROM activities WHERE id=?", (s["activity_id"],)).fetchone()
        if a["registration_level"] == "guest":
            raise ValueError("One-off events don't have a waiting list for children — book them as a party instead.")
        added, skipped = [], []
        for ref in refs:
            p = c.execute("SELECT * FROM participants WHERE ref=? AND status='active'", (ref,)).fetchone()
            if not p:
                continue
            acct = c.execute("SELECT * FROM accounts WHERE id=? AND status NOT IN ('closed','anonymised')",
                             (p["account_id"],)).fetchone()
            if not acct:
                skipped.append("%s: their family's account is closed" % p["first_name"])
                continue
            problems = eligibility.Checker(c, acct).problems(a, s, p)
            codes = {k for k, _ in problems}
            if "booked" in codes or (codes & OVERRIDE and not override):
                skipped.append("%s: %s" % (p["first_name"], next(m for k, m in problems if k == "booked" or k in OVERRIDE)))
                continue
            item = {"kind": "participant", "session": s, "activity": a, "participant": p}
            price = bookings._price(item)
            b = bookings._insert_booking(
                c, item, "waitlisted", account_id=acct["id"], price=price,
                funding="haf" if a["haf_only"] else ("free" if price == 0 else "paid"),
                group="S-%s:%d" % (acct["ref"], s["id"]), via="staff", staff_id=h.staff()["id"],
                notes="Added from a search" + ((" (override: %s)" % validate.text(d.get("reason"), 200)) if override else ""))
            added.append(b["id"])
        offered = waitlist.places_freed(c, s["id"], h) if added else 0
        audit.record(c, h, "waitlist.bulk_add", entity_type="session", entity_id=s["id"],
                     details={"added": len(added), "skipped": len(skipped), "override": override})
    return h.json({"ok": True, "added": len(added), "skipped": skipped, "offered": offered})


@route("POST", "/api/staff/bookings/<bid>/offer", auth="staff", perm="bookings.manage")
def offer(h, bid):
    with db.tx() as c:
        b = _booking(c, bid)
        if b["status"] != "waitlisted":
            raise ValueError("Only people on the waiting list can be offered a place.")
        s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (b["session_id"],)).fetchone()
        a = c.execute("SELECT * FROM activities WHERE id=?", (b["activity_id"],)).fetchone()
        if waitlist.free_places(c, s) < b["places"] and not h.has_perm("bookings.override"):
            raise ValueError("There isn't a free place on this session.")
        waitlist.make_offer(c, [b], s, a, h)
        audit.record(c, h, "waitlist.offer", entity_type="booking", entity_id=b["id"], account_id=b["account_id"])
        intray.resolve(c, "place_freed", entity_type="session", entity_id=s["id"], staff_id=h.staff()["id"])
    return h.json({"ok": True})


@route("POST", "/api/staff/bookings/<bid>/priority", auth="staff", perm="bookings.manage")
def priority(h, bid):
    d = h.json_body() or {}
    try:
        value = max(-10, min(int(d.get("priority") or 0), 10))
    except (TypeError, ValueError):
        raise Invalid({"priority": "Enter a number from -10 to 10."})
    with db.tx() as c:
        b = _booking(c, bid)
        c.execute("UPDATE bookings SET waitlist_priority=?, updated_at=? WHERE id=?", (value, db.now(), b["id"]))
        audit.record(c, h, "waitlist.priority", entity_type="booking", entity_id=b["id"], details={"priority": value})
    return h.json({"ok": True})


@route("POST", "/api/staff/sessions/<sid>/cancel", auth="staff", perm="bookings.manage")
def cancel_session(h, sid):
    d = h.json_body() or {}
    reason = validate.text(d.get("reason"), 300)
    if not reason:
        raise Invalid({"reason": "Give a reason — it goes in the message to families."})
    with db.tx() as c:
        n = bookings.cancel_session(c, h, int(sid), reason, refund=d.get("refund") or "auto",
                                    notify=d.get("notify", True))
    return h.json({"ok": True, "cancelled": n})


# ---------------------------------------------------------------- invoices (staff view)


@route("GET", "/admin/invoices/<number>", auth="staff", perm="bookings.view")
def staff_invoice(h, number):
    with db.read() as c:
        inv = c.execute("SELECT * FROM invoices WHERE number=?", (number,)).fetchone()
        if not inv:
            raise LookupError
        page = invoice_page.render(c, inv, nonce=h.new_nonce(), staff=True)
    return invoice_page.send(h, page)
