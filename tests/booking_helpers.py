"""Helpers for booking tests: activities straight into the database, and a
fake Stripe API server."""
import datetime
import hashlib
import hmac
import itertools
import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hah import booking_settings, db

_n = itertools.count(1)


def future(days):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


def set_settings(**kw):
    with db.tx() as c:
        booking_settings.save(c, None, kw)


def make_activity(sessions=2, capacity=10, price=0, level="full", status="published", min_age=60, max_age=155,
                  first_day=10, **kw):
    """A published activity with SESSIONS daily sessions from FIRST_DAY days
    ahead. Returns (activity_id, [session ids])."""
    n = next(_n)
    cols = dict(slug="test-activity-%d" % n, title="Test Activity %d" % n, category_id=2, centre_id=1,
                status=status, min_age_months=min_age, max_age_months=max_age, registration_level=level,
                price_pence=price, capacity_default=capacity, parent_must_stay=1 if level in ("guest", "short") else 0,
                created_at=db.now(), updated_at=db.now())
    cols.update(kw)
    with db.tx() as c:
        aid = c.execute("INSERT INTO activities(%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                        list(cols.values())).lastrowid
        sids = [c.execute("INSERT INTO activity_sessions(activity_id, date, start_time, end_time, capacity, created_at)"
                          " VALUES (?,?,?,?,?,?)", (aid, future(first_day + i), "10:00", "15:00", capacity,
                                                    db.now())).lastrowid for i in range(sessions)]
    return aid, sids


def booking(ref):
    with db.read() as c:
        return c.execute("SELECT * FROM bookings WHERE ref=?", (ref,)).fetchone()


# ---------------------------------------------------------------- fake Stripe


class FakeStripe:
    """Just enough of the Stripe API for Checkout Sessions and refunds."""

    def __init__(self):
        self.sessions, self.refunds, self.requests = {}, [], []
        self.payouts, self.balance_txns = [], []  # [{id, amount, ...}], [{..., "payout": po_id}]
        self.fail_next = False
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fake.requests.append((self.path, {}, dict(self.headers)))
                path, _, query = self.path.partition("?")
                q = dict(urllib.parse.parse_qsl(query))
                if path == "/v1/payouts":
                    return self._send(200, {"object": "list", "data": fake.payouts, "has_more": False})
                if path.startswith("/v1/payouts/"):
                    p = [x for x in fake.payouts if x["id"] == path.rsplit("/", 1)[-1]]
                    return self._send(200, p[0]) if p else self._send(404, {"error": {"message": "no such payout"}})
                if path == "/v1/balance_transactions":
                    rows = [t for t in fake.balance_txns if t.get("payout") == q.get("payout")]
                    if q.get("starting_after"):
                        rows = rows[[t["id"] for t in rows].index(q["starting_after"]) + 1:]
                    n = int(q.get("limit", 10))
                    return self._send(200, {"object": "list", "data": rows[:n], "has_more": len(rows) > n})
                sid = self.path.rsplit("/", 1)[-1]
                s = fake.sessions.get(sid)
                return self._send(200, s) if s else self._send(404, {"error": {"message": "no such session"}})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
                form = dict(urllib.parse.parse_qsl(raw))
                fake.requests.append((self.path, form, dict(self.headers)))
                if fake.fail_next:
                    fake.fail_next = False
                    return self._send(500, {"error": {"message": "boom"}})
                if self.path == "/v1/checkout/sessions":
                    sid = "cs_test_%d_%d" % (id(fake), len(fake.sessions))
                    total = sum(int(form["line_items[%d][price_data][unit_amount]" % i]) *
                                int(form["line_items[%d][quantity]" % i])
                                for i in range(100) if "line_items[%d][quantity]" % i in form)
                    fake.sessions[sid] = {"id": sid, "object": "checkout.session", "url": "https://stripe.test/pay/" + sid,
                                          "status": "open", "payment_status": "unpaid", "amount_total": total,
                                          "currency": "gbp", "client_reference_id": form.get("client_reference_id"),
                                          "metadata": {"checkout_ref": form.get("metadata[checkout_ref]")},
                                          "payment_intent": "pi_" + sid, "form": form}
                    return self._send(200, fake.sessions[sid])
                if self.path.endswith("/expire"):
                    sid = self.path.split("/")[-2]
                    fake.sessions[sid]["status"] = "expired"
                    return self._send(200, fake.sessions[sid])
                if self.path == "/v1/refunds":
                    r = {"id": "re_%d_%d" % (id(fake), len(fake.refunds)), "status": "succeeded", "amount": int(form["amount"]),
                         "payment_intent": form["payment_intent"]}
                    fake.refunds.append(r)
                    return self._send(200, r)
                return self._send(404, {"error": {"message": "unknown"}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def __enter__(self):
        self._env = {k: os.environ.get(k) for k in ("STRIPE_SECRET_KEY", "STRIPE_API_BASE", "STRIPE_WEBHOOK_SECRET")}
        os.environ.update(STRIPE_SECRET_KEY="sk_test_fake", STRIPE_WEBHOOK_SECRET="whsec_test",
                          STRIPE_API_BASE="http://127.0.0.1:%d" % self.httpd.server_address[1])
        return self

    def __exit__(self, *exc):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.httpd.shutdown()

    def pay(self, sid):
        s = self.sessions[sid]
        s.update(status="complete", payment_status="paid")
        return s

    def last_session(self):
        return list(self.sessions.values())[-1]


def webhook(client, event_type, obj, event_id=None, secret="whsec_test", t=None):
    body = json.dumps({"id": event_id or "evt_%d" % next(_n), "type": event_type,
                       "data": {"object": obj}}).encode()
    t = t or int(time.time())
    sig = hmac.new(secret.encode(), b"%d." % t + body, hashlib.sha256).hexdigest()
    return client.request("POST", "/api/stripe/webhook", body,
                          headers={"Content-Type": "application/json", "Stripe-Signature": "t=%d,v1=%s" % (t, sig)},
                          origin=False)
