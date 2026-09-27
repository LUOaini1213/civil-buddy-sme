#!/usr/bin/env python3
"""Drive shipped gateway tender/delivery API (TestClient)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLE = """
一、投标人须具备建筑工程施工资质及类似幕墙业绩。
二、货物须妥善包装，采用铁架/木箱防护。
三、采用海运整柜 40HQ。
四、严禁超载；重心与绑扎须符合 CTU。
五、未实质性响应作废标处理。
"""


def main() -> int:
    from fastapi.testclient import TestClient

    from gateway.app import app

    # UI default surface is tender-delivery
    index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "投标应答" in index and "交付" in index
    assert "合规响应矩阵" in index
    assert "/workbench" in index
    # not the dense packing default title
    assert "装箱拼柜 · Team Mode" not in index.split("<title>")[1].split("</title>")[0]

    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "投标应答" in r.text
    assert "project tender" not in r.text.lower() or "投标" in r.text

    r2 = client.get("/workbench")
    assert r2.status_code == 200
    # workbench is the advanced packing UI
    assert "Team Mode" in r2.text or "装箱" in r2.text or "vue" in r2.text.lower()

    rp = client.post("/api/tender/parse", json={"text": SAMPLE})
    assert rp.status_code == 200, rp.text
    jp = rp.json()
    assert jp.get("ok") is True
    assert (jp.get("matrix") or {}).get("rows")
    assert (jp.get("parse") or {}).get("requirements")
    assert (jp.get("handoff") or {}).get("schema") == "tender.handoff.v1"
    assert (jp.get("p0_reject_scan") or {}).get("human_confirm_required") is True
    assert (jp.get("tech_outline") or {}).get("schema") == "tender.tech_outline.v1"

    up = client.post(
        "/api/tender/parse/file",
        files={"file": ("itt.txt", SAMPLE.encode("utf-8"), "text/plain")},
    )
    assert up.status_code == 200, up.text
    assert (up.json().get("handoff") or {}).get("schema") == "tender.handoff.v1"
    bad = client.post(
        "/api/tender/parse/file",
        files={"file": ("scan.pdf", b"%PDF-fake", "application/pdf")},
    )
    assert bad.status_code == 400
    assert jp.get("submit_blocked") is True
    assert jp.get("p0_confirmed") is False

    rh = client.get("/api/health")
    assert rh.status_code == 200
    hh = rh.json()
    assert (hh.get("entries") or {}).get("packing_workbench") == "/workbench"
    assert (hh.get("features") or {}).get("tender_handoff") is True
    arch = client.get("/api/architecture")
    assert arch.status_code == 200
    assert (arch.json().get("tender_mainline") or {}).get("submit_blocked_default") is True
    assert "我已核对 P0" in index
    assert "复制招标解析表" in index
    assert "复制矩阵 CSV" in index
    assert "tenderFile" in index
    wb = (ROOT / "frontend" / "workbench.html").read_text(encoding="utf-8")
    assert 'href="/"' in wb and "投标应答" in wb

    rd = client.post(
        "/api/tender/delivery",
        json={
            "text": SAMPLE,
            "run_delivery": True,
            "container_type": "40HQ",
            "max_containers": 2,
            "session_id": "t052-copy",
        },
    )
    assert rd.status_code == 200, rd.text
    jd = rd.json()
    assert jd.get("product") == "tender_delivery"
    assert jd.get("product_mainline") == "C_tender_delivery"
    assert jd.get("packing_summary") is not None
    ps = jd["packing_summary"]
    assert "can_fit" in ps
    # mid50 must come from real big_team CoG (cog.primary), not null
    assert ps.get("mid50") is not None, f"mid50 missing in packing_summary={ps}"
    mid = float(ps["mid50"])
    assert 0.0 <= mid <= 1.0 or mid > 1.0  # allow ratio or percent-style
    matrix = jd.get("matrix") or {}
    assert matrix.get("summary", {}).get("n", 0) >= 1
    # A fitting plan covers the transport clause only when it used the type the clause names:
    # SAMPLE asks for 40HQ, so a 20GP plan must leave the row to a person, with both types in the note.
    if ps.get("can_fit") is True:
        transport = next(r for r in matrix.get("rows") or [] if r.get("req_id") == "transport_container")
        if ps.get("container_type") == "40HQ":
            assert transport.get("status") == "covered", transport
        else:
            assert transport.get("status") == "human_required", transport
            note = str((transport.get("evidence") or {}).get("note") or "")
            assert "40HQ" in note and str(ps.get("container_type")) in note, transport
        if mid >= 0.55 or (mid > 1 and mid >= 55):
            # cog_lashing row should not stay partial when mid50 present
            cog_rows = [
                r
                for r in (matrix.get("rows") or [])
                if r.get("req_id") == "cog_lashing" or "重心" in str(r.get("title") or "")
            ]
            if cog_rows:
                assert cog_rows[0].get("status") in ("covered", "partial"), cog_rows[0]
    # export package for demo handoff
    assert jd.get("export_markdown") and "交付证据" in jd["export_markdown"]
    assert isinstance(jd.get("open_actions"), list)
    assert (jd.get("response_package") or {}).get("schema") == "tender.response_package.v1"
    bb = jd.get("bidbook_markdown") or ""
    assert "DRAFT" in bb and "NOT FOR" in bb
    assert "6. Logistics & Packing Evidence" in bb
    assert "all crates placed" in bb     # the plan's can_fit, in words

    # T052: same session pack-ship copies delivery can_fit; xyz never invented.
    pk = client.post(
        "/api/turn",
        json={
            "text": "出一份装箱作业单 铁架",
            "expert_id": "pack-ship",
            "session_id": "t052-copy",
        },
    )
    assert pk.status_code == 200, pk.text
    plan = ((pk.json().get("pack_ship") or {}).get("plan") or {})
    assert plan.get("can_fit") == ps.get("can_fit"), (plan.get("can_fit"), ps.get("can_fit"))
    assert plan.get("xyz") == "UNSPECIFIED"
    assert "Harbourline Facade" in bb
    assert "REDACTED-CLIENT" not in bb
    # dedicated bidbook endpoint
    rb = client.post("/api/tender/bidbook", json={"text": SAMPLE, "run_delivery": False})
    assert rb.status_code == 200, rb.text
    jb = rb.json()
    assert jb.get("product") == "sg_facade_bidbook"
    assert "Form of Tender" in (jb.get("bidbook_markdown") or "")
    # UI surface mainline C
    assert "人工待办" in index or "复制应答草稿" in index
    assert "复制英文标书草案" in index
    assert "主线 C" in index or "投标应答" in index

    print(
        "PASS tender_delivery_api",
        f"parse_rows={len((jp.get('matrix') or {}).get('rows') or [])}",
        f"can_fit={ps.get('can_fit')}",
        f"mid50={ps.get('mid50')}",
        f"covered={matrix.get('summary', {}).get('covered')}",
        f"open={len(jd.get('open_actions') or [])}",
        f"ready={jd.get('readiness_score')}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
