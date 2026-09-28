# 装箱拼柜 · 联网知识（2026-08-15 现场）

岗位对应：工地/钢构物料的成箱与拼柜作业单。硬数值只走本仓 **packing 引擎**（https://github.com/LUOaini1213/civil-buddy-sme），本岗不写 xyz、不拍柜数。网上是入口，柜内尺寸、N0*、重心只抄工具回传或标 UNSPECIFIED。

## 2026-08-17

招标运输包装条款由主线 C 矩阵用 `can_fit` / mid50 当证据；`can_fit=false` 记 gap，不是 covered。MCP prompt：`civil.pack-ship.plan`。CTU Code 只列门户，不编条款号。

## 国际作业守则（2026-08-15 打开过）

- 官方标题与公司层 `company/web-portals.md` 同一句：**IMO/ILO/UNECE Code of Practice for Packing of Cargo Transport Units (CTU Code)**，2014 年版，非强制性全球作业守则。IMO 页述 2014 年由 UNECE Inland Transport Committee、IMO MSC、ILO Governing Body 核可；并作为 MSC.1/Circ.1497 印发。条款 UNSPECIFIED。https://www.imo.org/en/ourwork/safety/pages/ctu-code.aspx
- UNECE 专题页（同一守则）：https://unece.org/transport/intermodal-transport/imoilounece-code-practice-packing-cargo-transport-units-ctu-code
- CSC（International Convention for Safe Containers）Safety Approval Plate：柜况与铭牌由持证人员核，本岗不判「可装船」。
- 危险品另对 IMDG Code；本岗无申报正文则不编 UN 号。

## SG

- 港口现场规定对 MPA / PSA 作业通知，本岗不编申报号。工地起重/堆放仍对 MOM WSH，与装箱作业单分开。公司层门户见 `company/web-portals.md`。
- 本轮未把「几柜」写成新加坡法定限额；柜数只来自 packing-agent 或 UNSPECIFIED。

## 与 packing-agent 的边界

- packing-agent 原则：tools compute numbers; the model only routes。Civil Buddy 召唤本岗时同样遵守。
- 接通：同仓 `packing_assistant/`（sidecar 自动找根）或 `PACKING_AGENT_URL`。`pack-ship__health` 探测是否在线。
- 未接通：作业单仍出，柜数/坐标写 UNSPECIFIED，并提示启动 packing-agent。
- 禁止：用 LLM 补 20GP/40HQ 内尺寸当已核实；禁止把 CTU Code 条款号编出来。

2026-08-14 物机门户过网；2026-08-15 本岗补 CTU Code / packing-agent 边界。https://www.imo.org/en/ourwork/safety/pages/ctu-code.aspx
