"""Security baseline: response headers and CSP, request limits, rate limits,
safe uploads, log redaction, CSV formula safety and storage integrity."""
import os
import re
import struct
import threading
import unittest

from hah import config, images, markup, ratelimit, storage, web
from tests.support import ServerTestCase, data_path


def jpeg_with_metadata(orientation=6):
    """A tiny structurally valid JPEG carrying EXIF (orientation + a camera
    model), a comment and an XMP block — the kind of thing phones embed."""
    model = b"Phone-XYZ\x00"
    ifd = (b"\x00\x02"
           + struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0)
           + struct.pack(">HHII", 0x0110, 2, len(model), 8 + 2 + 24 + 4)
           + b"\x00\x00\x00\x00")
    exif = b"Exif\x00\x00" + b"MM\x00\x2a\x00\x00\x00\x08" + ifd + model
    xmp = b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>GPS 50.72,-1.84</x:xmpmeta>"
    seg = lambda marker, payload: b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload
    return (b"\xff\xd8"
            + seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
            + seg(0xE1, exif)
            + seg(0xE1, xmp)
            + seg(0xFE, b"taken at 12 Secret Road")
            + seg(0xDB, b"\x00" + bytes(64))
            + seg(0xDA, b"\x01\x01\x00\x00\x3f\x00")
            + b"\x12\x34\x56\xff\x00\x78"
            + b"\xff\xd9")


class HeadersTest(ServerTestCase):
    def test_security_headers_everywhere(self):
        c = self.client()
        for path in ("/", "/api/content", "/css/style.css", "/no-such-page"):
            with self.subTest(path=path):
                r = c.get(path)
                for name, value in web.SECURITY_HEADERS.items():
                    self.assertEqual(r.header(name), value)
                self.assertIsNone(r.header("Strict-Transport-Security"))

    def test_hsts_and_secure_cookie_behind_https(self):
        from tests.support import make_staff
        c = self.client()
        self.assertEqual(c.get("/", headers={"X-Forwarded-Proto": "https"})
                         .header("Strict-Transport-Security"), "max-age=31536000")
        u = make_staff()
        r = c.post_json("/api/staff/login", {"email": u["email"], "password": u["password"]},
                        headers={"X-Forwarded-Proto": "https"})
        self.assertIn("; Secure", r.header("Set-Cookie"))
        r = self.client().post_json("/api/staff/login", {"email": u["email"], "password": u["password"]})
        self.assertNotIn("Secure", r.header("Set-Cookie"))

    def test_pages_carry_a_csp_with_a_fresh_nonce(self):
        c = self.client()
        r1, r2 = c.get("/"), c.get("/")
        policy = r1.header("Content-Security-Policy-Report-Only")
        self.assertIsNotNone(policy)
        nonce = re.search(r"'nonce-([^']+)'", policy).group(1)
        self.assertIn('<script nonce="%s">window.HAH=' % nonce, r1.text)
        self.assertNotIn(nonce, r2.text)
        for directive in ("default-src 'self'", "object-src 'none'", "frame-ancestors 'none'",
                          "base-uri 'self'", "font-src 'self'"):
            self.assertIn(directive, policy)

    def test_csp_can_be_enforced(self):
        old, config.CSP_MODE = config.CSP_MODE, "enforce"
        try:
            r = self.client().get("/about")
            self.assertIsNotNone(r.header("Content-Security-Policy"))
            self.assertIsNone(r.header("Content-Security-Policy-Report-Only"))
        finally:
            config.CSP_MODE = old

    def test_api_responses_are_not_cached(self):
        self.assertEqual(self.client().get("/api/content").header("Cache-Control"), "no-store")
        self.assertEqual(self.admin().get("/api/admin/overview").header("Cache-Control"), "no-store")

    def test_no_third_party_fonts_or_eager_map(self):
        c = self.client()
        for path in ("/", "/admin"):
            self.assertNotIn("fonts.googleapis.com", c.get(path).text)
        self.assertEqual(c.get("/css/fonts.css").status, 200)
        self.assertEqual(c.get("/fonts/nunito-latin.woff2").status, 200)
        contact = c.get("/contact").text
        self.assertNotIn("<iframe", contact)
        self.assertIn("data-map-src=", contact)

    def test_csp_report_endpoint(self):
        r = self.client().post_json("/api/csp-report", {"csp-report": {
            "blocked-uri": "https://evil.example/x.js?secret=1", "violated-directive": "script-src"}})
        self.assertEqual(r.status, 204)
        from hah import cms
        self.assertEqual(cms._printable("a\nFAKE LOG LINE"), "a?FAKE LOG LINE")


class RequestLimitsTest(ServerTestCase):
    def raw_post(self, path, headers, body=b""):
        return self.client().request("POST", path, body, headers={"Content-Type": "application/json", **headers})

    def test_bad_content_lengths(self):
        for value in ("-5", "abc", "1e3"):
            with self.subTest(length=value):
                r = self.raw_post("/api/contact", {"Content-Length": value})
                self.assertEqual(r.status, 400)

    def test_body_too_large(self):
        big = b'{"name":"' + b"x" * (config.BODY_LIMIT + 10) + b'"}'
        self.assertEqual(self.raw_post("/api/contact", {}, big).status, 400)

    def test_chunked_bodies_refused(self):
        r = self.raw_post("/api/newsletter", {"Transfer-Encoding": "chunked"}, b"0\r\n\r\n")
        self.assertEqual(r.status, 400)


class RateLimitTest(ServerTestCase):
    def tearDown(self):
        ratelimit.reset()

    def test_wrong_staff_passwords_are_throttled(self):
        from tests.support import make_staff
        u = make_staff()
        c = self.client()
        limit = ratelimit.LIMITS["staff_login_pair"][0]
        for _ in range(limit):
            self.assertEqual(c.post_json("/api/staff/login", {"email": u["email"], "password": "wrong"}).status, 401)
        r = c.post_json("/api/staff/login", {"email": u["email"], "password": u["password"]})
        self.assertEqual(r.status, 429)  # even the right one, until it cools off

    def test_public_forms_are_throttled(self):
        old = ratelimit.LIMITS["public_form"]
        ratelimit.LIMITS["public_form"] = (3, 3600)
        try:
            c = self.client()
            for i in range(3):
                self.assertEqual(c.post_json("/api/newsletter", {"email": "a%d@example.com" % i}).status, 200)
            self.assertEqual(c.post_json("/api/newsletter", {"email": "z@example.com"}).status, 429)
        finally:
            ratelimit.LIMITS["public_form"] = old

    def test_request_info_is_staff_only(self):
        self.assertEqual(self.client().get("/api/admin/request-info").status, 401)
        info = self.admin().get("/api/admin/request-info",
                                headers={"X-Forwarded-For": "9.9.9.9"}).json()
        self.assertEqual(info["ip"], "127.0.0.1")  # no trusted proxies in tests
        self.assertEqual(info["x_forwarded_for"], "9.9.9.9")

    def test_client_ip_from_trusted_proxy_only(self):
        self.assertEqual(web.client_ip("1.1.1.1, 2.2.2.2", "10.0.0.1", 1), "2.2.2.2")
        self.assertEqual(web.client_ip("1.1.1.1, 2.2.2.2", "10.0.0.1", 2), "1.1.1.1")
        self.assertEqual(web.client_ip("1.1.1.1", "10.0.0.1", 0), "10.0.0.1")
        self.assertEqual(web.client_ip("", "10.0.0.1", 1), "10.0.0.1")
        self.assertEqual(web.client_ip("2.2.2.2", "10.0.0.1", 2), "10.0.0.1")


class UploadSafetyTest(ServerTestCase):
    def test_svg_and_disguised_files_are_refused(self):
        c = self.admin()
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        self.assertEqual(c.post_file("/api/admin/upload", "file", "logo.svg", svg).status, 400)
        self.assertEqual(c.post_file("/api/admin/upload", "file", "photo.png", svg).status, 400)
        self.assertEqual(c.post_file("/api/admin/upload", "file", "doc.pdf", b"%PDF-1.4").status, 400)

    def test_jpeg_location_and_camera_details_are_removed(self):
        c = self.admin()
        r = c.post_file("/api/admin/upload", "file", "Beach Day.jpeg", jpeg_with_metadata(), "image/jpeg")
        self.assertEqual(r.status, 200, r.text)
        url = r.json()["url"]
        self.assertTrue(url.endswith(".jpeg"))
        stored = self.client().get(url).body
        for secret in (b"Phone-XYZ", b"Secret Road", b"GPS 50.72", b"xmpmeta"):
            self.assertNotIn(secret, stored)
        self.assertTrue(stored.startswith(b"\xff\xd8\xff\xe0"))       # JFIF header stays first
        self.assertEqual(images._orientation(stored[stored.index(b"Exif"):]), 6)  # rotation kept
        self.assertTrue(stored.endswith(b"\x12\x34\x56\xff\x00\x78\xff\xd9"))  # image data untouched

    def test_old_svg_uploads_are_sandboxed(self):
        with open(os.path.join(config.UPLOADS, "old.svg"), "wb") as f:
            f.write(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        r = self.client().get("/uploads/old.svg")
        self.assertIn("sandbox", r.header("Content-Security-Policy"))


class ImagesUnitTest(unittest.TestCase):
    def test_sniff(self):
        self.assertEqual(images.sniff(b"\xff\xd8\xff\xe0...."), "jpeg")
        self.assertEqual(images.sniff(b"GIF89a...."), "gif")
        self.assertEqual(images.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "webp")
        self.assertIsNone(images.sniff(b"<svg"))

    def test_upright_photo_gets_no_exif_at_all(self):
        out = images.strip_jpeg_metadata(jpeg_with_metadata(orientation=1))
        self.assertNotIn(b"Exif", out)

    def test_corrupt_jpeg(self):
        with self.assertRaises(ValueError):
            images.strip_jpeg_metadata(b"\xff\xd8\xff\xe1\x00")


class LoggingTest(unittest.TestCase):
    def test_paths_are_logged_without_queries_or_tokens(self):
        self.assertEqual(web.redact_path("/whats-on?q=Maya+Smith"), "/whats-on")
        self.assertEqual(web.redact_path("/unsubscribe/abc123"), "/unsubscribe/[redacted]")
        self.assertEqual(web.redact_path("/u/xyz?x=1"), "/u/[redacted]")
        self.assertEqual(web.redact_path("/about"), "/about")


class CsvSafetyTest(ServerTestCase):
    def test_formula_like_cells_are_neutralised(self):
        self.assertEqual(markup.csv_safe("=HYPERLINK(\"x\")"), "'=HYPERLINK(\"x\")")
        self.assertEqual(markup.csv_safe("-1+2"), "'-1+2")
        self.assertEqual(markup.csv_safe("@SUM"), "'@SUM")
        self.assertEqual(markup.csv_safe("Sam"), "Sam")
        self.assertEqual(markup.csv_safe(-12.5), -12.5)
        self.client().post_json("/api/newsletter", {"email": "x@example.com", "name": "=cmd|'/c calc'!A1"})
        csv = self.admin().get("/api/admin/subscribers.csv").text
        self.assertIn("'=cmd", csv)


class StorageIntegrityTest(ServerTestCase):
    def test_corrupt_file_is_kept_not_overwritten(self):
        with open(data_path("messages.json"), "w") as f:
            f.write('[{"id": "1", "name": "Important"')  # truncated
        r = self.client().post_json("/api/contact", {"name": "New", "email": "n@example.com", "message": "hi"})
        self.assertEqual(r.status, 200)
        aside = [n for n in os.listdir(os.path.dirname(data_path("x"))) if n.startswith("messages.json.corrupt-")]
        self.assertEqual(len(aside), 1)
        with open(data_path(aside[0])) as f:
            self.assertIn("Important", f.read())

    def test_concurrent_signups_are_all_kept(self):
        emails = ["fan%d@example.com" % i for i in range(15)]
        threads = [threading.Thread(target=lambda e=e: self.client().post_json("/api/newsletter", {"email": e}))
                   for e in emails]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stored = {s["email"] for s in storage.load_json("subscribers.json", [])}
        self.assertTrue(set(emails) <= stored)
