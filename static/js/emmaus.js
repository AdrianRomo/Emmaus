/* Emmaus - small progressive enhancements. Every page works without this file. */
(function () {
  "use strict";

  var doc = document;
  doc.documentElement.classList.remove("no-js");

  function $(sel, root) { return (root || doc).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); }
  function csrfToken() {
    var match = doc.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    if (match) return decodeURIComponent(match[1]);
    var input = $('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : "";
  }
  function timeLabel(date) {
    return date.toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  }

  /* ---- Mobile menu ------------------------------------------------------ */
  $all("[data-menu-open]").forEach(function (button) {
    var panel = doc.getElementById(button.getAttribute("aria-controls"));
    if (!panel) return;
    function close() { panel.hidden = true; button.setAttribute("aria-expanded", "false"); button.focus(); }
    button.addEventListener("click", function () {
      panel.hidden = false;
      button.setAttribute("aria-expanded", "true");
      var first = $("a, button", panel);
      if (first) first.focus();
    });
    $all("[data-menu-close]", panel).forEach(function (b) { b.addEventListener("click", close); });
    panel.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  });

  /* ---- Folds that start closed on small screens ------------------------- */
  function applyFolds(root, skip) {
    $all("details[data-fold-below]", root).forEach(function (d) {
      if (skip && d.id && d.id in skip) return;
      var px = parseInt(d.getAttribute("data-fold-below"), 10);
      if (window.matchMedia && window.matchMedia("(max-width: " + px + "px)").matches) d.open = false;
    });
  }
  applyFolds(doc);

  /* ---- Live regions: polling with "last update" and stale warnings ------- */
  $all("[data-live-url]").forEach(function (region) {
    var url = region.getAttribute("data-live-url");
    var seconds = parseInt(region.getAttribute("data-live-interval") || "20", 10);
    var status = doc.getElementById(region.getAttribute("data-live-status") || "") || $("[data-live-status-for='" + region.id + "']");
    var lastOk = new Date();
    var timer = null;
    var stopped = false;

    function setStatus(state, text) {
      if (!status) return;
      status.setAttribute("data-state", state);
      status.textContent = text;
    }
    function schedule() { if (!stopped) timer = window.setTimeout(tick, seconds * 1000); }
    function tick() {
      if (doc.hidden) { schedule(); return; }
      if (region.contains(doc.activeElement) && doc.activeElement !== doc.body) { schedule(); return; }
      region.setAttribute("aria-busy", "true");
      if (status && status.getAttribute("data-state") !== "stale") status.setAttribute("data-state", "loading");
      fetch(url + (url.indexOf("?") === -1 ? "?" : "&") + "_=" + Date.now(), {
        credentials: "same-origin",
        headers: { "X-Requested-With": "fetch" },
        cache: "no-store"
      }).then(function (response) {
        if (response.redirected || response.status === 401 || response.status === 403) {
          stopped = true;
          setStatus("stale", "Tu sesión terminó. Vuelve a entrar para ver datos actuales.");
          return null;
        }
        if (!response.ok) throw new Error(String(response.status));
        return response.text();
      }).then(function (html) {
        if (html === null || html === undefined) return;
        // A refresh must not close a table or fold the reader opened (or reopen one they closed).
        var openState = {};
        $all("details[id]", region).forEach(function (d) { openState[d.id] = d.open; });
        region.innerHTML = html;
        $all("details[id]", region).forEach(function (d) { if (d.id in openState) d.open = openState[d.id]; });
        applyFolds(region, openState);
        lastOk = new Date();
        setStatus("ok", "Última actualización: " + timeLabel(lastOk));
        region.dispatchEvent(new CustomEvent("emmaus:updated", { bubbles: true }));
      }).catch(function () {
        var ago = Math.round((Date.now() - lastOk.getTime()) / 60000);
        setStatus("stale", "Sin conexión. Los datos pueden estar desactualizados (última actualización: " + timeLabel(lastOk) + (ago >= 1 ? ", hace " + ago + " min" : "") + ").");
      }).then(function () {
        region.removeAttribute("aria-busy");
        schedule();
      });
    }
    setStatus("ok", "Última actualización: " + timeLabel(lastOk));
    schedule();
  });

  /* ---- Conditional reveal (GOV.UK pattern) ------------------------------- */
  function refreshReveals(root) {
    $all("[data-reveal-for]", root).forEach(function (block) {
      var name = block.getAttribute("data-reveal-for");
      var codes = (block.getAttribute("data-reveal-codes") || "").split(",");
      var checked = $all('input[name="' + name + '"]:checked').map(function (i) { return i.value; });
      var show = checked.some(function (c) { return codes.indexOf(c) !== -1; });
      block.hidden = !show;
      $all("textarea, input, select", block).forEach(function (el) { el.disabled = !show; });
    });
  }
  $all("[data-reveal-for]").forEach(function (block) {
    block.removeAttribute("data-noscript-visible");
  });
  doc.addEventListener("change", function (e) {
    if (e.target && e.target.matches && e.target.matches("input[type=radio], input[type=checkbox]")) refreshReveals(doc);
  });
  refreshReveals(doc);

  /* ---- Exclusive checkbox options ("Prefiero no responder") --------------- */
  doc.addEventListener("change", function (e) {
    var input = e.target;
    if (!input.matches || !input.matches("input[type=checkbox]") || !input.checked) return;
    var group = $all('input[type=checkbox][name="' + input.name + '"]');
    if (input.hasAttribute("data-exclusive")) {
      group.forEach(function (other) { if (other !== input) other.checked = false; });
    } else {
      group.forEach(function (other) { if (other.hasAttribute("data-exclusive")) other.checked = false; });
    }
    refreshReveals(doc);
  });

  /* ---- Unsaved-work guard and double-submit protection -------------------- */
  var dirtyForms = [];
  $all("form[data-guard]").forEach(function (form) {
    form.addEventListener("input", function () { if (dirtyForms.indexOf(form) === -1) dirtyForms.push(form); });
    form.addEventListener("change", function () { if (dirtyForms.indexOf(form) === -1) dirtyForms.push(form); });
  });
  window.addEventListener("beforeunload", function (e) {
    if (dirtyForms.length) { e.preventDefault(); e.returnValue = ""; }
  });
  $all("form").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (form.classList.contains("is-submitting")) { e.preventDefault(); return; }
      dirtyForms = dirtyForms.filter(function (f) { return f !== form; });
      form.classList.add("is-submitting");
      window.setTimeout(function () { form.classList.remove("is-submitting"); }, 8000);
    });
  });
  $all("[data-confirm]").forEach(function (el) {
    el.addEventListener("click", function (e) {
      if (!window.confirm(el.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  /* ---- Error summary focus ----------------------------------------------- */
  var summary = $(".error-summary");
  if (summary) { summary.setAttribute("tabindex", "-1"); summary.focus(); }

  /* ---- Copy / print helpers ---------------------------------------------- */
  $all("[data-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      var target = doc.getElementById(button.getAttribute("data-copy"));
      if (!target) return;
      var text = target.value || target.textContent;
      (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () {
        button.textContent = "Copiado";
      }).catch(function () { target.select && target.select(); });
    });
  });
  $all("[data-print]").forEach(function (b) { b.addEventListener("click", function () { window.print(); }); });

  /* ---- Kiosk: idle lock and server heartbeat ----------------------------- */
  var body = doc.body;
  var idleSeconds = parseInt(body.getAttribute("data-kiosk-idle") || "0", 10);
  if (idleSeconds > 0) {
    var timeoutUrl = body.getAttribute("data-kiosk-timeout-url");
    var heartbeatUrl = body.getAttribute("data-kiosk-heartbeat-url");
    var warning = doc.getElementById("idle-warning");
    var last = Date.now();
    var lastBeat = Date.now();
    function activity() {
      last = Date.now();
      if (warning && warning.open) warning.close();
      if (heartbeatUrl && Date.now() - lastBeat > 120000) {
        lastBeat = Date.now();
        fetch(heartbeatUrl, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrfToken() } }).catch(function () {});
      }
    }
    ["input", "keydown", "pointerdown", "touchstart", "scroll"].forEach(function (evt) {
      doc.addEventListener(evt, activity, { passive: true });
    });
    window.setInterval(function () {
      var idle = (Date.now() - last) / 1000;
      if (warning && idle > idleSeconds - 60 && !warning.open && typeof warning.showModal === "function") warning.showModal();
      if (idle > idleSeconds) { dirtyForms = []; window.location.replace(timeoutUrl); }
    }, 5000);
    if (warning) {
      $all("[data-idle-continue]", warning).forEach(function (b) {
        b.addEventListener("click", function () { lastBeat = 0; activity(); });
      });
    }
  }

  /* ---- Presentation deck ------------------------------------------------- */
  var deck = $(".deck");
  if (deck) {
    body.classList.add("is-presenting");
    var index = 0;
    var counter = $("[data-deck-count]");
    function slides() { return $all(".slide", deck); }
    function show(i) {
      var list = slides();
      if (!list.length) return;
      index = Math.max(0, Math.min(list.length - 1, i));
      list.forEach(function (s, n) {
        s.classList.toggle("is-active", n === index);
        s.setAttribute("aria-hidden", n === index ? "false" : "true");
      });
      if (counter) counter.textContent = (index + 1) + " de " + list.length;
      if (history.replaceState) history.replaceState(null, "", "#" + (index + 1));
    }
    var fromHash = parseInt((location.hash || "#1").slice(1), 10);
    show(isNaN(fromHash) ? 0 : fromHash - 1);
    doc.addEventListener("keydown", function (e) {
      if (e.target && e.target.closest && e.target.closest("button, a, input, textarea, select, summary, [contenteditable]")) return;
      if (["ArrowRight", "PageDown", " "].indexOf(e.key) !== -1) { e.preventDefault(); show(index + 1); }
      if (["ArrowLeft", "PageUp"].indexOf(e.key) !== -1) { e.preventDefault(); show(index - 1); }
      if (e.key === "Home") show(0);
      if (e.key === "End") show(slides().length - 1);
      if (e.key === "f" || e.key === "F") toggleFull();
    });
    $all("[data-deck-prev]").forEach(function (b) { b.addEventListener("click", function () { show(index - 1); }); });
    $all("[data-deck-next]").forEach(function (b) { b.addEventListener("click", function () { show(index + 1); }); });
    function toggleFull() {
      if (!doc.fullscreenElement && deck.requestFullscreen) deck.requestFullscreen().catch(function () {});
      else if (doc.exitFullscreen) doc.exitFullscreen();
    }
    $all("[data-deck-full]").forEach(function (b) { b.addEventListener("click", toggleFull); });
    doc.addEventListener("fullscreenchange", function () {
      $all("[data-deck-full]").forEach(function (b) { b.textContent = doc.fullscreenElement ? "Salir de pantalla completa" : "Pantalla completa"; });
    });
    var idleTimer = null;
    function wake() {
      deck.classList.remove("is-idle");
      window.clearTimeout(idleTimer);
      idleTimer = window.setTimeout(function () { deck.classList.add("is-idle"); }, 3000);
    }
    doc.addEventListener("mousemove", wake);
    doc.addEventListener("keydown", wake);
    wake();
    deck.addEventListener("emmaus:updated", function () { show(index); });

    // "Cal" (light) by default; "Noche" for a dim room. The class sits on .deck, outside the
    // region the live refresh replaces, and the choice is remembered for the next deck.
    var THEME_KEY = "emmaus:deck-theme";
    var themeButtons = $all("[data-deck-theme]");
    function rememberedTheme() {
      try { return window.localStorage.getItem(THEME_KEY); } catch (err) { return null; }  // storage blocked: start in "Cal"
    }
    function setNight(night, remember) {
      deck.classList.toggle("deck--noche", night);
      themeButtons.forEach(function (b) { b.setAttribute("aria-pressed", night ? "true" : "false"); });
      if (!remember) return;
      try { window.localStorage.setItem(THEME_KEY, night ? "noche" : "cal"); } catch (err) { return; }  // storage blocked: the choice lasts until the page closes
    }
    setNight(rememberedTheme() === "noche", false);
    themeButtons.forEach(function (b) {
      b.addEventListener("click", function () { setNight(!deck.classList.contains("deck--noche"), true); });
    });
  }
})();
