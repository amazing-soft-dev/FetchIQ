# Implementation plan: roadmap Phases 0, 1 and 2

Companion to [ROADMAP.md](ROADMAP.md). Written 2026-11-05.

Repository facts the plan relies on (checked today):

- `origin/dev` and `origin/main` hold the same content. The only commits on `main` not on `dev` are the five `dev -> main` merge commits, so every branch below starts from `origin/dev` with nothing to sync first.
- The GitHub CLI is logged in as `amazing-soft-dev` with `repo` and `workflow` scopes, which is enough for `gh issue create`, `gh pr`, and pushes.
- The last issue number is 162. Nine Dependabot PRs (144 to 152) are open against `main`.
- `.github/workflows/open-pull-request.yml` opens a PR into `dev` on every push to a `feat/`, `fix/`, `docs/`, `ci/` or `build/` branch, and `merge-passing-pr.yml` merges it once CI passes. Pushing a branch is therefore the same as submitting it.

## Working conventions

One issue per work item, one branch per issue, one PR per branch. The issue is created first so its number can go into the commit.

### Create the issue and branch

```bash
gh issue create --title "<issue title>" --body "<what is wrong and what should happen>" --label enhancement
```

```bash
git fetch origin
```

```bash
git checkout -b <prefix>/<short-description> origin/dev
```

Branch prefixes follow CONTRIBUTING: `feat/` for behavior, `fix/` for bugs, `build/` for dependencies and tooling, `ci/` for workflows, `docs/` for documentation.

### Commit message template

Every commit on these branches ends with the three trailers below. `Closes #<n>` goes in the body so the auto-opened PR links and closes the issue.

```text
<type>(<scope>): <short description>

<one or two sentences on what changed and why>

Closes #<n>

Co-authored-by: shark314 <112891100+shark314@users.noreply.github.com>
Co-authored-by: tomost8 <178904341+tomost8@users.noreply.github.com>
Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

### Before every push

```bash
cd backend && python -m ruff check . && python -m ruff format --check . && DATABASE_NAME=fetchiq_test python -m pytest
```

```bash
cd frontend && npm run typecheck && npm run lint && npm run build
```

Pushing opens the PR into `dev`. Wait for CI and the auto-merge before starting a branch that depends on it, since dependent branches start from `origin/dev`.

## Work items

Issue numbers are assigned when each issue is created. Estimates are working days for one person.

| ID | Phase | Branch | Issue title | Depends on | Days |
| --- | --- | --- | --- | --- | --- |
| P0-1 | 0 | `build/dependency-bumps` | Apply open Dependabot bumps in one branch and close PRs 144 to 152 | none | 0.5 |
| P0-2 | 0 | `build/drop-python-dateutil` | Replace python-dateutil with stdlib month arithmetic | none | 0.5 |
| P0-3 | 0 | `ci/python-version-matrix` | Test Python 3.12, 3.13 and 3.14 in CI and raise the floor to 3.12 | P0-1 | 0.5 |
| P0-4 | 0 | `ci/pin-node-lts` | Pin Node 24 in CI, `.nvmrc` and `engines` | none | 0.25 |
| P0-5 | 0 | `build/frontend-test-runner` | Add Vitest and first tests for lib/schemas, lib/message, lib/api | P0-1 | 1 |
| P0-6 | 0 | `ci/backend-coverage` | Report backend coverage in CI with pytest-cov | P0-3 | 0.5 |
| P0-7 | 0 | `feat/structured-logging` | Add structured logging to the API and job runners | none | 1 |
| P1-1 | 1 | `fix/job-counts-full-scan` | Job status endpoint scans every candidate and evidence row on each poll | none | 1.5 |
| P1-2 | 1 | `fix/unbounded-collection-reads` | Bound the twelve to_list(None) reads | none | 1.5 |
| P1-3 | 1 | `feat/versioned-migrations` | Move the duplicate-login cleanup out of ensure_indexes into a versioned migration | none | 1.5 |
| P1-4 | 1 | `feat/job-lease` | Lease running jobs and campaigns with a worker id and heartbeat | P1-3 | 2 |
| P1-5 | 1 | `feat/health-readiness` | Split health into liveness and a readiness check for MongoDB and the GitHub token | P0-7 | 0.5 |
| P1-6 | 1 | `build/docker-compose` | Dockerfiles for both services and a compose file with MongoDB | P0-4 | 1 |
| P1-7 | 1 | `feat/api-token-guard` | Require a bearer token on every API route when one is configured | none | 1 |
| P2-1a | 2 | `feat/database-protocol` | Hide Motor types behind a Database protocol | P1-3, P1-4 | 1.5 |
| P2-1b | 2 | `feat/pymongo-async-client` | Switch the production client from Motor to pymongo.asynchronous | P2-1a | 1.5 |
| P2-1c | 2 | `ci/mongodb-service-container` | Run tests against a MongoDB service container and drop mongomock-motor | P2-1b, P1-6 | 2 |
| P2-2a | 2 | `feat/typed-api-responses` | Give every route a Pydantic response model | P1-1, P1-2 | 2 |
| P2-2b | 2 | `build/generated-api-types` | Generate frontend/lib/types.ts from OpenAPI and check it in CI | P2-2a, P0-5 | 1 |
| P2-3 | 2 | `build/ts-strict-flags` | Add noUncheckedIndexedAccess and exactOptionalPropertyTypes while the ESLint shim stays | P0-5 | 1 |
| P2-4 | 2 | `feat/query-data-layer` | Replace the three setInterval polls with TanStack Query | P0-5 | 1.5 |
| P2-5 | 2 | `feat/app-route-boundaries` | Add error, loading and not-found routes and move initial loads to server components | P2-4 | 1.5 |

Total: about 26 working days. Phase 0 is five days, Phase 1 nine, Phase 2 twelve.

## Item details

### P0-1 Apply open Dependabot bumps

Edit `backend/requirements.txt` and `frontend/package.json` to the versions Dependabot proposes, run `npm install` to refresh the lockfile, run both check suites. Close PRs 144 to 152 with a comment pointing at this branch. Leave `python-dateutil` at 3.0.0 here; P0-2 removes it.

Done when: both CI jobs pass with the new pins and the nine Dependabot PRs are closed.

### P0-2 Replace python-dateutil

`relativedelta` is used in `backend/app/dates.py` and `backend/app/export.py` to add or subtract whole months. Write `add_months(day: date, months: int) -> date` in `dates.py` that clamps to the last day of the target month, use it in both places, delete the dependency from `requirements.txt` and `pyproject.toml`. `tests/test_dates.py` must keep passing, and a new test must cover the month-end clamp (31 January minus one month is 31 December, 31 March minus one month is 28 or 29 February).

### P0-3 Python version matrix

In `ci.yml` turn the backend job into a matrix over `3.12`, `3.13`, `3.14`. Set `requires-python = ">=3.12"` and `target-version = "py312"` in `pyproject.toml`, run `ruff check --fix` for the `UP` rules that become available, and update CONTRIBUTING's "Python 3.12" line to name the matrix.

### P0-4 Pin Node

Set `node-version: "24"` in `ci.yml`, add `frontend/.nvmrc` containing `24`, and add `"engines": {"node": ">=24"}` to `package.json`. Update the Node line in CONTRIBUTING.

### P0-5 Frontend test runner

Add `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `jsdom` as devDependencies, a `vitest.config.ts`, and `"test": "vitest run"` in `package.json`. First tests:

- `lib/schemas.test.ts`: every message in `searchCriteriaSchema` and `messageDraftSchema` fires on the input that should trigger it.
- `lib/message.test.ts`: placeholder rendering and rejection of unknown placeholders.
- `lib/api.test.ts`: `errorMessage` handles a string `detail`, a list of `{msg}` objects, and a non-JSON body.

Add a `Test` step to the frontend CI job between lint and build.

### P0-6 Backend coverage

Add `pytest-cov` to the test section of `requirements.txt`, run pytest with `--cov=app --cov-report=term-missing --cov-report=xml` in CI, and upload `coverage.xml` as an artifact. Do not set a threshold in this item; record the starting number in the PR description so later items can be compared against it.

### P0-7 Structured logging

Add `backend/app/logging.py` that configures the root logger once from `main.py` with a JSON formatter (one line per record with `time`, `level`, `logger`, `message`, and any extra fields) and a `LOG_LEVEL` setting. Add log lines at: job status transitions in `runner.py`, rate-limit waits in `discovery.py` and `enrichment.py`, each enrichment miss, each campaign send and failure in `campaigns.py`, and startup in `main.py` with the database name and GitHub API base. Never log the installation token, the SMTP password, or recipient addresses at INFO.

### P1-1 Job counts full scan

`contact_counts` and `progress_counts` in `routes/jobs.py` run on every `GET /api/jobs/{id}`, and the job page polls every two seconds. Replace them with counters maintained on the job document: `counts.discovered`, `counts.enriched`, `counts.emails`, `counts.linkedin_without_email`, each updated with `$inc` where discovery inserts a candidate and where enrichment stores evidence. Add a one-time backfill (as a migration once P1-3 lands, or as an idempotent function in `ensure_indexes` until then) so existing jobs get correct numbers. Keep `test_discovery.py` and `test_enrichment.py` assertions on counts; add a test that a poll does not read `candidates` or `contact_evidence`.

### P1-2 Unbounded collection reads

The twelve `to_list(None)` sites, by file: `analysis.py` (3), `campaigns.py` (1), `contacts.py` (1), `enrichment.py` (2), `routes/contacts.py` (3), `routes/jobs.py` (2). For each one either iterate the cursor and accumulate, add a limit the caller already implies, or replace with an aggregation. `GET /api/contacts/logins` needs a product decision: cap at 5,000 and return `truncated: true`, and make `ContactsBrowser` show that cap when selecting all. The two sites in `routes/jobs.py` disappear with P1-1, so land P1-1 first or do them together.

### P1-3 Versioned migrations

Add `backend/app/migrations.py` with a `schema_version` collection holding one document `{_id: "fetchiq", version: int}`, a list of `(version, async callable)` steps, and `run_migrations(db)` called from the lifespan after `connect`. Move `_collapse_duplicate_logins` into migration 1 and `backfill_contact_filters` into migration 2. `ensure_indexes` then only creates indexes. Add `backend/scripts/migrate.py` for running migrations by hand. Tests: migrations run once, a second start is a no-op, a lower stored version runs only the missing steps.

### P1-4 Job lease

Add `worker_id` (a uuid generated at process start) and `heartbeat_at` to running `search_jobs` and `campaigns` documents. `kick` sets both; the run loop refreshes `heartbeat_at` at each checkpoint save; `resume_running` claims only documents whose heartbeat is older than `LEASE_SECONDS` (default 90) using `find_one_and_update` so two processes cannot both claim one. Lift the one-active-campaign rule in `queue_campaign` to one active campaign per worker. Tests: a fresh heartbeat is not claimed, a stale one is, two concurrent claims yield one winner.

### P1-5 Health and readiness

Keep `GET /api/health` constant for liveness. Add `GET /api/ready` that runs `db.command("ping")`, calls `GitHubAppAuth.token()`, and returns 503 with the failing component named when either fails. Log the failure with the logger from P0-7.

### P1-6 Docker and compose

`backend/Dockerfile` (python:3.12-slim, install requirements, run uvicorn without reload), `frontend/Dockerfile` (node:24-alpine, `next build`, standalone output), and `compose.yaml` with `mongo:8`, both services, and `.env` passed through. The GitHub App key is mounted, not copied. Add `output: "standalone"` to `next.config.ts`. Document the compose path in README under Local setup as an alternative to the venv instructions.

### P1-7 API token guard

Add `api_token: str = ""` to `Settings`. When it is non-empty, a FastAPI dependency on every router (and on the two CSV export routes, which the browser opens as plain links, so accept the token as a query parameter there too) requires `Authorization: Bearer <token>`. The frontend reads `NEXT_PUBLIC_API_TOKEN` and sends it from `lib/api.ts` and `exportHref`. When the setting is empty, behavior is unchanged so local development needs no token. Tests: missing, wrong and right token.

### P2-1a Database protocol

Add `backend/app/database.py` with a `Protocol` describing the subset of client, database, collection and cursor methods the app uses (`find`, `find_one`, `insert_one`, `update_one`, `update_many`, `delete_many`, `count_documents`, `distinct`, `aggregate`, `find_one_and_update`, `create_index`, `index_information`, `command`). Change every `AsyncIOMotorDatabase` import and type hint to the protocol. No runtime change; this item exists so P2-1b touches only `db.py` and `main.py`.

### P2-1b PyMongo async client

Replace `AsyncIOMotorClient` with `pymongo.AsyncMongoClient` in `db.py`, remove `motor` from both dependency files, and adjust the two API differences that matter here: `cursor.to_list()` takes `length=None` as a keyword in the new client, and the client must be closed with `await client.close()`. Tests still run on `mongomock-motor` in this item so the change can be reviewed in isolation. Run the full suite plus a manual job against a real MongoDB before pushing.

### P2-1c MongoDB service container in CI

Add a `services: mongo` block to the backend CI job, point tests at `mongodb://localhost:27017` through `MONGODB_URI`, and change the `db` fixture in `tests/conftest.py` to create and drop a uniquely named database per test. Remove `mongomock-motor`. The compose file from P1-6 gives the same setup locally. Expect a few tests to change where mongomock accepted queries a real server rejects.

### P2-2a Typed API responses

For each route in `routes/jobs.py`, `routes/contacts.py`, `routes/presets.py`, `routes/campaigns.py`, add a Pydantic model in a new `backend/app/schemas/` package and set `response_model`. The `public_job`, `present_campaign` and `_present` helpers return the model instead of a dict. Keep field names identical to the current JSON so the frontend keeps working unchanged. `GET /openapi.json` is then complete; add a test that it includes a schema for every route.

### P2-2b Generated frontend types

Add `openapi-typescript` as a devDependency and `"types": "openapi-typescript http://127.0.0.1:8000/openapi.json -o lib/api-types.ts"`. Replace the hand-written shapes in `lib/types.ts` with re-exports from the generated file, keeping `emptyCriteria` and `UNITED_STATES`. Add a CI step in the frontend job that starts the backend, regenerates, and fails on a diff. Requires P0-5 for the diff check to sit beside the test step.

### P2-3 TypeScript strict flags

Add `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes` to `tsconfig.json` and fix what `npm run typecheck` reports. This recovers some of the checking lost while `eslint.config.mjs` stubs out `typescript-eslint`. Open a separate `build/remove-eslint-shim` issue, labeled blocked, that references the upstream issue linked in the config file and is picked up when a TypeScript 7 compatible release exists.

### P2-4 TanStack Query

Add `@tanstack/react-query`, a `QueryClientProvider` in `app/layout.tsx` through a small client component, and hooks in `lib/queries.ts` wrapping the functions in `lib/api.ts`. Replace the `setInterval` loops in `JobPanel.tsx`, `MessageComposer.tsx` and `SearchStatusLink.tsx` with `refetchInterval` that stops on terminal statuses. The three components share one cache entry per job, so the header link and the panel stop making duplicate requests.

### P2-5 App route boundaries and server loads

Add `app/error.tsx`, `app/loading.tsx`, `app/not-found.tsx`, and `app/jobs/[id]/not-found.tsx`. Turn `app/page.tsx` and `app/contacts/page.tsx` into server components that fetch the geo catalog, presets, and outreach statuses from the backend URL in `API_PROXY_TARGET`, and pass them as `initialData` to the client components through the query hooks from P2-4.

## Execution order

Branches in the same row are independent and can be open at once. Each row starts after the previous row's PRs have merged to `dev`.

| Step | Branches |
| --- | --- |
| 1 | P0-1, P0-2, P0-4, P0-7 |
| 2 | P0-3, P0-5 |
| 3 | P0-6, P1-1, P1-2, P1-3, P1-7 |
| 4 | P1-4, P1-5, P1-6 |
| 5 | P2-1a, P2-2a, P2-3, P2-4 |
| 6 | P2-1b, P2-2b, P2-5 |
| 7 | P2-1c |

Two writers must never hold branches that touch the same file at once. The pairs to watch: P1-1 and P1-2 both edit `routes/jobs.py` (do P1-1 first or combine them); P1-3 and P1-4 both edit `db.py` and `runner.py`; P2-1a and P2-2a both touch every route file, so split them between two people only if P2-2a stays out of `db.py`.

## First session checklist

1. `git fetch origin` and confirm `origin/dev` still matches `origin/main` in content.
2. Create the four step-1 issues with `gh issue create`, note their numbers.
3. For each, create the branch from `origin/dev`, make the change, run both check suites, commit with the trailer block above and `Closes #<n>`, push.
4. Watch the four auto-opened PRs. Once merged, fetch and start step 2.
