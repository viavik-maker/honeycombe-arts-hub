-- Trial sessions: a child's first go at an activity, at a trial price.
-- NULL trial price = the normal price. One trial per child per activity.

ALTER TABLE activities ADD COLUMN trial_price_pence INTEGER
  CHECK (trial_price_pence IS NULL OR trial_price_pence BETWEEN 0 AND 100000);

CREATE INDEX ix_bookings_trials ON bookings(is_trial, activity_id) WHERE is_trial=1;
