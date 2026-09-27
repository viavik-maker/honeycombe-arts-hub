"""Card payments that land when they're no longer wanted, double payments, credit, refunds and disputes."""
import uuid

from hah import db, money, payments_stripe, ratelimit
from tests.booking_helpers import FakeStripe, booking, make_activity, set_settings, webhook
from tests.family_helpers import complete_child, last_email_to, ok, register_family
from tests.support import Client, ServerTestCase


def key():
    return uuid.uuid4().hex


def account_id(fam):
    with db.read() as c:
        return c.execute("SELECT id FROM accounts WHERE email=?", (fam.email,)).fetchone()[0]


def give_credit(aid, pence):
    with db.tx() as c:
        c.execute("INSERT INTO refunds(account_id, amount_pence, method, status, created_at, processed_at)"
                  " VALUES (?,?, 'account_credit', 'succeeded', ?, ?)", (aid, pence, db.now(), db.now()))


def credit(aid):
    with db.read() as c:
        return money.credit_balance(c, aid)


def card_refunds(payment_intent):
    """[(method, amount, status)] of refunds against the card payment with PAYMENT_INTENT."""
    with db.read() as c:
        return [tuple(r) for r in c.execute(
            "SELECT r.method, r.amount_pence, r.status FROM refunds r JOIN payments p ON p.id=r.payment_id"
            " WHERE p.stripe_payment_intent_id=? ORDER BY r.id", (payment_intent,))]


def open_items(type_, entity_type, entity_id):
    with db.read() as c:
        return c.execute("SELECT * FROM intray_items WHERE type=? AND entity_type=? AND entity_id=? AND status='open'",
                         (type_, entity_type, entity_id)).fetchall()


def checkout_id(ref):
    with db.read() as c:
        return c.execute("SELECT id FROM checkouts WHERE ref=?", (ref,)).fetchone()[0]


class LateCardPaymentTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()
        set_settings(booking_live=True, pay_later_for_all=True, sibling_discount_percent=0,
                     multi_day_discount_percent=0)

    def card_booking(self, fam, child, sid):
        return ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": child}],
                                                      "pay_mode": "card", "accept_terms": True,
                                                      "idempotency_key": key()})).json()

    def pay_later(self, fam, child, sid):
        return ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": child}],
                                                      "pay_mode": "pay_later", "accept_terms": True,
                                                      "idempotency_key": key()})).json()

    def test_payment_after_session_cancelled_is_refunded(self):
        with FakeStripe() as stripe:
            aid, (sid,) = make_activity(sessions=1, price=1500, first_day=30)
            fam = register_family()
            r = self.card_booking(fam, complete_child(fam), sid)
            sess = stripe.last_session()
            ok(self.admin().post_json("/api/staff/sessions/%d/cancel" % sid, {"reason": "Flood"}))
            self.assertEqual(ok(webhook(fam, "checkout.session.completed", stripe.pay(sess["id"]))).json()["result"],
                             "completed")
            b = booking(r["bookings"][0]["ref"])
            self.assertEqual(b["status"], "cancelled")
            with db.read() as c:
                self.assertIsNone(money.invoice_for_booking(c, b["id"]))
            self.assertEqual(card_refunds(sess["payment_intent"]), [("stripe", 1500, "pending")])
            self.assertTrue(open_items("late_payment", "checkout", checkout_id(r["checkout"])))
            payments_stripe.send_refunds()
            self.assertEqual(card_refunds(sess["payment_intent"]), [("stripe", 1500, "succeeded")])

    def test_payment_after_staff_cancel_of_held_place_is_refunded(self):
        with FakeStripe() as stripe:
            aid, (sid,) = make_activity(sessions=1, price=1500, first_day=30)
            fam = register_family()
            r = self.card_booking(fam, complete_child(fam), sid)
            sess = stripe.last_session()
            bid = booking(r["bookings"][0]["ref"])["id"]
            ok(self.admin().post_json("/api/staff/bookings/%d/cancel" % bid, {"reason": "Duplicate", "outcome": "credit"}))
            ok(webhook(fam, "checkout.session.completed", stripe.pay(sess["id"])))
            self.assertEqual(booking(r["bookings"][0]["ref"])["status"], "cancelled")
            self.assertEqual(card_refunds(sess["payment_intent"]), [("stripe", 1500, "pending")])
            self.assertEqual(credit(account_id(fam)), 0)

    def test_approval_while_card_payment_open_is_invoiced_once(self):
        with FakeStripe() as stripe:
            a1, (s1,) = make_activity(sessions=1, price=1000, first_day=30, requires_approval=1)
            a2, (s2,) = make_activity(sessions=1, price=2000, first_day=31)
            fam = register_family()
            child = complete_child(fam)
            d = ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": s1, "participant": child},
                                                                 {"session_id": s2, "participant": child}],
                                                       "pay_mode": "card", "accept_terms": True,
                                                       "idempotency_key": key()})).json()
            sess = stripe.last_session()
            self.assertEqual(sess["amount_total"], 2000)
            appr = booking([b["ref"] for b in d["bookings"] if b["status"] == "pending_approval"][0])
            ok(self.admin().post_json("/api/staff/bookings/%d/approve" % appr["id"], {}))
            ok(webhook(fam, "checkout.session.completed", stripe.pay(sess["id"])))
            with db.read() as c:
                lines = c.execute("SELECT l.invoice_id FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id"
                                  " WHERE l.booking_id=? AND i.status<>'void'", (appr["id"],)).fetchall()
                self.assertEqual(len(lines), 1)
                paid = money.invoice_for_booking(c, booking([b["ref"] for b in d["bookings"]
                                                             if b["ref"] != appr["ref"]][0])["id"])
                self.assertEqual((paid["total_pence"], money.balance(paid)), (2000, 0))
            self.assertEqual(card_refunds(sess["payment_intent"]), [])

    def test_late_payment_does_not_revive_spent_credit(self):
        with FakeStripe() as stripe:
            aid, sids = make_activity(sessions=2, price=1500, first_day=30, allow_pay_later=1)
            fam = register_family()
            child = complete_child(fam)
            aid_ = account_id(fam)
            give_credit(aid_, 1000)
            r = self.card_booking(fam, child, sids[0])
            sess = stripe.last_session()
            self.assertEqual(sess["amount_total"], 500)
            ok(webhook(fam, "checkout.session.expired", dict(sess, status="expired")))
            self.assertEqual(credit(aid_), 1000)  # the hold lapsed: the credit is theirs again
            self.pay_later(fam, child, sids[1])  # …and they spend it
            self.assertEqual(credit(aid_), 0)
            ok(webhook(fam, "checkout.session.completed", dict(stripe.pay(sess["id"]), status="complete")))
            self.assertEqual(booking(r["bookings"][0]["ref"])["status"], "expired")  # £5 doesn't pay for £15
            self.assertEqual(credit(aid_), 0)
            self.assertEqual(card_refunds(sess["payment_intent"]), [("stripe", 500, "pending")])
            self.assertIn("£5.00 to your card", last_email_to(fam.email))

    def test_late_payment_uses_credit_still_there(self):
        with FakeStripe() as stripe:
            aid, (sid,) = make_activity(sessions=1, price=1500, first_day=30)
            fam = register_family()
            aid_ = account_id(fam)
            give_credit(aid_, 1000)
            r = self.card_booking(fam, complete_child(fam), sid)
            sess = stripe.last_session()
            ok(webhook(fam, "checkout.session.expired", dict(sess, status="expired")))
            ok(webhook(fam, "checkout.session.completed", dict(stripe.pay(sess["id"]), status="complete")))
            b = booking(r["bookings"][0]["ref"])
            self.assertEqual(b["status"], "confirmed")
            self.assertEqual(credit(aid_), 0)
            with db.read() as c:
                inv = money.invoice_for_booking(c, b["id"])
            self.assertEqual((inv["paid_pence"], money.balance(inv)), (1500, 0))
            self.assertEqual(card_refunds(sess["payment_intent"]), [])

    def test_late_payment_after_child_rebooked_is_refunded(self):
        with FakeStripe() as stripe:
            aid, (sid,) = make_activity(sessions=1, capacity=5, price=1500, first_day=30, allow_pay_later=1)
            fam = register_family()
            child = complete_child(fam)
            r = self.card_booking(fam, child, sid)
            sess = stripe.last_session()
            ok(webhook(fam, "checkout.session.expired", dict(sess, status="expired")))
            again = self.pay_later(fam, child, sid)
            ok(webhook(fam, "checkout.session.completed", dict(stripe.pay(sess["id"]), status="complete")))
            self.assertEqual(booking(r["bookings"][0]["ref"])["status"], "expired")
            self.assertEqual(booking(again["bookings"][0]["ref"])["status"], "confirmed")
            self.assertEqual(card_refunds(sess["payment_intent"]), [("stripe", 1500, "pending")])


class InvoicePaymentTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()
        set_settings(booking_live=True, pay_later_for_all=True, sibling_discount_percent=0,
                     multi_day_discount_percent=0)

    def invoiced(self, price=2000):
        aid, (sid,) = make_activity(sessions=1, price=price, first_day=30, allow_pay_later=1)
        fam = register_family()
        d = ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": complete_child(fam)}],
                                                   "pay_mode": "pay_later", "accept_terms": True,
                                                   "idempotency_key": key()})).json()
        return fam, d["bookings"][0]["invoice"]["number"]

    def invoice(self, number):
        with db.read() as c:
            return c.execute("SELECT * FROM invoices WHERE number=?", (number,)).fetchone()

    def test_second_click_reuses_the_open_checkout(self):
        with FakeStripe() as stripe:
            fam, number = self.invoiced()
            first = ok(fam.post_json("/api/account/invoices/%s/pay" % number, {})).json()["redirect"]
            n = len(stripe.sessions)
            self.assertEqual(ok(fam.post_json("/api/account/invoices/%s/pay" % number, {})).json()["redirect"], first)
            self.assertEqual(len(stripe.sessions), n)

    def test_invoice_paid_twice_refunds_the_second(self):
        with FakeStripe() as stripe:
            fam, number = self.invoiced()
            inv = self.invoice(number)
            with db.tx() as c:  # two in the same second, neither open yet: distinct keys, no clash
                refs = [payments_stripe.pay_invoice(c, None, inv, account_id=inv["account_id"]) for _ in range(2)]
            self.assertNotEqual(*refs)
            sessions = []
            for ref in refs:
                payments_stripe.start_checkout(ref)
                sessions.append(stripe.last_session())
            self.assertNotEqual(sessions[0]["id"], sessions[1]["id"])
            for s in sessions:
                self.assertEqual(ok(webhook(fam, "checkout.session.completed", stripe.pay(s["id"]))).json()["result"],
                                 "paid_invoice")
            inv = self.invoice(number)
            self.assertEqual((inv["paid_pence"], money.balance(inv)), (2000, 0))
            self.assertEqual(card_refunds(sessions[0]["payment_intent"]), [])
            self.assertEqual(card_refunds(sessions[1]["payment_intent"]), [("stripe", 2000, "pending")])
            self.assertTrue(open_items("payment_problem", "checkout", checkout_id(refs[1])))
            payments_stripe.send_refunds()
            self.assertEqual(stripe.refunds[-1]["amount"], 2000)

    def test_cash_taken_while_card_payment_open(self):
        with FakeStripe() as stripe:
            fam, number = self.invoiced()
            ok(fam.post_json("/api/account/invoices/%s/pay" % number, {}))
            sess = stripe.last_session()
            ok(self.admin().post_json("/api/staff/payments", {"invoice_number": number, "method": "cash",
                                                              "amount_pence": 1500}))
            ok(webhook(fam, "checkout.session.completed", stripe.pay(sess["id"])))
            inv = self.invoice(number)
            self.assertEqual((inv["paid_pence"], money.balance(inv)), (2000, 0))
            self.assertEqual(card_refunds(sess["payment_intent"]), [("stripe", 1500, "pending")])

    def test_payment_for_voided_invoice_is_refunded(self):
        with FakeStripe() as stripe:
            fam, number = self.invoiced()
            ok(fam.post_json("/api/account/invoices/%s/pay" % number, {}))
            sess = stripe.last_session()
            ok(self.admin().post_json("/api/staff/invoices/%s/void" % number, {"reason": "Raised in error"}))
            ok(webhook(fam, "checkout.session.completed", stripe.pay(sess["id"])))
            inv = self.invoice(number)
            self.assertEqual((inv["status"], inv["paid_pence"]), ("void", 0))
            self.assertEqual(card_refunds(sess["payment_intent"]), [("stripe", 2000, "pending")])

    def test_payment_dates_are_uk_time(self):
        fam, number = self.invoiced()
        inv = self.invoice(number)
        with db.tx() as c:  # 00:30 BST on 2 July is 23:30 UTC on 1 July
            p = money.record_payment(c, amount=500, method="bank_transfer", account_id=inv["account_id"],
                                     received_at="2026-07-01T23:30:00Z")
            money.allocate(c, p["id"], inv["id"], 500)
        page = ok(fam.get("/account/invoices/" + number)).text
        self.assertIn("Bank transfer, 2 July 2026", page)


class GuestRefundTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()
        set_settings(booking_live=True)

    def test_guest_credit_refund_waits_for_finance(self):
        with FakeStripe() as stripe:
            aid, (sid,) = make_activity(sessions=1, price=500, first_day=30, level="guest", adult_price_pence=300,
                                        max_party_size=6, min_age=0, max_age=1200)
            g = Client()
            ok(g.post_json("/api/book/guest", {"session_id": sid, "email": "guest-%s@example.org" % key()[:8],
                                               "phone": "07700900123", "adults": 1, "children": 2, "adult_18": True,
                                               "ages_ok": True, "idempotency_key": key()}))
            sess = stripe.last_session()
            ok(webhook(g, "checkout.session.completed", stripe.pay(sess["id"])))
            fin = self.admin()
            ok(fin.post_json("/api/staff/sessions/%d/cancel" % sid, {"reason": "Ill", "refund": "credit"}))
            with db.read() as c:
                r = c.execute("SELECT r.* FROM refunds r JOIN credit_notes cn ON cn.id=r.credit_note_id JOIN"
                              " invoice_lines l ON l.invoice_id=cn.invoice_id JOIN bookings b ON b.id=l.booking_id"
                              " WHERE b.session_id=?", (sid,)).fetchone()
            self.assertEqual((r["method"], r["amount_pence"], r["status"]), ("bank_transfer", 1300, "pending"))
            pending = ok(fin.get("/api/staff/finance/summary")).json()["pending_refunds"]
            self.assertIn(r["id"], [x["id"] for x in pending])
            ok(fin.post_json("/api/staff/refunds/%d/done" % r["id"], {}))
            with db.read() as c:
                self.assertEqual(c.execute("SELECT status FROM refunds WHERE id=?", (r["id"],)).fetchone()[0],
                                 "succeeded")


class StripeEventsTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()
        set_settings(booking_live=True)

    def refund_row(self, stripe_id=None):
        with db.tx() as c:
            return c.execute("INSERT INTO refunds(amount_pence, method, status, stripe_refund_id, created_at)"
                             " VALUES (700, 'stripe', 'pending', ?, ?)", (stripe_id, db.now())).lastrowid

    def status(self, rid):
        with db.read() as c:
            return tuple(c.execute("SELECT status, stripe_refund_id FROM refunds WHERE id=?", (rid,)).fetchone())

    def test_refund_updates(self):
        with FakeStripe():
            client = Client()
            sid = "re_%s" % key()
            rid = self.refund_row(sid)
            ok(webhook(client, "refund.updated", {"id": sid, "object": "refund", "status": "succeeded", "amount": 700}))
            self.assertEqual(self.status(rid), ("succeeded", sid))
            # the webhook got here before send_refunds() stored the Stripe id: matched on our metadata
            rid2, sid2 = self.refund_row(), "re_%s" % key()
            ok(webhook(client, "refund.updated", {"id": sid2, "object": "refund", "status": "failed", "amount": 700,
                                                  "failure_reason": "expired_or_canceled_card",
                                                  "metadata": {"refund_id": str(rid2)}}))
            self.assertEqual(self.status(rid2), ("failed", sid2))
            self.assertTrue(open_items("refund_failed", "refund", rid2))
            # charge.refunded on a recent API version (no refunds list) is harmless; older ones still work
            ok(webhook(client, "charge.refunded", {"id": "ch_x", "object": "charge", "refunded": True}))
            rid3, sid3 = self.refund_row(), "re_%s" % key()
            with db.tx() as c:
                c.execute("UPDATE refunds SET stripe_refund_id=? WHERE id=?", (sid3, rid3))
            ok(webhook(client, "charge.refunded", {"id": "ch_y", "object": "charge", "refunds": {
                "data": [{"id": sid3, "object": "refund", "status": "succeeded", "amount": 700}]}}))
            self.assertEqual(self.status(rid3), ("succeeded", sid3))

    def test_dispute_links_the_payment(self):
        with FakeStripe() as stripe:
            aid, (sid,) = make_activity(sessions=1, price=3000, first_day=30)
            fam = register_family()
            ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": complete_child(fam)}],
                                                   "pay_mode": "card", "accept_terms": True, "idempotency_key": key()}))
            sess = stripe.last_session()
            ok(webhook(fam, "checkout.session.completed", stripe.pay(sess["id"])))
            ok(webhook(fam, "charge.dispute.created", {"id": "dp_1", "object": "dispute", "charge": "ch_disputed",
                                                       "payment_intent": sess["payment_intent"], "amount": 3000,
                                                       "reason": "fraudulent"}))
            with db.read() as c:
                pay = c.execute("SELECT * FROM payments WHERE stripe_payment_intent_id=?",
                                (sess["payment_intent"],)).fetchone()
            item = open_items("payment_dispute", "payment", pay["id"])[0]
            self.assertIn("£30.00", item["title"])
            self.assertIn(pay["ref"], item["title"])
            self.assertIn("ch_disputed", item["detail"])
            self.assertEqual(item["account_id"], pay["account_id"])
