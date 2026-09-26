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
