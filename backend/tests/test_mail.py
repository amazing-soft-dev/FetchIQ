import smtplib

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.mail import (
    MailFailure,
    check_mailbox,
    placeholder_values,
    render_template,
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


def test_login_reports_the_mailbox_without_sending() -> None:
    client = _Client()

    def connect(settings):
        return client

    result = check_mailbox(_settings(), connect=connect)
    assert result == {
        "ok": True,
        "from": "Ada <ada@example.com>",
        "host": "smtp.hostinger.com",
        "port": 465,
    }
    assert client.messages == []
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
