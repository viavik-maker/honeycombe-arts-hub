"""Data protection: families deleting their account, asking for their data,
and the retention jobs.

Deleting an account:
  1. the family asks (password re-entered); future bookings are cancelled,
     marketing stops and they're signed out;
  2. 14 days' cooling-off (staff can restore the account if they change
     their mind);
  3. the nightly job erases it, keeping only what the law makes us keep:
     invoices (6 years, as issued), accident and injury records (until the
     child is 25), and safeguarding information, which the DSL reviews.
Asking for their data raises an in-tray item; staff download the export
from the family's record (DSL-written material and restricted incidents
are left out)."""
import datetime
import json

from . import audit, bookings, catalogue, db, intray, marketing, money, outbox, worker
from .web import route

COOLING_OFF_DAYS = 14
UNVERIFIED_DAYS = 7


def _utc(days=0):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- families


@route("POST", "/api/account/delete", auth="account")
def request_deletion(h):
    from .accounts import recently_reauthenticated
    who = h.principal("account")
    if not recently_reauthenticated(who):
        return h.json({"error": "Please enter your password to delete your account.", "reauth": True}, 403)
    today = catalogue.uk_today().isoformat()
    with db.tx() as c:
        a = c.execute("SELECT * FROM accounts WHERE id=?", (who["id"],)).fetchone()
        cancelled = 0
        for b in c.execute("SELECT b.* FROM bookings b JOIN activity_sessions s ON s.id=b.session_id WHERE b.account_id=?"
                           " AND s.date>=? AND b.status IN ('confirmed','pending_approval','waitlisted','offered')",
                           (a["id"], today)).fetchall():
            inv = money.invoice_for_booking(c, b["id"])
            outcome = "none"
            if inv and b["price_pence"]:
                outcome = "refund_card" if money.paid_by_card(c, inv["id"]) else "refund_offline"
            bookings.cancel(c, h, b, reason="Account deleted by family", money_outcome=outcome, by_account=a["id"],
                            notify=False)
            cancelled += 1
        credit = money.credit_balance(c, a["id"])
        if credit > 0:
            intray.add(c, "refund_credit", "Account closing with %s credit — refund it" % money.pounds(credit),
                       perm="finance.manage", entity_type="account", entity_id=a["id"], account_id=a["id"])
        if a["email"]:
            marketing.unsubscribe(c, a["email"], "email", h)
            marketing.unsubscribe(c, a["email"], "sms", h)
        erase_after = _utc(COOLING_OFF_DAYS)
        c.execute("UPDATE accounts SET status='closed', closed_at=?, erase_after=?, updated_at=? WHERE id=?",
                  (db.now(), erase_after, db.now(), a["id"]))
        c.execute("DELETE FROM account_sessions WHERE account_id=?", (a["id"],))
        when = catalogue.parse_utc(erase_after).astimezone(catalogue.UK).strftime("%-d %B %Y")
        outbox.email(c, a["email"], "account_closing", {"first_name": a["first_name"], "when": when,
                                                        "cancelled": cancelled}, account_id=a["id"])
        intray.add(c, "account_deletion", "A family has asked to delete their account (erased on %s)" % when,
                   perm="gdpr.manage", entity_type="account", entity_id=a["id"], account_id=a["id"])
        audit.record(c, h, "gdpr.deletion_requested", entity_type="account", entity_id=a["id"], account_id=a["id"],
                     details={"cancelled_bookings": cancelled})
    from .accounts import clear_cookie
    return h.json({"ok": True, "erase_on": when}, headers={"Set-Cookie": clear_cookie(h)})


@route("POST", "/api/account/data-request", auth="account")
def data_request(h):
    who = h.principal("account")
    with db.tx() as c:
        new = intray.add(c, "data_request", "A family has asked for a copy of their data (reply within one month)",
                         perm="gdpr.manage", entity_type="account", entity_id=who["id"], account_id=who["id"])
        if new:
            outbox.email(c, who["email"], "data_request_received", {"first_name": who["first_name"]},
                         account_id=who["id"])
            audit.record(c, h, "gdpr.data_requested", entity_type="account", entity_id=who["id"], account_id=who["id"])
    return h.json({"ok": True, "message": "Thanks — we'll email you your data within a month (usually much sooner)."})


# ---------------------------------------------------------------- staff


def _account(c, ref):
    a = c.execute("SELECT * FROM accounts WHERE ref=?", (ref,)).fetchone()
    if not a:
        raise LookupError
    return a


@route("POST", "/api/staff/people/accounts/<ref>/restore", auth="staff", perm="gdpr.manage")
def restore(h, ref):
    with db.tx() as c:
        a = _account(c, ref)
        if a["status"] != "closed" or not a["erase_after"]:
            raise ValueError("Only accounts waiting to be deleted can be restored.")
        c.execute("UPDATE accounts SET status=?, closed_at=NULL, erase_after=NULL, updated_at=? WHERE id=?",
                  ("active" if a["password_hash"] else "pending_activation", db.now(), a["id"]))
        intray.resolve(c, "account_deletion", entity_type="account", entity_id=a["id"], staff_id=h.staff()["id"])
        audit.record(c, h, "gdpr.restored", entity_type="account", entity_id=a["id"], account_id=a["id"])
    return h.json({"ok": True})


@route("POST", "/api/staff/people/accounts/<ref>/erase", auth="staff", perm="gdpr.manage")
def erase_now(h, ref):
    with db.tx() as c:
        a = _account(c, ref)
        if a["status"] == "anonymised":
            raise ValueError("Already erased.")
        if a["status"] != "closed":
            raise ValueError("Close the account first (the family deletes it, or it's merged).")
        kept = erase_account(c, a["id"], h)
    return h.json({"ok": True, "kept": kept})


@route("GET", "/api/staff/people/accounts/<ref>/export", auth="staff", perm="gdpr.manage")
def export(h, ref):
    """Everything we hold about a family, for a subject access request."""
    with db.tx() as c:
        a = _account(c, ref)
        data = export_account(c, a)
        audit.record(c, h, "gdpr.export", entity_type="account", entity_id=a["id"], account_id=a["id"])
    body = json.dumps(data, indent=2, default=str).encode()
    return h.send(200, body, "application/json; charset=utf-8",
                  {"Content-Disposition": 'attachment; filename="data-%s.json"' % a["ref"], "Cache-Control": "no-store"})


def _rows(c, sql, args, drop=()):
    return [{k: r[k] for k in r.keys() if k not in drop} for r in c.execute(sql, args)]


SECRET_COLS = ("password_hash", "collection_pw_hash", "token_hash", "secret")


def export_account(c, a):
    aid = a["id"]
    out = {"account": {k: a[k] for k in a.keys() if k not in SECRET_COLS + ("staff_notes",)},
           "emergency_contacts": _rows(c, "SELECT full_name, relationship, phone, can_collect FROM emergency_contacts"
                                          " WHERE account_id=?", (aid,)),
           "people": []}
    for p in c.execute("SELECT * FROM participants WHERE account_id=?", (aid,)).fetchall():
        pid = p["id"]
        out["people"].append({
            "details": {k: p[k] for k in p.keys() if k not in SECRET_COLS},
            "health": _rows(c, "SELECT * FROM participant_health WHERE participant_id=?", (pid,)),
            "gp": _rows(c, "SELECT * FROM participant_gp WHERE participant_id=?", (pid,)),
            # what the family told us is theirs to see; nothing here is written by the DSL
            "family_information": _rows(c, "SELECT family_info, updated_at FROM participant_safeguarding"
                                           " WHERE participant_id=?", (pid,)),
            "attendance": _rows(c, "SELECT s.date, act.title, at.status, at.signed_in_at, at.signed_out_at,"
                                   " at.collected_by_name, at.collected_by_relationship FROM attendance at JOIN"
                                   " activity_sessions s ON s.id=at.session_id JOIN activities act ON act.id=s.activity_id"
                                   " WHERE at.participant_id=? ORDER BY s.date", (pid,)),
            "incidents": _rows(c, "SELECT i.occurred_at, i.kind, i.description, i.action_taken, i.first_aid_given,"
                                  " ip.acknowledged_at FROM incidents i JOIN incident_people ip ON ip.incident_id=i.id"
                                  " WHERE ip.participant_id=? AND i.restricted=0", (pid,)),
        })
    out["consents"] = _rows(c, "SELECT t.key, t.version, t.label, x.value, x.source, x.created_at, x.superseded_at,"
                               " p.first_name AS person FROM consents x JOIN consent_types t ON t.id=x.consent_type_id"
                               " LEFT JOIN participants p ON p.id=x.participant_id WHERE x.account_id=? ORDER BY x.id",
                            (aid,))
    out["bookings"] = _rows(c, "SELECT b.ref, s.date, s.start_time, act.title, b.status, b.price_pence, b.funding,"
                               " b.created_at, b.cancelled_at, b.cancel_reason FROM bookings b JOIN activity_sessions s"
                               " ON s.id=b.session_id JOIN activities act ON act.id=b.activity_id WHERE b.account_id=?"
                               " ORDER BY s.date", (aid,))
    out["invoices"] = [money.invoice_json(c, i) for i in c.execute("SELECT * FROM invoices WHERE account_id=?", (aid,))]
    out["payments"] = _rows(c, "SELECT ref, amount_pence, method, reference, received_at FROM payments WHERE account_id=?",
                            (aid,))
    out["messages"] = [dict(sent_at=r["sent_at"] or r["created_at"], channel=r["channel"], subject=r["subject"],
                            body=outbox.archived_body(r))
                       for r in c.execute("SELECT * FROM message_deliveries WHERE account_id=? AND kind<>'staff'"
                                          " ORDER BY id", (aid,))]
    if a["email"]:
        out["contact_form_messages"] = _rows(c, "SELECT name, email, phone, message, created_at FROM contact_messages"
                                                " WHERE email=?", (a["email"],))
        out["marketing_preferences"] = _rows(c, "SELECT email_opt_in, email_opt_in_at, sms_opt_in, sms_opt_in_at,"
                                                " unsubscribed_email_at, source, wording_version FROM marketing_preferences"
                                                " WHERE email=?", (a["email"],))
        out["guest_bookings"] = _rows(c, "SELECT b.ref, s.date, act.title, b.status, b.places FROM bookings b JOIN"
                                         " guest_contacts g ON g.id=b.guest_contact_id JOIN activity_sessions s ON"
                                         " s.id=b.session_id JOIN activities act ON act.id=b.activity_id WHERE g.email=?",
                                      (a["email"],))
    from . import send_support
    out["send_support_requests"] = [send_support.intake_json(c, it) for it in c.execute(
        "SELECT * FROM send_intakes WHERE account_id=?", (aid,)).fetchall()]
    out["exported_at"] = db.now()
    return out


# ---------------------------------------------------------------- erasure


def erase_account(c, aid, h=None):
    """Remove a family's personal data, keeping only what must be kept.
    Returns what was kept and why."""
    from . import send_support
    kept = []
    now = db.now()
    a = c.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()
    send_support.erase_for_account(c, aid)
    for p in c.execute("SELECT * FROM participants WHERE account_id=?", (aid,)).fetchall():
        pid = p["id"]
        accident = c.execute("SELECT 1 FROM incident_people WHERE participant_id=?", (pid,)).fetchone()
        sg = c.execute("SELECT * FROM participant_safeguarding WHERE participant_id=?", (pid,)).fetchone()
        sensitive = p["f_safeguarding"] or (sg and (sg["family_info"] or "").strip())
        c.execute("DELETE FROM participant_health WHERE participant_id=?", (pid,))
        c.execute("DELETE FROM participant_gp WHERE participant_id=?", (pid,))
        if sensitive:
            kept.append("safeguarding information for review by the DSL")
            intray.add(c, "erasure_safeguarding_review", "Erased account: safeguarding information needs a DSL decision",
                       perm="safeguarding.view", entity_type="participant", entity_id=pid, participant_id=pid)
        else:
            c.execute("DELETE FROM participant_safeguarding WHERE participant_id=?", (pid,))
        if accident or sensitive:
            kept.append("incident records (kept until the child is 25)" if accident else "child's name for the DSL")
            c.execute("UPDATE participants SET status='retention_hold', collection_pw_hash=NULL, collection_alert=NULL,"
                      " school_name=NULL, gender=NULL, updated_at=? WHERE id=?", (now, pid))
        else:
            c.execute("UPDATE participants SET status='anonymised', first_name='Deleted', last_name='person',"
                      " dob=substr(dob,1,4) || '-01-01', school_name=NULL, gender=NULL, collection_pw_hash=NULL,"
                      " collection_alert=NULL, photo_consent=NULL, anonymised_at=?, updated_at=? WHERE id=?",
                      (now, now, pid))
    c.execute("DELETE FROM emergency_contacts WHERE account_id=?", (aid,))
    c.execute("DELETE FROM consents WHERE account_id=?", (aid,))
    c.execute("DELETE FROM account_tokens WHERE account_id=?", (aid,))
    c.execute("DELETE FROM account_sessions WHERE account_id=?", (aid,))
    if a["email"]:
        c.execute("DELETE FROM marketing_preferences WHERE email=? OR account_id=?", (a["email"], aid))
        c.execute("DELETE FROM contact_messages WHERE email=?", (a["email"],))
    for g in c.execute("SELECT id FROM guest_contacts WHERE account_id=? OR email=?", (aid, a["email"] or "")).fetchall():
        c.execute("UPDATE guest_contacts SET email=?, phone=NULL, name=NULL, anonymised_at=? WHERE id=?",
                  ("erased-%d@invalid" % g["id"], now, g["id"]))
    c.execute("UPDATE message_deliveries SET to_address='[erased]', to_name=NULL, subject=NULL, body_text='[erased]',"
              " body_html=NULL, headers=NULL, secret=NULL WHERE account_id=?", (aid,))
    c.execute("UPDATE bookings SET notes=NULL, party_name=NULL WHERE account_id=?", (aid,))
    c.execute("UPDATE intray_items SET title='(erased)', detail=NULL WHERE account_id=? OR participant_id IN"
              " (SELECT id FROM participants WHERE account_id=? AND status='anonymised')", (aid, aid))
    if c.execute("SELECT 1 FROM invoices WHERE account_id=?", (aid,)).fetchone():
        kept.append("invoices as issued (kept 6 years for the accounts)")
    c.execute("UPDATE accounts SET status='anonymised', first_name='Deleted', last_name='account', email=NULL,"
              " mobile=NULL, address_line1=NULL, address_line2=NULL, town=NULL, postcode=NULL, password_hash=NULL,"
              " staff_notes=NULL, legacy_ref=NULL, anonymised_at=?, updated_at=? WHERE id=?", (now, now, aid))
    audit.record(c, h, "gdpr.erased", entity_type="account", entity_id=aid, account_id=aid,
                 details={"kept": sorted(set(kept))})
    return sorted(set(kept))


@worker.job("retention", daily_at="03:25", timeout=900)
def retention_job():
    """Nightly clean-up (Phase 1 scope): erase accounts past their cooling-off,
    purge sign-ups that never verified their email, and clear old sessions,
    links and IP addresses."""
    now = db.now()
    erased = purged = 0
    with db.read() as c:
        due = [r[0] for r in c.execute("SELECT id FROM accounts WHERE status='closed' AND erase_after IS NOT NULL"
                                       " AND erase_after<=?", (now,))]
    for aid in due:  # one transaction each, so a long run never blocks bookings for long
        with db.tx() as c:
            erase_account(c, aid)
            erased += 1
    with db.tx() as c:
        for r in c.execute("SELECT id FROM accounts WHERE status='pending_verification' AND created_at<?",
                           (_utc(-UNVERIFIED_DAYS),)).fetchall():
            if c.execute("SELECT 1 FROM participants WHERE account_id=?", (r["id"],)).fetchone():
                continue
            c.execute("DELETE FROM account_tokens WHERE account_id=?", (r["id"],))
            c.execute("DELETE FROM account_sessions WHERE account_id=?", (r["id"],))
            c.execute("DELETE FROM message_deliveries WHERE account_id=?", (r["id"],))
            c.execute("DELETE FROM accounts WHERE id=?", (r["id"],))
            purged += 1
        c.execute("DELETE FROM account_sessions WHERE expires_at<? OR idle_expires_at<?", (now, now))
        c.execute("DELETE FROM account_tokens WHERE expires_at<?", (_utc(-30),))
        c.execute("DELETE FROM guest_tokens WHERE expires_at<?", (_utc(-30),))
        c.execute("DELETE FROM staff_sessions WHERE expires_at<?", (now,))
        c.execute("UPDATE consents SET ip=NULL WHERE ip IS NOT NULL AND created_at<?", (_utc(-90),))
    return "erased %d, purged %d unverified" % (erased, purged)
