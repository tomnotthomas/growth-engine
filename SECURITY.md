# Security

The growth engine acts on its own: it builds and deploys sites, submits to directories and reads
analytics. This page says what it protects, from whom, and how. Report a vulnerability privately
through GitHub's "Report a vulnerability" on this repository, not in an issue.

## Shape

- **The engine** (scheduler, AI jobs under the Claude subscription, private project homes, secrets)
  runs on one always-on PC, the GEEKOM, in Ubuntu on WSL. It opens **no inbound port**. Its control
  API listens on `127.0.0.1:8765` only.
- **The Mac app** is the only way to see and steer it. It reaches the control API through an SSH
  tunnel (`ssh -L … geekom-wsl`) with the captain's existing SSH key, then logs in with a token it
  fetches over that same SSH login. Nothing operational is ever served on the web.
- **The public web** carries only what visitors need: the marketing site and the waitlist Worker on
  Cloudflare. They hold no engine access and no engine secrets.

## What we protect, and against what

| Asset | Threat | Guard |
|---|---|---|
| Accounts and reputation (Reddit, directories, mail) | the engine posting where it must not, or too much | the never-automate checks before every outward action (`policy.py`, CI job `never-automate`); channel levels; per-channel rate limits; channel and project pause; the kill switch |
| Secrets (Cloudflare token, stats token, PostHog key, webhook) | leaking from disk, backups, the repo, logs | encrypted store `~/.config/growth-engine/secrets.enc` (AES-256-CTR plus HMAC-SHA256; on WSL the key is wrapped with Windows DPAPI); never in the repo (gitleaks in CI), the private home, the env file, logs or the audit log |
| Cloudflare account | a stolen token | least privilege: an account API token with **Workers Scripts: Edit** and **D1: Edit** only, on the one account; no zone, DNS or billing rights |
| The engine host | remote access | no inbound ports; the control API binds to loopback, needs a token, refuses browsers (Origin header) and non-loopback Host headers (DNS rebinding) |
| The engine's code | a bad or malicious change reaching the always-on PC | updates only fast-forward `main` to commits whose GitHub checks all passed **and** whose signature GitHub verified; a self-check (config + unit tests) with the new code; automatic rollback; the kill switch stops updates |
| The public site | a broken or unchecked deploy going live | before the owner's launch go (`[deploy] launched = true`, false by default) nothing is uploaded: each build is only smoke-checked locally with `wrangler dev` and a local D1 on 127.0.0.1, and the Worker has no preview URLs; after it, production rolls back to the last good version if the live checks fail |
| Data the engine reads | exfiltration through outbound calls | outbound HTTP only to an allow-list derived from the config plus `[guard] outbound_allow`; Google's Indexing API is refused outright |
| Accountability | not knowing what the engine did, or someone rewriting history | append-only, hash-chained audit log of every outward action, block, control action, update, deploy and kill-switch change; `growth audit --verify` and the Mac app show whether the chain is intact |

## Honest limits

- The `file` key backend (plain Linux, macOS) keeps the master key next to the store with mode 0600.
  It keeps secrets out of the repository, the home, backups of either and the environment, but
  anyone who can read the user's files can read them. On WSL the default `dpapi` backend ties the
  key to the Windows user instead.
- The allow-list covers the engine's own HTTP requests. `wrangler` (Cloudflare) and `claude` (the
  AI jobs) are separate programs that make their own connections to Cloudflare and Anthropic.
- The audit log is tamper-evident, not tamper-proof: someone with the user's shell can delete the
  whole file. A missing or broken chain shows up in the app and the weekly digest.
- Whoever holds the Mac's SSH key to the GEEKOM controls the engine. Protect that key (FileVault,
  a passphrase or the macOS keychain agent).
- The waitlist Worker is public by design; its abuse limits are in docs/architecture.md.

## Operating it safely

- **Stop everything:** the kill switch in the Mac app (toolbar or menu bar), or `growth kill on
  --reason "…"` on the GEEKOM. Running work finishes its current step; nothing new starts.
- **Rotate a secret:** create the new value at the provider, `growth secret set NAME`, revoke the
  old one.
- **Check the record:** `growth audit` (newest entries) and `growth audit --verify`.
