#!/usr/bin/env python3
"""Load test: many families trying to book the same sessions at once.

Never run the seed step against the live site's data. Use a throwaway copy:

    export HAH_DATA_DIR=/tmp/hah-load            # an empty folder
    python3 server.py 8000 &                     # (once, so the database exists; then stop it or leave it)
    python3 tools/loadtest.py seed --families 200 --capacity 30
    python3 tools/loadtest.py run --url http://localhost:8000 --workers 50
    python3 tools/loadtest.py check              # no session overbooked?

"seed" creates fully registered families (signed in, with a complete child)
and one published activity with a few small sessions. "run" has every
family open the Book page, get a quote and confirm the same sessions,
all at once, and reports response times (target: 95% under a second).
"check" confirms no session has more confirmed places than its capacity.
Stdlib only, like the site."""
import argparse
import datetime
import http.client
import json
import os
import statistics
import sys
import threading
import time
import urllib.parse
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TOKENS = "loadtest-tokens.json"


def seed(args):
    from hah import app, booking_settings, config, db, family, security
    if not os.environ.get("HAH_DATA_DIR") or os.environ.get("RENDER"):
        sys.exit("Set HAH_DATA_DIR to a throwaway folder (and never run this on the live server).")
    app.prepare_data()
    with db.read() as c:
        if c.execute("SELECT COUNT(*) FROM accounts WHERE email NOT LIKE 'load%@example.invalid'").fetchone()[0]:
            sys.exit("This data folder has real-looking accounts in it — use an empty folder.")
    now, tokens = db.now(), []
    exp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.tx() as c:
        booking_settings.save(c, None, {"booking_live": True, "pay_later_for_all": True})
        aid = c.execute("INSERT INTO activities(slug, title, category_id, centre_id, status, registration_level,"
                        " min_age_months, max_age_months, price_pence, allow_pay_later, capacity_default, created_at,"
                        " updated_at) VALUES (?, 'Load Test Club', 2, 1, 'published', 'full', 60, 155, 2500, 1, ?, ?, ?)",
                        ("load-test-%d" % int(time.time()), args.capacity, now, now)).lastrowid
        sids = []
        for i in range(args.sessions):
            day = (datetime.date.today() + datetime.timedelta(days=14 + i)).isoformat()
            sids.append(c.execute("INSERT INTO activity_sessions(activity_id, date, start_time, end_time, capacity,"
                                  " created_at) VALUES (?,?,?,?,?,?)", (aid, day, "10:00", "15:00", args.capacity,
                                                                         now)).lastrowid)
        pw = security.hash_password("load test password", iterations=1000)
        for n in range(args.families):
            acc = c.execute(
                "INSERT INTO accounts(ref, kind, email, email_verified_at, password_hash, status, first_name, last_name,"
                " mobile, address_line1, town, postcode, source, created_at, updated_at, reconfirmed_at)"
                " VALUES (?, 'family', ?, ?, ?, 'active', 'Load', ?, '+447700900000', '1 Test St', 'Bournemouth',"
                " 'BH1 1AA', 'self', ?, ?, ?)",
                (family.new_ref("A"), "load%d@example.invalid" % n, now, pw, "Family%d" % n, now, now, now)).lastrowid
            for i in (1, 2):
                c.execute("INSERT INTO emergency_contacts(account_id, full_name, relationship, phone, can_collect,"
                          " priority, created_at, updated_at) VALUES (?, 'Contact', 'Aunt', '01202000000', 1, ?, ?, ?)",
                          (acc, i, now, now))
            ref = family.new_ref("P")
            pid = c.execute("INSERT INTO participants(ref, account_id, first_name, last_name, dob, education, school_name,"
                            " haf_status, target_level, created_at, updated_at) VALUES (?,?, 'Child', ?, '2018-01-01',"
                            " 'school', 'Test School', 'not_eligible', 'full', ?, ?)",
                            (ref, acc, "Family%d" % n, now, now)).lastrowid
            c.execute("INSERT INTO participant_gp(participant_id, surgery_name, surgery_phone, updated_at) VALUES"
                      " (?, 'Surgery', '01202000000', ?)", (pid, now))
            c.execute("INSERT INTO participant_health(participant_id, updated_at) VALUES (?, ?)", (pid, now))
            c.execute("INSERT INTO participant_safeguarding(participant_id, updated_at) VALUES (?, ?)", (pid, now))
            family.set_collection_password(c, pid, "load test")
            for k, v in (("photo", "none"), ("first_aid", "yes"), ("plasters", "yes"), ("emergency_treatment", "yes"),
                         ("go_home_alone", "no")):
                family.record_consent(c, acc, pid, k, v, "profile")
            for k in ("info_correct", "privacy_ack"):
                family.record_consent(c, acc, None, k, "yes", "profile")
            if family.compute_level(c, pid) != "full":
                sys.exit("Seeded child isn't complete: %s" % family.missing(c, pid, "full"))
            token, csrf = security.new_token(), security.new_token(24)
            c.execute("INSERT INTO account_sessions(token_hash, account_id, csrf_token, created_at, last_seen_at,"
                      " idle_expires_at, expires_at) VALUES (?,?,?,?,?,?,?)",
                      (security.hash_token(token), acc, csrf, now, now, exp, exp))
            tokens.append({"cookie": token, "csrf": csrf, "child": ref})
    path = os.path.join(config.DATA, TOKENS)
    with open(path, "w") as f:
        json.dump({"sessions": sids, "families": tokens}, f)
    os.chmod(path, 0o600)
    print("Seeded %d families, %d sessions of %d places. Tokens in %s" % (args.families, len(sids), args.capacity, path))


def run(args):
    from hah import config
    with open(os.path.join(config.DATA, TOKENS)) as f:
        data = json.load(f)
    u = urllib.parse.urlparse(args.url)
    timings, statuses, lock = {"catalogue": [], "quote": [], "confirm": []}, {}, threading.Lock()
    queue = list(data["families"])
    start_gate = threading.Event()

    def call(conn, method, path, fam, body=None):
        headers = {"Cookie": "hah_acct=" + fam["cookie"]}
        if body is not None:
            headers.update({"Content-Type": "application/json", "X-CSRF-Token": fam["csrf"],
                            "Origin": "%s://%s" % (u.scheme, u.netloc)})
        t = time.perf_counter()
        conn.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
        r = conn.getresponse()
        r.read()
        return r.status, time.perf_counter() - t

    def worker():
        start_gate.wait()
        while True:
            with lock:
                if not queue:
                    return
                fam = queue.pop()
            cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
            conn = cls(u.hostname, u.port, timeout=60)
            items = [{"session_id": s, "participant": fam["child"]} for s in data["sessions"]]
            try:
                for name, method, path, body in (
                        ("catalogue", "GET", "/api/book/catalogue", None),
                        ("quote", "POST", "/api/book/quote", {"items": items}),
                        ("confirm", "POST", "/api/book/confirm", {"items": items, "pay_mode": "pay_later",
                                                                  "accept_terms": True,
                                                                  "idempotency_key": uuid.uuid4().hex})):
                    status, took = call(conn, method, path, fam, body)
                    with lock:
                        timings[name].append(took)
                        statuses.setdefault(name, {}).setdefault(status, 0)
                        statuses[name][status] += 1
            except OSError as e:
                with lock:
                    statuses.setdefault("errors", {}).setdefault(str(e)[:60], 0)
                    statuses["errors"][str(e)[:60]] += 1
            finally:
                conn.close()

    threads = [threading.Thread(target=worker) for _ in range(args.workers)]
    [t.start() for t in threads]
    t0 = time.perf_counter()
    start_gate.set()
    [t.join() for t in threads]
    total = time.perf_counter() - t0
    print("%d families in %.1fs with %d workers" % (len(data["families"]), total, args.workers))
    ok = True
    for name, xs in timings.items():
        if not xs:
            continue
        xs.sort()
        p95 = xs[int(len(xs) * .95) - 1]
        print("  %-9s n=%-4d median %4.0f ms  p95 %4.0f ms  max %4.0f ms  statuses %s" % (
            name, len(xs), statistics.median(xs) * 1000, p95 * 1000, xs[-1] * 1000, statuses.get(name)))
        ok = ok and p95 < 1.0
    if statuses.get("errors"):
        print("  connection errors:", statuses["errors"])
        ok = False
    print("p95 under 1 second: %s" % ("yes" if ok else "NO"))


def check(args):
    from hah import catalogue, db
    with db.read() as c:
        bad = c.execute("SELECT s.id, s.date, s.capacity, SUM(b.places) AS taken FROM activity_sessions s JOIN bookings b"
                        " ON b.session_id=s.id WHERE b.status IN " + catalogue.HOLDING_SQL + " GROUP BY s.id"
                        " HAVING taken > s.capacity").fetchall()
        waiting = c.execute("SELECT COUNT(*) FROM bookings WHERE status='waitlisted'").fetchone()[0]
    if bad:
        print("OVERBOOKED:", [dict(r) for r in bad])
        sys.exit(1)
    print("No session is overbooked (%d on waiting lists)." % waiting)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--families", type=int, default=200)
    s.add_argument("--capacity", type=int, default=30)
    s.add_argument("--sessions", type=int, default=3)
    r = sub.add_parser("run")
    r.add_argument("--url", default="http://localhost:8000")
    r.add_argument("--workers", type=int, default=50)
    sub.add_parser("check")
    args = p.parse_args()
    {"seed": seed, "run": run, "check": check}[args.cmd](args)


if __name__ == "__main__":
    main()
