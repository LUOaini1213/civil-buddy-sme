# 系统级沙箱

`packing_assistant/sandbox.py` 是应用层策略：代码写盘前先问它。只要每一处写盘都记得问，它就管用。系统级沙箱是另一种东西——**不靠代码自觉，靠内核拒绝**：一轮任务里解析你的表格和文档、跑装箱引擎、写成稿这些活，放进一个单独的工作进程；这个进程一启动先把自己关进去，再自检，然后才接活。解析器有 bug、表格是恶意构造的、哪条路径漏了策略检查，它都写不到 `<工地>/.civil-buddy/out` 以外，起不了别的程序。

和 Codex 的分工一样：**模型对话留在宿主进程**（它要联网），**模型要求做的事在受限进程里做**（它不能）。

```bash
civil sandbox                      # 本机内核能限制什么 + 起一个受限进程当场自检（退出码 0 = 写入限制已被证实）
civil --sandbox-backend os exec "整理日报，……"
civil --sandbox-backend auto …     # 能用就用，不能用就退回应用层，并说明原因
```

也可以写进 `civil.toml`（`sandbox_backend = "os"`）或 `CIVIL_SANDBOX_BACKEND`。默认仍是 `app`。

## 内核管住了什么

| | 写入 | 起进程 | 网络 | 机制 |
| --- | --- | --- | --- | --- |
| Linux | ✓ | ✓ | ✓ | Landlock：只「处理」写类访问，所以读和执行到处可以，创建/写/截断/改名/删除只在写根之下；ABI ≥ 4 再禁 TCP。seccomp：`socket(AF_INET/AF_INET6)`、`execve/execveat` 返回 EPERM。都不需要特权，对 root 同样有效 |
| Windows | ✓ | ✓ | ✗ | 进程把自己的完整性级别降到 Low（降了就升不回去）：内核的强制完整性控制不许 Low 进程改动 Medium 的东西，而你名下的文件默认都是 Medium。宿主事先只给 `.civil-buddy/out` 打上 Low 标签。Job 对象 `ActiveProcessLimit=1` 禁止起子进程。不需要管理员，不装任何东西 |
| macOS | — | — | — | 没做。该接 Seatbelt，但这里没有机器能验证，所以不假装有 |

**Windows 的网络没有被内核限制**，文档和 `civil sandbox` 的输出都照实写：非管理员进程要断网只有 AppContainer（得改 Python 安装目录的 ACL）或防火墙规则（要管理员）两条路，都不该由一个命令行工具替你做。工作进程在应用层拒绝 inet 套接字，那是礼貌，不是墙。

`civil sandbox` 的自检不是读配置，是在受限进程里**绕开策略层直接写**：

```text
selftest（受限进程里当场试，答案来自操作系统）
         写 .civil-buddy/out   allowed
         写作业文件夹本身        denied
         写用户目录             denied
         写系统临时目录          denied
         起子进程              denied
         建 inet 套接字        denied
```

工作进程每次启动都跑这组自检；「里面能写、作业文件夹和用户目录不能写」三条有一条不对，它就报 `fatal` 退出，一个请求都不接。

## 拿不到的时候

Windows 的数据盘可能只继承 Modify 权限：创建者虽然是目录所有者，却没有设置 Low 标签所需的 `WRITE_OWNER`。启动器仅在设置标签返回 Access denied 时，对当前用户本人拥有、无链接的 `.civil-buddy/out` 补一条不继承的本人 `WRITE_OWNER` 允许项，再设置标签。保留其他 ACL，不授予 Full Control，不改所有者、原件或工作根目录，也不启用管理员权限；已有显式或继承的相关拒绝规则不覆盖，非本人所有或修复失败仍拒绝执行。该权限及 Low 标签持久保存在输出目录。详见 [Microsoft SECURITY_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-information)；真实权限与受限进程回归为 `scripts/test_windows_sandbox_label.py`。

- `sandbox_backend = os` 而拿不到（不在作业文件夹里、内核不支持、工作进程没能进入受限状态）：**本轮拒绝执行**（`sandbox_unavailable`），不会悄悄不设防地跑。
- `auto`：退回应用层，并在回复开头说明原因。
- 需要作业文件夹（`civil init` 或 `-C`）：那里的 `.civil-buddy/out` 是唯一的可写处。

## 开着它，有什么不一样

- 每轮多一个进程：实测整条 `civil exec` 中位数 0.80 s → 1.03 s（本机 Windows，5 次）。
- 草稿旁的 Excel 照出；**作业文件夹根目录那份副本由宿主在回合结束后补上**（规则不变：不覆盖不是我们写的同名文件，记入 `.civil-buddy/exports.json`）。
- **不会去改你文件夹里已有的工作簿**（平时点名一份 `.xlsx` 会往里加 `CB草稿-*` 工作表）：那是写作业文件夹，受限进程做不到，也不该替它开口子。
- 高风险岗位的确认句仍在宿主这边当场问——受限进程没有终端。

## 它不管什么

- **读**没有被限制：受限进程读得到你读得到的一切。`.env`、密钥文件仍由应用层拒读。Landlock 只有放行没有拒绝，要在内核层挡住某个子路径，就得放弃「到处可读」，那样解释器自己都起不来。
- Windows 上 `.civil-buddy/out` 的 Low 标签是持久的：本机其他 Low 完整性进程也能写这个草稿文件夹。
- 宿主进程本身（路由、模型对话、审批、补副本）不在沙箱里。
- 不是虚拟机，也不是容器：不隔离 CPU、内存、时间，也不防内核漏洞。

复验：`python scripts/test_os_sandbox.py`（Windows 与 Linux 各 8 项；内核不提供后端的机器上内核那 5 项会大声跳过，不会悄悄通过）。
