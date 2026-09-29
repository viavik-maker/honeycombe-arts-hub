"""Saved searches and bulk actions from People search results."""
import json

from hah import db, ratelimit
from tests.booking_helpers import make_activity, set_settings
from tests.family_helpers import complete_child, ok, register_family
from tests.support import ServerTestCase


class SavedSearchTest(ServerTestCase):
    def setUp(self):
        set_settings(booking_live=True)
        ratelimit.reset()

    def test_save_share_run_and_delete(self):
        mine = self.admin(roles=("manager",))
        other = self.admin(roles=("manager",))
        ok(mine.post_json("/api/staff/searches", {"name": "Private one", "scope": "children", "q": "Zara",
                                                  "filters": {"min_age": "5", "allergy": True, "bad": {"x": 1}}}))
        sid = ok(mine.post_json("/api/staff/searches", {"name": "Shared one", "scope": "families", "shared": True})).json()["id"]
        self.assertEqual(mine.post_json("/api/staff/searches", {"name": "", "scope": "children"}).status, 422)
        self.assertEqual(mine.post_json("/api/staff/searches", {"name": "Staff", "scope": "staff"}).status, 422)
        got = ok(mine.get("/api/staff/searches")).json()["searches"]
        self.assertEqual([s["name"] for s in got], ["Private one", "Shared one"])
        self.assertEqual(got[0]["filters"], {"min_age": "5", "allergy": True})  # nested values dropped
        theirs = ok(other.get("/api/staff/searches")).json()["searches"]
        self.assertEqual([(s["name"], s["mine"]) for s in theirs], [("Shared one", False)])
        # only the owner (or someone who manages staff) can delete it
        self.assertEqual(other.post_json("/api/staff/searches/%d/delete" % sid, {}).status, 404)
        ok(mine.post_json("/api/staff/searches/%d/delete" % sid, {}))
        self.assertEqual(ok(other.get("/api/staff/searches")).json()["searches"], [])

    def test_bulk_export_and_waiting_list(self):
        a = register_family()
        zara = complete_child(a, first_name="Zara")
        zed = complete_child(a, first_name="Zed", dob="2019-02-02")
        b = register_family()
        zoe = complete_child(b, first_name="Zoe")
        mgr = self.admin(roles=("manager",))
        # export only the ticked rows
        csv = mgr.request("POST", "/api/staff/search/export", json.dumps(
            {"q": "Z", "scope": "children", "refs": [zara, zoe]}).encode(), headers={"Content-Type": "application/json"})
        self.assertIn("Zara", csv.text)
        self.assertIn("Zoe", csv.text)
        self.assertNotIn("Zed", csv.text)
        self.assertEqual(ok(mgr.post_json("/api/staff/search", {"scope": "children", "refs": []})).json()["results"], [])
        # add them to a full session's waiting list: brothers and sisters grouped, the too-young one skipped
        aid, (sid,) = make_activity(sessions=1, capacity=0, min_age=96, waitlist_enabled=1)  # 8 and over
        r = ok(mgr.post_json("/api/staff/bookings/waitlist-add", {"session_id": sid, "participant_refs": [zara, zed, zoe]})).json()
        self.assertEqual(r["added"], 2)
        self.assertEqual(len(r["skipped"]), 1)
        self.assertIn("Zed", r["skipped"][0])
        r = ok(mgr.post_json("/api/staff/bookings/waitlist-add", {"session_id": sid, "participant_refs": [zara]})).json()
        self.assertEqual(r["added"], 0)  # already on it
        self.assertIn("already on the waiting list", r["skipped"][0])
        with db.read() as c:
            rows = c.execute("SELECT b.status, b.waitlist_group, b.created_via FROM bookings b WHERE session_id=?",
                             (sid,)).fetchall()
        self.assertEqual({x["status"] for x in rows}, {"waitlisted"})
        self.assertEqual(len({x["waitlist_group"] for x in rows}), 2)  # one group per family
        # with the override permission and a reason, the younger child can go on too
        r = ok(self.admin().post_json("/api/staff/bookings/waitlist-add", {
            "session_id": sid, "participant_refs": [zed], "override": True, "reason": "Sibling"})).json()
        self.assertEqual(r["added"], 1)
        # adding to a session with room offers it straight away
        aid2, (sid2,) = make_activity(sessions=1, capacity=5, first_day=40)
        r = ok(mgr.post_json("/api/staff/bookings/waitlist-add", {"session_id": sid2, "participant_refs": [zoe]})).json()
        self.assertEqual((r["added"], r["offered"]), (1, 1))
        with db.read() as c:
            self.assertEqual(c.execute("SELECT status FROM bookings WHERE session_id=?", (sid2,)).fetchone()[0], "offered")
        # session staff can't do bulk waiting-list adds
        self.assertEqual(self.admin(roles=("session_staff",)).post_json("/api/staff/bookings/waitlist-add", {
            "session_id": sid, "participant_refs": [zoe]}).status, 403)
