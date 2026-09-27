# Civil Buddy 团队协作与分活

仓库：https://github.com/LUOaini1213/civil-buddy-sme
Owner：`LUOaini1213`

---

## 一、权限怎么给（Owner 操作）

### 1. 邀请协作者

1. 打开 https://github.com/LUOaini1213/civil-buddy-sme
2. **Settings** → **Collaborators and teams**（或 **Manage access**）  
3. **Add people** → 输入队友 GitHub 用户名  
4. 权限选 **Write**（可推分支、开 PR）  
5. 队友邮箱点 **Accept invitation**

| 角色 | 建议权限 |
|------|----------|
| Owner（你） | Admin |
| 阶段1 开发 | Write |
| 阶段2 开发 | Write |
| 只读观摩 | Read |

### 2. 保护 main（强烈建议）

**Settings** → **Branches** → **Add branch protection rule**  
- Branch name pattern: `main`  
- 勾选：**Require a pull request before merging**  
- （可选）Require 1 approval  

### 3. 密钥

- 每人本地 `.env` / `deepseek api.txt`，**禁止提交**  
- 已在 `.gitignore`

---

## 二、分工总表（已定）

| 角色 | GitHub | 负责目录/模块 | 交付 |
|------|--------|----------------|------|
| **阶段1** | **@cuizhi-chat** | `material_parser` / `structure_*` / `box_scheme` / `packing.py` / `structure_calc` / `knowledge/` / `test/excel` / 确认页 | 可信的 `boxes[]` + 确认单 |
| **阶段2** | **@niudongrui** | `planner` / `loader` / `bin3d` / `evaluator` / `risk_*` / `visualizer` / `frontend` / `skjolber-service` | 装柜 layout + 风险 + 三视图 |
| **主控/联调** | **@LUOaini1213** | `orchestrator` / `finalize` / `harness` / `gateway` / `container_select` | 选柜、端到端、发布 |

| 同学 | 优先 Issues |
|------|-------------|
| cuizhi-chat（阶段1） | #1–#8（联调 #4 #11） |
| niudongrui（阶段2） | #9–#15（联调 #4 #11） |
| LUOaini1213（主控） | #16–#18 |

**改 `docs/api-spec.md` 或 `boxes[]` 字段 = 两边都要知情。**

---

## 三、分支约定

```text
main
feat/phase1-<简述>
feat/phase2-<简述>
fix/<简述>
docs/<简述>
```

流程：`main` 拉分支 → 开发 → `git push` → **Pull Request** → Review 合并。

---

## 四、Issue 分活清单（复制到 GitHub Issues）

### 标签建议

`phase1` · `phase2` · `orchestrator` · `bug` · `docs` · `priority-p0` · `priority-p1`

---

### 阶段1 Issues

#### P1-01 [P0] 校准箱型库与现场铁架参数
- Labels: `phase1`, `priority-p0`
- 路径：`knowledge/packing_knowledge_base.json`
- 验收：1.1/2/4/6 米铁架、铁笼外廓/自重/载荷与现场一致

#### P1-02 [P0] 尺寸覆盖 dims_override 补齐高频件
- Labels: `phase1`, `priority-p0`
- 路径：`knowledge/dims_override.json`
- 验收：REDACTED-CLIENT高频件不再靠错误估算

#### P1-03 [P0] 结构结论业务口径文档
- Labels: `phase1`, `docs`, `priority-p0`
- 路径：`docs/` 新增短文 + 与 `structure_calc` 对齐
- 验收：通过 / 需加强 / 不通过 有可执行标准

#### P1-04 [P0] boxes[] 契约冻结检查
- Labels: `phase1`, `phase2`, `priority-p0`
- 路径：`docs/api-spec.md`、`adapters.py`
- 验收：阶段2仅凭 boxes 能装柜；字段变更有 PR 说明

#### P1-05 [P1] 确认页六区 UI / 展示
- Labels: `phase1`, `priority-p1`
- 参考：`docs/team-a-user-output-template.md`
- 验收：材料表、箱明细、结构、确认按钮齐全

#### P1-06 [P1] PDF 装箱单解析边界
- Labels: `phase1`, `priority-p1`
- 路径：`tools/packing_list_parser.py`、`test/*.pdf`
- 验收：test 下 PDF 材料行完整、少漏少重

#### P1-07 [P1] Excel 业务集维护
- Labels: `phase1`, `priority-p1`
- 路径：`scripts/build_steel_test_set.py`、`test/excel/`
- 验收：`run_excel_tests.py` 全绿

#### P1-08 [P2] 结构计算书导出
- Labels: `phase1`, `priority-p1`
- 路径：`calc_report_md` → docx/pdf
- 验收：单箱可导出

---

### 阶段2 Issues

#### P2-01 [P0] 装载策略调参（并排/二层/COG）
- Labels: `phase2`, `priority-p0`
- 路径：`tools/bin3d.py`、`agents/planner.py`
- 验收：真实箱单 1 柜尽量装下；COG 不无误阻断

#### P2-02 [P0] 三视图 Vue 打磨
- Labels: `phase2`, `priority-p0`
- 路径：`frontend/index.html`、`visualizer`
- 验收：可缩放、分柜、图例清晰

#### P2-03 [P0] 与阶段1 boxes 联调
- Labels: `phase2`, `phase1`, `priority-p0`
- 验收：阶段1 导出 JSON → 阶段2 只喂 boxes 跑通

#### P2-04 [P1] skjolber 服务联调（有 JDK 时）
- Labels: `phase2`, `priority-p1`
- 路径：`skjolber-service/`、`SKJOLBER_URL`
- 验收：health + pack 与 python 对照

#### P2-05 [P1] 评估/风险阈值对齐业务
- Labels: `phase2`, `priority-p1`
- 路径：`evaluator.py`、`risk_compliance.py`、`knowledge`
- 验收：空隙/偏心/重量阈值有业务确认

#### P2-06 [P1] 装柜报表导出
- Labels: `phase2`, `priority-p1`
- 验收：layout + 绑扎建议 + 利用率表

#### P2-07 [P2] replan 闭环验证
- Labels: `phase2`, `priority-p1`
- 路径：evaluator → planner 回路
- 验收：装不下时自动加柜/改策略可演示

---

### 主控 / 联调 Issues

#### O-01 [P0] 端到端演示脚本固定
- Labels: `orchestrator`, `priority-p0`
- 路径：`gateway`、`main.py`、`scripts/run_test_shipments.py`
- 验收：一条命令演示 PDF→确认→报告

#### O-02 [P1] 主控选柜 UI 展示
- Labels: `orchestrator`, `priority-p1`
- 验收：开头推荐 + 结尾是否换柜 在前端可见

#### O-03 [P1] 保护 main + PR 规范宣贯
- Labels: `docs`, `priority-p0`
- 验收：队友按 PR 合入

---

## 五、一键创建 Issues（可选）

本机安装 [GitHub CLI](https://cli.github.com/) 并登录后：

```bash
gh auth login
python scripts/github_setup_team.py
```

脚本会在仓库创建上述 Labels 与 Issues（幂等：同名 title 跳过）。

---

## 六、每周节奏建议

| 时间 | 动作 |
|------|------|
| 周一 | 同步 main、认领 Issue |
| 周中 | 各自分支开发，小 PR |
| 周五 | 联调：阶段1 boxes → 阶段2 装柜；更新演示 |

---

## 七、PR 模板要点

- 改了什么 / 怎么测  
- 是否改动 `boxes[]` 契约（是则 @ 对方阶段）  
- 无密钥文件  
