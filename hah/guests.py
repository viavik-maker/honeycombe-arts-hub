"""One-off events without an account (mockup 4): an email address, a phone
number and how many are coming.

Free places are held for 30 minutes until the booker clicks the link we
email them (so nobody can fill an event with made-up addresses); paid places
go through Stripe. A guest who later registers with the same email sees
these bookings in their account."""
import datetime

from . import audit, bookings, catalogue, db, family, money, outbox, payments_stripe, ratelimit, security, validate, worker
from .validate import Invalid
from .web import route, site_url

ratelimit.LIMITS.update({"guest_booking": (20, 60 * 60)})
HOLD_MINUTES = 30


def _activity_session(c, sid):
    s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (sid,)).fetchone() if str(sid).isdigit() else None
    if not s:
        raise LookupError
    a = c.execute("SELECT * FROM activities WHERE id=?", (s["activity_id"],)).fetchone()
    if a["registration_level"] != "guest" or a["status"] != "published":
        raise LookupError
    return a, s


@route("GET", "/api/book/guest/<slug>", auth=None)
def event_info(h, slug):
    sid = h.query().get("session") or ""
    with db.read() as c:
        if not booking_live(c):
            return h.json({"error": "Online booking isn't open yet."}, 403)
        a, s = _activity_session(c, sid)
        if a["slug"] != slug:
            raise LookupError
        av = catalogue.availability(c, a, s)
        cen = c.execute("SELECT name FROM centres WHERE id=?", (catalogue.centre_of(a, s),)).fetchone()
    return h.json({"activity": {"title": a["title"], "summary": a["summary"], "age_text": catalogue.age_range_text(a),
                                "max_party_size": a["max_party_size"], "capacity_counts": a["capacity_counts"],
                                "child_price_pence": catalogue.price_of(a, s), "adult_price_pence": a["adult_price_pence"],
                                "centre": cen["name"] if cen else ""},
                   "session": {"id": s["id"], "date": s["date"], "start_time": s["start_time"], "end_time": s["end_time"],
                               "theme": s["theme"], "state": av["state"], "places_left": av["places_left"]},
                   "card_payments": payments_stripe.configured()})


def booking_live(c):
    from . import booking_settings
    return booking_settings.get("booking_live", c)


def _token(c, purpose, guest_id, booking_id=None, minutes=HOLD_MINUTES):
    t = security.new_token(24)
    c.execute("INSERT INTO guest_tokens(purpose, token_hash, guest_contact_id, booking_id, expires_at, created_at)"
              " VALUES (?,?,?,?,?,?)", (purpose, security.hash_token(t), guest_id, booking_id,
                                        catalogue.utc_iso(catalogue.uk_now() + datetime.timedelta(minutes=minutes)),
                                        db.now()))
    return t


@route("POST", "/api/book/guest")
def book(h):
    d = h.json_body() or {}
    if d.get("website"):  # honeypot: people don't fill in hidden boxes
        return h.json({"ok": True, "status": "pending_confirmation"})
    ip = h.client_ip()
    if not ratelimit.hit("guest_booking", ip):
        return h.json({"error": "Too many bookings from here — please try again later or call us."}, 429)
    errors = {}
    email = validate.email(d.get("email"))
    phone = validate.uk_phone(d.get("phone"))
    if not email:
        errors["email"] = "Enter your email address."
    if not phone:
        errors["phone"] = "Enter a UK phone number."
    try:
        adults, children = int(d.get("adults") or 0), int(d.get("children") or 0)
    except (TypeError, ValueError):
        adults = children = -1
    if adults < 0 or children < 0 or adults + children == 0:
        errors["children"] = "Choose how many are coming."
    if not d.get("adult_18"):
        errors["adult_18"] = "The person booking must be 18 or over."
    if children and not d.get("ages_ok"):
        errors["ages_ok"] = "Please confirm the children are within the age range."
    if errors:
        raise Invalid(errors)
    name = validate.text(d.get("name"), 80) or None
    with db.tx() as c:
        if not booking_live(c):
            return h.json({"error": "Online booking isn't open yet."}, 403)
        a, s = _activity_session(c, d.get("session_id"))
        if adults + children > a["max_party_size"]:
            raise Invalid({"children": "You can book up to %d places at once — call us for bigger groups." % a["max_party_size"]})
        state, _ = catalogue.window(a, s)
        if state != "open":
            raise ValueError("Booking isn't open for this session.")
        g = c.execute("SELECT * FROM guest_contacts WHERE email=? AND anonymised_at IS NULL", (email,)).fetchone()
        now = db.now()
        if g:
            # anyone can type any email here, so only fill gaps: never replace the number staff would ring
            c.execute("UPDATE guest_contacts SET phone=COALESCE(phone, ?), name=COALESCE(name, ?), last_booking_at=?"
                      " WHERE id=?", (phone, name, now, g["id"]))
            gid = g["id"]
            if c.execute("SELECT 1 FROM bookings WHERE session_id=? AND guest_contact_id=? AND status IN "
                         + catalogue.HOLDING_SQL, (s["id"], gid)).fetchone():
                return h.json({"error": "You've already booked this session — check your email. To change it, "
                                        "please call us."}, 409)
        else:
            acct = c.execute("SELECT id FROM accounts WHERE email=? AND email_verified_at IS NOT NULL AND status='active'",
                             (email,)).fetchone()
            gid = c.execute("INSERT INTO guest_contacts(email, phone, name, created_at, last_booking_at, account_id)"
                            " VALUES (?,?,?,?,?,?)", (email, phone, name, now, now, acct["id"] if acct else None)).lastrowid
        places = children + (adults if a["capacity_counts"] == "all_people" else 0) or 1
        if catalogue.places_taken(c, s["id"]) + places > s["capacity"] or catalogue.waitlist_count(c, s["id"]):
            return h.json({"error": "Sorry, there aren't enough places left for %d." % places}, 409)
        price = catalogue.price_of(a, s) * children + a["adult_price_pence"] * adults
        item = {"kind": "party", "session": s, "activity": a, "children": [], "adults": adults, "places": places}
        paid = price > 0
        if paid and not payments_stripe.configured():
            return h.json({"error": "Paying online isn't available yet — please call us on 07932 772905 to book."}, 409)
        hold = catalogue.utc_iso(catalogue.uk_now() + datetime.timedelta(minutes=HOLD_MINUTES))
        cid = c.execute("INSERT INTO checkouts(ref, guest_contact_id, idempotency_key, amount_pence, pay_mode, status,"
                        " expires_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (family.new_ref("K"), gid, str(d.get("idempotency_key") or family.new_ref("G"))[:64], price,
                         "stripe" if paid else "free", "creating" if paid else "completed", hold, now)).lastrowid
        b = bookings._insert_booking(c, item, "pending_payment" if paid else "pending_confirmation",
                                     guest_contact_id=gid, checkout_id=cid, price=price,
                                     funding="paid" if paid else "free", hold=hold, via="guest", party_name=name)
        c.execute("UPDATE bookings SET party_children=? WHERE id=?", (children, b["id"]))
        ctx = {"activity": a["title"], "when": "%s, %s–%s" % (catalogue.nice_date(s["date"]), s["start_time"],
                                                            s["end_time"]),
               "places": "%d adult%s, %d child%s" % (adults, "" if adults == 1 else "s", children,
                                                    "" if children == 1 else "ren")}
        if not paid:
            t = _token(c, "confirm_booking", gid, b["id"])
            outbox.email(c, email, "guest_confirm", ctx, secret="%s/book/guest/confirm#t=%s" % (site_url(h), t),
                         booking_id=b["id"])
        if d.get("marketing"):
            t2 = _token(c, "marketing_opt_in", gid, minutes=60 * 24 * 7)
            outbox.email(c, email, "newsletter_confirm", {}, secret="%s/book/guest/confirm#m=%s" % (site_url(h), t2))
        audit.record(c, h, "booking.guest", entity_type="booking", entity_id=b["id"], details={"places": places,
                                                                                               "paid": paid})
        checkout_ref = c.execute("SELECT ref FROM checkouts WHERE id=?", (cid,)).fetchone()[0]
    if paid:
        try:
            return h.json({"ok": True, "redirect": payments_stripe.start_checkout(checkout_ref, h, email=email)})
        except payments_stripe.StripeError:
            return h.json({"error": "Card payment couldn't start, so nothing was booked. Please try again."}, 502)
    return h.json({"ok": True, "status": "pending_confirmation", "minutes": HOLD_MINUTES})


@route("POST", "/api/book/guest/confirm")
def confirm(h):
    d = h.json_body() or {}
    token = d.get("token") or ""
    with db.tx() as c:
        t = c.execute("SELECT * FROM guest_tokens WHERE token_hash=?", (security.hash_token(token),)).fetchone()
        if not t or t["used_at"]:
            return h.json({"error": "This link has already been used or isn't valid."}, 400)
        if t["expires_at"] <= db.now():
            return h.json({"error": "Sorry, this link has expired, so the places were released. Please book again."}, 400)
        c.execute("UPDATE guest_tokens SET used_at=? WHERE id=?", (db.now(), t["id"]))
        g = c.execute("SELECT * FROM guest_contacts WHERE id=?", (t["guest_contact_id"],)).fetchone()
        if t["purpose"] == "marketing_opt_in":
            from . import marketing
            marketing.opt_in(c, g["email"], source="guest_booking", name=g["name"], guest_contact_id=g["id"])
            return h.json({"ok": True, "message": "Thanks — you're signed up for news about future events."})
        b = c.execute("SELECT * FROM bookings WHERE id=?", (t["booking_id"],)).fetchone()
        if b["status"] != "pending_confirmation":
            return h.json({"error": "This booking has already been confirmed or has lapsed."}, 400)
        b = bookings.confirm_booking(c, b)
        send_confirmation(c, h, c.execute("SELECT * FROM checkouts WHERE id=?", (b["checkout_id"],)).fetchone(), [b], None)
    return h.json({"ok": True, "message": "Your places are confirmed — see you there! We've emailed the details."})


def send_confirmation(c, h, checkout, rows, invoice):
    """The guest's "you're booked" email (also used after a card payment)."""
    g = c.execute("SELECT * FROM guest_contacts WHERE id=?", (checkout["guest_contact_id"],)).fetchone()
    inv_text = ""
    if invoice:
        inv = c.execute("SELECT * FROM invoices WHERE id=?", (invoice["id"],)).fetchone()
        inv_text = "Paid: %s (receipt %s)." % (money.pounds(inv["paid_pence"]), inv["number"])
    outbox.email(c, g["email"], "guest_booked", {"lines": bookings.summary_text(c, rows), "invoice": inv_text,
                                                  "register_url": site_url(h) + "/register"},
                 booking_id=rows[0]["id"] if rows else None)


@worker.job("guest_holds", every=60, timeout=60)
def expire_unconfirmed():
    """Free places nobody confirmed within 30 minutes go back on sale."""
    from . import waitlist
    with db.tx() as c:
        rows = c.execute("SELECT * FROM bookings WHERE status='pending_confirmation' AND hold_expires_at<=?",
                         (db.now(),)).fetchall()
        for b in rows:
            c.execute("UPDATE bookings SET status='expired', updated_at=? WHERE id=?", (db.now(), b["id"]))
        for sid in {b["session_id"] for b in rows}:
            waitlist.places_freed(c, sid)
    return "expired %d" % len(rows)
