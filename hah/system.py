"""Housekeeping jobs, the health check, and the admin's System status panel."""
import datetime
import os

from . import backup, config, db, worker
from .web import route

# the running Worker (set by app.main; tests make their own)
WORKER = None


@worker.job("nightly_backup", daily_at="02:30", timeout=1800)
def nightly_backup():
    return backup.run_nightly()


@worker.job("db_maintenance", daily_at="03:15", timeout=600)
def db_maintenance():
    with db.read() as c:
        result = c.execute("PRAGMA quick_check").fetchone()[0]
        c.execute("PRAGMA optimize")
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    if result != "ok":
        raise RuntimeError("database integrity check failed: %s" % result[:200])
    return "integrity ok"


@route("GET", "/healthz")
def healthz(h):
    """For Render's health check: is the web process up and the database
    answering? (Background jobs are reported to staff, not here — a slow
    backup must never get the site restarted.)"""
    ok = db.health()
    return h.json({"ok": ok}, 200 if ok else 503)


def backup_stale(jobs, now=None):
    """True if there hasn't been a successful backup in the last 26 hours."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    nightly = next((j for j in jobs if j["name"] == "nightly_backup"), {})
    done = nightly.get("last_finished_at")
    if not done or nightly.get("last_status") != "ok":
        return True
    finished = datetime.datetime.strptime(done, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    return now - finished > datetime.timedelta(hours=26)


@route("GET", "/api/admin/system-status", auth="staff", perm="system.view")
def system_status(h):
    jobs = worker.status()
    local = []
    if os.path.isdir(config.BACKUPS):
        local = sorted(({"name": fn, "kb": os.path.getsize(os.path.join(config.BACKUPS, fn)) // 1024}
                        for fn in os.listdir(config.BACKUPS) if fn.startswith(backup.PREFIX)),
                       key=lambda b: b["name"], reverse=True)
    return h.json({
        "database_ok": db.health(),
        "database_kb": os.path.getsize(config.DB_PATH) // 1024 if os.path.exists(config.DB_PATH) else 0,
        "jobs": jobs,
        "worker_running": WORKER is not None,
        "backup_stale": backup_stale(jobs),
        "offsite_backup_configured": backup.offsite_configured(),
        "local_backups": local,
    })


@route("POST", "/api/admin/backup-now", auth="staff", perm="system.view")
def backup_now(h):
    if WORKER is None:
        return h.json({"error": "The background worker isn't running on this server."}, 503)
    started = WORKER.run_now("nightly_backup", wait=False)
    return h.json({"ok": True, "started": bool(started)}, 202)
