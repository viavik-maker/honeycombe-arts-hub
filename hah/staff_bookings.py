"""Staff booking for a family (phone, email, reception) and the walk-in
"book and pay on the spot" screen.

Staff aren't blocked by an incomplete profile: the booking is flagged
"profile incomplete" on the register and the family is asked to finish it.
Anything that breaks a rule (age range, a full session, not HAF-eligible,
not yet published) needs the bookings.override permission and a reason."""
import datetime

from . import accounts, audit, bookings, catalogue, db, family, intray, money, outbox, validate
from .validate import Invalid
from .web import route, site_url

SOFT = {"level", "reconfirm", "account", "not_open", "closed"}           # staff may simply go ahead
NEVER = {"booked", "cancelled"}                                          # not even with an override
OVERRIDE = {"age", "full", "haf", "overlap", "unavailable", "adult", "haf_allowance", "trial"}  # needs bookings.override
# (book_for_family treats any code in none of these as needing an override too, so a new rule can't slip past)
PAY_MODES = ("record", "unpaid", "link", "comp")


def luhn_like(text):
    """True if TEXT contains something that looks like a card number — those
    must never be typed into reference or notes boxes."""
    digits = "".join(ch for ch in (text or "") if ch.isdigit() or ch == " ")
    for run in digits.split("  "):
        d = run.replace(" ", "")
        if 13 <= len(d) <= 19:
            total, alt = 0, False
            for ch in reversed(d):
                n = int(ch) * (2 if alt else 1)
                total += n - 9 if n > 9 else n
                alt = not alt
            if total % 10 == 0:
                return True
    return False


def clean_payment(d, total, due=None):
    """DUE: what's left to take once account credit is used (default TOTAL). With nothing due, a recorded
    payment defaults to £0 (nothing is recorded)."""
    p = d or {}
    due = total if due is None else due
    mode = p.get("mode") or ("comp" if not total else "unpaid")
    if mode not in PAY_MODES:
        raise Invalid({"payment": "Choose how it's being paid."})
    out = {"mode": mode}
    if mode == "record":
        if p.get("method") not in money.METHODS or p.get("method") in ("stripe_card", "account_credit"):
            raise Invalid({"method": "Choose how they paid."})
        try:
            amount = int(p.get("amount_pence") if p.get("amount_pence") not in (None, "") else due)
        except (TypeError, ValueError):
            raise Invalid({"amount_pence": "Enter the amount paid."})
        if amount < 0 or amount > due or (amount == 0 and due):
            raise Invalid({"amount_pence": "Enter an amount up to %s%s." % (
                money.pounds(due), " (their account credit covers the rest)" if due < total else "")})
        ref, notes = validate.text(p.get("reference"), 100), validate.text(p.get("notes"), 300)
        if luhn_like(ref) or luhn_like(notes):
            raise Invalid({"reference": "That looks like a card number — never write card numbers down here."})
        out.update(method=p["method"], amount=amount, reference=ref or None, notes=notes or None,
                   voucher_provider=validate.text(p.get("voucher_provider"), 60) or None)
    return out


def book_for_family(c, h, account, raw_items, *, pay, override=False, reason="", notify=True, is_trial=False,
                    via="staff"):
    """Book sessions for a family as a member of staff. Returns (bookings, invoice)."""
    staff = h.staff()
    if is_trial and isinstance(raw_items, list):
        raw_items = [dict(it, trial=True) if isinstance(it, dict) else it for it in raw_items]
    items = bookings.resolve_items(c, account, raw_items)
    lines, quote = bookings.assess(c, account, items, staff=True)
    problems = []
    for l in lines:
        codes = {p["code"] for p in l["problems"]}
        if l["outcome"] == "waitlist" and not override:
            codes.add("full")
        if "booked" in codes:
            problems.append("%s is already booked on %s %s." % (l["who"], l["activity"], l["date"]))
        if "cancelled" in codes:
            problems.append("%s %s has been cancelled." % (l["activity"], l["date"]))
        hard = codes - SOFT - NEVER
        if hard and not override:
            problems.append("%s — %s: %s" % (l["who"], l["activity"], "; ".join(p["message"] for p in l["problems"]
                                                                                 if p["code"] in hard) or "full"))
    if problems:
        return None, {"error": "Some places need an override.", "problems": problems,
                      "can_override": h.has_perm("bookings.override")}
    if override:
        if not h.has_perm("bookings.override"):
            raise ValueError("You don't have permission to override booking rules.")
        if not validate.text(reason, 300):
            raise Invalid({"reason": "Give a reason for overriding the rules."})
    total = sum(l["price_pence"] for l in lines)
    credit = min(money.credit_balance(c, account["id"]), total)  # used first, below
    pay = clean_payment(pay, total if pay.get("mode") != "comp" else 0, total - credit) if total else {"mode": "comp"}
    cid = c.execute("INSERT INTO checkouts(ref, account_id, idempotency_key, amount_pence, pay_mode, status,"
                    " created_by_staff, created_at, completed_at) VALUES (?,?,?,?, 'staff_offline', 'completed', ?,?,?)",
                    (family.new_ref("K"), account["id"], "staff-%s" % family.new_ref("X"), total, staff["id"], db.now(),
                     db.now())).lastrowid
    booked = []
    for l in lines:
        it = l["item"]
        comp = pay["mode"] == "comp"
        funding = "haf" if it["activity"]["haf_only"] else ("staff_comp" if comp and l["price_pence"] else l["funding"])
        incomplete = any(p["code"] == "level" for p in l["problems"])
        b = bookings._insert_booking(
            c, it, "confirmed", account_id=account["id"], checkout_id=cid, price=0 if comp else l["price_pence"],
            funding=funding, via=via, staff_id=staff["id"], profile_incomplete=incomplete, is_trial=l["trial"],
            discount=(l["discount_pence"], l["discount_reason"]),
            pay_later=pay["mode"] in ("unpaid", "link"), notes=("Override: " + reason) if override else None)
        booked.append(b)
    invoice = None
    if any(b["price_pence"] for b in booked):
        invoice = money.create_invoice(c, booked, account_id=account["id"], checkout_id=cid, staff_id=staff["id"])
        money.pay_with_credit(c, account["id"], invoice["id"])
        amount = min(pay.get("amount") or 0, money.balance(c.execute("SELECT * FROM invoices WHERE id=?",
                                                                      (invoice["id"],)).fetchone()))
        if pay["mode"] == "record" and amount > 0:
            p = money.record_payment(c, amount=amount, method=pay["method"], account_id=account["id"],
                                     reference=pay["reference"], voucher_provider=pay["voucher_provider"],
                                     notes=pay["notes"], staff_id=staff["id"])
            money.allocate(c, p["id"], invoice["id"], amount)
        invoice = c.execute("SELECT * FROM invoices WHERE id=?", (invoice["id"],)).fetchone()
    audit.record(c, h, "booking.staff_create", entity_type="checkout", entity_id=cid, account_id=account["id"],
                 details={"bookings": len(booked), "pay": pay["mode"], "override": bool(override)})
    if notify and account["email"]:
        bookings.send_summary(c, h, account, booked, invoice)
        gaps = sorted({b["participant_id"] for b in booked if b["profile_incomplete"] and b["participant_id"]})
        for pid in gaps:
            p = c.execute("SELECT ref, first_name FROM participants WHERE id=?", (pid,)).fetchone()
            first = min(c.execute("SELECT date FROM activity_sessions WHERE id=?", (b["session_id"],)).fetchone()[0]
                        for b in booked if b["participant_id"] == pid)
            outbox.email(c, account["email"], "complete_details",
                         {"first_name": account["first_name"], "child": p["first_name"],
                          "before": catalogue.nice_date(first),
                          "details_url": site_url(h) + "/account/family/" + p["ref"]}, account_id=account["id"])
    return booked, invoice


def _account_by_ref(c, ref):
    a = c.execute("SELECT * FROM accounts WHERE ref=? AND status NOT IN ('closed','anonymised')", (ref or "",)).fetchone()
    if not a:
        raise LookupError
    return a


@route("POST", "/api/staff/bookings/create", auth="staff", perm="bookings.manage")
def create(h):
    d = h.json_body() or {}
    with db.tx() as c:
        account = _account_by_ref(c, d.get("account_ref"))
        booked, invoice = book_for_family(c, h, account, d.get("items"), pay=d.get("payment") or {},
                                          override=bool(d.get("override")), reason=d.get("reason") or "",
                                          notify=d.get("notify", True), is_trial=bool(d.get("is_trial")))
        if booked is None:
            return h.json(invoice, 409)
        return h.json({"ok": True, "bookings": [bookings.booking_json(c, b) for b in booked],
                       "invoice": money.invoice_json(c, invoice, with_lines=False) if invoice else None})


# ---------------------------------------------------------------- walk-ins


@route("POST", "/api/staff/walkin", auth="staff", perm="bookings.manage")
def walkin(h):
    """Reception: find or create the family, book today's session, take the
    payment and sign the child in — all in one go."""
    d = h.json_body() or {}
    staff = h.staff()
    errors = {}
    sid = str(d.get("session_id") or "")
    if not sid.isdigit():
        errors["session_id"] = "Choose the session."
    parent, child = d.get("parent") or {}, d.get("child") or {}
    with db.tx() as c:
        account = None
        if d.get("account_ref"):
            account = _account_by_ref(c, d["account_ref"])
        else:
            first, last = validate.text(parent.get("first_name"), 60), validate.text(parent.get("last_name"), 60)
            email = validate.email(parent.get("email")) if parent.get("email") else None
            mobile = validate.uk_mobile(parent.get("mobile")) if parent.get("mobile") else None
            if not first:
                errors["parent.first_name"] = "Enter the parent's first name."
            if not last:
                errors["parent.last_name"] = "Enter the parent's last name."
            if parent.get("email") and not email:
                errors["parent.email"] = "Check the email address."
            if parent.get("mobile") and not mobile:
                errors["parent.mobile"] = "Enter a UK mobile number."
            if not email and not mobile:
                errors["parent.mobile"] = "Enter a mobile number or an email address."
            if email and c.execute("SELECT 1 FROM accounts WHERE email=? AND status<>'anonymised'", (email,)).fetchone():
                errors["parent.email"] = "This family already has an account — search for them instead."
        pid = None
        if account and child.get("ref"):
            p = c.execute("SELECT * FROM participants WHERE ref=? AND account_id=?", (child["ref"], account["id"])).fetchone()
            if not p:
                errors["child.ref"] = "Choose one of this family's children."
            else:
                pid = p["id"]
        else:
            cfirst, clast = validate.text(child.get("first_name"), 60), validate.text(child.get("last_name"), 60)
            dob = validate.date(child.get("dob"))
            if not cfirst:
                errors["child.first_name"] = "Enter the child's first name."
            if not clast:
                errors["child.last_name"] = "Enter the child's last name."
            if not dob or dob > catalogue.uk_today():
                errors["child.dob"] = "Enter the child's date of birth."
        contact = d.get("contact") or {}
        if not account and not (validate.text(contact.get("full_name"), 100) and validate.uk_phone(contact.get("phone"))):
            errors["contact.phone"] = "Add an emergency contact with a phone number."
        if d.get("first_aid") not in ("yes", "no") and not pid:
            errors["first_aid"] = "Ask about first aid."
        if d.get("photo") not in ("online", "internal", "none") and not pid:
            errors["photo"] = "Ask about photos."
        if errors:
            raise Invalid(errors)
        s = c.execute("SELECT s.*, a.registration_level FROM activity_sessions s JOIN activities a ON a.id=s.activity_id"
                      " WHERE s.id=?", (int(sid),)).fetchone()
        if not s:
            raise Invalid({"session_id": "Choose the session."})
        now = db.now()
        source = "staff_paper" if d.get("consent_source") == "staff_paper" else "staff_verbal"
        if not account:
            aid = c.execute(
                "INSERT INTO accounts(ref, kind, email, status, first_name, last_name, mobile, source, created_at,"
                " updated_at) VALUES (?, 'family', ?, 'pending_activation', ?, ?, ?, 'walkin', ?, ?)",
                (family.new_ref("A"), email, first, last, mobile, now, now)).lastrowid
            account = c.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
            c.execute("INSERT INTO emergency_contacts(account_id, full_name, relationship, phone, can_collect, priority,"
                      " created_at, updated_at) VALUES (?,?,?,?,?,1,?,?)",
                      (aid, validate.text(contact["full_name"], 100), validate.text(contact.get("relationship"), 60) or "Contact",
                       validate.uk_phone(contact["phone"]), 1 if contact.get("can_collect") else 0, now, now))
        if not pid:
            level = "short" if s["registration_level"] == "short" else "full"
            pid = c.execute("INSERT INTO participants(ref, account_id, first_name, last_name, dob, target_level,"
                            " needs_review, created_at, updated_at) VALUES (?,?,?,?,?,?,1,?,?)",
                            (family.new_ref("P"), account["id"], cfirst, clast, dob.isoformat(), level, now, now)).lastrowid
            allergies = validate.long_text(d.get("allergies"), 1000)
            c.execute("INSERT INTO participant_health(participant_id, allergies, updated_at, updated_by_staff)"
                      " VALUES (?,?,?,?)", (pid, allergies or None, now, staff["id"]))
            family.record_consent(c, account["id"], pid, "first_aid", d["first_aid"], source, staff_id=staff["id"])
            family.record_consent(c, account["id"], pid, "photo", d["photo"], source, staff_id=staff["id"])
            family.refresh_flags(c, pid)
            family.compute_level(c, pid)
        p = c.execute("SELECT ref FROM participants WHERE id=?", (pid,)).fetchone()
        booked, invoice = book_for_family(c, h, account, [{"session_id": s["id"], "participant": p["ref"]}],
                                          pay=d.get("payment") or {}, override=bool(d.get("override")),
                                          reason=d.get("reason") or "", notify=False, via="walkin")
        if booked is None:
            return h.json(invoice, 409)
        if d.get("sign_in", True):
            c.execute("UPDATE attendance SET status='present', signed_in_at=?, signed_in_by=? WHERE booking_id=?",
                      (now, staff["id"], booked[0]["id"]))
        if account["source"] == "walkin" and account["email"] and account["status"] == "pending_activation":
            accounts.send_activation(c, h, account)
        intray.add(c, "walkin_follow_up", "Walk-in: check the family completes their details online",
                   perm="bookings.view", entity_type="account", entity_id=account["id"], account_id=account["id"],
                   participant_id=pid)
        audit.record(c, h, "booking.walkin", entity_type="booking", entity_id=booked[0]["id"], account_id=account["id"],
                     participant_id=pid)
        return h.json({"ok": True, "account_ref": account["ref"], "booking": bookings.booking_json(c, booked[0]),
                       "invoice": money.invoice_json(c, invoice, with_lines=False) if invoice else None})


@route("GET", "/api/staff/sessions/upcoming", auth="staff", perm="bookings.view")
def upcoming_sessions(h):
    """Sessions staff can book onto (today and the next 8 weeks), for pickers."""
    q = h.query()
    days = 56 if q.get("range") != "today" else 0
    today = catalogue.uk_today()
    with db.read() as c:
        rows = c.execute("SELECT s.*, a.title, a.haf_only, a.price_pence AS a_price, a.registration_level"
                         " FROM activity_sessions s JOIN activities a ON a.id=s.activity_id WHERE s.date BETWEEN ? AND ?"
                         " AND s.status='scheduled' AND a.status IN ('published','unpublished','scheduled','draft')"
                         " ORDER BY s.date, s.start_time",
                         (today.isoformat(), (today + datetime.timedelta(days=days)).isoformat())).fetchall()
        out = [{"id": s["id"], "date": s["date"], "start_time": s["start_time"], "end_time": s["end_time"],
                "title": s["title"], "level": s["registration_level"],
                "price_pence": 0 if s["haf_only"] else (s["price_pence"] if s["price_pence"] is not None else s["a_price"]),
                "free": max(s["capacity"] - catalogue.places_taken(c, s["id"]), 0)} for s in rows]
    return h.json({"sessions": out})
