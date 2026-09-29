"""The registration forms, defined once.

The portal renders its forms from SPEC (served at /api/account/formspec), and
the server validates every save against it. So the two can't drift apart,
and a parent can only ever write the fields listed here (nothing like
haf_status='verified', pay-later or review flags).

Levels (what a child's profile is complete enough for):
  short — baby & toddler classes and other sessions where a parent stays
  full  — holiday clubs and other drop-off sessions (the full form, mockup 1)
  adult — young adults (18+) booking for themselves
Each field lists the levels it's required for; optional fields list none."""
import datetime

from . import validate
from .validate import Invalid

LEVELS = ("short", "full", "adult")
S, F, A = "short", "full", "adult"

# ---------------------------------------------------------------- sections

SPEC = {
    # the account holder (parent/carer, or the young adult themself)
    "you": {
        "scope": "account", "title": "Your details",
        "intro": "The parent or carer responsible — or you, if you're 18 or over and booking for yourself.",
        "fields": [
            {"key": "first_name", "label": "First name", "type": "text", "required": [S, F, A], "max": 60, "autocomplete": "given-name"},
            {"key": "last_name", "label": "Last name", "type": "text", "required": [S, F, A], "max": 60, "autocomplete": "family-name"},
            {"key": "mobile", "label": "Mobile number", "type": "tel", "required": [S, F, A], "autocomplete": "tel",
             "hint": "We text you if a session changes or a waiting-list place comes up."},
            {"key": "address_line1", "label": "Home address", "type": "text", "required": [F], "max": 120, "autocomplete": "address-line1"},
            {"key": "address_line2", "label": "Address line 2 (optional)", "type": "text", "required": [], "max": 120, "autocomplete": "address-line2"},
            {"key": "town", "label": "Town", "type": "text", "required": [F], "max": 60, "autocomplete": "address-level2"},
            {"key": "postcode", "label": "Postcode", "type": "postcode", "required": [S, F, A], "autocomplete": "postal-code"},
        ],
    },
    "child": {
        "scope": "participant", "title": "About your child",
        "fields": [
            {"key": "first_name", "label": "First name", "type": "text", "required": [S, F], "max": 60},
            {"key": "last_name", "label": "Last name", "type": "text", "required": [S, F], "max": 60},
            {"key": "dob", "label": "Date of birth", "type": "date", "required": [S, F], "hint": "We work out their age from this.",
             "error": "Enter their date of birth."},
            {"key": "gender", "label": "Gender (optional)", "type": "select", "required": [],
             "options": [["", "Prefer not to say"], ["girl", "Girl"], ["boy", "Boy"], ["other", "Other"]]},
            {"key": "education", "label": "Education", "type": "radio", "required": [F],
             "options": [["school", "At school"], ["home_educated", "Home educated"]]},
            {"key": "school_name", "label": "School name", "type": "text", "required": [], "max": 120,
             "hint": "Leave blank if home educated.", "show_if": ["education", "school"]},
            {"key": "haf_status", "label": "HAF funded places", "type": "radio", "required": [F],
             "hint": "HAF places are free for children who receive benefits-related free school meals. Not sure? Choose that and we'll check with you.",
             "options": [["claimed_eligible", "Eligible for a HAF funded place"], ["not_eligible", "Not eligible"], ["not_sure", "Not sure"]]},
        ],
    },
    "gp": {
        "scope": "participant", "title": "Doctor / GP",
        "fields": [
            {"key": "surgery_name", "label": "GP surgery name", "type": "text", "required": [F], "max": 120},
            {"key": "doctor_name", "label": "Doctor's name (optional)", "type": "text", "required": [], "max": 80},
            {"key": "surgery_phone", "label": "Surgery phone number", "type": "phone", "required": [F]},
            {"key": "surgery_postcode", "label": "Surgery postcode (optional)", "type": "postcode", "required": []},
        ],
    },
    "health": {
        "scope": "participant", "title": "Health, needs and requirements", "saved_counts": True,
        "intro": "Tell us anything that helps us look after them well. Leave a box blank if it doesn't apply.",
        "fields": [
            {"key": "allergies", "label": "Allergies", "type": "textarea", "required": [], "max": 1000,
             "placeholder": "e.g. nuts, bee stings — and what to do if there's a reaction"},
            {"key": "anaphylaxis", "label": "Severe allergy (anaphylaxis)", "type": "checkbox", "required": []},
            {"key": "adrenaline_pen", "label": "Carries an adrenaline pen", "type": "checkbox", "required": []},
            {"key": "medical_conditions", "label": "Medical conditions", "type": "textarea", "required": [], "max": 1000,
             "placeholder": "e.g. asthma, epilepsy, diabetes"},
            {"key": "medication", "label": "Medication", "type": "textarea", "required": [], "max": 1000,
             "placeholder": "Anything they take, or that we may need to give"},
            {"key": "dietary", "label": "Dietary requirements", "type": "textarea", "required": [], "max": 500,
             "placeholder": "e.g. vegetarian, halal, no dairy", "levels": [S, F]},
            {"key": "send_needs", "label": "SEND needs", "type": "textarea", "required": [], "max": 2000,
             "placeholder": "Special educational needs or disabilities, and any support that helps", "levels": [F]},
            {"key": "semh_needs", "label": "SEMH needs", "type": "textarea", "required": [], "max": 2000,
             "placeholder": "Social, emotional or mental health needs we should know about", "levels": [F]},
            {"key": "religious_requirements", "label": "Religious requirements", "type": "textarea", "required": [], "max": 500,
             "placeholder": "e.g. prayer times, dress, food", "levels": [F]},
            {"key": "access_needs", "label": "Access needs (optional)", "type": "textarea", "required": [], "max": 1000,
             "placeholder": "Anything that helps you take part", "levels": [A]},
        ],
    },
    "safeguarding": {
        "scope": "participant", "title": "Family information", "saved_counts": True, "levels": [F],
        "fields": [
            {"key": "family_info", "label": "Anything relevant to your child's safety", "type": "textarea", "required": [], "max": 2000,
             "hint": "Kept confidential: only our Designated Safeguarding Lead can see this.",
             "placeholder": "e.g. court orders, changes at home we should be aware of"},
            {"key": "collection_alert", "label": "Anyone who must not collect your child", "type": "textarea", "required": [], "max": 500,
             "hint": "Session staff see this at pick-up so they can act on it."},
        ],
    },
    "consents": {
        "scope": "participant", "title": "Permissions and consents",
        "consents": [
            {"key": "photo", "required": [S, F, A]},
            {"key": "first_aid", "required": [F]},
            {"key": "plasters", "required": [F]},
            {"key": "emergency_treatment", "required": [F]},
            {"key": "go_home_alone", "required": [F], "min_age_months": 132},
            {"key": "funder_share", "required": [], "levels": [F, A]},
        ],
    },
    "collection": {
        "scope": "participant", "title": "Collection password", "levels": [F],
        "intro": "Whoever collects your child will be asked for this password.",
        "fields": [
            {"key": "password", "label": "Collection password", "type": "secret", "required": [F], "min": 4, "max": 60,
             "autocomplete": "off"},
        ],
    },
}

CONTACTS = {"required": {S: 0, F: 2, A: 1}, "max": 4}
ACKNOWLEDGEMENTS = ("info_correct", "privacy_ack")
GO_HOME_ALONE_MIN_MONTHS = 132  # 11 years (DSL to confirm)


def _lower_first(label):
    """'First name' -> 'first name', but 'GP surgery name' stays as it is."""
    first = label.split(" ")[0]
    return label if first.isupper() and len(first) > 1 else label[:1].lower() + label[1:]


def fields(section):
    return {f["key"]: f for f in SPEC[section].get("fields", [])}


def age_months(dob, on=None):
    """Whole months between DOB and ON (inclusive-bound friendly)."""
    on = on or _uk_today()
    return (on.year - dob.year) * 12 + (on.month - dob.month) - (1 if on.day < dob.day else 0)


# ---------------------------------------------------------------- validation


def clean(section, data, level):
    """Check DATA for SECTION at LEVEL. Returns only the section's own fields,
    tidied; raises Invalid({field: message}) listing every problem."""
    spec = SPEC[section]
    out, errors = {}, {}
    data = data or {}
    for f in spec.get("fields", []):
        if f.get("levels") and level not in f["levels"]:
            continue
        key, typ = f["key"], f["type"]
        raw = data.get(key)
        if isinstance(raw, (list, dict)):
            raw = None
        required = level in f["required"]
        if f.get("show_if") and data.get(f["show_if"][0]) != f["show_if"][1]:
            out[key] = None
            continue
        if typ == "checkbox":
            out[key] = 1 if raw in (True, 1, "1", "on", "yes") else 0
            continue
        value = "" if raw is None else str(raw).strip()
        if not value:
            if required:
                errors[key] = f.get("error") or (
                    "Choose an answer for \u201c%s\u201d." % f["label"] if typ in ("radio", "select")
                    else "Enter %s." % _lower_first(f["label"]))
            out[key] = None
            continue
        if typ in ("text", "textarea", "secret"):
            limit = f.get("max", 200)
            value = validate.long_text(value, limit) if typ == "textarea" else validate.text(value, limit)
            if typ == "secret" and len(value) < f.get("min", 1):
                errors[key] = "Use at least %d characters." % f["min"]
        elif typ == "tel":
            v = validate.uk_mobile(value)
            if not v:
                errors[key] = "Enter a UK mobile number, like 07700 900123."
            value = v
        elif typ == "phone":
            v = validate.uk_phone(value)
            if not v:
                errors[key] = "Enter a UK phone number."
            value = v
        elif typ == "postcode":
            v = validate.postcode(value)
            if not v:
                errors[key] = "Enter a UK postcode, like BH1 4SX."
            value = v
        elif typ == "date":
            d = validate.date(value)
            if not d or d > _uk_today() or d.year < 1900:
                errors[key] = "Enter a real date of birth."
            value = d.isoformat() if d else None
        elif typ in ("select", "radio"):
            allowed = [o[0] for o in f["options"]]
            if value not in allowed:
                errors[key] = "Choose one of the options."
        out[key] = value
    if errors:
        raise Invalid(errors)
    return out


def consent_keys(level, age_in_months=None):
    """The consent questions asked at LEVEL: [{key, required}].
    A question is asked if it's required at that level or is optional there
    (funder sharing); going home alone only once the child is old enough."""
    out = []
    for q in SPEC["consents"]["consents"]:
        required = level in q["required"]
        optional_here = not q["required"] and level in q.get("levels", ())
        if not (required or optional_here):
            continue
        if q.get("min_age_months") and (age_in_months is None or age_in_months < q["min_age_months"]):
            continue
        out.append({"key": q["key"], "required": required})
    return out


def public_spec():
    """SPEC plus the contact rules, for the portal's form renderer."""
    return {"sections": SPEC, "contacts": CONTACTS, "acknowledgements": list(ACKNOWLEDGEMENTS),
            "go_home_alone_min_months": GO_HOME_ALONE_MIN_MONTHS}


def _uk_today():
    from .catalogue import uk_today  # the charity's date, not the server's (UTC)
    return uk_today()
