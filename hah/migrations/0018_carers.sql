-- Extra carers who can sign in to a family's account with their own email
-- and password (a grandparent who does the school-holiday bookings, say).
-- They can book, pay and see bookings; only the account holder changes
-- family details, consents, collection arrangements or the account itself.

CREATE TABLE carers(
  id             INTEGER PRIMARY KEY,
  ref            TEXT NOT NULL UNIQUE,
  account_id     INTEGER NOT NULL REFERENCES accounts(id),
  email          TEXT NOT NULL COLLATE NOCASE,
  first_name     TEXT NOT NULL,
  last_name      TEXT NOT NULL,
  relationship   TEXT,
  password_hash  TEXT,
  status         TEXT NOT NULL CHECK (status IN ('invited', 'active', 'removed')),
  invited_at     TEXT NOT NULL,
  activated_at   TEXT,
  last_login_at  TEXT,
  removed_at     TEXT,
  created_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_carers_email ON carers(email) WHERE status <> 'removed';
CREATE INDEX ix_carers_account ON carers(account_id);

CREATE TABLE carer_tokens(
  id          INTEGER PRIMARY KEY,
  carer_id    INTEGER NOT NULL REFERENCES carers(id) ON DELETE CASCADE,
  purpose     TEXT NOT NULL CHECK (purpose IN ('invite', 'reset_password')),
  token_hash  TEXT NOT NULL UNIQUE,
  expires_at  TEXT NOT NULL,
  used_at     TEXT,
  created_at  TEXT NOT NULL
);

ALTER TABLE account_sessions ADD COLUMN carer_id INTEGER REFERENCES carers(id);
