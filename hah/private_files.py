"""Private files: EHCPs and other plans families upload for the SEND lead.

They're kept in data/private/ (never under the public folder, so there's
no web address that serves them directly), under random names, and only
come back through routes that check who's asking: the family who uploaded
them, or staff with send.view. Downloads are always attachments (never shown
in the browser) and every staff download is logged.

Accepted: PDF, Word (.docx), JPEG and PNG, up to 10 MB, checked from the
file's contents rather than its name. Location and camera details are
removed from photos."""
import hashlib
import io
import os
import re
import secrets
import zipfile

from . import config, db, images

MAX_BYTES = 10 * 1024 * 1024
TYPES = {"pdf": ("application/pdf", ".pdf"),
         "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
         "jpeg": ("image/jpeg", ".jpg"), "png": ("image/png", ".png")}


class Rejected(ValueError):
    pass


def folder():
    path = os.path.join(config.DATA, "private")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def sniff(data):
    """The real type of DATA, or None."""
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile:
            return None
        if "word/document.xml" in names and "[Content_Types].xml" in names:
            return "docx"
        return None
    kind = images.sniff(data)
    return kind if kind in ("jpeg", "png") else None


def tidy_name(filename, ext):
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    stem = re.sub(r"[^\w .()-]+", "", stem).strip(" .")[:80] or "document"
    return stem + ext


def save(c, *, account_id, kind, filename, data, participant_id=None, intake_id=None, staff_id=None):
    """Check and store an upload. Returns the private_files row."""
    if not data:
        raise Rejected("That file is empty.")
    if len(data) > MAX_BYTES:
        raise Rejected("That file is over 10 MB — try a smaller scan or photo.")
    t = sniff(data)
    if not t:
        raise Rejected("Please upload a PDF, a Word document (.docx) or a photo (JPEG or PNG).")
    if t == "jpeg":
        try:
            data = images.strip_jpeg_metadata(data)
        except ValueError:
            raise Rejected("That photo couldn't be read — try saving it again as a JPEG.")
    content_type, ext = TYPES[t]
    stored = secrets.token_hex(16) + ext
    path = os.path.join(folder(), stored)
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(path, 0o600)
    fid = c.execute("INSERT INTO private_files(ref, account_id, participant_id, intake_id, kind, filename, content_type,"
                    " size, sha256, stored_name, uploaded_by_staff, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (secrets.token_urlsafe(18), account_id, participant_id, intake_id, kind, tidy_name(filename, ext),
                     content_type, len(data), hashlib.sha256(data).hexdigest(), stored, staff_id, db.now())).lastrowid
    return c.execute("SELECT * FROM private_files WHERE id=?", (fid,)).fetchone()


def read(row):
    with open(os.path.join(folder(), row["stored_name"]), "rb") as f:
        return f.read()


def delete(c, row):
    c.execute("UPDATE private_files SET deleted_at=? WHERE id=?", (db.now(), row["id"]))
    try:
        os.remove(os.path.join(folder(), row["stored_name"]))
    except FileNotFoundError:
        pass


def send(h, row):
    """Hand the file back as a download — never displayed inline."""
    data = read(row)
    safe = re.sub(r'[^\w .()-]', "_", row["filename"])
    return h.send(200, data, row["content_type"], {
        "Content-Disposition": 'attachment; filename="%s"' % safe, "Cache-Control": "no-store",
        "Content-Security-Policy": "sandbox; default-src 'none'", "X-Robots-Tag": "noindex"})


def as_json(row):
    return {"ref": row["ref"], "kind": row["kind"], "filename": row["filename"], "size": row["size"],
            "content_type": row["content_type"], "created_at": row["created_at"]}


def parse_upload(h):
    """(filename, bytes, form fields) from a multipart request with one file."""
    from email.parser import BytesParser
    from email.policy import HTTP
    ctype = h.headers.get("Content-Type") or ""
    if "multipart/form-data" not in ctype or "boundary=" not in ctype:
        raise ValueError("expected a file upload")
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + h.body())
    fields, found = {}, None
    for part in msg.iter_parts() if msg.is_multipart() else ():
        name = part.get_param("name", header="content-disposition")
        if part.get_filename():
            if found is None:
                found = (part.get_filename(), part.get_payload(decode=True) or b"")
        elif name:
            fields[name] = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")[:200]
    if not found:
        raise ValueError("no file found in the upload")
    return found[0], found[1], fields
