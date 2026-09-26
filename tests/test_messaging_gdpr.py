"""Messages (service vs news), unsubscribe, the newsletter migration, and GDPR self-service."""
import json
import os
import re
import uuid

from hah import db, gdpr, mail, marketing, outbox, ratelimit
from tests.booking_helpers import make_activity, set_settings
from tests.family_helpers import complete_child, last_email_to, ok, participant_id, register_family
from tests.support import ServerTestCase, data_path


class MessagingTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True)
        ratelimit.reset()

    def test_service_message_to_a_session(self):
        fam = register_family()
        child = complete_child(fam)
        aid, (sid,) = make_activity(sessions=1)
        ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": child}],
                                               "accept_terms": True, "idempotency_key": uuid.uuid4().hex}))
        staff = self.admin(roles=("manager",))
        body = {"kind": "service", "channel": "email", "subject": "Room change",
                "body": "Hi {{first_name}},\n\nWe're in **Room 2** tomorrow.", "audience": {"type": "session", "session_id": sid}}
        p = ok(staff.post_json("/api/staff/messages/preview", body)).json()
        self.assertEqual(p["count"], 1)
        self.assertIn("Hi Sarah", p["text"])
        self.assertEqual(staff.post_json("/api/staff/messages/send", dict(body, expected=5)).status, 409)
        ok(staff.post_json("/api/staff/messages/send", dict(body, expected=1)))
        outbox.send_due()
        msg = [m for m in mail.SENT if m["Subject"] == "Room change"][-1]
        self.assertIn("<strong>Room 2</strong>", msg.get_body(("html",)).get_content())
        self.assertIsNone(msg["List-Unsubscribe"])  # service messages aren't marketing
        camps = ok(staff.get("/api/staff/messages")).json()["campaigns"]
        self.assertEqual(camps[0]["recipients"], 1)
        arch = ok(staff.get("/api/staff/messages/archive?q=" + fam.email)).json()["messages"]
        self.assertTrue(any(m["subject"] == "Room change" for m in arch))
        # session staff can't send messages
        self.assertEqual(self.admin(roles=("session_staff",)).post_json("/api/staff/messages/preview", body).status, 403)

    def test_news_only_goes_to_opted_in_and_unsubscribe_works(self):
        c = self.client()
        ok(c.post_json("/api/newsletter", {"email": "yes@example.org", "name": "Yasmin"}))
        fam = register_family()  # an account holder who didn't opt in
        staff = self.admin()
        body = {"kind": "marketing", "channel": "email", "subject": "Summer news", "body": "Hello {{first_name}}"}
        with db.read() as dbc:
            opted = dbc.execute("SELECT COUNT(*) FROM marketing_preferences WHERE email_opt_in=1").fetchone()[0]
        p = ok(staff.post_json("/api/staff/messages/preview", body)).json()
        self.assertEqual(p["count"], opted)  # never the family who didn't opt in
        ok(staff.post_json("/api/staff/messages/send", dict(body, expected=opted)))
        outbox.send_due()
        msg = [m for m in mail.SENT if m["Subject"] == "Summer news" and "yes@example.org" in m["To"]][-1]
        self.assertFalse([m for m in mail.SENT if m["Subject"] == "Summer news" and fam.email in m["To"]])
        self.assertIn("yes@example.org", msg["To"])
        self.assertEqual(msg["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        url = re.search(r"<(.+)>", msg["List-Unsubscribe"]).group(1)
        path = "/" + url.split("/", 3)[3]
        # a GET only shows a page; the one-click POST unsubscribes (no Origin, like a mail program)
        self.assertEqual(c.get(path).status, 200)
        self.assertEqual(ok(staff.post_json("/api/staff/messages/preview", body)).json()["count"], opted)
        r = c.request("POST", path, b"List-Unsubscribe=One-Click",
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, origin=False)
        self.assertEqual(r.status, 200)
        self.assertEqual(ok(staff.post_json("/api/staff/messages/preview", body)).json()["count"], opted - 1)
        self.assertEqual(c.request("POST", path[:-3] + "xyz", b"", origin=False).status, 400)  # forged token
        # managers without the marketing permission can't send news
        self.assertEqual(self.admin(roles=("session_staff",)).post_json("/api/staff/messages/preview", body).status, 403)

    def test_account_preferences(self):
        fam = register_family()
        self.assertFalse(ok(fam.get("/api/account/preferences")).json()["email_news"])
        ok(fam.post_json("/api/account/preferences", {"email_news": True, "sms_news": True}))
        p = ok(fam.get("/api/account/preferences")).json()
        self.assertTrue(p["email_news"] and p["sms_news"])
        ok(fam.post_json("/api/account/preferences", {"email_news": False}))
        self.assertFalse(ok(fam.get("/api/account/preferences")).json()["email_news"])

    def test_legacy_subscribers_are_migrated_once(self):
        with open(data_path("subscribers.json"), "w") as f:
            json.dump([{"email": "old@example.org", "name": "Old Fan", "date": "2024-05-01"}, {"email": "bad"}], f)
        self.assertEqual(marketing.migrate_subscribers_json(), 1)
        self.assertFalse(os.path.exists(data_path("subscribers.json")))
        with db.read() as c:
            r = c.execute("SELECT * FROM marketing_preferences WHERE email='old@example.org'").fetchone()
            self.assertEqual((r["source"], r["email_opt_in_at"][:10]), ("legacy_newsletter", "2024-05-01"))
        self.assertEqual(marketing.migrate_subscribers_json(), 0)


class GdprTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True, pay_later_for_all=True)
        ratelimit.reset()

    def test_delete_account_then_erase_keeps_only_what_the_law_needs(self):
        fam = register_family()
        child = complete_child(fam)
        pid = participant_id(child)
        aid, sids = make_activity(price=1000, allow_pay_later=1, sessions=2, first_day=20)
        ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sids[0], "participant": child}],
                                               "pay_mode": "pay_later", "accept_terms": True,
                                               "idempotency_key": uuid.uuid4().hex}))
        # needs the password again (once the sign-in is more than 10 minutes old)
        with db.tx() as c:
            c.execute("UPDATE account_sessions SET reauth_at='2000-01-01T00:00:00Z'")
        r = fam.post_json("/api/account/delete", {})
        self.assertEqual(r.status, 403)
        self.assertTrue(r.json()["reauth"])
        ok(fam.post_json("/api/account/reauth", {"password": "our family passphrase"}))
        r = ok(fam.post_json("/api/account/delete", {})).json()
        self.assertIn("erase_on", r)
        self.assertEqual(fam.get("/api/account/me").status, 401)  # signed out
        self.assertIn("will be deleted", last_email_to(fam.email).lower() + " will be deleted")
        with db.read() as c:
            a = c.execute("SELECT * FROM accounts WHERE email=?", (fam.email,)).fetchone()
            self.assertEqual(a["status"], "closed")
            self.assertEqual(c.execute("SELECT status FROM bookings WHERE session_id=?", (sids[0],)).fetchone()[0],
                             "cancelled")
        # staff can export it during the cooling-off
        dsl = self.admin(roles=("dsl",))
        exp = dsl.get("/api/staff/people/accounts/%s/export" % a["ref"])
        self.assertEqual(exp.status, 200)
        data = exp.json()
        self.assertEqual(data["people"][0]["health"][0]["allergies"], "Peanuts")
        self.assertNotIn("password_hash", json.dumps(data))
        # an accident record means the child's name is kept (until 25); everything else goes
        staff = self.admin(roles=("session_staff",))
        with db.read() as c:
            bid = c.execute("SELECT id FROM bookings WHERE participant_id=? LIMIT 1", (pid,)).fetchone()[0]
        with db.tx() as c:
            c.execute("UPDATE bookings SET status='confirmed' WHERE id=?", (bid,))
        ok(staff.post_json("/api/staff/incidents", {"kind": "injury", "occurred_at_local": "2026-01-01T10:00",
                                                    "description": "Bumped head", "notify_mode": "at_collection",
                                                    "people": [{"booking_id": bid}]}))
        with db.tx() as c:
            c.execute("UPDATE accounts SET erase_after='2000-01-01T00:00:00Z' WHERE id=?", (a["id"],))
        self.assertIn("erased 1", gdpr.retention_job())
        with db.read() as c:
            a2 = c.execute("SELECT * FROM accounts WHERE id=?", (a["id"],)).fetchone()
            self.assertEqual((a2["status"], a2["email"], a2["mobile"]), ("anonymised", None, None))
            p = c.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
            self.assertEqual(p["status"], "retention_hold")
            self.assertFalse(c.execute("SELECT 1 FROM participant_health WHERE participant_id=?", (pid,)).fetchone())
            self.assertFalse(c.execute("SELECT 1 FROM emergency_contacts WHERE account_id=?", (a["id"],)).fetchone())
            self.assertTrue(c.execute("SELECT 1 FROM invoices WHERE account_id=?", (a["id"],)).fetchone())
            texts = " ".join(r[0] for r in c.execute("SELECT body_text FROM message_deliveries WHERE account_id=?",
                                                     (a["id"],)))
            self.assertNotIn("Sarah", texts)

    def test_unverified_signups_are_purged(self):
        c = self.client()
        ok(c.post_json("/api/account/register", {"kind": "family", "first_name": "Una", "last_name": "Verified",
                                                 "email": "unverified@example.org", "mobile": "07700900123",
                                                 "postcode": "BH1 1AA", "password": "a good long passphrase"}))
        with db.tx() as db_c:
            db_c.execute("UPDATE accounts SET created_at='2000-01-01T00:00:00Z' WHERE email='unverified@example.org'")
        gdpr.retention_job()
        with db.read() as db_c:
            self.assertFalse(db_c.execute("SELECT 1 FROM accounts WHERE email='unverified@example.org'").fetchone())

    def test_data_request_and_restore(self):
        fam = register_family()
        ok(fam.post_json("/api/account/data-request", {}))
        owner = self.admin()
        self.assertIn("data_request", [i["type"] for i in ok(owner.get("/api/staff/intray")).json()["items"]])
        ok(fam.post_json("/api/account/reauth", {"password": "our family passphrase"}))
        ok(fam.post_json("/api/account/delete", {}))
        with db.read() as c:
            ref = c.execute("SELECT ref FROM accounts WHERE email=?", (fam.email,)).fetchone()[0]
        self.assertEqual(self.admin(roles=("manager",)).post_json("/api/staff/people/accounts/%s/restore" % ref, {}).status, 403)
        ok(owner.post_json("/api/staff/people/accounts/%s/restore" % ref, {}))
        with db.read() as c:
            self.assertEqual(c.execute("SELECT status FROM accounts WHERE ref=?", (ref,)).fetchone()[0], "active")
