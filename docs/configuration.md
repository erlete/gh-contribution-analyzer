# Configuration

## Philosophy

`.env` carries deployment internals only (database, secret key, edge proxy). The
`MAIL_*` and `AI_*` environment values are seeds: on first boot they are
imported into the settings table, and only for keys that do not exist yet. From then
on the in-app settings screen is the single authority; changing the environment
later has no effect on already-seeded keys. All secrets stored in the database
(org tokens, mail secrets, AI keys) are encrypted with the deployment Fernet key
(`APP_SECRET_KEY`).

## Organizations

- Add an organization from the setup gate, the orgs page, or the CLI
  (`gca org add <login> --token-file <path>`). Adding validates the token
  immediately and queues a first sync.
- Removing an organization cascades: repos, commits, pull requests, reviews,
  rollups and every other derived row are deleted. The clone cache is cleaned up by
  the worker's weekly maintenance job.
- Each org can pause and resume its scheduled sync, replace its token, and
  revalidate it. A failing validation marks the org degraded; other orgs are
  unaffected.

### Token requirements

Each org needs its own fine-grained personal access token:

| Requirement | Value |
|---|---|
| Token type | Fine-grained PAT |
| Resource owner | The organization itself (user-owned tokens are rejected) |
| Repository access | The repositories you want analyzed |
| Contents | Read-only |
| Metadata | Read-only |
| Pull requests | Read-only |

Each token has its own adaptive rate budget; commit data is read from clones and
never consumes API quota.

## Repository filters

Per org, `repo_filter_mode` is one of `all`, `whitelist` or `blacklist`. On the
orgs page, repositories are picked from a dropdown of the org's known repos and
collected into a removable list. With nothing listed and mode `all`, every
repository is included. Changing filters immediately recomputes each repo's
inclusion flag.

## Person filters

The same mechanism exists per org for persons (`all`, `whitelist`, `blacklist`),
managed from the people screen, to exclude bots or scope reports to a team.

## Identity merging

Every unknown identity (git author name plus email, or GitHub login) automatically
creates a person. Identities are immutable rows; merging moves identity foreign
keys to the surviving person, so unmerge is always possible. Merges and unmerges
recompute the affected persons' rollups from the base tables.

Merge suggestions are generated after every sync and nightly. Signals, strongest
first:

- identical email on both persons (GitHub noreply addresses excluded)
- GitHub noreply email whose embedded login matches the other person's login
- identical email local part (at least 4 characters, generic mailbox names such as
  `admin` or `info` ignored)
- normalized full-name similarity (only counted at 0.8 similarity or higher)
- login equal to a name with spaces removed

Signals combine probabilistically into a score; pairs scoring at least 0.55 become
pending suggestions on the identity screen.

Names carried by more than two persons are excluded from name-based evidence:
they are machine or shared-account naming (a bot author, a service login
absorbed into several real people), and pairing their carriers would suggest
merging independent accounts. Emails are never excluded this way, since one
human committing under several name variants legitimately shares one email
across identities.

Every scan also revalidates pending suggestions against current data and
deletes the ones that no longer qualify, so merges and scoring improvements
clean up stale recommendations automatically. Dismissed suggestions are never
resurrected and never deleted. Each suggestion offers one button per
direction ("Keep X" absorbs the other person into X), so the survivor is always
explicit; the kept person retains its display name. After any merge, the survivor
is rescored against everyone else immediately, so related suggestions that were
cleared by the merge reappear without waiting for the next scan.

Commits are attributed to their git author only; `Co-authored-by` trailers are
not parsed (see docs/metrics.md).

## Mail

Two backends behind one interface. The backend auto-selects from configured
settings (Graph wins when the Azure values are present) and can be overridden
in-app.

### Microsoft Graph (production)

1. Create an Entra ID app registration.
2. Grant it the application permission `Mail.Send` and give admin consent.
3. Configure client id, client secret, tenant id and the sender mailbox (the
   mailbox the app sends as, e.g. `noreply@example.com`).

The backend uses the client credentials flow and `POST /users/{sender}/sendMail`;
attachments over 3 MB switch to an upload session automatically.

### SMTP

Host, port, optional username and password, STARTTLS toggle and sender address.
SMTP has no environment seeds: it is configured on the settings screen only. In
development, point it at Mailpit (host `mailpit`, port `1025`, STARTTLS off) so
every mail is captured at http://localhost:8025 instead of being delivered.

### Test send and degradation

The settings screen has a test-send button to verify the active backend. When mail
is unconfigured or a send fails, the app degrades gracefully: a dashboard banner
appears and reports are stored unsent. Once mail is fixed, stored reports can be
re-dispatched.

## AI insights

Configure an OpenAI-compatible endpoint: base URL, optional bearer key, and model
name. The insight service assembles a wide statistical context for the current
view, scope and period (totals, leaderboards, percentiles, per-repo splits),
requests a short English narrative, and caches the result in the database keyed
by view, org scope, period, data and instructions, so repeated visits do not
re-query the model.

Every insight area always renders. AI-generated text carries a brain AI chip in
the top right corner, in the web views and in the PDF reports. When AI is
unconfigured or a call fails, the same area shows deterministic data statements
built from the same context, without the chip, so the structure of every page
and report is identical either way.

The settings screen also holds per-area generation instructions (dashboard,
person views, repository views, reports). Whatever is written there is appended
to the AI prompt for that area, for example "highlight review activity" or
"write in a formal tone". Instructions are part of the cache key: saving new
ones regenerates affected insights on the next view.

Large combined report documents cap AI narrative calls at 40 sections; beyond
that, sections use the fallback statements so generation time stays bounded.

## Report schedules

Period kinds: `week`, `month`, `trimester`, `quarter`, `half_year`, `year`.

| Kind | Definition |
|---|---|
| week | ISO 8601 week, Monday start |
| month | Calendar month |
| trimester | 4-month blocks: Jan-Apr, May-Aug, Sep-Dec |
| quarter | Calendar quarter (3 months) |
| half_year | Jan-Jun and Jul-Dec |
| year | Calendar year |

Report kinds: `overview` is one document over the whole scope. `person` and
`repo` also produce one document each: an introduction with the scope's key
numbers, a table of contents with page numbers, then one analyzed section per
person or repository (key numbers, percentile position, activity trend,
splits, narrative).

Periodic reports are generated only when a period closes: a daily watcher looks at
the most recent fully closed period per enabled schedule and a job ledger
guarantees each period fires exactly once. Generation is never retroactive; older
closed periods are not backfilled. Generated reports are emailed to the active
recipients list, managed on the settings screen.

## Sync scheduling

- Automatic: every organization with sync enabled is synced every 6 hours (with
  jitter; the first run starts shortly after the worker boots).
- Manual: the "Sync now" button queues a request that the worker picks up within
  30 seconds. Orgs already running are skipped.
- Maintenance: weekly (Sunday 04:00 UTC), the worker repacks every clone with the
  blobless filter to keep them slim and removes orphaned clone directories.
