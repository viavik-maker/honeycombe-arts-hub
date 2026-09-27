"""The outbox: queued with the change that causes it, sent by the worker,
retried on failure; one-time links never stored; email and SMS providers."""
import http.server
import json
import os
import threading
import unittest
import urllib.parse

from hah import db, mail, outbox, sms, templating, validate
from tests.support import ServerTestCase


def deliveries():
    with db.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM message_deliveries ORDER BY id")]


class OutboxTest(ServerTestCase):
    def setUp(self):
        mail.SENT.clear()
        sms.SENT.clear()
        with db.tx() as c:
            c.execute("DELETE FROM message_deliveries")

    def test_rolled_back_change_sends_nothing(self):
        with self.assertRaises(RuntimeError):
            with db.tx() as c:
                outbox.email(c, "parent@example.org", "test_email", {"name": "X"})
                raise RuntimeError("booking failed")
        self.assertEqual(deliveries(), [])

    def test_email_is_sent_by_the_worker(self):
        with db.tx() as c:
            outbox.email(c, "Parent@Example.org", "test_email", {"name": "Poppy <b>"}, to_name="A Parent")
        self.assertEqual(deliveries()[0]["status"], "queued")
        self.assertEqual(outbox.send_due(), "sent 1, failed 0")
        msg = mail.SENT[0]
        self.assertEqual(msg["To"], "A Parent <parent@example.org>")
        self.assertIn("Test email", msg["Subject"])
        html = msg.get_body(("html",)).get_content()
        self.assertIn("Poppy &lt;b&gt;", html)
        self.assertEqual(deliveries()[0]["status"], "sent")
        self.assertIsNone(outbox.send_due())  # nothing left

    def test_one_time_links_are_never_stored(self):
        link = "https://example.org/admin#invite=SECRET-TOKEN-123"
        with db.tx() as c:
            outbox.email(c, "new@example.org", "staff_invite", {"name": "Sam", "inviter": "Poppy", "hours": 72},
                         secret=link, kind="staff")
        row = deliveries()[0]
        self.assertNotIn("SECRET-TOKEN-123", row["body_text"] + row["body_html"])
        self.assertEqual(row["secret"], link)
        outbox.send_due()
        sent = mail.SENT[0]
        self.assertIn(link, sent.get_body(("plain",)).get_content())
        self.assertIn('href="%s"' % link, sent.get_body(("html",)).get_content())
        with db.read() as c:
            dump = "\n".join(str(tuple(r)) for r in c.execute("SELECT * FROM message_deliveries"))
        self.assertNotIn("SECRET-TOKEN-123", dump)  # wiped once sent
        self.assertIn("[one-time link", outbox.archived_body(deliveries()[0]))

    def test_failures_retry_then_give_up(self):
        with db.tx() as c:
            outbox.email(c, "p@example.org", "test_email", {"name": "X"})
        real = mail.Connection.send

        def boom(self, msg):
            raise mail.MailError("server busy")
        mail.Connection.send = boom
        try:
            outbox.send_due()
            row = deliveries()[0]
            self.assertEqual((row["status"], row["attempts"]), ("queued", 1))
            self.assertGreater(row["next_attempt_at"], db.now())  # backs off
            for _ in range(len(outbox.RETRY_AFTER)):
                with db.tx() as c:
                    c.execute("UPDATE message_deliveries SET next_attempt_at=?", (db.now(),))
                outbox.send_due()
            row = deliveries()[0]
            self.assertEqual(row["status"], "failed")
            self.assertIn("server busy", row["error"])
        finally:
            mail.Connection.send = real

    def test_texts_waiting_for_sms_never_hold_up_email(self):
        saved = os.environ.get("SMS_PROVIDER")
        os.environ["SMS_PROVIDER"] = "disabled"  # the default until Twilio is set up
        try:
            with db.tx() as c:
                for i in range(outbox.BATCH + 5):
                    outbox.text_message(c, "07700 900%03d" % i, "test")
                c.execute("UPDATE message_deliveries SET created_at='2000-01-01T00:00:00Z' WHERE to_address=?",
                          ("+447700900000",))
                outbox.email(c, "blocked@example.org", "test_email", {"name": "X"})
            self.assertEqual(outbox.send_due(), "sent 1, failed 0")
            self.assertIn("blocked@example.org", mail.SENT[-1]["To"])
            by = {r["to_address"]: r for r in deliveries()}
            # a text left waiting over a day is dropped rather than sent late; newer ones wait for SMS to be set up
            self.assertEqual(by["+447700900000"]["status"], "cancelled")
            self.assertIn("aren't set up", by["+447700900000"]["error"])
            self.assertEqual(by["+447700900001"]["status"], "queued")
        finally:
            os.environ["SMS_PROVIDER"] = saved
        self.assertEqual(outbox.send_due(), "sent %d, failed 0" % outbox.BATCH)
        self.assertFalse(sms.SENT[0][0].endswith("900000"))

    def test_sms_to_uk_mobiles_only(self):
        with db.tx() as c:
            self.assertIsNone(outbox.text_message(c, "01202 123456", "test"))
            self.assertIsNotNone(outbox.text_message(c, "07700 900123", "test"))
        outbox.send_due()
        self.assertEqual(sms.SENT[0][0], "+447700900123")

    def test_contact_form_notifies_staff_by_email(self):
        r = self.client().post_json("/api/contact", {"name": "Eve", "email": "eve@example.org",
                                                     "message": "Hi [click](https://phish.example)"})
        self.assertEqual(r.status, 200)
        row = deliveries()[-1]
        self.assertEqual((row["template_key"], row["kind"]), ("contact_notification", "staff"))
        self.assertNotIn('href="https://phish.example"', row["body_html"])  # visitor text can't add links
        self.assertEqual(json.loads(row["headers"])["Reply-To"], "eve@example.org")

    def test_staff_invite_is_emailed(self):
        owner = self.admin()
        r = owner.post_json("/api/staff/users/invite", {"name": "Sam", "email": "sam2@example.org",
                                                         "roles": ["session_staff"]}).json()
        self.assertTrue(r["emailed"])
        row = deliveries()[-1]
        self.assertEqual(row["to_address"], "sam2@example.org")
        self.assertNotIn(r["link"].split("=")[1], row["body_text"])

    def test_admin_test_buttons(self):
        admin = self.admin()
        self.assertEqual(admin.post_json("/api/admin/test-email", {}).status, 200)
        self.assertEqual(admin.post_json("/api/admin/test-sms", {"to": "nope"}).status, 400)
        self.assertEqual(admin.post_json("/api/admin/test-sms", {"to": "07700900123"}).status, 200)
        st = admin.get("/api/admin/system-status").json()
        self.assertTrue(st["email"]["configured"])
        self.assertEqual(st["outbox"]["counts"]["email"]["queued"], 1)


class InboxMigrationTest(ServerTestCase):
    def test_messages_json_moves_into_the_database(self):
        from hah import cms, config
        with open(os.path.join(config.DATA, "messages.json"), "w") as f:
            json.dump([{"id": "abc123", "name": "Old Sender", "email": "o@example.org", "phone": "",
                        "message": "From the old inbox", "date": "2026-07-01 10:30", "read": True}], f)
        self.assertEqual(cms.migrate_messages_json(), 1)
        self.assertFalse(os.path.exists(os.path.join(config.DATA, "messages.json")))
        msgs = self.admin().get("/api/admin/overview").json()["messages"]
        old = next(m for m in msgs if m["id"] == "abc123")
        self.assertEqual((old["name"], old["date"], old["read"]), ("Old Sender", "2026-07-01 10:30", True))
        self.assertEqual(cms.migrate_messages_json(), 0)


class TwilioTest(unittest.TestCase):
    def setUp(self):
        self.requests, self.reply = [], (201, {"sid": "SM123"})
        test = self

        class Fake(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                test.requests.append((self.path, self.headers["Authorization"], urllib.parse.parse_qs(body.decode())))
                code, payload = test.reply
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.env = {k: os.environ.get(k) for k in ("SMS_PROVIDER", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
                                                    "TWILIO_FROM", "TWILIO_API_BASE")}
        os.environ.update({"SMS_PROVIDER": "twilio", "TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "tok",
                           "TWILIO_FROM": "HoneycombeH",
                           "TWILIO_API_BASE": "http://127.0.0.1:%d" % self.srv.server_address[1]})

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.srv.shutdown()

    def test_send(self):
        self.assertTrue(sms.configured())
        self.assertEqual(sms.send("+447700900123", "Hello"), "SM123")
        path, auth, form = self.requests[0]
        self.assertEqual(path, "/2010-04-01/Accounts/AC1/Messages.json")
        self.assertTrue(auth.startswith("Basic "))
        self.assertEqual((form["To"], form["From"], form["Body"]), (["+447700900123"], ["HoneycombeH"], ["Hello"]))

    def test_errors(self):
        self.reply = (400, {"code": 21610, "message": "unsubscribed"})
        with self.assertRaises(sms.SmsError) as e:
            sms.send("+447700900123", "x")
        self.assertTrue(e.exception.opted_out and e.exception.permanent)
        self.reply = (503, {"message": "down"})
        with self.assertRaises(sms.SmsError) as e:
            sms.send("+447700900123", "x")
        self.assertFalse(e.exception.permanent)


class ValidateTest(unittest.TestCase):
    def test_phones_and_postcodes(self):
        for raw in ("07700 900123", "+44 7700 900123", "447700900123", "0044 7700-900123", "(07700) 900 123"):
            self.assertEqual(validate.uk_mobile(raw), "+447700900123")
        for bad in ("01202 123456", "0770090012", "+1 555 123 4567", ""):
            self.assertIsNone(validate.uk_mobile(bad))
        self.assertEqual(validate.uk_phone("+44 1202 123456"), "01202123456")
        self.assertEqual(validate.postcode("bh14sx"), "BH1 4SX")
        self.assertIsNone(validate.postcode("not a postcode"))
        self.assertEqual(validate.email(" Sam@Example.ORG "), "sam@example.org")
        self.assertIsNone(validate.email("sam@localhost"))

    def test_template_values_are_plain_text(self):
        _, text, html = templating.render_email("contact_notification",
                                                {"name": "**Bold**", "email": "a@b.co", "phone": "", "message": "x"})
        self.assertIn("**Bold**", html.replace("<strong>**Bold**</strong>", "**Bold**"))
        self.assertNotIn("<strong>Bold</strong>", html)
