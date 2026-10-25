"""Mongo-backed campaign queue. One recipient is sent at a time, off the request."""

import asyncio
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pymongo import ReturnDocument

from app.contacts import contact_facts
from app.db import utcnow
from app.mail import (
    CONNECTION_ERROR,
    CopyFailure,
    Mailer,
    MailFailure,
    UnknownPlaceholder,
    placeholder_values,
    render_template,
)

ACTIVE = frozenset({"queued", "running", "paused"})
WAIT_STEP_SECONDS = 5.0
CONNECTION_BACKOFF = (120, 300, 600, 900)
INTERRUPTED = "Send was interrupted and was not retried."


class CampaignBusy(RuntimeError):
    pass


class NoRecipients(ValueError):
    pass


def _aware(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _iso(value: object) -> str | None:
    stamp = _aware(value)
    if stamp is None:
        return None
    return stamp.isoformat()


class CampaignRunner:
    def __init__(
        self,
        db,
        mailer: Mailer,
        sleeper: Callable | None = None,
        clock: Callable[[], datetime] | None = None,
        rng: Callable[[], float] | None = None,
    ) -> None:
        self.db = db
        self.mailer = mailer
        self.sleeper = sleeper or asyncio.sleep
        self.clock = clock or utcnow
        self.rng = rng or random.random
        self._tasks: dict[str, asyncio.Task] = {}
        self._connection_waits: dict[str, int] = {}

    async def kick(self, campaign_id: str) -> None:
        campaign = await self.db.campaigns.find_one({"_id": campaign_id})
        if campaign is None:
            raise KeyError(campaign_id)
        if campaign["status"] not in ACTIVE:
            raise RuntimeError("campaign is finished")
        await self.db.campaigns.update_one(
            {"_id": campaign_id},
            {
                "$set": {
                    "status": "running",
                    "control": None,
                    "error": None,
                    "updated_at": self.clock(),
                }
            },
        )
        current = self._tasks.get(campaign_id)
        if current is not None and not current.done():
            return
        self._tasks[campaign_id] = asyncio.create_task(self._run(campaign_id))

    async def resume_running(self) -> None:
        """Continue a campaign left running when the process stopped."""

        cursor = self.db.campaigns.find({"status": "running"}, {"_id": 1})
        async for campaign in cursor:
            campaign_id = campaign["_id"]
            current = self._tasks.get(campaign_id)
            if current is not None and not current.done():
                continue
            self._tasks[campaign_id] = asyncio.create_task(self._run(campaign_id))

    async def _run(self, campaign_id: str) -> None:
        try:
            await self._fail_interrupted(campaign_id)
            while True:
                if await self._stopped(campaign_id):
                    return
                if not await self._wait_for_cap(campaign_id):
                    return
                recipient = await self._claim(campaign_id)
                if recipient is None:
                    await self._finish(campaign_id, "succeeded", None)
                    return
                if await self._send_one(campaign_id, recipient):
                    return
                remaining = await self.db.campaign_recipients.count_documents(
                    {"campaign_id": campaign_id, "status": "queued"}
                )
                if remaining == 0:
                    await self._finish(campaign_id, "succeeded", None)
                    return
                if not await self._delay(campaign_id):
                    return
        except Exception as exc:
            await self._finish(campaign_id, "failed", str(exc)[:300])

    async def _fail_interrupted(self, campaign_id: str) -> None:
        await self.db.campaign_recipients.update_many(
            {"campaign_id": campaign_id, "status": "sending"},
            {
                "$set": {
                    "status": "failed",
                    "error": INTERRUPTED,
                    "updated_at": self.clock(),
                }
            },
        )

    async def _stopped(self, campaign_id: str) -> bool:
        campaign = await self.db.campaigns.find_one({"_id": campaign_id}, {"control": 1})
        control = (campaign or {}).get("control")
        if control == "cancel":
            await self._finish(campaign_id, "cancelled", None)
            return True
        if control == "pause":
            await self._finish(campaign_id, "paused", None)
            return True
        return False

    async def _wait_for_cap(self, campaign_id: str) -> bool:
        while True:
            if await self._stopped(campaign_id):
                return False
            campaign = await self.db.campaigns.find_one(
                {"_id": campaign_id},
                {"hourly_limit": 1},
            )
            if campaign is None:
                return False
            limit = int(campaign.get("hourly_limit") or 1)
            now = self.clock()
            window_start = now - timedelta(hours=1)
            query = {
                "campaign_id": campaign_id,
                "status": "sent",
                "sent_at": {"$gt": window_start},
            }
            sent = await self.db.campaign_recipients.count_documents(query)
            if sent < limit:
                return True
            oldest = await self.db.campaign_recipients.find_one(query, sort=[("sent_at", 1)])
            sent_at = _aware((oldest or {}).get("sent_at"))
            if sent_at is None:
                return True
            remaining = (sent_at + timedelta(hours=1) - now).total_seconds()
            if remaining <= 0:
                continue
            await self.sleeper(min(WAIT_STEP_SECONDS, remaining))

    async def _delay(self, campaign_id: str) -> bool:
        campaign = await self.db.campaigns.find_one(
            {"_id": campaign_id},
            {"delay_min_seconds": 1, "delay_max_seconds": 1},
        )
        if campaign is None:
            return False
        low = float(campaign.get("delay_min_seconds") or 0)
        high = float(campaign.get("delay_max_seconds") or 0)
        if high < low:
            high = low
        delay = low if high == low else low + (high - low) * float(self.rng())
        remaining = delay
        while remaining > 0:
            if await self._stopped(campaign_id):
                return False
            step = min(WAIT_STEP_SECONDS, remaining)
            await self.sleeper(step)
            remaining -= step
        return True

    async def _claim(self, campaign_id: str) -> dict | None:
        return await self.db.campaign_recipients.find_one_and_update(
            {"campaign_id": campaign_id, "status": "queued"},
            {"$set": {"status": "sending", "updated_at": self.clock()}},
            sort=[("position", 1)],
            return_document=ReturnDocument.AFTER,
        )

    async def _send_one(self, campaign_id: str, recipient: dict) -> bool:
        """Return True when the campaign must stop."""

        campaign = await self.db.campaigns.find_one({"_id": campaign_id})
        if campaign is None:
            return True
        login = str(recipient.get("login") or "")
        email = str(recipient.get("to_email") or "")
        name = recipient.get("name") if isinstance(recipient.get("name"), str) else None
        try:
            values = placeholder_values(name=name, login=login, email=email)
            subject = render_template(str(campaign.get("subject") or ""), values)
            body = render_template(str(campaign.get("body") or ""), values)
            copy_error = await asyncio.to_thread(
                self.mailer.send,
                recipient=email,
                subject=subject,
                body=body,
            )
        except MailFailure as exc:
            detail = str(exc)[:300]
            if exc.connection:
                await self._mark(recipient["_id"], "queued", None)
                await self.db.campaigns.update_one(
                    {"_id": campaign_id},
                    {"$set": {"error": detail, "updated_at": self.clock()}},
                )
                if not await self._wait_for_connection(campaign_id):
                    return True
                return False
            await self._mark(recipient["_id"], "failed", detail)
            if exc.stop:
                await self._finish(campaign_id, "failed", detail)
                return True
            return False
        except UnknownPlaceholder as exc:
            await self._mark(recipient["_id"], "failed", str(exc)[:300])
            await self._finish(campaign_id, "failed", str(exc)[:300])
            return True
        sent_at = self.clock()
        await self._mark(
            recipient["_id"],
            "sent",
            None,
            sent_at=sent_at,
            copy_error=copy_error,
            copied_at=None if copy_error else sent_at,
        )
        await self.db.candidates.update_one(
            {"login": login},
            {"$set": {"outreach_status": "message_sent", "updated_at": self.clock()}},
        )
        await self._clear_connection_wait(campaign_id, campaign.get("error"))
        return False

    async def _wait_for_connection(self, campaign_id: str) -> bool:
        """Wait out a dropped mailbox connection. Return False when the run stops."""

        index = self._connection_waits.get(campaign_id, 0)
        seconds = CONNECTION_BACKOFF[min(index, len(CONNECTION_BACKOFF) - 1)]
        self._connection_waits[campaign_id] = index + 1
        remaining = float(seconds)
        while remaining > 0:
            if await self._stopped(campaign_id):
                return False
            step = min(WAIT_STEP_SECONDS, remaining)
            await self.sleeper(step)
            remaining -= step
        return True

    async def _clear_connection_wait(self, campaign_id: str, error: object) -> None:
        self._connection_waits.pop(campaign_id, None)
        if isinstance(error, str) and error.startswith(CONNECTION_ERROR):
            await self.db.campaigns.update_one(
                {"_id": campaign_id},
                {"$set": {"error": None, "updated_at": self.clock()}},
            )

    async def _mark(
        self,
        recipient_id: str,
        status: str,
        error: str | None,
        sent_at: datetime | None = None,
        copy_error: str | None = None,
        copied_at: datetime | None = None,
    ) -> None:
        fields: dict = {"status": status, "error": error, "updated_at": self.clock()}
        if sent_at is not None:
            fields["sent_at"] = sent_at
        if status == "sent":
            fields["copy_error"] = copy_error
            if copied_at is not None:
                fields["copied_at"] = copied_at
        await self.db.campaign_recipients.update_one({"_id": recipient_id}, {"$set": fields})

    async def recover_connection_failures(self) -> None:
        """Pause a campaign stopped by a dropped connection and requeue that send.

        The message was not accepted, so Resume can try it again. This does not
        start sending.
        """

        cursor = self.db.campaigns.find({"status": "failed"})
        async for campaign in cursor:
            error = campaign.get("error")
            if not isinstance(error, str) or not error.startswith(CONNECTION_ERROR):
                continue
            rows = self.db.campaign_recipients.find(
                {"campaign_id": campaign["_id"], "status": "failed", "sent_at": None}
            )
            async for recipient in rows:
                message = recipient.get("error")
                if not isinstance(message, str) or not message.startswith(CONNECTION_ERROR):
                    continue
                await self._mark(recipient["_id"], "queued", None)
            await self.db.campaigns.update_one(
                {"_id": campaign["_id"]},
                {
                    "$set": {
                        "status": "paused",
                        "control": None,
                        "updated_at": self.clock(),
                    }
                },
            )

    async def copy_sent(self) -> None:
        """File Sent copies for accepted messages that do not have one yet."""

        cursor = self.db.campaign_recipients.find({"status": "sent", "copied_at": None})
        async for recipient in cursor:
            try:
                await self._copy_one(recipient)
            except (CopyFailure, UnknownPlaceholder, OSError, RuntimeError, TimeoutError) as exc:
                await self.db.campaign_recipients.update_one(
                    {"_id": recipient["_id"]},
                    {"$set": {"copy_error": str(exc)[:300], "updated_at": self.clock()}},
                )

    async def _copy_one(self, recipient: dict) -> None:
        campaign = await self.db.campaigns.find_one({"_id": recipient.get("campaign_id")})
        if campaign is None:
            raise RuntimeError("The message campaign was not found.")
        email = str(recipient.get("to_email") or "")
        if not email:
            raise RuntimeError("No public email.")
        login = str(recipient.get("login") or "")
        name = recipient.get("name") if isinstance(recipient.get("name"), str) else None
        values = placeholder_values(name=name, login=login, email=email)
        subject = render_template(str(campaign.get("subject") or ""), values)
        body = render_template(str(campaign.get("body") or ""), values)
        sent_at = _aware(recipient.get("sent_at"))
        await asyncio.to_thread(
            self.mailer.save_copy,
            recipient=email,
            subject=subject,
            body=body,
            sent_at=sent_at,
        )
        await self.db.campaign_recipients.update_one(
            {"_id": recipient["_id"]},
            {
                "$set": {
                    "copied_at": self.clock(),
                    "copy_error": None,
                    "updated_at": self.clock(),
                }
            },
        )

    async def _finish(self, campaign_id: str, status: str, error: str | None) -> None:
        await self.db.campaigns.update_one(
            {"_id": campaign_id},
            {
                "$set": {
                    "status": status,
                    "control": None,
                    "error": error,
                    "updated_at": self.clock(),
                }
            },
        )


async def _evidence_for(db, user_ids: list[int]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {user_id: [] for user_id in user_ids}
    if not user_ids:
        return grouped
    cursor = db.contact_evidence.find({"github_user_id": {"$in": user_ids}})
    for item in await cursor.to_list(None):
        user_id = item.get("github_user_id")
        if isinstance(user_id, int):
            grouped.setdefault(user_id, []).append(item)
    return grouped


async def queue_campaign(db, body) -> dict:
    """Store one campaign. Contacts without a public email are skipped, not queued."""

    active = await db.campaigns.count_documents({"status": {"$in": list(ACTIVE)}})
    if active:
        raise CampaignBusy("A message send is already in progress.")
    candidates = await db.candidates.find({"login": {"$in": body.logins}}).to_list(None)
    by_login = {
        candidate["login"]: candidate
        for candidate in candidates
        if isinstance(candidate.get("login"), str)
    }
    user_ids = [
        candidate["github_user_id"]
        for candidate in by_login.values()
        if isinstance(candidate.get("github_user_id"), int)
    ]
    grouped = await _evidence_for(db, user_ids)
    now = utcnow()
    campaign_id = str(uuid4())
    rows: list[dict] = []
    queued = 0
    for position, login in enumerate(body.logins):
        candidate = by_login.get(login)
        if candidate is None:
            rows.append(_recipient(campaign_id, position, login, None, None, "skipped", now))
            rows[-1]["error"] = "Contact was not found."
            continue
        user_id = candidate.get("github_user_id")
        evidence = grouped.get(user_id, []) if isinstance(user_id, int) else []
        email = contact_facts(candidate, evidence).get("public_email")
        name = candidate.get("name") if isinstance(candidate.get("name"), str) else None
        if not email:
            row = _recipient(campaign_id, position, login, name, None, "skipped", now)
            row["error"] = "No public email."
            rows.append(row)
            continue
        rows.append(_recipient(campaign_id, position, login, name, str(email), "queued", now))
        queued += 1
    if queued == 0:
        raise NoRecipients("None of the selected contacts have a public email.")
    campaign = {
        "_id": campaign_id,
        "subject": body.subject,
        "body": body.body,
        "hourly_limit": body.hourly_limit,
        "delay_min_seconds": body.delay_min_seconds,
        "delay_max_seconds": body.delay_max_seconds,
        "status": "queued",
        "control": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.campaigns.insert_one(campaign)
    await db.campaign_recipients.insert_many(rows)
    others = await db.campaigns.count_documents(
        {"_id": {"$ne": campaign_id}, "status": {"$in": list(ACTIVE)}}
    )
    if others:
        await db.campaign_recipients.delete_many({"campaign_id": campaign_id})
        await db.campaigns.delete_one({"_id": campaign_id})
        raise CampaignBusy("A message send is already in progress.")
    return campaign


def _recipient(
    campaign_id: str,
    position: int,
    login: str,
    name: str | None,
    email: str | None,
    status: str,
    now: datetime,
) -> dict:
    return {
        "_id": str(uuid4()),
        "campaign_id": campaign_id,
        "position": position,
        "login": login,
        "name": name,
        "to_email": email,
        "status": status,
        "error": None,
        "sent_at": None,
        "created_at": now,
        "updated_at": now,
    }


async def present_campaign(db, document: dict) -> dict:
    campaign_id = document["_id"]
    counts = {
        "queued": await _status_count(db, campaign_id, ["queued", "sending"]),
        "sent": await _status_count(db, campaign_id, ["sent"]),
        "failed": await _status_count(db, campaign_id, ["failed"]),
        "skipped": await _status_count(db, campaign_id, ["skipped"]),
    }
    cursor = db.campaign_recipients.find(
        {"campaign_id": campaign_id, "status": "failed"},
        {"login": 1, "error": 1},
    ).sort([("position", 1)])
    failures = await cursor.to_list(20)
    return {
        "id": campaign_id,
        "status": document.get("status"),
        "subject": document.get("subject") or "",
        "body": document.get("body") or "",
        "hourly_limit": int(document.get("hourly_limit") or 0),
        "delay_min_seconds": int(document.get("delay_min_seconds") or 0),
        "delay_max_seconds": int(document.get("delay_max_seconds") or 0),
        "counts": counts,
        "failures": [
            {"login": item.get("login") or "", "error": item.get("error")} for item in failures
        ],
        "error": document.get("error"),
        "created_at": _iso(document.get("created_at")),
        "updated_at": _iso(document.get("updated_at")),
    }


async def _status_count(db, campaign_id: str, statuses: list[str]) -> int:
    return int(
        await db.campaign_recipients.count_documents(
            {"campaign_id": campaign_id, "status": {"$in": statuses}}
        )
    )
