# 海之子杯 2026 · AI 智能体挑战 — 提交入口

> 本文是从 README 首屏移出的参赛材料，保留原文以便评委对照复跑；其中 R1–R23 为内部 UX 迭代轮次编号，「完全合格」为按赛题 checklist 的自评，不是官方评审结论。

## 参赛提交入口（海之子杯 · AI 智能体挑战）

| 评审维度 | 项目证据 | 可复跑命令 |
|----------|----------|------------|
| **场景创意价值** | 土木版 Codex：66 岗工作台，NL 一句话 pack 入口出真数字（tools 算柜数/坐标，模型只路由） | 起两个服务后在 :8765 聊天框输入 `pack test/sim_materials/small_one_container/materials.xlsx`（起法：Releases exe 双击，或 `cd demo && uvicorn app:app --port 8765`；引擎 `uvicorn gateway.app:app --port 8000`。只起 :8765 无引擎时会得到如实的说明卡，不出假数字） |
| **AI 协同能力** | Agent Middleware 策略引擎+失败恢复：四拍纠偏剧本（正常下单 → 越权被拒 → 工具挂掉自动恢复 → 成本超限熔断）；HITL 人确认后才拼柜 | `python scripts/demo_agent_middleware.py` |
| **技术创新** | 装箱引擎 NL→IntentSpec→白名单 tools→HITL→影子评测。净仓能复跑的长框架样例是 9 箱、`can_fit` 为真、净重 23800 kg 守恒（`scripts/test_pack_ship_crates_structure.py`）。446 t、29→25 柜的客户清单不在仓库里，不能当复验结果；29 柜基线已废弃 | `python main.py --demo` · `python scripts/test_pack_ship_crates_structure.py` |

> **66 岗诚实分级**（L1 知识库 66/66 · L2 工具写盘 66/66 · L3 引擎岗 1，每级挂可复跑验收）：[docs/depth-ladder.md](docs/depth-ladder.md)。申报定位与三维度证据映射：[docs/submission/haizizhi-positioning.md](docs/submission/haizizhi-positioning.md)。
>
> **UX 证据链（23 轮迭代，R1 立规矩 → R23 门禁自检）**：R17 界面填 Key（评委自带，不必改 .env）· R19 co-work 壳（左项目树 · 单一聊天框）· R20-21 `pack <本机路径>` 与回形针上传 · R22-23 物料来源诚实性（表读不到必须明说，网关/exe/CI 三层门禁）。设计公理/逐轮总结/附录 N-R 见 [docs/ux/ux-design-spec.md](docs/ux/ux-design-spec.md)；断网专项 `python scripts/test_offline_ui.py`（外域请求 0、pageerror 0）；端到端金线 `python scripts/r13_golden_path_e2e.py`（8/8 PASS，需 playwright）；体验记分卡 `python scripts/eval_competition_scorecard.py --skip-phase0`（本地校准综合 8.85，赢线 PASS）。

## 初审之后的进展（2026-09-12 ~ 2026-09-20）

> 上面的四件套与表格是 2026-08-31 初审定稿，原文保留以便对照。下表是初审提交之后仓库里真实发生的事，答辩以此为准；逐段人机协同见 [haizizhi-resume.md](haizizhi-resume.md) ⑪–⑮。

| 方向 | 做了什么 | 可复跑命令 |
|------|----------|------------|
| 工具真正接上引擎 | MCP 的 `ingest/plan/vgm/booking_draft` 由"投影已有快照"改为真实调用装箱引擎；PDF 装箱单可解析 | `python scripts/test_mcp_stdio.py` · `python scripts/test_packing_list_pdf.py` |
| 表格读取的静默错误 | `L (mm)` 类表头 8 种写法补齐识别缺口；英寸/英尺/磅与合并尺寸格 16 种写法修正 12 种；空计划不再报成功 | `python scripts/test_table_mapper_unit.py` · `python scripts/test_pack_ship_dimension_gate.py` |
| 招标响应逐行比对 | 一条招标要求只出一行，数字对不上标待核、不下结论。现行 `--check`：19 例，链接精确率 1.000、链接召回 0.980、冲突 14/14 全检出。更早的 14 例召回 0.974、9/9 不是这次的分数 | `python scripts/eval_tender_response_match.py --check` |
| 模型只路由，工具算数 | 模型驱动回合加确定性守卫：模型回复不得给出判定、复读收敛、数字必须指得回工具结果 | `npm run check` 中的 `model-loop` · `verdict-bench` · `number-provenance-bench` |
| 运行边界 | 系统级沙箱（工具工作跑在受内核约束的 worker 里，能力按平台如实分级）、声明式插件（一个文件夹或 zip 就是一批岗位）、原生桌面应用 | `npm run check` 中的 `os-sandbox` · `plugins` · `desktop-app` |
| 来源可追溯 | 四个只读数据源工具：响应缺数据集版本、哈希或逐字证据一律拒收 | `python scripts/test_readonly_sources.py`（17 例，离线 fixture） |

**2026-09-20 全量复跑（退出码全 0）**：`npm run check` 69/69 · `test_kb_k4_depth` 66 岗全绿 · `demo_one_shot` ALL_PASS · `eval_competition_scorecard --skip-phase0` 综合 **8.85**（赢线 PASS，冲刺 FAIL：phase0 仍是 quick n=12）· `eval_post_scorecard --all-pilots` 5 岗 × 4 门禁全 PASS。自评分是仓内校准口径，不是评审结论。

### Agent Middleware（赛道 1 · 完全合格）

对照表：[docs/civil-buddy/track1-qualified.md](docs/civil-buddy/track1-qualified.md)。  
Runtime 深做 **策略引擎**（拒绝弹原因）。**失败恢复**（retry → `UNSPECIFIED` 审计链）是演示脚本 `scripts/demo_agent_middleware.py` 里的一拍，不是运行时能力：生产路径不自动重试工具（`execute_with_recovery` 只有这个脚本调用）；运行时只有工具熔断（同一工具连续 3 次故障后拒绝，45 s 冷却后放一次试探调用）。  
剧本写死：正常下单 → 越权被拒 → 工具挂掉自动恢复 → 成本超限熔断。  
行业现网总判（人改口）：[industry-agent-eval-2026-08-25.md](docs/civil-buddy/industry-agent-eval-2026-08-25.md) — 内部起草搭子 **合格**；签认/投标 **不合格**。

```powershell
python scripts/demo_agent_middleware.py
npm run check
```

`npm run check` 必须过。不得把 API Key 提交进仓。
