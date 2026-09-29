"""Links in the website settings (booking, donate, social, Seesaw, volunteer) go straight into href="" on the
public site, so publishing refuses anything but web, email and phone links or pages on this site."""
from hah.cms import unsafe_link
from tests.support import ServerTestCase


class UnsafeLinkTest(ServerTestCase):
    def test_scheme_check(self):
        for ok in ("", None, "https://example.org/x", "http://example.org", "HTTPS://EXAMPLE.ORG",
                   "mailto:info@example.org", "tel:07932772905", "/contact", "/book?activity=clay", "#top"):
            with self.subTest(ok=ok):
                self.assertFalse(unsafe_link(ok))
        for bad in ("javascript:alert(1)", "JavaScript:alert(1)", " javascript:alert(1)", "java\tscript:alert(1)",
                    "java\nscript:alert(1)", "\x01javascript:alert(1)", "data:text/html,<script>alert(1)</script>",
                    "vbscript:msgbox(1)", "file:///etc/passwd", 42):
            with self.subTest(bad=bad):
                self.assertTrue(unsafe_link(bad))

    def test_publishing_refuses_a_javascript_link(self):
        c = self.admin()
        content = c.get("/api/admin/overview").json()["content"]
        for key in ("donateUrl", "bookingUrl", "facebook", "seesawUrl", "volunteerUrl", "someNewUrl"):
            with self.subTest(key=key):
                bad = dict(content, settings=dict(content["settings"], **{key: "javascript:alert(document.cookie)"}))
                r = c.post_json("/api/admin/content", bad)
                self.assertEqual(r.status, 400, r.text)
                self.assertEqual(r.json()["field"], key)
        self.assertNotIn("javascript:", str(self.client().get("/api/content").json()["settings"]))

    def test_publishing_accepts_safe_links(self):
        c = self.admin()
        content = c.get("/api/admin/overview").json()["content"]
        content["settings"].update(donateUrl="https://example.org/give", volunteerUrl="/contact",
                                   bookingUrl="mailto:bookings@example.org", seesawUrl="")
        r = c.post_json("/api/admin/content", content)
        self.assertEqual(r.status, 200, r.text)
        self.assertEqual(self.client().get("/api/content").json()["settings"]["donateUrl"], "https://example.org/give")
