"""School-year ages, email wording notes, the staff morning email and disk alerts."""
import datetime
import unittest

from hah import catalogue, db, mail, ops, outbox, ratelimit
from tests.booking_helpers import set_settings
from tests.family_helpers import last_email_to, ok, register_family
from tests.support import ServerTestCase


class SchoolYearTest(unittest.TestCase):
    def test_age_on_31_august(self):
        act = {"age_basis": "session_date", "age_by_school_year": 1}
        self.assertEqual(catalogue.age_on(act, {"date": "2026-10-06"}, None), datetime.date(2026, 8, 31))
        self.assertEqual(catalogue.age_on(act, {"date": "2027-03-01"}, None), datetime.date(2026, 8, 31))
        self.assertEqual(catalogue.age_on(act, {"date": "2027-09-01"}, None), datetime.date(2027, 8, 31))
        act = {"age_basis": "first_session", "age_by_school_year": 1}
        self.assertEqual(catalogue.age_on(act, {"date": "2027-10-01"}, "2027-07-20"), datetime.date(2026, 8, 31))
        # a child born 1 September is the youngest in the next year group
        on = catalogue.age_on({"age_basis": "session_date", "age_by_school_year": 1}, {"date": "2026-10-06"}, None)
        self.assertEqual(catalogue.months_between(datetime.date(2020, 9, 1), on) // 12, 5)
        self.assertEqual(catalogue.months_between(datetime.date(2020, 8, 31), on) // 12, 6)
        plain = {"age_basis": "session_date", "age_by_school_year": 0}
        self.assertEqual(catalogue.age_on(plain, {"date": "2026-10-06"}, None), datetime.date(2026, 10, 6))


class OpsTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()
        set_settings(booking_live=True)

    def test_email_wording_note(self):
        owner = self.admin()
        tpls = ok(owner.get("/api/staff/email-wording")).json()["templates"]
        keys = [t["key"] for t in tpls]
        self.assertIn("booking_received", keys)
        self.assertNotIn("staff_invite", keys)
        self.assertEqual(self.admin(roles=("manager",)).get("/api/staff/email-wording").status, 403)
        r = ok(owner.post_json("/api/staff/email-wording/account_verify",
                               {"intro": "Summer club now starts at **9:30**."})).json()
        self.assertIn("Summer club now starts at 9:30.", r["text"])
        self.assertEqual(owner.post_json("/api/staff/email-wording/nope", {"intro": "x"}).status, 404)
        self.assertEqual(owner.post_json("/api/staff/email-wording/account_verify", {"intro": "x" * 700}).status, 400)
        fam = register_family()  # the verification email carries the note, after the greeting
        body = [m for m in mail.SENT if fam.email in m["To"]][-1]
        text = body.get_body(("plain",)).get_content()
        self.assertLess(text.index("Hello"), text.index("Summer club now starts"))
        self.assertIn("<strong>9:30</strong>", body.get_body(("html",)).get_content())
        ok(owner.post_json("/api/staff/email-wording/account_verify", {"intro": ""}))  # cleared
        with db.read() as c:
            self.assertFalse(c.execute("SELECT 1 FROM email_intros").fetchone())

    def test_morning_email_counts_only(self):
        mgr = self.admin(roles=("session_staff",))
        ok(mgr.post_json("/api/staff/me/digest", {"on": True}))
        self.assertTrue(ok(mgr.get("/api/staff/me")).json()["daily_digest"])
        self.assertIn("sent", ops.send_digests())
        with db.read() as c:
            email = mgr.staff["email"]
        text = last_email_to(email)
        self.assertIn("In-tray:", text)
        self.assertIn("Today:", text)
        self.assertNotIn("Overdue invoices", text)  # only what their role can see
        ok(mgr.post_json("/api/staff/me/digest", {"on": False}))

    def test_disk_alert(self):
        owner = self.admin()  # owners are emailed
        self.assertIsNone(ops.disk_check({"total_mb": 10000, "free_mb": 5000, "free_pct": 50.0}))
        self.assertIn("low", ops.disk_check({"total_mb": 10000, "free_mb": 200, "free_pct": 2.0}))
        with db.read() as c:
            self.assertTrue(c.execute("SELECT 1 FROM intray_items WHERE type='disk_low' AND status<>'done'").fetchone())
        outbox.send_due()
        self.assertTrue(any("disk is nearly full" in (m["Subject"] or "") for m in mail.SENT))
        ops.disk_check({"total_mb": 10000, "free_mb": 5000, "free_pct": 50.0})  # cleared when there's room again
        with db.read() as c:
            self.assertFalse(c.execute("SELECT 1 FROM intray_items WHERE type='disk_low' AND status<>'done'").fetchone())
        self.assertIn("free_mb", ok(owner.get("/api/admin/system-status")).json()["disk"])
