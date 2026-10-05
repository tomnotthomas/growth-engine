# Project config reference

A project is a folder `projects/<id>/` in the private engine home. `examples/home/projects/example`
is a complete, working example; copy it to start a project.

```
projects/<id>/
  project.toml       brand, site, rules, channels, data sources, collections, waitlist, goal, jobs
  ui.toml            interface copy per language (every key in the example is required)
  keywords.toml      keyword map: one cluster per page, checked against titles and H1s
  pages/*.toml       one page spec per file
  data/*.toml        curated collection facts, measurements
  media.toml         every image or video a page may show, with its source
  media/             those files
  theme/css/*.css    tokens over the engine's base theme; theme/fonts/ for self-hosted fonts
```

## project.toml

| Table | Keys |
|---|---|
| top level | `id` (= folder name), `name`, `enabled`, `timezone`, `languages`, `default_language` |
| `[brand]` | `name` (used for `{brand}` everywhere), `wordmark`, `app_url` |
| `[site]` | `base_url` (https, no trailing slash), `domain_decided`, `indexable`, `live_languages` (the languages built and linked now; must include `default_language`; default all), `legal_notice` and `privacy` (a page id or a path), `footer_links` (page ids listed in the footer), `home`, `host_home`, `pages`, `ui`, `keywords`, `measurements`, `media`, `media_dir`, `share_image` (one path, or one per language), `theme`, `theme_css`, `theme_dirs`, `preload_fonts`, `theme_color`, `forbid_chars`, `out`, `base_theme` |
| `[legal]` | operator details the legal pages use as `{legal.<key>}`, for example `name`, `street`, `postcode_city`, `country`, `email`. A project with a waitlist refuses to build while a key its legal pages use is empty. |
| `[hardware_check]` | `floor` (per language), `models_prefix`, `models`, `not_yet`, `not_host`, `family`: fills `{hardware.floor}` and `{hardware.models}` and drives the `hardware-check` section, which reads the graphics card in the browser and sends nothing |
| `[rules]` | `footage_publishers`: whose game footage own posts and pages may show (own recordings only) |
| `[channels.<id>]` | `enabled`, `accounts` (at most one), `approval = { granted, evidence }` for needs-approval channels |
| `[data.<id>]` | `kind` (`http-json` or `file-json`), `url` or `path`, `id_field`, `keep_fields`, `list_key`, `timeout`, `retries`, `max_age`, `allow_empty` |
| `[collections.<id>]` | `curated`, `source`, `hub`, `match_live`, `match_curated`, `require_live`, `trust_live_list`, `min_searches`, `max_wave` |
| `[waitlist]` | `status_page`, `host_status_page`, `worker_name`, `database_name`, `d1_database_id`, `email_provider` (`brevo`, `resend` or `log`), `email_from`, `move_up_per_referral`, `max_credited_referrals`, `moved_up_mail`, `counter_min`, `signups_per_ip_hour` (default 40), `stats_token_env` |
| `[goal]` | `name`, `target`, `days`, `start` (the launch date), `zone` (country codes that count toward the goal; empty counts all) |
| `[analytics]` | `provider` (`none` or `posthog`), `capture_host` and `project_api_key` (the public write key; events go through the Worker's `/api/e` relay, cookieless, no IP and no person profile), `host`, `project_id` and `api_key_env` (the personal read key, from the environment, for the digest) |
| `[directories]` | `source` (URL or a file in the project), `audience_terms`, `regions`, `exclude_terms`, `fact_sheet`, `voice`, `draft_batch`, `submit` (verified sites), `launch_date` (default: `[goal] start`) |
| `[[site]]` in the `submit` file | `id` (catalogue id: host without `www.` plus path), `method` (`api` or `form`), `endpoint` (https, on the same site), `fields`, `captcha = false`, `account = false`, `terms_url`, `terms_checked` (YYYY-MM-DD), `terms_allow_automation` (default false; only the boolean `true` allows automatic submission) |
| `[jobs.<id>]` | `kind`, `schedule`, `enabled`, `catchup` (`latest`, `all` or `skip`), `max_late`, `max_attempts`, plus kind parameters (`key` for indexnow; the build publishes it as `/<key>.txt`) |

## Curated collection items

| Field | Meaning |
|---|---|
| `slug`, `name`, `short` | URL part (lower-case letters and digits joined by single dashes), display name, short name for headings |
| `status` | `playable`, `blocked` (needs `reason`), `native`, `unchecked` |
| `native_version` | `none` to be eligible for a page; anything else means a native version exists |
| `searches`, `wave`, `page` | demand, page wave, `page = false` to never generate one |
| `free`, `signin`, `publisher` | facts for lists and fact boxes |
| `facts` | `[{ label, value, tone = "ok" \| "no" }]` for the fact box |
| `verdict`, `description`, `why`, `how`, `faq`, `similar` | per-item copy that templates pull in with `"@field"` |
| `measurement` | id of a measurement with source and date (and optionally a clip) |

## Renaming a product

Change `[brand] name` and `wordmark` (and `app_url` or `base_url` if they change), then rebuild. Page
copy uses `{brand}`, and validation refuses the literal name. Images that show the name, such as a
share card, have to be re-rendered.

## Legal pages and languages

The legal notice and privacy pages are ordinary page specs with `kind = "legal"`, one block per
language, filled from `[legal]`. Keep the operator's name and address in the private home only. Every
page links them in its footer in its own language.

`languages` lists every language the project has copy for; `live_languages` lists the ones built
now. A language that is ready but not live gets no pages, no sitemap entries and no hreflang links,
so a later market wave is switched on by adding it to `live_languages` and rebuilding.
