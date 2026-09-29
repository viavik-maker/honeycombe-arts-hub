-- The in-tray: things staff need to act on, raised by the system
-- (approvals, HAF checks, health changes, payment problems, messages…).

CREATE TABLE intray_items(
  id               INTEGER PRIMARY KEY,
  type             TEXT NOT NULL,
  title            TEXT NOT NULL,              -- no health or safeguarding detail; child first name at most
  detail           TEXT,
  entity_type      TEXT,
  entity_id        INTEGER,
  account_id       INTEGER,
  participant_id   INTEGER,
  centre_id        INTEGER,
  activity_id      INTEGER,
  session_id       INTEGER,
  required_perm    TEXT NOT NULL,              -- only staff with this permission see it
  status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'snoozed', 'done')),
  snooze_until     TEXT,
  assigned_staff_id INTEGER REFERENCES staff_users(id),
  created_at       TEXT NOT NULL,
  resolved_at      TEXT,
  resolved_by      INTEGER REFERENCES staff_users(id)
);
CREATE INDEX ix_intray_open ON intray_items(status, created_at);
