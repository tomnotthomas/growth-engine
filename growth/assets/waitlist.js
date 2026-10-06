/* Waitlist forms, live counter, invite page, game search and cookieless analytics. Progressive
   enhancement: without this script the forms still post to /api/waitlist and land on the status page.
   All copy comes from the page's #wl-strings JSON, so the engine stays project-neutral.
   Nothing is stored on the visitor's device: no cookies, no localStorage; the analytics id lives only
   as long as the page. */
(function () {
  "use strict";

  var strings = {};
  var settings = {};
  try {
    strings = JSON.parse(document.getElementById("wl-strings").textContent);
    settings = JSON.parse((document.getElementById("wl-settings") || { textContent: "{}" }).textContent);
  } catch (e) {
    return;
  }
  var lang = settings.lang || document.documentElement.lang || "de";
  var query = new URLSearchParams(location.search);
  var EMAIL = /^[^\s@]{1,64}@[^\s@]{1,255}\.[a-z]{2,}$/i;
  var REF = /^[a-z0-9]{6,12}$/;
  var anon = makeId();
  var ref = (query.get("ref") || "").toLowerCase();
  if (!REF.test(ref)) ref = "";
  var statusPage = document.querySelector("[data-waitlist-status]");
  var role = (statusPage && statusPage.getAttribute("data-role")) || settings.role || "player";

  function t(key, values) {
    var text = (role === "host" && strings["host_" + key]) || strings[key] || "";
    Object.keys(values || {}).forEach(function (k) {
      text = text.split("{" + k + "}").join(values[k]);
    });
    return text;
  }
  function number(n) {
    try {
      return new Intl.NumberFormat(lang).format(n);
    } catch (e) {
      return String(n);
    }
  }
  function makeId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return String(Date.now()) + "-" + Math.random().toString(36).slice(2, 12);
  }

  // ---- analytics: relayed by our own server without IP, cookies or personal data ---------------------

  var queue = [];
  var timer = null;
  function track(event, props) {
    if (!settings.analytics) return;
    var base = { path: location.pathname, lang: lang, page: settings.page || "", role: role };
    ["utm_source", "utm_medium", "utm_campaign"].forEach(function (k) {
      if (query.get(k)) base[k] = query.get(k).slice(0, 40);
    });
    if (ref) base.referred = true;
    queue.push({ event: event, props: Object.assign(base, props || {}) });
    clearTimeout(timer);
    timer = setTimeout(flush, 400);
  }
  function flush() {
    if (!queue.length) return;
    var body = JSON.stringify({ anon: anon, events: queue.splice(0, queue.length) });
    if (navigator.sendBeacon && navigator.sendBeacon("/api/e", new Blob([body], { type: "application/json" }))) return;
    fetch("/api/e", { method: "POST", headers: { "content-type": "application/json" }, body: body, keepalive: true }).catch(function () {});
  }
  addEventListener("pagehide", flush);
  track("$pageview");

  // ---- referral: keep ?ref= on links within the site, and say who sent you ----------------------------

  if (ref) {
    document.querySelectorAll("a[href]").forEach(function (a) {
      var url;
      try {
        url = new URL(a.getAttribute("href"), location.href);
      } catch (e) {
        return;
      }
      if (url.origin !== location.origin || url.pathname.indexOf("/api/") === 0) return;
      url.searchParams.set("ref", ref);
      a.setAttribute("href", url.pathname + url.search + url.hash);
    });
    document.querySelectorAll("[data-ref-note]").forEach(function (note) {
      note.hidden = false;
    });
  }

  // ---- forms ------------------------------------------------------------------------------------

  var DOMAINS = ["gmail.com", "googlemail.com", "gmx.de", "gmx.net", "web.de", "t-online.de", "outlook.com", "outlook.de",
    "hotmail.com", "hotmail.de", "icloud.com", "me.com", "yahoo.com", "yahoo.de", "posteo.de", "proton.me", "protonmail.com",
    "freenet.de", "live.com", "live.de", "aol.com", "mail.de", "orange.fr", "free.fr", "ziggo.nl", "kpnmail.nl", "telenet.be"];

  function distance(a, b) {
    var row = [];
    for (var j = 0; j <= b.length; j++) row[j] = j;
    for (var i = 1; i <= a.length; i++) {
      var prev = row[0];
      row[0] = i;
      for (var k = 1; k <= b.length; k++) {
        var tmp = row[k];
        row[k] = Math.min(row[k] + 1, row[k - 1] + 1, prev + (a[i - 1] === b[k - 1] ? 0 : 1));
        prev = tmp;
      }
    }
    return row[b.length];
  }
  /** "lena@gmai.com" -> "lena@gmail.com"; null when the domain looks fine or is unknown. */
  function suggest(email) {
    var at = email.lastIndexOf("@");
    if (at < 1) return null;
    var domain = email.slice(at + 1).toLowerCase();
    if (DOMAINS.indexOf(domain) >= 0) return null;
    var best = null;
    DOMAINS.forEach(function (known) {
      var d = distance(domain, known);
      if (d > 0 && d <= 2 && (!best || d < best.d)) best = { d: d, domain: known };
    });
    return best ? email.slice(0, at + 1) + best.domain : null;
  }

  document.querySelectorAll("form[data-waitlist]").forEach(function (form) {
    if (ref) form.elements.ref.value = ref;
    form.elements.src.value = (query.get("utm_source") || "").slice(0, 40);
    var from = document.createElement("input");
    from.type = "hidden";
    from.name = "from";
    from.value = document.referrer && document.referrer.indexOf(location.origin) !== 0 ? document.referrer : "";
    form.appendChild(from);

    var input = form.querySelector("input[type=email]");
    var err = form.querySelector(".wl-err");
    var state = form.querySelector(".wl-state");
    var done = form.querySelector(".wl-done");
    var formRole = form.elements.role ? form.elements.role.value : "player";
    var seen = false;

    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (entries, observer) {
        if (!seen && entries.some(function (e) { return e.isIntersecting; })) {
          seen = true;
          track("waitlist_form_view", { role: formRole });
          observer.disconnect();
        }
      }).observe(form);
    }

    function clear() {
      err.hidden = true;
      err.textContent = "";
      form.removeAttribute("data-invalid");
      input.removeAttribute("aria-invalid");
    }
    function fail(message, fix) {
      err.textContent = message;
      if (fix) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "linkish";
        button.textContent = fix;
        button.addEventListener("click", function () {
          input.value = fix;
          clear();
          input.focus();
        });
        err.textContent = "";
        err.appendChild(document.createTextNode(message + " "));
        err.appendChild(button);
        err.appendChild(document.createTextNode("?"));
      }
      err.hidden = false;
      form.setAttribute("data-invalid", "");
      input.setAttribute("aria-invalid", "true");
      input.focus();
    }

    input.addEventListener("input", function () {
      if (!err.hidden && EMAIL.test(input.value.trim())) clear();
    });

    var wrong = done.querySelector("[data-wrong-address]");
    if (wrong) {
      wrong.addEventListener("click", function () {
        done.hidden = true;
        state.hidden = false;
        input.select();
        input.focus();
      });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (form.getAttribute("aria-busy") === "true") return;
      var value = input.value.trim();
      if (!value) return fail(t("empty_field"));
      if (!EMAIL.test(value)) return fail(t("invalid"));
      var fix = suggest(value);
      if (fix && form.getAttribute("data-checked") !== value) {
        form.setAttribute("data-checked", value); // a second press sends the address as typed
        return fail(t("did_you_mean"), fix);
      }
      var body = {};
      new FormData(form).forEach(function (v, k) {
        body[k] = v;
      });
      ["utm_medium", "utm_campaign"].forEach(function (k) {
        if (query.get(k)) body[k] = query.get(k).slice(0, 40);
      });
      form.setAttribute("aria-busy", "true");
      track("waitlist_submit", { role: formRole });
      fetch(form.action, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
        .then(function (res) {
          form.removeAttribute("aria-busy");
          if (res.status === 202) {
            var line = done.querySelector("[data-done-text]");
            if (line && t("sent_to")) line.textContent = t("sent_to", { email: value });
            state.hidden = true;
            done.hidden = false;
            done.focus();
          } else if (res.status === 400) {
            fail(t("invalid"));
          } else if (res.status === 429) {
            fail(t("busy"));
          } else if (res.status === 503) {
            fail(t("closed"));
          } else {
            fail(t("error"));
          }
        })
        .catch(function () {
          form.removeAttribute("aria-busy");
          fail(t("error"));
        });
    });
  });

  // ---- live counter -------------------------------------------------------------------------------

  var counters = document.querySelectorAll("[data-waitlist-count]");
  if (counters.length) {
    fetch("/api/waitlist/count")
      .then(function (res) {
        return res.ok ? res.json() : null;
      })
      .then(function (data) {
        if (!data) return;
        counters.forEach(function (el) {
          var n = data[el.getAttribute("data-waitlist-count")] || 0;
          if (n < (data.min || 1)) return;
          el.textContent = el.textContent.split("{n}").join(number(n));
          el.hidden = false;
        });
      })
      .catch(function () {});
  }

  // ---- game search ----------------------------------------------------------------------------------

  document.querySelectorAll("[data-game-search]").forEach(function (section) {
    var input = section.querySelector("[data-search-input]");
    var list = section.querySelector("[data-search-results]");
    var miss = section.querySelector("[data-search-miss]");
    var games = null;
    var sent = "";
    function normal(text) {
      return text.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
    }
    function render() {
      var q = normal(input.value);
      list.innerHTML = "";
      miss.hidden = true;
      if (!games || q.length < 2) return;
      var hits = games.filter(function (g) {
        return normal(g.name).indexOf(q) >= 0;
      }).slice(0, 8);
      hits.forEach(function (g) {
        var li = document.createElement("li");
        li.className = "hit " + g.status;
        var name = document.createElement(g.href ? "a" : "span");
        name.className = "name";
        name.textContent = g.name;
        if (g.href) name.href = g.href;
        var label = document.createElement("span");
        label.className = "status";
        label.textContent = t("search_status_" + g.status);
        li.appendChild(name);
        li.appendChild(label);
        if (g.note) {
          var note = document.createElement("span");
          note.className = "meta";
          note.textContent = g.note;
          li.appendChild(note);
        }
        list.appendChild(li);
      });
      miss.hidden = hits.length > 0;
      if (q !== sent && q.length >= 3) {
        sent = q;
        track("game_search", { found: hits.length > 0 });
      }
    }
    input.addEventListener("input", render);
    fetch(section.getAttribute("data-game-search"))
      .then(function (res) {
        return res.ok ? res.json() : [];
      })
      .then(function (data) {
        games = data;
        render();
      })
      .catch(function () {});
  });

  // ---- hardware check: the graphics card named by the browser, compared locally; nothing is sent -----

  document.querySelectorAll("[data-hardware-check]").forEach(function (box) {
    var config;
    try {
      config = JSON.parse(box.querySelector("[data-hw-rules]").textContent);
    } catch (e) {
      return;
    }
    var name = "";
    try {
      var gl = document.createElement("canvas").getContext("webgl");
      var info = gl && gl.getExtension("WEBGL_debug_renderer_info");
      name = info ? String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL) || "") : "";
    } catch (e) {
      name = "";
    }
    if (!name) return; // the browser hides it: the card list on the page answers instead
    var lower = name.toLowerCase();
    function has(list) {
      for (var i = 0; i < list.length; i++) if (lower.indexOf(String(list[i]).toLowerCase()) >= 0) return list[i];
      return "";
    }
    // the longest matching model wins ("RX 7900 XTX" over "RX 7900 XT")
    var model = config.rules.models.slice().sort(function (a, b) { return b.length - a.length; }).filter(function (m) {
      return new RegExp("(^|[^0-9a-z])" + m.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "($|[^0-9a-z])").test(lower);
    })[0];
    var verdict = model ? "fits" : has(config.rules.not_yet) ? "not_yet" : has(config.rules.not_host) ? "not_host" : has(config.rules.family) ? "below" : "";
    if (!verdict) return;
    // "ANGLE (Vendor, Model (0x…) Direct3D11 …, D3D11)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2,
    // Unspecified Version)", Firefox's "…, or similar": keep only the part that names the model
    var parts = name.replace(/,\s*or similar$/i, "").replace(/^ANGLE \((.*)\)$/, "$1")
      .replace(/\s+\(.*$/, "").replace(/\s+Direct3D.*$/i, "").split(",");
    var shown = parts[parts.length > 1 ? 1 : 0].replace(/^.*Renderer:\s*/, "").trim();
    box.querySelector("[data-hw-line]").textContent = config.messages[verdict].split("{model}").join(model || shown);
    box.classList.add("hw-" + verdict);
    box.hidden = false;
  });

  // ---- status and invite page ------------------------------------------------------------------------

  if (!statusPage) return;
  var title = statusPage.querySelector("[data-status-title]");
  var text = statusPage.querySelector("[data-status-text]");
  var card = statusPage.querySelector("[data-status-card]");
  var formBox = statusPage.querySelector("[data-status-form]");
  var secret = (location.hash.match(/[#&]s=([A-Za-z0-9_-]{20,64})/) || [])[1];

  function show(key, withForm) {
    title.textContent = t(key + "_title");
    text.textContent = t(key + "_text");
    if (formBox) formBox.hidden = !withForm;
  }

  if (secret) {
    // Keep the private link out of the address bar, but carry it when switching language (fragment only).
    history.replaceState(null, "", location.pathname + location.search);
    document.querySelectorAll("[data-lang-switch] a").forEach(function (a) {
      a.setAttribute("href", a.getAttribute("href").split("#")[0] + "#s=" + secret);
    });
    show(query.get("new") ? "new" : "status", false);
    fetch("/api/waitlist/status?s=" + encodeURIComponent(secret))
      .then(function (res) {
        return res.ok ? res.json() : Promise.reject(res.status);
      })
      .then(function (data) {
        card.querySelector('[data-k="position"]').textContent = t("position");
        card.querySelector('[data-v="position"]').textContent = number(data.position);
        card.querySelector('[data-k="referrals"]').textContent = t("referrals");
        card.querySelector('[data-v="referrals"]').textContent = number(data.referrals);
        var invite = card.querySelector("[data-invite]");
        invite.value = data.invite_url;
        var message = t("share_message_" + data.role, { url: data.invite_url });
        var targets = [
          ["whatsapp", "WhatsApp", "https://wa.me/?text=" + encodeURIComponent(message)],
          ["telegram", "Telegram", "https://t.me/share/url?url=" + encodeURIComponent(data.invite_url) + "&text=" + encodeURIComponent(message)],
          ["x", "X", "https://x.com/intent/post?text=" + encodeURIComponent(message)],
          ["email", t("email"), "mailto:?subject=" + encodeURIComponent(t("email_subject")) + "&body=" + encodeURIComponent(message)],
        ];
        var row = card.querySelector("[data-share-buttons]");
        if (navigator.share) {
          var native = document.createElement("button");
          native.type = "button";
          native.textContent = t("share_native");
          native.addEventListener("click", function () {
            track("referral_sent", { method: "native" });
            navigator.share({ text: message }).catch(function () {});
          });
          row.appendChild(native);
        }
        targets.forEach(function (target) {
          var a = document.createElement("a");
          a.href = target[2];
          a.textContent = target[1];
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.addEventListener("click", function () {
            track("referral_sent", { method: target[0] });
          });
          row.appendChild(a);
        });
        var copy = card.querySelector("[data-copy]");
        copy.addEventListener("click", function () {
          invite.select();
          (navigator.clipboard ? navigator.clipboard.writeText(invite.value) : Promise.reject())
            .catch(function () {
              document.execCommand("copy");
            })
            .then(function () {
              copy.textContent = t("copied");
              track("referral_sent", { method: "copy" });
            });
        });
        card.querySelector("[data-leave]").href = "/api/waitlist/leave?s=" + encodeURIComponent(secret) + "&lang=" + lang;
        text.textContent = t(query.get("new") ? "new_text" : "status_text", { move_up: data.move_up });
        card.hidden = false;
      })
      .catch(function () {
        show("expired", true);
      });
  } else if (query.get("sent")) {
    show("sent", true);
  } else if (query.get("left")) {
    show("left", true);
  } else if (query.get("e")) {
    var known = ["expired", "invalid", "closed", "busy", "already"];
    show(known.indexOf(query.get("e")) >= 0 ? query.get("e") : "error", true);
  } else {
    show("empty", true);
  }
})();
