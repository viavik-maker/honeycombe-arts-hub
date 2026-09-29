"""Trial sessions: trial pricing, one trial per child per activity, and the conversion report."""
import uuid

from hah import db, ratelimit
from tests.booking_helpers import future, make_activity, set_settings
from tests.family_helpers import complete_child, ok, register_family
from tests.support import ServerTestCase


class TrialTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True, pay_later_for_all=True)
        ratelimit.reset()
        self.fam = register_family()
        self.child = complete_child(self.fam)

    def quote(self, items):
        return ok(self.fam.post_json("/api/book/quote", {"items": items})).json()

    def test_trial_price_limits_and_conversion(self):
        aid, sids = make_activity(sessions=3, price=1500, allow_trial=1, trial_price_pence=500, allow_pay_later=1)
        other, (osid,) = make_activity(sessions=1, price=1500, allow_pay_later=1, first_day=30)
        # offered on the first session they pick; the second is the normal price
        q = self.quote([{"session_id": sids[0], "participant": self.child, "trial": True},
                        {"session_id": sids[1], "participant": self.child},
                        {"session_id": osid, "participant": self.child}])
        self.assertEqual([l["price_pence"] for l in q["lines"]], [500, 1500, 1500])
        self.assertEqual([l["trial_available"] for l in q["lines"]], [True, False, False])
        self.assertEqual(q["lines"][0]["trial_price_pence"], 500)
        self.assertEqual(q["total_pence"], 3500)
        # only one trial each
        q = self.quote([{"session_id": sids[0], "participant": self.child, "trial": True},
                        {"session_id": sids[1], "participant": self.child, "trial": True}])
        self.assertEqual(q["lines"][1]["outcome"], "blocked")
        self.assertEqual(q["lines"][1]["problems"][0]["code"], "trial")
        r = ok(self.fam.post_json("/api/book/confirm", {
            "items": [{"session_id": sids[0], "participant": self.child, "trial": True}], "pay_mode": "pay_later",
            "accept_terms": True, "idempotency_key": uuid.uuid4().hex})).json()
        ref = r["bookings"][0]["ref"]
        with db.read() as c:
            b = c.execute("SELECT * FROM bookings WHERE ref=?", (ref,)).fetchone()
        self.assertEqual((b["is_trial"], b["price_pence"]), (1, 500))
        # after that, no more trials of this activity
        q = self.quote([{"session_id": sids[1], "participant": self.child, "trial": True}])
        self.assertFalse(q["lines"][0]["trial_available"])
        self.assertEqual(q["lines"][0]["outcome"], "blocked")
        # the trial happened (3 days ago) and they came; then they booked a normal session
        with db.tx() as c:
            c.execute("UPDATE activity_sessions SET date=? WHERE id=?", (future(-3), sids[0]))
            c.execute("UPDATE attendance SET status='present' WHERE booking_id=?", (b["id"],))
        admin = self.admin()
        t = ok(admin.get("/api/staff/reports/trials?period=day&date=" + future(-3))).json()
        self.assertEqual((t["totals"]["trials"], t["totals"]["attended"], t["totals"]["converted"]), (1, 1, 0))
        self.assertEqual(t["totals"]["rate"], 0)
        ok(self.fam.post_json("/api/book/confirm", {
            "items": [{"session_id": sids[1], "participant": self.child}], "pay_mode": "pay_later",
            "accept_terms": True, "idempotency_key": uuid.uuid4().hex}))
        t = ok(admin.get("/api/staff/reports/trials?period=day&date=" + future(-3))).json()
        self.assertEqual((t["totals"]["converted"], t["totals"]["converted_same"], t["totals"]["rate"]), (1, 1, 100))
        self.assertEqual(t["activities"][0]["converted"], 1)
        self.assertEqual(self.admin(roles=("session_staff",)).get("/api/staff/reports/trials").status, 403)
        # the Trials quick filter finds it
        rows = ok(admin.get("/api/staff/bookings?quick=trials")).json()["bookings"]
        self.assertIn(ref, [x["ref"] for x in rows])

    def test_not_offered_unless_switched_on(self):
        aid, (sid,) = make_activity(sessions=1, price=1500, allow_pay_later=1)
        haf, (hsid,) = make_activity(sessions=1, haf_only=1, allow_trial=1)
        q = self.quote([{"session_id": sid, "participant": self.child}, {"session_id": hsid, "participant": self.child}])
        self.assertEqual([l["trial_available"] for l in q["lines"]], [False, False])
        q = self.quote([{"session_id": sid, "participant": self.child, "trial": True}])
        self.assertEqual(q["lines"][0]["outcome"], "blocked")
        # no trial price set = the normal price
        aid2, (sid2,) = make_activity(sessions=1, price=1200, allow_trial=1, allow_pay_later=1)
        q = self.quote([{"session_id": sid2, "participant": self.child, "trial": True}])
        self.assertEqual(q["lines"][0]["price_pence"], 1200)

    def test_staff_marking_and_activity_editor(self):
        aid, sids = make_activity(sessions=2, price=1500, allow_trial=1, trial_price_pence=0, allow_pay_later=1)
        refs = [ok(self.fam.post_json("/api/book/confirm", {
            "items": [{"session_id": s, "participant": self.child}], "pay_mode": "pay_later", "accept_terms": True,
            "idempotency_key": uuid.uuid4().hex})).json()["bookings"][0]["ref"] for s in sids]
        with db.read() as c:
            ids = [c.execute("SELECT id FROM bookings WHERE ref=?", (r,)).fetchone()[0] for r in refs]
        admin = self.admin()
        ok(admin.post_json("/api/staff/bookings/%d/trial" % ids[0], {"is_trial": True}))
        self.assertEqual(admin.post_json("/api/staff/bookings/%d/trial" % ids[1], {"is_trial": True}).status, 400)
        self.assertTrue(ok(admin.get("/api/staff/bookings/%d" % ids[0])).json()["booking"]["is_trial"])
        ok(admin.post_json("/api/staff/bookings/%d/trial" % ids[0], {"is_trial": False}))
        with db.read() as c:
            self.assertEqual(c.execute("SELECT is_trial, price_pence FROM bookings WHERE id=?", (ids[0],)).fetchone()[:],
                             (0, 1500))  # marking never changes the price
            self.assertTrue(c.execute("SELECT 1 FROM audit_log WHERE action='booking.trial'").fetchone())
        # the activity editor: an empty trial price means the normal price
        r = admin.post_json("/api/staff/activities/%d/update" % aid, {"trial_price_pence": "abc"})
        self.assertEqual(r.status, 422)
        self.assertIn("trial_price_pence", r.json()["errors"])
        a = ok(admin.post_json("/api/staff/activities/%d/update" % aid, {"trial_price_pence": ""})).json()["activity"]
        self.assertIsNone(a["trial_price_pence"])
        a = ok(admin.post_json("/api/staff/activities/%d/update" % aid, {"trial_price_pence": 750, "allow_trial": False})).json()["activity"]
        self.assertEqual((a["trial_price_pence"], a["allow_trial"]), (750, 0))
