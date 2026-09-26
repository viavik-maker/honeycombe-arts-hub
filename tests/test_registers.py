"""Registers (sign in/out, collection checks, printing, HAF export) and incidents."""
import uuid

from hah import db, ratelimit
from tests.booking_helpers import future, make_activity, set_settings
from tests.family_helpers import complete_child, last_email_to, ok, participant_id, register_family
from tests.support import ServerTestCase


def book_today(fam, child, **kw):
    """Book a session (booked for tomorrow, then moved to today)."""
    aid, (sid,) = make_activity(sessions=1, first_day=1, **kw)
    r = ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": child}],
                                               "accept_terms": True, "idempotency_key": uuid.uuid4().hex})).json()
    with db.tx() as c:
        c.execute("UPDATE activity_sessions SET date=? WHERE id=?", (future(0), sid))
    with db.read() as c:
        bid = c.execute("SELECT id FROM bookings WHERE ref=?", (r["bookings"][0]["ref"],)).fetchone()[0]
    return sid, bid


class RegisterTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True)
        ratelimit.reset()
        self.fam = register_family()
        self.child = complete_child(self.fam)  # allergies: Peanuts, collection password "blue tiger"
        with db.tx() as c:
            c.execute("UPDATE participant_safeguarding SET family_info='Private family matter' WHERE participant_id=?",
                      (participant_id(self.child),))
        self.sid, self.bid = book_today(self.fam, self.child)

    def test_session_staff_today_only_and_contents(self):
        staff = self.admin(roles=("session_staff",))
        d = ok(staff.get("/api/staff/registers/session/%d" % self.sid)).json()
        row = d["rows"][0]
        self.assertEqual(row["person"]["first_name"], "Maya")
        self.assertEqual(row["person"]["health"]["allergies"], "Peanuts")
        self.assertTrue(row["person"]["flags"]["anaphylaxis"])
        self.assertIsNone(row["person"]["haf"])  # HAF status hidden from session staff
        text = str(d)
        self.assertNotIn("Private family matter", text)
        self.assertNotIn("$p", text)  # no password hash
        # a session next week: session staff can't open it, a manager can
        aid, (later,) = make_activity(sessions=1, first_day=7)
        self.assertEqual(staff.get("/api/staff/registers/session/%d" % later).status, 403)
        self.assertEqual(self.admin(roles=("manager",)).get("/api/staff/registers/session/%d" % later).status, 200)
        # the day list
        day = ok(staff.get("/api/staff/registers?date=" + future(0))).json()
        self.assertIn(self.sid, [s["id"] for s in day["sessions"]])
        # printed copy: health yes, safeguarding and passwords no
        page = staff.get("/admin/registers/%d/print" % self.sid)
        self.assertEqual(page.status, 200)
        self.assertIn("ALLERGY: Peanuts", page.text)
        self.assertNotIn("Private family matter", page.text)
        self.assertNotIn("blue tiger", page.text)

    def test_sign_in_and_out_with_password(self):
        staff = self.admin(roles=("session_staff",))
        url = "/api/staff/attendance/%d" % self.bid
        self.assertEqual(staff.post_json(url, {"action": "out", "method": "password"}).status, 400)  # not in yet
        ok(staff.post_json(url, {"action": "in"}))
        r = staff.post_json(url, {"action": "out", "method": "password", "password": "red lion", "collected_by_name": "Jo"})
        self.assertEqual(r.status, 400)
        self.assertTrue(r.json().get("wrong_password"))
        r = ok(staff.post_json(url, {"action": "out", "method": "password", "password": "Blue Tiger ",
                                     "collected_by_name": "Jo Jones"})).json()
        self.assertEqual(r["release_method"], "password")
        with db.read() as c:
            self.assertEqual(c.execute("SELECT collected_by_name FROM attendance WHERE booking_id=?",
                                       (self.bid,)).fetchone()[0], "Jo Jones")

    def test_too_many_wrong_passwords_means_phone_check(self):
        staff = self.admin(roles=("session_staff",))
        url = "/api/staff/attendance/%d" % self.bid
        ok(staff.post_json(url, {"action": "in"}))
        for _ in range(5):
            staff.post_json(url, {"action": "out", "method": "password", "password": "nope", "collected_by_name": "X"})
        r = staff.post_json(url, {"action": "out", "method": "password", "password": "blue tiger", "collected_by_name": "X"})
        self.assertIn("phone", r.json()["error"])
        ok(staff.post_json(url, {"action": "out", "method": "known_adult_verified", "collected_by_name": "Jo Jones"}))

    def test_go_home_alone_needs_permission(self):
        staff = self.admin(roles=("session_staff",))
        url = "/api/staff/attendance/%d" % self.bid
        ok(staff.post_json(url, {"action": "in"}))
        r = staff.post_json(url, {"action": "out", "method": "went_home_alone"})
        self.assertIn("permission", r.json()["error"])

    def test_incident_at_collection_blocks_sign_out(self):
        staff = self.admin(roles=("session_staff",))
        r = ok(staff.post_json("/api/staff/incidents", {
            "kind": "injury", "occurred_at_local": future(0) + "T11:15", "session_id": self.sid,
            "description": "Grazed knee on the playground", "action_taken": "Cleaned, plaster applied",
            "first_aid_given": True, "notify_mode": "at_collection", "people": [{"booking_id": self.bid, "role": "injured"}]}))
        url = "/api/staff/attendance/%d" % self.bid
        ok(staff.post_json(url, {"action": "in"}))
        r = staff.post_json(url, {"action": "out", "method": "known_adult_verified", "collected_by_name": "Jo"})
        self.assertEqual(r.status, 409)
        self.assertTrue(r.json()["to_discuss"])
        ok(staff.post_json(url, {"action": "out", "method": "known_adult_verified", "collected_by_name": "Jo",
                                 "incident_discussed": True}))
        # the parent can read it now and acknowledge
        mine = ok(self.fam.get("/api/account/incidents")).json()["incidents"]
        self.assertEqual(mine[0]["description"], "Grazed knee on the playground")
        ok(self.fam.post_json("/api/account/incidents/%s/acknowledge" % mine[0]["ref"], {}))
        self.assertTrue(ok(self.fam.get("/api/account/incidents")).json()["incidents"][0]["acknowledged"])
        # another family can't
        other = register_family()
        self.assertEqual(other.post_json("/api/account/incidents/%s/acknowledge" % mine[0]["ref"], {}).status, 404)

    def test_notify_now_email_has_no_details(self):
        staff = self.admin(roles=("session_staff",))
        ok(staff.post_json("/api/staff/incidents", {
            "kind": "illness", "occurred_at_local": future(0) + "T12:00", "session_id": self.sid,
            "description": "Felt sick after lunch", "notify_mode": "now", "people": [{"booking_id": self.bid}]}))
        text = last_email_to(self.fam.email)
        self.assertIn("note about Maya", text)
        self.assertNotIn("sick", text)

    def test_not_notified_needs_reason(self):
        staff = self.admin(roles=("session_staff",))
        r = staff.post_json("/api/staff/incidents", {"kind": "behaviour", "occurred_at_local": future(0) + "T12:00",
                                                     "description": "x", "notify_mode": "not_notified",
                                                     "people": [{"booking_id": self.bid}]})
        self.assertEqual(r.status, 422)
        self.assertIn("not_notified_reason", r.json()["errors"])

    def test_safeguarding_concern_is_dsl_only(self):
        staff = self.admin(roles=("session_staff",))
        ok(staff.post_json("/api/staff/incidents", {"kind": "safeguarding", "occurred_at_local": future(0) + "T12:00",
                                                    "description": "Disclosure", "notify_mode": "now",
                                                    "people": [{"booking_id": self.bid}]}))
        owner = self.admin()
        self.assertFalse([i for i in ok(owner.get("/api/staff/incidents?kind=safeguarding")).json()["incidents"]])
        dsl = self.admin(roles=("dsl",))
        items = ok(dsl.get("/api/staff/incidents?kind=safeguarding")).json()["incidents"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["notify_mode"], "not_notified")
        self.assertEqual(owner.get("/api/staff/incidents/%d" % items[0]["id"]).status, 404)
        self.assertNotIn("Disclosure", ok(self.fam.get("/api/account/incidents")).text)
        with db.read() as c:
            self.assertEqual(c.execute("SELECT required_perm FROM intray_items WHERE type='safeguarding_concern'")
                             .fetchone()[0], "safeguarding.view")

    def test_haf_export(self):
        with db.tx() as c:
            c.execute("UPDATE participants SET haf_status='verified' WHERE id=?", (participant_id(self.child),))
        sid, bid = book_today(self.fam, self.child, haf_only=1, category_id=1)
        admin = self.admin()
        ok(admin.post_json("/api/staff/attendance/%d" % bid, {"action": "in"}))
        r = admin.get("/api/staff/reports/haf.csv?from=%s&to=%s" % (future(-1), future(1)))
        self.assertEqual(r.status, 200)
        self.assertIn("Maya", r.text)
        self.assertIn(future(0), r.text)
        self.assertEqual(self.admin(roles=("session_staff",)).get("/api/staff/reports/haf.csv").status, 403)
