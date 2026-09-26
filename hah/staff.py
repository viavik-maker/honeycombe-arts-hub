"""Staff accounts: sign-in with two-factor codes, sessions, invites, roles and
the audit log viewer.

Sign-in is two steps: email + password, then a 6-digit code from an
authenticator app (or a one-time recovery code). Staff without 2FA are
walked through setting it up at their first sign-in. Sessions last at most
STAFF_SESSION_HOURS and end after STAFF_IDLE_MINUTES of inactivity; only a
hash of the session token is stored."""
import datetime
import json

from . import audit, auth, config, db, mail, outbox, permissions, ratelimit, security
from .web import authenticator, route, site_url

COOKIE = "hah_staff"
INVITE_HOURS = 72
TOUCH_EVERY = 60  # seconds between last-seen updates (keeps reads cheap)

ratelimit.LIMITS.update({
    "staff_login_ip": (30, 15 * 60),        # any emails, from one connection
    "staff_login_pair": (5, 15 * 60),       # one email, from one connection
    "staff_totp": (6, 15 * 60),             # wrong codes per session
    "staff_setup": (10, 60 * 60),
    "staff_invite_check": (30, 60 * 60),
})

SLOW_DOWN = "Too many attempts — please wait 15 minutes and try again."


def _utc(minutes=0, hours=0):
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes, hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def mfa_required():
    return bool(db.get_setting("staff_mfa_required", True))


def _roles(c, staff_id):
    return sorted(r["role"] for r in c.execute("SELECT role FROM staff_roles WHERE staff_id=?", (staff_id,)))


# ---------------------------------------------------------------- sessions


@authenticator("staff")
def current_staff(h):
    token = h.cookie(COOKIE)
    if not token:
        return None
    now = db.now()
    with db.read() as c:
        row = c.execute(
            "SELECT s.id AS session_id, s.csrf_token, s.mfa_passed, s.last_seen_at, s.idle_expires_at,"
            " s.expires_at, u.id, u.email, u.name, u.status, u.totp_enabled"
            " FROM staff_sessions s JOIN staff_users u ON u.id = s.staff_id WHERE s.token_hash=?",
            (security.hash_token(token),)).fetchone()
        if not row or row["status"] != "active" or row["expires_at"] <= now or row["idle_expires_at"] <= now:
            return None
        roles = _roles(c, row["id"])
    last = datetime.datetime.strptime(row["last_seen_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    if (datetime.datetime.now(datetime.timezone.utc) - last).total_seconds() > TOUCH_EVERY:
        with db.tx() as c:
            c.execute("UPDATE staff_sessions SET last_seen_at=?, idle_expires_at=? WHERE id=?",
                      (now, _utc(minutes=config.STAFF_IDLE_MINUTES), row["session_id"]))
    return {
        "id": row["id"], "email": row["email"], "name": row["name"], "roles": roles,
        "perms": permissions.perms_for(roles), "csrf": row["csrf_token"],
        "mfa_passed": bool(row["mfa_passed"]), "totp_enabled": bool(row["totp_enabled"]),
        "session_id": row["session_id"],
    }


def _start_session(c, h, staff_id, mfa_passed):
    """Create a session (inside a tx). Returns (Set-Cookie value, CSRF token)."""
    token, csrf, now = security.new_token(), security.new_token(24), db.now()
    c.execute("INSERT INTO staff_sessions(token_hash, staff_id, csrf_token, mfa_passed, created_at, last_seen_at,"
              " idle_expires_at, expires_at, ip, user_agent) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (security.hash_token(token), staff_id, csrf, 1 if mfa_passed else 0, now, now,
               _utc(minutes=config.STAFF_IDLE_MINUTES), _utc(hours=config.STAFF_SESSION_HOURS),
               h.client_ip(), (h.headers.get("User-Agent") or "")[:200]))
    return ("%s=%s; %s; Max-Age=%d" % (COOKIE, token, h.cookie_attrs("Strict"), config.STAFF_SESSION_HOURS * 3600),
            csrf)


def _clear_cookie(h):
    return "%s=; %s; Max-Age=0" % (COOKIE, h.cookie_attrs("Strict"))


def _passed_mfa(c, h, me):
    """2FA done: replace the session with a fresh, fully signed-in one."""
    c.execute("DELETE FROM staff_sessions WHERE id=?", (me["session_id"],))
    return _start_session(c, h, me["id"], True)


def _after_password(c, h, user):
    """The next step once the password is right, plus (cookie, csrf)."""
    if user["totp_enabled"]:
        return "totp", _start_session(c, h, user["id"], False)
    if mfa_required():
        return "enrol", _start_session(c, h, user["id"], False)
    return "done", _start_session(c, h, user["id"], True)


# ---------------------------------------------------------------- first owner


@route("GET", "/api/staff/setup")
def setup_status(h):
    with db.read() as c:
        has_staff = c.execute("SELECT 1 FROM staff_users LIMIT 1").fetchone() is not None
    return h.json({"needed": not has_staff, "possible": auth.team_password_available()})


@route("POST", "/api/staff/setup")
def setup_owner(h):
    """Create the first owner account. Needs the current team password."""
    d = h.json_body() or {}
    ip = h.client_ip()
    if ratelimit.blocked("staff_setup", ip):
        return h.json({"error": SLOW_DOWN}, 429)
    with db.read() as c:
        if c.execute("SELECT 1 FROM staff_users LIMIT 1").fetchone():
            return h.json({"error": "The site is already set up — sign in instead."}, 409)
    if not auth.team_password_ok(d.get("team_password") or ""):
        ratelimit.hit("staff_setup", ip)
        return h.json({"error": "That isn't the current team password."}, 403)
    name, email = (d.get("name") or "").strip()[:100], (d.get("email") or "").strip().lower()[:200]
    password = d.get("password") or ""
    if not name or "@" not in email:
        return h.json({"error": "Please give your name and email address."}, 400)
    problem = security.password_problem(password, email=email)
    if problem:
        return h.json({"error": problem}, 400)
    with db.tx() as c:
        if c.execute("SELECT 1 FROM staff_users LIMIT 1").fetchone():
            return h.json({"error": "The site is already set up — sign in instead."}, 409)
        cur = c.execute("INSERT INTO staff_users(email, name, password_hash, password_changed_at, status, created_at)"
                        " VALUES (?,?,?,?, 'active', ?)",
                        (email, name, security.hash_password(password), db.now(), db.now()))
        c.execute("INSERT INTO staff_roles VALUES (?, 'owner')", (cur.lastrowid,))
        user = {"id": cur.lastrowid, "name": name, "totp_enabled": 0}
        audit.record(c, h, "staff.owner_created", entity_type="staff", entity_id=cur.lastrowid, actor=user)
        step, (cookie, csrf) = _after_password(c, h, user)
    auth.retire_team_password()
    return h.json({"ok": True, "step": step, "csrf": csrf}, headers={"Set-Cookie": cookie})


# ---------------------------------------------------------------- sign in


@route("POST", "/api/staff/login")
def login(h):
    d = h.json_body() or {}
    email, password = (d.get("email") or "").strip().lower()[:200], d.get("password") or ""
    ip = h.client_ip()
    if ratelimit.blocked("staff_login_ip", ip) or ratelimit.blocked("staff_login_pair", email + "|" + ip):
        return h.json({"error": SLOW_DOWN}, 429)
    with db.read() as c:
        user = c.execute("SELECT * FROM staff_users WHERE email=?", (email,)).fetchone()
    try:
        ok = user is not None and user["status"] == "active" and security.verify_password(password, user["password_hash"])
        if user is None:
            security.dummy_verify(password)
    except security.Busy:
        return h.json({"error": "The server is busy — please try again in a moment."}, 429)
    if not ok:
        ratelimit.hit("staff_login_ip", ip)
        ratelimit.hit("staff_login_pair", email + "|" + ip)
        with db.tx() as c:
            audit.record(c, h, "staff.login_failed", entity_type="staff",
                         entity_id=user["id"] if user else None, actor={})
        return h.json({"error": "That email and password don't match an active staff account."}, 401)
    with db.tx() as c:
        if security.needs_rehash(user["password_hash"]):
            c.execute("UPDATE staff_users SET password_hash=? WHERE id=?",
                      (security.hash_password(password), user["id"]))
        step, (cookie, csrf) = _after_password(c, h, user)
        if step == "done":
            c.execute("UPDATE staff_users SET last_login_at=? WHERE id=?", (db.now(), user["id"]))
            audit.record(c, h, "staff.login", entity_type="staff", entity_id=user["id"], actor=user)
    return h.json({"ok": True, "step": step, "csrf": csrf}, headers={"Set-Cookie": cookie})


@route("POST", "/api/staff/totp/verify", auth="staff", mfa=False)
def totp_verify(h):
    me = h.staff()
    d = h.json_body() or {}
    if ratelimit.blocked("staff_totp", me["session_id"]):
        with db.tx() as c:
            c.execute("DELETE FROM staff_sessions WHERE id=?", (me["session_id"],))
        return h.json({"error": SLOW_DOWN + " You'll need to sign in again."}, 429,
                      headers={"Set-Cookie": _clear_cookie(h)})
    code = (d.get("code") or "").strip()
    with db.tx() as c:
        user = c.execute("SELECT * FROM staff_users WHERE id=?", (me["id"],)).fetchone()
        step = security.verify_totp(user["totp_secret"], code, user["totp_last_step"]) if user["totp_enabled"] else None
        used_recovery = False
        if step is None and user["totp_enabled"]:
            hashes = json.loads(user["recovery_codes"] or "[]")
            hc = security.hash_token(code.lower())
            if hc in hashes:
                hashes.remove(hc)
                used_recovery = True
                c.execute("UPDATE staff_users SET recovery_codes=? WHERE id=?", (json.dumps(hashes), me["id"]))
        if step is None and not used_recovery:
            ratelimit.hit("staff_totp", me["session_id"])
            audit.record(c, h, "staff.totp_failed", entity_type="staff", entity_id=me["id"], actor=me)
            return h.json({"error": "That code didn't work — check your authenticator app and try again."}, 401)
        if step is not None:
            c.execute("UPDATE staff_users SET totp_last_step=? WHERE id=?", (step, me["id"]))
        c.execute("UPDATE staff_users SET last_login_at=? WHERE id=?", (db.now(), me["id"]))
        audit.record(c, h, "staff.login", entity_type="staff", entity_id=me["id"], actor=me,
                     details={"recovery_code": True} if used_recovery else None)
        cookie, csrf = _passed_mfa(c, h, me)
    return h.json({"ok": True, "step": "done", "csrf": csrf,
                   "recovery_codes_left": None if not used_recovery else len(hashes)},
                  headers={"Set-Cookie": cookie})


@route("GET", "/api/staff/totp/enrol", auth="staff", mfa=False)
def totp_enrol_start(h):
    me = h.staff()
    if me["totp_enabled"]:
        return h.json({"error": "Two-factor sign-in is already set up."}, 409)
    secret = security.new_totp_secret()
    with db.tx() as c:
        c.execute("UPDATE staff_users SET totp_secret=?, totp_last_step=NULL WHERE id=? AND totp_enabled=0",
                  (secret, me["id"]))
    return h.json({"secret": secret, "uri": security.totp_uri(secret, me["email"])})


@route("POST", "/api/staff/totp/enrol", auth="staff", mfa=False)
def totp_enrol_finish(h):
    me = h.staff()
    d = h.json_body() or {}
    if me["totp_enabled"]:
        return h.json({"error": "Two-factor sign-in is already set up."}, 409)
    if ratelimit.blocked("staff_totp", me["session_id"]):
        return h.json({"error": SLOW_DOWN}, 429)
    with db.tx() as c:
        user = c.execute("SELECT totp_secret FROM staff_users WHERE id=?", (me["id"],)).fetchone()
        step = security.verify_totp(user["totp_secret"], d.get("code"))
        if step is None:
            ratelimit.hit("staff_totp", me["session_id"])
            return h.json({"error": "That code didn't match. Check the time on your phone is right and try again."}, 400)
        codes, hashes = security.new_recovery_codes()
        c.execute("UPDATE staff_users SET totp_enabled=1, totp_last_step=?, recovery_codes=?, last_login_at=? WHERE id=?",
                  (step, json.dumps(hashes), db.now(), me["id"]))
        audit.record(c, h, "staff.2fa_enabled", entity_type="staff", entity_id=me["id"], actor=me)
        cookie, csrf = _passed_mfa(c, h, me)
    return h.json({"ok": True, "step": "done", "csrf": csrf, "recovery_codes": codes},
                  headers={"Set-Cookie": cookie})


@route("POST", "/api/staff/logout", auth="staff", mfa=False)
def logout(h):
    me = h.staff()
    with db.tx() as c:
        c.execute("DELETE FROM staff_sessions WHERE id=?", (me["session_id"],))
    return h.json({"ok": True}, headers={"Set-Cookie": _clear_cookie(h)})


@route("GET", "/api/staff/me", auth="staff", mfa=False)
def me_(h):
    me = h.staff()
    return h.json({
        "staff": {"id": me["id"], "name": me["name"], "email": me["email"], "roles": me["roles"]},
        "perms": sorted(me["perms"]) if me["mfa_passed"] else [],
        "csrf": me["csrf"], "mfa_passed": me["mfa_passed"], "totp_enabled": me["totp_enabled"],
        "mfa_required": mfa_required(),
    })


@route("POST", "/api/staff/password", auth="staff")
def change_password(h):
    me = h.staff()
    d = h.json_body() or {}
    with db.read() as c:
        stored = c.execute("SELECT password_hash FROM staff_users WHERE id=?", (me["id"],)).fetchone()[0]
    if not security.verify_password(d.get("current") or "", stored):
        return h.json({"error": "Your current password isn't right."}, 400)
    problem = security.password_problem(d.get("new") or "", email=me["email"])
    if problem:
        return h.json({"error": problem}, 400)
    with db.tx() as c:
        c.execute("UPDATE staff_users SET password_hash=?, password_changed_at=? WHERE id=?",
                  (security.hash_password(d["new"]), db.now(), me["id"]))
        # sign out everywhere else
        c.execute("DELETE FROM staff_sessions WHERE staff_id=? AND id<>?", (me["id"], me["session_id"]))
        audit.record(c, h, "staff.password_changed", entity_type="staff", entity_id=me["id"])
    return h.json({"ok": True})


@route("POST", "/api/staff/sessions/revoke-others", auth="staff")
def revoke_others(h):
    me = h.staff()
    with db.tx() as c:
        n = c.execute("DELETE FROM staff_sessions WHERE staff_id=? AND id<>?", (me["id"], me["session_id"])).rowcount
        audit.record(c, h, "staff.sessions_revoked", entity_type="staff", entity_id=me["id"], details={"count": n})
    return h.json({"ok": True, "revoked": n})


# ---------------------------------------------------------------- invites


def _new_link(c, h, staff_id, purpose):
    """Create a one-time link, email it if email is set up, and return
    (link, emailed)."""
    token = security.new_token()
    c.execute("INSERT INTO auth_tokens(purpose, token_hash, staff_id, expires_at, created_at, created_by)"
              " VALUES (?,?,?,?,?,?)",
              (purpose, security.hash_token(token), staff_id, _utc(hours=INVITE_HOURS), db.now(), h.staff()["id"]))
    # the token rides in the URL fragment, which browsers never send to the server (or its logs)
    link = "%s/admin#%s=%s" % (_site(h), "invite" if purpose == "staff_invite" else "reset", token)
    emailed = False
    if mail.configured():
        user = c.execute("SELECT name, email FROM staff_users WHERE id=?", (staff_id,)).fetchone()
        outbox.email(c, user["email"], purpose, {"name": user["name"], "inviter": h.staff()["name"],
                                                 "hours": INVITE_HOURS},
                     kind="staff", to_name=user["name"], secret=link, staff_id=staff_id)
        emailed = True
    return link, emailed


def _site(h):
    return site_url(h)


def _valid_link(c, token):
    row = c.execute("SELECT t.*, u.name, u.email, u.status FROM auth_tokens t JOIN staff_users u ON u.id=t.staff_id"
                    " WHERE t.token_hash=? AND t.purpose IN ('staff_invite','staff_reset')",
                    (security.hash_token(token or ""),)).fetchone()
    if not row or row["used_at"] or row["expires_at"] <= db.now() or row["status"] == "disabled":
        return None
    return row


@route("POST", "/api/staff/invite/check")
def invite_check(h):
    d = h.json_body() or {}
    if not ratelimit.hit("staff_invite_check", h.client_ip()):
        return h.json({"error": SLOW_DOWN}, 429)
    with db.read() as c:
        row = _valid_link(c, d.get("token"))
    if not row:
        return h.json({"error": "This link has expired or has already been used. Ask an administrator for a new one."}, 404)
    return h.json({"name": row["name"], "email": row["email"], "purpose": row["purpose"]})


@route("POST", "/api/staff/invite/accept")
def invite_accept(h):
    d = h.json_body() or {}
    if not ratelimit.hit("staff_invite_check", h.client_ip()):
        return h.json({"error": SLOW_DOWN}, 429)
    with db.tx() as c:
        row = _valid_link(c, d.get("token"))
        if not row:
            return h.json({"error": "This link has expired or has already been used. Ask an administrator for a new one."}, 404)
        problem = security.password_problem(d.get("password") or "", email=row["email"])
        if problem:
            return h.json({"error": problem}, 400)
        c.execute("UPDATE auth_tokens SET used_at=? WHERE id=?", (db.now(), row["id"]))
        c.execute("UPDATE staff_users SET password_hash=?, password_changed_at=?, status='active' WHERE id=?",
                  (security.hash_password(d["password"]), db.now(), row["staff_id"]))
        if row["purpose"] == "staff_reset":
            # a reset also resets 2FA (lost phone is the usual reason)
            c.execute("UPDATE staff_users SET totp_enabled=0, totp_secret=NULL, recovery_codes=NULL, totp_last_step=NULL"
                      " WHERE id=?", (row["staff_id"],))
            c.execute("DELETE FROM staff_sessions WHERE staff_id=?", (row["staff_id"],))
        user = c.execute("SELECT * FROM staff_users WHERE id=?", (row["staff_id"],)).fetchone()
        audit.record(c, h, "staff.invite_accepted" if row["purpose"] == "staff_invite" else "staff.reset_used",
                     entity_type="staff", entity_id=user["id"], actor=user)
        step, (cookie, csrf) = _after_password(c, h, user)
    return h.json({"ok": True, "step": step, "csrf": csrf}, headers={"Set-Cookie": cookie})


# ---------------------------------------------------------------- managing staff


def _user_json(c, row):
    return {"id": row["id"], "name": row["name"], "email": row["email"], "status": row["status"],
            "roles": _roles(c, row["id"]), "totp_enabled": bool(row["totp_enabled"]),
            "last_login_at": row["last_login_at"], "created_at": row["created_at"]}


@route("GET", "/api/staff/users", auth="staff", perm="staff.manage")
def list_users(h):
    with db.read() as c:
        users = [_user_json(c, r) for r in c.execute("SELECT * FROM staff_users ORDER BY status, name")]
    return h.json({"users": users, "roles": permissions.ROLES,
                   "grantable": [r for r in permissions.ROLES if permissions.can_grant(h.staff()["roles"], r)]})


def _check_roles(h, roles):
    if not isinstance(roles, list) or not roles or any(r not in permissions.ROLES for r in roles):
        return "Choose at least one role."
    bad = [r for r in roles if not permissions.can_grant(h.staff()["roles"], r)]
    if bad:
        return "You can't give the role: %s." % ", ".join(permissions.ROLES[r] for r in bad)
    return None


@route("POST", "/api/staff/users/invite", auth="staff", perm="staff.manage")
def invite(h):
    d = h.json_body() or {}
    name, email = (d.get("name") or "").strip()[:100], (d.get("email") or "").strip().lower()[:200]
    roles = sorted(set(d.get("roles") or []))
    if not name or "@" not in email or "." not in email.split("@")[-1]:
        return h.json({"error": "Please give a name and a valid email address."}, 400)
    problem = _check_roles(h, roles)
    if problem:
        return h.json({"error": problem}, 400)
    with db.tx() as c:
        if c.execute("SELECT 1 FROM staff_users WHERE email=?", (email,)).fetchone():
            return h.json({"error": "There's already a staff account with that email."}, 409)
        cur = c.execute("INSERT INTO staff_users(email, name, status, created_at, created_by) VALUES (?,?, 'invited', ?, ?)",
                        (email, name, db.now(), h.staff()["id"]))
        c.executemany("INSERT INTO staff_roles VALUES (?,?)", [(cur.lastrowid, r) for r in roles])
        link, emailed = _new_link(c, h, cur.lastrowid, "staff_invite")
        audit.record(c, h, "staff.invited", entity_type="staff", entity_id=cur.lastrowid, details={"roles": roles})
    return h.json({"ok": True, "id": cur.lastrowid, "link": link, "emailed": emailed, "expires_hours": INVITE_HOURS})


def _owners(c):
    return {r[0] for r in c.execute("SELECT r.staff_id FROM staff_roles r JOIN staff_users u ON u.id=r.staff_id"
                                    " WHERE r.role='owner' AND u.status='active'")}


def _target(c, h, staff_id):
    """The staff member being managed, if the current user may manage them."""
    row = c.execute("SELECT * FROM staff_users WHERE id=?", (staff_id,)).fetchone()
    if not row:
        h.json({"error": "No such staff member."}, 404)
        return None, True
    if "owner" in _roles(c, row["id"]) and "owner" not in h.staff()["roles"]:
        h.json({"error": "Only an owner can change an owner's account."}, 403)
        return None, True
    return row, False


@route("POST", "/api/staff/users/<int_id>/update", auth="staff", perm="staff.manage")
def update_user(h, int_id):
    d = h.json_body() or {}
    with db.tx() as c:
        row, err = _target(c, h, int(int_id))
        if err:
            return
        before = _roles(c, row["id"])
        if "roles" in d:
            roles = sorted(set(d["roles"] or []))
            problem = _check_roles(h, [r for r in roles if r not in before] or roles)
            removed = [r for r in before if r not in roles]
            if not problem and any(not permissions.can_grant(h.staff()["roles"], r) for r in removed):
                problem = "You can't take away a role you couldn't give."
            if problem:
                return h.json({"error": problem}, 400)
            if "owner" in before and "owner" not in roles and _owners(c) == {row["id"]}:
                return h.json({"error": "The site needs at least one owner."}, 400)
            c.execute("DELETE FROM staff_roles WHERE staff_id=?", (row["id"],))
            c.executemany("INSERT INTO staff_roles VALUES (?,?)", [(row["id"], r) for r in roles])
            audit.record(c, h, "staff.roles_changed", entity_type="staff", entity_id=row["id"],
                         details={"before": before, "after": roles})
        if d.get("name"):
            c.execute("UPDATE staff_users SET name=? WHERE id=?", (d["name"].strip()[:100], row["id"]))
        return h.json({"ok": True, "user": _user_json(c, c.execute("SELECT * FROM staff_users WHERE id=?",
                                                                    (row["id"],)).fetchone())})


@route("POST", "/api/staff/users/<int_id>/status", auth="staff", perm="staff.manage")
def set_status(h, int_id):
    d = h.json_body() or {}
    status = d.get("status")
    if status not in ("active", "disabled"):
        return h.json({"error": "status must be active or disabled"}, 400)
    with db.tx() as c:
        row, err = _target(c, h, int(int_id))
        if err:
            return
        if row["id"] == h.staff()["id"]:
            return h.json({"error": "You can't disable your own account."}, 400)
        if status == "disabled" and _owners(c) == {row["id"]}:
            return h.json({"error": "The site needs at least one owner."}, 400)
        if status == "active" and not row["password_hash"]:
            return h.json({"error": "They haven't accepted their invite yet — send a new link instead."}, 400)
        c.execute("UPDATE staff_users SET status=? WHERE id=?", (status, row["id"]))
        c.execute("DELETE FROM staff_sessions WHERE staff_id=?", (row["id"],))
        audit.record(c, h, "staff.enabled" if status == "active" else "staff.disabled",
                     entity_type="staff", entity_id=row["id"])
    return h.json({"ok": True})


@route("POST", "/api/staff/users/<int_id>/reset", auth="staff", perm="staff.manage")
def reset_user(h, int_id):
    """A one-time link to set a new password and set up 2FA again (e.g. a lost
    phone). Their current sign-ins end now."""
    with db.tx() as c:
        row, err = _target(c, h, int(int_id))
        if err:
            return
        if row["status"] == "disabled":
            return h.json({"error": "Re-enable the account first."}, 400)
        c.execute("DELETE FROM staff_sessions WHERE staff_id=?", (row["id"],))
        c.execute("UPDATE auth_tokens SET used_at=? WHERE staff_id=? AND used_at IS NULL", (db.now(), row["id"]))
        purpose = "staff_invite" if row["status"] == "invited" else "staff_reset"
        link, emailed = _new_link(c, h, row["id"], purpose)
        audit.record(c, h, "staff.link_issued", entity_type="staff", entity_id=row["id"], details={"purpose": purpose})
    return h.json({"ok": True, "link": link, "emailed": emailed, "expires_hours": INVITE_HOURS})


@route("POST", "/api/staff/security", auth="staff", perm="staff.manage")
def security_settings(h):
    """Owners only: whether every staff member must use two-factor sign-in."""
    if "owner" not in h.staff()["roles"]:
        return h.json({"error": "Only an owner can change this."}, 403)
    d = h.json_body() or {}
    required = bool(d.get("mfa_required", True))
    with db.tx() as c:
        db.set_setting("staff_mfa_required", required, c)
        audit.record(c, h, "settings.staff_mfa_required", details={"value": required})
    return h.json({"ok": True, "mfa_required": required})


# ---------------------------------------------------------------- audit log


@route("GET", "/api/staff/audit", auth="staff", perm="audit.view")
def audit_log(h):
    import urllib.parse
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(h.path).query))
    where, args = [], []
    if "safeguarding.view" not in h.staff()["perms"]:
        where.append("restricted=0")
    if q.get("action"):
        where.append("action LIKE ?")
        args.append(q["action"].replace("%", "") + "%")
    if q.get("actor"):
        where.append("actor_id=?")
        args.append(int(q["actor"]) if q["actor"].isdigit() else -1)
    page = max(1, int(q.get("page", "1")) if q.get("page", "1").isdigit() else 1)
    sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "")
    with db.read() as c:
        rows = [dict(r) for r in c.execute(sql + " ORDER BY id DESC LIMIT 51 OFFSET ?", args + [(page - 1) * 50])]
    for r in rows:
        r["details"] = json.loads(r["details"]) if r["details"] else None
    return h.json({"entries": rows[:50], "page": page, "more": len(rows) > 50})
