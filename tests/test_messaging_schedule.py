"""Scheduled messages, text-message cost estimates and age-band audiences."""
import datetime
import json
import unittest
import uuid

from hah import catalogue, db, mail, messaging, outbox, ratelimit
from tests.booking_helpers import make_activity, set_settings
from tests.family_helpers import complete_child, ok, register_family
from tests.support import ServerTestCase


def book(fam, child, sid):
    ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": child}],
                                           "accept_terms": True, "idempotency_key": uuid.uuid4().hex}))


def local(days=0, minutes=0):
    t = catalogue.uk_now() + datetime.timedelta(days=days, minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M")


class SmsCountTest(unittest.TestCase):
    def test_segments(self):
        self.assertEqual(messaging.sms_info("a" * 160)["segments"], 1)
        self.assertEqual(messaging.sms_info("a" * 161)["segments"], 2)
        self.assertEqual(messaging.sms_info("€" * 80)["segments"], 1)  # € counts as two
        self.assertEqual(messaging.sms_info("€" * 81)["segments"], 2)
        info = messaging.sms_info("Don’t forget")
        self.assertEqual((info["unicode"], info["odd"]), (True, ["’"]))
        self.assertEqual(messaging.sms_info("’" + "a" * 70)["segments"], 2)


class ScheduleTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True, sms_segment_pence=5)
        ratelimit.reset()

    def test_scheduled_message_goes_to_whoever_matches_then(self):
        aid, (sid,) = make_activity(sessions=1)
        first = register_family()
        book(first, complete_child(first), sid)
        staff = self.admin(roles=("manager",))
        body = {"kind": "service", "channel": "email", "subject": "Bring an apron", "body": "Hi {{first_name}}",
                "audience": {"type": "session", "session_id": sid}}
        r = staff.post_json("/api/staff/messages/send", dict(body, expected=1, send_at_local=local(minutes=-5)))
        self.assertEqual(r.status, 422)
        self.assertIn("send_at_local", r.json()["errors"])
        self.assertEqual(staff.post_json("/api/staff/messages/send", dict(body, expected=1, send_at_local=local(days=120))).status, 422)
        r = ok(staff.post_json("/api/staff/messages/send", dict(body, expected=1, send_at_local=local(days=1)))).json()
        self.assertTrue(r["scheduled"])
        cid = r["campaign_id"]
        with db.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM message_deliveries WHERE campaign_id=?", (cid,)).fetchone()[0], 0)
        camp = ok(staff.get("/api/staff/messages")).json()["campaigns"][0]
        self.assertEqual((camp["id"], camp["state"]), (cid, "scheduled"))
        self.assertIsNone(messaging.send_scheduled())  # not due yet
        # someone else books before it goes
        second = register_family()
        book(second, complete_child(second, first_name="Leo"), sid)
        with db.tx() as c:
            c.execute("UPDATE campaigns SET send_at='2000-01-01T00:00:00Z' WHERE id=?", (cid,))
        self.assertIn("sent 1", messaging.send_scheduled())
        outbox.send_due()
        to = [m["To"] for m in mail.SENT if m["Subject"] == "Bring an apron"]
        self.assertTrue(any(first.email in t for t in to) and any(second.email in t for t in to))
        camp = [x for x in ok(staff.get("/api/staff/messages")).json()["campaigns"] if x["id"] == cid][0]
        self.assertEqual((camp["state"], camp["recipients"]), ("sent", 2))
        self.assertEqual(staff.post_json("/api/staff/messages/%d/cancel" % cid, {}).status, 400)  # already sent
        # cancelling
        r = ok(staff.post_json("/api/staff/messages/send", dict(body, expected=2, subject="Never sent",
                                                                send_at_local=local(days=2)))).json()
        ok(staff.post_json("/api/staff/messages/%d/cancel" % r["campaign_id"], {}))
        with db.tx() as c:
            c.execute("UPDATE campaigns SET send_at='2000-01-01T00:00:00Z' WHERE id=?", (r["campaign_id"],))
        self.assertIsNone(messaging.send_scheduled())
        # the audience no longer exists when it's due: not sent, and staff are told
        r = ok(staff.post_json("/api/staff/messages/send", dict(body, expected=2, send_at_local=local(days=2)))).json()
        with db.tx() as c:
            c.execute("UPDATE campaigns SET send_at='2000-01-01T00:00:00Z', audience=? WHERE id=?",
                      (json.dumps({"type": "session", "session_id": 999999}), r["campaign_id"]))
        messaging.send_scheduled()
        with db.read() as c:
            self.assertEqual(c.execute("SELECT status FROM campaigns WHERE id=?", (r["campaign_id"],)).fetchone()[0], "cancelled")
            self.assertTrue(c.execute("SELECT 1 FROM intray_items WHERE type='message_failed'").fetchone())

    def test_sms_cost_estimate(self):
        aid, (sid,) = make_activity(sessions=1)
        fam = register_family()
        book(fam, complete_child(fam), sid)
        staff = self.admin(roles=("manager",))
        p = ok(staff.post_json("/api/staff/messages/preview", {
            "kind": "service", "channel": "sms", "body": "Don’t forget your apron, {{first_name}}",
            "audience": {"type": "session", "session_id": sid}})).json()
        self.assertEqual((p["count"], p["segments"], p["texts"], p["cost_pence"]), (1, 1, 1, 5))
        self.assertTrue(p["unicode"])
        self.assertEqual(p["odd"], ["’"])

    def test_age_band_audiences(self):
        aid, (sid,) = make_activity(sessions=1, min_age=0, max_age=1200)
        fam = register_family()
        book(fam, complete_child(fam), sid)  # born 2018-05-10
        age = catalogue.months_between(datetime.date(2018, 5, 10), catalogue.uk_today()) // 12
        staff = self.admin()
        base = {"kind": "service", "channel": "email", "subject": "Hello", "body": "Hi"}
        count = lambda aud: ok(staff.post_json("/api/staff/messages/preview", dict(base, audience=aud))).json()["count"]
        self.assertEqual(count({"type": "age", "min_age": age, "max_age": age}), 1)
        self.assertEqual(count({"type": "age", "min_age": age + 1, "max_age": 17}), 0)
        self.assertEqual(count({"type": "age", "min_age": 0, "max_age": age - 1}), 0)
        self.assertEqual(staff.post_json("/api/staff/messages/preview", dict(base, audience={"type": "age", "min_age": 9, "max_age": 3})).status, 422)
        # news: only opted-in families with a child that age
        ok(fam.post_json("/api/account/preferences", {"email_news": True}))
        ok(self.client().post_json("/api/newsletter", {"email": "fan@example.org", "name": "Fan"}))
        news = {"kind": "marketing", "channel": "email", "subject": "Teen workshop", "body": "Hi"}
        everyone = ok(staff.post_json("/api/staff/messages/preview", dict(news, audience={"type": "newsletter"}))).json()["count"]
        p = ok(staff.post_json("/api/staff/messages/preview", dict(news, audience={"type": "newsletter", "min_age": age, "max_age": age}))).json()
        self.assertGreaterEqual(everyone, 2)
        self.assertEqual(p["count"], 1)
        self.assertIn("aged", p["label"])
        self.assertEqual(ok(staff.post_json("/api/staff/messages/preview", dict(
            news, audience={"type": "newsletter", "min_age": 16, "max_age": ""}))).json()["count"], 0)
