"""Optional bearer-token guard for the API.

When ``API_TOKEN`` is set, every router requires ``Authorization: Bearer <token>``.
The two CSV export routes are opened by the browser as plain links, which cannot
carry a header, so they also accept ``?token=<token>``. ``/api/health`` is not on
a router and stays open for liveness probes. With the setting empty nothing is
checked, so local development needs no token.
"""

import secrets

from fastapi import HTTPException, Query, Request

_TOKEN_QUERY = Query(default=None, include_in_schema=False)


def _bearer(request: Request) -> str:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()


def _accepts_query_token(request: Request) -> bool:
    return request.url.path.endswith("/export.csv")


async def require_token(request: Request, token: str | None = _TOKEN_QUERY) -> None:
    settings = getattr(request.app.state, "settings", None)
    expected = (getattr(settings, "api_token", "") or "").strip()
    if not expected:
        return
    presented = _bearer(request)
    if not presented and token and _accepts_query_token(request):
        presented = token.strip()
    if not presented or not secrets.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(
            status_code=401,
            detail="API token missing or wrong",
            headers={"WWW-Authenticate": "Bearer"},
        )
