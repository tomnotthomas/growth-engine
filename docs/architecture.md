# Architecture

```
growth/
  config.py      engine.toml + projects/<id>/project.toml -> validated Engine / Project
  schedule.py    "every 6h" | "daily 04:00" | "weekly mon 05:30" -> slots (UTC, DST-aware)
  store.py       SQLite: run ledger, AI usage, side-effect ledger, blocks, text fingerprints
  runner.py      tick(): lock, stale-run recovery, due slots, catch-up, claim, run
  budget.py      AI budget: per day, per week, per project, time window, cooldown
  ai.py          claude -p under the subscription (no API keys, no tools, never --bare)
  policy.py      the never-automate list as hard checks on every outward action
  channels.py    every channel and its automation level (auto, human-queue, needs-approval, off)
  queue.py       the one human queue: Reddit drafts, as a static page
  directories.py launch-directory catalogue: import, scoring, status
  goal.py        sign-ups per day against the target, sources, referral k-factor
  analytics.py   read-only PostHog traffic and waitlist stats
  jobs/          fetch-data, build-site, indexnow, directories-*, digest (+ digest_render)
  site/          page specs, collections, renderer, JSON-LD, build (sitemap, robots, checks)
  themes/base/   the base stylesheet every generated page uses; projects add tokens.css
  assets/        waitlist.js (forms, live counter, invite page)
  waitlist/      the Cloudflare Worker (waitlist.js), D1 schema, bundle writer
  guard.py       kill switch, hash-chained audit log, per-channel rate limits, outbound allow-list
  secrets.py     the encrypted secrets store (DPAPI-wrapped key on WSL)
  update.py      self-update to the newest green, signed main commit, with self-check and rollback
  control/       the control API for the Mac app (127.0.0.1 only), its snapshot, operations, demo
mac/             the Mac control app (SwiftUI), built with mac/GrowthEngine/build.sh
```

## The run ledger: why nothing runs twice

Each job's schedule turns into discrete **slots**, the moments a run was meant to happen. The table
`runs` has `UNIQUE (scope, job, slot)`. To run a slot, a tick must **claim** it inside
`BEGIN IMMEDIATE`. Exactly one claimant wins, whatever the number of processes. On top of that, `tick`
holds an exclusive `flock` on `state/engine.lock`, so only one process runs jobs at a time. That also
makes stale-run recovery simple: a run still marked `running` when a tick takes the lock belongs to a
process that died, and becomes `interrupted`.

Retries are per slot and only for idempotent jobs (`failed` or `interrupted`, up to `max_attempts`,
while the slot is younger than `max_late`). With `catchup = "all"` every such slot is retried, even
after a later slot succeeded; otherwise only the latest. A job that finds no AI budget once it has
started gives its claim back, so its slot stays due. A new job starts at its latest slot and never
backfills.

Outward actions go through **`JobContext.act(action, key, do)`**: the policy check, then a row in the
`effects` table (`PRIMARY KEY (scope, key)`) reserved as `pending` before the action and marked `done`
after. A key that is `done` is skipped. A key still `pending` means the outcome is unknown (the power
went off between the two writes), so the engine doesn't repeat it and reports it instead. IndexNow keys
are `indexnow:<path>:<content hash>`; the digest webhook key is the digest's date.

## Jobs

| Kind | Scope | AI | Idempotent | What it does |
|---|---|---|---|---|
| `fetch-data` | project | no | yes | Copies each data source (HTTP JSON or a file) into `state/projects/<id>/data/`, keeping only `keep_fields`. Records when each item was first seen (the first fetch is the baseline). |
| `build-site` | project | no | yes | Builds `dist/<out>/` and swaps it in atomically; a failed build leaves the old site untouched. |
| `indexnow` | project | no | no | Pings IndexNow for indexable pages whose content hash changed; needs `[site] indexable`. |
| `directories-sync` | project | no | yes | Imports the launch-directory list into `state/projects/<id>/directories.json` and scores each entry, keeping statuses and drafts. |
| `directories-draft` | project | required | yes | Drafts listing texts for the best-scoring undrafted sites from the project's fact sheet (one AI run per batch); refuses text with numbers the fact sheet lacks. |
| `directories-submit` | project | no | no | From launch day on: submits once to each verified API or plain-form site through the side-effect ledger; marks the rest skipped. |
| `deploy` | project | no | yes, after the launch go (each schema, upload and promote step is a side effect, recorded per attempt) | When the built output changed: builds, link-checks, smoke-checks it locally with `wrangler dev` and a local D1, and only with `[deploy] launched = true` uploads and promotes it to production, smoke-checks the domain and rolls back to the last good version on failure. See docs/deploy.md. |
| `digest` | engine | optional | no | Collects facts, has Claude write the narrative (without AI budget it goes out on time with the facts alone), checks its numbers, writes `state/engine/digests/<date>.md` and `.html`, and optionally POSTs to a webhook. |

## The site generator

Inputs per project: `pages/*.toml` (one file per page, a `[langs.<lang>]` table per language, each
with path, title, description and sections), `ui.toml` (navigation, footer, forms, mails), curated
collection data, live data, `measurements.toml`, `media.toml` and the theme's `tokens.css`.

Section types: `hero`, `intro`, `tiles`, `proof`, `steps`, `features`, `spec`, `faq`, `article`
(paragraphs, h3, lists, tables, buttons, "last checked"), `close`, `item-hero`, `item-strip`,
`listing`, `waitlist-status`, `game-search` (searches every checked item in the browser from
`/data/<collection>-<lang>.json`; no match offers the waitlist form) and `hardware-check`. Copy may use `**bold**`, `[text](href)` and `~` for a non-breaking space.
Links can point at `page:<id>`, `item:<collection>/<slug>`, `app:` and `legal:privacy`. Placeholders
are `{brand}`, `{app_url}`, `{year}`, `{move_up}`, `{checked}`, `{legal.<key>}` and `{hardware.floor}` /
`{hardware.models}`, and on item pages every field of the
item. A value of `"@field"` takes the item's field (for example `body = "@why"` with a `fallback`).

Rules enforced at load time: every link resolves, every placeholder is known, the brand is never
written out literally (so a rename is a one-line change), forbidden characters stay out, every literal
entry of a `steps`, `features`, `faq` (`items` and `fallback`) or `spec` list has its section's keys in
every language, live or not, and each keyword cluster's primary words appear in its page's title or H1.

Rules enforced at build time:

- **Item pages** exist only for `playable` items that are in the live list (or had a page before:
  status changes keep the URL), have at least `min_searches`, belong to a wave up to `max_wave` and
  have no native version. A page stays `noindex` until its template's `index_if_any` evidence exists
  (a clip or measured numbers).
- **Whole site** is `noindex` with `robots.txt: Disallow: /` until `[site] indexable = true`. That
  setting requires a decided domain plus legal-notice and privacy pages.
- **Imagery:** every `src`, `poster` and `og:image` must be in the media manifest with a source.
  Remote images are refused. Game footage must be an own recording of a publisher in
  `[rules] footage_publishers`. CSS may only reference `data:` URIs and fonts. Data-source fields
  outside `keep_fields` (such as store art URLs) never reach the cache.
- **Legal pages:** a project with a waitlist refuses to build while its legal-notice or privacy page
  is unset or uses an empty `[legal]` value, so no sign-up form ever goes out without them.
- **Languages:** only `live_languages` are built and linked; the others stay ready.
- **Numbers:** measured values need `source` and `measured_at`. JSON-LD never carries ratings or
  reviews.
- **Structured data:** `Organization` and `WebSite` everywhere, `BreadcrumbList` with crumbs,
  `FAQPage` with FAQs, `Article` with `dateModified` on guides and item pages, `VideoObject` for clips.
- **Sitemap:** indexable pages only, `lastmod` from the content hash history, `xhtml:link` alternates.

Output: `dist/<out>/public/` (static, works on any host), plus `worker/`, `wrangler.toml` and
`schema.sql` when the project has a `[waitlist]`.

## Waitlist and referral loop

`growth/waitlist/waitlist.js` runs on Cloudflare Workers with D1. Sign-ups are double opt-in, and the
confirmation link opens a one-button page, so mail scanners that only `GET` links confirm nothing. Only
SHA-256 hashes of the confirm and status tokens are stored. A confirmed user gets a public invite code
(`/r/<code>`) and a private status link. Position = sign-up order minus `move_up_per_referral` per
confirmed invite, capped at `max_credited_referrals`. The live counter shows only real numbers, and
only from `counter_min` on. Abuse limits: a honeypot field, `signups_per_ip_hour` per hashed IP, and
at most three mails a day (ten minutes apart) to one address. Players and hosts get their own
status page and mails. The country comes from Cloudflare's geolocation and is stored without the IP. Every mail carries a one-click leave link that deletes the
row and recounts the inviter's credited invites. `/api/waitlist/stats` (Bearer `STATS_TOKEN`) feeds the digest.

Analytics, when `[analytics] provider = "posthog"`: the page script sends a short list of events
(page view, form view, submit, referral sent, game search) to the Worker's `/api/e`, which drops
every property not on its list and relays them to PostHog without IP, cookies or a person profile.
The Worker adds the server-side events (sign-up, confirmed, referral joined), each under a fresh random
id and tagged with the country stored at sign-up; D1 keeps no analytics id, so no address can be
joined to a visitor's page events. The digest's goal numbers come from the waitlist ledger (the Worker
stats); PostHog (only this project's `site`, with the personal key from the environment) feeds the
traffic and funnel sections. Only when the ledger is not reachable does the digest use PostHog's
confirmed events of the last 365 days for the goal, labelled as an estimate.

## The digest's goal tracker

`goal.py` reads the stats. Confirmed sign-ups per day against a linear plan over the `days` days from
`[goal] start` (the deadline is the last of them), what is needed per day from now on, a projection
from the last 7 days (only the days since the start in the first week), sign-ups by source, and the referral **k-factor**: for weekly cohorts at least two weeks old, the confirmed
sign-ups the cohort's members invited, divided by the cohort's size. With `[goal] zone` set, only
sign-ups from those countries count toward the goal; the rest are shown apart. Before the start the
status says when the goal starts.
