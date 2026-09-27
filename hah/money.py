"""Invoices, payments, credit notes, refunds and account credit.

All functions run inside the caller's db.tx(), so an invoice and the
booking it bills are written together or not at all, and invoice numbers
are gap-free.

  invoice balance = total - paid - credited   (paid is net of refunds)
  account credit  = refunds into credit - credit spent (pending or succeeded)
"""
import datetime

from . import booking_settings, catalogue, db, family, worker

METHODS = {
    "stripe_card": "Card (online)", "cash": "Cash", "card_terminal": "Card machine", "bank_transfer": "Bank transfer",
    "childcare_voucher": "Childcare vouchers", "tax_free_childcare": "Tax-Free Childcare",
    "account_credit": "Account credit", "other": "Other",
}
VOUCHER_PROVIDERS = ("Edenred", "Computershare", "Kiddivouchers", "Sodexo / Pluxee", "Care 4", "Other")


def pounds(pence):
    return ("-" if pence < 0 else "") + "£%.2f" % (abs(pence) / 100)


def next_number(c, kind):
    """HAH-2026-00042 / CN-2026-0007 — allocated inside the transaction, so
    numbers are never skipped (a rolled-back booking returns its number)."""
    s = booking_settings.get_all(c)
    year = catalogue.uk_today().year
    prefix = s["invoice_prefix"] if kind == "invoice" else s["credit_note_prefix"]
    name = "%s-%d" % (kind, year)
    c.execute("INSERT INTO counters(name, value) VALUES (?, 1) ON CONFLICT(name) DO UPDATE SET value=value+1", (name,))
    n = c.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()[0]
    return "%s-%d-%s" % (prefix, year, str(n).zfill(5 if kind == "invoice" else 4))


# ---------------------------------------------------------------- account credit


def credit_balance(c, account_id):
    if not account_id:
        return 0
    into = c.execute("SELECT COALESCE(SUM(amount_pence),0) FROM refunds WHERE account_id=? AND method='account_credit'"
                     " AND status='succeeded'", (account_id,)).fetchone()[0]
    spent = c.execute("SELECT COALESCE(SUM(amount_pence),0) FROM payments WHERE account_id=? AND method='account_credit'"
                      " AND status IN ('pending','succeeded')", (account_id,)).fetchone()[0]
    return into - spent


# ---------------------------------------------------------------- invoices


def _bill_to(c, account_id=None, guest_contact_id=None):
    if account_id:
        a = c.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        addr = ", ".join(x for x in (a["address_line1"], a["address_line2"], a["town"], a["postcode"]) if x)
        return "%s %s" % (a["first_name"], a["last_name"]), a["email"], addr or None
    g = c.execute("SELECT * FROM guest_contacts WHERE id=?", (guest_contact_id,)).fetchone()
    return g["name"] or g["email"], g["email"], None


def due_date(issue, first_session=None, terms=None, c=None):
    """issue + payment terms, but 3 days before the first session if that's
    sooner — and never before the issue date."""
    terms = booking_settings.get("payment_terms_days", c) if terms is None else terms
    due = issue + datetime.timedelta(days=terms)
    if first_session:
        due = min(due, datetime.date.fromisoformat(first_session) - datetime.timedelta(days=3))
    return max(due, issue)


def line_for(c, booking):
    """(description, service_date, participant_name, amount, funding_note) for a booking."""
    s = c.execute("SELECT s.*, a.title FROM activity_sessions s JOIN activities a ON a.id=s.activity_id WHERE s.id=?",
                  (booking["session_id"],)).fetchone()
    name = None
    if booking["participant_id"]:
        p = c.execute("SELECT first_name, last_name FROM participants WHERE id=?", (booking["participant_id"],)).fetchone()
        name = "%s %s" % (p["first_name"], p["last_name"])
    elif booking["kind"] == "party":
        name = booking["party_name"] or "%d place%s" % (booking["places"], "" if booking["places"] == 1 else "s")
    day = datetime.date.fromisoformat(s["date"]).strftime("%a %-d %b %Y")
    desc = "%s · %s %s–%s" % (s["title"], day, s["start_time"], s["end_time"])
    note = {"haf": "HAF funded", "free": "Free", "staff_comp": "Complimentary",
            "prepaid_legacy": "Paid via previous booking system"}.get(booking["funding"])
    if booking["discount_pence"] and booking["price_pence"]:
        note = "%s: −%s" % (booking["discount_reason"] or "Discount", pounds(booking["discount_pence"]))
    elif booking["is_trial"] and booking["price_pence"] is not None and booking["funding"] == "paid":
        note = "Trial session"
    return desc, s["date"], name, booking["price_pence"], note


def create_invoice(c, bookings, *, account_id=None, guest_contact_id=None, checkout_id=None, staff_id=None,
                   notes=None):
    """One invoice for BOOKINGS (rows). £0 lines (HAF, free) are included so a
    mixed basket reads as one document. Returns the invoice row."""
    if not bookings:
        return None
    name, email, addr = _bill_to(c, account_id, guest_contact_id)
    issue = catalogue.uk_today()
    lines = [line_for(c, b) for b in bookings]
    first = min(l[1] for l in lines)
    total = sum(l[3] for l in lines)
    number = next_number(c, "invoice")
    iid = c.execute(
        "INSERT INTO invoices(number, account_id, guest_contact_id, checkout_id, bill_to_name, bill_to_email,"
        " bill_to_address, issue_date, due_date, status, total_pence, notes, created_by_staff, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (number, account_id, guest_contact_id, checkout_id, name, email, addr, issue.isoformat(),
         due_date(issue, first, c=c).isoformat(), "issued" if total else "paid", total, notes, staff_id,
         db.now())).lastrowid
    for b, (desc, day, who, amount, note) in zip(bookings, lines):
        unit = amount + (b["discount_pence"] if amount else 0)  # the price before any discount
        c.execute("INSERT INTO invoice_lines(invoice_id, booking_id, description, service_date, participant_name,"
                  " unit_pence, amount_pence, funding_note) VALUES (?,?,?,?,?,?,?,?)",
                  (iid, b["id"], desc, day, who, unit, amount, note))
    return c.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()


def refresh_status(c, invoice_id):
    inv = c.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if inv["status"] == "void":
        return inv
    bal = inv["total_pence"] - inv["paid_pence"] - inv["credited_pence"]
    if inv["credited_pence"] >= inv["total_pence"] and inv["total_pence"] > 0:
        status = "credited"
    elif bal <= 0:
        status = "paid"
    elif inv["paid_pence"] > 0:
        status = "part_paid"
    else:
        status = "issued"
    c.execute("UPDATE invoices SET status=? WHERE id=?", (status, invoice_id))
    return c.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()


def balance(inv):
    return inv["total_pence"] - inv["paid_pence"] - inv["credited_pence"]


def invoice_for_booking(c, booking_id):
    return c.execute("SELECT i.* FROM invoices i JOIN invoice_lines l ON l.invoice_id=i.id WHERE l.booking_id=?"
                     " AND i.status<>'void' ORDER BY i.id DESC LIMIT 1", (booking_id,)).fetchone()


# ---------------------------------------------------------------- payments


def record_payment(c, *, amount, method, account_id=None, guest_contact_id=None, status="succeeded", reference=None,
                   voucher_provider=None, staff_id=None, notes=None, stripe_payment_intent_id=None,
                   stripe_checkout_session_id=None, received_at=None):
    if amount <= 0:
        raise ValueError("A payment must be more than £0.")
    if method not in METHODS:
        raise ValueError("Unknown payment method.")
    pid = c.execute(
        "INSERT INTO payments(ref, account_id, guest_contact_id, amount_pence, method, voucher_provider, reference,"
        " status, stripe_payment_intent_id, stripe_checkout_session_id, received_at, recorded_by_staff, notes,"
        " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (family.new_ref("P"), account_id, guest_contact_id, amount, method, voucher_provider, reference, status,
         stripe_payment_intent_id, stripe_checkout_session_id, received_at or db.now(), staff_id, notes,
         db.now())).lastrowid
    return c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()


def allocate(c, payment_id, invoice_id, amount):
    """Put AMOUNT of a payment against an invoice (capped at its balance;
    nothing against a void one). Returns what was allocated — the caller
    deals with any remainder."""
    inv = c.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    amount = min(amount, balance(inv))
    if amount <= 0 or inv["status"] == "void":
        return 0
    c.execute("INSERT INTO payment_allocations(payment_id, invoice_id, amount_pence) VALUES (?,?,?)"
              " ON CONFLICT(payment_id, invoice_id) DO UPDATE SET amount_pence=amount_pence+excluded.amount_pence",
              (payment_id, invoice_id, amount))
    c.execute("UPDATE invoices SET paid_pence=paid_pence+? WHERE id=?", (amount, invoice_id))
    refresh_status(c, invoice_id)
    return amount


def pay_with_credit(c, account_id, invoice_id, max_amount=None):
    """Use the family's account credit against an invoice. Returns pence used."""
    inv = c.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    use = min(credit_balance(c, account_id), balance(inv), max_amount if max_amount is not None else 10 ** 9)
    if use <= 0:
        return 0
    p = record_payment(c, amount=use, method="account_credit", account_id=account_id)
    allocate(c, p["id"], invoice_id, use)
    return use


# ---------------------------------------------------------------- credit notes and refunds


def credit_note(c, invoice, bookings, reason, *, staff_id=None, account_id=None, amount=None, invoice_lines=None):
    """Credit the invoice lines for BOOKINGS, or the given INVOICE_LINES (shop orders), or AMOUNT if given and
    smaller. Unpaid money is simply written off the invoice; anything already paid
    is returned as `refundable` for the caller to refund or turn into
    account credit. Returns (credit_note_row or None, refundable_pence)."""
    lines = list(invoice_lines) if invoice_lines is not None else [
        c.execute("SELECT * FROM invoice_lines WHERE invoice_id=? AND booking_id=?", (invoice["id"], b["id"]))
        .fetchone() for b in bookings]
    lines = [l for l in lines if l and l["amount_pence"] > 0]
    already = {r[0]: r[1] for r in c.execute(
        "SELECT invoice_line_id, SUM(amount_pence) FROM credit_note_lines WHERE invoice_line_id IN (%s)"
        " GROUP BY invoice_line_id" % ",".join("?" * len(lines)), [l["id"] for l in lines])} if lines else {}
    parts = [(l, l["amount_pence"] - already.get(l["id"], 0)) for l in lines]
    parts = [(l, a) for l, a in parts if a > 0]
    total = sum(a for _, a in parts)
    if amount is not None:
        total = min(total, max(int(amount), 0))
    if total <= 0:
        return None, 0
    unpaid = max(balance(invoice), 0)
    number = next_number(c, "credit_note")
    cn = c.execute("INSERT INTO credit_notes(number, invoice_id, account_id, reason, total_pence, created_by_staff,"
                   " created_by_account, created_at) VALUES (?,?,?,?,?,?,?,?)",
                   (number, invoice["id"], invoice["account_id"], reason[:300], total, staff_id, account_id,
                    db.now())).lastrowid
    left = total
    for l, a in parts:
        a = min(a, left)
        if a <= 0:
            break
        c.execute("INSERT INTO credit_note_lines(credit_note_id, booking_id, invoice_line_id, description, amount_pence)"
                  " VALUES (?,?,?,?,?)", (cn, l["booking_id"], l["id"], l["description"], a))
        left -= a
    c.execute("UPDATE invoices SET credited_pence=credited_pence+? WHERE id=?", (total, invoice["id"]))
    # the part of the credit that the unpaid balance can't absorb was paid: give it back
    refundable = max(total - unpaid, 0)
    if refundable:
        c.execute("UPDATE invoices SET paid_pence=paid_pence-? WHERE id=?", (refundable, invoice["id"]))
    refresh_status(c, invoice["id"])
    return c.execute("SELECT * FROM credit_notes WHERE id=?", (cn,)).fetchone(), refundable


def refund(c, credit_note_row, amount, how, *, account_id=None, staff_id=None, handed_back=False):
    """Give AMOUNT back. HOW: 'account_credit' (immediate), 'stripe' (queued;
    the worker calls Stripe outside the transaction), 'cash' or
    'bank_transfer' (done if HANDED_BACK — staff say they've given it back —
    otherwise pending, for Finance to hand back and mark done). Card refunds go
    against the invoice's card payments, newest first; anything left becomes
    credit (or, with no account, a pending refund to hand back)."""
    if amount <= 0:
        return []
    out = []
    if how == "stripe":
        pays = c.execute("SELECT p.* FROM payments p JOIN payment_allocations a ON a.payment_id=p.id"
                         " WHERE a.invoice_id=? AND p.method='stripe_card' AND p.status='succeeded'"
                         " ORDER BY p.id DESC", (credit_note_row["invoice_id"],)).fetchall()
        for p in pays:
            done = c.execute("SELECT COALESCE(SUM(amount_pence),0) FROM refunds WHERE payment_id=? AND status<>'failed'",
                             (p["id"],)).fetchone()[0]
            give = min(amount, p["amount_pence"] - done)
            if give <= 0:
                continue
            out.append(c.execute("INSERT INTO refunds(credit_note_id, payment_id, account_id, amount_pence, method,"
                                 " status, created_by_staff, created_at) VALUES (?,?,?,?, 'stripe', 'pending', ?,?)",
                                 (credit_note_row["id"], p["id"], account_id, give, staff_id, db.now())).lastrowid)
            amount -= give
            if amount <= 0:
                return out
        how, handed_back = ("account_credit" if account_id else "bank_transfer"), False
    status = "succeeded" if how == "account_credit" or handed_back else "pending"
    out.append(c.execute("INSERT INTO refunds(credit_note_id, account_id, amount_pence, method, status, created_by_staff,"
                         " created_at, processed_at) VALUES (?,?,?,?,?,?,?,?)",
                         (credit_note_row["id"], account_id, amount, how, status, staff_id, db.now(),
                          db.now() if status == "succeeded" else None)).lastrowid)
    return out


def paid_by_card(c, invoice_id):
    return c.execute("SELECT 1 FROM payment_allocations a JOIN payments p ON p.id=a.payment_id WHERE a.invoice_id=?"
                     " AND p.method='stripe_card' AND p.status='succeeded'", (invoice_id,)).fetchone() is not None


def invoice_json(c, inv, with_lines=True):
    out = {k: inv[k] for k in ("number", "issue_date", "due_date", "status", "total_pence", "paid_pence",
                               "credited_pence", "bill_to_name", "bill_to_email", "bill_to_address", "notes")}
    out["balance_pence"] = balance(inv)
    out["overdue"] = out["balance_pence"] > 0 and inv["due_date"] < catalogue.uk_today().isoformat()
    if with_lines:
        out["lines"] = [dict(description=l["description"], service_date=l["service_date"],
                             participant_name=l["participant_name"], amount_pence=l["amount_pence"],
                             funding_note=l["funding_note"])
                        for l in c.execute("SELECT * FROM invoice_lines WHERE invoice_id=? ORDER BY service_date, id",
                                           (inv["id"],))]
        out["credit_notes"] = [dict(number=r["number"], reason=r["reason"], total_pence=r["total_pence"],
                                    created_at=r["created_at"])
                               for r in c.execute("SELECT * FROM credit_notes WHERE invoice_id=? ORDER BY id",
                                                  (inv["id"],))]
        out["payments"] = [dict(method=METHODS.get(r["method"], r["method"]), amount_pence=r["amount_pence"],
                                received_at=r["received_at"])
                           for r in c.execute("SELECT p.method, a.amount_pence, p.received_at FROM payment_allocations a"
                                              " JOIN payments p ON p.id=a.payment_id WHERE a.invoice_id=? ORDER BY p.id",
                                              (inv["id"],))]
    return out


# ---------------------------------------------------------------- reminders

REMINDER_OFFSETS = {-3: "due in 3 days", 0: "due today", 7: "now overdue"}


@worker.job("invoice_reminders", daily_at="09:05", timeout=300)
def reminders_job():
    """Unpaid invoices: a reminder 3 days before the due date, on it, and a
    week after. Unpaid invoices are never cancelled automatically — they
    show under Finance → Delayed payments for staff to follow up."""
    from . import outbox
    from .web import site_url
    today = catalogue.uk_today()
    sent = 0
    with db.tx() as c:
        for offset, words in REMINDER_OFFSETS.items():
            due = (today - datetime.timedelta(days=offset)).isoformat()
            for inv in c.execute("SELECT * FROM invoices WHERE status IN ('issued','part_paid') AND due_date=?",
                                 (due,)).fetchall():
                key = "invoice-reminder-%s-%d" % (inv["number"], offset)
                if c.execute("SELECT 1 FROM counters WHERE name=?", (key,)).fetchone():
                    continue  # already sent this one
                c.execute("INSERT INTO counters(name, value) VALUES (?, 1)", (key,))
                if inv["bill_to_email"]:
                    outbox.email(c, inv["bill_to_email"], "invoice_reminder",
                                 {"name": inv["bill_to_name"], "number": inv["number"], "when": words,
                                  "balance": pounds(balance(inv)), "due": inv["due_date"],
                                  "invoice_url": site_url() + "/account/invoices/" + inv["number"]},
                                 account_id=inv["account_id"])
                    sent += 1
    return "sent %d reminder(s)" % sent
