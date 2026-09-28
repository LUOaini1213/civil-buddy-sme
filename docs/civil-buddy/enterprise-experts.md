# 全土木企业专家总表

对照：[易标 OpenBidKit](https://github.com/FB208/OpenBidKit_Yibiao) 把「投标」拆成可演示的模块；本表把同一拆法扩到土木企业全岗位。  
入口仍是一个产品 `civil-buddy`，**一个路由器 + 多专家**。专家不是 40 个常驻进程。

## 1. 易标实际卖什么（必须对齐，再扩）

| 易标模块 | 对应岗位动作 | 本产品是否照搬 |
|----------|--------------|----------------|
| 解析招标文件 | 读招标/图纸/清单，抽评分点 | 经营类专家要 |
| 技术标目录 → 扩写 | 按评分点出章节 | 经营类；施工方案用另一套 11 章 |
| 商务标 | 报价表、资信 | 经营类；无单价则 TBD |
| 企业知识库 | 历史方案、业绩、证书 | 共享层，所有专家只读 |
| 废标项检查 | 响应完整性 | 经营类质检，可泛化为「交付质检」 |
| 标书查重 | 套话/自抄 | 经营类 |
| MinerU 解析 | 扫描件进库 | 共享层 |
| 可编辑工作流 | 人改目录再生成 | 人机纠偏（海之子评分项） |

易标**不是**全土木：不做危大方案签发、不做现场交底、不做资料闭合、不管工人白话。那是我们要补的面。

同类轮子：

| 项目 | 能借什么 | 不要借什么 |
|------|----------|------------|
| [FB208/OpenBidKit_Yibiao](https://github.com/FB208/OpenBidKit_Yibiao) | 桌面工作区、解析→提纲→扩写→质检、企业库 | AGPL 整仓 fork；把产品做成「只写标书」 |
| [yibiao.pro](https://yibiao.pro) 商业版 | 30 秒框架 / 多专家写标的产品叙事 | 营销口径里的「2 分钟出可投标」 |
| [datadrivenconstruction/DDC_Skills…](https://github.com/datadrivenconstruction/DDC_Skills_for_AI_Agents_in_Construction) | 221 个施工公司自动化 skill：BIM→Excel、组价、日报、照片 | 西式工料库当国内定额 |
| [toreydai/construction-rag-demo](https://github.com/toreydai/construction-rag-demo) | 方案/规范/商务多源，矛盾单独标 | 假装库里已有全套 JGJ |
| [MayconAlvesss/AECAgent-RAG](https://github.com/MayconAlvesss/AECAgent-RAG) | 条款核验流程 | 欧标文本当中国库 |
| [opendatalab/MinerU](https://github.com/opendatalab/MinerU) | 扫描规范进 Markdown | — |
| [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | 引用回页 | — |

## 2. 和 WorkBuddy / 易标 的产品差

```text
WorkBuddy     通用办公：什么都能写
易标          投标办公：招标→技术标/商务标→查重废标
civil-buddy   土木企业：经营 + 技术 + 施工 + 安质环 + 商务 + 资料 + 工人
```

土木体现在：工序对象、条款双表、辖区不混、无来源数字待填、工人稿和技术稿分开、高风险确认门。不是专家头像多。

工具可见性是三级（通用 / 大类共享 / 专家独有），对照表见 `docs/yibiao-mcp-map.md` 与 `workbench/yibiao-map.json`。本轮只对齐施工方案专家，默认新加坡工地 SG。

## 3. 架构：一大类里多个专家

```text
                    ┌─ 用户 / 项目包 / 上传 PDF ─┐
                    v
              总控（router + 确认门 + 辖区）
                    │
     ┌─────────┬────┴────┬─────────┬─────────┐
     v         v         v         v         v
   经营大类  技术大类  施工大类  安质环   商务/资料/工人
     │         │         │         │         │
   多名专家  多名专家  多名专家  多名专家  多名专家
                    │
                    v
         共享：规范库(只读) / 企业库 / 硬规则 / 出稿管道
                    │
                    v
         质检专家（断言扫描、响应检查、混辖区）
```

规则：

- 大类 = 企业部门；专家 = 岗位动作；skill 文件 = `experts/<category>/<id>.md`
- 一次任务默认 **1 个主笔 + 至多 2 个会签**；海之子演示再砍到 1 主笔
- 专家不得覆盖非主笔章节；假设号由总控发 `A001…`
- 企业库 / 规范库只读；规范全文不进仓库

## 4. 八大类专家名册

状态：`v1` 已有骨架 · `demo` 海之子两周要做实 · `later` 企业版再填

### A. 经营投标（易标主场，我们只取土木投标）

| id | 专家 | 典型产出 | 状态 |
|----|------|----------|------|
| `bid-parse` | 招标解析 | 评分点、废标雷、工期/资质摘录 | demo |
| `bid-tech` | 技术标主笔 | 技术标目录与扩写草稿 | later |
| `bid-commercial` | 商务标 | 报价表头、资信目录 | later |
| `bid-compliance` | 废标/响应检查 | 未响应清单 | demo |
| `bid-similarity` | 查重 | 套话/自抄提示 | later |

### B. 勘察设计（按施工图审查专业拆）

房建：建筑 / 结构 / 岩土勘察 / 给排水 / 暖通 / 电气 / 消防 / 钢结构 / 园林。  
土木：市政道路 / 桥梁 / 隧道 / 交通工程。  
企业侧：设计统筹（会审、提资、变更）。已拆开，不再用「结构岩土」一个岗。

### C. 施工生产（现场技术员）

| id | 专家 | 典型产出 | 状态 |
|----|------|----------|------|
| `construction` | 施工方案 | 专项方案讨论提纲 | **v1 已写满** |
| `method-hazard` | 危大识别 | 是否危大、要否专家论证（只判定不签发） | demo |
| `schedule` | 进度 | 横道/关键线路口径 | later |
| `survey` | 测量 | 控制点/放样检查表 | later |
| `site-photo` | 影像资料 | 照片分类与隐患描述（不判合格） | later |

### D. 安全质量环保

| id | 专家 | 典型产出 | 状态 |
|----|------|----------|------|
| `safety-brief` | 安全交底 | 班前白话 + 技术交底草稿 | demo |
| `quality` | 质量 | 检查表骨架，无合格结论 | later |
| `env` | 环保文明 | 扬尘/弃土待填表 | later |
| `emergency` | 应急 | 预案目录，联系人待填 | later |

### E. 商务合约造价

| id | 专家 | 典型产出 | 状态 |
|----|------|----------|------|
| `cost` | 造价 | 工程量拆分，单价 TBD | v1 骨架 |
| `variation` | 变更签证 | 签证口径草稿 | later |
| `contract` | 合约 | 风险条款摘录 | later |

### F. 资料监理

| id | 专家 | 典型产出 | 状态 |
|----|------|----------|------|
| `supervision` | 资料/监理 | 资料目录、回复草稿 | v1 骨架 |
| `acceptance` | 验收 | 验收资料闭合检查 | later |

### G. 项目经理与工人（海之子「为工友谋幸福」）

| id | 专家 | 典型产出 | 状态 |
|----|------|----------|------|
| `pm-daily` | 项目日报 | 日报骨架 | later |
| `worker-brief` | 工友白话 | 3 分钟班前口播稿 | demo |
| `worker-rights` | 劳务权益 | 合同/考勤/讨薪口径（普法，不诉讼） | later |

### H. 海外与总控

| id | 专家 | 典型产出 | 状态 |
|----|------|----------|------|
| `jurisdiction` | 辖区守门 | CN / SG / HK / EU / DUAL，禁混用 | 规则已在 `jurisdictions.md` |
| `qa-auditor` | 质检 | 断言扫描、条款双表、混辖区 | **v1 脚本已有** |

## 5. 专家卡片（每个 md 四段，禁止再抄硬规则）

```markdown
# <id>
## 何时上场
## 必问输入（缺则停或 Axxx）
## 章节/交付骨架
## 额外禁令
```

会签权：只允许改自己主笔的 token / 章节。冲突由总控按主笔表裁。

## 6. 海之子两周只激活这一条链

不要上齐八大类。演示链：

```text
method-hazard（危大判定，人可改）
    → construction（方案 AI 草稿，已有）
    → safety-brief + worker-brief（一页技术交底 + 工友白话）
    → qa-auditor（扫描 + 双表）
```

经营类若要蹭易标差异：加 `bid-parse` 读一份虚构招标，抽出「须编制临边专项方案」再交给上面的链。这比再写一本技术标更像土木企业。

## 7. 企业版再铺

1. 知识库：企业历史方案（脱敏）+ 用户规范 PDF + 危大清单；矛盾门学 construction-rag-demo  
2. 经营：易标同构的「解析→提纲→扩写→废标检查」，输出标为草稿  
3. 造价/BIM：可链 DDC 的 `estimate-builder` / `ifc-to-excel`，不当国内定额  
4. 现场：日报、照片分类  
5. 只读 MCP：规范库 / 图档，V2，不回写

## 8. 2026-08-14 现场状态

- 16 大类 / 66 岗均 `aligned: true`，各有独有写入器；兄弟调用拒绝。
- 每岗 `web-knowledge.md` + 大类 `_shared` + `company/web-portals.md` 已联网两遍（2026-08-14）。只写官方标题，条款 UNSPECIFIED。
- 默认新加坡工地。`sg_only` / `zone_banner`：CN 成稿不再静默带 PSSCOC / GeBIZ / CONQUAS / toolbox / CORENET。
- 抽出 APPBCA-2026-12（2026-07-23）：2026-10-01 CORENET X 仅 GFA≥5,000 m² 强制 Gateway。
- 仍未做：标书查重、图纸/PDF 解析、施工以外 Word 填模、PE/QP/RTO 签认。
