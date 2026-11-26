# 第四优先：学习项目沙箱（v0.24.0）

> 目标：让 agent 在磁盘上真的建一个目录，在里面陪学生写代码、跑起来、验收到没到位。
> 不是「让助手改我的仓库」，而是「给这门课的每一章一个能动手的空地」。
>
> 范围：项目创建与登记、沙箱切根、验收执行器、学习模式的权限边界。
> **不含**项目级 venv、联网安装依赖、多语言验收。

## 0. 三个必须先修掉的坑

这三条是动手前就写在规划里的，实际做的时候全都撞上了：

1. **`_guard.WORKSPACE_ROOT` 是导入时读一次的常量。**
   `workspace.py` / `repo.py` / `desktop.py` 都是 `from ._guard import WORKSPACE_ROOT`，
   拿到的是**值拷贝**。想「临时换个根」只能改进程全局变量，而且三个模块各拿一份
   旧值——改了一处，另外两处还指着旧目录。
   改成：基础根（`WORKSPACE_ROOT`，仍是常量、仍可被测试覆盖）+ 运行时登记的
   额外根（`register_root`）+ 当前活动根（contextvar，`use_root` 切换）。
   不给 `resolve()` 加 `root` 参数，是因为它有十几处调用点，漏改一处的后果是
   **静默回落到仓库根**——学生在项目里写文件，文件落到了仓库里，还不报错。

2. **没有独立的「建项目」。**
   唯一的写入能力是 `write_file`，它要求目标文件的父目录已存在，
   于是「从零开始」这件事在工具层就做不到：总得先有人在资源管理器里建个文件夹。
   补 `projects.create()`：建目录、写 README/.gitignore、登记成根，一步到位。

3. **没有验收执行器。**
   学完一章没人能回答「我写对了吗」。补 `checks/<chapter>.py` + `backend/checks.py`：
   子进程跑、带超时、整棵进程树清理、收回结构化结果。
   没有脚本的章如实返回「这章没有自动验收」，绝不假装通过。

## 1. 两种根，不要混

| | 基础根 `base_root()` | 活动根 `active_root()` |
|---|---|---|
| 是什么 | 代码仓库本身 | 这次工具调用能碰到的目录 |
| 谁用 | 备份目录、桌面截图、`projects/` 的父目录 | `resolve()`、`rel_inside()`、git 的 cwd |
| 会变吗 | 不会（测试里可以改） | 每次进/出项目变一次 |

`edits.py` 的备份固定写基础根：项目目录里凭空多个 `backups/` 会污染学生的项目，
而且他一删就等于删掉了回滚点。这条区分是刻意留的，不是漏改。

## 2. 切根只有一个入口

```python
with projects.sandbox(project_id) as project:   # 进项目
    ...                                        # 活动根 = 项目目录
# 退出自动还原
```

`sandbox()` 做三件事：校验项目存在、`register_root` + `use_root`、`finally` 还原。
`use_root()` 拒绝未登记的目录——否则「切根」就等于「任意目录可读写」。

流式对话里，切根必须发生在**流内部**：

```python
def event_stream():
    with projects.sandbox(message.project_id):   # 对
        for event in run_agent_stream(...): ...
```

包住 `return StreamingResponse(...)` 是错的：响应对象构造完就退出了 `with`，
真正迭代时活动根已经还原。页面看起来正常，但工具全都作用在仓库根上——
这是最坏的一种失败，没有任何报错。

## 3. 项目目录

```
projects/<course_id>/<slug>/     # slug 从项目名转，转不出就用章节号
├── README.md                    # 章节任务 + 函数契约 + 怎么自测
├── .gitignore                   # __pycache__ / *.pyc / .venv
└── ...                          # 学生和 agent 写的代码
```

- 名字限制 40 字；`slugify` 只留 `[a-z0-9-]`，中文名转不出就用章节 id 兜底，
  重名自动加 `-2`、`-3`。
- 每章最多 20 个项目，防止「每次跑都新建一个」。
- **不提供删除目录**。`DELETE /projects/{id}` 只删数据库行，磁盘目录保留，
  列表里如实标成「目录不在了」。删文件是不可逆的，而这个功能的用户是刚开始
  写代码的人；把「清理列表」和「删掉我写了三小时的项目」合成一个按钮，
  早晚会有人手滑。
- `projects/` 在 `_guard.BLOCKED_DIRS` 里：从仓库根看过去整棵不可见，
  所以打开 A 项目时读不到 B 项目。切进项目后相对路径里没有这一层，不影响内部读写。

## 4. 验收脚本的契约

`curriculum/<course>/checks/<chapter>.py`，用 `python <script> <项目绝对路径>` 跑，
cwd 就是项目目录。**最后一行**输出：

```json
{"items": [{"name": "基本样例能拆出三个字段", "ok": true, "detail": ""}]}
```

`passed` / `total` 由执行器数——让脚本自己算就会算错。六个决定：

1. **独立进程**。学生代码会崩、会死循环、会 `sys.exit()`。同进程 import 一次
   `while True` 就把后端拖死，而且没法超时。
2. **同一个解释器**（`sys.executable`）。用系统 python 会踩到「学生用 3.11 写的
   语法，验收跑在 3.8 上」这种假失败。
3. **超时杀整棵进程树**。学生项目里 `subprocess.Popen` 起个服务是常见写法，
   只杀父进程会留下孤儿占着端口，下一次验收直接「地址已被占用」。
4. **容忍 stdout 噪音，只认最后一行 JSON**。学生代码里一句 `print("debug")` 是常态，
   要求「只能输出 JSON」等于让第一次验收必然失败；但也只在最后 40 行里找，
   不会把日志里随便一个 `{}` 当结果。
5. **退出码不重要**。非 0 但输出了合法 JSON 也算跑成功——「验收不通过」本身就
   该用非 0 退出码表达。
6. **零条检查不算通过**。脚本坏了、什么都没跑，是最该被看见的失败。

`_kit.py` 提供 `run(project, cases)`：按 `main.py` → `solution.py` → `app.py` 找模块，
用 spec 从文件加载（不走 `sys.path`，避免撞上同名包或旧缓存），每项单独 try。
失败信息只取 traceback 最后一行：倒数第二行是 `raise ...` 的源码，对刚学写代码的
人满屏都是噪音，真正有用的是「parse_react 返回了 list，应该返回字典」。

## 5. 学习模式的权限：有项目才有写和运行

```python
sandbox = bool(project_id)
if mode == "tutor":
    allow_code_edit = sandbox
    allow_command = sandbox
    allow_workspace = False
```

这条写在 `agent.py` 而不是接口里：接口有两个（`/chat` 与 `/chat/stream`），
以后再加第三个时不必记得抄一遍。两个方向都要挡：

- **有项目**：读写工具 + `run_command` 全部挂上，不用用户勾选——学习场景本来就该
  边写边跑；但根是项目目录，仓库整个不可见。
- **没项目**：即使请求里把 `allow_code_edit`、`allow_command` 都打开，也不给文件工具。
  否则导师能读到 `curriculum/` 里的题库和答案。

审批链没有被放松：`python -c` 仍是 ask 级、删除类仍是 deny。自动放行的只有
`python xxx.py` 这种白名单命令——学生项目跑起来靠的就是它。

## 6. 33 章都有验收了

每章一个 `checks/<章>.py` + `checks/<章>.md`。脚本只做两件事之一：

- **验函数**：正文「动手练」里最容易落成代码的那道题（很多章正文直接给了
  函数名，比如 `parse_react`、`resume_after_approval`、`replay_trace`）；
- **验产物文件**：那一道题的输出（`tool_call.json`、`memories.jsonl`、
  `agent_limits.md`……）。

| 章群 | 验的落点 |
|---|---|
| 1 模型原理 s1c1–s1c3 | 中英 token 对比；四份参数对比输出；切两半数 token + 上下文上限记录 |
| 2 提示词 s2c1–s2c3 | 四段式拼装与缺段判断；示例干净度；JSON 提取 + 字段校验 |
| 3 工具调用 s3c1–s3c4 | `tool_call.json`；`parameters` 补全；参数解析校验；规则版选工具 |
| 4 ReAct s4c1–s4c4 | `parse_react`；终止判断；三道护栏；messages 链路还原 |
| 5 记忆 s5c1–s5c3 | `trim_conversation`；jsonl 记忆读写；`bad_memory_example.md` |
| 6 RAG s6c1–s6c4 | 切句 + 完整性；余弦与排序；阈值 top-k + `reranked_chunks.json`；编造检测 |
| 7 流式/中断/审批/日志 s7c1–s7c4 | 逐块转发；取消清临时文件；审批与恢复；trace 回放 |
| 8 评测 s8c1–s8c3 | 失败抽题 + `test_cases.json`；`price_check`；新旧结果对比 + 三份回归产物 |
| 9 多 Agent s9c1–s9c3 | 选型意见；交接冲突检测；`agent_limits.md` 三条红灯 |
| 10 成本与迭代 s10c1–s10c2 | 成本计算与双模型对比；反馈分类与优先级 |

**纯讨论章（s1c2、s5c3、s9c1、s9c3）没有代码产物**，脚本验的是正文明确要求
的那份文件（四个输出 txt / 坏记忆示例 / 选型笔记 / 红灯清单），检查项都落在
「正文说了必须有的东西」上，不额外加要求。

加一章 = 写 `checks/<chapter>.py` + `checks/<chapter>.md`（任务说明，会进 README），
不用改后端。`checks.list_chapters()` 自动发现，前端据此决定显不显示「跑验收」。

`.md` 同时会在项目卡里就地展开（`GET /projects?chapter_id=` 返回章级 `brief`）：
学生建项目之前就能看到这一章要交什么，不用先翻 README。

## 7. 刻意不做的

- **项目级 venv**：为每个项目建 venv 要 10 秒以上，且验收和「学生自己跑」会用两个
  不同的 Python，出问题更难解释。现在的约定是「验收脚本只用标准库」，
  章节练习也据此设计（都不需要第三方包）。
- **需要联网或第三方包的那道题**：`tiktoken`、`sentence-transformers`、真调模型
  的题一律不进验收。文字说明里写清"这题不在此检查"，不假装验过。
- **pip install**：`pip` 不在白名单里，装东西要走用户确认。学习沙箱不该悄悄联网。
- **多语言验收**：契约里的 `sys.executable` 是 Python；要做 Node/Go 得先决定
  「怎么发现运行时」，那是另一个决定。

## 8. 验收（自己怎么确认它没坏）

- `backend/test_checks.py` 174 项：33 章脚本与说明齐全、**每章一份正确实现
  必须通过、一份错误实现必须不通过**、失败项都带原因、空目录不通过。
  双 fixture 是关键——只测"能过"的话，一个永远返回 ok 的脚本也能过。
- `backend/test_sandbox.py` 101 项：切根与还原、未登记目录被拒、建项目（重名/越界/
  非法名）、死循环 5 秒被杀、语法错误不抛异常、沙箱内读写边界、同级项目不可见、
  学习模式有/无项目的工具清单、七个接口的鉴权与错误码、`/chat` 里的切根与提示词。
- `backups/ui_smoke/probe_sandbox.py` 37 项：真浏览器建项目（验「点一次只建一个」）、
  空项目跑验收、写对通过、改坏逐条列出、再点一次取消挂载、刷新后挂载与结果都还在、
  切到没项目的章回到空状态、验收要求在卡片里就地展开且不按正文标题排。
- 全量离线 32 个测试文件全绿。真实 `chats.db` 未写入，全程临时库 + 临时项目目录。
