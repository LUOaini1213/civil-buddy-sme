/* A reverse proxy may combine several backends. Read their own explicit contracts. */
const ENTRY_PROBES = [
  ["agent_workbench", "/api/agent/capabilities", (body) => body?.available === true
    && Array.isArray(body.modes) && body.modes.includes("steps")
    && Array.isArray(body.sandbox) && body.sandbox.includes("read-only")
    && typeof body.features?.documents === "boolean"],
  ["packing_workbench", "/packing/api/health", (body) => body?.unified_packing?.available === true
    && body.unified_packing.page === "/packing" && body.unified_packing.agent_mode === "steps"],
  ["logistics", "/api/logistics/capabilities", (body) => body?.ok === true
    && Array.isArray(body.formats) && body.formats.includes("xlsx")
    && Array.isArray(body.packing_modes) && body.packing_modes.includes("packaged")],
];

export async function probeEntryCapabilities({ fetcher, timeoutMs = 1500 } = {}) {
  const rows = await Promise.all(ENTRY_PROBES.map(async ([name, path, accepts]) => {
    const controller = new AbortController();
    let timer;
    const timeout = new Promise((resolve) => {
      timer = setTimeout(() => { controller.abort(); resolve(false); }, timeoutMs);
    });
    const read = async () => {
      const response = await fetcher(path, { method: "GET", cache: "no-store", signal: controller.signal });
      if (response.status !== 200 || !response.headers?.get("content-type")?.toLowerCase().includes("application/json")) return false;
      const length = Number(response.headers.get("content-length") || 0);
      if (!Number.isFinite(length) || length < 0 || length > 65536) return false;
      const body = await response.json();
      if (JSON.stringify(body).length > 65536) return false;
      return accepts(body);
    };
    try {
      return [name, await Promise.race([read().catch(() => false), timeout])];
    } finally { clearTimeout(timer); controller.abort(); }
  }));
  return Object.fromEntries(rows);
}

/* Pure painting: no network, credentials, task execution or authorisation change. */
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
