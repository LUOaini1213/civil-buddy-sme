# 海之子杯申报 · 定位陈述与证据包（Civil Buddy）

> 更新：2026-09-20（复赛答辩前刷新：§b 补初审后新增的三条证据，§e 勾掉全绿复跑与红线自查；四件套本身仍是
> 2026-08-31 定稿，见 output/submission/，需按本次数字重建）· 服务 [knowledge_base/06_competition/constraints-hzzb.md](../../knowledge_base/06_competition/constraints-hzzb.md) 的提交物四件。
> 官方章程：<https://aicampus.3311csci.com/rules.html>（以官方页为准）。评审三维度官方原文见 constraints-hzzb.md。
> 所有"可复跑命令"均在本仓根目录实际执行验证过；冻结数字口径见 [docs/competition-demo-script.md](../competition-demo-script.md)。

## a) 一段话定位陈述

**Civil Buddy 是面向土木企业的"土木版 Codex"**：覆盖 16 大类 66 个岗位的 AI 工作台，每岗以"程序记忆（SOP）+ 岗位知识库 + 独有工具"起草内部交付物；策略引擎与失败恢复两层中间件护航；铁律 **"tools compute numbers; the model only routes"**——数字只由工具算，模型只做路由与组织语言。

本次提交展示一条**深度证据链**：从招标解析、废标检查、技术标目录，到净仓可复跑的长框架装箱（9 箱、装得下、23800 kg 守恒），以及一张诚实的 **66 岗分级地图**（L1 知识库草稿 66/66、L2 工具写盘 66/66、L3 全链路引擎岗 1——pack-ship）。446 t 单票和 29→25 柜依赖未入库的客户文件，不能从本仓库复验；29 柜基线已废弃。对外校准综合分 **8.85** 仍是仓内校准，不是评审结论。宽度是路线图，深度是可复跑证据；**缺数标 UNSPECIFIED 是产品特性，不是未完成**。L2=66 表示专属写盘栏位已覆盖，不表示 66 岗都有求解引擎。

**红线（产品设计与本申报共同的边界）**：不出签认件、不自动判定可投标、不承诺中标率、不代交任何官方系统。P0 资格/★/废标项与人身安全相关写盘一律人工确认（HITL），未确认时 `submit_blocked=true`。

## b) 三维度证据→命令映射

### 维度一：场景创意价值（真实痛点 + 落地可行 + 产业推广价值）

| 官方考察点 | 证据 | 可复跑命令 / 入口 |
|------------|------|--------------------|
| 真实痛点：土木企业 66 类岗位日常起草工作靠人肉重复 | 66 岗工作台：每岗"程序记忆（SOP）+ 知识库 + 独有工具"起草内部交付物（方案提纲、交底、台账、报价栏……缺数标 `[A001]`/`UNSPECIFIED`） | `python scripts/test_kb_k4_depth.py`（66/66 岗知识库深度闸） |
| 真实痛点：装柜出运凭经验拍柜数、拍坐标 | pack-ship 引擎岗：NL 一句话 `pack <表>` → 引擎出真数字作业单（柜数/坐标是 tools 输出） | Releases exe 双击（或 `cd demo && uvicorn app:app --port 8765`）+ `uvicorn gateway.app:app --port 8000`，在 :8765 聊天框输入 `pack test/sim_materials/small_one_container/materials.xlsx` |
| 落地可行：不依赖云、评委在界面填自己的 Key（DeepSeek / z.ai 任选，不用改 .env）、免编译冒烟 | 冒烟无需 API Key；工作台 exe 可从 GitHub Releases 下载试用 | `python scripts/demo_one_shot.py` → ALL_PASS |
| 落地可行：公司自己的岗位不改代码就能加（2026-09-19） | 声明式插件——一个文件夹或一个 zip 就是一批岗位，不是代码；另有原生桌面应用，可直接给不装开发环境的人用 | `npm run check` 中的 `plugins` · `example-plugin` · `desktop-app` |
| 产业推广价值：16 大类车道覆盖投标→生产→商务→后勤全链条 | 16 车道分级表与富化批次（T030–T039 滚动推进） | [docs/depth-ladder.md](../depth-ladder.md)（每行挂验收） |

### 维度二：AI 协同能力（人机协同规划 + AI 交互迭代 + AI 纠偏）

| 官方考察点 | 证据 | 可复跑命令 / 入口 |
|------------|------|--------------------|
| 人机协同规划：HITL 高风险写盘前人确认 | 高风险岗未确认 0 稿；确认句"我明白，将由持证人员签认"；成箱→HITL→拼柜 | `python scripts/demo_agent_middleware.py`（第三拍含 HITL）；`http://127.0.0.1:8000/workbench` |
| AI 交互迭代：一句话自然语言入口 + 意图路由（chat/run/both） | NL→IntentSpec→白名单 tools；无 Key 时 policy fallback 功能不哑；每岗金句冻结（41 条）Python/Rust 双侧实跑守护 | 按上行方式起 :8765 后输入 `pack ...`；`python main.py --eval`（phase0 quick 12/12）；`python scripts/test_stack_parity.py` |
| AI 纠偏管理：策略引擎（越权拒绝弹原因）+ 失败恢复（retry→`UNSPECIFIED` 审计链，演示脚本里的一拍，生产路径不自动重试工具）+ 成本熔断 | Agent Middleware 四拍剧本：正常下单 → 越权被拒 → 工具挂掉自动恢复 → 成本超限熔断 | `python scripts/demo_agent_middleware.py` |
| AI 纠偏管理：模型不得替人下判断（2026-09-19） | 模型驱动回合外加确定性守卫：模型回复不得给出判定、复读回复收敛、报告里的数字必须指得回工具结果记录。守卫是确定性代码，不能计作模型理解正确 | `npm run check` 中的 `model-loop` · `verdict-bench` · `number-provenance-bench` |
| 纠偏落到成稿：缺数不编造 | safety-brief 成稿 11 栏中毫米/电话为 `[A001]` 待填；各岗 TBD/UNSPECIFIED | `grep -n "A001" demo/kb/hse/safety-brief/outline.md`；或在 :8765 召唤安全交底专家看成稿待填栏 |

### 维度三：技术创新能力（创新构思 + 技术应用 + 工具整合 + 完成度）

| 官方考察点 | 证据 | 可复跑命令 / 入口 |
|------------|------|--------------------|
| 创新构思：土木版 Codex（IDE 隐喻搬进土木企业） | 技能一岗一份 `.agents/skills/<id>/SKILL.md`；任务选用 SOP；沙箱写盘 | `python -m packing_assistant.civil`（TUI）· `ide/README.md` |
| 技术应用：NL→IntentSpec→确定性流水线（`agent_mode=steps`）→影子评测 | 引擎正例过 30 项结构校核；负例 `--preset structure_fail` 证明合规门是活的（REJECT=门生效） | `python main.py --demo` · `python main.py --demo --preset structure_fail` |
| 工具整合：Rust 工作台 + Python 引擎 + MCP + KB 检索 + 前端 3D/CoG | 一个仓库三入口（:8765 工作台 / :8000 主线 C / TUI）；MCP stdio 工具表 | `npm run check`；`python scripts/test_mcp_stdio.py` |
| 完成度：诚实分级 + 评测口径不注水 | L1 66/66、L2 66/66、L3 1；净仓长框架 9 箱、装得下、23800 kg 守恒。446 t / 29→25 柜的客户文件不在仓库，不能复验；29 柜基线已废弃。对外校准综合分 8.85（不报 10.0） | `python scripts/test_pack_ship_crates_structure.py`；`python scripts/eval_competition_scorecard.py --skip-phase0` |
| 技术应用：外部数据必须带得出出处（2026-09-20） | 四个只读数据源工具：不接受 URL、文件、SQL、命令或凭据入参，端点只由已注册的工具名决定，响应缺数据集版本、哈希或逐字证据一律拒收；系统级沙箱的能力按平台如实分级披露 | `python scripts/test_readonly_sources.py`（17 例，离线 fixture）· `npm run check` 中的 `os-sandbox` |
| 完成度：每岗质量门禁可抽样复跑（R5） | 每岗记分卡四门禁（意图命中/KB 检索/交付物 schema/诚实度），试点 5 岗全 PASS、全离线零 Key | `python scripts/eval_post_scorecard.py --all-pilots`（产物 `output/posts/<岗>.json`） |

## c) 视频脚本表（115 秒重录版，2026-09-20）

> 官方限 ≤2 分钟。**已提交的成片是 8/31 那版（116 秒），内容停在初审时的功能**；下表是按今天的
> 产品重写的重录脚本。重录管线：`scripts/submission/record_v2.py`（1920×1080 真机录屏）→
> `tts_v2.py`（edge-tts 神经语音）→ `assemble_v2.py`（ffmpeg 合成 + 烧字幕），本机 ffmpeg 9.0.1 可用。

| 时间 | 画面 | 口播要点 | 操作 |
|------|------|----------|------|
| 0:00–0:10 | 工作台首屏，66 岗一闪而过 | "土木版 Codex：16 大类 66 个岗位。硬数字只由工具算，模型只负责路由。" | 打开 :8765 工作台 |
| 0:10–0:35 | 一句话装箱 | "把真实出运表丢进去——柜数、坐标、重心都是引擎算出来的，不是模型说出来的。" | 回形针上传 Excel 或输入 `pack <表格路径>`，出装箱作业单 |
| 0:35–0:50 | 确认闸 | "高风险的一步停在这里。人不点，它不往下走；关掉就是驳回。" | 停在 HITL 确认卡，展示非标预检 |
| 0:50–1:10 | 招标响应逐行比对 | "一条招标要求出一行，数字对不上会被指出来：招标写 60 日历天，投标写 90 天，标成待人工核验，不替人下结论。" | 展示 `conflict_requires_review` 与 `conflicts[]` |
| 1:10–1:30 | 模型想下判断，被守卫挡下 | "模型说得像，不等于模型算得对。它要替人下判定，守卫会挡；报告里每个数字都得指得回工具结果。" | 触发 verdict guard；展示数字指回 `pack-plan.json` |
| 1:30–1:45 | 运行边界 | "工具工作跑在受内核约束的进程里；公司自己的岗位是一个文件夹或一个 zip，不是代码。" | 沙箱、插件、桌面应用各一闪 |
| 1:45–1:55 | 红线收口 | "不出签认件，不判定可以投标。人确认之前，submit_blocked 一直是 true。" | 定格 `submit_blocked: true`，黑屏出仓库名 |

录制注意：先跑通 `python scripts/demo_one_shot.py` 与 :8000 网关再开录；装箱与"守卫挡下判定"各留一条
备选镜头；口播禁句见 [docs/competition-demo-script.md](../competition-demo-script.md)「不说的话」，以及
`output/submission/00-提交表单填写.txt` 里的禁句清单（含"128/128 全部装得下"这类说法）。

## d) 人机协同履历表

> 官方提交物第四件。**正式稿见 [haizizhi-resume.md](haizizhi-resume.md)**（15 行真实素材，2026-07-24 ~ 09-20，每行挂 git 证据与具体纠偏点；⑪–⑮ 为初审之后的工作）。下表保留最早的三行素材：

| 阶段（日期） | 人做什么 | AI 做什么 | 纠偏点 |
|--------------|----------|-----------|--------|
| 2026-08-28 泄漏审计 | 人发起历史提交泄漏审计，决定清洗范围并复核结果 | 脚本执行 filter-repo 历史清洗，随后全量回归测试跑绿 | 泄漏在合入前被审计拦截；清洗后以回归全绿证明功能未受损 |
| 2026-08-29 NL pack 断链 | 人在彩排中发现一句话 pack 入口断链，拍板修复优先级 | 定位断链五处并逐一修复，端到端复跑出真数字 | 彩排即纠偏：入口断了说明集成测试没覆盖到，补进冒烟口径 |
| 规划书每章复查 | 人定各章口径与"明确不做"边界 | 子代理逐章复查 product-plan，列出内部错误清单 | 人复核后改口（product-plan §14"规划书审阅发现必须改口"），机器不改总判 |

## e) 提交前待办勾选清单

- [x] **报名**：队长在活动官网 <https://aicampus.3311csci.com> 完成报名（截止 2026-08-31）
- [ ] **组队**：确认每队 ≤4 人、队员学籍信息齐全（海内外全日制在校生）
- [ ] **学籍/身份材料**：按官网要求准备在校证明或学生证材料
- [x] **视频**：已产出 116 秒中文口播版（官方限 ≤2 分钟 / MP4 / ≤500MB，FAQ 已核对），源在 output/submission/build_demo_video.py + narration.json
- [x] **技术说明文档**：已成 PDF（output/submission/01-说明文档-CivilBuddy.html → Edge 打印；官方对格式不设限，仅 ≤30MB）
- [x] **人机协同履历表**：已成 PDF，13 行带 git 证据（10 行正式稿 + round14-23 三行）
- [x] **格式核对**：四件套（智能体链接或代码包 / 技术说明 / 视频 / 履历表）命名、大小、附件格式逐项对照官方页
- [x] **提交前全绿复跑**（2026-09-20 复跑，四条退出码全 0：kb_k4 66 岗全绿 · demo_one_shot ALL_PASS · 综合 8.85 赢线 PASS · 记分卡 5 岗 4 门禁全 PASS；另 `npm run check` 69/69）：`python scripts/test_kb_k4_depth.py` · `python scripts/demo_one_shot.py` · `python scripts/eval_competition_scorecard.py --skip-phase0` · `python scripts/eval_post_scorecard.py --all-pilots` 四连，退出码全 0
- [x] **红线自查**（2026-09-20 全仓检索：四个禁句只以否定形式出现，如「禁止：可以投标 / 可以开工」「不承诺中标率」，符合 design.md 对否定句放行的规定）：全文检索禁句（中标率 / 可以投标 / 可以开工 / 代交官方系统），P0 字样必须连着"须人确认"
