"""Running the system day to day:

* Email wording — staff can add a short note above the standard wording of
  any family email (the standard text stays locked, so legal and safety
  wording can't be lost by accident).
* The staff daily digest — an optional morning email with today's numbers
  and what's waiting in the in-tray. Counts only: never names or health
  details.
* Disk alerts — a warning in the in-tray (and by email to owners) when the
  server's disk is nearly full, before backups or the database fail."""
import datetime
import os
import shutil

from . import audit, catalogue, config, db, intray, outbox, permissions, templating, validate, worker
from .web import route, site_url, staff_url

NOT_EDITABLE = ("_layout", "staff_invite", "staff_reset", "test_email", "contact_notification", "send_intake_staff",
                "staff_digest", "disk_low")
MAX_INTRO = 600
DISK_MIN_FREE_MB = 500
DISK_MIN_FREE_PCT = 10


def templates():
    folder = os.path.join(templating.TEMPLATES, "email")
    return sorted(fn[:-4] for fn in os.listdir(folder) if fn.endswith(".txt") and fn[:-4] not in NOT_EDITABLE)


class _Sample(dict):
    """Shows placeholders as [name] in previews."""

    def get(self, key, default=None):
        return "[%s]" % key


# ---------------------------------------------------------------- email wording


@route("GET", "/api/staff/email-wording", auth="staff", perm="settings.manage")
def wording_list(h):
    with db.read() as c:
        intros = {r["template_key"]: r["intro"] for r in c.execute("SELECT * FROM email_intros")}
    out = []
    for key in templates():
        subject, text, _ = templating.render_email(key, _Sample())
        out.append({"key": key, "subject": subject, "text": text.replace(templating.SECRET, "[one-time link]"),
                    "intro": intros.get(key, "")})
    return h.json({"templates": out, "max": MAX_INTRO})


@route("POST", "/api/staff/email-wording/<key>", auth="staff", perm="settings.manage")
def wording_save(h, key):
    if key not in templates():
        raise LookupError
    intro = validate.long_text((h.json_body() or {}).get("intro"), MAX_INTRO + 1) or ""
    if len(intro) > MAX_INTRO:
        raise ValueError("Keep the note under %d characters." % MAX_INTRO)
    with db.tx() as c:
        if intro:
            c.execute("INSERT INTO email_intros(template_key, intro, updated_by, updated_at) VALUES (?,?,?,?)"
                      " ON CONFLICT(template_key) DO UPDATE SET intro=excluded.intro, updated_by=excluded.updated_by,"
                      " updated_at=excluded.updated_at", (key, intro, h.staff()["id"], db.now()))
        else:
            c.execute("DELETE FROM email_intros WHERE template_key=?", (key,))
        audit.record(c, h, "email_wording.update", details={"template": key, "cleared": not intro})
    subject, text, _ = templating.render_email(key, _Sample(), intro)
    return h.json({"ok": True, "text": text.replace(templating.SECRET, "[one-time link]")})


# ---------------------------------------------------------------- the daily digest


@route("POST", "/api/staff/me/digest", auth="staff")
def digest_pref(h):
    on = (h.json_body() or {}).get("on") is True
    with db.tx() as c:
        c.execute("UPDATE staff_users SET daily_digest=? WHERE id=?", (1 if on else 0, h.staff()["id"]))
    return h.json({"ok": True, "on": on})


def digest_for(c, staff, today):
    perms = permissions.perms_for(staff["roles"])
    t = today.isoformat()
    lines = []
    if "registers.view" in perms or "bookings.view" in perms:
        rows = c.execute("SELECT a.title, s.start_time, s.capacity, (SELECT COALESCE(SUM(b.places),0) FROM bookings b"
                         " WHERE b.session_id=s.id AND b.status='confirmed') AS booked FROM activity_sessions s"
                         " JOIN activities a ON a.id=s.activity_id WHERE s.date=? AND s.status='scheduled'"
                         " ORDER BY s.start_time", (t,)).fetchall()
        lines.append("Today: %d session%s" % (len(rows), "" if len(rows) == 1 else "s"))
        lines += ["  %s %s — %d of %d booked" % (r["start_time"], r["title"], r["booked"], r["capacity"]) for r in rows]
    if "bookings.view" in perms:
        y = c.execute("SELECT COUNT(*) FROM bookings WHERE created_at>=? AND created_at<? AND status<>'expired'",
                      ((today - datetime.timedelta(days=1)).isoformat(), t)).fetchone()[0]
        lines.append("New bookings yesterday: %d" % y)
    if "finance.view" in perms:
        n, pence = c.execute("SELECT COUNT(*), COALESCE(SUM(total_pence-paid_pence-credited_pence),0) FROM invoices"
                             " WHERE status IN ('issued','part_paid') AND due_date<?", (t,)).fetchone()
        lines.append("Overdue invoices: %d (£%.2f)" % (n, pence / 100))
    cond = "required_perm IN (%s)" % ",".join("?" * len(perms)) if perms else "0"
    items = c.execute("SELECT type, COUNT(*) AS n FROM intray_items WHERE status<>'done' AND " + cond +
                      " GROUP BY type ORDER BY n DESC", sorted(perms)).fetchall()
    total = sum(r["n"] for r in items)
    lines.append("")
    lines.append("In-tray: %d waiting" % total)
    lines += ["  %s: %d" % (intray.TYPE_NAMES.get(r["type"], r["type"]), r["n"]) for r in items[:12]]
    return "\n".join(lines), total


@worker.job("staff_digest", daily_at="07:20", timeout=300)
def send_digests(today=None):
    today = today or catalogue.uk_today()
    sent = 0
    with db.tx() as c:
        for s in c.execute("SELECT * FROM staff_users WHERE daily_digest=1 AND status='active'").fetchall():
            roles = [r[0] for r in c.execute("SELECT role FROM staff_roles WHERE staff_id=?", (s["id"],))]
            body, _ = digest_for(c, {"roles": roles}, today)
            outbox.email(c, s["email"], "staff_digest", {"first_name": s["name"].split(" ")[0], "day":
                                                         today.strftime("%A %-d %B"), "summary": body,
                                                         "admin_url": staff_url(None) + "/admin"},
                         kind="staff", staff_id=s["id"])
            sent += 1
    return "sent %d" % sent if sent else None


# ---------------------------------------------------------------- disk space


def disk():
    u = shutil.disk_usage(config.DATA)
    return {"total_mb": u.total // 2**20, "free_mb": u.free // 2**20,
            "free_pct": round(100 * u.free / u.total, 1) if u.total else 0}


@worker.job("disk_check", every=3600, timeout=60)
def disk_check(usage=None):
    d = usage or disk()
    if d["free_mb"] >= DISK_MIN_FREE_MB and d["free_pct"] >= DISK_MIN_FREE_PCT:
        with db.tx() as c:
            intray.resolve(c, "disk_low", entity_type="system", entity_id=1)
        return None
    with db.tx() as c:
        new = intray.add(c, "disk_low", "The server's disk is nearly full (%d MB, %.0f%% free) — backups and bookings"
                         " may fail" % (d["free_mb"], d["free_pct"]), perm="system.view", entity_type="system", entity_id=1)
        if new:
            for s in c.execute("SELECT s.email, s.name, s.id FROM staff_users s JOIN staff_roles r ON r.staff_id=s.id"
                               " WHERE r.role='owner' AND s.status='active'").fetchall():
                outbox.email(c, s["email"], "disk_low", {"first_name": s["name"].split(" ")[0],
                                                         "free_mb": d["free_mb"], "free_pct": d["free_pct"]},
                             kind="staff", staff_id=s["id"])
    return "low: %d MB free" % d["free_mb"]
