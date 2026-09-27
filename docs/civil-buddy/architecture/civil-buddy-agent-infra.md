# Civil Buddy：Agent 基础设施设计

> 后续实现记录：本文保留设计时的源码盘点与历史验证。2026-09-21 的实际实现、测试证据及未覆盖能力见 [implementation.md](implementation.md)；文末“尚未实现”描述的是设计快照。

日期：2026-09-21；对应总架构 v0.5。源码基线为 PR58 `3e93025ee3794f96028ea8ce2067184a6af28a25`，前端模块化补充参考 PR56 `f32869349bf19a39d467c3f5ba46019eb53fe51a`。本文中“现有”表示已核对源码；“目标”表示待实现或统一的设计。未执行真实 DeepSeek、Jev、麦克风或语音模型验收。

## 1. 应提升为一级模块的能力

| 基础设施 | 现有底座 | Rust 迁移目标 |
|---|---|---|
| Sandbox 与执行 | 应用权限/路径检查；Linux、Windows OS worker；固定工程 worker | 统一授权与执行接口，准确报告平台能力，宿主启动受限工具进程 |
| 上下文 | Web 完整请求预算、裁剪、组成报告；Rust 较简化的历史压缩 | 每次主/子代理模型调用走同一个 ContextService 和最终预算检查 |
| 记忆 | 带原文出处的提取记忆、可选模型摘要、重建 API | 原始记录、用户陈述、工具结果和模型意见分层；摘要可失效/重建 |
| 任务与进度 | SSE、断线续流、后台任务、阶段时间线、协作事件 | 持久事件驱动任务树、工具进度、上下文占用和恢复；状态可重放 |
| RAG | 公共 KB FTS5/BM25、会话附件和历史检索；Rust 公共 KB 检索 | 增加项目资料作用域、版本化证据与精确定位，统一接口 |
| 语音输入 | 本地 faster-whisper、录音界面、术语表、浏览器识别降级 | 复用 ASR worker 与 UI，Rust 承接可用性、上传、取消和草稿回填 |
| 工具与模型适配 | schema、策略、取消、调用次数和部分总预算 | 统一调用记录、重试/幂等、实际用量、超时和错误分类 |
| 文档服务与共享技能 | 文件文字抽取、Word/Excel 新建、特定模板及草稿 sheet 更新 | PDF/Excel/Word 结构化读改、版本补丁、差异、公式重算和渲染验收；详见文档技能设计 |
| Skills 与子代理 | 66 岗位按需加载；部分领域协作与子任务预算 | 技能是 SOP，子代理是受控执行实例；共享预算，限定资料与工具 |

这些模块服务聊天与工程页面。Jev 使用相同证据、模型请求预算和审计体系；语音只是输入渠道；RAG 只提供资料；sandbox 只约束执行。不能把它们都隐藏在一个越来越大的 agent_loop 中。

## 2. 上下文、记忆和压缩

### 现有实现及缺口

[demo/context.py:187](https://github.com/LUOaini1213/civil-buddy/blob/3e93025ee3794f96028ea8ce2067184a6af28a25/demo/context.py#L187) 已有完整请求校验，计入消息元数据、工具 schema、系统提示、记忆、检索片段、历史与当前工具交互，并为回复留预算。`prepare_request` 保留当前用户消息及其后工具交互，按完整片段和连续历史后缀缩减可选内容，报告未纳入的来源。估算采用已在本地的 tokenizer 加裕量或 UTF-8 字节保守估算，不是 DeepSeek 官方计费精确值。

[task_memory.py:138](https://github.com/LUOaini1213/civil-buddy/blob/3e93025ee3794f96028ea8ce2067184a6af28a25/demo/task_memory.py#L138) 已有来源位置、版本替代和 `user_stated / assistant_claimed / tool_reported` 区分。用户陈述仍不自动成为已验证事实。`semantic_memory.py` 的可选模型摘要要求精确原文引用和数字支持，保留 `verified:false`；历史更正、来源变化会使缓存失效。`context_maintenance.py` 可以从原始对话/附件重建派生记忆与检索，不改原件，也不调用模型。

缺口是调用链不同：Web 问答使用上述预算，而 [runtime/model_loop.py:574](https://github.com/LUOaini1213/civil-buddy/blob/3e93025ee3794f96028ea8ce2067184a6af28a25/packing_assistant/runtime/model_loop.py#L574) 组装并追加消息，`model_client.py:109` 直接构造请求，未接同一全请求预算检查。步数上限和单次工具文本截取不能替代模型窗口检查。

Rust [context.rs:16](https://github.com/LUOaini1213/civil-buddy/blob/3e93025ee3794f96028ea8ce2067184a6af28a25/workbench/src/context.rs#L16) 目前默认窗口 1,000,000、回复预留 16,384，按中文字数/其他字符粗估，并把旧消息短摘录折入历史。Python 默认 32,768/4,096，两者行为不同。这里记录的是代码默认值，不是对任何模型真实窗口的确认；迁移不能把 Rust 现有模块当作 Python 完整上下文管理的等价实现。

### 目标 ContextService

每次模型调用，而非只在一轮开始时，执行以下流程：

1. 固定 workspace、session、task、模型配置版本、资料版本，以及当前用户要求和明确约束。
2. 加载所需技能与必要工具 schema；保留合法完整的 tool_call/result 消息组，避免单独裁掉调用或返回。
3. 读取有来源的任务记忆，按权限取得 RAG EvidencePack；子代理只获得自身需要的材料引用。
4. 为当前消息与在途工具结果预留不可随意裁剪的空间，再分配近期历史、记忆与检索片段。
5. 可选内容超限时整块减少；必要内容超限时返回 `context_overflow`，给出需缩小的输入，不能静默截断当前要求。
6. Provider Adapter 发请求前再校验最终序列化内容与输出预留，重写/重试/摘要调用也走相同检查。

拟议 `ContextSnapshot` 至少记录：`context_id、task_id、model_config_revision、source_revisions、messages_digest、input_estimate、output_reserve、effective_window、components、omitted_sources、summary_revision`。计费 usage 作为响应后的独立事实保存。模型窗口按实际供应商配置，不能通过调大本地数值假装模型支持。

整棵任务树的预算与每次调用的上下文窗口分开：前者限制累计调用、token 和时间；后者限制单次请求。子任务原子预占总预算，完成后按返回 usage 结算；供应商未返回 usage 时明确记为缺失或估算。

### 记忆分层

| 层次 | 权威来源 | 使用规则 |
|---|---|---|
| 原始材料与对话 | 原件、提交的用户消息、真实工具记录 | 保留不可变版本，是重建和引用的依据 |
| 工程事实与约束 | 用户明确陈述、确认绑定、确定性计算结果 | 各有 trust 与证据；新更正不抹掉旧来源 |
| 任务状态 | 宿主状态机、工具/子任务结果 | 包含已做/待做/阻塞/待确认；不能从模型文字推断完成 |
| 提取记忆 | 原文有界摘录与结构化字段 | 可重建；保留对象边界、来源位置、被替代关系 |
| 模型语义摘要 | 通过来源校验的模型输出 | 仍是未核验意见；不能授予权限或覆盖工具失败 |
| 岗位技能 | seed 与生成 SOP | 程序性知识，不是用户记忆或项目事实 |

历史确认句、旧 `p0_confirmed`、ASR 文本、检索资料中的授权声明均不能直接变成本次操作授权；审批服务验证当前作用域。压缩保留原始历史，允许用户查看本次实际送入模型的资料范围、被省略内容和摘要覆盖范围。

## 3. Sandbox、授权和执行后端

### 已有边界必须保留

[sandbox.py](https://github.com/LUOaini1213/civil-buddy/blob/3e93025ee3794f96028ea8ce2067184a6af28a25/packing_assistant/sandbox.py#L157) 与 `runtime/policy.py` 已做路径、敏感文件、spawn 白名单、工具归属、只读、失败/预算及写入策略检查。这些检查依赖调用路径经过策略层；不是操作系统隔离。应用层允许路径也不总是只限 `.civil-buddy/out`，应读取具体工具和当前配置。

[runtime/os_sandbox](https://github.com/LUOaini1213/civil-buddy/blob/3e93025ee3794f96028ea8ce2067184a6af28a25/packing_assistant/runtime/os_sandbox/__init__.py#L53) 已有独立 worker、自约束、自检及 app/os/auto 选择。明确指定 os 而无法启用时拒绝，auto 模式才报告原因并退回应用策略。

| 后端 | 当前可确认的机制 | 当前不能宣称的保证 |
|---|---|---|
| 应用内任务/线程 | schema、路径与工具策略；协作取消；延迟释放资源 | 原生库/任意代码均被内核限制、线程可强杀 |
| Linux OS worker | Landlock 写入规则；seccomp 网络/exec 限制及启动自检 | 完整读取隔离、已配置所有资源配额 |
| Windows OS worker | Low integrity、Job Object 单进程限制；写入自检 | 内核网络隔离、严格读取 allowlist、CPU/内存隔离；socket 替换不等价于内核隔离 |
| 工程 worker | 四种固定操作、输入输出大小上限、超时/取消后 kill+wait | 单凭独立进程就构成 OS 沙箱 |

当前 OS worker 启动继承 `os.environ`，不能声称已隔离模型凭据。目标改为最小环境变量集合；模型网络请求和密钥留在宿主，由宿主根据资料外发范围决定请求内容。

### 目标调用路径

`ToolRequest → 参数和作用域验证 → PolicyDecision → ExecutionProfile → Sandbox 自检 → 执行 → 结果检查/登记`

ToolSpec 增加 `read_scope、write_scope、network_requirement、spawn_requirement、resource_limits、cancel_mode、required_isolation`。这些是宿主定义的能力需求，不能由模型自行修改。

执行结果带 `enforcement_report`，分别描述 read/write/network/spawn/resources 的 enforced/app_checked/unsupported 状态及自检结果。不可只返回 `sandbox:true`。当工具确实要求当前平台无法保证的隔离时明确拒绝，不伪装成功。

PR58 截面工具在当前 OS worker 中拒绝嵌套启动工程子进程，这个限制是正确保留的。目标由 **Rust 宿主选择固定工程操作并直接启动受限工程 worker，在该 worker 自身施加对应隔离**；不让已禁止 spawn 的 worker 再启动第二层进程。各求解器要验证加载依赖、暂存写入、取消及子进程需求，完成前不宣称所有 sandbox 模式可用。

授权不等于执行成功，超时不等于已经终止。先标记 cancelling，收到协作停止或完成进程终止后再写 cancelled；保留此前成功产物，迟到结果不自动发布。取消与超时都记录实际执行后端和资源释放状态。

## 4. RAG 与工程证据

### 现有能力

公共知识由 `packing_assistant/kb_search.py` 维护 SQLite FTS5/BM25 候选检索，结合关键词、短语、文件名等评分；`demo/rag.py` 有岗位/部门/公司范围。会话侧 [local_retrieval.py](https://github.com/LUOaini1213/civil-buddy/blob/3e93025ee3794f96028ea8ce2067184a6af28a25/demo/local_retrieval.py#L23) 独立索引当前会话历史与附件，中文 unigram/bigram，900 字符块、160 重叠，支持指纹更新、删除与重建。语义摘要不是向量检索；本次未找到 embedding、向量库、RRF 或模型 reranker 的对应实现。

Rust `rag.rs` 已有公共 KB 的只读 FTS 和扫描回退，但尚未覆盖 Python 会话检索、完整来源 API 与语义记忆；Rust 附件链仍包含截取注入，不能等同完整项目 RAG。

当前有 PDF/DOCX/XLSX/文本抽取。检索引用主要定位到抽取文本的字符区间，PDF 页码、表格单元格、规范条款不是全部已保留。Python 上传路径对扫描 PDF 提示需 OCR；Rust parse.rs 另有 MinerU/Docling/Marker 外部 CLI 适配，未核实本机依赖或扫描件质量，不能称为开箱可用或完全没有 OCR 桥。同一 source_id 更新会替换文本，引用尚未包含不可变 source_revision/hash。

### 目标检索服务

`原件登记 → 解析/可选 OCR → 保留定位的分块 → 索引 → 作用域与适用性过滤 → 召回/排序 → EvidencePack`

四种资料域分别管理：公共岗位知识、工程项目资料、会话原文、摘要缓存。项目资料可在同一工程内被多个任务授权引用，不能默认跨工程或把所有会话历史都投入搜索。

拟议请求为 `RetrievalQuery { workspace_id, session_id, task_id, selected_source_ids, query, discipline, jurisdiction, as_of, allowed_versions, budget }`；先按权限/选中来源过滤，再检索排序。规范辖区、版本、有效日期涉及适用性，应按已确认元数据过滤；元数据缺失时标为待核对，不能因评分高就宣称适用。当前 tags 和部分辖区规则只是评分，迁移要明确区别。

统一引用为 `EvidenceRef { source_id, revision, content_hash, chunk_id, locator, quote, scope, trust, extractor_version }`。locator 按资料类型区分 PDF 页/位置、Word 段落/表格、Excel sheet/单元格、CAD entity/选集、IFC GUID/属性、对话 message/offset。无法得到精确定位时标明实际粒度，不伪造页码。

检索返回 query、使用的来源版本、检索方式、候选与最终片段、未覆盖资料和无结果状态。模型只能引用本次提供的 EvidenceRef；宿主核对引用与原版本，历史产物仍能回看当时依据。删除或换版使派生索引失效，保留或撤回原版本按项目规则处理。

首期复用 FTS/BM25，满足只配置 DeepSeek 就能运行。向量检索、混合融合和 reranker 是可选后续适配，依据实际中文工程查询集的召回与引用表现加入，不把额外 embedding 服务列为首期硬依赖。Jev 对给定证据做受约束决策，不充当检索引擎。

验收既测找到答案，也测找不到和找错：精确构件号、规范版本、单位、跨表格问题、当前所选附件、同名不同工程、来源换版、扫描件未解析、过期规范和矛盾证据。当前 `test_rag_parity.py` 对 Rust 与 Python 差异有信息输出，不能据此宣称已经严格等价；需补真实跨语言契约样本。

## 5. 上下文占用、任务进度和运行记录

界面区分以下三种状态：

| 显示 | 来自哪里 | 正确表达 |
|---|---|---|
| 上下文占用 | 单次请求 ContextSnapshot | 输入估算/可用输入窗口、输出预留、资料/历史/工具占比；说明估算方式 |
| 任务执行进度 | 宿主、工具和子代理事件 | 正在检索/计算/等待确认，完成了哪些子任务；未知时长不显示假百分比 |
| 整任务预算 | 全任务树预算账本 | 累计调用、实际/估算 token、重试、剩余额度与耗时 |

原有前端上下文条、任务记忆面板、SSE 阶段时间线与 PR56 session-watch/turn-stream 保留。UI 明示上下文数字属于哪一轮、哪个子任务、最新请求还是本地估算；不能用输入框字数充当即将发送的全部上下文，也不把上下文 70% 解释为任务完成 70%。

当前前端进入下一阶段时会把上一阶段视觉标为完成，这不证明上一阶段经过独立验收。迁移后以明确的 started/completed/failed 事件更新状态，界面阶段名不替代工具结果或验收记录。

在既有 `{seq,event,data}` 外形与每轮 seq 语义下，数据层统一携带 `workspace_id/session_id/turn_id/task_id/run_id/tool_call_id`，不同层 ID 可为空但不可混用。拟议事件包括：

```text
context.prepared / context.reduced / context.over_budget
retrieval.started / retrieval.completed / source.invalidated
task.started / task.waiting / task.completed / task.failed
tool.started / tool.progress / tool.completed / tool.failed
approval.required / approval.resolved
turn.cancelling / turn.cancelled / turn.completed / turn.interrupted
artifact.staged / artifact.published
```

新增事件先映射到现有前端认识的 context/status/collaboration/done/error，再逐步升级客户端。只有工具提供确定 total/current 时才显示有分母的进度；Agent 动态增加步骤时记录计划版本，不能靠固定动画推算完成率。用户看到的是操作与结果概要，不需要内部推理文本。

事件先可靠登记，再发送；重连按 turn_id+seq 续流，重复事件不重复执行 UI 副作用。重启后从事件和结果记录恢复展示，将未知的在途工作标为 interrupted；除幂等且明确授权的步骤外，不自动重放写入。先保存结果/终态，再释放租约。

Trace 将模型请求、工具、检索、sandbox 决策与产物关联起来。保存结构、摘要、来源版本、usage、时延和错误；对提示与结果正文采用最小必要记录和敏感信息处理，密钥不进入日志。资料受限时，trace 也受同一工作区权限约束。

## 6. 语音输入

现有 [demo/asr.py:31](https://github.com/LUOaini1213/civil-buddy/blob/3e93025ee3794f96028ea8ce2067184a6af28a25/demo/asr.py#L31) 与 voice.js 已提供语音入口，本地服务默认 faster-whisper small/CPU/int8，有 20 秒、8 MB 输入限制与领域术语 initial_prompt。这里“本地”指运行 Python 服务的机器，手机经局域网使用时音频会发送到该服务。浏览器 SpeechRecognition 是需明确同意的可选降级，可能使用浏览器厂商服务。Rust 当前健康能力声明仍为 `asr:false`，因此不能因前端有按钮就称 Rust 已支持语音。

现有接口是 `GET /api/asr/status`、`POST /api/asr/prepare`、`POST /api/asr`，保留其输入限制及 400/409/413/429/503 错误语义。当前取消“准备”等待不终止后台准备；停止录音会提交识别；识别超时只中止前端等待，后端没有完整识别取消 API。以下 cancel/隔离迟到回填属于新增目标，不能误列为已有能力。

目标链路：`用户开始录音 → 可见录音/停止状态 → ASR worker → 转写草稿 → 用户校对并提交 → 普通 TurnRequest`。原 ASR 和术语表先继续使用 Python，Rust 负责请求大小、类型、超时、取消、能力检查和记录关联；音频任务不进入 DeepSeek 或 Jev，除非将来另有明确产品配置。

ASR 返回至少保留 `transcript、engine、model、language、request_id、input_scope`，有可靠时间戳/置信信息才返回对应字段。主界面可检查构件编号、数字、单位和专有名词；术语纠正不能静默改写工程数值。没有模型包或运行依赖时显示未就绪，不能在一次录音请求中静默下载大模型或改用外部服务。

录音/识别期间切换会话、撤销输入或取消请求后，迟到转写不得进入新会话。语音来源和用户最终提交文字分别记录；原音频默认仅作临时处理，若未来要长期留存，需独立的保留设置。

转写不会自动点击发送，也不能把识别到的确认句直接兑换成高风险操作审批。用户校对后仍走与文字一致的明确提交和既有审批流程；历史语音中出现的确认不生效。

## 7. Rust 接口与接入顺序

首期仍是一个 Cargo package，不为每项服务起一个微服务。建议接口职责：

| 接口 | 接收 | 返回 |
|---|---|---|
| ContextService.prepare | 任务、模型配置、消息、工具、来源引用 | ContextSnapshot + 完整请求或明确超限 |
| MemoryRepository | 原始记录与来源版本 | 带 trust 的提取记忆/摘要、覆盖范围与失效原因 |
| RetrievalService.search | 有权限范围的查询 | EvidencePack，含引用、方法、遗漏和无结果状态 |
| PolicyService.authorize | 工具、参数摘要、工作区、用户授权 | 允许/需确认/拒绝及原因、作用域 |
| ExecutionService.run/cancel | 固定工具与 ExecutionProfile | 有 backend 和实际约束报告的事件/结果 |
| DocumentService.inspect/preview/apply/validate | 原件引用、版本前提、有界文档补丁 | 新版本、来源映射、差异、重算/渲染验证状态 |
| AsrService.transcribe/cancel | 显式录音请求与目标会话 | 待校对草稿或错误/取消 |
| EventStore.append/replay | 有 ID 和版本的事件 | 可续流的序号与投影 |

按闭环推进：

1. 固定现有上下文、RAG、语音、权限与事件 API 契约，集成 PR56/58；无需先换 UI。
2. Rust 先接 ContextService + RetrievalService + EventStore，把完整请求预算放到所有模型请求出口。
3. 接统一 Policy/Execution 与 sandbox 能力报告；先支持已验证的固定工程 worker，解决宿主启动与隔离组合。
4. 完成文字及语音草稿到同一任务入口、CAD 截面工具到真实 DeepSeek 的闭环。语音可选，不阻塞纯文字。
5. 接子代理任务树、共享总预算、上下文隔离与恢复；Jev 先 off，再 shadow。工程服务逐一纳入相同接口。

“只用 DeepSeek 跑通”意味着不强依赖 Jev、向量模型或云 ASR；仍保留本地检索、上下文预算、权限、进度和可选本地语音。离线模式不发 LLM 请求，能够检索资料和执行明确参数的工具，界面如实标明该模式。

## 8. 验收与本轮验证

必须覆盖：完整请求超过窗口前被拒绝；长工具结果与工具消息配对；更正使旧记忆失效；跨工程检索隔离；旧版本引用可回看；扫描件无文字不冒充已读取；取消后无迟到语音回填/产物发布；断线后事件不重复；明确 os 模式不可用时拒绝；sandbox 能力和实际平台一致；重试计入整树预算且不重复写入。

本轮在固定提交的独立快照中执行 `scripts.test_context_budget`、`scripts.test_task_memory`、`scripts.test_local_retrieval`，共 **54/54 通过**。dotenv 与模型凭证禁用，网络连接限定本机测试服务，活动工作区未修改。这证明既有预算、提取记忆和会话检索的该组离线测试通过；不证明 Rust 已移植这些能力，也不等于整个系统、真实模型、ASR 或 OS sandbox 验收通过。

语音与 OS sandbox 现有测试可作为下一轮实现的契约资产；本轮只核对其源码。总架构与工程服务复用见 [Rust 土木工作台架构](civil-buddy-rust-architecture.md)。

v0.5 的 [文档技能与 LLM/Jev 工程工作流](civil-buddy-document-skills.md) 是本基础设施的上层调用方：文件解析结果进入来源注册与 RAG，模型修改方案经过 Policy/Execution，生成版本回到 Artifact/EventStore。生成内容标为派生资料，不能自动当作新的原始工程事实再次检索。
