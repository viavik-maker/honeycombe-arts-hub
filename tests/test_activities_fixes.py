"""Activities: which date ages are checked on, bank holidays, duplicating for
next term, changing sessions that have bookings, and archived activities."""
import datetime
import uuid

from hah import booking_settings, catalogue, db, eligibility, ratelimit
from tests.booking_helpers import future, make_activity, set_settings
from tests.family_helpers import complete_child, ok, register_family
from tests.support import ServerTestCase


def category(key):
    with db.read() as c:
        return c.execute("SELECT id FROM activity_categories WHERE key=?", (key,)).fetchone()[0]


class AgeBasisTest(ServerTestCase):
    def test_new_activities_check_each_session_date_unless_a_term_block(self):
        admin = self.admin()
        base = {"title": "Club %s" % uuid.uuid4().hex[:6], "centre_id": 1, "registration_level": "full"}
        a = ok(admin.post_json("/api/staff/activities", dict(base, category_id=category("holiday_club")))).json()
        self.assertEqual(a["activity"]["age_basis"], "session_date")
        a = ok(admin.post_json("/api/staff/activities", dict(base, category_id=category("home_ed")))).json()
        self.assertEqual(a["activity"]["age_basis"], "first_session")
        a = ok(admin.post_json("/api/staff/activities", dict(base, category_id=category("holiday_club"),
                                                             age_basis="first_session"))).json()["activity"]
        self.assertEqual(a["age_basis"], "first_session")  # chosen by staff
        a = ok(admin.post_json("/api/staff/activities/%d/update" % a["id"], {"title": "Renamed"})).json()["activity"]
        self.assertEqual(a["age_basis"], "first_session")  # kept when not sent

    def test_first_session_means_the_first_one_still_to_come(self):
        fam = register_family()
        dob = (catalogue.uk_today() - datetime.timedelta(days=100)).isoformat()  # born after the class began
        ref = complete_child(fam, first_name="Baby", dob=dob, level="short")
        aid, (sid,) = make_activity(sessions=1, level="short", min_age=0, max_age=23, age_basis="first_session")
        with db.tx() as c:
            c.execute("INSERT INTO activity_sessions(activity_id, date, start_time, end_time, capacity, created_at)"
                      " VALUES (?,?,?,?,?,?)", (aid, future(-200), "10:00", "11:00", 10, db.now()))
        with db.read() as c:
            self.assertEqual(catalogue.first_session_date(c, aid), future(10))
            a = c.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
            s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (sid,)).fetchone()
            p = c.execute("SELECT * FROM participants WHERE ref=?", (ref,)).fetchone()
            acct = c.execute("SELECT * FROM accounts WHERE id=?", (p["account_id"],)).fetchone()
            self.assertNotIn("age", [code for code, _ in eligibility.Checker(c, acct).problems(a, s, p)])
            # a session before the first upcoming one (a staff view of the past) uses its own date
            self.assertEqual(catalogue.age_on(a, {"date": future(-200)}, future(10)),
                             datetime.date.fromisoformat(future(-200)))


class BankHolidayTest(ServerTestCase):
    def preview(self, admin, aid, body):
        r = ok(admin.post_json("/api/staff/activities/%d/sessions/generate" % aid, body))
        return [s["date"] for s in r.json()["sessions"]]

    def test_defaults_and_saved_dates_are_normalised(self):
        default = booking_settings.DEFAULTS["bank_holidays"]
        self.assertEqual(len(default), 24)
        self.assertTrue({"2026-12-25", "2026-12-28", "2027-03-26", "2027-12-27", "2028-01-03", "2028-04-17"} <= set(default))
        self.assertTrue(all(datetime.date.fromisoformat(d).weekday() < 5 for d in default))
        admin = self.admin()
        aid, _ = make_activity(sessions=0, status="draft")
        body = {"from": "2026-12-21", "to": "2026-12-31", "weekdays": [0, 4], "start_time": "10:00", "end_time": "11:00",
                "dry_run": True}
        dates = self.preview(admin, aid, body)
        self.assertEqual(dates, ["2026-12-21"])  # Fri 25th and Mon 28th are bank holidays
        set_settings(bank_holidays=["20261221", "25/12/2026", " 2026-12-28 "])
        self.assertEqual(booking_settings.get("bank_holidays"), ["2026-12-21", "2026-12-25", "2026-12-28"])
        dates = self.preview(admin, aid, body)
        self.assertEqual(dates, [])
        r = admin.post_json("/api/staff/settings/booking", {"settings": {"bank_holidays": ["31/02/2026"]}})
        self.assertEqual(r.status, 422)
        # a list saved before dates were tidied still matches
        with db.tx() as c:
            c.execute("UPDATE settings SET value='[\"20261221\"]' WHERE key='bank_holidays'")
        dates = self.preview(admin, aid, body)
        self.assertEqual(dates, ["2026-12-25", "2026-12-28"])


class DuplicateTest(ServerTestCase):
    def make_run(self, admin):
        aid, _ = make_activity(sessions=0, status="draft")
        ok(admin.post_json("/api/staff/activities/%d/sessions/generate" % aid, {  # Mon & Wed, 5–28 Oct 2026
            "from": "2026-10-05", "to": "2026-10-28", "weekdays": [0, 2], "start_time": "10:00", "end_time": "11:00",
            "skip_bank_holidays": False}))
        return aid

    def test_whole_weeks_from_the_monday_and_nothing_before_the_start(self):
        set_settings(bank_holidays=[])
        admin = self.admin()
        aid = self.make_run(admin)
        r = ok(admin.post_json("/api/staff/activities/%d/duplicate" % aid, {"new_start_date": "2027-01-06"})).json()
        self.assertEqual([s["date"] for s in r["activity"]["sessions"]],
                         ["2027-01-06", "2027-01-11", "2027-01-13", "2027-01-18", "2027-01-20", "2027-01-25", "2027-01-27"])

    def test_bank_holidays_are_left_out(self):
        set_settings(bank_holidays=["2026-12-28"])
        admin = self.admin()
        aid = self.make_run(admin)
        r = ok(admin.post_json("/api/staff/activities/%d/duplicate" % aid, {"new_start_date": "2026-12-21"})).json()
        self.assertEqual([s["date"] for s in r["activity"]["sessions"]],
                         ["2026-12-21", "2026-12-23", "2026-12-30", "2027-01-04", "2027-01-06", "2027-01-11", "2027-01-13"])


class SessionChangesTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()
        set_settings(booking_live=True, pay_later_for_all=True)

    def test_date_and_time_of_a_booked_session_cant_change(self):
        admin = self.admin()
        fam = register_family()
        child = complete_child(fam)
        aid, (booked, free) = make_activity(sessions=2)
        ok(fam.post_json("/api/book/confirm", {"items": [{"session_id": booked, "participant": child}],
                                               "pay_mode": "pay_later", "accept_terms": True,
                                               "idempotency_key": uuid.uuid4().hex}))
        for change in ({"date": future(30)}, {"start_time": "11:00"}, {"end_time": "16:00"}):
            r = admin.post_json("/api/staff/sessions/%d/update" % booked, change)
            self.assertEqual(r.status, 400, change)
            self.assertIn("move the bookings", r.json()["error"])
        ok(admin.post_json("/api/staff/sessions/%d/update" % booked, {"theme": "Clay", "capacity": 12}))
        ok(admin.post_json("/api/staff/sessions/%d/update" % free, {"date": future(30)}))

    def test_archived_activities_are_read_only(self):
        admin = self.admin()
        aid, (sid,) = make_activity(sessions=1, status="draft")
        ok(admin.post_json("/api/staff/activities/%d/status" % aid, {"to": "archived"}))
        for path, body in (("/api/staff/activities/%d/sessions" % aid, {"date": future(40), "start_time": "10:00",
                                                                        "end_time": "11:00"}),
                           ("/api/staff/sessions/%d/update" % sid, {"theme": "New"}),
                           ("/api/staff/sessions/%d/delete" % sid, {}),
                           ("/api/staff/activities/%d/sessions/generate" % aid, {
                               "from": future(40), "to": future(50), "weekdays": [0], "start_time": "10:00",
                               "end_time": "11:00"})):
            r = admin.post_json(path, body)
            self.assertEqual(r.status, 400, path)
            self.assertIn("Archived", r.json()["error"])
        with db.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM activity_sessions WHERE activity_id=?", (aid,)).fetchone()[0], 1)
