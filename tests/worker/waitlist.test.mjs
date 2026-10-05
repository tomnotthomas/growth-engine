// The waitlist Worker against a real SQLite database (node:sqlite standing in for Cloudflare D1).
// Run: node --test tests/worker/   (Node 22.5 or newer)
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
  paths: { status: { en: "/waitlist/", de: "/de/warteliste/" }, player: "/", host: "/host/" },
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
};

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
  const handle = makeHandler({ ...CONFIG, ...overrides.config }, { sendMail: async (_env, mail) => sent.push(mail) });
  let ip = 0;
  const call = (path, init = {}) => {
    const headers = new Headers(init.headers || {});
    if (!headers.has("cf-connecting-ip")) headers.set("cf-connecting-ip", `10.0.0.${++ip}`);
    return handle(new Request(`https://kiln.example${path}`, { ...init, headers, redirect: "manual" }), env);
  };
  const signup = (body, headers = {}) =>
    call("/api/waitlist", { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify({ role: "player", lang: "en", ...body }) });
  const confirm = (t) => call("/api/waitlist/confirm", { method: "POST", body: new URLSearchParams({ t }) });
  const tokenFrom = (mail, key) => new URL(mail.text.split(" ").find((w) => w.includes(`${key}=`))).searchParams.get(key);
  const join = async (email, ref) => {
    await signup({ email, ref });
    const mail = sent.filter((m) => m.to === email && m.subject.startsWith("confirm")).at(-1);
    const res = await confirm(tokenFrom(mail, "t"));
    return new URL(res.headers.get("location")).hash.slice(3);
  };
  return { env, sent, call, signup, confirm, join, tokenFrom };
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
  assert.equal((await confirm(t)).status, 303, "a used link just lands on the expired notice");
  assert.equal(sent.length, 2);
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
  assert.equal(sent.filter((m) => m.to === "solo@example.org").length, 1, "no second mail within a day");
});

test("bots, bad addresses, floods and a closed list", async () => {
  const { env, signup } = setup();
  assert.equal((await signup({ email: "bot@example.org", website: "http://spam" })).status, 202);
  assert.equal(row(env, "bot@example.org"), undefined);
  assert.equal((await signup({ email: "not-an-address" })).status, 400);
  const ip = { "cf-connecting-ip": "203.0.113.9" };
  const codes = [];
  for (let i = 0; i < 7; i++) codes.push((await signup({ email: `flood${i}@example.org` }, ip)).status);
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

test("sources are classified without storing the full referrer", () => {
  assert.equal(classify("", "https://www.google.de/search?q=x"), "search");
  assert.equal(classify("", "https://old.reddit.com/r/macgaming"), "reddit");
  assert.equal(classify("Newsletter!", ""), "newsletter");
  assert.equal(classify("", ""), "direct");
  assert.equal(classify("", "https://forum.example.net/thread/1"), "forum.example.net");
});
