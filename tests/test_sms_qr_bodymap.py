"""Twilio delivery reports and STOP replies, the 2FA QR code, and the injury body map."""
import base64
import hashlib
import hmac
import os
import unittest
import urllib.parse

from hah import db, outbox, qr, ratelimit, sms
from tests.booking_helpers import future, set_settings
from tests.family_helpers import complete_child, ok, register_family
from tests.support import ServerTestCase
from tests.test_registers import book_today

TOKEN = "twilio-test-token"


class QrTest(unittest.TestCase):
    def test_structure(self):
        m = qr.matrix("otpauth://totp/Honeycombe%20Arts%20Hub:a%40b.org?secret=JBSWY3DPEHPK3PXP&issuer=Honeycombe")
        n = len(m)
        self.assertEqual((n - 17) % 4, 0)
        for r0, c0 in ((0, 0), (0, n - 7), (n - 7, 0)):  # the three finder patterns
            self.assertTrue(all(m[r0][c0 + i] for i in range(7)))
            self.assertTrue(all(m[r0 + i][c0] for i in range(7)))
            self.assertFalse(m[r0 + 1][c0 + 1])
            self.assertTrue(m[r0 + 3][c0 + 3])
        self.assertEqual([m[6][i] for i in range(8, n - 8)], [i % 2 == 0 for i in range(8, n - 8)])  # timing
        self.assertTrue(m[n - 8][8])  # the dark module
        # known answer: version 1-M "hi" with mask 0, checked against another encoder
        self.assertEqual("".join("1" if x else "0" for x in qr.matrix("hi", mask=0)[9]), "101100000011010101111")
        svg = qr.svg("hello")
        self.assertTrue(svg.startswith("<svg") and "<path" in svg)
        with self.assertRaises(ValueError):
            qr.matrix("x" * 300)


def sign(url, params):
    payload = url + "".join(k + v for k, v in sorted(params.items()))
    return base64.b64encode(hmac.new(TOKEN.encode(), payload.encode(), hashlib.sha1).digest()).decode()


class TwilioHookTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()
        set_settings(booking_live=True)
        self._env = os.environ.get("TWILIO_AUTH_TOKEN")
        os.environ["TWILIO_AUTH_TOKEN"] = TOKEN

    def tearDown(self):
        if self._env is None:
            os.environ.pop("TWILIO_AUTH_TOKEN", None)
        else:
            os.environ["TWILIO_AUTH_TOKEN"] = self._env

    def post(self, path, params, signature=None):
        from hah import config
        c = self.client()
        url = (config.SITE_URL or c.origin) + path  # the address Twilio called, as the server sees it
        return c.request("POST", path, urllib.parse.urlencode(params).encode(), origin=False,
                         headers={"Content-Type": "application/x-www-form-urlencoded",
                                  "X-Twilio-Signature": signature if signature is not None else sign(url, params)})

    def test_delivery_report_and_stop(self):
        with db.tx() as c:
            did = outbox.text_message(c, "07700 900111", None, body="Hello")
            c.execute("UPDATE message_deliveries SET status='sent', provider_id='SM123' WHERE id=?", (did,))
            c.execute("INSERT INTO marketing_preferences(email, phone, email_opt_in, sms_opt_in, sms_opt_in_at,"
                      " wording_version, source, created_at, updated_at) VALUES ('t@example.org', '07700900111', 1, 1,"
                      " 'x', 'v1', 'staff', 'x', 'x')")
        params = {"MessageSid": "SM123", "MessageStatus": "delivered", "To": "+447700900111"}
        self.assertEqual(self.post("/api/twilio/status", params, signature="forged").status, 403)
        self.assertEqual(self.post("/api/twilio/status", params).status, 204)
        with db.read() as c:
            r = c.execute("SELECT delivery_status, delivered_at FROM message_deliveries WHERE id=?", (did,)).fetchone()
        self.assertEqual(r["delivery_status"], "delivered")
        self.assertTrue(r["delivered_at"])
        # they reply STOP: no more texts of any kind, and news texts are off
        r = self.post("/api/twilio/inbound", {"From": "+447700900111", "Body": "Stop", "MessageSid": "SM9"})
        self.assertEqual(r.status, 200)
        self.assertIn(b"<Response>", r.body)
        with db.tx() as c:
            again = outbox.text_message(c, "07700900111", None, body="Reminder")
        with db.read() as c:
            self.assertEqual(c.execute("SELECT status FROM message_deliveries WHERE id=?", (again,)).fetchone()[0],
                             "suppressed")
            self.assertEqual(c.execute("SELECT sms_opt_in FROM marketing_preferences WHERE email='t@example.org'")
                             .fetchone()[0], 0)
        # START lets service texts through again
        self.post("/api/twilio/inbound", {"From": "+447700900111", "Body": "START"})
        with db.tx() as c:
            third = outbox.text_message(c, "07700900111", None, body="Reminder")
        with db.read() as c:
            self.assertEqual(c.execute("SELECT status FROM message_deliveries WHERE id=?", (third,)).fetchone()[0], "queued")
        # an ordinary reply changes nothing
        ok(self.post("/api/twilio/inbound", {"From": "+447700900111", "Body": "Thanks, see you Tuesday"}))
        # no auth token configured: everything is refused
        os.environ.pop("TWILIO_AUTH_TOKEN")
        self.assertEqual(self.post("/api/twilio/status", params).status, 403)

    def test_status_callback_url_only_on_https(self):
        from hah import config
        old = config.SITE_URL
        try:
            config.SITE_URL = "http://localhost:8000"
            self.assertIsNone(sms.status_callback_url())
            config.SITE_URL = "https://www.honeycombeartshub.org.uk"
            self.assertEqual(sms.status_callback_url(), "https://www.honeycombeartshub.org.uk/api/twilio/status")
        finally:
            config.SITE_URL = old


class BodyMapTest(ServerTestCase):
    def test_marks_saved_cleaned_and_shown_to_the_family(self):
        ratelimit.reset()
        set_settings(booking_live=True)
        fam = register_family()
        child = complete_child(fam)
        sid, bid = book_today(fam, child)
        staff = self.admin(roles=("manager",))
        marks = [{"view": "front", "x": 30.26, "y": 80, "note": "Grazed knee"}, {"view": "side", "x": 1, "y": 1},
                 {"view": "back", "x": 150, "y": 5}, {"view": "back", "x": "a", "y": 2}] + \
                [{"view": "front", "x": 50, "y": 50}] * 20
        r = ok(staff.post_json("/api/staff/incidents", {
            "kind": "injury", "occurred_at_local": future(0) + "T11:15", "session_id": sid, "description": "Fell over",
            "action_taken": "Plaster", "first_aid_given": True, "notify_mode": "now", "body_map": marks,
            "people": [{"booking_id": bid, "role": "injured"}]})).json()
        iid = r["incident"]["id"] if "incident" in r else r["id"]
        got = ok(staff.get("/api/staff/incidents/%d" % iid)).json()["incident"]["body_map"]
        self.assertEqual(len(got), 12)  # bad marks dropped, capped at 12
        self.assertEqual(got[0], {"view": "front", "x": 30.3, "y": 80.0, "note": "Grazed knee"})
        mine = ok(fam.get("/api/account/incidents")).json()["incidents"]
        self.assertEqual(mine[0]["body_map"][0]["note"], "Grazed knee")
        ok(staff.post_json("/api/staff/incidents/%d/update" % iid, {"body_map": []}))
        self.assertEqual(ok(staff.get("/api/staff/incidents/%d" % iid)).json()["incident"]["body_map"], [])
