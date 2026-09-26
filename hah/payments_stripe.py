"""Card payments with Stripe Checkout (stdlib urllib, no SDK).

Settings (host environment only — never in the database or content.json):
  STRIPE_SECRET_KEY       sk_live_… / sk_test_…
  STRIPE_WEBHOOK_SECRET   whsec_… (Dashboard → Developers → Webhooks, endpoint
                          {SITE_URL}/api/stripe/webhook, events: checkout.session.*,
                          charge.refunded, charge.dispute.created)
  STRIPE_API_BASE         only for tests (a fake Stripe)

Flow: the booking transaction holds the places (pending_payment) and
commits; then we ask Stripe for a Checkout Session and send the family to
it. Stripe tells us the result by webhook (signed; checked here), and the
return page asks Stripe directly if the webhook is slow. No child names or
health details are ever sent to Stripe — line items use booking references."""
import datetime
import hashlib
import hmac
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from . import catalogue, db, intray, worker
from .web import route, site_url

API_VERSION = "2024-06-20"
TIMEOUT = 20


class StripeError(Exception):
    pass


def secret_key():
    return os.environ.get("STRIPE_SECRET_KEY", "")


def configured():
    return bool(secret_key())


def status():
    key = secret_key()
    return {"configured": bool(key), "mode": "live" if key.startswith("sk_live_") else ("test" if key else None),
            "webhook_secret": bool(os.environ.get("STRIPE_WEBHOOK_SECRET"))}


def _encode(data, prefix=""):
    """Stripe's form encoding: nested keys in brackets."""
    out = []
    if isinstance(data, dict):
        for k, v in data.items():
            out += _encode(v, "%s[%s]" % (prefix, k) if prefix else k)
    elif isinstance(data, list):
        for i, v in enumerate(data):
            out += _encode(v, "%s[%d]" % (prefix, i))
    elif data is not None:
        out.append((prefix, "true" if data is True else "false" if data is False else str(data)))
    return out


def call(method, path, data=None, idempotency_key=None):
    base = os.environ.get("STRIPE_API_BASE", "https://api.stripe.com").rstrip("/")
    body = urllib.parse.urlencode(_encode(data or {})).encode() if data is not None else None
    req = urllib.request.Request(base + path, data=body, method=method)
    req.add_header("Authorization", "Bearer " + secret_key())
    req.add_header("Stripe-Version", API_VERSION)
    if body is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if idempotency_key:
        req.add_header("Idempotency-Key", idempotency_key)
    ctx = ssl.create_default_context() if base.startswith("https") else None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        raise StripeError("Stripe said %s: %s" % (e.code, msg or e.reason))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise StripeError("couldn't reach Stripe: %s" % e)


# ---------------------------------------------------------------- starting a checkout


def _line_items(c, checkout):
    if checkout["invoice_id"]:
        inv = c.execute("SELECT number FROM invoices WHERE id=?", (checkout["invoice_id"],)).fetchone()
        return [{"price_data": {"currency": "gbp", "unit_amount": checkout["amount_pence"],
                                "product_data": {"name": "Invoice %s" % inv["number"]}}, "quantity": 1}]
    items, rows = [], c.execute(
        "SELECT b.ref, b.price_pence, a.title, s.date FROM bookings b JOIN activities a ON a.id=b.activity_id"
        " JOIN activity_sessions s ON s.id=b.session_id WHERE b.checkout_id=? AND b.status='pending_payment'"
        " ORDER BY s.date", (checkout["id"],)).fetchall()
    total = sum(r["price_pence"] for r in rows)
    if checkout["credit_pence"] or total != checkout["amount_pence"]:
        # account credit used: one line for the balance, so the sum is exact
        return [{"price_data": {"currency": "gbp", "unit_amount": checkout["amount_pence"],
                                "product_data": {"name": "Honeycombe Arts Hub booking %s" % checkout["ref"],
                                                 "description": "%d session place(s), after %s account credit" % (
                                                     len(rows), "£%.2f" % (checkout["credit_pence"] / 100))}},
                 "quantity": 1}]
    for r in rows:
        items.append({"price_data": {"currency": "gbp", "unit_amount": r["price_pence"],
                                     "product_data": {"name": "%s · %s" % (r["title"], catalogue.nice_date(r["date"])),
                                                      "description": "Booking %s" % r["ref"]}}, "quantity": 1})
    return items


def start_checkout(checkout_ref, h=None, email=None):
    """Create the Stripe Checkout Session for a committed checkout. Returns the
    URL to send the family to. On failure the held places are released."""
    from . import bookings
    with db.read() as c:
        checkout = c.execute("SELECT * FROM checkouts WHERE ref=?", (checkout_ref,)).fetchone()
        items = _line_items(c, checkout)
        if not email and checkout["account_id"]:
            email = c.execute("SELECT email FROM accounts WHERE id=?", (checkout["account_id"],)).fetchone()[0]
        elif not email and checkout["guest_contact_id"]:
            email = c.execute("SELECT email FROM guest_contacts WHERE id=?", (checkout["guest_contact_id"],)).fetchone()[0]
    base = site_url(h)
    back = "/account/bookings" if checkout["invoice_id"] else "/book/review"
    expires = int(time.time()) + 31 * 60
    payload = {"mode": "payment", "currency": "gbp", "line_items": items, "customer_email": email,
               "client_reference_id": checkout["ref"], "metadata": {"checkout_ref": checkout["ref"]},
               "payment_intent_data": {"metadata": {"checkout_ref": checkout["ref"]}},
               "success_url": base + "/book/done?c=%s&session_id={CHECKOUT_SESSION_ID}" % checkout["ref"],
               "cancel_url": base + back + "?cancelled=" + checkout["ref"], "expires_at": expires, "locale": "en-GB"}
    try:
        session = call("POST", "/v1/checkout/sessions", payload, idempotency_key="checkout-" + checkout["ref"])
    except StripeError:
        with db.tx() as c:
            bookings.release_checkout(c, checkout, status="failed")
        raise
    with db.tx() as c:
        c.execute("UPDATE checkouts SET status='awaiting_payment', stripe_session_id=?, stripe_url=?, expires_at=?"
                  " WHERE id=? AND status='creating'",
                  (session["id"], session["url"],
                   max(checkout["expires_at"] or "", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires))),
                   checkout["id"]))
    return session["url"]


def pay_invoice(c, h, inv, account_id=None, guest_contact_id=None):
    """Make a checkout for an invoice's balance (inside a tx). Returns its ref;
    call start_checkout(ref) after committing."""
    from . import family, money
    bal = money.balance(inv)
    if bal <= 0:
        raise ValueError("This invoice has been paid.")
    if not configured():
        raise ValueError("Card payments aren't available — please pay by bank transfer or vouchers, quoting %s."
                         % inv["number"])
    ref = family.new_ref("K")
    c.execute("INSERT INTO checkouts(ref, account_id, guest_contact_id, invoice_id, idempotency_key, amount_pence,"
              " pay_mode, status, expires_at, created_at) VALUES (?,?,?,?,?,?, 'stripe', 'creating', ?, ?)",
              (ref, account_id, guest_contact_id, inv["id"], "invoice-%s-%s" % (inv["number"], db.now()), bal,
               catalogue.utc_iso(catalogue.uk_now() + datetime.timedelta(minutes=35)), db.now()))
    return ref


# ---------------------------------------------------------------- webhook


def verify(raw, header, secret, tolerance=300, now=None):
    """Check a Stripe-Signature header (t=…,v1=…[,v1=…])."""
    if not header or not secret:
        return False
    t, sigs = None, []
    for part in header.split(","):
        k, _, v = part.strip().partition("=")
        if k == "t" and v.isdigit():
            t = int(v)
        elif k == "v1":
            sigs.append(v)
    if t is None or not sigs:
        return False
    expected = hmac.new(secret.encode(), b"%d." % t + raw, hashlib.sha256).hexdigest()
    return abs((now or time.time()) - t) <= tolerance and any(hmac.compare_digest(expected, s) for s in sigs)


def handle_session(c, obj, h=None):
    """A Checkout Session object (from a webhook or a lookup)."""
    from . import bookings
    ref = (obj.get("metadata") or {}).get("checkout_ref") or obj.get("client_reference_id")
    checkout = c.execute("SELECT * FROM checkouts WHERE ref=?", (ref or "",)).fetchone()
    if not checkout:
        return "unknown checkout"
    if obj.get("status") == "complete" and obj.get("payment_status") == "paid":
        return bookings.complete_card_checkout(c, checkout, payment_intent=obj.get("payment_intent"),
                                               amount=obj.get("amount_total") or 0, currency=obj.get("currency") or "",
                                               session_id=obj.get("id"), h=h)
    if obj.get("status") == "expired":
        return "released" if bookings.release_checkout(c, checkout) else "already"
    return "waiting"


@route("POST", "/api/stripe/webhook", csrf=False, body_limit=256 * 1024)
def webhook(h):
    raw = h.body()
    if not verify(raw, h.headers.get("Stripe-Signature") or "", os.environ.get("STRIPE_WEBHOOK_SECRET", "")):
        return h.json({"error": "bad signature"}, 400)
    try:
        event = json.loads(raw.decode())
        eid, etype, obj = event["id"], event["type"], event["data"]["object"]
    except (ValueError, KeyError, TypeError):
        return h.json({"error": "bad event"}, 400)
    from . import bookings
    with db.tx() as c:
        seen = c.execute("SELECT processed_at FROM stripe_events WHERE id=?", (eid,)).fetchone()
        if seen and seen["processed_at"]:
            return h.json({"ok": True, "duplicate": True})
        c.execute("INSERT OR IGNORE INTO stripe_events(id, type, received_at) VALUES (?,?,?)", (eid, etype, db.now()))
        result = "ignored"
        if etype in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            result = handle_session(c, obj, h)
        elif etype in ("checkout.session.expired", "checkout.session.async_payment_failed"):
            ref = (obj.get("metadata") or {}).get("checkout_ref") or obj.get("client_reference_id")
            checkout = c.execute("SELECT * FROM checkouts WHERE ref=?", (ref or "",)).fetchone()
            if checkout:
                result = "released" if bookings.release_checkout(
                    c, checkout, "failed" if etype.endswith("failed") else "expired") else "already"
        elif etype in ("charge.refunded", "refund.updated", "charge.refund.updated"):
            result = _refund_update(c, obj)
        elif etype == "charge.dispute.created":
            intray.add(c, "payment_dispute", "A card payment has been disputed (Stripe) — check the Stripe dashboard",
                       perm="finance.view", entity_type="stripe_charge", dedupe=False)
            result = "in-tray"
        c.execute("UPDATE stripe_events SET processed_at=?, status=? WHERE id=?", (db.now(), str(result)[:40], eid))
    return h.json({"ok": True, "result": result})


def _refund_update(c, obj):
    refunds = obj.get("refunds", {}).get("data") if obj.get("object") == "charge" else [obj]
    n = 0
    for r in refunds or []:
        status = {"succeeded": "succeeded", "failed": "failed", "canceled": "failed"}.get(r.get("status"))
        if status and r.get("id"):
            n += c.execute("UPDATE refunds SET status=?, processed_at=? WHERE stripe_refund_id=? AND status<>?",
                           (status, db.now(), r["id"], status)).rowcount
    return "refunds %d" % n


# ---------------------------------------------------------------- the return page


@route("GET", "/api/book/checkout/<ref>/status")
def checkout_status(h, ref):
    """Polled by /book/done. Only the family (or guest browser) that made the
    checkout can see it: accounts by session, guests by knowing the ref and
    the Stripe session id."""
    from . import bookings
    who = h.principal("account")
    q = h.query()
    with db.read() as c:
        checkout = c.execute("SELECT * FROM checkouts WHERE ref=?", (ref,)).fetchone()
    if not checkout or not ((who and checkout["account_id"] == who["id"]) or
                            (checkout["stripe_session_id"] and q.get("session_id") == checkout["stripe_session_id"])):
        return h.json({"error": "not found"}, 404)
    if checkout["status"] == "awaiting_payment" and configured() and checkout["stripe_session_id"] and \
            checkout["created_at"] < catalogue.utc_iso(catalogue.uk_now() - datetime.timedelta(seconds=8)):
        # the webhook is late: ask Stripe ourselves
        try:
            obj = call("GET", "/v1/checkout/sessions/" + urllib.parse.quote(checkout["stripe_session_id"]))
            with db.tx() as c:
                handle_session(c, obj, h)
        except StripeError:
            pass
    with db.read() as c:
        checkout = c.execute("SELECT * FROM checkouts WHERE id=?", (checkout["id"],)).fetchone()
        rows = c.execute("SELECT * FROM bookings WHERE checkout_id=? ORDER BY id", (checkout["id"],)).fetchall()
        return h.json({"status": checkout["status"], "bookings": [bookings.booking_json(c, b) for b in rows]})


# ---------------------------------------------------------------- background jobs


@worker.job("checkout_holds", every=60, timeout=300)
def expire_holds():
    """Release places held for card payments nobody finished. We check with
    Stripe first (and expire the session there), so a payment can't land
    after its place has gone."""
    from . import bookings
    now = db.now()
    with db.read() as c:
        due = c.execute("SELECT * FROM checkouts WHERE status IN ('creating','awaiting_payment') AND expires_at<=?",
                        (now,)).fetchall()
    released = 0
    for checkout in due:
        obj = None
        if checkout["stripe_session_id"] and configured():
            try:
                obj = call("GET", "/v1/checkout/sessions/" + urllib.parse.quote(checkout["stripe_session_id"]))
                if obj.get("status") == "open":
                    obj = call("POST", "/v1/checkout/sessions/%s/expire" % urllib.parse.quote(checkout["stripe_session_id"]), {})
            except StripeError:
                continue  # try again next minute rather than release a place that may be paid for
        with db.tx() as c:
            if obj is not None and obj.get("payment_status") == "paid":
                handle_session(c, obj)
            elif bookings.release_checkout(c, checkout):
                released += 1
    # stray holds whose checkout never got going
    with db.tx() as c:
        for b in c.execute("SELECT b.* FROM bookings b LEFT JOIN checkouts k ON k.id=b.checkout_id"
                           " WHERE b.status='pending_payment' AND b.hold_expires_at<=? AND (k.id IS NULL OR"
                           " k.status NOT IN ('creating','awaiting_payment'))", (now,)).fetchall():
            c.execute("UPDATE bookings SET status='expired', updated_at=? WHERE id=?", (db.now(), b["id"]))
            from . import waitlist
            waitlist.places_freed(c, b["session_id"])
            released += 1
    return "released %d" % released


@worker.job("stripe_refunds", every=60, timeout=300)
def send_refunds():
    """Card refunds are queued in the booking transaction and sent from here."""
    if not configured():
        return "not configured"
    with db.read() as c:
        rows = c.execute("SELECT r.*, p.stripe_payment_intent_id FROM refunds r JOIN payments p ON p.id=r.payment_id"
                         " WHERE r.method='stripe' AND r.status='pending' AND r.stripe_refund_id IS NULL"
                         " ORDER BY r.id LIMIT 20").fetchall()
    sent = failed = 0
    for r in rows:
        try:
            res = call("POST", "/v1/refunds", {"payment_intent": r["stripe_payment_intent_id"],
                                               "amount": r["amount_pence"], "metadata": {"refund_id": r["id"]}},
                       idempotency_key="refund-%d" % r["id"])
        except StripeError as e:
            with db.tx() as c:
                c.execute("UPDATE refunds SET failure_reason=? WHERE id=?", (str(e)[:300], r["id"]))
                intray.add(c, "refund_failed", "A card refund couldn't be sent — check Finance",
                           perm="finance.view", entity_type="refund", entity_id=r["id"])
            failed += 1
            continue
        with db.tx() as c:
            status = "succeeded" if res.get("status") == "succeeded" else ("failed" if res.get("status") == "failed"
                                                                           else "pending")
            c.execute("UPDATE refunds SET stripe_refund_id=?, status=?, processed_at=? WHERE id=?",
                      (res.get("id"), status, db.now() if status != "pending" else None, r["id"]))
        sent += 1
    return "sent %d, failed %d" % (sent, failed)
