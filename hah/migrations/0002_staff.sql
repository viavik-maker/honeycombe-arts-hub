-- Individual staff accounts (replacing the shared admin password), their
-- roles and sessions, one-time tokens, and the audit log.

CREATE TABLE staff_users(
  id                  INTEGER PRIMARY KEY,
  email               TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name                TEXT NOT NULL,
  password_hash       TEXT,                       -- NULL until an invite is accepted
  password_changed_at TEXT,
  totp_secret         TEXT,                       -- base32; NULL until 2FA is set up
  totp_enabled        INTEGER NOT NULL DEFAULT 0 CHECK (totp_enabled IN (0, 1)),
  totp_last_step      INTEGER,                    -- stops a code being used twice
  recovery_codes      TEXT,                       -- JSON list of sha256 hashes
  status              TEXT NOT NULL CHECK (status IN ('invited', 'active', 'disabled')),
  last_login_at       TEXT,
  created_at          TEXT NOT NULL,
  created_by          INTEGER REFERENCES staff_users(id)
);

CREATE TABLE staff_roles(
  staff_id INTEGER NOT NULL REFERENCES staff_users(id) ON DELETE CASCADE,
  role     TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'manager', 'session_staff', 'dsl', 'send_lead', 'finance')),
  PRIMARY KEY (staff_id, role)
);

CREATE TABLE staff_sessions(
  id              INTEGER PRIMARY KEY,
  token_hash      TEXT NOT NULL UNIQUE,           -- sha256 of the cookie value; the raw token is never stored
  staff_id        INTEGER NOT NULL REFERENCES staff_users(id) ON DELETE CASCADE,
  csrf_token      TEXT NOT NULL,
  mfa_passed      INTEGER NOT NULL DEFAULT 0 CHECK (mfa_passed IN (0, 1)),
  created_at      TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL,
  idle_expires_at TEXT NOT NULL,
  expires_at      TEXT NOT NULL,
  ip              TEXT,
  user_agent      TEXT
);
CREATE INDEX ix_staff_sessions_staff ON staff_sessions(staff_id);

-- One-time links (staff invites and password resets now; parent account
-- links later). Only the sha256 of the token is kept.
CREATE TABLE auth_tokens(
  id         INTEGER PRIMARY KEY,
  purpose    TEXT NOT NULL CHECK (purpose IN ('staff_invite', 'staff_reset')),
  token_hash TEXT NOT NULL UNIQUE,
  staff_id   INTEGER REFERENCES staff_users(id) ON DELETE CASCADE,
  payload    TEXT,
  expires_at TEXT NOT NULL,
  used_at    TEXT,
  created_at TEXT NOT NULL,
  created_by INTEGER REFERENCES staff_users(id)
);

-- Who did what, when. Details hold field NAMES only — never health,
-- safeguarding or other personal values. Rows can't be changed, and can't
-- be deleted until they're six years old.
CREATE TABLE audit_log(
  id             INTEGER PRIMARY KEY,
  at             TEXT NOT NULL,
  actor_type     TEXT NOT NULL CHECK (actor_type IN ('staff', 'account', 'guest', 'system')),
  actor_id       INTEGER,
  actor_name     TEXT,
  ip             TEXT,
  action         TEXT NOT NULL,
  entity_type    TEXT,
  entity_id      INTEGER,
  participant_id INTEGER,
  account_id     INTEGER,
  restricted     INTEGER NOT NULL DEFAULT 0 CHECK (restricted IN (0, 1)),  -- safeguarding: DSLs only
  details        TEXT
);
CREATE INDEX ix_audit_at ON audit_log(at);
CREATE INDEX ix_audit_actor ON audit_log(actor_type, actor_id);

CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'the audit log cannot be changed');
END;

CREATE TRIGGER audit_log_no_early_delete BEFORE DELETE ON audit_log
WHEN OLD.at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-6 years')
BEGIN
  SELECT RAISE(ABORT, 'audit entries are kept for six years');
END;
