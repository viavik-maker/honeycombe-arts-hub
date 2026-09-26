"""Background jobs, backups (local, encrypted, off-site upload) and the
admin System status panel."""
import datetime
import hashlib
import http.server
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest

from hah import backup, config, db, system, worker
from tests.support import ServerTestCase

UTC = datetime.timezone.utc
HAVE_OPENSSL = shutil.which("openssl") is not None


def job_row(name):
    with db.read() as c:
        return dict(c.execute("SELECT * FROM scheduled_jobs WHERE name=?", (name,)).fetchone())


class WorkerTest(ServerTestCase):
    def setUp(self):
        with db.tx() as c:
            c.execute("DELETE FROM scheduled_jobs")
        self.calls = []

    def make(self, **jobs):
        return worker.Worker(jobs=[worker.Job(name, fn, **kw) for name, (fn, kw) in jobs.items()])

    def test_interval_job_runs_now_daily_job_waits(self):
        w = self.make(tick=(lambda: self.calls.append("tick"), {"every": 60}),
                      nightly=(lambda: self.calls.append("nightly"), {"daily_at": "02:30"}))
        now = datetime.datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
        self.assertEqual(w.tick(now, wait=True), ["tick"])
        self.assertEqual(self.calls, ["tick"])
        # 02:30 UK summer time is 01:30 UTC the next morning
        self.assertEqual(job_row("nightly")["next_run_at"], "2026-09-27T01:30:00Z")
        self.assertEqual(w.tick(now + datetime.timedelta(seconds=30), wait=True), [])
        self.assertEqual(sorted(w.tick(datetime.datetime(2026, 9, 27, 1, 31, tzinfo=UTC), wait=True)),
                         ["nightly", "tick"])
        row = job_row("nightly")
        self.assertEqual(row["last_status"], "ok")
        self.assertEqual(row["next_run_at"], "2026-09-28T01:30:00Z")

    def test_daily_time_in_winter(self):
        j = worker.Job("x", lambda: None, daily_at="02:30")
        self.assertEqual(j.next_after(datetime.datetime(2026, 12, 1, 12, 0, tzinfo=UTC)),
                         datetime.datetime(2026, 12, 2, 2, 30, tzinfo=UTC))

    def test_failure_is_recorded_and_other_jobs_still_run(self):
        def boom():
            raise RuntimeError("disk full")
        w = self.make(bad=(boom, {"every": 60}), good=(lambda: "fine", {"every": 60}))
        w.tick(wait=True)
        self.assertEqual(job_row("bad")["last_status"], "failed")
        self.assertIn("disk full", job_row("bad")["last_error"])
        self.assertEqual(job_row("good")["last_status"], "ok")
        self.assertEqual(job_row("good")["last_detail"], "fine")

    def test_overrunning_job_is_flagged_and_not_restarted(self):
        release = threading.Event()
        w = self.make(slow=(lambda: release.wait(5), {"every": 1, "timeout": 0}))
        w.tick()
        time.sleep(0.05)
        self.assertEqual(w.tick(datetime.datetime.now(UTC) + datetime.timedelta(seconds=5)), [])
        self.assertEqual(job_row("slow")["last_status"], "timed_out")
        release.set()
        for _ in range(100):
            if "slow" not in w.running:
                break
            time.sleep(0.02)
        self.assertEqual(job_row("slow")["last_status"], "timed_out")  # stays flagged for staff

    def test_only_one_worker_per_site(self):
        a, b = worker.Worker(jobs=[]), worker.Worker(jobs=[])
        self.assertTrue(a.acquire_lock())
        try:
            self.assertFalse(b.acquire_lock())
        finally:
            a.stop()
        self.assertTrue(b.acquire_lock())
        b.stop()

    def test_background_loop_starts_and_stops(self):
        w = self.make(tick=(lambda: self.calls.append(1), {"every": 3600}))
        w.tick_seconds = 0.01
        self.assertTrue(w.start())
        for _ in range(100):
            if self.calls:
                break
            time.sleep(0.02)
        w.stop()
        self.assertEqual(self.calls, [1])


class LocalBackupTest(ServerTestCase):
    def test_archive_contains_consistent_database_and_files(self):
        with db.tx() as c:
            c.execute("INSERT INTO counters VALUES ('backup-check', 42)")
        with open(os.path.join(config.UPLOADS, "pic.png"), "wb") as f:
            f.write(b"\x89PNG fake")
        path = backup.make_backup()
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        with tarfile.open(path) as tar:
            names = set(tar.getnames())
            self.assertTrue({"booking.db", "content.json", "uploads/pic.png"} <= names)
            out = tempfile.mkdtemp()
            try:
                tar.extract("booking.db", out)
                import sqlite3
                c = sqlite3.connect(os.path.join(out, "booking.db"))
                self.assertEqual(c.execute("SELECT value FROM counters WHERE name='backup-check'")
                                 .fetchone()[0], 42)
                c.close()
            finally:
                shutil.rmtree(out)

    def test_pruning_keeps_a_week(self):
        os.makedirs(config.BACKUPS, exist_ok=True)
        for day in ("20260910", "20260919", "20260920", "20260926"):
            open(os.path.join(config.BACKUPS, "hah-%s-023000.tar.gz" % day), "w").close()
        open(os.path.join(config.BACKUPS, "notes.txt"), "w").close()
        removed = backup.prune_local(today=datetime.date(2026, 9, 26))
        self.assertEqual(removed, ["hah-20260910-023000.tar.gz", "hah-20260919-023000.tar.gz"])
        self.assertIn("notes.txt", os.listdir(config.BACKUPS))


class SigV4Test(unittest.TestCase):
    def test_matches_aws_documented_example(self):
        # "GET Object" example from the AWS Signature Version 4 documentation
        h = backup.sigv4_headers(
            "GET", "https://examplebucket.s3.amazonaws.com/test.txt", "us-east-1",
            "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            hashlib.sha256(b"").hexdigest(), "20130524T000000Z", {"Range": "bytes=0-9"})
        self.assertEqual(h["Authorization"],
                         "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
                         "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
                         "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


@unittest.skipUnless(HAVE_OPENSSL, "openssl not installed")
class OffsiteBackupTest(ServerTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.key, cert = os.path.join(self.tmp, "k.pem"), os.path.join(self.tmp, "c.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", self.key,
                        "-out", cert, "-days", "1", "-subj", "/CN=test trustees"],
                       check=True, capture_output=True)
        with open(cert) as f:
            self.cert = f.read()
        self.received = []
        received = self.received

        class FakeS3(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((self.path, self.headers, body))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.s3 = http.server.HTTPServer(("127.0.0.1", 0), FakeS3)
        threading.Thread(target=self.s3.serve_forever, daemon=True).start()
        self.saved = {k: getattr(config, k) for k in dir(config) if k.startswith("BACKUP_")}
        config.BACKUP_CERT = self.cert
        config.BACKUP_S3_ENDPOINT = "http://127.0.0.1:%d" % self.s3.server_address[1]
        config.BACKUP_S3_REGION, config.BACKUP_S3_BUCKET = "eu-west-2", "hah-backups"
        config.BACKUP_S3_ACCESS_KEY, config.BACKUP_S3_SECRET_KEY = "AKIATEST", "secret"

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(config, k, v)
        self.s3.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_encrypted_copy_is_uploaded_and_only_the_key_opens_it(self):
        summary = backup.run_nightly()
        self.assertIn("off-site copy uploaded", summary)
        self.assertEqual(len(self.received), 1)
        path, headers, body = self.received[0]
        self.assertRegex(path, r"^/hah-backups/backups/hah-\d{8}-\d{6}\.tar\.gz\.p7m$")
        self.assertTrue(headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIATEST/"))
        self.assertEqual(headers["x-amz-content-sha256"], hashlib.sha256(body).hexdigest())
        self.assertNotIn(b"content.json", body)  # not readable without the key
        enc = os.path.join(self.tmp, "b.p7m")
        with open(enc, "wb") as f:
            f.write(body)
        plain = os.path.join(self.tmp, "b.tar.gz")
        subprocess.run(["openssl", "cms", "-decrypt", "-inform", "DER", "-binary", "-in", enc,
                        "-inkey", self.key, "-out", plain], check=True, capture_output=True)
        with tarfile.open(plain) as tar:
            self.assertIn("content.json", tar.getnames())
        # the encrypted file isn't left lying around locally
        self.assertFalse([f for f in os.listdir(config.BACKUPS) if f.endswith(".p7m")])


class SystemStatusTest(ServerTestCase):
    def tearDown(self):
        system.WORKER = None

    def test_staff_only(self):
        self.assertEqual(self.client().get("/api/admin/system-status").status, 401)
        self.assertEqual(self.client().post_json("/api/admin/backup-now", {}).status, 401)

    def test_status_and_backup_now(self):
        admin = self.admin()
        st = admin.get("/api/admin/system-status").json()
        self.assertTrue(st["database_ok"])
        self.assertTrue(st["backup_stale"])
        self.assertFalse(st["offsite_backup_configured"])
        self.assertEqual({j["name"] for j in st["jobs"]}, {"nightly_backup", "db_maintenance"})

        self.assertEqual(admin.post_json("/api/admin/backup-now", {}).status, 503)  # no worker here
        system.WORKER = worker.Worker()
        self.assertEqual(admin.post_json("/api/admin/backup-now", {}).status, 202)
        for _ in range(200):
            if "nightly_backup" not in system.WORKER.running:
                break
            time.sleep(0.02)
        st = admin.get("/api/admin/system-status").json()
        self.assertFalse(st["backup_stale"])
        self.assertEqual(len(st["local_backups"]), 1)
        job = next(j for j in st["jobs"] if j["name"] == "nightly_backup")
        self.assertIn("off-site backup NOT configured", job["last_detail"])

    def test_db_maintenance_job(self):
        self.assertEqual(system.db_maintenance(), "integrity ok")
