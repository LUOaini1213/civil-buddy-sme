(() => {
const cbStudioText = (source, values = {}) => window.CBI18n?.t(source, values) ?? String(source || "").replace(/\{(\w+)\}/g, (match, key) => values[key] ?? match);
(() => {
  const $ = (id) => document.getElementById(id);
  let tree = null;
  let currentPath = "";
  let dirty = false;
  let selectedExpert = "";

  async function apiError(res) {
    const t = await res.text();
    try {
      const j = JSON.parse(t);
      if (typeof j.detail === "string") return j.detail;
      if (Array.isArray(j.detail)) return j.detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
    } catch (_) {}
    return t || res.statusText;
  }

  $("openStudio").addEventListener("click", () => openStudio());
  $("closeStudio").addEventListener("click", () => closeStudio());

  window.openStudio = async (path, expertId) => {
    $("studio").classList.remove("hidden");
    $("studio").setAttribute("aria-hidden", "false");
    await loadTree();
    if (expertId) fillExpert(expertId);
    if (path) await openFile(path);
  };

  window.studioOnCatalog = (cat) => {
    fillCatSelect(cat);
    if ($("softLimit") && cat.kb_soft_limit_kb) $("softLimit").value = cat.kb_soft_limit_kb;
  };

  function closeStudio() {
    if (dirty && !confirm("文件未保存，确定离开？")) return;
    $("studio").classList.add("hidden");
    $("studio").setAttribute("aria-hidden", "true");
    if (window.reloadCatalog) window.reloadCatalog();
  }

  async function loadTree() {
    tree = await fetch("/api/studio/tree").then((r) => r.json());
    $("studioTotal").textContent = `总库 ${tree.total_label} · 软上限 ${tree.kb_soft_limit_kb} KB / 本岗知识`;
    $("softLimit").value = tree.kb_soft_limit_kb;
    fillCatSelect({ categories: tree.categories });
    renderTree();
  }

  function fillCatSelect(cat) {
    const sel = $("expCat");
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = "";
    for (const c of cat.categories || []) {
      const o = document.createElement("option");
      o.value = c.id;
      o.textContent = c.name;
      o.title = c.id;
      sel.appendChild(o);
    }
    if (cur) sel.value = cur;
  }

  function renderTree() {
    const box = $("studioTree");
    box.innerHTML = "";
    box.appendChild(heading("公司规则（所有专家都能读）"));
    for (const f of tree.company.files || []) box.appendChild(fileBtn(f));

    for (const cat of tree.categories) {
      box.appendChild(heading(`${cat.name} · 大类共享 ${cat.shared.label}`));
      for (const f of cat.shared.files || []) box.appendChild(fileBtn(f));
      for (const exp of cat.experts) {
        const mark = exp.over_limit ? " over" : "";
        const h = document.createElement("button");
        h.type = "button";
        h.className = `tree-item${mark}`;
        h.textContent = `${exp.name} · 本岗知识 ${exp.label} · ${exp.count} 篇`;
        h.addEventListener("click", () => fillExpert(exp.id));
        box.appendChild(h);
        for (const f of exp.files || []) box.appendChild(fileBtn(f, exp.id));
      }
    }
  }

  function heading(text) {
    const d = document.createElement("div");
    d.className = "tree-h";
    d.textContent = text;
    return d;
  }

  function fmtBytes(n) {
    const x = Number(n) || 0;
    if (x < 1024) return `${x} B`;
    if (x < 1024 * 1024) return `${(x / 1024).toFixed(1)} KB`;
    return `${(x / (1024 * 1024)).toFixed(2)} MB`;
  }

  function fileBtn(f, expertId) {
    const path = f.path || "";
    const name = f.display || f.title || path.split("/").pop() || path;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "tree-item" + (path === currentPath ? " on" : "");
    b.textContent = `${name} · ${fmtBytes(f.bytes)} · ${f.chars || 0} 字`;
    b.title = path;
    b.addEventListener("click", () => {
      if (expertId) fillExpert(expertId);
      openFile(path);
    });
    return b;
  }

  function findExpert(id) {
    if (!tree) return null;
    for (const c of tree.categories) {
      const hit = (c.experts || []).find((e) => e.id === id);
      if (hit) return { ...hit, category: c.id };
    }
    return null;
  }

  function fillExpert(id) {
    const exp = findExpert(id);
    if (!exp) return;
    selectedExpert = id;
    const form = $("expertForm");
    form.id.value = exp.id;
    form.name.value = exp.name;
    form.category.value = exp.category;
    form.risk.value = exp.risk || "low";
    form.title.value = exp.title || "";
    form.delivers.value = exp.delivers || "";
    form.aliases.value = (exp.aliases || []).join(", ");
    form.id.readOnly = Boolean(exp.builtin);
  }

  async function openFile(path) {
    if (dirty && !confirm("当前编辑未保存，丢弃吗？")) return;
    const res = await fetch(`/api/studio/file?path=${encodeURIComponent(path)}`);
    if (!res.ok) {
      alert(await apiError(res));
      return;
    }
    const data = await res.json();
    currentPath = data.path;
    $("editor").value = data.content;
    const shown = data.display || data.title || data.path;
    $("filePath").textContent = data.display && data.path !== shown ? `${shown}  ·  ${data.path}` : data.path;
    $("fileSize").textContent = `${data.bytes} 字节 · ${data.chars} 字 · ${data.lines} 行`;
    $("saveFile").disabled = false;
    dirty = false;
    renderTree();
  }

  $("editor").addEventListener("input", () => {
    dirty = true;
    const t = $("editor").value;
    $("fileSize").textContent = `${new Blob([t]).size} 字节（未保存） · ${t.length} 字`;
  });

  $("saveFile").addEventListener("click", async () => {
    if (!currentPath) return;
    const res = await fetch("/api/studio/file", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: currentPath, content: $("editor").value }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      alert(typeof data.detail === "string" ? data.detail : "保存失败");
      return;
    }
    dirty = false;
    $("fileSize").textContent = `${data.bytes} 字节 · ${data.chars} 字 · 已保存`;
    await loadTree();
  });

  $("btnNewFile").addEventListener("click", async () => {
    const base = guessDir();
    const name = prompt("新文件名（.md 或 .txt）", "notes.md");
    if (!name) return;
    const path = `${base}/${name}`.replaceAll("//", "/");
    const res = await fetch("/api/studio/file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) {
      alert(await apiError(res));
      return;
    }
    await loadTree();
    await openFile(path);
  });

  $("btnDelFile").addEventListener("click", async () => {
    if (!currentPath) return;
    if (!confirm(`删除 ${currentPath}？`)) return;
    const res = await fetch(`/api/studio/file?path=${encodeURIComponent(currentPath)}`, { method: "DELETE" });
    if (!res.ok) {
      alert(await apiError(res));
      return;
    }
    currentPath = "";
    $("editor").value = "";
    $("filePath").textContent = cbStudioText("未打开文件");
    $("saveFile").disabled = true;
    dirty = false;
    await loadTree();
  });

  function guessDir() {
    if (currentPath && currentPath.includes("/")) return currentPath.split("/").slice(0, -1).join("/");
    if (selectedExpert) {
      const exp = findExpert(selectedExpert);
      if (exp) return `${exp.category}/${exp.id}`;
    }
    return "company";
  }

  $("expertForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target;
    const body = {
      id: f.id.value.trim(),
      name: f.name.value.trim(),
      category: f.category.value,
      title: f.title.value.trim(),
      delivers: f.delivers.value.trim(),
      risk: f.risk.value,
      aliases: f.aliases.value.trim(),
    };
    const res = await fetch("/api/studio/experts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      alert(await apiError(res));
      return;
    }
    selectedExpert = body.id;
    await loadTree();
    if (window.reloadCatalog) window.reloadCatalog();
    alert("专家已保存。左侧对话墙会立刻出现。");
  });

  $("btnDelExp").addEventListener("click", async () => {
    const id = $("expertForm").id.value.trim();
    if (!id) return;
    const exp = findExpert(id);
    const msg = exp && exp.builtin ? `内置专家 ${id} 将从召唤墙隐藏（可再保存恢复）。` : `删除自定义专家 ${id} 及其本岗知识？`;
    if (!confirm(msg)) return;
    const res = await fetch(`/api/studio/experts/${encodeURIComponent(id)}?delete_kb=true`, { method: "DELETE" });
    if (!res.ok) {
      alert(await apiError(res));
      return;
    }
    selectedExpert = "";
    await loadTree();
    if (window.reloadCatalog) window.reloadCatalog();
  });

  $("btnNewCat").addEventListener("click", async () => {
    const id = prompt("大类 id（英文，如 lab）");
    if (!id) return;
    const name = prompt("大类中文名", id);
    if (!name) return;
    const res = await fetch("/api/studio/categories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, name, blurb: "" }),
    });
    if (!res.ok) {
      alert(await apiError(res));
      return;
    }
    await loadTree();
  });

  $("btnNewExp").addEventListener("click", () => {
    const f = $("expertForm");
    f.id.readOnly = false;
    f.reset();
    selectedExpert = "";
    f.id.focus();
  });

  $("saveLimit").addEventListener("click", async () => {
    const kb = Number($("softLimit").value);
    const res = await fetch("/api/studio/limit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kb_soft_limit_kb: kb }),
    });
    if (!res.ok) {
      alert(await apiError(res));
      return;
    }
    await loadTree();
  });
})();

})();
