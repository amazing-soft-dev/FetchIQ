# FetchIQ upgrade roadmap

Written 2026-11-05 against branch `feat/search-result-analysis` (commit 3505120).

## Where the project stands

| Area | State today |
| --- | --- |
| Backend | FastAPI 0.162, Starlette 1.9, Pydantic 2.14, Motor 3.7.1 on PyMongo 4.19, httpx 0.28, PyJWT. About 7,000 lines in `backend/app`. |
| Runtime | Python 3.14 in the local venv, CI runs 3.12, `pyproject.toml` allows 3.11, Ruff targets `py311`. |
| Tests | 197 backend tests pass in about 30 s on `mongomock-motor`. Ruff check and format are clean. No frontend tests. No coverage report. |
| Frontend | Next 16.4, React 19.3, TypeScript 7.0, ESLint 10, Tailwind 4, Radix primitives, Zod 4, `motion`. About 3,200 lines, 14 client components, no server components beyond the three page shells. |
| Jobs | One `asyncio.Task` per job inside the API process (`runner.py`, `campaigns.py`). Checkpoints live in MongoDB. Nothing stops two API processes from running the same job. |
| Observability | No logging calls anywhere in `backend/app`. Health check returns a constant. |
| Deployment | None. No Dockerfile, no compose file. CONTRIBUTING says this is deliberate until a target exists. |
| Dependencies | Dependabot is on. Open bumps: Next 16.4.5, ESLint 10.14, lucide-react 1.69, motion 14.9, Ruff 0.17.4, Pydantic 2.14.3, pytest 9.2, pytest-asyncio 1.4.1, pydantic-settings 2.16.2, python-dateutil 4.0. |

The codebase is in good shape for its size: small modules, explicit checkpoints, a thorough backend test suite, pinned CI actions. The items below are ordered by how much risk they remove per day of work, not by how interesting they are.

## Phase 0: hygiene (about one week)

Low-risk changes that make every later phase cheaper.

1. **Land the open Dependabot PRs.** All are patch or minor except `python-dateutil` 4.0. Only two call sites use it, both for `relativedelta` on calendar months in `dates.py` and `export.py`. Replace them with a small month-arithmetic helper and drop the dependency instead of taking the major bump.
2. **Align Python versions.** Local development runs 3.14 but CI tests only 3.12. Add a CI matrix for 3.12, 3.13 and 3.14. Raise `requires-python` and the Ruff target to 3.12, which is the oldest version anyone actually runs, and let Ruff's `UP` rules modernize syntax.
3. **Align Node versions.** Local Node is 26, CI uses 22. Pick one LTS (24 is current LTS) and pin it in CI and in a `.nvmrc` or `engines` field.
4. **Add a frontend test runner.** Vitest plus Testing Library, run in CI. Start with the pure modules: `lib/schemas.ts`, `lib/message.ts`, the error-message parser in `lib/api.ts`, and `readQuery` in `ContactsBrowser.tsx`. These have no DOM dependencies and encode validation rules that must match the backend.
5. **Add coverage reporting** to the backend pytest run (`pytest-cov`) so later refactors in Phases 2 and 3 can be checked for regressions in tested surface.
6. **Add logging.** A `logging` setup in `main.py` with a structured formatter, plus log lines at job state changes, rate-limit waits, enrichment misses, and campaign sends. Today the only record of a problem is the `job_events` collection.

## Phase 1: operability (two to three weeks)

Changes needed before FetchIQ runs anywhere other than one developer's laptop.

1. **Fix the per-poll full scan.** The job page polls `GET /api/jobs/{id}` every two seconds. Each call runs `contact_counts`, which loads every candidate and every evidence row for the job into memory to count emails. Keep the counts on the job document (incremented as enrichment stores evidence) or replace the scan with one aggregation pipeline. The same applies to `progress_counts`.
2. **Bound the unbounded queries.** Twelve `to_list(None)` calls read whole collections. The worst are `GET /api/contacts/logins` (returns every matching login so the UI can "select all") and the campaign queue in `campaigns.py`. Page or stream these, or cap them and tell the UI.
3. **Replace the boot-time migration.** `ensure_indexes` deletes duplicate candidate rows on every start so the unique `login` index can build. That is a one-time migration running as a side effect of index creation, and it is destructive. Move it to a versioned migration script with a `schema_version` document, run once, and leave `ensure_indexes` idempotent and read-only on data.
4. **Add a job lease.** Store `worker_id` and `heartbeat_at` on running jobs and campaigns. `resume_running` on startup should claim only jobs whose heartbeat is stale. Without this, two API processes (or a crashed-and-restarted one racing a still-alive one) both run the same job and double-count slots.
5. **Make the health check honest.** `/api/health` should report MongoDB reachability and whether the GitHub installation token can be minted, with a separate liveness path that stays constant.
6. **Containerize local development.** A `Dockerfile` for each service and a compose file with MongoDB. This does not add a deploy target. It gives new contributors and CI a reproducible environment and removes the `.env` path-resolution logic in `config.py` from the critical path.
7. **Add an API guard before any shared deployment.** The API has no authentication and CORS is pinned to localhost. The minimum is a single shared bearer token read from settings and checked by a dependency on every router. Add this before the compose setup is ever pointed at a public host.

## Phase 2: platform upgrades (three to four weeks, can overlap Phase 1)

Upgrades driven by upstream deprecations and by drift between the two halves of the codebase.

1. **Motor to PyMongo's native async API.** Motor is deprecated upstream in favor of `pymongo.asynchronous`, which is already installed (PyMongo 4.19). The code uses `AsyncIOMotorClient` and `AsyncIOMotorDatabase` by name in `db.py` and type hints, and `mongomock-motor` in tests. Plan the swap in three steps: introduce a thin `Database` protocol so modules stop importing Motor types, switch the production client, then replace `mongomock-motor` in tests. For the last step, run a real MongoDB as a service container in CI and in the compose file, since `mongomock` does not track the async PyMongo API. This is the largest single change on the roadmap and should finish well before Motor's end of life.
2. **Typed API responses.** Routes return bare dicts, so the OpenAPI schema is empty and `frontend/lib/types.ts` is a hand-maintained copy of the backend shapes. Give each route a Pydantic response model, then generate the TypeScript types from the OpenAPI document with `openapi-typescript` in a `npm run types` step. Drift between the two halves becomes a CI failure instead of a runtime surprise.
3. **Remove the ESLint shim.** `eslint.config.mjs` stubs out `typescript-eslint` because it does not load under TypeScript 7, which means no TypeScript-aware lint rules run today. Track the upstream issue linked in that file and delete the shim as soon as a compatible release ships. Until then, consider adding `tsc --noEmit` strictness flags (`noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`) to recover some of the lost checking.
4. **Frontend data layer.** Three components each run their own `setInterval` poll against the same hand-written `fetch` wrapper. Introduce TanStack Query (or SWR) so polling, caching, dedupe and error state live in one place. This also prepares for item 2 of Phase 3.
5. **Next.js route conventions.** The `app/` tree has no `error.tsx`, `loading.tsx` or `not-found.tsx`. Add them. Move initial data loads (geo catalog, presets, outreach statuses) into server components and pass them as props, so the first paint does not wait on three client round-trips.

## Phase 3: product and GitHub efficiency (four to six weeks)

Changes that make a search cheaper in GitHub requests or make outreach safer.

1. **Use GraphQL for enrichment.** Enrichment today makes a REST `users/{login}` call plus an HTML fetch of the public profile page per candidate, then parses `itemprop="email"`, `mailto:` and a `data-hours-ahead-of-utc` attribute with regular expressions. GraphQL returns name, bio, company, email, location, `socialAccounts` and `websiteUrl` for up to 100 logins in one request, which cuts request volume by one to two orders of magnitude and removes the scrape. Keep the HTML path only for the UTC offset if GraphQL does not expose it, and keep the REST `social_accounts` endpoint as a fallback.
2. **Live job updates instead of polling.** Once counts are cheap (Phase 1, item 1), expose `GET /api/jobs/{id}/events` as Server-Sent Events fed from `job_events`. The job page, the status link in the header, and the campaign composer all stop polling.
3. **Conditional requests.** Store the `ETag` from profile and README responses on the candidate and send `If-None-Match` on refresh. A 304 does not count against the primary rate limit, which matters for the 14-day re-check logic in discovery.
4. **Bounded concurrency in enrichment.** `enrich_pending` walks candidates one at a time. A semaphore of three to five concurrent profiles, with the existing rate-limit handling, would shorten enrichment several times over without touching the checkpoint logic.
5. **Outreach safety.** Before campaigns run against real recipients at volume: a suppression list honored by `queue_campaign`, a one-click unsubscribe link and `List-Unsubscribe` header, and bounce processing through the IMAP connection that already exists for Sent copies. Also lift the one-campaign-at-a-time rule once the lease from Phase 1 exists, since the limit is there to protect a single in-process worker.
6. **Mail provider abstraction.** `mail.py` hard-codes Hostinger defaults and speaks raw SMTP and IMAP. The `Mailer` protocol already exists. Add a second implementation behind it (any transactional provider with a REST API) so sending does not depend on a mailbox login.

## Phase 4: scale (only when needed)

1. **Separate worker process.** Move `JobRunner` and `CampaignRunner` out of the API process into a worker that claims leased jobs. The API then scales independently and a long enrichment no longer shares an event loop with request handling. The lease from Phase 1 is the prerequisite.
2. **Multiple installations.** The README is explicit that FetchIQ does not pool tokens. If more throughput is ever needed, the honest option is one deployment per GitHub App installation, with search criteria partitioned by country, not a token pool in one process.

## What not to change

- The MongoDB document model and checkpoint design. They are the reason pause, resume and crash recovery work, and every phase above builds on them.
- The slot-allocation math in `slots.py` and the date-window splitting in `dates.py`. Both are well tested and independent of everything else.
- The branch and PR automation in `.github/workflows`. It works, actions are SHA-pinned, and the token is read-only.

## Suggested order of work

| Sprint | Items |
| --- | --- |
| 1 | Phase 0 in full. |
| 2 | Phase 1 items 1, 2, 3 (counts, bounded queries, migration). |
| 3 | Phase 1 items 4 to 7 (lease, health, containers, API guard). |
| 4 to 5 | Phase 2 item 1 (Motor replacement) alongside item 2 (typed responses). |
| 6 | Phase 2 items 3 to 5. |
| 7 to 9 | Phase 3 items 1 to 4 (GraphQL, SSE, ETags, concurrency). |
| 10 | Phase 3 items 5 and 6 (outreach safety, mail abstraction). |
| Later | Phase 4 when a second operator or a second machine appears. |
