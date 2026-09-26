"""The staff in-tray: things that need someone to act.

Raise items with add() inside the transaction that caused them. Titles are
seen by every staff member with the item's permission, so they never hold
health or safeguarding details (a child's first name at most)."""
from . import db


def add(c, type_, title, *, perm, detail=None, entity_type=None, entity_id=None, account_id=None,
        participant_id=None, centre_id=None, activity_id=None, session_id=None, dedupe=True):
    """Raise an in-tray item (skipped if an identical open one exists)."""
    if dedupe and c.execute(
            "SELECT 1 FROM intray_items WHERE status<>'done' AND type=? AND entity_type IS ? AND entity_id IS ?"
            " AND account_id IS ? AND participant_id IS ? AND session_id IS ?",
            (type_, entity_type, entity_id, account_id, participant_id, session_id)).fetchone():
        return None
    return c.execute(
        "INSERT INTO intray_items(type, title, detail, entity_type, entity_id, account_id, participant_id, centre_id,"
        " activity_id, session_id, required_perm, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (type_, title[:200], detail, entity_type, entity_id, account_id, participant_id, centre_id, activity_id,
         session_id, perm, db.now())).lastrowid


def resolve(c, type_, *, entity_type=None, entity_id=None, staff_id=None):
    """Close open items of TYPE for an entity (e.g. when the booking is approved)."""
    c.execute("UPDATE intray_items SET status='done', resolved_at=?, resolved_by=? WHERE status<>'done' AND type=?"
              " AND entity_type IS ? AND entity_id IS ?", (db.now(), staff_id, type_, entity_type, entity_id))
