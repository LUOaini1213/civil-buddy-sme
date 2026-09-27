/* Shared appearance preferences. Apply before CSS so page navigation never flashes. */
(function () {
  "use strict";
  var root = document.documentElement;
  var THEME_KEY = "cb_theme_v1", LARGE_KEY = "cb_large_v1";
  function read(key) { try { return localStorage.getItem(key); } catch (_) { return null; } }
  function syncButtons() {
    var dark = root.getAttribute("data-theme") === "dark";
    document.querySelectorAll("[data-cb-theme-toggle]").forEach(function (button) {
      button.textContent = dark ? "浅色" : "深色";
      button.setAttribute("aria-label", dark ? "切换为浅色主题" : "切换为深色主题");
    });
  }
  window.cbApplyTheme = function (theme) {
    var dark = theme !== "light";
    root.setAttribute("data-theme", dark ? "dark" : "light");
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", dark ? "#17181a" : "#f8fafc");
    syncButtons();
  };
  window.cbThemeSaved = function () { return read(THEME_KEY) || ""; };
  window.cbLargeSaved = function () { return read(LARGE_KEY) === "1"; };
  window.cbApplyTheme(read(THEME_KEY));
  root.classList.toggle("cb-large", window.cbLargeSaved());
  document.addEventListener("DOMContentLoaded", function () {
    syncButtons();
    document.querySelectorAll("[data-cb-theme-toggle]").forEach(function (button) {
      button.addEventListener("click", function () {
        var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
        try { localStorage.setItem(THEME_KEY, next); } catch (_) {}
        window.cbApplyTheme(next);
      });
    });
    // The home page has fixed drawers; follow the actual two-row header height.
    var header = document.querySelector(".cb-app-header");
    if (header && window.ResizeObserver) {
      new ResizeObserver(function () {
        root.style.setProperty("--cb-topbar-h", Math.ceil(header.getBoundingClientRect().height) + "px");
      }).observe(header);
    }
  });
  window.addEventListener("storage", function (event) {
    if (event.key === THEME_KEY) window.cbApplyTheme(read(THEME_KEY));
    if (event.key === LARGE_KEY) root.classList.toggle("cb-large", window.cbLargeSaved());
  });
})();
