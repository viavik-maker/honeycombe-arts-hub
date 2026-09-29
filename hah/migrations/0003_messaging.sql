-- Outgoing email and SMS (the outbox, which doubles as the message archive)
-- and the contact-form inbox (moved here from data/messages.json).

CREATE TABLE message_deliveries(
  id              INTEGER PRIMARY KEY,
  campaign_id     INTEGER,                       -- bulk messages (added with Messaging)
  template_key    TEXT,
  channel         TEXT NOT NULL CHECK (channel IN ('email', 'sms')),
  kind            TEXT NOT NULL CHECK (kind IN ('service', 'marketing', 'staff')),
  to_address      TEXT NOT NULL,
  to_name         TEXT,
  subject         TEXT,
  body_text       TEXT NOT NULL,                 -- one-time links appear as {{SECRET}}, never the link itself
  body_html       TEXT,
  headers         TEXT,                          -- JSON
  secret          TEXT,                          -- the one-time link, only until the send succeeds or gives up
  account_id      INTEGER,
  staff_id        INTEGER,
  participant_id  INTEGER,
  booking_id      INTEGER,
  status          TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'sending', 'sent', 'failed', 'suppressed', 'cancelled')),
  attempts        INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL,
  locked_until    TEXT,
  provider_id     TEXT,
  error           TEXT,
  created_at      TEXT NOT NULL,
  sent_at         TEXT
);
CREATE INDEX ix_deliveries_queue ON message_deliveries(status, next_attempt_at);
CREATE INDEX ix_deliveries_account ON message_deliveries(account_id);

CREATE TABLE contact_messages(
  id         INTEGER PRIMARY KEY,
  ref        TEXT NOT NULL UNIQUE,               -- the id the admin Inbox uses
  name       TEXT NOT NULL,
  email      TEXT NOT NULL,
  phone      TEXT,
  message    TEXT NOT NULL,
  created_at TEXT NOT NULL,
  read_at    TEXT,
  handled_by INTEGER REFERENCES staff_users(id)
);
