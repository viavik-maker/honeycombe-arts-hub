"""Fixes from the pre-launch security review: header injection, protected roles, 2FA guessing, rate-limit
eviction, audit actors, download names."""
from hah import db, private_files, ratelimit
from tests.family_helpers import PASSWORD, ok, register_family
from tests.support import Client, ServerTestCase, make_staff, next_code


class HardeningTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()

    def test_no_header_injection_through_redirects(self):
        r = Client().get("/account/invoices/X%0d%0aSet-Cookie:%20hah_acct=planted;%20Path=/")
        self.assertEqual(r.status, 302)
        self.assertIsNone(r.header("Set-Cookie"))
        self.assertNotIn("\n", r.header("Location"))
        self.assertIn("%0D%0A", r.header("Location").upper())

    def test_only_an_owner_can_reset_a_dsl(self):
        admin = self.admin(roles=("admin",))
        dsl = make_staff(("dsl",))
        for path, body in (("/reset", {}), ("/status", {"status": "disabled"}), ("/update", {"name": "Someone"})):
            r = admin.post_json("/api/staff/users/%d%s" % (dsl["id"], path), body)
            self.assertEqual(r.status, 403, path)
        self.assertEqual(self.admin().post_json("/api/staff/users/%d/reset" % dsl["id"], {}).status, 200)
        # an admin can still manage colleagues whose roles they could give
        mgr = make_staff(("manager",))
        self.assertEqual(admin.post_json("/api/staff/users/%d/reset" % mgr["id"], {}).status, 200)

    def test_signing_in_again_does_not_reset_wrong_code_count(self):
        u = make_staff(("manager",))
        blocked = False
        for _ in range(6):
            c = Client()
            c.csrf = c.post_json("/api/staff/login", {"email": u["email"], "password": u["password"]}).json()["csrf"]
            for _ in range(2):
                r = c.post_json("/api/staff/totp/verify", {"code": "000000"})
                blocked = blocked or r.status == 429
        self.assertTrue(blocked)
        c = Client()
        c.csrf = c.post_json("/api/staff/login", {"email": u["email"], "password": u["password"]}).json()["csrf"]
        self.assertEqual(c.post_json("/api/staff/totp/verify", {"code": next_code(u["totp_secret"])}).status, 429)

    def test_rate_limit_eviction_keeps_lockouts(self):
        old = ratelimit.MAX_KEYS
        ratelimit.MAX_KEYS = 50
        try:
            for _ in range(5):
                ratelimit.hit("collection_pw", "p1")
            self.assertTrue(ratelimit.blocked("collection_pw", "p1"))
            for i in range(200):  # someone spraying addresses at the code check
                ratelimit.hit("acct_code", "x%d@example.org" % i)
            self.assertTrue(ratelimit.blocked("collection_pw", "p1"))
            self.assertLessEqual(len(ratelimit._events), 60)
        finally:
            ratelimit.MAX_KEYS = old

    def test_family_route_with_a_staff_sign_in_in_the_same_browser(self):
        fam = register_family()
        u = make_staff(("manager",))
        fam.sign_in(u["email"], u["password"], u["totp_secret"])  # same browser: both cookies
        fam.csrf = ok(fam.get("/api/account/me")).json()["csrf"]
        with db.tx() as c:  # the staff sign-in is due its "last seen" update
            c.execute("UPDATE staff_sessions SET last_seen_at='2000-01-01T00:00:00Z' WHERE staff_id=?", (u["id"],))
        r = fam.post_json("/api/account/details", {"first_name": "Sarah", "last_name": "Parent", "mobile": "07700900001",
                                                   "postcode": "BH1 4SX"})
        self.assertEqual(r.status, 200, r.text)
        with db.read() as c:
            last = c.execute("SELECT actor_type FROM audit_log WHERE action LIKE 'account.%' ORDER BY id DESC LIMIT 1")\
                .fetchone()
        self.assertEqual(last["actor_type"], "account")

    def test_download_names_outside_latin1(self):
        sent = {}

        class H:
            def send(self, code, body, ctype, headers):
                sent.update(headers)

        orig = private_files.read
        private_files.read = lambda row: b"%PDF-1.4"
        try:
            private_files.send(H(), {"filename": "Łódź plan.pdf", "content_type": "application/pdf"})
        finally:
            private_files.read = orig
        d = sent["Content-Disposition"]
        d.encode("latin-1")  # must be sendable as a header
        self.assertIn('filename="__d_ plan.pdf"', d)
        self.assertIn("filename*=UTF-8''%C5%81%C3%B3d%C5%BA%20plan.pdf", d)
        self.assertTrue(PASSWORD)
