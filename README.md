# growth-engine

An autonomous growth engine that runs on one always-on PC and serves any number of projects. It builds
each project's search website, runs a double-opt-in waitlist with a referral loop, and writes a weekly
digest with headless Claude Code under an existing Claude subscription. It uses no paid API keys and no
paid services.

- **Multi-project.** The engine code knows nothing about any one product. Each project is a folder of
  config (brand, base URL, languages, keyword map, page specs, data sources, channels, rules) in a
  **private engine home** outside this public repository. `examples/home` holds a fictional example.
- **Scheduler.** A systemd user timer (or cron) calls `growth tick` every five minutes. A SQLite run
  ledger makes every scheduled slot run at most once, even across reboots, power cuts and racing ticks.
  Missed time is caught up by policy, and an interrupted side effect is never repeated.
- **AI under the subscription.** AI jobs run `claude -p` with every Anthropic key stripped from the
  environment. A budget (runs per day, per week, per project, a night-time window, and a cooldown after
  a usage limit) keeps them inside the subscription.
- **Search website.** Static pages per language from TOML page specs and live product data. JSON-LD,
  hreflang, a sitemap and IndexNow all sit behind config. Item pages are generated only for eligible
  items and stay `noindex` until they have real evidence. The build refuses third-party art, remote
  images, rating markup and numbers without a measurement source.
- **Waitlist.** A Cloudflare Worker with D1 (free tier): double opt-in, personal invite links, move-up
  rewards, share links, a live counter, and token-protected stats for the digest's goal tracker.
- **Autonomy rules.** Each channel either runs on its own or not at all. Reddit drafts are the only
  human queue. The never-automate list is enforced as hard checks: see [docs/channels.md](docs/channels.md).
- **Launch directories.** Imports the community list of places to post a startup
  (PlacesToPostYourStartup, CC0), scores each entry for the project's audience, drafts per-site
  listing text from the project's fact sheet, and from launch day on submits only where an API or a
  plain form is allowed: no captchas, no accounts. Everything else is skipped and reported.
- **Control from the Mac.** A native menu-bar app shows the goal tracker (per day, channel and
  country), what ran and what runs next, the Reddit drafts waiting, and the engine's health, and holds
  every control: the kill switch, jobs and channels on, off or paused, targets, budgets, rate limits,
  settings and the launch switch. It reaches the engine only through an SSH tunnel; the engine
  listens on localhost and opens no port. See [docs/app.md](docs/app.md).
- **Guarded.** Secrets encrypted at rest, least-privilege tokens, an outbound allow-list, per-channel
  rate limits, a kill switch, and a hash-chained audit log of every automated action and every
  control. See [SECURITY.md](SECURITY.md).
- **Weekly digest.** For each project: what ran, traffic, sign-ups per day against the goal with
  sources and the referral k-factor, and anything blocked. Claude writes the narrative from the facts;
  text with a number that isn't in the facts is dropped.

## Quick start

```sh
python3 -m growth --home examples/home check           # validate config
python3 -m growth --home examples/home build example   # fetch data and build dist/example/
python3 -m unittest discover -s tests -t .             # Python 3.11+; the Worker tests need Node 22.5+
```

Real projects live in a private home (for example `~/growth-home` on the always-on PC). Set
`GROWTH_HOME`, or pass `--home`.

| Command | What it does |
|---|---|
| `growth tick` | Run every due job once (the timer calls this) |
| `growth run <project\|engine> <job>` | Run one job now, still under the lock and the ledger |
| `growth build <project>` | Fetch data and build one site now |
| `growth check` | Validate engine.toml and every project |
| `growth status` | Last week's runs, AI budget use, anything blocked |
| `growth channels` | Every channel and how far it may be automated |
| `growth queue <project>` | Write the Reddit draft page and print its path |
| `growth deploy <project>` | Build and check locally now; production only after the launch go |
| `growth self-update` | Move to the newest green, signed main commit (the update timer calls this) |
| `growth serve [--demo]` | The control API for the Mac app, on 127.0.0.1 only (`--demo`: made-up data, a local preview of the app) |
| `growth kill on\|off\|status` | The kill switch |
| `growth audit [--verify]` | The audit log and its hash chain |
| `growth secret set\|list\|rm` | The encrypted secrets store |

## CI/CD

- **CI** (GitHub Actions, every push and PR): ruff and shellcheck, the unit tests on Python 3.11 and
  3.12, the example site's build with a link check, the never-automate and guard checks, the Mac
  app's build, and a gitleaks scan. `ci-ok` is green only when all of them are; require it for `main`.
  CodeRabbit reviews every PR.
- **Engine updates** (GEEKOM, every 15 minutes): fast-forward to the newest `main` commit whose checks
  all passed and whose signature GitHub verified, self-check with the new code, restart, or roll back.
- **Site deploys** (per project, when the built site changed): link check, a local check with
  `wrangler dev` on the engine's machine, and an upload to Cloudflare and production only after
  `[deploy] launched = true`, with automatic rollback. Updates and deploys show in the weekly digest and the Mac app. See [docs/deploy.md](docs/deploy.md).
- **One-time setup by the owner:** a free Cloudflare account and an API token with only Workers
  Scripts: Edit and D1: Edit, stored with `growth secret set CLOUDFLARE_API_TOKEN` (and
  `CLOUDFLARE_ACCOUNT_ID`) on the GEEKOM; then require the `ci-ok` check for `main` in GitHub's branch
  settings.

## Docs

- [docs/setup.md](docs/setup.md): install on Ubuntu or WSL, the private home, systemd, keeping WSL up
- [docs/architecture.md](docs/architecture.md): scheduler guarantees, jobs, AI budget, generator, digest
- [docs/projects.md](docs/projects.md): project config reference
- [docs/deploy.md](docs/deploy.md): the CI/CD pipeline, Cloudflare setup (free tier), the launch switch
- [docs/app.md](docs/app.md): the Mac control app
- [SECURITY.md](SECURITY.md): threat model and guards
- [docs/channels.md](docs/channels.md): channels, approval gates and the never-automate list

No site reaches production before the owner's launch go: until a project sets `[deploy] launched = true`
(from the Mac app), deploys are only checked locally and nothing is uploaded.
