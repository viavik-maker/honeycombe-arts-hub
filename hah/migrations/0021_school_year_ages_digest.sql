-- Ages by school year (a child's age on the 31 August before the school
-- year, as schools count it), the staff daily digest, and editable
-- introductions for the system emails.

ALTER TABLE activities ADD COLUMN age_by_school_year INTEGER NOT NULL DEFAULT 0 CHECK (age_by_school_year IN (0, 1));
ALTER TABLE staff_users ADD COLUMN daily_digest INTEGER NOT NULL DEFAULT 0 CHECK (daily_digest IN (0, 1));

CREATE TABLE email_intros(
  template_key TEXT PRIMARY KEY,
  intro        TEXT NOT NULL,           -- a short note staff add above the standard wording
  updated_by   INTEGER REFERENCES staff_users(id),
  updated_at   TEXT NOT NULL
);
