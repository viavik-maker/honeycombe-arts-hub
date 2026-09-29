"""The staff admin (CMS): login, publishing content, uploads, inbox and
newsletter tools — and that none of it works without logging in."""
import os

from tests.support import ServerTestCase, data_path

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
       b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
       b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


class AdminAuthTest(ServerTestCase):
    def test_admin_endpoints_need_login(self):
        c = self.client()
        self.assertEqual(c.get("/api/admin/overview").status, 401)
        self.assertEqual(c.get("/api/admin/subscribers.csv").status, 401)
        for path in ("/api/admin/content", "/api/admin/upload",
                     "/api/admin/messages", "/api/admin/subscribers"):
            with self.subTest(path=path):
                self.assertEqual(c.post_json(path, {}).status, 401)

    def test_overview_for_signed_in_staff(self):
        overview = self.admin().get("/api/admin/overview")
        self.assertEqual(overview.status, 200)
        data = overview.json()
        self.assertEqual(set(data), {"content", "messages", "subscribers"})
        self.assertIn("smtp", data["content"]["settings"])  # admins can see/edit SMTP

    def test_website_editing_needs_the_right_role(self):
        c = self.admin(roles=("session_staff",))
        self.assertEqual(c.get("/api/admin/overview").status, 403)
        self.assertEqual(c.post_json("/api/admin/content", {}).status, 403)

    def test_cross_origin_post_is_refused(self):
        c = self.client()
        r = c.post_json("/api/staff/login", {"email": "x@example.org", "password": "x"},
                        headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)


class AdminContentTest(ServerTestCase):
    def test_save_and_publish_round_trip(self):
        c = self.admin()
        content = c.get("/api/admin/overview").json()["content"]
        content["settings"]["tagline"] = "Tested tagline"
        r = c.post_json("/api/admin/content", content)
        self.assertEqual(r.status, 200, r.text)
        self.assertEqual(self.client().get("/api/content").json()["settings"]["tagline"],
                         "Tested tagline")
        self.assertTrue(os.path.exists(data_path("content.backup.json")))

    def test_invalid_content_is_rejected(self):
        c = self.admin()
        self.assertEqual(c.post_json("/api/admin/content", {"events": []}).status, 400)
        content = c.get("/api/admin/overview").json()["content"]
        content["gallery"] = "not a list"
        self.assertEqual(c.post_json("/api/admin/content", content).status, 400)
        content["gallery"] = []
        content["pages"] = []
        self.assertEqual(c.post_json("/api/admin/content", content).status, 400)


class AdminUploadTest(ServerTestCase):
    def test_image_upload_is_served_back(self):
        c = self.admin()
        r = c.post_file("/api/admin/upload", "file", "My Photo.PNG", PNG, "image/png")
        self.assertEqual(r.status, 200, r.text)
        url = r.json()["url"]
        self.assertRegex(url, r"^/uploads/my-photo-[0-9a-f]{8}\.png$")
        got = self.client().get(url)
        self.assertEqual(got.status, 200)
        self.assertEqual(got.body, PNG)
        self.assertEqual(got.header("Content-Type"), "image/png")

    def test_disallowed_file_type(self):
        c = self.admin()
        r = c.post_file("/api/admin/upload", "file", "evil.exe", b"MZ")
        self.assertEqual(r.status, 400)

    def test_upload_needs_multipart(self):
        c = self.admin()
        self.assertEqual(c.post_json("/api/admin/upload", {}).status, 400)
