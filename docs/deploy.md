# Deploying a project's site and waitlist

Nothing is public before the owner's launch go. Until then a deploy only builds the site and checks
it on the engine's own machine (`wrangler dev` with a throwaway local D1, on 127.0.0.1); nothing is
uploaded to Cloudflare, there are no preview URLs, and the production D1 is not touched. Until a
project sets `[site] indexable = true` (which needs the decided domain and the legal-notice and privacy
pages) every page also carries `noindex` and `robots.txt` disallows everything.

## The pipeline

```
push to a PR ──► CI: lint · unit tests (3.11, 3.12) · example build + link check · never-automate
                     · Mac app build · gitleaks ──► ci-ok (the required check) ──► merge to main
main on GitHub ──► GEEKOM growth-update timer (every 15 min): newest main commit with all checks green
                     and a GitHub-verified signature ──► fast-forward under the engine lock ──►
                     self-check (growth check + unit tests) ──► restart, or roll back and skip it
built site changed (content or engine) ──► deploy job (every 15 min, per project):
                     build ──► link check ──► local check (wrangler dev + local D1 on 127.0.0.1)
                     ──► [deploy] launched = true? ──► D1 + schema ──► wrangler versions upload
                     ──► promote to 100% ──► smoke checks on the domain ──► on failure: back to the
                     last good version (a rolled-back build is not promoted again; the next one is)
```

Every step of a deploy goes through the same guards as any outward action: the kill switch, the
never-automate checks, the `website` channel's rate limit and pause switch, and the audit log. Both
the updates and the deploys appear in the weekly digest and in the Mac app.

## Turning it on for a project

In the project's `project.toml` (in the private home):

```toml
[deploy]
launched = false          # the launch go; flip it in the Mac app (Project settings, Launch)

[jobs.deploy]
kind = "deploy"
schedule = "every 15m"    # deploys only when the built output changed
```

By hand: `growth deploy <project>` (or `deploy/cloudflare.sh <project>`), same rules.

## One-time setup (the owner)

1. A free Cloudflare account.
2. An **API token** (My Profile, API Tokens, Create Token, Custom token) with only:
   - Account, **Workers Scripts**, Edit
   - Account, **D1**, Edit
   - Account resources: include only your account. No zone or user permissions.
3. On the GEEKOM, into the encrypted store (never a file in the home or the repo):
   ```sh
   cd ~/growth-engine
   python3 -m growth secret set CLOUDFLARE_API_TOKEN
   python3 -m growth secret set CLOUDFLARE_ACCOUNT_ID   # from the dashboard's account home
   ```
4. Before the launch go: the domain on Cloudflare attached to the Worker (Workers, your Worker,
   Domains), and the Worker secrets, set once from `dist/<out>/` with `npx wrangler secret put <NAME>`:
   - `EMAIL_API_KEY`: the mail provider's key (Brevo or Resend; the sender domain verified there),
   - `STATS_TOKEN`: a long random string; also `growth secret set <PROJECT>_STATS_TOKEN` (the name in
     `[waitlist] stats_token_env`) so the dashboard and digest can read the numbers,
   - `HASH_SALT`: a long random string for hashing IPs in the rate limit.
5. In the project: `[waitlist] email_provider` set to `brevo` or `resend` (the default `log` only
   prints mails and blocks production), `email_from` on the verified domain, `[goal] start`.

After the launch go, the deploy job creates the D1 database on its first run and keeps its id in
`state/projects/<id>/deploy.json`. The generated `wrangler.toml` sets `preview_urls = false`, so an
uploaded version is never reachable on its own URL; the check before promoting is the local one, and
the live smoke checks only read (`/api/waitlist/count`). A build checked before the launch goes live on
the first deploy run after the switch is turned on.

Until `EMAIL_API_KEY` exists, the Worker answers sign-ups with "opens soon" and stores nothing.

### Free-tier limits that matter

| Service | Free limit | At 20,000 sign-ups in 46 days |
|---|---|---|
| Workers | 100,000 requests a day | fine |
| D1 | 5 GB, 5 million reads and 100,000 writes a day | fine |
| Static assets | free, unlimited | fine |
| Brevo | 300 mails a day | **not enough**: about 435 sign-ups a day, each needing a confirmation and a welcome mail (~900 mails a day) |
| Resend | 100 mails a day, 3,000 a month | **not enough** |

So the double opt-in needs either a paid mail plan at launch or fewer mails per sign-up. Setting
`moved_up_mail = false` saves the referral notifications, but the confirmation and welcome mails are
the minimum. That is the owner's call; nothing is bought by the engine.

## Why Workers static assets and not Pages

The site and the waitlist ship as one Worker with static assets: the forms post to `/api/*` on the
same origin, one upload versions both together, and Cloudflare's version API gives the upload,
promote and rollback steps directly. It is on the same free tier as Pages. A site without
`[waitlist]` is plain static output in `dist/<out>/public/` and can also go to Pages or GitHub Pages.
