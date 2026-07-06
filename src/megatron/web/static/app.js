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
  function showError(target, message) {
    var el = typeof target === "string" ? document.getElementById(target) : target;
    if (!el) return;
    el.textContent = "";
    var box = document.createElement("div");
    box.className = "alert alert-error py-2";
    box.setAttribute("role", "alert");
    var span = document.createElement("span");
    span.className = "text-sm";
    span.textContent = message;
    box.appendChild(span);
    el.appendChild(box);
  }

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

  document.addEventListener("DOMContentLoaded", function () {
    renderMarkdown();
    pollWhileActive(5);
  });

  window.MG = {
    escapeHtml: escapeHtml,
    apiFetch: apiFetch,
    apiSend: apiSend,
    errorText: errorText,
    showError: showError,
    toast: toast,
    renderMarkdown: renderMarkdown,
    pollWhileActive: pollWhileActive,
  };
})();
