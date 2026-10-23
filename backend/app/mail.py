"""Plain-text placeholders and one SMTP delivery."""

import re
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.config import Settings

ALLOWED_PLACEHOLDERS = frozenset({"first_name", "name", "login", "email"})
_TOKEN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


class UnknownPlaceholder(ValueError):
    def __init__(self, names: list[str]) -> None:
        self.names = names
        shown = ", ".join(f"{{{{ {name} }}}}" for name in names)
        super().__init__(f"Unknown placeholder: {shown}")


class MailFailure(Exception):
    """One delivery failed. ``stop`` ends the whole campaign."""

    def __init__(self, message: str, *, stop: bool) -> None:
        super().__init__(message)
        self.stop = stop


class Mailer(Protocol):
    def configured(self) -> bool:
        """True when a mailbox can accept messages."""

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        """Deliver one plain-text message."""


def unknown_placeholders(*templates: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for template in templates:
        for match in _TOKEN.finditer(template):
            name = match.group(1)
            if name in ALLOWED_PLACEHOLDERS or name in seen:
                continue
            seen.add(name)
            found.append(name)
    return found


def placeholder_values(*, name: str | None, login: str, email: str) -> dict[str, str]:
    """Values substituted into a subject or body. The email is the recipient."""

    cleaned = " ".join((name or "").split())
    return {
        "first_name": cleaned.split(" ", 1)[0] if cleaned else login,
        "name": cleaned or login,
        "login": login,
        "email": email,
    }


def render_template(template: str, values: dict[str, str]) -> str:
    unknown = unknown_placeholders(template)
    if unknown:
        raise UnknownPlaceholder(unknown)

    def replace(match: re.Match[str]) -> str:
        return values[match.group(1)]

    return _TOKEN.sub(replace, template)


def mail_configured(settings: Settings) -> bool:
    return bool(
        settings.smtp_username.strip() and settings.smtp_password and settings.smtp_from.strip()
    )


def _connect(settings: Settings):
    if settings.smtp_ssl:
        return smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30)
    client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    client.starttls()
    return client


def send_message(
    settings: Settings,
    *,
    recipient: str,
    subject: str,
    body: str,
    connect=None,
) -> None:
    """Send one message. Authentication and connection errors stop the campaign."""

    opener = connect or _connect
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    try:
        with opener(settings) as client:
            client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailFailure("The mail server refused the mailbox login.", stop=True) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailFailure("The mail server refused this recipient.", stop=False) from exc
    except smtplib.SMTPSenderRefused as exc:
        raise MailFailure("The mail server refused the sender address.", stop=True) from exc
    except (
        smtplib.SMTPConnectError,
        smtplib.SMTPServerDisconnected,
        smtplib.SMTPHeloError,
    ) as exc:
        raise MailFailure("Could not connect to the mail server.", stop=True) from exc
    except smtplib.SMTPException as exc:
        detail = str(exc)[:300] or "The mail server rejected this message."
        raise MailFailure(detail, stop=False) from exc
    except (TimeoutError, OSError) as exc:
        raise MailFailure("Could not connect to the mail server.", stop=True) from exc


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def configured(self) -> bool:
        return mail_configured(self.settings)

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        send_message(self.settings, recipient=recipient, subject=subject, body=body)
