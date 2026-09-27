"""The shop: things families buy (T-shirts, art packs, gift vouchers…),
replacing MagicBooking's e-shop. Off until Booking settings → The shop.

A family signs in, chooses products and pays by card (Stripe, through the
normal "pay this invoice" checkout) or when they collect. Every order is an
invoice, so Finance, reminders, refunds and the accounting export work as
they do for bookings. Stock is taken when the order is placed and put back
if it's cancelled; card orders left unpaid for a day are cancelled."""
import datetime
import re

from . import audit, booking_settings, catalogue, db, family, intray, money, outbox, payments_stripe, validate, worker
from .validate import Invalid
from .web import route, site_url

STATUSES = ("new", "ready", "collected", "posted", "cancelled")
STATUS_WORDS = {"new": "Received", "ready": "Ready to collect", "collected": "Collected", "posted": "Posted",
                "cancelled": "Cancelled"}
UNPAID_CARD_HOURS = 24
MAX_LINES = 20


def _live(c):
    return booking_settings.get("shop_live", c)


def product_json(p, staff=False):
    out = {"id": p["id"], "slug": p["slug"], "title": p["title"], "description": p["description"], "image": p["image"],
           "price_pence": p["price_pence"], "max_per_order": p["max_per_order"],
           "in_stock": p["stock"] is None or p["stock"] > 0,
           "left": p["stock"] if p["stock"] is not None and p["stock"] <= 5 else None}
    if staff:
        out.update(stock=p["stock"], status=p["status"], sort=p["sort"])
    return out


def order_json(c, o, staff=False):
    lines = [dict(title=r["title"], quantity=r["quantity"], unit_pence=r["unit_pence"], amount_pence=r["amount_pence"])
             for r in c.execute("SELECT * FROM shop_order_lines WHERE order_id=? ORDER BY id", (o["id"],))]
    inv = c.execute("SELECT * FROM invoices WHERE id=?", (o["invoice_id"],)).fetchone() if o["invoice_id"] else None
    out = {"ref": o["ref"], "status": o["status"], "status_text": STATUS_WORDS[o["status"]], "pay_mode": o["pay_mode"],
           "total_pence": o["total_pence"], "notes": o["notes"], "created_at": o["created_at"], "lines": lines,
           "invoice": {"number": inv["number"], "status": inv["status"], "balance_pence": money.balance(inv)}
           if inv else None}
    if staff:
        a = c.execute("SELECT ref, first_name, last_name, email, mobile FROM accounts WHERE id=?",
                      (o["account_id"],)).fetchone()
        out.update(staff_notes=o["staff_notes"], family={"ref": a["ref"], "name": "%s %s" % (a["first_name"], a["last_name"]),
                                                          "email": a["email"], "mobile": a["mobile"]})
    return out


# ---------------------------------------------------------------- families


@route("GET", "/api/shop")
def catalogue_api(h):
    with db.read() as c:
        live = _live(c)
        rows = c.execute("SELECT * FROM shop_products WHERE status='live' ORDER BY sort, title").fetchall() if live else []
        note = booking_settings.get("shop_collection_note", c)
    return h.json({"live": bool(live), "products": [product_json(p) for p in rows], "collection_note": note,
                   "card_payments": payments_stripe.configured()})


def _invoice_for(c, account_id, order, lines):
    name, email, addr = money._bill_to(c, account_id)
    issue = catalogue.uk_today()
    number = money.next_number(c, "invoice")
    iid = c.execute(
        "INSERT INTO invoices(number, account_id, bill_to_name, bill_to_email, bill_to_address, issue_date, due_date,"
        " status, total_pence, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (number, account_id, name, email, addr, issue.isoformat(), money.due_date(issue, c=c).isoformat(),
         "issued" if order["total_pence"] else "paid", order["total_pence"], "Shop order %s" % order["ref"],
         db.now())).lastrowid
    for ln in lines:
        c.execute("INSERT INTO invoice_lines(invoice_id, description, quantity, unit_pence, amount_pence, funding_note)"
                  " VALUES (?,?,?,?,?,?)", (iid, "Shop: %s" % ln["title"], ln["quantity"], ln["unit_pence"],
                                              ln["amount_pence"], "Order %s" % order["ref"]))
    return c.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()


@route("POST", "/api/shop/order", auth="account")
def place_order(h):
    who = h.principal("account")
    d = h.json_body() or {}
    key = str(d.get("idempotency_key") or "")[:64]
    if len(key) < 8:
        raise ValueError("Please reload the page and try again.")
    pay_mode = d.get("pay_mode") if d.get("pay_mode") in ("card", "on_collection") else None
    items = d.get("items") if isinstance(d.get("items"), list) else []
    if not items:
        raise ValueError("Choose something first.")
    if len(items) > MAX_LINES:
        raise ValueError("That's a lot of different things — please split it into two orders.")
    card_ref = None
    with db.tx() as c:
        if not _live(c):
            return h.json({"error": "The shop is closed at the moment."}, 403)
        old = c.execute("SELECT * FROM shop_orders WHERE account_id=? AND idempotency_key=?", (who["id"], key)).fetchone()
        if old:
            return h.json({"ok": True, "order": order_json(c, old)})
        lines, problems, wanted = [], [], {}
        for it in items:
            try:
                pid, qty = int((it or {}).get("product")), int((it or {}).get("quantity"))
            except (TypeError, ValueError):
                continue
            if qty <= 0:
                problems.append("Something in your basket is no longer available.")
                continue
            wanted[pid] = wanted.get(pid, 0) + qty
        # the same product twice counts as one line, so the per-order limit and stock see the whole quantity
        for pid, qty in wanted.items():
            p = c.execute("SELECT * FROM shop_products WHERE id=? AND status='live'", (pid,)).fetchone()
            if not p:
                problems.append("Something in your basket is no longer available.")
                continue
            if qty > p["max_per_order"]:
                problems.append("You can order up to %d of %s." % (p["max_per_order"], p["title"]))
            elif p["stock"] is not None and qty > p["stock"]:
                problems.append("Sorry, only %d of %s left." % (p["stock"], p["title"]) if p["stock"] else
                                "Sorry, %s has sold out." % p["title"])
            else:
                lines.append({"product": p, "title": p["title"], "unit_pence": p["price_pence"], "quantity": qty,
                              "amount_pence": p["price_pence"] * qty})
        if problems or not lines:
            return h.json({"error": " ".join(problems) or "Choose something first.", "problems": problems}, 409)
        total = sum(ln["amount_pence"] for ln in lines)
        if total and pay_mode is None:
            raise Invalid({"pay_mode": "Choose how you'd like to pay."})
        if pay_mode == "card" and total and not payments_stripe.configured():
            raise ValueError("Card payments aren't available — choose to pay when you collect.")
        ref = family.new_ref("O")
        oid = c.execute("INSERT INTO shop_orders(ref, account_id, idempotency_key, pay_mode, total_pence, notes,"
                        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                        (ref, who["id"], key, pay_mode or "on_collection", total,
                         validate.text(d.get("notes"), 300) or None, db.now(), db.now())).lastrowid
        for ln in lines:
            c.execute("INSERT INTO shop_order_lines(order_id, product_id, title, unit_pence, quantity, amount_pence)"
                      " VALUES (?,?,?,?,?,?)", (oid, ln["product"]["id"], ln["title"], ln["unit_pence"], ln["quantity"],
                                                ln["amount_pence"]))
            if ln["product"]["stock"] is not None:
                c.execute("UPDATE shop_products SET stock=stock-? WHERE id=?", (ln["quantity"], ln["product"]["id"]))
        order = c.execute("SELECT * FROM shop_orders WHERE id=?", (oid,)).fetchone()
        inv = _invoice_for(c, who["id"], order, lines)
        c.execute("UPDATE shop_orders SET invoice_id=? WHERE id=?", (inv["id"], oid))
        money.pay_with_credit(c, who["id"], inv["id"])
        inv = c.execute("SELECT * FROM invoices WHERE id=?", (inv["id"],)).fetchone()
        if pay_mode == "card" and money.balance(inv) > 0:
            card_ref = payments_stripe.pay_invoice(c, h, inv, account_id=who["id"])
        acct = c.execute("SELECT * FROM accounts WHERE id=?", (who["id"],)).fetchone()
        outbox.email(c, acct["email"], "shop_order", {
            "first_name": acct["first_name"], "ref": ref,
            "lines": "\n".join("• %d × %s — %s" % (ln["quantity"], ln["title"], money.pounds(ln["amount_pence"]))
                               for ln in lines),
            "total": money.pounds(total), "invoice": inv["number"],
            "payment": "We've emailed a separate receipt once your card payment goes through." if card_ref else
            ("Nothing to pay." if money.balance(inv) <= 0 else "Please pay %s when you collect, or online from My "
             "bookings." % money.pounds(money.balance(inv))),
            "collection": booking_settings.get("shop_collection_note", c)}, account_id=who["id"])
        intray.add(c, "shop_order", "New shop order %s (%s)" % (ref, money.pounds(total)), perm="shop.manage",
                   entity_type="shop_order", entity_id=oid, account_id=who["id"], dedupe=False)
        audit.record(c, h, "shop.order", entity_type="shop_order", entity_id=oid, account_id=who["id"],
                     details={"total": total, "pay_mode": pay_mode})
        result = order_json(c, c.execute("SELECT * FROM shop_orders WHERE id=?", (oid,)).fetchone())
    out = {"ok": True, "order": result}
    if card_ref:
        try:
            out["redirect"] = payments_stripe.start_checkout(card_ref, h)
        except payments_stripe.StripeError:
            out["warning"] = "We couldn't start the card payment — your order is saved; pay from My bookings."
    return h.json(out)


@route("GET", "/api/account/shop-orders", auth="account")
def my_orders(h):
    who = h.principal("account")
    with db.read() as c:
        rows = c.execute("SELECT * FROM shop_orders WHERE account_id=? ORDER BY id DESC LIMIT 50", (who["id"],)).fetchall()
        return h.json({"orders": [order_json(c, o) for o in rows]})


# ---------------------------------------------------------------- staff: products

SLUG = re.compile(r"[^a-z0-9]+")


def _clean_product(d, current=None):
    get = (lambda k: d[k] if k in d else (current[k] if current else None))
    errors, out = {}, {}
    out["title"] = validate.text(get("title"), 120)
    if not out["title"]:
        errors["title"] = "Enter a name."
    out["description"] = validate.long_text(get("description"), 2000) or None
    image = validate.text(get("image"), 300)
    if image and not re.match(r"^(/uploads/|/img/)[\w./-]+$", image):
        errors["image"] = "Choose an image from the site's uploads."
    out["image"] = image or None
    for k, lo, hi, default in (("price_pence", 0, 100000, 0), ("max_per_order", 1, 100, 10), ("sort", -1000, 1000, 0)):
        try:
            v = int(get(k) if get(k) not in (None, "") else default)
            if not lo <= v <= hi:
                raise ValueError
            out[k] = v
        except (TypeError, ValueError):
            errors[k] = "Enter a number from %d to %d." % (lo, hi)
    stock = get("stock")
    try:
        out["stock"] = None if stock in (None, "") else int(stock)
        if out["stock"] is not None and out["stock"] < 0:
            raise ValueError
    except (TypeError, ValueError):
        errors["stock"] = "Enter how many you have, or leave it empty for no limit."
    status = get("status") or "draft"
    if status not in ("draft", "live", "archived"):
        errors["status"] = "Choose a status."
    out["status"] = status
    if errors:
        raise Invalid(errors)
    return out


@route("GET", "/api/staff/shop/products", auth="staff", perm="shop.manage")
def staff_products(h):
    with db.read() as c:
        rows = c.execute("SELECT * FROM shop_products ORDER BY status='archived', sort, title").fetchall()
        sold = dict(c.execute("SELECT l.product_id, SUM(l.quantity) FROM shop_order_lines l JOIN shop_orders o"
                              " ON o.id=l.order_id WHERE o.status<>'cancelled' GROUP BY l.product_id").fetchall())
        return h.json({"products": [dict(product_json(p, staff=True), sold=sold.get(p["id"], 0)) for p in rows],
                       "live": bool(_live(c))})


@route("POST", "/api/staff/shop/products", auth="staff", perm="shop.manage")
def create_product(h):
    d = h.json_body() or {}
    with db.tx() as c:
        v = _clean_product(d)
        base = SLUG.sub("-", v["title"].lower()).strip("-")[:50] or "item"
        slug, n = base, 1
        while c.execute("SELECT 1 FROM shop_products WHERE slug=?", (slug,)).fetchone():
            n += 1
            slug = "%s-%d" % (base, n)
        v.update(slug=slug, created_at=db.now(), updated_at=db.now())
        cols = sorted(v)
        pid = c.execute("INSERT INTO shop_products(%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                        [v[k] for k in cols]).lastrowid
        audit.record(c, h, "shop.product_create", entity_type="shop_product", entity_id=pid)
        return h.json({"ok": True, "product": product_json(c.execute("SELECT * FROM shop_products WHERE id=?",
                                                                     (pid,)).fetchone(), staff=True)})


@route("POST", "/api/staff/shop/products/<pid>/update", auth="staff", perm="shop.manage")
def update_product(h, pid):
    d = h.json_body() or {}
    with db.tx() as c:
        p = c.execute("SELECT * FROM shop_products WHERE id=?", (int(pid) if pid.isdigit() else 0,)).fetchone()
        if not p:
            raise LookupError
        v = _clean_product(d, p)
        v["updated_at"] = db.now()
        c.execute("UPDATE shop_products SET %s WHERE id=?" % ",".join("%s=?" % k for k in v), [*v.values(), p["id"]])
        audit.record(c, h, "shop.product_update", entity_type="shop_product", entity_id=p["id"])
        return h.json({"ok": True, "product": product_json(c.execute("SELECT * FROM shop_products WHERE id=?",
                                                                     (p["id"],)).fetchone(), staff=True)})


# ---------------------------------------------------------------- staff: orders


@route("GET", "/api/staff/shop/orders", auth="staff", perm="shop.manage")
def staff_orders(h):
    q = h.query()
    view = q.get("view") or "open"
    where, args = [], []
    if view == "open":
        where.append("o.status IN ('new','ready')")
    elif view in STATUSES:
        where.append("o.status=?")
        args.append(view)
    text = (q.get("q") or "").strip()[:60]
    if text:
        where.append("(o.ref LIKE ? OR a.first_name || ' ' || a.last_name LIKE ? OR a.email LIKE ?)")
        args += ["%" + text + "%"] * 3
    with db.read() as c:
        rows = c.execute("SELECT o.* FROM shop_orders o JOIN accounts a ON a.id=o.account_id" +
                         (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY o.id DESC LIMIT 200",
                         args).fetchall()
        return h.json({"orders": [order_json(c, o, staff=True) for o in rows]})


def _order(c, ref):
    o = c.execute("SELECT * FROM shop_orders WHERE ref=?", (ref,)).fetchone()
    if not o:
        raise LookupError
    return o


def cancel_order(c, h, o, reason, staff_id=None):
    """Put the stock back and credit the invoice; anything paid goes back the way it came (card) or as credit.
    Does nothing to an order that's already cancelled."""
    o = c.execute("SELECT * FROM shop_orders WHERE id=?", (o["id"],)).fetchone()
    if o["status"] == "cancelled":
        return 0
    for ln in c.execute("SELECT * FROM shop_order_lines WHERE order_id=?", (o["id"],)).fetchall():
        c.execute("UPDATE shop_products SET stock=stock+? WHERE id=? AND stock IS NOT NULL", (ln["quantity"], ln["product_id"]))
    refunded = 0
    inv = c.execute("SELECT * FROM invoices WHERE id=?", (o["invoice_id"],)).fetchone() if o["invoice_id"] else None
    if inv and inv["status"] not in ("void", "credited") and inv["total_pence"]:
        lines = c.execute("SELECT * FROM invoice_lines WHERE invoice_id=?", (inv["id"],)).fetchall()
        cn, refundable = money.credit_note(c, inv, [], reason, staff_id=staff_id, invoice_lines=lines)
        if cn and refundable:
            how = "stripe" if money.paid_by_card(c, inv["id"]) else "account_credit"
            money.refund(c, cn, refundable, how, account_id=o["account_id"], staff_id=staff_id)
            refunded = refundable
    c.execute("UPDATE shop_orders SET status='cancelled', closed_at=?, updated_at=? WHERE id=?",
              (db.now(), db.now(), o["id"]))
    return refunded


@route("POST", "/api/staff/shop/orders/<ref>/status", auth="staff", perm="shop.manage")
def order_status(h, ref):
    d = h.json_body() or {}
    to = d.get("to")
    if to not in STATUSES:
        raise ValueError("Choose what's happened to the order.")
    with db.tx() as c:
        o = _order(c, ref)
        if o["status"] in ("cancelled", "collected", "posted") and to != o["status"]:
            raise ValueError("This order is already %s." % STATUS_WORDS[o["status"]].lower())
        refunded = 0
        if to == "cancelled":
            refunded = cancel_order(c, h, o, validate.text(d.get("reason"), 200) or "Order cancelled",
                                    staff_id=h.staff()["id"])
        else:
            c.execute("UPDATE shop_orders SET status=?, updated_at=?, closed_at=CASE WHEN ? IN ('collected','posted')"
                      " THEN ? END WHERE id=?", (to, db.now(), to, db.now(), o["id"]))
        if "staff_notes" in d:
            c.execute("UPDATE shop_orders SET staff_notes=? WHERE id=?", (validate.text(d["staff_notes"], 500) or None,
                                                                         o["id"]))
        if to == "ready" and d.get("notify", True):
            acct = c.execute("SELECT * FROM accounts WHERE id=?", (o["account_id"],)).fetchone()
            outbox.email(c, acct["email"], "shop_ready", {"first_name": acct["first_name"], "ref": o["ref"],
                                                          "collection": booking_settings.get("shop_collection_note", c)},
                         account_id=acct["id"])
        if to in ("collected", "posted", "cancelled"):
            intray.resolve(c, "shop_order", entity_type="shop_order", entity_id=o["id"], staff_id=h.staff()["id"])
        audit.record(c, h, "shop.order_" + to, entity_type="shop_order", entity_id=o["id"], account_id=o["account_id"],
                     details={"refunded": refunded})
        return h.json({"ok": True, "refunded_pence": refunded,
                       "order": order_json(c, c.execute("SELECT * FROM shop_orders WHERE id=?", (o["id"],)).fetchone(),
                                           staff=True)})


@worker.job("shop_unpaid", every=3600, timeout=120)
def cancel_unpaid():
    """Card orders nobody paid for within a day are cancelled, so their stock goes back on sale — but not while a
    card payment for one is under way (any that lands later is refunded)."""
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=UNPAID_CARD_HOURS)
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    with db.tx() as c:
        for o in c.execute("SELECT o.* FROM shop_orders o JOIN invoices i ON i.id=o.invoice_id WHERE o.pay_mode='card'"
                           " AND o.status='new' AND i.status='issued' AND i.paid_pence=0 AND o.created_at<? AND NOT"
                           " EXISTS (SELECT 1 FROM checkouts k WHERE k.invoice_id=i.id AND k.status IN"
                           " ('creating','awaiting_payment') AND k.expires_at>?)", (cutoff, db.now())).fetchall():
            cancel_order(c, None, o, "Card payment not completed")
            acct = c.execute("SELECT * FROM accounts WHERE id=?", (o["account_id"],)).fetchone()
            outbox.email(c, acct["email"], "shop_cancelled", {"first_name": acct["first_name"], "ref": o["ref"],
                                                              "shop_url": site_url(None) + "/shop"}, account_id=acct["id"])
            n += 1
    return "cancelled %d unpaid" % n if n else None
