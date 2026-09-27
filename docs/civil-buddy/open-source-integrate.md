# 土木工作台开源集成清单

更新日期：2026-09-21。学校比赛用途。Excel 保留上一轮 6 张表的清单；本轮仅更新 Markdown 的排程运行状态与边界，未改 Excel，新增实现以本文和 [排程说明](planning-workbench.md) 为准。

[Excel 清单](open-source-integrate.xlsx) 保留全部历史及后续追加记录。主清单 51 条（18 条建议、13 条历史暂缓、20 条新增/深化），包括同一生态功能和书签，包含自研模块与复用组件，不能视为 51 个独立开源项目。首批目标 4 项。“GitHub加深命中”15 条是检索证据，不重复计数。

本地主目录 Excel 副本另有一张架构研究表，其独有内容保留在 [专业工作台架构研究（历史摘录）](workbench-architecture-notes.md)，不覆盖此清单中较新的排程记录。

## 首批交付与状态

| 顺序 | 模块 | 可演示流程 | 数据前提 | 当前状态 | 复用组件 |
| --- | --- | --- | --- | --- | --- |
| 首批 1 | 截面性质 | 现有 CAD 选集 → 有孔截面 A/形心/I → 保存报告 | 闭合轮廓与单位，长度可为空 | 已接入；离线/HTTP/浏览器真重启恢复通过；CAD Agent 已接；真实项目待验收 | sectionproperties 深化 / 确定性几何 |
| 首批 2 | Pynite 梁分析 | 跨度/材料/截面/荷载表单 → 反力与内力挠度 | 真实输入或明确标为教学算例 | 已接入；离线/HTTP/浏览器改参、撤销、保存重开通过；真实项目待验收 | Pynite 受限参数适配器 |
| 首批 3 | 施工排程与日期甘特 | 明确工期/日历→CPM→资源预览→保存→对话建议/确认→基线/周PPC→项目包交接 | 本地 PDF 候选完成日期检查，缺工期/日历/依赖/资源，不能作为真实 CPM 验收 | 已接入；本轮 Agent19、对话HTTP6、UI25通过；浏览器合成资源方案11→12天、撤销回11天通过。项目包旧历史兼容已通过，包专项 11/11 | Frappe Gantt + 自研CPM/版本/PPC/受限Agent/项目包 + OR-Tools；文件读取MPXJ |
| 首批 4 | IFC 检查与差异 | IFC + IDS → 缺项列表；两版 IFC → 差异定位 | 授权 IFC、IDS 及两版稳定 GUID 模型 | 已接入；离线/HTTP/浏览器检查、差异、保存通过；真实项目待验收 | IfcTester / IfcDiff，复用已有生态 |
| 后续 | 问题交换 / 边坡 / 管网 | 按候选页逐项验证，先建立一条完整算例 | 资料到位再进入开发 | 尚未实施 | BCF / PySlope / WNTR / PySWMM |
| 后续 | 点云 / 交通 / 碳核算 | 每项独立 worker 与示例数据包 | 许可清楚的点云、流量和 LCA 数据 | 尚未实施 | PDAL + Potree / SUMO / Brightway |
| 本轮深化 | 场内最短路线 | 明确道路→150m→封路180m→不可达 | 明确方向、权重和来源 | 已接入；离线8项、桌面浏览器通过，真实路网待验 | NetworkX，独立于施工关键路径 |

运行方法与实际能力见 [工程工作台](engineering-workbench.md)、[施工排程工作台](planning-workbench.md)，[第三方说明](../../demo/static/engineering-notices.txt) 记录实际安装版本和许可差异。

## 四人分工

| 分工 | 职责 | 接口约定 | 验收交付 |
| --- | --- | --- | --- |
| 你 / 集成负责人 | 主界面、项目保存、数据来源、受限 Agent 工具、演示串联 | 统一 project_id、单位、错误格式、取消、版本；汇总许可证 | 端到端演示及可重现环境；不承担其他人的全部模块实现 |
| 同事 A / 结构计算 | 截面性质、Pynite 适配器、解析验算样例 | 与 CAD 保持实体 ID，参数表说明单位和输入依据 | 有孔截面 + 简支梁结果与解析一致 |
| 同事 B / BIM 质量 | IFC 上传、IDS 检查、IfcDiff 对比和构件定位 | 输出 GUID、规则 ID、前后值及错误原因 | 至少一个缺项、一个新增、一个删除、一个修改可复现 |
| 同事 C / 计划与演示 | 施工日历/依赖、资源方案、基线周PPC、文件往返与比赛材料 | 沿用项目存储和撤销，不另起第二套业务后台 | 8/11天算例、MPP/P6读取、重启恢复；区分自研与复用、真实与合成。 |

## 使用和署名口径

- **用途：** 学校比赛的选型与交付记录。更新日期 2026-09-21。非商业用途不自动免除开源许可义务。
- **首批状态：** 四项首批按本轮离线、HTTP 与浏览器证据标注状态；真实项目未验收。其余候选不因安装了库就标为已接入。
- **原始记录：** 保留全部6张表与历史记录，追加本轮6项排程/路线/候选。第6表15条检索命中不重复计数。
- **新旧优先级：** 历史 1–12 为原排序。当前 P0 首批、P1 后续、P2 拓展、P3 备选/暂缓；P0 不表示完成。
- **原创性说明：** 比赛章程尚未提供，不能保证任何复用比例符合比赛规则。提交前核对开源复用、AI 辅助与成果署名要求。
- **自研与复用：** 自研重点为统一项目/来源追溯、参数确认、受限工具调用、校验、撤销、界面和测试。求解器与控件如实署名。
- **交付物：** 可重现启动说明、依赖版本、THIRD_PARTY_NOTICES、上游许可文本、适用源码/修改记录、测试输入与预期、演示说明。
- **工程数据：** 缺失参数明确留空；只使用图中明确标注或用户确认的数值。教学算例与实际项目分开标记。
- **AGPL/GPL：** 不按“传染”一概排除。按所选版本和实际复制、修改、分发、网络服务方式履行条款；独立进程不是自动豁免。
- **许可来源：** GNU AGPL/GPL 条文、具体仓库 LICENSE 和依赖源文件优先。未明确授权的项目先补证，不直接复制。
- **数据许可：** 软件开源不等于图纸、地图、模型、点云或 LCA 数据可分发。公开演示数据应有授权。
- **审查边界：** 本轮已实现首批适配器；研究清单不代替工程验收。维护提交不保证质量，未提供的真实图纸/模型仍待验收。

## 新增与深化候选

### N01 sectionproperties

- **优先级/状态：** P0 首批。已接入；离线/HTTP/浏览器真重启恢复通过；CAD Agent 已接；真实项目待验收。
- **功能：** 截面性质。已接入 CAD 确认材料区/孔洞，计算面积、形心、惯性矩、主轴及回转半径。
- **岗位与接入：** structure / cad-to-3d。Python 受限计算工具；CAD Agent 已接 cad_section_properties，只用已确认选集和单位，不补尺寸。
- **许可与署名：** MIT 主项目；cytriangle LGPL3；Triangle 另有非商业/分发条件。 保留主库与依赖许可、版权及 Triangle 致谢。学校非商业不等于免除条款。
- **数据：** 闭合外环和孔洞、明确单位；几何截面性质不需要拉伸长度。
- **最小验收：** 有孔矩形面积/形心/I 与解析值一致，换单位一致，孔洞保留。
- **限制：** 只计算已确认离散轮廓的几何性质；不相连区域分别处理。无扭转、翘曲、强度或规范验算。
- **维护证据：** v3.10.2 2026-01-24；提交 2026-04-16
- **来源：** [官方仓库](https://github.com/robbievanleeuwen/section-properties)；[文档](https://sectionproperties.readthedocs.io/en/stable/)；[许可来源](https://github.com/robbievanleeuwen/section-properties/blob/master/LICENSE)；[补充来源](https://github.com/m-clare/cytriangle/blob/main/src/c/triangle.c)。

### N02 Pynite

- **优先级/状态：** P0 首批。已接入；离线/HTTP/浏览器改参、撤销、保存重开通过；真实项目待验收。
- **功能：** 简支梁与框架分析。从表单定义梁、支座、荷载、E/I，输出反力、内力及挠度曲线。
- **岗位与接入：** structure。本地固定 JSON 参数适配器，Pynite 一阶弹性梁/框架，输入输出用 SI 单位。
- **许可与署名：** MIT；直接数值/绘图库依赖另列第三方清单。 保留 LICENSE、版权及修改说明。版本与输入写入报告。
- **数据：** 跨度、边界、材料 E、截面 I、荷载值及单位均由用户确认。
- **最小验收：** 均布简支梁反力 qL/2、Mmax=qL²/8、挠度 5qL⁴/(384EI) 与解析一致。
- **限制：** 力学分析不是完整规范验算或施工签认；不自动编荷载和截面。
- **维护证据：** v3.2.0 2026-09-13；提交 2026-09-14
- **来源：** [官方仓库](https://github.com/JWock82/Pynite)；[文档](https://pynite.readthedocs.io/en/latest/)；[许可来源](https://github.com/JWock82/Pynite/blob/main/LICENSE)；[补充来源](https://github.com/JWock82/Pynite/blob/main/pyproject.toml)。

### N03 Frappe Gantt

- **优先级/状态：** P0 首批。已接入；拖动/撤销/重开通过；新排程页390px通过；真实计划待验。
- **功能：** 日期甘特与排程结果展示。保留日期/进度拖动与撤销；新排程页展示自研CPM和资源方案。
- **岗位与接入：** plan-master / plan-lookahead。本地1.2.2控件；日期页可编辑，排程页只读图配参数编辑。
- **许可与署名：** MIT；保留版权与许可。
- **数据：** 日期页录入明确日期；排程页录入工期与日历，不编造缺失参数。
- **最小验收：** 旧日期手势可一次撤销；新页CPM8天/资源11天、保存重启恢复。
- **限制：** 控件负责显示与日期编辑；CPM/资源求解来自本项目内核及OR-Tools。
- **维护证据：** 本轮采用 npm 1.2.2；官方 tarball 完整性及许可见 vendor/SOURCE.json。早期 GitHub release 记录 v1.0.3。
- **来源：** [官方仓库](https://github.com/frappe/gantt)；[文档](https://docs.frappe.io/gantt/config)；[许可](https://github.com/frappe/gantt/blob/master/license.txt)；[版本包](https://registry.npmjs.org/frappe-gantt/1.2.2)。

### N04 IfcTester / IfcDiff

- **优先级/状态：** P0 首批。已接入；离线/HTTP/浏览器检查、差异、保存通过；真实项目待验收。
- **功能：** IFC 信息检查与版本差异。复用已有 IfcOpenShell 生态，检查属性/分类/材料要求并比较两版 IFC。
- **岗位与接入：** bim-coord / bim-deliver。Python 库或受限 CLI，报告 JSON 与构件 GlobalId 对应。
- **许可与署名：** IfcOpenShell/IfcTester/IfcDiff 0.8.5 LGPLv3+；bcf-client 0.8.5 安装元数据 GPLv3、源头 LGPLv3+，差异待澄清。 锁定版本并审计依赖，保留 LGPL 文本、源代码可获得性及适用修改记录。
- **数据：** 有语义 IFC；检查规则 IDS；比较需两版模型且尽量保持 GlobalId。
- **最小验收：** 缺一个必需属性能定位构件；新增/删除/改属性各一件能准确列出。
- **限制：** IDS 不做几何碰撞或完整规范审查；Diff 按 GUID，统计含空间并排除 IfcFeatureElement（如开孔），要求相同 schema。
- **维护证据：** 官方 0.8.5 文档；目录提交 2026-09-21
- **来源：** [官方仓库](https://github.com/IfcOpenShell/IfcOpenShell)；[文档](https://docs.ifcopenshell.org/ifctester.html)、[文档2](https://docs.ifcopenshell.org/ifcdiff.html)；[许可来源](https://github.com/IfcOpenShell/IfcOpenShell/blob/v0.8.0/src/ifcdiff/COPYING.LESSER)；[补充来源](https://github.com/IfcOpenShell/IfcOpenShell/blob/v0.8.0/src/ifctester/pyproject.toml)。

### N05 BCF / bcf-client

- **优先级/状态：** P1 后续。库作为依赖已安装；BCF 问题交换界面与流程未集成。
- **功能：** 问题闭环与项目交接。质检问题关联模型构件、视点、评论和责任人，交换 BCF 文件。
- **岗位与接入：** bim-coord / design-coord。现有 IfcOpenShell 生态。BCF-XML 2.1/3.0 文件交换，后续接 API 3.0。
- **许可与署名：** 实际安装 bcf-client 0.8.5 的 METADATA 为 GPLv3、bcf/__init__.py 为 LGPLv3+；声明差异待上游澄清。 锁定版本复核，不直接把整个包定为 GPL 或 LGPL。按最终确认许可提供相应材料。
- **数据：** 模型 GlobalId、问题说明、视点与责任人；离线交接无需新服务器。
- **最小验收：** 导出后重新导入，问题 ID、构件引用、状态、视点与评论一致。
- **限制：** 不是完整 CDE/多用户后台；账号权限、冲突和状态流需工作台实现。
- **维护证据：** 目录提交 2026-09-21
- **来源：** [官方仓库](https://github.com/IfcOpenShell/IfcOpenShell/tree/v0.8.0/src/bcf)；[文档](https://docs.ifcopenshell.org/bcf.html)；[许可来源](https://github.com/IfcOpenShell/IfcOpenShell/blob/v0.8.0/src/bcf/COPYING.LESSER)；[补充来源](https://github.com/IfcOpenShell/IfcOpenShell/blob/v0.8.0/src/bcf/pyproject.toml)。

### N06 PySlope

- **优先级/状态：** P1 后续。候选已核验，尚未集成。
- **功能：** 二维边坡工况比较。Bishop 圆弧滑面搜索，比较坡形、水位和坡顶荷载变化。
- **岗位与接入：** geotech。Python worker，明确搜索范围与输入 JSON。
- **许可与署名：** MIT；Django/Plotly 等依赖另审计。 保留主库与依赖许可，报告搜索设置和版本。
- **数据：** 坡形、土层、重度、黏聚力、摩擦角、水位与荷载。
- **最小验收：** 官方算例可复现，固定搜索参数；改变水位/荷载有可解释影响。
- **限制：** 二维圆弧/水平土层适用范围；dynamic 指移动荷载偏距，并非地震动力分析。
- **维护证据：** v1.4.0 / 最近提交 2025-10-18
- **来源：** [官方仓库](https://github.com/JesseBonanno/PySlope)；[文档](https://pyslope.readthedocs.io/en/latest/)；[许可来源](https://github.com/JesseBonanno/PySlope/blob/main/LICENSE.txt)；[补充来源](https://github.com/JesseBonanno/PySlope)。

### N07 concreteproperties

- **优先级/状态：** P2 后续。候选已核验，尚未集成。
- **功能：** 混凝土截面研究。配筋截面开裂刚度、弯矩曲率与 N-M 相互作用分析。
- **岗位与接入：** structure。隔离 Python 3.12+ worker，输入材料与配筋，不直接升级主环境。
- **许可与署名：** MIT；sectionproperties/cytriangle/Triangle 依赖许可另算。 保留第三方文本及 Triangle 致谢，记录所用材料模型和规范范围。
- **数据：** 截面、配筋坐标/直径、钢筋与混凝土材料参数。
- **最小验收：** 复现官方配筋截面 N-M 点和材料参数，不自动生成配筋。
- **限制：** 官方规范实现为 AS3600:2018、NZS3101:2006，不能称完整 GB/EC 规范验算。
- **维护证据：** v0.8.0 2026-07-06；提交 2026-09-17
- **来源：** [官方仓库](https://github.com/robbievanleeuwen/concrete-properties)；[文档](https://concrete-properties.readthedocs.io/en/stable/)；[许可来源](https://github.com/robbievanleeuwen/concrete-properties/blob/master/LICENSE)；[补充来源](https://www.cs.cmu.edu/~quake/triangle.html)。

### N08 WNTR + EPANET

- **优先级/状态：** P1 后续。候选已核验，尚未集成。
- **功能：** 供水管网工况。读取 INP，展示节点压力、管段流量以及漏损/停泵工况。
- **岗位与接入：** plumbing / municipal。Python WNTR 调用 EPANET，引擎运行在独立任务中。
- **许可与署名：** WNTR BSD-3-Clause；EPANET 新增代码 MIT，原 EPA 代码另有公共领域声明。 分开保留 WNTR 与 EPANET 上游许可及版权，注明模型来源。
- **数据：** INP 管网、标高、管径、粗糙度、需求/水源与工况。
- **最小验收：** 官方 INP 基线结果一致，修改需求后压力变化可复算，单位转换正确。
- **限制：** 需完整拓扑和边界数据，图上线条不能自动变成可求解管网。
- **维护证据：** WNTR v1.5.0 2026-07-01
- **来源：** [官方仓库](https://github.com/USEPA/WNTR)；[文档](https://usepa.github.io/WNTR/getting_started.html)、[文档2](https://usepa.github.io/WNTR/model_io.html)；[许可来源](https://github.com/USEPA/WNTR/blob/main/LICENSE.md)；[补充来源](https://github.com/OpenWaterAnalytics/EPANET)。

### N09 PySWMM

- **优先级/状态：** P1 后续。候选已核验，尚未集成。
- **功能：** 排水与积水工况。载入 SWMM 模型，显示节点水位、管渠流量和溢流过程。
- **岗位与接入：** plumbing / hydraulic / municipal。Python PySWMM wrapper 调用 SWMM toolkit，受限 worker。
- **许可与署名：** PySWMM BSD-2-Clause；toolkit CC0；引擎新增代码 MIT，原 EPA 代码公共领域。 分别保留 wrapper/toolkit/引擎条款，注明降雨与模型数据来源。
- **数据：** SWMM INP、降雨序列、汇水分区、管网与出流边界。
- **最小验收：** 官方小流域工况重算流量/水位一致，错误 INP 可读报错，可取消任务。
- **限制：** 不是看到地图即可预测城市内涝；缺少降雨/边界不得填默认设计值。
- **维护证据：** PySWMM v2.1.0 2025-09-09
- **来源：** [官方仓库](https://github.com/pyswmm/pyswmm)；[文档](https://pyswmm.github.io/pyswmm/quickstart.html)；[许可来源](https://github.com/pyswmm/pyswmm/blob/master/LICENSE.txt)；[补充来源](https://github.com/pyswmm/swmm-python/blob/dev/swmm-toolkit/LICENSE.md)、[补充来源2](https://github.com/pyswmm/Stormwater-Management-Model/blob/develop/LICENSE)。

### N10 PDAL + Potree

- **优先级/状态：** P2 后续。候选已核验，尚未集成。
- **功能：** 测量点云预处理与查看。LAS/LAZ 筛选、裁剪和地面分类，浏览器显示点云与测量标注。
- **岗位与接入：** survey。PDAL JSON pipeline 独立进程预处理，Potree 前端查看。
- **许可与署名：** PDAL BSD-3-Clause；Potree BSD-2-Clause 文本；插件依赖另算。 保留双项目许可及第三方插件声明，数据采集和坐标系来源单独记录。
- **数据：** 有授权 LAS/LAZ、坐标参考系、单位、选区和目标精度。
- **最小验收：** 同一已知距离在源点云与浏览器一致；裁剪计数正确，大图可分块。
- **限制：** 点云地面提取不等于土方量。土方还需两期同坐标/高程基准、边界、网格与空洞误差，Potree 只负责查看。
- **维护证据：** PDAL v2.10.2 2026-06-12；Potree 具体采用版本另锁定。
- **来源：** [官方仓库](https://github.com/PDAL/PDAL)、[官方仓库2](https://github.com/potree/potree)；[文档](https://pdal.io/en/stable/pipeline.html)、[文档2](https://pdal.io/en/stable/stages/filters.smrf.html)、[文档3](https://potree.org/)；[许可来源](https://github.com/PDAL/PDAL/blob/master/LICENSE.txt)；[补充来源](https://github.com/potree/potree/blob/develop/LICENSE)。

### N11 Eclipse SUMO

- **优先级/状态：** P2 后续。候选已核验，尚未集成。
- **功能：** 交通组织方案对比。交通路网、需求与信号方案的仿真回放和指标比较。
- **岗位与接入：** traffic。SUMO 独立进程，通过 TraCI 受限控制并读取指标。
- **许可与署名：** EPL-2.0；存在 secondary GPL 条款，按选定分发组合核对。 保留许可证和版权，公开修改与分发义务按适用许可落实。
- **数据：** 路网、车流需求、车型、信号方案和仿真时段。
- **最小验收：** 固定随机种子复现实例，基线与改信号方案的旅行时间/排队可比较。
- **限制：** 需求校准决定结果含义，不把未校准仿真当交通审批结论。
- **维护证据：** SUMO 稳定版 1.27.1 2026-06-25
- **来源：** [官方仓库](https://github.com/eclipse-sumo/sumo)；[文档](https://sumo.dlr.de/docs/TraCI/Interfacing_TraCI_from_Python.html)、[文档2](https://sumo.dlr.de/docs/Simulation/Output/TripInfo.html)；[许可来源](https://github.com/eclipse-sumo/sumo/blob/main/LICENSE)；[补充来源](https://sumo.dlr.de/docs/Libraries_Licenses.html)。

### N12 Brightway / bw2calc

- **优先级/状态：** P2 后续。候选已核验，尚未集成。
- **功能：** 材料碳排方案比较。数量与材料数据映射后计算 LCA，显示构件/材料贡献。
- **岗位与接入：** cost / env / bim-qto。Python LCA(demand, method)，lci/lcia 在隔离计算任务执行。
- **许可与署名：** bw2calc BSD-3-Clause；LCA 数据库和方法数据单独授权。 保留软件许可。不能将 ecoinvent 等有授权限制的数据随仓库分发。
- **数据：** 工程量/质量、材料映射、功能单位、生命周期边界和有授权数据库。
- **最小验收：** 固定小案例复算一致，单位映射正确，报告数据/方法版本，缺因子明确报缺。
- **限制：** 开源计算软件不附赠可任意复制的排放数据库；不由模型编排放因子。
- **维护证据：** bw2calc 2.5.0 2026-05-16；提交 2026-08-05
- **来源：** [官方仓库](https://github.com/brightway-lca/brightway2-calc)；[文档](https://docs.brightway.dev/en/latest/content/api/bw2calc/lca/index.html)；[许可来源](https://github.com/brightway-lca/brightway2-calc/blob/main/LICENSE)；[补充来源](https://ecoinvent.org/licenses/)。

### N13 Speckle Viewer

- **优先级/状态：** P3 备选。历史候选，当前未重新核验版本。
- **功能：** 已有清单的查看器备选。模型查看与对象交互，不新增第二套同质查看页面。
- **岗位与接入：** bim-deliver。按已有 Viewer 方案选其一，属性和来源 ID 接统一界面。
- **许可与署名：** 历史记录为开源 Viewer，采用时复核具体组件 LICENSE。 保留实际选定组件版权/许可；服务器与 Viewer 不混为同一许可。
- **数据：** 可公开演示的模型与对象 ID。
- **最小验收：** 模型重开、点选 ID 和属性一致。
- **限制：** 仅查看不等于碰撞、计量或版本同步已经实现。
- **维护证据：** 保留原 Markdown 记录，维护状态待复核。
- **来源：** [官方仓库](https://github.com/specklesystems/speckle-server)；[文档](https://docs.speckle.systems/developers/viewer/introduction)；[许可来源](https://github.com/specklesystems/speckle-server)。

### N14 TUM Open Infra Platform

- **优先级/状态：** P3 暂缓。历史候选，当前未重新核验版本。
- **功能：** 基础设施模型研究备选。研究参考，现阶段不替换已有 CAD/BIM 主链。
- **岗位与接入：** municipal / bim-deliver。先核验可编程接口与许可证，再决定独立工具接入。
- **许可与署名：** 原记录为研究查看器，具体代码许可待复核。 未核清许可前不复制源代码进工作台。
- **数据：** 适配的数据模型与可运行接口样例。
- **最小验收：** 能自动导入并返回可核验对象/几何才进入实施。
- **限制：** 原检索未发现可直接用的 QTO/碰撞 API，不等于断言项目没有该能力。
- **维护证据：** 保留原 Markdown 记录，维护状态待复核。
- **来源：** [官方仓库](https://www.cee.ed.tum.de/en/ccbe/research/research-fields/building-information-modeling-in-infrastructure/tum-open-infra-platform/)；[文档](https://www.cee.ed.tum.de/en/ccbe/research/research-fields/building-information-modeling-in-infrastructure/tum-open-infra-platform/)。

### N15 Civil Buddy CPM / 基线 / 周 PPC / 受限对话与项目包

- **优先级/状态：** P0 深化。已接入；上一轮核心15项、HTTP14项及浏览器真重启恢复资源11天。本轮受限Agent19/19、对话HTTP6/6、UI25/24和浏览器合成改参/撤销通过；全项目检查 113/113 通过，项目包旧历史兼容已通过，包专项 11/11。
- **功能：** 工作日历、WBS、四类依赖、正负时距、关键任务、基线与周承诺；新增受限对话提出原值→新值建议、确认计算、撤销请求，以及含历史和已保存原件的完整项目包。
- **岗位与接入：** plan-master / plan-lookahead。本项目 Python 内核和版本存储；Frappe 展示计算结果。排程页确定性对话和统一 Agent 共用受限工具；模型不直接保存或导出。
- **许可与署名：** 本项目 MIT；前端及求解器依赖分别署名。 说明自研范围，保留明确输入、结果版本与测试证据。
- **数据：** 明确工期、工作日历、依赖、资源及完成事实；不从名称或日期差猜工期。本地 PDF 候选仅完成日期检查，缺工期/日历/依赖/资源，不能作为真实 CPM 验收。
- **最小验收：** 合成 CPM 8天；基线8天、资源11天、PPC50%；重开一致。
- **限制：** 统一日历、整工作日；实际日期仅记录；不是完整 MSP/P6 引擎。建议需明确确认，旧参数/方法/版本冲突拒绝；包导入创建新副本并重置签认，资源约束核验不重新证明最优性。
- **维护/验证证据：** 上一轮离线、HTTP及浏览器记录与本轮新增专项分开列示，见文末。
- **来源：** [官方仓库](https://github.com/LUOaini1213/civil-buddy-sme)；[本项目排程说明](planning-workbench.md)；[许可](../../LICENSE)。本轮变更在工作分支，公开默认分支可能尚未同步。

### N16 OR-Tools CP-SAT

- **优先级/状态：** P0 深化。已接入；优化9项；单班组11天、双班组8天通过。
- **功能：** 资源容量约束排程。明确班组/设备容量下求可行或最优日期，预览后应用，可撤销。
- **岗位与接入：** plan-resource / plan-master。固定 Python worker；区间与累计资源约束；不执行模型代码。
- **许可与署名：** Apache-2.0；传递依赖保留各自许可。 保留版本、LICENSE 与适用 NOTICE；区分自研适配和求解器。
- **数据：** 整数工期、四类依赖、资源容量及工序明确需求。
- **最小验收：** CPM8天→单班组11天→保存重启恢复；进度改动不静默降级。
- **限制：** 统一日历、不可中断、整单位资源；OPTIMAL才证明最优；不算资源关键线路。
- **维护/验证证据：** 已安装锁定 ortools 9.15.6755，实际求解通过。
- **来源：** [官方仓库](https://github.com/google/or-tools)；[文档](https://developers.google.com/optimization/scheduling/job_shop)；[许可](https://github.com/google/or-tools/blob/v9.15/LICENSE); [补充来源](https://developers.google.com/optimization/cp/cp_solver)。

### N17 MPXJ

- **优先级/状态：** P0 深化。已接入；公开MPP16任务网页上传/保存刷新通过；P6合成往返通过。
- **功能：** MPP / P6 计划文件读取。MPP、XER、PMXML 转受限 MSPDI；展示原日期与未映射报告，确认后应用。
- **岗位与接入：** plan-master / 文件导入。MPXJ 16.7.0 + JPype1 1.7.1 + 便携 JRE21，固定子进程。
- **许可与署名：** LGPL-2.1；JPype、JRE 与捆绑 Java 库另按各自条款。 保留准确版本许可、来源与摘要；分发时核对 LGPL 和捆绑库要求。
- **数据：** 单项目文件，明确日历/工期。新导入保存时保留授权原文件字节；旧记录缺原件时明确列出，上传与记录 SHA256 一致的原件后可补齐。
- **最小验收：** 二进制MPP读入16任务/16原日期；XER/PMXML实际读取。
- **限制：** 不写原生MPP/P6；不等于源排程引擎；部分资源占用明确拒绝。
- **维护/验证证据：** 锁定 mpxj16.7.0；官方MPP固定提交文件与SHA256已核验。
- **来源：** [官方仓库](https://github.com/joniles/mpxj)；[文档](https://www.mpxj.org/)；[许可](https://github.com/joniles/mpxj/blob/v16.7.0/LICENSE); [补充来源](https://github.com/joniles/mpxj/blob/c5e1320cde0acd404031495aa66c17639a57239b/junit/data/generated/task-links/task-links-project2000-mpp9.mpp)。

### N18 NetworkX

- **优先级/状态：** P0 深化。已接入；路线8项；桌面浏览器150m→封路180m→不可达。
- **功能：** 场内最短路线。编辑明确道路权重、方向和封路状态，显示最短路线及分段来源。
- **岗位与接入：** dispatch / municipal / routes。Python MultiDiGraph + Dijkstra，独立道路页面。
- **许可与署名：** BSD-3-Clause。 保留版权、许可与版本；道路数据授权单独确认。
- **数据：** 道路节点和每段明确距离(m)或固定通行时间(min)、数值来源。
- **最小验收：** 单向/封路/不可达；平行边来源和单位核对。
- **限制：** 道路最短路不是施工CPM；不猜车速，不做车辆调度；未接独立项目保存。
- **维护/验证证据：** 已安装锁定 networkx3.6.1；实际算法与页面验证通过。
- **来源：** [官方仓库](https://github.com/networkx/networkx)；[文档](https://networkx.org/documentation/stable/reference/algorithms/shortest_paths.html)；[许可](https://github.com/networkx/networkx/blob/networkx-3.6.1/LICENSE.txt); [补充来源](https://networkx.org/documentation/stable/reference/classes/multidigraph.html)。

### N19 OpenProject

- **优先级/状态：** P2 后续。后续候选，未安装、未接入、未验收。
- **功能：** 项目协作平台候选。候选方向：多人工作包、责任人与项目协作；先做独立接口验证。
- **岗位与接入：** plan-master / 协作候选。尚未接入；后续评估官方API与部署，不嵌入整套平台。
- **许可与署名：** 官方主仓 LICENSE 为 GPLv3；选版及扩展功能另核。 采用前锁定版本及实际部署/复制范围，保留源码与许可证义务。
- **数据：** 测试项目、用户/权限边界、实际协作需求。
- **最小验收：** 采用前验证工作包读取/更新、权限、冲突与审计。
- **限制：** 不将平台宣传等同当前工作台能力，不声称 Project/P6 无损互通。
- **维护/验证证据：** 本轮只核官方仓库/许可入口；版本、维护及API验证待补。
- **来源：** [官方仓库](https://github.com/opf/openproject)；[文档](https://www.openproject.org/docs/api/)；[许可](https://github.com/opf/openproject/blob/dev/LICENSE); [补充来源](https://www.openproject.org/docs/)。

### N20 GanttProject

- **优先级/状态：** P3 备选。后续候选，未安装、未接入、未验收。
- **功能：** 桌面计划与互通候选。候选方向：桌面甘特流程参考与文件交换对照。
- **岗位与接入：** plan-master / 互通候选。尚未接入；先核文件往返与可调用接口，不复制整套桌面应用。
- **许可与署名：** 官方主仓 LICENSE 为 GPLv3；分发组件逐项核对。 采用前锁定版本及复制范围，保留许可、版权和对应源码要求。
- **数据：** 授权计划文件、明确工期/日历/资源，源软件对照结果。
- **最小验收：** 采用前验证实际日期、依赖、WBS和资源往返差异。
- **限制：** 不能据README声称已兼容全部MSP/P6；当前主线仍为自研受限排程。
- **维护/验证证据：** 本轮只核官方仓库/许可入口；具体版本能力待验证。
- **来源：** [官方仓库](https://github.com/bardsoftware/ganttproject)；[文档](https://www.ganttproject.biz/)；[许可](https://github.com/bardsoftware/ganttproject/blob/master/LICENSE)。

## 历史记录与后续补充

原许可证判断、命令、星标与数据逐格保留。新评估取代旧“AGPL 传染”“T040 专有写盘”或“一次只接一个 MCP”等口径。未经复核的后续补充不作为当前能力承诺。后续写入批次标注 2026-09-22（晚于本次核验日），日期及许可信息保留待核。

### 建议接入与当前复核

| 优先级 | 项目 | 许可 | 地址 | 接到哪一岗 | 怎么接 | 本仓纪律 | 备注 | 当前优先级 | 2026-09-21 状态 | 当前范围与修正 | 采用前验证 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | IfcOpenShell ifcclash + IfcMCP | LGPL-3.0-or-later | https://github.com/IfcOpenShell/IfcOpenShell | bim-coord / bim-qto | pip install ifcmcp[mcp]；stdio MCP：碰撞、QTO、查询 | 不假装 IFC 全量、不编单价 | 官方 src/ifcmcp；pip install ifcopenshell-mcp | P1 生态基础 | 历史记录，未据此认定已集成 | IfcTester/IfcDiff 为同一生态深化，见“核验候选”首批。 | 真实输入 → 工具成功 → 来源 ID/报告 → 保存重开；依赖及授权同时复核。 |
| 2 | OpenTakeoff | Apache-2.0 | https://github.com/Kentucky-ai/opentakeoff | 图纸量测 | npx -y opentakeoff-mcp（53 工具） | 扫描 PDF 默认仍拒绝 | 几何引擎开源；训练模型专有 | P3 历史备选 | 历史记录，未据此认定已集成 | 保留旧描述；星标、工具数、安装命令与许可需锁定具体版本重新核验。 | 真实输入 → 工具成功 → 来源 ID/报告 → 保存重开；依赖及授权同时复核。 |
| 3 | conmcp | MIT | https://github.com/ContractorKeith/conmcp | 图纸读图 / 算量草稿 | stdio MCP：分图、渲染、抽表、确定性 LF/SF/EA | 服务端不调 LLM；数量仍可 UNSPECIFIED | 商业施工 PDF；vision 给客户端模型 | P3 历史备选 | 历史记录，未据此认定已集成 | 保留旧描述；星标、工具数、安装命令与许可需锁定具体版本重新核验。 | 真实输入 → 工具成功 → 来源 ID/报告 → 保存重开；依赖及授权同时复核。 |
| 4 | smartaec/ifcMCP | Apache-2.0 | https://github.com/smartaec/ifcMCP | bim-qto 查询 | FastMCP + IfcOpenShell：实体/属性/空间 | 只查询不编单价 | 约 34 star；HTTP MCP | P3 历史备选 | 历史记录，未据此认定已集成 | 保留旧描述；星标、工具数、安装命令与许可需锁定具体版本重新核验。 | 真实输入 → 工具成功 → 来源 ID/报告 → 保存重开；依赖及授权同时复核。 |
| 5 | ekkodale/IFC-MCP | MIT | https://github.com/ekkodale/IFC-MCP | bim-coord 查询 | IFC 实体类型/聚合分析 MCP | 无模型则停 | 10 star，偏 FM 查询 | P3 历史备选 | 历史记录，未据此认定已集成 | 保留旧描述；星标、工具数、安装命令与许可需锁定具体版本重新核验。 | 真实输入 → 工具成功 → 来源 ID/报告 → 保存重开；依赖及授权同时复核。 |
| 6 | lean-planning-mcp | 见该仓 README | https://github.com/jeffersonbim/lean-planning-mcp | plan-master / plan-lookahead | 只读 MSP/P6/Synchro 进度：关键线路、PPC | 只读，不改原进度文件 | AWP/LPS 层；.xml 无需 JVM | P3 历史备选 | 历史记录，未据此认定已集成 | 保留旧描述；星标、工具数、安装命令与许可需锁定具体版本重新核验。 | 真实输入 → 工具成功 → 来源 ID/报告 → 保存重开；依赖及授权同时复核。 |
| 7 | pyGAEB | MIT | https://github.com/frameIQ/pygaeb | bid-parse / cost | GAEB XML → 本仓矩阵 | 不并易标 AGPL | 欧洲清单族 | P3 历史备选 | 历史记录，未据此认定已集成 | 原“不并易标 AGPL”是历史表述，当前不按许可证标签一概排除。 | 真实输入 → 工具成功 → 来源 ID/报告 → 保存重开；依赖及授权同时复核。 |
| 8 | That Open Fragments | MIT | https://docs.thatopen.com/Tutorials/Fragments/ | bim-deliver 查看 | 嵌 Three.js 查看器 | 查看器不是计量引擎 |  | P3 历史备选 | 历史记录，未据此认定已集成 | 保留旧描述；星标、工具数、安装命令与许可需锁定具体版本重新核验。 | 真实输入 → 工具成功 → 来源 ID/报告 → 保存重开；依赖及授权同时复核。 |
| 9 | bim-ootb | MIT | https://github.com/red1oon/bim-ootb | BIM 查看/规则碰撞 | 浏览器 IFC 查看 + 规则碰撞 | 不当第二套 ERP | web-ifc；ERP 内核另说 | P3 历史备选 | 历史记录，未据此认定已集成 | 保留旧描述；星标、工具数、安装命令与许可需锁定具体版本重新核验。 | 真实输入 → 工具成功 → 来源 ID/报告 → 保存重开；依赖及授权同时复核。 |
| 10 | civ-core | Apache-2.0 | https://github.com/ZGQ2001/civ-core | 试验/检测旁路 | Tauri + MCP 当工具 | 不替换 66 岗 |  | P3 历史备选 | 历史记录，未据此认定已集成 | 保留旧描述；星标、工具数、安装命令与许可需锁定具体版本重新核验。 | 真实输入 → 工具成功 → 来源 ID/报告 → 保存重开；依赖及授权同时复核。 |
| 11 | EngineerCMS | Apache-2.0 | https://github.com/3xxx/engineercms | supervision 资料 | 本地 Go /v1 HTTP | 不并后台 UI |  | P3 历史备选 | 历史记录，未据此认定已集成 | 保留旧描述；星标、工具数、安装命令与许可需锁定具体版本重新核验。 | 真实输入 → 工具成功 → 来源 ID/报告 → 保存重开；依赖及授权同时复核。 |
| 12 | opensource-construction/osc-directory | MIT | https://github.com/opensource-construction/osc-directory | 目录/再搜 | AEC 开源目录，当索引不当引擎 | 只当书签 | org 24 仓 | P3 历史备选 | 历史记录，未据此认定已集成 | 保留旧描述；星标、工具数、安装命令与许可需锁定具体版本重新核验。 | 真实输入 → 工具成功 → 来源 ID/报告 → 保存重开；依赖及授权同时复核。 |
| 13 | Frugal-Takeoff | 待核 LICENSE | https://github.com/phlurblepoot/Frugal-Takeoff | 图纸量测 / 现场检查照片 | 自托管 Web：PDF 量测、报价 PDF、现场三阶段照片清单 | 扫描 PDF 默认仍拒绝；不编单价 | GitHub加深补充 | P3 历史备选 | 2026-09-22 加深命中，未集成 | 自托管无 SaaS；许可文件研究未打开 | 真实输入 → 工具成功 → 来源 ID；先打开 LICENSE |
| 14 | tender-writer-v4 | MIT（v2 页述；v4 以仓内 LICENSE 为准） | https://github.com/Hugin-Z/tender-writer-v4 | bid-parse / bid-tech | 五阶段：解析→评分矩阵→提纲→分章→终审；正式投标仍人工 | 不十万字写标、不代交、submit_blocked | 旧仓 tender-writer 停更 | P3 历史备选 | 2026-09-22 加深命中，未集成 | 对照本仓易标五段，不 fork 进仓，可对照工序 | 核对 v4 LICENSE 与扫描 PDF 边界 |
| 15 | compas_ifc | 见 COMPAS 仓 | https://github.com/compas-dev/compas_ifc | bim-qto 语义查询 | Python 读 IFC2x3/IFC4/IFC4X3 空间树 | 不编单价、无模型则停 | ETH 研究前端模型 | P3 历史备选 | 2026-09-22 加深命中，未集成 | 简化 API，不是碰撞引擎 | 锁定许可与版本 |
| 16 | ifc-core-mcp | 待核 | https://github.com/shuji-bonji/ifc-core-mcp | bim-deliver 规范查阅 | IFC4.3 实体/属性/Pset 规范检索，不解析 .ifc 文件 | 不是模型算量 | 规范查阅 MCP | P3 历史备选 | 2026-09-22 加深命中，未集成 | 与 IfcMCP 文件操作互补 | 打开 LICENSE |
| 17 | LingYan-Bid-Agent | 待核 | https://github.com/joychin/LingYan-Bid-Agent | bid-tech Word 草稿 | 本机解析招标、按评分搭目录、带修订痕迹 Word；BYOK | 不代交、结论回溯原文 | topic:bid-writing | P3 历史备选 | 2026-09-22 加深命中，未集成 | 扫描 PDF 默认拒绝 | 核 LICENSE 与是否编业绩 |
| 18 | Awesome-Construction-Takeoff | 策展列表 | https://github.com/ishandutta2007/Awesome-Construction-Takeoff | 目录/再搜 | 算量/估造价开源索引，不当引擎 | 只当书签 | 2026-08 更新；链出 OpenTakeoff/BidWright | P3 书签 | 2026-09-22 加深命中 | 不是可执行工具 | 条目需逐条核许可 |

### 原暂缓记录与当前修正

| 项目 | 许可 | 地址 | 为什么不并 | 当前优先级（历史表名保留） | 当前复核（原判断留档） | 采用前要求 |
| --- | --- | --- | --- | --- | --- | --- |
| OpenBidKit / 易标 | AGPL-3.0 | https://github.com/FB208/OpenBidKit_Yibiao | 规划书已禁 fork | P3 暂缓 | 学校非商业不自动排除 AGPL。原“规划书已禁 fork”作为历史决定保留，当前暂缓是产品范围与合规成本选择。 | 按选定版本落实 AGPL 源码、声明及网络交互相关要求，比赛规则仍待确认。 |
| OpenConstructionERP | AGPL-3.0 | https://github.com/datadrivenconstruction/OpenConstructionERP | AGPL；192 模块也不该整仓并 | P3 暂缓 | 不以 AGPL 一概排除。完整 ERP 重叠大，首轮不并入全部业务模块。 | 明确复制/修改/分发/部署方式，按实际 AGPL 条款准备相应源码与许可。 |
| BidWright | AGPL-3.0 | https://github.com/braedonsaunders/bidwright | AGPL 估造价平台 官方仓已补：braedonsaunders/bidwright AGPL-3.0-only。 | P3 待复核 | 原项目链接和版本未核实，不沿用 AGPL 一票否决。 | 先找准确官方仓库与 LICENSE，再决定是否使用。 |
| ONLYOFFICE Community | AGPL v3 | https://github.com/ONLYOFFICE/DocumentServer | 不嵌 AGPL 套件 | P3 暂缓 | 不以 AGPL 一概排除。当前优先土木计算和模型闭环，文档服务部署后续。 | 具体版本的许可、署名/界面及商标要求需按官方条款复核。 |
| bimwright rvt-mcp / nwd-mcp | Apache-2.0 | https://github.com/bimwright | 要本机 Revit/Navisworks；不 COM 接管正在打开的窗口 | P3 条件候选 | 依赖有授权的本机 Revit/Navisworks，比赛机器若没有就不纳入主链。 | 开源连接器许可不包含 Autodesk 软件授权。 |
| ScanBIM MCP | MIT 但走 APS 云 | https://github.com/ScanBIM-Labs/scanbim-mcp | 依赖 Autodesk Platform Services，不是本机作业根 | P3 条件候选 | 依赖 APS 云及账户配置，离线比赛展示优先级较低。 | 核对 APS 服务和模型上传授权，不能把 MIT 当云服务许可。 |
| trades-mcp | MIT | https://github.com/Mahender22/trades-mcp | 美执照/BLS 工资；会编 hourly 数，本仓不编市场价 | P3 暂缓 | 面向美国公开职业/工资数据，不匹配当前首批工程计算。 | 如未来接入，保留数据地区、日期及来源，不把参考数冒充本地报价。 |
| ibuilder/massing | 混合（查看器 MIT，Blender GPL） | https://github.com/ibuilder/massing | 全生命周期平台过大；Bonsai/GPL 进程不要链进 MIT 仓 | P3 暂缓 | 混合许可按组件判断，不将 GPL/Blender 一概视为不可用。平台范围偏大。 | 明确模块边界和实际分发组合。独立进程并不自动消除所有许可义务。 |
| construction-hub | 未声明 | https://github.com/halunhaku/construction-hub | 无许可；Cloudflare | P3 待复核 | 未找到可用开源授权的历史记录仍需补证。 | 没有许可证不等于允许复制。先确认权利人授权。 |
| WorkDSH | MIT | https://github.com/techflag/workdsh | 通用壳 | P3 暂缓 | 通用壳与当前工作台重叠，保留参考，不增加第二套入口。 | 采用具体代码前核对所选提交许可。 |
| openbim-mcp | 见该仓 | https://github.com/helenkwok/openbim-mcp | 只有 frag 转换 | P3 暂缓 | 原记录偏 Fragments 转换，已有路线覆盖，先补实际能力而非同类外壳。 | 功能和许可证均按实际选定版本复核。 |
| BiaoShu-SKILL | 待核 | https://github.com/Get00/BiaoShu-SKILL | 14 步全自动写标；本仓禁止十万字写标/代交。GitHub加深补充。 | P3 暂缓 | 与 bid-tech 工序可对照，不整包并入 | 先核 LICENSE 与是否编造业绩 |
| tender-writer（停更） | MIT 页述 | https://github.com/Hugin-Z/tender-writer | 停更，指向 v4。不要 clone 旧仓。 | P3 书签 | 用 v4 对照工序 | 以 v4 LICENSE 为准 |

### GitHub 检索式

| 检索式（GitHub） | 用途 |
| --- | --- |
| mcp IFC clash in:readme license:mit archived:false | README 里自称 MCP+IFC |
| topic:bim topic:mcp is:public | topics 交叉 |
| org:IfcOpenShell | 官方工具链 ifcclash/ifcmcp/ifc5d |
| org:opensource-construction | AEC 开源目录 |
| org:bimwright | Revit/Navis MCP（要桌面软件） |
| npx opentakeoff-mcp in:readme | 已发布 MCP 包 |
| pygaeb GAEB license:mit | 清单 XML |
| topic:bid-writing archived:false | 中文/政府类写标 skill |
| tender-writer-v4 in:readme | Hugin-Z 五阶段写标 |
| conmcp takeoff mcp license:mit | 图纸 MCP |
| ifcMCP ifcopenshell language:python | IFC MCP 实现 |
| org:specklesystems | AEC 数据枢纽 Viewer/SDK |
| Frugal-Takeoff in:name | 自托管量测 Web |
| compas_ifc language:python | ETH IFC 简化 API |

### GitHub 加深命中（来源证据，不重复计数）

| 日期 | 项目 | 许可 | GitHub | 接到哪一岗 | 怎么接 | 相对本仓 | 来源检索 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-09-22 | IfcOpenShell / ifcmcp | LGPL-3.0-or-later | https://github.com/IfcOpenShell/IfcOpenShell | bim-coord / bim-qto | pip install ifcmcp[mcp] | 已在建议接入#1 | org:IfcOpenShell |
| 2026-09-22 | OpenTakeoff | Apache-2.0 | https://github.com/Kentucky-ai/opentakeoff | 图纸量测 | npx -y opentakeoff-mcp | 已在建议接入#2 | npx opentakeoff-mcp in:readme |
| 2026-09-22 | conmcp | MIT | https://github.com/ContractorKeith/conmcp | 图纸读图 | stdio 分图/渲染/抽表 | 已在建议接入#3 | topic:mcp takeoff |
| 2026-09-22 | smartaec/ifcMCP | Apache-2.0 | https://github.com/smartaec/ifcMCP | bim-qto 查询 | FastMCP HTTP | 已在建议接入#4 | ifcMCP ifcopenshell |
| 2026-09-22 | ekkodale/IFC-MCP | MIT | https://github.com/ekkodale/IFC-MCP | bim 查询 | 实体聚合 MCP | 已在建议接入#5 | topic:ifc mcp |
| 2026-09-22 | lean-planning-mcp | 见 README | https://github.com/jeffersonbim/lean-planning-mcp | plan-master | 只读 MSP/P6 | 已在建议接入#6 | MCP schedule primavera |
| 2026-09-22 | Frugal-Takeoff | 待核 LICENSE | https://github.com/phlurblepoot/Frugal-Takeoff | 量测 Web | 自托管 localhost:3000 | 本轮新补#13 | in:name takeoff self-hosted |
| 2026-09-22 | tender-writer-v4 | MIT（v2 页述） | https://github.com/Hugin-Z/tender-writer-v4 | bid-parse / bid-tech | 五阶段矩阵，人工终审 | 本轮新补#14 | topic:bid-writing |
| 2026-09-22 | compas_ifc | 见仓 | https://github.com/compas-dev/compas_ifc | bim-qto | Python 空间树 | 本轮新补#15 | language:python ifc |
| 2026-09-22 | ifc-core-mcp | 待核 | https://github.com/shuji-bonji/ifc-core-mcp | 规范查阅 | IFC4.3 实体定义，不读 .ifc | 本轮新补#16 | IFC4.3 MCP specification |
| 2026-09-22 | LingYan-Bid-Agent | 待核 | https://github.com/joychin/LingYan-Bid-Agent | bid-tech | 本机 Word 修订痕迹 | 本轮新补#17 | topic:bid-writing |
| 2026-09-22 | Awesome-Construction-Takeoff | 列表 | https://github.com/ishandutta2007/Awesome-Construction-Takeoff | 书签 | 策展索引 | 本轮新补#18 | topic:awesome takeoff |
| 2026-09-22 | osc-directory | MIT | https://github.com/opensource-construction/osc-directory | 书签 | AEC 开源目录 | 已在建议接入#12 | org:opensource-construction |
| 2026-09-22 | BidWright | AGPL-3.0-only | https://github.com/braedonsaunders/bidwright | 估造价平台 | 不并仓；官方仓已写入不要并仓 | 不要并仓已补官方 URL | Awesome-Construction-Takeoff 链出 |
| 2026-09-22 | bimwright rvt-mcp | Apache-2.0 | https://github.com/bimwright | 条件：本机 Revit | 不要 COM 接管打开窗口 | 不要并仓已有 | org:bimwright |

## 早期首批四项检查记录（历史）

- 专项通过：records 12、HTTP 8、frame 16、工程 UI 14、CAD UI 51、CAD Agent 8（真实 solver + 离线模型）、IFC/IDS 9。最终检查：npm run check 102/102；指定 http-demo/runtime-api/office-job/pipeline/shadow-eval/rust 六组深度检查 6/6 全通过。ZIP 843 文件清单与摘要核验通过，包内梁 solver 实算总反力 4000 N。浏览器梁 q 1→2 kN/m：总反力 4000→8000 N、Mz 2000→4000 Nm、dy 2.08333→4.16667 mm；未应用表单锁运行，撤销 q 回 1000 并重开正确。IFC Name 缺失 1 项含 GUID，diff 新增 1/改变 1，均保存。截面 A 3600 mm²、Ixx=Iyy 4920000 mm⁴、1 孔，真重启后重开一致；无长度仅 HTTP 验收，浏览器样例仍为合成默认 6 m。甘特拖动/撤销/保存刷新/390px 通过。真实项目待验收。
- 对当前 6 表定点更新，除已列明状态、许可核验、数量范围和说明外，其余单元格值及公式保持一致。后续追加内容完整保留。
- 首批 4 项、主清单 45 条经公式与独立计数核对；临时改变优先级，首批计数由 4 变 3 后恢复 4。
- 重算、公式错误扫描、受影响范围渲染、导出重读均已执行。导出缓存另经文件结构检查，真实项目验收仍待输入资料。


## 上一轮排程深化检查记录

- **上一轮排程验收：** 最终npm check 108/108；提交边界专项3/3；核心15、优化9、交换19、路线8、UI18、HTTP14通过。浏览器资源11天重启仍11，基线8天，PPC50%。390px仅排程页；路线桌面验收。这些是上一轮证据，不代表本轮新增功能全项目门禁已通过。
- **计划文件交接：** MPP公开合成文件16任务/16原日期，网页上传确认后保存刷新恢复。P6为合成XER/PMXML往返。JSON是计划交换文件，不含基线/周承诺/历史/原文件字节，非完整项目包。
- **实现与候选边界：** Frappe、自研CPM/基线周PPC、OR-Tools、MPXJ、NetworkX已接入。OpenProject/GanttProject仍为后续候选。资源仅整单位，1对应XML1.0；50%占用拒绝。真实 CPM 输入仍不完整，未完成真实计划验收。
- **部署与证据：** 详见 docs/civil-buddy/planning-workbench.md。MPXJ使用项目本地便携JRE，不装系统Java；保留SHA256与许可。源Project/P6软件重开、跨电脑迁移及真实施工验收仍待补。

## 本轮受限对话与项目包检查记录

- **已验专项：** 受限排程 Agent 19/19、对话 HTTP 6/6、排程 UI 离线测试 25/25。包括真实资源求解、脚本模型、用户明确值、未知编号/越权/篡改拒绝、仅建议不写入、确认应用、版本冲突及取消隔离，另验持久撤销不再提供取消按钮。本轮全项目检查 113/113 通过；项目包旧历史兼容已通过，包专项 11/11，最终晚取消与聊天回归另验 4/4。
- **本轮浏览器：** 已保存合成资源方案 11 天；B 工期 4→5 的建议不修改计划，确认应用后为 12 天，撤销编辑恢复 11 天。检查回复区分 CPM 8 天与保存资源方案 11 天。这不是用户真实项目验收。
- **对话与确认：** 排程页 `/conversation` 提供确定性命令；已保存计划可经 `planning_project_id` 接入统一 Agent。模型仅调用检查、解释、建议、撤销请求四个空参数工具，不能代填数值或签认。`/proposals/{id}/apply` 在用户明确确认后核对项目、版本、输入摘要与方法，成功后更新草稿；保存和导出仍使用现有权限及签认入口。撤销保存需单独确认并创建新修订。
- **取消与失败：** 对话及草稿计算可取消，晚到结果不会覆盖新状态；计算失败保留旧计划与结果。持久保存、撤销和项目包导入不提供提交后的取消按钮，避免把已提交结果显示为未发生。
- **完整项目交接：** `POST /projects/{id}/export` 导出当前计划、计算结果、基线、周承诺、保留历史、来源记录与已保存原件；`POST /projects/import` 创建新副本并重置签认，保留历史修订。旧记录缺原件时保留缺项说明，可经 `/projects/{id}/source` 上传摘要匹配原件补齐。普通 JSON/CSV/XLSX/XML 仍为计划交换文件，不含完整项目状态。
- **导入核验边界：** ZIP 固定文件白名单、摘要与尺寸检查；核对保存结果的日期、依赖、资源容量和历史/基线一致性，保留资源求解记录，不重新运行优化器或重新证明最优。跨电脑实机迁移仍待验收。
- **真实计划证据：** 本地 PDF 候选完成日期检查，缺工期、日历、依赖、资源，不能作为真实 CPM 验收；不记录私人路径、项目名称、计划日期或源文件内容。本轮未修改 Excel。
