# Channels and how far each may be automated

Every job either runs fully on its own or does not run at all. The one exception is Reddit, where the
engine may only queue drafts that the owner reads, edits and posts from their own account. This table
mirrors `growth/channels.py`; a test fails if they drift apart. A project can switch a channel on only
within its level: `off` channels refuse to load, and `needs-approval` channels load only with the
platform's approval recorded in the project config:

```toml
[channels.tiktok]
enabled = true
approval = { granted = "2026-11-02", evidence = "TikTok audit approval email, app id ..." }
```

| Channel | Level | What it covers | Why |
|---|---|---|---|
| `website` | auto | Static search pages (generated site) | Own pages on own hosting. |
| `indexnow` | auto | IndexNow pings (Bing, Yandex and others) | Free, keyed to the own domain. Switch on once the domain is public. |
| `analytics` | auto | Reading own analytics (PostHog) for the digest | Read-only access to the project's own data. |
| `digest` | auto | Weekly digest to the owner | A report to the owner; nothing is published. |
| `reddit` | human-queue | Reddit replies and posts | Reddit bans automated posting, commenting and voting, and new API access needs approval since 11 Nov 2025. Drafts only; the owner posts. |
| `forums` | off | German forums (ComputerBase, Hardwareluxx, PCGH, macuser.de) | Forum rules punish promotional posts, automated or not, and nobody posts by hand. |
| `comment-replies` | off | Replies to TikTok and Instagram comments | No reply API for small accounts. |
| `tiktok` | needs-approval | TikTok posting | Unaudited Content Posting API clients can only post privately; public posting needs TikTok's audit, and inbox drafts need an approved `video.upload` scope. |
| `youtube` | needs-approval | YouTube Shorts uploads | Uploads from an unverified API project are forced to private until Google's API audit passes. |
| `instagram` | needs-approval | Instagram Reels publishing (Graph API) | Allowed for an own business account through an own Meta app; record both once they exist. |
| `outreach-email` | off | Press, creator and publisher emails | UWG § 7 needs consent for advertising email, B2B included; individual pitches need a person to send them, and the owner opted out of manual steps. |
| `waitlist-email` | needs-approval | Waitlist, referral and milestone emails (double opt-in only) | Needs the own domain, a sender (Brevo or Resend) and the consent log first. |
| `directory-submit` | auto | Launch directories with an API or a plain form | Only sites the project verified: terms allow automated submission, no captcha, no account. Never before launch day, once per site. |
| `directories` | off | Directory listings that need an account, a captcha or a person, and the Steam curator page | Captchas and account creation are never automated; these are skipped and reported in the digest. |
| `google-indexing-api` | off | Google Indexing API | Only for job postings and livestreams; using it for normal pages risks losing access. |
| `paid-ads` | off | Paid ads and boosted posts | Costs money; boosting game footage is never allowed. |

## The never-automate list

These are hard checks in `growth/policy.py`. Every outward action passes them first; a violation is
refused, logged as blocked and reported in the weekly digest.

1. Posting, commenting or voting on Reddit or forums from scripts.
2. More than one account per platform.
3. Buying followers, views, likes or reviews.
4. Engagement pods.
5. The same text in several communities (texts are fingerprinted per community).
6. DMs nobody asked for.
7. Emails to lists the recipient didn't join (every recipient needs a recorded opt-in).
8. Game footage in own posts or pages from publishers the project's rules don't allow, or that isn't
   an own recording (a project lists the allowed publishers in `[rules] footage_publishers`).
9. Boosting any game footage with paid ads.
10. Google's Indexing API for normal pages.
11. Fake activity or invented numbers (UWG § 5): every measured number needs a source and a date,
    structured data never claims ratings or reviews, and the digest drops AI text that contains a
    number the data doesn't.
12. Solving captchas or creating accounts automatically.
13. Submitting anywhere whose terms were not checked to allow it (each verified site records
    `terms_url` and `terms_checked`).

## Launch directories

`directories-sync` imports a community list (default: mmccaff/PlacesToPostYourStartup, CC0) into the
project's catalogue and scores every entry for the project's audience. `directories-draft` writes each
site's listing text from the project's fact sheet. `directories-submit` waits for launch day. Then it
submits once to each site listed in the project's verified-submit file (an API or a plain form whose
terms allow it, recorded with `captcha = false` and `account = false`), and marks every other drafted
entry as skipped. Subreddits from the list stay in the owner's Reddit queue. The digest lists what was
skipped.
