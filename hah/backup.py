"""Nightly backups of everything under the data folder.

make_backup() writes data/backups/hah-YYYYMMDD-HHMMSS.tar.gz containing a
consistent snapshot of the booking database (SQLite's online backup), the
JSON files and uploaded images. Local copies are kept for a week — they sit
on the same disk as the live data, so they guard against mistakes, not disk
loss. Render's own daily disk snapshots are the second line.

When off-site backup is configured (see config.BACKUP_*), the archive is also
encrypted to the trustees' X.509 certificate with `openssl cms` (AES-256) and
uploaded to an S3-compatible bucket in the UK/EU. The server only ever holds
the public certificate, so it cannot read its own off-site backups; the
trustees keep the private key offline. To restore:

    openssl cms -decrypt -inform DER -binary -in hah-….tar.gz.p7m \\
        -inkey trustees-backup.key -out hah-….tar.gz
    tar -xzf hah-….tar.gz        # booking.db, *.json, uploads/
"""
import datetime
import hashlib
import hmac
import os
import sqlite3
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request

from . import config

KEEP_LOCAL_DAYS = 7
PREFIX = "hah-"
_JSON_FILES = ("content.json", "subscribers.json", "secrets.json")


def offsite_configured():
    return all((config.BACKUP_CERT, config.BACKUP_S3_ENDPOINT, config.BACKUP_S3_REGION,
                config.BACKUP_S3_BUCKET, config.BACKUP_S3_ACCESS_KEY, config.BACKUP_S3_SECRET_KEY))


def make_backup(when=None):
    """Write a local archive; returns its path."""
    when = when or datetime.datetime.now(datetime.timezone.utc)
    os.makedirs(config.BACKUPS, exist_ok=True)
    name = "%s%s.tar.gz" % (PREFIX, when.strftime("%Y%m%d-%H%M%S"))
    final = os.path.join(config.BACKUPS, name)
    with tempfile.TemporaryDirectory(dir=config.BACKUPS) as tmp:
        if os.path.exists(config.DB_PATH):
            snap = os.path.join(tmp, "booking.db")
            src, dst = sqlite3.connect(config.DB_PATH), sqlite3.connect(snap)
            try:
                src.backup(dst)  # consistent even while the site is writing
            finally:
                dst.close()
                src.close()
        part = final + ".part"
        with tarfile.open(part, "w:gz") as tar:
            if os.path.exists(os.path.join(tmp, "booking.db")):
                tar.add(os.path.join(tmp, "booking.db"), arcname="booking.db")
            for fn in _JSON_FILES:
                p = os.path.join(config.DATA, fn)
                if os.path.exists(p):
                    tar.add(p, arcname=fn)
            if os.path.isdir(config.UPLOADS):
                tar.add(config.UPLOADS, arcname="uploads")
        os.replace(part, final)
    os.chmod(final, 0o600)
    return final


def prune_local(today=None, keep_days=KEEP_LOCAL_DAYS):
    """Keep only the last KEEP_DAYS days of local archives. Returns the names removed."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    cutoff = today - datetime.timedelta(days=keep_days)
    removed = []
    if not os.path.isdir(config.BACKUPS):
        return removed
    for fn in os.listdir(config.BACKUPS):
        if not fn.startswith(PREFIX):
            continue
        try:
            made = datetime.datetime.strptime(fn[len(PREFIX):len(PREFIX) + 8], "%Y%m%d").date()
        except ValueError:
            continue
        if made <= cutoff:
            os.remove(os.path.join(config.BACKUPS, fn))
            removed.append(fn)
    return sorted(removed)


def encrypt(path, cert_pem):
    """PATH -> PATH.p7m, encrypted to CERT_PEM (openssl cms, AES-256, DER)."""
    out = path + ".p7m"
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        f.write(cert_pem)
        cert_file = f.name
    try:
        subprocess.run(["openssl", "cms", "-encrypt", "-binary", "-aes-256-cbc", "-outform", "DER",
                        "-in", path, "-out", out, cert_file],
                       check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as e:
        raise RuntimeError("encryption failed: %s" % e.stderr.decode(errors="replace").strip()[:300])
    finally:
        os.unlink(cert_file)
    return out


# ---------------------------------------------------------------- S3 upload (SigV4)


def _hmac(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_headers(method, url, region, access_key, secret_key, payload_sha256, amz_date,
                  extra_headers=None, service="s3"):
    """AWS Signature Version 4 headers for one request (works with any
    S3-compatible store). AMZ_DATE is 'YYYYMMDDTHHMMSSZ'."""
    u = urllib.parse.urlsplit(url)
    headers = {"host": u.netloc, "x-amz-content-sha256": payload_sha256, "x-amz-date": amz_date}
    for k, v in (extra_headers or {}).items():
        headers[k.lower()] = str(v).strip()
    signed = ";".join(sorted(headers))
    canonical_headers = "".join("%s:%s\n" % (k, headers[k]) for k in sorted(headers))
    query = "&".join("%s=%s" % (urllib.parse.quote(k, safe="-_.~"), urllib.parse.quote(v, safe="-_.~"))
                     for k, v in sorted(urllib.parse.parse_qsl(u.query, keep_blank_values=True)))
    canonical = "\n".join([method, urllib.parse.quote(u.path or "/", safe="/-_.~"), query,
                           canonical_headers, signed, payload_sha256])
    scope = "%s/%s/%s/aws4_request" % (amz_date[:8], region, service)
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), amz_date[:8]), region), service),
                "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {k: v for k, v in headers.items() if k != "host"}
    out["Authorization"] = ("AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
                            % (access_key, scope, signed, signature))
    return out


def upload(path, key, when=None):
    """PUT the file at PATH to the configured bucket as KEY (path-style URL)."""
    with open(path, "rb") as f:
        data = f.read()
    when = when or datetime.datetime.now(datetime.timezone.utc)
    url = "%s/%s/%s" % (config.BACKUP_S3_ENDPOINT.rstrip("/"), config.BACKUP_S3_BUCKET,
                        urllib.parse.quote(key, safe="/-_.~"))
    headers = sigv4_headers("PUT", url, config.BACKUP_S3_REGION, config.BACKUP_S3_ACCESS_KEY,
                            config.BACKUP_S3_SECRET_KEY, hashlib.sha256(data).hexdigest(),
                            when.strftime("%Y%m%dT%H%M%SZ"),
                            {"content-type": "application/pkcs7-mime"})
    req = urllib.request.Request(url, data=data, method="PUT", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        if resp.status >= 300:
            raise RuntimeError("upload failed: HTTP %s" % resp.status)
    return url


def run_nightly():
    """The nightly job: local archive, prune, and off-site copy if configured.
    Returns a short summary for the job log (no personal data)."""
    path = make_backup()
    removed = prune_local()
    size = os.path.getsize(path)
    summary = "local %s (%d KB)" % (os.path.basename(path), size // 1024)
    if removed:
        summary += "; pruned %d" % len(removed)
    if offsite_configured():
        enc = encrypt(path, config.BACKUP_CERT)
        try:
            upload(enc, "backups/" + os.path.basename(enc))
        finally:
            os.remove(enc)
        summary += "; off-site copy uploaded"
    else:
        summary += "; off-site backup NOT configured"
    return summary
