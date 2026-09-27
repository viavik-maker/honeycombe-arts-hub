"""Backups: private files included, one-time links left out, and the
integrity header Object Lock buckets need on upload."""
import base64
import hashlib
import http.server
import os
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import unittest
import uuid

from hah import backup, config, db, outbox
from tests.support import ServerTestCase


class BackupContentsTest(ServerTestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def test_private_files_are_backed_up_with_their_modes(self):
        name = "ehcp-%s.pdf" % uuid.uuid4().hex
        private = os.path.join(config.DATA, "private")
        os.makedirs(private, mode=0o700, exist_ok=True)
        with open(os.path.join(private, name), "wb") as f:
            f.write(b"%PDF- an EHCP")
        os.chmod(os.path.join(private, name), 0o600)
        with tarfile.open(backup.make_backup()) as tar:
            member = tar.getmember("private/" + name)
            self.assertEqual(member.mode & 0o777, 0o600)
            self.assertEqual(tar.extractfile(member).read(), b"%PDF- an EHCP")

    def test_one_time_links_stay_out_of_the_backup(self):
        token = "LIVE-TOKEN-%s" % uuid.uuid4().hex
        with db.tx() as c:
            did = outbox.email(c, "reset-%s@example.org" % uuid.uuid4().hex[:8], "account_reset",
                               {"first_name": "Jo", "minutes": 30}, secret="https://site/reset-password#t=" + token)
        with tarfile.open(backup.make_backup()) as tar:
            tar.extract("booking.db", self.out)
        snap = sqlite3.connect(os.path.join(self.out, "booking.db"))
        try:
            status, secret = snap.execute("SELECT status, secret FROM message_deliveries WHERE id=?", (did,)).fetchone()
        finally:
            snap.close()
        self.assertIsNone(secret)
        self.assertEqual(status, "cancelled")  # a restore can't send it without the link
        with open(os.path.join(self.out, "booking.db"), "rb") as f:
            self.assertNotIn(token.encode(), f.read())  # not even in free pages
        with db.read() as c:  # the live message still goes out
            row = c.execute("SELECT status, secret FROM message_deliveries WHERE id=?", (did,)).fetchone()
        self.assertEqual(row["status"], "queued")
        self.assertIn(token, row["secret"])


class UploadIntegrityTest(unittest.TestCase):
    def test_signing_extra_headers_matches_aws_example(self):
        # "PUT Object" example from the AWS Signature Version 4 documentation
        h = backup.sigv4_headers(
            "PUT", "https://examplebucket.s3.amazonaws.com/test$file.text", "us-east-1",
            "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            hashlib.sha256(b"Welcome to Amazon S3.").hexdigest(), "20130524T000000Z",
            {"Date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-storage-class": "REDUCED_REDUNDANCY"})
        self.assertEqual(h["Authorization"],
                         "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
                         "SignedHeaders=date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class, "
                         "Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")

    def test_content_md5(self):
        self.assertEqual(backup.content_md5(b""), "1B2M2Y8AsgTpgAmY7PhCfg==")  # MD5 of nothing, base64

    def test_upload_sends_a_signed_content_md5(self):
        received = []

        class FakeS3(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):
                received.append((self.headers, self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        s3 = http.server.HTTPServer(("127.0.0.1", 0), FakeS3)
        threading.Thread(target=s3.serve_forever, daemon=True).start()
        saved = {k: getattr(config, k) for k in dir(config) if k.startswith("BACKUP_S3_")}
        config.BACKUP_S3_ENDPOINT = "http://127.0.0.1:%d" % s3.server_address[1]
        config.BACKUP_S3_REGION, config.BACKUP_S3_BUCKET = "eu-west-2", "hah-backups"
        config.BACKUP_S3_ACCESS_KEY, config.BACKUP_S3_SECRET_KEY = "AKIATEST", "secret"
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "b.p7m")
            with open(path, "wb") as f:
                f.write(b"encrypted backup bytes")
            backup.upload(path, "backups/b.p7m")
        finally:
            for k, v in saved.items():
                setattr(config, k, v)
            s3.shutdown()
            shutil.rmtree(tmp, ignore_errors=True)
        headers, body = received[0]
        self.assertEqual(headers["Content-MD5"], base64.b64encode(hashlib.md5(body).digest()).decode())
        self.assertIn("SignedHeaders=content-md5;", headers["Authorization"])  # signed, so it can't be altered in transit
