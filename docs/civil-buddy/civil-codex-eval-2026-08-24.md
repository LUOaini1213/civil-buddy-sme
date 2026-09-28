# 联网评估 · 土木版 Codex · 2026-08-24

问：Civil Buddy 现在是不是 **完整的土木版 Codex**，官方事实有没有漂。

**总判：部分合格。**  
它是带纪律的土木 application harness，产品面已按 Codex 的四件套搭了骨架（TUI / exec / app / MCP）。它 **不是** OpenAI Codex 的对等物，也 **不是** 可投标 / 可开工 / 可代签的行业 agent。  
行业总判句仍以 [industry-agent-eval-2026-08-17.md](industry-agent-eval-2026-08-17.md) 为准，本文 **不改** 那句。

禁止写成产品能力：中标率 +N%、可以投标、可以开工、GeBIZ 代交。

---

## 1. 官方页（本日打开，不是只读旧笔记）

| 门户 | 现场原文 | 本仓 | 判定 |
|------|----------|------|------|
| [IRAS Current GST rates](https://www.iras.gov.sg/taxes/goods-services-tax-(gst)/basics-of-gst/current-gst-rates) | *The current GST rate in Singapore is 9%.* 历史表：2023=8%，2024-01-01 起 9%。 | company 页 + 聊天抄「页述 9%」 | **抄对** |
| [SCDF Fire Code 2023](https://www.scdf.gov.sg/fire-safety-services-listing/fire-code-2023) | *Fire Code 2023* / *Code of Practice for Fire Precautions in Buildings 2023* | 只列官方标题，条款 UNSPECIFIED | **抄对** |
| [IMO CTU Code](https://www.imo.org/en/ourwork/safety/pages/ctu-code.aspx) | *2014 IMO/ILO/UNECE Code of Practice…* **non-mandatory** global code | 2014、非强制、不编条款号 | **抄对** |
| [MOF procurement processes](https://www.mof.gov.sg/policies/government-procurement/procurement-processes/)（页更 2025-12-01） | 评标按 **招标文件已公布标准**；授标公示在 GeBIZ。GeBIZ 是机会门户。Tender Lite 已扩到 construction（2025-05 起）。 | GeBIZ **不是**评分办法；分值只抄 ITT | **抄对** |

IRAS 渲染页现场有 9%。抓取失败不得改口「官方没写 9%」。7%/8% 只可当历史档。

离线闸（不抓 IRAS）：`live_eval()` → `verdict=offline_gate_pass`，五针全中（GST 9% / Fire Code 2023 / CTU 2014 非强制 / GeBIZ≠评分 / APPBCA-2026-12 5000）。沙箱拒 `.env` 与 generic spawn。

---

## 2. 现网 Codex 是什么（本日文档 + GitHub）

来源：[learn.chatgpt.com/docs/build-skills](https://learn.chatgpt.com/docs/build-skills) · [openai.com/index/introducing-the-codex-app](https://openai.com/index/introducing-the-codex-app/) · [github.com/openai/codex](https://github.com/openai/codex)

GitHub **openai/codex**：★ **116,442** · 9,720 commits · Apache-2.0 · 今日仍在推。CLI 是本地 coding agent；另有 **App / IDE 扩展 / Cloud / Web**。

现网 Codex 产品面：

| 面 | 现网能力 |
|----|----------|
| CLI | 全屏终端 agent；`$skill` / `/skills`；斜杠控模型、权限、sandbox |
| App | 多 thread、worktree、Git diff、技能管理、从 CLI `codex app` 拉起桌面 |
| IDE | VS Code / Cursor / Windsurf **商店扩展**，和 CLI 同一套 agent |
| Skills | Agent Skills 标准；先 name+description（最多约 **2% 上下文 / 8,000 字**），选中再读 `SKILL.md`；仓库扫 `.agents/skills` |
| 隐式选用 | **模型**按 description 选用；`allow_implicit_invocation` 可关 |
| Sandbox | **系统级**（Seatbelt/Landlock 一类）；默认无外网 |
| Approvals | 读仓库可写、出仓/联网要批；另有 auto-review |
| 分发 | Plugins（skill + MCP 打包），不是只丢文件夹 |

星标不是合格线，只说明对标物量级。

---

## 3. 土木版 Codex 对位（本机刚跑的闸，不是 GitHub 上的旧树）

本机：`python scripts/test_civil_codex.py` **PASS**；`live_eval()` **offline_gate_pass**。  
远程开发仓库：★ **0** · 上次 `pushed_at` **2026-08-20**。本日 Codex 面（TUI/thread/config）**还没推上去**。评本机，不要假装远程已是这一版。

| Codex 面 | 本仓 | 判定 |
|----------|------|------|
| CLI TUI | `python -m packing_assistant.civil`：REPL + `/skills` `/new` `/bg` `/sandbox` `/approvals`。不是 Rust 全屏 TUI | **骨架** |
| exec | `civil "任务"` / `civil exec` | **通过** |
| App | `civil app` → :8765 网页工作台 + threads。不是原生桌面、无 Git worktree/diff | **骨架** |
| IDE | `ide/cursor/mcp.json` · `civil mcp --pack construction`。无 Marketplace 扩展 | **骨架（MCP 真，扩展假）** |
| Skills 格式 | `.agents/skills/<id>/SKILL.md` × 66，frontmatter `name`+`description` | **通过（格式）** |
| 渐进披露 | 目录可只读 name+description；**agent_loop 不把 66 份 description 灌进模型**（规则匹配，不是模型选用） | **部分**（比硬灌安全，但不是 Codex 的模型选用） |
| `$` / `@` / 召唤 | 有 | **通过** |
| 隐式选用 | 强短语表 + 长 alias；「施工发票」不误选施工岗 | **窄通过**（规则，不是 LLM） |
| Sandbox | 应用层写根 + 拒 `.env` + 拒 generic spawn。无内核 jail | **部分**（诚实：不是 Seatbelt） |
| Approvals | `untrusted` / `on-request` / `never` + 确认句。无 auto-review | **部分** |
| 并行 thread | `/bg`、同 session 仍串行（Scheduler 锁） | **部分** |
| Plugins / Cloud / Git worktree | 无 | **缺口 / 不做** |

66 份 description 若全塞进 prompt，会超过 Codex 现网的 ~8,000 字目录预算。本仓用规则选用、不灌全表，这是对的；不要改口成「已经和 Codex 一样隐式匹配」。

---

## 4. 旁证（不是合格线）

| 仓 | 现场 | 差在哪 |
|----|------|--------|
| [openai/codex](https://github.com/openai/codex) | ★116442 · 今日推 | 软件仓库 agent。本仓是土木草稿 host，不该比星标。 |
| [OpenBidKit 易标](https://github.com/FB208/OpenBidKit_Yibiao) | ★**2492** · fork 674 · AGPL-3.0 · **今日 09:18 仍推**；目录已有 `.agents/skills` | 写标桌面、查重、十万字营销。本仓不并 AGPL，也不做「1.03 元 11 万字」。他们也在用 Codex skill 目录，说明格式对，但他们是 **给 Codex/助手用的标书工具**，不是自己当 host。 |
| [DDC Skills](https://github.com/datadrivenconstruction/DDC_Skills_for_AI_Agents_in_Construction) | ★**290** · MIT · README 现写 **238** skills（badge/正文仍夹 221）· 今日有 star | 菜谱给 Claude Code，不是运行时。可偷 IFC→Excel 题目，不要灌 238 份进工作台。 |

星标、营销字数、「中标率」不当合格标准。

---

## 5. 仍不合格 / 未做齐

1. **不是完整 Codex 壳**：无原生 App、无 IDE 商店扩展、无系统沙箱、无模型隐式选用、无 plugin 目录。  
2. **远程 GitHub 落后本机**（08-20）；外人 `git clone` 看不到本日 TUI。  
3. 扫描招标 PDF 默认仍拒绝。  
4. 多数 66 岗仍是提纲级 KB，construction 才写满十一章。  
5. 沙箱仍是路径策略，不是内核隔离。  
6. 当「可投标 / 可开工」：**不合格**（且禁止这么宣传）。

有纪律的内部起草搭子 + Codex **骨架**：**部分合格**。  
完整土木版 Codex（对等 CLI/App/IDE/系统沙箱）：**尚未**。

本机复验：

```powershell
python scripts/test_civil_codex.py
python -c "from packing_assistant.runtime.eval_live import live_eval; print(live_eval()['verdict'])"
```
