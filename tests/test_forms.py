"""The public contact form and newsletter sign-up, and the admin tools that
manage what they collect."""
from tests.support import ServerTestCase


class ContactFormTest(ServerTestCase):
    def test_message_lands_in_inbox(self):
        r = self.client().post_json("/api/contact", {
            "name": "Sam Parent", "email": "sam@example.com", "phone": "07000 000000",
            "message": "Do you have space on Tuesday?", "website": ""})
        self.assertEqual(r.status, 200, r.text)
        admin = self.admin()
        msgs = admin.get("/api/admin/overview").json()["messages"]
        self.assertEqual(msgs[0]["name"], "Sam Parent")
        self.assertFalse(msgs[0]["read"])
        mid = msgs[0]["id"]

        r = admin.post_json("/api/admin/messages", {"action": "read", "id": mid, "read": True})
        self.assertTrue(r.json()["messages"][0]["read"])
        r = admin.post_json("/api/admin/messages", {"action": "delete", "id": mid})
        self.assertFalse([m for m in r.json()["messages"] if m["id"] == mid])

    def test_missing_fields(self):
        r = self.client().post_json("/api/contact", {"name": "Sam", "email": "nope"})
        self.assertEqual(r.status, 400)
        self.assertIn("error", r.json())

    def test_honeypot_is_silently_dropped(self):
        before = len(self.admin().get("/api/admin/overview").json()["messages"])
        r = self.client().post_json("/api/contact", {
            "name": "Bot", "email": "bot@example.com", "message": "spam", "website": "http://spam"})
        self.assertEqual(r.status, 200)
        after = len(self.admin().get("/api/admin/overview").json()["messages"])
        self.assertEqual(before, after)

    def test_malformed_body(self):
        r = self.client().request("POST", "/api/contact", b"{not json",
                                  headers={"Content-Type": "application/json"})
        self.assertEqual(r.status, 400)


class NewsletterTest(ServerTestCase):
    def test_signup_duplicate_export_and_delete(self):
        c = self.client()
        self.assertEqual(c.post_json("/api/newsletter", {"email": "Fan@Example.com"}).status, 200)
        r = c.post_json("/api/newsletter", {"email": "fan@example.com"})
        self.assertEqual(r.json().get("note"), "already subscribed")

        admin = self.admin()
        subs = admin.get("/api/admin/overview").json()["subscribers"]
        self.assertEqual([s["email"] for s in subs], ["fan@example.com"])
        csv = admin.get("/api/admin/subscribers.csv")
        self.assertEqual(csv.status, 200)
        self.assertIn("attachment", csv.header("Content-Disposition"))
        self.assertTrue(csv.text.startswith("email,name,date,source"))
        self.assertIn("fan@example.com", csv.text)

        r = admin.post_json("/api/admin/subscribers", {"action": "delete", "email": "fan@example.com"})
        self.assertEqual(r.json()["subscribers"], [])

    def test_invalid_email(self):
        self.assertEqual(self.client().post_json("/api/newsletter", {"email": "nope"}).status, 400)
