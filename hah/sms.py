"""Text messages, behind a small provider interface so the provider can be
swapped later.

SMS_PROVIDER=twilio | log | disabled (default disabled)
  twilio: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_FROM (an
          alphanumeric sender such as "HoneycombeH", or a number) or
          TWILIO_MESSAGING_SERVICE_SID
  log:    writes messages to the log and keeps them in SENT (dev/tests)

Only UK mobile numbers are texted. Never put health or safeguarding
details in a text: they pass through the provider's systems."""
import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SENT = []


class SmsError(Exception):
    def __init__(self, message, permanent=False, opted_out=False):
        super().__init__(message)
        self.permanent, self.opted_out = permanent, opted_out


def provider_name():
    return os.environ.get("SMS_PROVIDER", "disabled")


def configured():
    name = provider_name()
    if name == "log":
        return True
    if name == "twilio":
        env = os.environ
        return bool(env.get("TWILIO_ACCOUNT_SID") and env.get("TWILIO_AUTH_TOKEN")
                    and (env.get("TWILIO_FROM") or env.get("TWILIO_MESSAGING_SERVICE_SID")))
    return False


def send(to_e164, body):
    """Send one text; returns the provider's message id."""
    name = provider_name()
    if name == "log":
        SENT.append((to_e164, body))
        sys.stderr.write("[sms] to %s…%s: %d characters\n" % (to_e164[:4], to_e164[-3:], len(body)))
        return "log-%d" % len(SENT)
    if name == "twilio" and configured():
        return _twilio(to_e164, body)
    raise SmsError("text messages aren't set up (SMS_PROVIDER)")


def _twilio(to, body):
    env = os.environ
    sid = env["TWILIO_ACCOUNT_SID"]
    base = env.get("TWILIO_API_BASE", "https://api.twilio.com")
    form = {"To": to, "Body": body}
    callback = status_callback_url()
    if callback:
        form["StatusCallback"] = callback
    if env.get("TWILIO_MESSAGING_SERVICE_SID"):
        form["MessagingServiceSid"] = env["TWILIO_MESSAGING_SERVICE_SID"]
    else:
        form["From"] = env["TWILIO_FROM"]
    auth = base64.b64encode(("%s:%s" % (sid, env["TWILIO_AUTH_TOKEN"])).encode()).decode()
    req = urllib.request.Request(
        "%s/2010-04-01/Accounts/%s/Messages.json" % (base.rstrip("/"), urllib.parse.quote(sid)),
        data=urllib.parse.urlencode(form).encode(), method="POST",
        headers={"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode()).get("sid", "")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
        except ValueError:
            detail = {}
        code = detail.get("code")
        if code == 21610:  # the person replied STOP to this sender
            raise SmsError("recipient has opted out of texts", permanent=True, opted_out=True)
        permanent = 400 <= e.code < 500 and e.code != 429
        raise SmsError("Twilio %s: %s" % (code or e.code, detail.get("message", e.reason)), permanent=permanent)
    except OSError as e:
        raise SmsError("couldn't reach Twilio: %s" % e)


# ---------------------------------------------------------------- delivery reports and replies


def status_callback_url():
    """Where Twilio reports delivery (only when the site has a public https address)."""
    from . import config
    base = (config.SITE_URL or "").rstrip("/")
    return base + "/api/twilio/status" if base.startswith("https://") else None


def verify_twilio(url, params, signature):
    """Twilio signs each request: base64(HMAC-SHA1(auth token, URL + every POST field and value, sorted))."""
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not token or not signature:
        return False
    payload = url + "".join(k + v for k, v in sorted(params.items()))
    expected = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, signature)
