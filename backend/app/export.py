"""Stream one CSV row per GitHub user id.

Selection rule: for each contact kind, use the valid evidence seen most recently.
``other_social_urls`` keeps every remaining valid social URL. Missing values are
empty cells. Cells that start with ``=``, ``+``, ``-``, or ``@`` are prefixed so
a spreadsheet does not treat them as formulas.
"""

import csv
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from dateutil.relativedelta import relativedelta

from app.db import utcnow

COLUMNS = [
    "github_username",
    "github_profile_url",
    "name",
    "bio",
    "company",
    "country",
    "utc_offset",
    "us_state",
    "github_created_at",
    "account_age_months",
    "public_email",
    "website",
    "linkedin_url",
    "x_url",
    "facebook_url",
    "instagram_url",
    "other_social_urls",
    "public_repos_count",
    "primary_languages",
    "matched_keywords",
    "relevant_repository_urls",
    "email_source_url",
    "contact_source_urls",
    "email_validation_status",
    "location_confidence",
    "last_checked_at",
    "collection_status",
]

KIND_COLUMNS = {
    "website": "website",
    "linkedin": "linkedin_url",
    "x": "x_url",
    "facebook": "facebook_url",
    "instagram": "instagram_url",
}

CONTACT_FIELDS = (
    "public_email",
    "website",
    "linkedin_url",
    "x_url",
    "facebook_url",
    "instagram_url",
    "other_social_urls",
)


def sanitize_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    if text[:1] in {"=", "+", "-", "@"}:
        return f"'{text}"
    return text


def format_csv_row(values: list[object]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([sanitize_cell(value) for value in values])
    return buffer.getvalue()


def _stamp(document: dict) -> datetime:
    value = (
        document.get("last_seen_at")
        or document.get("last_checked_at")
        or document.get("created_at")
    )
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


def _select(evidence: list[dict], kind: str) -> dict | None:
    matches = [
        item
        for item in evidence
        if item.get("kind") == kind and item.get("validation_status") == "valid"
    ]
    if not matches:
        return None
    return max(matches, key=_stamp)


def _join(values: list[str], separator: str) -> str:
    cleaned = [item for item in values if item]
    return separator.join(cleaned)


def _age_months(created: object, checked: datetime | None) -> str:
    if not isinstance(created, str) or not created:
        return ""
    start = datetime.fromisoformat(created.replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    end = checked or utcnow()
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    delta = relativedelta(end, start)
    return str(delta.years * 12 + delta.months)


def _plain_number(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return format(value, "g")
    return str(value)


def build_row(candidate: dict, evidence: list[dict]) -> dict[str, str]:
    selected = {kind: _select(evidence, kind) for kind in ("email", *KIND_COLUMNS)}
    email = selected["email"]
    other_rows = [
        item
        for item in evidence
        if item.get("kind") == "other_social" and item.get("validation_status") == "valid"
    ]
    other = [
        item["normalized_value"]
        for item in sorted(other_rows, key=lambda item: item.get("normalized_value") or "")
    ]
    sources: list[str] = []
    for item in (email, *(selected[kind] for kind in KIND_COLUMNS)):
        if item and item.get("source_url") and item["source_url"] not in sources:
            sources.append(item["source_url"])
    for item in evidence:
        if item.get("kind") == "other_social" and item.get("validation_status") == "valid":
            source = item.get("source_url")
            if source and source not in sources:
                sources.append(source)

    invalid_email = [
        item
        for item in evidence
        if item.get("kind") == "email" and item.get("validation_status") != "valid"
    ]
    if email:
        email_status = email.get("validation_status") or "valid"
        email_source = email.get("source_url") or ""
        public_email = email.get("normalized_value") or ""
    elif invalid_email:
        latest = max(invalid_email, key=_stamp)
        email_status = latest.get("validation_status") or "invalid"
        email_source = latest.get("source_url") or ""
        public_email = ""
    else:
        email_status = ""
        email_source = ""
        public_email = ""

    checked = candidate.get("last_checked_at")
    row = {
        "github_username": candidate.get("login") or "",
        "github_profile_url": candidate.get("profile_url") or "",
        "name": candidate.get("name") or "",
        "bio": candidate.get("bio") or "",
        "company": candidate.get("company") or "",
        "country": candidate.get("country") or "",
        "utc_offset": candidate.get("utc_offset") or "",
        "us_state": candidate.get("us_state") or "",
        "github_created_at": candidate.get("github_created_at") or "",
        "account_age_months": _age_months(candidate.get("github_created_at"), checked),
        "public_email": public_email,
        "website": (selected["website"] or {}).get("normalized_value") or "",
        "linkedin_url": (selected["linkedin"] or {}).get("normalized_value") or "",
        "x_url": (selected["x"] or {}).get("normalized_value") or "",
        "facebook_url": (selected["facebook"] or {}).get("normalized_value") or "",
        "instagram_url": (selected["instagram"] or {}).get("normalized_value") or "",
        "other_social_urls": _join(other, " | "),
        "public_repos_count": _plain_number(candidate.get("public_repos_count")),
        "primary_languages": _join(list(candidate.get("primary_languages") or []), "; "),
        "matched_keywords": _join(list(candidate.get("matched_keywords") or []), "; "),
        "relevant_repository_urls": _join(
            list(candidate.get("relevant_repository_urls") or []), " | "
        ),
        "email_source_url": email_source,
        "contact_source_urls": _join(sources, " | "),
        "email_validation_status": email_status,
        "location_confidence": _plain_number(candidate.get("location_confidence")),
        "last_checked_at": checked.isoformat()
        if isinstance(checked, datetime)
        else (checked or ""),
        "collection_status": candidate.get("collection_status") or "",
    }
    return {column: "" if row[column] is None else str(row[column]) for column in COLUMNS}


def has_public_contact(row: dict[str, str]) -> bool:
    return any(row.get(field) for field in CONTACT_FIELDS)


def dedupe_candidates(candidates: list[dict]) -> list[dict]:
    chosen: dict[int, dict] = {}
    for candidate in candidates:
        user_id = candidate["github_user_id"]
        current = chosen.get(user_id)
        if current is None or _stamp(candidate) >= _stamp(current):
            chosen[user_id] = candidate
    return [chosen[user_id] for user_id in sorted(chosen)]


async def stream_job_csv(db, job_id: str, *, include_no_contact: bool) -> AsyncIterator[str]:
    """Yield CSV lines from a Mongo cursor without holding every candidate."""

    yield format_csv_row(COLUMNS)
    cursor = db.candidates.find({"job_id": job_id}).sort(
        [("github_user_id", 1), ("last_checked_at", -1)]
    )
    seen: set[int] = set()
    async for candidate in cursor:
        user_id = candidate["github_user_id"]
        if user_id in seen:
            continue
        seen.add(user_id)
        evidence = await db.contact_evidence.find({"github_user_id": user_id}).to_list(None)
        row = build_row(candidate, evidence)
        if not include_no_contact and not has_public_contact(row):
            continue
        yield format_csv_row([row[column] for column in COLUMNS])
