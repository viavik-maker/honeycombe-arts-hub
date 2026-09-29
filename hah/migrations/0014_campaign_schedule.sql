-- Messages can be scheduled for later. Who gets them is worked out when
-- they go (the audience rule is stored, not the list).

ALTER TABLE campaigns ADD COLUMN status TEXT NOT NULL DEFAULT 'sent'
  CHECK (status IN ('scheduled', 'sent', 'cancelled'));
ALTER TABLE campaigns ADD COLUMN send_at TEXT;
ALTER TABLE campaigns ADD COLUMN sent_at TEXT;
ALTER TABLE campaigns ADD COLUMN cancelled_by INTEGER REFERENCES staff_users(id);
ALTER TABLE campaigns ADD COLUMN problem TEXT;
CREATE INDEX ix_campaigns_due ON campaigns(status, send_at);
