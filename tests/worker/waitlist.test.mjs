// The waitlist Worker against a real SQLite database (node:sqlite standing in for Cloudflare D1).
// Run: node --test tests/worker/*.test.mjs   (Node 22.5 or newer)
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { makeHandler, classify } from "../../growth/waitlist/waitlist.js";

const SCHEMA = readFileSync(new URL("../../growth/waitlist/schema.sql", import.meta.url), "utf8");

const CONFIG = {
  brand: "Kiln",
  baseUrl: "https://kiln.example",
  languages: ["en", "de"],
  defaultLanguage: "en",
  site: "example",
  wordmark: "KILN",
  home: { en: "/", de: "/de/" },
  paths: { status: { player: { en: "/waitlist/", de: "/de/warteliste/" }, host: { en: "/host/status/" } }, player: "/", host: "/host/" },
  rolePaths: { player: { en: "/", de: "/de/" }, host: { en: "/host/" } },
  moveUpPerReferral: 5,
  maxCreditedReferrals: 50,
  movedUpMail: true,
  counterMin: 0,
  signupsPerIpHour: 5,
  email: { provider: "log", from: "Kiln <beta@kiln.example>" },
  mail: Object.fromEntries(
    ["en", "de"].map((l) => [
      l,
      Object.fromEntries(
        ["confirm", "welcome", "movedup", "again"].flatMap((k) => [
          [`${k}_subject`, `${k} {brand}`],
          [`${k}_body`, `${k} {confirm_url} {status_url} {invite_url} {position} {leave_url}`],
        ]),
      ),
    ]),
  ),
  pages: { en: { confirm_title: "Confirm", confirm_text: "One click.", confirm_button: "Confirm", leave_title: "Leave", leave_text: "Bye.", leave_button: "Leave" } },
  analytics: { host: "https://eu.i.posthog.example", key: "phc_test" },
};
CONFIG.mail.en.host_welcome_subject = "host welcome {brand}";
CONFIG.mail.en.host_welcome_body = "host welcome {status_url} {leave_url}";

class D1 {
  constructor() {
    this.db = new DatabaseSync(":memory:");
    this.db.exec(SCHEMA);
  }
  prepare(sql) {
    return new Statement(this.db, sql, []);
  }
}
class Statement {
  constructor(db, sql, args) {
    Object.assign(this, { db, sql, args });
  }
  bind(...args) {
    return new Statement(this.db, this.sql, args);
  }
  async first() {
    const row = this.db.prepare(this.sql).get(...this.args);
    return row ? { ...row } : null;
  }
  async all() {
    return { results: this.db.prepare(this.sql).all(...this.args).map((r) => ({ ...r })) };
  }
  async run() {
    const r = this.db.prepare(this.sql).run(...this.args);
    return { meta: { changes: r.changes } };
  }
}

function setup(overrides = {}) {
  const sent = [];
  const env = { DB: new D1(), STATS_TOKEN: "stats-secret", HASH_SALT: "salt", ...overrides.env };
  let down = false;
  let clock = Date.parse("2026-10-05T12:00:00Z");
  const relayed = [];
  const handle = makeHandler({ ...CONFIG, ...overrides.config }, {
    sendMail: async (_env, mail) => {
      if (down) throw new Error("mail provider answered 503");
      sent.push(mail);
    },
    now: () => new Date(clock),
    capture: async (_env, events) => relayed.push(...events),
  });
  let ip = 0;
  const call = (path, init = {}) => {
    const headers = new Headers(init.headers || {});
    if (!headers.has("cf-connecting-ip")) headers.set("cf-connecting-ip", `10.0.0.${++ip}`);
    const request = new Request(`https://kiln.example${path}`, { ...init, headers, redirect: "manual" });
    if (init.country) Object.defineProperty(request, "cf", { value: { country: init.country } });
    return handle(request, env);
  };
  const signup = (body, headers = {}, country = "DE") =>
    call("/api/waitlist", { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify({ role: "player", lang: "en", ...body }), country });
  const confirm = (t) => call("/api/waitlist/confirm", { method: "POST", body: new URLSearchParams({ t }) });
  const tokenFrom = (mail, key) => new URL(mail.text.split(" ").find((w) => w.includes(`${key}=`))).searchParams.get(key);
  const join = async (email, ref) => {
    await signup({ email, ref });
    const mail = sent.filter((m) => m.to === email && m.subject.startsWith("confirm")).at(-1);
    const res = await confirm(tokenFrom(mail, "t"));
    return new URL(res.headers.get("location")).hash.slice(3);
  };
  const later = (minutes) => (clock += minutes * 60e3);
  return { env, sent, call, signup, confirm, join, tokenFrom, relayed, later, mailDown: (value) => (down = value) };
}

const row = (env, email) => env.DB.db.prepare("SELECT * FROM signups WHERE email = ?").get(email);

test("double opt-in: nobody is on the list until they confirm", async () => {
  const { env, sent, signup, confirm, tokenFrom } = setup();
  const res = await signup({ email: "Ada@Example.org" });
  assert.equal(res.status, 202);
  assert.equal(row(env, "ada@example.org").confirmed_at, null);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].subject, "confirm Kiln");
  const t = tokenFrom(sent[0], "t");
  assert.equal(env.DB.db.prepare("SELECT COUNT(*) AS n FROM signups WHERE confirm_hash = ?").get(t).n, 0, "only the hash is stored");
  const done = await confirm(t);
  assert.equal(done.status, 303);
  assert.match(done.headers.get("location"), /^https:\/\/kiln\.example\/waitlist\/\?new=1#s=/);
  assert.ok(row(env, "ada@example.org").confirmed_at);
  assert.equal(sent.at(-1).subject, "welcome Kiln");
  assert.match(sent.at(-1).text, / 1 /, "first in line");
  assert.match(sent.at(-1).text, /https:\/\/kiln\.example\/waitlist\/#s=/, "the status link in the mail is absolute");
  const again = await confirm(t);
  assert.equal(again.status, 303);
  assert.match(again.headers.get("location"), /\/waitlist\/\?e=already$/, "a reused link says you're already in");
  assert.equal(sent.length, 2, "no extra mail within ten minutes");
});

test("mail scanners that only GET a confirm link confirm nothing", async () => {
  const { env, sent, signup, call, tokenFrom } = setup();
  await signup({ email: "bo@example.org" });
  const res = await call(`/api/waitlist/confirm?t=${tokenFrom(sent[0], "t")}`);
  assert.equal(res.status, 200);
  assert.match(await res.text(), /<form method="post" action="\/api\/waitlist\/confirm">/);
  assert.equal(row(env, "bo@example.org").confirmed_at, null);
});

test("each confirmed referral moves the inviter up five places", async () => {
  const { env, sent, join, call } = setup();
  const secrets = [];
  for (let i = 1; i <= 6; i++) secrets.push(await join(`p${i}@example.org`));
  const before = await (await call(`/api/waitlist/status?s=${secrets[5]}`)).json();
  assert.equal(before.position, 6);
  const code = row(env, "p6@example.org").code;
  await join("friend@example.org", code);
  assert.equal(row(env, "friend@example.org").source, "referral");
  const after = await (await call(`/api/waitlist/status?s=${secrets[5]}`)).json();
  assert.equal(after.referrals, 1);
  assert.equal(after.position, 2);
  assert.equal(after.invite_url, `https://kiln.example/r/${code}`);
  const moved = sent.find((m) => m.to === "p6@example.org" && m.subject.startsWith("movedup"));
  assert.ok(moved, "the inviter hears about it");
  assert.doesNotMatch(moved.text, /s=/, "a moved-up mail never carries the private status link");
});

test("an unconfirmed inviter earns nothing and the same address can't sign up twice", async () => {
  const { env, sent, signup } = setup();
  await signup({ email: "solo@example.org" });
  const code = row(env, "solo@example.org").code;
  await signup({ email: "pal@example.org", ref: code });
  assert.notEqual(row(env, "pal@example.org").source, "referral");
  const res = await signup({ email: "solo@example.org" });
  assert.equal(res.status, 202, "same answer, so the form reveals nothing");
  assert.equal(env.DB.db.prepare("SELECT COUNT(*) AS n FROM signups").get().n, 2);
  assert.equal(sent.filter((m) => m.to === "solo@example.org").length, 1, "no second mail within ten minutes");
});

test("bots, bad addresses, floods and a closed list", async () => {
  const { env, signup } = setup();
  assert.equal((await signup({ email: "bot@example.org", website: "http://spam" })).status, 202);
  assert.equal(row(env, "bot@example.org"), undefined);
  assert.equal((await signup({ email: "not-an-address" })).status, 400);
  const ip = { "cf-connecting-ip": "203.0.113.9" };
  const codes = [];
  for (let i = 0; i < 7; i++) codes.push((await signup({ email: `flood${i}@example.org` }, ip)).status); // limit 5 in this config
  assert.deepEqual(codes, [202, 202, 202, 202, 202, 429, 429]);
  const closed = setup({ config: { email: { provider: "brevo", from: "" } } });
  assert.equal((await closed.signup({ email: "early@example.org" })).status, 503);
});

test("form posts without JavaScript land on the status page", async () => {
  const { call } = setup();
  const res = await call("/api/waitlist", { method: "POST", body: new URLSearchParams({ email: "nojs@example.org", role: "player", lang: "de" }) });
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("location"), "https://kiln.example/de/warteliste/?sent=1");
});

test("stats need the token and report days, sources and referral cohorts", async () => {
  const { call, join, env } = setup();
  await join("a@example.org");
  await join("b@example.org", row(env, "a@example.org").code);
  assert.equal((await call("/api/waitlist/stats")).status, 401);
  assert.equal((await call("/api/waitlist/stats", { headers: { authorization: "Bearer wrong" } })).status, 401);
  const stats = await (await call("/api/waitlist/stats", { headers: { authorization: "Bearer stats-secret" } })).json();
  assert.equal(stats.totals.confirmed, 2);
  assert.equal(stats.days.reduce((n, d) => n + d.confirmed, 0), 2);
  assert.equal(stats.days.reduce((n, d) => n + d.referred, 0), 1);
  assert.deepEqual(stats.sources.map((s) => s.source).sort(), ["direct", "referral"]);
  assert.equal(stats.cohorts.reduce((n, c) => n + c.invited, 0), 1);
  const count = await (await call("/api/waitlist/count")).json();
  assert.equal(count.player, 2);
});

test("invite links redirect with the code, and leaving deletes the address", async () => {
  const { call, join, env } = setup();
  const secret = await join("c@example.org");
  const code = row(env, "c@example.org").code;
  const res = await call(`/r/${code}`);
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("location"), `https://kiln.example/?ref=${code}&utm_source=referral`);
  const left = await call("/api/waitlist/leave", { method: "POST", body: new URLSearchParams({ s: secret }) });
  assert.equal(left.status, 303);
  assert.equal(row(env, "c@example.org"), undefined);
});

test("a failed mail can be retried right away instead of being throttled for a day", async () => {
  const { env, sent, signup, confirm, tokenFrom, mailDown } = setup();
  mailDown(true);
  assert.equal((await signup({ email: "retry@example.org" })).status, 500);
  assert.equal((await signup({ email: "retry@example.org" })).status, 500);
  mailDown(false);
  await signup({ email: "retry@example.org" });
  assert.equal(sent.filter((m) => m.to === "retry@example.org").length, 1, "the confirmation goes out");
  await confirm(tokenFrom(sent.at(-1), "t"));
  env.DB.db.prepare("UPDATE signups SET mail_sent_at = NULL WHERE email = ?").run("retry@example.org");
  mailDown(true);
  assert.equal((await signup({ email: "retry@example.org" })).status, 500);
  mailDown(false);
  await signup({ email: "retry@example.org" });
  assert.ok(sent.at(-1).subject.startsWith("again"), "a confirmed address gets its links again");
});

test("confirming twice at once credits the inviter once and sends one welcome mail", async () => {
  const { env, sent, join, signup, confirm, tokenFrom } = setup();
  await join("inviter@example.org");
  await signup({ email: "twice@example.org", ref: row(env, "inviter@example.org").code });
  const t = tokenFrom(sent.at(-1), "t");
  const [a, b] = await Promise.all([confirm(t), confirm(t)]);
  assert.deepEqual([a.status, b.status], [303, 303]);
  assert.equal([a, b].filter((r) => /e=already/.test(r.headers.get("location"))).length, 1);
  assert.equal(row(env, "inviter@example.org").referrals, 1);
  assert.equal(sent.filter((m) => m.to === "twice@example.org" && m.subject.startsWith("welcome")).length, 1);
});

test("leaving and joining again through the same invite does not credit the inviter twice", async () => {
  const { env, join, call } = setup();
  await join("inviter@example.org");
  const code = row(env, "inviter@example.org").code;
  const s = await join("friend@example.org", code);
  assert.equal(row(env, "inviter@example.org").referrals, 1);
  await call("/api/waitlist/leave", { method: "POST", body: new URLSearchParams({ s }) });
  assert.equal(row(env, "inviter@example.org").referrals, 0);
  await join("friend@example.org", code);
  assert.equal(row(env, "inviter@example.org").referrals, 1);
});

test("an uncredited referral over the cap leaving keeps the inviter at the cap", async () => {
  const { env, join, call } = setup({ config: { maxCreditedReferrals: 1 } });
  await join("inviter@example.org");
  const code = row(env, "inviter@example.org").code;
  await join("f1@example.org", code);
  const s = await join("f2@example.org", code);
  assert.equal(row(env, "inviter@example.org").referrals, 1);
  await call("/api/waitlist/leave", { method: "POST", body: new URLSearchParams({ s }) });
  assert.equal(row(env, "inviter@example.org").referrals, 1);
});

test("sources are classified without storing the full referrer", () => {
  assert.equal(classify("", "https://www.google.de/search?q=x"), "search");
  assert.equal(classify("", "https://old.reddit.com/r/macgaming"), "reddit");
  assert.equal(classify("Newsletter!", ""), "newsletter");
  assert.equal(classify("", ""), "direct");
  assert.equal(classify("", "https://forum.example.net/thread/1"), "forum.example.net");
});

test("a lost confirmation mail can be sent again after ten minutes, at most three a day", async () => {
  const { sent, signup, later } = setup();
  await signup({ email: "lost@example.org" });
  await signup({ email: "lost@example.org" });
  assert.equal(sent.length, 1, "not within ten minutes");
  later(11);
  await signup({ email: "lost@example.org" });
  later(11);
  await signup({ email: "lost@example.org" });
  later(11);
  await signup({ email: "lost@example.org" });
  assert.equal(sent.length, 3, "three confirmation mails a day at most");
  later(24 * 60);
  await signup({ email: "lost@example.org" });
  assert.equal(sent.length, 4, "a new day allows a new mail");
});

test("a reused confirm link mails the status link again once the throttle allows it", async () => {
  const { sent, signup, confirm, tokenFrom, later } = setup();
  await signup({ email: "again@example.org" });
  const t = tokenFrom(sent[0], "t");
  await confirm(t);
  later(11);
  const res = await confirm(t);
  assert.match(res.headers.get("location"), /e=already/);
  assert.ok(sent.at(-1).subject.startsWith("again"));
  assert.match(sent.at(-1).text, /https:\/\/kiln\.example\/waitlist\/#s=/);
});

test("the moved-up mail comes only on a real move and carries a working leave link", async () => {
  const { env, sent, join, call, tokenFrom } = setup();
  await join("first@example.org");
  const code = row(env, "first@example.org").code;
  await join("friend1@example.org", code);
  assert.equal(sent.filter((m) => m.subject.startsWith("movedup")).length, 0, "already first in line: nothing moved, no mail");
  for (let i = 0; i < 6; i++) await join(`p${i}@example.org`);
  const late = row(env, "p5@example.org").code;
  await join("friend2@example.org", late);
  const moved = sent.find((m) => m.to === "p5@example.org" && m.subject.startsWith("movedup"));
  assert.ok(moved);
  const leave = new URL(moved.text.split(" ").find((w) => w.includes("/api/waitlist/leave")));
  const res = await call("/api/waitlist/leave", { method: "POST", body: new URLSearchParams({ c: leave.searchParams.get("c"), l: leave.searchParams.get("l"), lang: "de" }) });
  assert.equal(res.headers.get("location"), "https://kiln.example/de/warteliste/?left=1", "the language survives");
  assert.equal(row(env, "p5@example.org"), undefined);
  const forged = await call("/api/waitlist/leave", { method: "POST", body: new URLSearchParams({ c: row(env, "first@example.org").code, l: "x".repeat(32) }) });
  assert.equal(forged.status, 303);
  assert.ok(row(env, "first@example.org"), "a guessed leave token deletes nothing");
  assert.ok(tokenFrom);
});

test("hosts land on their own status page and get their own mails", async () => {
  const { sent, signup, confirm, tokenFrom } = setup();
  const res = await signup({ email: "host@example.org", role: "host" });
  assert.equal(res.status, 202);
  const done = await confirm(tokenFrom(sent[0], "t"));
  assert.match(done.headers.get("location"), /^https:\/\/kiln\.example\/host\/status\/\?new=1#s=/);
  assert.equal(sent.at(-1).subject, "host welcome Kiln");
});

test("the country is stored for the in-zone goal, and stats report it", async () => {
  const { call, signup, confirm, sent, tokenFrom, env } = setup();
  await signup({ email: "nl@example.org" }, {}, "NL");
  await confirm(tokenFrom(sent.at(-1), "t"));
  await signup({ email: "us@example.org" }, {}, "US");
  await confirm(tokenFrom(sent.at(-1), "t"));
  assert.equal(row(env, "nl@example.org").country, "NL");
  const stats = await (await call("/api/waitlist/stats", { headers: { authorization: "Bearer stats-secret" } })).json();
  assert.deepEqual(stats.countries.map((c) => c.country).sort(), ["NL", "US"]);
  assert.ok(stats.days.every((d) => "country" in d));
});

test("analytics: only listed events and properties are relayed, with no IP, email or person profile", async () => {
  const { call, relayed, signup, confirm, sent, tokenFrom } = setup();
  const anon = "a1b2c3d4-0000-4000-8000-000000000000";
  const res = await call("/api/e", {
    method: "POST",
    headers: { "content-type": "application/json" },
    country: "AT",
    body: JSON.stringify({
      anon,
      events: [
        { event: "$pageview", props: { path: "/", lang: "de", email: "leak@example.org", utm_source: "reddit" } },
        { event: "something_else", props: {} },
      ],
    }),
  });
  assert.equal(res.status, 204);
  assert.equal(relayed.length, 1);
  const [view] = relayed;
  assert.equal(view.event, "$pageview");
  assert.equal(view.distinct_id, anon);
  assert.equal(view.properties.country, "AT");
  assert.equal(view.properties.site, "example");
  assert.equal(view.properties.email, undefined, "properties outside the list are dropped");
  assert.equal(view.properties.$process_person_profile, false);
  assert.equal(view.properties.$ip, null);
  await signup({ email: "funnel@example.org", anon }, {}, "AT");
  await confirm(tokenFrom(sent.at(-1), "t"));
  const names = relayed.map((e) => e.event);
  assert.deepEqual(names.slice(1), ["waitlist_signup", "waitlist_confirmed"]);
  assert.ok(relayed.every((e) => !JSON.stringify(e).includes("funnel@example.org")), "no email in any event");
  assert.equal(relayed.at(-1).distinct_id, anon, "the funnel links by the page's random id only");
});

test("an expired confirm link and a bad request keep the language", async () => {
  const { call } = setup();
  const res = await call("/api/waitlist/confirm", { method: "POST", body: new URLSearchParams({ t: "x".repeat(32), lang: "de" }) });
  assert.equal(res.headers.get("location"), "https://kiln.example/de/warteliste/?e=expired");
  const page = await call("/api/waitlist/confirm?t=abc&lang=en");
  const html = await page.text();
  assert.match(html, /<title>Confirm \| Kiln<\/title>/);
  assert.match(html, /class="wordmark"/);
  assert.match(html, /name="lang" value="en"/);
});

test("the same address signing up twice at once gets the same answer and one mail", async () => {
  const { env, sent, signup } = setup();
  const [a, b] = await Promise.all([signup({ email: "race@example.org" }), signup({ email: "race@example.org" })]);
  assert.deepEqual([a.status, b.status], [202, 202]);
  assert.equal(env.DB.db.prepare("SELECT COUNT(*) AS n FROM signups WHERE email = ?").get("race@example.org").n, 1);
  assert.equal(sent.filter((m) => m.to === "race@example.org").length, 1);
});
