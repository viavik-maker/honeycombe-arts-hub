"""Staff roles and what each can do.

Routes name the permission they need (@route(..., perm="bookings.manage"));
a staff member has a permission if any of their roles grants it. The
matrix follows docs/booking/DESIGN.md §4.0 with the review amendments:
safeguarding information is DSL-only (even owners need the DSL role), and
session staff only see health details through a session's register."""

ROLES = {
    "owner": "Owner (trustee / manager with overall responsibility)",
    "admin": "Administrator",
    "manager": "Manager",
    "session_staff": "Session staff",
    "dsl": "Designated Safeguarding Lead",
    "send_lead": "SEND lead",
    "finance": "Finance",
}

_ALL_BUT_SENSITIVE = {
    "site.content", "activities.view", "activities.manage", "bookings.view", "bookings.manage",
    "bookings.override", "payments.record", "finance.view", "finance.manage", "registers.view",
    "registers.mark", "people.view_basic", "people.view_health", "people.edit", "incidents.log",
    "incidents.view", "incidents.manage", "messaging.service", "messaging.marketing",
    "reports.view", "import.run", "settings.manage", "staff.manage", "system.view",
}

ROLE_PERMS = {
    "owner": _ALL_BUT_SENSITIVE | {"audit.view", "gdpr.manage", "send.view"},
    "admin": _ALL_BUT_SENSITIVE | {"send.view"},
    "manager": {
        "site.content", "activities.view", "activities.manage", "bookings.view", "bookings.manage",
        "bookings.override", "payments.record", "finance.view", "registers.view", "registers.mark",
        "people.view_basic", "people.view_health", "people.edit", "incidents.log", "incidents.view",
        "incidents.manage", "messaging.service", "messaging.marketing", "reports.view", "send.view",
        "system.view",
    },
    "session_staff": {
        "activities.view", "bookings.view", "payments.record", "registers.view", "registers.mark",
        "people.view_basic", "incidents.log",
    },
    "dsl": {
        "activities.view", "bookings.view", "registers.view", "registers.mark", "people.view_basic",
        "people.view_health", "people.edit", "safeguarding.view", "incidents.log", "incidents.view",
        "incidents.manage", "reports.view", "audit.view", "gdpr.manage", "send.view",
    },
    "send_lead": {
        "activities.view", "bookings.view", "registers.view", "registers.mark", "people.view_basic",
        "people.view_health", "people.edit", "incidents.log", "incidents.view", "reports.view",
        "send.view",
    },
    "finance": {
        "activities.view", "bookings.view", "payments.record", "finance.view", "finance.manage",
        "people.view_basic", "reports.view",
    },
}

# roles only an owner may hand out
OWNER_GRANTED = {"owner", "dsl"}


def perms_for(roles):
    out = set()
    for r in roles:
        out |= ROLE_PERMS.get(r, set())
    return out


def can_grant(granter_roles, role):
    """May someone with GRANTER_ROLES give ROLE to a colleague? Only owners
    grant owner or DSL; otherwise you can only grant a role whose
    permissions you already have yourself."""
    if role not in ROLES:
        return False
    if role in OWNER_GRANTED:
        return "owner" in granter_roles
    return "owner" in granter_roles or ROLE_PERMS[role] <= perms_for(granter_roles)
