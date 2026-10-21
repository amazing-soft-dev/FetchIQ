# Contributing to FetchIQ

## Branches

`dev` is the integration branch. `main` is the release branch. Short-lived branches open into `dev`, and `dev` promotes to `main`.

Create the issue with `GH_TOKEN` before editing, then branch from the latest `dev`:

```bash
gh issue create --title "Short summary" --body "What is wrong and what should happen."
git fetch origin
git checkout -b fix/<short-description> origin/dev
```

Use one of these branch prefixes:

- `feat/<short-description>` for user-facing behavior
- `fix/<short-description>` for bug fixes
- `docs/<short-description>` for documentation
- `ci/<short-description>` for workflow changes
- `build/<short-description>` for dependency and build setup

Put `Closes #<issue>` in the commit message. Do not push directly to `dev` or `main`.

Pushing the branch runs [`.github/workflows/open-pull-request.yml`](.github/workflows/open-pull-request.yml), which opens the pull request into `dev`. After CI passes, [`.github/workflows/merge-passing-pr.yml`](.github/workflows/merge-passing-pr.yml) merges it. That push to `dev` opens `dev` into `main`, and the merge workflow merges that pull request after CI passes.

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
