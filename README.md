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

## Docs

- [docs/setup.md](docs/setup.md): install on Ubuntu or WSL, the private home, systemd, keeping WSL up
- [docs/architecture.md](docs/architecture.md): scheduler guarantees, jobs, AI budget, generator, digest
- [docs/projects.md](docs/projects.md): project config reference
- [docs/deploy.md](docs/deploy.md): one-command deploy to Cloudflare (free tier) and the static-only option
- [docs/channels.md](docs/channels.md): channels, approval gates and the never-automate list

Nothing in this repository deploys, posts or sends anything by itself. Deploying is a separate,
explicit command (`deploy/cloudflare.sh`).
