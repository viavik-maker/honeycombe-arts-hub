"""Parent accounts and family profiles: registration with an emailed code,
sign-in, reset, activation of imported families, levels, sections,
consents, collection details and who can see what."""
import re

from hah import db, family, mail, security, sms
from tests.family_helpers import PASSWORD, complete_child, last_email_to, ok, participant_id, register_family
from tests.support import Client, ServerTestCase


def intray_types():
    with db.read() as c:
        return [r[0] for r in c.execute("SELECT type FROM intray_items WHERE status<>'done'")]


class RegistrationTest(ServerTestCase):
    def test_register_with_code_then_signed_in(self):
        c = register_family()
        me = c.get("/api/account/me").json()
        self.assertEqual(me["account"]["status"], "active")
        self.assertEqual(me["participants"], [])
        self.assertFalse(me["acknowledged"])

    def test_code_is_not_stored_and_wrong_codes_fail(self):
        c = Client()
        c.post_json("/api/account/register", {"first_name": "A", "last_name": "B", "email": "codes@example.org",
                                              "mobile": "07700900999", "postcode": "BH1 4SX", "password": PASSWORD})
        body = last_email_to("codes@example.org")
        code = re.search(r"\b(\d{6})\b", body).group(1)
        with db.read() as conn:
            dump = " ".join(str(tuple(r)) for r in conn.execute("SELECT * FROM message_deliveries"))
            dump += " ".join(str(tuple(r)) for r in conn.execute("SELECT * FROM account_tokens"))
        self.assertNotIn(code, dump)
        wrong = "000000" if code != "000000" else "111111"
        self.assertEqual(c.post_json("/api/account/register/verify", {"email": "codes@example.org", "code": wrong}).status, 400)
        self.assertEqual(c.post_json("/api/account/register/verify", {"email": "codes@example.org", "code": code}).status, 200)

    def test_existing_email_gets_the_same_answer(self):
        c = register_family(email="taken@example.org")
        r1 = Client().post_json("/api/account/register", {"first_name": "X", "last_name": "Y", "email": "taken@example.org",
                                                          "mobile": "07700900999", "postcode": "BH1 4SX", "password": PASSWORD})
        r2 = Client().post_json("/api/account/register", {"first_name": "X", "last_name": "Y", "email": "fresh@example.org",
                                                          "mobile": "07700900999", "postcode": "BH1 4SX", "password": PASSWORD})
        self.assertEqual((r1.status, r1.json()), (r2.status, r2.json()))
        self.assertIn("already have one", last_email_to("taken@example.org"))

    def test_validation(self):
        r = Client().post_json("/api/account/register", {"first_name": "", "email": "bad", "mobile": "0120",
                                                         "postcode": "x", "password": "short"})
        self.assertEqual(r.status, 422)
        self.assertTrue({"first_name", "email", "mobile", "postcode", "password"} <= set(r.json()["errors"]))

    def test_adult_registration(self):
        r = Client().post_json("/api/account/register", {"kind": "adult", "first_name": "Kai", "last_name": "Young",
                                                         "email": "kai@example.org", "mobile": "07700900321",
                                                         "postcode": "BH1 4SX", "password": PASSWORD, "dob": "2012-01-01"})
        self.assertEqual(r.status, 422)
        self.assertIn("dob", r.json()["errors"])
        c = register_family(kind="adult", email="kai2@example.org", dob="2004-03-02")
        me = c.get("/api/account/me").json()
        self.assertEqual(len(me["participants"]), 1)
        p = me["participants"][0]
        self.assertTrue(p["is_account_holder"])
        self.assertEqual(p["level"], "none")
        c.post_json("/api/account/contacts", {"contacts": [{"full_name": "Mum", "relationship": "Mother",
                                                            "phone": "07700900111"}]})
        ok(c.post_json("/api/account/participants/%s/consents" % p["ref"], {"answers": {"photo": "online"}}))
        ok(c.post_json("/api/account/acknowledge", {"info_correct": True, "privacy_ack": True}))
        self.assertEqual(c.get("/api/account/me").json()["participants"][0]["level"], "adult")


class SignInTest(ServerTestCase):
    def test_login_logout(self):
        register_family(email="login@example.org")
        c = Client()
        self.assertEqual(c.post_json("/api/account/login", {"email": "login@example.org", "password": "wrong password"}).status, 401)
        r = c.post_json("/api/account/login", {"email": "LOGIN@example.org", "password": PASSWORD})
        self.assertEqual(r.status, 200)
        self.assertIn("hah_acct=", r.header("Set-Cookie"))
        self.assertIn("SameSite=Lax", r.header("Set-Cookie"))
        c.csrf = r.json()["csrf"]
        self.assertEqual(c.get("/api/account/me").status, 200)
        self.assertEqual(c.post_json("/api/account/logout", {}).status, 200)
        self.assertEqual(c.get("/api/account/me").status, 401)

    def test_forgot_and_reset(self):
        old = register_family(email="forgot@example.org")
        r = Client().post_json("/api/account/password/forgot", {"email": "forgot@example.org"})
        self.assertEqual(r.status, 200)
        token = re.search(r"reset-password#t=([\w-]+)", last_email_to("forgot@example.org")).group(1)
        c = Client()
        self.assertEqual(c.post_json("/api/account/password/reset", {"token": token, "password": "short"}).status, 422)
        r = c.post_json("/api/account/password/reset", {"token": token, "password": "a brand new family pass"})
        self.assertEqual(r.status, 200)
        self.assertEqual(old.get("/api/account/me").status, 401)  # other sessions ended
        self.assertEqual(Client().post_json("/api/account/password/reset", {"token": token, "password": "again another pass"}).status, 400)

    def test_forgot_is_neutral(self):
        a = Client().post_json("/api/account/password/forgot", {"email": "nobody@example.org"})
        register_family(email="somebody@example.org")
        b = Client().post_json("/api/account/password/forgot", {"email": "somebody@example.org"})
        self.assertEqual(a.json(), b.json())


class ActivationTest(ServerTestCase):
    def make_imported(self, email):
        with db.tx() as c:
            aid = c.execute("INSERT INTO accounts(ref, kind, email, status, first_name, last_name, postcode, source,"
                            " created_at, updated_at) VALUES (?, 'family', ?, 'pending_activation', 'Ann', 'Import',"
                            " 'BH1 4SX', 'import', ?, ?)", (family.new_ref("A"), email, db.now(), db.now())).lastrowid
            c.execute("INSERT INTO participants(ref, account_id, first_name, last_name, dob, needs_review, created_at,"
                      " updated_at) VALUES (?,?, 'Leo', 'Import', '2015-02-03', 1, ?, ?)",
                      (family.new_ref("P"), aid, db.now(), db.now()))
        return aid

    def test_activate_with_child_dob(self):
        self.make_imported("imported@example.org")
        Client().post_json("/api/account/password/forgot", {"email": "imported@example.org"})
        body = last_email_to("imported@example.org")
        self.assertNotIn("Leo", body)  # activation emails never name children
        token = re.search(r"activate#t=([\w-]+)", body).group(1)
        c = Client()
        self.assertEqual(c.post_json("/api/account/activate/check", {"token": token}).json()["check"], "dob")
        r = c.post_json("/api/account/activate", {"token": token, "child_dob": "2015-02-04", "password": "a good family pass"})
        self.assertEqual(r.status, 400)
        r = c.post_json("/api/account/activate", {"token": token, "child_dob": "2015-02-03", "password": "a good family pass"})
        self.assertEqual(r.status, 200, r.text)
        c.csrf = r.json()["csrf"]
        me = c.get("/api/account/me").json()
        self.assertEqual(me["account"]["status"], "active")
        self.assertIn("review", me["participants"][0]["missing"])

    def test_link_locks_after_five_wrong_answers(self):
        self.make_imported("locked@example.org")
        Client().post_json("/api/account/password/forgot", {"email": "locked@example.org"})
        token = re.search(r"activate#t=([\w-]+)", last_email_to("locked@example.org")).group(1)
        for _ in range(5):
            Client().post_json("/api/account/activate", {"token": token, "child_dob": "2000-01-01", "password": "a good family pass"})
        r = Client().post_json("/api/account/activate", {"token": token, "child_dob": "2015-02-03", "password": "a good family pass"})
        self.assertEqual(r.status, 400)
        self.assertIn("activation_problem", intray_types())


class FamilyProfileTest(ServerTestCase):
    def test_levels(self):
        c = register_family()
        r = c.post_json("/api/account/participants", {"first_name": "Tia", "last_name": "P", "dob": "2024-01-01",
                                                      "target_level": "short"})
        ref = r.json()["ref"]
        p = c.get("/api/account/me").json()["participants"][0]
        self.assertEqual(p["level"], "none")
        self.assertEqual(set(p["missing"]), {"health", "consents", "confirm"})
        ok(c.post_json("/api/account/participants/%s/health" % ref, {}))  # blank = nothing to tell us
        ok(c.post_json("/api/account/participants/%s/consents" % ref, {"answers": {"photo": "online"}}))
        ok(c.post_json("/api/account/acknowledge", {"info_correct": True, "privacy_ack": True}))
        self.assertEqual(c.get("/api/account/me").json()["participants"][0]["level"], "short")
        full_ref = complete_child(c, "Maya", "2018-05-10", "full")
        people = {p["ref"]: p for p in c.get("/api/account/me").json()["participants"]}
        self.assertEqual(people[full_ref]["level"], "full")
        self.assertEqual(people[full_ref]["missing"], [])
        self.assertTrue(family.meets("full", "short") and not family.meets("short", "full"))

    def test_other_families_are_invisible(self):
        a, b = register_family(), register_family()
        ref = complete_child(a, "Maya", "2018-05-10", "full")
        self.assertEqual(b.get("/api/account/participants/%s" % ref).status, 404)
        self.assertEqual(b.post_json("/api/account/participants/%s/health" % ref, {"allergies": "x"}).status, 404)
        self.assertEqual(b.post_json("/api/account/participants/%s/archive" % ref, {}).status, 404)
        self.assertEqual(a.get("/api/account/participants/%s" % ref).status, 200)

    def test_parents_cannot_mark_haf_verified_or_set_hidden_fields(self):
        c = register_family()
        ref = complete_child(c)
        r = c.post_json("/api/account/participants/%s/child" % ref, {
            "first_name": "Maya", "last_name": "Parent", "dob": "2018-05-10", "education": "school",
            "haf_status": "verified", "needs_review": 0, "level": "full", "f_safeguarding": 0})
        self.assertEqual(r.status, 422)
        r = c.post_json("/api/account/participants/%s/child" % ref, {
            "first_name": "Maya", "last_name": "Parent", "dob": "2018-05-10", "education": "school",
            "haf_status": "not_sure"})
        self.assertEqual(r.status, 200)
        self.assertIn("haf_verify", intray_types())

    def test_consents_are_versioned_and_append_only(self):
        c = register_family()
        ref = complete_child(c)
        ok(c.post_json("/api/account/participants/%s/consents" % ref, {"answers": {
            "photo": "online", "first_aid": "yes", "plasters": "no", "emergency_treatment": "yes", "go_home_alone": "no"}}))
        pid = participant_id(ref)
        with db.read() as conn:
            rows = conn.execute("SELECT value, superseded_at FROM consents WHERE participant_id=? AND consent_type_id IN"
                                " (SELECT id FROM consent_types WHERE key='photo') ORDER BY id", (pid,)).fetchall()
            self.assertEqual([r[0] for r in rows], ["internal", "online"])
            self.assertIsNotNone(rows[0][1])
            self.assertEqual(conn.execute("SELECT photo_consent FROM participants WHERE id=?", (pid,)).fetchone()[0], "online")
        r = c.post_json("/api/account/participants/%s/consents" % ref, {"answers": {"photo": "online"}})
        self.assertEqual(r.status, 422)  # required answers missing

    def test_collection_changes_need_password_and_notify(self):
        c = register_family()
        ref = complete_child(c)
        pid = participant_id(ref)
        with db.read() as conn:
            stored = conn.execute("SELECT collection_pw_hash FROM participants WHERE id=?", (pid,)).fetchone()[0]
        self.assertNotIn("blue tiger", stored)
        self.assertTrue(security.verify_collection_password("Blue  Tiger", stored))
        # the session's re-auth window is from sign-in; age it
        with db.tx() as conn:
            conn.execute("UPDATE account_sessions SET reauth_at='2000-01-01T00:00:00Z'")
        r = c.post_json("/api/account/participants/%s/collection" % ref, {"password": "red lion", "confirm": "red lion"})
        self.assertEqual(r.status, 403)
        self.assertTrue(r.json()["reauth"])
        self.assertEqual(c.post_json("/api/account/reauth", {"password": "nope nope nope"}).status, 400)
        ok(c.post_json("/api/account/reauth", {"password": PASSWORD}))
        sms.SENT.clear()
        ok(c.post_json("/api/account/participants/%s/collection" % ref, {"password": "red lion", "confirm": "red lion"}))
        self.assertIn("collection details for Maya", last_email_to(c.email))
        self.assertTrue(sms.SENT and "collection details" in sms.SENT[-1][1])
        r = c.get("/api/account/participants/%s" % ref).json()
        self.assertEqual(r["collection"], {"set": True})  # never shown back

    def test_safeguarding_info_goes_to_the_dsl(self):
        c = register_family()
        ref = complete_child(c)
        ok(c.post_json("/api/account/participants/%s/safeguarding" % ref, {"family_info": "Court order in place"}))
        self.assertIn("safeguarding_info_submitted", intray_types())
        with db.read() as conn:
            row = conn.execute("SELECT restricted, details FROM audit_log WHERE action='participant.safeguarding_changed'"
                               " ORDER BY id DESC").fetchone()
            self.assertEqual(row[0], 1)
            self.assertNotIn("Court order", row[1] or "")
            f_sg = conn.execute("SELECT f_safeguarding FROM participants WHERE ref=?", (ref,)).fetchone()[0]
        self.assertEqual(f_sg, 0)  # only a DSL sets the flag

    def test_contacts_validation_and_collectors(self):
        c = register_family()
        r = c.post_json("/api/account/contacts", {"contacts": [{"full_name": "", "relationship": "", "phone": "12"}]})
        self.assertEqual(r.status, 422)
        self.assertIn("contacts.0.phone", r.json()["errors"])

    def test_health_flags(self):
        c = register_family()
        ref = complete_child(c)
        with db.read() as conn:
            row = conn.execute("SELECT f_allergy, f_anaphylaxis, f_send FROM participants WHERE ref=?", (ref,)).fetchone()
        self.assertEqual(tuple(row), (1, 1, 0))

    def test_children_over_18_register_themselves(self):
        c = register_family()
        r = c.post_json("/api/account/participants", {"first_name": "Old", "last_name": "P", "dob": "2000-01-01"})
        self.assertEqual(r.status, 422)
