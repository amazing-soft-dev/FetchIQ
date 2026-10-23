import imaplib
import smtplib

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.mail import (
    MailFailure,
    check_mailbox,
    placeholder_values,
    render_template,
    save_copy,
    send_message,
    unknown_placeholders,
)
from app.main import create_app


def test_placeholders_use_the_first_word_and_fall_back_to_the_login() -> None:
    values = placeholder_values(name="Ada Lovelace", login="ada", email="ada@example.com")
    assert render_template("Hello {{ first_name }}", values) == "Hello Ada"
    assert render_template("{{ name }} <{{ email }}>", values) == "Ada Lovelace <ada@example.com>"
    unnamed = placeholder_values(name="  ", login="grace", email="grace@example.com")
    assert unnamed["first_name"] == "grace"
    assert unnamed["name"] == "grace"
    assert render_template("{{login}}", unnamed) == "grace"


def test_unknown_placeholder_is_rejected() -> None:
    assert unknown_placeholders("Hello {{ frist_name }}") == ["frist_name"]
    with pytest.raises(ValueError, match="frist_name"):
        render_template("Hello {{ frist_name }}", {"frist_name": "Ada"})


class _Client:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.messages = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def login(self, username: str, password: str) -> None:
        return None

    def send_message(self, message) -> None:
        if self.fail is not None:
            raise self.fail
        self.messages.append(message)


def _settings() -> Settings:
    return Settings(
        database_name="fetchiq_test",
        smtp_username="mailer",
        smtp_password="secret",
        smtp_from="Ada <ada@example.com>",
    )


def test_recipient_refusal_does_not_stop_the_campaign() -> None:
    client = _Client(smtplib.SMTPRecipientsRefused({}))

    def connect(settings):
        return client

    with pytest.raises(MailFailure) as caught:
        send_message(
            _settings(),
            recipient="ada@example.com",
            subject="Hello",
            body="Hi",
            connect=connect,
        )
    assert caught.value.stop is False


def test_authentication_failure_stops_the_campaign() -> None:
    client = _Client(smtplib.SMTPAuthenticationError(535, b"no"))

    def connect(settings):
        return client

    with pytest.raises(MailFailure) as caught:
        send_message(
            _settings(),
            recipient="ada@example.com",
            subject="Hello",
            body="Hi",
            connect=connect,
        )
    assert caught.value.stop is True


def _unconfigured() -> Settings:
    return Settings(
        database_name="fetchiq_test",
        smtp_username="",
        smtp_password="",
        smtp_from="",
    )


def test_blank_mailbox_is_not_connected() -> None:
    def connect(settings):
        raise AssertionError("should not connect")

    result = check_mailbox(_unconfigured(), connect=connect)
    assert result == {"ok": False, "detail": "Mail is not configured."}


class _Imap:
    def __init__(self, fail: Exception | None = None, folders: list | None = None) -> None:
        self.fail = fail
        self.folders = folders or [b'(\\HasNoChildren \\Sent) "." "INBOX.Sent"']
        self.appended: list[tuple] = []

    def login(self, username: str, password: str) -> None:
        if isinstance(self.fail, imaplib.IMAP4.error):
            raise self.fail

    def list(self):
        return "OK", self.folders

    def append(self, mailbox, flags, date_time, message):
        self.appended.append((mailbox, message))
        return "OK", [b"ok"]

    def logout(self):
        return "BYE", [b""]


def _imap(client: _Imap):
    def connect(settings):
        if isinstance(client.fail, TimeoutError):
            raise client.fail
        return client

    return connect


def test_login_reports_the_mailbox_without_sending() -> None:
    client = _Client()
    imap = _Imap()

    def connect(settings):
        return client

    result = check_mailbox(_settings(), connect=connect, imap_connect=_imap(imap))
    assert result == {
        "ok": True,
        "from": "Ada <ada@example.com>",
        "host": "smtp.hostinger.com",
        "port": 465,
        "sent_folder": "INBOX.Sent",
    }
    assert client.messages == []
    assert imap.appended == []
    assert "password" not in result


def test_refused_login_is_not_connected() -> None:
    client = _Client()

    def login(username: str, password: str) -> None:
        raise smtplib.SMTPAuthenticationError(535, b"no")

    client.login = login

    def connect(settings):
        return client

    result = check_mailbox(_settings(), connect=connect)
    assert result == {"ok": False, "detail": "The mail server refused the mailbox login."}
    assert client.messages == []


def test_connection_failure_is_not_connected() -> None:
    class Closed:
        def __enter__(self):
            raise TimeoutError("timed out")

        def __exit__(self, *args):
            return False

    def connect(settings):
        return Closed()

    result = check_mailbox(_settings(), connect=connect)
    assert result == {"ok": False, "detail": "Could not connect to the mail server."}


async def test_mail_check_endpoint_reports_missing_configuration(db) -> None:
    app = create_app(settings=_unconfigured(), db=db)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/mail/check")
    assert response.status_code == 200
    assert response.json() == {"ok": False, "detail": "Mail is not configured."}


def test_accepted_message_is_appended_once() -> None:
    smtp = _Client()
    imap = _Imap()

    def connect(settings):
        return smtp

    error = send_message(
        _settings(),
        recipient="ada@example.com",
        subject="Hello",
        body="Hi",
        connect=connect,
        imap_connect=_imap(imap),
    )
    assert error is None
    assert len(smtp.messages) == 1
    folder, raw = imap.appended[0]
    assert folder == "INBOX.Sent"
    assert raw == smtp.messages[0].as_bytes()
    assert b"Message-ID:" in raw


def test_refused_recipient_is_not_appended() -> None:
    smtp = _Client(smtplib.SMTPRecipientsRefused({}))
    imap = _Imap()

    def connect(settings):
        return smtp

    with pytest.raises(MailFailure):
        send_message(
            _settings(),
            recipient="ada@example.com",
            subject="Hello",
            body="Hi",
            connect=connect,
            imap_connect=_imap(imap),
        )
    assert imap.appended == []


def test_missing_sent_folder_does_not_undo_the_send() -> None:
    smtp = _Client()
    imap = _Imap(folders=[b'(\\HasNoChildren) "." "INBOX"'])

    def connect(settings):
        return smtp

    error = send_message(
        _settings(),
        recipient="ada@example.com",
        subject="Hello",
        body="Hi",
        connect=connect,
        imap_connect=_imap(imap),
    )
    assert error == "The mailbox has no Sent folder."
    assert len(smtp.messages) == 1
    assert imap.appended == []


def test_plain_sent_folder_name_is_used() -> None:
    imap = _Imap(folders=[b'(\\HasNoChildren) "." "Sent"'])
    result = check_mailbox(
        _settings(),
        connect=lambda settings: _Client(),
        imap_connect=_imap(imap),
    )
    assert result["ok"] is True
    assert result["sent_folder"] == "Sent"


def test_imap_login_failure_is_not_connected() -> None:
    imap = _Imap(fail=imaplib.IMAP4.error("AUTHENTICATIONFAILED"))
    result = check_mailbox(
        _settings(),
        connect=lambda settings: _Client(),
        imap_connect=_imap(imap),
    )
    assert result == {"ok": False, "detail": "The mail server refused the mailbox login."}


def test_save_copy_does_not_open_smtp() -> None:
    imap = _Imap()
    save_copy(
        _settings(),
        recipient="ada@example.com",
        subject="Hello Ada",
        body="Hi ada",
        sent_at=None,
        connect=_imap(imap),
    )
    folder, raw = imap.appended[0]
    assert folder == "INBOX.Sent"
    assert b"Hello Ada" in raw
    assert b"Hi ada" in raw
