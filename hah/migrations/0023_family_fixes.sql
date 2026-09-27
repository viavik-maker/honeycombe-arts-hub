-- What a family last told us about HAF in the portal. A staff decision
-- (haf_status 'verified', or a 'not_eligible' the family didn't choose)
-- then stands when the family saves the form again.
ALTER TABLE participants ADD COLUMN haf_family_answer TEXT;

-- Set when a child's record is handed over to them at 18: what the parent
-- wrote stays with the DSL and isn't part of the young adult's account.
ALTER TABLE participant_safeguarding ADD COLUMN handed_over_at TEXT;
