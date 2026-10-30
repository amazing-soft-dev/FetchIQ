from app.extract import classify_url, extract_published_contacts, normalize_email, normalize_url


def test_null_and_noreply_emails_are_not_contacts() -> None:
    assert normalize_email("") is None
    assert normalize_email("ada@users.noreply.github.com") is None
    assert normalize_email("ada@noreply.github.com") is None


def test_markdown_keeps_published_email_and_skips_code() -> None:
    text = """
Hello from Ada.

Email me at ada@example.com or [mail](mailto:ada.lovelace@example.com).

See https://www.linkedin.com/in/ada and https://x.com/ada.

```
hidden@example.com
```

`also-hidden@example.com`
"""
    found = extract_published_contacts(text)
    assert found.emails == ["ada@example.com", "ada.lovelace@example.com"]
    kinds = {kind for kind, _url in found.urls}
    assert kinds == {"linkedin", "x"}


def test_angle_bracket_email_in_readme_is_kept() -> None:
    found = extract_published_contacts(
        "To contact me write me at <jscarton@gmail.com>\n\nPortfolio: https://arepasapp.com"
    )
    assert found.emails == ["jscarton@gmail.com"]
    assert ("other_social", "https://arepasapp.com") in found.urls


def test_name_and_domain_are_not_combined_into_an_email() -> None:
    found = extract_published_contacts("Ada Lovelace writes at https://example.com")
    assert found.emails == []
    assert found.urls == [("other_social", "https://example.com")]


def test_truncated_bracket_url_does_not_raise() -> None:
    assert normalize_url("http://[::1") is None
    assert normalize_url("http://[::1]/path") is None
    found = extract_published_contacts("Email ada@example.com and http://[::1]/path")
    assert found.emails == ["ada@example.com"]
    assert all("[::1" not in url for _kind, url in found.urls)


def test_profile_blog_can_be_a_website() -> None:
    assert classify_url("https://example.com/notes/", prefer_website=True) == (
        "website",
        "https://example.com/notes",
    )
    assert classify_url("https://github.com/ada") is None


def test_placeholder_emails_are_not_contacts() -> None:
    assert normalize_email("your.email@example.com") is None
    assert normalize_email("YourEmail@gmail.com") is None
    assert normalize_email("your_name@example.org") is None
    assert normalize_email("email@example.com") is None
    assert normalize_email("name@example.net") is None
    assert normalize_email("user@mail.example") is None
    assert normalize_email("ada@example.com") == "ada@example.com"
    assert normalize_email("email@gmail.com") == "email@gmail.com"


def test_link_text_email_wins_over_placeholder_mailto_target() -> None:
    found = extract_published_contacts(
        "- Email: [luisdavid25torrealba@gmail.com](mailto:your.email@example.com)  "
    )
    assert found.emails == ["luisdavid25torrealba@gmail.com"]


def test_placeholder_mailto_without_visible_email_yields_nothing() -> None:
    text = (
        '[Email me](mailto:your.email@example.com) <a href="mailto:your.email@example.com">me</a>'
    )
    found = extract_published_contacts(text)
    assert found.emails == []
