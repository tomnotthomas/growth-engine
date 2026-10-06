// The host page's hardware check (growth/assets/waitlist.js) run against real browser renderer strings.
// Run: node --test tests/worker/*.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SCRIPT = readFileSync(new URL("../../growth/assets/waitlist.js", import.meta.url), "utf8");

const RULES = {
  rules: { models: ["RX 6800", "RX 7900 XTX"], not_yet: ["GeForce"], not_host: ["Apple", "Intel"], family: ["Radeon"] },
  messages: { fits: "{model}: fits.", not_yet: "{model}: not supported yet.", not_host: "{model}: this computer cannot host.", below: "{model}: below the floor." },
};

/** Runs the page script with a host-page hardware check box and a browser naming `renderer`; returns the line shown. */
function check(renderer) {
  const line = { textContent: "" };
  const box = {
    hidden: true,
    classList: { add() {} },
    querySelector: (sel) => (sel === "[data-hw-rules]" ? { textContent: JSON.stringify(RULES) } : line),
  };
  const gl = { getExtension: () => ({ UNMASKED_RENDERER_WEBGL: 1 }), getParameter: () => renderer };
  const document = {
    documentElement: { lang: "en" },
    referrer: "",
    getElementById: (id) => (id === "wl-strings" ? { textContent: "{}" } : null),
    querySelector: () => null,
    querySelectorAll: (sel) => (sel === "[data-hardware-check]" ? [box] : []),
    createElement: () => ({ getContext: () => gl }),
  };
  const location = new URL("https://kiln.example/host/");
  const context = { document, location, URLSearchParams, URL, Intl, Math, Date, JSON, String, RegExp, Object,
    addEventListener() {}, setTimeout() {}, clearTimeout() {}, navigator: {}, fetch: () => Promise.reject(new Error("no network")) };
  context.window = context;
  vm.runInNewContext(SCRIPT, context);
  return box.hidden ? null : line.textContent;
}

test("Chrome on macOS names the chip, not the ANGLE version field", () => {
  assert.equal(check("ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)"), "Apple M2: this computer cannot host.");
});

test("Chrome on Windows names the card without the PCI id and Direct3D tail", () => {
  assert.equal(
    check("ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    "NVIDIA GeForce RTX 4070: not supported yet.",
  );
  assert.equal(
    check("ANGLE (Intel, Intel(R) UHD Graphics 620 (0x00005917) Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    "Intel(R) UHD Graphics 620: this computer cannot host.",
  );
  assert.equal(check("ANGLE (AMD, AMD Radeon RX 7900 XTX (0x0000744C) Direct3D11 vs_5_0 ps_5_0, D3D11)"), "RX 7900 XTX: fits.");
});

test("Firefox's rounded '…, or similar' strings name the card", () => {
  assert.equal(check("ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0), or similar"), "NVIDIA GeForce GTX 980: not supported yet.");
  assert.equal(check("Apple M1, or similar"), "Apple M1: this computer cannot host.");
  assert.equal(check("ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0), or similar"), "AMD Radeon RX 580: below the floor.");
});

test("a browser that hides the renderer shows nothing", () => {
  assert.equal(check(""), null);
});
