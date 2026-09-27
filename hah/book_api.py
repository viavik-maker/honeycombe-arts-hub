"""What families use to book: the catalogue, the quote, confirming, My
bookings (cancel, absence, waiting-list offers) and invoices.

Everything a parent can reach is scoped to their own account; prices and
payment options are always worked out here, never taken from the browser."""
import urllib.parse

from . import (audit, booking_settings, bookings, catalogue, db, eligibility, family, invoice_page, money,
               payments_stripe)
from .web import route


def _who(h):
    return h.principal("account")


def _live(c=None):
    return booking_settings.get("booking_live", c)


# ---------------------------------------------------------------- catalogue


@route("GET", "/api/book/catalogue")
def catalogue_api(h):
    who = _who(h)
    q = h.query()
    staff_preview = h.staff() is not None and h.staff().get("mfa_passed")
    with db.read() as c:
        settings = booking_settings.get_all(c)
        cats = [dict(key=r["key"], name=r["name"], colour=r["colour"]) for r in
                c.execute("SELECT * FROM activity_categories ORDER BY sort")]
        out = {"live": settings["booking_live"], "message": settings["bookings_open_message"],
               "signed_in": bool(who), "categories": cats, "people": [], "activities": [],
               "card_payments": payments_stripe.configured()}
        if not settings["booking_live"] and not staff_preview:
            return h.json(out)
        people = []
        if who:
            account = c.execute("SELECT * FROM accounts WHERE id=?", (who["id"],)).fetchone()
            people = family.account_participants(c, who["id"])
            out["people"] = [{"ref": p["ref"], "first_name": p["first_name"], "age": family.age_years(p["dob"]),
                              "level": p["level"], "target_level": p["target_level"],
                              "is_account_holder": bool(p["is_account_holder"])} for p in people]
            checker = eligibility.Checker(c, account)
        today = catalogue.uk_today().isoformat()
        only = q.get("activity")
        for a in c.execute("SELECT a.*, cat.key AS category_key, cat.name AS category, cat.colour, cen.name AS centre"
                           " FROM activities a JOIN activity_categories cat ON cat.id=a.category_id"
                           " JOIN centres cen ON cen.id=a.centre_id WHERE a.status='published'"
                           " ORDER BY cat.sort, a.title").fetchall():
            if only and a["slug"] != only:
                continue
            sessions = []
            for s in c.execute("SELECT * FROM activity_sessions WHERE activity_id=? AND date>=? AND status='scheduled'"
                               " ORDER BY date, start_time", (a["id"], today)).fetchall():
                av = catalogue.availability(c, a, s)
                if av["state"] == "closed":
                    continue
                row = {"id": s["id"], "date": s["date"], "start_time": s["start_time"], "end_time": s["end_time"],
                       "theme": s["theme"], "price_pence": 0 if a["haf_only"] else catalogue.price_of(a, s),
                       "state": av["state"], "places_left": av["places_left"], "opens_at": av["opens_at"]}
                if who and a["registration_level"] != "guest":
                    row["eligibility"] = {}
                    for p in people:
                        probs = checker.problems(a, s, p)
                        status = c.execute("SELECT status FROM bookings WHERE session_id=? AND participant_id=? AND"
                                           " status NOT IN ('cancelled','expired')", (s["id"], p["id"])).fetchone()
                        row["eligibility"][p["ref"]] = {
                            "ok": not probs, "code": probs[0][0] if probs else None,
                            "message": probs[0][1] if probs else None,
                            "status": bookings.STATUS_WORDS[status[0]] if status else None}
                sessions.append(row)
            if not sessions:
                continue
            out["activities"].append({
                "id": a["id"], "slug": a["slug"], "title": a["title"], "summary": a["summary"],
                "description": a["description"], "image": a["image"], "category": a["category"],
                "category_key": a["category_key"], "colour": a["colour"], "centre": a["centre"],
                "age_text": catalogue.age_range_text(a), "min_age_months": a["min_age_months"],
                "max_age_months": a["max_age_months"], "level": a["registration_level"],
                "parent_must_stay": bool(a["parent_must_stay"]), "haf_only": bool(a["haf_only"]),
                "requires_approval": bool(a["requires_approval"]), "adult_price_pence": a["adult_price_pence"],
                "max_party_size": a["max_party_size"], "waitlist": bool(a["waitlist_enabled"]),
                "trial": bool(a["allow_trial"]) and not a["haf_only"] and a["registration_level"] != "guest",
                "trial_price_pence": a["trial_price_pence"],
                "sessions": sessions})
    return h.json(out)


# ---------------------------------------------------------------- quote and confirm


def _account(c, h):
    return c.execute("SELECT * FROM accounts WHERE id=?", (_who(h)["id"],)).fetchone()


@route("POST", "/api/book/quote", auth="account")
def quote(h):
    d = h.json_body() or {}
    with db.read() as c:
        if not _live(c):
            return h.json({"error": "Online booking isn't open yet."}, 403)
        account = _account(c, h)
        items = bookings.resolve_items(c, account, d.get("items"))
        _, q = bookings.assess(c, account, items)
    return h.json(q)


@route("POST", "/api/book/confirm", auth="account")
def confirm(h):
    d = h.json_body() or {}
    if not d.get("accept_terms"):
        return h.json({"error": "Please tick to accept the booking terms.", "errors": {"accept_terms": "Please tick"
                       " to accept the booking terms."}}, 422)
    try:
        with db.tx() as c:
            if not _live(c):
                return h.json({"error": "Online booking isn't open yet."}, 403)
            account = _account(c, h)
            checkout, rows, needs_card = bookings.confirm_basket(c, h, account, d.get("items"), d.get("pay_mode"),
                                                                 d.get("idempotency_key"))
            result = {"checkout": checkout["ref"], "status": checkout["status"],
                      "bookings": [bookings.booking_json(c, b) for b in rows]}
    except bookings.Refused as e:
        return h.json({"error": str(e), "quote": e.quote}, 409)
    if needs_card:
        try:
            result["redirect"] = payments_stripe.start_checkout(checkout["ref"], h)
        except payments_stripe.StripeError:
            return h.json({"error": "Card payment couldn't start, so nothing was booked. Please try again in a few "
                                    "minutes."}, 502)
    elif checkout["status"] == "awaiting_payment" and checkout["stripe_url"]:
        result["redirect"] = checkout["stripe_url"]  # a repeated click on the same basket
    return h.json(result)


# ---------------------------------------------------------------- My bookings


@route("GET", "/api/account/bookings", auth="account")
def my_bookings(h):
    who = _who(h)
    today = catalogue.uk_today().isoformat()
    with db.read() as c:
        rows = c.execute("SELECT b.* FROM bookings b JOIN activity_sessions s ON s.id=b.session_id"
                         " WHERE (b.account_id=? OR b.guest_contact_id IN (SELECT id FROM guest_contacts WHERE"
                         " account_id=?)) AND b.status<>'expired' ORDER BY s.date, s.start_time",
                         (who["id"], who["id"])).fetchall()
        items = [bookings.booking_json(c, b) for b in rows]
        invoices = [money.invoice_json(c, i, with_lines=False) for i in c.execute(
            "SELECT * FROM invoices WHERE account_id=? AND status<>'void' ORDER BY id DESC", (who["id"],))]
        credit = money.credit_balance(c, who["id"])
    upcoming = [b for b in items if b["date"] >= today and b["status"] != "cancelled"]
    return h.json({"upcoming": upcoming, "offers": [b for b in upcoming if b["status"] == "offered"],
                   "past": [b for b in items if b["date"] < today and b["status"] == "confirmed"][-50:],
                   "cancelled": [b for b in items if b["status"] == "cancelled" and b["date"] >= today],
                   "invoices": invoices, "credit_pence": credit, "card_payments": payments_stripe.configured()})


def _own_booking(c, h, ref):
    b = c.execute("SELECT * FROM bookings WHERE ref=? AND account_id=?", (ref, _who(h)["id"])).fetchone()
    if not b:
        raise LookupError
    return b


@route("GET", "/api/account/bookings/<ref>/cancel-terms", auth="account")
def cancel_terms(h, ref):
    with db.read() as c:
        return h.json(bookings.cancel_terms(c, _own_booking(c, h, ref)))


@route("POST", "/api/account/bookings/<ref>/cancel", auth="account")
def cancel_booking(h, ref):
    with db.tx() as c:
        b = _own_booking(c, h, ref)
        terms = bookings.cancel_terms(c, b)
        if not terms["allowed"]:
            raise ValueError(terms["message"])
        outcome = terms["money"] if terms["money"] in ("credit", "refund_card") else "none"
        bookings.cancel(c, h, b, reason="Cancelled by family", money_outcome=outcome, by_account=_who(h)["id"])
    return h.json({"ok": True, "message": "Booking cancelled. " + terms["message"]})


@route("POST", "/api/account/bookings/<ref>/absence", auth="account")
def absence(h, ref):
    with db.tx() as c:
        b = _own_booking(c, h, ref)
        if b["status"] != "confirmed" or not b["participant_id"]:
            raise ValueError("You can only tell us about an absence for a confirmed booking.")
        s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (b["session_id"],)).fetchone()
        if catalogue.session_start(s) <= catalogue.uk_now() and s["date"] < catalogue.uk_today().isoformat():
            raise ValueError("That session has already happened.")
        bookings.report_absence(c, h, b, _who(h))
    return h.json({"ok": True, "message": "Thanks for letting us know."})


@route("POST", "/api/account/bookings/<ref>/accept", auth="account")
def accept(h, ref):
    d = h.json_body() or {}
    with db.tx() as c:
        b = _own_booking(c, h, ref)
        checkout, needs_card = bookings.accept_offer(c, h, _account(c, h), b, d.get("pay_mode"))
    if needs_card:
        try:
            return h.json({"ok": True, "redirect": payments_stripe.start_checkout(checkout["ref"], h)})
        except payments_stripe.StripeError:
            return h.json({"error": "Card payment couldn't start. The place has been released."}, 502)
    return h.json({"ok": True})


@route("POST", "/api/account/bookings/<ref>/decline-offer", auth="account")
def decline_offer(h, ref):
    with db.tx() as c:
        b = _own_booking(c, h, ref)
        if b["status"] not in ("offered", "waitlisted"):
            raise ValueError("There's no offer or waiting-list place to give up.")
        bookings.cancel(c, h, b, reason="Family left the waiting list", by_account=_who(h)["id"], notify=False)
    return h.json({"ok": True})


# ---------------------------------------------------------------- invoices


def _own_invoice(c, h, number):
    inv = c.execute("SELECT * FROM invoices WHERE number=? AND account_id=? AND status<>'void'",
                    (number, _who(h)["id"])).fetchone()
    if not inv:
        raise LookupError
    return inv


@route("GET", "/api/account/invoices/<number>", auth="account")
def invoice_api(h, number):
    with db.read() as c:
        return h.json(money.invoice_json(c, _own_invoice(c, h, number)))


@route("GET", "/account/invoices/<number>")
def invoice_html(h, number):
    who = _who(h)
    if not who:
        return h.send(302, b"", headers={"Location": "/login?next=" + urllib.parse.quote("/account/invoices/" + number, safe="")})
    with db.read() as c:
        inv = c.execute("SELECT * FROM invoices WHERE number=? AND account_id=? AND status<>'void'",
                        (number, who["id"])).fetchone()
        if not inv:
            return h.send(404, b"Invoice not found", "text/plain; charset=utf-8")
        page = invoice_page.render(c, inv, can_pay=payments_stripe.configured(), nonce=h.new_nonce())
    return invoice_page.send(h, page)


@route("POST", "/api/account/invoices/<number>/pay", auth="account")
def pay_invoice(h, number):
    with db.tx() as c:
        inv = _own_invoice(c, h, number)
        used = money.pay_with_credit(c, _who(h)["id"], inv["id"])
        inv = c.execute("SELECT * FROM invoices WHERE id=?", (inv["id"],)).fetchone()
        if money.balance(inv) <= 0:
            audit.record(c, h, "invoice.pay_credit", entity_type="invoice", entity_id=inv["id"])
            return h.json({"ok": True, "message": "Paid with your account credit (%s)." % money.pounds(used)})
        ref = payments_stripe.pay_invoice(c, h, inv, account_id=_who(h)["id"])
    try:
        return h.json({"ok": True, "redirect": payments_stripe.start_checkout(ref, h)})
    except payments_stripe.StripeError:
        return h.json({"error": "Card payment couldn't start — please try again in a few minutes."}, 502)


@route("GET", "/booking-terms")
def booking_terms(h):
    from .pages import page
    s = booking_settings.get_all()
    return page(h, "booking-terms.html", "/booking-terms",
                subs={"offer_hours": str(s["waitlist_offer_hours"]), "hold_minutes": str(s["hold_minutes"]),
                      "terms_days": str(s["payment_terms_days"]), "cutoff_hours": str(s["cancel_cutoff_hours"]),
                      "refund_days": str(s["refund_days"])})
