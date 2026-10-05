// Waitlist API for Cloudflare Workers + D1: double opt-in, personal invite links, move-up rewards,
// a live counter and token-protected stats for the weekly digest. Project-neutral: every name,
// path and text comes from the generated config.js.
//
//   POST /api/waitlist                sign up (form post or JSON); always the same answer
//   GET  /api/waitlist/confirm?t=     a page with one button; POST confirms (mail scanners only GET)
//   POST /api/waitlist/confirm        confirm, credit the referrer, send the welcome mail
//   GET  /api/waitlist/status?s=      position, referrals, invite link (private token)
//   GET  /api/waitlist/leave?s=       a page with one button; POST deletes the entry
//   GET  /api/waitlist/count          confirmed sign-ups per role (cached for a minute)
//   GET  /api/waitlist/stats          daily numbers, sources, referral cohorts (Bearer STATS_TOKEN)
//   GET  /r/<code>                    invite link: redirects to the inviter's page with ?ref=

const EMAIL = /^[^\s@]{1,64}@[^\s@]{1,255}\.[a-z]{2,}$/i;
const CODE = /^[a-z0-9]{6,12}$/;
const TOKEN = /^[A-Za-z0-9_-]{20,64}$/;
const SEARCH = /(^|\.)(google|bing|duckduckgo|ecosia|yahoo|qwant|startpage|brave)\./;

export function makeHandler(config, deps = {}) {
  const send = deps.sendMail || ((env, mail) => sendMail(config, env, mail));
  const now = deps.now || (() => new Date());

  return async function handle(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    try {
      if (path.startsWith("/r/")) return invite(path.slice(3), env);
      if (path === "/api/waitlist" && request.method === "POST") return await signup(request, env);
      if (path === "/api/waitlist/confirm") return request.method === "POST" ? await confirm(request, env) : buttonPage("confirm", url, "t");
      if (path === "/api/waitlist/leave") return request.method === "POST" ? await leave(request, env) : buttonPage("leave", url, "s");
      if (path === "/api/waitlist/status" && request.method === "GET") return await status(url, env);
      if (path === "/api/waitlist/count" && request.method === "GET") return await count(env);
      if (path === "/api/waitlist/stats" && request.method === "GET") return await stats(request, env);
      if (env.ASSETS) return env.ASSETS.fetch(request);
      return json({ error: "not found" }, 404);
    } catch (err) {
      console.error("waitlist error", err && err.stack ? err.stack : err);
      return json({ error: "server error" }, 500);
    }
  };

  // ---- sign up ---------------------------------------------------------------------------------

  async function signup(request, env) {
    const wantsJson = (request.headers.get("content-type") || "").includes("application/json");
    let body;
    try {
      body = wantsJson ? await request.json() : Object.fromEntries(await request.formData());
    } catch {
      return reply(wantsJson, 400, { error: "bad request" }, statusUrl({ e: "invalid" }));
    }
    const lang = config.languages.includes(body.lang) ? body.lang : config.defaultLanguage;
    const role = body.role === "host" ? "host" : "player";
    const email = String(body.email || "").trim().toLowerCase();
    if (body.website) return reply(wantsJson, 202, { ok: true }, statusUrl({ sent: 1 }, lang)); // honeypot
    if (!EMAIL.test(email) || email.length > 254) return reply(wantsJson, 400, { error: "invalid email" }, statusUrl({ e: "invalid" }, lang));
    if (!mailReady(env)) return reply(wantsJson, 503, { error: "not open yet" }, statusUrl({ e: "closed" }, lang));
    if (!(await allowIp(request, env))) return reply(wantsJson, 429, { error: "too many sign-ups" }, statusUrl({ e: "busy" }, lang));

    const stamp = now().toISOString();
    const existing = await env.DB.prepare("SELECT * FROM signups WHERE email = ?").bind(email).first();
    if (existing) {
      // Same answer as a new sign-up, so the form never reveals who is on the list.
      if (!recently(existing.mail_sent_at, 24 * 3600e3)) {
        if (existing.confirmed_at) {
          const s = token();
          await env.DB.prepare("UPDATE signups SET status_hash = ?, mail_sent_at = ? WHERE id = ?").bind(await hash(s), stamp, existing.id).run();
          await mail(env, existing.lang, existing.email, "again", { status_url: statusUrl({}, existing.lang, s), leave_url: leaveUrl(s, existing.lang) });
        } else {
          const t = token();
          await env.DB.prepare("UPDATE signups SET confirm_hash = ?, mail_sent_at = ? WHERE id = ?").bind(await hash(t), stamp, existing.id).run();
          await mail(env, existing.lang, existing.email, "confirm", { confirm_url: confirmUrl(t, existing.lang) });
        }
      }
      return reply(wantsJson, 202, { ok: true }, statusUrl({ sent: 1 }, lang));
    }

    let referrer = null;
    const ref = String(body.ref || "").toLowerCase();
    if (CODE.test(ref)) referrer = await env.DB.prepare("SELECT id FROM signups WHERE code = ? AND confirmed_at IS NOT NULL").bind(ref).first();
    const source = referrer ? "referral" : classify(String(body.src || ""), String(body.from || ""));
    const t = token();
    await env.DB.prepare(
      "INSERT INTO signups (email, role, lang, code, confirm_hash, referred_by, source, page, created_at, mail_sent_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
      .bind(email, role, lang, await freeCode(env), await hash(t), referrer ? referrer.id : null, source, String(body.page || "").slice(0, 60), stamp, stamp)
      .run();
    await mail(env, lang, email, "confirm", { confirm_url: confirmUrl(t, lang) });
    return reply(wantsJson, 202, { ok: true }, statusUrl({ sent: 1 }, lang));
  }

  async function confirm(request, env) {
    const wantsJson = (request.headers.get("content-type") || "").includes("application/json");
    const body = wantsJson ? await request.json().catch(() => ({})) : Object.fromEntries(await request.formData());
    const t = String(body.t || "");
    const row = TOKEN.test(t) ? await env.DB.prepare("SELECT * FROM signups WHERE confirm_hash = ?").bind(await hash(t)).first() : null;
    if (!row) return reply(wantsJson, 404, { error: "unknown or used link" }, statusUrl({ e: "expired" }));
    const s = token();
    const stamp = now().toISOString();
    await env.DB.prepare("UPDATE signups SET confirmed_at = ?, confirm_hash = NULL, status_hash = ? WHERE id = ? AND confirmed_at IS NULL")
      .bind(stamp, await hash(s), row.id)
      .run();
    if (row.referred_by) await creditReferrer(env, row.referred_by);
    const position = await positionOf(env, row.id, row.role);
    await mail(env, row.lang, row.email, "welcome", {
      status_url: statusUrl({}, row.lang, s),
      invite_url: inviteUrl(row.code),
      position,
      move_up: config.moveUpPerReferral,
      leave_url: leaveUrl(s, row.lang),
    });
    return reply(wantsJson, 200, { ok: true, s }, statusUrl({ new: 1 }, row.lang, s));
  }

  async function creditReferrer(env, id) {
    const ref = await env.DB.prepare("SELECT * FROM signups WHERE id = ?").bind(id).first();
    if (!ref || !ref.confirmed_at) return;
    if (ref.referrals >= config.maxCreditedReferrals) return;
    await env.DB.prepare("UPDATE signups SET referrals = referrals + 1 WHERE id = ?").bind(id).run();
    if (config.movedUpMail && !recently(ref.moved_up_mail_at, 24 * 3600e3)) {
      const position = await positionOf(env, ref.id, ref.role);
      await env.DB.prepare("UPDATE signups SET moved_up_mail_at = ? WHERE id = ?").bind(now().toISOString(), ref.id).run();
      // No status link here: only the owner's own mails carry their private token.
      await mail(env, ref.lang, ref.email, "movedup", { position, invite_url: inviteUrl(ref.code), move_up: config.moveUpPerReferral });
    }
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
      "SELECT substr(confirmed_at, 1, 10) AS date, role, COUNT(*) AS confirmed, SUM(CASE WHEN referred_by IS NOT NULL THEN 1 ELSE 0 END) AS referred " +
        "FROM signups WHERE confirmed_at IS NOT NULL GROUP BY date, role ORDER BY date",
    ).all();
    const created = await env.DB.prepare("SELECT substr(created_at, 1, 10) AS date, COUNT(*) AS signups FROM signups GROUP BY date ORDER BY date").all();
    const sources = await env.DB.prepare(
      "SELECT source, role, COUNT(*) AS confirmed FROM signups WHERE confirmed_at IS NOT NULL GROUP BY source, role ORDER BY confirmed DESC",
    ).all();
    // Referral cohorts: users confirmed in a week, and how many confirmed sign-ups they brought in.
    const cohorts = await env.DB.prepare(
      "SELECT date(s.confirmed_at, '-6 days', 'weekday 1') AS week, COUNT(*) AS size, " +
        "SUM((SELECT COUNT(*) FROM signups f WHERE f.referred_by = s.id AND f.confirmed_at IS NOT NULL)) AS invited " +
        "FROM signups s WHERE s.confirmed_at IS NOT NULL GROUP BY week ORDER BY week",
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
        cohorts: cohorts.results || [],
      },
      200,
      { "cache-control": "no-store" },
    );
  }

  async function leave(request, env) {
    const body = Object.fromEntries(await request.formData());
    const s = String(body.s || "");
    if (TOKEN.test(s)) await env.DB.prepare("DELETE FROM signups WHERE status_hash = ?").bind(await hash(s)).run();
    return Response.redirect(absolute(statusUrl({ left: 1 })), 303);
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

  // ---- helpers -----------------------------------------------------------------------------------

  function recently(stamp, ms) {
    return Boolean(stamp) && now().getTime() - Date.parse(stamp) < ms;
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

  async function mail(env, lang, to, kind, values) {
    const strings = config.mail[lang] || config.mail[config.defaultLanguage];
    const fill = (text) => text.replace(/\{(\w+)\}/g, (m, k) => (k in values ? String(values[k]) : k === "brand" ? config.brand : m));
    const message = { to, subject: fill(strings[`${kind}_subject`]), text: fill(strings[`${kind}_body`]) };
    if (values.leave_url) message.unsubscribe = values.leave_url;
    await send(env, message);
  }

  function mailReady(env) {
    return config.email.provider === "log" || Boolean(env.EMAIL_API_KEY && config.email.from);
  }

  function statusUrl(params, lang, secret) {
    const base = (config.paths.status[lang] || config.paths.status[config.defaultLanguage]);
    const query = new URLSearchParams(params).toString();
    return base + (query ? `?${query}` : "") + (secret ? `#s=${secret}` : "");
  }
  function confirmUrl(t, lang) {
    return absolute(`/api/waitlist/confirm?t=${t}&lang=${lang}`);
  }
  function leaveUrl(s, lang) {
    return absolute(`/api/waitlist/leave?s=${s}&lang=${lang}`);
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

  function buttonPage(kind, url, param) {
    const value = url.searchParams.get(param) || "";
    const lang = config.languages.includes(url.searchParams.get("lang")) ? url.searchParams.get("lang") : config.defaultLanguage;
    const t = config.pages[lang] || config.pages[config.defaultLanguage];
    const page =
      `<!doctype html><html lang="${lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">` +
      `<meta name="robots" content="noindex"><title>${esc(t[`${kind}_title`])}</title><link rel="stylesheet" href="/assets/site.css"></head>` +
      `<body class="page-action"><main class="art intro"><div><h1>${esc(t[`${kind}_title`])}</h1></div><div class="body">` +
      `<p>${esc(t[`${kind}_text`])}</p><form method="post" action="/api/waitlist/${kind}">` +
      `<input type="hidden" name="${param}" value="${esc(value)}"><button class="lpill solid" type="submit"><span>${esc(t[`${kind}_button`])}</span></button>` +
      `</form></div></main></body></html>`;
    return new Response(page, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "x-robots-tag": "noindex" } });
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
