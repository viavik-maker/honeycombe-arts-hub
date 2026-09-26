"""The attendance dashboard."""
import datetime
import unittest

from hah import db, ratelimit, reports
from tests.booking_helpers import future, set_settings
from tests.family_helpers import complete_child, ok, register_family
from tests.support import ServerTestCase
from tests.test_registers import book_today


class PeriodTest(unittest.TestCase):
    def test_ranges(self):
        d = datetime.date(2026, 9, 26)
        self.assertEqual(reports.period_range("week", d)[:2], (datetime.date(2026, 9, 21), datetime.date(2026, 9, 27)))
        self.assertEqual(reports.period_range("month", d)[:2], (datetime.date(2026, 9, 1), datetime.date(2026, 9, 30)))
        self.assertEqual(reports.period_range("year", d, 4)[:3], (datetime.date(2026, 4, 1), datetime.date(2027, 3, 31), "2026–27"))
        self.assertEqual(reports.period_range("quarter", d, 4)[:2], (datetime.date(2026, 7, 1), datetime.date(2026, 9, 30)))
        self.assertEqual(reports.period_range("year", datetime.date(2027, 2, 1), 9)[:2],
                         (datetime.date(2026, 9, 1), datetime.date(2027, 8, 31)))


class AttendanceReportTest(ServerTestCase):
    def test_counts_from_registers(self):
        set_settings(booking_live=True)
        ratelimit.reset()
        fam = register_family()
        child = complete_child(fam)
        sid, bid = book_today(fam, child)
        admin = self.admin()
        ok(admin.post_json("/api/staff/attendance/%d" % bid, {"action": "in"}))
        d = ok(admin.get("/api/staff/reports/attendance?period=day&date=" + future(0))).json()
        self.assertEqual(d["totals"]["present"], 1)
        self.assertEqual(d["totals"]["children"], 1)
        self.assertEqual(d["totals"]["new_children"], 1)
        self.assertEqual(d["categories"][0]["category"], "Holiday club")
        y = ok(admin.get("/api/staff/reports/attendance?period=year")).json()
        self.assertEqual(len(y["series"]), 12)
        csv = admin.get("/api/staff/reports/attendance.csv?period=week&date=" + future(0))
        self.assertIn("Holiday club", csv.text)
        self.assertEqual(self.admin(roles=("session_staff",)).get("/api/staff/reports/attendance").status, 403)
        with db.read() as c:
            self.assertTrue(c.execute("SELECT 1 FROM audit_log WHERE action='report.attendance_export'").fetchone())


class HistoricAttendanceTest(ServerTestCase):
    def test_import_and_year_on_year(self):
        set_settings(booking_live=True, reporting_year_start_month=9)
        owner = self.admin()
        last_year = catalogue_today().year - 1
        bad = owner.post_json("/api/staff/reports/historic/import", {"csv": "month,category,attendances\n"
                                                                            "13/2025,Holiday club,5\n2025-08,Knitting,4\n2025-08,HAF,lots"})
        self.assertEqual(bad.status, 422)
        self.assertEqual(len(bad.json()["problems"]), 3)
        csv = "month,category,attendances,children\n%d-10,holiday club,120,40\n10/%d,HAF,30,\n%d-11,Holiday club,80" % (
            last_year, last_year, last_year)
        p = ok(owner.post_json("/api/staff/reports/historic/import", {"csv": csv})).json()
        self.assertEqual((p["count"], p["total"], p["replaces"]), (3, 230, 0))
        with db.read() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM historic_attendance").fetchone()[0], 0)  # only a check
        ok(owner.post_json("/api/staff/reports/historic/import", {"csv": csv, "commit": True}))
        r = ok(owner.post_json("/api/staff/reports/historic/import", {"csv": "%d-10,Holiday club,125" % last_year,
                                                                      "commit": True})).json()
        self.assertEqual(r["replaced"], 1)
        rows = ok(owner.get("/api/staff/reports/historic")).json()["rows"]
        self.assertEqual(sorted((x["month"], x["category"], x["attendances"]) for x in rows),
                         [("%d-10" % last_year, "HAF", 30), ("%d-10" % last_year, "Holiday club", 125),
                          ("%d-11" % last_year, "Holiday club", 80)])
        self.assertEqual(rows[0]["category"] in ("HAF", "Holiday club"), True)
        # managers can see it but not change it
        mgr = self.admin(roles=("manager",))
        ok(mgr.get("/api/staff/reports/historic"))
        self.assertEqual(mgr.post_json("/api/staff/reports/historic/import", {"csv": csv}).status, 403)
        # the chart: October last year includes the history
        y = ok(mgr.get("/api/staff/reports/year-on-year?years=3")).json()["years"]
        self.assertEqual(len(y), 3)
        month = [m for yr in y for m in yr["months"] if m["key"] == "%d-10" % last_year][0]
        self.assertEqual((month["historic"], month["total"]), (155, 155))
        self.assertTrue(any(yr["has_historic"] for yr in y))
        self.assertEqual(len(y[0]["months"]), 12)
        self.assertTrue(y[-1]["months"][0]["key"].endswith("-09"))  # reporting year from September
        ok(owner.post_json("/api/staff/reports/historic/%d/delete" % rows[0]["id"], {}))


def catalogue_today():
    from hah import catalogue
    return catalogue.uk_today()
