"""Per-job counters kept on the job document, so a poll reads one document.

``counts.discovered``
    Candidates whose ``job_id`` is this job.
``counts.enriched``
    Those candidates with ``enrichment_status`` ``"enriched"``.
``counts.emails``
    Email evidence rows across this job's candidates.
``counts.linkedin_without_email``
    Candidates with LinkedIn evidence and no email evidence.
``counts.errors``
    Enrichment misses, unchanged.

Each candidate records the share it last added to its job under ``counted``
(``emails`` and ``linkedin_without_email``). Enrichment credits the difference
between the new share and the recorded one, and a move between jobs carries the
recorded share from the old job to the new one. ``recount_jobs`` rebuilds every
counter from the collections and is migration 3.
"""

from collections.abc import Iterable

from app.database import Database

CONTACT_KINDS = ("email", "linkedin")
CONTACT_COUNTS = ("emails", "linkedin_without_email")
JOB_COUNTS = ("discovered", "enriched", *CONTACT_COUNTS)

_EVIDENCE_BATCH = 500


def contact_share(evidence: Iterable[dict]) -> dict[str, int]:
    """What one candidate's evidence adds to ``emails`` and ``linkedin_without_email``."""

    emails = 0
    linkedin = False
    for item in evidence:
        kind = item.get("kind")
        if kind == "email":
            emails += 1
        elif kind == "linkedin":
            linkedin = True
    return {"emails": emails, "linkedin_without_email": int(linkedin and emails == 0)}


def recorded_share(candidate: dict) -> dict[str, int]:
    stored = candidate.get("counted") or {}
    return {key: int(stored.get(key) or 0) for key in CONTACT_COUNTS}


def candidate_share(candidate: dict) -> dict[str, int]:
    """Everything a candidate currently adds to its job's counters."""

    share = recorded_share(candidate)
    share["discovered"] = 1
    share["enriched"] = int(candidate.get("enrichment_status") == "enriched")
    return share


def zero_share() -> dict[str, int]:
    return dict.fromkeys(JOB_COUNTS, 0)


async def evidence_kinds(db: Database, github_user_id: int) -> list[dict]:
    cursor = db.contact_evidence.find(
        {"github_user_id": github_user_id, "kind": {"$in": list(CONTACT_KINDS)}},
        {"kind": 1},
    )
    return [document async for document in cursor]


async def credit_candidate(
    db: Database,
    job_id: str,
    candidate: dict,
    evidence: list[dict] | None = None,
    *,
    enriched: int = 0,
) -> dict[str, int]:
    """Record the candidate's current share and apply the change to its job.

    ``evidence`` is the candidate's contact evidence when the caller already has
    it; otherwise it is read. ``enriched`` is added to ``counts.enriched`` as given.
    """

    if evidence is None:
        evidence = await evidence_kinds(db, candidate["github_user_id"])
    share = contact_share(evidence)
    before = recorded_share(candidate)
    if share != before:
        await db.candidates.update_one({"_id": candidate["_id"]}, {"$set": {"counted": share}})
    delta = {key: share[key] - before[key] for key in CONTACT_COUNTS}
    delta["enriched"] = enriched
    inc = {f"counts.{key}": value for key, value in delta.items() if value}
    if inc:
        await db.search_jobs.update_one({"_id": job_id}, {"$inc": inc})
    return share


async def recount_jobs(db: Database) -> int:
    """Rebuild every job's counters and every candidate's recorded share.

    Streams the candidate and evidence collections once each instead of loading
    them; the per-user tallies it keeps are two integers per GitHub user.
    """

    rows: list[tuple[object, str | None, int | None, bool]] = []
    cursor = db.candidates.find({}, {"job_id": 1, "github_user_id": 1, "enrichment_status": 1})
    async for candidate in cursor:
        user_id = candidate.get("github_user_id")
        rows.append(
            (
                candidate["_id"],
                candidate.get("job_id"),
                user_id if isinstance(user_id, int) else None,
                candidate.get("enrichment_status") == "enriched",
            )
        )

    emails: dict[int, int] = {}
    linkedin: set[int] = set()
    user_ids = sorted({user_id for _id, _job, user_id, _enriched in rows if user_id is not None})
    for start in range(0, len(user_ids), _EVIDENCE_BATCH):
        batch = user_ids[start : start + _EVIDENCE_BATCH]
        evidence = db.contact_evidence.find(
            {"github_user_id": {"$in": batch}, "kind": {"$in": list(CONTACT_KINDS)}},
            {"github_user_id": 1, "kind": 1},
        )
        async for item in evidence:
            user_id = item.get("github_user_id")
            if not isinstance(user_id, int):
                continue
            if item.get("kind") == "email":
                emails[user_id] = emails.get(user_id, 0) + 1
            elif item.get("kind") == "linkedin":
                linkedin.add(user_id)

    totals: dict[str, dict[str, int]] = {}
    for candidate_id, job_id, user_id, enriched in rows:
        count = emails.get(user_id, 0) if user_id is not None else 0
        share = {
            "emails": count,
            "linkedin_without_email": int(user_id in linkedin and count == 0),
        }
        await db.candidates.update_one({"_id": candidate_id}, {"$set": {"counted": share}})
        if not isinstance(job_id, str):
            continue
        bucket = totals.setdefault(job_id, zero_share())
        bucket["discovered"] += 1
        bucket["enriched"] += int(enriched)
        bucket["emails"] += share["emails"]
        bucket["linkedin_without_email"] += share["linkedin_without_email"]

    updated = 0
    async for job in db.search_jobs.find({}, {"_id": 1}):
        counts = totals.get(job["_id"], zero_share())
        await db.search_jobs.update_one(
            {"_id": job["_id"]},
            {"$set": {f"counts.{key}": value for key, value in counts.items()}},
        )
        updated += 1
    return updated
