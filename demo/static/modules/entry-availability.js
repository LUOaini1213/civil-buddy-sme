/* Shared homepage != shared backend. Only an explicit route capability enables a link. */
export function applyEntryAvailability({ doc, capabilities = {}, t = (s) => s } = {}) {
  const unavailable = new Set();
  for (const link of doc.querySelectorAll("[data-cb-entry][data-cb-capability]")) {
    const enabled = capabilities[link.dataset.cbCapability] === true;
    link.setAttribute("role", "link");
    if (enabled) {
      link.setAttribute("href", link.dataset.cbEntry);
      link.removeAttribute("aria-disabled");
      link.removeAttribute("tabindex");
      link.removeAttribute("title");
    } else {
      link.removeAttribute("href");
      link.setAttribute("aria-disabled", "true");
      link.setAttribute("tabindex", "-1");
      link.setAttribute("title", t("本实例未开放此入口；需部署对应后端服务。"));
      unavailable.add(t(link.dataset.cbEntryName || link.textContent.trim()));
    }
  }
  const status = doc.getElementById("cbEntryAvailability");
  if (status) {
    status.hidden = unavailable.size === 0;
    status.textContent = unavailable.size
      ? t("本实例未开放：{entries}。可继续使用已开放的岗位任务。", { entries: [...unavailable].join(" / ") })
      : "";
  }
}
