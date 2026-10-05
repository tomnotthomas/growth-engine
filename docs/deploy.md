# Deploying a project's site and waitlist

Nothing is public until someone runs the deploy command, and that command asks for the project id as
confirmation. Until a project sets `[site] indexable = true` (which needs the decided domain and the
legal-notice and privacy pages), every page carries `noindex` and `robots.txt` disallows everything,
even when deployed.

## One command (Cloudflare, free tier)

```sh
GROWTH_HOME=~/growth-home ~/growth-engine/deploy/cloudflare.sh <project>
```

It builds the site, creates the D1 database on the first run (and records its id in the project's
`[waitlist] d1_database_id`), applies `schema.sql`, checks the Worker secrets, and runs
`wrangler deploy` from `dist/<out>/`. One-time prerequisites, done by the owner:

1. A Cloudflare account and `npx wrangler login` on the deploying machine.
2. The domain on Cloudflare (or a CNAME), attached to the Worker under Workers, Domains.
3. Worker secrets, set from `dist/<out>/` with `npx wrangler secret put <NAME>`:
   - `EMAIL_API_KEY`: the mail provider's API key (Brevo or Resend; the sender domain must be verified there),
   - `STATS_TOKEN`: a long random string; also put it in the engine's env file under the project's `stats_token_env`,
   - `HASH_SALT`: a long random string for hashing IPs in the rate limit.
4. In the project: `[waitlist] email_provider` set to `brevo` or `resend` (the default `log` only
   prints mails, for local development, and the deploy command refuses it), `email_from` with an
   address on the verified domain, and `[goal] start` set to the launch date.

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

## Static only (GitHub Pages or Cloudflare Pages)

`dist/<out>/public/` is a plain static site and can be served by GitHub Pages or Cloudflare Pages as
it is. Without the Worker the waitlist forms have nowhere to post, so this suits only sites without
`[waitlist]`.
