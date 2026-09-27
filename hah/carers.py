"""Extra carers: people the account holder invites to sign in to the family's
account with their own email and password (up to MAX_CARERS).

A carer can see the family's children and bookings, book, pay, cancel,
report absences and read incident notes. Only the account holder can change
family or child details, consents, collection arrangements, news
preferences, carers, or ask for the family's data or to delete the account
(routes marked auth="holder"). Removing a carer signs them out at once."""
from . import audit, db, family, outbox, ratelimit, security, validate
from .validate import Invalid
from .web import route, site_url

MAX_CARERS = 3
INVITE_DAYS = 7
RESET_MINUTES = 60


def _utc(**kw):
    from .accounts import _utc as u
    return u(**kw)


def _token(c, carer_id, purpose, **kw):
    token = security.new_token()
    c.execute("UPDATE carer_tokens SET used_at=? WHERE carer_id=? AND purpose=? AND used_at IS NULL",
              (db.now(), carer_id, purpose))
    c.execute("INSERT INTO carer_tokens(carer_id, purpose, token_hash, expires_at, created_at) VALUES (?,?,?,?,?)",
              (carer_id, purpose, security.hash_token(token), _utc(**kw), db.now()))
    return token


def _token_row(c, token, purpose):
    return c.execute("SELECT t.id AS token_id, cr.*, a.first_name AS holder_first, a.last_name AS holder_last,"
                     " a.status AS account_status FROM carer_tokens t JOIN carers cr ON cr.id=t.carer_id"
                     " JOIN accounts a ON a.id=cr.account_id WHERE t.token_hash=? AND t.purpose=? AND t.used_at IS NULL"
                     " AND t.expires_at>?", (security.hash_token(token or ""), purpose, db.now())).fetchone()


def carer_json(r):
    return {"ref": r["ref"], "first_name": r["first_name"], "last_name": r["last_name"], "email": r["email"],
            "relationship": r["relationship"], "status": r["status"], "invited_at": r["invited_at"],
            "activated_at": r["activated_at"], "last_login_at": r["last_login_at"]}


def _send_invite(c, h, carer, holder):
    token = _token(c, carer["id"], "invite", days=INVITE_DAYS)
    outbox.email(c, carer["email"], "carer_invite",
                 {"first_name": carer["first_name"], "holder": "%s %s" % (holder["first_name"], holder["last_name"]),
                  "days": INVITE_DAYS}, to_name=carer["first_name"],
                 secret="%s/carer-invite#t=%s" % (site_url(h), token), account_id=holder["id"])


# ---------------------------------------------------------------- the account holder manages carers


@route("GET", "/api/account/carers", auth="holder")
def list_carers(h):
    who = h.principal("account")
    with db.read() as c:
        rows = c.execute("SELECT * FROM carers WHERE account_id=? AND status<>'removed' ORDER BY id", (who["id"],))
        return h.json({"carers": [carer_json(r) for r in rows], "max": MAX_CARERS})


@route("POST", "/api/account/carers", auth="holder")
def invite(h):
    from .accounts import recently_reauthenticated
    who = h.principal("account")
    if not recently_reauthenticated(who):
        return h.json({"error": "Please enter your password to add a carer.", "reauth": True}, 403)
    d = h.json_body() or {}
    errors = {}
    first, last = validate.text(d.get("first_name"), 60), validate.text(d.get("last_name"), 60)
    email = validate.email(d.get("email"))
    if not first:
        errors["first_name"] = "Enter their first name."
    if not last:
        errors["last_name"] = "Enter their last name."
    if not email:
        errors["email"] = "Enter their email address."
    if errors:
        raise Invalid(errors)
    if not ratelimit.hit("acct_email_send", email.lower()):
        raise ValueError("Too many emails to that address — please try later.")
    with db.tx() as c:
        holder = c.execute("SELECT * FROM accounts WHERE id=?", (who["id"],)).fetchone()
        if email.lower() == (holder["email"] or "").lower():
            raise Invalid({"email": "That's your own email address."})
        if c.execute("SELECT COUNT(*) FROM carers WHERE account_id=? AND status<>'removed'", (holder["id"],)).fetchone()[0] \
                >= MAX_CARERS:
            raise ValueError("You can add up to %d carers." % MAX_CARERS)
        if c.execute("SELECT 1 FROM carers WHERE email=? AND account_id=? AND status<>'removed'",
                     (email, holder["id"])).fetchone():
            raise Invalid({"email": "They're already on your list."})
        sent = {"ok": True, "message": "We've emailed %s to explain what happens next." % email}
        if c.execute("SELECT 1 FROM carers WHERE email=? AND status<>'removed'", (email,)).fetchone() or \
                c.execute("SELECT 1 FROM accounts WHERE email=? AND status NOT IN ('anonymised')", (email,)).fetchone():
            # the same answer as for a new address, so this can't be used to find out who has an account here;
            # the address's owner hears why
            outbox.email(c, email, "carer_invite_unavailable",
                         {"first_name": first, "holder": "%s %s" % (holder["first_name"], holder["last_name"])},
                         to_name=first)
            return h.json(sent)
        cid = c.execute("INSERT INTO carers(ref, account_id, email, first_name, last_name, relationship, status,"
                        " invited_at, created_at) VALUES (?,?,?,?,?,?, 'invited', ?,?)",
                        (family.new_ref("C"), holder["id"], email, first, last,
                         validate.text(d.get("relationship"), 60) or None, db.now(), db.now())).lastrowid
        carer = c.execute("SELECT * FROM carers WHERE id=?", (cid,)).fetchone()
        _send_invite(c, h, carer, holder)
        audit.record(c, h, "carer.invited", entity_type="carer", entity_id=cid, account_id=holder["id"],
                     account_actor=holder["id"])
        return h.json(sent)


def _mine(c, who, ref):
    r = c.execute("SELECT * FROM carers WHERE ref=? AND account_id=? AND status<>'removed'", (ref, who["id"])).fetchone()
    if not r:
        raise LookupError
    return r


@route("POST", "/api/account/carers/<ref>/resend", auth="holder")
def resend(h, ref):
    who = h.principal("account")
    with db.tx() as c:
        r = _mine(c, who, ref)
        if r["status"] != "invited":
            raise ValueError("They've already accepted.")
        if not ratelimit.hit("acct_email_send", r["email"].lower()):
            raise ValueError("Too many emails to that address — please try later.")
        _send_invite(c, h, r, c.execute("SELECT * FROM accounts WHERE id=?", (who["id"],)).fetchone())
    return h.json({"ok": True})


@route("POST", "/api/account/carers/<ref>/remove", auth="holder")
def remove(h, ref):
    who = h.principal("account")
    with db.tx() as c:
        r = _mine(c, who, ref)
        remove_carer(c, r)
        audit.record(c, h, "carer.removed", entity_type="carer", entity_id=r["id"], account_id=who["id"],
                     account_actor=who["id"])
        if r["status"] == "active":
            outbox.email(c, r["email"], "carer_removed", {"first_name": r["first_name"],
                                                          "holder": "%s %s" % (who["first_name"], who["last_name"])})
    return h.json({"ok": True})


def remove_carer(c, r):
    c.execute("UPDATE carers SET status='removed', removed_at=?, password_hash=NULL WHERE id=?", (db.now(), r["id"]))
    c.execute("UPDATE carer_tokens SET used_at=? WHERE carer_id=? AND used_at IS NULL", (db.now(), r["id"]))
    c.execute("DELETE FROM account_sessions WHERE carer_id=?", (r["id"],))


def erase_for_account(c, account_id):
    """Account erasure: carers' details go too."""
    for r in c.execute("SELECT * FROM carers WHERE account_id=?", (account_id,)).fetchall():
        remove_carer(c, r)
    c.execute("DELETE FROM carer_tokens WHERE carer_id IN (SELECT id FROM carers WHERE account_id=?)", (account_id,))
    c.execute("UPDATE carers SET email='removed-' || ref || '@invalid', first_name='Removed', last_name='carer',"
              " relationship=NULL WHERE account_id=?", (account_id,))


# ---------------------------------------------------------------- the carer accepts


@route("POST", "/api/account/carer-invite/check")
def invite_check(h):
    if not ratelimit.hit("acct_reset", h.client_ip()):
        return h.json({"error": "Too many attempts — please wait a few minutes and try again."}, 429)
    with db.read() as c:
        r = _token_row(c, (h.json_body() or {}).get("token"), "invite")
    if not r or r["status"] != "invited" or r["account_status"] != "active":
        return h.json({"error": "This invitation has expired or has already been used. Ask them to send a new one."}, 400)
    return h.json({"first_name": r["first_name"], "email": r["email"],
                   "holder": "%s %s" % (r["holder_first"], r["holder_last"])})


@route("POST", "/api/account/carer-invite/accept")
def invite_accept(h):
    from .accounts import start_session
    d = h.json_body() or {}
    if not ratelimit.hit("acct_reset", h.client_ip()):
        return h.json({"error": "Too many attempts — please wait a few minutes and try again."}, 429)
    if not d.get("agree"):
        raise Invalid({"agree": "Please confirm you'll keep the family's information private."})
    with db.tx() as c:
        r = _token_row(c, d.get("token"), "invite")
        if not r or r["status"] != "invited" or r["account_status"] != "active":
            return h.json({"error": "This invitation has expired or has already been used. Ask them to send a new one."}, 400)
        problem = security.password_problem(d.get("password") or "", email=r["email"])
        if problem:
            return h.json({"error": problem, "errors": {"password": problem}}, 422)
        c.execute("UPDATE carer_tokens SET used_at=? WHERE id=?", (db.now(), r["token_id"]))
        c.execute("UPDATE carers SET password_hash=?, status='active', activated_at=? WHERE id=?",
                  (security.hash_password(d["password"]), db.now(), r["id"]))
        audit.record(c, h, "carer.accepted", entity_type="carer", entity_id=r["id"], account_id=r["account_id"],
                     account_actor=r["account_id"])
        holder = c.execute("SELECT email, first_name FROM accounts WHERE id=?", (r["account_id"],)).fetchone()
        if holder["email"]:
            outbox.email(c, holder["email"], "carer_joined", {"first_name": holder["first_name"],
                                                              "carer": "%s %s" % (r["first_name"], r["last_name"])},
                         account_id=r["account_id"])
        cookie, csrf = start_session(c, h, r["account_id"], carer_id=r["id"])
    return h.json({"ok": True, "csrf": csrf}, headers={"Set-Cookie": cookie})


# ---------------------------------------------------------------- forgotten passwords (called from accounts)


def send_reset(c, h, email):
    r = c.execute("SELECT * FROM carers WHERE email=? AND status='active'", (email,)).fetchone()
    if not r:
        return
    token = _token(c, r["id"], "reset_password", minutes=RESET_MINUTES)
    outbox.email(c, email, "account_reset", {"first_name": r["first_name"], "minutes": RESET_MINUTES},
                 secret="%s/reset-password#t=%s" % (site_url(h), token), account_id=r["account_id"])


def reset_password(c, h, token, password):
    """(body, status, headers) if TOKEN is a carer's reset link, else None."""
    from .accounts import start_session
    r = _token_row(c, token, "reset_password")
    if not r:
        return None
    if r["status"] != "active" or r["account_status"] != "active":
        return {"error": "This link has expired or has already been used. Ask for a new one."}, 400, None
    problem = security.password_problem(password, email=r["email"])
    if problem:
        return {"error": problem, "errors": {"password": problem}}, 422, None
    c.execute("UPDATE carer_tokens SET used_at=? WHERE id=?", (db.now(), r["token_id"]))
    c.execute("UPDATE carers SET password_hash=? WHERE id=?", (security.hash_password(password), r["id"]))
    c.execute("DELETE FROM account_sessions WHERE carer_id=?", (r["id"],))
    audit.record(c, h, "carer.password_reset", entity_type="carer", entity_id=r["id"], account_id=r["account_id"],
                 account_actor=r["account_id"])
    cookie, csrf = start_session(c, h, r["account_id"], carer_id=r["id"])
    return {"ok": True, "csrf": csrf}, 200, {"Set-Cookie": cookie}
