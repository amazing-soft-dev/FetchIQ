import imaplib
import smtplib

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.mail import (
    MailFailure,
    SmtpMailer,
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


def test_sender_refusal_keeps_the_server_reply() -> None:
    client = _Client(smtplib.SMTPSenderRefused(553, b"5.7.1 denied", "ada@example.com"))

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
    assert caught.value.connection is True
    assert str(caught.value) == "The mail server refused the sender address. (553 5.7.1 denied)"


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


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (TimeoutError("timed out"), "timed out"),
        (ConnectionRefusedError("no"), "connection refused"),
        (smtplib.SMTPServerDisconnected("bye"), "the server closed the connection"),
    ],
)
def test_connection_failure_reports_the_cause(error: Exception, cause: str) -> None:
    def connect(settings):
        raise error

    result = check_mailbox(_settings(), connect=connect, sleeper=lambda _seconds: None)
    assert result == {"ok": False, "detail": f"Could not connect to the mail server. ({cause})"}


def test_dropped_connection_is_retried() -> None:
    calls = 0
    waits: list[float] = []
    client = _Client()

    def connect(settings):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("timed out")
        return client

    result = check_mailbox(
        _settings(),
        connect=connect,
        imap_connect=_imap(_Imap()),
        sleeper=waits.append,
    )
    assert result["ok"] is True
    assert calls == 3
    assert waits == [0.5, 1.0]


class _SessionSmtp:
    def __init__(
        self,
        fail_on_send: int | None = None,
        sender_refusal: tuple[int, bytes] | None = None,
    ) -> None:
        self.fail_on_send = fail_on_send
        self.sender_refusal = sender_refusal
        self.messages: list = []
        self.sends = 0
        self.closed = False

    def login(self, username: str, password: str) -> None:
        return None

    def send_message(self, message) -> None:
        self.sends += 1
        if self.sender_refusal is not None:
            code, text = self.sender_refusal
            raise smtplib.SMTPSenderRefused(code, text, "ada@example.com")
        if self.fail_on_send is not None and self.sends == self.fail_on_send:
            raise smtplib.SMTPServerDisconnected("bye")
        self.messages.append(message)

    def quit(self) -> None:
        self.closed = True


class _SessionImap:
    def __init__(self) -> None:
        self.lists = 0
        self.appended: list = []

    def login(self, username: str, password: str) -> None:
        return None

    def list(self):
        self.lists += 1
        return "OK", [b'(\\HasNoChildren \\Sent) "." "INBOX.Sent"']

    def append(self, mailbox, flags, date_time, message):
        self.appended.append(mailbox)
        return "OK", [b"ok"]

    def logout(self):
        return "BYE", [b""]


def test_smtp_session_is_reused_and_sent_folder_is_resolved_once() -> None:
    smtp_opens: list[_SessionSmtp] = []
    imap_opens: list[_SessionImap] = []

    def connect(settings):
        client = _SessionSmtp()
        smtp_opens.append(client)
        return client

    def imap_connect(settings):
        client = _SessionImap()
        imap_opens.append(client)
        return client

    mailer = SmtpMailer(_settings(), connect=connect, imap_connect=imap_connect)
    assert mailer.send(recipient="a@example.com", subject="Hi", body="One") is None
    assert mailer.send(recipient="b@example.com", subject="Hi", body="Two") is None
    assert len(smtp_opens) == 1
    assert len(imap_opens) == 1
    assert len(smtp_opens[0].messages) == 2
    assert imap_opens[0].lists == 1
    assert imap_opens[0].appended == ["INBOX.Sent", "INBOX.Sent"]


def test_sender_refusal_reconnects_and_the_next_message_is_sent() -> None:
    smtp_opens: list[_SessionSmtp] = []

    def connect(settings):
        refusal = (421, b"try again") if not smtp_opens else None
        client = _SessionSmtp(sender_refusal=refusal)
        smtp_opens.append(client)
        return client

    def imap_connect(settings):
        return _SessionImap()

    mailer = SmtpMailer(_settings(), connect=connect, imap_connect=imap_connect)
    assert mailer.send(recipient="a@example.com", subject="Hi", body="One") is None
    assert mailer.send(recipient="b@example.com", subject="Hi", body="Two") is None
    assert len(smtp_opens) == 2
    assert smtp_opens[0].closed is True
    assert smtp_opens[0].messages == []
    assert len(smtp_opens[1].messages) == 2


@pytest.mark.parametrize("code", [450, 553])
def test_repeated_sender_refusal_does_not_stop(code: int) -> None:
    smtp_opens: list[_SessionSmtp] = []

    def connect(settings):
        client = _SessionSmtp(sender_refusal=(code, b"denied"))
        smtp_opens.append(client)
        return client

    mailer = SmtpMailer(_settings(), connect=connect)
    with pytest.raises(MailFailure) as caught:
        mailer.send(recipient="a@example.com", subject="Hi", body="One")
    assert caught.value.stop is False
    assert caught.value.connection is True
    assert str(caught.value) == f"The mail server refused the sender address. ({code} denied)"
    assert len(smtp_opens) == 2
    assert all(client.closed for client in smtp_opens)


def test_dropped_smtp_session_reconnects_once() -> None:
    smtp_opens: list[_SessionSmtp] = []
    imap_opens: list[_SessionImap] = []

    def connect(settings):
        fail_on_send = 2 if not smtp_opens else None
        client = _SessionSmtp(fail_on_send=fail_on_send)
        smtp_opens.append(client)
        return client

    def imap_connect(settings):
        client = _SessionImap()
        imap_opens.append(client)
        return client

    mailer = SmtpMailer(_settings(), connect=connect, imap_connect=imap_connect)
    assert mailer.send(recipient="a@example.com", subject="Hi", body="One") is None
    assert mailer.send(recipient="b@example.com", subject="Hi", body="Two") is None
    assert len(smtp_opens) == 2
    assert smtp_opens[0].closed is True
    assert len(smtp_opens[0].messages) == 1
    assert len(smtp_opens[1].messages) == 1
    assert len(imap_opens) == 1
    assert imap_opens[0].lists == 1


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
