"""Family profile edge cases: what extra carers see, saving a section at a
lower level, going home alone, a child's age on editing, and HAF decisions."""
import re

from hah import db, mail, outbox, ratelimit, sms
from tests.family_helpers import PASSWORD, complete_child, last_email_to, ok, participant_id, register_family
from tests.support import Client, ServerTestCase


def open_intray(pid, type_):
    with db.read() as c:
        return c.execute("SELECT COUNT(*) FROM intray_items WHERE type=? AND participant_id=? AND status<>'done'",
                         (type_, pid)).fetchone()[0]


class CarerViewTest(ServerTestCase):
    def test_carers_dont_see_family_information(self):
        ratelimit.reset()
        fam = register_family()
        child = complete_child(fam)
        ok(fam.post_json("/api/account/participants/%s/safeguarding" % child,
                         {"family_info": "Court order in place", "collection_alert": "Mr X"}))
        self.assertEqual(ok(fam.get("/api/account/participants/" + child)).json()["safeguarding"]["family_info"],
                         "Court order in place")
        email = "carer-%s@example.org" % child.lower()
        ok(fam.post_json("/api/account/carers", {"first_name": "Gran", "last_name": "Smith", "email": email}))
        token = re.search(r"carer-invite#t=([\w-]+)", last_email_to(email)).group(1)
        gran = Client()
        gran.csrf = ok(gran.post_json("/api/account/carer-invite/accept", {"token": token, "password": "sunny orchard teapot",
                                                                            "agree": True})).json()["csrf"]
        d = ok(gran.get("/api/account/participants/" + child))
        self.assertIsNone(d.json()["safeguarding"])
        self.assertNotIn("Court order", d.text)
        self.assertNotIn("Mr X", d.text)


class SectionsTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()

    def test_saving_health_at_the_short_level_keeps_the_rest(self):
        fam = register_family()
        ref = complete_child(fam)
        ok(fam.post_json("/api/account/participants/%s/health" % ref, {
            "allergies": "Peanuts", "send_needs": "Autism: needs a quiet space", "semh_needs": "Anxiety",
            "religious_requirements": "Halal"}))
        ok(fam.post_json("/api/account/participants/%s/target" % ref, {"target_level": "short"}))
        ok(fam.post_json("/api/account/participants/%s/health" % ref, {"allergies": "Peanuts and sesame"}))
        with db.read() as c:
            h = c.execute("SELECT * FROM participant_health WHERE participant_id=?", (participant_id(ref),)).fetchone()
            p = c.execute("SELECT f_send, f_semh, f_religious, f_allergy FROM participants WHERE ref=?", (ref,)).fetchone()
        self.assertEqual((h["allergies"], h["send_needs"], h["semh_needs"], h["religious_requirements"]),
                         ("Peanuts and sesame", "Autism: needs a quiet space", "Anxiety", "Halal"))
        self.assertEqual(tuple(p), (1, 1, 1, 1))

    def test_a_first_yes_to_going_home_alone_needs_the_password_and_is_told(self):
        fam = register_family()
        ref = complete_child(fam, dob="2014-01-01")  # 12: asked, and answered "no"
        answers = {"photo": "internal", "first_aid": "yes", "plasters": "yes", "emergency_treatment": "yes"}
        # a first answer of "no" is not a change to how they get home
        other = complete_child(fam, first_name="Sam", dob="2018-01-01")
        with db.tx() as c:
            c.execute("UPDATE participants SET dob='2014-06-01' WHERE ref=?", (other,))  # had a birthday: now asked
            c.execute("UPDATE account_sessions SET reauth_at='2000-01-01T00:00:00Z'")
        ok(fam.post_json("/api/account/participants/%s/consents" % other, {"answers": dict(answers, go_home_alone="no")}))
        # a first "yes" (no answer before) needs the password, and the family is told
        with db.tx() as c:
            c.execute("DELETE FROM consents WHERE participant_id=? AND consent_type_id IN"
                      " (SELECT id FROM consent_types WHERE key='go_home_alone')", (participant_id(ref),))
        r = fam.post_json("/api/account/participants/%s/consents" % ref, {"answers": dict(answers, go_home_alone="yes")})
        self.assertEqual(r.status, 403)
        self.assertTrue(r.json()["reauth"])
        ok(fam.post_json("/api/account/reauth", {"password": PASSWORD}))
        outbox.send_due()
        n_mail, n_sms = len(mail.SENT), len(sms.SENT)
        ok(fam.post_json("/api/account/participants/%s/consents" % ref, {"answers": dict(answers, go_home_alone="yes")}))
        outbox.send_due()
        self.assertEqual(len(sms.SENT) - n_sms, 1)
        self.assertIn("going home alone", last_email_to(fam.email))
        self.assertGreater(len(mail.SENT), n_mail)
        with db.read() as c:
            self.assertEqual(c.execute("SELECT go_home_alone FROM participants WHERE ref=?", (ref,)).fetchone()[0], 1)

    def test_editing_a_child_checks_they_are_under_18(self):
        fam = register_family()
        ref = complete_child(fam)
        r = fam.post_json("/api/account/participants/%s/child" % ref, {
            "first_name": "Maya", "last_name": "Parent", "dob": "1990-01-01", "education": "school",
            "school_name": "X", "haf_status": "not_eligible"})
        self.assertEqual(r.status, 422)
        self.assertIn("dob", r.json()["errors"])
        with db.read() as c:
            self.assertEqual(c.execute("SELECT dob FROM participants WHERE ref=?", (ref,)).fetchone()[0], "2018-05-10")


class HafDecisionTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()

    def child_body(self, haf):
        return {"first_name": "Maya", "last_name": "Parent", "dob": "2018-05-10", "education": "school",
                "haf_status": haf}

    def haf(self, ref):
        with db.read() as c:
            return c.execute("SELECT haf_status FROM participants WHERE ref=?", (ref,)).fetchone()[0]

    def test_the_familys_own_answer_can_change(self):
        fam = register_family()
        ref = complete_child(fam)  # answered "not eligible" themselves
        ok(fam.post_json("/api/account/participants/%s/child" % ref, self.child_body("claimed_eligible")))
        self.assertEqual(self.haf(ref), "claimed_eligible")

    def test_a_staff_decision_stands_and_a_new_claim_goes_to_staff(self):
        fam = register_family()
        ref = complete_child(fam)
        pid = participant_id(ref)
        ok(fam.post_json("/api/account/participants/%s/child" % ref, self.child_body("claimed_eligible")))
        with db.tx() as c:  # staff decline the claim (as bookings.decline does)
            c.execute("UPDATE participants SET haf_status='not_eligible' WHERE id=?", (pid,))
        self.assertEqual(open_intray(pid, "haf_verify"), 0)
        ok(fam.post_json("/api/account/participants/%s/child" % ref, self.child_body("not_eligible")))
        self.assertEqual(open_intray(pid, "haf_verify"), 0)
        ok(fam.post_json("/api/account/participants/%s/child" % ref, self.child_body("claimed_eligible")))
        self.assertEqual(self.haf(ref), "not_eligible")
        self.assertEqual(open_intray(pid, "haf_verify"), 1)
        # verified stays verified too
        with db.tx() as c:
            c.execute("UPDATE participants SET haf_status='verified' WHERE id=?", (pid,))
        ok(fam.post_json("/api/account/participants/%s/child" % ref, self.child_body("not_sure")))
        self.assertEqual(self.haf(ref), "verified")
