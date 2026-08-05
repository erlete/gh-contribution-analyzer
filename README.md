# gh-contribution-analyzer

Self-hosted, dockerized analyzer of GitHub contribution activity across multiple
organizations, covering private and public repositories. It extracts full contribution
history, computes volume, churn and significance metrics with percentile rankings,
renders a server-side admin dashboard, generates PDF reports on a schedule and on
demand, emails them to configurable recipients, and can optionally enrich views and
reports with AI-generated insights. Single admin user, English only.

## Features

- Multiple GitHub organizations, each with its own fine-grained personal access token
- Full-history extraction through bare blobless mirror clones, so commit data never
  consumes API rate limits
- Volume, churn and significance metrics with percentile rankings per person, period
  and scope
- Server-rendered admin dashboard with activity trends, repo and person detail views
- PDF reports, both periodic (week, month, trimester, quarter, half year, year) and
  on demand with custom ranges; individual reports are single documents with an
  introduction, a table of contents and one analyzed section per person or repo
- Scheduled email delivery via Microsoft Graph or SMTP, with graceful degradation
  when mail is unconfigured or failing (reports are stored unsent and can be
  re-dispatched later)
- AI insights through any OpenAI-compatible endpoint, with per-area operator
  instructions; when AI is off or failing, every insight area falls back to plain
  data statements, and AI-generated text is marked with an AI chip
- Org, repository and person filters (whitelist or blacklist per org)
- Person identity merge and unmerge with automatic merge suggestions

## Architecture

Four services defined in `compose.yml`. In production only Caddy is exposed.

| Service | Image | Role |
|---|---|---|
| caddy | `caddy:2.11` | Edge proxy, TLS, basic auth. The only published ports (80/443). |
| app | `ghcr.io/erlete/gh-contribution-analyzer` | FastAPI web process (uvicorn). Serves the dashboard. Never touches clones. |
| worker | same image, `gca-worker` command | Sync engine, metrics ingestion, schedules, report generation, email dispatch. Owns the clone volume. |
| postgres | `postgres:18` | Source of truth. Named volume. |

## Quickstart (development)

```sh
docker compose -f compose.yml -f compose.dev.yml up --build
```

- Dashboard (app, direct): http://localhost:8000
- Caddy (production-like entry, basic auth): http://localhost:8080
- Mailpit (captures all outgoing mail): http://localhost:8025
- Default dev basic auth credentials: `root` / `admin`

The dev overlay builds the image locally, publishes every service port, enables live
reload and points the SMTP mail seed at Mailpit.

## Production deployment (no clone needed)

`compose.yml` references the published image, so you only need three files:

1. Download `compose.yml`, `deploy/Caddyfile` and `.env.example` from the repository,
   keeping `Caddyfile` at `deploy/Caddyfile` next to `compose.yml`.
2. Copy `.env.example` to `.env` and fill it in (see `docs/deployment.md`).
3. Start the stack:

```sh
docker compose up -d
```

The image is pulled from `ghcr.io/erlete/gh-contribution-analyzer` (tag selectable
via `GCA_TAG`, default `latest`). See `docs/deployment.md` for the full guide.

## First-run setup gate

Until at least one organization validates, every route redirects to `/setup`. There
you provide an organization login plus a fine-grained personal access token. The app
verifies that the token can see that organization and list its repositories, and it
rejects user accounts: the token's resource owner must be the organization itself,
and only organizations can be added. Once one org validates, the rest of the app
routes normally.

## CLI

The image ships an admin CLI for headless operation. Tokens are read from a file or
stdin, never from argv:

```sh
gca org add my-org --token-file /path/to/token
gca sync run
gca report generate --kind overview
```

Run these inside the app or worker container, for example
`docker compose exec worker gca sync run`.

## Release flow

- Conventional commit messages; PRs are squash-merged only, never merge commits
- Feature branches squash into `rc/x.y.z` branches; an rc branch squashes into
  `main` with a conventional-commit PR title
- release-please manages versioning and release PRs from `main`
- When a release is created, the container image is built and pushed to GHCR with
  semver tags plus `latest`
- The `stable` branch is fast-forwarded to the latest release tag

## Documentation

- [Deployment](docs/deployment.md): production deploy, environment variables,
  volumes, backups, upgrades
- [Configuration](docs/configuration.md): orgs, tokens, filters, identity merging,
  mail, AI, schedules
- [Metrics](docs/metrics.md): how volume, churn, significance and percentiles are
  computed, including limitations
