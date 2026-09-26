"""Sending email over SMTP (stdlib smtplib), always with a verified TLS
certificate.

Settings come from the host's environment (never content.json):
  SMTP_HOST, SMTP_PORT (587 = STARTTLS, 465 = TLS from the start),
  SMTP_USER, SMTP_PASSWORD, MAIL_FROM ("Honeycombe Arts Hub <bookings@…>"),
  MAIL_REPLY_TO, STAFF_NOTIFY_TO (where contact-form messages go).
Older sites still using the SMTP box in admin → Settings keep working until
the environment variables are set (the admin shows a reminder).

MAIL_BACKEND=memory keeps messages in SENT instead (tests); MAIL_BACKEND=file
writes each one to data/mail-outbox/ as an .eml file (local development)."""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr

from .storage import load_json

SENT = []  # the memory backend's outbox


class MailError(Exception):
    def __init__(self, message, permanent=False):
        super().__init__(message)
        self.permanent = permanent


def settings():
    """The SMTP settings in use: environment first, else the old admin box."""
    env = os.environ
    if env.get("SMTP_HOST"):
        return {"host": env["SMTP_HOST"], "port": int(env.get("SMTP_PORT") or 587),
                "user": env.get("SMTP_USER", ""), "password": env.get("SMTP_PASSWORD", ""),
                "from": env.get("MAIL_FROM") or env.get("SMTP_USER", ""),
                "reply_to": env.get("MAIL_REPLY_TO", ""), "source": "environment"}
    site = (load_json("content.json", {}) or {}).get("settings", {})
    old = site.get("smtp") or {}
    if old.get("host"):
        return {"host": old["host"], "port": int(old.get("port") or 587), "user": old.get("user", ""),
                "password": old.get("password", ""), "from": old.get("user") or site.get("emailGeneral", ""),
                "reply_to": site.get("emailGeneral", ""), "source": "admin settings"}
    return None


def backend():
    return os.environ.get("MAIL_BACKEND", "smtp")


def configured():
    return backend() in ("memory", "file") or settings() is not None


def staff_notify_address():
    site = (load_json("content.json", {}) or {}).get("settings", {})
    return (os.environ.get("STAFF_NOTIFY_TO") or (site.get("smtp") or {}).get("notifyTo")
            or site.get("emailGeneral") or "")


def _one_line(value):
    """Header values can't contain line breaks (they'd let a form add headers)."""
    return " ".join(str(value or "").split())


def build(to, subject, text, html=None, to_name=None, headers=None, cfg=None):
    cfg = cfg or settings() or {"from": "Honeycombe Arts Hub <no-reply@localhost>", "reply_to": ""}
    msg = EmailMessage()
    msg["Subject"] = _one_line(subject)
    msg["From"] = _one_line(cfg["from"])
    msg["To"] = formataddr((_one_line(to_name or ""), _one_line(to)))
    if cfg.get("reply_to"):
        msg["Reply-To"] = _one_line(cfg["reply_to"])
    msg["Date"] = formatdate(localtime=False)
    domain = parseaddr(cfg["from"])[1].partition("@")[2] or None
    msg["Message-ID"] = make_msgid(domain=domain)
    for k, v in (headers or {}).items():
        msg[_one_line(k)] = _one_line(v)
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


class Connection:
    """One SMTP connection for a batch of messages."""

    def __init__(self):
        self.cfg = settings()
        self.smtp = None

    def __enter__(self):
        if backend() in ("memory", "file"):
            return self
        if not self.cfg:
            raise MailError("email isn't set up (SMTP_HOST)")
        ctx = ssl.create_default_context()  # verifies the server's certificate and name
        try:
            if self.cfg["port"] == 465:
                self.smtp = smtplib.SMTP_SSL(self.cfg["host"], 465, timeout=20, context=ctx)
            else:
                self.smtp = smtplib.SMTP(self.cfg["host"], self.cfg["port"], timeout=20)
                self.smtp.starttls(context=ctx)
            if self.cfg["user"] and self.cfg["password"]:
                self.smtp.login(self.cfg["user"], self.cfg["password"])
        except (OSError, smtplib.SMTPException) as e:
            raise MailError("couldn't connect to the mail server: %s" % e)
        return self

    def send(self, msg):
        if backend() == "memory":
            SENT.append(msg)
            return msg["Message-ID"]
        if backend() == "file":
            import time
            from . import config
            folder = os.path.join(config.DATA, "mail-outbox")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "%d.eml" % time.time_ns()), "wb") as f:
                f.write(msg.as_bytes())
            return msg["Message-ID"]
        try:
            self.smtp.send_message(msg)
        except smtplib.SMTPRecipientsRefused as e:
            raise MailError("recipient refused: %s" % e, permanent=True)
        except (OSError, smtplib.SMTPException) as e:
            raise MailError(str(e))
        return msg["Message-ID"]

    def __exit__(self, *exc):
        if self.smtp:
            try:
                self.smtp.quit()
            except (OSError, smtplib.SMTPException):
                pass
