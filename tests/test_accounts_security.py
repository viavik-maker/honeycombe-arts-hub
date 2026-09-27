"""Registration can't be taken over by a second registrant, sign-in and code
checks are slowed per address and per client, imported accounts with nothing
to check aren't activated online, and malformed input gets a 4xx, not a 500."""
import re
import uuid

from hah import db, family, mail, outbox, ratelimit
from tests.family_helpers import PASSWORD, complete_child, last_email_to, ok, register_family
from tests.support import Client, ServerTestCase

ATTACKER_PW = "attacker passphrase 99"


def unique_email(tag):
    return "%s-%s@example.org" % (tag, uuid.uuid4().hex[:8])


def details(email, **kw):
    return dict({"kind": "family", "first_name": "Vic", "last_name": "Tim", "email": email, "mobile": "07700 900111",
                 "postcode": "BH1 4SX", "password": PASSWORD}, **kw)


def codes_to(email):
    outbox.send_due()
    return [re.search(r"\b(\d{6})\b", m.get_body(("plain",)).get_content()).group(1)
            for m in mail.SENT if email in m["To"]]


class RegistrationTakeoverTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()

    def test_second_registrant_never_sets_the_owners_password(self):
        email = unique_email("victim")
        owner = Client()
        ok(owner.post_json("/api/account/register", details(email)))
        ok(Client().post_json("/api/account/register", details(email, first_name="Mallory", password=ATTACKER_PW)))
        # the newest code came from the other attempt; with the owner's password it finishes the owner's
        r = ok(owner.post_json("/api/account/register/verify",
                               {"email": email, "code": codes_to(email)[-1], "password": PASSWORD}))
        owner.csrf = r.json()["csrf"]
        self.assertEqual(ok(owner.get("/api/account/me")).json()["account"]["first_name"], "Vic")
        complete_child(owner)
        self.assertEqual(Client().post_json("/api/account/login", {"email": email, "password": ATTACKER_PW}).status, 401)
        ok(Client().post_json("/api/account/login", {"email": email, "password": PASSWORD}))

    def test_details_come_from_the_attempt_that_finishes(self):
        email = unique_email("second")
        ok(Client().post_json("/api/account/register", details(email, first_name="Mallory", password=ATTACKER_PW)))
        owner = Client()
        ok(owner.post_json("/api/account/register", details(email, kind="adult", first_name="Kai", dob="2000-02-02")))
        with db.read() as c:  # nothing about the account changed while it's unverified
            a = c.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
            self.assertEqual((a["first_name"], a["password_hash"]), ("Mallory", None))
        r = ok(owner.post_json("/api/account/register/verify",
                               {"email": email, "code": codes_to(email)[0], "password": PASSWORD}))
        self.assertEqual(r.json()["kind"], "adult")
        owner.csrf = r.json()["csrf"]
        me = ok(owner.get("/api/account/me")).json()
        self.assertEqual((me["account"]["first_name"], me["account"]["kind"]), ("Kai", "adult"))
        self.assertEqual([(p["first_name"], p["dob"]) for p in me["participants"]], [("Kai", "2000-02-02")])
        with db.read() as c:  # every other code is finished with
            self.assertFalse(c.execute("SELECT 1 FROM account_tokens t JOIN accounts a ON a.id=t.account_id WHERE"
                                       " a.email=? AND t.used_at IS NULL", (email,)).fetchone())

    def test_the_code_needs_the_password(self):
        email = unique_email("needspw")
        c = Client()
        ok(c.post_json("/api/account/register", details(email)))
        code = codes_to(email)[-1]
        for pw in (None, "", ATTACKER_PW, ["x"]):
            self.assertEqual(c.post_json("/api/account/register/verify",
                                         {"email": email, "code": code, "password": pw}).status, 400)
        ok(c.post_json("/api/account/register/verify", {"email": email, "code": code, "password": PASSWORD}))

    def test_resend_needs_the_password_and_answers_the_same(self):
        email = unique_email("resend")
        ok(Client().post_json("/api/account/register", details(email)))
        n = len(codes_to(email))
        wrong = ok(Client().post_json("/api/account/register/resend", {"email": email, "password": ATTACKER_PW}))
        nobody = ok(Client().post_json("/api/account/register/resend", {"email": unique_email("nobody"),
                                                                         "password": PASSWORD}))
        self.assertEqual(len(codes_to(email)), n)  # nobody else can use up the emails we'll send
        right = ok(Client().post_json("/api/account/register/resend", {"email": email, "password": PASSWORD}))
        self.assertEqual(len(codes_to(email)), n + 1)
        self.assertEqual(wrong.json(), right.json())
        self.assertEqual(nobody.json(), right.json())
        # the earlier code still works (a resend doesn't cancel it)
        ok(Client().post_json("/api/account/register/verify",
                              {"email": email, "code": codes_to(email)[0], "password": PASSWORD}))

    def test_signing_in_before_verifying_sends_a_code_for_that_attempt(self):
        email = unique_email("pending")
        ok(Client().post_json("/api/account/register", details(email)))
        ok(Client().post_json("/api/account/register", details(email, first_name="Mallory", password=ATTACKER_PW)))
        n = len(codes_to(email))
        self.assertEqual(ok(Client().post_json("/api/account/login", {"email": email, "password": PASSWORD})).json()["next"],
                         "verify")
        self.assertIn("Hello Vic", last_email_to(email))
        self.assertEqual(len(codes_to(email)), n + 1)
        self.assertEqual(Client().post_json("/api/account/login", {"email": email, "password": "not either one"}).status,
                         401)

    def test_codes_are_limited_per_client(self):
        for _ in range(29):  # as if spread across many addresses
            ratelimit.hit("acct_code_ip", "127.0.0.1")
        email = unique_email("spray")
        ok(Client().post_json("/api/account/register", details(email)))
        self.assertEqual(Client().post_json("/api/account/register/verify", {"email": unique_email("other"),
                                                                             "code": "123456"}).status, 400)
        r = Client().post_json("/api/account/register/verify",
                               {"email": email, "code": codes_to(email)[-1], "password": PASSWORD})
        self.assertEqual(r.status, 429)
        ratelimit.reset()


class SignInSlowDownTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()

    def tearDown(self):
        ratelimit.reset()

    def test_failures_to_one_address_from_anywhere_slow_it_down(self):
        fam = register_family(email=unique_email("target"))
        self.assertEqual(Client().post_json("/api/account/login", {"email": fam.email, "password": "wrong guess"}).status,
                         401)
        for _ in range(19):  # the rest from other places
            ratelimit.hit("acct_login_email", fam.email)
        r = Client().post_json("/api/account/login", {"email": fam.email, "password": PASSWORD})
        self.assertEqual(r.status, 429)
        # an address with no account answers the same way
        nobody = unique_email("nobody")
        for _ in range(20):
            ratelimit.hit("acct_login_email", nobody)
        self.assertEqual(Client().post_json("/api/account/login", {"email": nobody, "password": PASSWORD}).json(),
                         r.json())
        # other addresses are unaffected
        other = register_family(email=unique_email("other"))
        ok(Client().post_json("/api/account/login", {"email": other.email, "password": PASSWORD}))


class ActivationWithNothingToCheckTest(ServerTestCase):
    def test_refused_online_and_staff_are_asked(self):
        email = unique_email("bare")
        with db.tx() as c:
            aid = c.execute("INSERT INTO accounts(ref, kind, email, status, first_name, last_name, source, created_at,"
                            " updated_at) VALUES (?, 'family', ?, 'pending_activation', 'Bo', 'Import', 'import', ?, ?)",
                            (family.new_ref("A"), email, db.now(), db.now())).lastrowid
        ok(Client().post_json("/api/account/password/forgot", {"email": email}))
        token = re.search(r"activate#t=([\w-]+)", last_email_to(email)).group(1)
        self.assertEqual(ok(Client().post_json("/api/account/activate/check", {"token": token})).json()["check"], "contact")
        r = Client().post_json("/api/account/activate", {"token": token, "password": "a good family pass"})
        self.assertEqual(r.status, 400)
        self.assertIn("call us", r.json()["error"])
        with db.read() as c:
            self.assertEqual(c.execute("SELECT status FROM accounts WHERE id=?", (aid,)).fetchone()[0], "pending_activation")
            self.assertTrue(c.execute("SELECT 1 FROM intray_items WHERE type='activation_problem' AND account_id=?"
                                      " AND status<>'done'", (aid,)).fetchone())


class MalformedInputTest(ServerTestCase):
    def test_bad_shapes_are_refused_not_crashed(self):
        fam = register_family()
        ref = complete_child(fam)
        self.assertEqual(fam.post_json("/api/account/participants", ["x"]).status, 400)
        self.assertEqual(fam.post_json("/api/account/login", "a string").status, 400)
        self.assertEqual(fam.post_json("/api/account/participants/%s/consents" % ref, {"answers": ["photo"]}).status, 422)
        r = fam.post_json("/api/account/participants/%s/consents" % ref, {"answers": {
            "photo": ["none"], "first_aid": "yes", "plasters": "yes", "emergency_treatment": "yes"}})
        self.assertEqual(r.status, 422)
        self.assertIn("photo", r.json()["errors"])
        for rows in (["x"], [{"full_name": "A", "relationship": "B", "phone": 1202123456}],
                     [{"full_name": ["A"], "relationship": {"b": 1}, "phone": None}]):
            self.assertEqual(fam.post_json("/api/account/contacts", {"contacts": rows}).status, 422, rows)
        r = fam.post_json("/api/account/participants", {"first_name": "A", "last_name": "B", "dob": 20200101,
                                                        "target_level": ["full"]})
        self.assertIn(r.status, (200, 422))
        self.assertEqual(Client().post_json("/api/account/register", {"email": 5, "password": 7, "first_name": {}}).status,
                         422)
