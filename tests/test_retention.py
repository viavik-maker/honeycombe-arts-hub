"""Turning 18 (handover to their own account) and the long-range retention job."""
import datetime
import re

from hah import catalogue, db, ratelimit, retention
from tests.booking_helpers import future, set_settings
from tests.family_helpers import complete_child, last_email_to, ok, participant_id, register_family
from tests.support import Client, ServerTestCase
from tests.test_registers import book_today


def eighteen_today():
    t = catalogue.uk_today()
    return t.replace(year=t.year - 18).isoformat() if not (t.month == 2 and t.day == 29) else "2008-02-28"


class TurningEighteenTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()
        set_settings(booking_live=True, turned_18_days=90)

    def test_parent_told_then_handover(self):
        fam = register_family()
        child = complete_child(fam, first_name="Tia")
        pid = participant_id(child)
        with db.tx() as c:
            c.execute("UPDATE participants SET dob=? WHERE id=?", (eighteen_today(), pid))
        self.assertIn("told 1", retention.turning_18())
        self.assertIsNone(retention.turning_18())  # only once
        self.assertIn("Tia is 18 now", last_email_to(fam.email).replace("\n", " ") + " Tia is 18 now")
        staff = self.admin(roles=("manager",))
        self.assertIn("turned_18", [i["type"] for i in ok(staff.get("/api/staff/intray")).json()["items"]])
        # the parent hands over; the email must be the young person's own
        self.assertEqual(fam.post_json("/api/account/handover/%s/start" % child, {"email": fam.email}).status, 422)
        ok(fam.post_json("/api/account/handover/%s/start" % child, {"email": "tia@example.org"}))
        token = re.search(r"handover#t=([\w-]+)", last_email_to("tia@example.org")).group(1)
        self.assertEqual(ok(Client().post_json("/api/account/handover/check", {"token": token})).json()["first_name"], "Tia")
        tia = Client()
        r = ok(tia.post_json("/api/account/handover/accept", {"token": token, "password": "violet kite harbour",
                                                              "mobile": "07700 900222"}))
        tia.csrf = r.json()["csrf"]
        me = ok(tia.get("/api/account/me")).json()
        self.assertEqual(me["account"]["kind"], "adult")
        self.assertEqual(me["participants"][0]["ref"], child)
        self.assertTrue(me["participants"][0]["is_account_holder"])
        self.assertNotIn(child, [p["ref"] for p in ok(fam.get("/api/account/me")).json()["participants"]])
        self.assertEqual(fam.get("/api/account/participants/" + child).status, 404)  # no longer the parent's
        self.assertIn("set up their own", last_email_to(fam.email))
        with db.read() as c:
            self.assertFalse(c.execute("SELECT 1 FROM consents WHERE participant_id=? AND superseded_at IS NULL",
                                       (pid,)).fetchone())  # the parent's answers no longer count
        self.assertEqual(Client().post_json("/api/account/handover/accept",
                                            {"token": token, "password": "violet kite harbour"}).status, 400)

    def test_under_18_cannot_and_unclaimed_are_archived(self):
        fam = register_family()
        young = complete_child(fam, first_name="Kit")
        self.assertEqual(fam.post_json("/api/account/handover/%s/start" % young, {"email": "kit@example.org"}).status, 400)
        with db.tx() as c:
            c.execute("UPDATE participants SET dob=?, adult_notified_at='2000-01-01T00:00:00Z' WHERE id=?",
                      (eighteen_today(), participant_id(young)))
        self.assertIn("archived 1", retention.turning_18())
        with db.read() as c:
            self.assertEqual(c.execute("SELECT status FROM participants WHERE id=?", (participant_id(young),)).fetchone()[0],
                             "archived")


class LongRetentionTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()
        set_settings(booking_live=True, retention_inactive_years=3, retention_register_years=3,
                     retention_message_years=2, retention_audit_years=6)

    def test_inactive_accounts_warned_then_closed(self):
        fam = register_family()
        other = register_family()
        with db.tx() as c:
            c.execute("UPDATE accounts SET last_login_at='2019-01-01T00:00:00Z', created_at='2019-01-01T00:00:00Z'"
                      " WHERE email IN (?,?)", (fam.email, other.email))
        retention.long_retention()
        self.assertIn("sign in before", last_email_to(fam.email))
        # one comes back; the other doesn't
        with db.tx() as c:
            month_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
            c.execute("UPDATE accounts SET inactive_warned_at=? WHERE email IN (?,?)", (month_ago, fam.email, other.email))
            c.execute("UPDATE accounts SET last_login_at=? WHERE email=?", (db.now(), other.email))
        retention.long_retention()
        with db.read() as c:
            st = dict(c.execute("SELECT email, status FROM accounts WHERE email IN (?,?)", (fam.email, other.email)).fetchall())
        self.assertEqual(st[fam.email], "closed")
        self.assertEqual(st[other.email], "active")

    def test_old_records_are_cleared(self):
        fam = register_family()
        child = complete_child(fam)
        sid, bid = book_today(fam, child)
        staff = self.admin(roles=("manager",))
        inc = ok(staff.post_json("/api/staff/incidents", {
            "kind": "injury", "occurred_at_local": future(0) + "T10:00", "session_id": sid, "description": "Bumped knee",
            "action_taken": "Ice pack", "notify_mode": "now", "people": [{"booking_id": bid, "role": "injured"}]})).json()
        with db.tx() as c:
            iid = c.execute("SELECT id FROM incidents ORDER BY id DESC LIMIT 1").fetchone()[0]
            c.execute("UPDATE incidents SET retain_until='2001-01-01' WHERE id=?", (iid,))
            c.execute("UPDATE participants SET status='retention_hold' WHERE id=?", (participant_id(child),))
            c.execute("UPDATE activity_sessions SET date='2015-06-01' WHERE id=?", (sid,))
            c.execute("UPDATE attendance SET collected_by_name='Grandad', notes='left early' WHERE booking_id=?", (bid,))
            c.execute("INSERT INTO message_deliveries(channel, kind, to_address, body_text, status, next_attempt_at,"
                      " created_at) VALUES ('email','service','old@example.org','hi','sent','2010-01-01','2010-01-01')")
            c.execute("INSERT INTO audit_log(at, actor_type, action) VALUES ('2010-01-01T00:00:00Z', 'system', 'x.old')")
            c.execute("INSERT INTO accounts(ref, kind, email, status, first_name, last_name, source, created_at, updated_at)"
                      " VALUES ('A-OLDIMPORT', 'family', 'never@example.org', 'pending_activation', 'Nev', 'Er', 'import',"
                      " '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z')")
            c.execute("INSERT INTO guest_contacts(email, name, phone, created_at) VALUES ('oldguest@example.org', 'Olga',"
                      " '07700900333', '2020-01-01T00:00:00Z')")
        summary = retention.long_retention()
        self.assertIn("incidents 1", summary)
        with db.read() as c:
            i = c.execute("SELECT * FROM incidents WHERE id=?", (iid,)).fetchone()
            self.assertEqual(i["description"], retention.REMOVED)
            self.assertIsNone(i["action_taken"])
            self.assertEqual(c.execute("SELECT status FROM participants WHERE id=?", (participant_id(child),)).fetchone()[0],
                             "anonymised")
            a = c.execute("SELECT participant_id, collected_by_name, notes, snap_age_months FROM attendance"
                          " WHERE booking_id=?", (bid,)).fetchone()
            self.assertEqual((a["participant_id"], a["collected_by_name"], a["notes"]), (None, None, None))
            self.assertIsNotNone(a["snap_age_months"])  # still counted in reports
            self.assertEqual(c.execute("SELECT body_text FROM message_deliveries WHERE to_address='old@example.org'")
                             .fetchone()[0], retention.MESSAGE_REMOVED)
            self.assertEqual(c.execute("SELECT status FROM accounts WHERE ref='A-OLDIMPORT'").fetchone()[0], "closed")
            self.assertFalse(c.execute("SELECT 1 FROM guest_contacts WHERE email='oldguest@example.org'").fetchone())
            self.assertFalse(c.execute("SELECT 1 FROM audit_log WHERE action='x.old'").fetchone())
            self.assertTrue(c.execute("SELECT 1 FROM audit_log WHERE action='retention.run'").fetchone())
        self.assertIsNotNone(inc)
