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

  /* ---- Mobile menu: a modal dialog (audit A11Y-02) ------------------------ */
  // Open: focus moves into the panel, everything else on the page is inert (where the browser lacks `inert`: hidden
  // from assistive technology and out of the tab order), and Tab and Shift+Tab cycle inside. Escape, "Cerrar menú"
  // or a window grown past the menu's breakpoint close it; focus goes back to "Menú".
  var FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), " +
    "textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1'])";
  function focusables(root) {
    return $all(FOCUSABLE, root).filter(function (el) { return el.getClientRects().length > 0; });
  }
  function inertOutside(keep) {
    var changed = [];
    Array.prototype.forEach.call(doc.body.children, function (el) {
      if (el === keep || el.tagName === "DIALOG" || el.tagName === "SCRIPT") return;  // a notice may open on top
      if ("inert" in el) {
        if (!el.inert) { el.inert = true; changed.push({ el: el }); }
        return;
      }
      if (el.getAttribute("aria-hidden") === "true") return;
      var own = el.matches && el.matches(FOCUSABLE) ? [el] : [];  // the skip link is itself a child of <body>
      var tabbable = own.concat(focusables(el)).map(function (f) {
        var before = f.getAttribute("tabindex");
        f.setAttribute("tabindex", "-1");
        return { el: f, tabindex: before };
      });
      el.setAttribute("aria-hidden", "true");
      changed.push({ el: el, hidden: true, tabbable: tabbable });
    });
    return changed;
  }
  function restoreOutside(changed) {
    changed.forEach(function (c) {
      if (!c.hidden) { c.el.inert = false; return; }
      c.el.removeAttribute("aria-hidden");
      c.tabbable.forEach(function (t) {
        if (t.tabindex === null) t.el.removeAttribute("tabindex"); else t.el.setAttribute("tabindex", t.tabindex);
      });
    });
  }
  $all("[data-menu-open]").forEach(function (button) {
    var panel = doc.getElementById(button.getAttribute("aria-controls"));
    if (!panel) return;
    var changed = [];
    function insideNotice(el) { return !!(el && el.closest && el.closest("dialog[open]")); }
    function onKey(e) {
      if (insideNotice(e.target)) return;  // "¿Sigues aquí?" answers its own keys
      if (e.key === "Escape") { e.preventDefault(); close(true); return; }
      if (e.key !== "Tab") return;
      var items = focusables(panel);
      if (!items.length) { e.preventDefault(); return; }
      var first = items[0], last = items[items.length - 1], active = doc.activeElement;
      if (e.shiftKey && (active === first || !panel.contains(active))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (active === last || !panel.contains(active))) { e.preventDefault(); first.focus(); }
    }
    function onFocusIn(e) {
      if (panel.contains(e.target) || insideNotice(e.target)) return;
      var items = focusables(panel);
      if (items.length) items[0].focus();
    }
    function open() {
      if (!panel.hidden) return;
      panel.hidden = false;
      button.setAttribute("aria-expanded", "true");
      changed = inertOutside(panel);
      doc.addEventListener("keydown", onKey);
      doc.addEventListener("focusin", onFocusIn);
      var items = focusables(panel);
      if (items.length) items[0].focus();
    }
    function close(giveFocusBack) {
      if (panel.hidden) return;
      panel.hidden = true;
      button.setAttribute("aria-expanded", "false");
      restoreOutside(changed);
      changed = [];
      doc.removeEventListener("keydown", onKey);
      doc.removeEventListener("focusin", onFocusIn);
      if (giveFocusBack) button.focus();
    }
    button.addEventListener("click", open);
    $all("[data-menu-close]", panel).forEach(function (b) { b.addEventListener("click", function () { close(true); }); });
    if (window.matchMedia) {  // past 760px "Menú" is not shown (rotulo.css): the bar has the links again
      var narrow = window.matchMedia("(max-width: 760px)");
      var onResize = function () { if (!narrow.matches) close(false); };
      if (narrow.addEventListener) narrow.addEventListener("change", onResize);
      else if (narrow.addListener) narrow.addListener(onResize);
    }
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
      region.setAttribute("data-live-busy", "");  // rotulo.css dims the region if the fetch is slow
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
        // Arrival motion (the panel, the page head, marks filling in) is for the first load only: from the
        // first replacement on, rotulo.css replays nothing inside this region.
        region.setAttribute("data-live-settled", "");
        region.innerHTML = html;
        $all("details[id]", region).forEach(function (d) { if (d.id in openState) d.open = openState[d.id]; });
        applyFolds(region, openState);
        lastOk = new Date();
        setStatus("ok", "Última actualización: " + timeLabel(lastOk));
        region.dispatchEvent(new CustomEvent("emmaus:updated", { bubbles: true }));
      }).catch(function () {
        var ago = Math.round((Date.now() - lastOk.getTime()) / 60000);
        setStatus("stale", "Sin conexión. Los datos pueden estar desactualizados. Última actualización: " + timeLabel(lastOk) + (ago >= 1 ? ", hace " + ago + (ago === 1 ? " minuto" : " minutos") : "") + ".");
      }).then(function () {
        region.removeAttribute("aria-busy");
        region.removeAttribute("data-live-busy");
        schedule();
      });
    }
    setStatus("ok", "Última actualización: " + timeLabel(lastOk));
    schedule();
  });

  /* ---- Live status: announce a change of state, never the time (audit A11Y-07) ---------- */
  // "Última actualización: 14:05:20" changes on every refresh, so its line is aria-live="off". A separate role="status"
  // element, data-live-announce="<id of that line>", receives only a lost connection or an ended session, once when
  // it happens, and is emptied when the data flows again.
  $all("[data-live-announce]").forEach(function (announcer) {
    var line = doc.getElementById(announcer.getAttribute("data-live-announce"));
    if (!line || typeof MutationObserver === "undefined") return;
    var was = line.getAttribute("data-state");
    new MutationObserver(function () {
      var state = line.getAttribute("data-state");
      if (state === "stale" && was !== "stale") announcer.textContent = line.textContent;
      if (state === "ok" && was === "stale") announcer.textContent = "";
      was = state;
    }).observe(line, { attributes: true, attributeFilter: ["data-state"], childList: true, characterData: true, subtree: true });
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

  /* ---- Staff: "¿Sigues aquí?" before the idle logout (audit A11Y-04) -------- */
  // events.middleware logs a staff session out after data-idle-seconds without a request, and typing is not a request.
  // The countdown starts from the server's last touch (data-idle-remaining, the seconds really left) and is shared by
  // every tab of the browser (localStorage). Two minutes before the end a notice offers "Seguir aquí"; it, Escape, and
  // typing or choosing in a form (at most once every two minutes) send one GET to data-idle-ping-url, which touches the
  // session. An idle page sends nothing until the end, and then only a check that does not touch the session
  // (?comprobar=1, a background request): Emmaus says the session closed only when the server says so.
  var body = doc.body;
  var staffIdle = parseInt(body.getAttribute("data-idle-seconds") || "0", 10) * 1000;
  var pingUrl = body.getAttribute("data-idle-ping-url");
  if (staffIdle > 0 && pingUrl) {
    var TOUCH_KEY = "emmaus:staff-touched";
    var warnBefore = staffIdle > 120000 ? 120000 : staffIdle / 2;
    var leftAtLoad = parseInt(body.getAttribute("data-idle-remaining") || "", 10);
    var touched = Date.now() - (isNaN(leftAtLoad) ? 0 : Math.max(0, staffIdle - leftAtLoad * 1000));
    var lastPing = touched;
    var notice = null;
    var noticeReturn = null;
    var noticeState = "";   // "warn", "offline" or "closed" while the notice is open
    var ended = false;
    var checking = false;
    var sharedTouch = function () {
      try { return parseInt(window.localStorage.getItem(TOUCH_KEY) || "0", 10) || 0; }
      catch (err) { return 0; }  // storage blocked: this tab keeps its own countdown, and the server check still decides
    };
    var shareTouch = function (when) {
      touched = Math.max(touched, when);
      try { if (when > sharedTouch()) window.localStorage.setItem(TOUCH_KEY, String(when)); }
      catch (err) { return; }  // storage blocked: the other tabs ask the server before they announce anything
    };
    shareTouch(touched);
    var verdict = function (response) {
      if (response.status === 204) return "alive";
      if (response.type === "opaqueredirect" || (response.status >= 300 && response.status < 400) ||
          response.status === 401 || response.status === 403) return "closed";  // sent to the login page
      return "offline";  // a 5xx or anything else: the server did not say the session closed
    };
    var ask = function (touch) {
      var url = touch ? pingUrl : pingUrl + (pingUrl.indexOf("?") === -1 ? "?" : "&") + "comprobar=1";
      // A check goes as a background request, which the middleware never counts as activity
      var headers = touch ? {} : { "X-Requested-With": "fetch" };
      return fetch(url, { credentials: "same-origin", cache: "no-store", redirect: "manual", headers: headers }).then(function (response) {
        var result = verdict(response);
        var left = parseInt(response.headers.get("X-Idle-Remaining") || "", 10);
        if (result === "alive" && !isNaN(left)) shareTouch(Date.now() - (staffIdle - left * 1000));
        return result;
      }, function () { return "offline"; });  // the request never reached the server
    };
    var inWords = function (ms) {
      var s = Math.max(1, Math.ceil(ms / 1000));
      if (s >= 90) return Math.round(s / 60) + " minutos";
      if (s >= 50) return "1 minuto";
      return Math.ceil(s / 10) * 10 + " segundos";
    };
    var TEXTS = {
      warn: ["¿Sigues aquí?", "", "Seguir aquí"],
      offline: ["Sin conexión", "No pudimos confirmar tu sesión con el servidor. Lo que escribiste sigue en esta pantalla: revisa la conexión y vuelve a intentar.", "Volver a intentar"],
      closed: ["Tu sesión se cerró", "Por seguridad cerramos tu sesión. Si escribiste algo que no se guardó, cópialo antes de volver a entrar.", "Volver a entrar"]
    };
    var setText = function (el, text) { if (el.textContent !== text) el.textContent = text; };
    var buildNotice = function () {
      var d = doc.createElement("dialog");
      d.className = "dialog";
      d.setAttribute("data-staff-idle", "");
      d.setAttribute("role", "alertdialog");
      d.setAttribute("aria-labelledby", "staff-idle-title");
      d.setAttribute("aria-describedby", "staff-idle-text");
      d.innerHTML = '<div class="dialog__body"><h2 id="staff-idle-title"></h2><p id="staff-idle-text"></p></div>' +
        '<div class="dialog__actions"><button class="btn btn--primary" type="button"></button></div>';
      $("button", d).addEventListener("click", function () {
        if (noticeState === "closed") { dirtyForms = []; window.location.assign(window.location.href); return; }  // to the login page
        d.close();
      });
      d.addEventListener("close", function () {  // the button or Escape; a quiet close has already cleared the state
        var was = noticeState;
        noticeState = "";
        if (was === "warn" || was === "offline") touch(true);
        if (noticeReturn && noticeReturn.focus && doc.contains(noticeReturn)) noticeReturn.focus();
        noticeReturn = null;
      });
      doc.body.appendChild(d);
      return d;
    };
    var show = function (kind, left) {
      if (!notice) notice = buildNotice();
      var t = TEXTS[kind];
      setText($("#staff-idle-title", notice), t[0]);
      setText($("#staff-idle-text", notice), kind === "warn" ?
        "Por seguridad, tu sesión se cerrará en " + inWords(left) + " si no hay actividad. Lo que no hayas guardado se perdería." : t[1]);
      setText($("button", notice), t[2]);
      noticeState = kind;
      if (notice.open || typeof notice.showModal !== "function") return;
      noticeReturn = doc.activeElement;
      notice.showModal();
      $("button", notice).focus();
    };
    var closeQuietly = function () {  // another tab kept the session open: no ping, no announcement
      if (!notice || !notice.open || noticeState !== "warn") return;
      noticeState = "";
      notice.close();
    };
    var touch = function (fromNotice) {
      var before = touched;
      lastPing = touched = Date.now();  // at once, so the notice does not come back while the answer is on its way
      return ask(true).then(function (result) {
        if (result === "alive") { shareTouch(lastPing); return; }
        touched = before;
        if (result === "closed") { ended = true; show("closed"); }
        else if (fromNotice) show("offline");  // typing offline says nothing now: the notice comes at its time
      });
    };
    var tick = function () {
      if (ended || checking) return;
      touched = Math.max(touched, sharedTouch());  // another tab's activity counts
      if (notice && notice.open && noticeState !== "warn") return;  // "Sin conexión" waits for the person
      var left = staffIdle - (Date.now() - touched);
      if (left > warnBefore) { closeQuietly(); return; }
      if (left > 0) { show("warn", left); return; }
      checking = true;  // the end: ask the server, without touching the session, before saying anything
      ask(false).then(function (result) {
        checking = false;
        if (result === "closed") { ended = true; show("closed"); }
        else if (result === "offline") show("offline");
        // else still open (another tab, or a second of rounding): the countdown moved; the next tick, a second from
        // now, looks again, so the server is asked at most once a second
      });
    };
    var typed = function (e) {
      var t = e.target;
      // Not while the notice is open: opening it takes the focus, and a text box then fires "change" by itself
      if (ended || (notice && notice.open) || !t || !t.closest || !t.closest("form")) return;
      if (Date.now() - Math.max(lastPing, touched, sharedTouch()) >= 120000) touch(false);
    };
    doc.addEventListener("input", typed);
    doc.addEventListener("change", typed);
    window.addEventListener("storage", function (e) { if (e.key === TOUCH_KEY) tick(); });
    window.setInterval(tick, 1000);
  }

  /* ---- Kiosk: idle lock and server heartbeat ----------------------------- */
  var idleSeconds = parseInt(body.getAttribute("data-kiosk-idle") || "0", 10);
  if (idleSeconds > 0) {
    var timeoutUrl = body.getAttribute("data-kiosk-timeout-url");
    var heartbeatUrl = body.getAttribute("data-kiosk-heartbeat-url");
    var warning = doc.getElementById("idle-warning");
    var warningReturn = null;
    var last = Date.now();
    var lastBeat = Date.now();
    function activity() {
      // While "¿Sigues aquí?" is open only its button or Escape answer it (audit A11Y-05): a key (Tab, an arrow, Shift),
      // the wheel or a touch elsewhere neither closes it nor restarts the clock, so a screen reader can read it.
      if (warning && warning.open) return;
      last = Date.now();
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
      if (warning && idle > idleSeconds - 60 && !warning.open && typeof warning.showModal === "function") {
        warningReturn = doc.activeElement;
        warning.showModal();
        var answer = $("[data-idle-continue]", warning);
        if (answer) answer.focus();
      }
      if (idle > idleSeconds) { dirtyForms = []; window.location.replace(timeoutUrl); }
    }, 5000);
    if (warning) {
      warning.setAttribute("role", "alertdialog");  // it interrupts: read at once, answered before anything else
      var warningText = $(".dialog__body p", warning);
      if (warningText) {
        if (!warningText.id) warningText.id = "idle-warning-text";
        warning.setAttribute("aria-describedby", warningText.id);
      }
      $all("[data-idle-continue]", warning).forEach(function (b) {
        b.addEventListener("click", function () { warning.close(); });  // Enter, Space, a click or a tap
      });
      warning.addEventListener("close", function () {  // the button or Escape: the only answers
        lastBeat = 0;
        activity();
        if (warningReturn && warningReturn.focus && doc.contains(warningReturn)) warningReturn.focus();
        warningReturn = null;
      });
    }
  }

  /* ---- Folded rows ("Mostrar los N cortes", the reports history) ---------- */
  $all("[data-run-toggle]").forEach(function (b) {
    b.addEventListener("click", function () {
      var open = b.getAttribute("aria-expanded") !== "true";
      b.setAttribute("aria-expanded", open ? "true" : "false");
      b.textContent = b.getAttribute(open ? "data-hide-label" : "data-show-label") || b.textContent;
      $all('[data-run="' + b.getAttribute("data-run-toggle") + '"]').forEach(function (row) { row.hidden = !open; });
    });
  });

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
      // Typing stays in a field. On a focused button or link, Space and Enter only press it (Space on "Modo noche"
      // must not also advance); the arrows, Av Pág/Re Pág, Inicio/Fin and "F" move the deck from there too.
      var target = e.target && e.target.closest ? e.target : null;
      if (target && target.closest("input, textarea, select, [contenteditable]")) return;
      if (target && (e.key === " " || e.key === "Enter") && target.closest("button, a, summary")) return;
      if (e.key === "Escape") { leave(); return; }
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
    // Escape goes back to where the presentation was opened from ("Salir", data-deck-exit). In full screen the
    // browser itself answers the first Escape by leaving full screen (and may or may not hand the key to the page),
    // so an Escape while full screen, or within a second of leaving it, only leaves full screen; the next one exits.
    var exitLink = deck.querySelector("[data-deck-exit]");
    var leftFullAt = 0;
    function leave() {
      if (doc.fullscreenElement || Date.now() - leftFullAt < 1000) return;
      if (exitLink) window.location.assign(exitLink.href);
    }
    $all("[data-deck-full]").forEach(function (b) { b.addEventListener("click", toggleFull); });
    // A pointer click on a toolbar button leaves the focus there: the deck (tabindex -1) takes it back, so a clicker's
    // keys (Space included) reach the slides. A button pressed from the keyboard (detail 0) keeps the focus, so
    // Enter can be pressed again on "Siguiente".
    $all("[data-deck-prev], [data-deck-next], [data-deck-full], [data-deck-theme]").forEach(function (b) {
      b.addEventListener("click", function (e) {
        if (e.detail === 0) return;
        deck.focus({ preventScroll: true });
      });
    });
    doc.addEventListener("fullscreenchange", function () {
      if (!doc.fullscreenElement) leftFullAt = Date.now();
      $all("[data-deck-full]").forEach(function (b) { b.textContent = doc.fullscreenElement ? "Salir de pantalla completa" : "Pantalla completa"; });
    });
    var idleTimer = null;
    function wake() {
      deck.classList.remove("is-idle");
      window.clearTimeout(idleTimer);
      idleTimer = window.setTimeout(function () { deck.classList.add("is-idle"); }, 3000);
    }
    // The bar hides 3 s after the pointer last moved and stays hidden while the presenter uses the keys: a clicker's
    // arrows must not bring it back over the slide. Moving the pointer or touching the screen shows it again, and
    // a control that has the focus keeps it visible (rotulo.css).
    doc.addEventListener("mousemove", wake);
    doc.addEventListener("touchstart", wake, { passive: true });
    wake();
    deck.addEventListener("emmaus:updated", function () { show(index); });

    // When the staff session ends, the live refresh stops and says so in the bar ("Live regions" above), but the
    // bar hides itself after 3 s. The slides then stop claiming "en vivo": every [data-deck-live] label gives way
    // to its [data-deck-ended] twin. The bar's message is the signal: state "stale" and a text about the session
    // (a dropped network is also "stale", but keeps polling and must not say the session ended).
    var liveStatus = doc.getElementById("deck-estado");
    if (liveStatus && window.MutationObserver) {
      new MutationObserver(function (records, observer) {
        if (liveStatus.getAttribute("data-state") !== "stale" || !/sesi[oó]n/i.test(liveStatus.textContent)) return;
        observer.disconnect();
        $all("[data-deck-live]", deck).forEach(function (el) { el.hidden = true; });
        $all("[data-deck-ended]", deck).forEach(function (el) { el.hidden = false; });
      }).observe(liveStatus, { attributes: true, childList: true, characterData: true, subtree: true });
    }

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

  /* ---- Offline guard for step forms ---------------------------------------- */
  // Without a network, "Continuar" lands on the browser's error page and the choice on screen is gone when the
  // person comes back. A form with a [data-offline-notice] inside is not sent while the browser knows it is
  // offline: the notice says so and the form stays as it is. Capture phase on the document, so this runs before
  // the double-submit guard above: a refused submit must not make the next tap look like a repeat.
  if ($("[data-offline-notice]")) {
    doc.addEventListener("submit", function (e) {
      if (navigator.onLine !== false) return;
      var notice = e.target && e.target.querySelector && e.target.querySelector("[data-offline-notice]");
      if (!notice) return;
      e.preventDefault();
      e.stopPropagation();
      notice.hidden = false;
    }, true);
    window.addEventListener("online", function () {
      $all("[data-offline-notice]").forEach(function (notice) { notice.hidden = true; });
    });
  }
})();
