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

FetchIQ authenticates to GitHub as one GitHub App installation. It signs a short-lived JWT with the app's private key, trades it for an installation token, caches that token, and renews it shortly before it expires. Rate limits are honored with `Retry-After` and exponential backoff. FetchIQ does not use a pool of tokens or export a list for resale.

## Stack

- FastAPI and Motor, database name `fetchiq`
- Next.js search UI and CSV download
- Collections: `search_presets`, `search_jobs`, `candidates`, `contact_evidence`, `job_events`, `campaigns`, `campaign_recipients`

`AIMS_local` and `AthensDB` are never opened.

## Local setup

```bash
cp .env.example .env
```

Set `MONGODB_URI` in `.env`, plus the three GitHub App values:

- `GITHUB_APP_ID`: the app's ID from its settings page.
- `GITHUB_APP_INSTALLATION_ID`: the installation of that app on the organization (the number at the end of `https://github.com/organizations/<org>/settings/installations/<id>`).
- `GITHUB_APP_PRIVATE_KEY_PATH`: path to the `.pem` file generated under the app's "Private keys". A relative path is resolved from the repository root.

The app needs no repository or organization permissions, since FetchIQ reads only public data. The API refuses to start, naming the setting, when a value is missing or the key file cannot be read. GitHub Enterprise Server works by pointing `GITHUB_API_BASE` at its API root, because the token exchange uses the same base URL. Do not commit `.env` or the key file.

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

`POST /api/jobs` stores criteria and leaves the job `queued`. `POST /api/jobs/{id}/start`, `/pause`, `/resume`, and `/cancel` control the run. A 429 or rate-limit 403 waits until the GitHub rate limit resets, then continues from the same checkpoint unless the user paused or cancelled. Resume continues a paused job from that checkpoint with the same installation.

Search criteria:

- `countries`: one or more country names. `United States` can include `us_states`.
- `account_age`: `min_years`, `min_months`, `max_years`, `max_months`, with min less than or equal to max. The window is UTC calendar arithmetic.
- `keywords`: optional.
- `candidate_limit`: stop after this many distinct GitHub user ids.

`GET /api/geo` lists countries and US states for the search form. `POST /api/presets` saves the same criteria for a later run.

When a slice reports more than 1,000 hits, the job splits the date window. A slice that cannot be split is marked incomplete.

`GET /api/jobs/{id}/export.csv` streams UTF-8 CSV. One row is emitted per GitHub user id. `include_no_contact=false` omits rows with no public contact. Cells that start with `=`, `+`, `-`, or `@` are prefixed so a spreadsheet does not treat them as formulas.

## Messages

The contacts page sends a plain-text message to the selected people who have a public email. That address is the recipient. The subject and body may contain `{{ first_name }}`, `{{ name }}`, `{{ login }}`, and `{{ email }}`. `first_name` is the first word of the display name, or the username when the name is missing. Any other placeholder is rejected.

`POST /api/campaigns` stores the campaign and returns. A background worker sends one message at a time, waits a random delay between the campaign's minimum and maximum, and waits when the hourly limit is reached. Pause and cancel use the same control as a search job. A contact is marked Message sent after the mail server accepts the message. A second campaign cannot start while one is queued, running, or paused.

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_SSL`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM`. The default host is `smtp.hostinger.com` on port 465. Leaving the username, password, or from address blank keeps sending disabled.

`POST /api/mail/check` logs into SMTP and IMAP, confirms a Sent folder, and does not send a message. The contacts page keeps Send disabled until both succeed. After the server accepts a message, that same message is filed in Sent. Messages already marked sent are filed once when the API starts, and are not sent again.

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
npm run build
```

Branching, commit messages, pull request checks, and CI are described in [CONTRIBUTING.md](CONTRIBUTING.md).
