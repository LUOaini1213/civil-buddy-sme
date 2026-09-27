# Agent Middleware · 一页架构 + 3 分钟演示

赛道 1 交卷面：**完全合格**（[对照表](track1-qualified.md)）。不是可以投标、可以开工。

不做五个平庸包装。Runtime 只深做 **两层**：

1. **策略引擎** — 谁能调哪个工具、花多少 token、能否碰生产数据；拒绝当场弹出原因  
2. **失败恢复** — 下游超时 / 工具报错 → 重试 → 降级 `UNSPECIFIED` → 留下审计链（演示脚本 `scripts/demo_agent_middleware.py` 里的一拍；生产路径不自动重试工具，`execute_with_recovery` 只有这个脚本调用）  

`submit_blocked=true`。不判定可以投标 / 可以开工。密钥不进 git。

## 一页架构

```text
                         用户 / Host
                              │
                              v
                      run_agent / ToolEngine
                              │
              ┌───────────────┴───────────────┐
              │                               │
              v                               v
     ┌─────────────────┐            ┌──────────────────┐
     │  策略引擎 Policy  │            │  失败恢复 Recovery │
     │  who / tool /    │            │  timeout → retry  │
     │  tokens / prod   │            │  → degrade        │
     │  DENY + 原因弹窗  │            │  → 审计链         │
     └────────┬────────┘            └─────────┬────────┘
              │                               │
              v                               v
         ALLOW 才执行工具              数字永不编造
         密钥路径 / D:\layout 拒       can_fit=UNSPECIFIED
         exclusive 跨岗拒             circuit 步数/token 熔断
```

评委记住一句：**闸在后端，prompt 改不掉；失败降级不编柜数。**

## 3 分钟现场剧本（写死）

不需要 API Key：

```powershell
python scripts/demo_agent_middleware.py
npm run check
```

| 拍 | 动作 | 评委必须看见 |
|----|------|----------------|
| 1 正常下单 | `出一份税务日历` | `ALLOW` · `wrote=true` · 页述 **GST 9%** |
| 2 越权被拒 | `bid-parse` 调 `pack-ship__plan`；写 `.env` | **原因弹窗**：「岗 bid-parse 不能调 pack-ship__plan」；密钥路径拒绝，文件不落地 |
| 3 工具挂掉 | 下游超时 | 审计：`call → retry → degrade` · `can_fit=UNSPECIFIED` |
| 4 成本熔断 | session steps/tokens 用尽 | `circuit_open` · 原因含「成本超限」· 工具未再执行 |
| 附 死锁 | finance-tax ⇄ pack-ship Wait-for 成环 | `deadlock` · 当场失败 · 不是 `session_busy` |

口播禁句：可以投标、可以开工、中标率、GeBIZ 代交、我们做了 RAG。

## 复现

```powershell
git clone https://github.com/LUOaini1213/civil-buddy-sme.git
cd civil-buddy-sme
pip install -r requirements.txt
npm run check
```
