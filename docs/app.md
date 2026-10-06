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

## How it is organised

The app answers four questions, in plain words:

- **Where do I start?** *Start here* explains what the engine does, shows the one next step with
  one button, and the path from a new project to a launched one (name and address, goal, legal
  notice, first build, Cloudflare, publishing, launch).
- **Which project am I in?** The switcher at the top of the sidebar shows the project and its
  address; click it to switch, or to add a project (it starts from the example's pages, with
  everything that acts outward switched off).
- **What is it doing, and when?** *Schedule* lists what runs next, by day, and what ran and what
  it produced. Details and "Run now" open per task on demand.
- **How do I edit it?** *Edit project*: Basics (name, address, goal, legal notice), Website text
  (title, description, heading and subline of every page and language), Schedule (each task on or
  off, how often), Connections (Cloudflare and other keys, stored encrypted on the engine's
  computer, never shown again), Launch.

*Waiting for you* holds the Reddit replies the engine drafted; it never posts them. *Results*
shows sign-ups per day against the goal, where they came from and from which countries. Under
*More*: *Safety and limits* (stop everything, pause a project or one kind of activity, limits
per day, the AI budget), *Engine health* and the *Activity record* (the audit log).

When the GEEKOM is asleep or off, a banner says so with the time it was last seen, the last data
stays visible but dimmed, and controls are disabled until it is back. The engine catches up by its
own rules when it wakes (see docs/setup.md), and nothing that already ran runs twice.

## Every control is real, checked and recorded

Every control is a request to the engine's control API, which validates it exactly like a config
file edit, refuses it with the reason if it does not pass, and writes it to the audit log with the
actor `captain`. Settings changed in the app live in `state/engine/control.json` on the engine's
computer, a layer over the TOML files. A control can never switch on a channel above its level,
skip a never-automate check, or post a Reddit reply.

`GrowthEngine --selftest --port <p> --token-command "<prints the token>"` drives every control
through the app's own client code against an engine and checks each change took effect; CI runs it
against a throwaway home. Run it only against a scratch home, never a real one.

## Demo without the GEEKOM

```sh
python3 -m growth serve --demo          # made-up data from the example project, on 127.0.0.1:8765
```

Then in the app's Settings choose "This Mac" with the token `demo`. A line at the top marks it as
demo data; changes there are thrown away. The demo is a local preview only: it listens on 127.0.0.1,
and the `demo` token is accepted only by a demo server: a server for a real home refuses it, even if
it was stored as `APP_TOKEN`.

To try every control for real on this Mac without the GEEKOM, run an engine against a scratch copy
of the example home (loopback only, its own secrets folder), and set "This Mac" with the token
command `cd ~/growth-engine && GROWTH_CONFIG_DIR=/tmp/scratch-config python3 -m growth app-token`:

```sh
cp -R examples/home /tmp/scratch-home
GROWTH_CONFIG_DIR=/tmp/scratch-config python3 -m growth --home /tmp/scratch-home serve --port 8766
```

## Light on memory

One SwiftUI process (about 40 to 60 MB) that polls every 15 seconds while the window is open and
every minute otherwise, plus one `ssh` process for the tunnel. On the GEEKOM the control API is one
small Python process capped at 256 MB by its unit.
