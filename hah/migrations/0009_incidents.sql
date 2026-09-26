-- Injuries, illness, behaviour and safeguarding concerns. Safeguarding
-- concerns are restricted: only the DSL role sees them (not even owners).
-- Accident records for children are kept until their 25th birthday.

CREATE TABLE incidents(
  id                   INTEGER PRIMARY KEY,
  ref                  TEXT NOT NULL UNIQUE,
  occurred_at          TEXT NOT NULL,             -- UK local 'YYYY-MM-DDTHH:MM'
  session_id           INTEGER REFERENCES activity_sessions(id),
  centre_id            INTEGER REFERENCES centres(id),
  kind                 TEXT NOT NULL CHECK (kind IN ('injury', 'illness', 'behaviour', 'safeguarding',
                                                     'near_miss', 'other')),
  severity             TEXT NOT NULL DEFAULT 'minor' CHECK (severity IN ('minor', 'moderate', 'serious')),
  location             TEXT,
  description          TEXT NOT NULL,
  action_taken         TEXT,
  first_aid_given      INTEGER NOT NULL DEFAULT 0,
  first_aider          TEXT,
  witnesses            TEXT,
  restricted           INTEGER NOT NULL DEFAULT 0 CHECK (restricted IN (0, 1)),
  notify_mode          TEXT NOT NULL CHECK (notify_mode IN ('now', 'at_collection', 'not_notified')),
  not_notified_reason  TEXT,
  parent_notified_at   TEXT,
  discussed_at         TEXT,                      -- talked through with the parent at collection
  discussed_by         INTEGER REFERENCES staff_users(id),
  follow_up            TEXT,
  riddor_reportable    INTEGER NOT NULL DEFAULT 0,
  status               TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
  closed_at            TEXT,
  created_by           INTEGER REFERENCES staff_users(id),
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  retain_until         TEXT                       -- set from the youngest child's DOB + 25 years
);
CREATE INDEX ix_incidents_session ON incidents(session_id);
CREATE INDEX ix_incidents_status ON incidents(status, occurred_at);

-- Who was involved: a registered child/adult, or just a name (guest events).
CREATE TABLE incident_people(
  id               INTEGER PRIMARY KEY,
  incident_id      INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  participant_id   INTEGER REFERENCES participants(id),
  booking_id       INTEGER REFERENCES bookings(id),
  guest_contact_id INTEGER REFERENCES guest_contacts(id),
  person_name      TEXT,
  role             TEXT NOT NULL DEFAULT 'involved' CHECK (role IN ('injured', 'involved', 'witness')),
  account_id       INTEGER REFERENCES accounts(id),
  acknowledged_at  TEXT,                          -- parent read and acknowledged it online
  CHECK (participant_id IS NOT NULL OR person_name IS NOT NULL)
);
CREATE INDEX ix_incident_people_participant ON incident_people(participant_id);
CREATE INDEX ix_incident_people_account ON incident_people(account_id);
