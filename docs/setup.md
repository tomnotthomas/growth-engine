# Setup on the always-on PC (Ubuntu, or Ubuntu on WSL)

The engine is plain Python 3.11+ with no dependencies, plus Claude Code for the AI jobs. These steps
assume Ubuntu on WSL on a Windows 11 PC that is always on. Native Ubuntu skips step 4.

## 1. Code and private home

```sh
git clone https://github.com/tomnotthomas/growth-engine.git ~/growth-engine
mkdir -p ~/growth-home && cp -r ~/growth-engine/examples/home/* ~/growth-home/
```

Put each real project folder into `~/growth-home/projects/<id>/`, and keep the home private (a private
git repository, or backups). It holds the project configs, keyword data, curated facts and later the
run ledger (`state/`) and built sites (`dist/`). None of it belongs in the public repository.

```sh
cd ~/growth-engine && GROWTH_HOME=~/growth-home python3 -m growth check
```

## 2. Claude Code under the subscription

Install Claude Code and log in once with the subscription account (`claude`, then `/login`). Don't
set `ANTHROPIC_API_KEY`. The engine strips it from the AI jobs' environment anyway, so a stray key
can't turn a job into paid API usage. AI jobs run `claude -p --output-format json --tools ""`: no
tools, text only.

Budget (in `engine.toml`, `[ai]`): at most 3 headless runs a day and 10 a week across all projects,
4 a week per project, started only between 01:00 and 07:00, and a 5-hour cooldown after Claude reports
a usage limit. Today the only AI job is the weekly digest (1 run a week). A job that has no budget is
retried on later ticks and recorded as missed, with the reason, once it is too late.

## 3. Scheduler

```sh
~/growth-engine/deploy/install-wsl.sh ~/growth-home
```

This installs `growth-engine.timer` as a user unit (every 5 minutes, `Persistent=true`) and enables
lingering, so it runs without a login session. It writes `~/.config/growth-engine/env` with
`GROWTH_HOME`. Secrets go in that file too:

| Variable | Used by |
|---|---|
| `GROWTH_DIGEST_WEBHOOK` | optional: the digest is also POSTed there (e.g. an ntfy.sh topic URL) |
| `<PROJECT>_STATS_TOKEN` (name set in the project's `[waitlist] stats_token_env`) | the digest's goal tracker reads the Worker's stats |
| PostHog personal API key (name set in `[analytics] api_key_env`) | traffic in the digest |

Logs: `journalctl --user -u growth-engine.service`. Status: `python3 -m growth status`.

**Cron instead of systemd:** `*/5 * * * * cd ~/growth-engine && GROWTH_HOME=~/growth-home python3 -m growth tick >> ~/growth-home/tick.log 2>&1`.
The guarantees are the same, because they live in the engine, not in the timer.

## 4. WSL: systemd and surviving reboots

1. In Ubuntu, `/etc/wsl.conf` must contain:
   ```ini
   [boot]
   systemd=true
   ```
   Then run `wsl --shutdown` in Windows and open Ubuntu again.
2. WSL stops a distro when no Windows process uses it. To keep it up from boot, run once in an
   elevated PowerShell:
   ```powershell
   powershell -ExecutionPolicy Bypass -File \\wsl$\Ubuntu\home\<user>\growth-engine\deploy\windows\keep-wsl-running.ps1 -User <linux user>
   ```
   This registers a start-up task that keeps the distro alive with `sleep infinity`.
3. In Windows power settings, set the PC to never sleep (it may turn the screen off).

## What happens when the PC is off

The timer fires right after boot (`Persistent=true`). For each job the engine compares the slots that
were due with its ledger:

- `catchup = "latest"` (the default) runs only the most recent missed slot and records the older ones
  as missed;
- `catchup = "all"` runs each missed slot that is younger than `max_late`;
- `catchup = "skip"` runs nothing that is more than 15 minutes late.

A job that was running when the power went off is marked interrupted. Idempotent jobs (fetch, build)
are retried. Others are not. An outward action whose outcome is unknown (the PC died between "send"
and "sent") is never repeated, and the digest reports it.
