"""SEND support requests (mockup 6) and the private file store."""
import io
import os
import uuid
import zipfile

from hah import db, gdpr, private_files, ratelimit, send_support
from tests.family_helpers import complete_child, last_email_to, ok, participant_id, register_family
from tests.support import ServerTestCase, data_path

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


def upload(client, name, data, kind="ehcp"):
    boundary = uuid.uuid4().hex
    body = (("--%s\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\n%s\r\n" % (boundary, kind)).encode()
            + ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
               "Content-Type: application/octet-stream\r\n\r\n" % (boundary, name)).encode()
            + data + ("\r\n--%s--\r\n" % boundary).encode())
    return client.request("POST", "/api/account/send/files", body,
                          headers={"Content-Type": "multipart/form-data; boundary=" + boundary})


FORM = {"contact_method": "phone", "best_time": "after_school", "needs": ["autism", "sensory"],
        "interests": ["holiday_clubs"], "good_day": "A quiet corner and a visual timetable",
        "overwhelm": "Hand dryers", "communication": "Verbal", "current_support": "1:1 at lunch",
        "one_to_one": "sometimes", "ehcp": "yes", "consent_share_staff": True}


class SendSupportTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()

    def test_request_with_documents_through_to_agreed_plan(self):
        fam = register_family()
        # uploads: checked from the contents, not the name
        self.assertEqual(upload(fam, "ehcp.pdf", b"MZ\x90\x00 not really a pdf").status, 400)
        self.assertEqual(upload(fam, "plan.docx", b"PK\x03\x04 broken zip").status, 400)
        big = upload(fam, "huge.pdf", PDF + b"0" * (private_files.MAX_BYTES + 1))
        self.assertEqual(big.status, 400)
        self.assertIn("10 MB", big.json()["error"])
        f1 = ok(upload(fam, "EHCP final 2025.pdf", PDF)).json()["file"]
        f2 = ok(upload(fam, "care plan.docx", docx(), kind="other_plan")).json()["file"]
        self.assertEqual(f1["filename"], "EHCP final 2025.pdf")
        self.assertTrue(f2["filename"].endswith(".docx"))
        # the form needs consent and at least one interest
        r = fam.post_json("/api/account/send", dict(FORM, consent_share_staff=False, interests=[],
                                                     child={"first_name": "Ari", "dob": "2017-03-03"}))
        self.assertEqual(r.status, 422)
        self.assertIn("consent_share_staff", r.json()["errors"])
        self.assertIn("interests", r.json()["errors"])
        ref = ok(fam.post_json("/api/account/send", dict(FORM, child={"first_name": "Ari", "dob": "2017-03-03"},
                                                         files=[f1["ref"], f2["ref"]]))).json()["ref"]
        mine = ok(fam.get("/api/account/send")).json()
        self.assertEqual(mine["requests"][0]["child"]["first_name"], "Ari")
        self.assertEqual(len(mine["requests"][0]["files"]), 2)
        self.assertEqual(mine["pending_files"], [])
        ack = last_email_to(fam.email)
        self.assertIn("SEND lead will be in touch", ack)
        self.assertNotIn("Hand dryers", ack)  # no details in emails
        # who can see it
        lead = self.admin(roles=("send_lead",))
        self.assertIn("send_intake", [i["type"] for i in ok(lead.get("/api/staff/intray")).json()["items"]])
        self.assertEqual(self.admin(roles=("finance",)).get("/api/staff/send/" + ref).status, 403)
        self.assertEqual(self.admin(roles=("session_staff",)).get("/api/staff/send/files/" + f1["ref"]).status, 403)
        other = register_family()
        self.assertEqual(other.get("/api/account/send/files/" + f1["ref"]).status, 404)
        d = ok(lead.get("/api/staff/send/" + ref)).json()["request"]
        self.assertEqual(d["good_day"], "A quiet corner and a visual timetable")
        dl = lead.get("/api/staff/send/files/" + f1["ref"])
        self.assertEqual(dl.body, PDF)
        self.assertIn("attachment", dl.header("Content-Disposition"))
        self.assertIn("sandbox", dl.header("Content-Security-Policy"))
        self.assertEqual(ok(fam.get("/api/account/send/files/" + f1["ref"])).body, PDF)
        with db.read() as c:
            self.assertTrue(c.execute("SELECT 1 FROM audit_log WHERE action='send.file_downloaded' AND restricted=1").fetchone())
            stored = c.execute("SELECT stored_name FROM private_files WHERE ref=?", (f1["ref"],)).fetchone()[0]
        # never reachable as a plain web address
        for path in ("/private/" + stored, "/data/private/" + stored, "/uploads/" + stored, "/../data/private/" + stored):
            self.assertNotEqual(self.client().get(path).status, 200, path)
        # the SEND lead works it through
        ok(lead.post_json("/api/staff/send/%s/update" % ref, {"status": "contacted", "note": "Called mum, visit next week"}))
        self.assertEqual(fam.post_json("/api/account/send/files/%s/delete" % f2["ref"], {}).status, 400)
        self.assertEqual(lead.post_json("/api/staff/send/%s/update" % ref, {"status": "plan_agreed"}).status, 422)
        ok(lead.post_json("/api/staff/send/%s/update" % ref, {"status": "plan_agreed",
                                                             "plan_summary": "Quiet corner available; 5-minute warnings."}))
        self.assertIn("recorded Ari's support plan", last_email_to(fam.email))
        pid = participant_id(mine["requests"][0]["child"]["ref"])
        with db.read() as c:
            self.assertEqual(c.execute("SELECT support_plan FROM participants WHERE id=?", (pid,)).fetchone()[0],
                             "Quiet corner available; 5-minute warnings.")
        with db.read() as c:
            iid = c.execute("SELECT id FROM send_intakes WHERE ref=?", (ref,)).fetchone()[0]
            self.assertEqual(c.execute("SELECT status FROM intray_items WHERE type='send_intake' AND entity_id=?",
                                       (iid,)).fetchone()[0], "done")

    def test_existing_child_and_erasure_removes_files(self):
        fam = register_family()
        child = complete_child(fam)
        f = ok(upload(fam, "photo.png", PNG, kind="other_plan")).json()["file"]
        ok(fam.post_json("/api/account/send", dict(FORM, ehcp="no", child_ref=child, files=[f["ref"]])))
        with db.read() as c:
            row = c.execute("SELECT * FROM private_files WHERE ref=?", (f["ref"],)).fetchone()
            aid = row["account_id"]
        path = os.path.join(private_files.folder(), row["stored_name"])
        self.assertTrue(os.path.exists(path))
        self.assertTrue(path.startswith(data_path("private")))
        with db.tx() as c:
            c.execute("UPDATE accounts SET status='closed' WHERE id=?", (aid,))
            gdpr.erase_account(c, aid)
        self.assertFalse(os.path.exists(path))
        with db.read() as c:
            self.assertIsNone(c.execute("SELECT good_day FROM send_intakes WHERE account_id=?", (aid,)).fetchone()[0])

    def test_unattached_uploads_are_purged(self):
        fam = register_family()
        f = ok(upload(fam, "x.pdf", PDF)).json()["file"]
        with db.tx() as c:
            c.execute("UPDATE private_files SET created_at='2000-01-01T00:00:00Z' WHERE ref=?", (f["ref"],))
        self.assertIn("removed 1", send_support.purge_unattached())
        self.assertEqual(fam.get("/api/account/send/files/" + f["ref"]).status, 404)

