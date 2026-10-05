// Waitlist API for Cloudflare Workers + D1: double opt-in, personal invite links, move-up rewards,
// a live counter, token-protected stats for the weekly digest, and a cookieless analytics relay.
// Project-neutral: every name, path and text comes from the generated config.js.
//
//   POST /api/waitlist                sign up (form post or JSON); always the same answer
//   GET  /api/waitlist/confirm?t=     a page with one button; POST confirms (mail scanners only GET)
//   POST /api/waitlist/confirm        confirm, credit the referrer, send the welcome mail
//   GET  /api/waitlist/status?s=      position, referrals, invite link (private token)
//   GET  /api/waitlist/leave?...      a page with one button; POST deletes the entry
//   GET  /api/waitlist/count          confirmed sign-ups per role (cached for a minute)
//   GET  /api/waitlist/stats          daily numbers, sources, referral cohorts (Bearer STATS_TOKEN)
//   POST /api/e                       analytics events from the pages, relayed without IP or cookies
//   GET  /r/<code>                    invite link: redirects to the inviter's page with ?ref=

const EMAIL = /^[^\s@]{1,64}@[^\s@]{1,255}\.[a-z]{2,}$/i;
const CODE = /^[a-z0-9]{6,12}$/;
const TOKEN = /^[A-Za-z0-9_-]{20,64}$/;
const ANON = /^[A-Za-z0-9-]{8,64}$/;
const SEARCH = /(^|\.)(google|bing|duckduckgo|ecosia|yahoo|qwant|startpage|brave)\./;
const RESEND_AFTER_MS = 10 * 60e3; // a fresh mail at most every 10 minutes per address ...
const MAILS_PER_DAY = 3; // ... and at most 3 a day
const MOVED_UP_EVERY_MS = 24 * 3600e3;

// Events the pages may send, and the properties each may carry. Nothing else is relayed.
const CLIENT_EVENTS = new Set(["$pageview", "waitlist_form_view", "waitlist_submit", "referral_sent", "game_search"]);
const CLIENT_PROPS = new Set(["path", "lang", "role", "page", "channel", "utm_source", "utm_medium", "utm_campaign", "method", "found", "referred"]);

// Every relayed event names its site, so a PostHog project shared with an app keeps them apart.
const SITE = { id: "" };

export function makeHandler(config, deps = {}) {
  SITE.id = String(config.site || "");
  const send = deps.sendMail || ((env, mail) => sendMail(config, env, mail));
  const now = deps.now || (() => new Date());
  const relay = deps.capture || ((env, events) => captureEvents(config, env, events));

  return async function handle(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    const later = (promise) => (ctx && ctx.waitUntil ? ctx.waitUntil(promise) : promise);
    try {
      if (path.startsWith("/r/")) return invite(path.slice(3), env);
      if (path === "/api/waitlist" && request.method === "POST") return await signup(request, env, later);
      if (path === "/api/waitlist/confirm") return request.method === "POST" ? await confirm(request, env, later) : buttonPage("confirm", url, ["t"]);
      if (path === "/api/waitlist/leave") return request.method === "POST" ? await leave(request, env) : buttonPage("leave", url, ["s", "c", "l"]);
      if (path === "/api/waitlist/status" && request.method === "GET") return await status(url, env);
      if (path === "/api/waitlist/count" && request.method === "GET") return await count(env);
      if (path === "/api/waitlist/stats" && request.method === "GET") return await stats(request, env);
      if (path === "/api/e" && request.method === "POST") return await clientEvents(request, env, later);
      if (env.ASSETS) return env.ASSETS.fetch(request);
      return json({ error: "not found" }, 404);
    } catch (err) {
      console.error("waitlist error", err && err.stack ? err.stack : err);
      return json({ error: "server error" }, 500);
    }
  };

  // ---- sign up ---------------------------------------------------------------------------------

  async function signup(request, env, later) {
    const wantsJson = (request.headers.get("content-type") || "").includes("application/json");
    let body;
    try {
      body = wantsJson ? await request.json() : Object.fromEntries(await request.formData());
    } catch {
      return reply(wantsJson, 400, { error: "bad request" }, statusUrl({ e: "invalid" }, langOf(request)));
    }
    const lang = config.languages.includes(body.lang) ? body.lang : config.defaultLanguage;
    const role = body.role === "host" ? "host" : "player";
    const email = String(body.email || "").trim().toLowerCase();
    if (body.website) return reply(wantsJson, 202, { ok: true }, statusUrl({ sent: 1 }, lang, "", role)); // honeypot
    if (!EMAIL.test(email) || email.length > 254) return reply(wantsJson, 400, { error: "invalid email" }, statusUrl({ e: "invalid" }, lang, "", role));
    if (!mailReady(env)) return reply(wantsJson, 503, { error: "not open yet" }, statusUrl({ e: "closed" }, lang, "", role));
    if (!(await allowIp(request, env))) return reply(wantsJson, 429, { error: "too many sign-ups" }, statusUrl({ e: "busy" }, lang, "", role));

    const stamp = now().toISOString();
    const existing = await env.DB.prepare("SELECT * FROM signups WHERE email = ?").bind(email).first();
    if (existing) {
      // Same answer as a new sign-up, so the form never reveals who is on the list.
      if (mayMail(existing)) {
        if (existing.confirmed_at) {
          await sendStatusAgain(env, existing);
        } else {
          const t = token();
          await env.DB.prepare("UPDATE signups SET confirm_hash = ? WHERE id = ?").bind(await hash(t), existing.id).run();
          await mail(env, existing, "confirm", { confirm_url: confirmUrl(t, existing.lang) });
        }
        await mailed(env, existing);
      }
      return reply(wantsJson, 202, { ok: true }, statusUrl({ sent: 1 }, lang, "", role));
    }

    let referrer = null;
    const ref = String(body.ref || "").toLowerCase();
    if (CODE.test(ref)) referrer = await env.DB.prepare("SELECT id FROM signups WHERE code = ? AND confirmed_at IS NOT NULL").bind(ref).first();
    const source = referrer ? "referral" : classify(String(body.src || ""), String(body.from || ""));
    const country = countryOf(request);
    const t = token();
    const row = {
      email,
      role,
      lang,
      code: await freeCode(env),
      source,
    };
    const inserted = await env.DB.prepare(
      "INSERT INTO signups (email, role, lang, code, confirm_hash, referred_by, source, page, country, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(email) DO NOTHING",
    )
      .bind(email, role, lang, row.code, await hash(t), referrer ? referrer.id : null, source, String(body.page || "").slice(0, 60), country, stamp)
      .run();
    // The same address sent twice at once: the other request saved it and mails it; this one answers the same.
    if (!inserted.meta.changes) return reply(wantsJson, 202, { ok: true }, statusUrl({ sent: 1 }, lang, "", role));
    const saved = await env.DB.prepare("SELECT * FROM signups WHERE email = ?").bind(email).first();
    await mail(env, saved, "confirm", { confirm_url: confirmUrl(t, lang) });
    await mailed(env, saved);
    later(relay(env, [event("waitlist_signup", token(), { role, lang, channel: source, page: saved.page, referred: Boolean(referrer), ...utm(body) }, request)]));
    return reply(wantsJson, 202, { ok: true }, statusUrl({ sent: 1 }, lang, "", role));
  }

  async function confirm(request, env, later) {
    const wantsJson = (request.headers.get("content-type") || "").includes("application/json");
    const body = wantsJson ? await request.json().catch(() => ({})) : Object.fromEntries(await request.formData());
    const t = String(body.t || "");
    const lang = config.languages.includes(body.lang) ? body.lang : config.defaultLanguage;
    const row = TOKEN.test(t) ? await env.DB.prepare("SELECT * FROM signups WHERE confirm_hash = ?").bind(await hash(t)).first() : null;
    if (!row) return reply(wantsJson, 404, { error: "unknown link" }, statusUrl({ e: "expired" }, lang));
    if (row.confirmed_at) {
      // A confirm link used twice: the person is already on the list. Mail their status link again.
      if (mayMail(row)) {
        await sendStatusAgain(env, row);
        await mailed(env, row);
      }
      return reply(wantsJson, 200, { ok: true, already: true }, statusUrl({ e: "already" }, row.lang, "", row.role));
    }
    const s = token();
    const stamp = now().toISOString();
    const done = await env.DB.prepare("UPDATE signups SET confirmed_at = ?, status_hash = ? WHERE id = ? AND confirmed_at IS NULL")
      .bind(stamp, await hash(s), row.id)
      .run();
    if (!done.meta.changes) return reply(wantsJson, 200, { ok: true, already: true }, statusUrl({ e: "already" }, row.lang, "", row.role));
    const events = [event("waitlist_confirmed", token(), { role: row.role, lang: row.lang, channel: row.source, referred: Boolean(row.referred_by) }, request)];
    if (row.referred_by) {
      await creditReferrer(env, row.referred_by);
      events.push(event("referral_joined", token(), { role: row.role, lang: row.lang, channel: "referral" }, request));
    }
    const position = await positionOf(env, row.id, row.role);
    await mail(env, row, "welcome", {
      status_url: absolute(statusUrl({}, row.lang, s, row.role)),
      invite_url: inviteUrl(row.code),
      position,
      move_up: config.moveUpPerReferral,
      leave_url: await leaveUrl(env, row),
    });
    later(relay(env, events));
    return reply(wantsJson, 200, { ok: true, s }, statusUrl({ new: 1 }, row.lang, s, row.role));
  }

  async function creditReferrer(env, id) {
    const ref = await env.DB.prepare("SELECT * FROM signups WHERE id = ?").bind(id).first();
    if (!ref || !ref.confirmed_at) return;
    if (ref.referrals >= config.maxCreditedReferrals) return;
    const before = await positionOf(env, ref.id, ref.role);
    await env.DB.prepare("UPDATE signups SET referrals = referrals + 1 WHERE id = ?").bind(id).run();
    const after = await positionOf(env, ref.id, ref.role);
    // Only tell them when they really moved, and at most once a day.
    if (config.movedUpMail && after < before && !recently(ref.moved_up_mail_at, MOVED_UP_EVERY_MS)) {
      // No status link here: only the owner's own mails carry their private token.
      await mail(env, ref, "movedup", {
        position: after,
        invite_url: inviteUrl(ref.code),
        move_up: config.moveUpPerReferral,
        leave_url: await leaveUrl(env, ref),
      });
      await env.DB.prepare("UPDATE signups SET moved_up_mail_at = ? WHERE id = ?").bind(now().toISOString(), ref.id).run();
    }
  }

  async function sendStatusAgain(env, row) {
    const s = token();
    await env.DB.prepare("UPDATE signups SET status_hash = ? WHERE id = ?").bind(await hash(s), row.id).run();
    await mail(env, row, "again", { status_url: absolute(statusUrl({}, row.lang, s, row.role)), leave_url: await leaveUrl(env, row) });
  }

  // ---- reading -----------------------------------------------------------------------------------

  async function status(url, env) {
    const s = url.searchParams.get("s") || "";
    const row = TOKEN.test(s) ? await env.DB.prepare("SELECT * FROM signups WHERE status_hash = ?").bind(await hash(s)).first() : null;
    if (!row) return json({ error: "unknown link" }, 404);
    return json(
      {
        role: row.role,
        position: await positionOf(env, row.id, row.role),
        referrals: row.referrals,
        move_up: config.moveUpPerReferral,
        invite_url: inviteUrl(row.code),
      },
      200,
      { "cache-control": "no-store" },
    );
  }

  async function count(env) {
    const rows = await env.DB.prepare("SELECT role, COUNT(*) AS n FROM signups WHERE confirmed_at IS NOT NULL GROUP BY role").all();
    const out = { player: 0, host: 0, min: config.counterMin };
    for (const r of rows.results || []) out[r.role] = r.n;
    return json(out, 200, { "cache-control": "public, max-age=60" });
  }

  async function stats(request, env) {
    const auth = request.headers.get("authorization") || "";
    if (!env.STATS_TOKEN || !(await sameSecret(auth, `Bearer ${env.STATS_TOKEN}`))) return json({ error: "unauthorized" }, 401);
    const days = await env.DB.prepare(
      "SELECT substr(confirmed_at, 1, 10) AS date, role, country, COUNT(*) AS confirmed, SUM(CASE WHEN referred_by IS NOT NULL THEN 1 ELSE 0 END) AS referred " +
        "FROM signups WHERE confirmed_at IS NOT NULL GROUP BY date, role, country ORDER BY date",
    ).all();
    const created = await env.DB.prepare("SELECT substr(created_at, 1, 10) AS date, COUNT(*) AS signups FROM signups GROUP BY date ORDER BY date").all();
    const sources = await env.DB.prepare(
      "SELECT source, role, country, COUNT(*) AS confirmed FROM signups WHERE confirmed_at IS NOT NULL GROUP BY source, role, country ORDER BY confirmed DESC",
    ).all();
    // Referral cohorts: users confirmed in a week, and how many confirmed sign-ups they brought in.
    const cohorts = await env.DB.prepare(
      "SELECT date(s.confirmed_at, '-6 days', 'weekday 1') AS week, COUNT(*) AS size, " +
        "SUM((SELECT COUNT(*) FROM signups f WHERE f.referred_by = s.id AND f.confirmed_at IS NOT NULL)) AS invited " +
        "FROM signups s WHERE s.confirmed_at IS NOT NULL GROUP BY week ORDER BY week",
    ).all();
    const countries = await env.DB.prepare(
      "SELECT country, role, COUNT(*) AS confirmed FROM signups WHERE confirmed_at IS NOT NULL GROUP BY country, role ORDER BY confirmed DESC",
    ).all();
    const totals = await env.DB.prepare(
      "SELECT SUM(CASE WHEN confirmed_at IS NOT NULL THEN 1 ELSE 0 END) AS confirmed, SUM(CASE WHEN confirmed_at IS NULL THEN 1 ELSE 0 END) AS pending FROM signups",
    ).first();
    return json(
      {
        generated_at: now().toISOString(),
        totals: { confirmed: totals.confirmed || 0, pending: totals.pending || 0 },
        days: days.results || [],
        created: created.results || [],
        sources: sources.results || [],
        countries: countries.results || [],
        cohorts: cohorts.results || [],
      },
      200,
      { "cache-control": "no-store" },
    );
  }

  async function leave(request, env) {
    const body = Object.fromEntries(await request.formData());
    const lang = config.languages.includes(body.lang) ? body.lang : config.defaultLanguage;
    let row = null;
    const s = String(body.s || "");
    if (TOKEN.test(s)) row = await env.DB.prepare("SELECT id, role, referred_by FROM signups WHERE status_hash = ?").bind(await hash(s)).first();
    const code = String(body.c || "");
    if (!row && CODE.test(code)) {
      const candidate = await env.DB.prepare("SELECT id, role, code, referred_by FROM signups WHERE code = ?").bind(code).first();
      if (candidate && (await sameSecret(String(body.l || ""), await leaveToken(env, candidate)))) row = candidate;
    }
    if (row) {
      await env.DB.prepare("DELETE FROM signups WHERE id = ?").bind(row.id).run();
      if (row.referred_by) {
        await env.DB.prepare(
          "UPDATE signups SET referrals = MIN(?, (SELECT COUNT(*) FROM signups f WHERE f.referred_by = ? AND f.confirmed_at IS NOT NULL)) WHERE id = ?",
        )
          .bind(config.maxCreditedReferrals, row.referred_by, row.referred_by)
          .run();
      }
    }
    return Response.redirect(absolute(statusUrl({ left: 1 }, lang, "", row ? row.role : "player")), 303);
  }

  async function invite(code, env) {
    code = code.toLowerCase();
    let target = config.paths.player;
    if (CODE.test(code)) {
      const row = await env.DB.prepare("SELECT role, lang FROM signups WHERE code = ? AND confirmed_at IS NOT NULL").bind(code).first();
      if (row) target = (config.rolePaths[row.role] || {})[row.lang] || config.paths[row.role] || target;
    }
    const to = new URL(absolute(target));
    if (CODE.test(code)) to.searchParams.set("ref", code);
    to.searchParams.set("utm_source", "referral");
    return Response.redirect(to.toString(), 302);
  }

  async function clientEvents(request, env, later) {
    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "bad request" }, 400);
    }
    const list = Array.isArray(body.events) ? body.events.slice(0, 20) : [];
    const anon = ANON.test(String(body.anon || "")) ? String(body.anon) : null;
    const events = [];
    for (const item of list) {
      if (!item || !CLIENT_EVENTS.has(item.event) || !anon) continue;
      const props = {};
      for (const [key, value] of Object.entries(item.props || {})) {
        if (CLIENT_PROPS.has(key) && ["string", "number", "boolean"].includes(typeof value)) props[key] = typeof value === "string" ? value.slice(0, 120) : value;
      }
      events.push(event(item.event, anon, props, request));
    }
    if (events.length) later(relay(env, events));
    return new Response(null, { status: 204 });
  }

  // ---- helpers -----------------------------------------------------------------------------------

  function recently(stamp, ms) {
    return Boolean(stamp) && now().getTime() - Date.parse(stamp) < ms;
  }

  function mayMail(row) {
    const today = now().toISOString().slice(0, 10);
    const sentToday = row.mail_day === today ? row.mails_today || 0 : 0;
    return sentToday < MAILS_PER_DAY && !recently(row.mail_sent_at, RESEND_AFTER_MS);
  }

  async function mailed(env, row) {
    const stamp = now().toISOString();
    const today = stamp.slice(0, 10);
    await env.DB.prepare(
      "UPDATE signups SET mail_sent_at = ?, mails_today = CASE WHEN mail_day = ? THEN mails_today + 1 ELSE 1 END, mail_day = ? WHERE id = ?",
    )
      .bind(stamp, today, today, row.id)
      .run();
  }

  async function positionOf(env, id, role) {
    const row = await env.DB.prepare(
      "SELECT COUNT(*) + 1 AS pos FROM signups o, (SELECT id, id - ? * referrals AS score FROM signups WHERE id = ?) me " +
        "WHERE o.role = ? AND o.confirmed_at IS NOT NULL AND o.id != me.id AND " +
        "((o.id - ? * o.referrals) < me.score OR ((o.id - ? * o.referrals) = me.score AND o.id < me.id))",
    )
      .bind(config.moveUpPerReferral, id, role, config.moveUpPerReferral, config.moveUpPerReferral)
      .first();
    return row ? row.pos : 1;
  }

  async function allowIp(request, env) {
    const ip = request.headers.get("cf-connecting-ip") || "unknown";
    const hour = now().toISOString().slice(0, 13);
    const ipHash = await hash(`${env.HASH_SALT || ""}:${ip}`);
    await env.DB.prepare("INSERT INTO ip_hits (ip_hash, hour, n) VALUES (?, ?, 1) ON CONFLICT(ip_hash, hour) DO UPDATE SET n = n + 1")
      .bind(ipHash, hour)
      .run();
    const row = await env.DB.prepare("SELECT n FROM ip_hits WHERE ip_hash = ? AND hour = ?").bind(ipHash, hour).first();
    if (Math.random() < 0.02) await env.DB.prepare("DELETE FROM ip_hits WHERE hour < ?").bind(hour).run();
    return row.n <= config.signupsPerIpHour;
  }

  async function freeCode(env) {
    for (let i = 0; i < 8; i++) {
      const code = randomCode(8);
      if (!(await env.DB.prepare("SELECT 1 FROM signups WHERE code = ?").bind(code).first())) return code;
    }
    throw new Error("could not find a free invite code");
  }

  async function mail(env, row, kind, values) {
    const strings = config.mail[row.lang] || config.mail[config.defaultLanguage];
    const key = row.role === "host" && strings[`host_${kind}_subject`] ? `host_${kind}` : kind;
    const fill = (text) => text.replace(/\{(\w+)\}/g, (m, k) => (k in values ? String(values[k]) : k === "brand" ? config.brand : m));
    const message = { to: row.email, subject: fill(strings[`${key}_subject`]), text: fill(strings[`${key}_body`]) };
    if (values.leave_url) message.unsubscribe = values.leave_url;
    await send(env, message);
  }

  function mailReady(env) {
    return config.email.provider === "log" || Boolean(env.EMAIL_API_KEY && config.email.from);
  }

  function langOf(request) {
    const lang = new URL(request.url).searchParams.get("lang");
    return config.languages.includes(lang) ? lang : config.defaultLanguage;
  }

  function statusUrl(params, lang, secret = "", role = "player") {
    const byRole = config.paths.status[role] || config.paths.status.player;
    const base = byRole[lang] || byRole[config.defaultLanguage];
    const query = new URLSearchParams(params).toString();
    return base + (query ? `?${query}` : "") + (secret ? `#s=${secret}` : "");
  }
  function confirmUrl(t, lang) {
    return absolute(`/api/waitlist/confirm?t=${t}&lang=${lang}`);
  }
  async function leaveToken(env, row) {
    return (await hash(`leave:${env.HASH_SALT || ""}:${row.id}:${row.code}`)).slice(0, 32);
  }
  async function leaveUrl(env, row) {
    return absolute(`/api/waitlist/leave?c=${row.code}&l=${await leaveToken(env, row)}&lang=${row.lang}`);
  }
  function inviteUrl(code) {
    return absolute(`/r/${code}`);
  }
  function absolute(path) {
    return path.startsWith("http") ? path : config.baseUrl + path;
  }

  function reply(wantsJson, code, data, redirectTo) {
    if (wantsJson) return json(data, code);
    return Response.redirect(absolute(redirectTo), 303);
  }

  function buttonPage(kind, url, params) {
    const lang = config.languages.includes(url.searchParams.get("lang")) ? url.searchParams.get("lang") : config.defaultLanguage;
    const t = config.pages[lang] || config.pages[config.defaultLanguage];
    const hidden = params.map((p) => `<input type="hidden" name="${p}" value="${esc(url.searchParams.get(p) || "")}">`).join("");
    const title = `${t[`${kind}_title`]} | ${config.brand}`;
    const page =
      `<!doctype html><html lang="${lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">` +
      `<meta name="robots" content="noindex"><title>${esc(title)}</title><link rel="stylesheet" href="/assets/site.css"></head>` +
      `<body class="page-action"><header class="nav"><a class="wordmark" href="${esc(config.home[lang] || "/")}">${esc(config.wordmark)}</a></header>` +
      `<main class="art intro"><div><h1>${esc(t[`${kind}_title`])}</h1></div><div class="body">` +
      `<p>${esc(t[`${kind}_text`])}</p><form method="post" action="/api/waitlist/${kind}">${hidden}<input type="hidden" name="lang" value="${lang}">` +
      `<button class="lpill solid action-button" type="submit"><span>${esc(t[`${kind}_button`])}</span></button>` +
      `</form></div></main></body></html>`;
    return new Response(page, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "x-robots-tag": "noindex" } });
  }
}

/** The visitor's country from Cloudflare's geolocation (two letters), or "" when unknown. Never stored with the IP. */
function countryOf(request) {
  const code = request && request.cf && request.cf.country ? String(request.cf.country).toUpperCase() : "";
  return /^[A-Z]{2}$/.test(code) && code !== "XX" && code !== "T1" ? code : "";
}

/** One analytics event: no personal data, no IP, no person profile; country from Cloudflare only. */
function event(name, anon, props, request) {
  const country = countryOf(request) || undefined;
  const clean = Object.fromEntries(Object.entries(props).filter(([, v]) => v !== undefined && v !== ""));
  return {
    event: name,
    distinct_id: anon || "anonymous",
    properties: { ...clean, country, site: SITE.id, $process_person_profile: false, $geoip_disable: true, $ip: null },
  };
}

function utm(body) {
  const out = {};
  for (const key of ["utm_source", "utm_medium", "utm_campaign"]) {
    const value = String(body[key] || "").replace(/[^A-Za-z0-9._-]/g, "").slice(0, 40);
    if (value) out[key] = value;
  }
  return out;
}

export async function captureEvents(config, env, events) {
  const analytics = config.analytics || {};
  if (!analytics.key || !analytics.host || !events.length) return;
  const stamp = new Date().toISOString();
  try {
    await fetch(`${analytics.host.replace(/\/$/, "")}/batch/`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ api_key: analytics.key, batch: events.map((e) => ({ ...e, timestamp: stamp })) }),
    });
  } catch (err) {
    console.error("analytics relay failed", String(err));
  }
}

export function classify(src, referer) {
  const clean = src.toLowerCase().replace(/[^a-z0-9._-]/g, "").slice(0, 40);
  if (clean && clean !== "referral") return clean;
  let host = "";
  try {
    host = new URL(referer).hostname.toLowerCase();
  } catch {
    return "direct";
  }
  if (SEARCH.test(host)) return "search";
  if (host.endsWith("reddit.com")) return "reddit";
  if (["t.co", "x.com", "twitter.com"].includes(host)) return "x";
  return host.replace(/^www\./, "").slice(0, 40) || "direct";
}

export async function sendMail(config, env, message) {
  const provider = config.email.provider;
  if (provider === "log") {
    console.log("mail", JSON.stringify(message));
    return;
  }
  const headers = message.unsubscribe ? { "List-Unsubscribe": `<${message.unsubscribe}>` } : {};
  let res;
  if (provider === "resend") {
    res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { authorization: `Bearer ${env.EMAIL_API_KEY}`, "content-type": "application/json" },
      body: JSON.stringify({ from: config.email.from, to: [message.to], subject: message.subject, text: message.text, headers }),
    });
  } else if (provider === "brevo") {
    const match = /^(.*)<(.+)>$/.exec(config.email.from);
    const sender = match ? { name: match[1].trim(), email: match[2].trim() } : { email: config.email.from };
    res = await fetch("https://api.brevo.com/v3/smtp/email", {
      method: "POST",
      headers: { "api-key": env.EMAIL_API_KEY, "content-type": "application/json" },
      body: JSON.stringify({ sender, to: [{ email: message.to }], subject: message.subject, textContent: message.text, headers }),
    });
  } else {
    throw new Error(`unknown email provider ${provider}`);
  }
  if (!res.ok) throw new Error(`mail provider answered ${res.status}`);
}

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8", ...headers } });
}

function token() {
  const bytes = crypto.getRandomValues(new Uint8Array(24));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomCode(length) {
  const alphabet = "abcdefghjkmnpqrstuvwxyz23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}

async function hash(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function sameSecret(a, b) {
  const [x, y] = await Promise.all([hash(a), hash(b)]);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x.charCodeAt(i) ^ y.charCodeAt(i);
  return diff === 0;
}

function esc(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
