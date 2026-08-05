# Deployment

Production runs entirely from the published container image. You do not clone the
repository; you download three files, fill in one of them, and start the stack.

## Prerequisites

- Docker with Compose v2 (`docker compose`, not the legacy `docker-compose`)

The stack binds to `127.0.0.1` only: Caddy publishes the dashboard on
`127.0.0.1:${APP_PORT:-8080}` and nothing else is exposed. For remote access,
front it with whatever you already run for TLS (reverse proxy, VPN, SSH
tunnel); the stack itself never listens on a public interface.

## Files needed

| File | Where to put it | Purpose |
|---|---|---|
| `compose.yml` | working directory | Service definitions. References `ghcr.io/erlete/gh-contribution-analyzer`, no build context. |
| `deploy/Caddyfile` | `deploy/` next to `compose.yml` | Caddy site config: basic auth plus reverse proxy to the app. Mounted read-only. |
| `.env` | working directory | Your deployment configuration, copied from `.env.example`. |

Then:

```sh
docker compose up -d
```

## Environment variables

All variables live in `.env`, grouped as in `.env.example`. Operational settings
(org tokens, recipients, schedules) are managed in-app; the `MAIL_*` and
`AI_*` values only seed initial defaults on first boot and can be changed from the
settings screen afterwards.

### Database

| Variable | Description |
|---|---|
| `POSTGRES_USER` | Postgres role name. Default `gca`. |
| `POSTGRES_PASSWORD` | Postgres password. Change it. |
| `POSTGRES_DB` | Database name. Default `gca`. |
| `DATABASE_URL` | Full SQLAlchemy URL used by app and worker, e.g. `postgresql+psycopg://gca:change-me@postgres:5432/gca`. The host must match the compose service name (`postgres`). Must agree with the three variables above. |

### Application

| Variable | Description |
|---|---|
| `APP_SECRET_KEY` | Fernet key used to encrypt credentials at rest (org tokens, mail secrets, AI keys). Required. |
| `APP_BASE_URL` | Public base URL of the deployment, e.g. `https://gca.example.com`. |
| `TZ` | Container timezone. Default `UTC`. |

Generate `APP_SECRET_KEY` exactly as documented in `.env.example`:

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Caddy (the only exposed service, loopback only)

| Variable | Description |
|---|---|
| `APP_PORT` | Port the dashboard is published on, always bound to `127.0.0.1`. Default `8080`. |
| `BASIC_AUTH_USER` | Basic auth username. Default `root`. |
| `BASIC_AUTH_HASH` | bcrypt hash of the basic auth password, with every `$` doubled as `$$`. Required in production. |

Generate `BASIC_AUTH_HASH` with the command below. The `sed` step doubles the
dollar signs: Docker Compose re-interpolates `$` sequences in `.env` values, so
an unescaped bcrypt hash gets silently mangled and every login fails.

```sh
docker run --rm caddy:2 caddy hash-password --plaintext 'your-password' | sed 's/\$/\$\$/g'
```

### Mail seed: Microsoft Graph (production)

Requires an Entra ID app registration with the application permission `Mail.Send`
and admin consent. Seeds the Graph backend on first boot only.

| Variable | Description |
|---|---|
| `MAIL_AZURE_CLIENT_ID` | App registration client id. |
| `MAIL_AZURE_CLIENT_SECRET` | App registration client secret. |
| `MAIL_AZURE_TENANT_ID` | Entra tenant id. |
| `MAIL_SENDER_ADDRESS` | Mailbox to send as, e.g. `noreply@example.com`. |

SMTP is the alternative mail backend and has no environment seeds: configure it
on the settings screen (in dev, point it at Mailpit with host `mailpit`, port
`1025`, STARTTLS off).

### AI seed: OpenAI-compatible endpoint

| Variable | Description |
|---|---|
| `AI_SERVICE_URL` | Base URL of an OpenAI-compatible API. |
| `AI_SERVICE_KEY` | Bearer key. |
| `AI_SERVICE_MODEL` | Model name to request. |

### Image tag

| Variable | Description |
|---|---|
| `GCA_TAG` | Image tag for app and worker. Defaults to `latest`. Set to a specific version to pin. |

## Volumes

| Volume | Mounted into | Contents | Disposable? |
|---|---|---|---|
| `pgdata` | postgres | The database. Source of truth for everything. | No. Back it up. |
| `clones` | worker only | Bare blobless mirror clones of synced repositories. Pure cache: if lost, the worker re-clones and re-ingests idempotently. | Yes. |
| `reports` | worker (writes), app (serves) | Generated PDF reports. | Optional to keep; regenerable on demand from the database. |
| `caddy-data`, `caddy-config` | caddy | TLS certificates and Caddy state. | Recreated automatically, but keeping `caddy-data` avoids re-issuing certificates. |

## Backups

- Dump Postgres regularly; it is the only source of truth:

  ```sh
  docker compose exec postgres pg_dump -U gca gca > backup.sql
  ```

- Optionally back up the `reports` volume if you want to keep historical PDFs
  without regenerating them.
- Never back up `clones`; it is a disposable cache that rebuilds itself.

## Upgrades

1. Pick the new version: either leave `GCA_TAG` unset (tracks `latest`) or set it to
   the new tag in `.env`.
2. Pull and restart:

   ```sh
   docker compose pull
   docker compose up -d
   ```

The container entrypoint runs schema migrations before starting each service,
serialized through a Postgres advisory lock, so app and worker can start in any
order.

## Authentication model

Caddy basic auth is the single authentication layer. The app itself has no login;
it trusts the network boundary. Only Caddy publishes a port, bound to
`127.0.0.1:${APP_PORT:-8080}`, so every request to the dashboard passes through
basic auth with the `BASIC_AUTH_USER` and `BASIC_AUTH_HASH` credentials. Traffic
is plain HTTP on loopback; anything remote must arrive through your own TLS
proxy or tunnel. Do not publish the app, worker or postgres ports in production.
