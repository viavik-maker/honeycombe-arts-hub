-- SEND support requests (mockup 6) and private files (EHCPs and other plans).
-- Only the family who sent them and staff with the send.view permission can
-- see them. Files live in data/private/, never under the public folder.

CREATE TABLE send_intakes(
  id                    INTEGER PRIMARY KEY,
  ref                   TEXT NOT NULL UNIQUE,
  account_id            INTEGER NOT NULL REFERENCES accounts(id),
  participant_id        INTEGER NOT NULL REFERENCES participants(id),
  contact_method        TEXT NOT NULL CHECK (contact_method IN ('phone', 'visit', 'email')),
  best_time             TEXT NOT NULL,
  needs                 TEXT NOT NULL,           -- JSON list, e.g. ["autism","sensory"]
  needs_other           TEXT,
  interests             TEXT NOT NULL,           -- JSON list: holiday_clubs, regular, one_off, not_sure
  good_day              TEXT,
  overwhelm             TEXT,
  communication         TEXT,
  current_support       TEXT,
  one_to_one            TEXT CHECK (one_to_one IN ('yes', 'sometimes', 'no', 'not_sure')),
  ehcp                  TEXT NOT NULL CHECK (ehcp IN ('yes', 'being_assessed', 'no')),
  consent_share_staff   INTEGER NOT NULL CHECK (consent_share_staff = 1),
  consent_professionals INTEGER NOT NULL DEFAULT 0,
  status                TEXT NOT NULL DEFAULT 'submitted'
                        CHECK (status IN ('submitted', 'contacted', 'visit_booked', 'plan_agreed', 'closed')),
  visit_at              TEXT,
  assigned_staff_id     INTEGER REFERENCES staff_users(id),
  plan_summary          TEXT,                    -- what register staff see once a plan is agreed
  plan_agreed_at        TEXT,
  plan_agreed_by        INTEGER REFERENCES staff_users(id),
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL
);
CREATE INDEX ix_send_intakes_status ON send_intakes(status, created_at);
CREATE INDEX ix_send_intakes_participant ON send_intakes(participant_id);

-- The SEND lead's notes on a request (append-only in practice).
CREATE TABLE send_notes(
  id         INTEGER PRIMARY KEY,
  intake_id  INTEGER NOT NULL REFERENCES send_intakes(id) ON DELETE CASCADE,
  staff_id   INTEGER REFERENCES staff_users(id),
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE private_files(
  id             INTEGER PRIMARY KEY,
  ref            TEXT NOT NULL UNIQUE,           -- random: used in download links
  account_id     INTEGER NOT NULL REFERENCES accounts(id),
  participant_id INTEGER REFERENCES participants(id),
  intake_id      INTEGER REFERENCES send_intakes(id),
  kind           TEXT NOT NULL CHECK (kind IN ('ehcp', 'other_plan')),
  filename       TEXT NOT NULL,                  -- tidied original name, for the download
  content_type   TEXT NOT NULL,
  size           INTEGER NOT NULL,
  sha256         TEXT NOT NULL,
  stored_name    TEXT NOT NULL UNIQUE,           -- random name on disk
  uploaded_by_staff INTEGER REFERENCES staff_users(id),
  created_at     TEXT NOT NULL,
  deleted_at     TEXT
);
CREATE INDEX ix_private_files_intake ON private_files(intake_id);
CREATE INDEX ix_private_files_account ON private_files(account_id);

ALTER TABLE participants ADD COLUMN support_plan TEXT;         -- summary shown on registers
ALTER TABLE participants ADD COLUMN support_plan_agreed_at TEXT;
