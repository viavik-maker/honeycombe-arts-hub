"""The public website: every page renders, partials and data are filled in,
nothing private leaks, and odd paths can't escape the public folder."""
import json
import re

from tests.support import ServerTestCase

SITE = "https://honeycombeartshub.org.uk"
PAGES = ["/", "/whats-on", "/past-events", "/about", "/gallery", "/get-involved",
         "/holiday-club", "/arts-award", "/emerging-artists", "/testimonials",
         "/contact", "/policies", "/privacy", "/safeguarding"]


def embedded_content(html):
    m = re.search(r"<script[^>]*>window\.HAH=(.*?)</script>", html, re.S)
    return json.loads(m.group(1).replace("<\\/", "</")) if m else None


class PublicPagesTest(ServerTestCase):
    def test_every_page_renders(self):
        c = self.client()
        for path in PAGES:
            with self.subTest(path=path):
                r = c.get(path)
                self.assertEqual(r.status, 200)
                self.assertTrue(r.header("Content-Type").startswith("text/html"))
                self.assertEqual(r.header("Cache-Control"), "no-store")
                self.assertEqual(r.header("X-Content-Type-Options"), "nosniff")
                self.assertNotIn("<!--#include", r.text)
                self.assertNotIn("<!--#block", r.text)
                self.assertNotIn("<!--#data-->", r.text)
                self.assertIn('<link rel="canonical" href="%s%s">' % (SITE, path), r.text)
                self.assertIn('application/ld+json', r.text)
                content = embedded_content(r.text)
                self.assertIsNotNone(content, "window.HAH missing")
                self.assertNotIn("smtp", content["settings"])

    def test_admin_shell_renders(self):
        r = self.client().get("/admin")
        self.assertEqual(r.status, 200)
        self.assertIn('data-tab="dashboard"', r.text)
        self.assertIn('name="robots" content="noindex"', r.text)

    def test_event_detail_page(self):
        r = self.client().get("/whats-on/summer-holiday-arts-club")
        self.assertEqual(r.status, 200)
        self.assertIn('href="%s/whats-on/summer-holiday-arts-club"' % SITE, r.text)

    def test_trailing_slash_and_html_suffix(self):
        c = self.client()
        self.assertEqual(c.get("/about/").status, 200)
        r = c.get("/privacy.html")
        self.assertEqual(r.status, 200)
        self.assertIn('href="%s/privacy"' % SITE, r.text)

    def test_contact_and_get_involved_blocks_are_filled(self):
        c = self.client()
        contact = c.get("/contact").text
        self.assertIn('class="contact-card', contact)
        self.assertIn("google.com/maps", contact)
        involved = c.get("/get-involved").text
        self.assertIn('class="feature-row', involved)

    def test_real_404(self):
        r = self.client().get("/no-such-page")
        self.assertEqual(r.status, 404)
        self.assertTrue(r.header("Content-Type").startswith("text/html"))

    def test_static_files_and_caching(self):
        c = self.client()
        css = c.get("/css/style.css")
        self.assertEqual(css.status, 200)
        self.assertIn("text/css", css.header("Content-Type"))
        self.assertEqual(css.header("Cache-Control"), "no-cache")
        img = c.get("/img/logo.png")
        self.assertEqual(img.status, 200)
        self.assertEqual(img.header("Cache-Control"), "public, max-age=86400")

    def test_head_request(self):
        r = self.client().request("HEAD", "/")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body, b"")

    def test_paths_cannot_escape_public_folder(self):
        c = self.client()
        for path in ("/..%2fserver.py", "/%2e%2e/%2e%2e/etc/passwd", "/../server.py",
                     "/%2e%2e/data/auth.json"):
            with self.subTest(path=path):
                r = c.get(path)
                self.assertIn(r.status, (403, 404))
                self.assertNotIn("import ", r.text)
                self.assertNotIn('"salt"', r.text)

    def test_missing_upload_is_404(self):
        self.assertEqual(self.client().get("/uploads/nope.png").status, 404)


class PublicContentApiTest(ServerTestCase):
    def test_content_api_strips_smtp(self):
        r = self.client().get("/api/content")
        self.assertEqual(r.status, 200)
        data = r.json()
        self.assertNotIn("smtp", data["settings"])
        self.assertTrue(data["events"])
        self.assertIn("contact", data["pages"])


class BookingSwitchOverTest(ServerTestCase):
    def test_book_now_and_membership_copy_switch_when_booking_goes_live(self):
        from hah import storage
        from tests.booking_helpers import set_settings
        set_settings(booking_live=False)
        c = self.client()
        home = c.get("/").text
        self.assertIn('"bookingLive": false', home)
        self.assertIn('data-when-booking="on" hidden', home)
        # staff reworded one of the membership lines: that edit must survive
        storage.update_json("content.json", {}, lambda x: (x["events"][5].update(price="Members only, £2"), x)[1])
        set_settings(booking_live=True)
        home = c.get("/").text
        self.assertIn('"bookingLive": true', home)
        content = storage.load_json("content.json", {})
        self.assertNotIn("You must be a member", content["events"][0]["description"])
        self.assertEqual(content["events"][5]["price"], "Members only, £2")
        self.assertIn('href="/account"', c.get("/get-involved").text)
        self.assertIn("Your account", c.get("/get-involved").text)
        set_settings(booking_live=False)
