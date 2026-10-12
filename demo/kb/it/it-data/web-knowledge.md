# 数据备份 · 联网题名（2026-08-14 现场）

检索日 2026-08-14。本岗只列备份 / 灾难恢复 / 留存相关题名。禁止编造 RPO、RTO、保留天数、演练周期。无业务确认 → `[A001] RPO 待填`、`[A002] RTO 待填`。不写备份软件许可号、介质序列号、完整内网段。2026-08-14 已打开 PDPA SSO / CSA Codes of Practice。

辖区：以本次明确项目辖区为准；未提供则 UNSPECIFIED，不默认国家。多个对象分别归属，禁止静默混用 CN / SG。本页只保留历史检索入口，不构成本次适用或现行条款核实。

## CN

- 《中华人民共和国网络安全法》（2016-11-07 通过；2025-10-28 修正，2026-01-01 施行）。2016 年公布文本曾写「数据分类、重要数据备份和加密」等原则；2025 修正后条序以重新公布文本为准，禁止把旧条号或「日志不少于六个月」直接填进本公司承诺。https://www.cac.gov.cn/2025-12/29/c_1768735112911946.htm
- 《中华人民共和国数据安全法》（2021-06-10 通过，2021-09-01 施行）：数据处理含收集、存储、使用、加工、传输、提供、公开；重要数据处理者义务见正式文本。https://www.cac.gov.cn/2021-06/11/c_1624994566919140.htm
- 《中华人民共和国个人信息保护法》（2021-08-20 通过，2021-11-01 施行）：保存期限「为实现处理目的所必要的最短时间」是原则口径，不是本公司保留年数。https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm
- GB/T 22239-2019《信息安全技术 网络安全等级保护基本要求》。二次资料常转述二级侧重本地备份、三级谈异地实时备份与热冗余；未抽原文标 `unverified`，禁止自编「第 x.x.x 条」或把转述写成已承诺分钟数。
- GB/T 20988-2025《网络安全技术 信息系统灾难恢复规范》（2025-06-30 发布，2026-01-01 实施）：https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=ABE370E7DABA83CD71BCAC2042A95F70 。替代 GB/T 20988-2007；旧版能力等级与 RTO/RPO 对照表不得再当现行数字抄进承诺栏。
- GB/T 22080-2025《网络安全技术 信息安全管理体系 要求》（2026-01-01 实施）只作题名对照。

## SG

- PDPA 2012（SSO 现行文本截至 2026-08-14）Part 6「Care of Personal Data」题名含 Protection / Retention / Transfer；Part 6A「Notification of Data Breaches」。https://sso.agc.gov.sg/Act/PDPA2012
- PDPC 门户：https://www.pdpc.gov.sg/ 。不把 PDPC 执法案例里的罚款额写成本公司 SLA。
- CSA 页题 *Codes of Practice*（页更 2026-07-29）：*Cybersecurity Code of Practice for Critical Information Infrastructure 2026* 管指定 CII 的恢复与演练义务；施工企业未指定则不得套用 CII 时限。https://www.csa.gov.sg/legislation/codes-of-practice/

## ISO

- ISO/IEC 27001:2022 *Information security, cybersecurity and privacy protection — Information security management systems — Requirements*
- ISO/IEC 27002:2022 *… Information security controls*（备份/可用性控制只写题名，不编保留点）

## 本岗口径

- 「3-2-1」是业界讨论口诀，不是 GB / PDPA / ISO 条款号。
- 禁止：4 小时 RPO、24 小时 RTO、可在 N 小时内恢复、已具备恢复能力、满足等保三级备份。演练无记录则整段待填。
