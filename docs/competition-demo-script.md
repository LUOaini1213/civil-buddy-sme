# 评委 5 分钟路径（Civil Buddy · 当前版本）

> 仓库：**github.com/LUOaini1213/civil-buddy-sme**（原独立仓 packing-agent 已并入本树）。
> 产品：土木版 Codex —— 66 岗工作台，装箱/拼柜（pack-ship）是其中一岗。  
> 原则：**tools compute numbers, the model only routes**——柜数/坐标由引擎算，模型不写 xyz、不拍柜数；高风险写盘/出运须人确认。  
> Harness 0.6.4 · 13 agents · 主路径 `agent_mode=steps`（无 API Key 走 policy fallback，功能不哑）。

---

## 0) 环境预检（上场前 2 分钟）

```bash
pip install -r requirements.txt
python scripts/demo_one_shot.py                        # 冒烟 → ALL SELECTED CHECKS PASSED（无需 API Key）
uvicorn gateway.app:app --host 127.0.0.1 --port 8000   # 装箱引擎网关（:8000）
```

工作台（:8765，二选一）：

```bash
cd workbench && cargo run --release --bin civil-workbench   # Rust 正式版
# 或 Python 参考实现（免编译）：
cd demo && uvicorn app:app --host 127.0.0.1 --port 8765
```

API Key 可选（自带，不必 DeepSeek）；无 Key 时 LLM 环节为 policy_fallback，tools 数字照常。

---

## 5 分钟路径（唯一主戏）

| 时间 | 操作 | 看点 |
|------|------|------|
| 0:00–1:00 | `python scripts/demo_one_shot.py` | 冒烟全绿，**无 Key 可跑**：harness 起得来、门禁全过 |
| 1:00–2:00 | `python scripts/demo_agent_middleware.py` | **AI 协同四拍纠偏剧本**：正常下单 → 越权被拒（弹原因）→ 工具挂掉自动恢复（审计链）→ 成本超限熔断 |
| 2:00–3:30 | :8765 工作台聊天框输入 `pack test/sim_materials/small_one_container/materials.xlsx` | **自然语言 pack 入口**：一句话 → 识别为 pack-ship 动作 → 引擎加载表 → 出**真数字**装箱作业单（柜数/坐标是 tools 输出，以现场屏幕为准） |
| 3:30–4:30 | `python main.py --demo`（默认过合规门，done/WARN 收尾）；再跑负例 `python main.py --demo --preset structure_fail` | 正例：30 项结构校核通过、WARN 提示装前人工复核；负例：**BOX-01 合规阻断生效**（REJECT 是预期）——证明门是活的，不是装饰 |
| 4:30–5:00 | `python main.py --eval`（phase0 quick **12/12**，退出码 0）+ 口播冻结数字收束 | 评测口径诚实，见下 |

备份深度（评委追问时）：`http://127.0.0.1:8000/workbench` 工程装柜台——HITL 确认、拼柜 3D / CoG；投标主线 C 响应矩阵在 `http://127.0.0.1:8000/`（草稿，资质/废标项 human_required）。

---

## 冻结数字（口播只报这三个，带口径限定）

| 数字 | 口径限定语（必须跟着说） |
|------|--------------------------|
| **29→25 柜**（446t 单票对照） | 29 柜基线已废弃。446 t 客户清单 `output/cases_446t/materials.json` 不在仓库里，净仓不能复跑，不要当成今天的结果 |
| **mid50 0.594**（同一 446t 单票） | 客户文件不在仓库，不能复验。不要把 0.594 当成净仓结果 |
| **scorecard 8.85** | 本地校准综合分、**对外口径**；phase0 quick（n=12，pass_rate 1.0）封顶口径，**不报 10.0** |

---

## 不说的话（禁句）

- **不说「中标率」「可以投标」「可以开工」**——P0 资格/★/废标项须人确认，产品不自动判定可投标。
- **不说「GeBIZ 代交」**及任何代为提交官方系统的表述。
- 不说「模型自己摆箱子 / 模型决定几柜」——柜数坐标是 tools，模型只路由。
- 不回避边界：TMS/ERP 为 stub、VGM 须托运人签认、大票需绑扎复核。

---

## 命令速查

```bash
# 冒烟与协同剧本
python scripts/demo_one_shot.py
python scripts/demo_agent_middleware.py

# 引擎演示（正例 / 负例）
python main.py --demo
python main.py --demo --preset structure_fail

# 评测
python main.py --eval                                 # phase0 quick 12 case
python scripts/run_phase0_baseline.py --quick         # 同口径
python scripts/eval_competition_scorecard.py --skip-phase0   # 综合分卡（对外 8.85）

# 大票对照证据（上场前一天跑够即可）
# 446 t 客户清单不在仓库，下面这条命令在净仓会失败，不要当成可复验结果。
# python scripts/compare_446t_agent_vs_tool.py --full-agent  # 客户文件不在仓库，不能复验；29 柜基线已废弃

# 每岗记分卡（R5，评委追问岗位质量时）
python scripts/eval_post_scorecard.py --all-pilots
```

## 证据与文档

| 文件 | 用途 |
|------|------|
| [competition-evidence-one-pager.md](./competition-evidence-one-pager.md) | 一页证据。446 t 对照的客户文件不在仓库，不能复验 |
| `output/competition/SCORECARD.md` | 分卡（8.85 与 hard gates 明细） |
| `output/phase0/BASELINE_REPORT.md` | phase0 quick 基线报告 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) · [harness-design.md](./harness-design.md) | 架构 |
