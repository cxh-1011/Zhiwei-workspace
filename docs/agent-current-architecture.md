# 知微 Agent 当前实现说明

## 对话与工具

- `POST /chat` 是统一聊天入口，接受 `mode`：`chat`、`file`、`paper`，以及 `allow_writes`。
- 千问通过 DashScope OpenAI 兼容接口 Function Calling 选择工具。
- 工具都在 `backend/tools/` 注册：`web_search`、`scholarly_search`、`paper_search`、`search_uploaded_files`、`list_uploaded_files`。
- `scholarly_search` 实时查询 Semantic Scholar 与 Crossref 的学术元数据，返回题名、作者、年份、摘要、DOI 和可用的开放获取链接。它不登录或抓取知网、IEEE Xplore、ScienceDirect，也不绕过付费全文权限。可选配置 `SEMANTIC_SCHOLAR_API_KEY` 提高 Semantic Scholar API 使用能力，`CROSSREF_MAILTO` 用于 Crossref polite pool。
- 工具只做搜索、检索和「学习档案写入」，没有任意 Python、Shell 或任意文件写入工具。
- 工具结果会回给千问；它可以继续调用工具，或生成最终回答。
- 文件问答支持 TXT、PDF、DOCX，以及 JPG、JPEG、PNG、WEBP 图片。图片（最大 10 MB）作为 Base64 视觉输入发送给配置的 `AGENT_VISION_MODEL`，默认 `qwen3-vl-plus`；图像问答不走文本切片检索。
- 默认每次最多 5 轮、每轮 3 个工具、60 秒、12,000 tokens；环境变量可以调节，代码另有硬上限。
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

`allow_workspace` 默认为 `false`，此时 `list_dir`、`read_file`、`grep_code` **不会注册**，模型看不到它们。用户在前端勾选「允许读代码」后才注册。

这三个工具只读：不能写文件、不能执行命令。安全边界有三层：

| 边界 | 实现 | 作用 |
| --- | --- | --- |
| 路径限制 | 所有路径经 `realpath` 解析后必须落在 `AGENT_WORKSPACE` 之下 | 挡住 `..` 穿越、绝对路径、符号链接逃逸 |
| 文件黑名单 | 目录 `.git` / `.venv` / `__pycache__` / `uploads` / `backups` 等；文件名前缀 `.env`；后缀 `.db` / `.key` / `.pem` 等 | 防止把 API Key、数据库、依赖读进模型上下文 |
| 读取上限 | 单文件 512 KB、单次 400 行、单行 400 字符、grep 最多 100 条 | 防止一次读爆上下文 |

`AGENT_WORKSPACE` 留空时默认使用项目根目录。回归测试见 `backend/test_workspace_tools.py`。

## 部署

部署前先备份服务器上的代码、`.env`、`chats.db`、`uploads/` 和 `paper_index/`。新版本新增 `openai` Python 依赖，上传代码后需要在 Python 3.11 虚拟环境里执行：

```bash
python -m pip install -r requirements.txt
```

随后重启 Uvicorn，并确认 `/auth/status`、`/docs` 和登录后的聊天页面可用。这个工作区只完成本地代码，尚未连接或修改阿里云服务器。
