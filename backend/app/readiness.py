"""Readiness checks behind ``GET /api/ready``.

``/api/health`` answers a constant and says the process is alive. ``/api/ready``
says it can serve: MongoDB answers a ping and the GitHub App installation token
can be minted (or is cached). Each failing component is named in the response
and logged; the route answers 503 when any check fails.
"""

import logging

log = logging.getLogger(__name__)


async def readiness_checks(app) -> dict[str, str]:
    checks: dict[str, str] = {}
    try:
        await app.state.db.command("ping")
        checks["mongodb"] = "ok"
    except Exception as exc:
        log.warning(
            "readiness check failed",
            extra={"component": "mongodb", "error": type(exc).__name__},
        )
        checks["mongodb"] = "failed: ping did not return"
    try:
        await app.state.github.auth.token()
        checks["github"] = "ok"
    except Exception as exc:
        log.warning(
            "readiness check failed",
            extra={"component": "github", "error": str(exc)[:200]},
        )
        checks["github"] = f"failed: {str(exc)[:200] or type(exc).__name__}"
    return checks


def is_ready(checks: dict[str, str]) -> bool:
    return all(value == "ok" for value in checks.values())
