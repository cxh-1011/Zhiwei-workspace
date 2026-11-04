# 知微 Agent 当前实现说明

## 对话与工具

- `POST /chat` 是统一聊天入口，接受 `mode`：`chat`、`file`、`paper`，以及 `allow_writes`、`allow_workspace`、`allow_code_edit`、`allow_command`、`show_thinking`。
- 千问通过 DashScope OpenAI 兼容接口 Function Calling 选择工具。
- 工具都在 `backend/tools/` 注册：`web_search`、`scholarly_search`、`paper_search`、`search_uploaded_files`、`list_uploaded_files`。
- `scholarly_search` 实时查询 Semantic Scholar 与 Crossref 的学术元数据，返回题名、作者、年份、摘要、DOI 和可用的开放获取链接。它不登录或抓取知网、IEEE Xplore、ScienceDirect，也不绕过付费全文权限。可选配置 `SEMANTIC_SCHOLAR_API_KEY` 提高 Semantic Scholar API 使用能力，`CROSSREF_MAILTO` 用于 Crossref polite pool。
- 工具做搜索、检索、只读的代码与 git 查看、「学习档案写入」，以及（显式开启后）工作区文件写入与受限命令执行。命令执行不经 shell、三档分类、需确认的命令挂起等用户点选，详见[命令执行](#命令执行)。
- 工具结果会回给千问；它可以继续调用工具，或生成最终回答。
- 文件问答支持 TXT、PDF、DOCX，以及 JPG、JPEG、PNG、WEBP 图片。图片（最大 10 MB）作为 Base64 视觉输入发送给配置的 `AGENT_VISION_MODEL`，默认 `qwen3-vl-plus`；图像问答不走文本切片检索。
- 预算是**两档**的：普通问答 5 轮 / 单轮 3 个工具 / 60 秒 / 24,000 字符 / 12,000 tokens；勾选「允许改代码」或「允许跑命令」后自动切到 20 轮 / 单轮 6 个工具 / 300 秒 / 60,000 字符 / 60,000 tokens。
- 预算集中在 `agent.py` 的 `Budget` 类：`CHAT_BUDGET` / `CODE_BUDGET` 两个实例，由 `budget_for(allow_code_edit or allow_command)` 选择。环境变量只在代码内的上下界之间可调，`start` 事件把整套预算带给前端，UI 不硬编码任何数字。
- `_fit_context()` 裁剪历史时**钉住第一条 user 消息**。早期版本只从后往前装，最近几轮的工具体积一旦就超过上限，`kept_groups` 会是空的，整段上下文只剩 system —— 模型拿不到问题只能乱答。改代码模式单轮工具结果 6000 字符、最多 6 个，很容易踩到。
- 聊天历史保留最近 12 条供短期上下文使用。论文检索仍使用当前 FAISS 论文索引；重新上传论文会替换当前索引。

## 流式输出与中断

对话默认走 `POST /chat/stream`，返回 `text/event-stream`。`run_agent_stream()` 是一个生成器，边跑边产出事件，`run_agent()` 只是把它跑完并返回旧格式结果的包装，所以 `/chat`、`/paper_chat`、`/chat_file` 这些老接口行为不变。

| 事件 | 含义 | 前端处理 |
| --- | --- | --- |
| `start` | 运行开始，带 `run_id` | 记下 `run_id` 供停止按钮使用 |
| `round` | 新一轮模型调用开始，带 `round` / `max_rounds` | 过程面板插入「第 N 轮」分隔行 |
| `reasoning` | 模型的思考片段（与正文分开） | 写进过程面板的「模型思考」块，纯文本追加 |
| `token` | 模型输出的文本片段 | 追加到回答区（纯文本，用 rAF 合并重绘） |
| `tool_start` | 即将调用工具，带工具名与参数 | 过程面板插入工具条目，状态为执行中 |
| `file_edit` | 写入类工具落盘成功，带 `path` / `added` / `removed` / `diff` | 在对应工具条目里展开红绿 diff（该事件排在 `tool_end` 之前，才能挂到刚建的条目上） |
| `command_ask` | 有命令等待确认，带 `command` / `reason` / `cwd` / `wait_seconds` / `approval_id` | 过程面板里插入确认卡片，两个按钮回 `POST /chat/approve/{run_id}` |
| `command_verdict` | 审批结果 `approved` / `denied` / `timeout` / `cancelled` | 卡片变成已允许/已拒绝，非用户点选的结果（超时、取消）也在这里回写 |
| `tool_end` | 工具返回，带 `ok` / 耗时 / 结果摘要 | 条目打勾并显示耗时与结果摘要 |
| `limit` | 触发某个预算上限或用户停止 | 面板标题显示具体原因 |
| `error` | 模型调用或执行异常 | 面板内插入红色错误条目 |
| `done` | 运行结束，带 `reply`、`steps`、`status`、`total_tokens` | 用 Markdown 重绘回答，过程面板就地换成完整时间线 |

几个实现要点：

- **工具调用参数是分片到达的**，`chat_completion_stream()` 按 `index` 累加 `arguments`，不能只读某一帧。
- **思考内容单独走一条流**。`reasoning_content` 会被单独 yield 成 `reasoning` 事件，绝不混进 `content`，否则正文会掺进模型的自我对话。`show_thinking=true` 时才会请求：Qwen3 系列通过 `extra_body.enable_thinking` 打开，推理类模型（reasoner / thinking）本来就会返回，无需开关。
- **可选参数一律可降级**。`_open_stream()` 按「带 stream_options 和 extra_body → 只带其一 → 都去掉」依次尝试，只对参数类报错重试，鉴权或网络错误直接抛出，不会把 401 也重试四遍。
- **落库放在流的末尾**。`/chat/stream` 把 `_persist_chat()` 放在生成器的 `finally` 里，前端中途断开也不会丢记录。
- **中断是协作式的**。`/chat/cancel/{run_id}` 只在内存字典 `_CANCEL` 里记一笔，Agent 在「每轮开始前」「每次工具调用前」以及「每个流式分片到达时」检查，因此停止会立刻生效；超过 5 分钟的标记自动清理。状态记为 `cancelled`，已生成的半截回答会保留并写入记录。
- **Nginx 必须关掉缓冲**（`proxy_buffering off`），否则逐字输出会被攒成一次性返回。响应头里的 `X-Accel-Buffering: no` 是第二道保险。

离线测试（不需要 API Key，用假模型替换 `agent.chat_completion_stream`）：

```bash
cd backend
..\.venv\Scripts\python.exe test_streaming.py    # 事件顺序、工具调用、非法参数、取消
..\.venv\Scripts\python.exe test_stream_sse.py   # 401、SSE 帧、消息与运行记录落库
..\.venv\Scripts\python.exe test_thinking.py     # 思考事件、轮次事件、参数降级重试
```

## 运行记录

- `agent_runs` 保存一次执行的状态、用时和回答。
- `agent_steps` 保存模型轮次、工具名称、参数、结果摘要、思考摘要（`reasoning`）和成功状态。
- `GET /agent_runs?chat_id=...` 返回会话运行记录，历史聊天打开后也会显示对应工具过程与思考摘要。

## 长期记忆与学习计划

- 顶部“学习档案”入口允许用户主动保存或删除长期记忆。
- Agent 只参考用户确认保存的记忆和进行中的计划。
- **默认不会自动写入**：`allow_writes` 默认为 `false`，此时 `remember`、`list_learning_tasks`、`set_task_status` 三个工具**不会注册**，模型看不到它们。
- 用户在前端勾选「允许助手记录」后，`POST /chat` 携带 `allow_writes: true`，后端才注册这三个写入工具。系统提示词同时要求：只在用户明确说出「记住 / 记一下」或「勾选 / 完成某项任务」时调用，勾选前必须先 `list_learning_tasks` 取编号，写入后要说明写了什么。
- 助手写入的记忆在 `memories.source` 里标记为 `agent`，前端显示「助手写入」标签，用户可随时删除；用户自己添加的标记为 `user`。`source` 列由 `init_db()` 自动迁移补齐，旧库无需手工改。
- 学习计划由用户填写目标后调用千问生成并保存；可以标记完成、恢复或删除。
- 记忆最多保留 100 条，计划最多保留最近 200 条。

### 写入类工具

| 工具 | 作用 | 关键约束 |
| --- | --- | --- |
| `remember` | 写入一条长期记忆 | 内容相同的不重复写入；上限 1200 字符；来源记为 `agent` |
| `list_learning_tasks` | 列出计划与任务编号、完成状态 | 只读，供勾选前取 `plan_id` / `task_id` |
| `set_task_status` | 把任务标记为 `done` / `todo` | `plan_id` 与 `task_id` 必须匹配，否则返回失败并提示先查列表 |

## 工作区只读工具

`allow_workspace` 默认为 `false`，此时下面这一组工具**不会注册**，模型看不到它们。用户在前端勾选「允许读代码」后才注册。

| 工具 | 用途 | 典型调用时机 |
| --- | --- | --- |
| `repo_map` | 目录结构 + 每个源码文件的行数与顶层函数/类名 | 面对不熟悉的仓库，第一步 |
| `list_dir` | 单层目录列表 | 确认某个目录里有什么 |
| `read_file` | 带行号的分页读取（`start_line` / `max_lines`） | 已经定位到具体文件之后 |
| `grep_code` | 正则搜索，返回「文件:行号: 内容」 | 找一个函数或常量的位置 |
| `git_status` | 分支、HEAD、未提交条目 | 动手前确认工作区是否干净 |
| `git_diff` | 未提交差异（含 `--stat` 与未跟踪文件清单） | 确认「刚才那步改了什么」 |
| `git_log` | 最近提交（oneline + 标签） | 了解某个文件的演变 |

七个工具全部只读：不能写文件、不能执行命令。路径判断统一走 `backend/tools/_guard.py`，读写执行三类工具共用同一套规则；写入类工具复用其中的 `resolve(rel, write=True)`（额外拒绝目录目标与不存在的父目录）。

安全边界：

| 边界 | 实现 | 作用 |
| --- | --- | --- |
| 路径限制 | 所有路径经 `realpath` 解析后必须落在 `AGENT_WORKSPACE` 之下 | 挡住 `..` 穿越、绝对路径、符号链接逃逸 |
| 红线（目录） | `.git` / `.venv` / `venv` / `env` / `node_modules` / `__pycache__` / `uploads` / `backups` / `logs` / `.ssh` / `.aws` / `.gnupg` / `.workbuddy` / `.idea` / `.vscode`，命中即整棵子树不可见 | 防止把 API Key、数据库、依赖、备份读进模型上下文 |
| 红线（文件名与后缀） | 前缀 `.env`（含 `.env.example`）；精确名 `.npmrc` / `.pypirc` / `.netrc` / `credentials*` / `secrets.*` / `id_rsa` 等；后缀 `.db` / `.sqlite` / `.key` / `.pem` / `.p12` / `.kdbx` 等，先剥掉 SQLite 的 `-wal` / `-shm` / `-journal` 尾巴再判断 | 同上。附属文件是最容易漏判的一处：`chats.db-wal` 的后缀是 `.db-wal` 而不是 `.db` |
| 拒绝话术 | `block_reason()` 返回中文类别，`resolve()` 拼进报错并明说「换个路径写法也不会通过」 | 模型不会反复试探 `.env.bak`、`config/.env`，省轮次 |
| 二进制 | 读前 8 KB 嗅探 NUL 字节与 UTF-8 失败比例；已知文本后缀跳过探针；探针截断处「去掉尾部 4 字节」重判一次 | 不把乱码塞进上下文，中文文件不被误判 |
| 体积 | 单文件读取 2 MB、单次 400 行、单行 400 字符；grep 与仓库地图对超过 512 KB 的文件直接跳过；grep 最多 100 条 | 防止一个日志拖垮整个扫描 |
| git 调用 | `subprocess` 直接调用（不经 shell）、15 秒超时、输出截断 6000 字符、`core.quotepath=false` | 防命令注入、防卡死、中文文件名不转义 |

`repo_map` 的预算是**广度优先**：先保证每个文件都被列出，再按剩余空间分配符号数量（12 → 8 → 6 → 4 → 2 → 1 → 0 逐档退让）。反过来先给符号会在仓库稍大时让整个 `frontend/` 消失，而「有哪些文件」才是地图的主要用途；具体符号可以用 `grep_code` 补。公开符号（不带下划线）排在同文件私有符号之前，避免 `_helper` 把入口函数挤掉。

红线**只判路径，不猜内容**：`README.md` 里写着 `SECRET_KEY=...` 的示例照样可读。按内容过滤会同时误伤文档、又漏掉真正的密钥，把规则钉在路径上更可靠。

同一份清单也写进了提示词（「允许改代码」时第 ⑦–⑨ 条）。模型不必先撞墙才知道边界，工具层再硬拦一次，两层互为兜底。

`AGENT_WORKSPACE` 留空时默认使用项目根目录。回归测试见 `backend/test_workspace_tools.py`（38 项）、`backend/test_repo_tools.py`（55 项）与 `backend/test_redlines.py`（70 项）。

## 代码修改与撤销

`allow_code_edit` 默认为 `false`。开启后注册 `write_file` 与 `edit_file`，**并自动带上全部只读工具**——改代码必须能先读代码，所以不要求用户再单独勾一次「允许读代码」。

| 工具 | 用途 | 关键约束 |
| --- | --- | --- |
| `write_file` | 新建文件或整体重写 | 单文件 200 KB；已存在则先备份；内容与磁盘一致时直接返回「未写盘」 |
| `edit_file` | 按锚点局部替换 `old_string` → `new_string` | 锚点必须逐字一致且**在文件内唯一**；重复时报错并列出行号；支持 `replace_all` |

实现要点：

- **落盘前先备份**。备份写到 `backups/agent-edits/<run_id>/NNN__<扁平化路径>`，`backups` 本身在沙箱黑名单里，模型读不到备份内容，也就不会把备份当源码再改一遍。备份失败即取消写入——不接受「没有备份的写」。
- **写入失败要回滚**。写盘抛 `OSError` 时把备份复制回去，不留半截文件。
- **锚点唯一性先校验再动手**。出现 0 次 → 提示先 `read_file` 看原文；出现多次 → 报出前 5 处行号，绝不替模型猜一处改。这条挡住了「批量替换把无关函数一起改掉」这类最难查的事故。
- **行尾沿用原文件**。CRLF 文件继续写 CRLF，否则统一 diff 会变成「每一行都改了」，用户根本看不出真实改动。
- **预算**。单次运行最多 20 个文件、累计 1 MB，单文件 200 KB；超限直接拒绝并提示拆成多轮，防止模型一轮把半个仓库重写。
- **diff 双通道**。完整 diff 走 SSE 的 `file_edit` 事件给前端渲染；返回给模型的工具结果只放 1400 字片段，用来核对缩进，不占上下文预算。

撤销：

- `POST /code/undo/{run_id}` 取 `agent_steps` 里成功写入的步骤，**逆序**还原。同一文件改两次时，先还第二次的备份再还第一次，结果才是运行前的样子；`write_file` 新建的文件没有备份，直接删除。
- 回滚前比对 `after_hash`（写入内容的 SHA-1 前 16 位）：如果文件在这次改动之后又被用户或别的工具改过，跳过并在 `skipped` 里说明原因，**不覆盖**后来的编辑。
- 备份只增不删，`backups/` 已在 `.gitignore` 中，不影响版本库；需要清理时按目录手工删除即可。

回归测试见 `backend/test_edit_tools.py`（71 项，全部在临时工作区里跑，不碰仓库真实文件）。

## 命令执行

`run_command` 让 Agent 从「能改」变成「能验证」。默认关闭，`allow_command` 独立于 `allow_code_edit`：能改文件不等于能执行东西。

**定位**：不是沙箱。命令在用户权限下跑真实进程，能读写用户能读写的一切。它降低误伤，不提供安全边界。

```
classify_command(command, cwd)
  ├─ deny  → 破坏性/外联/起外壳：rm、mv、cp、curl、chmod、sudo、powershell、bash、find -delete
  │          含 shell 元字符：| > < && ; ` 换行
  │          参数里出现红线路径：.env、*.db、私钥
  ├─ ask   → 装依赖、构建、python -c、python -m <非测试模块>、git 写操作、未知命令
  └─ auto  → python -m pytest/unittest、python <工作区内脚本>、只读 git、ls/cat/head
```

实现要点：

- **自己分词**，不启 shell。Windows 下 `shlex(posix=False)` 会把引号留在 token 里、`posix=True` 又会吃掉反斜杠路径，所以用 30 行的状态机：按空白切分、支持引号、遇到 `| > < & ;` 与反引号直接报错。引号内的字符不算元字符，`pytest -k "a and b"` 照常能用。
- **分类要看 cwd**。`python helper.py` 在 `cwd=src` 时指向 `src/helper.py`；不带 cwd 去解析会把正常命令误判成「脚本不存在」而要求确认——这个 bug 是第一次跑测试时发现的。
- **解释器归一**。`python` / `python3` / `pytest` 一律换成当前 `sys.executable`，否则会调到一个没装依赖的系统 Python，报错信息还跟项目无关。
- **环境变量脱敏**：启动前剔除名字含 `KEY`/`TOKEN`/`SECRET`/`PASSWORD`/`CREDENTIAL` 的变量，并设 `PYTHONUTF8=1`、`GIT_PAGER=cat`、`GIT_TERMINAL_PROMPT=0`。不剔除的话，一句 `print(os.environ)` 就能把 Key 抄进上下文。
- **杀进程树**：Windows `taskkill /F /T /PID`，POSIX `killpg`。只杀父进程会留下孤儿——测试框架、构建工具都会拉子进程。
- **输出采集有上限**：独立读线程最多采 40 KB，主线程 50ms 轮询退出码、取消标记与超时。回传时保留头 2600 + 尾 1600 字符，因为失败摘要都在尾部。
- **退出码非零不算工具故障**：命令确实跑完了，只是它自己失败。`ok=True`，退出码写在正文头部，输出原样回给模型定位问题；只有拒绝、超时、启动失败才算 `ok=False`。
- **重复调用检测对命令放行**。检索类工具重复搜同一个词没有新信息，但「改完代码重跑同一条测试」是最正确的动作——第一版拦掉了它，实测模型被迫去掉 `-v` 再跑一次，白烧一轮。

审批链路（`agent.py`）：

- `_APPROVALS` 是内存字典，`approval_id → {run_id, Event, allow}`，生命周期只有一次请求。
- 生成器 `yield command_ask` 后调 `_wait_approval`，内部按 0.25 秒轮询 `Event`，同时检查取消标记与超时。不直接阻塞 `Event.wait(timeout)`，是因为那样点「停止」要等满 120 秒才生效。
- `POST /chat/approve/{run_id}` 按「运行号 + 审批号」双键校验，跨运行点不动；超时后条目已移除，接口返回 404 而不是悄悄放行。
- 被拒绝时回给模型的话里明确写「不要原样重试」。不写这句，它会换个参数重试同一条命令。

回归测试见 `backend/test_command_tools.py`（100 项）。

## 部署

部署前先备份服务器上的代码、`.env`、`chats.db`、`uploads/` 和 `paper_index/`。新版本新增 `openai` Python 依赖，上传代码后需要在 Python 3.11 虚拟环境里执行：

```bash
python -m pip install -r requirements.txt
```

随后重启 Uvicorn，并确认 `/auth/status`、`/docs` 和登录后的聊天页面可用。这个工作区只完成本地代码，尚未连接或修改阿里云服务器。
