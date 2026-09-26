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

    def test_every_admin_api_route_requires_login(self):
        for r in web.ROUTES:
            if "/api/admin/" in r.regex.pattern and not r.regex.pattern.endswith(("login$", "logout$")):
                with self.subTest(route=r.regex.pattern):
                    self.assertEqual(r.auth, "admin")


class UnknownPathTest(ServerTestCase):
    def test_unknown_post_is_401_then_404(self):
        self.assertEqual(self.client().post_json("/api/nope", {}).status, 401)
        self.assertEqual(self.admin().post_json("/api/nope", {}).status, 404)

    def test_unknown_get_is_the_404_page(self):
        r = self.client().get("/api/nope")
        self.assertEqual(r.status, 404)

    def test_unsupported_method(self):
        self.assertEqual(self.client().request("DELETE", "/").status, 501)
