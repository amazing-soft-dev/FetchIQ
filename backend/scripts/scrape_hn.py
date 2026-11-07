"""Read developer contacts from Hacker News "Who wants to be hired?" threads.

From ``backend/``::

    python scripts/scrape_hn.py                # the newest thread into MongoDB
    python scripts/scrape_hn.py --threads 3    # the newest three months
    python scripts/scrape_hn.py --all          # every thread whoishiring ever posted
    python scripts/scrape_hn.py --dry-run      # parse and print, write nothing

Rows land in the same ``candidates`` and ``contact_evidence`` collections the
GitHub pipeline fills, with ``source: "hackernews"`` (see ``app.hackernews``).
Re-running a month follows the GitHub duplicate rules: people stored within two
weeks or already holding an email are left alone unless the post adds a personal
address; the summary line reports them as ``fresh`` and ``has email``.
``MONGODB_URI`` and ``DATABASE_NAME`` come from ``.env`` as for the API.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.db import connect, ensure_indexes  # noqa: E402
from app.hackernews import (  # noqa: E402
    HackerNewsClient,
    parse_comment,
    scrape,
    store_thread,
    thread_from_item,
)
from app.logs import configure_logging  # noqa: E402


def summary(thread, result) -> str:
    return (
        f"{thread.title}: inserted {result.inserted}, updated {result.updated}, "
        f"merged {result.merged}, fresh {result.fresh}, has email {result.has_email}, "
        f"skipped {result.skipped}, evidence rows {result.evidence}"
    )


async def dry_run(threads: int | None, limit: int) -> int:
    async with HackerNewsClient() as hn:
        found = await hn.hiring_threads(limit=threads)
        for thread in found:
            comments = await hn.comments(thread)
            print(f"{thread.title}  ({thread.url})  top-level comments: {len(comments)}")
            with_email = 0
            for comment in comments:
                contact = parse_comment(comment)
                with_email += bool(contact.emails)
            print(f"  posts with an email: {with_email}")
            for comment in comments[:limit]:
                contact = parse_comment(comment)
                print(
                    json.dumps(
                        {
                            "login": contact.login,
                            "comment_url": contact.source_url,
                            "location": contact.location_raw,
                            "utc_offset": contact.utc_offset,
                            "remote": contact.remote,
                            "relocate": contact.relocate,
                            "role": contact.role,
                            "emails": contact.emails,
                            "linkedin": contact.linkedin_url,
                            "github": contact.github_url,
                            "resume": contact.resume_url,
                            "urls": contact.urls,
                            "technologies": contact.technologies[:8],
                        },
                        ensure_ascii=False,
                    )
                )
    return 0


async def run(threads: int | None) -> int:
    settings = Settings()
    configure_logging(settings.log_level)
    client, db = await connect(settings)
    try:
        await ensure_indexes(db)
        results = await scrape(db, threads=threads)
        for thread, result in results:
            print(summary(thread, result))
        return 0
    finally:
        await client.close()


async def run_one(thread_id: int) -> int:
    """Store a single thread by id, for re-reading one month."""

    settings = Settings()
    configure_logging(settings.log_level)
    client, db = await connect(settings)
    try:
        await ensure_indexes(db)
        async with HackerNewsClient() as hn:
            item = await hn.item(thread_id)
            thread = thread_from_item(item or {})
            if thread is None:
                print(f"item {thread_id} is not a 'Who wants to be hired?' thread")
                return 1
            comments = await hn.comments(thread)
            result = await store_thread(db, thread, comments)
        print(summary(thread, result))
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--threads", type=int, default=1, help="newest N threads (default 1)")
    parser.add_argument("--all", action="store_true", help="every thread ever posted")
    parser.add_argument("--thread-id", type=int, help="one HN item id to read instead")
    parser.add_argument("--dry-run", action="store_true", help="parse and print, write nothing")
    parser.add_argument("--show", type=int, default=5, help="posts to print in a dry run")
    args = parser.parse_args()
    count = None if args.all else args.threads
    if args.dry_run:
        sys.exit(asyncio.run(dry_run(count, args.show)))
    if args.thread_id:
        sys.exit(asyncio.run(run_one(args.thread_id)))
    sys.exit(asyncio.run(run(count)))
