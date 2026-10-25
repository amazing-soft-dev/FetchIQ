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


def filter_fields(candidate: dict, evidence: list[dict]) -> dict[str, Any]:
    """Email and LinkedIn facts stored on a candidate so the list can page in Mongo."""

    facts = contact_facts(candidate, evidence)
    return {
        "has_email": bool(facts["has_email"]),
        "personal": bool(facts["personal"]),
        "other": bool(facts["other"]),
        "has_linkedin": bool(facts["has_linkedin"]),
        "linkedin_url": facts["linkedin_url"],
    }


async def evidence_by_user(db, user_ids: list[int]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {user_id: [] for user_id in user_ids}
    if not user_ids:
        return grouped
    cursor = db.contact_evidence.find({"github_user_id": {"$in": user_ids}})
    for item in await cursor.to_list(None):
        user_id = item.get("github_user_id")
        if isinstance(user_id, int):
            grouped.setdefault(user_id, []).append(item)
    return grouped


_BACKFILL_BATCH = 200


async def backfill_contact_filters(db) -> int:
    """Fill email and LinkedIn flags on candidates that do not have them yet."""

    updated = 0
    while True:
        candidates = await (
            db.candidates.find({"has_email": {"$exists": False}})
            .limit(_BACKFILL_BATCH)
            .to_list(_BACKFILL_BATCH)
        )
        if not candidates:
            return updated
        user_ids = [
            candidate["github_user_id"]
            for candidate in candidates
            if isinstance(candidate.get("github_user_id"), int)
        ]
        grouped = await evidence_by_user(db, user_ids)
        for candidate in candidates:
            user_id = candidate.get("github_user_id")
            evidence = grouped.get(user_id, []) if isinstance(user_id, int) else []
            fields = filter_fields(candidate, evidence)
            if is_blank(candidate.get("public_email")):
                shown = contact_facts(candidate, evidence)["public_email"]
                if shown:
                    fields["public_email"] = shown
            await db.candidates.update_one({"_id": candidate["_id"]}, {"$set": fields})
            updated += 1
