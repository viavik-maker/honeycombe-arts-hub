"""Family import (dry run, commit, activation, rollback), pre-sold bookings, guest booking."""
import re
import uuid

from hah import db, ratelimit
from tests.booking_helpers import FakeStripe, make_activity, set_settings, webhook
from tests.family_helpers import last_email_to, ok
from tests.support import ServerTestCase

CSV = """Family ID,Parent First Name,Parent Surname,Email Address,Mobile Phone,Postcode,Child First Name,Child Surname,Child DOB
F1,Jane,Smith,jane@example.org,07700 900111,BH1 1AA,Tom,Smith,03/04/2016
F1,Jane,Smith,jane@example.org,07700 900111,BH1 1AA,Tia,Smith,21/09/2019
F2,Raj,Patel,raj@example.org,,BH5 2BB,Asha,Patel,2017-12-01
F3,No,Email,,07700900333,,Kid,Email,01/01/2018
F4,Bad,Date,bad@example.org,,,Kid,Date,31/02/2018
"""


class ImportTest(ServerTestCase):
    def test_dry_run_commit_activate_rollback(self):
        admin = self.admin()
        p = ok(admin.post_json("/api/staff/import/preview", {"csv": CSV})).json()
        m = p["mapping"]
        self.assertEqual(m["email"], "Email Address")
        self.assertEqual(m["parent_first_name"], "Parent First Name")
        self.assertEqual(m["child_dob"], "Child DOB")
        self.assertEqual(m["legacy_ref"], "Family ID")
        dry = ok(admin.post_json("/api/staff/import/run", {"csv": CSV, "mapping": m})).json()
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["stats"]["families"], 3)
        self.assertEqual(dry["stats"]["children"], 3)
        self.assertEqual(dry["stats"]["errors"], 2)
        with db.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM accounts WHERE source='import'").fetchone()[0], 0)
        r = ok(admin.post_json("/api/staff/import/run", {"csv": CSV, "mapping": m, "commit": True, "filename": "mb.csv"})).json()
        bid = r["batch_id"]
        with db.read() as c:
            jane = c.execute("SELECT * FROM accounts WHERE email='jane@example.org'").fetchone()
            self.assertEqual((jane["status"], jane["legacy_ref"], jane["mobile"]), ("pending_activation", "F1", "+447700900111"))
            kids = c.execute("SELECT first_name, dob, needs_review FROM participants WHERE account_id=? ORDER BY dob",
                             (jane["id"],)).fetchall()
            self.assertEqual([(k[0], k[1], k[2]) for k in kids], [("Tom", "2016-04-03", 1), ("Tia", "2019-09-21", 1)])
            # nothing sensitive imported
            self.assertEqual(c.execute("SELECT COUNT(*) FROM participant_health").fetchone()[0], 0)
        ok(admin.post_json("/api/staff/import/%d/activation" % bid, {}))
        text = last_email_to("jane@example.org")
        self.assertNotIn("Tom", text)  # activation emails never name children
        token = re.search(r"#t=([\w-]+)", text).group(1)
        # activate with a child's date of birth
        from tests.support import Client
        fam = Client()
        ok(fam.post_json("/api/account/activate", {"token": token, "child_dob": "2019-09-21",
                                                   "password": "a brand new passphrase"}))
        # sending again skips families already sent
        self.assertEqual(ok(admin.post_json("/api/staff/import/%d/activation" % bid, {})).json()["sent"], 0)
        # rollback keeps the family who activated
        r = ok(admin.post_json("/api/staff/import/%d/rollback" % bid, {})).json()
        self.assertEqual((r["removed"], r["kept"]), (2, 1))  # Raj, and the family whose child's date was wrong
        self.assertEqual(self.admin(roles=("manager",)).post_json("/api/staff/import/preview", {"csv": CSV}).status, 403)

    def test_existing_family_is_skipped(self):
        admin = self.admin()
        csv = "Email,First name,Last name\nexisting@example.org,A,B\n"
        from tests.family_helpers import register_family
        ratelimit.reset()
        register_family(email="existing@example.org")
        m = ok(admin.post_json("/api/staff/import/preview", {"csv": csv})).json()["mapping"]
        m.update(parent_first_name="First name", parent_last_name="Last name")
        dry = ok(admin.post_json("/api/staff/import/run", {"csv": csv, "mapping": m})).json()
        self.assertEqual(dry["stats"]["existing_families"], 1)
        self.assertEqual(dry["stats"]["families"], 0)

    def test_legacy_prepaid_bookings(self):
        from tests.family_helpers import complete_child, register_family
        ratelimit.reset()
        fam = register_family()
        child = complete_child(fam)
        aid, sids = make_activity(price=3000, sessions=2)
        admin = self.admin()
        r = ok(admin.post_json("/api/staff/bookings/legacy", {"participant_ref": child, "session_ids": sids})).json()
        self.assertEqual(len(r["created"]), 2)
        with db.read() as c:
            rows = c.execute("SELECT funding, status, price_pence FROM bookings WHERE session_id IN (?,?)", sids).fetchall()
            self.assertEqual({tuple(x) for x in rows}, {("prepaid_legacy", "confirmed", 0)})
            self.assertEqual(c.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 0)
        again = ok(admin.post_json("/api/staff/bookings/legacy", {"participant_ref": child, "session_ids": sids})).json()
        self.assertEqual(len(again["problems"]), 2)


class GuestTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True)
        ratelimit.reset()

    def body(self, sid, **kw):
        b = {"session_id": sid, "email": "guest@example.org", "phone": "07700 900444", "adults": 1, "children": 2,
             "ages_ok": True, "adult_18": True, "idempotency_key": uuid.uuid4().hex}
        b.update(kw)
        return b

    def test_free_event_needs_email_confirmation(self):
        aid, (sid,) = make_activity(sessions=1, level="guest", capacity=3, min_age=0, max_age=1200)
        c = self.client()
        r = c.post_json("/api/book/guest", self.body(sid, adult_18=False))
        self.assertEqual(r.status, 422)
        r = ok(c.post_json("/api/book/guest", self.body(sid))).json()
        self.assertEqual(r["status"], "pending_confirmation")
        # places are held (2 children counted; capacity 3 leaves 1)
        self.assertEqual(c.post_json("/api/book/guest", self.body(sid, email="other@example.org")).status, 409)
        # booking twice with the same email is refused
        self.assertEqual(c.post_json("/api/book/guest", self.body(sid, children=1)).status, 409)
        text = last_email_to("guest@example.org")
        token = re.search(r"#t=([\w-]+)", text).group(1)
        with db.read() as db_c:
            stored = " ".join(r[0] or "" for r in db_c.execute("SELECT body_text FROM message_deliveries"))
            self.assertNotIn(token, stored)
        ok(c.post_json("/api/book/guest/confirm", {"token": token}))
        self.assertEqual(c.post_json("/api/book/guest/confirm", {"token": token}).status, 400)
        self.assertIn("confirmed", last_email_to("guest@example.org"))
        with db.read() as db_c:
            self.assertEqual(db_c.execute("SELECT status FROM bookings WHERE session_id=?", (sid,)).fetchone()[0], "confirmed")

    def test_unconfirmed_places_are_released(self):
        from hah import guests
        aid, (sid,) = make_activity(sessions=1, level="guest", capacity=2, min_age=0, max_age=1200)
        ok(self.client().post_json("/api/book/guest", self.body(sid)))
        with db.tx() as c:
            c.execute("UPDATE bookings SET hold_expires_at='2000-01-01T00:00:00Z' WHERE session_id=?", (sid,))
        guests.expire_unconfirmed()
        with db.read() as c:
            self.assertEqual(c.execute("SELECT status FROM bookings WHERE session_id=?", (sid,)).fetchone()[0], "expired")

    def test_honeypot_and_non_guest_activities(self):
        aid, (sid,) = make_activity(sessions=1, level="full")
        self.assertEqual(self.client().post_json("/api/book/guest", self.body(sid)).status, 404)
        aid, (sid2,) = make_activity(sessions=1, level="guest", min_age=0, max_age=1200)
        ok(self.client().post_json("/api/book/guest", self.body(sid2, website="http://spam")))
        with db.read() as c:
            self.assertFalse(c.execute("SELECT 1 FROM bookings WHERE session_id=?", (sid2,)).fetchone())

    def test_paid_event_through_stripe_and_newsletter_opt_in(self):
        with FakeStripe() as stripe:
            aid, (sid,) = make_activity(sessions=1, level="guest", price=500, adult_price_pence=300, min_age=0, max_age=1200)
            c = self.client()
            r = ok(c.post_json("/api/book/guest", self.body(sid, marketing=True))).json()
            self.assertIn("stripe.test", r["redirect"])
            sess = stripe.last_session()
            self.assertEqual(sess["amount_total"], 1300)
            ok(webhook(c, "checkout.session.completed", stripe.pay(sess["id"])))
            self.assertIn("places are confirmed", last_email_to("guest@example.org"))
            with db.read() as db_c:
                self.assertEqual(db_c.execute("SELECT status FROM bookings WHERE session_id=?", (sid,)).fetchone()[0],
                                 "confirmed")
                self.assertFalse(db_c.execute("SELECT 1 FROM marketing_preferences WHERE email='guest@example.org'")
                                 .fetchone())  # not until they confirm
            outbox_text = [m for m in __import__("hah").mail.SENT if "Confirm you'd like news" in m["Subject"]]
            self.assertTrue(outbox_text)
            token = re.search(r"#m=([\w-]+)", outbox_text[-1].get_body(("plain",)).get_content()).group(1)
            ok(c.post_json("/api/book/guest/confirm", {"token": token}))
            with db.read() as db_c:
                self.assertTrue(db_c.execute("SELECT email_opt_in FROM marketing_preferences WHERE"
                                             " email='guest@example.org'").fetchone()[0])
