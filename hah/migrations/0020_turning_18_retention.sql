-- Children who turn 18 get their own account (a handover), and the
-- long-range retention jobs keep track of what they've warned about.

ALTER TABLE participants ADD COLUMN adult_notified_at TEXT;   -- the parent was told they've turned 18
ALTER TABLE accounts ADD COLUMN inactive_warned_at TEXT;       -- "we'll delete your account unless you sign in"

CREATE TABLE handovers(
  id              INTEGER PRIMARY KEY,
  participant_id  INTEGER NOT NULL REFERENCES participants(id),
  from_account_id INTEGER NOT NULL REFERENCES accounts(id),
  email           TEXT NOT NULL COLLATE NOCASE,
  token_hash      TEXT NOT NULL UNIQUE,
  expires_at      TEXT NOT NULL,
  used_at         TEXT,
  new_account_id  INTEGER REFERENCES accounts(id),
  created_at      TEXT NOT NULL
);
CREATE INDEX ix_handovers_participant ON handovers(participant_id);
