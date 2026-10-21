"""Contact review helpers: personal inboxes, profile merges, and list filters."""

import re
from datetime import datetime
from typing import Any

from app.extract import normalize_email

PERSONAL_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
        "yahoo.com",
        "ymail.com",
        "rocketmail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "pm.me",
        "gmx.com",
        "gmx.net",
        "zoho.com",
    }
)

SEED_STATUSES = (
    ("message_sent", "Message sent", 1),
    ("no_reply", "No reply", 2),
    ("not_interested", "Not interested", 3),
    ("interested", "Interested", 4),
    ("next_step", "Next step", 5),
)

FILL_FIELDS = (
    "name",
    "bio",
    "company",
    "location_raw",
    "country",
    "utc_offset",
    "location_confidence",
    "github_created_at",
    "public_repos_count",
)

PAGE_SIZES = frozenset({25, 50, 100})
NOT_SET = "none"
_STATUS_ID = re.compile(r"[^a-z0-9]+")


def email_domain(value: str | None) -> str:
    normalized = normalize_email(value or "")
    if not normalized or "@" not in normalized:
        return ""
    return normalized.rsplit("@", 1)[1]


def is_personal_email(value: str | None) -> bool:
    return email_domain(value) in PERSONAL_EMAIL_DOMAINS


def status_slug(label: str) -> str:
    cleaned = _STATUS_ID.sub("_", label.casefold()).strip("_")
    return cleaned[:40]


def is_blank(value: object) -> bool:
    return value is None or value == ""


def _stamp(document: dict) -> datetime:
    value = document.get("last_seen_at") or document.get("created_at")
    if isinstance(value, datetime):
        return value
    return datetime.min


def latest_valid_email(evidence: list[dict]) -> str | None:
    matches = [
        item
        for item in evidence
        if item.get("kind") == "email"
        and item.get("validation_status") == "valid"
        and item.get("normalized_value")
    ]
    if not matches:
        return None
    return str(max(matches, key=_stamp)["normalized_value"])


def latest_valid_value(evidence: list[dict], kind: str) -> str | None:
    matches = [
        item
        for item in evidence
        if item.get("kind") == kind
        and item.get("validation_status") == "valid"
        and item.get("normalized_value")
    ]
    if not matches:
        return None
    return str(max(matches, key=_stamp)["normalized_value"])


def merge_profile_fields(
    stored: dict, incoming: dict, chosen_email: str | None
) -> tuple[dict[str, Any], bool]:
    """Fill empty profile fields. A new public email is the only replacement."""

    updates: dict[str, Any] = {}
    for field in FILL_FIELDS:
        if is_blank(stored.get(field)) and not is_blank(incoming.get(field)):
            updates[field] = incoming[field]
    old = normalize_email(stored.get("public_email") or "") or None
    chosen = normalize_email(chosen_email or "") or None
    email_changed = bool(chosen) and chosen != old
    if email_changed:
        updates["public_email"] = chosen
    return updates, email_changed


def contact_facts(candidate: dict, evidence: list[dict]) -> dict[str, Any]:
    """Emails and LinkedIn used by filters and the review table."""

    emails = [
        str(item["normalized_value"])
        for item in evidence
        if item.get("kind") == "email"
        and item.get("validation_status") == "valid"
        and item.get("normalized_value")
    ]
    stored_email = candidate.get("public_email")
    if not emails and isinstance(stored_email, str) and stored_email:
        emails = [stored_email]
    personal = any(is_personal_email(email) for email in emails)
    other = any(not is_personal_email(email) for email in emails)
    linkedin = latest_valid_value(evidence, "linkedin")
    public_email = candidate.get("public_email") or latest_valid_email(evidence) or None
    return {
        "has_email": bool(emails),
        "personal": personal,
        "other": other,
        "has_linkedin": bool(linkedin),
        "linkedin_url": linkedin,
        "public_email": public_email,
    }


def matches_contact_filters(
    facts: dict[str, Any],
    *,
    email: str,
    email_kind: str,
    linkedin: str,
) -> bool:
    if email == "has" and not facts["has_email"]:
        return False
    if email == "missing" and facts["has_email"]:
        return False
    if email_kind == "personal" and not facts["personal"]:
        return False
    if email_kind == "other" and not facts["other"]:
        return False
    if linkedin == "has" and not facts["has_linkedin"]:
        return False
    if linkedin == "missing" and facts["has_linkedin"]:
        return False
    return True
