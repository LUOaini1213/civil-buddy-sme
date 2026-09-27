from __future__ import annotations

import json
from pathlib import Path

from catalog_seed import CATEGORIES as SEED_CATEGORIES
from catalog_seed import EXPERTS as SEED_EXPERTS
from catalog_seed import Expert
from config import DATA_ROOT
from kbio import ensure_expert_kb, ensure_kb_root, folder_stats, format_bytes, valid_id

DATA = DATA_ROOT / "user_catalog.json"


def _empty_user() -> dict:
    return {
        "categories": [],
        "experts": [],
        "patches": {},
        "disabled": [],
        "kb_soft_limit_kb": 200,
    }


def load_user() -> dict:
    if not DATA.is_file():
        return _empty_user()
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    base = _empty_user()
    base.update({k: raw[k] for k in base if k in raw})
    return base


def save_user(data: dict) -> None:
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_by_id() -> dict[str, Expert]:
    return {e.id: e for e in SEED_EXPERTS}


def all_categories() -> list[dict]:
    user = load_user()
    seen = {c["id"]: dict(c) for c in SEED_CATEGORIES}
    for extra in user.get("categories") or []:
        cid = extra.get("id")
        if not cid:
            continue
        if cid in seen:
            seen[cid] = {**seen[cid], **extra, "builtin": seen[cid].get("builtin", True)}
        else:
            seen[cid] = {
                "id": cid,
                "name": extra.get("name") or cid,
                "blurb": extra.get("blurb") or "",
                "builtin": False,
            }
    return list(seen.values())


def _cat_name(cat_id: str) -> str:
    for c in all_categories():
        if c["id"] == cat_id:
            return c["name"]
    return cat_id


def all_experts() -> list[Expert]:
    user = load_user()
    disabled = set(user.get("disabled") or [])
    patches = user.get("patches") or {}
    out: list[Expert] = []
    for seed in SEED_EXPERTS:
        if seed.id in disabled:
            continue
        patch = patches.get(seed.id) or {}
        out.append(_apply(seed, patch, builtin=True))
    for raw in user.get("experts") or []:
        eid = raw.get("id")
        if not eid or eid in disabled or any(e.id == eid for e in out):
            continue
        out.append(_from_dict(raw, builtin=False))
    return out


def get_expert(expert_id: str) -> Expert | None:
    for exp in all_experts():
        if exp.id == expert_id:
            return exp
    return None


def catalog_payload() -> dict:
    ensure_kb_root()
    user = load_user()
    limit = int(user.get("kb_soft_limit_kb") or 200)
    experts = []
    for exp in all_experts():
        st = folder_stats(_kb_private(exp.category, exp.id), f"{exp.category}/{exp.id}", "expert")
        shared = folder_stats(_kb_shared(exp.category), f"{exp.category}/_shared", "category")
        row = exp.to_dict()
        row["kb_bytes"] = st["bytes"]
        row["kb_count"] = st["count"]
        row["kb_label"] = format_bytes(st["bytes"])
        row["shared_bytes"] = shared["bytes"]
        row["over_limit"] = st["bytes"] > limit * 1024
        experts.append(row)
    from config import KB_ROOT

    company = folder_stats(KB_ROOT / "company", "company", "company")
    return {
        "mode_plain": "未选用 skill = 土木版 Codex 路由器，无本岗库、无出稿工具",
        "mode_expert": "选用 skill 后该岗独立完成（理解→检索本库+大类库→成稿→自检）",
        "categories": all_categories(),
        "experts": experts,
        "kb_soft_limit_kb": limit,
        "company_kb": {"bytes": company["bytes"], "count": company["count"], "label": format_bytes(company["bytes"])},
        "max_file_bytes": 512 * 1024,
    }


def resolve_mentions(text: str) -> list[str]:
    """Only explicit @name / 召唤name. Bare words like 施工 must not auto-summon."""
    found: list[str] = []
    blob = text or ""
    if not blob.strip():
        return found
    for exp in all_experts():
        labels = [exp.id, exp.name, *exp.aliases]
        hit = False
        for lab in labels:
            if not lab:
                continue
            if f"@{lab}" in blob or f"召唤{lab}" in blob:
                hit = True
                break
        if hit and exp.id not in found:
            found.append(exp.id)
    return found


def upsert_category(cid: str, name: str, blurb: str) -> dict:
    if not valid_id(cid):
        raise ValueError("大类 id：小写字母开头，字母数字连字符，2–32 位")
    user = load_user()
    cats = [c for c in user["categories"] if c.get("id") != cid]
    cats.append({"id": cid, "name": name.strip() or cid, "blurb": blurb.strip(), "builtin": False})
    user["categories"] = cats
    save_user(user)
    from config import KB_ROOT

    ensure_expert_kb(cid, "_placeholder", "placeholder")
    placeholder = KB_ROOT / cid / "_placeholder"
    if placeholder.exists():
        import shutil

        shutil.rmtree(placeholder)
    return {"id": cid, "name": name, "blurb": blurb}


def upsert_expert(payload: dict) -> Expert:
    eid = (payload.get("id") or "").strip()
    if not valid_id(eid):
        raise ValueError("专家 id：小写字母开头，字母数字连字符，2–32 位")
    cat = (payload.get("category") or "").strip()
    if not any(c["id"] == cat for c in all_categories()):
        raise ValueError("未知大类，请先建大类")
    name = (payload.get("name") or eid).strip()
    data = {
        "id": eid,
        "name": name,
        "category": cat,
        "category_name": _cat_name(cat),
        "title": (payload.get("title") or "").strip() or name,
        "delivers": (payload.get("delivers") or "").strip() or "独立成稿",
        "risk": "high" if payload.get("risk") == "high" else "low",
        "aliases": _aliases(payload.get("aliases")),
        "pipeline": (payload.get("pipeline") or Expert.default_pipeline()).strip(),
        "builtin": False,
        "enabled": True,
    }
    user = load_user()
    if any(s.id == eid for s in SEED_EXPERTS):
        patch = {k: data[k] for k in ("name", "title", "delivers", "risk", "aliases", "pipeline", "category", "category_name")}
        user["patches"][eid] = patch
        if eid in user["disabled"]:
            user["disabled"].remove(eid)
    else:
        user["experts"] = [e for e in user["experts"] if e.get("id") != eid]
        user["experts"].append(data)
    save_user(user)
    ensure_kb_root()
    ensure_expert_kb(cat, eid, name)
    return get_expert(eid)


def disable_or_delete_expert(eid: str, *, delete_kb: bool) -> None:
    user = load_user()
    if any(s.id == eid for s in SEED_EXPERTS):
        if eid not in user["disabled"]:
            user["disabled"].append(eid)
        save_user(user)
        return
    before = [e for e in user["experts"] if e.get("id") == eid]
    user["experts"] = [e for e in user["experts"] if e.get("id") != eid]
    save_user(user)
    if delete_kb and before:
        remove = before[0]
        from kbio import remove_expert_kb

        remove_expert_kb(remove.get("category") or "", eid)


def set_soft_limit(kb: int) -> int:
    kb = max(8, min(int(kb), 8192))
    user = load_user()
    user["kb_soft_limit_kb"] = kb
    save_user(user)
    return kb


def tree_payload() -> dict:
    ensure_kb_root()
    from config import KB_ROOT

    limit = int(load_user().get("kb_soft_limit_kb") or 200)
    company = folder_stats(KB_ROOT / "company", "company", "company")
    cats = []
    total = company["bytes"]
    for cat in all_categories():
        shared = folder_stats(KB_ROOT / cat["id"] / "_shared", f"{cat['id']}/_shared", "category")
        members = []
        for exp in all_experts():
            if exp.category != cat["id"]:
                continue
            priv = folder_stats(KB_ROOT / cat["id"] / exp.id, f"{cat['id']}/{exp.id}", "expert")
            members.append(
                {
                    "id": exp.id,
                    "name": exp.name,
                    "builtin": exp.builtin,
                    "risk": exp.risk,
                    "title": exp.title,
                    "delivers": exp.delivers,
                    "aliases": list(exp.aliases),
                    "pipeline": exp.pipeline,
                    **priv,
                    "label": format_bytes(priv["bytes"]),
                    "over_limit": priv["bytes"] > limit * 1024,
                }
            )
            total += priv["bytes"]
        total += shared["bytes"]
        cats.append(
            {
                **cat,
                "shared": {**shared, "label": format_bytes(shared["bytes"])},
                "experts": members,
            }
        )
    return {
        "company": {**company, "label": format_bytes(company["bytes"])},
        "categories": cats,
        "total_bytes": total,
        "total_label": format_bytes(total),
        "kb_soft_limit_kb": limit,
        "max_file_bytes": 512 * 1024,
    }


def _kb_private(category: str, eid: str) -> Path:
    from config import KB_ROOT

    return KB_ROOT / category / eid


def _kb_shared(category: str) -> Path:
    from config import KB_ROOT

    return KB_ROOT / category / "_shared"


def _aliases(value) -> tuple[str, ...]:
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("，", ",").split(",") if p.strip()]
        return tuple(parts)
    if isinstance(value, (list, tuple)):
        return tuple(str(x).strip() for x in value if str(x).strip())
    return ()


def _apply(seed: Expert, patch: dict, builtin: bool) -> Expert:
    data = seed.to_dict()
    data.update({k: patch[k] for k in patch if k in data})
    if "aliases" in patch:
        data["aliases"] = _aliases(patch["aliases"])
    data["builtin"] = builtin
    return _from_dict(data, builtin=builtin)


def _from_dict(raw: dict, builtin: bool) -> Expert:
    return Expert(
        id=raw["id"],
        name=raw.get("name") or raw["id"],
        category=raw.get("category") or "construction",
        category_name=raw.get("category_name") or _cat_name(raw.get("category") or "construction"),
        title=raw.get("title") or "",
        delivers=raw.get("delivers") or "",
        risk="high" if raw.get("risk") == "high" else "low",
        aliases=_aliases(raw.get("aliases")),
        pipeline=raw.get("pipeline") or Expert.default_pipeline(),
        builtin=builtin,
        enabled=raw.get("enabled", True),
    )
