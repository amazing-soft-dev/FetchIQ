"""Write the API's OpenAPI document without starting a server.

From ``backend/``::

    python scripts/openapi.py                # writes openapi.json next to this file's parent
    python scripts/openapi.py --out -        # prints it

The frontend's ``npm run types`` turns the document into ``lib/api-types.ts``,
and CI regenerates it to catch drift between the two halves.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "openapi.json"


def document() -> dict:
    # Settings are not read from the environment: no database or GitHub App is needed
    # to describe the routes.
    app = create_app(settings=Settings(mongodb_uri="mongodb://unused", database_name="fetchiq"))
    return app.openapi()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="file path, or - for stdout")
    args = parser.parse_args()
    text = json.dumps(document(), indent=2, sort_keys=True) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
