-- Platform tables shared by every part of the booking system.

-- Gap-free sequences (invoice numbers etc.), bumped inside the same transaction.
CREATE TABLE counters(
  name  TEXT PRIMARY KEY,
  value INTEGER NOT NULL
);

-- Booking-system settings edited by staff (JSON values). Secrets never go here:
-- they live in the host's environment variables.
CREATE TABLE settings(
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  updated_by_staff_id INTEGER
);

-- Background jobs: when each last ran and how it went.
CREATE TABLE scheduled_jobs(
  name             TEXT PRIMARY KEY,
  next_run_at      TEXT,
  last_started_at  TEXT,
  last_finished_at TEXT,
  last_status      TEXT CHECK (last_status IN ('ok', 'failed', 'timed_out', 'running')),
  last_error       TEXT,
  last_detail      TEXT
);
