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

```bash
cd backend
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload

cd ../frontend
npm install
npm run dev
```

The frontend proxies `/api` to `http://127.0.0.1:8000`.

## Tests

Backend tests use an in-memory MongoDB (`fetchiq_test`), not a shared cluster.

```bash
cd backend
ruff check .
ruff format --check .
pytest
```

```bash
cd frontend
npm run typecheck
npm run lint
```
