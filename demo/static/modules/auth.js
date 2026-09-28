const cbAuthText = (source, values = {}) => globalThis.CBI18n?.t(source, values) ?? String(source || "").replace(/\{(\w+)\}/g, (match, key) => values[key] ?? match);
/* Optional shared secret (CIVIL_TOKEN on the server, for CIVIL_HOST=0.0.0.0).
   Kept in a cookie so plain download links and uploads carry it too; asked for once — on
   boot when /api/health says auth is on, or on the first 401 — then the failed /api/ call is
   retried once. Pure of app state: hand it the window and document it should use. */
export const TOKEN_COOKIE = "cb_token";

export function createAuth({ win, doc, prompt } = {}) {
  const ask = prompt || ((message) => (win && typeof win.prompt === "function" ? win.prompt(message) : null));
  let promptOpen = false;
  let namedSession = false;

  function hasToken() {
    return namedSession || String(doc.cookie || "").split(";").some((c) => c.trim().startsWith(`${TOKEN_COOKIE}=`));
  }

  function setToken(tok) {
    const v = encodeURIComponent(String(tok || "").trim());
    doc.cookie = `${TOKEN_COOKIE}=${v}; path=/; max-age=${60 * 60 * 24 * 30}; SameSite=Lax`;
  }

  async function askToken(reason) {
    if (promptOpen) return false;
    promptOpen = true;
    try {
      const tok = ask(`${reason || "这个工作台需要访问口令"}（CIVIL_TOKEN）`);
      if (!tok) return false;
      setToken(tok);
      return true;
    } finally {
      promptOpen = false;
    }
  }

  /* Wrap win.fetch: a 401 from /api/ asks for the token once and retries the same request. */
  function installFetchGuard() {
    if (!win || typeof win.fetch !== "function" || win.fetch.cbGuarded) return false;
    const rawFetch = win.fetch.bind(win);
    const guarded = async function cbFetch(input, init) {
      const res = await rawFetch(input, init);
      const url = typeof input === "string" ? input : (input && input.url) || "";
      if (res.headers?.get("x-civil-identity-mode") === "named_single_user_instance" && res.status !== 401) namedSession = true;
      if (res.status === 401 && url.startsWith("/api/") && res.headers?.get("x-civil-login") === "/auth/login") {
        namedSession = false;
        win.location.assign("/auth/login");
        return res;
      }
      if (res.status === 401 && url.startsWith("/api/") && !(init && init.cbRetried)) {
        if (await askToken(cbAuthText("口令缺失或不对"))) return cbFetch(input, { ...(init || {}), cbRetried: true });
      }
      return res;
    };
    guarded.cbGuarded = true;
    win.fetch = guarded;
    return true;
  }

  return { hasToken, setToken, askToken, installFetchGuard };
}
