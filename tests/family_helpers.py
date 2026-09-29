"""Helpers for tests that need registered families."""
import re

from hah import db, mail, outbox
from tests.support import Client

PASSWORD = "our family passphrase"
_n = [0]


def last_email_to(address):
    """Send the outbox and return the plain text of the newest email to ADDRESS."""
    outbox.send_due()
    for msg in reversed(mail.SENT):
        if address.lower() in msg["To"].lower():
            return msg.get_body(("plain",)).get_content()
    raise AssertionError("no email to %s" % address)


def register_family(kind="family", email=None, **extra):
    """Register and verify an account through the API. Returns a signed-in Client."""
    _n[0] += 1
    email = email or "parent%d@example.org" % _n[0]
    c = Client()
    body = {"kind": kind, "first_name": "Sarah", "last_name": "Parent%d" % _n[0], "email": email,
            "mobile": "07700 900%03d" % (_n[0] % 1000), "postcode": "BH1 4SX", "password": PASSWORD}
    body.update(extra)
    r = c.post_json("/api/account/register", body)
    assert r.status == 200, r.text
    code = re.search(r"\b(\d{6})\b", last_email_to(email)).group(1)
    r = c.post_json("/api/account/register/verify", {"email": email, "code": code, "password": body["password"]})
    assert r.status == 200, r.text
    c.csrf = r.json()["csrf"]
    c.email = email
    return c


def complete_child(c, first_name="Maya", dob="2018-05-10", level="full"):
    """Add a child and fill in everything LEVEL needs. Returns the child's ref."""
    r = c.post_json("/api/account/participants", {"first_name": first_name, "last_name": "Parent", "dob": dob,
                                                  "target_level": level})
    assert r.status == 200, r.text
    ref = r.json()["ref"]
    if level == "full":
        c.post_json("/api/account/details", {"first_name": "Sarah", "last_name": "Parent", "mobile": "07700900001",
                                             "address_line1": "1 High St", "town": "Bournemouth", "postcode": "BH1 4SX"})
        c.post_json("/api/account/contacts", {"contacts": [
            {"full_name": "Grandma Jo", "relationship": "Grandmother", "phone": "01202 123456", "can_collect": True},
            {"full_name": "Uncle Tom", "relationship": "Uncle", "phone": "07700 900555"}]})
        ok(c.post_json("/api/account/participants/%s/child" % ref, {
            "first_name": first_name, "last_name": "Parent", "dob": dob, "education": "school",
            "school_name": "Boscombe Primary", "haf_status": "not_eligible"}))
        ok(c.post_json("/api/account/participants/%s/gp" % ref, {"surgery_name": "Boscombe Surgery",
                                                                 "surgery_phone": "01202 000000"}))
        ok(c.post_json("/api/account/participants/%s/safeguarding" % ref, {}))
        ok(c.post_json("/api/account/participants/%s/collection" % ref, {"password": "blue tiger", "confirm": "blue tiger"}))
        answers = {"photo": "internal", "first_aid": "yes", "plasters": "yes", "emergency_treatment": "yes",
                   "go_home_alone": "no"}
    else:
        answers = {"photo": "none"}
    ok(c.post_json("/api/account/participants/%s/health" % ref, {"allergies": "Peanuts", "anaphylaxis": True}))
    ok(c.post_json("/api/account/participants/%s/consents" % ref, {"answers": answers}))
    ok(c.post_json("/api/account/acknowledge", {"info_correct": True, "privacy_ack": True}))
    return ref


def ok(r):
    assert r.status == 200, r.text
    return r


def participant_id(ref):
    with db.read() as c:
        return c.execute("SELECT id FROM participants WHERE ref=?", (ref,)).fetchone()[0]
