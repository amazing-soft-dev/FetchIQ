# Contributing to FetchIQ

## Branches

`main` is the only long-lived branch on the remote, and it is the integration target for current work. `dev` was the integration branch for the first feature pull requests and was merged into `main` in pull request #11. That branch is not on the remote now. Do not recreate it unless the team explicitly decides to restore a separate integration branch.

Use a short-lived branch from the latest `main`:

- `feat/<short-description>` for user-facing behavior
- `fix/<short-description>` for bug fixes
- `docs/<short-description>` for documentation
- `ci/<short-description>` for workflow changes
- `build/<short-description>` for dependency and build setup

Open a pull request into `main`. Do not push directly to `main`.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(<optional-scope>): <short description>
```

Types used in this repository include `feat`, `fix`, `docs`, `test`, `build`, and `ci`.

## Checks before opening a pull request

Backend dependencies come from `backend/requirements.txt`. That file does not install the `app` package. Pytest imports `app` because `backend/pyproject.toml` sets `pythonpath = ["."]`, so run the backend commands from `backend/`.

```bash
python -m pip install -r backend/requirements.txt
cd backend
python -m ruff check .
python -m ruff format --check .
DATABASE_NAME=fetchiq_test python -m pytest
```

```bash
cd frontend
npm ci
npm run typecheck
npm run lint
npm run build
```

GitHub Actions runs these same checks on pull requests and pushes to `main` (and to `dev` if that branch exists again). The workflow is [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

- **Backend:** Python 3.12, install `backend/requirements.txt`, Ruff, pytest against the in-memory database name `fetchiq_test`.
- **Frontend:** Node 22, `npm ci`, typecheck, lint, and `next build`.

The workflow token can read repository contents only. Third-party actions are pinned to commit SHAs. Dependabot opens weekly update pull requests for GitHub Actions, pip, and npm. Those pull requests are not merged automatically.

## Deployment

FetchIQ has no staging or production deployment in this repository. There is no hosting manifest, GitHub Environment, deploy credential, automatic production release, or rollback automation. Merging to `main` runs the validation workflow only. Do not add a deploy job until a target environment, separate secrets, and an approval policy exist.
