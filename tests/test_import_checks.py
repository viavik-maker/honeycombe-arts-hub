"""Family import: files that weren't read properly, and rows that disagree about a family."""
import uuid

from hah import importer
from tests.family_helpers import ok
from tests.support import ServerTestCase

HEAD = "Family ID,Parent first name,Parent last name,Email,Child first name,Child DOB\r\n"


class ImportChecksTest(ServerTestCase):
    def test_unreadable_characters_are_refused(self):
        raw = (HEAD + "101,Siân,Jones,sian-%s@example.org,Zoë,01/02/2015\r\n" % uuid.uuid4().hex[:8]).encode("cp1252")
        admin = self.admin()
        # what a browser gets reading Excel's Windows-1252 CSV as UTF-8
        r = admin.post_json("/api/staff/import/preview", {"csv": raw.decode("utf-8", "replace")})
        self.assertEqual(r.status, 400)
        self.assertIn("CSV UTF-8", r.json()["error"])
        # decoded as Windows-1252 (the page's fallback), the names come through intact
        p = ok(admin.post_json("/api/staff/import/preview", {"csv": raw.decode("cp1252")})).json()
        self.assertEqual(p["sample"][0][1], "Siân")

    def test_same_email_with_a_different_family_is_reported(self):
        shared, other = "shared-%s@example.org" % uuid.uuid4().hex[:8], "same-%s@example.org" % uuid.uuid4().hex[:8]
        csv = HEAD + ("101,Siân,Jones,{s},Zoë,01/02/2015\r\n"
                      "202,Siân,Jones,{s},Tom,03/04/2016\r\n"       # different Family ID
                      "101,Mark,Evans,{s},Ann,03/04/2017\r\n"       # different parent
                      "303,Ria,Khan,{o},Ali,05/06/2016\r\n"
                      "303,ria,KHAN,{o},Isa,05/06/2018\r\n").format(s=shared, o=other)  # same family
        admin = self.admin()
        m = ok(admin.post_json("/api/staff/import/preview", {"csv": csv})).json()["mapping"]
        dry = ok(admin.post_json("/api/staff/import/run", {"csv": csv, "mapping": m})).json()
        problems = {p["row"]: p["message"] for p in dry["problems"] if p["result"] == "error"}
        self.assertEqual(sorted(problems), [3, 4])
        self.assertIn("different Family ID", problems[3])
        self.assertIn("different parent name", problems[4])
        self.assertEqual((dry["stats"]["families"], dry["stats"]["children"]), (2, 3))

    def test_parse_csv_refuses_replacement_characters(self):
        with self.assertRaises(ValueError):
            importer.parse_csv(HEAD + "1,Si�n,Jones,a@example.org,Zo�,01/02/2015\r\n")
