# gh-contribution-analyzer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the full multi-org GitHub contribution analyzer described in
`docs/superpowers/specs/2026-08-05-gh-contribution-analyzer-design.md`, from scaffold to
validated end-to-end deployment.

**Architecture:** FastAPI web app plus APScheduler worker sharing one image, Postgres 18
source of truth, blobless bare mirror clones as disposable cache, Caddy edge with basic
auth, GHCR-published image via release-please. Server-rendered Jinja2 with htmx.

**Tech Stack:** Python 3.14, uv, FastAPI 0.141, SQLAlchemy 2.0.51 (async, psycopg 3),
Alembic, httpx2, WeasyPrint 69, matplotlib, APScheduler 3.11, htmx 2.0.10,
Chart.js 4.5.1, ruff, mypy, pytest.

Execution notes for all tasks: run commands from the repo root; commit with
conventional-commit messages; `LEFTHOOK=0` on commits (hook manager binary absent on
this machine); never mention any AI tool anywhere; no em-dashes in any text.

---

## Phase 0: Scaffold

### Task 0.1: Python project skeleton
**Files:** Create `pyproject.toml`, `src/gca/__init__.py` (`__version__ = "0.1.0"`),
`src/gca/py.typed`, `tests/__init__.py`, `.python-version` (3.14).
- [ ] `pyproject.toml`: project `gca`, version 0.1.0, requires-python `>=3.14`,
  dependencies: fastapi, uvicorn[standard], jinja2, python-multipart, sqlalchemy,
  psycopg[binary], alembic, pydantic, pydantic-settings, httpx2, weasyprint>=69,
  matplotlib, apscheduler<4, cryptography, itsdangerous. Dev group: pytest,
  pytest-asyncio, ruff, mypy, types tooling. `[tool.ruff]` line-length 88, target
  py314, lint select E W F I B C4 UP SIM N S, ignore E501 S101(tests) S603 S607 (git
  subprocess by design). `[tool.mypy]` strict-ish (disallow_untyped_defs, warn unused
  ignores), plugins pydantic. `[tool.pytest.ini_options]` asyncio_mode auto, markers
  unit/integration. `[build-system]` hatchling; `[tool.uv]` default-groups dev.
- [ ] `uv lock && uv sync`, verify `uv run python -c "import gca"`.
- [ ] Commit `chore: scaffold python project with uv, ruff, mypy, pytest`.

### Task 0.2: Config and app factory
**Files:** Create `src/gca/config.py`, `src/gca/main.py`, `tests/unit/test_config.py`.
- [ ] `Settings(BaseSettings)` env fields: `database_url`, `app_secret_key`,
  `app_base_url` (default `http://localhost`), `tz` (default UTC), seed fields
  `mail_azure_client_id/secret/tenant_id`, `mail_sender_address`, `smtp_host/port/
  username/password/starttls/sender_address`, `ai_service_url/key/model`;
  `model_config = SettingsConfigDict(env_file=".env", extra="ignore")`;
  `get_settings()` cached.
- [ ] `create_app()` returns FastAPI with `/healthz` returning `{"status": "ok",
  "version": __version__}`. Test with httpx2 ASGITransport.
- [ ] Commit `feat: add settings and fastapi app factory with healthcheck`.

### Task 0.3: Container and compose stack
**Files:** Create `Dockerfile`, `.dockerignore`, `compose.yml`, `compose.dev.yml`,
`deploy/Caddyfile`, `docker/entrypoint.sh`, `docker/gca-askpass`.
- [ ] Dockerfile per Astral multistage guidance (see spec section 4): builder
  `python:3.14-slim` + `COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /uvx /bin/`,
  cache-mounted `uv sync --locked --no-install-project --no-dev`, copy src, final sync
  `--no-editable --no-dev`; runtime stage installs `git libpango-1.0-0
  libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core`, copies `.venv`, non-root
  user `gca` uid 999, installs `docker/gca-askpass` (prints `$GCA_GIT_TOKEN`) to
  `/usr/local/bin/`, entrypoint runs `alembic upgrade head` (web only) then exec CMD;
  `LABEL org.opencontainers.image.source=https://github.com/erlete/gh-contribution-analyzer`.
- [ ] `compose.yml`: services caddy (ports 80/443, `deploy/Caddyfile` mounted, caddy
  data volume, `restart: unless-stopped`), app (`ghcr.io/erlete/gh-contribution-analyzer:latest`,
  env_file .env, depends_on postgres healthy, reports volume read), worker (same
  image, command `gca-worker`, clone volume + reports volume), postgres:18 (healthcheck
  `pg_isready`, data volume). No published ports except caddy.
- [ ] `compose.dev.yml`: app/worker `build: .`, `image:` override to local tag, all
  service ports published (app 8000, postgres 5432, mailpit 8025/1025, caddy 8080),
  mailpit service, SMTP seed env pointing at mailpit, bind mount src for reload,
  uvicorn `--reload`.
- [ ] Caddyfile: `{$CADDY_DOMAIN}` site, `basic_auth` with `{$BASIC_AUTH_USER}`
  `{$BASIC_AUTH_HASH}`, `reverse_proxy app:8000`.
- [ ] Commit `feat: add dockerfile, compose stacks and caddy edge`.

### Task 0.4: CI and release automation
**Files:** Create `.github/workflows/ci.yml`, `.github/workflows/release.yml`,
`release-please-config.json`, `.release-please-manifest.json`.
- [ ] Resolve current commit SHAs for third-party actions via `gh api` and pin
  `owner/action@SHA # vX` (checkout v7, setup-uv v9, docker login v4, buildx v4,
  metadata v6, build-push v7, release-please v5).
- [ ] ci.yml: on pull_request + push to `rc/**`; concurrency cancel; jobs: lint
  (ruff check, ruff format --check), typecheck (mypy src), test (postgres:18 service
  container, `uv run pytest`), build (docker build, no push).
- [ ] release.yml: on push to main; job release (release-please v5, manifest);
  job publish `if release_created`: checkout, buildx, login ghcr with GITHUB_TOKEN,
  metadata semver tags + latest, build-push linux/amd64; job promote `if
  release_created`: fast-forward `stable` to the tag using
  `git push origin +refs/tags/${tag}:refs/heads/stable` with contents write.
- [ ] release-please-config.json: release-type python, package `.`,
  extra-files generic updaters for `uv.lock` (toml jsonpath package gca version) and
  none else (`src/gca/__init__.py` handled natively). Manifest `{".": "0.1.0"}`
  after first release; start manifest at `{}` bootstrap-sha root commit.
- [ ] Commit `ci: add lint, test, build and release automation`.

## Phase 1: Database layer

### Task 1.1: Engine, base, crypto
**Files:** Create `src/gca/db/__init__.py`, `src/gca/db/engine.py` (async engine via
`postgresql+psycopg`, `async_sessionmaker`, `get_session` FastAPI dependency),
`src/gca/db/base.py` (`class Base(DeclarativeBase)` with naming convention),
`src/gca/crypto.py` (`encrypt_str/decrypt_str` Fernet using `APP_SECRET_KEY`),
`tests/unit/test_crypto.py` (roundtrip + wrong-key failure).
- [ ] Commit `feat: add async database engine and credential encryption`.

### Task 1.2: Models
**Files:** Create `src/gca/models/{__init__,org,repo,person,activity,rollup,ops}.py`
exactly as laid out in spec section 6. Key signatures used by later phases:
`Org(id, login, display_name, repo_filter_mode: FilterMode, person_filter_mode,
sync_enabled, created_at)`, `OrgCredential(org_id unique, token_encrypted,
validated_at, rate_snapshot: JSONB)`, `Repo(id, org_id, node_id, name,
default_branch, is_private, is_archived, is_fork, included, clone_status: CloneStatus,
last_fetched_at, last_ingested_oid, pr_cursor)`, `Person(id, display_name,
created_at)`, `Identity(id, person_id, kind: IdentityKind, name, email, login,
node_id, avatar_url; unique (kind, lower(email), lower(name)) partial / (kind,
node_id))`, `Commit(id, repo_id, oid unique-per-repo, authored_at, committed_at,
author_identity_id, additions, deletions, files_changed, is_merge, is_mechanical,
significance: float, churn_lines, self_churn_lines, cross_churn_lines,
message_subject)`, `CommitFile(id, commit_id, repo_id, authored_at, path, old_path,
additions, deletions, file_class: FileClass)`, `PullRequest`, `Review`,
`PersonRepoDayStats(person_id, repo_id, org_id, day, commits, additions, deletions,
churn, self_churn, cross_churn, significance, prs_opened, prs_merged, reviews;
PK person/repo/day)`, `SyncRun`, `JobLedger(job_key, period_key unique together)`,
`ReportSchedule`, `Report`, `Recipient`, `schedule_recipients`, `Setting(key PK,
value JSONB)`, `Insight(cache_key unique, content, model, created_at)`,
`RepoFilter(org_id, repo_name)`, `PersonFilter(org_id, person_id)`,
`MergeSuggestion(person_a_id, person_b_id, score, reasons JSONB, status)`.
- [ ] Alembic init (`migrations/`, async template adapted to sync psycopg for
  migrations), autogenerate initial revision, apply against dockerized scratch
  postgres, `alembic upgrade head` + `downgrade base` both clean.
- [ ] Commit `feat: add data model and initial migration`.

### Task 1.3: Settings service and seeding
**Files:** Create `src/gca/services/settings.py`, `tests/unit/test_settings_seed.py`.
- [ ] `SettingsStore` over `Setting` rows: typed getters (`mail_config() -> MailConfig
  | None`, `ai_config() -> AIConfig | None`, `general()`), `seed_from_env(settings)`
  writes only missing keys, encrypting secrets; `mail.provider` auto: graph if azure
  seed present else smtp if host present else none. Unit-test seeding idempotency with
  sqlite-backed session (aiosqlite) or fakes.
- [ ] Commit `feat: add settings store with env seeding`.

## Phase 2: Identity

### Task 2.1: Identity resolution and merge
**Files:** Create `src/gca/identity/{__init__,resolver,merge,suggest}.py`,
`tests/unit/test_identity_suggest.py`, `tests/integration/test_identity_merge.py`.
- [ ] `resolver.get_or_create_identity(session, kind, *, name, email, login, node_id)
  -> Identity` (normalizes, auto-creates Person named from display name or login).
- [ ] `merge.merge_persons(session, target_id, source_id)` moves identities and
  rewrites rollups person_id; `merge.unmerge_identity(session, identity_id)` splits an
  identity back out to a fresh person and recomputes affected rollups.
- [ ] `suggest.score_pair(a: PersonView, b: PersonView) -> tuple[float, list[str]]`:
  exact email 1.0, noreply login match 0.95 (parse `\d+\+(.+)@users.noreply.github.com`),
  same email local-part 0.6, normalized name similarity (casefold, strip accents,
  token sort, difflib ratio) weighted 0.5, login vs name 0.4; cap 1.0, reasons list.
  `suggest.generate(session)` upserts pending MergeSuggestions above 0.55, skipping
  dismissed pairs. Table-driven unit tests with concrete pairs.
- [ ] Commit `feat: add identity resolution, person merge and suggestions`.

## Phase 3: Git ingestion

### Task 3.1: Clone manager
**Files:** Create `src/gca/sync/gitrepo.py`, `tests/integration/test_gitrepo.py`
(synthetic repos via `git init` fixtures in tmp dirs, `file://` remotes).
- [ ] `GitMirror(base_dir, org, repo)` with `clone_url(token) / ensure(token) /
  fetch(token) / log_numstat(default_branch, since_oid) -> list[RawCommit] /
  repack()`. Blobless: `clone --bare --filter=blob:none`; auth only when token:
  env `GCA_GIT_TOKEN` + `GIT_ASKPASS=gca-askpass` + username embedded as
  `x-access-token` via `credential.username` config flag, never in URL.
  `RawCommit(oid, parents, author_name, author_email, authored_at, committed_at,
  subject, files: list[RawFile(path, old_path, additions, deletions)])`; parser uses
  `git log -z --numstat -M --pretty=format:...%x1f...` records, binary files (`-`)
  count 0, merge commits have no files. Assert exact numbers from scripted history.
- [ ] Commit `feat: add blobless mirror clone manager with numstat parser`.

### Task 3.2: Classification and significance
**Files:** Create `src/gca/metrics/classify.py`, `src/gca/metrics/significance.py`,
`tests/unit/test_classify.py`, `tests/unit/test_significance.py`.
- [ ] `classify_path(path) -> FileClass` rules: generated/vendored (lockfiles,
  `vendor/`, `node_modules/`, `dist/`, `.min.*`, maps, snapshots, `*.svg`,
  `migrations/` auto), docs (`*.md`, `docs/`), config (yaml/toml/ini/json without
  code), tests (`test_*`, `*_test.*`, `tests/`, `__tests__/`), else code. Weights
  CODE 1.0 TESTS 0.7 CONFIG 0.5 DOCS 0.3 GENERATED 0.05.
- [ ] `score_commit(files: Sequence[RawFile], is_merge) -> tuple[float, bool]`
  returning (significance, is_mechanical): mechanical when total adds+dels >= 2000 and
  min(adds, dels)/max(adds, dels) >= 0.8, or >= 50 files all renames; mechanical
  multiplies weighted mass by 0.2; significance `log1p(mass)`, merges 0.
- [ ] Commit `feat: add path classification and significance scoring`.

### Task 3.3: Ingest pipeline, churn, rollups
**Files:** Create `src/gca/sync/ingest.py`, `src/gca/metrics/churn.py`,
`src/gca/metrics/rollup.py`, `tests/integration/test_ingest.py` (real postgres).
- [ ] `ingest.ingest_repo(session, repo, raw_commits)` inserts commits + files
  (skipping known oids), resolves identities, then `churn.compute_for_commits`
  (window 21d: per file, churned = min(dels, prior adds in window); self vs cross by
  author person), then `rollup.apply_for_commits` upserting day stats, updates
  `repo.last_ingested_oid`. Integration test: scripted 3-author history with a rewrite
  inside and outside the window asserts exact churn splits and rollups.
- [ ] Commit `feat: add commit ingestion with churn and daily rollups`.

## Phase 4: GitHub API sync

### Task 4.1: API client with GraphQL check and REST fallback
**Files:** Create `src/gca/sync/api.py`, `tests/unit/test_api_client.py` (fixture
responses via httpx2 MockTransport).
- [ ] Before coding, verify online whether fine-grained PATs are accepted by the
  GraphQL API today; implement accordingly. `GitHubClient(token)` exposes
  `validate_org(login) -> OrgInfo` (must be an Organization, else `NotAnOrgError`),
  `list_repos(login) -> list[RepoInfo]`, `pull_requests(repo, cursor) -> PRPage`
  (PRs with nested reviews), `rate_status()`. GraphQL primary; REST fallback
  (`/orgs/{login}`, `/orgs/{login}/repos`, `/repos/{o}/{r}/pulls?state=all`,
  `/repos/{o}/{r}/pulls/{n}/reviews`) selected automatically when GraphQL rejects the
  token. Retry/backoff honoring `retry-after` and remaining==0 sleeps until reset;
  raises `RateLimitExhausted` carrying reset time for the orchestrator.
- [ ] Commit `feat: add github api client with graphql and rest fallback`.

### Task 4.2: Org sync orchestrator
**Files:** Create `src/gca/sync/orchestrator.py`,
`tests/integration/test_orchestrator.py` (API mocked, git real).
- [ ] `sync_org(org_id, *, full=False)`: discovery upsert, filter application,
  bounded `asyncio.Semaphore(4)` per-repo pipeline (fetch, numstat, ingest, PR sync
  with cursor persistence), SyncRun rows, per-repo checkpoint fields, org marked
  degraded on auth failure without affecting other orgs. `remove_org(org_id)` cascade
  delete + clone dir removal. Also prune identities/persons left orphaned.
- [ ] Commit `feat: add org sync orchestrator with checkpoints and cascade removal`.

## Phase 5: Scheduler

### Task 5.1: Worker, jobs, period math
**Files:** Create `src/gca/worker.py`, `src/gca/scheduler/{jobs,ledger,periods}.py`,
`tests/unit/test_periods.py`.
- [ ] `periods.period_bounds(kind, ref_date)` and `periods.closed_period_ending
  (kind, today)` for week/month/trimester(4-month)/quarter/half_year/year, tz-aware;
  exhaustive unit tests incl. boundaries.
- [ ] Worker: APScheduler BlockingScheduler-style asyncio setup with jobs: org sync
  every 6h (per org, jittered), period watcher daily 00:15 (ledger-guarded report
  generation + email dispatch for enabled schedules), clone maintenance weekly,
  suggestion generation daily. Job ledger unique insert as the idempotency gate.
- [ ] Commit `feat: add worker scheduler with period logic and job ledger`.

## Phase 6: Web UI

### Task 6.1: Base shell, org context, setup gate
**Files:** Create `src/gca/web/{__init__,deps,context}.py`,
`src/gca/web/routers/{setup,dashboard}.py`, `src/gca/web/templates/` (`base.html`,
`setup.html`, `dashboard.html`, partials), `src/gca/web/static/` (vendored
`htmx.min.js` 2.0.10, `chart.umd.js` 4.5.1, `app.css`), `tests/integration/test_web_setup.py`.
- [ ] Middleware: no validated org -> redirect all to `/setup` (except static,
  healthz). Setup POST validates org+PAT via GitHubClient, stores encrypted, seeds
  first sync job. Org multi-select stored in a cookie (`orgs=1,2`), `context.scope()`
  dependency resolves to org id list (empty = all).
- [ ] Dashboard: KPI cards (period selector), Chart.js activity trend fed by a JSON
  data endpoint, top movers table, insight panel placeholder hidden without AI.
- [ ] Commit `feat: add web shell, setup gate and dashboard`.

### Task 6.2: Repos, people, orgs, people management, settings
**Files:** Create `src/gca/web/routers/{repos,people,manage,orgs,settings}.py` +
templates, `tests/integration/test_web_views.py`.
- [ ] Repos list (sortable stats, filter state), repo detail (trend, contributors,
  significance mix). People list, person detail (percentiles panel, per-repo split,
  identities). Manage: suggestions queue accept/dismiss (htmx), manual merge picker,
  unmerge, per-org person filters. Orgs: add/remove (confirm cascade), repo filters
  editor, token revalidate, sync now, status. Settings: mail (provider fields,
  test-send), AI (fields, test button), recipients CRUD, schedule toggles per period
  and report kind.
- [ ] Commit `feat: add repos, people, orgs, management and settings views`.

## Phase 7: Reports

### Task 7.1: PDF builders and report service
**Files:** Create `src/gca/reports/{builder,render,service}.py`,
`src/gca/reports/templates/{report_base.html,overview.html,person.html,repo.html}`,
`tests/integration/test_reports.py`.
- [ ] `render.chart_png(fig) -> bytes` matplotlib helpers; WeasyPrint HTML templates
  sharing tokens with web css. Builders: overview (org scope KPIs, trends, top
  contributors, percentile table), person (volume, churn, significance, percentile
  position, repo split), repo (contributors, trend, churn). `service.generate(kind,
  period, scope, subjects) -> Report` writes PDF to reports volume, records row.
  On-demand path accepts custom ranges and explicit repo/person lists.
- [ ] Commit `feat: add pdf report generation`.

## Phase 8: Mail

### Task 8.1: Backends and dispatch
**Files:** Create `src/gca/mail/{__init__,backend,smtp,graph,dispatch}.py`,
`tests/unit/test_mail_backends.py`, `tests/integration/test_mail_smtp.py` (Mailpit).
- [ ] `MailBackend` protocol: `send(message: OutgoingMail) -> None`;
  `OutgoingMail(subject, html_body, recipients, attachments: list[tuple[str, bytes,
  str]])`. SMTP via smtplib STARTTLS. Graph: client credentials token cache
  (exp-60s), `sendMail` JSON with base64 fileAttachment when <3MB else upload
  session; errors typed `MailDeliveryError`. `dispatch.send_report(report)` resolves
  backend from SettingsStore, marks emailed_at or leaves unsent + banner flag.
- [ ] Commit `feat: add graph and smtp mail backends with dispatch`.

## Phase 9: AI insights

### Task 9.1: Client and insight service
**Files:** Create `src/gca/ai/{client,insights}.py`, `tests/unit/test_ai_insights.py`
(MockTransport).
- [ ] `AIClient(base_url, key, model)` chat-completions call, 30s timeout, single
  retry. `insights.for_view(view, scope, period) -> str | None`: cache hit via
  `Insight.cache_key = sha256(view|scope|period|data_hash)`; builds compact stats
  context, English-only prompt, stores result; `None` on any failure (panels hide).
  Report narrative uses the same service with a report-specific view key.
- [ ] Commit `feat: add ai insight service with caching and degradation`.

## Phase 10: CLI and docs

### Task 10.1: Admin CLI
**Files:** Create `src/gca/cli.py` (argparse, `gca` entry point),
`tests/integration/test_cli.py`.
- [ ] Subcommands: `org add <login> --token-file <path>`, `org list`, `org remove`,
  `sync run [--org]`, `report generate --kind --period|--from/--to [--org]`,
  `suggest run`, `settings seed`. Console script `gca = gca.cli:main`; used for
  headless setup and E2E driving (tokens read from files, never argv).
- [ ] Commit `feat: add admin cli`.

### Task 10.2: Documentation
**Files:** Create `README.md`, `docs/deployment.md`, `docs/configuration.md`,
`docs/metrics.md`.
- [ ] README: what it is, feature list, screenshots section, dev quickstart
  (`docker compose -f compose.yml -f compose.dev.yml up`), prod deploy without clone
  (download compose.yml + Caddyfile + .env), release flow. metrics.md documents the
  formulas and the churn approximation honestly.
- [ ] Commit `docs: add readme and operator documentation`.

## Phase 11: Validation and delivery

### Task 11.1: Full-stack validation
- [ ] `docker compose -f compose.yml -f compose.dev.yml up --build`; alembic runs,
  healthz green, Mailpit reachable.
- [ ] Playwright browser pass: setup gate with real org 1 PAT (typed from file via
  CLI instead if UI paste is unsafe), dashboard renders, add org 2, trigger syncs,
  wait for completion, verify repos/people populate, run a merge suggestion accept,
  generate on-demand report, download PDF, trigger test email, verify in Mailpit.
- [ ] Real 2-org full sync completes; spot-check numbers against `git log --shortstat`
  for one repo per org.

### Task 11.2: Adversarial review and hardening
- [ ] Dispatch independent review agents over the diff: correctness (churn SQL,
  cursor handling, timezone math), security (token handling, template escaping,
  subprocess args), ops (compose, migrations, degradation paths). Fix confirmed
  findings, re-run tests.

### Task 11.3: Ship
- [ ] Push `main` and `rc/0.1.0`, open PR rc -> main titled
  `feat: initial release of the contribution analyzer`, squash-merge, watch
  release-please PR, merge it, watch release.yml publish image + move `stable`.
- [ ] Redeploy prod-shaped stack from the published image locally to prove
  clone-free deployment. Record any unresolved external blocker in
  `BLOCKERS_dnc.md`.
