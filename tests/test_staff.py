"""Staff accounts: first-owner setup, sign-in with two-factor codes, sessions,
invites and resets, role rules and the audit log."""
import hashlib
import time
import unittest

from hah import db, security
from tests.support import ADMIN_PASSWORD, Client, ServerTestCase, make_staff, next_code, reset_data


def audit_actions():
    with db.read() as c:
        return [r[0] for r in c.execute("SELECT action FROM audit_log ORDER BY id")]


class TotpUnitTest(unittest.TestCase):
    def test_rfc6238_vectors(self):
        # RFC 6238 appendix B (SHA-1, 8 digits) — checked through hotp()
        key = b"12345678901234567890"
        for t, code in ((59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"),
                        (2000000000, "69279037")):
            self.assertEqual(security.hotp(key, t // 30, digits=8), code)

    def test_verify_window_and_replay(self):
        secret = security.new_totp_secret()
        now = 1_800_000_000
        code = security.totp(secret, now)
        step = security.verify_totp(secret, code, at=now)
        self.assertEqual(step, now // 30)
        self.assertIsNone(security.verify_totp(secret, code, last_step=step, at=now))  # replay
        self.assertIsNotNone(security.verify_totp(secret, security.totp(secret, now - 30), at=now))  # drift
        self.assertIsNone(security.verify_totp(secret, security.totp(secret, now - 90), at=now))
        self.assertIsNone(security.verify_totp(secret, "12345", at=now))

    def test_password_hashing(self):
        h = security.hash_password("three random words")
        self.assertTrue(h.startswith("pbkdf2_sha256$"))
        self.assertTrue(security.verify_password("three random words", h))
        self.assertFalse(security.verify_password("three random word", h))
        self.assertFalse(security.verify_password("x", "garbage"))
        self.assertIsNotNone(security.password_problem("short"))
        self.assertIsNotNone(security.password_problem("password123"))
        self.assertIsNotNone(security.password_problem("aaaaaaaaaaaa"))
        self.assertIsNone(security.password_problem("correct horse battery staple"))


class FirstOwnerSetupTest(ServerTestCase):
    def setUp(self):
        reset_data()

    def test_setup_needs_team_password_then_2fa(self):
        c = self.client()
        self.assertEqual(c.get("/api/staff/setup").json(), {"needed": True, "possible": True})
        r = c.post_json("/api/staff/setup", {"team_password": "wrong", "name": "Poppy", "email": "p@example.org",
                                             "password": "a good long passphrase"})
        self.assertEqual(r.status, 403)
        r = c.post_json("/api/staff/setup", {"team_password": ADMIN_PASSWORD, "name": "Poppy",
                                             "email": "Poppy@Example.org", "password": "a good long passphrase"})
        self.assertEqual(r.status, 200, r.text)
        self.assertEqual(r.json()["step"], "enrol")
        c.csrf = r.json()["csrf"]
        # nothing else works until 2FA is set up
        self.assertEqual(c.get("/api/admin/overview").status, 401)
        enrol = c.get("/api/staff/totp/enrol").json()
        self.assertTrue(enrol["uri"].startswith("otpauth://totp/"))
        self.assertEqual(c.post_json("/api/staff/totp/enrol", {"code": "000000"}).status, 400)
        r = c.post_json("/api/staff/totp/enrol", {"code": next_code(enrol["secret"])})
        self.assertEqual(r.status, 200, r.text)
        self.assertEqual(len(r.json()["recovery_codes"]), 10)
        c.csrf = r.json()["csrf"]
        me = c.get("/api/staff/me").json()
        self.assertEqual(me["staff"]["roles"], ["owner"])
        self.assertEqual(me["staff"]["email"], "poppy@example.org")
        self.assertEqual(c.get("/api/admin/overview").status, 200)
        # set up once only
        self.assertEqual(self.client().get("/api/staff/setup").json()["needed"], False)
        r = self.client().post_json("/api/staff/setup", {"team_password": ADMIN_PASSWORD, "name": "X",
                                                         "email": "x@example.org", "password": "another good one"})
        self.assertEqual(r.status, 409)
        self.assertIn("staff.owner_created", audit_actions())
        self.assertIn("staff.2fa_enabled", audit_actions())

    def test_weak_password_refused(self):
        r = self.client().post_json("/api/staff/setup", {"team_password": ADMIN_PASSWORD, "name": "P",
                                                         "email": "p@example.org", "password": "password123"})
        self.assertEqual(r.status, 400)


class SignInTest(ServerTestCase):
    def test_password_then_code(self):
        u = make_staff(("manager",))
        c = Client()
        r = c.post_json("/api/staff/login", {"email": u["email"].upper(), "password": u["password"]})
        self.assertEqual(r.json()["step"], "totp")
        cookie = r.header("Set-Cookie")
        for part in ("hah_staff=", "HttpOnly", "SameSite=Strict", "Max-Age=43200"):
            self.assertIn(part, cookie)
        c.csrf = r.json()["csrf"]
        self.assertEqual(c.get("/api/staff/me").json()["perms"], [])  # not until the code
        self.assertEqual(c.get("/api/admin/overview").status, 401)
        self.assertEqual(c.post_json("/api/staff/totp/verify", {"code": "000000"}).status, 401)
        code = next_code(u["totp_secret"])
        r = c.post_json("/api/staff/totp/verify", {"code": code})
        self.assertEqual(r.status, 200, r.text)
        c.csrf = r.json()["csrf"]
        self.assertIn("site.content", c.get("/api/staff/me").json()["perms"])
        # the same code can't be used again, even in a new sign-in
        c2 = Client()
        c2.csrf = c2.post_json("/api/staff/login", {"email": u["email"], "password": u["password"]}).json()["csrf"]
        self.assertEqual(c2.post_json("/api/staff/totp/verify", {"code": code}).status, 401)

    def test_wrong_password_and_unknown_email_look_the_same(self):
        u = make_staff()
        a = self.client().post_json("/api/staff/login", {"email": u["email"], "password": "nope nope nope"})
        b = self.client().post_json("/api/staff/login", {"email": "nobody@example.org", "password": "nope nope nope"})
        self.assertEqual((a.status, a.json()), (b.status, b.json()))
        self.assertIn("staff.login_failed", audit_actions())

    def test_recovery_code_works_once(self):
        c = Client()
        u = make_staff(totp=False)
        r = c.post_json("/api/staff/login", {"email": u["email"], "password": u["password"]})
        self.assertEqual(r.json()["step"], "enrol")
        c.csrf = r.json()["csrf"]
        secret = c.get("/api/staff/totp/enrol").json()["secret"]
        codes = c.post_json("/api/staff/totp/enrol", {"code": next_code(secret)}).json()["recovery_codes"]
        c2 = Client()
        c2.csrf = c2.post_json("/api/staff/login", {"email": u["email"], "password": u["password"]}).json()["csrf"]
        r = c2.post_json("/api/staff/totp/verify", {"code": codes[0]})
        self.assertEqual(r.status, 200, r.text)
        self.assertEqual(r.json()["recovery_codes_left"], 9)
        c3 = Client()
        c3.csrf = c3.post_json("/api/staff/login", {"email": u["email"], "password": u["password"]}).json()["csrf"]
        self.assertEqual(c3.post_json("/api/staff/totp/verify", {"code": codes[0]}).status, 401)

    def test_disabled_and_invited_accounts_cannot_sign_in(self):
        for status in ("disabled", "invited"):
            u = make_staff(status=status)
            r = self.client().post_json("/api/staff/login", {"email": u["email"], "password": u["password"]})
            self.assertEqual(r.status, 401)

    def test_logout_and_expired_sessions(self):
        c = self.admin()
        self.assertEqual(c.post_json("/api/staff/logout", {}).status, 200)
        self.assertEqual(c.get("/api/staff/me").status, 401)
        c = self.admin()
        with db.tx() as conn:
            conn.execute("UPDATE staff_sessions SET idle_expires_at='2000-01-01T00:00:00Z'")
        self.assertEqual(c.get("/api/staff/me").status, 401)

    def test_sessions_store_only_a_hash(self):
        c = self.admin()
        token = c.cookies["hah_staff"]
        with db.read() as conn:
            hashes = [r[0] for r in conn.execute("SELECT token_hash FROM staff_sessions")]
        self.assertNotIn(token, hashes)
        self.assertIn(hashlib.sha256(token.encode()).hexdigest(), hashes)

    def test_csrf_and_origin_are_required(self):
        c = self.admin()
        good = c.csrf
        c.csrf = "wrong"
        self.assertEqual(c.post_json("/api/staff/sessions/revoke-others", {}).status, 403)
        c.csrf = good
        self.assertEqual(c.post_json("/api/staff/sessions/revoke-others", {}, origin=False).status, 403)
        self.assertEqual(c.post_json("/api/staff/sessions/revoke-others", {}).status, 200)

    def test_password_change_signs_out_other_sessions(self):
        u = make_staff()
        a, b = Client(), Client()
        a.sign_in(u["email"], u["password"], u["totp_secret"])
        b.sign_in(u["email"], u["password"], u["totp_secret"])
        self.assertEqual(a.post_json("/api/staff/password", {"current": "wrong", "new": "brand new passphrase"}).status, 400)
        self.assertEqual(a.post_json("/api/staff/password", {"current": u["password"], "new": "short"}).status, 400)
        self.assertEqual(a.post_json("/api/staff/password", {"current": u["password"], "new": "brand new passphrase"}).status, 200)
        self.assertEqual(a.get("/api/staff/me").status, 200)
        self.assertEqual(b.get("/api/staff/me").status, 401)


class StaffManagementTest(ServerTestCase):
    def test_invite_accept_and_enrol(self):
        owner = self.admin()
        r = owner.post_json("/api/staff/users/invite", {"name": "Sam", "email": "sam@example.org",
                                                         "roles": ["session_staff"]})
        self.assertEqual(r.status, 200, r.text)
        link = r.json()["link"]
        self.assertIn("/admin#invite=", link)
        token = link.split("#invite=")[1]
        with db.read() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM auth_tokens WHERE token_hash=?", (token,)).fetchone())
        self.assertEqual(owner.post_json("/api/staff/users/invite", {"name": "Sam", "email": "sam@example.org",
                                                                       "roles": ["session_staff"]}).status, 409)
        new = Client()
        self.assertEqual(new.post_json("/api/staff/invite/check", {"token": token}).json()["name"], "Sam")
        self.assertEqual(new.post_json("/api/staff/invite/accept", {"token": token, "password": "short"}).status, 400)
        r = new.post_json("/api/staff/invite/accept", {"token": token, "password": "sam's long passphrase"})
        self.assertEqual(r.json()["step"], "enrol")
        new.csrf = r.json()["csrf"]
        secret = new.get("/api/staff/totp/enrol").json()["secret"]
        r = new.post_json("/api/staff/totp/enrol", {"code": next_code(secret)})
        new.csrf = r.json()["csrf"]
        self.assertEqual(new.get("/api/staff/me").json()["staff"]["roles"], ["session_staff"])
        self.assertEqual(new.get("/api/admin/overview").status, 403)  # session staff don't edit the website
        # links are single use
        self.assertEqual(Client().post_json("/api/staff/invite/accept", {"token": token, "password": "another passphrase"}).status, 404)

    def test_role_rules(self):
        admin = self.admin(roles=("admin",))
        r = admin.post_json("/api/staff/users/invite", {"name": "D", "email": "d@example.org", "roles": ["dsl"]})
        self.assertEqual(r.status, 400)  # only owners appoint safeguarding leads
        r = admin.post_json("/api/staff/users/invite", {"name": "O", "email": "o@example.org", "roles": ["owner"]})
        self.assertEqual(r.status, 400)
        r = admin.post_json("/api/staff/users/invite", {"name": "M", "email": "m@example.org", "roles": ["manager"]})
        self.assertEqual(r.status, 200, r.text)
        manager = self.admin(roles=("manager",))
        self.assertEqual(manager.get("/api/staff/users").status, 403)
        owner = self.admin()
        r = owner.post_json("/api/staff/users/invite", {"name": "D", "email": "dsl@example.org", "roles": ["dsl"]})
        self.assertEqual(r.status, 200)
        # admins can't manage owners
        self.assertEqual(admin.post_json("/api/staff/users/%d/status" % owner.staff["id"], {"status": "disabled"}).status, 403)

    def test_last_owner_is_protected(self):
        reset_data()
        owner = self.admin()
        r = owner.post_json("/api/staff/users/%d/update" % owner.staff["id"], {"roles": ["admin"]})
        self.assertEqual(r.status, 400)
        self.assertEqual(owner.post_json("/api/staff/users/%d/status" % owner.staff["id"],
                                         {"status": "disabled"}).status, 400)

    def test_disable_ends_sessions_and_reset_link_resets_2fa(self):
        owner = self.admin()
        u = make_staff(("manager",))
        worker = Client()
        worker.sign_in(u["email"], u["password"], u["totp_secret"])
        self.assertEqual(owner.post_json("/api/staff/users/%d/status" % u["id"], {"status": "disabled"}).status, 200)
        self.assertEqual(worker.get("/api/staff/me").status, 401)
        self.assertEqual(owner.post_json("/api/staff/users/%d/status" % u["id"], {"status": "active"}).status, 200)
        link = owner.post_json("/api/staff/users/%d/reset" % u["id"], {}).json()["link"]
        token = link.split("#reset=")[1]
        r = Client().post_json("/api/staff/invite/accept", {"token": token, "password": "after the reset pass"})
        self.assertEqual(r.json()["step"], "enrol")  # new phone: set 2FA up again
        actions = audit_actions()
        for a in ("staff.disabled", "staff.enabled", "staff.link_issued", "staff.reset_used"):
            self.assertIn(a, actions)

    def test_audit_log_viewer(self):
        owner = self.admin()
        with db.tx() as c:
            c.execute("INSERT INTO audit_log(at, actor_type, action, restricted) VALUES (?, 'system', 'safeguarding.viewed', 1)",
                      (db.now(),))
        entries = owner.get("/api/staff/audit").json()["entries"]
        self.assertTrue(entries)
        self.assertNotIn("safeguarding.viewed", [e["action"] for e in entries])  # owners aren't DSLs by default
        dsl = self.admin(roles=("dsl",))
        self.assertIn("safeguarding.viewed", [e["action"] for e in dsl.get("/api/staff/audit").json()["entries"]])
        self.assertEqual(self.admin(roles=("manager",)).get("/api/staff/audit").status, 403)

    def test_audit_log_is_append_only(self):
        self.admin()
        with self.assertRaises(Exception):
            with db.tx() as c:
                c.execute("UPDATE audit_log SET action='x'")
        with self.assertRaises(Exception):
            with db.tx() as c:
                c.execute("DELETE FROM audit_log")

    def test_mfa_requirement_owner_only(self):
        self.assertEqual(self.admin(roles=("admin",)).post_json("/api/staff/security", {"mfa_required": False}).status, 403)
        owner = self.admin()
        self.assertEqual(owner.post_json("/api/staff/security", {"mfa_required": False}).status, 200)
        u = make_staff(totp=False)
        r = self.client().post_json("/api/staff/login", {"email": u["email"], "password": u["password"]})
        self.assertEqual(r.json()["step"], "done")
        owner.post_json("/api/staff/security", {"mfa_required": True})


class LegacyTeamPasswordTest(ServerTestCase):
    def setUp(self):
        reset_data()

    def write_auth(self, password):
        import base64, hashlib, json, os
        from hah import config
        salt = os.urandom(16)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
        with open(os.path.join(config.DATA, "auth.json"), "w") as f:
            json.dump({"salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(dk).decode(),
                       "iterations": 120_000}, f)

    def setup(self, team):
        return self.client().post_json("/api/staff/setup", {"team_password": team, "name": "P",
                                                            "email": "p@example.org", "password": "a good long passphrase"})

    def test_live_site_team_password_is_used_then_retired(self):
        import os
        from hah import config
        self.write_auth("our-real-team-pass")
        self.assertEqual(self.setup(ADMIN_PASSWORD).status, 403)  # the stored one wins
        self.assertEqual(self.setup("our-real-team-pass").status, 200)
        self.assertFalse(os.path.exists(os.path.join(config.DATA, "auth.json")))

    def test_published_default_password_is_never_accepted(self):
        self.write_auth("honeycomb2026")
        self.assertEqual(self.setup("honeycomb2026").status, 403)
        self.assertEqual(self.setup(ADMIN_PASSWORD).status, 200)  # falls back to the environment secret
