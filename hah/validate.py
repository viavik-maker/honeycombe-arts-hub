"""Checking and tidying what people type into forms."""
import datetime
import re

EMAIL_RE = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[A-Za-z]{2,}$")
POSTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$")


class Invalid(ValueError):
    """Form problems: {field: message}. Routes turn it into a 422 response."""

    def __init__(self, errors):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


def _str(value):
    """VALUE as text: numbers are written out; anything else that isn't text (lists, objects) counts as blank."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def email(value):
    v = _str(value).strip().lower()
    return v if len(v) <= 200 and EMAIL_RE.match(v) else None


def uk_mobile(value):
    """A UK mobile number in international form (+447…), or None.
    Accepts 07… , +447… , 447… and 00447… with spaces, dashes or brackets."""
    digits = re.sub(r"[\s\-().]", "", _str(value))
    if digits.startswith("+"):
        digits = digits[1:]
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = "44" + digits[1:]
    if re.fullmatch(r"447\d{9}", digits):
        return "+" + digits
    return None


def uk_phone(value):
    """Any UK phone number, tidied (mobile or landline), or None."""
    raw = re.sub(r"[\s\-().]", "", _str(value))
    if raw.startswith("+44"):
        raw = "0" + raw[3:]
    elif raw.startswith("0044"):
        raw = "0" + raw[4:]
    return raw if re.fullmatch(r"0\d{9,10}", raw) else None


def postcode(value):
    v = re.sub(r"\s+", "", _str(value).upper())
    if not POSTCODE_RE.match(v):
        return None
    return v[:-3] + " " + v[-3:]


def date(value):
    """'YYYY-MM-DD' -> datetime.date, or None."""
    try:
        return datetime.date.fromisoformat(_str(value).strip())
    except ValueError:
        return None


def text(value, limit=200):
    return " ".join(_str(value).split())[:limit]


def long_text(value, limit=5000):
    return _str(value).strip().replace("\r\n", "\n")[:limit]
