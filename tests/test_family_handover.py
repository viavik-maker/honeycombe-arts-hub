"""Handing a child's record to them at 18: what the parent told us in
confidence stays with the DSL, and the answer about the email address
doesn't reveal who has an account."""
import re
import uuid

from hah import db, ratelimit
from tests.booking_helpers import set_settings
from tests.family_helpers import complete_child, last_email_to, ok, participant_id, register_family
from tests.support import Client, ServerTestCase
from tests.test_retention import eighteen_today


class HandoverPrivacyTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()
        set_settings(booking_live=True, turned_18_days=90)

    def adult_child(self, fam, name):
        child = complete_child(fam, first_name=name)
        with db.tx() as c:
            c.execute("UPDATE participants SET dob=? WHERE id=?", (eighteen_today(), participant_id(child)))
        return child

    def test_family_information_stays_with_the_dsl(self):
        fam = register_family()
        child = self.adult_child(fam, "Tia")
        ok(fam.post_json("/api/account/participants/%s/safeguarding" % child,
                         {"family_info": "Court order: Mr X must have no contact", "collection_alert": "Mr X"}))
        email = "tia-%s@example.org" % uuid.uuid4().hex[:8]
        ok(fam.post_json("/api/account/handover/%s/start" % child, {"email": email}))
        token = re.search(r"handover#t=([\w-]+)", last_email_to(email)).group(1)
        tia = Client()
        tia.csrf = ok(tia.post_json("/api/account/handover/accept", {"token": token,
                                                                      "password": "violet kite harbour"})).json()["csrf"]
        d = ok(tia.get("/api/account/participants/" + child))
        self.assertIsNone(d.json()["safeguarding"])
        self.assertNotIn("Mr X", d.text)
        # nor can they write over it
        self.assertEqual(tia.post_json("/api/account/participants/%s/safeguarding" % child, {"family_info": "x"}).status, 404)
        with db.read() as c:
            p = c.execute("SELECT * FROM participants WHERE ref=?", (child,)).fetchone()
            sg = c.execute("SELECT * FROM participant_safeguarding WHERE participant_id=?", (p["id"],)).fetchone()
        self.assertEqual((p["collection_alert"], p["photo_consent"], p["go_home_alone"]), (None, None, None))
        self.assertIsNotNone(sg["handed_over_at"])
        self.assertIn("Court order: Mr X must have no contact", sg["family_info"])
        self.assertIn("Must not collect", sg["family_info"])  # the alert is kept for the DSL, not on their record
        # the DSL still sees it
        dsl = self.admin(roles=("dsl",))
        self.assertIn("Court order", ok(dsl.get("/api/staff/people/participants/%s?tab=safeguarding" % child)).text)

    def test_an_address_with_an_account_gets_the_same_answer(self):
        fam = register_family()
        child = self.adult_child(fam, "Ren")
        taken = register_family()
        self.assertEqual(fam.post_json("/api/account/handover/%s/start" % child, {"email": fam.email}).status, 422)
        a = ok(fam.post_json("/api/account/handover/%s/start" % child, {"email": taken.email}))
        self.assertIn("already signs in to an account here", last_email_to(taken.email))
        b = ok(fam.post_json("/api/account/handover/%s/start" % child,
                             {"email": "ren-%s@example.org" % uuid.uuid4().hex[:8]}))
        self.assertEqual(a.json(), b.json())
        with db.read() as c:
            self.assertFalse(c.execute("SELECT 1 FROM handovers WHERE email=?", (taken.email,)).fetchone())
