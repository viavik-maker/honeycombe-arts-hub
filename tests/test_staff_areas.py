"""Staff bookings, walk-ins, finance, people search/records and the in-tray."""
from hah import db, ratelimit
from tests.booking_helpers import make_activity, set_settings
from tests.family_helpers import complete_child, last_email_to, ok, participant_id, register_family
from tests.support import ServerTestCase


def account_ref(client):
    with db.read() as c:
        return c.execute("SELECT ref FROM accounts WHERE email=?", (client.email,)).fetchone()[0]


class StaffBookingTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True)
        ratelimit.reset()

    def test_book_for_family_with_cash_and_incomplete_profile(self):
        fam = register_family()
        ref = complete_child(fam, level="short", first_name="Leo", dob="2019-03-01")  # not full level
        aid, sids = make_activity(price=2500, level="full")
        admin = self.admin(roles=("manager",))
        r = ok(admin.post_json("/api/staff/bookings/create", {
            "account_ref": account_ref(fam), "items": [{"session_id": sids[0], "participant": ref}],
            "payment": {"mode": "record", "method": "cash", "amount_pence": 2500}})).json()
        b = r["bookings"][0]
        self.assertEqual(b["status"], "confirmed")
        self.assertEqual(r["invoice"]["balance_pence"], 0)
        with db.read() as c:
            self.assertEqual(c.execute("SELECT profile_incomplete FROM bookings WHERE ref=?", (b["ref"],)).fetchone()[0], 1)
        self.assertIn("Complete Leo's details", last_email_to(fam.email))
        # takings show the cash
        s = ok(admin.get("/api/staff/finance/summary")).json()
        self.assertIn("Cash", [t["method"] for t in s["takings"]])

    def test_rules_need_override_and_reason(self):
        fam = register_family()
        ref = complete_child(fam)
        aid, (sid,) = make_activity(sessions=1, capacity=0, min_age=12, max_age=20, waitlist_enabled=0)
        body = {"account_ref": account_ref(fam), "items": [{"session_id": sid, "participant": ref}], "payment": {}}
        session_staff = self.admin(roles=("session_staff",))
        self.assertEqual(session_staff.post_json("/api/staff/bookings/create", body).status, 403)
        admin = self.admin()
        r = admin.post_json("/api/staff/bookings/create", body)
        self.assertEqual(r.status, 409)
        self.assertTrue(r.json()["problems"])
        self.assertEqual(admin.post_json("/api/staff/bookings/create", dict(body, override=True)).status, 422)
        ok(admin.post_json("/api/staff/bookings/create", dict(body, override=True, reason="Sibling group, agreed")))

    def test_card_numbers_refused_in_references(self):
        fam = register_family()
        ref = complete_child(fam)
        aid, sids = make_activity(price=1000)
        r = self.admin().post_json("/api/staff/bookings/create", {
            "account_ref": account_ref(fam), "items": [{"session_id": sids[0], "participant": ref}],
            "payment": {"mode": "record", "method": "card_terminal", "reference": "4111 1111 1111 1111"}})
        self.assertEqual(r.status, 422)

    def test_walkin_creates_family_books_pays_and_signs_in(self):
        aid, (sid,) = make_activity(sessions=1, price=500, level="short", min_age=0, max_age=59, first_day=0)
        admin = self.admin(roles=("manager",))
        body = {"session_id": sid, "parent": {"first_name": "Amy", "last_name": "Walker", "email": "amy@example.org",
                                              "mobile": "07700 900999"},
                "child": {"first_name": "Bea", "last_name": "Walker", "dob": "2024-06-01"},
                "contact": {"full_name": "Nan Walker", "relationship": "Grandmother", "phone": "01202 555555"},
                "allergies": "Dairy", "photo": "none", "first_aid": "yes",
                "payment": {"mode": "record", "method": "cash", "amount_pence": 500}}
        r = admin.post_json("/api/staff/walkin", dict(body, parent={"first_name": "Amy"}))
        self.assertEqual(r.status, 422)
        r = ok(admin.post_json("/api/staff/walkin", body)).json()
        self.assertEqual(r["booking"]["attendance"], "present")
        self.assertEqual(r["invoice"]["balance_pence"], 0)
        with db.read() as c:
            a = c.execute("SELECT * FROM accounts WHERE ref=?", (r["account_ref"],)).fetchone()
            self.assertEqual((a["source"], a["status"]), ("walkin", "pending_activation"))
            src = c.execute("SELECT source FROM consents WHERE account_id=? AND superseded_at IS NULL LIMIT 1",
                            (a["id"],)).fetchone()[0]
            self.assertEqual(src, "staff_verbal")
        self.assertIn("activate", last_email_to("amy@example.org").lower())
        # the same email can't create a second family
        self.assertEqual(admin.post_json("/api/staff/walkin", body).status, 422)


class FinanceTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True, pay_later_for_all=True)
        ratelimit.reset()

    def test_record_payment_void_and_exports(self):
        fam = register_family()
        ref = complete_child(fam)
        aid, sids = make_activity(price=3000, allow_pay_later=1)
        r = ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sids[0], "participant": ref}],
                                                   "pay_mode": "pay_later", "accept_terms": True,
                                                   "idempotency_key": "k-finance-1"})).json()
        number = r["bookings"][0]["invoice"]["number"]
        fin = self.admin(roles=("finance",))
        unpaid = ok(fin.get("/api/staff/finance/invoices?view=unpaid")).json()["invoices"]
        self.assertIn(number, [i["number"] for i in unpaid])
        self.assertEqual(fin.post_json("/api/staff/payments", {"invoice_number": number, "method": "childcare_voucher",
                                                               "amount_pence": 5000}).status, 422)  # more than owed
        ok(fin.post_json("/api/staff/payments", {"invoice_number": number, "method": "childcare_voucher",
                                                 "voucher_provider": "Edenred", "amount_pence": 1000}))
        inv = [i for i in ok(fin.get("/api/staff/finance/invoices?view=all&q=" + number)).json()["invoices"]][0]
        self.assertEqual((inv["status"], inv["balance_pence"]), ("part_paid", 2000))
        self.assertEqual(fin.post_json("/api/staff/invoices/%s/void" % number, {"reason": "test"}).status, 400)
        csv = fin.get("/api/staff/finance/payments.csv")
        self.assertEqual(csv.status, 200)
        self.assertIn("Edenred", csv.text)
        self.assertEqual(fin.get("/api/staff/finance/invoices.csv").status, 200)
        self.assertEqual(self.admin(roles=("session_staff",)).get("/api/staff/finance/summary").status, 403)


class PeopleAndIntrayTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()

    def test_search_records_and_audit(self):
        fam = register_family()
        ref = complete_child(fam, first_name="Zara")
        staff = self.admin(roles=("session_staff",))
        res = ok(staff.post_json("/api/staff/search", {"q": "Zara", "scope": "children"})).json()["results"]
        self.assertEqual(res[0]["ref"], ref)
        # session staff can't search by health, or open the health tab
        self.assertEqual(staff.post_json("/api/staff/search", {"scope": "children", "filters": {"allergy": True}}).status, 400)
        self.assertEqual(staff.get("/api/staff/people/participants/%s?tab=health" % ref).status, 403)
        mgr = self.admin(roles=("manager",))
        d = ok(mgr.get("/api/staff/people/participants/%s?tab=health" % ref)).json()
        self.assertEqual(d["health"]["allergies"], "Peanuts")
        self.assertNotIn("safeguarding", d["tabs"])
        self.assertEqual(mgr.get("/api/staff/people/participants/%s?tab=safeguarding" % ref).status, 403)
        with db.read() as c:
            self.assertTrue(c.execute("SELECT 1 FROM audit_log WHERE action='participant.view_health' AND participant_id=?",
                                      (participant_id(ref),)).fetchone())
        fams = ok(mgr.post_json("/api/staff/search", {"q": fam.email, "scope": "families"})).json()["results"]
        self.assertEqual(len(fams), 1)
        rec = ok(mgr.get("/api/staff/people/accounts/%s" % fams[0]["ref"])).json()
        self.assertEqual(rec["participants"][0]["first_name"], "Zara")
        csv = mgr.request("POST", "/api/staff/search/export", b'{"q": "Zara", "scope": "children"}',
                          headers={"Content-Type": "application/json"})
        self.assertIn("Zara", csv.text)

    def test_duplicate_child_and_merge(self):
        a = register_family()
        complete_child(a, first_name="Dup", dob="2017-01-01")
        b = register_family()
        ok(b.post_json("/api/account/participants", {"first_name": "Dup", "last_name": "Parent", "dob": "2017-01-01"}))
        dsl = self.admin(roles=("dsl",))
        items = ok(dsl.get("/api/staff/intray")).json()["items"]
        self.assertIn("duplicate_child", [i["type"] for i in items])
        owner = self.admin()
        self.assertNotIn("duplicate_child", [i["type"] for i in ok(owner.get("/api/staff/intray")).json()["items"]])
        dups = ok(owner.get("/api/staff/people/duplicates")).json()["children"]
        self.assertTrue(any(d["name"] == "dup parent" for d in dups))
        ok(owner.post_json("/api/staff/people/accounts/%s/merge" % account_ref(b), {"into_ref": account_ref(a)}))
        with db.read() as c:
            n = c.execute("SELECT COUNT(*) FROM participants p JOIN accounts x ON x.id=p.account_id WHERE x.email=?",
                          (a.email,)).fetchone()[0]
            self.assertEqual(n, 2)
            self.assertEqual(c.execute("SELECT status FROM accounts WHERE email=?", (b.email,)).fetchone()[0], "closed")

    def test_intray_actions_respect_permissions(self):
        self.client().post_json("/api/contact", {"name": "Visitor", "email": "v@example.org", "message": "Hello"})
        owner = self.admin()
        items = [i for i in ok(owner.get("/api/staff/intray")).json()["items"] if i["type"] == "contact_message"]
        self.assertTrue(items)
        iid = items[0]["id"]
        staff = self.admin(roles=("session_staff",))  # no site.content
        self.assertEqual(staff.post_json("/api/staff/intray/%d" % iid, {"action": "done"}).status, 404)
        ok(owner.post_json("/api/staff/intray/%d" % iid, {"action": "snooze", "days": 2}))
        self.assertNotIn(iid, [i["id"] for i in ok(owner.get("/api/staff/intray")).json()["items"]])
        self.assertIn(iid, [i["id"] for i in ok(owner.get("/api/staff/intray?view=snoozed")).json()["items"]])
        ok(owner.post_json("/api/staff/intray/%d" % iid, {"action": "done"}))
        self.assertIn(iid, [i["id"] for i in ok(owner.get("/api/staff/intray?view=done")).json()["items"]])

