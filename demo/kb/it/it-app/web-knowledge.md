# 系统需求 · 联网题名（2026-08-14 现场）

岗位对应：把法规/标准只写成需求约束的题名，不编品牌、不写接口实地址、不宣称合规通过。人脸、身份证、行踪属敏感/个人数据时只列告知与最小必要原则，不做法务结论。2026-08-14 已打开 PDPA SSO / CSA / IMDA。

辖区：以本次明确项目辖区为准；未提供则 UNSPECIFIED，不默认国家。多个对象分别归属，禁止静默混用 CN / SG。本页只保留历史检索入口，不构成本次适用或现行条款核实；未抽原文条款 → `unspecified_clause`。

## CN

- 《中华人民共和国网络安全法》（2016-11-07 通过；根据 2025-10-28 修正决定修正）。需求里「拟申报等保几级」只记用户口头目标；2025 修正后条序以重新公布文本为准。https://www.cac.gov.cn/2025-12/29/c_1768735112911946.htm
- 《中华人民共和国数据安全法》（2021-06-10 通过，2021-09-01 施行）：https://www.cac.gov.cn/2021-06/11/c_1624994566919140.htm
- 《中华人民共和国个人信息保护法》（2021-08-20 通过，2021-11-01 施行）：合法正当必要、最小范围收集；敏感个人信息含生物识别、行踪轨迹及不满十四周岁未成年人信息。公共场所图像采集须为维护公共安全所必需并设显著提示。https://www.cac.gov.cn/2021-08/20/c_1631050028355286.htm
- 制度名：网络安全等级保护制度。定级用 GB/T 22240-2020《信息安全技术 网络安全等级保护定级指南》；基本要求 GB/T 22239-2019；测评 GB/T 28448-2019。IT 不替企业定级。
- GB/T 22080-2025《网络安全技术 信息安全管理体系 要求》（2026-01-01 实施）可与 ISO/IEC 27001 对照，不是上线批准。

## SG

- Personal Data Protection Act 2012：治理组织对个人数据的 collection, use and disclosure。SSO 现行文本截至 2026-08-14。https://sso.agc.gov.sg/Act/PDPA2012
- PDPC 门户与 DPO 登记入口：https://www.pdpc.gov.sg/ 。需求说明书写「须指定 DPO」，不编登记号。DPO 入门现题 *Kickstart Your Data Protection Journey* https://www.pdpc.gov.sg/organisations/resources/getting-started-as-a-data-protection-officer-dpo
- PDPA 目录题名可引用：Consent / Purpose / Access and Correction / Care of Personal Data / Notification of Data Breaches。不编条号义务清单。义务概览 https://www.pdpc.gov.sg/data-protection-obligations
- IMDA 页题 *Infocomm Media Cyber Security*；Telecommunications Cybersecurity Code of Practice：指定电信持牌人，不是智慧工地 APP 的默认验收依据。https://www.imda.gov.sg/regulations-and-licensing-listing/infocomm-media-cyber-security
- CSA 页题 *Cybersecurity Act*（页更 2026-07-16）：仅当用户出示 CII / STCC / ESCI / FDI 指定时才写入范围，否则排除。https://www.csa.gov.sg/legislation/cybersecurity-act/ ；SSO https://sso.agc.gov.sg/Act/CA2018
- CSA 页题 *Codes of Practice*（页更 2026-07-29）：*Cybersecurity Code of Practice for Critical Information Infrastructure 2026*。未出示 CII 指定不得套用。https://www.csa.gov.sg/legislation/codes-of-practice/

## ISO

- ISO/IEC 27001:2022 *Information security, cybersecurity and privacy protection — Information security management systems — Requirements*
- ISO/IEC 27002:2022 *… Information security controls*（非功能需求可点名「对照控制目录」，不可写「通过 ISO 认证」）

## 通用 禁令

- 劳务实名、门禁人脸：CN 会签 PIPL 敏感信息规则；SG 会签 PDPA。字段以用户/地方平台接口说明书为准，缺则待填。
- 施工企业默认不是 CSA 指定 CII，也不是 IMDA 电信持牌人。
- 禁止：指定唯一品牌、编造接口 URL、本系统已属等保三级所以合规、可以上线。RPO/RTO 会签 `it-data`，本岗不填小时数。
