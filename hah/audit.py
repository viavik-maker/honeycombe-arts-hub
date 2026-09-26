"""The audit log: who did what, when.

Record inside the same transaction as the change it describes. DETAILS must
never contain personal values (health, safeguarding, contact details) —
record which fields changed, not what they changed to."""
import json

from . import db


def record(c, h, action, *, entity_type=None, entity_id=None, participant_id=None,
           account_id=None, details=None, restricted=False, actor=None, account_actor=None):
    """Write one audit row. H is the request handler (for the actor and IP),
    or None for system actions. The actor is the signed-in staff member, else
    the signed-in parent; ACTOR (a staff dict, or {} for "nobody yet") or
    ACCOUNT_ACTOR (an account id) override that, e.g. during sign-in."""
    if account_actor is not None:
        actor_type, actor_id, actor_name = "account", account_actor, None
    else:
        staff = actor if actor is not None else (h.staff() if h is not None else None)
        parent = h.principal("account") if (h is not None and actor is None and not staff) else None
        if staff:
            actor_type, actor_id, actor_name = "staff", staff["id"], staff["name"]
        elif parent:
            actor_type, actor_id, actor_name = "account", parent["id"], None
        else:
            actor_type, actor_id, actor_name = "system", None, None
    c.execute("INSERT INTO audit_log(at, actor_type, actor_id, actor_name, ip, action, entity_type,"
              " entity_id, participant_id, account_id, restricted, details)"
              " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              (db.now(), actor_type, actor_id, actor_name, h.client_ip() if h is not None else None,
               action, entity_type, entity_id, participant_id, account_id, 1 if restricted else 0,
               json.dumps(details) if details is not None else None))
