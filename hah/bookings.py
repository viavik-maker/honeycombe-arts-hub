"""The booking engine: quotes, confirming a basket, card checkouts,
approvals, cancellations and attendance rows.

Every function here runs inside the caller's db.tx() (BEGIN IMMEDIATE), so
two families can't both take the last place: capacity is checked and the
booking written while holding SQLite's write lock.

Order of decisions for each chosen session (design A2.8):
  1. can they book it at all (published, booking window, eligibility)?
  2. is there a place? (anyone already waiting keeps their turn) → else waiting list
  3. does it need approval (activity setting, unverified HAF claim)?
  4. how is it paid for (free, card now, pay later, vouchers)?"""
import datetime

from . import (audit, booking_settings, catalogue, db, eligibility, family, intray, money, outbox, payments_stripe,
               waitlist)
from .web import site_url

STATUS_WORDS = {"confirmed": "Confirmed", "pending_payment": "Awaiting payment", "pending_approval": "Waiting for approval",
                "waitlisted": "On the waiting list", "offered": "Place offered", "cancelled": "Cancelled",
                "expired": "Expired", "pending_confirmation": "Waiting for email confirmation"}
MAX_ITEMS = 60


class Refused(Exception):
    """The basket can't be booked as it stands (the quote says why)."""

    def __init__(self, message, quote=None):
        super().__init__(message)
        self.quote = quote


# ---------------------------------------------------------------- the basket


def resolve_items(c, account, raw):
    """Client items → [{kind, session, activity, participant | children+adults}]."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("Choose at least one session.")
    if len(raw) > MAX_ITEMS:
        raise ValueError("Please book up to %d sessions at a time." % MAX_ITEMS)
    people = {p["ref"]: p for p in family.account_participants(c, account["id"])}
    out, seen = [], set()
    for it in raw:
        if not isinstance(it, dict):
            raise ValueError("Something went wrong with your basket — please choose again.")
        sid = str(it.get("session_id") or "")
        s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (int(sid),)).fetchone() if sid.isdigit() else None
        if not s:
            raise ValueError("A session you chose is no longer available — please go back and choose again.")
        a = c.execute("SELECT * FROM activities WHERE id=?", (s["activity_id"],)).fetchone()
        if a["registration_level"] == "guest":
            kids = [people[r] for r in (it.get("participants") or []) if r in people]
            try:
                adults = max(0, min(int(it.get("adults") or 0), a["max_party_size"]))
            except (TypeError, ValueError):
                adults = 0
            if not kids and not adults:
                raise ValueError("Choose who is coming to %s." % a["title"])
            if ("party", s["id"]) in seen:
                continue
            seen.add(("party", s["id"]))
            out.append({"kind": "party", "session": s, "activity": a, "children": kids, "adults": adults})
        else:
            p = people.get(it.get("participant"))
            if not p:
                raise ValueError("Choose who is coming to %s." % a["title"])
            if (s["id"], p["id"]) in seen:
                continue
            seen.add((s["id"], p["id"]))
            out.append({"kind": "participant", "session": s, "activity": a, "participant": p,
                        "trial": it.get("trial") is True})
    return out


# ---------------------------------------------------------------- trials


def trial_price(activity, session):
    tp = activity["trial_price_pence"]
    return catalogue.price_of(activity, session) if tp is None else min(tp, catalogue.price_of(activity, session))


def trial_used(c, activity_id, participant_id):
    """Has this child had (or got) a place on this activity already? Then it's not their first go."""
    return bool(c.execute("SELECT 1 FROM bookings WHERE activity_id=? AND participant_id=? AND status IN "
                          + catalogue.HOLDING_SQL, (activity_id, participant_id)).fetchone())


def can_trial(c, activity, participant):
    return bool(activity["allow_trial"]) and not activity["haf_only"] and not trial_used(c, activity["id"],
                                                                                        participant["id"])


def _places(item):
    if item["kind"] == "participant":
        return 1
    if item.get("places"):  # a guest party: counted from the numbers they gave
        return item["places"]
    n = len(item["children"])
    return n + (item["adults"] if item["activity"]["capacity_counts"] == "all_people" else 0) or 1


def _price(item):
    a, s = item["activity"], item["session"]
    if a["haf_only"]:
        return 0
    unit = catalogue.price_of(a, s)
    if item["kind"] == "participant":
        return trial_price(a, s) if item.get("trial") else unit
    return unit * len(item["children"]) + a["adult_price_pence"] * item["adults"]


def assess(c, account, items):
    """Work out what would happen to each item. Returns (lines, quote)."""
    checker = eligibility.Checker(c, account)
    allocated, basket, lines, trials = {}, [], [], set()
    for it in items:
        a, s = it["activity"], it["session"]
        problems, trial_ok = [], False
        if not catalogue.is_bookable(a):
            problems.append(("unavailable", "%s isn't taking bookings." % a["title"]))
        state, opens = catalogue.window(a, s)
        if state == "not_open_yet":
            problems.append(("not_open", "Booking opens %s." % catalogue.nice_time(catalogue.parse_utc(opens))))
        elif state == "closed":
            problems.append(("closed", "Booking has closed for this session."))
        elif state == "cancelled":
            problems.append(("cancelled", "This session has been cancelled."))
        if it["kind"] == "participant":
            p = it["participant"]
            problems += checker.problems(a, s, p, basket)
            basket.append((s, p["id"]))
            who, ref = p["first_name"], p["ref"]
            # one trial per child per activity: the first session they pick for it
            trial_ok = (a["id"], p["id"]) not in trials and can_trial(c, a, p)
            if it.get("trial"):
                if trial_ok:
                    trials.add((a["id"], p["id"]))
                else:
                    problems.append(("trial", "%s can only have one trial session of %s, before booking it "
                                              "normally." % (p["first_name"], a["title"])))
        else:
            for p in it["children"]:
                problems += [x for x in checker.problems(a, s, p, basket) if x[0] in ("age", "booked", "account",
                                                                                     "reconfirm", "overlap")]
            names = [p["first_name"] for p in it["children"]]
            if it["adults"]:
                names.append("%d adult%s" % (it["adults"], "" if it["adults"] == 1 else "s"))
            who, ref = " and ".join(names), None
        price, places = _price(it), _places(it)
        funding = "haf" if a["haf_only"] else ("free" if price == 0 else "paid")
        taken = catalogue.places_taken(c, s["id"]) + allocated.get(s["id"], 0)
        waiting = catalogue.waitlist_count(c, s["id"])
        if problems:
            outcome = "blocked"
        elif taken + places > s["capacity"] or waiting:
            if a["waitlist_enabled"]:
                outcome = "waitlist"
            else:
                outcome, problems = "blocked", [("full", "Sorry, this session is full.")]
        elif a["requires_approval"]:
            outcome = "approval"
        elif a["haf_only"] and it["kind"] == "participant" and it["participant"]["haf_status"] != "verified":
            outcome = "approval"
        elif price == 0:
            outcome = "free"
        else:
            outcome = "pay"
        if outcome not in ("waitlist", "blocked"):
            allocated[s["id"]] = allocated.get(s["id"], 0) + places
        lines.append({
            "item": it, "session_id": s["id"], "participant": ref, "who": who, "activity": a["title"],
            "activity_slug": a["slug"], "date": s["date"], "start_time": s["start_time"], "end_time": s["end_time"],
            "theme": s["theme"], "price_pence": price, "places": places, "funding": funding, "outcome": outcome,
            "trial": bool(it.get("trial")), "trial_available": trial_ok,
            "trial_price_pence": trial_price(a, s) if trial_ok else None,
            "approval_reason": ("activity" if a["requires_approval"] else "haf_claim") if outcome == "approval" else None,
            "problems": [{"code": k, "message": m} for k, m in problems],
            "fix_url": "/account/family/%s?for=%s" % (ref, a["registration_level"])
            if ref and any(k in ("level", "haf") for k, _ in problems) else
            ("/account" if any(k in ("reconfirm", "account") for k, _ in problems) else None),
        })
    return lines, _quote(c, account, lines)


def _quote(c, account, lines):
    pay = [l for l in lines if l["outcome"] == "pay"]
    total = sum(l["price_pence"] for l in pay)
    credit = min(money.credit_balance(c, account["id"]), total)
    settings = booking_settings.get_all(c)
    options = []
    if total:
        if payments_stripe.configured() or total - credit == 0:
            options.append("card")
        if all(l["item"]["activity"]["allow_pay_later"] for l in pay) and \
                (account["pay_later_allowed"] or settings["pay_later_for_all"]):
            options.append("pay_later")
        options.append("voucher")
    return {
        "lines": [{k: v for k, v in l.items() if k != "item"} for l in lines],
        "blocked": sum(1 for l in lines if l["outcome"] == "blocked"),
        "total_pence": total, "credit_pence": credit, "due_now_pence": total - credit,
        "pay_options": options, "needs_payment": bool(total),
        "counts": {k: sum(1 for l in lines if l["outcome"] == k) for k in ("pay", "free", "approval", "waitlist",
                                                                          "blocked")},
    }


# ---------------------------------------------------------------- writing bookings


def _insert_booking(c, item, status, *, account_id=None, guest_contact_id=None, checkout_id=None, price=0,
                    funding="paid", approval_reason=None, hold=None, pay_later=False, group=None, via="online",
                    staff_id=None, notes=None, profile_incomplete=False, is_trial=False, party_name=None):
    a, s = item["activity"], item["session"]
    kind = item["kind"]
    bid = c.execute(
        "INSERT INTO bookings(ref, checkout_id, session_id, activity_id, kind, participant_id, account_id,"
        " guest_contact_id, party_adults, party_children, party_name, places, status, approval_reason, funding,"
        " price_pence, pay_later, is_trial, profile_incomplete, hold_expires_at, waitlist_group, created_via,"
        " created_by_staff, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (family.new_ref("B"), checkout_id, s["id"], a["id"], kind,
         item["participant"]["id"] if kind == "participant" else None, account_id, guest_contact_id,
         item.get("adults", 0) if kind == "party" else 0,
         len(item.get("children") or []) if kind == "party" else 0, party_name, _places(item), status,
         approval_reason, funding, price, 1 if pay_later else 0, 1 if is_trial else 0, 1 if profile_incomplete else 0,
         hold, group, via, staff_id, notes, db.now(), db.now())).lastrowid
    if kind == "party":
        for p in item.get("children") or []:
            c.execute("INSERT INTO booking_party_children(booking_id, participant_id) VALUES (?,?)", (bid, p["id"]))
    b = c.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
    if status == "confirmed":
        add_attendance(c, b)
    return b


def add_attendance(c, b):
    """The register row for a confirmed booking (with reporting snapshots)."""
    if c.execute("SELECT 1 FROM attendance WHERE booking_id=?", (b["id"],)).fetchone():
        return
    s = c.execute("SELECT s.date, cat.report_group FROM activity_sessions s JOIN activities a ON a.id=s.activity_id"
                  " JOIN activity_categories cat ON cat.id=a.category_id WHERE s.id=?", (b["session_id"],)).fetchone()
    age = haf = send = None
    if b["participant_id"]:
        p = c.execute("SELECT dob, f_send FROM participants WHERE id=?", (b["participant_id"],)).fetchone()
        age = catalogue.months_between(datetime.date.fromisoformat(p["dob"]), datetime.date.fromisoformat(s["date"]))
        send = p["f_send"]
        haf = 1 if b["funding"] == "haf" else 0
    c.execute("INSERT INTO attendance(booking_id, session_id, participant_id, snap_age_months, snap_haf, snap_send,"
              " snap_category, updated_at) VALUES (?,?,?,?,?,?,?,?)",
              (b["id"], b["session_id"], b["participant_id"], age, haf, send, s["report_group"], db.now()))


def confirm_booking(c, b, *, staff_id=None):
    """A reserved booking (approval, offer, card payment) becomes confirmed."""
    c.execute("UPDATE bookings SET status='confirmed', hold_expires_at=NULL, offer_expires_at=NULL, updated_at=?,"
              " approved_at=COALESCE(approved_at, CASE WHEN ? IS NOT NULL THEN ? END),"
              " approved_by=COALESCE(approved_by, ?) WHERE id=?", (db.now(), staff_id, db.now(), staff_id, b["id"]))
    b = c.execute("SELECT * FROM bookings WHERE id=?", (b["id"],)).fetchone()
    add_attendance(c, b)
    return b


def _raise_approval_items(c, b, item, reason):
    a = item["activity"]
    if reason == "haf_claim":
        p = item["participant"]
        intray.add(c, "haf_verify", "Check HAF eligibility for %s (%s)" % (p["first_name"], a["title"]),
                   perm="bookings.manage", entity_type="participant", entity_id=p["id"], participant_id=p["id"],
                   account_id=b["account_id"], activity_id=a["id"])
    elif reason == "voucher":
        intray.add(c, "voucher_payment", "Paying by vouchers/Tax-Free Childcare: %s" % a["title"],
                   perm="payments.record", entity_type="checkout", entity_id=b["checkout_id"],
                   account_id=b["account_id"], activity_id=a["id"])
    else:
        intray.add(c, "booking_approval", "Approve booking: %s on %s" % (a["title"], catalogue.nice_date(item["session"]["date"])),
                   perm="bookings.manage", entity_type="booking", entity_id=b["id"], account_id=b["account_id"],
                   participant_id=b["participant_id"], activity_id=a["id"], session_id=b["session_id"],
                   dedupe=False)


def confirm_basket(c, h, account, raw_items, pay_mode, idempotency_key):
    """Book the basket. Returns (checkout row, bookings, needs_card)."""
    key = str(idempotency_key or "")[:64]
    if len(key) < 8:
        raise ValueError("Please reload the page and try again.")
    old = c.execute("SELECT * FROM checkouts WHERE account_id=? AND idempotency_key=?", (account["id"], key)).fetchone()
    if old:
        return old, c.execute("SELECT * FROM bookings WHERE checkout_id=?", (old["id"],)).fetchall(), False
    items = resolve_items(c, account, raw_items)
    lines, quote = assess(c, account, items)
    if quote["blocked"]:
        raise Refused("Some sessions can't be booked — see the notes below.", quote)
    if quote["needs_payment"]:
        if pay_mode not in quote["pay_options"]:
            raise Refused("Choose how you'd like to pay.", quote)
    else:
        pay_mode = "free"
    mode = {"card": "stripe" if quote["due_now_pence"] else "credit"}.get(pay_mode, pay_mode)
    card_due = quote["due_now_pence"] if mode == "stripe" else 0
    hold = catalogue.utc_iso(catalogue.uk_now() + datetime.timedelta(
        minutes=booking_settings.get("hold_minutes", c))) if mode == "stripe" else None
    ref = family.new_ref("K")
    cid = c.execute("INSERT INTO checkouts(ref, account_id, idempotency_key, amount_pence, credit_pence, pay_mode,"
                    " status, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (ref, account["id"], key, card_due, quote["credit_pence"] if mode in ("stripe", "credit",
                                                                                          "pay_later") else 0,
                     mode, "creating" if mode == "stripe" else "completed", hold, db.now())).lastrowid
    booked = []
    for l in lines:
        it, outcome = l["item"], l["outcome"]
        kw = dict(account_id=account["id"], checkout_id=cid, price=l["price_pence"], funding=l["funding"],
                  is_trial=l["trial"])
        if outcome == "waitlist":
            b = _insert_booking(c, it, "waitlisted", group="%s:%d" % (ref, it["session"]["id"]), **kw)
        elif outcome == "approval":
            b = _insert_booking(c, it, "pending_approval", approval_reason=l["approval_reason"], **kw)
            _raise_approval_items(c, b, it, l["approval_reason"])
        elif outcome == "free":
            b = _insert_booking(c, it, "confirmed", **kw)
        elif mode == "stripe":
            b = _insert_booking(c, it, "pending_payment", hold=hold, **kw)
        elif mode == "voucher":
            b = _insert_booking(c, it, "pending_approval", approval_reason="voucher", **kw)
            _raise_approval_items(c, b, it, "voucher")
        else:  # pay_later or fully covered by credit
            b = _insert_booking(c, it, "confirmed", pay_later=(mode == "pay_later"), **kw)
        booked.append(b)
    checkout = c.execute("SELECT * FROM checkouts WHERE id=?", (cid,)).fetchone()
    invoice = None
    if mode in ("pay_later", "credit"):
        invoice = _invoice_checkout(c, checkout, booked)
        money.pay_with_credit(c, account["id"], invoice["id"])
    elif mode == "stripe" and quote["credit_pence"]:
        p = money.record_payment(c, amount=quote["credit_pence"], method="account_credit", account_id=account["id"],
                                 status="pending")
        c.execute("UPDATE checkouts SET credit_payment_id=? WHERE id=?", (p["id"], cid))
    audit.record(c, h, "booking.create", entity_type="checkout", entity_id=cid, account_id=account["id"],
                 details={"bookings": len(booked), "pay_mode": mode})
    if mode != "stripe":
        send_summary(c, h, account, booked, invoice)
    return c.execute("SELECT * FROM checkouts WHERE id=?", (cid,)).fetchone(), booked, mode == "stripe"


def _invoice_checkout(c, checkout, bookings):
    """Invoice the confirmed bookings of a checkout (£0 lines too, if anything costs money)."""
    confirmed = [b for b in bookings if b["status"] == "confirmed" and b["funding"] != "prepaid_legacy"]
    if not any(b["price_pence"] for b in confirmed):
        return None
    return money.create_invoice(c, confirmed, account_id=checkout["account_id"],
                                guest_contact_id=checkout["guest_contact_id"], checkout_id=checkout["id"])


def summary_text(c, bookings):
    rows = []
    for b in bookings:
        s = c.execute("SELECT s.*, a.title FROM activity_sessions s JOIN activities a ON a.id=s.activity_id WHERE s.id=?",
                      (b["session_id"],)).fetchone()
        who = ""
        if b["participant_id"]:
            who = c.execute("SELECT first_name FROM participants WHERE id=?", (b["participant_id"],)).fetchone()[0] + ": "
        elif b["kind"] == "party":
            who = "%d place%s: " % (b["places"], "" if b["places"] == 1 else "s")
        price = " (%s)" % money.pounds(b["price_pence"]) if b["price_pence"] else ""
        rows.append("• %s%s, %s %s–%s — %s%s" % (who, s["title"], catalogue.nice_date(s["date"]), s["start_time"],
                                               s["end_time"], STATUS_WORDS[b["status"]], price))
    return "\n".join(rows)


def send_summary(c, h, account, bookings, invoice=None):
    """The "we've got your booking" email, listing what happened to each place."""
    if not account or not account["email"] or not bookings:
        return
    lines = summary_text(c, bookings)
    notes = []
    if any(b["status"] == "pending_approval" for b in bookings):
        notes.append("Places waiting for approval are held for you — we'll email you when we've checked them.")
    if any(b["status"] == "waitlisted" for b in bookings):
        notes.append("If a place comes up on the waiting list we'll email and text you, and hold it for you for a while.")
    inv_text = ""
    if invoice:
        inv = c.execute("SELECT * FROM invoices WHERE id=?", (invoice["id"],)).fetchone()
        bal = money.balance(inv)
        inv_text = "Invoice %s: total %s%s." % (inv["number"], money.pounds(inv["total_pence"]),
                                                ", paid in full" if bal <= 0 else ", %s to pay by %s" % (
                                                    money.pounds(bal), catalogue.nice_date(inv["due_date"])))
    outbox.email(c, account["email"], "booking_received",
                 {"first_name": account["first_name"], "lines": lines, "notes": "\n".join(notes),
                  "invoice": inv_text, "bookings_url": site_url(h) + "/account/bookings"},
                 account_id=account["id"], booking_id=bookings[0]["id"])


# ---------------------------------------------------------------- card checkouts


def complete_card_checkout(c, checkout, *, payment_intent, amount, currency="gbp", session_id=None, h=None):
    """Stripe says the checkout is paid. Idempotent: running twice does nothing
    more. Confirms the held places, records the payment and the invoice."""
    checkout = c.execute("SELECT * FROM checkouts WHERE id=?", (checkout["id"],)).fetchone()
    if checkout["status"] == "completed":
        return "already"
    if currency.lower() != "gbp" or int(amount) != checkout["amount_pence"]:
        intray.add(c, "payment_problem", "Card payment for %s didn't match (%s paid, %s expected)" % (
            checkout["ref"], money.pounds(int(amount)), money.pounds(checkout["amount_pence"])),
            perm="finance.view", entity_type="checkout", entity_id=checkout["id"], account_id=checkout["account_id"])
        return "mismatch"
    pay = money.record_payment(c, amount=int(amount), method="stripe_card", account_id=checkout["account_id"],
                               guest_contact_id=checkout["guest_contact_id"], stripe_payment_intent_id=payment_intent,
                               stripe_checkout_session_id=session_id)
    c.execute("UPDATE checkouts SET status='completed', completed_at=? WHERE id=?", (db.now(), checkout["id"]))
    if checkout["invoice_id"]:  # paying an existing invoice
        money.allocate(c, pay["id"], checkout["invoice_id"], int(amount))
        inv = c.execute("SELECT * FROM invoices WHERE id=?", (checkout["invoice_id"],)).fetchone()
        acct = c.execute("SELECT * FROM accounts WHERE id=?", (checkout["account_id"],)).fetchone() \
            if checkout["account_id"] else None
        to = acct["email"] if acct else inv["bill_to_email"]
        outbox.email(c, to, "payment_received", {"name": inv["bill_to_name"], "number": inv["number"],
                                                 "amount": money.pounds(int(amount)),
                                                 "balance": money.pounds(money.balance(inv))},
                     account_id=checkout["account_id"])
        return "paid_invoice"
    bookings = c.execute("SELECT * FROM bookings WHERE checkout_id=?", (checkout["id"],)).fetchall()
    late = []
    for b in bookings:
        if b["status"] == "pending_payment":
            confirm_booking(c, b)
        elif b["status"] == "expired" and b["price_pence"]:
            s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (b["session_id"],)).fetchone()
            if s["status"] == "scheduled" and waitlist.free_places(c, s) >= b["places"]:
                confirm_booking(c, b)
            else:
                late.append(b)
    if checkout["credit_payment_id"]:
        c.execute("UPDATE payments SET status='succeeded' WHERE id=?", (checkout["credit_payment_id"],))
    bookings = c.execute("SELECT * FROM bookings WHERE checkout_id=?", (checkout["id"],)).fetchall()
    invoice = _invoice_checkout(c, checkout, bookings)
    if invoice:
        money.allocate(c, pay["id"], invoice["id"], int(amount))
        if checkout["credit_payment_id"]:
            money.allocate(c, checkout["credit_payment_id"], invoice["id"], checkout["credit_pence"])
    if late:
        intray.add(c, "late_payment", "Card payment arrived after the place was released (%s) — refund or rebook"
                   % checkout["ref"], perm="finance.view", entity_type="checkout", entity_id=checkout["id"],
                   account_id=checkout["account_id"])
    acct = c.execute("SELECT * FROM accounts WHERE id=?", (checkout["account_id"],)).fetchone() \
        if checkout["account_id"] else None
    if acct:
        send_summary(c, h, acct, bookings, invoice)
    elif checkout["guest_contact_id"]:
        from . import guests
        guests.send_confirmation(c, h, checkout, bookings, invoice)
    audit.record(c, h, "checkout.paid", entity_type="checkout", entity_id=checkout["id"],
                 account_id=checkout["account_id"], details={"amount": int(amount)})
    return "completed"


def release_checkout(c, checkout, status="expired"):
    """An abandoned or failed card payment: give the held places back."""
    checkout = c.execute("SELECT * FROM checkouts WHERE id=?", (checkout["id"],)).fetchone()
    if checkout["status"] not in ("creating", "awaiting_payment"):
        return False
    c.execute("UPDATE checkouts SET status=? WHERE id=?", (status, checkout["id"]))
    sessions = set()
    for b in c.execute("SELECT * FROM bookings WHERE checkout_id=? AND status='pending_payment'",
                       (checkout["id"],)).fetchall():
        c.execute("UPDATE bookings SET status='expired', hold_expires_at=NULL, updated_at=? WHERE id=?",
                  (db.now(), b["id"]))
        sessions.add(b["session_id"])
    if checkout["credit_payment_id"]:
        c.execute("UPDATE payments SET status='failed' WHERE id=? AND status='pending'", (checkout["credit_payment_id"],))
    for sid in sessions:
        waitlist.places_freed(c, sid)
    return True


# ---------------------------------------------------------------- approvals


def approve(c, h, b, *, verify_haf=True):
    """Staff approve a booking that was waiting (activity approval, HAF claim
    or paying by vouchers). Paid places get an invoice to pay."""
    if b["status"] != "pending_approval":
        raise ValueError("This booking isn't waiting for approval.")
    staff = h.staff() if h else None
    sid = staff["id"] if staff else None
    if b["approval_reason"] == "haf_claim" and verify_haf and b["participant_id"]:
        c.execute("UPDATE participants SET haf_status='verified', haf_verified_at=?, haf_verified_by=?, updated_at=?"
                  " WHERE id=?", (db.now(), sid, db.now(), b["participant_id"]))
        intray.resolve(c, "haf_verify", entity_type="participant", entity_id=b["participant_id"], staff_id=sid)
    if b["price_pence"]:
        c.execute("UPDATE bookings SET pay_later=1 WHERE id=?", (b["id"],))
    b = confirm_booking(c, b, staff_id=sid)
    intray.resolve(c, "booking_approval", entity_type="booking", entity_id=b["id"], staff_id=sid)
    invoice = None
    if b["price_pence"]:
        invoice = money.create_invoice(c, [b], account_id=b["account_id"], guest_contact_id=b["guest_contact_id"],
                                       checkout_id=b["checkout_id"], staff_id=sid)
        if b["account_id"]:
            money.pay_with_credit(c, b["account_id"], invoice["id"])
    if b["approval_reason"] == "voucher" and b["checkout_id"] and not c.execute(
            "SELECT 1 FROM bookings WHERE checkout_id=? AND status='pending_approval' AND approval_reason='voucher'",
            (b["checkout_id"],)).fetchone():
        intray.resolve(c, "voucher_payment", entity_type="checkout", entity_id=b["checkout_id"], staff_id=sid)
    audit.record(c, h, "booking.approve", entity_type="booking", entity_id=b["id"], account_id=b["account_id"],
                 participant_id=b["participant_id"])
    acct = c.execute("SELECT * FROM accounts WHERE id=?", (b["account_id"],)).fetchone() if b["account_id"] else None
    if acct and acct["email"]:
        inv_text = ""
        if invoice:
            inv = c.execute("SELECT * FROM invoices WHERE id=?", (invoice["id"],)).fetchone()
            if money.balance(inv) > 0:
                inv_text = "Invoice %s: %s to pay by %s. You can pay by card from your account, or by childcare " \
                           "vouchers or Tax-Free Childcare quoting %s." % (inv["number"], money.pounds(money.balance(inv)),
                                                                          catalogue.nice_date(inv["due_date"]), inv["number"])
        outbox.email(c, acct["email"], "booking_approved",
                     {"first_name": acct["first_name"], "lines": summary_text(c, [b]), "invoice": inv_text,
                      "bookings_url": site_url(h) + "/account/bookings"}, account_id=acct["id"], booking_id=b["id"])
    return b


def decline(c, h, b, reason, *, haf_not_eligible=False):
    if b["status"] not in ("pending_approval", "waitlisted", "offered"):
        raise ValueError("Only bookings waiting for approval (or on the waiting list) can be declined.")
    staff = h.staff() if h else None
    sid = staff["id"] if staff else None
    c.execute("UPDATE bookings SET status='cancelled', cancelled_at=?, cancelled_by_staff=?, cancel_reason=?, updated_at=?"
              " WHERE id=?", (db.now(), sid, reason or "Declined", db.now(), b["id"]))
    if haf_not_eligible and b["participant_id"]:
        c.execute("UPDATE participants SET haf_status='not_eligible', updated_at=? WHERE id=?", (db.now(), b["participant_id"]))
        intray.resolve(c, "haf_verify", entity_type="participant", entity_id=b["participant_id"], staff_id=sid)
    intray.resolve(c, "booking_approval", entity_type="booking", entity_id=b["id"], staff_id=sid)
    audit.record(c, h, "booking.decline", entity_type="booking", entity_id=b["id"], account_id=b["account_id"],
                 participant_id=b["participant_id"])
    waitlist.places_freed(c, b["session_id"], h)
    acct = c.execute("SELECT * FROM accounts WHERE id=?", (b["account_id"],)).fetchone() if b["account_id"] else None
    if acct and acct["email"]:
        b2 = c.execute("SELECT * FROM bookings WHERE id=?", (b["id"],)).fetchone()
        outbox.email(c, acct["email"], "booking_declined",
                     {"first_name": acct["first_name"], "lines": summary_text(c, [b2]), "reason": reason or "",
                      "haf": "If you'd like a paid place instead, you can book one on our website." if
                      b["approval_reason"] == "haf_claim" else "", "book_url": site_url(h) + "/book"},
                     account_id=acct["id"], booking_id=b["id"])


# ---------------------------------------------------------------- cancelling


def cancel_terms(c, b):
    """What happens if the family cancels BOOKING now:
    {'allowed', 'absence_only', 'money': 'refund_card'|'credit'|'none', 'amount', 'message'}."""
    s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (b["session_id"],)).fetchone()
    settings = booking_settings.get_all(c)
    start = catalogue.session_start(s)
    now = catalogue.uk_now()
    out = {"allowed": False, "absence_only": False, "money": "none", "amount": 0, "message": ""}
    if b["status"] in ("cancelled", "expired") or start <= now:
        out["message"] = "This booking can't be changed now."
        return out
    if b["status"] in ("waitlisted", "offered", "pending_approval"):
        out.update(allowed=True, message="Nothing has been paid, so there's nothing to refund.")
        return out
    if b["status"] == "pending_payment":
        out["message"] = "This place is being paid for — finish or cancel the card payment instead."
        return out
    cutoff = settings["cancel_cutoff_hours"]
    if start - now < datetime.timedelta(hours=cutoff):
        out.update(absence_only=b["participant_id"] is not None,
                   message="It's less than %d hours before the session, so it can't be cancelled or refunded — but "
                           "please let us know if they can't come." % cutoff)
        return out
    out["allowed"] = True
    inv = money.invoice_for_booking(c, b["id"])
    if not inv or not b["price_pence"]:
        out["message"] = "There's nothing to refund."
        return out
    line = c.execute("SELECT amount_pence FROM invoice_lines WHERE invoice_id=? AND booking_id=?",
                     (inv["id"], b["id"])).fetchone()
    amount = line["amount_pence"] if line else b["price_pence"]
    unpaid = max(money.balance(inv), 0)
    refundable = max(amount - unpaid, 0)
    days = (start.date() - now.date()).days
    if not refundable:
        out.update(money="none", amount=0, message="The %s is taken off your invoice." % money.pounds(amount))
    elif days >= settings["refund_days"] and money.paid_by_card(c, inv["id"]):
        out.update(money="refund_card", amount=refundable,
                   message="%s will be refunded to your card (it can take 5–10 days to appear)." % money.pounds(refundable))
    else:
        out.update(money="credit", amount=refundable,
                   message="%s will be added to your account as credit for future bookings." % money.pounds(refundable))
    return out


def cancel(c, h, b, *, reason, money_outcome="none", by_staff=None, by_account=None, notify=True, amount=None):
    """Cancel a booking. MONEY_OUTCOME: 'none' (nothing back), 'credit'
    (account credit), 'refund_card' (Stripe), 'refund_offline' (staff have
    handed it back). Invoiced lines always get a credit note, so the unpaid
    part of an invoice goes away whatever is chosen."""
    if b["status"] in ("cancelled", "expired"):
        raise ValueError("This booking is already cancelled.")
    was_holding = b["status"] in catalogue.HOLDING
    c.execute("UPDATE bookings SET status='cancelled', cancelled_at=?, cancelled_by_staff=?, cancelled_by_account=?,"
              " cancel_reason=?, hold_expires_at=NULL, offer_expires_at=NULL, updated_at=? WHERE id=?",
              (db.now(), by_staff, by_account, (reason or "")[:300], db.now(), b["id"]))
    c.execute("DELETE FROM attendance WHERE booking_id=? AND status='expected' AND signed_in_at IS NULL", (b["id"],))
    refunded = 0
    inv = money.invoice_for_booking(c, b["id"])
    if inv and b["price_pence"]:
        if money_outcome == "none":
            # nothing handed back: only the unpaid part comes off the invoice
            amount = min(amount if amount is not None else b["price_pence"], max(money.balance(inv), 0))
        cn, refundable = (None, 0) if amount == 0 else money.credit_note(
            c, inv, [b], reason or "Booking cancelled", staff_id=by_staff, account_id=by_account, amount=amount)
        if cn and refundable:
            how = {"credit": "account_credit", "refund_card": "stripe", "refund_offline": "bank_transfer"}[money_outcome]
            if how == "account_credit" and not b["account_id"]:
                how = "bank_transfer"
            money.refund(c, cn, refundable, how, account_id=b["account_id"], staff_id=by_staff)
            refunded = refundable
    intray.resolve(c, "booking_approval", entity_type="booking", entity_id=b["id"], staff_id=by_staff)
    audit.record(c, h, "booking.cancel", entity_type="booking", entity_id=b["id"], account_id=b["account_id"],
                 participant_id=b["participant_id"], details={"money": money_outcome, "refunded": refunded})
    if was_holding:
        waitlist.places_freed(c, b["session_id"], h)
    acct = c.execute("SELECT * FROM accounts WHERE id=?", (b["account_id"],)).fetchone() if b["account_id"] else None
    if notify and acct and acct["email"]:
        b2 = c.execute("SELECT * FROM bookings WHERE id=?", (b["id"],)).fetchone()
        outbox.email(c, acct["email"], "booking_cancelled",
                     {"first_name": acct["first_name"], "lines": summary_text(c, [b2]),
                      "money": {"credit": "%s has been added to your account as credit." % money.pounds(refunded),
                                "refund_card": "%s is being refunded to your card." % money.pounds(refunded),
                                "refund_offline": "%s is being refunded to you." % money.pounds(refunded)
                                }.get(money_outcome, "") if refunded else ""},
                     account_id=acct["id"], booking_id=b["id"])
    if by_account and b["participant_id"]:
        intray.add(c, "parent_cancelled", "Booking cancelled by family: %s" % catalogue.nice_date(
            c.execute("SELECT date FROM activity_sessions WHERE id=?", (b["session_id"],)).fetchone()[0]),
            perm="bookings.view", entity_type="booking", entity_id=b["id"], account_id=b["account_id"],
            participant_id=b["participant_id"], session_id=b["session_id"], dedupe=False)
    return refunded


def cancel_session(c, h, session_id, reason, *, refund="auto", notify=True):
    """Staff cancel a whole session: every booking is cancelled with its money
    returned (card → card refund, otherwise account credit), and families are
    told by email and text."""
    s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (session_id,)).fetchone()
    if not s:
        raise LookupError
    if s["status"] == "cancelled":
        raise ValueError("This session is already cancelled.")
    staff = h.staff() if h else None
    a = c.execute("SELECT * FROM activities WHERE id=?", (s["activity_id"],)).fetchone()
    c.execute("UPDATE activity_sessions SET status='cancelled', cancelled_reason=? WHERE id=?", ((reason or "")[:300], s["id"]))
    told, n = set(), 0
    for b in c.execute("SELECT * FROM bookings WHERE session_id=? AND status NOT IN ('cancelled','expired')",
                       (s["id"],)).fetchall():
        outcome = "none"
        inv = money.invoice_for_booking(c, b["id"])
        if inv and b["price_pence"]:
            outcome = "credit" if refund == "credit" else ("refund_card" if money.paid_by_card(c, inv["id"]) else "credit")
        if b["status"] == "pending_payment":
            c.execute("UPDATE bookings SET status='cancelled', cancelled_at=?, cancel_reason=?, updated_at=? WHERE id=?",
                      (db.now(), "Session cancelled", db.now(), b["id"]))
        else:
            cancel(c, h, b, reason="Session cancelled: %s" % (reason or ""), money_outcome=outcome,
                   by_staff=staff["id"] if staff else None, notify=False)
        n += 1
        acct = c.execute("SELECT * FROM accounts WHERE id=?", (b["account_id"],)).fetchone() if b["account_id"] else None
        target = acct or (c.execute("SELECT email, phone AS mobile, name AS first_name, id FROM guest_contacts WHERE id=?",
                                    (b["guest_contact_id"],)).fetchone() if b["guest_contact_id"] else None)
        if notify and target and (b["account_id"], b["guest_contact_id"]) not in told:
            told.add((b["account_id"], b["guest_contact_id"]))
            ctx = {"first_name": target["first_name"] or "there", "activity": a["title"],
                   "when": "%s %s–%s" % (catalogue.nice_date(s["date"]), s["start_time"], s["end_time"]),
                   "reason": reason or "", "bookings_url": site_url(h) + "/account/bookings"}
            outbox.email(c, target["email"], "session_cancelled", ctx, account_id=b["account_id"], booking_id=b["id"])
            if target["mobile"]:
                outbox.text_message(c, target["mobile"], "session_cancelled", ctx, account_id=b["account_id"],
                                    booking_id=b["id"])
    audit.record(c, h, "session.cancel", entity_type="session", entity_id=s["id"], details={"bookings": n})
    return n


def report_absence(c, h, b, account):
    """After the cancellation cutoff: "they can't come" (no refund)."""
    c.execute("UPDATE attendance SET status='absent_notified', updated_at=? WHERE booking_id=? AND status='expected'",
              (db.now(), b["id"]))
    s = c.execute("SELECT date FROM activity_sessions WHERE id=?", (b["session_id"],)).fetchone()
    p = c.execute("SELECT first_name FROM participants WHERE id=?", (b["participant_id"],)).fetchone()
    intray.add(c, "absence_reported", "%s won't be coming on %s" % (p["first_name"], catalogue.nice_date(s["date"])),
               perm="registers.view", entity_type="booking", entity_id=b["id"], account_id=account["id"],
               participant_id=b["participant_id"], session_id=b["session_id"])
    audit.record(c, h, "booking.absence", entity_type="booking", entity_id=b["id"], account_id=account["id"])


# ---------------------------------------------------------------- accepting a waiting-list offer


def accept_offer(c, h, account, b, pay_mode):
    """A family takes up an offered place. Returns (checkout, needs_card)."""
    if b["status"] != "offered" or (b["offer_expires_at"] or "") <= db.now():
        raise ValueError("Sorry, this offer has expired.")
    a = c.execute("SELECT * FROM activities WHERE id=?", (b["activity_id"],)).fetchone()
    p = c.execute("SELECT * FROM participants WHERE id=?", (b["participant_id"],)).fetchone() if b["participant_id"] else None
    now = db.now()
    if a["requires_approval"] or (a["haf_only"] and p and p["haf_status"] != "verified"):
        reason = "activity" if a["requires_approval"] else "haf_claim"
        c.execute("UPDATE bookings SET status='pending_approval', approval_reason=?, offer_expires_at=NULL, updated_at=?"
                  " WHERE id=?", (reason, now, b["id"]))
        s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (b["session_id"],)).fetchone()
        _raise_approval_items(c, b, {"activity": a, "session": s, "participant": p}, reason)
        return None, False
    if not b["price_pence"]:
        confirm_booking(c, b)
        send_summary(c, h, account, [c.execute("SELECT * FROM bookings WHERE id=?", (b["id"],)).fetchone()])
        return None, False
    settings = booking_settings.get_all(c)
    options = ["voucher"]
    if payments_stripe.configured():
        options.append("card")
    if a["allow_pay_later"] and (account["pay_later_allowed"] or settings["pay_later_for_all"]):
        options.append("pay_later")
    credit = min(money.credit_balance(c, account["id"]), b["price_pence"])
    if credit == b["price_pence"]:
        options.append("card")
    if pay_mode not in options:
        raise ValueError("Choose how you'd like to pay.")
    mode = {"card": "stripe" if credit < b["price_pence"] else "credit"}.get(pay_mode, pay_mode)
    hold = catalogue.utc_iso(catalogue.uk_now() + datetime.timedelta(minutes=settings["hold_minutes"]))
    cid = c.execute("INSERT INTO checkouts(ref, account_id, idempotency_key, amount_pence, credit_pence, pay_mode, status,"
                    " expires_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (family.new_ref("K"), account["id"], "offer-%s-%s" % (b["ref"], now),
                     b["price_pence"] - credit if mode == "stripe" else 0, credit, mode,
                     "creating" if mode == "stripe" else "completed", hold if mode == "stripe" else None, now)).lastrowid
    c.execute("UPDATE bookings SET checkout_id=?, updated_at=? WHERE id=?", (cid, now, b["id"]))
    b = c.execute("SELECT * FROM bookings WHERE id=?", (b["id"],)).fetchone()
    checkout = c.execute("SELECT * FROM checkouts WHERE id=?", (cid,)).fetchone()
    if mode == "stripe":
        c.execute("UPDATE bookings SET status='pending_payment', hold_expires_at=?, offer_expires_at=NULL WHERE id=?",
                  (hold, b["id"]))
        if credit:
            pay = money.record_payment(c, amount=credit, method="account_credit", account_id=account["id"], status="pending")
            c.execute("UPDATE checkouts SET credit_payment_id=? WHERE id=?", (pay["id"], cid))
        return c.execute("SELECT * FROM checkouts WHERE id=?", (cid,)).fetchone(), True
    if mode == "voucher":
        c.execute("UPDATE bookings SET status='pending_approval', approval_reason='voucher', offer_expires_at=NULL"
                  " WHERE id=?", (b["id"],))
        s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (b["session_id"],)).fetchone()
        _raise_approval_items(c, b, {"activity": a, "session": s, "participant": p}, "voucher")
        return checkout, False
    c.execute("UPDATE bookings SET pay_later=? WHERE id=?", (1 if mode == "pay_later" else 0, b["id"]))
    b = confirm_booking(c, b)
    invoice = money.create_invoice(c, [b], account_id=account["id"], checkout_id=cid)
    money.pay_with_credit(c, account["id"], invoice["id"])
    send_summary(c, h, account, [b], invoice)
    return checkout, False


def booking_json(c, b):
    s = c.execute("SELECT s.*, a.title, a.slug, a.parent_must_stay FROM activity_sessions s JOIN activities a"
                  " ON a.id=s.activity_id WHERE s.id=?", (b["session_id"],)).fetchone()
    who = None
    if b["participant_id"]:
        p = c.execute("SELECT ref, first_name FROM participants WHERE id=?", (b["participant_id"],)).fetchone()
        who = {"ref": p["ref"], "first_name": p["first_name"]}
    out = {"ref": b["ref"], "status": b["status"], "status_text": STATUS_WORDS[b["status"]], "kind": b["kind"],
           "activity": s["title"], "activity_slug": s["slug"], "date": s["date"], "start_time": s["start_time"],
           "end_time": s["end_time"], "theme": s["theme"], "session_status": s["status"], "who": who,
           "places": b["places"], "party_adults": b["party_adults"], "party_children": b["party_children"],
           "price_pence": b["price_pence"], "funding": b["funding"], "offer_expires_at": b["offer_expires_at"],
           "approval_reason": b["approval_reason"], "parent_must_stay": bool(s["parent_must_stay"]),
           "is_trial": bool(b["is_trial"])}
    if b["status"] == "waitlisted":
        out["position"] = c.execute(
            "SELECT COUNT(*) FROM bookings WHERE session_id=? AND status='waitlisted' AND (waitlist_priority>? OR"
            " (waitlist_priority=? AND (created_at<? OR (created_at=? AND id<=?))))",
            (b["session_id"], b["waitlist_priority"], b["waitlist_priority"], b["created_at"], b["created_at"],
             b["id"])).fetchone()[0]
    inv = money.invoice_for_booking(c, b["id"])
    if inv:
        out["invoice"] = {"number": inv["number"], "status": inv["status"], "balance_pence": money.balance(inv)}
    att = c.execute("SELECT status FROM attendance WHERE booking_id=?", (b["id"],)).fetchone()
    out["attendance"] = att["status"] if att else None
    return out
