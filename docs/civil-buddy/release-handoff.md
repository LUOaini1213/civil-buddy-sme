# 统一工作台：启动、交接与验收

本轮整合现有 Rust Agent 主程序、Python 确定性业务工具、英文招标与箱单联动、条款引用、回复证据校验、当前轮签认和独立实例身份。发布包使用源码白名单，不包含原始业务资料、账号密钥、会话数据库或已经签认的状态。

新比赛使用 [civil-buddy-sme](https://github.com/LUOaini1213/civil-buddy-sme)。原 `civil-buddy` 仓库的 PR 和旧版安装包仅作为历史记录；本轮提交、构建与发布须以新仓库的提交号核对。

2026-09-27 的后续[审查修复](review-fixes-20260927.md)覆盖箱单单位与公式读取、文档完成回执、模型端点及损坏项目包。正式提交的 `v0.7.0` / `0c0e803` 保持冻结；旧发布包不会随源码修复自动改变。

## 启动个人工程

本机试用可继续使用 `scripts/start_unified_workbench.py`。需要身份和工程隔离时，每位同事使用独立且互不包含的工程目录、状态目录和启动端口；不能把同一个物理工程目录同时分给两个人。登录口令文件必须放在工程目录外，避免被选为资料。

下面命令在解压后的发布包根目录运行。先安装 README 列出的 Python 依赖；`C:\CivilJobs\demo` 必须是自己已有的工程目录。日常使用将工程和状态放在发布包外，升级程序时保留它们原来的绝对路径。

```powershell
# 仅首次生成个人口令；已有文件会拒绝覆盖，之后继续使用它。
& .venv/Scripts/python.exe -c "import secrets,pathlib; p=pathlib.Path('C:/CivilBuddySecrets/teammate-a/login-token.txt'); p.parent.mkdir(parents=True,exist_ok=True); p.open('x',encoding='utf-8').write(secrets.token_urlsafe(48))"
& .venv/Scripts/python.exe scripts/start_unified_workbench.py --binary bin/civil-workbench.exe --python .venv/Scripts/python.exe --state-root C:/CivilBuddyState/teammate-a/demo --user-id teammate-a --workspace C:/CivilJobs/demo --token-file C:/CivilBuddySecrets/teammate-a/login-token.txt --port 8765 --open
```

在 `/auth/login` 页面粘贴该文件中的口令。页面使用 HttpOnly、SameSite=Strict 会话 Cookie，退出或服务重启后重新登录。口令不放在 URL 中。后台工具服务使用另一个每次启动随机生成的内部口令，不接受匿名请求，也不接收模型提供商密钥。

一个实例只绑定一位用户和一个精确工程根目录。启动器在传入的状态基础目录下追加 `accounts/<user>/projects/<root-hash>/`，终端的 `State:` 显示这个实际实例目录；工程目录另有 `.civil-buddy/instance-owner.sqlite` 归属记录。再次启动仍传最初的 `--state-root` 基础目录，不能把 `State:` 的 `accounts/.../projects/...` 叶目录再次传入，否则会重复追加层级并触发归属拒绝。不能通过修改请求里的用户、工程路径或 session_id 切换归属。现有实现**不是同一进程多租户平台**。无身份配置的入口仅供本机使用。

工程和状态目录必须互不包含，也不能包含其他实例的归属目录。`identity.sqlite` 是保留的实例状态文件，不能作为工程资料放入工程根；归属检查扫描超过 100000 项时拒绝启动，应选择专用工程目录。该检查不是针对同一操作系统账号下恶意文件修改的隔离边界。

公网部署需自行配置 HTTPS 反向代理，并给每个实例独立入口；启动追加 `--public-origin https://你的域名`。不要把 Python 内部端口暴露公网。本文不代表云端部署已经完成。

## 资料和模型的处理规则

- 原始 DXF、招标文件和箱单保留原件。选择资料后，确定性工具产生计算记录；模型只能解释记录，不能改变柜型、状态、数值来源或批准提交。
- 提问、解释、否定执行和“不要写文件”不启动写入。Rust Agent 保存文档副本前，需由使用者明确选择岗位；“自动选择”仍可读取和预览，模型自行加载低风险岗位不能替代人的分类。选定或加载高风险岗位、调用结构或截面计算后，写入另需本轮确认框中的完整确认句，或整条消息仅为该确认句；粘贴资料中的单行或单句、加引号的引用、历史确认均不授权。后续加载低风险岗位不会清除已提高的风险级别。岗位选择不是对所有工程语义的自动核验，也不替代持证人员签认。
- Agent 执行历史保留操作人、工具事件、权限选择、真实 usage、错误及取消状态。刷新可续读事件；重启后的未完成任务标为 interrupted，不自动重放写入。
- 取消时先显示“正在停止”。已开始的同步文件操作会等待结束，后续步骤停止；确认结束前，同一会话不能再次执行或导出。不会把“已请求取消”当成“已停止”，也不宣称可以立即硬中断同步写入。
- 费用和上下文占用分别显示。缺少提供商用量时不能把估计值当作真实账单；资料引用校验也不等于工程结论正确。

## 四人交接建议

| 人员 | 负责核对 | 交付证据 |
|---|---|---|
| 队长罗文杰 | 版本、接口整合、最终演示 | 发布提交号、包哈希、问题清单 |
| 崔子轩 | CAD 和图纸尺寸 | 原图摘要、单位、孔洞、导出重开记录 |
| 牛东睿 | 工期与资源排程 | 依赖/工期来源、关键线路、改参和撤销记录 |
| 刘景轩 | 箱单与招标联动 | 页码/行号、逐包装字段、人工核对项、导出文件 |

此表是建议分工，不能作为已经完成工作的证明。

## 项目包交接与备份

CAD、排程、物流页面分别使用自身的项目导出/导入。导入创建新副本；签认需重新完成。记录源文件哈希、单位、参数、版本号、处理报告、软件提交和已知缺项。不要用不同内容的同名文件替换原图。

岗位会话包保留原附件、角色、交付物与业务会话，导入产生新会话并清除原签认。它不包含新 Rust Agent 页的执行数据库；Agent 执行历史由同一归属下的完整状态备份恢复，不能把岗位会话包称为整个工作台的完整备份。

完整状态备份用于自己同一归属下的恢复，必须同时保留**工程根的绝对路径、最初 `--state-root` 基础目录的绝对路径、`--user-id`**。工程内的归属数据库还绑定实际实例状态目录；只改程序目录可以，移动工程或状态、改用户名不能直接延续原实例。不同同事使用业务项目包交接，不复制身份 SQLite、登录口令、模型密钥或活动 Cookie。需改变工程或状态绝对路径时，建立新实例并导入支持导入的业务项目包；这不迁移 Rust Agent 执行历史，不直接改数据库绑定。

### 停机后备份自己的实例

先保存页面中的项目，等待任务结束或取消完成，再在启动窗口按 Ctrl+C，等启动器退出、两个服务停止。以下命令不会关闭正在运行的服务；只有停机后才可复制数据库。不要只复制单个 `.sqlite` 文件，要连同可能存在的 WAL 文件、工程中的 `.civil-buddy` 和新文档副本一起保存。

将下面四项改成自己的实际值。备份目录必须尚不存在，且位于工程和状态目录之外；建议使用独立的受控磁盘。示例状态基础目录仅供这一位用户的这一项工程使用。脚本只读取原目录，写入新的个人备份目录，不删除原件。

```powershell
$ErrorActionPreference = 'Stop'
$workspaceRoot = 'C:\CivilJobs\demo'
$stateBase = 'C:\CivilBuddyState\teammate-a\demo'
$instanceUser = 'teammate-a'
$backupRoot = 'E:\CivilBuddyBackups\demo-20260930-1800'
$workspaceRoot = (Resolve-Path -LiteralPath $workspaceRoot).ProviderPath.TrimEnd('\')
$stateBase = (Resolve-Path -LiteralPath $stateBase).ProviderPath.TrimEnd('\')
$backupRoot = [IO.Path]::GetFullPath($backupRoot).TrimEnd('\')
if (Test-Path -LiteralPath $backupRoot) { throw '备份目标已存在，请选择新的空目录名。' }
foreach ($source in @($workspaceRoot, $stateBase)) {
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "源目录不存在：$source" }
    if ($backupRoot.Equals($source, [StringComparison]::OrdinalIgnoreCase) -or
        $backupRoot.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw '备份目录不能位于工程或状态目录内。'
    }
}
New-Item -ItemType Directory -Path $backupRoot | Out-Null
Copy-Item -LiteralPath $workspaceRoot -Destination (Join-Path $backupRoot 'workspace') -Recurse -Force
Copy-Item -LiteralPath $stateBase -Destination (Join-Path $backupRoot 'state-base') -Recurse -Force
[ordered]@{
    user_id = $instanceUser
    workspace_root = $workspaceRoot
    state_base = $stateBase
    saved_at = (Get-Date).ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backupRoot 'restore-locations.json') -Encoding UTF8
```

只有两份目录复制和清单写入全部成功，才保留为完成的备份。若命令失败，保留原目录不动，下次选择另一新备份目录，不把半份备份当成可恢复证据。该备份含个人实例的归属数据库和历史，不能作为发给同事的项目包。示例口令文件和模型配置在备份目录之外，继续由本人单独保管，不写进清单。

### 升级程序，继续使用原状态

停止旧启动器，将新版解压到另一个程序目录，按新版 README 准备其虚拟环境。**不搬动或合并工程与状态目录**，从新版目录使用同样的三个归属参数启动：

```powershell
Set-Location 'C:\CivilBuddyApps\preview-new'
& .venv/Scripts/python.exe scripts/start_unified_workbench.py --binary bin/civil-workbench.exe --python .venv/Scripts/python.exe --state-root C:/CivilBuddyState/teammate-a/demo --user-id teammate-a --workspace C:/CivilJobs/demo --token-file C:/CivilBuddySecrets/teammate-a/login-token.txt --port 8765 --check
& .venv/Scripts/python.exe scripts/start_unified_workbench.py --binary bin/civil-workbench.exe --python .venv/Scripts/python.exe --state-root C:/CivilBuddyState/teammate-a/demo --user-id teammate-a --workspace C:/CivilJobs/demo --token-file C:/CivilBuddySecrets/teammate-a/login-token.txt --port 8765 --open
```

若旧实例最初传的是 `--state-root runtime/unified`，应继续指向**旧程序目录下这个基础目录的绝对路径**，例如 `C:/CivilBuddyApps/preview-old/runtime/unified`；不要在新版目录继续使用相对路径，也不要把状态复制进新目录后尝试重新绑定。前面的包外目录示例供首次建立实例使用，不是现有实例的迁移步骤。

### 从个人备份恢复到原路径

先停止自己的旧实例。只在两个原目标目录均不存在或为空时执行；任一目标已有内容就停止，不能将备份与现有数据库混合。下面命令沿用备份步骤中的四个变量，先核对记录的两个绝对路径和用户名，再复制；没有删除或覆盖非空目录的操作。

```powershell
$ErrorActionPreference = 'Stop'
$locations = Get-Content -LiteralPath (Join-Path $backupRoot 'restore-locations.json') -Raw | ConvertFrom-Json
if ($locations.user_id -cne $instanceUser -or
    $locations.workspace_root -cne $workspaceRoot -or $locations.state_base -cne $stateBase) {
    throw '用户名或原绝对路径不一致，停止恢复；不要修改归属数据库。'
}
$copies = @(
    @{ Source = (Join-Path $backupRoot 'workspace'); Target = $workspaceRoot },
    @{ Source = (Join-Path $backupRoot 'state-base'); Target = $stateBase }
)
foreach ($copy in $copies) {
    if (-not (Test-Path -LiteralPath $copy.Source -PathType Container)) { throw "备份目录缺失：$($copy.Source)" }
    if (Test-Path -LiteralPath $copy.Target) {
        if (-not (Test-Path -LiteralPath $copy.Target -PathType Container) -or
            @(Get-ChildItem -LiteralPath $copy.Target -Force).Count -ne 0) {
            throw "恢复目标非空或不是目录，拒绝覆盖：$($copy.Target)"
        }
    }
}
foreach ($copy in $copies) {
    if (-not (Test-Path -LiteralPath $copy.Target)) { New-Item -ItemType Directory -Path $copy.Target | Out-Null }
    Get-ChildItem -LiteralPath $copy.Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $copy.Target -Recurse -Force
    }
}
```

复制完成后，按上面的新版启动命令重新登录。在 Agent 中打开原工程，从服务端会话列表找回历史任务，核对任务状态、原件 SHA-256，并下载一份旧副本与备份文件核对哈希；同时重开所用的 CAD、计划或物流项目。没有核对通过前不要开始新写入。浏览器缓存不是备份，历史签认也不会授权新任务。以上是同归属原路径恢复流程；另一台电脑是否可用仍需实际验收。

验证发布 ZIP：

```powershell
& .venv/Scripts/python.exe scripts/build_unified_release.py --verify <发布包.zip>
```

校验器检查成员路径、大小和 SHA-256；它不证明其他电脑已经能启动，也不证明二进制与源代码相符。正式构建记录应另保存编译命令和源码提交。

## 验收项目

### 当前完善顺序与行业资料

2026-09-30 的优先级来自现有实现缺口和公开一手资料，属于产品设计取舍，不表示取得行业认证：

- [BCA 的 IDD 说明](https://www1.bca.gov.sg/growth-and-transformation/productivity/idd-integrated-digital-delivery/)强调跨阶段协作、结构化资料和明确的角色及交付物。因此先把同一工程目录的资料修订、问题负责人、处理依据和内部复核接起来。
- [UK BIM Framework 的 CDE 工作流指南（2020 年版）](https://www.ukbimframework.org/wp-content/uploads/2021/02/Guidance-Part-C_Facilitating-the-common-data-environment-workflow-and-technical-solutions_Edition-1.pdf)提供修订、状态和留痕的设计参考。本项目只实现单用户实例内的资料控制，不声称 ISO 19650 合规、多人会签或完整 CDE。
- [IMO/ILO/UNECE CTU Code](https://www.imo.org/en/ourwork/safety/pages/ctu-code.aspx)涵盖运输单元的装载与固定。当前求解器的几何可行结果不能替代架体、稳定性、绑扎和吊装核定；原始姿态或禁叠要求不能在导入和成箱中被丢弃。

新版“项目资料与问题”页与 Agent 使用同一已授权工程目录注册表，保存资料快照、SHA-256、版本标记、RFI/质量/变更/待办事项、负责人、截止日期和处理依据。新修订会重新打开已解决的关联事项；来源内容变化、来源不可读或未解决事项会阻止内部复核。岗位名称、文件名、原文和个人输入不会随界面语言切换而改写。

边界：每个工程最多 100 份受控资料、每份 256 个修订、2000 个事项；单文件 16 MiB，历史快照累计 512 MiB。普通 API 概览最多检查 64 MiB，超出者显示未检查；页面刷新和交接记录使用完整检查模式，覆盖 512 MiB 快照额度。所有记录只代表该实例身份；填写负责人不会发送通知。来源是当前检查时的内容，后续改变仍需重新检查。

“下载项目交接包”调用 `GET /api/project-control/package?workspace=<已注册工程 ID>`，导出当前台账修订的只读原文件快照、`manifest.json` 和完整工程审计记录 `audit.jsonl`。清单保存版本、SHA-256、来源状态及检查时间、事项与截止日期、内部复核记录和导出截止时间；内容与清单在同一数据库读快照中绑定，并逐文件核对哈希。来源已变更、不可读或尚未完成复核时仍可交接，清单明确标记草稿、未就绪，不形成签认。包大小上限 128 MiB，逐文件处理并通过临时文件流式下载；超限可分别下载修订快照和 JSON 交接记录，不删除历史。这个项目包不包含旧修订的文件内容、未登记文件、实例数据库、登录凭据、模型配置或运行会话，不能替代实例备份与恢复。

下一阶段的验收应覆盖：真实招标补遗导致旧应答失效、两组不同的已包装货物、真实工程计划变更、真实工程文档的 Word 分页与 Excel 重算，以及另一台 Windows 机器的安装与恢复。公开指南可以帮助选择流程，不能代替这些实测证据。

| 场景 | 必须保留的证据 | 边界 |
|---|---|---|
| 招标 → 箱单 → 装柜 → 应答草稿 | 条款原文和位置、箱单来源、实际工具记录、缺项和草稿 | 合成示例必须标注；不能声称已批准投标或订舱 |
| CAD → 建模 → 保存 → 恢复 → 改参 → 撤销 → GLB 重开 | 原图哈希、明确单位和高度/长度、孔洞及尺寸对照 | 用户要求只用图中明确尺寸；缺失时停在待补充 |
| 计划 → 关键线路 → 改参 → 撤销 → 项目包 | 工期、前后依赖、日历和资源来源 | 缺项不自动编造为真实计划 |
| 箱单 → 台账 → 项目包 | 每个字段的页码/单元格、包装数量和尺寸 | 缺逐包装尺寸时不能继续真实装柜验收 |
| 新电脑安装 → 登录 → 导入 → 重开 | 同事、机器环境、包哈希、截图、实际结果 | 同机临时目录或子进程测试不算跨电脑验收 |

仓库中已有真实文件的历史限定验证，详见各领域说明。它们不能替代当前版本的完整真实链路验收。本轮本地盘点发现指定物流图片明确标注为合成样例；指定 CAD 仍缺拉伸长度和实体确认，未生成模型。尚未取得明确尺寸、完整计划或第二台电脑记录时，验收表必须留为待完成。

### 2026-09-30 本机验证与基准说明

自动化和浏览器演示使用合成工程、独立临时状态和本地脚本模型。首次 DeepSeek 请求因启动环境继承了不同的 Key 返回 HTTP 401。随后按用户指示读取现有项目的本地配置，通过官方模型列表接口验证并接入 deepseek-flash；未把密钥复制进仓库或发布包。首轮已认证的真实模型工作流取得 9 次模型响应，但连续文档预览未通过修改数字的引用校验，最终触发预算拒绝；没有生成副本，原件哈希不变。该失败保留在验收记录中，不算完整工作流通过；供应商返回的 token 用量也不等于费用账单。 补齐工具参数结构和格式错误提示后，使用同一提示、样例生成器、校验标准和预算重新验收：5 次真实模型响应、约 15.6 秒完成，Word 与 Excel 两个新副本数值正确，未改部分和目标样式保留，原件哈希不变。这次模型流程只验证了一条合成资料流程，当时未进行 Word 渲染或 Excel 重算。后续独立 Office 验收已用本机 Microsoft Word 16.0 将其简单报告只读转为一页 PDF，并逐页检查；Microsoft Excel 16.0 重算并另存独立副本，公式 `D2=B2*2` 在 `B2=6` 时返回并保存缓存 `12`。原件与登记副本哈希保持不变。此检查没有把 Office 引擎接入产品，也未覆盖复杂真实工程文档。本轮没有迁移私人业务目录或完成另一台电脑验收。真实浏览器已验证资料登记、事项保存、重启后恢复、中英文切换、带工程目录进入 Agent 的只读检查，以及实际下载 ZIP。下载包逐成员解压和 CRC 检查通过，未解决事项保留为草稿。

Rust 主机与固定 Python 服务的真实 HTTP 验收覆盖登录 Cookie、目录与身份隔离、工程重启恢复、会话包交接、文档副本和来源保留、双进程停止。自动测试另覆盖修订冲突、问题变化后的复核失效、凭据文件拒绝、交接包快照及哈希一致性、活跃内容 PDF 禁止内嵌预览，以及 Excel 原件和早期导出副本不被自动覆盖。

运输要求新增限制后，原幕墙样例的直立、A 架和禁叠要求会触发补资料，不再自动给出柜数。旧开发评测 `test/benchmarks/model_mode/requests.json` 保持原样，可用 `--legacy-set` 复跑；新 `requests_handling_v2.json` 明确把这些输入的正确结果改为拒绝，12/12 通过的开发集分数不能与 2026-09-26 的旧分数直接比较。正向几何求解回归使用另外构造并明确命名的合成输入；原始幕墙 Excel 没有删除运输备注。

封存箱单评测未修改数据或门槛。通用的“封面后唯一可识别材料表”读取修复使 pl06 正确读取材料，但 pl01 中 3900 mm 的 Upright only 板件仍被拒绝，旧成功期待未被改写为通过。当前达到原有整体门槛并不表示该受限板件已经得到可装运方案。

## 可复跑检查

```powershell
# 不启动服务、不联网调用模型；先发现解释器、依赖、端口及目录问题
& .venv/Scripts/python.exe scripts/start_unified_workbench.py --python .venv/Scripts/python.exe --binary bin/civil-workbench.exe --check
npm run check
npm run check:full
# 指定当前编译产物，启动临时实例做真实 HTTP / 重启 / 隔离回归
& .venv/Scripts/python.exe scripts/test_unified_runtime_http.py --binary workbench/target/debug/civil-workbench.exe
```

普通测试使用离线脚本模型，不访问真实收费模型。提示注入集是合成开发集；通过不能宣称能抵御所有恶意文档或所有模型行为。执行结果以本次测试日志为准。

普通问答会展示本轮实际检索或提交核验的原文引文。宿主在任务收尾重新读取所选原件，记录定位、哈希和核对时间；来源变化、不可读或核验失败会保留状态。中英切换不翻译原文。这只能证明引文与核对时的文件是否一致，不能代替对回答全部结论的审核。

需检查已配置的真实模型时，可在独立的空工程目录运行以下命令。它会发送合成 PDF/Word/Excel 资料并产生模型费用；不是普通离线测试的一部分。具名实例的 `--workspace` 必须事先指向同一验收目录，登录口令放在目录外。

```powershell
& .venv/Scripts/python.exe scripts/unified_acceptance.py --base http://127.0.0.1:8765 --work-dir C:/CivilBuddy/acceptance-job --report-dir C:/CivilBuddy/acceptance-report --live --expected-model deepseek-flash --expected-provider-host api.deepseek.com --token-file C:/CivilBuddy/login-token.txt
```

示例主机名和模型名须与使用者实际选定的服务一致。脚本不会读取或修改提供商密钥；缺少可核对的主机元数据时拒绝开始。超时会请求取消并等待终态，失败或取消后仍核对原件哈希；如果没有观察到停机，会明确保留“仍运行或未知”。报告区分提供商返回的用量和估算值，不宣称能限定货币费用，也不将产品回执当作提供商独立证明。

第二台电脑与真实资料验收可填写[验收记录模板](acceptance/sme-preview-checklist.md)。空项不算通过，填写后的私人业务记录不要回传公共仓库。

2026-09-30 另在停机的合成验收实例完成完整目录备份、逐文件哈希核对、保留原目录后原路径恢复，再用不同程序目录的 `.8` 包启动。原身份、任务、用量和两份下载字节一致；未新增模型调用。该实测验证同机、同绝对路径恢复和程序升级，不代表迁移到新工程路径或第二台电脑通过。
