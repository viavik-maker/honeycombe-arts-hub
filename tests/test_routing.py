"""The route registry, and how unknown or protected paths answer."""
import unittest

from hah import web
from tests.support import ServerTestCase


class PatternTest(unittest.TestCase):
    def test_segment_and_path_params(self):
        rx = web.compile_pattern("/whats-on/<slug>")
        self.assertEqual(rx.match("/whats-on/summer-club").groupdict(), {"slug": "summer-club"})
        self.assertIsNone(rx.match("/whats-on/a/b"))
        self.assertIsNone(rx.match("/whats-on/"))
        rx = web.compile_pattern("/uploads/<path:name>")
        self.assertEqual(rx.match("/uploads/a/b.png").group("name"), "a/b.png")

    def test_literal_characters_are_escaped(self):
        rx = web.compile_pattern("/api/admin/subscribers.csv")
        self.assertIsNotNone(rx.match("/api/admin/subscribers.csv"))
        self.assertIsNone(rx.match("/api/admin/subscribersXcsv"))

    def test_every_admin_api_route_requires_a_permission(self):
        public = {"/api/staff/setup", "/api/staff/login", "/api/staff/invite/check",
                  "/api/staff/invite/accept"}
        for r in web.ROUTES:
            pattern = r.regex.pattern.strip("^$").replace("\\", "")
            if pattern.startswith(("/api/admin/", "/api/staff/")) and pattern not in public:
                with self.subTest(route=pattern):
                    self.assertEqual(r.auth, "staff")
                    if r.mfa:
                        self.assertTrue(r.perm or pattern in (
                            "/api/staff/me", "/api/staff/password", "/api/staff/logout",
                            "/api/staff/sessions/revoke-others",
                            # the in-tray filters each item by the permission it names
                            "/api/staff/intray", "/api/staff/intray/(?P<iid>[^/]+)"), "no permission set")

    def test_signed_in_posts_are_csrf_protected(self):
        for r in web.ROUTES:
            if r.method == "POST" and r.auth:
                with self.subTest(route=r.regex.pattern):
                    self.assertTrue(r.csrf)


class UnknownPathTest(ServerTestCase):
    def test_unknown_post_is_401_then_404(self):
        self.assertEqual(self.client().post_json("/api/nope", {}).status, 401)
        self.assertEqual(self.admin().post_json("/api/nope", {}).status, 404)

    def test_unknown_get_is_the_404_page(self):
        r = self.client().get("/api/nope")
        self.assertEqual(r.status, 404)

    def test_unsupported_method(self):
        self.assertEqual(self.client().request("DELETE", "/").status, 501)


class RefusedRequestsDoNothingTest(ServerTestCase):
    """A refused request must never reach the route's code."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.calls = []
        web.route("POST", "/api/admin/_probe", auth="staff", perm="staff.manage")(lambda h: cls.calls.append(1) or h.json({"ok": True}))
        web.route("GET", "/api/admin/_probe", auth="staff", perm="staff.manage")(lambda h: cls.calls.append(1) or h.json({"ok": True}))

    @classmethod
    def tearDownClass(cls):
        web.ROUTES[:] = [r for r in web.ROUTES if "_probe" not in r.regex.pattern]
        super().tearDownClass()

    def test_route_code_never_runs_when_refused(self):
        anon = self.client()
        self.assertEqual(anon.post_json("/api/admin/_probe", {}).status, 401)
        self.assertEqual(anon.get("/api/admin/_probe").status, 401)
        low = self.admin(roles=("session_staff",))
        self.assertEqual(low.post_json("/api/admin/_probe", {}).status, 403)
        self.assertEqual(low.get("/api/admin/_probe").status, 403)
        high = self.admin()
        good = high.csrf
        high.csrf = "stale"
        self.assertEqual(high.post_json("/api/admin/_probe", {}).status, 403)
        self.assertEqual(self.calls, [])
        high.csrf = good
        self.assertEqual(high.post_json("/api/admin/_probe", {}).status, 200)
        self.assertEqual(self.calls, [1])

    def test_forbidden_publish_changes_nothing(self):
        before = self.client().get("/api/content").json()
        low = self.admin(roles=("session_staff",))
        hacked = dict(before, settings=dict(before["settings"], tagline="HACKED"))
        self.assertEqual(low.post_json("/api/admin/content", hacked).status, 403)
        self.assertEqual(self.client().get("/api/content").json()["settings"]["tagline"],
                         before["settings"]["tagline"])
