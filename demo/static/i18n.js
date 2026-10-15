/* Product language preferences. Source documents and editable values are never translated. */
(function () {
  "use strict";
  const KEY = "cb_locale_v1";
  const catalog = Object.assign(Object.create(null), {
    "工作台": "Workbench", "工作台导航": "Workbench navigation", "Agent 工作台": "Agent workbench",
    "项目资料与问题": "Project records & issues",
    "装箱拼柜": "Packing", "工程计算": "Engineering", "施工计划": "Construction plan",
    "日期计划": "Schedule", "现场路线": "Site routes", "箱单与装运": "Materials & shipping",
    "切换主题": "Change theme", "浅色": "Light", "深色": "Dark",
    "切换为浅色主题": "Switch to light theme", "切换为深色主题": "Switch to dark theme",
    "刷新": "Refresh", "取消": "Cancel", "关闭": "Close", "保存": "Save", "删除": "Delete",
    "打开": "Open", "下载": "Download", "导出": "Export", "返回": "Back", "设置": "Settings",
    "访问令牌": "Access token", "登录": "Sign in", "Civil Buddy 登录": "Civil Buddy sign in",
    "每位成员使用独立实例与资料目录。请输入管理员提供的访问令牌。": "Each member uses a separate instance and workspace. Enter the access token provided by your administrator."
  });
  const normalize = (value) => value === "en" || value === "en-US" || value === "en-GB" ? "en" : "zh-CN";
  const read = () => { try { return localStorage.getItem(KEY); } catch (_) { return null; } };
  let locale = normalize(read()), ready = false;
  const bindings = [];
  const ignored = "script,style,textarea,pre,code,[contenteditable],[data-cb-no-i18n],[data-i18n-skip],[data-i18n-dynamic]";
  function t(source, parameters) {
    const key = String(source == null ? "" : source);
    const template = locale === "en" && Object.hasOwn(catalog, key) ? catalog[key] : key;
    return template.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (match, name) =>
      parameters && Object.hasOwn(parameters, name) ? String(parameters[name]) : match);
  }
  function captureStatic() {
    const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.parentElement || node.parentElement.closest(ignored) || !node.nodeValue.trim()) continue;
      const raw = node.nodeValue, source = raw.trim();
      bindings.push({ node, source, before: raw.slice(0, raw.indexOf(source)), after: raw.slice(raw.indexOf(source) + source.length), last: raw });
    }
    document.querySelectorAll("[placeholder],[title],[aria-label],[data-i18n-title]").forEach((node) => {
      if (node.closest("[data-cb-no-i18n],[data-i18n-skip],[data-i18n-dynamic]") || node.id === "agentRiskConfirmation") return;
      for (const attr of ["placeholder", "title", "aria-label"]) {
        if (node.hasAttribute(attr)) bindings.push({ node, attr, source: node.getAttribute(attr), last: node.getAttribute(attr), before: "", after: "" });
      }
    });
  }
  function render() {
    document.documentElement.lang = locale;
    bindings.forEach((item) => {
      if (!item.node.isConnected) return;
      const current = item.attr ? item.node.getAttribute(item.attr) : item.node.nodeValue;
      // A controller has replaced this UI node: it now owns the value, not the static translator.
      if (current !== item.last) return;
      item.last = item.before + t(item.source) + item.after;
      if (item.attr) item.node.setAttribute(item.attr, item.last); else item.node.nodeValue = item.last;
    });
    document.querySelectorAll("[data-cb-language-toggle]").forEach((button) => {
      button.textContent = locale === "en" ? "中文" : "English";
      button.setAttribute("aria-label", locale === "en" ? "切换为中文" : "Switch to English");
      button.setAttribute("title", locale === "en" ? "切换为中文" : "Switch to English");
    });
  }
  function setLocale(value, persist = true) {
    const next = normalize(value), changed = next !== locale;
    locale = next;
    if (persist) { try { localStorage.setItem(KEY, locale); } catch (_) { /* Optional storage. */ } }
    render();
    if (changed) window.dispatchEvent(new CustomEvent("cb:languagechange", { detail: { locale } }));
  }
  window.CBI18n = {
    get locale() { return locale; }, t, setLocale,
    add(messages) { Object.assign(catalog, messages); if (ready) render(); },
    // Explicit app-owned text binding; never use this on source excerpts or user input.
    text(node, source, parameters) { node.textContent = t(source, parameters); return node; }
  };
  document.documentElement.lang = locale;
  document.addEventListener("DOMContentLoaded", () => {
    captureStatic(); ready = true;
    if (!document.querySelector("[data-cb-language-toggle]")) {
      const button = document.createElement("button");
      button.type = "button"; button.setAttribute("data-cb-language-toggle", "");
      button.className = "cb-language-toggle";
      const host = document.querySelector(".cb-app-actions, .cb-top-actions, header .actions")
        || document.querySelector("header, .topbar, .header");
      if (host) host.append(button);
      else { button.classList.add("cb-language-floating"); document.body.prepend(button); }
    }
    document.querySelectorAll("[data-cb-language-toggle]").forEach((button) =>
      button.addEventListener("click", () => setLocale(locale === "en" ? "zh-CN" : "en")));
    render();
  });
  window.addEventListener("storage", (event) => { if (event.key === KEY) setLocale(read(), false); });
})();
