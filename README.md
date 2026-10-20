# FetchIQ

FetchIQ finds public GitHub profiles that match a location and account-age window, then records contact details those developers have already published on their profile or in a public README.

The search is not an exhaustive index of GitHub. User search returns at most 1,000 hits per query, so jobs split wide date windows and record when a slice stays incomplete.

## What FetchIQ stores

Only fields the developer published through the GitHub profile API or in public README content: display name, company, bio, blog, a public email when the API returns one, and social URLs written in those sources. Each stored contact keeps the source URL it came from.

These sources are never used to fill an email:

- commit patch files
- commit headers
- `noreply` placeholders
- addresses assembled from a name and a domain

One `GITHUB_TOKEN` is read from the environment. Rate limits are honored with `Retry-After` and exponential backoff. FetchIQ does not rotate tokens, send email, or export a list for resale.

## Stack

- FastAPI and Motor, database name `fetchiq`
- Next.js search UI and CSV download
- Collections: `search_presets`, `search_jobs`, `candidates`, `contact_evidence`, `job_events`

`AIMS_local` and `AthensDB` are never opened.

## Local setup

```bash
cp .env.example .env
```

Set `MONGODB_URI` and `GITHUB_TOKEN` in `.env`. Do not commit that file.

From the repository root, create a virtual environment and install the backend from `backend/requirements.txt`:

```bash
python -m venv .venv
```

Activate it. On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

On macOS or Linux:

```bash
source .venv/bin/activate
```

```bash
python -m pip install -r backend/requirements.txt
python -m uvicorn app.main:app --reload --app-dir backend

cd frontend
npm install
npm run dev
```

The frontend proxies `/api` to `http://127.0.0.1:8000`.

## Jobs

`POST /api/jobs` stores criteria and leaves the job `queued`. `POST /api/jobs/{id}/start`, `/pause`, `/resume`, and `/cancel` control the run. A 429 or rate-limit 403 pauses the job and keeps the date-window checkpoint. Resume continues from that checkpoint with the same token.

Search criteria:

- `countries`: one or more country names. `United States` can include `us_states`.
- `account_age`: `min_years`, `min_months`, `max_years`, `max_months`, with min less than or equal to max. The window is UTC calendar arithmetic.
- `keywords`: optional.
- `candidate_limit`: stop after this many distinct GitHub user ids.

`GET /api/geo` lists countries and US states for the search form. `POST /api/presets` saves the same criteria for a later run.

When a slice reports more than 1,000 hits, the job splits the date window. A slice that cannot be split is marked incomplete.

`GET /api/jobs/{id}/export.csv` streams UTF-8 CSV. One row is emitted per GitHub user id. `include_no_contact=false` omits rows with no public contact. Cells that start with `=`, `+`, `-`, or `@` are prefixed so a spreadsheet does not treat them as formulas.

## Branches

`dev` is the long-lived integration branch. Open `feat/*`, `fix/*`, and `ci/*` pull requests against `dev`, and merge them with a merge commit. Promote `dev` to `main` with a second pull request. Do not delete `dev` after that promotion. CI runs on pull requests to `dev` and `main`, and on pushes to those branches.

## Tests

Backend tests use an in-memory MongoDB (`fetchiq_test`), not a shared cluster.

With `.venv` activated:

```bash
cd backend
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

```bash
cd frontend
npm run typecheck
npm run lint
```
