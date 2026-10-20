from app.extract import classify_url, extract_published_contacts, normalize_email


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


def test_name_and_domain_are_not_combined_into_an_email() -> None:
    found = extract_published_contacts("Ada Lovelace writes at https://example.com")
    assert found.emails == []
    assert found.urls == [("other_social", "https://example.com")]


def test_profile_blog_can_be_a_website() -> None:
    assert classify_url("https://example.com/notes/", prefer_website=True) == (
        "website",
        "https://example.com/notes",
    )
    assert classify_url("https://github.com/ada") is None
