"""Narrow extractors for addresses and social URLs published in Markdown.

Emails are taken only from text and mailto links. Code spans are ignored.
Noreply placeholders are dropped, and nothing is invented from a name or domain.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse

from markdown_it import MarkdownIt

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
URL_RE = re.compile(r"https?://[^\s<>()\]]+", re.IGNORECASE)
NOREPLY_DOMAINS = ("users.noreply.github.com", "noreply.github.com")

SOCIAL_HOSTS = {
    "linkedin.com": "linkedin",
    "twitter.com": "x",
    "x.com": "x",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "instagram.com": "instagram",
}

_PARSER = MarkdownIt()


@dataclass
class PublishedContacts:
    emails: list[str] = field(default_factory=list)
    urls: list[tuple[str, str]] = field(default_factory=list)


def _walk(tokens):
    for token in tokens or []:
        yield token
        yield from _walk(token.children)


def normalize_email(value: str) -> str | None:
    text = value.strip().strip("<>").split("?", 1)[0].strip()
    if not EMAIL_RE.fullmatch(text):
        return None
    local, separator, domain = text.rpartition("@")
    if (
        separator != "@"
        or not local
        or ".." in domain
        or domain.startswith(".")
        or domain.endswith(".")
    ):
        return None
    normalized = f"{local}@{domain.casefold()}"
    host = domain.casefold()
    if host in NOREPLY_DOMAINS or any(host.endswith(f".{item}") for item in NOREPLY_DOMAINS):
        return None
    return normalized


def normalize_url(value: str) -> str | None:
    text = value.strip().strip("<>").rstrip(").,;")
    if text.startswith("//"):
        text = f"https:{text}"
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or "@" in parsed.netloc:
        return None
    host = parsed.netloc.casefold()
    if host.endswith(":80") and parsed.scheme == "http":
        host = host[:-3]
    if host.endswith(":443") and parsed.scheme == "https":
        host = host[:-4]
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme.casefold(), host, path, "", "", ""))


def classify_url(value: str, *, prefer_website: bool = False) -> tuple[str, str] | None:
    """Return ``(kind, normalized_url)`` for a published http(s) URL."""

    normalized = normalize_url(value)
    if normalized is None:
        return None
    host = urlparse(normalized).netloc.removeprefix("www.")
    if host == "github.com" or host.endswith(".github.com"):
        return None
    for domain, kind in SOCIAL_HOSTS.items():
        if host == domain or host.endswith(f".{domain}"):
            return kind, normalized
    if prefer_website:
        return "website", normalized
    return "other_social", normalized


def extract_published_contacts(text: str) -> PublishedContacts:
    found = PublishedContacts()
    seen_emails: set[str] = set()
    seen_urls: set[tuple[str, str]] = set()
    for token in _walk(_PARSER.parse(text or "")):
        if token.type in {"fence", "code_block", "code_inline", "html_block", "html_inline"}:
            continue
        if token.type == "link_open":
            href = token.attrGet("href") or ""
            if href.casefold().startswith("mailto:"):
                _add_email(found, seen_emails, href.split(":", 1)[1])
            else:
                _add_url(found, seen_urls, href)
            continue
        if token.type == "text" and token.content:
            for email in EMAIL_RE.findall(token.content):
                _add_email(found, seen_emails, email)
            for url in URL_RE.findall(token.content):
                _add_url(found, seen_urls, url)
    return found


def _add_email(found: PublishedContacts, seen: set[str], value: str) -> None:
    normalized = normalize_email(value)
    if normalized and normalized.casefold() not in seen:
        seen.add(normalized.casefold())
        found.emails.append(normalized)


def _add_url(found: PublishedContacts, seen: set[tuple[str, str]], value: str) -> None:
    classified = classify_url(value)
    if classified and classified not in seen:
        seen.add(classified)
        found.urls.append(classified)
