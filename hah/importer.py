"""Admin → Import families: bring the MagicBooking family export across.

Only identity and contact details come over, plus each child's name and
date of birth — no health, SEND or safeguarding information (families enter
that themselves when they activate). Login details can't be migrated, so
every imported family gets an "Activate your account" email with a one-time
link; activation asks for a child's date of birth as a check.

Steps: upload the CSV → match its columns to ours → dry run (nothing is
saved; problems are listed by row number) → import → send activation
emails when you're ready (3–4 weeks before bookings open). A batch can be
rolled back while none of its families have activated or booked."""
import csv
import datetime
import io
import json
import re

from . import accounts, audit, catalogue, db, family, validate, worker
from .web import route

FIELDS = {
    "parent_first_name": "Parent first name", "parent_last_name": "Parent last name", "email": "Email",
    "mobile": "Mobile", "address_line1": "Address line 1", "address_line2": "Address line 2", "town": "Town",
    "postcode": "Postcode", "legacy_ref": "Family ID (MagicBooking)", "child_first_name": "Child first name",
    "child_last_name": "Child last name", "child_dob": "Child date of birth",
}
REQUIRED = ("parent_first_name", "parent_last_name", "email")
GUESS = {
    "parent_first_name": r"(parent|carer|guardian|account).*(first|fore|given)|^first ?name$",
    "parent_last_name": r"(parent|carer|guardian|account).*(last|sur|family)|^(last ?name|surname)$",
    "email": r"e-?mail", "mobile": r"mobile|phone|tel", "address_line1": r"address.*1|^address$|street",
    "address_line2": r"address.*2", "town": r"town|city", "postcode": r"post ?code|zip",
    "legacy_ref": r"(family|account|customer).*(id|ref|number)",
    "child_first_name": r"(child|participant|attendee|member).*(first|fore|given)",
    "child_last_name": r"(child|participant|attendee|member).*(last|sur|family)",
    "child_dob": r"(dob|birth)",
}
MAX_BYTES = 5 * 1024 * 1024


def parse_csv(text):
    text = (text or "").lstrip("﻿")
    if "\ufffd" in text:  # characters that couldn't be decoded: stop rather than import a mangled "Si\ufffdn"
        raise ValueError("Some characters in this file couldn't be read (for example accented names). In Excel,"
                         " choose Save As → \"CSV UTF-8 (Comma delimited)\" and upload that file.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any(x.strip() for x in r)]
    if not rows:
        raise ValueError("The file is empty.")
    return [h.strip() for h in rows[0]], rows[1:]


def guess_mapping(headers):
    out = {}
    for field, pattern in GUESS.items():
        for h in headers:
            if re.search(pattern, h, re.I) and h not in out.values():
                out[field] = h
                break
    return out


def parse_dob(value, order):
    v = (value or "").strip()
    d = validate.date(v)
    if d:
        return d
    m = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})$", v)
    if not m:
        return None
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000 if y <= catalogue.uk_today().year % 100 else 1900
    day_, month = (a, b) if order == "dmy" else (b, a)
    try:
        return datetime.date(y, month, day_)
    except ValueError:
        return None


def plan(c, headers, rows, mapping, order="dmy"):
    """Work out what an import would do. Returns (families, results, stats)."""
    idx = {f: headers.index(col) for f, col in mapping.items() if col in headers}
    missing = [FIELDS[f] for f in REQUIRED if f not in idx]
    if missing:
        raise ValueError("Match these columns first: %s." % ", ".join(missing))
    get = (lambda r, f: r[idx[f]].strip() if f in idx and idx[f] < len(r) else "")
    families, results = {}, []
    for n, r in enumerate(rows, start=2):
        email = validate.email(get(r, "email"))
        first, last = validate.text(get(r, "parent_first_name"), 60), validate.text(get(r, "parent_last_name"), 60)
        if not email:
            results.append((n, "error", "No valid email address"))
            continue
        if not first or not last:
            results.append((n, "error", "Parent name missing"))
            continue
        fam = families.get(email)
        if fam is None:
            existing = c.execute("SELECT status FROM accounts WHERE email=? AND status<>'anonymised'", (email,)).fetchone()
            mobile_raw = get(r, "mobile")
            fam = families[email] = {
                "email": email, "first_name": first, "last_name": last,
                "mobile": validate.uk_mobile(mobile_raw) if mobile_raw else None,
                "address_line1": validate.text(get(r, "address_line1"), 120) or None,
                "address_line2": validate.text(get(r, "address_line2"), 120) or None,
                "town": validate.text(get(r, "town"), 80) or None,
                "postcode": validate.postcode(get(r, "postcode")) if get(r, "postcode") else None,
                "legacy_ref": validate.text(get(r, "legacy_ref"), 60) or None,
                "children": [], "rows": [], "exists": existing is not None}
        else:
            ref = validate.text(get(r, "legacy_ref"), 60) or None
            if ref and fam["legacy_ref"] and ref != fam["legacy_ref"]:
                results.append((n, "error", "Same email as row %d but a different Family ID — check which is right"
                                % fam["rows"][0]))
                continue
            if (first.lower(), last.lower()) != (fam["first_name"].lower(), fam["last_name"].lower()):
                results.append((n, "error", "Same email as row %d but a different parent name — check which is right"
                                % fam["rows"][0]))
                continue
        fam["rows"].append(n)
        if fam["exists"]:
            results.append((n, "skipped", "Already has an account on the new system"))
            continue
        cf = validate.text(get(r, "child_first_name"), 60)
        if not cf:
            results.append((n, "created" if len(fam["rows"]) == 1 else "skipped",
                            "Family only (no child on this row)" if len(fam["rows"]) == 1 else "No child on this row"))
            continue
        cl = validate.text(get(r, "child_last_name"), 60) or last
        dob = parse_dob(get(r, "child_dob"), order)
        if not dob or dob > catalogue.uk_today() or dob.year < 1990:
            results.append((n, "error", "Child's date of birth missing or not a date"))
            continue
        if any(k["first_name"].lower() == cf.lower() and k["dob"] == dob.isoformat() for k in fam["children"]):
            results.append((n, "skipped", "Same child listed twice"))
            continue
        fam["children"].append({"first_name": cf, "last_name": cl, "dob": dob.isoformat(), "row": n})
        results.append((n, "created" if len(fam["children"]) == 1 else "added_child", None))
    new = [f for f in families.values() if not f["exists"]]
    stats = {"rows": len(rows), "families": len(new), "children": sum(len(f["children"]) for f in new),
             "existing_families": sum(1 for f in families.values() if f["exists"]),
             "errors": sum(1 for r in results if r[1] == "error"),
             "no_mobile": sum(1 for f in new if not f["mobile"])}
    return families, results, stats


@route("POST", "/api/staff/import/preview", auth="staff", perm="import.run", body_limit=MAX_BYTES + 4096)
def preview(h):
    d = h.json_body() or {}
    headers, rows = parse_csv(d.get("csv"))
    return h.json({"headers": headers, "sample": rows[:5], "rows": len(rows), "fields": FIELDS,
                   "required": REQUIRED, "mapping": guess_mapping(headers)})


@route("POST", "/api/staff/import/run", auth="staff", perm="import.run", body_limit=MAX_BYTES + 4096)
def run(h):
    d = h.json_body() or {}
    headers, rows = parse_csv(d.get("csv"))
    mapping = {k: v for k, v in (d.get("mapping") or {}).items() if k in FIELDS and v}
    order = "mdy" if d.get("date_order") == "mdy" else "dmy"
    with db.tx() as c:
        families, results, stats = plan(c, headers, rows, mapping, order)
        report = [{"row": n, "result": res, "message": msg} for n, res, msg in results if res in ("error", "skipped")]
        if not d.get("commit"):
            return h.json({"dry_run": True, "stats": stats, "problems": report[:500]})
        now = db.now()
        bid = c.execute("INSERT INTO import_batches(filename, status, mapping, stats, created_by, created_at)"
                        " VALUES (?, 'committed', ?, ?, ?, ?)",
                        (validate.text(d.get("filename"), 200) or None, json.dumps(mapping), json.dumps(stats),
                         h.staff()["id"], now)).lastrowid
        by_row = {}
        for f in families.values():
            if f["exists"]:
                continue
            aid = c.execute(
                "INSERT INTO accounts(ref, kind, email, status, first_name, last_name, mobile, address_line1, address_line2,"
                " town, postcode, source, import_batch_id, legacy_ref, created_at, updated_at)"
                " VALUES (?, 'family', ?, 'pending_activation', ?,?,?,?,?,?,?, 'import', ?,?,?,?)",
                (family.new_ref("A"), f["email"], f["first_name"], f["last_name"], f["mobile"], f["address_line1"],
                 f["address_line2"], f["town"], f["postcode"], bid, f["legacy_ref"], now, now)).lastrowid
            by_row[f["rows"][0]] = (aid, None)
            for k in f["children"]:
                pid = c.execute("INSERT INTO participants(ref, account_id, first_name, last_name, dob, target_level,"
                                " needs_review, created_at, updated_at) VALUES (?,?,?,?,?, 'full', 1, ?, ?)",
                                (family.new_ref("P"), aid, k["first_name"], k["last_name"], k["dob"], now, now)).lastrowid
                by_row[k["row"]] = (aid, pid)
        raw = {n: r for n, r in enumerate(rows, start=2)}
        for n, res, msg in results:
            aid, pid = by_row.get(n, (None, None))
            c.execute("INSERT INTO import_rows(batch_id, row_no, raw, result, message, account_id, participant_id,"
                      " created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (bid, n, json.dumps(raw.get(n)), res, msg, aid, pid, now))
        audit.record(c, h, "import.commit", entity_type="import", entity_id=bid, details=stats)
    return h.json({"ok": True, "batch_id": bid, "stats": stats, "problems": report[:500]})


def _batch_json(c, b):
    counts = {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM accounts WHERE import_batch_id=? GROUP BY status",
                                            (b["id"],))}
    return {"id": b["id"], "filename": b["filename"], "status": b["status"], "created_at": b["created_at"],
            "stats": json.loads(b["stats"]), "activation_sent": b["activation_sent"],
            "waiting": counts.get("pending_activation", 0), "activated": counts.get("active", 0)}


@route("GET", "/api/staff/import/batches", auth="staff", perm="import.run")
def batches(h):
    with db.read() as c:
        return h.json({"batches": [_batch_json(c, b) for b in
                                   c.execute("SELECT * FROM import_batches ORDER BY id DESC").fetchall()],
                       "fields": FIELDS})


@route("GET", "/api/staff/import/<bid>/problems.csv", auth="staff", perm="import.run")
def problems_csv(h, bid):
    with db.read() as c:
        rows = [[r["row_no"], r["result"], r["message"] or ""] for r in c.execute(
            "SELECT * FROM import_rows WHERE batch_id=? AND result IN ('error','skipped') ORDER BY row_no", (int(bid),))]
    return h.csv("import-%s-problems.csv" % bid, ["Row", "Result", "Why"], rows)


@route("POST", "/api/staff/import/<bid>/rollback", auth="staff", perm="import.run")
def rollback(h, bid):
    """Undo a batch: removes its families that haven't activated or booked."""
    with db.tx() as c:
        b = c.execute("SELECT * FROM import_batches WHERE id=? AND status='committed'", (int(bid),)).fetchone()
        if not b:
            raise LookupError
        removed = kept = 0
        for a in c.execute("SELECT * FROM accounts WHERE import_batch_id=?", (b["id"],)).fetchall():
            busy = a["status"] != "pending_activation" or c.execute(
                "SELECT 1 FROM bookings WHERE account_id=? LIMIT 1", (a["id"],)).fetchone()
            if busy:
                kept += 1
                continue
            c.execute("UPDATE import_rows SET account_id=NULL, participant_id=NULL WHERE account_id=?", (a["id"],))
            c.execute("DELETE FROM consents WHERE account_id=?", (a["id"],))
            c.execute("DELETE FROM participants WHERE account_id=?", (a["id"],))
            c.execute("DELETE FROM message_deliveries WHERE account_id=?", (a["id"],))
            c.execute("DELETE FROM intray_items WHERE account_id=?", (a["id"],))
            c.execute("DELETE FROM accounts WHERE id=?", (a["id"],))
            removed += 1
        c.execute("UPDATE import_batches SET status='rolled_back', rolled_back_at=? WHERE id=?", (db.now(), b["id"]))
        audit.record(c, h, "import.rollback", entity_type="import", entity_id=b["id"],
                     details={"removed": removed, "kept": kept})
    return h.json({"ok": True, "removed": removed, "kept": kept})


@route("POST", "/api/staff/import/<bid>/activation", auth="staff", perm="import.run")
def send_activation(h, bid):
    """Queue "Activate your account" emails (the outbox sends them steadily)."""
    d = h.json_body() or {}
    reminder = bool(d.get("reminder"))
    with db.tx() as c:
        b = c.execute("SELECT * FROM import_batches WHERE id=? AND status='committed'", (int(bid),)).fetchone()
        if not b:
            raise LookupError
        sent = 0
        for a in c.execute("SELECT * FROM accounts WHERE import_batch_id=? AND status='pending_activation'"
                           " AND email IS NOT NULL", (b["id"],)).fetchall():
            if not reminder and c.execute("SELECT 1 FROM account_tokens WHERE account_id=? AND purpose='activate'",
                                          (a["id"],)).fetchone():
                continue
            accounts.send_activation(c, h, a, reminder=reminder)
            sent += 1
        c.execute("UPDATE import_batches SET activation_sent=activation_sent+? WHERE id=?", (sent, b["id"]))
        audit.record(c, h, "import.activation", entity_type="import", entity_id=b["id"], details={"sent": sent})
    return h.json({"ok": True, "sent": sent})


@worker.job("import_purge", daily_at="03:40", timeout=300)
def purge_raw_rows():
    """Raw import lines are only needed while the import is checked (30 days)."""
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.tx() as c:
        n = c.execute("UPDATE import_rows SET raw=NULL WHERE raw IS NOT NULL AND created_at<?", (cutoff,)).rowcount
    return "purged %d" % n


# ---------------------------------------------------------------- bookings already sold in MagicBooking


@route("POST", "/api/staff/bookings/legacy", auth="staff", perm="bookings.manage")
def legacy(h):
    """Key in places already sold in MagicBooking: they count towards
    capacity and appear on registers, but raise no invoice."""
    from . import bookings, waitlist
    d = h.json_body() or {}
    with db.tx() as c:
        p = c.execute("SELECT * FROM participants WHERE ref=? AND status='active'", (d.get("participant_ref") or "",)).fetchone()
        if not p:
            raise LookupError
        made, problems = [], []
        for sid in d.get("session_ids") or []:
            s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (int(sid),)).fetchone() if str(sid).isdigit() else None
            if not s:
                problems.append("Session %s not found" % sid)
                continue
            if c.execute("SELECT 1 FROM bookings WHERE session_id=? AND participant_id=? AND status NOT IN"
                         " ('cancelled','expired')", (s["id"], p["id"])).fetchone():
                problems.append("%s: already booked" % s["date"])
                continue
            if waitlist.free_places(c, s) < 1 and not d.get("override"):
                problems.append("%s: full (tick override to add anyway)" % s["date"])
                continue
            a = c.execute("SELECT * FROM activities WHERE id=?", (s["activity_id"],)).fetchone()
            item = {"kind": "participant", "session": s, "activity": a, "participant": p}
            b = bookings._insert_booking(c, item, "confirmed", account_id=p["account_id"], price=0,
                                         funding="prepaid_legacy", via="staff", staff_id=h.staff()["id"],
                                         notes=validate.text(d.get("note"), 200) or "Paid via MagicBooking")
            made.append(b["ref"])
        audit.record(c, h, "booking.legacy", entity_type="participant", entity_id=p["id"], participant_id=p["id"],
                     details={"created": len(made)})
    return h.json({"ok": True, "created": made, "problems": problems})
