# gh-contribution-analyzer, design specification

Date: 2026-08-05. Status: approved by the project owner, toolchain validated against
current online sources on the same date.

## 1. Overview

Self-hosted, dockerized analyzer of GitHub contribution activity across multiple
organizations, covering private and public repositories. It extracts full contribution
history, computes volume, churn and significance metrics with percentile rankings,
renders a server-side admin dashboard, generates PDF reports (periodic and on-demand),
emails them to configurable recipients, and optionally enriches views and reports with
LLM-generated insights. Single admin user, English only.

## 2. Architecture

Four services. In production (`compose.yml`) only Caddy is exposed:

| Service | Image | Role |
|---|---|---|
| caddy | `caddy:2.11` | Edge proxy, TLS, `basic_auth` with the root user. Only published ports (80/443). |
| app | `ghcr.io/erlete/gh-contribution-analyzer` | FastAPI web process (uvicorn). Serves dashboard and htmx partials. Never touches clones. |
| worker | same image, worker command | Sync engine, metrics ingestion, schedules, report generation, email dispatch. Owns the clone volume. |
| postgres | `postgres:18` | Source of truth. Named volume. |

`compose.yml` references the published image (no build context), so production deploys
need only `compose.yml`, `Caddyfile` and `.env`. `compose.dev.yml` (applied with
`docker compose -f compose.yml -f compose.dev.yml`) overrides with `build: .`, bind
mounts, published ports for every service, and adds Mailpit (`axllent/mailpit`).
Compose files use no `version:` key (obsolete), healthchecks with
`depends_on: condition: service_healthy`, named volumes, and `restart: unless-stopped`
in production. The clone volume mounts only into worker; PDFs live in a reports volume
mounted into worker (write) and app (read).

## 3. Validated toolchain (verified 2026-08-05)

Runtime: Python 3.14 (3.14.6), image `python:3.14-slim` (Debian trixie). Packages, all
current stable on PyPI: fastapi 0.141.1, uvicorn 0.52.1, sqlalchemy 2.0.51,
alembic 1.19.0, pydantic 2.13.4, pydantic-settings 2.14.2, jinja2 3.1.6,
httpx2 2.9.1 (maintained continuation of httpx by Pydantic Services; original httpx is
dormant at 0.28.1), weasyprint 69.0 (includes a security fix, pin >= 69),
matplotlib 3.11.1, apscheduler 3.11.3 (4.x is still alpha, 3.x is the production line),
psycopg 3.3.4 (one driver for sync Alembic and async app, safer with poolers than
asyncpg), cryptography 50.0.0. Tooling: uv 0.12.1 (packaging, lockfile), ruff 0.16.1
(linter and formatter, replaces black/isort/flake8), mypy 2.3.0 (CI type gate),
pytest 9.1.1. Frontend assets vendored, no npm: htmx 2.0.10 (v4 still beta),
Chart.js 4.5.1. Infra: postgres:18 (18.4), caddy:2.11 (2.11.4, directive renamed to
`basic_auth` since 2.8), axllent/mailpit v1.30, uv Docker binaries from
`ghcr.io/astral-sh/uv:0.12.1`.

GitHub Actions (current majors): actions/checkout v7, astral-sh/setup-uv v9,
docker/login-action v4, docker/setup-buildx-action v4, docker/metadata-action v6,
docker/build-push-action v7, googleapis/release-please-action v5. Third-party actions
are pinned by commit SHA with a version comment, per current GitHub hardening guidance.

## 4. Repository layout and conventions

```
src/gca/                  application package (gca = GitHub Contribution Analyzer)
  __init__.py             __version__, bumped by release-please
  main.py                 FastAPI app factory
  worker.py               worker entrypoint (APScheduler)
  cli.py                  admin CLI (org add --token-file, sync, report, ...)
  config.py               pydantic-settings, env only
  db/                     engine, session, alembic env
  models/                 SQLAlchemy models by domain
  sync/                   GraphQL client, clone manager, numstat ingest, rate budget
  metrics/                churn, significance, rollups, percentiles
  identity/               identities, persons, merge, suggestions
  reports/                PDF builders, period logic
  mail/                   backend protocol, graph backend, smtp backend
  ai/                     OpenAI-compatible client, insight service, cache
  scheduler/              jobs, job ledger
  web/                    routers, templates/, static/ (htmx, chart.js, css)
migrations/               alembic scripts
tests/                    unit/ and integration/, synthetic git fixtures
deploy/                   Caddyfile
.github/workflows/        ci.yml, release.yml
compose.yml  compose.dev.yml  Dockerfile  .dockerignore
pyproject.toml  uv.lock  release-please-config.json  .release-please-manifest.json
```

src layout, PEP 8 naming (short lowercase modules), pyproject.toml as the single config
surface: `[project.dependencies]` for runtime, PEP 735 `[dependency-groups]` dev group,
`[tool.ruff]` (line-length 88, target py314, rules E, W, F, I, B, C4, UP, SIM, N, S;
E501 ignored, the formatter owns line length), `[tool.mypy]`, `[tool.pytest.ini_options]`.
Dockerfile follows Astral's official multi-stage pattern: uv binaries copied from the
pinned distroless image, `uv sync --locked --no-install-project --no-dev` against
bind-mounted `pyproject.toml`/`uv.lock` with a cache mount, then copy source and
`uv sync --locked --no-editable`, final stage copies `/app/.venv` into `python:3.14-slim`
with WeasyPrint runtime deps (`libpango-1.0-0`, `libpangoft2-1.0-0`,
`libharfbuzz-subset0`, a fonts package) and git, runs as a non-root user,
`UV_COMPILE_BYTECODE=1`, `PYTHONUNBUFFERED=1`.

## 5. Configuration model

`.env` carries deployment internals only: Postgres credentials, `DATABASE_URL`,
`APP_SECRET_KEY` (Fernet key encrypting credentials at rest), `APP_BASE_URL`, `TZ`,
Caddy domain and basic auth hash, and optional seed values for mail (Graph or SMTP) and
AI. Seeds are imported into the settings table on first boot only; the in-app settings
screen is the authority afterwards. Everything operational lives in-app: org PATs,
repo and person filters, recipients, schedules, mail and AI configuration, sync tuning.

Setup gate: with zero validated orgs every route redirects to `/setup`, which accepts
an org login plus a fine-grained PAT, validates that the token can see that org and
lists repositories, and rejects user accounts (GraphQL `owner` must be an
Organization). Only after one org validates does the rest of the app route.

## 6. Data model (Postgres 18, Alembic migrations)

- `orgs`: login, display name, repo_filter_mode (all, whitelist, blacklist),
  person_filter_mode, sync settings. Deleting an org cascades to everything derived.
- `org_credentials`: encrypted PAT (Fernet), last validation, rate-limit snapshot.
- `repos`: org FK, GraphQL node id, name, default branch, private/archived/fork flags,
  clone state (status, last fetch, last ingested commit oid), inclusion flag derived
  from filters.
- `repo_filters`, `person_filters`: per-org rows naming repos or persons, with the
  org-level mode deciding whitelist or blacklist semantics. Default: no rows, all
  included.
- `persons`: display name, merged flag lineage. `identities`: immutable rows, kind
  git_author (name plus email) or github_login (login plus node id, avatar), person FK.
  Every unknown identity auto-creates a person; merges move identity FKs, so unmerge is
  always possible.
- `merge_suggestions`: person pair, score, reasons (jsonb), status
  (pending, accepted, dismissed).
- `commits`: repo FK, oid, authored/committed timestamps, author identity FK,
  additions, deletions, files_changed, is_merge, mechanical flag, significance score,
  churn_lines, self_churn_lines, cross_churn_lines. Merge commits carry zero stats.
- `commit_files`: commit FK, denormalized repo FK and authored_at, path, old_path,
  additions, deletions, file class.
- `pull_requests`: repo FK, number, node id, author identity FK, state, created/merged/
  closed timestamps, additions, deletions, changed_files. `reviews`: PR FK, reviewer
  identity FK, state, submitted_at.
- `person_repo_day_stats`: daily-grain rollup per person, repo and day (commit count,
  additions, deletions, churn splits, significance sum, PRs opened/merged, reviews).
  Maintained incrementally after each ingest; every dashboard aggregate and every
  period report reads from rollups, percentiles via `percent_rank()` at query time.
- `sync_runs`: per org/repo run log with status and stats. `job_ledger`: idempotency
  for scheduled jobs (job key plus period key unique).
- `report_schedules`: period kind (week, month, trimester, quarter, half_year, year),
  enabled flag, report kinds, org scope, recipients M2M. `reports`: kind (overview,
  person, repo), period bounds, org scope, subject, pdf path, status, emailed_at.
- `recipients`: email, active. `settings`: key/value jsonb. `insights`: cache keyed by
  view, org scope and period, storing markdown, model and timestamp.

Period definitions: ISO weeks; calendar months; trimesters are 4-month blocks (Jan-Apr,
May-Aug, Sep-Dec); quarters are calendar quarters; half-years Jan-Jun/Jul-Dec; years
calendar. Periodic generation happens only at period close (daily watcher, ledger
guarded), never retroactively.

## 7. Sync engine

Per org: discovery lists all repos via GraphQL (paginated), upserts, applies filters.
Per included repo, a pipeline with per-stage checkpoints, resumable after interruption,
bounded concurrency (default 4 repos in flight per org):

1. Clone or fetch: bare blobless mirror (`--filter=blob:none`) in the worker-only
   volume. Tokens injected per invocation via an ephemeral credential helper reading
   process env, never written to disk or remote URLs.
2. Commit ingest: new commits reachable from the default branch since the last
   ingested oid, `git log --numstat -M` batches; git lazily fetches only the blobs
   those diffs need. Each commit is deduplicated by oid, classified per file, scored,
   and rolled up.
3. Churn: for each new commit and file, churned lines = min(deletions, additions to
   the same path within the trailing 21-day window, configurable), attributed self or
   cross by comparing author persons. A documented file-level approximation, computed
   from stored numstat data, no extra git access.
4. PRs and reviews: GraphQL with stored cursors, incremental.
5. Maintenance (weekly): `git repack -a -d --filter=blob:none` returns clones to their
   slim steady state; clones are disposable cache, Postgres is the source of truth.

Rate budgeting: adaptive, driven by response rate-limit data, backing off on secondary
limits; each org PAT has its own budget. API usage is limited to discovery and PR and
review sync; commit data never touches the API.

## 8. Metrics

- Volume: commits, additions, deletions, files touched, PRs opened and merged, reviews
  given.
- Churn: as computed above, absolute and as a ratio of the author's added lines,
  self and cross split.
- Significance: per commit, `log1p(sum(class_weight * (additions + 0.5 * deletions)))`
  over non-mechanical files. Class weights: code 1.0, tests 0.7, config 0.5, docs 0.3,
  vendored or generated 0.05 (path and extension heuristics, e.g. lockfiles, minified
  assets, vendor directories). Commits with near-symmetric mass changes across many
  files (renames, reformat sweeps) are flagged mechanical and down-weighted. Merge
  commits score zero. PR and review activity is reported as its own dimension, not
  folded into significance.
- Percentiles: for any metric, period and org scope, a person's `percent_rank()` among
  persons with any activity in that period and scope.

## 9. Web UI

Server-rendered Jinja2 with htmx partial updates and Chart.js charts. A persistent
org multi-select in the header scopes every view (minimum one selectable; empty
selection means all orgs). Views: dashboard (KPIs, activity trends, top movers,
insight panel), repos list and repo detail, people list and person detail (both with
percentile context), people management (merge suggestions queue, manual merge and
unmerge, per-org person filters), orgs management (add org with PAT, token status,
repo filters, sync status and manual trigger, remove with cascade), reports (archive,
on-demand builder with full or custom range and repo and person selection, periodic
toggles), settings (mail, AI, recipients, general). Basic auth happens at Caddy; the
app trusts the network boundary.

## 10. Email

One `MailBackend` protocol, two implementations. Graph backend: client credentials
token flow against the Entra tenant, `POST /users/{sender}/sendMail`, attachments
over 3MB switch to an upload session automatically. SMTP backend: STARTTLS-capable,
used by Mailpit in dev. Backend auto-selects from configured settings (Graph wins when
Azure values are present), overridable in-app, test-send button on the settings
screen. Unconfigured or failing mail degrades to a dashboard banner plus reports
stored unsent; a later successful configuration can re-dispatch.

## 11. AI insights

OpenAI-compatible client (custom Qwen endpoint, base URL plus bearer key plus model
name) via httpx2, strict timeouts. The insight service assembles a compact statistical
context (aggregates and deltas for the current view, scope and period), requests a
short English narrative, and caches it in `insights`. Surfaces: dashboard panel, repo
and person detail panels, report narrative sections, per period kind. Unconfigured or
erroring AI hides the panels; reports render without narrative sections.

## 12. CI/CD, branching, releases

`ci.yml` on pull requests and rc pushes: ruff check, ruff format --check, mypy,
pytest (unit; integration against a service container Postgres), docker build check.
`release.yml` on push to main, single workflow so the GITHUB_TOKEN
no-downstream-trigger caveat never applies: job 1 runs release-please v5 (manifest
config, python strategy, extra-files generic updater keeps `uv.lock` and
`src/gca/__init__.py` in sync); when it reports `release_created`, job 2 builds and
pushes the image to GHCR (docker metadata-action semver tags plus latest, OCI labels
including `org.opencontainers.image.source`), and job 3 fast-forwards `stable` to the
release tag. Workflow permissions are least-privilege per job (`contents: write`,
`pull-requests: write` for release-please; `packages: write` for publish). Branch
flow: feature branches squash into `rc/x.y.z`, rc squashes into main with a
conventional-commit PR title, squash only, never merge commits.

## 13. Testing

- Unit: metrics (significance, mechanical detection, period math), identity
  suggestion scoring, path classification, mail backend selection, config seeding.
- Synthetic git fixtures: scripted repos with known histories drive clone manager and
  ingest tests end to end against `file://` remotes, asserting exact stats, churn and
  rollups.
- GraphQL client: recorded response fixtures, cursor and rate-budget behavior.
- Integration: full app against real Postgres (dockerized), Alembic migrations run,
  setup gate, org add, sync of a synthetic repo, report generation, Mailpit delivery.
- E2E: compose.dev stack, browser automation across setup, dashboard, people merge,
  report download; final validation syncs the two real orgs via their PATs.

## 14. Graceful degradation summary

| Capability | Unconfigured or failing | Behavior |
|---|---|---|
| Mail | no backend configured, auth failure, send failure | banner, reports stored unsent, re-dispatch on fix |
| AI | no endpoint, timeout, error | panels hidden, reports without narrative |
| GitHub token | invalid, expired, rate-exhausted | org marked degraded, sync paused with visible status, other orgs unaffected |
| Clone volume loss | volume deleted | full re-clone and re-ingest, idempotent |

## 15. Out of scope (initial release)

Multi-user auth and roles, GitHub App auth, issues and comments ingestion, i18n,
retroactive periodic reports, horizontal scaling beyond one worker.
