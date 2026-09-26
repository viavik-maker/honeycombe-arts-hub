"""Extra carers who sign in to a family's account."""
import re
import uuid

from hah import db, gdpr, ratelimit
from tests.booking_helpers import make_activity, set_settings
from tests.family_helpers import PASSWORD, complete_child, last_email_to, ok, register_family
from tests.support import Client, ServerTestCase

CARER_PW = "sunny orchard teapot"


def invite_link(email):
    return re.search(r"carer-invite#t=([\w-]+)", last_email_to(email)).group(1)


class CarerTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True, pay_later_for_all=True)
        ratelimit.reset()

    def add_carer(self, fam, email="gran@example.org"):
        return fam.post_json("/api/account/carers", {"first_name": "Gran", "last_name": "Smith", "email": email,
                                                     "relationship": "Grandmother"})

    def accept(self, email="gran@example.org"):
        token = invite_link(email)
        gran = Client()
        self.assertEqual(gran.post_json("/api/account/carer-invite/accept", {"token": token, "password": CARER_PW}).status,
                         422)  # must agree to keep things private
        r = ok(gran.post_json("/api/account/carer-invite/accept", {"token": token, "password": CARER_PW, "agree": True}))
        gran.csrf = r.json()["csrf"]
        gran.email = email
        return gran, token

    def test_invite_accept_book_and_limits(self):
        fam = register_family()
        child = complete_child(fam)
        # needs the password again once the sign-in is a few minutes old
        with db.tx() as c:
            c.execute("UPDATE account_sessions SET reauth_at='2000-01-01T00:00:00Z'")
        r = self.add_carer(fam)
        self.assertEqual(r.status, 403)
        ok(fam.post_json("/api/account/reauth", {"password": PASSWORD}))
        self.assertEqual(self.add_carer(fam, email=fam.email).status, 422)  # their own address
        ok(self.add_carer(fam))
        self.assertEqual(self.add_carer(fam).status, 422)  # already a carer somewhere
        other = register_family()
        self.assertEqual(self.add_carer(fam, email=other.email).status, 422)  # has their own account
        check = ok(Client().post_json("/api/account/carer-invite/check", {"token": invite_link("gran@example.org")})).json()
        self.assertEqual(check["first_name"], "Gran")
        gran, token = self.accept()
        self.assertEqual(Client().post_json("/api/account/carer-invite/accept",
                                            {"token": token, "password": CARER_PW, "agree": True}).status, 400)  # used
        self.assertIn("accepted your invitation", last_email_to(fam.email))
        me = ok(gran.get("/api/account/me")).json()
        self.assertEqual(me["carer"]["first_name"], "Gran")
        self.assertEqual(me["participants"][0]["ref"], child)
        self.assertIsNone(ok(fam.get("/api/account/me")).json()["carer"])
        # the carer signs in with their own email and password
        again = Client()
        r = ok(again.post_json("/api/account/login", {"email": "gran@example.org", "password": CARER_PW}))
        again.csrf = r.json()["csrf"]
        self.assertEqual(ok(again.get("/api/account/me")).json()["carer"]["email"], "gran@example.org")
        # they can book, and both get the confirmation
        aid, (sid,) = make_activity(sessions=1, price=1000, allow_pay_later=1)
        ok(gran.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": child}], "pay_mode": "pay_later",
                                                "accept_terms": True, "idempotency_key": uuid.uuid4().hex}))
        self.assertIn("Hello Gran", last_email_to("gran@example.org"))
        self.assertIn(child, [p["ref"] for p in me["participants"]])
        self.assertEqual(len(ok(fam.get("/api/account/bookings")).json()["upcoming"]), 1)
        ok(gran.get("/api/account/participants/" + child))
        # ...but can't change the family, the account, or other carers
        for path, body in (("/api/account/details", {}), ("/api/account/contacts", {"contacts": []}),
                           ("/api/account/participants/%s/health" % child, {}), ("/api/account/participants", {}),
                           ("/api/account/preferences", {"email_news": True}), ("/api/account/delete", {}),
                           ("/api/account/data-request", {}), ("/api/account/carers", {}), ("/api/account/send", {})):
            r = gran.post_json(path, body)
            self.assertEqual(r.status, 403, path)
            self.assertTrue(r.json().get("holder_only"), path)
        self.assertEqual(gran.get("/api/account/data-export").status, 403)
        self.assertEqual(gran.get("/api/account/carers").status, 403)
        # a forgotten password works for carers too
        ok(Client().post_json("/api/account/password/forgot", {"email": "gran@example.org"}))
        reset = re.search(r"reset-password#t=([\w-]+)", last_email_to("gran@example.org")).group(1)
        ok(Client().post_json("/api/account/password/reset", {"token": reset, "password": "a brand new carer phrase"}))
        self.assertEqual(gran.get("/api/account/me").status, 401)  # other sessions signed out
        # the holder removes them: signed out at once, can't sign in again
        new = Client()
        new.csrf = ok(new.post_json("/api/account/login", {"email": "gran@example.org",
                                                           "password": "a brand new carer phrase"})).json()["csrf"]
        carers = ok(fam.get("/api/account/carers")).json()["carers"]
        self.assertEqual(carers[0]["status"], "active")
        ok(fam.post_json("/api/account/carers/%s/remove" % carers[0]["ref"], {}))
        self.assertEqual(new.get("/api/account/me").status, 401)
        self.assertEqual(Client().post_json("/api/account/login", {"email": "gran@example.org",
                                                                    "password": "a brand new carer phrase"}).status, 401)
        self.assertIn("removed you", last_email_to("gran@example.org"))
        # another family can't touch this family's carers
        self.assertEqual(other.post_json("/api/account/carers/%s/remove" % carers[0]["ref"], {}).status, 404)
        with db.read() as c:
            self.assertTrue(c.execute("SELECT 1 FROM audit_log WHERE action='carer.login'").fetchone())

    def test_staff_see_and_remove_and_erasure(self):
        fam = register_family()
        complete_child(fam)
        ok(self.add_carer(fam, email="nana@example.org"))
        gran, _ = self.accept("nana@example.org")
        with db.read() as c:
            ref = c.execute("SELECT ref FROM accounts WHERE email=?", (fam.email,)).fetchone()[0]
        rec = ok(self.admin(roles=("manager",)).get("/api/staff/people/accounts/" + ref)).json()
        self.assertEqual(rec["carers"][0]["email"], "nana@example.org")
        self.assertEqual(self.admin(roles=("session_staff",)).post_json(
            "/api/staff/people/carers/%s/remove" % rec["carers"][0]["ref"], {}).status, 403)
        # the holder's data download lists their carers
        data = ok(fam.get("/api/account/data-export?format=json")).json()
        self.assertEqual(data["carers"][0]["first_name"], "Gran")
        # erasing the family removes the carers' details too
        with db.tx() as c:
            aid = c.execute("SELECT id FROM accounts WHERE email=?", (fam.email,)).fetchone()[0]
            c.execute("UPDATE accounts SET status='closed' WHERE id=?", (aid,))
            gdpr.erase_account(c, aid)
        with db.read() as c:
            r = c.execute("SELECT * FROM carers WHERE account_id=?", (aid,)).fetchone()
        self.assertEqual((r["status"], r["first_name"]), ("removed", "Removed"))
        self.assertNotIn("nana@", r["email"])
        self.assertEqual(gran.get("/api/account/me").status, 401)
