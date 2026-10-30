"""Last-resort email lookup from the author of a user's earliest commits.

Used only when the profile API, the profile page, and every README published no
email. It reads the first three commits of the three oldest owned repositories,
oldest first, through each commit's ``.patch`` page, and stops at the first
usable address. Only the ``From:`` header is read, never the diff. Noreply and
placeholder addresses are skipped and never stored.
"""

import math

from app.extract import EMAIL_RE, normalize_email
from app.github import GitHubClient, parse_last_page

MAX_REPOS = 3
MAX_COMMITS = 3


def patch_author_email(patch: str) -> str | None:
    """The usable address in the ``From:`` header of a ``.patch`` page."""

    headers: list[str] = []
    for line in patch.splitlines():
        if not line.strip():
            break
        if line[0] in " \t" and headers:
            headers[-1] += line
        else:
            headers.append(line)
    for header in headers:
        if header[:5].lower() != "from:":
            continue
        for candidate in EMAIL_RE.findall(header):
            email = normalize_email(candidate)
            if email:
                return email
    return None


async def find_commit_email(github: GitHubClient, login: str) -> tuple[str, str] | None:
    """Return ``(email, commit_url)`` from the earliest commits, or None."""

    for repo in await _oldest_repos(github, login):
        for commit_url in await _oldest_commit_urls(github, login, repo):
            response = await github.get(f"{commit_url}.patch", headers={"Accept": "text/plain"})
            if response.status_code != 200:
                continue
            email = patch_author_email(response.text)
            if email:
                return email, commit_url
    return None


async def _oldest_repos(github: GitHubClient, login: str) -> list[str]:
    response = await github.get(
        f"/users/{login}/repos",
        params={"per_page": 100, "type": "owner", "sort": "created", "direction": "asc"},
    )
    if response.status_code >= 400:
        return []
    payload = response.json()
    if not isinstance(payload, list):
        return []
    owned = [
        repo
        for repo in payload
        if isinstance(repo, dict) and repo.get("name") and not repo.get("fork")
    ]
    owned.sort(key=lambda repo: str(repo.get("created_at") or ""))
    return [str(repo["name"]) for repo in owned[:MAX_REPOS]]


async def _oldest_commit_urls(github: GitHubClient, login: str, repo: str) -> list[str]:
    """URLs of the first commits of a repository, oldest first."""

    path = f"/repos/{login}/{repo}/commits"
    head = await github.get(path, params={"per_page": 1})
    if head.status_code >= 400:
        return []
    first = head.json()
    if not isinstance(first, list):
        return []
    total = parse_last_page(head.headers.get("link")) or len(first)
    if total == 0:
        return []
    # Commits are listed newest first, so the oldest ones sit on the last page.
    last = math.ceil(total / MAX_COMMITS)
    commits = list(reversed(await _commit_page(github, path, last)))
    if len(commits) < MAX_COMMITS and last > 1:
        earlier = list(reversed(await _commit_page(github, path, last - 1)))
        commits += earlier[: MAX_COMMITS - len(commits)]
    urls: list[str] = []
    for commit in commits:
        url = commit.get("html_url")
        if not isinstance(url, str) and isinstance(commit.get("sha"), str):
            url = f"https://github.com/{login}/{repo}/commit/{commit['sha']}"
        if isinstance(url, str):
            urls.append(url)
    return urls


async def _commit_page(github: GitHubClient, path: str, page: int) -> list[dict]:
    response = await github.get(path, params={"per_page": MAX_COMMITS, "page": page})
    if response.status_code >= 400:
        return []
    payload = response.json()
    if not isinstance(payload, list):
        return []
    return [commit for commit in payload if isinstance(commit, dict)]
