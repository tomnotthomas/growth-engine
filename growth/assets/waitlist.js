/* Waitlist forms, live counter and the invite page. Progressive enhancement: without this script the
   forms still post to /api/waitlist and land on the status page. All copy comes from the page's
   #wl-strings JSON, so the engine stays project-neutral. Nothing is stored on the visitor's device. */
(function () {
  "use strict";

  var strings = {};
  try {
    strings = JSON.parse(document.getElementById("wl-strings").textContent);
  } catch (e) {
    return;
  }
  var lang = document.documentElement.lang || "de";
  var query = new URLSearchParams(location.search);
  var EMAIL = /^[^\s@]{1,64}@[^\s@]{1,255}\.[a-z]{2,}$/i;

  function t(key, values) {
    var text = strings[key] || "";
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

  // ---- forms ------------------------------------------------------------------------------------

  document.querySelectorAll("form[data-waitlist]").forEach(function (form) {
    var ref = (query.get("ref") || "").toLowerCase();
    if (/^[a-z0-9]{6,12}$/.test(ref)) form.elements.ref.value = ref;
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

    function fail(message) {
      err.textContent = message;
      err.hidden = false;
      form.setAttribute("data-invalid", "");
      input.setAttribute("aria-invalid", "true");
      input.focus();
    }

    input.addEventListener("input", function () {
      if (!err.hidden && EMAIL.test(input.value.trim())) {
        err.hidden = true;
        form.removeAttribute("data-invalid");
        input.removeAttribute("aria-invalid");
      }
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (form.getAttribute("aria-busy") === "true") return;
      if (!EMAIL.test(input.value.trim())) return fail(t("invalid"));
      var body = {};
      new FormData(form).forEach(function (value, key) {
        body[key] = value;
      });
      form.setAttribute("aria-busy", "true");
      fetch(form.action, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
        .then(function (res) {
          form.removeAttribute("aria-busy");
          if (res.status === 202) {
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

  // ---- status and invite page ------------------------------------------------------------------------

  var page = document.querySelector("[data-waitlist-status]");
  if (!page) return;
  var title = page.querySelector("[data-status-title]");
  var text = page.querySelector("[data-status-text]");
  var card = page.querySelector("[data-status-card]");
  var secret = (location.hash.match(/[#&]s=([A-Za-z0-9_-]{20,64})/) || [])[1];

  function show(key) {
    title.textContent = t(key + "_title");
    text.textContent = t(key + "_text");
  }

  if (secret) {
    history.replaceState(null, "", location.pathname + location.search); // keep the private link out of the address bar
    show(query.get("new") ? "new" : "status");
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
          ["WhatsApp", "https://wa.me/?text=" + encodeURIComponent(message)],
          ["Telegram", "https://t.me/share/url?url=" + encodeURIComponent(data.invite_url) + "&text=" + encodeURIComponent(message)],
          ["X", "https://x.com/intent/post?text=" + encodeURIComponent(message)],
          [t("email"), "mailto:?subject=" + encodeURIComponent(t("email_subject")) + "&body=" + encodeURIComponent(message)],
        ];
        var row = card.querySelector("[data-share-buttons]");
        if (navigator.share) {
          var native = document.createElement("button");
          native.type = "button";
          native.textContent = t("share_native");
          native.addEventListener("click", function () {
            navigator.share({ text: message }).catch(function () {});
          });
          row.appendChild(native);
        }
        targets.forEach(function (target) {
          var a = document.createElement("a");
          a.href = target[1];
          a.textContent = target[0];
          a.target = "_blank";
          a.rel = "noopener noreferrer";
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
            });
        });
        card.querySelector("[data-leave]").href = "/api/waitlist/leave?s=" + encodeURIComponent(secret) + "&lang=" + lang;
        text.textContent = t(query.get("new") ? "new_text" : "status_text", { move_up: data.move_up });
        card.hidden = false;
      })
      .catch(function () {
        show("expired");
      });
  } else if (query.get("sent")) {
    show("sent");
  } else if (query.get("left")) {
    show("left");
  } else if (query.get("e")) {
    show(["expired", "invalid", "closed", "busy"].indexOf(query.get("e")) >= 0 ? query.get("e") : "error");
  } else {
    show("empty");
  }
})();
