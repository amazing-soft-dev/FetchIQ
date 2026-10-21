# Contributing to FetchIQ

## Branches

`dev` is the integration branch. `main` is the release branch. Short-lived branches open into `dev`, and `dev` promotes to `main`.

Run **Start change**, then check out the branch named in the issue, then edit. The workflow is [`.github/workflows/start-change.yml`](.github/workflows/start-change.yml). It creates an empty branch from the latest `dev`:

- `feat/<short-description>` for user-facing behavior
- `fix/<short-description>` for bug fixes
- `docs/<short-description>` for documentation
- `ci/<short-description>` for workflow changes
- `build/<short-description>` for dependency and build setup

Open a pull request into `dev`. Do not push directly to `dev` or `main`. After that pull request is merged and checks pass, open a pull request from `dev` into `main` and merge it with a merge commit.

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

GitHub Actions runs these same checks on pull requests and pushes to `dev` and `main`. The workflow is [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

- **Backend:** Python 3.12, install `backend/requirements.txt`, Ruff, pytest against the in-memory database name `fetchiq_test`.
- **Frontend:** Node 22, `npm ci`, typecheck, lint, and `next build`.

The workflow token can read repository contents only. Third-party actions are pinned to commit SHAs. Dependabot opens weekly update pull requests for GitHub Actions, pip, and npm. Those pull requests are not merged automatically.

## Deployment

FetchIQ has no staging or production deployment in this repository. There is no hosting manifest, GitHub Environment, deploy credential, automatic production release, or rollback automation. Merging to `main` runs the validation workflow only. Do not add a deploy job until a target environment, separate secrets, and an approval policy exist.
