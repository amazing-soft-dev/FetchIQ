import smtplib

import pytest

from app.config import Settings
from app.mail import (
    MailFailure,
    placeholder_values,
    render_template,
    send_message,
    unknown_placeholders,
)


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
