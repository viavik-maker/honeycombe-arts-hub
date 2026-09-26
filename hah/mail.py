"""Optional email notifications (contact-form messages to the team)."""
import smtplib
from email.message import EmailMessage


def try_send_email(settings, subject, body):
    smtp = settings.get("smtp") or {}
    host, user = smtp.get("host"), smtp.get("user")
    to = smtp.get("notifyTo") or settings.get("emailGeneral")
    if not host or not to:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = user or to
        msg["To"] = to
        msg.set_content(body)
        with smtplib.SMTP(host, int(smtp.get("port") or 587), timeout=8) as s:
            s.starttls()
            if user and smtp.get("password"):
                s.login(user, smtp["password"])
            s.send_message(msg)
        return True
    except Exception as e:  # email is best-effort; message is stored regardless
        print(f"[mail] send failed: {e}")
        return False
