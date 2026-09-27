# civil：在工地文件夹里干活的命令行

Codex 在代码仓库里工作；`civil` 在**工地文件夹**里工作。这一页是从零到出稿、复核的完整走法，每条命令都能照抄。背后的对位与缺口见 [civil-codex-eval-2026-09-19.md](civil-codex-eval-2026-09-19.md)；三道确定性检查各自的基准与数字见 `test/benchmarks/number_provenance`、`test/benchmarks/verdicts`、`test/benchmarks/task_intent` 下的 README。

下文的 `civil` 即 `python -m packing_assistant.civil`（在仓库根目录、已装 `requirements.txt` 的环境里）。

## 1. 进文件夹，写一份本工程说明

```bash
cd D:/工程/东桥二标
civil init          # 写 CIVIL.md：项目、辖区、业主、合同号……只填你确认过的，留空的栏在成稿里保持 UNSPECIFIED
civil status        # 当前文件夹、CIVIL.md 读到了什么、sandbox / approval、模式、模型（不显示 Key）
```

`CIVIL.md` 相当于 Codex 的 `AGENTS.md`：向上就近找，子文件夹里的那份覆盖上层，总量上限 32 KiB。之后在这个文件夹或它的子目录里运行 `civil`，会话和文书都落在 `<工地>/.civil-buddy/out`，不会写进别处；`civil -C <文件夹> …` 等同于先 `cd`。

## 2. 交代一件事

```bash
civil "整理日报，日期：5月6日，部位：东桥3号墩，天气：晴，出勤：钢筋工12人"
civil exec "packing.csv 要几个柜"                  # 点名文件夹里的装箱单：装箱引擎真算，留下 pack-plan.md
civil exec "解析招标 招标文件.docx"                 # 解析的是那份文件，不是这句话
civil exec "全面检查投标响应：招标文件.docx 投标响应.docx"   # 逐条对照，数值对不上的单列；角色只从文件名读
civil exec - < 任务.txt                            # 任务从标准输入读
civil exec --jsonl "……" > turn.jsonl               # 逐行 JSON 事件：thread.started → turn.started → item.* → turn.completed
civil exec -o 回复.txt "……"                        # 最终回复另存
```

几条不会变的规矩：

- **数字只由工具算。** 缺重量或尺寸的装箱行会逐条列出来，不给柜数；`can_fit=False` 按失败说，不是「方案已出」。
- **不猜。** 任务里点了两份表、或一份都没点，就不替你选；招标/响应的角色读不出来，就不分角色。
- **高风险岗位**（施工方案、安全交底、结构、岩土……）每次写盘前要你确认当前操作：TUI 里原样输入「我明白，将由持证人员签认」或 `I understand; a licensed person will sign this off.`；`exec` 不提问、如实返回 `approval_required`（明确确认本次操作时加 `--confirm`）。历史签认、引用或否认确认句均不能授权新操作。
- 一切产出都是内部讨论草稿：`submit_blocked=true`，不下「可以投标 / 可以开工」的结论。
- `--sandbox read-only` 时只读不写：`steps` 下整轮不执行、只答复；模型模式下装箱照算，只是不落盘。

要不靠代码自觉、靠内核拒绝：`civil --sandbox-backend os …` 让工具在自我禁闭的进程里跑（只能写 `.civil-buddy/out`、不能起进程，Linux 上也不能联网），`civil sandbox` 当场自检。细节和边界见 [os-sandbox.md](os-sandbox.md)。

## 3. 两种跑法

| | `steps`（默认） | `--mode model` |
| --- | --- | --- |
| 谁来选岗位、读哪些资料 | 规则路由 + 你在任务里点名的文件 | 模型：列步骤、`load_skill` 读 SOP、`list/read_job_file`、`run_skill` / `pack_plan` / `tender_compare` |
| 调不调模型 | 不调。内网、无 Key、CI 都能跑 | 调。`CIVIL_API_BASE` / `CIVIL_API_KEY` / `CIVIL_MODEL`，任何 OpenAI 兼容接口 |
| 成稿里的字从哪来 | 你的原话 + 你文件夹里的资料 + CIVIL.md | 同左——`run_skill` 没有自由文本参数，**模型写的字进不了成稿** |
| 回复里的数字 | 工具给的 | 过一道数字溯源：没出处的先改写一次，改完还在的原样点名给你 |
| 回复里的结论 | 模板里写死了不下结论 | 过一道结论护栏：「可以订舱」「符合招标文件的要求」这类话先改写一次，改完还在的从正文里划掉并点名 |

```bash
# 本机 Ollama 就能跑模型模式（提示词里有 66 个岗位的目录，上下文请给到 16k）
export CIVIL_API_BASE=http://127.0.0.1:11434/v1 CIVIL_API_KEY=ollama CIVIL_MODEL=qwen2.5:3b
civil --mode model "根据 现场记录.txt 整理今天的日报"
civil --mode auto  "……"      # 配了模型且连得上就用，否则回到 steps，并告诉你为什么
```

模式也可以写进 `civil.toml`（`agent_mode = "model"`）、环境变量 `CIVIL_AGENT_MODE`，或在 TUI 里 `/mode model`。环境里单单放着一个 Key **不会**让它自己切到模型模式。

## 4. 复核一份文稿（不调模型）

```bash
civil review 周报.md             # 你自己写的、同事给的、别的 AI 出的，都行
civil review pm-daily__log.md    # civil 自己的稿按文件名去 .civil-buddy/out 里找；重名时请写全路径
civil review --json 周报.md
```

它回答两个问题：文稿里哪些**数字、条款号**在工地资料里找不到出处（对照 CIVIL.md、文件夹里其他文件、你在这个文件夹的对话里说过的话）；有没有写**不该由文稿下的结论**（「已具备报审条件」算，「本稿不判定可以开工」这种否认不算）。装箱引擎算出来的数不在任何资料文件里，它们的出处是报告旁边那份 `pack-plan.json`（工具结果记录），复核时一并算作证据。退出码：0 干净 · 1 有待核对 · 2 审不了。找不到出处不等于错，但不能就这样交出去；反过来，干净也只说明数字都有出处，不说明数字是对的。

## 5. 自己公司的岗位：插件

```bash
civil plugin install examples/plugins/site-forms      # 目录或 .zip；--job 则装进当前作业文件夹
civil "出一份项目周报，周次：第 12 周，部位：东桥3号墩，资料见 现场记录.txt"
civil plugin trust site-forms                         # 看过内容之后
```

插件是纯声明的（SOP + 表单模板 + 知识），不含代码；未受信任时它的岗位一律按高风险、写盘要确认句。格式与安全边界见 [plugins.md](plugins.md)。

## 6. 不想用终端：桌面窗口

```bash
civil desktop                                  # 或 python -m packing_assistant.desktop <作业文件夹>
civil -C <文件夹> desktop --launcher <目录>     # 写一个双击即开的 Civil Buddy.pyw
```

原生窗口（Tk，不用装任何东西），功能和这一页讲的是同一套：打开文件夹、交代任务、看步骤、在对话框里输入确认句、双击打开成稿、复核。见 [desktop-app.md](desktop-app.md)。

## 7. 交互式（TUI）

```text
civil
/status  /init  /mode [steps|model|auto]  /model [名称]  /skills [词]
/approvals [untrusted|on-request|never]   /sandbox [read-only|workspace-write]
/new [标题]  /threads  /resume <id>  /bg <任务>  /files  /plan  /review <文稿>  /plugins
/confirm <完整签认句>  # 仅确认并重试当前尚待签认的原任务，完成后即失效
$construction 编制深基坑专项施工方案        # $id 或 @岗位名 = 点名岗位
```

每一轮都记在 thread 旁边的 rollout 里，`civil resume --last "接着上次的，把部位改成 4 号墩"` 会带着前面的对话继续（模型模式下有意义；`steps` 模式的上下文主要是项目、辖区等会话槽）。恢复历史不恢复操作授权；切换对话、提交新任务或完成一次确认重试后，都需要为新的高风险操作重新确认。

## 8. 它不是什么

不是 OpenAI Codex 的二进制，也不调用它；系统级沙箱要显式打开（默认只有应用层的写根与密钥拒读），且不限制读取；桌面窗口没有打包成安装程序；没有商店扩展、Cloud。是不是「可以投标 / 可以开工」，它不判，也禁止这么宣传。
