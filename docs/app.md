# The Mac app (control center)

`Growth Engine.app` is a menu-bar app with a window. It shows the goal tracker, what the engine did
and will do next, what waits for the captain, and the engine's health, and it is where every control
lives. It talks to the engine only through an SSH tunnel to the GEEKOM; nothing is opened to the
network.

## Install

```sh
mac/GrowthEngine/build.sh --install      # needs the Xcode Command Line Tools; ~30 s
open ~/Applications/"Growth Engine.app"
```

It runs as a menu-bar icon (no Dock icon). The icon shows the state: running, halted (kill switch),
offline, or a count of drafts waiting. Open the Control Center from the menu (⌘O).

On the GEEKOM, `deploy/install-wsl.sh` already started the control API (`growth-app.service`, on
127.0.0.1:8765). The app's default connection is the `geekom-wsl` SSH alias; change it under
Settings (⌘,).

## What it shows

- **Overview:** confirmed sign-ups against the target and the plan line, the pace, what is waiting
  for you (Reddit drafts, failed jobs, blocked actions), the next scheduled runs, and engine health
  (last scheduler tick, AI budget, audit chain, last update).
- **Goal:** sign-ups per day against what is needed, by channel and by country (countries outside
  the goal's zone are shown apart), and the targets.
- **Waiting for you:** the Reddit drafts. "Copy and open thread" puts the text on the clipboard and
  opens the thread; you post from your own account. Approve, reject or mark posted. The engine never
  posts them.
- **Jobs, Channels, Activity, Project settings, Engine, Audit log.**

When the GEEKOM is asleep or off, a banner says so with the time it was last seen, the last data
stays visible but dimmed, and controls are disabled until it is back. The engine catches up by its
own rules when it wakes (see docs/setup.md), and nothing that already ran runs twice.

## What it controls

Every control is a request to the engine's control API, which validates it exactly like a config
file edit, refuses it with the reason if it does not pass, and writes it to the audit log with the
actor `captain`. Settings changed in the app live in `state/engine/control.json` on the GEEKOM, a
layer over the TOML files.

| Control | Where |
|---|---|
| Kill switch (halt or resume the whole engine) | toolbar, menu bar |
| Pause or resume a project | Jobs |
| Switch a job on or off, run it now | Jobs, Engine |
| Pause a channel, set its rate limit (e.g. `20/24h`) | Channels |
| Target, start date, days, countries that count | Goal |
| AI runs per day, week and project | Engine |
| Brand name, domain, indexing, keywords | Project settings |
| The launch switch (allows production deploys) | Project settings |
| Approve, reject, mark posted | Waiting for you |

A control can never switch on a channel above its level, skip a never-automate check, or post a
Reddit draft.

## Demo without the GEEKOM

```sh
python3 -m growth serve --demo          # made-up data from the example project, on 127.0.0.1:8765
```

Then in the app's Settings choose "This Mac" with the token `demo`. A banner marks the data as
demo data. This is a local preview only: the demo server listens on 127.0.0.1, serves a throwaway
copy of the example home, and controls nothing real. The token `demo` is accepted only by a demo
server; a server for a real home refuses it, even if it was stored as `APP_TOKEN`.

## Light on memory

One SwiftUI process (about 40 to 60 MB) that polls every 15 seconds while the window is open and
every minute otherwise, plus one `ssh` process for the tunnel. On the GEEKOM the control API is one
small Python process capped at 256 MB by its unit.
