"""Registers (sign in/out, collection checks, printing, HAF export) and incidents."""
import json
import uuid

from hah import db, ratelimit
from tests.booking_helpers import future, make_activity, set_settings
from tests.family_helpers import complete_child, last_email_to, ok, participant_id, register_family
from tests.support import ServerTestCase


def book_today(fam, child, **kw):
    """Book a session (booked for tomorrow, then moved to today)."""
    aid, (sid,) = make_activity(sessions=1, first_day=1, **kw)
    r = ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": sid, "participant": child}],
                                               "accept_terms": True, "idempotency_key": uuid.uuid4().hex})).json()
    with db.tx() as c:
        c.execute("UPDATE activity_sessions SET date=? WHERE id=?", (future(0), sid))
    with db.read() as c:
        bid = c.execute("SELECT id FROM bookings WHERE ref=?", (r["bookings"][0]["ref"],)).fetchone()[0]
    return sid, bid


class RegisterTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True)
        ratelimit.reset()
        self.fam = register_family()
        self.child = complete_child(self.fam)  # allergies: Peanuts, collection password "blue tiger"
        with db.tx() as c:
            c.execute("UPDATE participant_safeguarding SET family_info='Private family matter' WHERE participant_id=?",
                      (participant_id(self.child),))
        self.sid, self.bid = book_today(self.fam, self.child)

    def test_session_staff_today_only_and_contents(self):
        staff = self.admin(roles=("session_staff",))
        d = ok(staff.get("/api/staff/registers/session/%d" % self.sid)).json()
        row = d["rows"][0]
        self.assertEqual(row["person"]["first_name"], "Maya")
        self.assertEqual(row["person"]["health"]["allergies"], "Peanuts")
        self.assertTrue(row["person"]["flags"]["anaphylaxis"])
        self.assertIsNone(row["person"]["haf"])  # HAF status hidden from session staff
        text = str(d)
        self.assertNotIn("Private family matter", text)
        self.assertNotIn("$p", text)  # no password hash
        # a session next week: session staff can't open it, a manager can
        aid, (later,) = make_activity(sessions=1, first_day=7)
        self.assertEqual(staff.get("/api/staff/registers/session/%d" % later).status, 403)
        self.assertEqual(self.admin(roles=("manager",)).get("/api/staff/registers/session/%d" % later).status, 200)
        # the day list
        day = ok(staff.get("/api/staff/registers?date=" + future(0))).json()
        self.assertIn(self.sid, [s["id"] for s in day["sessions"]])
        # printed copy: health yes, safeguarding and passwords no
        page = staff.get("/admin/registers/%d/print" % self.sid)
        self.assertEqual(page.status, 200)
        self.assertIn("ALLERGY: Peanuts", page.text)
        self.assertNotIn("Private family matter", page.text)
        self.assertNotIn("blue tiger", page.text)

    def test_sign_in_and_out_with_password(self):
        staff = self.admin(roles=("session_staff",))
        url = "/api/staff/attendance/%d" % self.bid
        self.assertEqual(staff.post_json(url, {"action": "out", "method": "password"}).status, 400)  # not in yet
        ok(staff.post_json(url, {"action": "in"}))
        r = staff.post_json(url, {"action": "out", "method": "password", "password": "red lion", "collected_by_name": "Jo"})
        self.assertEqual(r.status, 400)
        self.assertTrue(r.json().get("wrong_password"))
        r = ok(staff.post_json(url, {"action": "out", "method": "password", "password": "Blue Tiger ",
                                     "collected_by_name": "Jo Jones"})).json()
        self.assertEqual(r["release_method"], "password")
        with db.read() as c:
            self.assertEqual(c.execute("SELECT collected_by_name FROM attendance WHERE booking_id=?",
                                       (self.bid,)).fetchone()[0], "Jo Jones")

    def test_too_many_wrong_passwords_means_phone_check(self):
        staff = self.admin(roles=("session_staff",))
        url = "/api/staff/attendance/%d" % self.bid
        ok(staff.post_json(url, {"action": "in"}))
        for _ in range(5):
            staff.post_json(url, {"action": "out", "method": "password", "password": "nope", "collected_by_name": "X"})
        r = staff.post_json(url, {"action": "out", "method": "password", "password": "blue tiger", "collected_by_name": "X"})
        self.assertIn("phone", r.json()["error"])
        ok(staff.post_json(url, {"action": "out", "method": "known_adult_verified", "collected_by_name": "Jo Jones"}))

    def test_go_home_alone_needs_permission(self):
        staff = self.admin(roles=("session_staff",))
        url = "/api/staff/attendance/%d" % self.bid
        ok(staff.post_json(url, {"action": "in"}))
        r = staff.post_json(url, {"action": "out", "method": "went_home_alone"})
        self.assertIn("permission", r.json()["error"])

    def test_incident_at_collection_blocks_sign_out(self):
        staff = self.admin(roles=("session_staff",))
        r = ok(staff.post_json("/api/staff/incidents", {
            "kind": "injury", "occurred_at_local": future(0) + "T11:15", "session_id": self.sid,
            "description": "Grazed knee on the playground", "action_taken": "Cleaned, plaster applied",
            "first_aid_given": True, "notify_mode": "at_collection", "people": [{"booking_id": self.bid, "role": "injured"}]}))
        url = "/api/staff/attendance/%d" % self.bid
        ok(staff.post_json(url, {"action": "in"}))
        r = staff.post_json(url, {"action": "out", "method": "known_adult_verified", "collected_by_name": "Jo"})
        self.assertEqual(r.status, 409)
        self.assertTrue(r.json()["to_discuss"])
        ok(staff.post_json(url, {"action": "out", "method": "known_adult_verified", "collected_by_name": "Jo",
                                 "incident_discussed": True}))
        # the parent can read it now and acknowledge
        mine = ok(self.fam.get("/api/account/incidents")).json()["incidents"]
        self.assertEqual(mine[0]["description"], "Grazed knee on the playground")
        ok(self.fam.post_json("/api/account/incidents/%s/acknowledge" % mine[0]["ref"], {}))
        self.assertTrue(ok(self.fam.get("/api/account/incidents")).json()["incidents"][0]["acknowledged"])
        # another family can't
        other = register_family()
        self.assertEqual(other.post_json("/api/account/incidents/%s/acknowledge" % mine[0]["ref"], {}).status, 404)

    def test_notify_now_email_has_no_details(self):
        staff = self.admin(roles=("session_staff",))
        ok(staff.post_json("/api/staff/incidents", {
            "kind": "illness", "occurred_at_local": future(0) + "T12:00", "session_id": self.sid,
            "description": "Felt sick after lunch", "notify_mode": "now", "people": [{"booking_id": self.bid}]}))
        text = last_email_to(self.fam.email)
        self.assertIn("note about Maya", text)
        self.assertNotIn("sick", text)

    def test_not_notified_needs_reason(self):
        staff = self.admin(roles=("session_staff",))
        r = staff.post_json("/api/staff/incidents", {"kind": "behaviour", "occurred_at_local": future(0) + "T12:00",
                                                     "description": "x", "notify_mode": "not_notified",
                                                     "people": [{"booking_id": self.bid}]})
        self.assertEqual(r.status, 422)
        self.assertIn("not_notified_reason", r.json()["errors"])

    def test_safeguarding_concern_is_dsl_only(self):
        staff = self.admin(roles=("session_staff",))
        ok(staff.post_json("/api/staff/incidents", {"kind": "safeguarding", "occurred_at_local": future(0) + "T12:00",
                                                    "description": "Disclosure", "notify_mode": "now",
                                                    "people": [{"booking_id": self.bid}]}))
        owner = self.admin()
        self.assertFalse([i for i in ok(owner.get("/api/staff/incidents?kind=safeguarding")).json()["incidents"]])
        dsl = self.admin(roles=("dsl",))
        items = ok(dsl.get("/api/staff/incidents?kind=safeguarding")).json()["incidents"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["notify_mode"], "not_notified")
        self.assertEqual(owner.get("/api/staff/incidents/%d" % items[0]["id"]).status, 404)
        self.assertNotIn("Disclosure", ok(self.fam.get("/api/account/incidents")).text)
        with db.read() as c:
            self.assertEqual(c.execute("SELECT required_perm FROM intray_items WHERE type='safeguarding_concern'")
                             .fetchone()[0], "safeguarding.view")

    def test_haf_export(self):
        with db.tx() as c:
            c.execute("UPDATE participants SET haf_status='verified' WHERE id=?", (participant_id(self.child),))
        sid, bid = book_today(self.fam, self.child, haf_only=1, category_id=1)
        admin = self.admin()
        ok(admin.post_json("/api/staff/attendance/%d" % bid, {"action": "in"}))
        r = admin.get("/api/staff/reports/haf.csv?from=%s&to=%s" % (future(-1), future(1)))
        self.assertEqual(r.status, 200)
        self.assertIn("Maya", r.text)
        self.assertIn(future(0), r.text)
        sid2, bid2 = book_today(self.fam, self.child, haf_only=1, category_id=1)  # an afternoon session, same day
        ok(admin.post_json("/api/staff/attendance/%d" % bid2, {"action": "in"}))
        import csv as _csv, io as _io
        row = [x for x in _csv.DictReader(_io.StringIO(admin.get("/api/staff/reports/haf.csv?from=%s&to=%s" % (
            future(-1), future(1))).text.lstrip("\ufeff"))) if x["First name"] == "Maya" and x["Last name"] == "Parent"][-1]
        self.assertEqual((row["Days booked"], row["Days attended"], row["Dates attended"]), ("1", "1", future(0)))
        self.assertEqual(self.admin(roles=("session_staff",)).get("/api/staff/reports/haf.csv").status, 403)


    def test_cancelled_after_arriving_stays_until_signed_out(self):
        staff = self.admin(roles=("session_staff",))
        url = "/api/staff/attendance/%d" % self.bid
        ok(staff.post_json(url, {"action": "in"}))
        with db.tx() as c:  # e.g. the session is cancelled mid-morning to trigger refunds
            c.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (self.bid,))
            c.execute("UPDATE activity_sessions SET status='cancelled' WHERE id=?", (self.sid,))
        rows = ok(staff.get("/api/staff/registers/session/%d" % self.sid)).json()["rows"]
        self.assertEqual([(r["booking_id"], r["cancelled"]) for r in rows], [(self.bid, True)])
        day = ok(staff.get("/api/staff/registers/day/" + future(0))).json()
        self.assertIn(self.sid, [x["session"]["id"] for x in day["sessions"]])
        ok(staff.post_json(url, {"action": "out", "method": "known_adult_verified", "collected_by_name": "Jo Jones"}))
        self.assertEqual(ok(staff.get("/api/staff/registers/session/%d" % self.sid)).json()["rows"], [])
        self.assertEqual(staff.post_json(url, {"action": "in"}).status, 404)  # nothing left to mark

    def test_collection_alert_and_dsl_check_before_release(self):
        with db.tx() as c:
            c.execute("UPDATE participants SET collection_alert='Father must not collect', f_safeguarding=1 WHERE id=?",
                      (participant_id(self.child),))
        staff = self.admin(roles=("session_staff",))
        row = ok(staff.get("/api/staff/registers/session/%d" % self.sid)).json()["rows"][0]
        self.assertTrue(row["person"]["check_with_dsl"])
        page = staff.get("/admin/registers/%d/print" % self.sid).text
        self.assertIn("COLLECTION ALERT: Father must not collect", page)
        self.assertIn("Check with the DSL", page)
        pack = ok(staff.get("/api/staff/registers/offline-pack")).json()
        prow = [r for s in pack["sessions"] for r in s["rows"] if r["booking_id"] == self.bid][0]
        self.assertEqual((prow["person"]["collection_alert"], prow["person"]["check_with_dsl"]),
                         ("Father must not collect", True))
        url = "/api/staff/attendance/%d" % self.bid
        ok(staff.post_json(url, {"action": "in"}))
        out = {"action": "out", "method": "known_adult_verified", "collected_by_name": "Jo Jones"}
        r = staff.post_json(url, out)
        self.assertEqual((r.status, r.json()["needs_check"]), (400, "alert"))
        r = staff.post_json(url, dict(out, alert_checked=True))
        self.assertEqual(r.json()["needs_check"], "dsl")
        ok(staff.post_json(url, dict(out, alert_checked=True, dsl_checked=True)))
        # the flag itself stays DSL-only information: never the reason
        self.assertNotIn("f_safeguarding", json.dumps(row))

    def test_other_release_needs_a_name_and_a_note(self):
        staff = self.admin(roles=("session_staff",))
        url = "/api/staff/attendance/%d" % self.bid
        ok(staff.post_json(url, {"action": "in"}))
        self.assertEqual(staff.post_json(url, {"action": "out", "method": "other"}).status, 400)
        self.assertEqual(staff.post_json(url, {"action": "out", "method": "other", "collected_by_name": "Aunt Sue"}).status, 400)
        ok(staff.post_json(url, {"action": "out", "method": "other", "collected_by_name": "Aunt Sue",
                                 "notes": "Parent rang to say Sue is collecting; ID checked"}))
        with db.read() as c:
            a = c.execute("SELECT collected_by_name, notes FROM attendance WHERE booking_id=?", (self.bid,)).fetchone()
        self.assertEqual(a["collected_by_name"], "Aunt Sue")
        self.assertIn("ID checked", a["notes"])

    def test_earlier_incident_waits_for_the_next_collection_and_only_shown_ones_are_marked(self):
        manager = self.admin(roles=("manager",))
        ok(manager.post_json("/api/staff/incidents", {
            "kind": "injury", "occurred_at_local": future(-3) + "T16:00", "description": "Bumped head after pick-up",
            "action_taken": "Cold compress", "notify_mode": "at_collection",
            "people": [{"participant_ref": self.child, "role": "injured"}]}))
        with db.tx() as c:  # a DSL-only incident for the same child is never "discussed" at the door
            iid = c.execute("SELECT id FROM incidents ORDER BY id DESC LIMIT 1").fetchone()[0]
            c.execute("INSERT INTO incidents(ref, kind, occurred_at, description, notify_mode, restricted, created_at,"
                      " updated_at) SELECT 'I-RESTRICT' || id, kind, occurred_at, 'restricted', 'at_collection', 1,"
                      " created_at, updated_at FROM incidents WHERE id=?", (iid,))
            rid = c.execute("SELECT id FROM incidents WHERE ref LIKE 'I-RESTRICT%'").fetchone()[0]
            c.execute("INSERT INTO incident_people(incident_id, participant_id, role) VALUES (?,?, 'injured')",
                      (rid, participant_id(self.child)))
        staff = self.admin(roles=("session_staff",))
        url = "/api/staff/attendance/%d" % self.bid
        ok(staff.post_json(url, {"action": "in"}))
        r = staff.post_json(url, {"action": "out", "method": "known_adult_verified", "collected_by_name": "Jo"})
        self.assertEqual(r.status, 409)
        self.assertEqual(len(r.json()["to_discuss"]), 1)
        ok(staff.post_json(url, {"action": "out", "method": "known_adult_verified", "collected_by_name": "Jo",
                                 "incident_discussed": True}))
        with db.read() as c:
            self.assertIsNotNone(c.execute("SELECT discussed_at FROM incidents WHERE id=?", (iid,)).fetchone()[0])
            self.assertIsNone(c.execute("SELECT discussed_at FROM incidents WHERE id=?", (rid,)).fetchone()[0])

    def test_record_access_is_limited(self):
        with db.tx() as c:
            c.execute("UPDATE participants SET collection_alert='Court order', support_plan='Quiet space' WHERE id=?",
                      (participant_id(self.child),))
        fin = self.admin(roles=("finance",))
        p = ok(fin.get("/api/staff/people/participants/%s" % self.child)).json()["participant"]
        self.assertEqual((p["collection_alert"], p["support_plan"]), (None, None))
        mgr = ok(self.admin(roles=("manager",)).get("/api/staff/people/participants/%s" % self.child)).json()
        self.assertEqual(mgr["participant"]["collection_alert"], "Court order")
        ss = self.admin(roles=("session_staff",))
        found = ok(ss.post_json("/api/staff/search", {"scope": "children", "q": "Maya"})).json()["results"]
        self.assertTrue(found)
        self.assertTrue(all(not r["flags"] for r in found))  # flags only on that day's register


class OfflineRegisterTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True)
        ratelimit.reset()
        self.fam = register_family()
        self.child = complete_child(self.fam)
        with db.tx() as c:
            c.execute("UPDATE participant_safeguarding SET family_info='Private family matter' WHERE participant_id=?",
                      (participant_id(self.child),))
        self.sid, self.bid = book_today(self.fam, self.child)

    def test_pack_and_catching_up(self):
        import datetime as _dt
        staff = self.admin(roles=("session_staff",))
        pack = ok(staff.get("/api/staff/registers/offline-pack")).json()
        s = [x for x in pack["sessions"] if x["id"] == self.sid][0]
        row = [r for r in s["rows"] if r["booking_id"] == self.bid][0]
        self.assertIn("ALLERGY: Peanuts", row["person"]["needs"])
        text = json.dumps(pack)
        self.assertNotIn("Private family matter", text)  # never safeguarding
        self.assertNotIn("collection_pw", text)
        self.assertNotIn("blue tiger", text)
        with db.read() as c:
            self.assertTrue(c.execute("SELECT 1 FROM audit_log WHERE action='register.offline_pack'").fetchone())
        # changes made offline arrive later with their real times
        url = "/api/staff/attendance/%d" % self.bid
        t_in = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=40)).strftime("%Y-%m-%dT%H:%M:%S.123Z")
        ok(staff.post_json(url, {"action": "in", "offline": True, "at": t_in}))
        self.assertTrue(ok(staff.post_json(url, {"action": "in", "offline": True, "at": t_in})).json()["already"])
        self.assertEqual(staff.post_json(url, {"action": "out", "offline": True, "at": t_in, "method": "password",
                                               "password": "blue tiger", "collected_by_name": "Jo"}).status, 400)
        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(staff.post_json(url, {"action": "out", "offline": True, "at": old,
                                               "method": "known_adult_verified", "collected_by_name": "Jo"}).status, 400)
        t_out = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ok(staff.post_json(url, {"action": "out", "offline": True, "at": t_out, "method": "known_adult_verified",
                                 "collected_by_name": "Jo Jones"}))
        # an evening session last night, synced this morning
        from hah import catalogue
        uk_midnight = catalogue.uk_now().replace(hour=0, minute=0, second=0, microsecond=0)
        late = (uk_midnight - _dt.timedelta(minutes=30)).astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _, bid2 = book_today(self.fam, complete_child(self.fam, first_name="Ada"))
        with db.tx() as c:
            c.execute("UPDATE activity_sessions SET date=? WHERE id=(SELECT session_id FROM bookings WHERE id=?)",
                      (future(-1), bid2))
        ok(staff.post_json("/api/staff/attendance/%d" % bid2, {"action": "in", "offline": True, "at": late}))
        self.assertEqual(staff.post_json("/api/staff/attendance/%d" % bid2, {"action": "note", "notes": "x"}).status, 403)
        with db.read() as c:
            a = c.execute("SELECT signed_in_at, signed_out_at FROM attendance WHERE booking_id=?", (self.bid,)).fetchone()
            self.assertEqual((a["signed_in_at"], a["signed_out_at"]), (t_in.replace(".123Z", "Z"), t_out))
            self.assertTrue(c.execute("SELECT 1 FROM audit_log WHERE action='attendance.out' AND details LIKE '%offline%'")
                            .fetchone())
