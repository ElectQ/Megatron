// Megatron shared UI helpers, loaded on every authenticated page.
// The signed session cookie authenticates /api/admin/* calls, so no bearer
// token is embedded in the page (which also keeps the cookie HttpOnly-safe).
(function () {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Same-origin fetch with cookie credentials; sets JSON content-type when a body is present.
  function apiFetch(url, options) {
    options = options || {};
    var headers = Object.assign({}, options.headers);
    if (options.body != null && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    return fetch(url, Object.assign({ credentials: "same-origin" }, options, { headers: headers }));
  }

  // POST/PUT a plain object as JSON.
  function apiSend(url, method, data) {
    return apiFetch(url, { method: method, body: data == null ? undefined : JSON.stringify(data) });
  }

  // Extract a human-readable message from a failed Response without trusting it as HTML.
  async function errorText(response) {
    try {
      var data = await response.clone().json();
      if (data && (data.detail || data.error)) return String(data.detail || data.error);
    } catch (e) { /* body was not JSON */ }
    try { return (await response.text()) || ("HTTP " + response.status); }
    catch (e) { return "HTTP " + response.status; }
  }

  // Render an error alert into `target` (id or element) using textContent — no XSS from server text.
  function showAlert(target, message, kind) {
    var el = typeof target === "string" ? document.getElementById(target) : target;
    if (!el) return;
    el.textContent = "";
    var box = document.createElement("div");
    box.className = "alert alert-" + (kind || "error") + " py-2";
    box.setAttribute("role", kind === "error" ? "alert" : "status");
    var span = document.createElement("span");
    span.className = "text-sm";
    span.textContent = message;
    box.appendChild(span);
    el.appendChild(box);
  }
  function showError(target, message) { showAlert(target, message, "error"); }
  function showInfo(target, message) { showAlert(target, message, "info"); }
  function showSuccess(target, message) { showAlert(target, message, "success"); }

  // Non-blocking toast notification.
  function toast(message, kind) {
    var host = document.getElementById("mg-toast-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "mg-toast-host";
      document.body.appendChild(host);
    }
    var t = document.createElement("div");
    t.className = "mg-toast alert alert-" + (kind || "info");
    t.setAttribute("role", "status");
    t.textContent = message;
    host.appendChild(t);
    setTimeout(function () { t.classList.add("mg-toast-hide"); }, 3200);
    setTimeout(function () { t.remove(); }, 3600);
  }

  // Render sanitized markdown for each [data-md-src] element, pulling raw text
  // from the element whose id it names. Falls back to escaped text if libs are absent.
  function renderMarkdown() {
    document.querySelectorAll("[data-md-src]").forEach(function (el) {
      var src = document.getElementById(el.getAttribute("data-md-src"));
      if (!src) return;
      var raw = src.textContent || "";
      if (typeof marked === "undefined") { el.textContent = raw; return; }
      var html = marked.parse(raw);
      el.innerHTML = (typeof DOMPurify !== "undefined") ? DOMPurify.sanitize(html) : escapeHtml(raw);
    });
  }

  // Reload while any [data-poll-active] element is present (work in progress),
  // but only when the tab is visible so background tabs stay quiet.
  function pollWhileActive(seconds) {
    if (!document.querySelector("[data-poll-active]")) return;
    setTimeout(function () {
      if (document.visibilityState === "visible") location.reload();
      else pollWhileActive(seconds);
    }, (seconds || 5) * 1000);
  }

  // ---- Localized date picker -------------------------------------------
  // Native <input type=date> ignores the page language in Chromium (it follows
  // the browser locale), so we render our own. Display follows document.lang
  // via Intl; the value is stored ISO (YYYY-MM-DD) in a hidden input named for
  // the field, so server forms are unchanged. Enhance any <div class="mg-date"
  // data-name data-value>.
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function toISO(y, m, d) { return y + "-" + pad2(m + 1) + "-" + pad2(d); }
  function parseISO(s) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s || "")) return null;
    var p = s.split("-");
    return { y: +p[0], m: +p[1] - 1, d: +p[2] };
  }

  function closeAllCalendars() {
    document.querySelectorAll(".mg-cal").forEach(function (p) { p.classList.add("hidden"); });
  }

  function initDatePickers(root) {
    var lang = document.documentElement.lang || "en";
    var zh = lang.indexOf("zh") === 0;
    var locale = zh ? "zh-CN" : "en-US";
    var dispFmt = new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric" });
    var titleFmt = new Intl.DateTimeFormat(locale, { year: "numeric", month: "long" });
    var wdFmt = new Intl.DateTimeFormat(locale, { weekday: "narrow" });
    var placeholder = zh ? "选择日期" : "Select date";
    var todayLabel = zh ? "今天" : "Today";
    var clearLabel = zh ? "清除" : "Clear";
    var weekdays = [];
    for (var i = 0; i < 7; i++) weekdays.push(wdFmt.format(new Date(2023, 0, 1 + i))); // Jan 1 2023 = Sunday
    var now = new Date();
    var today = { y: now.getFullYear(), m: now.getMonth(), d: now.getDate() };

    (root || document).querySelectorAll(".mg-date").forEach(function (box) {
      if (box.dataset.enhanced) return;
      box.dataset.enhanced = "1";

      var hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = box.dataset.name;
      hidden.value = box.dataset.value || "";

      var field = document.createElement("button");
      field.type = "button";
      field.className = "input input-bordered input-sm w-full text-left mg-date-field";
      field.setAttribute("aria-label", box.dataset.label || placeholder);

      var panel = document.createElement("div");
      panel.className = "mg-cal hidden";

      box.appendChild(hidden);
      box.appendChild(field);
      box.appendChild(panel);

      var view;

      function setDisplay() {
        var p = parseISO(hidden.value);
        field.textContent = p ? dispFmt.format(new Date(p.y, p.m, p.d)) : placeholder;
        field.classList.toggle("is-placeholder", !p);
      }

      function render() {
        var first = new Date(view.y, view.m, 1);
        var startWd = first.getDay();
        var dim = new Date(view.y, view.m + 1, 0).getDate();
        var sel = parseISO(hidden.value);
        var html = '<div class="mg-cal-head">'
          + '<button type="button" class="mg-cal-nav" data-nav="-1" aria-label="prev">‹</button>'
          + '<span class="mg-cal-title">' + titleFmt.format(first) + "</span>"
          + '<button type="button" class="mg-cal-nav" data-nav="1" aria-label="next">›</button></div>'
          + '<div class="mg-cal-grid">';
        weekdays.forEach(function (w) { html += '<span class="mg-cal-wd">' + w + "</span>"; });
        for (var b = 0; b < startWd; b++) html += "<span></span>";
        for (var d = 1; d <= dim; d++) {
          var isSel = sel && sel.y === view.y && sel.m === view.m && sel.d === d;
          var isToday = today.y === view.y && today.m === view.m && today.d === d;
          html += '<button type="button" class="mg-cal-day'
            + (isSel ? " is-selected" : "") + (isToday ? " is-today" : "")
            + '" data-day="' + d + '">' + d + "</button>";
        }
        html += "</div><div class=\"mg-cal-foot\">"
          + '<button type="button" class="mg-cal-btn" data-act="today">' + todayLabel + "</button>"
          + '<button type="button" class="mg-cal-btn" data-act="clear">' + clearLabel + "</button></div>";
        panel.innerHTML = html;
      }

      field.addEventListener("click", function (e) {
        e.stopPropagation();
        var wasOpen = !panel.classList.contains("hidden");
        closeAllCalendars();
        if (wasOpen) return;
        var p = parseISO(hidden.value) || today;
        view = { y: p.y, m: p.m };
        render();
        panel.classList.remove("hidden");
      });

      panel.addEventListener("click", function (e) {
        e.stopPropagation();
        var btn = e.target.closest("button");
        if (!btn) return;
        if (btn.dataset.nav) {
          view.m += +btn.dataset.nav;
          if (view.m < 0) { view.m = 11; view.y--; }
          if (view.m > 11) { view.m = 0; view.y++; }
          render();
        } else if (btn.dataset.day) {
          hidden.value = toISO(view.y, view.m, +btn.dataset.day);
          setDisplay();
          panel.classList.add("hidden");
          hidden.dispatchEvent(new Event("change", { bubbles: true }));
        } else if (btn.dataset.act === "today") {
          hidden.value = toISO(today.y, today.m, today.d);
          setDisplay();
          panel.classList.add("hidden");
        } else if (btn.dataset.act === "clear") {
          hidden.value = "";
          setDisplay();
          panel.classList.add("hidden");
        }
      });

      setDisplay();
    });
  }

  document.addEventListener("click", closeAllCalendars);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeAllCalendars(); });

  // ---- Cron humanizer --------------------------------------------------
  // Turn common 5-field cron expressions into readable, language-aware text.
  // Uncommon expressions keep their raw form. The raw cron is always retained
  // as the title (tooltip).
  function humanizeCron(expr, lang) {
    var raw = (expr || "").trim();
    var f = raw.split(/\s+/);
    if (f.length !== 5) return null;
    var zh = lang.indexOf("zh") === 0;
    var min = f[0], hr = f[1], dom = f[2], mon = f[3], dow = f[4];
    var isNum = function (s) { return /^\d+$/.test(s); };
    var hhmm = function (h, m) { return ("0" + h).slice(-2) + ":" + ("0" + m).slice(-2); };
    var wd = zh ? ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
                : ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    var mm;
    if (min === "*" && hr === "*" && dom === "*" && mon === "*" && dow === "*") return zh ? "每分钟" : "Every minute";
    if ((mm = /^\*\/(\d+)$/.exec(min)) && hr === "*" && dom === "*" && mon === "*" && dow === "*")
      return zh ? ("每 " + mm[1] + " 分钟") : ("Every " + mm[1] + " min");
    if (min === "0" && (mm = /^\*\/(\d+)$/.exec(hr)) && dom === "*" && mon === "*" && dow === "*")
      return zh ? ("每 " + mm[1] + " 小时") : ("Every " + mm[1] + "h");
    if (isNum(min) && isNum(hr)) {
      var t = hhmm(+hr, +min);
      if (dom === "*" && mon === "*" && dow === "*") return zh ? ("每天 " + t) : ("Daily " + t);
      if (dom === "*" && mon === "*" && isNum(dow) && +dow <= 6) return zh ? ("每" + wd[+dow] + " " + t) : ("Weekly " + wd[+dow] + " " + t);
      if (isNum(dom) && mon === "*" && dow === "*") return zh ? ("每月 " + (+dom) + " 日 " + t) : ("Monthly day " + (+dom) + " " + t);
    }
    return null;
  }

  function initCron(root) {
    var lang = document.documentElement.lang || "en";
    (root || document).querySelectorAll("[data-cron]").forEach(function (el) {
      var raw = el.getAttribute("data-cron");
      if (!raw) return;
      var human = humanizeCron(raw, lang);
      if (human) { el.textContent = human; el.title = raw; }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    renderMarkdown();
    pollWhileActive(5);
    initDatePickers();
    initCron();
  });

  window.MG = {
    escapeHtml: escapeHtml,
    apiFetch: apiFetch,
    apiSend: apiSend,
    errorText: errorText,
    showError: showError,
    showInfo: showInfo,
    showSuccess: showSuccess,
    toast: toast,
    renderMarkdown: renderMarkdown,
    pollWhileActive: pollWhileActive,
    initDatePickers: initDatePickers,
    initCron: initCron,
  };
})();
