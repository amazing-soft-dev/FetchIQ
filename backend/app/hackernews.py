"""Developer contacts from Hacker News "Who wants to be hired?" threads.

The ``whoishiring`` account posts three threads a month. Only "Who wants to be
hired?" holds developer contact details: one top-level comment per person,
written as labelled lines such as ``Location:``, ``Remote:``, ``Technologies:``,
``Email:`` and ``LinkedIn:``. Replies to those comments are discussion and are
never read.

Threads come from the official HN API (``/v0/user/whoishiring.json`` lists the
submissions, ``/v0/item/{id}.json`` gives each title). Comments come from the
Algolia HN search API, which returns a whole thread in pages of up to 1,000.
When Algolia is unreachable the comments are read one item at a time from the
official API instead.

Each comment becomes one ``candidates`` document and a set of
``contact_evidence`` rows with the same field names the GitHub pipeline writes
(see ``app.enrichment``), so the contacts page, filters, CSV export and
campaigns work on them unchanged:

- ``login`` is the HN username, ``profile_url`` the HN profile page.
- ``github_user_id`` holds a negative number derived from the username.
  Hacker News has no numeric user ids, and the evidence index needs one that
  cannot collide with a real GitHub id. ``source`` is ``"hackernews"`` so the
  two can always be told apart.
- Emails are taken from text, ``mailto:`` links and the common obfuscations
  (``name [at] example [dot] com``). Placeholders and noreply addresses are
  dropped by ``app.extract.normalize_email`` as everywhere else.
- ``bio`` keeps the comment's own text, ``location_raw`` the ``Location:``
  line, ``country`` what ``app.locations`` can make of it.

Duplicates follow the GitHub rules in ``app.discovery.upsert_candidate``. A post
first looks for the person it belongs to: the same HN account, then the GitHub
candidate whose profile the post links, then any record that already holds one
of the post's addresses. A match stored within the last two weeks is left alone,
and so is one that already has an email, unless the post adds a personal
address that becomes the primary ("personal wins"). Otherwise the record is
updated from the post, GitHub records keeping their login and job. The outreach
status is cleared only when the primary email changes.
"""

import asyncio
import hashlib
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pymongo.errors import DuplicateKeyError

from app.contacts import filter_fields, is_blank, is_personal_email, merge_profile_fields
from app.counts import candidate_share
from app.db import utcnow
from app.extract import classify_url, normalize_email
from app.locations import normalize_location

log = logging.getLogger(__name__)

HN_API = "https://hacker-news.firebaseio.com/v0"
ALGOLIA_API = "https://hn.algolia.com/api/v1"
HN_ITEM_URL = "https://news.ycombinator.com/item?id={id}"
HN_USER_URL = "https://news.ycombinator.com/user?id={login}"
SOURCE = "hackernews"
ALGOLIA_PAGE_SIZE = 1000
# Concurrent item reads against the official API when Algolia is not used.
ITEM_CONCURRENCY = 8
REQUEST_TIMEOUT = 30.0

WANTS_TO_BE_HIRED = re.compile(r"who wants to be hired", re.IGNORECASE)
THREAD_MONTH = re.compile(r"\(([A-Za-z]+)\s+(\d{4})\)\s*$")

# A labelled line: ``Location: Berlin``. Labels are matched case-insensitively
# and the Résumé label is accepted with or without its accents.
_LABEL_RE = re.compile(
    r"^\s*(?P<label>[A-Za-zÀ-ſ/ ]{2,40}?)\s*:\s*(?P<value>.*)$",
)
_LABELS = {
    "location": "location",
    "remote": "remote",
    "willing to relocate": "relocate",
    "relocate": "relocate",
    "technologies": "technologies",
    "tech": "technologies",
    "stack": "technologies",
    "resume/cv": "resume",
    "résumé/cv": "resume",
    "resume": "resume",
    "résumé": "resume",
    "cv": "resume",
    "email": "email",
    "e-mail": "email",
    "contact": "email",
    "linkedin": "linkedin",
    "github": "github",
    "website": "website",
    "site": "website",
    "portfolio": "website",
    "homepage": "website",
    "role": "role",
    "roles": "role",
    "position": "role",
}

# ``name [at] example [dot] com``, ``name (at) example dot com``, ``name AT example DOT com``
_OBFUSCATED_AT = re.compile(
    r"(?P<local>[A-Za-z0-9._%+\-]+)\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|\s+at\s+|<at>)\s*"
    r"(?P<domain>[A-Za-z0-9.\-]+(?:\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\s+dot\s+|<dot>)\s*[A-Za-z0-9\-]+)+)",
    re.IGNORECASE,
)
_DOT_TOKEN = re.compile(
    r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|\s+dot\s+|<dot>)\s*", re.IGNORECASE
)
# ``(UTC+2)``, ``(GMT-5)``, ``(UTC+5:30)`` after a place name.
_TZ_NOTE = re.compile(r"\s*[\(\[][^()\[\]]*\b(?:UTC|GMT)\b[^()\[\]]*[\)\]]", re.IGNORECASE)
_PLAIN_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_MAILTO = re.compile(r"""mailto:([^"'>\s]+)""", re.IGNORECASE)
_HREF = re.compile(r"""<a\s+[^>]*href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_URL = re.compile(r"https?://[^\s<>()\]\"']+", re.IGNORECASE)
_GITHUB_PROFILE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<login>[A-Za-z0-9](?:[A-Za-z0-9\-]{0,37}[A-Za-z0-9])?)/?$",
    re.IGNORECASE,
)
_GITHUB_ROLE_HOLDERS = frozenset(
    {
        "features",
        "topics",
        "about",
        "pricing",
        "sponsors",
        "orgs",
        "settings",
        "login",
        "join",
        "explore",
        "marketplace",
    }
)
_UTC_OFFSET = re.compile(
    r"\b(?:UTC|GMT)\s*(?P<sign>[+\-−])\s*(?P<hours>\d{1,2})(?:[:.](?P<minutes>\d{2}))?\b",
    re.IGNORECASE,
)


@dataclass
class Thread:
    id: int
    title: str
    posted_at: datetime
    url: str = ""

    def __post_init__(self) -> None:
        if not self.url:
            self.url = HN_ITEM_URL.format(id=self.id)


@dataclass
class Comment:
    id: int
    author: str
    thread_id: int
    text_html: str
    posted_at: datetime | None = None

    @property
    def url(self) -> str:
        return HN_ITEM_URL.format(id=self.id)


@dataclass
class DeveloperContact:
    """What one comment published, before it is written to MongoDB."""

    login: str
    comment_id: int
    thread_id: int
    source_url: str
    text: str
    posted_at: datetime | None
    name: str | None = None
    role: str | None = None
    location_raw: str | None = None
    remote: str | None = None
    relocate: str | None = None
    technologies: list[str] = field(default_factory=list)
    resume_url: str | None = None
    github_login: str | None = None
    github_url: str | None = None
    utc_offset: str | None = None
    emails: list[str] = field(default_factory=list)
    urls: list[tuple[str, str]] = field(default_factory=list)

    @property
    def linkedin_url(self) -> str | None:
        for kind, url in self.urls:
            if kind == "linkedin":
                return url
        return None


# --- text helpers -----------------------------------------------------------


def hn_user_id(login: str) -> int:
    """A stable negative id for an HN username.

    GitHub ids are positive, so a negative number never collides with one. The
    low 52 bits of a SHA-256 keep the value inside what BSON and JavaScript
    both store exactly.
    """

    digest = hashlib.sha256(login.strip().casefold().encode("utf-8")).digest()
    return -(int.from_bytes(digest[:8], "big") & ((1 << 52) - 1)) - 1


def html_to_text(raw: str) -> str:
    """HN comment HTML as plain lines. ``<p>`` and ``<br>`` become line breaks."""

    text = raw or ""
    text = re.sub(r"<\s*(?:p|br)\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</\s*p\s*>", "\n", text, flags=re.IGNORECASE)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.replace("\r", "").split("\n")]
    return "\n".join(line for line in lines if line).strip()


def deobfuscate_emails(text: str) -> list[str]:
    """Addresses written as ``name [at] example [dot] com`` and the like."""

    found: list[str] = []
    for match in _OBFUSCATED_AT.finditer(text or ""):
        domain = _DOT_TOKEN.sub(".", match.group("domain"))
        found.append(f"{match.group('local')}@{domain}")
    return found


def extract_emails(text: str, raw_html: str = "") -> list[str]:
    """Every distinct usable email in a comment, plain text first, then mailto links."""

    found: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        normalized = normalize_email(value)
        if normalized and normalized.casefold() not in seen:
            seen.add(normalized.casefold())
            found.append(normalized)

    for email in _PLAIN_EMAIL.findall(text or ""):
        add(email)
    for match in _MAILTO.finditer(raw_html or ""):
        add(html.unescape(match.group(1)))
    for email in deobfuscate_emails(text or ""):
        add(email)
    return found


def extract_urls(text: str, raw_html: str = "") -> list[str]:
    """Every distinct http(s) URL, from hrefs first, then from the text.

    HN shortens long link text with an ellipsis while the ``href`` keeps the
    whole address, so a text URL that is a prefix of a linked one is dropped.
    """

    found: list[str] = []
    seen: set[str] = set()
    for match in _HREF.finditer(raw_html or ""):
        url = html.unescape(match.group(1))
        if url.casefold().startswith("http") and url not in seen:
            seen.add(url)
            found.append(url)
    linked = list(found)
    for url in _URL.findall(text or ""):
        cleaned = url.rstrip(".,;:!?")
        if cleaned in seen or any(full.startswith(cleaned) for full in linked):
            continue
        seen.add(cleaned)
        found.append(cleaned)
    return found


def full_url(value: str, raw_html: str = "") -> str | None:
    """The first URL in a labelled value, lengthened to its ``href`` when HN cut it."""

    match = _URL.search(value or "")
    if match is None:
        return None
    short = match.group(0).rstrip(".,;:!?")
    for href in _HREF.finditer(raw_html or ""):
        candidate = html.unescape(href.group(1))
        if candidate.startswith(short):
            return candidate
    return short


def place_only(location: str | None) -> str | None:
    """A ``Location:`` value without its timezone note, for the country lookup."""

    if not location:
        return None
    cleaned = _TZ_NOTE.sub("", location).strip(" ,;-")
    return cleaned or None


def github_login_from_url(url: str) -> str | None:
    match = _GITHUB_PROFILE.match(url.strip())
    if match is None:
        return None
    login = match.group("login")
    if login.casefold() in _GITHUB_ROLE_HOLDERS:
        return None
    return login


def parse_utc_offset(text: str) -> str | None:
    match = _UTC_OFFSET.search(text or "")
    if match is None:
        return None
    sign = "-" if match.group("sign") in "-−" else "+"
    hours = int(match.group("hours"))
    minutes = int(match.group("minutes") or 0)
    if hours > 14 or minutes > 59:
        return None
    return f"{sign}{hours:02d}:{minutes:02d}"


def _label_key(label: str) -> str | None:
    folded = " ".join(label.casefold().split())
    folded = folded.replace("é", "e")
    return _LABELS.get(folded)


def parse_labelled_lines(text: str) -> dict[str, str]:
    """``{"location": "Berlin", "remote": "Yes", ...}`` from the labelled lines of a post.

    Only the first occurrence of each label counts. Lines without a known label
    are ignored, so a ``Note: ...`` inside the free text is harmless.
    """

    fields: dict[str, str] = {}
    for line in (text or "").split("\n"):
        match = _LABEL_RE.match(line)
        if match is None:
            continue
        key = _label_key(match.group("label"))
        if key is None or key in fields:
            continue
        value = match.group("value").strip()
        if value:
            fields[key] = value
    return fields


def split_technologies(value: str) -> list[str]:
    """Comma, semicolon, pipe or bullet separated items. Brackets keep their contents."""

    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in value or "":
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        if depth == 0 and char in ",;|•\n":
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))

    items: list[str] = []
    seen: set[str] = set()
    for part in parts:
        item = " ".join(part.split()).strip(" .")
        if not item or len(item) > 60:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    return items[:50]


def parse_comment(comment: Comment) -> DeveloperContact:
    """Everything one comment published, as a ``DeveloperContact``."""

    text = html_to_text(comment.text_html)
    fields = parse_labelled_lines(text)
    contact = DeveloperContact(
        login=comment.author,
        comment_id=comment.id,
        thread_id=comment.thread_id,
        source_url=comment.url,
        text=text,
        posted_at=comment.posted_at,
        role=fields.get("role"),
        location_raw=fields.get("location"),
        remote=fields.get("remote"),
        relocate=fields.get("relocate"),
        technologies=split_technologies(fields.get("technologies", "")),
        utc_offset=parse_utc_offset(fields.get("location", "")),
    )
    contact.emails = extract_emails(text, comment.text_html)

    resume = fields.get("resume")
    if resume:
        contact.resume_url = full_url(resume, comment.text_html)

    seen: set[tuple[str, str]] = set()
    for raw in extract_urls(text, comment.text_html):
        login = github_login_from_url(raw)
        if login and contact.github_login is None:
            contact.github_login = login
            contact.github_url = f"https://github.com/{login}"
            continue
        classified = classify_url(raw, prefer_website=True)
        if classified is None or classified in seen:
            continue
        seen.add(classified)
        contact.urls.append(classified)
    return contact


# --- fetching ----------------------------------------------------------------


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def thread_from_item(item: dict[str, Any]) -> Thread | None:
    title = item.get("title")
    if not isinstance(title, str) or not WANTS_TO_BE_HIRED.search(title):
        return None
    if item.get("deleted") or item.get("dead"):
        return None
    posted = _parse_time(item.get("time")) or datetime.now(UTC)
    return Thread(id=int(item["id"]), title=title.strip(), posted_at=posted)


class HackerNewsClient:
    """Reads threads and comments. ``client`` is any ``httpx.AsyncClient``."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._own_client = client is None

    async def __aenter__(self) -> "HackerNewsClient":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._own_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HackerNewsClient must be used as an async context manager")
        return self._client

    async def _json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def submitted_ids(self, user: str = "whoishiring") -> list[int]:
        payload = await self._json(f"{HN_API}/user/{user}.json")
        if not isinstance(payload, dict):
            return []
        return [int(item) for item in payload.get("submitted") or [] if isinstance(item, int)]

    async def item(self, item_id: int) -> dict[str, Any] | None:
        payload = await self._json(f"{HN_API}/item/{item_id}.json")
        return payload if isinstance(payload, dict) else None

    async def hiring_threads(self, *, limit: int | None = None) -> list[Thread]:
        """The newest "Who wants to be hired?" threads, newest first.

        Submissions come newest first and three are posted a month, so reading
        ``3 * limit`` plus a margin finds ``limit`` matching threads.
        """

        ids = await self.submitted_ids()
        window = ids if limit is None else ids[: limit * 3 + 6]
        semaphore = asyncio.Semaphore(ITEM_CONCURRENCY)

        async def read(item_id: int) -> Thread | None:
            async with semaphore:
                item = await self.item(item_id)
            return thread_from_item(item) if item else None

        threads = [thread for thread in await asyncio.gather(*(read(i) for i in window)) if thread]
        threads.sort(key=lambda thread: thread.posted_at, reverse=True)
        return threads if limit is None else threads[:limit]

    async def comments(self, thread: Thread) -> list[Comment]:
        """Top-level comments of a thread. Algolia first, the item API as fallback."""

        try:
            return await self._algolia_comments(thread)
        except (httpx.HTTPError, ValueError) as exc:
            log.warning(
                "algolia unavailable, reading items one by one",
                extra={"thread_id": thread.id, "error": str(exc)},
            )
            return await self._item_comments(thread)

    async def _algolia_comments(self, thread: Thread) -> list[Comment]:
        comments: list[Comment] = []
        page = 0
        while True:
            payload = await self._json(
                f"{ALGOLIA_API}/search_by_date",
                params={
                    "tags": f"comment,story_{thread.id}",
                    "hitsPerPage": ALGOLIA_PAGE_SIZE,
                    "page": page,
                },
            )
            if not isinstance(payload, dict):
                raise ValueError("unexpected Algolia payload")
            for hit in payload.get("hits") or []:
                comment = _comment_from_hit(hit, thread.id)
                if comment is not None:
                    comments.append(comment)
            page += 1
            if page >= int(payload.get("nbPages") or 0):
                break
        return comments

    async def _item_comments(self, thread: Thread) -> list[Comment]:
        story = await self.item(thread.id)
        kids = [k for k in (story or {}).get("kids") or [] if isinstance(k, int)]
        semaphore = asyncio.Semaphore(ITEM_CONCURRENCY)

        async def read(item_id: int) -> Comment | None:
            async with semaphore:
                item = await self.item(item_id)
            return _comment_from_item(item, thread.id) if item else None

        found = await asyncio.gather(*(read(k) for k in kids))
        return [comment for comment in found if comment is not None]


def _comment_from_hit(hit: dict[str, Any], thread_id: int) -> Comment | None:
    """Only comments whose parent is the thread itself are developer posts."""

    if not isinstance(hit, dict):
        return None
    if int(hit.get("parent_id") or 0) != thread_id:
        return None
    author = hit.get("author")
    text = hit.get("comment_text")
    if not isinstance(author, str) or not author or not isinstance(text, str):
        return None
    return Comment(
        id=int(hit["objectID"]),
        author=author,
        thread_id=thread_id,
        text_html=text,
        posted_at=_parse_time(hit.get("created_at_i")) or _parse_time(hit.get("created_at")),
    )


def _comment_from_item(item: dict[str, Any], thread_id: int) -> Comment | None:
    if item.get("type") != "comment" or item.get("deleted") or item.get("dead"):
        return None
    author = item.get("by")
    text = item.get("text")
    if not isinstance(author, str) or not author or not isinstance(text, str):
        return None
    return Comment(
        id=int(item["id"]),
        author=author,
        thread_id=thread_id,
        text_html=text,
        posted_at=_parse_time(item.get("time")),
    )


# --- storing -----------------------------------------------------------------

# A person stored or updated within this window is not rewritten, as in discovery.
FRESH_PROFILE = timedelta(days=14)
# The login used when an HN username is already taken by an unrelated GitHub record.
COLLISION_PREFIX = "hn:"


def evidence_rows(contact: DeveloperContact, user_id: int | None = None) -> list[dict[str, Any]]:
    """The ``contact_evidence`` rows one post publishes, without timestamps."""

    if user_id is None:
        user_id = hn_user_id(contact.login)
    rows: list[dict[str, Any]] = []
    for email in contact.emails:
        rows.append(
            {
                "github_user_id": user_id,
                "kind": "email",
                "normalized_value": email,
                "raw_value": email,
                "source_url": contact.source_url,
            }
        )
    for kind, url in contact.urls:
        rows.append(
            {
                "github_user_id": user_id,
                "kind": kind,
                "normalized_value": url,
                "raw_value": url,
                "source_url": contact.source_url,
            }
        )
    return rows


def choose_public_email(stored: str | None, incoming: list[str]) -> str | None:
    """The primary address after a post arrives. A personal address wins.

    A record with no email takes the post's personal address, or its first one.
    A record with a work address takes a personal one from the post. A record
    with a personal address keeps it.
    """

    current = normalize_email(stored or "") or None
    personal = next((email for email in incoming if is_personal_email(email)), None)
    if current is None:
        return personal or (incoming[0] if incoming else None)
    if personal is not None and not is_personal_email(current):
        return personal
    return current


def hn_fields(contact: DeveloperContact, thread: Thread) -> dict[str, Any]:
    """The Hacker News details kept beside the shared candidate fields."""

    return {
        "hn_username": contact.login,
        "hn_thread_id": thread.id,
        "hn_thread_title": thread.title,
        "hn_comment_id": contact.comment_id,
        "hn_comment_url": contact.source_url,
        "hn_posted_at": contact.posted_at,
        "role": contact.role,
        "remote": contact.remote,
        "willing_to_relocate": contact.relocate,
        "technologies": contact.technologies,
        "resume_url": contact.resume_url,
    }


def profile_fields(contact: DeveloperContact) -> dict[str, Any]:
    """The shared profile fields a post can fill, in the GitHub pipeline's names."""

    location = normalize_location(place_only(contact.location_raw))
    return {
        "name": contact.name,
        "bio": contact.text[:2000] or None,
        "company": None,
        "location_raw": contact.location_raw,
        "country": location.country,
        "utc_offset": contact.utc_offset,
        "location_confidence": location.confidence if contact.location_raw else None,
        "github_created_at": None,
        "public_repos_count": None,
    }


def candidate_fields(
    contact: DeveloperContact,
    thread: Thread,
    now: datetime,
    *,
    job_id: str | None = None,
    login: str | None = None,
) -> dict[str, Any]:
    """The ``candidates`` document for a new person, in the GitHub pipeline's field names."""

    evidence = evidence_rows(contact)
    public_email = choose_public_email(None, contact.emails)
    document: dict[str, Any] = {
        "source": SOURCE,
        "sources": [SOURCE],
        "job_id": job_id,
        "github_user_id": hn_user_id(contact.login),
        "login": login or contact.login,
        "profile_url": HN_USER_URL.format(login=contact.login),
        "enrichment_status": "enriched",
        "collection_status": "enriched",
        **profile_fields(contact),
        "public_email": public_email,
        "last_checked_at": now,
        "updated_at": now,
        **hn_fields(contact, thread),
        "github_login": contact.github_login,
        "github_url": contact.github_url,
    }
    document.update(
        filter_fields(
            {"public_email": public_email},
            [{**row, "validation_status": "valid"} for row in evidence],
        )
    )
    return document


@dataclass
class StoreResult:
    inserted: int = 0
    updated: int = 0
    merged: int = 0
    fresh: int = 0
    has_email: int = 0
    skipped: int = 0
    evidence: int = 0

    def add(self, outcome: str) -> None:
        if outcome in ("inserted", "updated", "merged", "fresh", "has_email"):
            setattr(self, outcome, getattr(self, outcome) + 1)
        else:
            self.skipped += 1


@dataclass
class Stored:
    """What ``store_contact`` did.

    ``moved`` is the share an updated HN record took from its previous job, as
    ``upsert_candidate`` reports it, so a job can carry the counters over.
    """

    outcome: str
    previous_job_id: str | None = None
    moved: dict[str, int] | None = None


def _checked_at(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def find_match(db, contact: DeveloperContact) -> dict | None:
    """The stored person a post belongs to, if any.

    The same HN account first, then the GitHub candidate whose profile the post
    links, then any record that already holds one of the post's addresses.
    """

    existing = await db.candidates.find_one({"source": SOURCE, "hn_username": contact.login})
    if existing is not None:
        return existing
    if contact.github_login:
        pattern = f"^{re.escape(contact.github_login)}$"
        existing = await db.candidates.find_one(
            {"login": {"$regex": pattern, "$options": "i"}, "source": {"$ne": SOURCE}}
        )
        if existing is not None:
            return existing
    if contact.emails:
        existing = await db.candidates.find_one({"public_email": {"$in": contact.emails}})
        if existing is not None:
            return existing
        row = await db.contact_evidence.find_one(
            {
                "kind": "email",
                "normalized_value": {"$in": contact.emails},
                "validation_status": "valid",
            },
            {"github_user_id": 1},
        )
        if row is not None and isinstance(row.get("github_user_id"), int):
            return await db.candidates.find_one({"github_user_id": row["github_user_id"]})
    return None


async def _upsert_evidence(db, rows: list[dict[str, Any]], stamp: datetime) -> None:
    for row in rows:
        try:
            await db.contact_evidence.update_one(
                {
                    "github_user_id": row["github_user_id"],
                    "kind": row["kind"],
                    "normalized_value": row["normalized_value"],
                },
                {
                    "$setOnInsert": {**row, "validation_status": "valid", "created_at": stamp},
                    "$set": {"last_seen_at": stamp},
                },
                upsert=True,
            )
        except DuplicateKeyError:
            continue


async def _insert(
    db, contact: DeveloperContact, thread: Thread, stamp: datetime, job_id: str | None
) -> Stored:
    for login in (contact.login, f"{COLLISION_PREFIX}{contact.login}"):
        fields = candidate_fields(contact, thread, stamp, job_id=job_id, login=login)
        try:
            await db.candidates.insert_one({**fields, "created_at": stamp, "discovered_at": stamp})
        except DuplicateKeyError:
            continue
        await _upsert_evidence(db, evidence_rows(contact), stamp)
        return Stored("inserted")
    return Stored("skipped")


async def store_contact(
    db,
    contact: DeveloperContact,
    thread: Thread,
    *,
    job_id: str | None = None,
    now: datetime | None = None,
) -> Stored:
    """Insert, update or merge one post. See the module docstring for the rules.

    Outcomes: ``inserted`` (a new person), ``updated`` (an HN record rewritten from
    a newer post), ``merged`` (a GitHub record that gained this post), ``fresh``
    and ``has_email`` (left alone, as in discovery), ``skipped`` (could not be
    stored under either login).
    """

    stamp = now or utcnow()
    existing = await find_match(db, contact)
    if existing is None:
        return await _insert(db, contact, thread, stamp, job_id)

    stored_email = normalize_email(existing.get("public_email") or "") or None
    chosen = choose_public_email(stored_email, contact.emails)
    email_changed = chosen is not None and chosen != stored_email
    checked = _checked_at(existing.get("last_checked_at"))
    # A new personal address is never stale, so it passes both skips below.
    if not email_changed:
        if checked is not None and checked >= stamp - FRESH_PROFILE:
            return Stored("fresh")
        if stored_email is not None:
            return Stored("has_email")

    user_id = existing.get("github_user_id")
    if not isinstance(user_id, int):
        user_id = hn_user_id(contact.login)
    await _upsert_evidence(db, evidence_rows(contact, user_id), stamp)
    evidence = [row async for row in db.contact_evidence.find({"github_user_id": user_id})]

    filled, _changed = merge_profile_fields(existing, profile_fields(contact), chosen)
    filled.update(hn_fields(contact, thread))
    if is_blank(existing.get("github_login")) and contact.github_login:
        filled["github_login"] = contact.github_login
        filled["github_url"] = contact.github_url
    if not isinstance(existing.get("github_user_id"), int):
        filled["github_user_id"] = user_id
    final_email = filled.get("public_email", existing.get("public_email"))
    filled.update(filter_fields({"public_email": final_email}, evidence))
    filled["last_checked_at"] = stamp
    filled["updated_at"] = stamp

    from_hn = existing.get("source") == SOURCE
    previous_job: str | None = None
    moved: dict[str, int] | None = None
    if from_hn and job_id is not None and existing.get("job_id") != job_id:
        previous = existing.get("job_id")
        previous_job = previous if isinstance(previous, str) else None
        moved = candidate_share(existing)
        filled["job_id"] = job_id

    update: dict[str, Any] = {"$set": filled, "$addToSet": {"sources": SOURCE}}
    if email_changed and existing.get("outreach_status"):
        # A new address means the old conversation does not apply, as in enrichment.
        update["$unset"] = {"outreach_status": ""}
    await db.candidates.update_one({"_id": existing["_id"]}, update)
    return Stored("updated" if from_hn else "merged", previous_job, moved)


async def store_thread(
    db,
    thread: Thread,
    comments: list[Comment],
    *,
    job_id: str | None = None,
    now: datetime | None = None,
) -> StoreResult:
    result = StoreResult()
    for comment in comments:
        contact = parse_comment(comment)
        if not contact.emails and not contact.urls and not contact.github_login:
            result.skipped += 1
            continue
        stored = await store_contact(db, contact, thread, job_id=job_id, now=now)
        result.add(stored.outcome)
        if stored.outcome in ("inserted", "updated", "merged"):
            result.evidence += len(evidence_rows(contact))
    return result


async def scrape(
    db,
    *,
    threads: int | None = 1,
    client: httpx.AsyncClient | None = None,
) -> list[tuple[Thread, StoreResult]]:
    """Read the newest ``threads`` "Who wants to be hired?" posts into MongoDB."""

    results: list[tuple[Thread, StoreResult]] = []
    async with HackerNewsClient(client) as hn:
        for thread in await hn.hiring_threads(limit=threads):
            comments = await hn.comments(thread)
            result = await store_thread(db, thread, comments)
            log.info(
                "hacker news thread stored",
                extra={
                    "thread_id": thread.id,
                    "title": thread.title,
                    "comments": len(comments),
                    "inserted": result.inserted,
                    "updated": result.updated,
                    "merged": result.merged,
                    "fresh": result.fresh,
                    "has_email": result.has_email,
                    "skipped": result.skipped,
                },
            )
            results.append((thread, result))
    return results
