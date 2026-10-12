# 智能化弱电 · 联网知识（2026-08-14）

岗位对应：综合布线、安防、楼控等子系统原则与联动接口。火灾自动报警主体交消防/电气。不编品牌与像素。

辖区：仅以用户明确给定资料为准；未给则 UNSPECIFIED，不默认新加坡或中国。总体辖区不能补入对象空栏；CN / SG / EU 来源分别归属，不静默混用。本页是注明日期的历史检索参考，不是规范全文库或当前已核验依据；未抽出原文的标准只写全名，条款 UNSPECIFIED。

## CN
- 《建设工程勘察设计管理条例》：https://www.mee.gov.cn/zcwj/gwywj/202001/t20200114_759321.shtml
- 《房屋建筑和市政基础设施工程施工图设计文件审查管理办法》：https://www.moj.gov.cn/pub/sfbgw/flfggz/flfggzbmgz/201307/t20130726_145338.html
- 标题可列（住建部公告/地方政务转载出现的标准名）：《智能建筑设计标准》《建筑电气与智能化通用规范》《民用建筑电气设计标准》《消防设施通用规范》
- 深圳市建筑工务署转载《建筑电气与智能化通用规范》：https://szwb.sz.gov.cn/gwszwfw/zsk/hybz/content/post_9913357.html
- 白银区人民政府转载《消防设施通用规范》公告（废止清单点名《火灾自动报警系统设计规范》相关强条）：https://www.baiyinqu.gov.cn/XZJDBMDW/bmdw/byqcsglzhzfj/fdzdgknr/lzyj/zcfg/art/2022/art_5b02456c663542b4a2a96bd5531e07be.html
- 住建部门户：https://www.mohurd.gov.cn/

公开口径：
- 《建筑电气与智能化通用规范》为强制性工程建设规范，全部条文必须严格执行；与现行标准不一致时以该通规为准。
- 智能化图应与电气、给排水、暖通有关内容协调。深度包括目录、说明、系统图、平面图。
- 常见病：只有点位图没有系统图。本岗不替代火灾自动报警设计。

## SG
- Personal Data Protection Act 2012（SSO 现列 current as at 2026-08-14；最近一次修订 Act 19 of 2025 自 2025-12-05）：https://sso.agc.gov.sg/Act/PDPA2012
- PDPC / PDPA Overview：https://www.pdpc.gov.sg/overview-of-pdpa/the-legislation/personal-data-protection-act
- IMDA Code of Practice for Info-communication Facilities in Buildings（COPIF；检索口径仍列 COPIF 2018，自 2018-12-15 生效）：https://www.imda.gov.sg/regulations-and-licensing-listing/code-of-practice-for-info-communication-facilities-in-buildings
- IMDA 楼内电信设施与用地报审入口：https://www.imda.gov.sg/regulations-and-licensing-listing/interconnection-and-access/imdas-requirements-to-be-included-in-land-use-proposal-submissions-to-ura/code-of-practice-infocomm-facilities
- IMDA Public Consultation on the Review of the COPIF（Issued Date 2026-03-18；属征求意见，不是已生效新版 COP）：https://www.imda.gov.sg/regulations-and-licences/regulations/consultations/consultation-papers/2026/public-consultation-on-the-review-of-the-copif
- Cybersecurity Act（CSA 页更 2026-07-16）：https://www.csa.gov.sg/legislation/cybersecurity-act/
- Cybersecurity Act（SSO）：https://sso.agc.gov.sg/Act/CA2018
- CSA Guide on Conducting Threat Identification and Assessing Effectiveness of Controls for Smart Buildings（2026-03-31 发布；指南不是法律）：https://www.csa.gov.sg/resources/publications/guide-on-conducting-threat-identification-and-assessing-effectiveness-of-controls-for-smart-buildings/
- Building Control Act / Approved Document：https://www1.bca.gov.sg/safety-and-standards/building-control-act/
- Fire Code 2023 章名 Electrical Fire Alarm System；Emergency Voice Communication System and Fire Command Centre (FCC)（Clause 8.2 页更 2025-09-03）：https://www.scdf.gov.sg/fire-safety-services-listing/fire-code-2023/table-of-content
- Emergency Voice / FCC 专页：https://www.scdf.gov.sg/fire-safety-services-listing/fire-code-2023/table-of-content/chapter-8-emergency-lighting-voice-communication-systems/clause-8.2-emergency-voice-communication-system-and-fire-command-centre-fcc
- SCDF Acts, Codes & Regulations（通告标题含 SS 645 电气火灾报警、SS 546 应急广播、Advisory on Installation of Gates/Turnstiles in Buildings）：https://www.scdf.gov.sg/fire-safety-services-listing/downloads/acts-codes-and-regulations
- SCDF Plan Approval：https://www.scdf.gov.sg/fire-safety-services-listing/plans-submission-process/plan-approval

公开口径：
- PDPA 规制组织收集、使用和披露个人数据；由 PDPC 执行。安防录像、门禁身份、访客登记属个人数据场景时只对 PDPA 标题，不编保存天数、像素或公安平台参数。
- IMDA 官方列 COPIF，用于楼宇信息通信设施。页为官方标题入口，正文常为动态加载。本岗只写全名 COPIF 2018，不编机房面积或管槽尺寸。2026-03-18 征求意见不是已强制新版。
- CSA Cybersecurity Act 建立国家网络安全框架；CII 部门含 Transport（Land / Maritime / Aviation）、Infocomm 等。普通商业楼弱电不因此自动变成 CII。Smart Buildings 指南补充 TR 111:2023、IMDA IoT Cybersecurity Guide，不是出图依据。
- 报警、应急广播、消防控制室对 SCDF 章名 Electrical Fire Alarm System 与 Emergency Voice Communication System and Fire Command Centre。页称应急语音须符合 SS 546。本岗只写联动接口，不替代火灾自动报警与消防专篇，不把目录里的面积/高度数字当本项目已判定门槛。
- 安防/门禁断电释放原则不替代消防专篇。闸机/门禁另有 SCDF 通告标题 Advisory on Installation of Gates/Turnstiles in Buildings（2021-12-01）。
- 不编 SS 条款号与品牌型号。

## 通用
禁令：不编摄像头像素和存储天数（除非用户给）；不编品牌型号；不宣称技防审图或 IMDA/SCDF/PDPC 通过；不替代火灾自动报警设计；禁止把住建部令或 JGJ 当新加坡法；不写「可以开工 / 报审通过」。
