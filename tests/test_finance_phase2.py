"""Discounts, aged debt, the accounting export and Stripe payout matching."""
import uuid

from hah import db, payments_stripe, ratelimit
from tests.booking_helpers import FakeStripe, booking, make_activity, set_settings, webhook
from tests.family_helpers import complete_child, ok, register_family
from tests.support import ServerTestCase


def confirm(fam, items, pay_mode="pay_later"):
    return ok(fam.post_json("/api/book/confirm", {"items": items, "pay_mode": pay_mode, "accept_terms": True,
                                                  "idempotency_key": uuid.uuid4().hex})).json()


class DiscountTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True, pay_later_for_all=True, sibling_discount_percent=0,
                     multi_day_discount_percent=0, multi_day_min_sessions=5)
        ratelimit.reset()

    def test_sibling_and_multi_day(self):
        fam = register_family()
        a, b = complete_child(fam, first_name="Ada"), complete_child(fam, first_name="Ben", dob="2017-03-03")
        aid, sids = make_activity(sessions=3, price=2000, allow_pay_later=1)
        items = [{"session_id": sids[0], "participant": a}, {"session_id": sids[0], "participant": b}]
        q = ok(fam.post_json("/api/book/quote", {"items": items})).json()
        self.assertEqual(q["total_pence"], 4000)  # off until a percentage is set
        set_settings(sibling_discount_percent=10, multi_day_discount_percent=20, multi_day_min_sessions=3)
        q = ok(fam.post_json("/api/book/quote", {"items": items})).json()
        self.assertEqual(sorted(l["price_pence"] for l in q["lines"]), [1800, 2000])
        self.assertEqual(q["total_pence"], 3800)
        disc = [l for l in q["lines"] if l["discount_pence"]][0]
        self.assertEqual((disc["full_price_pence"], disc["discount_reason"]), (2000, "Sibling discount (10%)"))
        # three sessions for one child: multi-day wins over sibling (never both)
        items3 = [{"session_id": s, "participant": a} for s in sids] + [{"session_id": sids[0], "participant": b}]
        q = ok(fam.post_json("/api/book/quote", {"items": items3})).json()
        prices = {(l["participant"], l["session_id"]): l["price_pence"] for l in q["lines"]}
        self.assertEqual([prices[(a, s)] for s in sids], [1600, 1600, 1600])
        self.assertEqual(prices[(b, sids[0])], 1800)
        # booked: the invoice shows the full price and the discount
        r = confirm(fam, items)
        with db.read() as c:
            lines = c.execute("SELECT l.unit_pence, l.amount_pence, l.funding_note FROM invoice_lines l JOIN bookings b"
                              " ON b.id=l.booking_id WHERE b.discount_pence>0").fetchall()
        self.assertEqual([(x["unit_pence"], x["amount_pence"]) for x in lines], [(2000, 1800)])
        self.assertIn("Sibling discount", lines[0]["funding_note"])
        # a brother or sister already booked on the session counts too
        c3 = complete_child(fam, first_name="Cal", dob="2016-01-01")
        q = ok(fam.post_json("/api/book/quote", {"items": [{"session_id": sids[0], "participant": c3}]})).json()
        self.assertEqual(q["lines"][0]["price_pence"], 1800)
        # moving a discounted booking to a session of the same list price is allowed
        disc_ref = [x["ref"] for x in r["bookings"] if booking(x["ref"])["discount_pence"]][0]
        with db.read() as c:
            bid = c.execute("SELECT id FROM bookings WHERE ref=?", (disc_ref,)).fetchone()[0]
        ok(self.admin().post_json("/api/staff/bookings/%d/move" % bid, {"session_id": sids[1]}))
        # trials and HAF places are never discounted
        tid, (tsid,) = make_activity(sessions=1, price=2000, allow_trial=1, trial_price_pence=500, allow_pay_later=1,
                                     first_day=40)
        q = ok(fam.post_json("/api/book/quote", {"items": [{"session_id": tsid, "participant": a, "trial": True},
                                                           {"session_id": tsid, "participant": b}]})).json()
        self.assertEqual(sorted(l["price_pence"] for l in q["lines"]), [500, 2000])


class FinanceReportTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True, pay_later_for_all=True, sibling_discount_percent=0, multi_day_discount_percent=0)
        ratelimit.reset()

    def test_aged_debt_and_accounting_export(self):
        fam = register_family()
        child = complete_child(fam)
        aid, sids = make_activity(sessions=2, price=1500, allow_pay_later=1)
        confirm(fam, [{"session_id": sids[0], "participant": child}])
        confirm(fam, [{"session_id": sids[1], "participant": child}])
        with db.tx() as c:
            invs = c.execute("SELECT * FROM invoices ORDER BY id DESC LIMIT 2").fetchall()
            c.execute("UPDATE invoices SET issue_date=date('now','-50 days'), due_date=date('now','-45 days') WHERE id=?",
                      (invs[1]["id"],))
        fin = self.admin(roles=("finance",))
        d = ok(fin.get("/api/staff/finance/aged-debt")).json()
        row = [r for r in d["rows"] if r["email"] == fam.email][0]
        self.assertEqual((row["31_60"], row["current"], row["total_pence"]), (1500, 1500, 3000))
        self.assertGreaterEqual(d["totals"]["31_60"], 1500)
        self.assertIn(fam.email, fin.get("/api/staff/finance/aged-debt.csv").text)
        self.assertEqual(self.admin(roles=("session_staff",)).get("/api/staff/finance/aged-debt").status, 403)
        # pay one in cash, then export
        ok(fin.post_json("/api/staff/payments", {"invoice_number": invs[0]["number"], "method": "cash",
                                                 "amount_pence": 1500}))
        csv = fin.get("/api/staff/finance/accounting.csv").text
        self.assertIn("Invoice,%s" % invs[0]["number"], csv)
        self.assertIn("Payment,", csv)
        self.assertIn("Cash", csv)
        self.assertIn("Holiday club", csv)  # the category column
        self.assertNotIn("Maya", csv)  # no children's names
        with db.read() as c:
            self.assertTrue(c.execute("SELECT 1 FROM audit_log WHERE action='finance.export_accounting'").fetchone())

    def test_stripe_payout_matching(self):
        with FakeStripe() as stripe:
            fin = self.admin(roles=("finance",))
            fam = register_family()
            child = complete_child(fam)
            aid, (sid,) = make_activity(sessions=1, price=3000, first_day=30)
            confirm(fam, [{"session_id": sid, "participant": child}], pay_mode="card")
            sess = stripe.last_session()
            ok(webhook(fam, "checkout.session.completed", stripe.pay(sess["id"])))
            stripe.payouts.append({"id": "po_test1", "object": "payout", "amount": 2900 + 480, "currency": "gbp",
                                   "status": "paid", "arrival_date": 1790000000, "created": 1789900000})
            stripe.balance_txns += [
                {"id": "txn_1", "type": "charge", "amount": 3000, "fee": 100, "net": 2900, "created": 1789800000,
                 "payout": "po_test1", "source": {"id": "ch_1", "object": "charge", "payment_intent": sess["payment_intent"]}},
                {"id": "txn_2", "type": "charge", "amount": 500, "fee": 20, "net": 480, "created": 1789800000,
                 "payout": "po_test1", "source": {"id": "ch_2", "object": "charge", "payment_intent": "pi_elsewhere"}},
                {"id": "txn_3", "type": "payout", "amount": -3380, "fee": 0, "net": -3380, "created": 1789900000,
                 "payout": "po_test1", "source": "po_test1"}]
            p = ok(fin.get("/api/staff/finance/stripe/payouts")).json()
            self.assertEqual(p["payouts"][0]["id"], "po_test1")
            d = ok(fin.get("/api/staff/finance/stripe/payouts/po_test1")).json()
            self.assertTrue(d["adds_up"])
            self.assertEqual((d["totals"]["gross"], d["totals"]["fees"], d["totals"]["unmatched"]), (3500, 120, 1))
            mine = [r for r in d["rows"] if r["id"] == "txn_1"][0]
            self.assertTrue(mine["matched"])
            self.assertEqual(mine["ours"]["kind"], "payment")
            self.assertTrue(mine["ours"]["invoices"])  # the invoice it paid
            csv = fin.get("/api/staff/finance/stripe/payouts/po_test1?format=csv").text
            self.assertIn("NO", csv)
            self.assertEqual(fin.get("/api/staff/finance/stripe/payouts/not-an-id").status, 404)
        # not configured: says so rather than failing
        self.assertFalse(payments_stripe.configured())
        self.assertFalse(ok(fin.get("/api/staff/finance/stripe/payouts")).json()["configured"])
