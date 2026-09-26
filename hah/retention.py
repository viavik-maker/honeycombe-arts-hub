"""Turning 18, and keeping data only as long as we need it.

Turning 18 (daily): the parent is told their child is now an adult, and can
hand the child's record to them — the young person gets an email to set up
their own account, and the record moves across. If nothing happens within
turned_18_days, the child leaves the parent's account (archived; they can
register themselves at any time).

Long-range retention (nightly, after gdpr.retention_job), with the periods
in Booking settings:
  * accounts nobody has used for retention_inactive_years: warned by email,
    then closed 30 days later (the normal erasure then runs);
  * accident and incident records past their date (the child's 25th
    birthday): the details are cleared; safeguarding concerns go to the DSL;
  * children held only for those records are then anonymised;
  * registers older than retention_register_years lose the link to the
    child and who collected them (the counts stay for reports);
  * old messages lose their text (the delivery record stays); the audit log
    is trimmed;
  * imported families who never activated go after a year, and one-off
    guests' contact details a year after their last event;
  * invoices over 6 years old lose the name and address of erased families."""
import datetime

from . import audit, booking_settings, catalogue, db, family, intray, outbox, ratelimit, security, validate, worker
from .validate import Invalid
from .web import route, site_url

WARN_DAYS = 30
HANDOVER_DAYS = 14
REMOVED = "[removed at the end of the retention period]"
MESSAGE_REMOVED = "[message removed after the retention period]"


def _years_ago(today, n):
    try:
        return today.replace(year=today.year - n)
    except ValueError:
        return today.replace(year=today.year - n, day=28)


def _utc(**kw):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- turning 18


@worker.job("turning_18", daily_at="07:10", timeout=300)
def turning_18(today=None):
    today = today or catalogue.uk_today()
    adult_dob = _years_ago(today, 18).isoformat()
    days = booking_settings.get("turned_18_days")
    told = moved = 0
    with db.tx() as c:
        for p in c.execute("SELECT p.*, a.email, a.first_name AS parent FROM participants p JOIN accounts a"
                           " ON a.id=p.account_id WHERE p.is_account_holder=0 AND p.status='active' AND p.dob<=?"
                           " AND p.adult_notified_at IS NULL AND a.status IN ('active','pending_activation')",
                           (adult_dob,)).fetchall():
            c.execute("UPDATE participants SET adult_notified_at=? WHERE id=?", (db.now(), p["id"]))
            if p["email"]:
                outbox.email(c, p["email"], "turned_18", {"first_name": p["parent"], "child": p["first_name"],
                                                          "days": days}, account_id=p["account_id"])
            intray.add(c, "turned_18", "%s has turned 18" % p["first_name"], perm="people.edit",
                       entity_type="participant", entity_id=p["id"], participant_id=p["id"], account_id=p["account_id"])
            told += 1
        cutoff = _utc(days=-days)
        today_iso = today.isoformat()
        for p in c.execute("SELECT * FROM participants WHERE is_account_holder=0 AND status='active'"
                           " AND adult_notified_at IS NOT NULL AND adult_notified_at<?", (cutoff,)).fetchall():
            upcoming = c.execute("SELECT 1 FROM bookings b JOIN activity_sessions s ON s.id=b.session_id WHERE"
                                 " b.participant_id=? AND s.date>=? AND b.status IN " + catalogue.HOLDING_SQL,
                                 (p["id"], today_iso)).fetchone()
            if upcoming:
                continue  # wait until what's booked has happened
            c.execute("UPDATE participants SET status='archived', updated_at=? WHERE id=?", (db.now(), p["id"]))
            audit.record(c, None, "participant.archived_at_18", entity_type="participant", entity_id=p["id"],
                         participant_id=p["id"], account_id=p["account_id"])
            moved += 1
    return "told %d, archived %d" % (told, moved) if told or moved else None


@route("POST", "/api/account/handover/<ref>/start", auth="holder")
def start_handover(h, ref):
    who = h.principal("account")
    email = validate.email((h.json_body() or {}).get("email"))
    if not email:
        raise Invalid({"email": "Enter their own email address."})
    if not ratelimit.hit("acct_email_send", email.lower()):
        raise ValueError("Too many emails to that address — please try later.")
    with db.tx() as c:
        p = c.execute("SELECT * FROM participants WHERE ref=? AND account_id=? AND status='active' AND"
                      " is_account_holder=0", (ref, who["id"])).fetchone()
        if not p:
            raise LookupError
        if family.age_years(p["dob"]) < 18:
            raise ValueError("%s can have their own account once they're 18." % p["first_name"])
        if email.lower() == (who["email"] or "").lower() or \
                c.execute("SELECT 1 FROM accounts WHERE email=? AND status<>'anonymised'", (email,)).fetchone() or \
                c.execute("SELECT 1 FROM carers WHERE email=? AND status<>'removed'", (email,)).fetchone():
            raise Invalid({"email": "That email address already has an account here. Use one that's just theirs."})
        token = security.new_token()
        c.execute("UPDATE handovers SET used_at=? WHERE participant_id=? AND used_at IS NULL", (db.now(), p["id"]))
        c.execute("INSERT INTO handovers(participant_id, from_account_id, email, token_hash, expires_at, created_at)"
                  " VALUES (?,?,?,?,?,?)", (p["id"], who["id"], email, security.hash_token(token),
                                           _utc(days=HANDOVER_DAYS), db.now()))
        outbox.email(c, email, "handover_invite", {"first_name": p["first_name"], "days": HANDOVER_DAYS,
                                                   "parent": "%s %s" % (who["first_name"], who["last_name"])},
                     to_name=p["first_name"], secret="%s/handover#t=%s" % (site_url(h), token), account_id=who["id"])
        audit.record(c, h, "participant.handover_started", entity_type="participant", entity_id=p["id"],
                     participant_id=p["id"], account_id=who["id"], account_actor=who["id"])
    return h.json({"ok": True})


def _handover_row(c, token):
    return c.execute("SELECT hv.*, p.first_name, p.last_name, p.status AS p_status, p.account_id AS p_account"
                     " FROM handovers hv JOIN participants p ON p.id=hv.participant_id WHERE hv.token_hash=?"
                     " AND hv.used_at IS NULL AND hv.expires_at>?", (security.hash_token(token or ""), db.now())).fetchone()


@route("POST", "/api/account/handover/check")
def handover_check(h):
    if not ratelimit.hit("acct_reset", h.client_ip()):
        return h.json({"error": "Too many attempts — please wait a few minutes and try again."}, 429)
    with db.read() as c:
        r = _handover_row(c, (h.json_body() or {}).get("token"))
    if not r or r["p_status"] != "active" or r["p_account"] != r["from_account_id"]:
        return h.json({"error": "This link has expired or has already been used. Ask for a new one."}, 400)
    return h.json({"first_name": r["first_name"], "email": r["email"]})


@route("POST", "/api/account/handover/accept")
def handover_accept(h):
    from .accounts import start_session
    d = h.json_body() or {}
    if not ratelimit.hit("acct_reset", h.client_ip()):
        return h.json({"error": "Too many attempts — please wait a few minutes and try again."}, 429)
    with db.tx() as c:
        r = _handover_row(c, d.get("token"))
        if not r or r["p_status"] != "active" or r["p_account"] != r["from_account_id"]:
            return h.json({"error": "This link has expired or has already been used. Ask for a new one."}, 400)
        problem = security.password_problem(d.get("password") or "", email=r["email"])
        if problem:
            return h.json({"error": problem, "errors": {"password": problem}}, 422)
        if c.execute("SELECT 1 FROM accounts WHERE email=? AND status<>'anonymised'", (r["email"],)).fetchone():
            return h.json({"error": "That email address already has an account. Sign in with it instead."}, 409)
        mobile = validate.uk_mobile(d.get("mobile") or "") or None
        now = db.now()
        aid = c.execute("INSERT INTO accounts(ref, kind, email, email_verified_at, password_hash, status, first_name,"
                        " last_name, mobile, source, created_at, updated_at, activated_at) VALUES"
                        " (?, 'adult', ?,?,?, 'active', ?,?,?, 'self', ?,?,?)",
                        (family.new_ref("A"), r["email"], now, security.hash_password(d["password"]), r["first_name"],
                         r["last_name"], mobile, now, now, now)).lastrowid
        pid = r["participant_id"]
        # their record moves; the parent's answers for them (consents) no longer count, so they give their own
        c.execute("UPDATE participants SET account_id=?, is_account_holder=1, target_level='adult', needs_review=1,"
                  " collection_pw_hash=NULL, go_home_alone=NULL, updated_at=? WHERE id=?", (aid, now, pid))
        c.execute("UPDATE consents SET superseded_at=? WHERE participant_id=? AND superseded_at IS NULL", (now, pid))
        c.execute("UPDATE handovers SET used_at=?, new_account_id=? WHERE id=?", (now, aid, r["id"]))
        family.compute_level(c, pid)
        family.recompute_account(c, r["from_account_id"])
        parent = c.execute("SELECT email, first_name FROM accounts WHERE id=?", (r["from_account_id"],)).fetchone()
        if parent["email"]:
            outbox.email(c, parent["email"], "handover_done", {"first_name": parent["first_name"],
                                                               "child": r["first_name"]}, account_id=r["from_account_id"])
        audit.record(c, h, "participant.handed_over", entity_type="participant", entity_id=pid, participant_id=pid,
                     account_id=aid, account_actor=aid, details={"from_account": r["from_account_id"]})
        intray.resolve(c, "turned_18", entity_type="participant", entity_id=pid)
        cookie, csrf = start_session(c, h, aid)
    return h.json({"ok": True, "csrf": csrf}, headers={"Set-Cookie": cookie})


# ---------------------------------------------------------------- long-range retention


@worker.job("retention_long", daily_at="03:45", timeout=900)
def long_retention(today=None):
    today = today or catalogue.uk_today()
    st = booking_settings.get_all()
    out = {}
    with db.tx() as c:
        out["warned"], out["closed"] = _inactive_accounts(c, today, st["retention_inactive_years"])
    with db.tx() as c:
        out["incidents"], out["released"] = _incident_records(c, today)
    with db.tx() as c:
        cutoff = _years_ago(today, st["retention_register_years"]).isoformat()
        # the counts (with their age/HAF/SEND snapshots) stay for reports; who it was, and who collected them, go
        out["registers"] = c.execute(
            "UPDATE attendance SET participant_id=NULL, collected_by_name=NULL, collected_by_relationship=NULL,"
            " notes=NULL WHERE (participant_id IS NOT NULL OR collected_by_name IS NOT NULL OR notes IS NOT NULL) AND"
            " session_id IN (SELECT id FROM activity_sessions WHERE date<?)", (cutoff,)).rowcount
        msg_cutoff = _years_ago(today, st["retention_message_years"]).isoformat()
        out["messages"] = c.execute(
            "UPDATE message_deliveries SET body_text=?, body_html=NULL, headers=NULL, secret=NULL WHERE created_at<?"
            " AND body_text<>? AND status NOT IN ('queued','sending')", (MESSAGE_REMOVED, msg_cutoff, MESSAGE_REMOVED)).rowcount
        year_ago = _years_ago(today, 1).isoformat()
        # imported MagicBooking families who never activated: closed, then erased by the nightly job
        stale = [r[0] for r in c.execute("SELECT id FROM accounts WHERE status='pending_activation' AND source='import'"
                                         " AND created_at<?", (year_ago,))]
        for aid in stale:
            c.execute("UPDATE accounts SET status='closed', closed_at=?, erase_after=?, updated_at=? WHERE id=?",
                      (db.now(), db.now(), db.now(), aid))
        out["never_activated"] = len(stale)
        # one-off guests: contact details go a year after their last event
        guests = [r[0] for r in c.execute(
            "SELECT g.id FROM guest_contacts g WHERE g.anonymised_at IS NULL AND g.account_id IS NULL AND g.created_at<?"
            " AND NOT EXISTS (SELECT 1 FROM bookings b JOIN activity_sessions s ON s.id=b.session_id"
            " WHERE b.guest_contact_id=g.id AND s.date>=?)", (year_ago, year_ago))]
        for gid in guests:
            c.execute("UPDATE guest_contacts SET email=?, phone=NULL, name=NULL, anonymised_at=? WHERE id=?",
                      ("erased-%d@invalid" % gid, db.now(), gid))
            c.execute("UPDATE bookings SET party_name=NULL, notes=NULL WHERE guest_contact_id=?", (gid,))
        out["guests"] = len(guests)
        audit_cutoff = _years_ago(today, max(6, st["retention_audit_years"])).isoformat()
        out["audit"] = c.execute("DELETE FROM audit_log WHERE at<?", (audit_cutoff,)).rowcount
        fin_cutoff = _years_ago(today, 6).isoformat()
        old = [r[0] for r in c.execute(
            "SELECT i.id FROM invoices i LEFT JOIN accounts a ON a.id=i.account_id LEFT JOIN guest_contacts g"
            " ON g.id=i.guest_contact_id WHERE i.issue_date<? AND i.bill_to_name<>'Erased' AND"
            " (a.status='anonymised' OR g.anonymised_at IS NOT NULL OR (i.account_id IS NULL AND i.guest_contact_id"
            " IS NULL))", (fin_cutoff,))]
        for iid in old:
            c.execute("UPDATE invoices SET bill_to_name='Erased', bill_to_email=NULL, bill_to_address=NULL WHERE id=?",
                      (iid,))
            c.execute("UPDATE invoice_lines SET participant_name=NULL WHERE invoice_id=?", (iid,))
        out["invoices"] = len(old)
        done = {k: v for k, v in out.items() if v}
        if done:
            audit.record(c, None, "retention.run", details=done)
    return ", ".join("%s %d" % kv for kv in done.items()) if done else None


def _inactive_accounts(c, today, years):
    cutoff_date = _years_ago(today, years).isoformat()
    cutoff = cutoff_date + "T00:00:00Z"
    warned = closed = 0
    # anyone warned who has been back since: stop the clock
    c.execute("UPDATE accounts SET inactive_warned_at=NULL WHERE inactive_warned_at IS NOT NULL AND"
              " (last_login_at>inactive_warned_at OR EXISTS (SELECT 1 FROM bookings b WHERE b.account_id=accounts.id"
              " AND b.created_at>accounts.inactive_warned_at))")
    idle = ("a.status IN ('active','pending_activation') AND COALESCE(a.last_login_at, a.activated_at, a.created_at)<?"
            " AND NOT EXISTS (SELECT 1 FROM bookings b JOIN activity_sessions s ON s.id=b.session_id WHERE"
            " b.account_id=a.id AND (s.date>=? OR b.created_at>=?))"
            " AND NOT EXISTS (SELECT 1 FROM carers cr WHERE cr.account_id=a.id AND cr.last_login_at>=?)")
    when = (today + datetime.timedelta(days=WARN_DAYS)).strftime("%-d %B %Y")
    for a in c.execute("SELECT a.* FROM accounts a WHERE a.inactive_warned_at IS NULL AND " + idle,
                       (cutoff, cutoff_date, cutoff, cutoff)).fetchall():
        c.execute("UPDATE accounts SET inactive_warned_at=? WHERE id=?", (db.now(), a["id"]))
        if a["email"]:
            outbox.email(c, a["email"], "inactive_warning", {"first_name": a["first_name"], "years": years,
                                                             "when": when, "signin_url": site_url(None) + "/login"},
                         account_id=a["id"])
        warned += 1
    for a in c.execute("SELECT a.* FROM accounts a WHERE a.inactive_warned_at<? AND " + idle,
                       (_utc(days=-WARN_DAYS), cutoff, cutoff_date, cutoff, cutoff)).fetchall():
        c.execute("UPDATE accounts SET status='closed', closed_at=?, erase_after=?, updated_at=? WHERE id=?",
                  (db.now(), db.now(), db.now(), a["id"]))
        c.execute("DELETE FROM account_sessions WHERE account_id=?", (a["id"],))
        audit.record(c, None, "gdpr.closed_inactive", entity_type="account", entity_id=a["id"], account_id=a["id"])
        closed += 1
    return warned, closed


def _incident_records(c, today):
    """Accident records past the child's 25th birthday; then children held only for them."""
    cleared = 0
    for inc in c.execute("SELECT * FROM incidents WHERE retain_until IS NOT NULL AND retain_until<? AND"
                         " description<>?", (today.isoformat(), REMOVED)).fetchall():
        if inc["restricted"]:  # safeguarding: the DSL decides
            intray.add(c, "retention_review", "A safeguarding record has reached the end of its retention period",
                       perm="safeguarding.view", entity_type="incident", entity_id=inc["id"])
            continue
        c.execute("UPDATE incidents SET description=?, action_taken=NULL, witnesses=NULL, follow_up=NULL,"
                  " location=NULL, first_aider=NULL, body_map=NULL, updated_at=? WHERE id=?",
                  (REMOVED, db.now(), inc["id"]))
        c.execute("UPDATE incident_people SET person_name=CASE WHEN participant_id IS NULL THEN '(removed)' END"
                  " WHERE incident_id=?", (inc["id"],))
        cleared += 1
    released = 0
    for p in c.execute("SELECT * FROM participants WHERE status='retention_hold' AND f_safeguarding=0").fetchall():
        still = c.execute("SELECT 1 FROM incidents i JOIN incident_people ip ON ip.incident_id=i.id WHERE"
                          " ip.participant_id=? AND (i.retain_until IS NULL OR i.retain_until>=? OR i.restricted=1)",
                          (p["id"], today.isoformat())).fetchone()
        sg = c.execute("SELECT family_info FROM participant_safeguarding WHERE participant_id=?", (p["id"],)).fetchone()
        if still or (sg and (sg["family_info"] or "").strip()):
            continue  # still needed, or the DSL decides (as in gdpr.erase_account)
        c.execute("DELETE FROM participant_safeguarding WHERE participant_id=?", (p["id"],))
        c.execute("UPDATE participants SET status='anonymised', first_name='Deleted', last_name='person',"
                  " dob=substr(dob,1,4) || '-01-01', anonymised_at=?, updated_at=? WHERE id=?",
                  (db.now(), db.now(), p["id"]))
        released += 1
    return cleared, released
