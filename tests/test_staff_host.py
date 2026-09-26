"""An optional separate address for staff (STAFF_HOST)."""
from hah import config
from tests.support import ServerTestCase

STAFF = "staff.example.org"


class StaffHostTest(ServerTestCase):
    def setUp(self):
        self._old = config.STAFF_HOST
        config.STAFF_HOST = STAFF

    def tearDown(self):
        config.STAFF_HOST = self._old

    def get(self, path, host):
        return self.client().request("GET", path, headers={"Host": host})

    def test_admin_only_on_the_staff_address(self):
        public = "www.example.org"
        r = self.get("/admin", public)
        self.assertEqual(r.status, 302)
        self.assertEqual(r.header("Location"), "https://staff.example.org/admin")
        self.assertEqual(self.get("/admin/admin.js?v=2", public).header("Location"),
                         "https://staff.example.org/admin/admin.js?v=2")
        self.assertEqual(self.get("/api/staff/me", public).status, 404)
        self.assertEqual(self.get("/api/admin/system-status", public).status, 404)
        r = self.client().request("POST", "/api/staff/login", b"{}", headers={"Host": public, "Content-Type": "application/json"},
                                  origin=False)
        self.assertEqual(r.status, 404)
        self.assertEqual(self.get("/", public).status, 200)  # the public site is unchanged
        self.assertEqual(self.get("/api/content", public).status, 200)
        # on the staff address
        self.assertEqual(self.get("/admin", STAFF).status, 200)
        self.assertEqual(self.get("/api/staff/me", STAFF + ":443").status, 401)  # reachable (not signed in)
        r = self.get("/", STAFF)
        self.assertEqual((r.status, r.header("Location")), (302, "/admin"))

    def test_off_by_default(self):
        config.STAFF_HOST = ""
        self.assertEqual(self.get("/admin", "anything.example.org").status, 200)
