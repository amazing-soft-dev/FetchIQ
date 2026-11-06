"""Run pending data migrations by hand.

From ``backend/``::

    python scripts/migrate.py            # apply pending steps, then create indexes
    python scripts/migrate.py --dry-run  # show the stored and pending versions only

The API runs the same steps at startup, so this is for applying them ahead of a
deploy or after restoring a database.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.db import connect, ensure_indexes  # noqa: E402
from app.logs import configure_logging  # noqa: E402
from app.migrations import LATEST, current_version, pending_migrations, run_migrations  # noqa: E402


async def main(dry_run: bool) -> int:
    settings = Settings()
    configure_logging(settings.log_level)
    client, db = await connect(settings)
    try:
        version = await current_version(db)
        pending = await pending_migrations(db)
        print(f"{settings.database_name}: schema version {version}, latest {LATEST}")
        if not pending:
            print("nothing to apply")
            return 0
        if dry_run:
            print("pending: " + ", ".join(str(number) for number in pending))
            return 0
        applied = await run_migrations(db)
        await ensure_indexes(db)
        print("applied: " + ", ".join(str(number) for number in applied))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="list pending steps only")
    sys.exit(asyncio.run(main(parser.parse_args().dry_run)))
