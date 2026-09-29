"""Background jobs (backups now; reminders, waiting-list offers and the email
outbox as the booking system grows).

One worker runs per site: it takes an exclusive lock on data/worker.lock, so
a second process (e.g. during a deploy overlap) quietly stays idle. Every job
runs in its own thread with a time limit, so one slow job (a stuck upload)
can't hold up the others. Each run is recorded in scheduled_jobs, which is
what staff see under System status.

Register a job with:
    @worker.job("nightly_backup", daily_at="02:30", timeout=1800)
    def nightly_backup():
        return "short summary for the log"   # never personal data
"""
import datetime
import os
import sys
import threading
import time
import traceback

from . import config, db

try:
    import fcntl
except ImportError:  # pragma: no cover  (Windows dev machines: no lock, fine locally)
    fcntl = None

try:
    from zoneinfo import ZoneInfo
    LOCAL = ZoneInfo(config.TIMEZONE)
except Exception:  # pragma: no cover  (no tz database installed)
    LOCAL = datetime.timezone.utc

UTC = datetime.timezone.utc


class Job:
    def __init__(self, name, fn, every=None, daily_at=None, timeout=600):
        if (every is None) == (daily_at is None):
            raise ValueError("a job needs exactly one of every= or daily_at=")
        self.name, self.fn, self.every, self.daily_at, self.timeout = name, fn, every, daily_at, timeout

    def next_after(self, t):
        """The next time this job is due after UTC datetime T."""
        if self.every:
            return t + datetime.timedelta(seconds=self.every)
        hh, mm = map(int, self.daily_at.split(":"))
        local = t.astimezone(LOCAL)
        due = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if due <= local:
            due = (local + datetime.timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        return due.astimezone(UTC)


JOBS = []


def job(name, every=None, daily_at=None, timeout=600):
    def register(fn):
        JOBS.append(Job(name, fn, every=every, daily_at=daily_at, timeout=timeout))
        return fn
    return register


def _iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) if s else None


class Worker:
    def __init__(self, jobs=None, tick_seconds=5):
        self.jobs = list(JOBS if jobs is None else jobs)
        self.tick_seconds = tick_seconds
        self.running = {}  # job name -> (thread, started monotonic)
        self._stop = threading.Event()
        self._thread = None
        self._lock_file = None

    # ------------------------------------------------ lifecycle
    def acquire_lock(self):
        if fcntl is None:
            return True
        f = open(os.path.join(config.DATA, "worker.lock"), "a")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self._lock_file = f
        return True

    def start(self):
        """Start the background loop. False if another worker already runs."""
        if not self.acquire_lock():
            sys.stderr.write("[worker] another worker holds the lock; staying idle\n")
            return False
        self._thread = threading.Thread(target=self._loop, name="worker", daemon=True)
        self._thread.start()
        return True

    def stop(self, wait=10):
        self._stop.set()
        if self._thread:
            self._thread.join(wait)
        if self._lock_file:
            self._lock_file.close()
            self._lock_file = None

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # never let the loop die
                traceback.print_exc()
            self._stop.wait(self.tick_seconds)

    # ------------------------------------------------ scheduling
    def tick(self, now=None, wait=False):
        """Start every job that's due. With WAIT, run them to completion
        (tests); otherwise each runs in its own thread."""
        now = now or datetime.datetime.now(UTC)
        self._check_timeouts()
        with db.read() as c:
            state = {r["name"]: r for r in c.execute("SELECT * FROM scheduled_jobs")}
        started = []
        for j in self.jobs:
            if j.name in self.running:
                continue
            row = state.get(j.name)
            due = _parse(row["next_run_at"]) if row else None
            if due is None:
                # first time we've seen this job: interval jobs run now, daily jobs wait for their time
                due = now if j.every else j.next_after(now)
                with db.tx() as c:
                    c.execute("INSERT INTO scheduled_jobs(name, next_run_at) VALUES (?,?) "
                              "ON CONFLICT(name) DO UPDATE SET next_run_at=excluded.next_run_at",
                              (j.name, _iso(due)))
            if due <= now:
                started.append(self._start(j, now, wait))
        return [s for s in started if s]

    def run_now(self, name, wait=True):
        """Run one job immediately (the admin "Back up now" button, tests)."""
        j = next(j for j in self.jobs if j.name == name)
        if j.name in self.running:
            return None
        return self._start(j, datetime.datetime.now(UTC), wait)

    def _start(self, j, now, wait):
        with db.tx() as c:
            c.execute("INSERT INTO scheduled_jobs(name, next_run_at, last_started_at, last_status) "
                      "VALUES (?,?,?, 'running') ON CONFLICT(name) DO UPDATE SET "
                      "next_run_at=excluded.next_run_at, last_started_at=excluded.last_started_at, "
                      "last_status='running', last_error=NULL",
                      (j.name, _iso(j.next_after(now)), _iso(now)))
        t = threading.Thread(target=self._run, args=(j,), name="job:" + j.name, daemon=True)
        self.running[j.name] = (t, time.monotonic())
        t.start()
        if wait:
            t.join()
        return j.name

    def _run(self, j):
        status, error, detail = "ok", None, None
        try:
            detail = j.fn()
        except Exception as e:
            status, error = "failed", ("%s: %s" % (type(e).__name__, e))[:500]
            sys.stderr.write("[worker] %s failed: %s\n" % (j.name, error))
        try:
            with db.tx() as c:
                # a job that overran keeps its timed_out mark
                c.execute("UPDATE scheduled_jobs SET last_finished_at=?, last_error=?, last_detail=?, "
                          "last_status=CASE WHEN last_status='timed_out' AND ?='ok' THEN 'timed_out' ELSE ? END "
                          "WHERE name=?",
                          (db.now(), error, (str(detail)[:500] if detail is not None else None),
                           status, status, j.name))
        finally:
            self.running.pop(j.name, None)

    def _check_timeouts(self):
        for name, (t, began) in list(self.running.items()):
            j = next((j for j in self.jobs if j.name == name), None)
            if j and t.is_alive() and time.monotonic() - began > j.timeout:
                with db.tx() as c:
                    c.execute("UPDATE scheduled_jobs SET last_status='timed_out', "
                              "last_error=? WHERE name=? AND last_status='running'",
                              ("still running after %ds" % j.timeout, name))
                sys.stderr.write("[worker] %s is taking longer than %ds\n" % (name, j.timeout))


def status():
    """Job states for the admin's System status panel."""
    with db.read() as c:
        rows = {r["name"]: dict(r) for r in c.execute("SELECT * FROM scheduled_jobs")}
    return [dict(rows.get(j.name, {"name": j.name}), name=j.name,
                 schedule=("daily at %s (UK time)" % j.daily_at) if j.daily_at else "every %ds" % j.every)
            for j in JOBS]
