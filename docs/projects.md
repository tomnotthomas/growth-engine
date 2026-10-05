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
| `[site]` | `base_url` (https, no trailing slash), `domain_decided`, `indexable`, `legal_notice`, `privacy`, `home`, `host_home`, `pages`, `ui`, `keywords`, `measurements`, `media`, `media_dir`, `share_image`, `theme`, `theme_css`, `theme_dirs`, `preload_fonts`, `theme_color`, `forbid_chars`, `out`, `base_theme` |
| `[rules]` | `footage_publishers`: whose game footage own posts and pages may show (own recordings only) |
| `[channels.<id>]` | `enabled`, `accounts` (at most one), `approval = { granted, evidence }` for needs-approval channels |
| `[data.<id>]` | `kind` (`http-json` or `file-json`), `url` or `path`, `id_field`, `keep_fields`, `list_key`, `timeout`, `retries`, `max_age`, `allow_empty` |
| `[collections.<id>]` | `curated`, `source`, `hub`, `match_live`, `match_curated`, `require_live`, `trust_live_list`, `min_searches`, `max_wave` |
| `[waitlist]` | `status_page`, `worker_name`, `database_name`, `d1_database_id`, `email_provider` (`brevo`, `resend` or `log`), `email_from`, `move_up_per_referral`, `max_credited_referrals`, `moved_up_mail`, `counter_min`, `signups_per_ip_hour`, `stats_token_env` |
| `[goal]` | `name`, `target`, `days`, `start` (the launch date) |
| `[analytics]` | `provider` (`none` or `posthog`), `host`, `project_id`, `api_key_env`, `focus_os` |
| `[directories]` | `source` (URL or a file in the project), `audience_terms`, `regions`, `exclude_terms`, `fact_sheet`, `voice`, `draft_batch`, `submit` (verified sites), `launch_date` (default: `[goal] start`) |
| `[jobs.<id>]` | `kind`, `schedule`, `enabled`, `catchup` (`latest`, `all` or `skip`), `max_late`, `max_attempts`, plus kind parameters (`key` for indexnow) |

## Curated collection items

| Field | Meaning |
|---|---|
| `slug`, `name`, `short` | URL part, display name, short name for headings |
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
