"""Can this person book this session? One place for the rules, used by the
Book page (to grey things out and explain why), the quote and the confirm
transaction (which checks again, so nothing relies on the browser)."""
import datetime

from . import catalogue, family

SECTION_WORDS = {"you": "your details", "contacts": "emergency contacts", "child": "about them", "gp": "GP",
                 "health": "health", "safeguarding": "family information", "consents": "permissions",
                 "collection": "collection password", "confirm": "confirmations", "review": "a quick check"}


class Checker:
    """Caches per-request lookups (profile gaps, a child's bookings)."""

    def __init__(self, c, account):
        self.c, self.account = c, account
        self._missing, self._first = {}, {}

    def missing(self, p, level):
        key = (p["id"], level)
        if key not in self._missing:
            self._missing[key] = family.missing(self.c, p["id"], level)
        return self._missing[key]

    def first_date(self, activity_id):
        if activity_id not in self._first:
            self._first[activity_id] = catalogue.first_session_date(self.c, activity_id)
        return self._first[activity_id]

    def problems(self, activity, session, p, basket=()):
        """[(code, message)] for participant P (a row) booking SESSION.
        BASKET: (session row, participant id) pairs already chosen, so two
        clashing sessions in one basket are caught too."""
        c, out, name = self.c, [], p["first_name"]
        if self.account["status"] != "active":
            out.append(("account", "Please verify your email address before booking."))
        elif self.account["source"] == "import" and not self.account["reconfirmed_at"]:
            out.append(("reconfirm", "Please check and confirm your family's details before booking."))
        # age
        on = catalogue.age_on(activity, session, self.first_date(activity["id"]))
        months = catalogue.months_between(datetime.date.fromisoformat(p["dob"]), on)
        if not activity["min_age_months"] <= months <= activity["max_age_months"]:
            out.append(("age", "%s will be %s — this is for ages %s." % (
                name, _age_words(months), catalogue.age_range_text(activity))))
        # form level
        level = activity["registration_level"]
        if level == "adult":
            if not p["is_account_holder"]:
                out.append(("adult", "This is for young adults (18+) booking for themselves."))
            elif self.missing(p, "adult"):
                out.append(("level", "Please complete your details first (%s)." % _gaps(self.missing(p, "adult"))))
        elif level in ("short", "full"):
            if p["is_account_holder"]:
                out.append(("adult", "This activity is for children."))
            elif self.missing(p, level):
                out.append(("level", "Complete %s's details first: %s." % (name, _gaps(self.missing(p, level)))))
        # HAF
        if activity["haf_only"] and p["haf_status"] not in ("verified", "claimed_eligible", "not_sure"):
            out.append(("haf", "%s's details say they aren't eligible for HAF." % name
                        if p["haf_status"] == "not_eligible" else "Tell us whether %s gets free school meals (HAF)." % name))
        # already booked / clashes
        mine = c.execute("SELECT b.status, s.* FROM bookings b JOIN activity_sessions s ON s.id=b.session_id"
                         " WHERE b.participant_id=? AND s.date=? AND b.status IN "
                         "('pending_payment','pending_approval','offered','confirmed','waitlisted')",
                         (p["id"], session["date"])).fetchall()
        for other in mine:
            if other["id"] == session["id"]:
                out.append(("booked", "%s is already booked on this session." % name if other["status"] != "waitlisted"
                            else "%s is already on the waiting list." % name))
            elif other["status"] != "waitlisted" and _overlap(other, session):
                out.append(("overlap", "%s is booked on another session at the same time." % name))
        for s2, pid in basket:
            if pid == p["id"] and s2["id"] != session["id"] and s2["date"] == session["date"] and _overlap(s2, session):
                out.append(("overlap", "You've chosen two sessions at the same time for %s." % name))
        if activity["haf_only"] and activity["haf_allowance_days"]:
            used = c.execute("SELECT COUNT(*) FROM bookings WHERE participant_id=? AND activity_id=? AND status IN "
                             "('pending_payment','pending_approval','offered','confirmed')",
                             (p["id"], activity["id"])).fetchone()[0]
            used += sum(1 for s2, pid in basket if pid == p["id"] and s2["activity_id"] == activity["id"]
                        and s2["id"] != session["id"])
            if used >= activity["haf_allowance_days"]:
                out.append(("haf_allowance", "%s has used all %d funded HAF days." % (name, activity["haf_allowance_days"])))
        return out


def _overlap(a, b):
    return a["start_time"] < b["end_time"] and b["start_time"] < a["end_time"]


def _gaps(keys):
    return ", ".join(SECTION_WORDS.get(k, k) for k in keys if k != "not_adult") or "a few details"


def _age_words(months):
    if months < 0:
        return "not born yet"
    if months < 24:
        return "%d month%s" % (months, "" if months == 1 else "s")
    return str(months // 12)
