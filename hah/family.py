"""Families: accounts, the people who attend, and how complete each profile is.

A child's *level* is the most demanding kind of activity their profile is
complete enough for (full > short). The parent chooses a *target level*
when adding a child ("what will they come to?"); booking an activity that
needs more asks only for the missing sections. Young adults (18+) booking
for themselves are the account holder and use the adult level."""
import datetime
import json
import secrets

from . import db, formspec, security

REF_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O/1/I


def new_ref(prefix):
    return prefix + "-" + "".join(secrets.choice(REF_ALPHABET) for _ in range(8))


def today():
    from .catalogue import uk_today  # the charity's date, not the server's (UTC)
    return uk_today()


def age_years(dob, on=None):
    return formspec.age_months(datetime.date.fromisoformat(dob), on) // 12


# ---------------------------------------------------------------- consents


def consent_type_ids(c):
    """{key: id} of the active version of each consent question."""
    return {r["key"]: r["id"] for r in c.execute(
        "SELECT key, MAX(id) AS id FROM consent_types WHERE active=1 GROUP BY key")}


def current_consents(c, account_id, participant_id=None):
    """{key: value} of answers that count: the latest answer, to the current
    version of the question, not merely carried over from an import."""
    ids = consent_type_ids(c)
    by_id = {v: k for k, v in ids.items()}
    rows = c.execute(
        "SELECT consent_type_id, value FROM consents WHERE account_id=? AND participant_id IS ?"
        " AND superseded_at IS NULL AND source <> 'import'", (account_id, participant_id))
    return {by_id[r["consent_type_id"]]: r["value"] for r in rows if r["consent_type_id"] in by_id}


def record_consent(c, account_id, participant_id, key, value, source, *, staff_id=None, ip=None):
    """Record an answer (append-only; the previous answer is superseded)."""
    type_id = consent_type_ids(c)[key]
    options = [o[0] for o in json.loads(c.execute("SELECT options FROM consent_types WHERE id=?",
                                                  (type_id,)).fetchone()[0])]
    if value not in options:
        raise ValueError("%s: choose one of the options" % key)
    now = db.now()
    c.execute("UPDATE consents SET superseded_at=? WHERE account_id=? AND participant_id IS ? AND superseded_at IS NULL"
              " AND consent_type_id IN (SELECT id FROM consent_types WHERE key=?)",
              (now, account_id, participant_id, key))
    c.execute("INSERT INTO consents(consent_type_id, account_id, participant_id, value, source, recorded_by_staff_id,"
              " ip, created_at) VALUES (?,?,?,?,?,?,?,?)",
              (type_id, account_id, participant_id, value, source, staff_id, ip, now))
    if participant_id and key == "photo":
        c.execute("UPDATE participants SET photo_consent=? WHERE id=?", (value, participant_id))
    if participant_id and key == "go_home_alone":
        c.execute("UPDATE participants SET go_home_alone=? WHERE id=?", (1 if value == "yes" else 0, participant_id))


# ---------------------------------------------------------------- health flags


def refresh_flags(c, pid):
    """Keep the register/search flags in step with the health section."""
    h = c.execute("SELECT * FROM participant_health WHERE participant_id=?", (pid,)).fetchone()
    has = lambda k: 1 if h and (h[k] or "").strip() else 0  # noqa: E731
    c.execute("UPDATE participants SET f_allergy=?, f_anaphylaxis=?, f_medical=?, f_dietary=?, f_send=?, f_semh=?,"
              " f_religious=?, updated_at=? WHERE id=?",
              (has("allergies"), 1 if h and h["anaphylaxis"] else 0,
               1 if h and ((h["medical_conditions"] or "").strip() or (h["medication"] or "").strip()) else 0,
               has("dietary"), has("send_needs"), has("semh_needs"), has("religious_requirements"), db.now(), pid))


# ---------------------------------------------------------------- completeness


def _filled(row, section, level):
    """Required fields of SECTION at LEVEL that ROW (a dict) is missing."""
    return [f["key"] for f in formspec.SPEC[section]["fields"]
            if level in f["required"] and not (row or {}).get(f["key"])]


def missing(c, pid, level):
    """What a participant still needs for LEVEL, as a list of section keys
    ('you', 'contacts', 'child', 'gp', 'health', 'safeguarding', 'consents',
    'collection', 'confirm'). Empty means ready."""
    p = c.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
    acct = dict(c.execute("SELECT * FROM accounts WHERE id=?", (p["account_id"],)).fetchone())
    gaps = []
    if _filled(acct, "you", level):
        gaps.append("you")
    n_contacts = c.execute("SELECT COUNT(*) FROM emergency_contacts WHERE account_id=?", (acct["id"],)).fetchone()[0]
    if n_contacts < formspec.CONTACTS["required"][level]:
        gaps.append("contacts")
    if level == "adult":
        if not p["is_account_holder"] or age_years(p["dob"]) < 18:
            return ["not_adult"]
    else:
        if _filled(dict(p), "child", level):
            gaps.append("child")
        if level == "full":
            gp = c.execute("SELECT * FROM participant_gp WHERE participant_id=?", (pid,)).fetchone()
            if _filled(dict(gp) if gp else None, "gp", level):
                gaps.append("gp")
            if not c.execute("SELECT 1 FROM participant_safeguarding WHERE participant_id=?", (pid,)).fetchone():
                gaps.append("safeguarding")
            if not p["collection_pw_hash"]:
                gaps.append("collection")
        if not c.execute("SELECT 1 FROM participant_health WHERE participant_id=?", (pid,)).fetchone():
            gaps.append("health")
    months = formspec.age_months(datetime.date.fromisoformat(p["dob"]))
    answered = current_consents(c, acct["id"], pid)
    if any(q["required"] and q["key"] not in answered for q in formspec.consent_keys(level, months)):
        gaps.append("consents")
    acks = current_consents(c, acct["id"], None)
    if any(k not in acks for k in formspec.ACKNOWLEDGEMENTS):
        gaps.append("confirm")
    if p["needs_review"] and "review" not in gaps:
        gaps.append("review")
    return gaps


def compute_level(c, pid):
    """Recalculate and store a participant's level."""
    p = c.execute("SELECT is_account_holder FROM participants WHERE id=?", (pid,)).fetchone()
    if p["is_account_holder"]:
        level = "adult" if not missing(c, pid, "adult") else "none"
    elif not missing(c, pid, "full"):
        level = "full"
    elif not missing(c, pid, "short"):
        level = "short"
    else:
        level = "none"
    c.execute("UPDATE participants SET level=? WHERE id=?", (level, pid))
    return level


def recompute_account(c, account_id):
    for r in c.execute("SELECT id FROM participants WHERE account_id=? AND status='active'", (account_id,)).fetchall():
        compute_level(c, r["id"])


LEVEL_RANK = {"none": 0, "short": 1, "full": 2}


def meets(level_have, level_needed):
    """Does a participant at LEVEL_HAVE satisfy an activity needing LEVEL_NEEDED?"""
    if level_needed == "guest":
        return True
    if level_needed == "adult":
        return level_have == "adult"
    return LEVEL_RANK.get(level_have, 0) >= LEVEL_RANK[level_needed]


# ---------------------------------------------------------------- summaries


def participant_summary(c, p):
    dob = datetime.date.fromisoformat(p["dob"])
    target = p["target_level"]
    return {
        "ref": p["ref"], "first_name": p["first_name"], "last_name": p["last_name"], "dob": p["dob"],
        "age": formspec.age_months(dob) // 12, "is_account_holder": bool(p["is_account_holder"]),
        "level": p["level"], "target_level": target, "needs_review": bool(p["needs_review"]),
        "missing": missing(c, p["id"], target), "status": p["status"],
        "flags": {k: bool(p["f_" + k]) for k in ("allergy", "anaphylaxis", "medical", "dietary", "send", "semh")},
    }


def account_participants(c, account_id):
    return c.execute("SELECT * FROM participants WHERE account_id=? AND status='active' ORDER BY is_account_holder DESC,"
                     " dob", (account_id,)).fetchall()


def contacts(c, account_id):
    return [dict(r) for r in c.execute("SELECT id, full_name, relationship, phone, can_collect, priority"
                                       " FROM emergency_contacts WHERE account_id=? ORDER BY priority", (account_id,))]


def set_collection_password(c, pid, password):
    c.execute("UPDATE participants SET collection_pw_hash=?, collection_pw_set_at=?, updated_at=? WHERE id=?",
              (security.hash_collection_password(password), db.now(), db.now(), pid))
