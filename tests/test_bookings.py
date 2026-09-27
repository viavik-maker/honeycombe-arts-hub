"""Activities admin, the Book page API, the booking engine, invoices and Stripe."""
import threading
import uuid

from hah import db, outbox, ratelimit, sms
from tests.booking_helpers import FakeStripe, booking, future, make_activity, set_settings, webhook
from tests.family_helpers import complete_child, last_email_to, ok, participant_id, register_family
from tests.support import ServerTestCase


def key():
    return uuid.uuid4().hex


class ActivitiesAdminTest(ServerTestCase):
    def test_create_generate_publish_duplicate_export(self):
        admin = self.admin()
        r = ok(admin.post_json("/api/staff/activities", {"title": "Autumn Half Term Club", "category_id": 2,
                                                        "centre_id": 1, "min_age_months": 72, "max_age_months": 155,
                                                        "registration_level": "full", "price_pence": 3000}))
        a = r.json()["activity"]
        self.assertEqual(a["slug"], "autumn-half-term-club")
        self.assertEqual(a["status"], "draft")
        aid = a["id"]
        # can't publish without sessions (and card payments aren't set up)
        r = admin.post_json("/api/staff/activities/%d/status" % aid, {"to": "published"})
        self.assertEqual(r.status, 409)
        self.assertTrue(any("session" in p for p in r.json()["problems"]))
        # generate: Mon–Fri over two weeks, skipping one day
        start = future(14)
        body = {"from": start, "to": future(27), "weekdays": [0, 1, 2, 3, 4], "start_time": "10:00",
                "end_time": "15:00", "capacity": 24, "skip_dates": [future(15)], "themes": ["Puppets", "Clay"],
                "dry_run": True}
        preview = ok(admin.post_json("/api/staff/activities/%d/sessions/generate" % aid, body)).json()
        self.assertTrue(preview["dry_run"])
        n = len(preview["sessions"])
        self.assertTrue(8 <= n <= 10, n)
        self.assertNotIn(future(15), [s["date"] for s in preview["sessions"]])
        with db.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM activity_sessions WHERE activity_id=?", (aid,)).fetchone()[0], 0)
        body["dry_run"] = False
        r = ok(admin.post_json("/api/staff/activities/%d/sessions/generate" % aid, body)).json()
        self.assertEqual(r["created"], n)
        self.assertEqual(r["activity"]["sessions"][0]["theme"], "Puppets")
        # generating again creates nothing new
        self.assertEqual(ok(admin.post_json("/api/staff/activities/%d/sessions/generate" % aid, body)).json()["created"], 0)
        # still no way to pay: allow pay later, then it can go live
        r = admin.post_json("/api/staff/activities/%d/status" % aid, {"to": "published"})
        self.assertEqual(r.status, 409)
        self.assertIn("card payments", r.json()["problems"][0])
        ok(admin.post_json("/api/staff/activities/%d/update" % aid, {"allow_pay_later": True}))
        r = ok(admin.post_json("/api/staff/activities/%d/status" % aid, {"to": "published"}))
        self.assertEqual(r.json()["activity"]["status"], "published")
        # lists and export
        rows = ok(admin.get("/api/staff/activities?tab=published")).json()["activities"]
        self.assertIn(aid, [x["id"] for x in rows])
        csv = admin.get("/api/staff/activities/%d/export.csv" % aid)
        self.assertEqual(csv.status, 200)
        self.assertIn("text/csv", csv.header("Content-Type"))
        self.assertIn("Puppets", csv.text)
        self.assertEqual(admin.get("/api/staff/activities.csv").status, 200)
        # duplicate for next term keeps weekdays (starting on a Monday, so the whole first week is kept)
        import datetime
        monday = datetime.date.fromisoformat(future(100))
        monday -= datetime.timedelta(days=monday.weekday())
        r = ok(admin.post_json("/api/staff/activities/%d/duplicate" % aid, {"new_start_date": monday.isoformat(),
                                                                            "title": "Spring Half Term Club"})).json()
        copy = r["activity"]
        self.assertEqual(copy["status"], "draft")
        self.assertEqual(len(copy["sessions"]), n)
        d0 = datetime.date.fromisoformat(r["activity"]["sessions"][0]["date"])
        self.assertEqual(d0.weekday(), datetime.date.fromisoformat(preview["sessions"][0]["date"]).weekday())
        self.assertGreaterEqual(d0, monday)

    def test_session_validation_and_capacity(self):
        admin = self.admin()
        aid, (sid, _) = make_activity(capacity=2)
        r = admin.post_json("/api/staff/activities/%d/sessions" % aid, {"date": future(3), "start_time": "15:00",
                                                                        "end_time": "10:00"})
        self.assertEqual(r.status, 422)
        self.assertIn("end_time", r.json()["errors"])
        r = admin.post_json("/api/staff/activities/%d/update" % aid, {"min_age_months": 100, "max_age_months": 50})
        self.assertEqual(r.status, 422)

    def test_permissions(self):
        aid, _ = make_activity()
        staff = self.admin(roles=("session_staff",))
        self.assertEqual(staff.get("/api/staff/activities").status, 200)
        self.assertEqual(staff.post_json("/api/staff/activities/%d/status" % aid, {"to": "archived"}).status, 403)
        self.assertEqual(self.client().get("/api/staff/activities").status, 401)

    def test_scheduled_publishing(self):
        from hah import activities
        aid, _ = make_activity(status="scheduled", publish_at="2000-01-01T00:00:00Z")
        activities.publish_scheduled()
        with db.read() as c:
            self.assertEqual(c.execute("SELECT status FROM activities WHERE id=?", (aid,)).fetchone()[0], "published")


class BookingFlowTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True, pay_later_for_all=False)

    def family(self, **kw):
        ratelimit.reset()  # registering many families from one test IP
        c = register_family()
        c.child = complete_child(c, **kw)
        return c

    def confirm(self, c, items, pay_mode=None, status=200):
        r = c.post_json("/api/book/confirm", {"items": items, "pay_mode": pay_mode, "accept_terms": True,
                                              "idempotency_key": key()})
        self.assertEqual(r.status, status, r.text)
        return r.json()

    def test_catalogue_hidden_until_live(self):
        set_settings(booking_live=False)
        make_activity()
        d = ok(self.client().get("/api/book/catalogue")).json()
        self.assertFalse(d["live"])
        self.assertEqual(d["activities"], [])
        fam = self.family()
        self.assertEqual(fam.post_json("/api/book/quote", {"items": []}).status, 403)

    def test_catalogue_shows_eligibility(self):
        aid, sids = make_activity(min_age=60, max_age=90)  # Maya is 8: too old
        fam = self.family()
        d = ok(fam.get("/api/book/catalogue")).json()
        act = [a for a in d["activities"] if a["id"] == aid][0]
        e = act["sessions"][0]["eligibility"][fam.child]
        self.assertFalse(e["ok"])
        self.assertEqual(e["code"], "age")
        # signed out: sessions but no personal eligibility
        d = ok(self.client().get("/api/book/catalogue")).json()
        self.assertNotIn("eligibility", [a for a in d["activities"] if a["id"] == aid][0]["sessions"][0])

    def test_free_booking_confirms_and_emails(self):
        aid, sids = make_activity(price=0)
        fam = self.family()
        d = self.confirm(fam, [{"session_id": sids[0], "participant": fam.child}])
        self.assertEqual(d["bookings"][0]["status"], "confirmed")
        text = last_email_to(fam.email)
        self.assertIn("Confirmed", text)
        with db.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM attendance a JOIN bookings b ON b.id=a.booking_id"
                                       " WHERE b.ref=?", (d["bookings"][0]["ref"],)).fetchone()[0], 1)
        # booking the same session again is refused
        r = fam.post_json("/api/book/quote", {"items": [{"session_id": sids[0], "participant": fam.child}]})
        self.assertEqual(r.json()["lines"][0]["problems"][0]["code"], "booked")

    def test_incomplete_profile_is_blocked_with_a_fix_link(self):
        aid, sids = make_activity(level="full")
        fam = register_family()
        ref = complete_child(fam, level="short", first_name="Leo", dob="2019-02-01")
        q = ok(fam.post_json("/api/book/quote", {"items": [{"session_id": sids[0], "participant": ref}]})).json()
        self.assertEqual(q["lines"][0]["outcome"], "blocked")
        self.assertEqual(q["lines"][0]["problems"][0]["code"], "level")
        self.assertIn("/account/family/%s" % ref, q["lines"][0]["fix_url"])
        self.confirm(fam, [{"session_id": sids[0], "participant": ref}], status=409)

    def test_pay_later_invoice_numbering_and_idor(self):
        set_settings(pay_later_for_all=True)
        aid, sids = make_activity(price=3000, allow_pay_later=1)
        fam = self.family()
        q = ok(fam.post_json("/api/book/quote", {"items": [{"session_id": s, "participant": fam.child}
                                                           for s in sids]})).json()
        self.assertEqual(q["total_pence"], 6000)
        self.assertIn("pay_later", q["pay_options"])
        self.assertNotIn("card", q["pay_options"])  # Stripe isn't set up in this test
        d = self.confirm(fam, [{"session_id": s, "participant": fam.child} for s in sids], "pay_later")
        self.assertTrue(all(b["status"] == "confirmed" for b in d["bookings"]))
        inv = d["bookings"][0]["invoice"]
        self.assertEqual(inv["balance_pence"], 6000)
        self.assertRegex(inv["number"], r"^HAH-\d{4}-\d{5}$")
        page = fam.get("/account/invoices/" + inv["number"])
        self.assertEqual(page.status, 200)
        self.assertIn("£60.00", page.text)
        self.assertIn("noindex", page.header("X-Robots-Tag"))
        # another family can't see it or cancel the booking
        other = self.family(first_name="Zed")
        self.assertEqual(other.get("/account/invoices/" + inv["number"]).status, 404)
        self.assertEqual(other.get("/api/account/invoices/" + inv["number"]).status, 404)
        self.assertEqual(other.post_json("/api/account/bookings/%s/cancel" % d["bookings"][0]["ref"], {}).status, 404)
        # next invoice is the next number
        d2 = self.confirm(other, [{"session_id": sids[0], "participant": other.child}], "pay_later")
        n1, n2 = int(inv["number"].rsplit("-", 1)[1]), int(d2["bookings"][0]["invoice"]["number"].rsplit("-", 1)[1])
        self.assertEqual(n2, n1 + 1)

    def test_pay_later_needs_permission(self):
        aid, sids = make_activity(price=3000, allow_pay_later=1)
        fam = self.family()
        q = ok(fam.post_json("/api/book/quote", {"items": [{"session_id": sids[0], "participant": fam.child}]})).json()
        self.assertEqual(q["pay_options"], ["voucher"])
        self.confirm(fam, [{"session_id": sids[0], "participant": fam.child}], "pay_later", status=409)

    def test_voucher_goes_to_approval_then_invoice(self):
        aid, sids = make_activity(price=2500)
        fam = self.family()
        d = self.confirm(fam, [{"session_id": sids[0], "participant": fam.child}], "voucher")
        b = d["bookings"][0]
        self.assertEqual(b["status"], "pending_approval")
        admin = self.admin()
        items = ok(admin.get("/api/staff/bookings?quick=approval")).json()["bookings"]
        bid = [x["id"] for x in items if x["ref"] == b["ref"]][0]
        r = ok(admin.post_json("/api/staff/bookings/%d/approve" % bid, {})).json()
        self.assertEqual(r["booking"]["status"], "confirmed")
        self.assertEqual(r["booking"]["invoice"]["balance_pence"], 2500)
        self.assertIn("Invoice", last_email_to(fam.email))

    def test_haf_claim_needs_verification(self):
        aid, sids = make_activity(haf_only=1, category_id=1)
        fam = self.family()
        pid = participant_id(fam.child)
        with db.tx() as c:
            c.execute("UPDATE participants SET haf_status='claimed_eligible' WHERE id=?", (pid,))
        d = self.confirm(fam, [{"session_id": sids[0], "participant": fam.child}])
        self.assertEqual(d["bookings"][0]["status"], "pending_approval")
        self.assertEqual(d["bookings"][0]["funding"], "haf")
        with db.read() as c:
            self.assertTrue(c.execute("SELECT 1 FROM intray_items WHERE type='haf_verify' AND participant_id=?"
                                      " AND status='open'", (pid,)).fetchone())
        admin = self.admin()
        bid = booking(d["bookings"][0]["ref"])["id"]
        ok(admin.post_json("/api/staff/bookings/%d/approve" % bid, {}))
        with db.read() as c:
            self.assertEqual(c.execute("SELECT haf_status FROM participants WHERE id=?", (pid,)).fetchone()[0], "verified")
            self.assertFalse(c.execute("SELECT 1 FROM intray_items WHERE type='haf_verify' AND participant_id=?"
                                       " AND status='open'", (pid,)).fetchone())
        # once verified, the next HAF day confirms straight away, and no invoice is raised
        d = self.confirm(fam, [{"session_id": sids[1], "participant": fam.child}])
        self.assertEqual(d["bookings"][0]["status"], "confirmed")
        self.assertNotIn("invoice", d["bookings"][0])
        # not eligible → can't book HAF at all
        with db.tx() as c:
            c.execute("UPDATE participants SET haf_status='not_eligible' WHERE id=?", (pid,))
        aid2, sids2 = make_activity(haf_only=1, category_id=1)
        q = ok(fam.post_json("/api/book/quote", {"items": [{"session_id": sids2[0], "participant": fam.child}]})).json()
        self.assertEqual(q["lines"][0]["problems"][0]["code"], "haf")

    def test_waitlist_offer_accept_and_cancel_with_credit(self):
        set_settings(pay_later_for_all=True, cancel_cutoff_hours=48)
        aid, (sid,) = make_activity(sessions=1, capacity=1, price=2000, allow_pay_later=1)
        first, second = self.family(), self.family(first_name="Leo")
        d1 = self.confirm(first, [{"session_id": sid, "participant": first.child}], "pay_later")
        self.assertEqual(d1["bookings"][0]["status"], "confirmed")
        q = ok(second.post_json("/api/book/quote", {"items": [{"session_id": sid, "participant": second.child}]})).json()
        self.assertEqual(q["lines"][0]["outcome"], "waitlist")
        d2 = self.confirm(second, [{"session_id": sid, "participant": second.child}])  # nothing to pay yet
        self.assertEqual(d2["bookings"][0]["status"], "waitlisted")
        mine = ok(second.get("/api/account/bookings")).json()
        self.assertEqual(mine["upcoming"][0]["position"], 1)
        # the first family cancels: unpaid, so the invoice is simply credited; the place is offered
        terms = ok(first.get("/api/account/bookings/%s/cancel-terms" % d1["bookings"][0]["ref"])).json()
        self.assertTrue(terms["allowed"])
        ok(first.post_json("/api/account/bookings/%s/cancel" % d1["bookings"][0]["ref"], {}))
        self.assertEqual(booking(d2["bookings"][0]["ref"])["status"], "offered")
        self.assertIn("A place has come up", last_email_to(second.email))
        self.assertTrue(any("place has come up" in body for _, body in sms.SENT))
        with db.read() as c:
            inv = c.execute("SELECT * FROM invoices WHERE number=?", (d1["bookings"][0]["invoice"]["number"],)).fetchone()
            self.assertEqual(inv["status"], "credited")
        # accept: pay later
        ok(second.post_json("/api/account/bookings/%s/accept" % d2["bookings"][0]["ref"], {"pay_mode": "pay_later"}))
        self.assertEqual(booking(d2["bookings"][0]["ref"])["status"], "confirmed")

    def test_paid_cancellation_becomes_credit_and_is_used(self):
        set_settings(pay_later_for_all=True, refund_days=7)
        aid, sids = make_activity(price=2000, allow_pay_later=1, first_day=20)
        fam = self.family()
        d = self.confirm(fam, [{"session_id": sids[0], "participant": fam.child}], "pay_later")
        number = d["bookings"][0]["invoice"]["number"]
        admin = self.admin()
        # reception takes cash for the invoice
        from hah import money
        with db.tx() as c:
            inv = c.execute("SELECT * FROM invoices WHERE number=?", (number,)).fetchone()
            p = money.record_payment(c, amount=2000, method="cash", account_id=inv["account_id"])
            money.allocate(c, p["id"], inv["id"], 2000)
        terms = ok(fam.get("/api/account/bookings/%s/cancel-terms" % d["bookings"][0]["ref"])).json()
        self.assertEqual(terms["money"], "credit")  # paid in cash, not by card
        self.assertEqual(terms["amount"], 2000)
        ok(fam.post_json("/api/account/bookings/%s/cancel" % d["bookings"][0]["ref"], {}))
        self.assertEqual(ok(fam.get("/api/account/bookings")).json()["credit_pence"], 2000)
        # the credit pays for the next booking automatically
        q = ok(fam.post_json("/api/book/quote", {"items": [{"session_id": sids[1], "participant": fam.child}]})).json()
        self.assertEqual((q["credit_pence"], q["due_now_pence"]), (2000, 0))
        self.assertIn("card", q["pay_options"])  # "card" with nothing to pay = use credit
        d = self.confirm(fam, [{"session_id": sids[1], "participant": fam.child}], "card")
        self.assertEqual(d["bookings"][0]["status"], "confirmed")
        self.assertEqual(d["bookings"][0]["invoice"]["balance_pence"], 0)
        self.assertEqual(ok(fam.get("/api/account/bookings")).json()["credit_pence"], 0)
        del admin

    def test_session_cancel_by_staff(self):
        aid, sids = make_activity(price=0)
        fam = self.family()
        self.confirm(fam, [{"session_id": sids[0], "participant": fam.child}])
        admin = self.admin()
        self.assertEqual(admin.post_json("/api/staff/sessions/%d/cancel" % sids[0], {}).status, 422)
        r = ok(admin.post_json("/api/staff/sessions/%d/cancel" % sids[0], {"reason": "Storm damage"})).json()
        self.assertEqual(r["cancelled"], 1)
        self.assertIn("Storm damage", last_email_to(fam.email))

    def test_last_places_race(self):
        """Many families at once for 3 places: exactly 3 get them."""
        aid, (sid,) = make_activity(sessions=1, capacity=3, waitlist_enabled=0)
        families = [self.family(first_name="Kid%d" % i) for i in range(10)]
        results = []

        def go(f):
            r = f.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": f.child}],
                                                  "accept_terms": True, "idempotency_key": key()})
            results.append(r.status)
        threads = [threading.Thread(target=go, args=(f,)) for f in families]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results.count(200), 3, results)
        self.assertEqual(results.count(409), 7, results)
        with db.read() as c:
            self.assertEqual(c.execute("SELECT SUM(places) FROM bookings WHERE session_id=? AND status='confirmed'",
                                       (sid,)).fetchone()[0], 3)

    def test_idempotent_confirm(self):
        aid, sids = make_activity()
        fam = self.family()
        k = key()
        body = {"items": [{"session_id": sids[0], "participant": fam.child}], "accept_terms": True, "idempotency_key": k}
        a = ok(fam.post_json("/api/book/confirm", body)).json()
        b = ok(fam.post_json("/api/book/confirm", body)).json()
        self.assertEqual(a["checkout"], b["checkout"])
        with db.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM bookings WHERE session_id=?", (sids[0],)).fetchone()[0], 1)


class StripeTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True)
        ratelimit.reset()

    def test_card_checkout_webhook_and_refund(self):
        with FakeStripe() as stripe:
            aid, sids = make_activity(price=3000, first_day=30)
            fam = register_family()
            child = complete_child(fam)
            r = ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sids[0], "participant": child}],
                                                       "pay_mode": "card", "accept_terms": True,
                                                       "idempotency_key": key()})).json()
            self.assertTrue(r["redirect"].startswith("https://stripe.test/pay/"))
            self.assertEqual(r["bookings"][0]["status"], "pending_payment")
            sess = stripe.last_session()
            self.assertEqual(sess["amount_total"], 3000)
            self.assertNotIn("Maya", str(sess["form"]))  # no child names go to Stripe
            # a forged webhook is refused
            self.assertEqual(webhook(fam, "checkout.session.completed", stripe.pay(sess["id"]), secret="wrong").status, 400)
            self.assertEqual(booking(r["bookings"][0]["ref"])["status"], "pending_payment")
            # the real one confirms, once
            ok(webhook(fam, "checkout.session.completed", sess, event_id="evt_same"))
            d = ok(webhook(fam, "checkout.session.completed", sess, event_id="evt_same")).json()
            self.assertTrue(d.get("duplicate"))
            b = booking(r["bookings"][0]["ref"])
            self.assertEqual(b["status"], "confirmed")
            with db.read() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM payments WHERE method='stripe_card' AND"
                                           " stripe_payment_intent_id=?", (sess["payment_intent"],)).fetchone()[0], 1)
            status = ok(fam.get("/api/book/checkout/%s/status" % r["checkout"])).json()
            self.assertEqual(status["status"], "completed")
            self.assertEqual(status["bookings"][0]["invoice"]["balance_pence"], 0)
            # cancelling 30 days ahead refunds the card
            terms = ok(fam.get("/api/account/bookings/%s/cancel-terms" % b["ref"])).json()
            self.assertEqual(terms["money"], "refund_card")
            ok(fam.post_json("/api/account/bookings/%s/cancel" % b["ref"], {}))
            from hah import payments_stripe
            payments_stripe.send_refunds()
            self.assertEqual(stripe.refunds[-1]["amount"], 3000)
            with db.read() as c:
                self.assertEqual(c.execute("SELECT status FROM refunds ORDER BY id DESC LIMIT 1").fetchone()[0], "succeeded")

    def test_abandoned_checkout_releases_the_place(self):
        with FakeStripe() as stripe:
            aid, (sid,) = make_activity(sessions=1, capacity=1, price=1500)
            fam = register_family()
            child = complete_child(fam)
            r = ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": child}],
                                                       "pay_mode": "card", "accept_terms": True,
                                                       "idempotency_key": key()})).json()
            ok(webhook(fam, "checkout.session.expired", dict(stripe.last_session(), status="expired")))
            self.assertEqual(booking(r["bookings"][0]["ref"])["status"], "expired")
            with db.read() as c:
                self.assertEqual(c.execute("SELECT status FROM checkouts WHERE ref=?", (r["checkout"],)).fetchone()[0],
                                 "expired")

    def test_late_card_payment_is_refunded_automatically(self):
        set_settings(pay_later_for_all=True)
        with FakeStripe() as stripe:
            aid, (sid,) = make_activity(sessions=1, capacity=1, price=1500, allow_pay_later=1)
            fam = register_family()
            child = complete_child(fam)
            r = ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": child}],
                                                       "pay_mode": "card", "accept_terms": True,
                                                       "idempotency_key": key()})).json()
            sess = stripe.last_session()
            ok(webhook(fam, "checkout.session.expired", dict(sess, status="expired")))
            other = register_family()
            ok(other.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": complete_child(other)}],
                                                     "pay_mode": "pay_later", "accept_terms": True, "idempotency_key": key()}))
            # the payment turns up after all
            ok(webhook(fam, "checkout.session.completed", dict(stripe.pay(sess["id"]), status="complete")))
            self.assertEqual(booking(r["bookings"][0]["ref"])["status"], "expired")
            from hah import payments_stripe
            payments_stripe.send_refunds()
            self.assertEqual(stripe.refunds[-1]["amount"], 1500)
            self.assertIn("booked by someone else", last_email_to(fam.email))
            with db.read() as c:
                self.assertEqual(c.execute("SELECT status FROM refunds ORDER BY id DESC LIMIT 1").fetchone()[0], "succeeded")
                self.assertTrue(c.execute("SELECT 1 FROM intray_items WHERE type='late_payment'").fetchone())

    def test_stripe_failure_books_nothing(self):
        with FakeStripe() as stripe:
            aid, (sid,) = make_activity(sessions=1, price=1500)
            fam = register_family()
            child = complete_child(fam)
            stripe.fail_next = True
            r = fam.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": child}],
                                                    "pay_mode": "card", "accept_terms": True, "idempotency_key": key()})
            self.assertEqual(r.status, 502)
            with db.read() as c:
                self.assertEqual(c.execute("SELECT status FROM bookings WHERE session_id=?", (sid,)).fetchone()[0],
                                 "expired")

    def test_hold_expiry_checks_stripe_first(self):
        from hah import payments_stripe
        with FakeStripe() as stripe:
            aid, (sid,) = make_activity(sessions=1, price=1500)
            fam = register_family()
            child = complete_child(fam)
            r = ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": child}],
                                                       "pay_mode": "card", "accept_terms": True,
                                                       "idempotency_key": key()})).json()
            stripe.pay(stripe.last_session()["id"])  # paid, but the webhook never came
            with db.tx() as c:
                c.execute("UPDATE checkouts SET expires_at='2000-01-01T00:00:00Z' WHERE ref=?", (r["checkout"],))
            payments_stripe.expire_holds()
            self.assertEqual(booking(r["bookings"][0]["ref"])["status"], "confirmed")


class OutboxNoLeakTest(ServerTestCase):
    def test_booking_emails_have_no_health_details(self):
        set_settings(booking_live=True)
        aid, sids = make_activity()
        fam = register_family()
        child = complete_child(fam)
        ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sids[0], "participant": child}],
                                               "accept_terms": True, "idempotency_key": key()}))
        outbox.send_due()
        with db.read() as c:
            for r in c.execute("SELECT body_text FROM message_deliveries"):
                self.assertNotIn("Peanuts", r[0])


class RemindersAndTermsTest(ServerTestCase):
    def test_invoice_reminders_once(self):
        set_settings(booking_live=True, pay_later_for_all=True)
        aid, sids = make_activity(price=1000, allow_pay_later=1, first_day=3)
        ratelimit.reset()
        fam = register_family()
        child = complete_child(fam)
        ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sids[0], "participant": child}],
                                               "pay_mode": "pay_later", "accept_terms": True, "idempotency_key": key()}))
        from hah import money
        with db.tx() as c:  # due today
            c.execute("UPDATE invoices SET due_date=issue_date")
        self.assertIn("sent 1", money.reminders_job())
        self.assertIn("sent 0", money.reminders_job())
        self.assertIn("due today", last_email_to(fam.email))

    def test_terms_page_uses_settings(self):
        set_settings(cancel_cutoff_hours=72)
        r = self.client().get("/booking-terms")
        self.assertEqual(r.status, 200)
        self.assertIn("72 hours", r.text)
