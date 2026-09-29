"""Parent and young-adult accounts: registration, email verification,
sign-in, password reset, activation of accounts imported from MagicBooking,
and re-authentication before sensitive changes.

Registration verifies the email address with a 6-digit code *before* any
child details are collected, so a mistyped address never receives a child's
information. Answers never reveal whether an email already has an account.

Until it's verified, each registration for an address is a separate attempt:
its details and password hash are kept with its code (account_tokens.payload)
and the account itself is left alone. Finishing needs a code (so the inbox)
*and* the password of one of the attempts, whose details then apply — so
someone who registers an address that isn't theirs can never choose the
password its owner signs in with."""
import datetime
import json
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
    "acct_login_email": (20, 15 * 60),      # failed sign-ins to one address, from anywhere
    "acct_register_ip": (15, 60 * 60),
    "acct_email_send": (5, 60 * 60),        # emails to one address per hour
    "acct_code": (8, 30 * 60),              # wrong codes per address
    "acct_code_ip": (30, 30 * 60),          # wrong codes from one client, whatever the address
    "acct_reset": (20, 60 * 60),
})
ATTEMPT_HOURS = 24  # an unfinished registration can be picked up again (a new code) for this long
MAX_ATTEMPTS = 5    # the newest attempts whose passwords are checked (each check is deliberately slow)

SLOW_DOWN = "Too many attempts — please wait a few minutes and try again."
SENT = "If that email address can be used, we've sent a message to it. Check your inbox (and spam folder)."
BAD_CODE = ("That code or password didn't work. Use a code from one of our emails (you can ask for a new one) "
            "and the password you chose when you registered.")


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
                        " s.reauth_at, s.carer_id, cr.status AS carer_status, cr.first_name AS carer_first_name,"
                        " cr.last_name AS carer_last_name, cr.email AS carer_email, cr.ref AS carer_ref,"
                        " cr.password_hash AS carer_password_hash, a.* FROM account_sessions s"
                        " JOIN accounts a ON a.id=s.account_id LEFT JOIN carers cr ON cr.id=s.carer_id"
                        " WHERE s.token_hash=?", (security.hash_token(token),)).fetchone()
    if not row or row["status"] != "active" or row["expires_at"] <= now or row["idle_expires_at"] <= now:
        return None
    if row["carer_id"] and row["carer_status"] != "active":
        return None  # removed by the account holder
    last = datetime.datetime.strptime(row["last_seen_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    if (datetime.datetime.now(datetime.timezone.utc) - last).total_seconds() > TOUCH_EVERY and not db.in_tx():
        with db.tx() as c:
            c.execute("UPDATE account_sessions SET last_seen_at=?, idle_expires_at=? WHERE id=?",
                      (now, _utc(days=IDLE_DAYS), row["session_id"]))
    who = dict(row)
    who["csrf"] = row["csrf_token"]
    return who


def start_session(c, h, account_id, carer_id=None):
    """(Set-Cookie value, csrf) for a new signed-in session (as an extra carer if CARER_ID)."""
    token, csrf, now = security.new_token(), security.new_token(24), db.now()
    c.execute("INSERT INTO account_sessions(token_hash, account_id, csrf_token, created_at, last_seen_at,"
              " idle_expires_at, expires_at, reauth_at, ip, user_agent, carer_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (security.hash_token(token), account_id, csrf, now, now, _utc(days=IDLE_DAYS),
               _utc(days=SESSION_DAYS), now, h.client_ip(), (h.headers.get("User-Agent") or "")[:200], carer_id))
    if carer_id:
        c.execute("UPDATE carers SET last_login_at=? WHERE id=?", (now, carer_id))
    else:
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


def _new_attempt(c, account_id, payload):
    """A code for one registration attempt (PAYLOAD: its details and password hash; None for an account
    registered before attempts were kept apart). Other attempts' codes stay valid."""
    while True:
        code = "%06d" % _secrets.randbelow(1_000_000)
        token_hash = security.hash_token("%d:%s" % (account_id, code))
        if not c.execute("SELECT 1 FROM account_tokens WHERE token_hash=?", (token_hash,)).fetchone():
            break
    c.execute("INSERT INTO account_tokens(purpose, token_hash, account_id, payload, expires_at, created_at)"
              " VALUES ('verify_email',?,?,?,?,?)", (token_hash, account_id, json.dumps(payload) if payload else None,
                                                      _utc(minutes=CODE_MINUTES), db.now()))
    return code


def _send_code(c, acct, payload):
    code = _new_attempt(c, acct["id"], payload)
    outbox.email(c, acct["email"], "account_verify", {"first_name": (payload or acct)["first_name"],
                                                      "minutes": CODE_MINUTES}, secret=code, account_id=acct["id"])


def _attempts(c, account_id):
    return c.execute("SELECT * FROM account_tokens WHERE account_id=? AND purpose='verify_email' AND used_at IS NULL"
                     " AND created_at>? ORDER BY id DESC", (account_id, _utc(hours=-ATTEMPT_HOURS))).fetchall()


def _attempt_for(acct, attempts, password):
    """The payload of the registration attempt made with PASSWORD ({} for an older account whose password is
    on the account itself), or None. Raises security.Busy."""
    hashes = []
    for t in attempts:
        payload = json.loads(t["payload"]) if t["payload"] else None
        if payload and payload["password_hash"] not in [x for x, _ in hashes] and len(hashes) < MAX_ATTEMPTS:
            hashes.append((payload["password_hash"], payload))
    if acct["password_hash"]:
        hashes.append((acct["password_hash"], {}))
    if not hashes:
        security.dummy_verify(password)
    for pw_hash, payload in hashes:
        if security.verify_password(password, pw_hash):
            return payload
    return None


def _apply_registration(c, account_id, a):
    """Verified: the details from the attempt that finished become the account's."""
    now = db.now()
    c.execute("UPDATE accounts SET kind=?, password_hash=?, first_name=?, last_name=?, mobile=?, postcode=?,"
              " updated_at=? WHERE id=?", (a["kind"], a["password_hash"], a["first_name"], a["last_name"], a["mobile"],
                                          a["postcode"], now, account_id))
    c.execute("DELETE FROM participants WHERE account_id=?", (account_id,))
    if a["kind"] == "adult":
        c.execute("INSERT INTO participants(ref, account_id, is_account_holder, first_name, last_name, dob,"
                  " target_level, created_at, updated_at) VALUES (?,?,1,?,?,?, 'adult', ?,?)",
                  (family.new_ref("P"), account_id, a["first_name"], a["last_name"], a["dob"], now, now))


def _password(d):
    return d.get("password") if isinstance(d.get("password"), str) else ""


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
    problem = security.password_problem(_password(d), email=email or "")
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
        carer = c.execute("SELECT * FROM carers WHERE email=? AND status<>'removed'", (email,)).fetchone()
        if carer and not existing:
            if _can_email(email):  # already a carer on a family account: they sign in with that
                outbox.email(c, email, "account_exists", {"first_name": carer["first_name"],
                                                          "signin_url": _site(h) + "/login",
                                                          "reset_url": _site(h) + "/forgot-password",
                                                          "activate_url": _site(h) + "/activate"})
            return h.json({"ok": True, "message": SENT})
        if existing and existing["status"] != "pending_verification":
            if _can_email(email):
                outbox.email(c, email, "account_exists", {"first_name": existing["first_name"],
                                                          "signin_url": _site(h) + "/login",
                                                          "reset_url": _site(h) + "/forgot-password",
                                                          "activate_url": _site(h) + "/activate"},
                             account_id=existing["id"])
            return h.json({"ok": True, "message": SENT})
        now = db.now()
        attempt = {"kind": kind, "first_name": you["first_name"], "last_name": you["last_name"], "mobile": you["mobile"],
                   "postcode": you["postcode"], "dob": dob.isoformat() if dob else None, "password_hash": pw_hash}
        # started before but never verified: this is another attempt, and the account is left as it is
        # (the address's owner may be the one who started, and this may not be them)
        acct = existing or c.execute("SELECT * FROM accounts WHERE id=?", (c.execute(
            "INSERT INTO accounts(ref, kind, email, status, first_name, last_name, mobile, postcode, source, created_at,"
            " updated_at) VALUES (?,?,?, 'pending_verification', ?,?,?,?, 'self', ?,?)",
            (family.new_ref("A"), kind, email, you["first_name"], you["last_name"], you["mobile"], you["postcode"],
             now, now)).lastrowid,)).fetchone()
        if _can_email(email):
            _send_code(c, acct, attempt)
    return h.json({"ok": True, "message": SENT})


@route("POST", "/api/account/register/verify")
def register_verify(h):
    """Step 2: the code from the email, with the password chosen in step 1. Signs them in."""
    d = h.json_body() or {}
    email = validate.email(d.get("email")) or ""
    code = "".join(ch for ch in validate.text(d.get("code"), 20) if ch.isdigit())
    ip = h.client_ip()
    if ratelimit.blocked("acct_code", email) or ratelimit.blocked("acct_code_ip", ip):
        return h.json({"error": SLOW_DOWN + " You can ask for a new code."}, 429)
    with db.read() as c:
        acct = c.execute("SELECT * FROM accounts WHERE email=? AND status='pending_verification'",
                         (email,)).fetchone() if email else None
        tok = acct and c.execute(
            "SELECT * FROM account_tokens WHERE account_id=? AND purpose='verify_email' AND used_at IS NULL"
            " AND expires_at>? AND token_hash=?",
            (acct["id"], db.now(), security.hash_token("%d:%s" % (acct["id"], code)))).fetchone()
        attempts = _attempts(c, acct["id"]) if tok else []
    try:
        # any of the codes shows they have the inbox; the password says whose details these are
        attempt = _attempt_for(acct, attempts, _password(d)) if tok else None
    except security.Busy:
        return h.json({"error": "The server is busy — please try again in a moment."}, 429)
    if attempt is None:
        ratelimit.hit("acct_code", email)
        ratelimit.hit("acct_code_ip", ip)
        return h.json({"error": BAD_CODE}, 400)
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM account_tokens t JOIN accounts a ON a.id=t.account_id WHERE t.id=?"
                         " AND t.used_at IS NULL AND a.status='pending_verification'", (tok["id"],)).fetchone():
            return h.json({"error": BAD_CODE}, 400)  # finished in the meantime (another tab)
        c.execute("UPDATE account_tokens SET used_at=? WHERE account_id=? AND purpose='verify_email' AND used_at IS NULL",
                  (db.now(), acct["id"]))
        if attempt:
            _apply_registration(c, acct["id"], attempt)
        c.execute("UPDATE accounts SET status='active', email_verified_at=?, activated_at=?, updated_at=? WHERE id=?",
                  (db.now(), db.now(), db.now(), acct["id"]))
        link_guest_bookings(c, acct["id"], email)
        _audit(c, h, "account.created", acct["id"])
        cookie, csrf = start_session(c, h, acct["id"])
        family.recompute_account(c, acct["id"])
        kind = c.execute("SELECT kind FROM accounts WHERE id=?", (acct["id"],)).fetchone()[0]
    return h.json({"ok": True, "csrf": csrf, "kind": kind}, headers={"Set-Cookie": cookie})


@route("POST", "/api/account/register/resend")
def register_resend(h):
    """A new code for the registration made with this password (so nobody else can use up the emails an
    address may be sent). The answer is the same whatever happens."""
    d = h.json_body() or {}
    email = validate.email(d.get("email")) or ""
    if not ratelimit.hit("acct_register_ip", h.client_ip()):
        return h.json({"error": SLOW_DOWN}, 429)
    with db.read() as c:
        acct = c.execute("SELECT * FROM accounts WHERE email=? AND status='pending_verification'",
                         (email,)).fetchone() if email else None
        attempts = _attempts(c, acct["id"]) if acct else []
    try:
        attempt = _attempt_for(acct, attempts, _password(d)) if acct else None
        if not acct:
            security.dummy_verify(_password(d))  # the same time taken either way
    except security.Busy:
        return h.json({"error": "The server is busy — please try again in a moment."}, 429)
    if attempt is not None:
        with db.tx() as c:
            if _can_email(email):
                _send_code(c, acct, attempt or None)
    return h.json({"ok": True, "message": SENT})


def link_guest_bookings(c, account_id, email):
    """A guest who books one-off events and later registers with the same
    (now verified) email sees those bookings in their account."""
    c.execute("UPDATE guest_contacts SET account_id=? WHERE email=? AND account_id IS NULL", (account_id, email))


# ---------------------------------------------------------------- sign in / out


@route("POST", "/api/account/login")
def login(h):
    d = h.json_body() or {}
    email, password = validate.email(d.get("email")) or "", _password(d)
    ip = h.client_ip()
    # per address too (whether or not it has an account), so guessing one family's password from many
    # places is slowed down as well
    if ratelimit.blocked("acct_login_ip", ip) or ratelimit.blocked("acct_login_pair", email + "|" + ip) or \
            ratelimit.blocked("acct_login_email", email):
        return h.json({"error": SLOW_DOWN}, 429)
    carer, attempts, attempt = None, [], None
    with db.read() as c:
        acct = c.execute("SELECT * FROM accounts WHERE email=? AND status IN ('active','pending_verification')",
                         (email,)).fetchone() if email else None
        if not acct and email:
            carer = c.execute("SELECT cr.* FROM carers cr JOIN accounts a ON a.id=cr.account_id WHERE cr.email=?"
                              " AND cr.status='active' AND a.status='active'", (email,)).fetchone()
        if acct and acct["status"] == "pending_verification":
            attempts = _attempts(c, acct["id"])
    try:
        if carer:
            ok = bool(carer["password_hash"] and security.verify_password(password, carer["password_hash"]))
        elif acct and acct["status"] == "pending_verification":
            attempt = _attempt_for(acct, attempts, password)
            ok = attempt is not None
        else:
            ok = bool(acct and acct["password_hash"] and security.verify_password(password, acct["password_hash"]))
            if not acct or not acct["password_hash"]:
                security.dummy_verify(password)
    except security.Busy:
        return h.json({"error": "The server is busy — please try again in a moment."}, 429)
    if ok and carer:
        with db.tx() as c:
            if security.needs_rehash(carer["password_hash"]):
                c.execute("UPDATE carers SET password_hash=? WHERE id=?", (security.hash_password(password), carer["id"]))
            cookie, csrf = start_session(c, h, carer["account_id"], carer_id=carer["id"])
            audit.record(c, h, "carer.login", entity_type="carer", entity_id=carer["id"],
                         account_id=carer["account_id"], account_actor=carer["account_id"])
        return h.json({"ok": True, "csrf": csrf}, headers={"Set-Cookie": cookie})
    if not ok:
        ratelimit.hit("acct_login_ip", ip)
        ratelimit.hit("acct_login_pair", email + "|" + ip)
        ratelimit.hit("acct_login_email", email)
        return h.json({"error": "That email and password don't match. Coming from our old booking system? "
                                "Use \u201cActivate your account\u201d instead."}, 401)
    with db.tx() as c:
        if acct["status"] == "pending_verification":
            if _can_email(email):
                _send_code(c, acct, attempt or None)
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
    if not security.verify_password(d.get("password") or "",
                                    who["carer_password_hash"] if who.get("carer_id") else who["password_hash"]):
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
        if not acct and email and _can_email(email):
            from . import carers
            carers.send_reset(c, h, email)
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
        if not row:
            from . import carers
            done = carers.reset_password(c, h, d.get("token"), d.get("password") or "")
            if done:  # (body, status, headers)
                return h.json(*done)
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
    return h.json({"first_name": row["first_name"], "check": "dob" if has_child else ("postcode" if has_postcode else "contact")})


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
        if not dobs and not postcode:
            # nothing to check it's really them (the link may have been forwarded): staff set it up with them
            intray.add(c, "activation_problem", "Imported account needs activating with the family (no child or"
                       " postcode to check online)", account_id=row["account_id"], perm="people.edit")
            return h.json({"error": "We can't set up this account online, as we don't have the details to check "
                                    "it's you. Please call us on 07932 772905 and we'll do it with you."}, 400)
        if dobs:
            ok = (validate.date(d.get("child_dob")) or "") and validate.date(d.get("child_dob")).isoformat() in dobs
        else:
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
