# Setup on the always-on PC (Ubuntu, or Ubuntu on WSL)

The engine is plain Python 3.11+ with no dependencies, plus Claude Code for the AI jobs. These steps
assume Ubuntu on WSL on a Windows 11 PC that is always on. Native Ubuntu skips step 4.

## In one command

```sh
curl -fsSL https://raw.githubusercontent.com/tomnotthomas/growth-engine/main/deploy/bootstrap.sh | bash -s -- <home-source>
```

`<home-source>` is the private home: a local folder or an rsync source like `user@laptop:/path/to/home`.
The script clones or updates the code, copies the home to `~/growth-home` (without `state/` and
`dist/`) and runs the scheduler install below. The steps it automates:

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
a usage limit. A job that needs AI (the launch-directory drafts) and has no budget is retried on
later ticks and recorded as missed, with the reason, once it is too late. The weekly digest only uses
AI for its narrative: without budget it goes out on time with the fact tables alone.

## 3. Scheduler

```sh
~/growth-engine/deploy/install-wsl.sh ~/growth-home
```

This installs three user units and enables lingering, so they run without a login session:

| Unit | What it does |
|---|---|
| `growth-engine.timer` | the scheduler: `growth tick` every 5 minutes (`Persistent=true`) |
| `growth-update.timer` | self-update every 15 minutes: the newest `main` commit whose GitHub checks passed and whose signature GitHub verified, a self-check, rollback if it fails (docs/deploy.md). `GROWTH_AUTO_UPDATE=0` when installing leaves it off |
| `growth-app.service` | the control API for the Mac app, on `127.0.0.1:8765` only (docs/app.md) |

It writes `~/.config/growth-engine/env` with `GROWTH_HOME` and `PATH`, nothing secret. Secrets go
into the **encrypted store** instead (SECURITY.md), one by one:

```sh
cd ~/growth-engine && python3 -m growth secret set <NAME>     # asks for the value; `secret list`, `secret rm`
```

| Secret | Used by |
|---|---|
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | the deploy job (scopes in docs/deploy.md) |
| `GROWTH_DIGEST_WEBHOOK` | optional: the digest is also POSTed there (e.g. an ntfy.sh topic URL; add its host to `[guard] outbound_allow`) |
| `<PROJECT>_STATS_TOKEN` (name set in the project's `[waitlist] stats_token_env`) | the goal tracker reads the Worker's stats |
| PostHog personal API key (name set in `[analytics] api_key_env`) | traffic in the digest |
| `GITHUB_TOKEN` | optional: a read-only token raises GitHub's API limit for the update checks |
| `APP_TOKEN` | created by the installer; the Mac app's login, fetched over SSH |

Logs: `journalctl --user -u growth-engine.service` (or `-u growth-update.service`,
`-u growth-app.service`). Status: `python3 -m growth status`. `tick` exits non-zero when a job failed,
so `systemctl --user status growth-engine.service` shows the failure.

**Guards** (engine.toml `[guard]`): `outbound_allow` lists extra hosts the engine may call, and
`[guard.rate_limits]` caps outward actions per channel (`directory-submit = "20/24h"`). The kill
switch: `growth kill on --reason "…"`, `growth kill off`, or the Mac app. The audit log:
`growth audit`, `growth audit --verify`.

**The Mac app:** see [app.md](app.md). It reaches the engine through `ssh geekom-wsl`, so the GEEKOM
needs the Mac's SSH key in `~/.ssh/authorized_keys` of the WSL user and an SSH server in WSL (or a
`ProxyJump` through Windows' OpenSSH), reachable only on the home network.

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
