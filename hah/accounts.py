"""Parent and young-adult accounts: registration, email verification,
sign-in, password reset, activation of accounts imported from MagicBooking,
and re-authentication before sensitive changes.

Registration verifies the email address with a 6-digit code *before* any
child details are collected, so a mistyped address never receives a child's
information. Answers never reveal whether an email already has an account."""
import datetime
import secrets as _secrets

from . import audit, db, family, formspec, intray, outbox, ratelimit, security, validate
from .validate import Invalid
from .web import authenticator, route, site_url

COOKIE = "hah_acct"
SESSION_DAYS = 30
IDLE_DAYS = 7
REAUTH_MINUTES = 10
CODE_MINUTES = 30
RESET_MINUTES = 60
ACTIVATE_DAYS = 30
TOUCH_EVERY = 60

ratelimit.LIMITS.update({
    "acct_login_ip": (40, 15 * 60),
    "acct_login_pair": (8, 15 * 60),
    "acct_register_ip": (15, 60 * 60),
    "acct_email_send": (5, 60 * 60),        # emails to one address per hour
    "acct_code": (8, 30 * 60),              # wrong codes per address
    "acct_reset": (20, 60 * 60),
})

SLOW_DOWN = "Too many attempts — please wait a few minutes and try again."
SENT = "If that email address can be used, we've sent a message to it. Check your inbox (and spam folder)."


def _utc(**kw):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _site(h):
    return site_url(h)


# ---------------------------------------------------------------- sessions


@authenticator("account")
def current_account(h):
    token = h.cookie(COOKIE)
    if not token:
        return None
    now = db.now()
    with db.read() as c:
        row = c.execute("SELECT s.id AS session_id, s.csrf_token, s.last_seen_at, s.idle_expires_at, s.expires_at,"
                        " s.reauth_at, a.* FROM account_sessions s JOIN accounts a ON a.id=s.account_id"
                        " WHERE s.token_hash=?", (security.hash_token(token),)).fetchone()
    if not row or row["status"] != "active" or row["expires_at"] <= now or row["idle_expires_at"] <= now:
        return None
    last = datetime.datetime.strptime(row["last_seen_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    if (datetime.datetime.now(datetime.timezone.utc) - last).total_seconds() > TOUCH_EVERY:
        with db.tx() as c:
            c.execute("UPDATE account_sessions SET last_seen_at=?, idle_expires_at=? WHERE id=?",
                      (now, _utc(days=IDLE_DAYS), row["session_id"]))
    who = dict(row)
    who["csrf"] = row["csrf_token"]
    return who


def start_session(c, h, account_id):
    """(Set-Cookie value, csrf) for a new signed-in session."""
    token, csrf, now = security.new_token(), security.new_token(24), db.now()
    c.execute("INSERT INTO account_sessions(token_hash, account_id, csrf_token, created_at, last_seen_at,"
              " idle_expires_at, expires_at, reauth_at, ip, user_agent) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (security.hash_token(token), account_id, csrf, now, now, _utc(days=IDLE_DAYS),
               _utc(days=SESSION_DAYS), now, h.client_ip(), (h.headers.get("User-Agent") or "")[:200]))
    c.execute("UPDATE accounts SET last_login_at=? WHERE id=?", (now, account_id))
    return "%s=%s; %s; Max-Age=%d" % (COOKIE, token, h.cookie_attrs("Lax"), SESSION_DAYS * 86400), csrf


def clear_cookie(h):
    return "%s=; %s; Max-Age=0" % (COOKIE, h.cookie_attrs("Lax"))


def recently_reauthenticated(who):
    return bool(who.get("reauth_at")) and who["reauth_at"] >= _utc(minutes=-REAUTH_MINUTES)


def _audit(c, h, action, account_id, details=None):
    audit.record(c, h, action, entity_type="account", entity_id=account_id, account_id=account_id,
                 account_actor=account_id, details=details)


# ---------------------------------------------------------------- one-time codes and links


def _new_code(c, account_id, purpose, minutes):
    code = "%06d" % _secrets.randbelow(1_000_000)
    c.execute("UPDATE account_tokens SET used_at=? WHERE account_id=? AND purpose=? AND used_at IS NULL",
              (db.now(), account_id, purpose))
    c.execute("INSERT INTO account_tokens(purpose, token_hash, account_id, expires_at, created_at) VALUES (?,?,?,?,?)",
              (purpose, security.hash_token("%d:%s" % (account_id, code)), account_id, _utc(minutes=minutes), db.now()))
    return code


def _new_link_token(c, account_id, purpose, **kw):
    token = security.new_token()
    c.execute("UPDATE account_tokens SET used_at=? WHERE account_id=? AND purpose=? AND used_at IS NULL",
              (db.now(), account_id, purpose))
    c.execute("INSERT INTO account_tokens(purpose, token_hash, account_id, expires_at, created_at) VALUES (?,?,?,?,?)",
              (purpose, security.hash_token(token), account_id, _utc(**kw), db.now()))
    return token


def _can_email(address):
    return ratelimit.hit("acct_email_send", address.lower())


def send_activation(c, h, account, *, reminder=False):
    """Email an imported family their "activate your account" link."""
    token = _new_link_token(c, account["id"], "activate", days=ACTIVATE_DAYS)
    outbox.email(c, account["email"], "account_activate", {"first_name": account["first_name"],
                                                           "days": ACTIVATE_DAYS, "reminder": reminder},
                 to_name=account["first_name"], secret="%s/activate#t=%s" % (_site(h), token),
                 account_id=account["id"])
    c.execute("UPDATE accounts SET updated_at=? WHERE id=?", (db.now(), account["id"]))


# ---------------------------------------------------------------- registration


def _clean_registration(d):
    kind = d.get("kind") if d.get("kind") in ("family", "adult") else "family"
    errors = {}
    try:
        you = formspec.clean("you", d, "adult" if kind == "adult" else "short")
    except Invalid as e:
        errors.update(e.errors)
        you = {}
    email = validate.email(d.get("email"))
    if not email:
        errors["email"] = "Enter a valid email address."
    problem = security.password_problem(d.get("password") or "", email=email or "")
    if problem:
        errors["password"] = problem
    dob = None
    if kind == "adult":
        dob = validate.date(d.get("dob"))
        if not dob:
            errors["dob"] = "Enter your date of birth."
        elif formspec.age_months(dob) < 18 * 12:
            errors["dob"] = "You need to be 18 or over to book for yourself — a parent or carer can register you instead."
    if errors:
        raise Invalid(errors)
    return kind, email, you, dob


@route("POST", "/api/account/register")
def register(h):
    """Step 1: your details and a password. We email a 6-digit code."""
    d = h.json_body() or {}
    if d.get("website"):
        return h.json({"ok": True, "message": SENT})
    if not ratelimit.hit("acct_register_ip", h.client_ip()):
        return h.json({"error": SLOW_DOWN}, 429)
    try:
        kind, email, you, dob = _clean_registration(d)
    except Invalid as e:
        return h.json({"error": "Please check the highlighted boxes.", "errors": e.errors}, 422)
    try:
        pw_hash = security.hash_password(d["password"])
    except security.Busy:
        return h.json({"error": "The server is busy — please try again in a moment."}, 429)
    with db.tx() as c:
        existing = c.execute("SELECT * FROM accounts WHERE email=? AND status <> 'anonymised'", (email,)).fetchone()
        if existing and existing["status"] != "pending_verification":
            if _can_email(email):
                outbox.email(c, email, "account_exists", {"first_name": existing["first_name"],
                                                          "signin_url": _site(h) + "/login",
                                                          "reset_url": _site(h) + "/forgot-password",
                                                          "activate_url": _site(h) + "/activate"},
                             account_id=existing["id"])
            return h.json({"ok": True, "message": SENT})
        now = db.now()
        if existing:  # started before but never verified: start again with these details
            account_id = existing["id"]
            c.execute("UPDATE accounts SET kind=?, password_hash=?, first_name=?, last_name=?, mobile=?, postcode=?,"
                      " updated_at=? WHERE id=?",
                      (kind, pw_hash, you["first_name"], you["last_name"], you["mobile"], you["postcode"], now, account_id))
            c.execute("DELETE FROM participants WHERE account_id=?", (account_id,))
        else:
            account_id = c.execute(
                "INSERT INTO accounts(ref, kind, email, password_hash, status, first_name, last_name, mobile, postcode,"
                " source, created_at, updated_at) VALUES (?,?,?,?, 'pending_verification', ?,?,?,?, 'self', ?,?)",
                (family.new_ref("A"), kind, email, pw_hash, you["first_name"], you["last_name"], you["mobile"],
                 you["postcode"], now, now)).lastrowid
        if kind == "adult":
            c.execute("INSERT INTO participants(ref, account_id, is_account_holder, first_name, last_name, dob,"
                      " target_level, created_at, updated_at) VALUES (?,?,1,?,?,?, 'adult', ?,?)",
                      (family.new_ref("P"), account_id, you["first_name"], you["last_name"], dob.isoformat(), now, now))
        if _can_email(email):
            code = _new_code(c, account_id, "verify_email", CODE_MINUTES)
            outbox.email(c, email, "account_verify", {"first_name": you["first_name"], "minutes": CODE_MINUTES},
                         secret=code, account_id=account_id)
    return h.json({"ok": True, "message": SENT})


@route("POST", "/api/account/register/verify")
def register_verify(h):
    """Step 2: the code from the email. Signs them in."""
    d = h.json_body() or {}
    email = validate.email(d.get("email")) or ""
    code = "".join(ch for ch in str(d.get("code") or "") if ch.isdigit())
    if ratelimit.blocked("acct_code", email):
        return h.json({"error": SLOW_DOWN + " You can ask for a new code."}, 429)
    with db.tx() as c:
        acct = c.execute("SELECT * FROM accounts WHERE email=? AND status='pending_verification'", (email,)).fetchone()
        tok = acct and c.execute(
            "SELECT * FROM account_tokens WHERE account_id=? AND purpose='verify_email' AND used_at IS NULL"
            " AND expires_at>? AND token_hash=?",
            (acct["id"], db.now(), security.hash_token("%d:%s" % (acct["id"], code)))).fetchone()
        if not tok:
            ratelimit.hit("acct_code", email)
            return h.json({"error": "That code didn't work. Check the latest email we sent, or ask for a new code."}, 400)
        c.execute("UPDATE account_tokens SET used_at=? WHERE id=?", (db.now(), tok["id"]))
        c.execute("UPDATE accounts SET status='active', email_verified_at=?, activated_at=?, updated_at=? WHERE id=?",
                  (db.now(), db.now(), db.now(), acct["id"]))
        link_guest_bookings(c, acct["id"], email)
        _audit(c, h, "account.created", acct["id"])
        cookie, csrf = start_session(c, h, acct["id"])
        family.recompute_account(c, acct["id"])
    return h.json({"ok": True, "csrf": csrf, "kind": acct["kind"]}, headers={"Set-Cookie": cookie})


@route("POST", "/api/account/register/resend")
def register_resend(h):
    d = h.json_body() or {}
    email = validate.email(d.get("email")) or ""
    with db.tx() as c:
        acct = c.execute("SELECT * FROM accounts WHERE email=? AND status='pending_verification'", (email,)).fetchone()
        if acct and _can_email(email):
            code = _new_code(c, acct["id"], "verify_email", CODE_MINUTES)
            outbox.email(c, email, "account_verify", {"first_name": acct["first_name"], "minutes": CODE_MINUTES},
                         secret=code, account_id=acct["id"])
    return h.json({"ok": True, "message": SENT})


def link_guest_bookings(c, account_id, email):
    """A guest who books one-off events and later registers with the same
    (now verified) email sees those bookings in their account."""
    c.execute("UPDATE guest_contacts SET account_id=? WHERE email=? AND account_id IS NULL", (account_id, email))


# ---------------------------------------------------------------- sign in / out


@route("POST", "/api/account/login")
def login(h):
    d = h.json_body() or {}
    email, password = validate.email(d.get("email")) or "", d.get("password") or ""
    ip = h.client_ip()
    if ratelimit.blocked("acct_login_ip", ip) or ratelimit.blocked("acct_login_pair", email + "|" + ip):
        return h.json({"error": SLOW_DOWN}, 429)
    with db.read() as c:
        acct = c.execute("SELECT * FROM accounts WHERE email=? AND status IN ('active','pending_verification')",
                         (email,)).fetchone() if email else None
    try:
        ok = bool(acct and acct["password_hash"] and security.verify_password(password, acct["password_hash"]))
        if not acct or not acct["password_hash"]:
            security.dummy_verify(password)
    except security.Busy:
        return h.json({"error": "The server is busy — please try again in a moment."}, 429)
    if not ok:
        ratelimit.hit("acct_login_ip", ip)
        ratelimit.hit("acct_login_pair", email + "|" + ip)
        return h.json({"error": "That email and password don't match. Coming from our old booking system? "
                                "Use \u201cActivate your account\u201d instead."}, 401)
    with db.tx() as c:
        if acct["status"] == "pending_verification":
            if _can_email(email):
                code = _new_code(c, acct["id"], "verify_email", CODE_MINUTES)
                outbox.email(c, email, "account_verify", {"first_name": acct["first_name"], "minutes": CODE_MINUTES},
                             secret=code, account_id=acct["id"])
            return h.json({"ok": True, "next": "verify"})
        if security.needs_rehash(acct["password_hash"]):
            c.execute("UPDATE accounts SET password_hash=? WHERE id=?", (security.hash_password(password), acct["id"]))
        cookie, csrf = start_session(c, h, acct["id"])
    return h.json({"ok": True, "csrf": csrf}, headers={"Set-Cookie": cookie})


@route("POST", "/api/account/logout", auth="account")
def logout(h):
    with db.tx() as c:
        c.execute("DELETE FROM account_sessions WHERE token_hash=?", (security.hash_token(h.cookie(COOKIE)),))
    return h.json({"ok": True}, headers={"Set-Cookie": clear_cookie(h)})


@route("POST", "/api/account/reauth", auth="account")
def reauth(h):
    """Re-enter your password before changing who can collect a child, etc."""
    who = h.principal("account")
    d = h.json_body() or {}
    if ratelimit.blocked("acct_login_pair", "reauth|%d" % who["id"]):
        return h.json({"error": SLOW_DOWN}, 429)
    if not security.verify_password(d.get("password") or "", who["password_hash"]):
        ratelimit.hit("acct_login_pair", "reauth|%d" % who["id"])
        return h.json({"error": "That password isn't right."}, 400)
    with db.tx() as c:
        c.execute("UPDATE account_sessions SET reauth_at=? WHERE id=?", (db.now(), who["session_id"]))
    return h.json({"ok": True})


# ---------------------------------------------------------------- forgotten password / activation


@route("POST", "/api/account/password/forgot")
def forgot(h):
    d = h.json_body() or {}
    email = validate.email(d.get("email")) or ""
    if not ratelimit.hit("acct_reset", h.client_ip()):
        return h.json({"error": SLOW_DOWN}, 429)
    with db.tx() as c:
        acct = c.execute("SELECT * FROM accounts WHERE email=? AND status IN ('active','pending_activation')",
                         (email,)).fetchone() if email else None
        if acct and _can_email(email):
            if acct["status"] == "pending_activation":
                send_activation(c, h, acct)
            else:
                token = _new_link_token(c, acct["id"], "reset_password", minutes=RESET_MINUTES)
                outbox.email(c, email, "account_reset", {"first_name": acct["first_name"], "minutes": RESET_MINUTES},
                             secret="%s/reset-password#t=%s" % (_site(h), token), account_id=acct["id"])
    return h.json({"ok": True, "message": SENT})


def _token_row(c, token, purpose):
    return c.execute("SELECT t.*, a.status, a.email, a.first_name FROM account_tokens t JOIN accounts a"
                     " ON a.id=t.account_id WHERE t.token_hash=? AND t.purpose=? AND t.used_at IS NULL AND t.expires_at>?",
                     (security.hash_token(token or ""), purpose, db.now())).fetchone()


@route("POST", "/api/account/password/reset")
def reset(h):
    d = h.json_body() or {}
    if not ratelimit.hit("acct_reset", h.client_ip()):
        return h.json({"error": SLOW_DOWN}, 429)
    with db.tx() as c:
        row = _token_row(c, d.get("token"), "reset_password")
        if not row or row["status"] != "active":
            return h.json({"error": "This link has expired or has already been used. Ask for a new one."}, 400)
        problem = security.password_problem(d.get("password") or "", email=row["email"])
        if problem:
            return h.json({"error": problem, "errors": {"password": problem}}, 422)
        c.execute("UPDATE account_tokens SET used_at=? WHERE id=?", (db.now(), row["id"]))
        c.execute("UPDATE accounts SET password_hash=?, updated_at=? WHERE id=?",
                  (security.hash_password(d["password"]), db.now(), row["account_id"]))
        c.execute("DELETE FROM account_sessions WHERE account_id=?", (row["account_id"],))
        _audit(c, h, "account.password_reset", row["account_id"])
        cookie, csrf = start_session(c, h, row["account_id"])
    return h.json({"ok": True, "csrf": csrf}, headers={"Set-Cookie": cookie})


@route("POST", "/api/account/activate/check")
def activate_check(h):
    d = h.json_body() or {}
    if not ratelimit.hit("acct_reset", h.client_ip()):
        return h.json({"error": SLOW_DOWN}, 429)
    with db.read() as c:
        row = _token_row(c, d.get("token"), "activate")
        if not row or row["status"] != "pending_activation":
            return h.json({"error": "This link has expired or has already been used. Enter your email below for a new one."}, 400)
        has_child = c.execute("SELECT 1 FROM participants WHERE account_id=? AND is_account_holder=0",
                              (row["account_id"],)).fetchone()
        has_postcode = c.execute("SELECT postcode FROM accounts WHERE id=?", (row["account_id"],)).fetchone()[0]
    return h.json({"first_name": row["first_name"], "check": "dob" if has_child else ("postcode" if has_postcode else "none")})


@route("POST", "/api/account/activate")
def activate(h):
    """Imported families choose a password. To make a forwarded or
    mistyped activation email useless to anyone else, they also confirm a
    child's date of birth (or their postcode)."""
    d = h.json_body() or {}
    if not ratelimit.hit("acct_reset", h.client_ip()):
        return h.json({"error": SLOW_DOWN}, 429)
    with db.tx() as c:
        row = _token_row(c, d.get("token"), "activate")
        if not row or row["status"] != "pending_activation":
            return h.json({"error": "This link has expired or has already been used. Enter your email below for a new one."}, 400)
        dobs = {r[0] for r in c.execute("SELECT dob FROM participants WHERE account_id=? AND is_account_holder=0",
                                        (row["account_id"],))}
        postcode = c.execute("SELECT postcode FROM accounts WHERE id=?", (row["account_id"],)).fetchone()[0]
        ok = True
        if dobs:
            ok = (validate.date(d.get("child_dob")) or "") and validate.date(d.get("child_dob")).isoformat() in dobs
        elif postcode:
            ok = validate.postcode(d.get("postcode")) == postcode
        if not ok:
            attempts = row["attempts"] + 1
            c.execute("UPDATE account_tokens SET attempts=?, used_at=CASE WHEN ?>=5 THEN ? ELSE NULL END WHERE id=?",
                      (attempts, attempts, db.now(), row["id"]))
            if attempts >= 5:
                intray.add(c, "activation_problem", "Activation link locked after 5 wrong answers",
                           account_id=row["account_id"], perm="people.edit")
            return h.json({"error": "That doesn't match what we have. Check and try again, or call us."}, 400)
        problem = security.password_problem(d.get("password") or "", email=row["email"])
        if problem:
            return h.json({"error": problem, "errors": {"password": problem}}, 422)
        c.execute("UPDATE account_tokens SET used_at=? WHERE id=?", (db.now(), row["id"]))
        c.execute("UPDATE accounts SET password_hash=?, status='active', email_verified_at=?, activated_at=?,"
                  " updated_at=? WHERE id=?",
                  (security.hash_password(d["password"]), db.now(), db.now(), db.now(), row["account_id"]))
        link_guest_bookings(c, row["account_id"], row["email"])
        _audit(c, h, "account.activated", row["account_id"])
        cookie, csrf = start_session(c, h, row["account_id"])
    return h.json({"ok": True, "csrf": csrf}, headers={"Set-Cookie": cookie})


@route("POST", "/api/account/activate/resend")
def activate_resend(h):
    return forgot(h)
