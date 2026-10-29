"""Plain-text placeholders, one SMTP delivery, and a copy in Sent."""

import imaplib
import re
import smtplib
import ssl
import threading
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from typing import Protocol

from app.config import Settings

ALLOWED_PLACEHOLDERS = frozenset({"first_name", "name", "login", "email"})
CONNECTION_ERROR = "Could not connect to the mail server."
SENDER_ERROR = "The mail server refused the sender address."
DEFERRED_ERROR = "The mail server deferred this message."
REJECTED_ERROR = "The mail server rejected this message."
_TOKEN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_CONNECT_ATTEMPTS = 3
_CONNECT_BACKOFF = (0.5, 1.0)
_DROPPED_SESSION = (
    smtplib.SMTPConnectError,
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPHeloError,
)


class UnknownPlaceholder(ValueError):
    def __init__(self, names: list[str]) -> None:
        self.names = names
        shown = ", ".join(f"{{{{ {name} }}}}" for name in names)
        super().__init__(f"Unknown placeholder: {shown}")


class MailFailure(Exception):
    """One delivery failed. ``stop`` ends the whole campaign.

    ``connection`` means the message was not accepted. The campaign keeps
    running, leaves that recipient queued, and waits before trying again.
    """

    def __init__(self, message: str, *, stop: bool, connection: bool = False) -> None:
        super().__init__(message)
        self.stop = stop
        self.connection = connection


class CopyFailure(Exception):
    """The message was accepted, but the Sent copy was not stored."""


class Mailer(Protocol):
    def configured(self) -> bool:
        """True when a mailbox can accept messages."""

    def check(self) -> dict:
        """Log in without sending a message."""

    def send(self, *, recipient: str, subject: str, body: str) -> str | None:
        """Deliver one plain-text message. Return a Sent-copy error, if the copy failed."""

    def save_copy(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        sent_at: datetime | None,
    ) -> None:
        """File an already accepted message in Sent. Do not send it."""


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


def _is_connection_error(exc: BaseException) -> bool:
    """True when the session or socket failed, not when the server replied with an error.

    ``smtplib.SMTPException`` subclasses ``OSError``, so a bare ``except OSError``
    also catches every SMTP reply error, such as a rate limit on DATA.
    """

    if isinstance(exc, _DROPPED_SESSION):
        return True
    return isinstance(exc, OSError) and not isinstance(exc, smtplib.SMTPException)


def _connection_cause(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "timed out"
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused"
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return "the server closed the connection"
    if isinstance(exc, ssl.SSLError):
        return "the TLS handshake failed"
    if isinstance(exc, (smtplib.SMTPServerDisconnected, smtplib.SMTPHeloError)):
        return "the server closed the connection"
    if isinstance(exc, smtplib.SMTPConnectError):
        return "connection refused"
    text = str(exc).lower()
    if "getaddrinfo" in text or "name or service" in text or "nodename" in text:
        return "could not resolve the host"
    if "timed out" in text or "timeout" in text:
        return "timed out"
    if "refused" in text:
        return "connection refused"
    return f"could not reach the server, {type(exc).__name__}"


def _server_reply(exc: smtplib.SMTPException) -> tuple[int | None, str]:
    code = getattr(exc, "smtp_code", None)
    text = getattr(exc, "smtp_error", None)
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    reply = " ".join(str(text or "").split())
    if not reply and not code:
        reply = " ".join(str(exc).split())
    return (code if isinstance(code, int) else None), reply


def _sender_failure(exc: smtplib.SMTPSenderRefused) -> MailFailure:
    _, reply = _server_reply(exc)
    shown = f"{exc.smtp_code} {reply}".strip()
    detail = f"{SENDER_ERROR} ({shown})" if shown else SENDER_ERROR
    return MailFailure(detail[:300], stop=False, connection=True)


def _rejection(exc: smtplib.SMTPException) -> MailFailure:
    """The server answered and said no. Keep its reply instead of calling it a dropped connection.

    A 4xx reply is temporary: the recipient stays queued and is tried again later.
    A 5xx reply is final for this recipient only.
    """

    code, reply = _server_reply(exc)
    shown = f"{code} {reply}".strip() if code else reply
    if code is not None and 400 <= code < 500:
        detail = f"{DEFERRED_ERROR} ({shown})" if shown else DEFERRED_ERROR
        return MailFailure(detail[:300], stop=False, connection=True)
    detail = f"{REJECTED_ERROR} ({shown})" if shown else REJECTED_ERROR
    return MailFailure(detail[:300], stop=False)


def _with_mailbox(settings: Settings, connect, action, sleeper=None) -> None:
    opener = connect or _connect
    pause = sleeper or time.sleep
    for attempt in range(_CONNECT_ATTEMPTS):
        try:
            with opener(settings) as client:
                client.login(settings.smtp_username, settings.smtp_password)
                action(client)
            return
        except smtplib.SMTPAuthenticationError as exc:
            raise MailFailure("The mail server refused the mailbox login.", stop=True) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise MailFailure("The mail server refused this recipient.", stop=False) from exc
        except smtplib.SMTPSenderRefused as exc:
            raise _sender_failure(exc) from exc
        except OSError as exc:
            if not _is_connection_error(exc):
                raise _rejection(exc) from exc
            if attempt + 1 < _CONNECT_ATTEMPTS:
                pause(_CONNECT_BACKOFF[attempt])
                continue
            cause = _connection_cause(exc)
            raise MailFailure(f"{CONNECTION_ERROR} ({cause})", stop=True, connection=True) from exc


_SENT_FALLBACK = ("INBOX.Sent", "Sent", "Sent Items")
_QUOTED_NAME = re.compile(r'"((?:\\.|[^"])*)"\s*$')


def _message(
    settings: Settings,
    *,
    recipient: str,
    subject: str,
    body: str,
    sent_at: datetime | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message["Subject"] = subject
    stamp = sent_at or datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    message["Date"] = format_datetime(stamp)
    message["Message-ID"] = make_msgid()
    message.set_content(body)
    return message


def _imap_connect(settings: Settings):
    if settings.imap_ssl:
        return imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port, timeout=30)
    return imaplib.IMAP4(settings.imap_host, settings.imap_port, timeout=30)


def _folder_name(entry: object) -> tuple[str, str]:
    if isinstance(entry, tuple):
        entry = entry[0] if entry else b""
    text = entry.decode("utf-8", "replace") if isinstance(entry, bytes) else str(entry)
    flags = ""
    if text.startswith("("):
        end = text.find(")")
        if end != -1:
            flags = text[: end + 1]
    quoted = _QUOTED_NAME.search(text)
    if quoted:
        name = quoted.group(1).replace(r"\"", '"')
    else:
        parts = text.split()
        name = parts[-1] if parts else ""
    return flags, name


def _internaldate(stamp: datetime) -> str:
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC).strftime('"%d-%b-%Y %H:%M:%S +0000"')


def _sent_folder(entries: list | None) -> str:
    names: list[str] = []
    for entry in entries or []:
        if entry is None:
            continue
        flags, name = _folder_name(entry)
        if not name:
            continue
        names.append(name)
        if re.search(r"\\Sent\b", flags):
            return name
    for candidate in _SENT_FALLBACK:
        if candidate in names:
            return candidate
    raise CopyFailure("The mailbox has no Sent folder.")


def _with_imap(settings: Settings, connect, action):
    opener = connect or _imap_connect
    try:
        client = opener(settings)
    except (TimeoutError, OSError) as exc:
        raise CopyFailure("Could not connect to the mail server.") from exc
    try:
        try:
            client.login(settings.smtp_username, settings.smtp_password)
        except imaplib.IMAP4.error as exc:
            raise CopyFailure("The mail server refused the mailbox login.") from exc
        except (TimeoutError, OSError) as exc:
            raise CopyFailure("Could not connect to the mail server.") from exc
        return action(client)
    finally:
        logout = getattr(client, "logout", None)
        if logout is not None:
            try:
                logout()
            except (imaplib.IMAP4.error, OSError, TimeoutError):
                pass


def _read_sent_folder(client) -> str:
    try:
        typ, data = client.list()
    except imaplib.IMAP4.error as exc:
        raise CopyFailure("Could not read the mailbox folders.") from exc
    if typ != "OK":
        raise CopyFailure("Could not read the mailbox folders.")
    return _sent_folder(data)


def append_message(
    settings: Settings,
    message: EmailMessage,
    *,
    connect=None,
    sent_at: datetime | None = None,
) -> str:
    """Upload one message to Sent. Return the folder name."""

    raw = message.as_bytes()
    internaldate = _internaldate(sent_at or datetime.now(UTC))

    def action(client) -> str:
        folder = _read_sent_folder(client)
        try:
            status, _ = client.append(folder, "\\Seen", internaldate, raw)
        except imaplib.IMAP4.error as exc:
            raise CopyFailure("Could not save the message in Sent.") from exc
        if status != "OK":
            raise CopyFailure("Could not save the message in Sent.")
        return folder

    return _with_imap(settings, connect, action)


def save_copy(
    settings: Settings,
    *,
    recipient: str,
    subject: str,
    body: str,
    sent_at: datetime | None,
    connect=None,
) -> None:
    """File one already accepted message. No SMTP connection is opened."""

    if not mail_configured(settings):
        raise CopyFailure("Mail is not configured.")
    message = _message(
        settings,
        recipient=recipient,
        subject=subject,
        body=body,
        sent_at=sent_at,
    )
    append_message(settings, message, connect=connect, sent_at=sent_at)


def send_message(
    settings: Settings,
    *,
    recipient: str,
    subject: str,
    body: str,
    connect=None,
    imap_connect=None,
) -> str | None:
    """Send one message. Authentication and connection errors stop the campaign.

    A Sent-copy failure is returned and does not stop the campaign.
    """

    message = _message(settings, recipient=recipient, subject=subject, body=body)
    _with_mailbox(settings, connect, lambda client: client.send_message(message))
    try:
        append_message(settings, message, connect=imap_connect)
    except CopyFailure as exc:
        return str(exc)[:300]
    return None


def check_mailbox(settings: Settings, *, connect=None, imap_connect=None, sleeper=None) -> dict:
    """Log into SMTP and IMAP. No message is sent."""

    if not mail_configured(settings):
        return {"ok": False, "detail": "Mail is not configured."}
    try:
        _with_mailbox(settings, connect, lambda client: None, sleeper)
    except MailFailure as exc:
        return {"ok": False, "detail": str(exc)}
    try:
        folder = _with_imap(settings, imap_connect, _read_sent_folder)
    except CopyFailure as exc:
        return {"ok": False, "detail": str(exc)}
    return {
        "ok": True,
        "from": settings.smtp_from.strip(),
        "host": settings.smtp_host,
        "port": settings.smtp_port,
        "sent_folder": folder,
    }


class SmtpMailer:
    """One SMTP session and one IMAP session, shared by every send."""

    def __init__(self, settings: Settings, *, connect=None, imap_connect=None) -> None:
        self.settings = settings
        self._connect = connect or _connect
        self._imap_connect = imap_connect or _imap_connect
        self._lock = threading.Lock()
        self._smtp = None
        self._imap = None
        self._sent_folder: str | None = None

    def configured(self) -> bool:
        return mail_configured(self.settings)

    def check(self) -> dict:
        return check_mailbox(self.settings)

    def send(self, *, recipient: str, subject: str, body: str) -> str | None:
        if not mail_configured(self.settings):
            raise MailFailure("Mail is not configured.", stop=True)
        message = _message(self.settings, recipient=recipient, subject=subject, body=body)
        with self._lock:
            self._send_smtp(message)
            try:
                self._append_copy(message)
            except CopyFailure as exc:
                return str(exc)[:300]
        return None

    def save_copy(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        sent_at: datetime | None,
    ) -> None:
        if not mail_configured(self.settings):
            raise CopyFailure("Mail is not configured.")
        message = _message(
            self.settings,
            recipient=recipient,
            subject=subject,
            body=body,
            sent_at=sent_at,
        )
        with self._lock:
            self._append_copy(message, sent_at)

    def _send_smtp(self, message: EmailMessage) -> None:
        for attempt in range(2):
            try:
                self._deliver(message)
                return
            except MailFailure as exc:
                if not exc.connection:
                    raise
                self._close_smtp()
                if attempt == 1:
                    raise
            except OSError as exc:
                self._close_smtp()
                if not _is_connection_error(exc):
                    raise _rejection(exc) from exc
                if attempt == 1:
                    cause = _connection_cause(exc)
                    raise MailFailure(
                        f"{CONNECTION_ERROR} ({cause})",
                        stop=True,
                        connection=True,
                    ) from exc

    def _deliver(self, message: EmailMessage) -> None:
        client = self._smtp_client()
        try:
            client.send_message(message)
        except smtplib.SMTPRecipientsRefused as exc:
            raise MailFailure("The mail server refused this recipient.", stop=False) from exc
        except smtplib.SMTPSenderRefused as exc:
            raise _sender_failure(exc) from exc
        except smtplib.SMTPException as exc:
            if _is_connection_error(exc):
                raise
            raise _rejection(exc) from exc

    def _smtp_client(self):
        if self._smtp is not None:
            return self._smtp
        client = self._connect(self.settings)
        try:
            client.login(self.settings.smtp_username, self.settings.smtp_password)
        except smtplib.SMTPAuthenticationError as exc:
            _close_client(client)
            raise MailFailure("The mail server refused the mailbox login.", stop=True) from exc
        except OSError:
            _close_client(client)
            raise
        self._smtp = client
        return client

    def _close_smtp(self) -> None:
        client = self._smtp
        self._smtp = None
        _close_client(client)

    def _append_copy(self, message: EmailMessage, sent_at: datetime | None = None) -> None:
        raw = message.as_bytes()
        internaldate = _internaldate(sent_at or datetime.now(UTC))
        for attempt in range(2):
            try:
                client = self._imap_client()
                folder = self._sent_folder_name(client)
                status, _ = client.append(folder, "\\Seen", internaldate, raw)
                if status != "OK":
                    raise CopyFailure("Could not save the message in Sent.")
                return
            except CopyFailure:
                raise
            except (imaplib.IMAP4.abort, TimeoutError, OSError) as exc:
                self._close_imap()
                if attempt == 1:
                    raise CopyFailure(f"{CONNECTION_ERROR} ({_connection_cause(exc)})") from exc
            except imaplib.IMAP4.error as exc:
                raise CopyFailure("Could not save the message in Sent.") from exc

    def _sent_folder_name(self, client) -> str:
        if self._sent_folder is not None:
            return self._sent_folder
        try:
            typ, data = client.list()
        except (imaplib.IMAP4.abort, TimeoutError, OSError):
            raise
        except imaplib.IMAP4.error as exc:
            raise CopyFailure("Could not read the mailbox folders.") from exc
        if typ != "OK":
            raise CopyFailure("Could not read the mailbox folders.")
        self._sent_folder = _sent_folder(data)
        return self._sent_folder

    def _imap_client(self):
        if self._imap is not None:
            return self._imap
        try:
            client = self._imap_connect(self.settings)
        except (TimeoutError, OSError):
            raise
        try:
            client.login(self.settings.smtp_username, self.settings.smtp_password)
        except imaplib.IMAP4.error as exc:
            _close_client(client, "logout")
            raise CopyFailure("The mail server refused the mailbox login.") from exc
        except (TimeoutError, OSError):
            _close_client(client, "logout")
            raise
        self._imap = client
        return client

    def _close_imap(self) -> None:
        client = self._imap
        self._imap = None
        self._sent_folder = None
        _close_client(client, "logout")


def _close_client(client, method: str = "quit") -> None:
    if client is None:
        return
    closer = getattr(client, method, None) or getattr(client, "close", None)
    if closer is None:
        return
    try:
        closer()
    except (smtplib.SMTPException, imaplib.IMAP4.error, OSError, TimeoutError):
        pass
