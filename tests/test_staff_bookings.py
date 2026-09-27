"""Staff booking: account credit and the amount taken, and sessions that can't be booked at all."""
from hah import db, money, ratelimit
from tests.booking_helpers import make_activity, set_settings
from tests.family_helpers import complete_child, ok, register_family
from tests.support import ServerTestCase


def family_with_credit(pence):
    fam = register_family()
    fam.child = complete_child(fam)
    with db.tx() as c:
        fam.account_id, fam.ref = c.execute("SELECT id, ref FROM accounts WHERE email=?", (fam.email,)).fetchone()
        if pence:
            c.execute("INSERT INTO refunds(account_id, amount_pence, method, status, created_at, processed_at)"
                      " VALUES (?,?, 'account_credit', 'succeeded', ?, ?)", (fam.account_id, pence, db.now(), db.now()))
    return fam


def payments(account_id):
    with db.read() as c:
        return [tuple(r) for r in c.execute(
            "SELECT p.method, p.amount_pence, COALESCE(SUM(a.amount_pence), 0) FROM payments p LEFT JOIN"
            " payment_allocations a ON a.payment_id=p.id WHERE p.account_id=? GROUP BY p.id ORDER BY p.id",
            (account_id,))]


class StaffBookingMoneyTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True, sibling_discount_percent=0, multi_day_discount_percent=0)
        ratelimit.reset()

    def book(self, admin, fam, sid, payment, status=200):
        r = admin.post_json("/api/staff/bookings/create", {"account_ref": fam.ref, "payment": payment,
                                                           "items": [{"session_id": sid, "participant": fam.child}]})
        self.assertEqual(r.status, status, r.text)
        return r.json()

    def test_cash_is_only_what_credit_leaves(self):
        admin = self.admin(roles=("manager",))
        aid, (s1, s2, s3) = make_activity(sessions=3, price=2000)
        fam = family_with_credit(1000)
        # asking to take the full price is refused: the credit pays part of it
        r = self.book(admin, fam, s1, {"mode": "record", "method": "cash", "amount_pence": 2000}, status=422)
        self.assertIn("£10.00", r["errors"]["amount_pence"])
        # with no amount given, the rest is taken
        r = self.book(admin, fam, s1, {"mode": "record", "method": "cash"})
        self.assertEqual(r["invoice"]["balance_pence"], 0)
        self.assertEqual(payments(fam.account_id), [("account_credit", 1000, 1000), ("cash", 1000, 1000)])
        with db.read() as c:
            self.assertEqual(money.credit_balance(c, fam.account_id), 0)
        # credit that covers it all: nothing to take, nothing recorded
        fam2 = family_with_credit(5000)
        r = self.book(admin, fam2, s2, {"mode": "record", "method": "cash"})
        self.assertEqual(r["invoice"]["balance_pence"], 0)
        self.assertEqual(payments(fam2.account_id), [("account_credit", 2000, 2000)])
        # no credit: the full price as before
        fam3 = family_with_credit(0)
        self.book(admin, fam3, s3, {"mode": "record", "method": "card_terminal"})
        self.assertEqual(payments(fam3.account_id), [("card_terminal", 2000, 2000)])

    def test_cancelled_session_refused_even_with_override(self):
        admin = self.admin(roles=("manager",))
        aid, (sid,) = make_activity(sessions=1, price=1500)
        fam = family_with_credit(0)
        with db.tx() as c:
            c.execute("UPDATE activity_sessions SET status='cancelled' WHERE id=?", (sid,))
        for extra in ({}, {"override": True, "reason": "Family insisted"}):
            r = admin.post_json("/api/staff/bookings/create", dict(
                {"account_ref": fam.ref, "items": [{"session_id": sid, "participant": fam.child}],
                 "payment": {"mode": "unpaid"}}, **extra))
            self.assertEqual(r.status, 409, r.text)
            self.assertIn("cancelled", " ".join(r.json()["problems"]))
        with db.read() as c:
            self.assertFalse(c.execute("SELECT 1 FROM bookings WHERE session_id=?", (sid,)).fetchone())
