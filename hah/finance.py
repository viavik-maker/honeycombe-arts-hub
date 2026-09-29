"""Admin → Finance: invoices and delayed payments, recording payments taken
offline (cash, card machine, bank transfer, vouchers, Tax-Free Childcare),
daily takings, refunds to hand back, and spreadsheet exports."""
import datetime

from . import audit, catalogue, db, money, outbox, payments_stripe, staff_bookings, validate
from .validate import Invalid
from .web import route, site_url


def _invoice(c, number):
    inv = c.execute("SELECT * FROM invoices WHERE number=?", (number,)).fetchone()
    if not inv:
        raise LookupError
    return inv


def _range(q, default_days=0):
    end = validate.date(q.get("to")) or catalogue.uk_today()
    start = validate.date(q.get("from")) or end - datetime.timedelta(days=default_days)
    return start, end


def _utc_bounds(start, end):
    """UK-local dates → UTC instants covering them (for *_at columns)."""
    a = datetime.datetime.combine(start, datetime.time()).replace(tzinfo=catalogue.UK)
    b = datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time()).replace(tzinfo=catalogue.UK)
    return catalogue.utc_iso(a), catalogue.utc_iso(b)


@route("GET", "/api/staff/finance/summary", auth="staff", perm="finance.view")
def summary(h):
    q = h.query()
    start, end = _range(q)
    lo, hi = _utc_bounds(start, end)
    today = catalogue.uk_today().isoformat()
    with db.read() as c:
        takings = [dict(method=money.METHODS[r["method"]], key=r["method"], count=r["n"], amount_pence=r["total"])
                   for r in c.execute("SELECT method, COUNT(*) AS n, SUM(amount_pence) AS total FROM payments WHERE"
                                      " status='succeeded' AND method<>'account_credit' AND received_at>=? AND received_at<?"
                                      " GROUP BY method ORDER BY total DESC", (lo, hi))]
        refunds = c.execute("SELECT COALESCE(SUM(amount_pence),0) FROM refunds WHERE status='succeeded' AND"
                            " method<>'account_credit' AND processed_at>=? AND processed_at<?", (lo, hi)).fetchone()[0]
        outstanding = c.execute("SELECT COUNT(*), COALESCE(SUM(total_pence-paid_pence-credited_pence),0) FROM invoices"
                                " WHERE status IN ('issued','part_paid')").fetchone()
        overdue = c.execute("SELECT COUNT(*), COALESCE(SUM(total_pence-paid_pence-credited_pence),0) FROM invoices"
                            " WHERE status IN ('issued','part_paid') AND due_date<?", (today,)).fetchone()
        pending_refunds = [dict(r) for r in c.execute(
            "SELECT r.id, r.amount_pence, r.method, r.status, r.failure_reason, r.created_at, cn.number AS credit_note"
            " FROM refunds r LEFT JOIN credit_notes cn ON cn.id=r.credit_note_id WHERE r.status='pending'"
            " OR (r.status='failed') ORDER BY r.id DESC LIMIT 50")]
        credit = c.execute("SELECT COALESCE(SUM(amount_pence),0) FROM refunds WHERE method='account_credit' AND"
                           " status='succeeded'").fetchone()[0] - c.execute(
            "SELECT COALESCE(SUM(amount_pence),0) FROM payments WHERE method='account_credit' AND status IN"
            " ('pending','succeeded')").fetchone()[0]
    return h.json({"from": start.isoformat(), "to": end.isoformat(), "takings": takings,
                   "takings_total_pence": sum(t["amount_pence"] for t in takings), "refunds_pence": refunds,
                   "outstanding": {"count": outstanding[0], "pence": outstanding[1]},
                   "overdue": {"count": overdue[0], "pence": overdue[1]}, "pending_refunds": pending_refunds,
                   "credit_held_pence": credit, "methods": money.METHODS, "voucher_providers": money.VOUCHER_PROVIDERS,
                   "card_payments": payments_stripe.configured()})


@route("GET", "/api/staff/finance/invoices", auth="staff", perm="finance.view")
def invoices(h):
    q = h.query()
    where, args = ["1=1"], []
    today = catalogue.uk_today().isoformat()
    view = q.get("view") or "unpaid"
    if view == "unpaid":
        where.append("status IN ('issued','part_paid')")
    elif view == "overdue":
        where.append("status IN ('issued','part_paid') AND due_date<?")
        args.append(today)
    elif view in ("paid", "void", "credited"):
        where.append("status=?")
        args.append(view)
    text = (q.get("q") or "").strip()
    if text:
        where.append("(number LIKE ? OR bill_to_name LIKE ? OR bill_to_email LIKE ?)")
        args += ["%" + text[:60] + "%"] * 3
    with db.read() as c:
        rows = c.execute("SELECT * FROM invoices WHERE " + " AND ".join(where) + " ORDER BY due_date, id LIMIT 300",
                         args).fetchall()
        out = []
        for inv in rows:
            j = money.invoice_json(c, inv, with_lines=False)
            acct = c.execute("SELECT ref, mobile, pay_later_allowed FROM accounts WHERE id=?", (inv["account_id"],)).fetchone() \
                if inv["account_id"] else None
            j.update(account_ref=acct["ref"] if acct else None, mobile=acct["mobile"] if acct else None,
                     pay_later_allowed=bool(acct["pay_later_allowed"]) if acct else False)
            out.append(j)
    return h.json({"invoices": out})


@route("POST", "/api/staff/payments", auth="staff", perm="payments.record")
def record_payment(h):
    """Record money taken for an invoice (not card-online: Stripe does those)."""
    d = h.json_body() or {}
    with db.tx() as c:
        inv = _invoice(c, d.get("invoice_number"))
        if inv["status"] == "void":
            raise ValueError("This invoice has been voided.")
        bal = money.balance(inv)
        if bal <= 0:
            raise ValueError("This invoice is already paid.")
        pay = staff_bookings.clean_payment(dict(d, mode="record"), bal)
        received = validate.date(d.get("received_on"))
        at = catalogue.utc_iso(datetime.datetime.combine(received, datetime.time(12)).replace(tzinfo=catalogue.UK)) \
            if received else None
        p = money.record_payment(c, amount=pay["amount"], method=pay["method"], account_id=inv["account_id"],
                                 guest_contact_id=inv["guest_contact_id"], reference=pay["reference"],
                                 voucher_provider=pay["voucher_provider"], notes=pay["notes"], staff_id=h.staff()["id"],
                                 received_at=at)
        money.allocate(c, p["id"], inv["id"], pay["amount"])
        audit.record(c, h, "payment.record", entity_type="invoice", entity_id=inv["id"], account_id=inv["account_id"],
                     details={"method": pay["method"], "amount": pay["amount"]})
        inv = c.execute("SELECT * FROM invoices WHERE id=?", (inv["id"],)).fetchone()
        if d.get("send_receipt", True) and inv["bill_to_email"]:
            outbox.email(c, inv["bill_to_email"], "payment_received",
                         {"name": inv["bill_to_name"], "number": inv["number"], "amount": money.pounds(pay["amount"]),
                          "balance": money.pounds(money.balance(inv))}, account_id=inv["account_id"])
        return h.json({"ok": True, "invoice": money.invoice_json(c, inv, with_lines=False)})


@route("POST", "/api/staff/invoices/<number>/void", auth="staff", perm="finance.manage")
def void(h, number):
    d = h.json_body() or {}
    reason = validate.text(d.get("reason"), 300)
    if not reason:
        raise Invalid({"reason": "Give a reason."})
    with db.tx() as c:
        inv = _invoice(c, number)
        if c.execute("SELECT 1 FROM payment_allocations WHERE invoice_id=?", (inv["id"],)).fetchone():
            raise ValueError("Money has been paid against this invoice — cancel the bookings with a credit note instead.")
        c.execute("UPDATE invoices SET status='void', voided_at=?, void_reason=? WHERE id=?", (db.now(), reason, inv["id"]))
        audit.record(c, h, "invoice.void", entity_type="invoice", entity_id=inv["id"], account_id=inv["account_id"])
    return h.json({"ok": True})


@route("POST", "/api/staff/invoices/<number>/remind", auth="staff", perm="payments.record")
def remind(h, number):
    """Email the family a link to view and pay the invoice."""
    with db.tx() as c:
        inv = _invoice(c, number)
        if money.balance(inv) <= 0 or not inv["bill_to_email"]:
            raise ValueError("Nothing to pay, or no email address.")
        outbox.email(c, inv["bill_to_email"], "invoice_reminder",
                     {"name": inv["bill_to_name"], "number": inv["number"], "when": "ready to pay",
                      "balance": money.pounds(money.balance(inv)), "due": inv["due_date"],
                      "invoice_url": site_url(h) + "/account/invoices/" + inv["number"]}, account_id=inv["account_id"])
        audit.record(c, h, "invoice.remind", entity_type="invoice", entity_id=inv["id"])
    return h.json({"ok": True})


@route("POST", "/api/staff/refunds/<rid>/done", auth="staff", perm="finance.manage")
def refund_done(h, rid):
    """An offline refund (cash, bank transfer) has been handed back."""
    with db.tx() as c:
        r = c.execute("SELECT * FROM refunds WHERE id=? AND status IN ('pending','failed') AND method<>'stripe'",
                      (int(rid),)).fetchone()
        if not r:
            raise LookupError
        c.execute("UPDATE refunds SET status='succeeded', processed_at=?, created_by_staff=COALESCE(created_by_staff, ?)"
                  " WHERE id=?", (db.now(), h.staff()["id"], r["id"]))
        audit.record(c, h, "refund.done", entity_type="refund", entity_id=r["id"])
    return h.json({"ok": True})


@route("POST", "/api/staff/accounts/<ref>/pay-later", auth="staff", perm="finance.manage")
def pay_later(h, ref):
    d = h.json_body() or {}
    with db.tx() as c:
        a = c.execute("SELECT id FROM accounts WHERE ref=?", (ref,)).fetchone()
        if not a:
            raise LookupError
        c.execute("UPDATE accounts SET pay_later_allowed=?, updated_at=? WHERE id=?",
                  (1 if d.get("allowed") else 0, db.now(), a["id"]))
        audit.record(c, h, "account.pay_later", entity_type="account", entity_id=a["id"], account_id=a["id"],
                     details={"allowed": bool(d.get("allowed"))})
    return h.json({"ok": True})


# ---------------------------------------------------------------- exports


@route("GET", "/api/staff/finance/invoices.csv", auth="staff", perm="finance.view")
def invoices_csv(h):
    start, end = _range(h.query(), 365)
    with db.tx() as c:
        rows = [[i["number"], i["issue_date"], i["due_date"], i["status"], i["bill_to_name"], i["bill_to_email"] or "",
                 i["total_pence"] / 100, i["paid_pence"] / 100, i["credited_pence"] / 100, money.balance(i) / 100]
                for i in c.execute("SELECT * FROM invoices WHERE issue_date BETWEEN ? AND ? ORDER BY id",
                                   (start.isoformat(), end.isoformat()))]
        audit.record(c, h, "finance.export_invoices", details={"rows": len(rows)})
    return h.csv("invoices-%s-to-%s.csv" % (start, end), ["Number", "Issued", "Due", "Status", "Bill to", "Email",
                                                          "Total (£)", "Paid (£)", "Credited (£)", "Balance (£)"], rows)


@route("GET", "/api/staff/finance/payments.csv", auth="staff", perm="finance.view")
def payments_csv(h):
    start, end = _range(h.query(), 31)
    lo, hi = _utc_bounds(start, end)
    with db.tx() as c:
        rows = []
        for p in c.execute("SELECT p.*, GROUP_CONCAT(i.number, ' ') AS invoices FROM payments p LEFT JOIN"
                           " payment_allocations a ON a.payment_id=p.id LEFT JOIN invoices i ON i.id=a.invoice_id"
                           " WHERE p.status='succeeded' AND p.received_at>=? AND p.received_at<? GROUP BY p.id"
                           " ORDER BY p.received_at", (lo, hi)):
            rows.append([p["ref"], catalogue.parse_utc(p["received_at"]).astimezone(catalogue.UK).strftime("%Y-%m-%d %H:%M"),
                         money.METHODS[p["method"]], p["voucher_provider"] or "", p["reference"] or "",
                         p["amount_pence"] / 100, p["invoices"] or ""])
        audit.record(c, h, "finance.export_payments", details={"rows": len(rows)})
    return h.csv("payments-%s-to-%s.csv" % (start, end), ["Ref", "Received", "Method", "Voucher provider", "Reference",
                                                          "Amount (£)", "Invoices"], rows)
