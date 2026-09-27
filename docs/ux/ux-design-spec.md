# Civil Buddy UX 设计规范（ux round13 定稿 · R1 立规矩 · R13 收官）

> 本轮（ux round1）为后续 12 轮 UX 迭代立规矩：定公理、定 token、定语言、定路线。
> 本轮只加文档与 `:root{}` 变量块，**不改任何布局与现有样式**。
> 红线：工地内网运行 → **任何界面零 CDN、零外链资源**（已全量盘点通过，见附录 A；R12 升级为 CI 门禁与断网专项，见附录 K）。

## 1. 设计公理

项目即"Civil Buddy = 土木版 Codex"，原则 **tools compute numbers; the model only routes**。四条公理：

1. **一个输入框**（借 Codex 简洁）：所有任务从同一个 composer 进入——自然语言、`/`指令、`@`文件、追问改方案同框；不做多入口分叉。
2. **一条流水线**（进度可见）：tools 算数字、模型只路由；流水线每个阶段（解析→组队→装柜→复核）在事件流上可见、可计时、可回放。
3. **一份正式交付物**（施工单位专业口径）：产出是可签认的文书（装柜单/作业单/交底/方案），带免责声明与签认栏，"内部讨论草稿 · 不是签认件"必须常驻。
4. **一道审批门**（HITL 合规）：`submit_blocked` 默认阻断；审批必须**显式决策**（确认/驳回），**Esc/关闭/刷新 ≠ 放行**；P0 资格、★、废标项一律"须人确认"。

### 三态交互模式（借自 Codex CLI TUI，pattern-only）

| 态 | Codex 做法 | Civil Buddy 落法 |
| --- | --- | --- |
| 普通提示 | 单一 composer，slash 弹窗、@ 文件搜索、粘贴预览、历史回溯都在这一个框里 | index/workbench/:8765 三端统一"一个输入框"；R2 输入轮、R9 指令面板轮落地 |
| 审批请求 | `approval_overlay.rs`：动作特定选项（accept/reject/always）+ 每次选择都发**显式决策事件**；Esc 映射为 Cancel，永不静默变成"继续" | R5 审批卡轮：审批卡按钮=动作特定（确认提交/驳回重做/补参数），决策写审计；关闭卡片=驳回 |
| 输出呈现 | `history_cell` 追加式会话流：turn、diff、exec 输出都是不可变历史 cell，浮层叠加不打断历史 | R6 审计时间线轮：流水线事件→追加式 cell 流，含签字/确认/阻断事件，可回放 |

## 2. 设计 token（`:root{}` 变量块，可直接粘进页面）

命名空间 `--cb-*`（Civil Buddy），与各页现有变量并存、不覆盖；后续轮逐步切换引用。

```css
:root {
  /* === Civil Buddy UX tokens · ux(round1) · 见 docs/ux/ux-design-spec.md === */
  /* 色板：工程蓝 / 安全橙 / 合规红 / 通过绿 / 中性灰阶（slate） */
  --cb-blue: #2563eb;        /* 工程蓝：主操作、链接、选中 */
  --cb-blue-strong: #1d4ed8; /* 工程蓝-深：hover/按下 */
  --cb-blue-soft: #eff6ff;   /* 工程蓝-底：选中行、信息底 */
  --cb-orange: #ea580c;      /* 安全橙：警示、待人工补全、UNSPECIFIED */
  --cb-orange-soft: #fff7ed;
  --cb-red: #dc2626;         /* 合规红：阻断、废标项、禁句拦截 */
  --cb-red-soft: #fef2f2;
  --cb-green: #059669;       /* 通过绿：PASS、已签认 */
  --cb-green-soft: #ecfdf5;
  --cb-gray-50: #f8fafc; --cb-gray-100: #f1f5f9; --cb-gray-200: #e2e8f0;
  --cb-gray-400: #94a3b8; --cb-gray-500: #64748b; --cb-gray-900: #0f172a;
  --cb-text: var(--cb-gray-900);
  --cb-text-muted: var(--cb-gray-500);
  --cb-line: var(--cb-gray-200);
  /* 字体（零外链，系统栈） */
  --cb-font: "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  --cb-mono: ui-monospace, "Cascadia Code", Consolas, monospace; /* 锚点[A001]/坐标/UNSPECIFIED 用 mono */
  /* 字号阶梯 */
  --cb-fs-xs: 12px; --cb-fs-sm: 13px; --cb-fs-md: 14px; --cb-fs-lg: 16px;
  --cb-fs-xl: 18px; --cb-fs-2xl: 22px; --cb-fs-3xl: 28px;
  /* 间距（4px 基） */
  --cb-space-1: 4px; --cb-space-2: 8px; --cb-space-3: 12px; --cb-space-4: 16px;
  --cb-space-5: 24px; --cb-space-6: 32px; --cb-space-7: 48px;
  /* 圆角 / 阴影 */
  --cb-radius-sm: 6px; --cb-radius-md: 10px; --cb-radius-lg: 14px; --cb-radius-full: 999px;
  --cb-shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --cb-shadow-md: 0 4px 18px rgba(15, 23, 42, 0.10);
  --cb-shadow-lg: 0 10px 32px rgba(15, 23, 42, 0.16);
}
```

落地位置：`frontend/workbench.html`（紧跟现有 `:root`）、`demo/static/styles.css`（:8765 UI）。`frontend/index.html` 色板已与 token 同源（slate+蓝），后续轮再切换引用。

## 3. 语言规范

### 3.1 界面语言
界面文案一律简体中文；代码标识符、API 路由、锚点（`[A001]`）、`UNSPECIFIED`、坐标/吨位数字保持原文等宽字体。

### 3.2 术语表

| 术语 | 用法 | 不说 |
| --- | --- | --- |
| 岗位 | 66 岗专家对外统一叫「岗位」（岗位/专家岗位） | 不用 "Agent 角色" 面向用户 |
| 装柜 | 拼柜/装柜/订柜（订柜 N0）；柜数与坐标由 tools 计算 | "模型摆箱子"（禁句） |
| 作业单 | 绑扎/空隙作业单（secureWorkOrder 产物） | "施工单" |
| 交底 | 班前白话交底（安全交底材料） | "培训材料" |
| 审批 | HITL 审批门：确认/驳回，决策留痕 | "自动通过"、"一键放行" |
| UNSPECIFIED | 数据哨兵：**界面上显示为「未提供」安全橙徽标**，点开可见原文哨兵；导出文书保留 `UNSPECIFIED` 原文与 `[A001]` 锚点供人工补全 | 不译、不藏、不伪填 |
| 纠偏卡 | 错误/拒绝/缺数的统一可行动卡片（R7）：发生了什么+为什么(code)+现在能做什么，见附录 F | 不用「失败了」「出错了」裸文本收尾 |
| 熔断 | 连续失败/超预算自动停下止损（`circuit_open`/`deny_budget`），卡上写明数字 | 不说"崩了/挂了/系统故障" |
| 降级 | 工具失败后标 `UNSPECIFIED` 不编数（recovery 层），卡上写明已尝试次数 | 不说"部分成功"掩盖失败 |

### 3.3 禁句表（承 docs/competition-demo-script.md「不说的话」）

界面、文书、口播均不得出现：**中标率 / 可以投标 / 可以开工 / GeBIZ 代交（及任何代交官方系统表述）/ 模型自己摆箱子·模型决定几柜**。P0 字样必须连着"须人确认"。边界要直说：TMS/ERP 为 stub、VGM 须托运人签认、大票需绑扎复核。

## 4. 12 轮路线图（R2–R13，每轮一行：主题 + 验收标准）

| 轮 | 主题 | 验收标准 |
| --- | --- | --- |
| R2 | 输入：三端统一单输入框（自然语言+/指令+@文件+改方案） | 一个 composer 覆盖全部输入模式；Esc=取消且语义有提示；CI 标记不丢 |
| R3 | 进度：流水线事件流可视化（consumeSse→阶段步骤条） | 每阶段有状态色（蓝=进行/绿=过/红=断）+ 耗时；事件全量可回放 |
| R4 | 文书预览：装柜单/作业单/交底 A4 预览（PDF/docx 一致） | 预览内容与导出 docx 逐字段一致；UNSPECIFIED 显示「未提供」徽标 |
| R5 | 审批卡：submit_blocked 审批卡（动作特定选项+显式决策事件） | 决策必须显式点击；Esc/关闭=驳回不=放行；决策写入审计 |
| R6 | 审计时间线：追加式会话 cell 流（签字/确认/阻断全留痕） | 任一运行可回放全部事件；签认/确认有时间戳与操作者 |
| R7 | 错误恢复：HITL checkpoint 恢复 UI + 失败重试路径 | 断点续跑不丢状态；恢复入口从时间线可达 |
| R8 | 窄屏：响应式三栏→单栏（768px 断点） | 768/375 宽度下输入、流水线、审批卡全部可用 |
| R9 | 指令面板：`/` 命令 + 66 岗检索面板（ux round9 落地，见附录 H） | 66 岗可检索可点名；面板零 CDN、键盘可达 |
| R10 | 引导：空态三步剧本强化（装柜 demo 路径，ux round10 落地，见附录 I） | 新用户 3 分钟内完成首跑（demo_one_shot 同路径） |
| R11 | 主题：--cb-* token 全量接管 + 明暗双套（prefers-color-scheme） | 三端零硬编码色值引用旧变量；明暗切换无对比度回归 |
| R12 | 离线：内网离线资源自检（零外链断言入 CI，落地见附录 K） | 断网全功能；CI 增加"零 CDN/外链"静态断言 |
| R13 | 终评：scorecard + 禁句自查 + 可用性走查收官 | eval_competition_scorecard 分数不降；禁句全文检索为零 |

## 5. 借鉴来源清单

| 来源 | 许可 | 借什么 |
| --- | --- | --- |
| openai/codex `codex-rs/tui/src/bottom_pane/approval_overlay.rs` · `chat_composer.rs` · `history_cell/`（github.com/openai/codex） | Apache-2.0 | 三态交互模式：审批=动作特定选项+显式决策事件+Esc≠继续；单输入框承载所有输入；输出=追加式历史 cell。**pattern-only**（Rust TUI→Web 场景，不抄代码） |
| picocss/pico `css/pico.min.css`（picocss.com） | MIT | token 思路：单一 `--spacing`/`--border-radius` 变量派生全局、语义化变量命名、系统字体栈。可抄变量取值思路（本轮仅借模式） |
| watercss/water.css `out/water.css`（watercss.kkga.de） | MIT | token 思路：语义色命名（text-main/text-muted/focus/border）+ `prefers-color-scheme` 明暗双套同名词。R11 主题轮按此扩展 |
| web.dev 离线清单（web.dev/learn/pwa，App Shell / offline fallback） | 文档 pattern-only | App Shell=静态壳同源自足、数据走 /api；资源一律 cache 可得。R12 借其"壳不依赖网络"的自检口径（本项目壳在本机，天然成立，故只收口不自建缓存） |
| Workbox precaching（developer.chrome.com/docs/workbox） | 文档 pattern-only | revision 清单 + install 预缓存 + cache-first 的失效思想。R12 借思想不引库；SW 本体经 K.5 评估为不做 |

## 13 轮总结表（ux round13 定稿 · 每轮一行：主题 / 落地物 / 验收态）

| 轮 | 主题 | 落地物 | 验收态 |
| --- | --- | --- | --- |
| R1 | 立规矩 | 本 spec：四公理 + `--cb-*` token + 术语/禁句表 + 12 轮路线图 + 基线体检（附录 A） | 只加文档与变量块，零布局改动；红线（零 CDN）全量盘点通过 |
| R2 | 三端统一输入框 | 一个 composer：自然语言 + `/` 指令 + `@` 文件 + 追问改方案 | 三端落地；Esc=取消；CI 标记不丢 |
| R3 | 八阶段时间线 | `CB_TL_STAGES` 八轨（理解→召唤→成箱→确认→拼柜→合规→落盘→收口）+ 附录 B 事件映射表 | 状态色蓝/绿/红 + 耗时；事件可回放 |
| R4 | 文书预览 | `cbDoc*` 组件 + vendored marked（附录 C，零 CDN） | 预览与导出逐字段一致；UNSPECIFIED「未提供」徽标 |
| R5 | HITL 审批卡 | `.cb-apr` 卡（附录 D）：确认/驳回/补参数动作特定三按钮 | Esc/关闭=驳回≠放行；决策写审计 |
| R6 | 跨运行审计时间线 | `[data-cb-audit-panel]`（附录 E）+ JSON 导出 | 任一运行可回放；签字/确认有时间戳与操作者 |
| R7 | 纠偏卡片 | `.cb-fix` 话术映射（附录 F）：code→人话→动作 | 恢复入口从时间线可达；断点续跑不丢状态 |
| R8 | 窄屏 + PWA | 768/375 断点 + 44px 触控 + manifest/icons（附录 G） | 768/375 下输入/流水线/审批卡全部可用 |
| R9 | 斜杠面板 | `/` 命令 + 66 岗检索（附录 H） | 面板零 CDN、键盘可达 |
| R10 | 空态引导 | 空态卡 + 三示例卡 + 三步 checklist + 网关兜底（附录 I）；顺手修 R6 致命 JS 语法错误 | 新用户 3 分钟首跑（demo_one_shot 同路径） |
| R11 | 双主题无障碍 | `--cb-*` 明暗两套 + aria-live + aria-label + focus-visible + 大字开关（附录 J） | 三端 token 接管；明暗切换零对比度回归 |
| R12 | 离线门禁 | 零外链断言 + JS 语法门禁进 CI + 断网专项 playwright（附录 K） | 断网全动线绿：外域请求 0、pageerror 0 |
| R13 | 终评定稿 | 本节总结表 + K.7 金线终验 + 已知边界清单 + CI 补 R5-R12 标记门禁 + README 证据链 | 金线 8/8 PASS；scorecard 8.85 不降；禁句检索为零 |

## 已知边界清单（ux round13 收官时点合并历史提醒，不阻断验收）

| 项 | 现状与边界 | 出处 |
| --- | --- | --- |
| 大字模式细节 | `html.cb-large` 把 `--cb-fs-*` 阶梯整体 +2 档（G.3）；只接了顶栏开关与持久化，**未逐组件走查大字下是否溢出**（表格/3D canvas 等宽内容可能需要横向滚动） | R8/R11 |
| 对比度残留 | 白字在 #059669/#ea580c 彩底按钮 3.8/3.6:1（浅色端既有值，大字粗体达标线内）；装饰性彩边 8 位 alpha hex、JS canvas 3D 渐变、TEAM_ROSTER 成员色、demo 暗色导轨、投标页 hero 深色洗底、文书工具条 #1e293b = 双主题恒定 chrome（附录 J.2 全表） | R11 |
| Rust 侧 audit 无耗时 | 审计节点行「耗时（ms）」仅 :8000 网关事件流有（B.5/E.4）；:8765 Rust 侧 run 节点无 duration_ms 字段，时间线该列留空，属数据源口径差异非前端缺陷 | R3/R6 |
| 下载文件名双 `.md` | LLM 工具链路偶发交付名 `xxx.md.md`（write_md 对已带 `.md` 的名字不重复追加，实际为模型传参携带）；仅影响下载建议文件名，内容与预览不受影响 | R13 金线实测 |
| SW 不做 | 结论与备复发 K.5：本机 serve 壳自足，cache-first 旧壳掩盖每轮 UI 更新且与网关 no-store 冲突 | R12 |
| 断网专项局限 | route abort 法覆盖"外链卡死/白屏/JS 挂"事故，不做防火墙级断网；loopback 服务可达是部署侧前提（K.4） | R12 |

## 附录 A：UX 基线体检（ux round1 只读盘点）

| 资产 | 位置 | 行数 | CSS | CDN | 字体 | 主色 | 响应式 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 宿主页 | `frontend/index.html` | 850 | 内联 `<style>`，`:root` 17 变量（light slate） | **零** | Segoe UI/PingFang SC/Microsoft YaHei | --brand #2563eb | 1 个 @media |
| 大 Team 工程台 | `frontend/workbench.html` | 4971 | 内联 `<style>`，`:root` 26 变量（dark） | **零** | 同上 + Cascadia/Consolas mono | --accent #5b8def | 5 个 @media |
| Rust :8765 工作台 | serve `demo/static/`（`workbench/src/api.rs:75` ServeDir；index.html 148 / styles.css 209 / app.js 677 / studio.js 304） | 1338 | styles.css `:root` 12 变量（light paper） | **零**（styles.css 同源外链） | 同上 | --accent #0f766e | 2 个 @media |
| civil.py CLI | `packing_assistant/civil.py` | 225 | argparse CLI（tui/exec/app/mcp/serve/skills/resume），非富文本 TUI | 零 | 终端 | — | — |

三端 `:root` 命名互不一致（--accent 三种蓝/青）、明暗主题分裂（workbench 深、其余浅）→ 由 R11 统一。

**专业合规元素现状**：

- `submit_blocked`：`frontend/index.html:642/797/815`（submitBlocked 状态）；`gateway/app.py:331/598`（submit_blocked_default=True）。**workbench.html 无呈现**（R5 补审批卡）。
- 免责声明：仅 `demo/static/index.html:16`「内部讨论草稿 · 不是签认件」。index/workbench 缺（R4/R5 补）。
- 签认：`demo/static/index.html` confirmOk 勾选「我明白，将由持证人员签认」；`packing_assistant/civil.py:21` CONFIRM 常量同句。无独立签字栏（R6 时间线补签认留痕）。
- UNSPECIFIED：哨兵遍布 `packing_assistant/runtime/*` 与 `expert_turn.py` 等；**前端无显示规则**（本轮 spec §3.2 定规则，R4 落地）。
- 术语：装柜（workbench.html 5 处）、作业单/交底少量出现；三端无统一术语表（本轮 §3.2 定稿）。
- CI frontend 断言（`.github/workflows/ci.yml:24-40,100-107`）要求 workbench.html 保留：`大 Team / org-chart / /api/pipeline / consumeSse / hitl_summary / draw3d / TEAM_ROSTER / /api/whatif / What-if / 订柜 N0 / 3D 用柜 / perCabinCog / secureWorkOrder / big_team_a_b / /api/whatif/apply / /api/profiles / POR 装柜单 / 应用为当前方案`；index.html 保留 `Civil Buddy / /workbench`。每轮改 UI 前先对照此清单。

## 附录 B：阶段时间线 · 事件→阶段映射表（ux round3 定稿，后续轮共用）

> R3 落地「一条流水线（进度可见）」：把 SSE 事件流映射为固定 8 阶段轨道。
> 组件为纯 vanilla JS/CSS，两端同构：`frontend/workbench.html`（Vue2 内联，`CB_TL_*` 常量）与
> `demo/static/app.js`（`cbTlCreate()` + `CB_TL_STAGES`/`CB_PHASE_STAGE`）。零 CDN、零外链。

### B.1 阶段轨道与状态色

固定轨道（顺序即流水线顺序，阶段可被打回重做，重做计入 `reruns` 徽记）：

```
理解任务 → 召唤岗位 → 成箱 → 人工确认 → 拼柜 → 合规校核 → 落盘 → 收口
```

| 状态 | 图标 | token 色 | 语义 |
| --- | --- | --- | --- |
| 待执行 | `·` 空心 | --cb-gray-400 | 未到达 |
| 进行中 | spinner（prefers-reduced-motion 时静态圆） | --cb-blue 工程蓝 | 事件流正在此阶段 |
| 完成 | `✓` | --cb-green 通过绿 | 阶段事件闭合 |
| 打回/待人工 | `⚠` | --cb-orange 安全橙 | replan 打回、HITL 等待、UNSPECIFIED——不藏、不红字恐慌 |
| 阻断 | `⛔` | --cb-red 合规红 | 仅 agent status=error 等硬失败 |

### B.2 借鉴来源（pattern-only）

| 来源 | 许可 | 借什么 |
| --- | --- | --- |
| Aider-AI/aider `aider/waiting.py` `Spinner`（github.com/Aider-AI/aider） | Apache-2.0 | 进行中=单行 spinner+文字标签，不打扰历史流；渲染降级（unicode→ASCII → web 场景 prefers-reduced-motion 去动画） |
| openai/codex `codex-rs/tui/src/chatwidget.rs` + `history_cell.rs`（github.com/openai/codex） | Apache-2.0 | 追加式会话流；进行中事件合并为单格（active_cell），完成后定格为不可变历史（时间线折叠为一行摘要）；幂等去重 |
| VS Code Tasks presentation（code.visualstudio.com/docs/debugtest/tasks） | 文档 pattern-only | 长输出默认折叠成一行、出问题才展开；reveal 语义映射为"展开/收起"按钮 |

### B.3 :8000 `/api/pipeline/stream` 事件映射（gateway/app.py → packing_assistant/teams/big_team.py、agent_loop.py）

SSE 形状：`data: {...}\n\n`（fetch+ReadableStream 解析，workbench `consumeSse`/`onStreamEvent`，WS 同源 HUB 复用同一处理）。

| 事件 type | 关键字段 | → 阶段 | 人话 |
| --- | --- | --- | --- |
| `run_start` | run_id, team_mode | 重置时间线；理解任务=进行中 | 开始装柜任务 |
| `agent_start` | node | 见节点映射表 B.4 | 阶段进入进行中 |
| `agent_end` | node, status, duration_ms, step.message | 同上；status=error→阻断红 | 阶段完成（子行记一句话结果+耗时） |
| `tool_start` / `tool_end` | node, tool, duration_ms | node 映射优先，tool 映射兜底 | 当前阶段下缩进子行：工具名+结果 |
| `hitl` | hitl_summary, session_id | 人工确认=进行中+高亮 | "等待人工确认…"（R5 审批卡挂载点 `[data-r5-approval-slot]`） |
| `replan` | message, replan_round | 合规校核=⚠ 安全橙 | critic 打回重做 |
| `debate` | message | 成箱子行 | 有界辩论 |
| `done` | public, summary, artifact_paths | 收口=完成；未完成的落盘补完成 | 折叠为一行摘要：`流水线完成 · Ns` + 数字 chips（订柜 N0/实装柜数/成箱箱数/风险裁决/ship_ok，**只抄事件数值**） |
| `replay_start` / `replay_done` | run_id | 重置（带「回放」徽标）/ 收口 | 历史回放 |
| `error` | message | 当前阶段 ⚠ + 子行 | 事件流报错 |

### B.4 :8000 节点/工具 → 阶段（big_team 团队节点 + agent_loop 白名单工具）

| node（agent_start/end） | 阶段 | | tool（llm_scheduler 模式） | 阶段 |
| --- | --- | --- | --- | --- |
| `intent` | 理解任务 | | `intent.interpret` / `knowledge.search` | 理解任务 |
| `orchestrator` | 召唤岗位 | | `container.select` | 召唤岗位 |
| `material_parser` `structure` `box_scheme` `present_team_a` `bounded_debate` | 成箱 | | `team_a.run` / `team_a.rebox` | 成箱 |
| `hitl_wait` / `user_confirm` | 人工确认 | | `hitl.check` / `hitl.confirm` | 人工确认 |
| `planner` `loader` `evaluator` | 拼柜 | | `team_b.plan_load_eval` | 拼柜 |
| `risk_compliance` `replan_critic` | 合规校核 | | `team_b.risk` / `replan.critic` | 合规校核 |
| `visualizer` | 落盘 | | `team_b.visualize`、`manifest*` `tms*` `booking*` `secure*` `docx*` `plan.export*` 等前缀 | 落盘 |
| `finalize_run` / `finalize` | 收口 | | `finalize.run` | 收口 |

兜底规则：未列出工具 → 附在**当前进行中阶段**的子行（计数于 `cbTl.fallbackTools`，不产生「未知」桶）。

### B.5 :8765 `/api/chat` 事件映射（workbench/src/api.rs、agent.rs）

SSE 形状：`event: <name>\ndata: {...}\n\n`（demo/static/app.js `streamChat`→`cbTlCreate()`）。

| event | payload.phase | → 阶段 | 人话 |
| --- | --- | --- | --- |
| `context` | — | （不进时间线：已有 ctx 仪表） | 上下文用量 |
| `status` | `understand` / `compress` / `import` | 理解任务 | 听懂为 run/chat；上下文压缩 |
| `status` | `summon` / `queue` / `plain` | 召唤岗位 | 已召唤某岗；独立专家 i/N；未点名岗位 |
| `status` | `harness` / `scheme_gate` | 成箱 | 一人公司成套 harness steps |
| `status` | `hitl_gate` / `hitl` / `confirm` | 人工确认（高亮+R5 占位） | HITL：专项未确认不出施工草稿 |
| `status` | `plan_load_eval` / `pack` | 拼柜 | 规划→装载→评估 |
| `status` | `risk` | 合规校核 | 出运门禁 |
| `status` | `exclusive` / `write` / `price` / `doc` / `deliver` | 落盘 | 专属出稿工具步（pack-ship__plan/export/health 等）、价表/草稿写盘 |
| `status` | `done` | 收口 | 收口 |
| `status` | `think` / `talk` / `chat` / 工具名（`search_kb` `read_kb` `list_kb` `web_search` `web_open` `list_attachments` `read_attachment` 等） | 当前阶段子行（不落「未知」） | 解释轮次/出稿后白话说明/工具一句话结果 |
| `token` | — | （正文流式渲染，不进时间线） | 正文输出 |
| `error` | — | 当前阶段 ⚠ 安全橙 | 报错不恐慌 |
| `done` | mode, deliverables | 收口=完成 | 整条折叠为一行摘要：`完成 · 一人公司成套/岗位收工/回答完毕 · 文书 N 份`（N 只抄 `deliverables.length`） |

### B.6 CI

frontend 断言沿用 §附录 A 的 18 标记清单；时间线组件不删任何标记。`frontend/workbench.html` 保留 `consumeSse`（R3 起重新用于 `/api/pipeline/stream` 全流程流式）与 `hitl_summary`。

## 附录 C：交付物文书预览组件（ux round4 定稿）

> R4 落地「一份正式交付物」：聊天/工作台里的 markdown 交付物按正式文书呈现，
> 而非终端输出。两端同构 vanilla 组件；零 CDN、零外链字体。

### C.1 组件结构（`cbDocOpen` / `cbDocClose` / `cbDocDecorate`）

| 端 | 组件源 | 样式 | 接入点 |
| --- | --- | --- | --- |
| :8765 | `demo/static/docpreview.js`（canonical） | `demo/static/styles.css` 追加 `.cb-doc-*` 块 | `index.html` 引入；`app.js` done 后聊天流内交付物卡片（预览/下载），侧栏 `#files` .md 点开即预览（`/api/file`，out_root 内） |
| :8000 | `frontend/vendor/cb-doc.js`（同构副本，头部注明 canonical） | `frontend/workbench.html` 内联同构 CSS 块 | `workbench.html` done 事件捕获 `ev.artifact_paths` → 「交付物文书」面板（`[data-cb-doc-panel]`），`openDocArtifact` 走 `GET /api/artifact`（gateway/app.py，限 ROOT 内 .md/.markdown/.json/.txt/.jsonl 文本） |

DOM 结构（追加到 `document.body`，打印时独占文档流）：

```
.cb-doc-overlay[hidden] > .cb-doc-backdrop + .cb-doc-modal
  .cb-doc-toolbar（标题/字符数 · 复制 Markdown / 下载 .md / 打印存 PDF / 关闭）
  .cb-doc-scroll > article.cb-doc-page（白底 A4 版心 794×1000）
    header.cb-doc-head   页眉：项目名 + 岗位（等宽）
    .cb-doc-body.cb-doc-md   marked 渲染正文（经 sanitize：去 script/iframe/on*）
    footer.cb-doc-foot   页脚：生成时间 + 「ux(round4) 文书预览 v1」
                         + .cb-doc-disclaim「内部讨论 AI 草稿 · 不签认」（合规红，常驻）
```

### C.2 版式与诚实元素

- 版式：正文系统仿宋/宋体栈（`FangSong → 仿宋_GB2312 → SimSun → …`），标题黑体栈，表格实线边框（GB/T 9704 工程文书惯例 pattern，自拟）；零外链字体。
- 诚实元素（spec §3.2 落地）：`UNSPECIFIED`/待填类 → 安全橙徽章「未提供」（title 保留原文哨兵）；`[A001]` → 橙色虚线锚点徽章；标题含「工具计算/回传/只抄/非本岗编造」的小节 → 浅蓝底 + `tool-computed` 徽记；正文首段含「不构成/仅供内部讨论/不是签认」→ 合规红横幅样式。
- 打印：`@media print` 隐藏聊天/工作台 chrome 只留文书本体；页眉页脚 `position:fixed` 每页复现。

### C.3 vendored 依赖清单（零 CDN，运行时全部同源）

| 名 | 版本 | 体积 | 许可 | 来源 URL | 落位 |
| --- | --- | --- | --- | --- | --- |
| marked | 12.0.2 | 35,479 B | MIT | https://registry.npmjs.org/marked/-/marked-12.0.2.tgz | `demo/static/vendor/marked.min.js` + `frontend/vendor/marked.min.js`（LICENSE 同目录） |
| vue | 2.7.16 | 107,679 B | MIT | https://registry.npmjs.org/vue/-/vue-2.7.16.tgz | `frontend/vendor/vue.min.js` + `vue.LICENSE`（**修复**：workbench.html 原直连 jsdelivr CDN，违背零外链红线，本轮改同源引用） |

`frontend/vendor/cb-doc.js` 为 `demo/static/docpreview.js` 同构副本（非第三方依赖，改动须同步两份）。

## 附录 D：HITL 审批卡组件（ux round5 定稿）

> R5 落地「一道审批门」：把"等确认"从文本提示变成正式审批交互。
> 契约（承 U-R1 / codex `approval_overlay.rs` pattern-only）：**审批必须 = 显式决策事件 + Esc/关闭=驳回永不放行 + 决策进审计链**。
> 组件为两端同构 vanilla 实现，零 CDN、零外链、全 token 化。

### D.1 组件结构（`.cb-apr`，挂载点 `[data-r5-approval-slot]`）

| 端 | 挂载位置 | 实现 |
| --- | --- | --- |
| :8000 | `frontend/workbench.html` 时间线「人工确认」阶段（`#cb-hitl-slot`，`cbTlOnEvent` 的 `type==='hitl'` 分支挂载） | Vue2 内联组件（`cbApr` 状态 + `cbAprConfirm/cbAprReject/cbAprLater/cbAprEsc`） |
| :8765 | `demo/static/app.js` 聊天流内本条消息的时间线（`cbTlCreate` 的 `mountApproval`，`done` 事件 `hitl.pending=true` 时挂载） | vanilla DOM + `CB_APR_WAITING` 全局 Esc 注册表 |

```
.cb-apr（合规红左边条 .cb-apr-bar；approved→绿、rejected→橙）
  .cb-apr-head   标题「人工确认 · 成箱方案」+ 风险徽章（high=橙 / low=灰）+ 状态徽 + ✕
  .cb-apr-body
    .cb-apr-chips    方案摘要：只抄事件/状态字段（N0*、柜型、箱数、材料数、毛重、can_fit 若有）——不编数字
    .cb-apr-block    风险与阻断（非标 overall/ship_gate、结构不通过、待详设、超长、VGM 待签；无则直说"工具未报告阻断项"）
    .cb-apr-actions  决策区三按钮：确认并拼柜（主，--cb-blue）/ 驳回（红 --cb-red）/ 稍后（灰）
    .cb-apr-foot     Esc/关闭=驳回永不放行 · 高风险岗提示确认句「我明白，将由持证人员签认」（流程层已强制门禁，UI 只提示不重复实现）
  审计行 .cb-tl-audit-row（追加式、折叠态常驻、永不可折叠消失；R6 全量审计时间线承接）
    格式：`审计 · <决策>（<理由>） · YYYY-MM-DD HH:MM:SS · 操作者=本地用户 · 未静默放行`
```

### D.2 决策事件与三态

| 态 | 触发 | 事件 | 后续 |
| --- | --- | --- | --- |
| 等待 waiting | `hitl` 事件 / `hitl.pending` | 无决策，checkpoint 已落盘（durable） | 「稍后」折叠为等待条，可重新展开；刷新回来自恢复 |
| 已确认 approved | 显式点「确认并拼柜」 | :8000 `POST /api/confirm action=confirm`（既有链路）；:8765 勾选 confirm_ok 句并重提原文 | 拼柜继续；卡片定格「已确认」+ 审计行 |
| 已驳回 rejected | 显点点「驳回」/ ✕ / **Esc（全局）** | :8000 `POST /api/confirm action=cancel`（引擎 `phase=cancelled`）；:8765 不重提、不出稿 | 橙 ⚠ need_revision 式呈现「已驳回 · 未放行 · 请修改输入后重跑」，不粉饰；审计行 |

- Esc 映射=Cancel（永不静默变成"继续"）；关闭按钮与 Esc 同语义；网络失败不改变本地驳回判定。
- 高风险 = `hitl_summary.cards[].level∈{warn,err}` 或结构不通过 / 超长 / 非标 FAIL·WARN·NEED_DESIGN；:8765 闸门（scheme/exclusive_write）按 expert.risk=high 恒为高风险。
- 移动端：`@media (max-width: 768px)` 决策按钮全宽纵排（R8 打底）。

### D.3 借鉴来源（pattern-only）

| 来源 | 许可 | 借什么 |
| --- | --- | --- |
| openai/codex `approval_overlay.rs`（github.com/openai/codex） | Apache-2.0 | 选择必发显式决策事件；Esc=Cancel 不随键位变；动作特定选项（confirm/reject/later）；决策插入历史 cell（→审计行） |
| GitHub PR review 三态（approve/request changes/comment） | 交互 pattern | 三态决策区 + 决策可追溯；驳回必须给理由位（此处由引擎状态承载） |
| Temporal/Argo 人工审批节点 | 交互 pattern | 等待卡常驻 + 超时/升级提示语义（此处为"durable checkpoint 可安全关闭后 resume"提示） |

## 附录 E：跨运行审计时间线组件（ux round6 定稿）

> R6 落地「任一运行可回放全部事件；签认/确认有时间戳与操作者」：把 U-R5 单次运行内的审计行
> 扩展为**跨运行全量可回放审计时间线**——"谁来都得说清楚 AI 做了什么、人批了什么"。
> 两端同构 vanilla 实现，零 CDN、零外链、全 token 化；数据端点**只读**，越界 403。

### E.1 数据源与关联键（全部只读）

| 数据源 | 位置 | 关键字段 | 说明 |
| --- | --- | --- | --- |
| 事件流 trace.jsonl | `output/runs/<run_id>/trace.jsonl`（:8000） | `type, run_id, session_id, seq, ts, t_ms, duration_ms, status, node, tool` | `packing.stream.v1`；每事件带 session_id → **跨 run 串联键 = session_id** |
| HITL checkpoint | `output/runs/<run_id>/checkpoint.json`（:8000） | `user_action(confirm/cancel), status, phase, saved_at` | U-R5 交接：服务端已持久 user_action → 决策节点的权威来源 |
| session 索引 | `output/sessions/<session_id>.json`（:8000） | `run_id, status, saved_at` | 无 session 参数时返回最近会话列表 |
| demo trace | `demo/out/<session>/runs/<run_id>/trace.json`（:8765） | `steps[]{expert,tool,ok,legal,note}, hitl{required,confirmed,pending,gate}` | Rust Run 结构；run 间排序用目录 mtime |

### E.2 端点（只读聚合）

| 端 | 端点 | 实现 |
| --- | --- | --- |
| :8000 | `GET /api/audit?session=<id>`（`gateway/app.py`） | 聚合 trace.jsonl + checkpoint.json + session 索引；tool_start/end 成对合并；无 session → 最近会话列表；`..`、路径分隔符、控制字符 → **403**；schema=`civil.audit.v1` |
| :8765 | `GET /api/harness/audit/{session}`（`workbench/src/api.rs` + `harness.rs::audit_session`） | 扫描 `demo/out/<session>/runs/*/trace.json`；同语义 403；schema=`civil.audit.v1` |

### E.3 节点类型 → 配色 → 来源映射（四色）

| kind | 色 | 语义 | :8000 来源事件 | :8765 来源 |
| --- | --- | --- | --- | --- |
| `tool` 工具执行 | --cb-blue 蓝 | AI 做了什么（岗位节点/工具步/辩论） | `agent_start/end`（合并）、`tool_start/end`（合并）、`debate` | `steps[]`（ok 且 note 非"已写入"） |
| `decision` 人工决策 | --cb-red 合规红边 | 人批了什么（**永久置顶 · 不可折叠**） | `hitl`（等待）+ checkpoint `user_action`（confirm=放行 / cancel=未放行） | `Run.hitl`（confirmed/pending/rejected） |
| `error` 错误/重试 | --cb-orange 橙 | 失败与打回，不藏不恐慌 | `status=error` 的事件、`replan` | `steps[]`（ok=false 或 legal=false 非法工具拦截） |
| `write` 写盘 | --cb-green 绿 | 产物落了哪些盘 | `tool_end` 工具名匹配落盘前缀（承附录 B.4：`manifest* tms* booking* secure* docx* plan.export* export* vgm* …`）、`done`（artifact_paths） | `steps[]`（note 含「已写入」） |

节点行：`类型徽 + 一句话摘要 + 耗时（ms，仅 :8000 有）`；点「原始」展开 JSON 负载（折叠模式同 U-R3/U-R4）。
**决策节点永久置顶**：面板顶部固定"人工决策"区块，跨 run 汇总（标题/时间/run_id/操作者=本地用户 · 未静默放行），不随折叠消失。

### E.4 组件与导出

| 端 | 组件 | 样式 |
| --- | --- | --- |
| :8000 | `frontend/workbench.html` 历史页 `[data-cb-audit-panel]`（Vue2：`loadAudit/auditRunsDesc/auditToggleRaw/auditExport`；session 框默认跟随当前 sessionId） | `.cb-audit-*` 内联块 |
| :8765 | `demo/static/index.html` 侧栏 `#auditPanel` + `app.js`（`loadAuditPanel/auditNodeEl/refreshAuditSoon`；done 事件后自动刷新） | `demo/static/styles.css` `.cb-audit-*` 块 |

- 分组：按 session 一次加载全部匹配 run（新 run 在上），run 内节点按 `ts/seq` 时间正序（可回放）。
- 导出（人机协同履历表素材）：「复制 JSON / 下载 JSON」→ `{schema, exported_at, product, session_id, counts, decisions, runs[]}`，文件名 `audit-<session>.json`；只抄数据源字段，不编数字。
- 借鉴来源（pattern-only）：Langfuse session→trace→observation 分层与单列 trace log view（MIT）、Argo Workflows 节点时间线与重试标记（Apache-2.0）、Git 提交图纵向时间轴+泳道分组（pattern）。

## 附录 F：纠偏卡片 · 错误与恢复话术映射（ux round7 定稿）

> R7 落地「错误恢复」门面：把散在事件流/日志里的拒绝、熔断、降级、缺数变成**用户能行动的卡片**
> （海之子杯评审维度二：AI 纠偏管理）。统一结构三段式：
> **发生了什么（一句人话）+ 为什么（策略 code/规则名，等宽）+ 现在能做什么（≤3 条动作/指引）**。
> 两端同构 vanilla 实现，零 CDN、零外链、全 `--cb-*` token；动作**预填输入框草稿，不自动发送**（重试除外=重放同 payload）。

### F.1 组件结构（`.cb-fix`）

| 端 | 分类/渲染源 | 挂载点 |
| --- | --- | --- |
| :8765 | `demo/static/fixcard.js`（canonical：`CB_FIX.classify/classifyMissing/cardEl`）+ `app.js` 端侧动作句柄 | 聊天流内失败消息体后（catch / SSE error）；`done` 正文含哨兵时挂缺数提示条 |
| :8000 | `frontend/vendor/cb-fix.js`（同构副本，改动须同步两份）+ Vue2 组件 `cb-fix-card` | 时间线错误位（`cbTlOnEvent` error/agent_end）、总览报错条下方、`revise-nl` unsupported、`done` public 缺数提示条 |

```
.cb-fix（左边条：拦截/熔断=合规红，其余=安全橙；role=alert）
  .cb-fix-head   徽章（已拦截/已熔断/已降级/可重试/暂不支持/缺数/合规阻断/失败）+ 一句人话标题
  .cb-fix-why    code 徽片（等宽，如 deny_cross_expert）+ 规则一句话
  .cb-fix-meta   重试/退避信息——只抄事件字段（attempts、audit 动作序列 call→retry→degrade），不编
  .cb-fix-what   「现在能做什么」≤3：按钮（prefill/retry）或纯指引（note）
  .cb-fix-raw    <details>「原始记录」折叠原文（一次点击看原始负载，Sentry 卡模式）
```

### F.2 话术映射表（code → 人话 → 动作；模式对准后端 reason 原文，不编数字）

| code（来源） | 触发原文（摘要） | 发生了什么（人话） | 现在能做什么（≤3） |
| --- | --- | --- | --- |
| `deny_chat_write`（policy.py） | 拒绝：提问回合不能调写盘工具 X | 这是提问回合，AI 不会写盘：X 被策略拦下 | [改成出稿任务]预填「写一份 」；指引：说成「写一份…」即 run 意图 |
| `deny_cross_expert` | 拒绝：岗 A 不能调 T（exclusive 属于 B） | 岗 A 越权调了 B 的专属工具 T，已拦截 | [召唤 @B]预填「@B 」；指引：或改写成 B 岗的活 |
| `circuit_open` | 熔断：工具 T 连续失败 n 次 | 工具 T 连着失败 n 次，先熔断止损 | [重试]重放同 payload；指引：稍后再试/换个说法 |
| `deny_budget` | 熔断：session 成本超限 steps s/ms tokens t/mt | 本轮预算用完（steps s/ms · tokens t/mt），已停下 | [缩短输入重跑]预填；[新开会话]（:8765）；指引 |
| `deny_production` | 拒绝：目标 P 视为生产数据 | 目标 P 是生产数据区，写入被拒 | 指引：输出只落本次运行输出目录，禁 D:\layout / prod |
| `deny_secret` | 拒绝：…secret/.env | 目标碰到密钥/敏感文件，写入被拒，文件未落地 | 指引：密钥永不写盘，走环境变量/密钥管理 |
| `deny_sandbox` | 拒绝：…沙箱/越界 | 操作超出本次运行的沙箱范围，被拦下 | 指引：读写限于本次会话工作区 |
| `deny_cancelled` / `deny_unknown` | 已取消 / 未知工具 X | 任务已取消未执行 / 调用了未注册工具 X | 指引（检查工具名/已取消） |
| `invalid_args` | 拒绝：工具 T 缺少参数 K | 工具 T 缺参数 K，没跑成 | [重试]；指引：参数由调用方组装，AI 不编 |
| `revise_unsupported`（nl_revision） | 无此功能：…（status=unsupported） | 这个改法还不会：方案保持原样，没有假装成功 | hints[] 预填 chips（≤2）+ 能力清单 note（总 ≤3） |
| `recovery_degrade`（recovery.py） | 下游失败 code，工具 T 降级，不编柜数 | 工具 T 重试后仍失败，已降级：数字标「未提供」，不编造 | [重试本工具]；指引；meta=共尝试 n 次 · call→retry→degrade |
| `timeout` | 失败：工具 T 下游超时（t s） | 工具 T 等了 t 秒没回应 | [重试]重放同 payload；指引 |
| `compliance_block`（risk/BOX 类） | 阻断/非标/ship_gate/废标/超载 | 合规校核拦下，不是工具坏了：按下面改即可重跑 | [改箱型重跑]预填；[减载重重跑]预填；指引：阻断项见原文 |
| `missing_data`（UNSPECIFIED/[A001] 扫描） | done 正文/public 含哨兵 | 还有 n 处数据未提供——补一句话即可重跑 | [去补数]预填「补充：[Axxx] …」；指引：发送即重跑，缺的数 AI 不编 |
| （兜底）`error` | 其余报错 | 原文截断呈现 | 可重试则[重试]，否则修改输入重跑指引 |

### F.3 交互红线（承公理 3/4）

- 动作按钮一律**预填草稿，不自动发送**；唯一例外=重试（重放同 payload，:8765 重发 `state.lastSend`，:8000 重跑 `runPipelineTrace`）。
- 「重试次数与退避」只抄 middleware/recovery 事件字段（`attempts`/`audit`），前端不编、不估算。
- 合规阻断不粉饰成"失败了"：列出阻断原文 + 改箱型/减载重引导；熔断写明止损语义与预算数字。
- 错误不用 toast（自动消失不可达），一律常驻卡片 + 折叠原文；Esc/刷新不丢失（卡片随历史消息定格）。

### F.4 借鉴来源（pattern-only）

| 来源 | 许可 | 借什么 |
| --- | --- | --- |
| Sentry issue 卡（docs.sentry.io/product/issues/issue-details） | 文档 pattern-only | 卡头=类型+上下文；动作直接在卡上；「原始记录」一次点击展开（堆栈→原始 reason） |
| NN/g Error-Message Guidelines | 文档 pattern-only | 错误三要素：可见、建设性（说清下一步）、尊重用户付出；禁"something went wrong"式空话 |
| GitHub Primer 禁 toast 共识 / toasts-are-bad-UX 辩论 | 文档 pattern-only | 错误与需行动的决策不用自动消失的 toast，用常驻卡 |
| Linear 乐观更新+持久重试入口 | 文档 pattern-only | 失败可回滚重试：卡上直接给「重试」，重放同 payload |
| openai/codex `exec_cell`/`history_cell` | Apache-2.0 | 错误=不可变历史 cell 的一部分，重试不改写历史只追加（承 R3 追加式会话流） |

## 附录 G：窄屏 / 工地手机适配（ux round8 定稿）

> R8 落地验收（§4 路线图）：768/375 宽度下输入、流水线、审批卡全部可用。
> 真实场景：项目主任/工友在工地用手机**看进度、批 HITL、看交底**——落地可行性的直观证据。
> 借鉴 pattern-only：GitHub Primer（min viewport 320px、移动触控目标 44px 到 AAA、
> 内容定断点、constrained fluid layout）、antd mobile / TDesign Mobile（列表式/卡片式布局、
> 卡片纵排、主操作全宽按钮）、PWA 最小件（manifest + theme-color；SW 离线缓存评估结论=不做，见附录 K.5）。

### G.1 断点 token（两端 `:root` 已落）

| token | 值 | 档位 | 说明 |
| --- | --- | --- | --- |
| `--cb-bp-mobile` | 430px | ≤430 手机竖屏 | 375/414 落此档；时间线缩轨、审计节点单列 |
| `--cb-bp-tablet` | 768px | 431-768 平板 | 三栏折叠、触控 44px、正文 14px/1.6 |
| `--cb-bp-desktop` | 769px | ≥769 桌面 | 不收紧（桌面布局原样） |

媒体查询条件不支持 `var()`：规则内使用字面量并注释 token 名。附录 A 基线里散落的旧断点
（workbench 720/640/1100/820/768、demo 1100/720/768）语义归并入三档，旧规则保留不动；
round8 新增规则一律 `@media screen and (max-width: …)`——**打印仍走 `@media print` 的 A4 版式**。

### G.2 关键动线窄屏规则（优先级从高到低）

| 动线 | ≤430（手机竖屏） | 431-768（平板） | 桌面不变 |
| --- | --- | --- | --- |
| 聊天+时间线+审批卡 | 时间线左轨道缩窄（子行 margin 10→4px）；composer 按钮整行 | 审批卡决策区纵排全宽、主决策 48px（附录 D.2 打底补齐）；正文 14px/1.6 | 原样 |
| 文书预览 | A4 版心退化为流式宽（794→100%）、页边距 56/60→16/24 | 同左；`.cb-doc-md table` 块级横向滚动 + 粘性首列 | A4 版心 |
| 审计面板 | 节点 flex-wrap 单列（徽标/摘要/时间竖排）、JSON 展开全宽 | 触控 ≥44px | 原样 |
| 顶部导航 | 汉堡键 | workbench ≤820 汉堡键开 `.sidebar.mobile-open`（复用既有 CSS，`☰ 菜单`）；页签横向滚动；demo ≤768 汉堡键开 `.rail.mobile-open`（`☰ 栏目`）；顶栏换行 | 原样 |
| 表格（材料表） | 横向滚动 | `table.data` 块级横向滚动 + 粘性首列 | 原样 |

### G.3 触控与可读性

- 可点目标 `min-height: var(--cb-touch-min)` = 44px（Primer：视觉 32px 按钮在移动端须垫高到 44 才达 AAA）；
  审批卡主决策 `var(--cb-touch-lg)` = 48px（拇指优先）。
- 正文 ≥14px、行高 1.6（≤768 生效：聊天正文/交底/纠偏卡话术）；徽标、时间戳等辅助信息可 10-12px。
- **大字模式钩子**：`html.cb-large` 把 `--cb-fs-*` 字号阶梯整体 +2 档
  （xs 12→14、sm 13→16、md 14→18、lg 16→22、xl 18→28、2xl 22→32、3xl 28→36），
  两端 CSS 已落，R11 主题轮在此之上接明暗切换即可。

### G.4 PWA 最小件（不做 Service Worker，离线缓存留 R12）

- `manifest.webmanifest` 两端同构（`frontend/` 与 `demo/static/` 各一份，改动须同步）：
  `name=土木伙伴 Civil Buddy`（中文）、`display=standalone`、`theme_color=#2563eb`、
  `background_color=#f8fafc`、`start_url=/`、icons（SVG any + PNG 192/512 maskable）。
- 图标：`icons/cb-icon.svg` + `icons/cb-icon-{192,512}.png`（`scripts/gen_pwa_icons.py` 纯标准库生成，零第三方）。
- head 三件：`<meta name="viewport" … viewport-fit=cover>`（刘海屏安全区）+ `<meta name="theme-color" content="#2563eb">` + `<link rel="manifest">`。
- 服务：:8000 由 gateway `app.mount("/static", frontend/)` 提供清单；:8765 由 `ServeDir(demo/static)` 同理——零新端点、零 CDN。
- 本轮顺手修红线遗留：`frontend/index.html` 的 jsdelivr CDN `<script>` 换成同源 vendored 副本（承附录 C.3）。

### G.5 借鉴来源（pattern-only）

| 来源 | 许可 | 借什么 |
| --- | --- | --- |
| GitHub Primer Responsive/Layout foundations（primer.style） | 文档 pattern-only | min viewport 320px 起步；移动触控目标垫到 44px（AAA）；内容定断点、constrained fluid、按可用性收拢布局 |
| antd mobile（mobile.ant.design）+ 设计稿 750=2×375 惯例 | MIT | 卡片式/列表式布局；移动端主操作全宽按钮；整卡纵排 |
| Tencent TDesign Mobile（tdesign.tencent.com） | 文档 pattern-only | 样式属性全部收敛进主题 token（对齐 --cb-* 命名空间做法） |
| web.dev / MDN Web App Manifest | 文档 pattern-only | manifest 最小字段：name/short_name + start_url + display + theme_color + icons |

## 附录 H：快捷指令面板 · / 命令 + 常用任务模板（ux round9 定稿）

> R9 落地「指令面板」：输入 `/` 弹命令面板（与 R2 @岗补全同一浮层体系）——老手直达、新手有路。
> 两端同构（:8000 `frontend/workbench.html` Vue 内联 / :8765 `demo/static/app.js` vanilla），
> 零 CDN、零外链、全 `--cb-*` token、全键盘可达。**面板任何动作都不自动发送**（承附录 F.3 红线）。

### H.1 交互模式

| 行为 | 落法 | 来源（pattern-only） |
| --- | --- | --- |
| 唤起 | 输入 `/`（行首/空白后）即弹面板；继续输入按名过滤 | codex slash popup |
| 模糊过滤+排序 | 前缀命中(0) > 中文子串命中(1) > 子序列命中(3)；空查询=原序全量；别名命中不重复展示 | VS Code Command Palette「模糊必须带排序」 |
| 键盘 | ↑↓ 移动、Enter/Tab 确认、Esc 关闭（关闭记住 token，编辑后才重开——同 @岗）；票子菜单内 Esc=返回命令层 | codex / Raycast「子菜单 Esc=返回上一级」 |
| 模板填空 | 选中模板命令 → 整框替换为带 `<待填>` 占位的结构化草稿，先改后发 | GitHub Saved replies / Issue templates、Raycast argument placeholder |
| 最近任务 | 面板顶部「最近 3 条任务」（localStorage `cb_recent_tasks_v1`，去重存 8 条），点击重填不发送 | VS Code palette recency |
| 老手直达 | 直接发送 `/pack <票名>`、`/bid <要点>`、`/safety <要点>` → 发送前展开成任务文本再走正常链路；`/audit /doc /eval` 就地执行不发送；未带参给用法提示 | codex Tab-queue 语义（就地化） |

### H.2 命令清单（两端同构；图标 = `--cb-*` 色块 + 单字，不引图标库）

| 命令 | 图标/色 | 名称 | 一句说明 | 动作 |
| --- | --- | --- | --- | --- |
| `/pack` | 装·蓝 | 装箱拼柜 | 选 sim_materials 票，预填装柜任务 | 票选择子菜单 → 模板 |
| `/bid` | 标·深蓝 | 招标解析 | 预填招标解析模板（@招标解析） | 模板 |
| `/safety` | 安·安全橙 | 安全交底 | 预填班前白话交底模板 | 模板 |
| `/audit` | 审·合规红 | 审计面板 | 打开跨运行审计时间线 | 跳转（附录 E 面板） |
| `/doc` | 文·通过绿 | 最近交付物 | 预览最近一份交付物文书 | 跳转（附录 C 预览） |
| `/eval` | 评·中性灰 | 记分卡摘要 | 竞赛记分卡 / 离线自检 | 跳转（见 H.4） |
| （:8765 附加）`/skills /new /threads /bg /help` | 灰 | 既有客户端命令 | 面板给发现性；确认后预填命令文本 | 预填命令 |

### H.3 模板文案（`<待填>` 占位；数字一概不预编）

- `/pack`（选中票 `<票名>` 后）：`pack test/sim_materials/<票名>/materials.xlsx（<story>）` + `要求：40HQ 高利用率装柜；柜数与坐标由 tools 计算，模型不摆箱子；出装柜单草稿，须人工确认后才拼柜。`（:8000 网关 `_load_materials_from_text` 原生解析 NL 中的仓库相对 xlsx 路径——模板路径即真实可用数据路径）
- `/bid`：`@招标解析 解析这份招标文件：项目名称：<待填> 关键条款：<待填：工期 / 资质 / 报价上限> 请列出资格条件与废标项清单；P0 资格须人工确认，是否投、怎么投由人决定。`
- `/safety`：`@安全交底 写一份班前白话交底：作业内容：<待填> 主要风险与防护：<待填> 给工友的白话版，一条一个动作；先讨论，说「写一份」才出草稿。`

### H.4 数据源

| 数据 | 来源 | 说明 |
| --- | --- | --- |
| 票选择子菜单（/pack 第二级） | `test/sim_materials/*/materials.xlsx` 磁盘枚举 + `INDEX.json` 元数据 | 由 `scripts/gen_cb_tickets.py` 生成静态清单（gen_cb_posts.py 同款双产物）：`demo/static/tickets.js`（window.CB_TICKETS）+ workbench 内嵌 BEGIN/END 块；**无目录枚举端点**（:8000 `/api/artifact` 只读文件、:8765 `/api/file` 仅 out_root），故走静态清单，与磁盘一致由生成脚本保证 |
| 最近任务 | `localStorage["cb_recent_tasks_v1"]` | 记录时机：:8765 form submit、:8000 runTeamA/runPipelineTrace；仅本地，不上传 |
| /audit | :8000 `POST 无`（Vue 跳历史页 + `loadAudit()`）；:8765 `#auditPanel.scrollIntoView` + `loadAuditPanel()` | 附录 E 既有只读端点 |
| /doc | :8000 `docArtifacts[0]` → `GET /api/artifact`；:8765 `done` 事件 deliverables 最近一份 → `/api/file` | 附录 C 既有预览组件 |
| /eval | :8000 `GET /api/artifact?path=output/competition/scorecard_latest.json` → cbDocOpen 渲染摘要；:8765 `GET /api/eval/live` → 状态行闸门通过数（只抄返回值） | 无新端点 |

### H.5 借鉴来源（pattern-only）

| 来源 | 许可 | 借什么 |
| --- | --- | --- |
| openai/codex `slash_command.rs` / `command_popup.rs`（github.com/openai/codex） | Apache-2.0 | 输入 / 弹浮层、按名过滤、命令行=名称+一句描述、别名不重复展示、Esc 关闭记忆 token |
| VS Code Command Palette（code.visualstudio.com、issue #1964） | 文档 pattern-only | 模糊过滤必须带模糊排序（前缀>子串>子序列）；全程键盘可达；最近项前置 |
| Raycast Manual：Search Bar / Arguments / Snippets | 文档 pattern-only | 别名直达；参数 placeholder 提示；子菜单 Esc=返回上一级 |
| GitHub Saved replies / Issue templates | 文档 pattern-only | 模板选择器（名称+描述）；选中=插入带占位草稿，先编辑后提交 |

## 附录 I：空态与三步新手引导（ux round10 定稿）

> R10 落地「引导」门面（§4 路线图 R10）：评委/试用者打开页面 30 秒内知道
> **这是什么、能干什么、第一步点哪**。组件两端同构 vanilla 实现
> （:8765 `demo/static/index.html`+`app.js`+`styles.css`；:8000 `frontend/workbench.html` Vue 内联），
> 零 CDN、零外链、全 `--cb-*` token；示例卡动作一律**预填不自动发送**（承附录 F.3 红线）。

### I.1 空态卡（无任何会话/无结果时）

结构（shadcn/ui Empty / Tailwind UI empty state 四段式 pattern-only：图标+定位句+主动作+诚实小字）：

```
.cb-empty
  .cb-empty-media    "CB" 图标块（--cb-blue 底白字）
  .cb-empty-title    土木版 Codex：66 岗工作台
  .cb-empty-desc     工具算数、模型只路由。输入任务，或点一张示例卡预填（不会自动发送）。
  .cb-empty-cards    三张示例任务卡（等宽 grid，≤768 单列；点击=预填输入框，不发送）：
    tone-blue   装  /pack 装一张票      sim_materials 票 → 装柜单草稿，柜数由 tools 算（预填真实小票 small_one_container）
    tone-strong 标  /bid 解析一段招标    资格条件与废标项清单 · P0 资格须人工确认
    tone-orange 安  /safety 出安全交底   工友班前三分钟白话，一条一个动作
  .cb-empty-note     产出永远是 AI 草稿，高风险岗需人工确认。
```

- :8765 挂在 `#log`（原一行 `.welcome` 欢迎语升级为本卡，**保留 `.welcome` 类**——发送首条消息时沿用既有移除逻辑，空态卡即 Codex 历史流的第 0 号 cell）；示例卡点击走 `cbEmptyPrefill()`，复用附录 H `cbSlashTemplate` 生成草稿。
- :8000 挂在 `workspace-inner`（`v-if="!hasAnyResult"`，简洁/完整两种模式都显示，位于演示剧本/empty-hero 之后）；点击走 `cbEmptySample()` → 左栏 composer 预填（`cmdApplyDraft`），不发送。
- 三卡预填文案=附录 H.3 模板原文，`<待填>` 占位数字一概不预编。

### I.2 三步引导 checklist（首访一次性）

- 存储：`localStorage["cb_onboarded_v1"]` = `{s:[b,b,b], done, dismissed}`；键不存在=首访自动弹出；✕ 关闭=不再自动弹出；右上角 **?** 按钮（:8765 顶栏 `#onboardHelp` / :8000 顶栏 `@click="cbObReopen"`）随时重开；全部完成 → 1.2s 后自动收起。
- 三步与打勾条件（只抄真实交互事件，不靠估算）：

| 步 | 文案 | 打勾触发（:8765） | 打勾触发（:8000） |
| --- | --- | --- | --- |
| ① | 输入任务，或点一张示例卡（预填，不自动发送） | `#input` input 非空、`data-fill` chips、指令面板/最近任务 `cbCmdApplyDraft`、示例卡 `cbEmptyPrefill` | `watch: userInput` 非空（打字/示例卡/面板预填同源） |
| ② | 看时间线跑完：8 阶段收口 ✓ | `streamChat` done 事件 | `onStreamEvent` type=`done`/`replay_done` |
| ③ | 在审批卡点确认 · 或文书预览 / 下载 .md | 审批卡 `cb-apr-confirm` 点击、`openDeliverable`、交付物卡「下载」、预览层「下载 .md」（`docpreview.js` 调 `global.cbObStep(3)`） | `cbAprConfirm`（显式确认并拼柜）、`openDocArtifact`、预览层「下载 .md」（`vendor/cb-doc.js` 同构钩子） |

- 未完成的下一步高亮蓝边（`li.now`），已完成划线灰化（`li.on`）；卡脚常驻诚实句「产出永远是 AI 草稿，高风险岗需人工确认 · 全部完成自动收起」。
- R10 路线图验收「新用户 3 分钟内完成首跑（demo_one_shot 同路径）」：示例卡预填的 `/pack small_one_container` 即 demo 同款真实数据路径，发送即走 `runTeamA`/`/api/chat` 正常链路，与演示共用一条管线。

### I.3 网关兜底空态（加载失败 / 后端未起）

触发条件与文案（纠偏卡三段式：发生了什么 + 为什么 + 现在能做什么，承附录 F；命令一键复制）：

| 端 | 触发 | 呈现位置 | 启动命令（复制按钮） |
| --- | --- | --- | --- |
| :8000 `workbench.html` | `refreshHealth()` catch（/api/health 不可达）或网关自检 DOWN 且无任何结果 | 工作区顶部 `.cb-empty-down`（合规红边） | `python -m uvicorn gateway.app:app --port 8000` |
| :8000 `index.html` | `loadExperts()`（/api/experts 探测）失败；探测恢复自动撤卡 | 页头下方纠偏卡（--err-bg） | 同上 |
| :8765 | `boot()` /api/health 失败（此前为裸 unhandled rejection，本轮修复） | `#log` 顶部 `#cbDownCard`（带「重试检测」自愈按钮） | `cargo run --release --bin civil-workbench`（或 zip 内 start-workbench.bat） |

- 卡上一律带「重试检测」：:8000 重调 `refreshHealth`/探测，:8765 重拉 /api/health，成功即撤卡并提示「后端已恢复」。
- 复制走 `navigator.clipboard`（127.0.0.1 安全上下文可用），失败回退为提示手选，不静默。

### I.4 借鉴来源（pattern-only）

| 来源 | 许可 | 借什么 |
| --- | --- | --- |
| shadcn/ui `Empty` 组件（ui.shadcn.com/docs/components/base/empty） | MIT | 四段结构：Header→Media(icon)→Title→Description→Content(主动作)；图标块居中、一句话定位 |
| Tailwind UI / Tailwind Plus empty state | 文档 pattern-only | 图标+标题+一句描述+单个主 CTA；列表空态即引导 |
| PostHog / Appcues 首访 checklist | 文档 pattern-only | 3 步 checklist：逐项打勾、全完成自动收起、✕ 可跳过、随时可重开；步骤=产品真实动作 |
| openai/codex 首启欢迎（session header + 单 composer 聚焦） | Apache-2.0 | 欢迎区只占一屏不堆字；首条输入前不产生历史噪音；空态即第 0 号历史 cell |
| NN/g Empty-State / Onboarding 指南 | 文档 pattern-only | 空态=教育时机：说清"为什么空"和"下一步做什么"，不放占位图表 |

## 附录 J：明暗双主题与无障碍核对（ux round11 定稿）

> R11 落地「主题」门面（§4 路线图 R11）：三端 `:root` 浅色为默认 + `:root[data-theme=dark]` 显式暗色 +
> 未设置时跟随 `prefers-color-scheme`；顶栏统一「明/暗」文字开关 + 「大」大字开关，双 localStorage 持久化。
> 暗色取值 = workbench 现行暗盘（零回归）；浅色 = round1 定稿色板。零 CDN、零外链。

### J.1 主题机制（三端同构）

| 机制 | 落法 |
| --- | --- |
| 变量组织 | water.css pattern：`--cb-*` 同名变量明暗两套；页面级旧变量（--bg/--panel/--ink/--accent…）全部改为 `var(--cb-*)` 别名，随主题自动翻转 |
| 选择器 | `:root`（浅，默认）→ `:root[data-theme="dark"]`（显式暗）→ `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }`（跟随系统）；暗块两处同构，改动须同步 |
| 持久化 | `localStorage["cb_theme_v1"]` = `light/dark`（未设置=跟随系统，系统变化实时跟随，VS Code "system" 语义）；`cb_large_v1` = `0/1` |
| 防闪烁 | `<head>` 内联脚本在首帧前应用持久化值（VS Code settings pattern）；`meta[name=theme-color]` 随主题切换 #2563eb/#0c121a |
| 开关 | 三端顶栏统一位置：`明/暗` 文字按钮（显示当前主题，aria-pressed=深色激活）+ `大` 按钮（aria-pressed）；无图标库 |
| 灰阶反转 | 暗色 `--cb-gray-50/100/200` ↔ 浅色 `--cb-gray-200/500/900` 对调（U-R1 交接：面板灰与文字灰互为反转） |
| 彩色提亮 | 工程蓝 #2563eb→#5b8def、安全橙 #ea580c→#e6b84d、合规红 #dc2626→#f07178、通过绿 #059669→#3ecf8e（暗底各提亮一档保对比度） |
| 彩底文字 | `--cb-on-accent`：浅色 #ffffff / **暗色 #0c121a**——亮化后的彩底用深字（白字在暗色彩底仅 1.9-3.2:1 不达 AA） |

### J.2 主题变量映射与对比度核对表（WCAG 2.x，正文 ≥4.5:1 / 大字粗体 ≥3:1 / 非文本 ≥3:1）

| token | 浅色 | 暗色 | 用途 | 浅色对比度（底 #fff/#f8fafc） | 暗色对比度（底 #141c28/#0c121a） |
| --- | --- | --- | --- | --- | --- |
| --cb-text | #0f172a | #f1f5fb | 正文 | 17.9 / 17.1 | 15.7 / 17.2 |
| --cb-text-muted | #64748b | #a8b4c8 | 次要文字 | 4.76 / 4.55 | 8.2 / 9.0 |
| --cb-text-faint | #94a3b8 | #7a8aa3 | 仅装饰弱文本 | 2.56（**AA 豁免**：非正文） | 4.9 |
| --cb-blue | #2563eb | #5b8def | 主操作/链接/选中 | 5.17 | 5.3 / 5.8 |
| --cb-blue-ink | #1d4ed8 | #8db4ff | 14px 蓝色正文 | 6.70 | 8.2 |
| --cb-orange | #ea580c | #e6b84d | 警示徽/边/待补 | 3.56（≥18px 粗体才作文字） | 9.2 |
| --cb-orange-ink | #c2410c | #f0d48a | 14px 橙色正文 | 5.18 | 11.8 |
| --cb-red | #dc2626 | #f07178 | 阻断/废标/禁句 | 4.83 | 6.0 |
| --cb-green | #059669 | #3ecf8e | PASS/已签认 | 3.77（≥18px 粗体才作文字） | 8.6 |
| --cb-green-ink | #047857 | #7ee7b0 | 14px 绿色正文 | 5.48 | 11.4 |
| --cb-on-accent | #ffffff | #0c121a | 彩色实底上文字 | 蓝 5.17 / 红 4.83 | 蓝 5.3 / 橙 9.2 / 红 6.0 / 绿 8.6 |
| --cb-focus | #2563eb | #8db4ff | :focus-visible 焦点环 | 5.17（非文本 ≥3:1） | 8.2 |
| --cb-bg / --cb-panel / --cb-panel-soft | #f8fafc / #ffffff / #f1f5f9 | #0c121a / #141c28 / #1a2433 | 页底/浮层/次级面 | — | — |
| --cb-line / --cb-line-soft | #e2e8f0 / #f1f5f9 | #334155 / #253041 | 边框/分隔（非文本 ≥3:1 按需） | — | — |
| --cb-gray-50…900 | 浅色板（§2） | 反转板（50=#0c121a … 900=#f1f5fb） | 渐变/骨架 | — | — |

**已知残留（不阻断 R11 验收，R13 走查收口）**：
- 白字在 #059669/#ea580c 彩底按钮 3.8/3.6:1（浅色端既有值，大字粗体达标线内沿用；后续可改 ink-on-soft）。
- 装饰性彩边 8 位 alpha hex（#3ecf8e55 等 5 处）与 JS canvas 3D 渐变（#1c2a40 等）、TEAM_ROSTER 成员色、demo 暗色导轨（--rail）、投标页 hero 深色洗底、文书工具条 #1e293b —— 均为**双主题恒定 chrome**（固定深底白字或半透明彩边，两主题可读）。
- index.html 徽章 #475569→--cb-gray-500（4.76:1）已达标；`--cb-faint` 仅用于装饰位。

### J.3 无障碍核对表（本轮落地项）

| 项 | 要求（来源） | :8000 workbench | :8765 demo | 宿主 index |
| --- | --- | --- | --- | --- |
| 流式播报 | aria-live=polite 容器页面加载即存在（MDN/TetraLogical：动态注入不播报）；**只在收口/出错播报一行**，不逐 token 刷屏 | ✅ `#cbLive`（done/error 各一行，数字只抄事件字段） | ✅ `#cbLive`（done/error 各一行） | 不适用（无流式输出） |
| 审批卡按钮 | aria-label 说明显式决策语义（确认=决策写入审计；驳回=不放行；稍后=等待条） | ✅ 三按钮 | ✅ 三按钮（✕ 关闭已有） | 不适用 |
| 时间线节点 | 容器 role=list + 节点 role=listitem | ✅（round3 已有） | ✅ 本轮补 | 不适用 |
| 焦点可见 | `:focus-visible { outline: 2px solid var(--cb-focus) }` 全局 | ✅ | ✅ | ✅ |
| 对比度 | 新 token 4.5:1 核对表 | ✅ 见 J.2 | ✅ 见 J.2 | ✅ 见 J.2 |
| 大字模式 | html.cb-large 接 UI 开关 + cb_large_v1 持久化（附录 G 钩子启用） | ✅ 顶栏「大」 | ✅ 顶栏「大」 | ✅ 顶栏「大」（并补齐 index 缺失的 cb-large 字号块） |
| 主题开关 | 明/暗文字按钮 + cb_theme_v1 + 跟随系统 + 防闪烁 | ✅ | ✅ | ✅ |

### J.4 借鉴来源（pattern-only）

| 来源 | 许可 | 借什么 |
| --- | --- | --- |
| watercss/water.css `out/water.css`（npm registry 同源取包核对） | MIT | 语义变量同名明暗两套 + `@media (prefers-color-scheme: dark)` 覆写 `:root`；`--focus` 语义 token；`color-scheme` 原生控件跟随 |
| daisyUI 主题组织（daisyui.com） | MIT | `:root[data-theme=dark]` 属性选择器承载显式主题，未设置时回退系统偏好 |
| VS Code / Electron nativeTheme（code.visualstudio.com） | 文档 pattern-only | 持久化 light/dark/system 三态、system 跟随 OS 实时变化、启动先于首帧应用防 FOUC |
| MDN ARIA Live Regions + web.dev/learn/accessibility/javascript | 文档 pattern-only | polite 等空闲再播报；live 区必须页面加载即存在；流式输出聚合为一行再宣布 |
| TetraLogical「Why are my live regions not working?」 | 文档 pattern-only | 动态注入的 live 区不播报 → 静态占位 + textContent 先清后写 |
| WCAG 2.x SC 1.4.3 / 2.4.7 / 4.1.3（w3.org） | 标准 | 4.5:1 正文对比、焦点可见、状态消息播报 |

## 附录 K：断网可用性收口（ux round12 定稿）

> R12 落地验收（§4 路线图 R12）：断网全功能；CI 增加"零 CDN/外链"静态断言。
> 施工现场多为工地内网/弱网：页面引用任何外域资源（CDN 脚本/字体/图片/统计）都会白屏或卡死。
> 本轮把"零外链"从附录 A 的人工盘点升级为**机器门禁**（进 CI/precommit/npm check）+
> **浏览器级断网专项**（playwright 拦截全部外域请求跑核心动线）。

### K.1 零外链断言（scripts/test_no_external_urls.py，三处登记）

- **扫描范围**：`frontend/`、`demo/static/`、`workbench/src/` 下 `*.html *.css *.js *.webmanifest *.svg`
  （本轮实测 19 个文件）。`workbench/src` 目前无前端资产（`include_str!` 只内联 contract/seed/yibiao-map
  三份 JSON），纳入是为防将来在 rs 旁落 UI 文件漏网；`.rs` 不扫——Rust 侧的 https://（LLM 端点、
  websearch 检索、法规原文引用）都是服务器行为，浏览器从不加载。
- **判定**：先做注释遮蔽（HTML `<!-- -->`、CSS `/* */`、JS `/* */` 与 `//`，带字符串感知的
  状态机；HTML 内联 `<script>/<style>` 分别按 JS/CSS 处理），再在遮蔽后文本中找
  `http(s)://` 与协议相对引用（`src="//"`、`css url(//)`——跟随页面协议照样出外网）。
- **白名单（三类，其余 host 一律违规）**：W1 回环 `127.0.0.1`/`localhost`（同机 API 端点与
  兜底卡里给用户复制的启动命令）；W2 `www.w3.org`（SVG/XML 命名空间标识符，按规范不发起请求）；
  W3 仅出现在注释里的文档/仓库 URL（第 1 步已遮蔽，vue/marked 的 LICENSE 头即此类）。
  另有显式豁免清单 2 条：`marked.min.js` 的运行时错误提示字符串
  `"Please report this to https://github.com/markedjs/marked."`（异常文案，非资源加载）。
- **登记**：`run_precommit_tests.py`、`npm-check.cjs`、`.github/workflows/ci.yml` frontend 步骤。
- **自检**：注入 `https://cdn.example.com/probe.js` → 测试精确报出 `file:line` → 移除复绿（R12 已验）。
- **已知局限**：JS 正则字面量内的 `//` 可能被当行注释遮蔽（只漏报不误报）；漏网风险由 K.4 兜底。

### K.2 资源与运行时路径清单（全部同源相对）

| 资产 | 路径 | 断网表现 |
| --- | --- | --- |
| JS 依赖 | `/static/vendor/vue.min.js`、`marked.min.js`、`cb-fix.js`、`cb-doc.js`（R2-R4 vendored，附 LICENSE） | 本机文件，随服务起 |
| 字体 | 系统栈（Segoe UI/PingFang SC/Microsoft YaHei + Consolas），零 webfont | 终端自带，零下载 |
| 图标/manifest | `/static/icons/cb-icon*.png|svg`、`/static/manifest.webmanifest`（start_url/scope=`/`、icons 相对路径，脚本附加断言） | 同源相对 |
| 数据 | 浏览器 `fetch` 仅同源 `/api/*`；无任何第三方统计/字体/图片 | 服务在本机 |

外联检索（`workbench/src/websearch.rs` → duckduckgo）是 **Rust 服务器侧可选功能**：断网时该功能
降级/报缺，浏览器界面不依赖它（页面照常出文书，引用条目标 UNSPECIFIED/不编造，承 R10 诚实规则）。

### K.3 JS/CSS 语法门禁（scripts/test_js_syntax.py）

U-R10 用 esprima 才发现的致命 bug（块注释内 `runs/*/trace.json` 的 `*/` 提前终止注释，
:8765 app.js 整体失效）固化为门禁：11 个独立 js + 5 段 HTML 内联 `<script>` 逐段
`node --check`（parse-only，不执行）；本机无 node → SKIP（exit 0 + 说明），CI ubuntu
runner 自带 node 必跑，并另加 `find … -print0 | node --check` bash 循环双保险；附带 CSS
花括号配平体检（纯 python，本地也必跑，防 R11 量级的大段 `<style>` 改动被截断）。
登记同 K.1 三处。

### K.4 断网专项（scripts/test_offline_ui.py，playwright route abort）

- **方法**：playwright `context.route("**/*")` 把**所有非 localhost 请求 abort**（模拟外网彻底不通），
  两条硬断言：被 abort 的外域请求 == 0（连"试图出网"都不许）+ 全程无未捕获 JS 异常（pageerror==0）。
- **本轮实测动线（全绿）**：
  - :8765 Rust 工作台：示例卡预填 → 发送 → 阶段时间线 → 交付物「文书预览」打开 → 审计面板加载 + 导出按钮；
  - :8000 宿主页：填入样例 → 先理解再处理 → 「本轮意图」回显；
  - :8000 /workbench（三步演示）：满载演示 → HITL 确认闸（`.hitl-bar`）→ 非标预检「演示一键勾选」
    → 确认并拼柜 · resume → 拼柜完成出裁决（can_fit/ship_ok/mid50）。
- **局限**：本机无法做防火墙/hosts 级断网模拟；此法覆盖红线真正防的事故——"页面因外链资源
  卡死/白屏/JS 挂掉"。loopback 服务可达是前提，属部署侧事项（两端都在本机起）。

### K.5 Service Worker 结论：**不做**

| 维度 | 评估 |
| --- | --- |
| 收益 | 断网场景下两端都由本机服务 serve，静态资产同源秒回；SW 只能拦截已注册 origin 的请求，**帮不了"服务没起/首次打开"**。web.dev App Shell 针对公网站点离线可开——本项目壳就在本机，App Shell 天然成立，缓存无增益 |
| 风险 | cache-first 旧壳会**掩盖每轮 UI 更新**（R2-R11 每轮都改 UI/JS），"修了没生效"类问题成倍难查；网关已显式对 index 发 `no-store` 防缓存旧 UI，SW 与之直接冲突 |
| 成本 | Workbox 式 revision 失效需要构建链与 SW 更新协议，违背零依赖 vendored 现状 |
| 备忘 | 若未来要"现场笔记本拔网线演示"：再做不迟——仅缓存静态资产、`SW_VERSION` 常量 + 更新即 `skipWaiting+clients.claim` 并提示刷新（借 Workbox precache 思想，不引库）；API 一律 network-only |

### K.6 断网核对表（发版前过一遍）

| 项 | 门槛 | 工具/入口 |
| --- | --- | --- |
| 零外链静态断言 | PASS（19 文件，白名单外 0 命中） | `python scripts/test_no_external_urls.py`（CI/precommit/npm check 必跑） |
| JS 语法 | node --check 全绿（或本地 SKIP + CI 绿） | `python scripts/test_js_syntax.py` |
| CSS 体检 | `<style>`/`.css` 花括号配平 | 同上（无 node 也跑） |
| 断网动线 | 两端核心动线全绿、外域请求 0 次、pageerror 0 | `python scripts/test_offline_ui.py`（需 playwright） |
| manifest | start_url/scope/icons 全相对路径 | K.1 脚本附加断言 |
| SW | 未注册（K.5 结论） | grep `serviceWorker` 应为 0 命中 |

### K.7 终验记录（ux round13 收官时点 · 端到端金线逐项）

**金线**：`python scripts/r13_golden_path_e2e.py`（playwright chromium headless；自动起 `uvicorn gateway.app:app :8000` + `CIVIL_PORT=8765 workbench/target/release/civil-workbench.exe`，真实任务走 LLM 链路，测毕自杀进程）。

| # | 项 | 结果 | 证据摘录 |
| --- | --- | --- | --- |
| 1 | 空态引导 | PASS | 空态卡在，三步引导可见，步骤数=3，`?` 入口可重开 |
| 2 | 示例卡预填 | PASS | 点击 `/pack` 示例卡后输入框=`pack test/sim_materials/…`，1.5s 内无自动发送 |
| 3 | 时间线八阶段 | PASS | 八轨 chips=understand/summon/box/hitl/pack/risk/write/finalize 齐全 |
| 4 | HITL 审批卡确认 | PASS | 高风险钢结构岗触发审批卡，点「确认并重提」后状态=`已确认 · 已重新提交` |
| 5 | 文书预览下载 | PASS | 预览浮层打开，真实下载 `钢结构专业说明草稿.md` 1536B 非空，三步引导第 3 步打勾 |
| 6 | 审计含决策节点 | PASS | 审计头摘要=`决策 2 · 工具 1 · 写盘 1 · 错误 0`，决策节点置顶渲染 |
| 7 | 主题切换 | PASS | 暗：`data-theme=dark`+`aria-pressed=true`+`cb_theme_v1=dark`；回切 light 成功 |
| 8 | 窄屏 375px 审批 | PASS | 375×812 视口审批按钮全宽 48px 高、完全在视口内（box=[53,287,273,48]），可点确认 |

汇总 **8/8 PASS，零未捕获 JS 异常**。

**过程记录（诚实）**：首轮第 6 项 FAIL（决策数=0、节点=0）——定性为**金线脚本竞态**而非产品缺陷：`#auditPanel` 的 unhide 是同步的，脚本在面板显示瞬间即读 body，此刻仍是「加载中…」。经独立探针复现（正确等待 `.cb-audit-pinned` 且摘要不含「加载中」后：决策 2、决策节点 4 个）确认数据与渲染均正常，仅修脚本等待条件（`wait_for_selector('.cb-audit-pinned')` + `wait_for_function` 摘要就绪），**产品代码零改动**，复跑 8/8 全绿。

**配套终验（同一时点）**：

| 项 | 结果 |
| --- | --- |
| `test_stack_parity.py` | PASS（contract + golden 41 金句 + mirrors + roster 66 岗） |
| `run_precommit_tests.py --quick` | ALL GREEN（booking_regression + p2_gates + stack_parity + post_scorecard） |
| `demo_one_shot.py` | ALL SELECTED CHECKS PASSED（冒烟无需 API Key） |
| `eval_competition_scorecard.py --skip-phase0` | raw 8.90 → **overall 8.85**，hard gates 8/8 True，赢线 PASS（不降） |
| `eval_post_scorecard.py --all-pilots` | 5/5 岗 PASS（bid-parse / bid-tech / bid-compliance / cost / safety-brief） |
| `test_offline_ui.py`（R12 断网专项） | PASS：外域请求 0 次、pageerror 0 |
| `cargo test --release --lib --test intents_golden` | 2/2 ok（frozen baseline + contract vocab wired） |
| 禁句全文检索（§3.3 六词，UI 三端资产） | 0 命中 |
| CI 补门禁 | ci.yml frontend 步骤新增 R5–R12 UI 标记断言（cb-apr / audit-panel / slash / 空态卡 / 主题持久化，三文件全 OK） |

**局限**：金线=单用户单会话真浏览器动线，不含负载/多用户/长程稳定性；LLM 链路依赖 `demo/.env` 的 Key，纯断网口径以 K.4 为准；本地无 node，`test_js_syntax.py` 按 R12 设计 SKIP（语法门禁由 CI ubuntu runner 必跑兜底）。

## 附录 L · 符号纪律对照表（ux(round14)，门禁 `scripts/test_ux_no_emoji.py`）

用户反馈"输出不要这么多符号"。规则：**状态语义用纯文字或 CSS 单色圆点，不用字形符号**。

| 原符号 | 替换为 | 出现位置（举例） |
|---|---|---|
| ✓ / ✅ | 文字"可用/完成/已应用"或 CSS 实心绿点（.cb-ob-dot/.cb-tl-st） | 时间线阶段、what-if 应用行、评分导出 |
| ⚠ / ⚠️ | 文字"预警/注意/已驳回"（橙 token 色） | 审批卡阻断行、纠偏卡、总览横幅 |
| ⛔ | 文字"阻断/不可出运"（合规红 token 色） | 出运裁决、缺尺寸提示 |
| ✗ / ✕ | 文字"未过"；关闭按钮用 ×（U+00D7 排版符，豁免） | 评分导出、浮层关闭 |
| ☰ 🗺 🔧 📦 | 文字"菜单/CB/工具名"（单字色块图标沿用 装/标/安 式样） | 移动菜单、空态媒体、时间线工具行 |
| ★ | **保留**（豁免）：评分点业务排版，标书语境真实用法；UI 徽章位改文字"重点" | 招标评分点、HITL 徽章 |
| → | **保留**（豁免）：流程箭头，正文排版 | 时间线、说明文字 |

门禁：precommit 步骤 `test_ux_no_emoji.py` 扫 9 个浏览器资产，禁 U+2600-27BF/U+2B00-2BFF/emoji 区/U+FE0F，豁免 ★→×。round14 共清理 workbench 28 处 + index 1 处（demo 侧在中断的前次实现中已清理）。

## 附录 M · 装箱直达（ux(round15)，两端落地）

需求：**在聊天框输入「装箱」直接打开装柜台**，不必先记住 `/pack` 或表名。

| 项 | 口径 |
|---|---|
| 触发词 | `装箱` `装柜` `拼柜` `装箱拼柜` `装箱作业单` `装柜台` |
| 匹配方式 | **整条精确匹配**（trim + 去尾部中英标点）。`装箱。` `拼柜！` 触发；`帮我装箱一下` `装箱怎么算的` **不触发**，仍走 pack-ship 专家问答——不抢正常提问 |
| 目标 | `http://127.0.0.1:8000/workbench` 装柜台 3D 工程台（成箱 → 人确认 → 拼柜/重心） |
| 前置探针 | 先 `GET :8000/api/health`（2.5s 超时，网关 CORS 为 `*`）；**通了才开标签**，不可达不弹空白报错页 |
| 已打开态 | `.cb-pack-ok` 蓝卡 + 手动链接（兜底弹窗被拦） |
| 未启动态 | 复用 `.cb-empty-down` 红卡（同为后端不可达语义）+ `role="alert"` + 启动命令一键复制 + 「重试」就地重探 |
| 上下文 | 触发条**不进 `state.history`**，不占对话上下文，不计入 token 估算 |
| 顺序 | **先落卡片再 `window.open`**——部分浏览器/内嵌视图把 `window.open` 当原地跳转，后落卡片会随原页面一起被替换，回到本页无痕迹（实测发现并修） |
| 两端 | `workbench/src/api.rs:76` 以 `ServeDir` 直供 `demo/static`，故改 `demo/static/app.js` 一处，Rust :8765 与 Python 参考实现同时生效 |
| 红线 | 回环地址属 R12 零外链白名单（`ALLOWED_HOSTS` 含 127.0.0.1）；卡片文案零装饰符号，过 R14 门禁 |

**实测记录**（:8765 + :8000 双服务真浏览器）：上行 → 卡 `cb-pack-ok`、`window.open` 收到 `/workbench`、`state.history` 增量 0；停 :8000 后 → 卡 `cb-empty-down`、`openedUrls` 为空（未弹空白页）；重启 :8000 点「重试」→ 卡就地翻蓝、页面仅 1 张卡不叠加。

## 附录 N · 直达词泛化 / 空态露出 / 回跳 / 试用边界 + R14 白屏事故（ux(round16)）

### N.1 四项落地

| # | 项 | 口径 |
|---|---|---|
| A | 直达词泛化 | 词表由 `CB_SLASH` 的 `name + aliases` 自动生成（与 `/` 面板同一份口径，杜绝两处漂移）+ `CB_DIRECT_EXTRA` 少量口语补充，共 24 词。`pack` → 开装柜台；`kind:"nav"` → `cbCmdNav` 就地打开；`tpl` → `cbCmdApplyDraft` 预填不自动发送（附录 F.3 红线）。整条精确匹配，`帮我装箱一下` / `招标文件怎么读` 不触发 |
| B | 空态露出 | 空态卡在示例卡与免责小字之间加一行直达提示；`code` 块走蓝 token 随明暗主题翻转。理由：评委第一屏发现不了的功能等于没做 |
| C | 装柜台回跳 | 装柜台此前是单向终点（只能按浏览器后退），顶栏 `top-actions` 加「回工作台」→ `http://127.0.0.1:8765/`（回环属 R12 零外链白名单） |
| D | 试用包边界 | `packing_bridge::probe()` 的 `python_root` 为空 = 磁盘无 `packing_assistant/` = 试用 zip，没有引擎可启动。此时卡片改「装柜台不在试用包内」并**撤掉那条它跑不了的 uvicorn 命令**，只留「重试」。Python 参考实现无该字段 → 探针返回 `null`（未知），按完整仓库口径走，安全降级 |

### N.2 R14 白屏事故与守卫（诚实记录）

**事故**：R14 往 `frontend/workbench.html` 加了左侧会话栏与新输入条的 markup，**JS 半边整块没落**——`cbNewTask` / `cbOpenSession` / `cbSessions` / `cbRelTime` / `cbPlusOpen` 五处绑定零定义。Vue 2 把 `@click="cbNewTask"` 编译成 `on:{click: cbNewTask}`，在**渲染期**求值 → ReferenceError → **装柜台整页白屏**。带修饰符的 `@click.prevent="cbPlusOpen"` 被包成函数，是点击期求值，故不白屏但点了没反应。

**为什么四道门禁全绿仍漏**：符号纪律 / 零外链 / JS 语法 / CI 标记断言**都只做静态扫描，不执行页面**。`node --check` 只验语法，不解析 Vue 模板表达式；标记断言只查字符串在不在。

**守卫**：`scripts/test_vue_bindings.py` —— 扫模板里的裸标识符事件绑定（`@click="ident"`）与 `v-for` 数据源，逐个对账 `data/computed/methods` 定义面；表达式型绑定（带括号/点号）不在范围内以避免误报。已登记 precommit 与 CI frontend 步骤。自检：回滚到 R14 状态可精确报出 3 处致命绑定，修复后复绿。

**沉淀的纪律**：markup 与其 JS 半边必须同轮落地；宁可不加按钮，不留半拉子绑定。

## 附录 O · 模型设置面板（ux(round17)）

需求：**评委/试用者用自己的 Key 就能跑**，并可在 DeepSeek 各模型与 z.ai（GLM）等 OpenAI 兼容供应商之间切换，不必改 `demo/.env` 再重启进程。

| 项 | 口径 |
|---|---|
| 入口 | :8765 顶栏「模型」按钮 → 居中浮层（Esc / 点遮罩 / 关闭 三种关法） |
| 供应商预设 | DeepSeek（`https://api.deepseek.com`）· z.ai（`https://api.z.ai/api/paas/v4`）· OpenAI · 自填。选中后自动填接口地址与首选模型，两者均可改 |
| 模型 | `datalist` 给常见值但**可自由输入**；面板与文档均注明「以供应商文档为准」，不写死 |
| 后端 | `GET/POST /api/llm-config`（`workbench/src/api.rs`）；覆盖存 `config.rs` 的 `RUNTIME_LLM: RwLock<Option<LlmConfig>>`，`llm_config()` 优先读它，未设置则回退 env |
| Key 边界 | 只存**进程内存**：不写盘、不进 localStorage、不进日志；`GET` 只回首尾各 4 位掩码，**永不回明文**；进程退出即失效；`{"clear":true}` 回退 `.env` |
| 空字段语义 | POST 里空字段=保持当前值 —— 只换模型不必重填 Key（实测验证） |
| 断网红线 | 浏览器**从不**直连供应商，`/chat/completions` 由 Rust 侧 `reqwest` 发出；预设端点只是表单默认值，页面加载不发请求。故在 `test_no_external_urls.py` 的 `EXEMPT_URLS` 里按该门禁要求逐条写明理由后豁免 |
| 连带修 | `scripts/start-workbench.bat` 此前缺 `demo\.env` 会 `exit /b 1` **硬退出**；有了本面板该门槛就是错的（评委可零配置启动、界面里填 Key），改为提示后继续启动 |

**实测**（真 exe，dist 试用包）：初始 `source=env` 显示 `.env` 口径 → 切 z.ai 自动填端点与候选模型 → 存假 Key 后 `source=runtime`、顶栏徽章翻「已配置 API Key」、模型徽章变 `glm-5.3`、Key 输入框自动清空 → 只 POST `model` 时掩码不变证明 Key 保持 → 响应与页面 DOM 中 `grep` 明文 Key 命中 **0** → `clear` 回退 env。

## 附录 N.3 · 装箱直达的冷启动竞态（round16 复测补记）

真 exe 冷启动期 `/api/health` 要跑 `packing_bridge::probe()`（`spawn_blocking` + 对 :8000 的 HTTP 探测），实测**超过 2s**。最初把试用判别挡在渲染前面，导致页面刚加载就输入「装箱」时长时间无任何反馈（Python 参考实现快，故未暴露）。两处修正：

1. 判别结果改为**蹭 `boot()` 那一次 `/api/health`** 缓存（`cbPackTrialFrom`），装箱直达时直接用，不再发赶时间的请求；
2. 探针加 `AbortController` 超时，且卡片**先落通用文案再异步改写**，保证回车后立即有反馈。

判据只认 `http.up=false && python_root==null`；**不可用 `packing_agent.connected`** —— `url_configured()` 有默认值故它恒为 `true`。

## 附录 P · co-work 壳改造（ux(round19)，P0-P9）

用户反馈「页面不干净」。实测：一屏塞了**三栏 + 5 个独立输入入口 + 7 个功能区块**。
目标形态：**左栏项目（两级）· 专家按需召唤 · 无本地文件入口 · 中间一个聊天框**。

### P.1 分阶段与净效果

| 阶段 | 内容 |
|---|---|
| P0 | 清场与止血：拆本地文件入口、删死 UI、修 exe 上的噪音与死键（净 −122 行） |
| P1 | 三栏 → 两栏 + 审计抽屉 + 设置菜单 |
| P2 | 66 岗常驻名册 → 按需召唤（复用 cbCmd 两级机加两个 mode） |
| P3-P5 | Rust 项目/会话索引 + 自动归类 + 对话落盘（新模块 projects.rs） |
| P6 | 左栏两级项目树，点会话恢复对话历史 |
| P7 | 装柜台改对话流内嵌面板，不再跳走 |
| P8 | Python 镜像 + contract/projects.v1.json 契约单源 + 双栈对拍 |
| P9 | 收口：spec、重打包、Release |

### P.2 「先补守卫，再删 DOM」——本轮最重要的纪律

删 UI 前先给 **7 处无守卫的 `$()` 读取**补守卫。其中 `app.js:306` 的
`$("clearExperts").addEventListener` 是**顶层裸调用**：删该元素会在脚本加载期抛
TypeError，`boot` / `cbComposerInit` / `cbThemeWire` 全不执行，**整页变砖** —— 与
R14 白屏事故同一类。其余：`renderSummon` 的 `summonBar.innerHTML`、`refreshKb` 与
clearExperts 回调的 `kblist`、`renderCites`/`renderFiles` 的 `const box`、
`streamChat` 的 `confirmOk.checked`（删该框会打断全站发送）、`studio.js:18-19`。

**本轮真踩了一次**：P6 第一版用 `s.index("
函数关键字 ", i)` 猜函数体结束边界，
而 `reloadCatalog` / `loadJobRoot` / `handleSlash` / `cbRunBackground` 都是
`async function` 开头，边界跳过它们把四个函数一起删了（`handleSlash` 还是 ci.yml
断言的必保标识），表现为 boot 抛 ReferenceError、左栏空白、网关兜底卡误弹。
已回滚重做：改**花括号配平**精确定位（跳过字符串与注释），并在替换后逐个断言
9 个必保标识前后计数一致。**教训：批量改 JS 不许用「下一个关键字」猜边界。**

### P.3 修掉三处只在评委那侧发作的「脏」

Rust exe 没有 `/api/config`、`/api/threads`、`/api/skills`（只有 Python 参考实现有）：

| 现象 | 根因 | 处置 |
|---|---|---|
| 每次开页面对话流多一行 `SyntaxError: Unexpected end of JSON input` | `loadPolicy` catch 里 `addStatus(String(e))` | 静默保留默认 policy。**这是「页面不干净」的字面来源** |
| 左栏永远空白 | `loadThreads` 404 后 `box.textContent=""` | 改项目树 + 降级文案 |
| 「+ 新建任务」是死键 | `await fetch().then(r=>r.json())` 抛在 `cbResetToEmpty()` 之前 | 客户端优先：先本地清空，再 try 登记远端 |

顺带修一个既有缺陷：`cbResetToEmpty()` 只把 `#cbEmpty` 设 `hidden=false`，而发首条
消息用的是 `welcome.remove()` 把元素整个删掉 —— 按钮 title 写着「回到空态卡」，
一旦发过消息就永远回不去。改 `cbHideWelcome()` 隐藏式。

### P.4 抽屉 ≥1280 默认展开是硬约束

`test_offline_ui:150` 与金线第 6 项都是 `page.click("#loadAudit")` 且**无前置展开
动作**，Playwright 的 click 会等元素可见 —— 默认收起 = 两个 e2e 直接挂。
结果：本轮两个 e2e 脚本**一行都没改**。

### P.5 后端为什么是 JSON 三件套而不是 SQLite（三条实测前提）

① 发布包（`package-workbench-release.ps1:45-52`）只拷 exe + demo/kb + demo/static +
skills，**没有 `packing_assistant/`、没有 `data/civilbuddy.db`** → projects 表对
「评委 exe 能用」贡献为零。
② `session.summary.json` 的 project 槽实测 **2554 个里 2551 个是 `UNSPECIFIED`**，
且 Rust 侧从不写它 → 靠它归类＝全仓一个「未归类」桶。
③ `trace.json` 的 project 实测高频值是 `"问"`（99 条，前端从输入框前 40 字现搓）与
`H-worker-brief`（78 条 eval fixture）→ 自动建档会造出几十个垃圾项目。

**所以：归类是自动的，建档是人的动作。** 自动归类对活跃项目 name+aliases 做
子串包含最长优先，**永不新建项目**。改名只改 name、**id 不动**、旧名进 aliases，
存量会话零回写自动跟随；合并写墓碑保留不删，解析跟随一跳。
存量 3250 个目录全部落 `p-inbox`，不做自动归类（依据②③）。
扫盘分级读取、绝不打开 trace.json、上限 500，**实测 3250 目录 208ms**。

### P.6 恢复对话历史：没有免费方案

实测 Rust `/api/chat` 的 history **完全由浏览器提供**（`api.rs:701-733`），服务端
从不落盘正文；前端 `state.history` 纯内存，刷新即丢。故新增
`<out>/<sid>/transcript.jsonl`，在 chat 收尾统一追加。**一个钩子覆盖三条出口**
（run_plain / firm / 专家循环）—— 在 `send` 闭包里截 `done` 取回复，spawn 收尾写一次；
逐个分支挂钩必漏。前端点会话后**整体替换** `state.history`（不 merge，避免与浏览器
内存分叉）。**明确不还原**时间线卡 / 审批卡 / 引用块（渲染装饰，不影响接着聊）；
没有留存正文时 UI 明说「上文从此刻重新开始」，不假装接上了。

### P.7 已知边界（诚实清单）

- 装柜台内嵌 iframe 的 `sandbox` 必须含 `allow-same-origin`（否则 :8000 自己的同源
  fetch 全挂），配 `allow-scripts` 后对 :8000 等于不设防 —— 它本就是用户自己在跑的
  同机应用，且跨 origin 决定了它碰不到 :8765 的 DOM
- `#kblist` 仍在抽屉内，未随召唤面板迁走：召唤面板是选完即关的浮层，而 `refreshKb`
  是异步 fetch，渲染进去会立刻被隐藏
- SQLite `projects` 表 / `SCHEMA_VERSION` 1→2 **本轮明确不做**（见 P.5 ①），
  且 `storage.py:195-207` 的 `_ensure_schema` 只有 `CREATE TABLE IF NOT EXISTS`，
  **没有 ALTER 路径**，将来真要上表得先补迁移能力
- `gateway/app.py` 的 `/api/audit` 不加字段（一加 `test_audit_parity.py:112` 立刻红）

### P.8 双栈对拍连踩三个 Windows 编码陷阱

首跑报「8 处双栈不一致」，逐个查下来**全部是编码问题，没有一处真实行为分叉**：
① 差异打印撞控制台 GBK → `UnicodeEncodeError`（同 `rag_parity` 踩过的坑）；
② `python -c <含中文源码>` 在 Windows 上 argv 按控制台代码页编码 → 改写 UTF-8 临时
脚本文件；③ 子进程 stdout 按 GBK 写而父进程按 UTF-8 解 → 子进程改输出
`ensure_ascii=True` 的纯 ASCII JSON。**对拍脚本本身不推敲，会把编码问题误报成
架构问题。**

### P.9 新增守卫

`scripts/test_projects_parity.py`（双栈对拍，自造 fixture 零存量依赖）已进 precommit
与 ci.yml。`workbench/tests/projects.rs` 12 条断言同样自造 fixture。

## 附录 Q · 用自己的表装箱（ux(round20-21)）

用户问「我的测试表在哪 / 能不能给个文件地址就装箱」。实测后发现**能力本来就全在，
只是中间断了一节**，而断的方式很坏。

### Q.1 改造前：给了路径却静默用演示数据

| 输入 | 箱数 | 利用率 |
|---|---|---|
| 仓库内相对路径 `test/sim_materials/small_one_container/materials.xlsx` | 8 | 0.0014（真读了，与表逐项吻合） |
| 绝对路径 `C:\Users\LW\Desktop\我的表.xlsx` | 15 | 0.675 |
| **什么都不给**，只说「帮我装箱」 | 15 | 0.675 |

后两行**数字完全一样** —— 绝对路径被丢弃后回落到了演示预设物料，**不报错、照样给
一串很像样的柜数**。演示时说「这是我的表」而屏幕上其实是样例，会很难看。

根因：exe 把整句话当 `user_input` 文本发给网关，网关的 `_load_materials_from_text`
（gateway/app.py:1031）出于防目录穿越**只认仓库内相对路径**。

### Q.2 三段能力本来都是现成的

| 环节 | 状态 |
|---|---|
| exe 读本机任意路径 | 已有：`attach::allow_local_path` 只挡 `D:\layout` / `\windows\` / `\program files` / 回收站 |
| 网关按**绝对**路径解析表 | 已有：`/api/table/parse/json` 里 `if not p.is_absolute(): p = ROOT / p` —— 绝对路径直接用，**无仓库沙箱** |
| 网关直接收物料数组 | 已有：`PipelineRequest.materials` |

### Q.3 解法：不放宽沙箱，换显式通道

那个沙箱**是对的，别动** —— `user_input` 里可能混着 LLM 生成或从文档粘来的内容，
不能让它随便读盘。改为职责各归各位：

```
exe 收到 pack <路径> → find_table_path 找出表格路径
  → attach::allow_local_path 判能不能读
     （该策略本来就归 exe，与老「导入」按钮同一信任级别：
       路径是用户**显式**给的，不是从自由文本猜的）
  → 网关 /api/table/parse/json 读表
     （Python 的 table_mapper 处理脏表头与单位归一，见 test/generic_tables/
       G6_messy_headers；在 Rust 重写是明显倒退）
  → materials **数组**喂 /api/pipeline（不传路径，沙箱问题根本不存在）
```

**网关侧零改动**，两个端点都是现成的。

### Q.4 失败必须明说（本轮核心）

路径给了却读不到时，作业单里写：

> **注意：给了表格路径但读不到** —— `<路径>`：<原因>。
> 下面的数字**不是**这张表算出来的，不要当成它的结果。

绝不静默回落。这是「缺数标 UNSPECIFIED、不编造」纪律的同一条底线，改造前这里是破的。

### Q.5 回形针（round21）

round19 拆界面时把上传入口一并拆了，用自己的表只剩「放进仓库 / 给绝对路径」两条，
最自然的「拖进来」没有了。补回**一个**入口：composer 里的「附件」按钮 + 一行可 ×
移除的 chip。**不恢复**原先那排杂项（本机路径框 / 成套投标 / 5 个预填 chip）。

- 图标是**文字**「附件」——符号纪律禁 emoji 与 dingbats，回形针图标会撞 `test_ux_no_emoji`
- 拖拽一并接回：round19 删它是因为它当时**看不见**（删了按钮它还活着，拖文件进来
  就静默 POST）。现在有可见 chip 与 `.composer.drop` 高亮，它不再是隐藏通道
- `state.jobAttachments` 改回 `state.attachments` —— round19 那个改名的理由是
  「没有写入方的同名数组等于留后门」，现在回形针是**唯一且显式**的写入方，名副其实

**关键：光加回形针不够。** 上传件按 `<id>.bin` 存（无扩展名），而 `load_table` 按后缀
分发，直接喂会判 unsupported —— 回形针传了表却依旧走演示物料，等于换个姿势重演
「静默回落」。故加 `attach::latest_table_upload`：按存下的原文件名取扩展名，惰性
物化一份 `<id><ext>` 再交给 `run_table`。

### Q.6 已知边界

- 需要 `:8000` 装箱网关起着，否则「装箱」只给启动指引
- `pretty_path` 去掉 Windows `canonicalize` 的 `\\?\` 扩展长度前缀（只影响显示）
- 同一会话附件数有上限（`attach::MAX_FILES`）

### Q.7 过程记录：连踩两次同一个编码坑

补丁脚本走 heredoc 时 `sys.stdin.encoding=gbk`，UTF-8 中文被按 GBK 解码，匹配串
静默损坏、断言假失败（此前几次能中是运气）。另有 `cat >> <<EOF` 把 Rust 原始串里的
`\\` 吞成一个 `\`，导致测试断言写错，一度以为是 `pretty_path` 有 bug。
**结论：含中文或反斜杠的补丁一律写脚本文件执行，不走 heredoc。**


## 附录 R · 物料来源诚实性 + 门禁自身的可信度（ux(round22-23)）

发版 0.4.0 前的收口轮。起点是「打包发布」，做下去发现**该修的比该打的多**。

### R.1 网关层的静默回落（round22）

round20 只在 exe 侧（`packs.rs`）给「表格路径读不到」加了标注，但**任何直接调网关的人**仍是老样子。改造前实测：

| 输入 | 结果 |
|---|---|
| `pack C:\...\我的表.xlsx`（读不到） | 15 箱 / 利用率 0.675 |
| 只说「帮我装箱」，什么都不给 | 15 箱 / 利用率 0.675 |

**完全一样** —— 路径被 `_load_materials_from_text` 的仓库沙箱丢弃后静默回落到演示预设物料，不报错，照样返回一串很像样的柜数。演示时说「这是我的表」，屏幕上其实是样例数据。

**沙箱本身是对的，不放宽**：`user_input` 里可能混着 LLM 生成或从文档粘来的内容，不能让它随便读盘。只要求「看见了但没用上」这件事必须写进 `materials_notice`。挂载点是四个 JSON 端点各自已有的 `public_response(...)` 之后加一行；SSE 在**首帧**发 `type=notice`（不等收口）。没有 notice 时**不加键**，老响应形状零变化。

### R.2 exe 侧同一件事，此前完全没有测试（round23）

评委下载运行的是 exe，且**评委机上 :8000 根本不存在**，`run_table` 必然连不上 —— 这条路径反而最常走。补 `workbench/tests/pack_table.rs` 时当场逮到两处真缺陷：

1. **`materials` 是个没有任何 description 的裸 string**，工具描述里也没说「用户给了文件路径就放这儿」。整个 round20 靠模型自己猜着把 Windows 路径塞进一个叫 materials 的参数——猜不中，读表分支根本不触发，作业单里连「读不到」都没有。→ 把要求写死进参数契约。
2. **路径被放进 `notes` 就当没看见**。→ `materials → notes → project_name` 依次扫。**模型放错格子不该等于功能静默失效。**

### R.3 门禁自身烂了好几轮没人看见 —— 根因是 CI 不跑 cargo

`ci.yml` 此前**完全没有 cargo**（只有 `test_rag_parity.py --skip-rust`），`workbench/tests/` 下所有 Rust 门禁从来没在 CI 上执行过。发版前一查，三条断言已经陈红：

| 断言 | 何时烂的 | 为什么没人发现 |
|---|---|---|
| `index.html` 含 `ctxBar` | round19 P0 就把它删了（附录 P 里明写「有守卫，可安全删」） | CI 不跑 |
| 联网知识含写死的 `2026-08-14` | hr-labor 刷新到 `2026-08-28` 即红（80 篇里就这一篇被刷新过） | 同上 |
| `app.js` 含「能聊能跑」 | 这句文案早没了 | 同上 |

修法是**改回断言本来的意图**，不是删了了事：`ctxBar` → 钉当前外壳（`projTree` + `composer`）；写死日期 → 钉「有一次注明日期的核验」这件事本身（新增 `has_dated_pass` 手扫 `YYYY-MM-DD`，不为一条断言引 regex 依赖）；文案 → 钉 exe 实际吐出的两条**红线正文**（`不会自动发送` / `不是签认件`）与两个功能入口，与 `ci.yml` 的 `ux_marks` 指向同一批东西，两道门禁不会各说各话。

CI 加 `rust` job（stable + rust-cache + `cargo test --locked`），Linux 上验过全绿。

### R.4 零配置评委看到的第一条错误，指错了地方

真浏览器走评委路径时发现：没配 Key 的第一条提示是「在 `demo/.env` 写入 …」，可 round17 起界面里就能填（设置 → 模型设置，运行时覆盖、无需重启）—— 那正是 v0.2.0 想省掉的那一步。改成先指界面、再说 `.env`。

**Python 参考实现不跟着改**：`demo/app.py` 没有 `/api/llm-config`，面板在那边填不了，它的提示指 `.env` 才是诚实的。这是两栈分叉的又一处，见 R.6。

### R.5 三条自检（门禁不自检就不算门禁）

本轮每加一道门禁都把对应的修复临时回退一次，确认它精确报红、恢复后复绿：

- 把 `hit = _table_path_mentioned(...)` 改成 `hit = ""` → `test_materials_notice.py` 报「静默回落又回来了」
- 删掉 `notes` 兜底 → `test_path_in_notes_still_seen` 单项红，其余三例仍绿
- （反面教材）我自己在 round23 的提交信息里写过「cargo 全量 41 项全过」，**那句是错的**：跑的是 `cargo test | grep | head`，读到的是 `head` 的退出码。此后一律核 `PIPESTATUS[0]`。

### R.6 已知欠账（不假装已完成）

- **两栈路由仍分叉**：Rust 有 `/api/upload`、`/api/local`、`/api/llm-config`、`/api/harness/*`；Python 有 `/api/config`、`/api/threads`、`/api/skills`。前端对两边都做了静默降级，但 `/api/config` 在 exe 上每次开页仍 404（只在 devtools 可见，不再写进对话流）。exe 根本没有 sandbox/approval 概念，**没有真值可回**，所以补路由等于编造——留着 404 比编一个值诚实。连带：`/sandbox`、`/approval` 两个斜杠命令在 exe 上回显的是名义默认值。
- **CI 的 cargo 是 debug build**，与发布用的 `--release` 不是同一套编译产物。
- 附录 P 记的「点会话不恢复完整 UI 状态」（时间线卡 / 审批卡 / 引用块）仍未做。
