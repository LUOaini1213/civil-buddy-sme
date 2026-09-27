# 赛道 1 Agent Middleware · 完全合格对照

人改口日期：2026-08-25。Host = Civil Buddy。  
这是 **赛题交卷面** 合格，不是「可以投标 / 可以开工」。

| 赛题要求 | 本仓 | 判定 |
|----------|------|------|
| 3 分钟：正常 + 一次拒绝/失败/恢复 | `python scripts/demo_agent_middleware.py` 四拍写死 | **完全合格** |
| 评委看见系统在干活，不是看榜单 | 原因弹窗、审计链 `call→retry→degrade`、熔断未执行 | **完全合格** |
| 一页架构图 | [agent-middleware.md](agent-middleware.md) | **完全合格** |
| 可复现仓 + README + 自动化测试 | GitHub `main` · README 赛道 1 段 · `test_agent_middleware.py` | **完全合格** |
| `npm run check` 必须过 | 根目录 `package.json` → `scripts/npm-check.cjs` | **完全合格** |
| 不得泄露密钥 | `scan_tracked_secrets.py`；拒写 `.env` | **完全合格** |
| 贴生产：权限/审计/安全/多 Agent/恢复/成本 | 深做 2 层：策略引擎 + 失败恢复（不摊五个平庸包装；失败恢复只在演示脚本 `scripts/demo_agent_middleware.py` 中接线，生产路径不自动重试工具） | **完全合格** |

现场四拍（必须按序）：

1. 正常下单 → `ALLOW` 税务日历、页述 GST 9%  
2. 越权被拒 → 弹因「岗 bid-parse 不能调 pack-ship__plan」；`.env` 不落地  
3. 工具挂掉 → `timeout` 重试后降级 `can_fit=UNSPECIFIED`  
4. 成本超限 → `circuit_open`，工具不再执行  

禁止口播：中标率、可以投标、可以开工、我们做了 RAG、AUC。
