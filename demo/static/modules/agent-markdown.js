// Render untrusted model prose from lexer tokens. Never parse or inject HTML.
// Large/unsupported parser outputs fall back to the unchanged source text.
export function renderAgentMarkdown(host, source, { lexer, t = (value) => value } = {}) {
  const text = typeof source === "string" ? source : "";
  const doc = host.ownerDocument;
  const plain = () => { host.replaceChildren(doc.createTextNode(text)); host.classList.remove("agent-markdown"); return false; };
  if (typeof lexer !== "function" || text.length > 128_000) return plain();
  let count = 0;
  const make = (tag, value) => {
    if (++count > 6000) throw new Error("markdown node limit");
    const element = doc.createElement(tag);
    if (value !== undefined) element.textContent = value;
    return element;
  };
  const literal = (parent, value) => {
    if (++count > 6000) throw new Error("markdown node limit");
    parent.appendChild(doc.createTextNode(typeof value === "string" ? value : ""));
  };
  // Marked escapes these characters inside inline code/escapes, but not fenced
  // code. Undo exactly one lexer-encoding layer; never parse the result as HTML.
  const unescapeLexer = (value) => String(value || "").replace(/&(amp|lt|gt|quot|#39);/g,
    (_, entity) => ({ amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" })[entity]);
  function link(href) {
    if (typeof href !== "string" || href.length > 4096 || !/^https?:\/\//i.test(href)
      || /[\u0000-\u0020\u007f]/.test(href)) return null;
    try {
      const url = new URL(href);
      return ["https:", "http:"].includes(url.protocol) && url.hostname && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }
  function append(parent, tokens, depth = 0) {
    if (!Array.isArray(tokens) || depth > 32) throw new Error("markdown token limit");
    for (const token of tokens) {
      if (!token || typeof token.type !== "string" || ++count > 6000) throw new Error("invalid markdown tokens");
      const children = (target) => token.tokens ? append(target, token.tokens, depth + 1) : literal(target, token.text || "");
      let element;
      switch (token.type) {
        case "space": break;
        case "heading":
          element = make("h" + Math.min(6, Math.max(3, Number(token.depth) + 2 || 3)));
          children(element); parent.appendChild(element); break;
        case "paragraph":
          element = make("p"); children(element); parent.appendChild(element); break;
        case "text":
          if (token.tokens) append(parent, token.tokens, depth + 1);
          else literal(parent, typeof token.raw === "string" ? token.raw : unescapeLexer(token.text));
          break;
        case "escape": literal(parent, unescapeLexer(token.text)); break;
        case "strong": case "em": case "del":
          element = make(token.type); children(element); parent.appendChild(element); break;
        case "codespan": parent.appendChild(make("code", unescapeLexer(token.text))); break;
        case "code":
          element = make("pre"); element.appendChild(make("code", token.text || "")); parent.appendChild(element); break;
        case "br": parent.appendChild(make("br")); break;
        case "hr": parent.appendChild(make("hr")); break;
        case "blockquote":
          element = make("blockquote"); children(element); parent.appendChild(element); break;
        case "list":
          element = make(token.ordered ? "ol" : "ul");
          if (token.ordered && Number.isSafeInteger(token.start) && token.start > 0) element.start = token.start;
          if (!Array.isArray(token.items)) throw new Error("invalid list");
          for (const item of token.items) {
            const li = make("li");
            if (item.task) literal(li, item.checked ? "☑ " : "☐ ");
            append(li, item.tokens, depth + 1); element.appendChild(li);
          }
          parent.appendChild(element); break;
        case "table": {
          const wrap = make("div"), table = make("table"), head = make("thead"), body = make("tbody");
          wrap.className = "agent-table-scroll"; wrap.tabIndex = 0; wrap.setAttribute("role", "region");
          wrap.setAttribute("aria-label", t("答案表格"));
          const row = (cells, heading) => {
            if (!Array.isArray(cells)) throw new Error("invalid table");
            const tr = make("tr");
            cells.forEach((cell, index) => {
              const td = make(heading ? "th" : "td");
              if (heading) td.scope = "col";
              if (["left", "center", "right"].includes(token.align?.[index])) td.style.textAlign = token.align[index];
              if (cell.tokens) append(td, cell.tokens, depth + 1); else literal(td, cell.text);
              tr.appendChild(td);
            });
            return tr;
          };
          head.appendChild(row(token.header, true));
          if (!Array.isArray(token.rows)) throw new Error("invalid table rows");
          for (const cells of token.rows) body.appendChild(row(cells, false));
          table.append(head, body); wrap.appendChild(table); parent.appendChild(wrap); break;
        }
        case "link": {
          const href = link(unescapeLexer(token.href));
          element = make(href ? "a" : "span"); children(element);
          if (href) {
            element.href = href; element.target = "_blank"; element.rel = "noopener noreferrer";
            element.referrerPolicy = "no-referrer";
          } else if (typeof token.href === "string") literal(element, " (" + token.href + ")");
          parent.appendChild(element); break;
        }
        case "image": parent.appendChild(make("span", token.text ? unescapeLexer(token.text) : t("图片未加载"))); break;
        // HTML and unknown extensions remain visible source, never live nodes.
        default: literal(parent, token.raw || token.text || "");
      }
    }
  }
  try {
    const fragment = doc.createDocumentFragment();
    append(fragment, lexer(text, { gfm: true, breaks: false }));
    host.replaceChildren(fragment); host.classList.add("agent-markdown"); return true;
  } catch (_) { return plain(); }
}
