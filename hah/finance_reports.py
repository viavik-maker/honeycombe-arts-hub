"""Admin → Finance, for the treasurer: aged debt, the accounting export, and
matching Stripe payouts (what arrives in the bank) to the payments behind
them.

The accounting export is one CSV of everything that moved in a period:
invoice lines by category (with any discount), credit notes, payments by
method and refunds. It holds no children's names."""
import datetime
import re
import urllib.parse

from . import audit, catalogue, db, money, payments_stripe
from .web import route

BUCKETS = (("current", "Not due yet"), ("1_30", "1–30 days overdue"), ("31_60", "31–60 days"),
           ("61_90", "61–90 days"), ("over_90", "Over 90 days"))


def _bucket(days_over):
    if days_over <= 0:
        return "current"
    if days_over <= 30:
        return "1_30"
    if days_over <= 60:
        return "31_60"
    if days_over <= 90:
        return "61_90"
    return "over_90"


def aged_debt(c, today=None):
    today = today or catalogue.uk_today()
    people, totals = {}, {k: 0 for k, _ in BUCKETS}
    for inv in c.execute("SELECT i.*, a.ref AS account_ref, a.mobile FROM invoices i LEFT JOIN accounts a"
                         " ON a.id=i.account_id WHERE i.status IN ('issued','part_paid') ORDER BY i.due_date"):
        bal = money.balance(inv)
        if bal <= 0:
            continue
        over = (today - datetime.date.fromisoformat(inv["due_date"])).days
        b = _bucket(over)
        key = inv["account_ref"] or ("guest", inv["guest_contact_id"])
        row = people.setdefault(key, {"name": inv["bill_to_name"], "account_ref": inv["account_ref"],
                                      "email": inv["bill_to_email"], "mobile": inv["mobile"], "invoices": [],
                                      "total_pence": 0, "oldest_days": 0, **{k: 0 for k, _ in BUCKETS}})
        row["invoices"].append({"number": inv["number"], "due_date": inv["due_date"], "balance_pence": bal,
                                "days_overdue": max(over, 0)})
        row[b] += bal
        row["total_pence"] += bal
        row["oldest_days"] = max(row["oldest_days"], over)
        totals[b] += bal
    rows = sorted(people.values(), key=lambda r: (-r["oldest_days"], -r["total_pence"]))
    return {"buckets": [{"key": k, "label": l} for k, l in BUCKETS], "rows": rows,
            "totals": dict(totals, total=sum(totals.values()))}


@route("GET", "/api/staff/finance/aged-debt", auth="staff", perm="finance.view")
def aged_debt_api(h):
    with db.read() as c:
        return h.json(dict(aged_debt(c), as_of=catalogue.uk_today().isoformat()))


@route("GET", "/api/staff/finance/aged-debt.csv", auth="staff", perm="finance.view")
def aged_debt_csv(h):
    with db.tx() as c:
        d = aged_debt(c)
        audit.record(c, h, "finance.export_aged_debt", details={"rows": len(d["rows"])})
    rows = [[r["name"], r["email"] or "", r["mobile"] or "", " ".join(i["number"] for i in r["invoices"])]
            + [r[k] / 100 for k, _ in BUCKETS] + [r["total_pence"] / 100] for r in d["rows"]]
    rows.append(["Total", "", "", ""] + [d["totals"][k] / 100 for k, _ in BUCKETS] + [d["totals"]["total"] / 100])
    return h.csv("aged-debt-%s.csv" % catalogue.uk_today(), ["Bill to", "Email", "Mobile", "Invoices"]
                 + ["%s (£)" % l for _, l in BUCKETS] + ["Total (£)"], rows)


# ---------------------------------------------------------------- accounting export


def _local_date(utc):
    return catalogue.parse_utc(utc).astimezone(catalogue.UK).date().isoformat() if utc else ""


def accounting_rows(c, start, end):
    from .finance import _utc_bounds
    lo, hi = _utc_bounds(start, end)
    s, e = start.isoformat(), end.isoformat()
    out = []
    for r in c.execute(
            "SELECT i.issue_date, i.number, i.bill_to_name, l.description, l.unit_pence, l.amount_pence,"
            " cat.name AS category FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id"
            " LEFT JOIN bookings b ON b.id=l.booking_id LEFT JOIN activities a ON a.id=b.activity_id"
            " LEFT JOIN activity_categories cat ON cat.id=a.category_id WHERE i.issue_date BETWEEN ? AND ?"
            " AND l.amount_pence<>0 ORDER BY i.id, l.id", (s, e)):
        out.append([r["issue_date"], "Invoice", r["number"], r["bill_to_name"], r["description"], r["category"] or "",
                    "", (r["unit_pence"] - r["amount_pence"]) / 100, r["amount_pence"] / 100, r["number"]])
    for r in c.execute("SELECT number, bill_to_name, total_pence, voided_at, void_reason FROM invoices"
                       " WHERE voided_at>=? AND voided_at<? ORDER BY id", (lo, hi)):
        out.append([_local_date(r["voided_at"]), "Invoice voided", r["number"], r["bill_to_name"],
                    r["void_reason"] or "Voided", "", "", 0, -r["total_pence"] / 100, r["number"]])
    for r in c.execute(
            "SELECT cn.number, cn.created_at, i.number AS invoice, i.bill_to_name, l.description, l.amount_pence,"
            " cat.name AS category FROM credit_note_lines l JOIN credit_notes cn ON cn.id=l.credit_note_id"
            " JOIN invoices i ON i.id=cn.invoice_id LEFT JOIN bookings b ON b.id=l.booking_id"
            " LEFT JOIN activities a ON a.id=b.activity_id LEFT JOIN activity_categories cat ON cat.id=a.category_id"
            " WHERE cn.created_at>=? AND cn.created_at<? ORDER BY cn.id, l.id", (lo, hi)):
        out.append([_local_date(r["created_at"]), "Credit note", r["number"], r["bill_to_name"], r["description"],
                    r["category"] or "", "", 0, -r["amount_pence"] / 100, r["invoice"]])
    for r in c.execute(
            "SELECT p.*, GROUP_CONCAT(i.number, ' ') AS invoices, MIN(i.bill_to_name) AS name FROM payments p"
            " LEFT JOIN payment_allocations pa ON pa.payment_id=p.id LEFT JOIN invoices i ON i.id=pa.invoice_id"
            " WHERE p.status='succeeded' AND p.method<>'account_credit' AND p.received_at>=? AND p.received_at<?"
            " GROUP BY p.id ORDER BY p.received_at", (lo, hi)):
        out.append([_local_date(r["received_at"]), "Payment", r["ref"], r["name"] or "", r["reference"] or "", "",
                    money.METHODS[r["method"]], 0, r["amount_pence"] / 100, r["invoices"] or ""])
    for r in c.execute(
            "SELECT r.*, cn.number AS credit_note, i.number AS invoice, i.bill_to_name FROM refunds r"
            " LEFT JOIN credit_notes cn ON cn.id=r.credit_note_id LEFT JOIN invoices i ON i.id=cn.invoice_id"
            " WHERE r.status='succeeded' AND r.method<>'account_credit' AND r.processed_at>=? AND r.processed_at<?"
            " ORDER BY r.processed_at", (lo, hi)):
        out.append([_local_date(r["processed_at"]), "Refund", r["credit_note"] or "", r["bill_to_name"] or "",
                    "Refund", "", {"stripe": "Card (Stripe)"}.get(r["method"], r["method"].replace("_", " ").capitalize()),
                    0, -r["amount_pence"] / 100, r["invoice"] or ""])
    return sorted(out, key=lambda x: (x[0], x[1]))


@route("GET", "/api/staff/finance/accounting.csv", auth="staff", perm="finance.view")
def accounting_csv(h):
    from .finance import _range
    start, end = _range(h.query(), 31)
    with db.tx() as c:
        rows = accounting_rows(c, start, end)
        audit.record(c, h, "finance.export_accounting", details={"from": start.isoformat(), "to": end.isoformat(),
                                                                  "rows": len(rows)})
    return h.csv("accounting-%s-to-%s.csv" % (start, end),
                 ["Date", "Type", "Number", "Contact", "Description", "Category", "Method", "Discount (£)",
                  "Amount (£)", "Invoice"], rows)


# ---------------------------------------------------------------- Stripe payouts

PAYOUT_ID = re.compile(r"^po_[A-Za-z0-9_]{3,64}$")
MAX_PAGES = 10


def _when(ts):
    return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).astimezone(catalogue.UK).date().isoformat() \
        if ts else None


@route("GET", "/api/staff/finance/stripe/payouts", auth="staff", perm="finance.view")
def payouts(h):
    if not payments_stripe.configured():
        return h.json({"configured": False, "payouts": []})
    try:
        d = payments_stripe.call("GET", "/v1/payouts?limit=20")
    except payments_stripe.StripeError as e:
        return h.json({"error": str(e)}, 502)
    return h.json({"configured": True, "payouts": [
        {"id": p["id"], "amount_pence": p["amount"], "currency": p.get("currency"), "status": p.get("status"),
         "arrival_date": _when(p.get("arrival_date")), "created": _when(p.get("created"))} for p in d.get("data", [])]})


def reconcile(c, payout_id):
    """The balance transactions in a payout, each matched to our payment or refund."""
    txns, after = [], None
    for _ in range(MAX_PAGES):
        q = {"payout": payout_id, "limit": 100, "expand[]": "data.source"}
        if after:
            q["starting_after"] = after
        d = payments_stripe.call("GET", "/v1/balance_transactions?" + urllib.parse.urlencode(q))
        txns += d.get("data", [])
        if not d.get("has_more") or not d.get("data"):
            break
        after = d["data"][-1]["id"]
    rows, totals = [], {"gross": 0, "fees": 0, "net": 0, "unmatched": 0}
    for t in txns:
        if t.get("type") == "payout":
            continue  # the payout itself
        src = t.get("source") if isinstance(t.get("source"), dict) else {"id": t.get("source")}
        pi = src.get("payment_intent")
        ours = None
        if t.get("type") in ("charge", "payment") and pi:
            p = c.execute("SELECT p.ref, p.status, GROUP_CONCAT(i.number, ' ') AS invoices, MIN(i.bill_to_name) AS name"
                          " FROM payments p LEFT JOIN payment_allocations pa ON pa.payment_id=p.id LEFT JOIN invoices i"
                          " ON i.id=pa.invoice_id WHERE p.stripe_payment_intent_id=? GROUP BY p.id", (pi,)).fetchone()
            if p:
                ours = {"kind": "payment", "ref": p["ref"], "status": p["status"], "invoices": p["invoices"] or "",
                        "name": p["name"] or ""}
        elif t.get("type") in ("refund", "payment_refund") and src.get("id"):
            r = c.execute("SELECT r.status, cn.number, i.number AS invoice, i.bill_to_name FROM refunds r"
                          " LEFT JOIN credit_notes cn ON cn.id=r.credit_note_id LEFT JOIN invoices i ON i.id=cn.invoice_id"
                          " WHERE r.stripe_refund_id=?", (src["id"],)).fetchone()
            if r:
                ours = {"kind": "refund", "ref": r["number"] or "", "status": r["status"], "invoices": r["invoice"] or "",
                        "name": r["bill_to_name"] or ""}
        rows.append({"id": t.get("id"), "type": t.get("type"), "date": _when(t.get("created")),
                     "description": t.get("description") or "", "amount_pence": t.get("amount", 0),
                     "fee_pence": t.get("fee", 0), "net_pence": t.get("net", 0), "ours": ours,
                     "matched": bool(ours) or t.get("type") not in ("charge", "payment", "refund", "payment_refund")})
        totals["gross"] += t.get("amount", 0)
        totals["fees"] += t.get("fee", 0)
        totals["net"] += t.get("net", 0)
        if not rows[-1]["matched"]:
            totals["unmatched"] += 1
    return rows, totals


@route("GET", "/api/staff/finance/stripe/payouts/<pid>", auth="staff", perm="finance.view")
def payout_detail(h, pid):
    if not PAYOUT_ID.match(pid):
        raise LookupError
    if not payments_stripe.configured():
        raise ValueError("Card payments aren't set up.")
    try:
        p = payments_stripe.call("GET", "/v1/payouts/" + pid)
        with db.read() as c:
            rows, totals = reconcile(c, pid)
    except payments_stripe.StripeError as e:
        return h.json({"error": str(e)}, 502)
    if h.query().get("format") == "csv":
        with db.tx() as c:
            audit.record(c, h, "finance.export_payout", details={"payout": pid, "rows": len(rows)})
        return h.csv("stripe-payout-%s.csv" % pid, ["Date", "Type", "Stripe id", "Our ref", "Bill to", "Invoices",
                                                    "Gross (£)", "Fee (£)", "Net (£)", "Matched"],
                     [[r["date"], r["type"], r["id"], (r["ours"] or {}).get("ref", ""), (r["ours"] or {}).get("name", ""),
                       (r["ours"] or {}).get("invoices", ""), r["amount_pence"] / 100, r["fee_pence"] / 100,
                       r["net_pence"] / 100, "yes" if r["matched"] else "NO"] for r in rows])
    return h.json({"payout": {"id": p["id"], "amount_pence": p["amount"], "status": p.get("status"),
                              "arrival_date": _when(p.get("arrival_date"))},
                   "rows": rows, "totals": totals, "adds_up": totals["net"] == p["amount"]})
