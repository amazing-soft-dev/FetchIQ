# FetchIQ

FetchIQ finds public GitHub profiles that match a location and account-age window, then records contact details those developers have already published on their profile or in a public README.

The search is not an exhaustive index of GitHub. User search returns at most 1,000 hits per query, so jobs split wide date windows and record when a slice stays incomplete.

## What FetchIQ stores

Only fields the developer published through the GitHub profile API or in public README content: display name, company, bio, blog, a public email when the API returns one, and social URLs written in those sources. Each stored contact keeps the source URL it came from.

When the profile API, the profile page, and every README published no email, FetchIQ makes one last attempt. It takes the three oldest public repositories the user owns (forks are skipped) and reads each one's first three commits, oldest first, through the commit's `.patch` page. Only the `From:` header of the patch is read, never the diff. The search stops at the first usable address, and the stored contact keeps the commit URL as its source.

These sources are never used to fill an email:

- `noreply` placeholders, including `<id>+<login>@users.noreply.github.com` commit authors
- addresses assembled from a name and a domain
- the body or diff of a commit

FetchIQ authenticates to GitHub as one GitHub App installation. It signs a short-lived JWT with the app's private key, trades it for an installation token, caches that token, and renews it shortly before it expires. Rate limits are honored with `Retry-After` and exponential backoff. FetchIQ does not use a pool of tokens or export a list for resale.

## Stack

- FastAPI and PyMongo's async client, database name `fetchiq`
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

Either run the three parts in containers, or install them on the machine.

**Containers.** With Docker installed, `compose.yaml` at the repository root starts MongoDB, the API on port 8000 and the UI on port 3000 from the same `.env`:

```bash
docker compose up --build
```

The key file named by `GITHUB_APP_PRIVATE_KEY_PATH` is mounted read-only into the API container and never copied into an image. `MONGODB_URI` is set to the `mongo` service inside the containers, and its data lives in the `mongo-data` volume. `API_TOKEN` from `.env` is baked into the UI build as `NEXT_PUBLIC_API_TOKEN`, so run the command again with `--build` after changing it. This is a development setup, not a deployment target.

**On the machine.** From the repository root, create a virtual environment and install the backend from `backend/requirements.txt`:

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

The API is open by default, which is fine while it listens on localhost only. Before pointing it at any other host, set `API_TOKEN` in `.env`: every request then needs `Authorization: Bearer <token>`, and the two CSV export links also accept `?token=<token>` because the browser opens them without headers. Put the same value in `frontend/.env.local` as `NEXT_PUBLIC_API_TOKEN` so the UI sends it; Next bakes that value into the client bundle at build time, so rebuild after changing it. `GET /api/health` answers a constant while the process is alive; `GET /api/ready` pings MongoDB and mints (or reuses) the GitHub App token, and answers 503 naming the failing component. Neither needs the API token, so probes carry no credentials.

Data migrations are versioned in `backend/app/migrations.py` and recorded in the `schema_version` collection. The API applies pending ones at startup, before it creates indexes. To apply them ahead of a deploy, or to see what is pending, run `python scripts/migrate.py` (or `--dry-run`) from `backend/`.

The API writes one JSON object per line to stderr for startup, job and campaign state changes, GitHub rate-limit waits, enrichment misses, and message sends. `LOG_LEVEL` (default `INFO`) sets the level. Tokens, passwords, and recipient addresses are never logged.

## Jobs

`POST /api/jobs` stores criteria and leaves the job `queued`. `POST /api/jobs/{id}/start`, `/pause`, `/resume`, and `/cancel` control the run. A 429 or rate-limit 403 waits until the GitHub rate limit resets, then continues from the same checkpoint unless the user paused or cancelled. Resume continues a paused job from that checkpoint with the same installation.

Search criteria:

- `countries`: one or more country names. `United States` can include `us_states`.
- `account_age`: `min_years`, `min_months`, `max_years`, `max_months`, with min less than or equal to max. The window is UTC calendar arithmetic.
- `keywords`: optional.
- `candidate_limit`: stop after this many distinct GitHub user ids.

`GET /api/geo` lists countries and US states for the search form. `POST /api/presets` saves the same criteria for a later run.

City and region names are shipped in `backend/app/data/places.json`. That file is built by `backend/scripts/build_places.py` from [GeoNames](https://www.geonames.org/) (CC BY 4.0) and [countriesnow.space](https://countriesnow.space/). The raw dumps are not in this repository. Choosing a country searches those cities, then the regions people write, then the country name. One location is finished before the next begins, and the list is saved on the job so a later edit to the file does not change a paused run. FetchIQ does not add its own search throttle or a pool of tokens.

When a slice reports more than 1,000 hits, the job splits the date window. A slice that cannot be split is marked incomplete.

`GET /api/jobs/{id}/export.csv` streams UTF-8 CSV. One row is emitted per GitHub user id. `include_no_contact=false` omits rows with no public contact. Cells that start with `=`, `+`, `-`, or `@` are prefixed so a spreadsheet does not treat them as formulas.

## Hacker News contacts

Developers also publish contact details in the monthly "Ask HN: Who wants to be hired?" threads posted by [whoishiring](https://news.ycombinator.com/submitted?id=whoishiring). `backend/scripts/scrape_hn.py` reads those threads into the same `candidates` and `contact_evidence` collections, so the contacts page, filters, CSV export and campaigns work on them unchanged.

```
python scripts/scrape_hn.py              # newest thread
python scripts/scrape_hn.py --threads 3  # newest three months
python scripts/scrape_hn.py --all        # every thread since 2011
python scripts/scrape_hn.py --dry-run    # parse and print, write nothing
```

The UI offers the same search. The header's Search menu opens `/search/github` (the GitHub form) or `/search/hacker-news`, and `/` redirects to the GitHub form. A Hacker News search is a job like any other (`POST /api/jobs/hackernews`, then the usual start, pause, resume and cancel routes, `kind: "hackernews"` on the job): it reads the newest `months_back` threads (1 to 24), keeps every post that has a contact unless the optional filters narrow it (locations, keywords matched against the Technologies line and the post text, remote only), and when `github_lookup` is on it reads the GitHub profile a post links, then its README, for contacts without a personal email. One Hacker News job runs at a time. The job page lists each thread read with its posts stored, merged into GitHub records, unchanged, filtered out or without a contact, and the GitHub lookup tally. The Contacts page filters by source and shows one badge per origin.

Threads are found through the official HN API and comments through the Algolia HN search API, with the item API as a fallback. Only top-level comments of a "Who wants to be hired?" thread are read; replies are discussion. "Who is hiring?" and freelancer threads are company posts and are skipped.

Each post becomes one candidate with `source: "hackernews"`. `login` is the HN username and `profile_url` the HN profile. `github_user_id` holds a negative number derived from the username, since HN has no numeric ids and the evidence index needs one that cannot collide with a GitHub id. `location_raw`, `country`, `utc_offset`, `public_email`, `linkedin_url` and the email flags are filled the same way the GitHub pipeline fills them. The HN-only details live beside them: `hn_thread_id`, `hn_comment_id`, `hn_comment_url`, `hn_posted_at`, `role`, `remote`, `willing_to_relocate`, `technologies`, `resume_url`, `github_login` and `github_url`. Emails written as `name [at] example [dot] com` are read; placeholders and noreply addresses are dropped as everywhere else. A post with no email, URL or GitHub link is not stored. Duplicates follow the GitHub rules: a post first looks for the same HN account, then the GitHub candidate whose profile it links, then any record that already holds one of its addresses. A match stored within the last 14 days is left alone, and so is one that already has an email, unless the post adds a personal address, which then becomes the primary email. Otherwise the record is updated from the post; a GitHub record keeps its login and job and gains `hackernews` in its `sources`. The outreach status is cleared only when the primary email changes. An HN username already taken by an unrelated GitHub record is stored as `hn:<username>`.

## Messages

The contacts page sends a plain-text message to the selected people who have a public email. That address is the recipient. The subject and body may contain `{{ first_name }}`, `{{ name }}`, `{{ login }}`, and `{{ email }}`. `first_name` is the first word of the display name, or the username when the name is missing. Any other placeholder is rejected.

`POST /api/campaigns` stores the campaign and returns. A background worker sends one message at a time, waits a random delay between the campaign's minimum and maximum, and waits when the hourly limit is reached. Pause and cancel use the same control as a search job. A contact is marked Message sent after the mail server accepts the message. A second campaign cannot start on the same API process while one is queued, running, or paused.

A running job or campaign is leased to the process that runs it, which refreshes a heartbeat at every checkpoint and during waits. A restarted process, or a second one, takes a running document over only once that heartbeat is 90 seconds old, and checks for lapsed leases every 30 seconds, so one crash costs at most about two minutes and no job runs twice.

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_SSL`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM`. The default host is `smtp.hostinger.com` on port 465. Leaving the username, password, or from address blank keeps sending disabled.

`POST /api/mail/check` logs into SMTP and IMAP, confirms a Sent folder, and does not send a message. The contacts page keeps Send disabled until both succeed. After the server accepts a message, that same message is filed in Sent. Messages already marked sent are filed once when the API starts, and are not sent again.

## Tests

Backend tests run against a real MongoDB at `MONGODB_URI` (default `mongodb://localhost:27017`; `docker compose up mongo` provides one). Each test creates its own `fetchiq_test_<id>` database and drops it when it ends, so a shared server is never written to outside those names.

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
