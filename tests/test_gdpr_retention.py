"""Erasure, exports and long-range retention: what is kept, for how long,
and what a family's own download leaves out."""
import datetime
import json
import uuid

from hah import bookings, catalogue, db, gdpr, incidents, marketing, messaging, ratelimit, retention
from tests.booking_helpers import make_activity, set_settings
from tests.family_helpers import complete_child, ok, participant_id, register_family
from tests.support import ServerTestCase

OLD = "2000-01-01T00:00:00Z"


def uid():
    return uuid.uuid4().hex[:10]


def add_account(c, status="active", kind="family", source="self", created_at=None, **kw):
    now = db.now()
    cols = dict(ref="A-" + uid(), kind=kind, email="%s@example.org" % uid(), status=status, first_name="Jo",
                last_name="Bloggs", source=source, created_at=created_at or now, updated_at=created_at or now)
    cols.update(kw)
    return c.execute("INSERT INTO accounts(%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                     list(cols.values())).lastrowid


def add_person(c, aid, first_name="Amy", dob="2016-01-01", **kw):
    now = db.now()
    cols = dict(ref="P-" + uid(), account_id=aid, first_name=first_name, last_name="Bloggs", dob=dob, created_at=now,
                updated_at=now)
    cols.update(kw)
    return c.execute("INSERT INTO participants(%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                     list(cols.values())).lastrowid


def add_incident(c, pid, occurred_at, retain_until, description="Fell off the stage, cut to head"):
    now = db.now()
    iid = c.execute("INSERT INTO incidents(ref, occurred_at, kind, description, notify_mode, created_at, updated_at,"
                    " retain_until) VALUES (?,?, 'injury', ?, 'now', ?,?,?)",
                    ("I-" + uid(), occurred_at, description, now, now, retain_until)).lastrowid
    c.execute("INSERT INTO incident_people(incident_id, participant_id, role) VALUES (?,?, 'injured')", (iid, pid))
    return iid


def years_on(day, n):
    return day.replace(year=day.year + n) if not (day.month == 2 and day.day == 29) else day.replace(
        year=day.year + n, day=28)


class ImportedNeverActivatedTest(ServerTestCase):
    def test_closed_after_a_year_only_if_nothing_booked(self):
        act, (upcoming, past) = make_activity(sessions=2, first_day=7)
        long_ago = (catalogue.uk_today() - datetime.timedelta(days=800)).isoformat()
        imported = "2025-08-01T09:00:00Z"
        with db.tx() as c:
            c.execute("UPDATE activity_sessions SET date=? WHERE id=?", (long_ago, past))
            a = c.execute("SELECT * FROM activities WHERE id=?", (act,)).fetchone()
            ids = {}
            for name, sid in (("coming", upcoming), ("lapsed", past), ("nothing", None)):
                aid = add_account(c, status="pending_activation", source="import", created_at=OLD)
                pid = add_person(c, aid, needs_review=1)
                c.execute("INSERT INTO participant_health(participant_id, allergies, anaphylaxis, updated_at)"
                          " VALUES (?, 'peanuts', 1, ?)", (pid, imported))
                if sid:
                    s = c.execute("SELECT * FROM activity_sessions WHERE id=?", (sid,)).fetchone()
                    p = c.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
                    b = bookings._insert_booking(c, {"kind": "participant", "session": s, "activity": a,
                                                       "participant": p}, "confirmed", account_id=aid,
                                                   funding="prepaid_legacy", via="staff", notes="Paid via MagicBooking")
                    c.execute("UPDATE bookings SET created_at=? WHERE id=?", (imported if sid == upcoming else OLD, b["id"]))
                ids[name] = (aid, pid)
        retention.long_retention()
        gdpr.retention_job()
        with db.read() as c:
            status = {k: c.execute("SELECT status FROM accounts WHERE id=?", (aid,)).fetchone()[0]
                      for k, (aid, _) in ids.items()}
            self.assertEqual(status, {"coming": "pending_activation", "lapsed": "anonymised", "nothing": "anonymised"})
            coming = ids["coming"][1]
            self.assertEqual(c.execute("SELECT first_name FROM participants WHERE id=?", (coming,)).fetchone()[0], "Amy")
            self.assertTrue(c.execute("SELECT 1 FROM participant_health WHERE participant_id=?", (coming,)).fetchone())
            self.assertEqual(c.execute("SELECT status FROM bookings WHERE participant_id=?", (coming,)).fetchone()[0],
                             "confirmed")


class IncidentRetentionTest(ServerTestCase):
    def test_an_adults_injury_is_kept_at_least_seven_years(self):
        staff = self.admin(roles=("manager",))
        yesterday = catalogue.uk_today() - datetime.timedelta(days=1)
        with db.tx() as c:
            aid = add_account(c, kind="adult")
            adult = c.execute("SELECT ref FROM participants WHERE id=?",
                              (add_person(c, aid, "Sam", "1995-05-01", is_account_holder=1),)).fetchone()[0]
            child = c.execute("SELECT ref FROM participants WHERE id=?", (add_person(c, aid, "Kit", "2018-03-03"),)).fetchone()[0]
        for ref in (adult, child):
            ok(staff.post_json("/api/staff/incidents", {
                "kind": "injury", "occurred_at_local": yesterday.isoformat() + "T14:00", "notify_mode": "at_collection",
                "description": "Fell off stage %s" % ref, "people": [{"participant_ref": ref, "role": "injured"}]}))
        retention.long_retention()
        with db.read() as c:
            got = {r["description"]: r["retain_until"] for r in c.execute(
                "SELECT description, retain_until FROM incidents WHERE description LIKE 'Fell off stage %'")}
        self.assertEqual(got["Fell off stage " + adult], years_on(yesterday, incidents.MIN_YEARS).isoformat())
        self.assertEqual(got["Fell off stage " + child], "2043-03-03")  # the child's 25th birthday is later

    def test_existing_records_get_the_floor_before_anything_is_cleared(self):
        recent = (catalogue.uk_today() - datetime.timedelta(days=2)).isoformat()
        with db.tx() as c:
            aid = add_account(c, kind="adult")
            pid = add_person(c, aid, "Sam", "1990-01-01", is_account_holder=1)
            new = add_incident(c, pid, recent + "T10:00", "2015-01-01")  # made before the floor existed
            old = add_incident(c, pid, "2010-06-01T10:00", "2015-01-01")
        retention.long_retention()
        with db.read() as c:
            n = c.execute("SELECT description, retain_until FROM incidents WHERE id=?", (new,)).fetchone()
            o = c.execute("SELECT description FROM incidents WHERE id=?", (old,)).fetchone()
        self.assertEqual(n["retain_until"], years_on(datetime.date.fromisoformat(recent), 7).isoformat())
        self.assertNotEqual(n["description"], retention.REMOVED)
        self.assertEqual(o["description"], retention.REMOVED)  # over 7 years and past 25: cleared as before


class ExportTest(ServerTestCase):
    def test_family_export_leaves_out_staff_flags_and_children_now_adults(self):
        adult_dob = (catalogue.uk_today() - datetime.timedelta(days=365 * 19 + 5)).isoformat()
        with db.tx() as c:
            aid = add_account(c)
            amy = add_person(c, aid, "Amy", f_safeguarding=1, needs_review=1, f_allergy=1, haf_status="verified")
            c.execute("INSERT INTO participant_health(participant_id, allergies, updated_at, updated_by_staff)"
                      " VALUES (?, 'Peanuts', ?, 1)", (amy, db.now()))
            add_person(c, aid, "Tia", adult_dob, status="archived", adult_notified_at=OLD)  # left at 18
            add_person(c, aid, "Bo", "2019-01-01", status="archived")  # removed by the parent: still their child
            add_person(c, aid, "Deleted", "2015-01-01", status="anonymised")
            a = c.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
            for self_service in (True, False):
                data = gdpr.export_account(c, a, self_service=self_service)
                self.assertEqual([p["details"]["first_name"] for p in data["people"]], ["Amy", "Bo"])
                amy_out = data["people"][0]
                self.assertEqual((amy_out["details"]["f_allergy"], amy_out["details"]["haf_status"]), (1, "verified"))
                self.assertEqual(amy_out["health"][0]["allergies"], "Peanuts")
                dump = json.dumps(data, default=str)
                for staff_only in ("f_safeguarding", "needs_review", "haf_verified_by", "updated_by_staff",
                                   "collection_pw_hash", "staff_notes", "dsl_reviewed"):
                    self.assertNotIn(staff_only, dump)
                html = gdpr.render_export_html(data)
                self.assertNotIn("F safeguarding", html)
                self.assertNotIn("Tia", html)


class ErasureTest(ServerTestCase):
    def setUp(self):
        ratelimit.reset()
        set_settings(booking_live=True)

    def test_erasure_keeps_consents_a_suppression_row_and_only_a_minimal_child_record(self):
        fam = register_family()
        held, gone = complete_child(fam, first_name="Maya"), complete_child(fam, first_name="Leo", dob="2017-02-02")
        ok(fam.post_json("/api/account/preferences", {"email_news": True, "sms_news": True}))
        with db.tx() as c:
            aid = c.execute("SELECT id FROM accounts WHERE email=?", (fam.email,)).fetchone()[0]
            add_incident(c, participant_id(held), "2026-01-01T10:00", "2043-05-10")
            consents = c.execute("SELECT COUNT(*) FROM consents WHERE account_id=?", (aid,)).fetchone()[0]
            c.execute("UPDATE accounts SET status='closed', erase_after=? WHERE id=?", (OLD, aid))
        self.assertGreater(consents, 0)
        gdpr.retention_job()
        with db.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM consents WHERE account_id=?", (aid,)).fetchone()[0], consents)
            m = dict(c.execute("SELECT * FROM marketing_preferences WHERE email=?", (fam.email,)).fetchone())
            self.assertEqual((m["email_opt_in"], m["sms_opt_in"], m["phone"], m["name"], m["account_id"]),
                             (0, 0, None, None, None))
            self.assertIsNotNone(m["unsubscribed_email_at"])
            self.assertIsNotNone(m["unsubscribed_sms_at"])
            for ref, status, name in ((held, "retention_hold", "Maya"), (gone, "anonymised", "Deleted")):
                p = c.execute("SELECT * FROM participants WHERE ref=?", (ref,)).fetchone()
                self.assertEqual((p["status"], p["first_name"]), (status, name))
                self.assertEqual([p[k] for k in ("f_allergy", "f_anaphylaxis", "f_medical", "f_send", "f_semh",
                                                 "f_religious")], [0] * 6)
                self.assertEqual([p[k] for k in ("haf_status", "photo_consent", "education", "go_home_alone",
                                                 "school_name", "collection_pw_set_at")],
                                 ["unknown", None, None, None, None, None])
        # an old newsletter list can't put them back on the news list
        with db.tx() as c:
            marketing.opt_in(c, fam.email, source="legacy_newsletter")
            self.assertNotIn(fam.email, [k for k, _ in messaging.recipients(c, "marketing", "email", {})[0]])
        # consent history goes 6 years after the erasure
        retention.long_retention()
        with db.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM consents WHERE account_id=?", (aid,)).fetchone()[0], consents)
        with db.tx() as c:
            c.execute("UPDATE accounts SET anonymised_at=? WHERE id=?", ("2019-01-01T00:00:00Z", aid))
        retention.long_retention()
        with db.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM consents WHERE account_id=?", (aid,)).fetchone()[0], 0)

    def test_unverified_adult_signups_are_purged(self):
        with db.tx() as c:
            adult = add_account(c, status="pending_verification", kind="adult", created_at=OLD)
            add_person(c, adult, "Ada", "2000-01-01", is_account_holder=1, target_level="adult")
            family = add_account(c, status="pending_verification", created_at=OLD)
            add_person(c, family, "Kid")  # someone added a child: kept, as before
        gdpr.retention_job()
        with db.read() as c:
            self.assertFalse(c.execute("SELECT 1 FROM accounts WHERE id=?", (adult,)).fetchone())
            self.assertFalse(c.execute("SELECT 1 FROM participants WHERE account_id=?", (adult,)).fetchone())
            self.assertTrue(c.execute("SELECT 1 FROM accounts WHERE id=?", (family,)).fetchone())


class InvoiceRetentionTest(ServerTestCase):
    def test_six_years_from_the_end_of_the_financial_year(self):
        set_settings(reporting_year_start_month=4)
        try:
            with db.tx() as c:
                aid = add_account(c, status="anonymised", anonymised_at=OLD)
                ids = {}
                for day in ("2020-03-31", "2020-04-01"):  # the last day of 2019/20 and the first of 2020/21
                    ids[day] = c.execute(
                        "INSERT INTO invoices(number, account_id, bill_to_name, bill_to_email, bill_to_address,"
                        " issue_date, due_date, status, total_pence, created_at) VALUES (?,?, 'Jo Bloggs',"
                        " 'jo@example.org', '1 High St', ?,?, 'paid', 1000, ?)",
                        ("INV-" + uid(), aid, day, day, db.now())).lastrowid
            retention.long_retention(today=datetime.date(2026, 9, 27))
            with db.read() as c:
                got = {d: tuple(c.execute("SELECT bill_to_name, bill_to_address FROM invoices WHERE id=?", (i,)).fetchone())
                       for d, i in ids.items()}
        finally:
            set_settings(reporting_year_start_month=1)
        self.assertEqual(got, {"2020-03-31": ("Erased", None), "2020-04-01": ("Jo Bloggs", "1 High St")})
