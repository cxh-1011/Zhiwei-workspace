# AI Robot 改造方案：从「套壳聊天机器人」到「可观测的工具调用 Agent」

> 面向：研一、跨考、agent 开发方向
> 目标：把这套现有网站改造成一个**能自主选工具、能多轮调用、过程可观测、结果可测量**的 agent 系统，并让它成为你后续研究的试验台。
> 用法：按 Phase 顺序做，**每个 Phase 结束必须提交一次 git**。任何一步做坏了，`git reset --hard` 回到上一个 Phase 即可，不会污染已有功能。

---

## 目录

- [第 0 部分：先想清楚三件事](#第-0-部分先想清楚三件事)
- [Phase 1：安全网（git + 配置分离 + 日志）](#phase-1安全网)
- [Phase 2：修 P0 缺陷（上传覆盖 / 文件截断 / 错误吞掉）](#phase-2修-p0-缺陷)
- [Phase 3：工具协议与工具注册表](#phase-3工具协议与工具注册表)
- [Phase 4：把三条链路改写成工具](#phase-4把三条链路改写成工具)
- [Phase 5：Agent 主循环（多轮 + 限制 + 降级）](#phase-5agent-主循环)
- [Phase 6：Trace 落库 + 前端过程展示](#phase-6trace-落库--前端过程展示)
- [Phase 7：短期记忆 → 长期记忆](#phase-7记忆)
- [Phase 8：评测集与实验](#phase-8评测集与实验)
- [附录 A：目标文件结构](#附录-a目标文件结构)
- [附录 B：常见坑清单](#附录-b常见坑清单)
- [附录 C：每个 Phase 的验收清单](#附录-c验收清单)

---

## 第 0 部分：先想清楚三件事

改造之前，你要能对导师/面试官讲清楚下面三句话。讲不清楚，代码写得再多也是"又一个 chatbot"。

### 1. 你现在的系统不是 agent，是一个"手写分支的聊天程序"

看 `backend/main.py:331`：

```python
need_search_words = ["新闻", "今天", "最新", "最近", "现在", "价格", "天气", "股票", "比赛"]
need_search = any(word in message.text for word in need_search_words)
```

**是你在决定什么时候联网，不是模型在决定。** 这是 if-else，不是工具调用。判定标准的区别：

| | 手写分支（现在） | Agent（目标） |
|---|---|---|
| 谁决定用不用工具 | 你的关键词列表 | 模型 |
| 谁决定用什么参数 | 你（把整句原文丢过去） | 模型（提取 query） |
| 能不能连续调多次 | 不能 | 能 |
| 拿到结果后能不能再决策 | 不能 | 能 |
| 失败时怎么办 | 你没写 | 循环有降级路径 |

### 2. 别让模型"输出 JSON 然后你用正则去解析"

你已经踩过这个坑了。看 `backend/main.py:48` 那段 `[MUSIC_CARD]` 格式约定，以及前端 `app.js:1553` 为了识别它写的 `isMusicReply()`——**这就是 prompt 层 hack 的典型代价：格式靠约定，解析靠猜，模型稍微不听话整个功能就废。**

正确做法是**原生 function calling**：工具用 JSON Schema 声明，模型返回结构化的 `tool_calls`，由 SDK 保证格式。千问通过 OpenAI 兼容模式原生支持：

```python
from openai import OpenAI
client = OpenAI(
    api_key=DASHSCOPE_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",  # 国内
    # 国际站用 https://dashscope-intl.aliyuncs.com/compatible-mode/v1
)
```

音乐推荐应该变成一个**工具**（`recommend_music`），模型调用它、拿到结构化数据，前端根据结构化数据渲染卡片——而不是让模型自己拼 `[MUSIC_CARD]` 文本。

### 3. 你的论文素材在 trace 里，不在模型里

"我做了一个 agent"没有研究价值。"我测量了 qwen 系列在四类工具上的选择错误率，发现 70% 的失败发生在多轮调用的第 2 轮"——这才有价值。

**所以 Phase 6 的 trace，不是前端美化，是你实验数据的采集装置。** 它必须落库、必须结构化、必须能导出成表格。前端时间线只是它的一种展示形式。

---

## Phase 1：安全网

> 目标：在动任何业务代码之前，先获得"随时可回滚"的能力。

### 1.1 初始化 git

你项目里**目前没有版本控制**（我确认过 `git rev-parse` 失败），只有一个手写的 `backend/main_backup.py`。这会变成 `main_backup2.py`、`main_backup_最终版.py`。今天就用 git 替掉它。

```powershell
cd D:\hy\ai\ai_robot
git init
git branch -M main
```

### 1.2 先写 `.gitignore`（**顺序不能反**）

**在第一次 `git add` 之前**必须先有 `.gitignore`。否则 `.env`（含 DashScope/Tavily 密钥、登录密码哈希）和 `chats.db`（用户聊天记录）会被提交进仓库。跨考生最容易踩这个坑。

在项目根目录建 `.gitignore`：

```gitignore
# ===== 密钥与敏感配置（绝对不能提交）=====
.env
*.env
backend/.env
!*.env.example

# ===== 数据库与用户数据 =====
*.db
*.sqlite
*.sqlite3
backend/chats.db

# ===== 用户上传的文件（含隐私）=====
backend/uploads/*
!backend/uploads/.gitkeep

# ===== 向量索引产物（可由论文重新生成，体积大）=====
backend/paper_index/
*.faiss
*.pkl

# ===== Python =====
.venv/
venv/
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.mypy_cache/

# ===== 日志与临时文件 =====
*.log
logs/
trace_export/
*.tmp

# ===== 编辑器 / 系统 =====
.vscode/
.idea/
.DS_Store
Thumbs.db

# ===== 我自己的备份文件（用 git 代替它）=====
*_backup.py
*_backup2.py
*_old.py
```

同时建一个 `.env.example`（**可以提交**，给别人看你需要哪些配置，但不含真值）：

```dotenv
# 复制成 .env 后填入真实值
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxxxx
APP_PASSWORD_HASH=请用脚本生成
APP_SECRET_KEY=请用脚本生成
APP_COOKIE_SECURE=false

# Agent 循环限制（Phase 5 使用）
AGENT_MAX_ROUNDS=5
AGENT_TOTAL_TIMEOUT=45
AGENT_MAX_TOKENS_PER_RUN=20000
```

### 1.3 清理并首次提交

```powershell
# 先把备份文件挪走（git 会忽略它，但眼不见心不烦）
Remove-Item backend\main_backup.py

git add -A
git status          # ⚠️ 仔细看这个列表里有没有 .env / chats.db / uploads 里的文件
git commit -m "chore: 初始化版本控制，建立改造基线"
```

**验收**：`git status` 里看不到 `.env`、`chats.db`、`backend/uploads/` 下的任何真实文件。如果看到了，说明 `.gitignore` 写错了，`git rm --cached <文件>` 后再改。

### 1.4 定一个提交规范（后面每个 Phase 都用到）

```
<类型>: <做了什么>
类型：feat 新功能 / fix 修缺陷 / refactor 重构 / docs 文档 / chore 杂项
```

例如 `refactor: 抽出工具协议与注册表`。这样你回头 `git log` 就是一份现成的改造日志——**写论文的"系统实现"章节可以直接从这里抄**。

### 1.5 打标签标记基线

```powershell
git tag v0.1.0-baseline
```

以后任何时候 `git diff v0.1.0-baseline` 就能看到你所有改动的总览。这是你"回滚版本"的锚点。

---

## Phase 2：修 P0 缺陷

> 目标：清掉会在后面 debug agent 循环时干扰你的噪声。这些改动都**不改变接口形状**，前端不用动。

### 2.1 加日志（最重要）

现在 `backend/main.py:472`：

```python
except Exception as e:
    raise HTTPException(status_code=502, detail="AI 对话暂时不可用，请稍后重试。") from e
```

线上你只会看到"不可用"。是超时？欠费？模型名写错？限流？**你无从知道。** 加日志：

新建 `backend/logging_setup.py`：

```python
import logging
import sys
from logging.handlers import RotatingFileHandler
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")


def setup_logging(level=logging.INFO):
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)

    # 单文件 5MB，保留 5 个，防止日志把磁盘写满
    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "app.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(stream)
    root.addHandler(file_handler)
```

在 `main.py` 顶部（import 之后）加：

```python
from logging_setup import setup_logging
import logging

setup_logging()
logger = logging.getLogger("ai_robot")
```

然后把吞异常的地方改成：

```python
except Exception:
    logger.exception("chat 调用失败 | chat_id=%s | 输入=%r", message.chat_id, message.text[:80])
    raise HTTPException(status_code=502, detail="AI 对话暂时不可用，请稍后重试。")
```

三个位置都要改：`chat()`（`main.py:472`）、`chat_file()`（`main.py:772`）、`paper_chat()`（`main.py:613` 现在**完全没有 try/except**，异常直接 500 HTML，前端 `parseJson` 会拿到 `_raw`）。

### 2.2 修上传文件互相覆盖

现在（`main.py:556` 和 `main.py:589`）：

```python
path = os.path.join(UPLOAD_DIR, filename)
```

两个人传 `paper.pdf` 直接覆盖。改成 uuid 前缀存盘 + 保留原始显示名：

新建 `backend/storage.py`：

```python
import os
import uuid

import logging

logger = logging.getLogger("ai_robot")

ALLOWED_EXT = {".txt", ".pdf", ".docx"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024      # 普通文件 20MB
MAX_PAPER_BYTES = 50 * 1024 * 1024       # 论文 50MB
MAX_READ_CHARS = 60_000                  # 读进 prompt 的字符上限


def safe_display_name(name: str) -> str:
    """只保留文件名部分，去掉路径和危险字符"""
    base = os.path.basename(name or "").strip()
    base = base.replace("\x00", "")
    return base or "unnamed"


def save_upload(content: bytes, original_name: str, upload_dir: str) -> dict:
    display = safe_display_name(original_name)
    ext = os.path.splitext(display)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("仅支持 TXT、PDF 和 DOCX 文件。")

    # 磁盘文件名用 uuid 前缀，杜绝覆盖；原始名存库供展示
    stored = f"{uuid.uuid4().hex}_{display}"
    path = os.path.join(upload_dir, stored)

    with open(path, "wb") as f:
        f.write(content)

    logger.info("上传落盘 | 原名=%s | 存储名=%s | 大小=%d", display, stored, len(content))
    return {"stored_name": stored, "display_name": display, "path": path, "size": len(content)}


def read_text_from_file(path: str, max_chars: int = MAX_READ_CHARS) -> str:
    """按扩展名解析文本，并强制截断，避免把 token 打爆"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read(max_chars + 1)
    elif ext == ".pdf":
        import pymupdf
        doc = pymupdf.open(path)
        parts, total = [], 0
        for page in doc:
            piece = page.get_text()
            parts.append(piece)
            total += len(piece)
            if total > max_chars:
                break
        doc.close()
        text = "".join(parts)
    elif ext == ".docx":
        from docx import Document
        doc = Document(path)
        parts, total = [], 0
        for p in doc.paragraphs:
            parts.append(p.text)
            total += len(p.text)
            if total > max_chars:
                break
        text = "\n".join(parts)
    else:
        raise ValueError("目前只支持读取 TXT、PDF 和 DOCX 文件。")

    if not text.strip():
        raise ValueError("没有从文件中读取到可用文字。扫描版 PDF 请先进行 OCR。")

    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[内容过长，已截断]"

    return text
```

然后把 `main.py` 的 `/upload` 和 `/upload_paper` 改成调用 `save_upload()`，`read_file()` 改成调用 `read_text_from_file()`。

**注意**：改成 uuid 存盘后，前端传回来的 `filename` 是**存储名**，`/chat_file` 和 `/files` 都要以存储名为准。为了不破坏前端，最省事的做法是让 `/files` 返回存储名列表（现在就是这么做的），前端显示略丑但功能正常。想显示原始名的话，Phase 3 顺手加一张 `files` 表存 `stored_name ↔ display_name` 映射。

### 2.3 给 DashScope 调用加超时和重试

现在 `Generation.call()` 没有任何超时控制（`main.py:425`、`main.py:752`、`paper_agent.py:209`）。Phase 5 的多轮循环里，一次卡住会吃掉整个预算。统一封装：

新建 `backend/llm.py`：

```python
import logging
import time

from openai import OpenAI, APITimeoutError, APIConnectionError, RateLimitError

from config import DASHSCOPE_API_KEY

logger = logging.getLogger("ai_robot")

MODEL = "qwen-plus"

# 千问的 OpenAI 兼容模式，原生支持 tools / tool_calls
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

client = OpenAI(api_key=DASHSCOPE_API_KEY, base_url=BASE_URL, timeout=30.0, max_retries=0)


def chat_completion(messages, tools=None, tool_choice=None, temperature=0.7, max_retries=2):
    """带超时、重试、日志的一次模型调用。返回 (message, usage)"""
    last_err = None

    for attempt in range(max_retries + 1):
        started = time.time()
        try:
            kwargs = {"model": MODEL, "messages": messages, "temperature": temperature}
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = tool_choice or "auto"

            resp = client.chat.completions.create(**kwargs)
            cost_ms = int((time.time() - started) * 1000)

            usage = getattr(resp, "usage", None)
            logger.info(
                "LLM 调用成功 | 耗时=%dms | 尝试=%d | tokens=%s",
                cost_ms, attempt + 1,
                getattr(usage, "total_tokens", "?"),
            )
            return resp.choices[0].message, usage

        except (APITimeoutError, APIConnectionError, RateLimitError) as e:
            last_err = e
            wait = 1.5 ** attempt
            logger.warning("LLM 调用失败 | 类型=%s | 尝试=%d | %.1fs 后重试", type(e).__name__, attempt + 1, wait)
            time.sleep(wait)

        except Exception:
            logger.exception("LLM 调用异常（不重试）")
            raise

    logger.error("LLM 重试耗尽 | 最后错误=%s", last_err)
    raise last_err
```

**验收**：本地的 `/chat` 依然能正常回话，只是底层从 `dashscope.Generation` 换成了 OpenAI 兼容模式。这一步单独测通再往下走——**换模型 SDK 是最容易连带炸的地方，不要和 Phase 3 一起做。**

**提交**：`fix: 加日志、修上传覆盖、文件截断、LLM 调用超时重试`

---

## Phase 3：工具协议与工具注册表

> 目标：定义一个"加新工具只要写一个文件"的协议。这是整个改造的地基。

### 3.1 工具协议

新建 `backend/tools/base.py`：

```python
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolResult:
    """工具的统一返回。永远不要 raise 给上层，用 ok=False 表达失败。"""
    ok: bool
    content: str                 # 给模型看的文本
    data: dict = field(default_factory=dict)   # 给前端/日志看的结构化数据
    error: str = ""

    def as_tool_message(self) -> str:
        """转成回给模型的 tool 消息内容。失败也要给模型一个可读的说明，让它有机会改用别的工具。"""
        if self.ok:
            return self.content
        return f"工具执行失败：{self.error}。请改用其他方式，或如实告知用户你无法获取该信息。"


@dataclass
class Tool:
    name: str
    description: str          # ⚠️ 模型只靠这句话决定用不用这个工具，写得越具体越好
    parameters: dict          # JSON Schema（object 类型）
    func: Callable[..., ToolResult]

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(self, **kwargs) -> ToolResult:
        try:
            result = self.func(**kwargs)
            if not isinstance(result, ToolResult):
                return ToolResult(ok=True, content=str(result))
            return result
        except TypeError as e:
            # 模型经常漏参数或传错参数名，这是最常见失败之一，要单独记
            return ToolResult(ok=False, content="", error=f"参数不正确：{e}")
        except Exception as e:
            return ToolResult(ok=False, content="", error=f"{type(e).__name__}: {e}")
```

**三条铁律**（后面每个工具都遵守）：

1. **一个工具只做一件事**。"检索"和"读取"要分开，别做 `search_and_read`。
2. **参数必须是模型能从用户话里提取出来的**。让模型传"文档ID"是合理的（它能看到文档列表工具的输出），让它传"行号"就不合理。
3. **失败返回 `ok=False`，不抛异常**。抛异常会中断循环，返回失败能让模型自己换策略——这本身就是 agent 能力的体现，也是你论文里的"恢复能力"指标。

### 3.2 注册表

新建 `backend/tools/registry.py`：

```python
import logging

from tools.base import Tool

logger = logging.getLogger("ai_robot")

_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if tool.name in _REGISTRY:
        raise ValueError(f"工具名重复：{tool.name}")
    _REGISTRY[tool.name] = tool
    logger.info("注册工具 | %s | 参数=%s", tool.name, list(tool.parameters.get("properties", {})))
    return tool


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def all_schemas() -> list[dict]:
    return [t.schema() for t in _REGISTRY.values()]


def tool_names() -> list[str]:
    return list(_REGISTRY.keys())
```

新建 `backend/tools/__init__.py`：

```python
"""工具包。import 这个包即完成所有工具注册。"""
from tools.registry import register, get_tool, all_schemas, tool_names  # noqa: F401

# ⚠️ 顺序无所谓，但必须 import 到，否则工具不会注册
from tools import web_search      # noqa: F401
from tools import paper_search    # noqa: F401
from tools import file_reader     # noqa: F401
from tools import music           # noqa: F401
```

**关键**：`all_schemas()` 是唯一入口。以后加工具只改 `tools/` 目录，`main.py` 一行都不用动。这个设计本身就可以写进论文的"系统架构"。

**提交**：`feat: 工具协议与工具注册表`

---

## Phase 4：把三条链路改写成工具

> 目标：把你现有的三个功能（联网、论文、文件）加音乐，全部变成工具。**这一步只是"搬家"，先别改调用逻辑**，保证每个工具能单独调用成功。

### 4.1 `tools/web_search.py`（Tavily）

```python
import logging

from tavily import TavilyClient

from config import TAVILY_API_KEY
from tools.base import Tool, ToolResult
from tools.registry import register

logger = logging.getLogger("ai_robot")

_client = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None

DESCRIPTION = """联网搜索最新信息。
适用：新闻、时事、价格、天气、股票、比赛结果、近期发布的产品或论文、任何你的训练数据里没有的时效性信息。
不适用：用户已上传的文件内容（用 file_reader）、已上传的论文内容（用 paper_search）、闲聊与常识。
参数 query 应写成一句独立的检索式，而不是照抄用户的整句话。"""

PARAMS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "检索关键词或短句，例如「2026年 ACL 会议 截稿时间」",
        },
        "max_results": {
            "type": "integer",
            "description": "返回条数，1-5，默认 3",
            "minimum": 1,
            "maximum": 5,
        },
    },
    "required": ["query"],
}


def _run(query: str, max_results: int = 3) -> ToolResult:
    if _client is None:
        return ToolResult(ok=False, content="", error="未配置 TAVILY_API_KEY，联网功能不可用")

    resp = _client.search(query=query, max_results=max_results)
    items = resp.get("results", [])
    if not items:
        return ToolResult(ok=True, content="没有搜索到相关结果。", data={"query": query, "count": 0})

    blocks = []
    for i, item in enumerate(items, 1):
        blocks.append(
            f"[{i}] {item.get('title', '')}\n"
            f"URL: {item.get('url', '')}\n"
            f"摘要: {item.get('content', '')[:600]}"
        )
    content = "\n\n".join(blocks)

    return ToolResult(
        ok=True,
        content=content,
        data={"query": query, "count": len(items),
              "urls": [i.get("url") for i in items]},
    )


register(Tool(name="web_search", description=DESCRIPTION, parameters=PARAMS, func=_run))
```

### 4.2 `tools/paper_search.py`（论文检索）

**这一步要顺带修掉一个设计缺陷**：现在 `paper_agent.py:32` 的 `db = None` 是全局单例，所有人共用一份索引，A 上传论文 B 提问会读到 A 的论文。改成"多篇论文 + 按论文隔离"。

先加一个论文清单表（在 `database.py` 里）：

```sql
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    index_dir TEXT NOT NULL,       -- 每篇论文一个独立索引目录
    chunks INTEGER DEFAULT 0,
    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

修改 `paper_agent.py`：`load_paper()` 接收 `paper_id`，索引存到 `paper_index/<paper_id>/`；`ask_paper()` 改成 `search_paper(paper_id, question, k)`，缓存改成 `dict[int, FAISS]` 而不是单个全局变量。

然后工具拆成**两个**（这是刻意的，为了制造多轮调用的真实场景）：

```python
# tools/paper_search.py

DESCRIPTION_LIST = """列出用户已上传的论文。
适用：用户提到「我的论文」「那篇论文」但没指明是哪一篇时，先用这个工具看清有哪些论文可选。"""

DESCRIPTION_SEARCH = """在指定的某一篇已上传论文里检索内容并返回原文片段。
适用：用户的问题需要依据论文原文回答。
前置：必须先知道 paper_id。如果不知道，先调用 list_papers。
不适用：用户已上传的普通文件（用 file_reader）、公开的联网信息（用 web_search）。"""

PARAMS_SEARCH = {
    "type": "object",
    "properties": {
        "paper_id": {"type": "integer", "description": "论文编号，来自 list_papers 的返回"},
        "question": {"type": "string", "description": "要在论文中检索的问题"},
        "k": {"type": "integer", "description": "返回片段数，默认 3，最大 6",
              "minimum": 1, "maximum": 6},
    },
    "required": ["paper_id", "question"],
}
```

**为什么要拆成两个**：`list_papers` → `paper_search` 这个两跳结构，是**天然的多轮工具调用测试用例**。你可以直接测："模型会不会在不知道 paper_id 时先调 list_papers？还是直接瞎编一个 id？" 这个数据点比联网搜索有意思得多。

### 4.3 `tools/file_reader.py`（文件检索）

**注意**：你现在的 `chat_file` 是把整个文件塞进 prompt。改成工具后要做**分块检索**而不是整文件投喂，否则上下文必爆。

最小可用版（不引入 embedding，先用关键词/分块 + 让模型自己多看几块）：

```python
PARAMS = {
    "type": "object",
    "properties": {
        "filename": {"type": "string", "description": "文件名，来自 list_files 的返回"},
        "question": {"type": "string", "description": "要在这份文件中查找的内容"},
        "start_chunk": {"type": "integer", "description": "从第几块开始读（用于翻页查看后续内容），默认 0"},
    },
    "required": ["filename", "question"],
}
```

同样拆出 `list_files` 工具。返回内容里带上 `第 X-Y 块 / 共 N 块`，让模型知道还能不能继续翻——**这样"该继续却停了"这个失败模式才会出现，你才有数据可测。**

### 4.4 `tools/music.py`（音乐推荐）

这是用来**替换掉 `[MUSIC_CARD]` 那套 hack 的**：

```python
DESCRIPTION = """根据用户的情绪、场景推荐歌曲。
适用：用户表达情绪（难过、压力大、失眠）、要求推荐歌单、问学习/运动时听什么。
参数 mood 填用户描述的情绪或场景，count 默认 3。"""

PARAMS = {
    "type": "object",
    "properties": {
        "mood": {"type": "string", "description": "用户的情绪或使用场景，例如「焦虑」「深夜学习」"},
        "count": {"type": "integer", "description": "推荐几首，默认 3", "minimum": 1, "maximum": 5},
    },
    "required": ["mood"],
}
```

工具内部调模型生成结构化歌曲列表，返回 `ToolResult(ok=True, data={"songs": [{"song": ..., "singer": ..., "reason": ...}]})`。

**前端的改动**（这才是正确的分层）：
- 删掉 `app.js` 里的 `isMusicReply()` 和 `parseMusicSongs()`——不用再靠正则猜了。
- 前端拿到 `trace` 里最后一条 `recommend_music` 工具的结构化 `data.songs`，直接渲染卡片。
- `SYSTEM_PROMPT` 里那一大段 `[MUSIC_CARD]` 格式约定**整段删掉**。

**验收**：写一个 `backend/scratch_test_tools.py`，手动逐个调用四个工具，确认都能返回 `ok=True`。**先不接循环。**

**提交**：`refactor: 三条链路改写为独立工具，音乐卡片改为工具结构化返回`

---

## Phase 5：Agent 主循环

> 目标：让模型自己决定调什么工具、调几次。这是本次改造的核心。

### 5.1 系统提示词

`SYSTEM_PROMPT` 要**大幅缩短**。工具调用的世界里，提示词只负责"人设 + 行为准则"，工具的描述在 schema 里：

```python
SYSTEM_PROMPT = """你是一个严谨的 AI 助手，可以调用工具来获取你不掌握的信息。

工作要求：
1. 需要外部信息时调用工具，不要凭记忆编造事实。
2. 一次只解决一件事；需要多个信息就分多次调用。
3. 调用工具前，先确认参数是否齐全。缺参数时先用能获取该参数的工具（例如先 list_papers 再 paper_search）。
4. 工具返回失败时，可以换一种方式重试一次；仍失败就如实告诉用户你没能获取到信息。
5. 回答使用中文，基于工具返回的内容作答，并说明信息来源（论文名 / 文件名 / 网址）。
6. 不需要工具就能回答的问题，直接回答，不要为了用工具而用工具。"""
```

**第 6 条很重要**：没有它，模型会倾向于对"你好"这种话也去联网搜索。这个失败模式叫**过度调用（over-calling）**，是你要测的指标之一。

### 5.2 循环本体（可直接用）

新建 `backend/agent.py`：

```python
import json
import logging
import os
import time
import uuid

from llm import chat_completion
from tools import all_schemas, get_tool
import tools as tools_pkg  # noqa: F401  确保工具已注册

logger = logging.getLogger("ai_robot")

MAX_ROUNDS = int(os.getenv("AGENT_MAX_ROUNDS", "5"))
TOTAL_TIMEOUT = float(os.getenv("AGENT_TOTAL_TIMEOUT", "45"))
MAX_TOOL_CALLS_PER_ROUND = 3


class BudgetExceeded(Exception):
    pass


def _estimate_tokens(text: str) -> int:
    """粗略估算：中文按 1 字符 ≈ 1 token，英文按 4 字符 ≈ 1 token。只用来做预算保护，不要求精确。"""
    if not text:
        return 0
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    other = len(text) - ascii_chars
    return int(ascii_chars / 4) + other


class RunRecorder:
    """收集一次完整 run 的 trace。Phase 6 会把它落库。"""

    def __init__(self, run_id: str, question: str):
        self.run_id = run_id
        self.question = question
        self.steps: list[dict] = []
        self.started = time.time()
        self.total_tokens = 0
        self.status = "running"     # running / ok / max_rounds / timeout / error

    def add(self, round_no: int, kind: str, **payload):
        self.steps.append({
            "run_id": self.run_id,
            "round_no": round_no,
            "step_index": len(self.steps),
            "kind": kind,                 # llm_call / tool_call / final / limit
            "elapsed_ms": int((time.time() - self.started) * 1000),
            **payload,
        })

    def finish(self, status: str):
        self.status = status
        self.duration_ms = int((time.time() - self.started) * 1000)
        return self


def run_agent(question: str, history: list[dict] | None = None, system_prompt: str = None) -> dict:
    """
    多轮工具调用循环。
    返回 {"reply": str, "run_id": str, "steps": [...], "status": str, "rounds": int}
    """
    from main import SYSTEM_PROMPT  # 或把 SYSTEM_PROMPT 移到独立模块避免循环 import

    run_id = uuid.uuid4().hex
    recorder = RunRecorder(run_id, question)

    messages = [{"role": "system", "content": system_prompt or SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": question})

    schemas = all_schemas()
    deadline = time.time() + TOTAL_TIMEOUT

    reply = ""
    status = "ok"
    round_no = 0

    try:
        for round_no in range(1, MAX_ROUNDS + 1):

            if time.time() > deadline:
                status = "timeout"
                recorder.add(round_no, "limit", reason="总时长超限", detail=f"{TOTAL_TIMEOUT}s")
                break

            # 每轮都按预算裁剪历史，防止上下文滚雪球
            messages = _trim_messages(messages)

            message, usage = chat_completion(messages, tools=schemas, tool_choice="auto")
            if usage:
                recorder.total_tokens += getattr(usage, "total_tokens", 0) or 0

            tool_calls = getattr(message, "tool_calls", None)

            # ── 情况 1：模型不调工具，直接给答案 → 正常结束
            if not tool_calls:
                reply = message.content or ""
                recorder.add(round_no, "final", content=reply,
                             tokens=recorder.total_tokens)
                status = "ok"
                break

            # ── 情况 2：模型要调工具
            calls = tool_calls[:MAX_TOOL_CALLS_PER_ROUND]
            if len(tool_calls) > MAX_TOOL_CALLS_PER_ROUND:
                recorder.add(round_no, "limit", reason="单轮工具数超限",
                             detail=f"模型请求 {len(tool_calls)} 个，只执行前 {MAX_TOOL_CALLS_PER_ROUND} 个")

            # 必须把 assistant 的这次 tool_calls 原样放回历史，否则模型会重复调用
            messages.append({
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.function.name,
                                     "arguments": c.function.arguments},
                    }
                    for c in calls
                ],
            })

            for call in calls:
                name = call.function.name
                started = time.time()

                # 解析参数：模型偶尔会给出非法 JSON
                try:
                    args = json.loads(call.function.arguments or "{}")
                    parse_error = ""
                except json.JSONDecodeError as e:
                    args, parse_error = {}, f"参数不是合法 JSON：{e}"

                tool = get_tool(name)

                if parse_error:
                    result_content = f"工具执行失败：{parse_error}"
                    ok, data = False, {}
                elif tool is None:
                    result_content = f"工具执行失败：不存在名为 {name} 的工具。可用工具：{tools_pkg.tool_names()}"
                    ok, data = False, {}
                    logger.error("模型调用了不存在的工具 | %s", name)
                else:
                    result = tool.run(**args)
                    result_content = result.as_tool_message()
                    ok, data = result.ok, result.data

                elapsed = int((time.time() - started) * 1000)
                recorder.add(round_no, "tool_call", tool=name, args=args,
                             ok=ok, elapsed_ms=elapsed,
                             result_preview=result_content[:300],
                             result_data=data)

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": name,
                    "content": result_content,
                })

        else:
            # for 循环跑满都没 break → 到达轮数上限
            status = "max_rounds"
            recorder.add(MAX_ROUNDS, "limit", reason="达到最大轮数",
                         detail=f"上限 {MAX_ROUNDS} 轮")
            reply = _force_final_answer(messages)

    except Exception:
        logger.exception("agent 循环异常 | run_id=%s", run_id)
        status = "error"
        reply = "抱歉，处理过程中出现异常，请稍后重试。"

    recorder.finish(status)
    return {
        "reply": reply,
        "run_id": run_id,
        "steps": recorder.steps,
        "status": status,
        "rounds": round_no,
        "duration_ms": recorder.duration_ms,
        "total_tokens": recorder.total_tokens,
    }


def _trim_messages(messages: list[dict], budget_tokens: int = 6000) -> list[dict]:
    """保留 system + 最近的消息，按预算裁剪。注意：不能把 tool 消息和它对应的 assistant.tool_calls 拆散。"""
    if sum(_estimate_tokens(m.get("content") or "") for m in messages) <= budget_tokens:
        return messages

    system = [m for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]

    kept, used = [], 0
    # 从最新往回保留
    for m in reversed(rest):
        cost = _estimate_tokens(m.get("content") or "")
        if used + cost > budget_tokens:
            break
        kept.append(m)
        used += cost
    kept.reverse()

    # 安全修正：如果开头是 tool 消息，说明它的 assistant 请求被裁掉了，一并丢弃
    while kept and kept[0]["role"] == "tool":
        kept.pop(0)

    return system + kept


def _force_final_answer(messages: list[dict]) -> str:
    """到达轮数上限时，禁止再用工具，逼模型基于已有信息作答（降级路径）。"""
    messages = messages + [{
        "role": "user",
        "content": "已达到工具调用上限。请立刻基于你已经获得的信息给出最终回答；信息不足的部分请明确说明。不要再请求调用工具。",
    }]
    try:
        message, _ = chat_completion(messages, tools=None)   # 注意：不传 tools
        return message.content or "（未能生成回答）"
    except Exception:
        logger.exception("强制收尾失败")
        return "抱歉，这次问题需要的信息太多，我没能在限定步数内完成。请把问题拆小一点再问，或者直接指明用哪个工具。"
```

### 5.3 三个限制分别防什么

| 限制 | 环境变量 | 防的失败模式 | 触发后的行为 |
|---|---|---|---|
| 最大轮数 | `AGENT_MAX_ROUNDS=5` | **死循环 / 反复调同一个工具** | 禁用工具，逼它用现有信息作答 |
| 总时长 | `AGENT_TOTAL_TIMEOUT=45` | 某次工具卡住吃掉整个请求 | 中断循环，返回已有结果 |
| 单轮工具数 | 常量 3 | 模型一次要调 8 个工具 | 只执行前 3 个，记一条 limit 日志 |

**这三个限制都是环境变量/常量，就是为了后面做实验调它们。** 不要写死在业务逻辑里。

### 5.4 接进 FastAPI

`main.py` 的 `/chat` 改成：

```python
@app.post("/chat")
def chat(message: Message):
    try:
        history = load_recent_history(message.chat_id, budget_tokens=2000)  # Phase 7 实现
        result = run_agent(message.text, history=history)

        update_chat_title(message.chat_id, message.text[:15])
        save_message(message.chat_id, "user", message.text)
        save_message(message.chat_id, "ai", result["reply"])

        save_trace(result)      # Phase 6 实现

        return {
            "reply": result["reply"],
            "run_id": result["run_id"],
            "status": result["status"],       # 前端可据此提示"达到步数上限"
            "steps": result["steps"],         # 首版先一次性返回全部步骤
        }
    except Exception:
        logger.exception("chat 失败 | chat_id=%s", message.chat_id)
        raise HTTPException(status_code=502, detail="AI 对话暂时不可用，请稍后重试。")
```

`/paper_chat` 和 `/chat_file` 这时可以**先保留不动**（前端还在用），新功能走 `/chat`。等 Phase 6 前端改完再删——这样你随时能退回去。

**验收**：
```powershell
# 用一个需要联网的问题测
curl.exe -s -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" -d "{\"text\":\"今天有什么AI新闻\",\"chat_id\":1}"
```
返回的 JSON 里 `steps` 应该至少有 1 条 `tool_call`（`web_search`），`status` 是 `ok`，`rounds` 是 2。**多试几个不触发工具的问题（如"你好"），确认 `rounds` 是 1 且没有 tool_call——这是在测过度调用。**

**提交**：`feat: agent 多轮工具调用循环，含轮数/时长/工具数三重限制与降级`

---

## Phase 6：Trace 落库 + 前端过程展示

> 目标：把过程变成**数据**。前端只是展示器，数据才是资产。

### 6.1 建 trace 表

在 `database.py` 的 `init_db()` 里加：

```sql
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    chat_id INTEGER,
    question TEXT,
    status TEXT,               -- ok / max_rounds / timeout / error
    rounds INTEGER,
    duration_ms INTEGER,
    total_tokens INTEGER,
    reply TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step_index INTEGER,
    round_no INTEGER,
    kind TEXT,                 -- llm_call / tool_call / final / limit
    tool TEXT,
    args TEXT,                 -- JSON 字符串
    ok INTEGER,
    elapsed_ms INTEGER,
    result_preview TEXT,
    result_data TEXT,          -- JSON 字符串，前端渲染音乐卡片等用它
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_steps_run ON agent_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_chat ON agent_runs(chat_id);
```

然后实现 `save_trace(result)` 和 `get_trace(run_id)`。加两个接口：

```python
@app.get("/trace/{run_id}")
def trace(run_id: str):
    return {"data": get_trace(run_id)}

@app.get("/messages_with_trace/{chat_id}")
def messages_with_trace(chat_id: int):
    """打开历史会话时，能连每一步的过程一起还原"""
    ...
```

### 6.2 关于"实时过程展示"

你想做前端**边跑边显示步骤**。两条路：

**路线 A（推荐先做）：轮询**
- 前端 POST `/chat` 时带上 `run_id`（或后端生成后立刻返回）
- 后端把步骤写进内存字典 `RUNNING[run_id] = recorder`，加 `GET /trace_live/{run_id}` 返回当前进度
- 前端每 600ms 轮询一次，渲染增量

优点：**改后端 20 行，nginx 不用动，不会有 SSE 的缓冲坑。** 上线部署是最省事的。

**路线 B（有余力再做）：SSE**
- `StreamingResponse` + `text/event-stream`
- 注意 nginx 要配 `proxy_buffering off;`，否则事件会在代理层被缓冲住，前端看起来还是"一次性出现"——**这个坑很常见，别在部署日踩。**

建议：**Phase 6 用轮询把功能做出来，SSE 作为 Phase 8 之后的优化项。** 先能展示，再谈实时。

### 6.3 前端时间线（UI 建议）

在 AI 回复上方加一个可折叠的 `<details>` 区块，标题是状态摘要，内容是步骤列表：

```
▸ ⚙ 调用 2 个工具 · 3 轮 · 2.4s                    [展开]
```

展开后：

```
第 1 轮  🧠 思考中…
        🔧 web_search
           参数: {"query": "2026年AI新闻", "max_results": 3}
           ✓ 3 条结果 · 820ms
第 2 轮  ✅ 生成回答
```

配色规则（有心理学效果，用户一眼就知道出没出问题）：
- `ok=True` 的工具 → 绿色勾
- `ok=False` → 琥珀色警告 + 错误原因
- `limit` 类型的步骤 → 灰色，显示"达到最大轮数"
- `status != "ok"` 时，回复末尾加一行小字提示，例如"⚠️ 本次达到 5 步上限，回答可能不完整"

**关键要求**：这些步骤必须来自后端返回的 `steps`，前端**不允许自己编**。这样你抓到的 bug 才是真实的。

**提交**：`feat: trace 落库与前端过程时间线`

---

## Phase 7：记忆

> 顺序很重要：**先短期，后长期。** 跳过短期直接做长期记忆，等于在没有地基的地方盖二楼。

### 7.1 短期记忆（先做这个）

你现在**根本没有把历史喂回模型**——`main.py:407` 的 prompt 里只有当前这一句。前端看起来"记得"，只是因为把历史画在评论区里了。

实现：

```python
def load_recent_history(chat_id: int, budget_tokens: int = 2000) -> list[dict]:
    """取最近若干条消息，拼成 messages 格式，按预算裁剪"""
    rows = get_messages(chat_id)        # 现在没有 LIMIT，要加上
    messages, used = [], 0
    for role, content in reversed(rows):
        cost = _estimate_tokens(content)
        if used + cost > budget_tokens:
            break
        messages.append({"role": "assistant" if role == "ai" else "user",
                         "content": content})
        used += cost
    messages.reverse()
    return messages
```

**这一步本身就是一个可做的实验**：对比"无历史 / 最近 5 轮 / 按 token 预算裁剪"三种策略对回答质量的影响。

### 7.2 长期记忆（后做这个）

长期记忆要回答三个问题，缺一个都做不成：

1. **存什么**？不是存整段对话。存"事实/偏好"，例如"用户在写关于多轮工具调用的论文""用户偏好中文回答""用户的项目用 FastAPI"。让模型在每轮结束前判断"这句里有没有值得长期记住的信息"，有就调用 `remember(content, category)` 工具——**对，长期记忆也应该是一个工具**，这样它自动纳入你的 trace 和评测体系。
2. **怎么取**？向量检索（你已经有 FAISS 和 DashScope embedding 的经验，直接复用）。检索到 top-k 相关记忆，拼到 system 消息后面。
3. **怎么知道它有用**？（最重要，也最容易被忽略）**在评测集里加"需要用到历史信息"的题目**，对比"开记忆 / 关记忆"的准确率。如果没有提升，说明你的记忆设计是错的——**这个结论本身就是论文素材。**

⚠️ 长期记忆最大的风险是**污染**：记住错误信息后，后续每一轮都被带偏。所以必须有：
- 每条记忆带 `created_at` 和 `source_run_id`
- 提供一个查看/删除记忆的界面（哪怕是个接口）
- 记忆条数上限 + 过期策略

---

## Phase 8：评测集与实验

> 这一步把"项目"变成"课题"。**成本极低，收益极高，别跳过。**

### 8.1 建评测集

新建 `eval/cases.jsonl`，每行一题：

```json
{"id": "t01", "question": "今天有什么AI新闻", "expect_tools": ["web_search"], "expect_max_rounds": 2, "category": "时效性"}
{"id": "t02", "question": "你好", "expect_tools": [], "expect_max_rounds": 1, "category": "过度调用"}
{"id": "t03", "question": "总结一下我上传的那篇论文", "expect_tools": ["list_papers", "paper_search"], "expect_max_rounds": 3, "category": "多跳"}
{"id": "t04", "question": "我最近很难过，推荐几首歌", "expect_tools": ["recommend_music"], "expect_max_rounds": 2, "category": "工具选择"}
{"id": "t05", "question": "帮我查一下不存在的论文XYZ的内容", "expect_tools": [], "expect_max_rounds": 3, "category": "错误处理", "expect_no_hallucination": true}
```

**先写 30 条就够**，覆盖六类：时效性 / 过度调用 / 多跳 / 工具选择 / 错误处理 / 不需要工具。手工写，不要用 AI 生成——你自己设计题目时想的那些"模型可能会怎么错"，就是你论文的 hypothesis。

### 8.2 跑批脚本

新建 `eval/run_eval.py`：读 jsonl → 逐条调 `/chat` → 从返回的 `steps` 里提取实际调用的工具序列 → 和 `expect_tools` 比对 → 输出 CSV。

指标：

| 指标 | 定义 | 用途 |
|---|---|---|
| 工具选择准确率 | 调用的工具集合与期望一致 | 核心指标 |
| 工具序列准确率 | 调用**顺序**也一致 | 多跳能力 |
| 平均轮数 | — | 效率 |
| 过度调用率 | 期望 `[]` 却调了工具的比例 | 提示词质量 |
| 上限触发率 | `status == "max_rounds"` 的比例 | 循环设计合理性 |
| 平均 token / 平均耗时 | — | 成本 |
| 未调用工具却编造答案 | 需要人工抽查 | 幻觉 |

### 8.3 三个可以出结论的实验

1. **消融**：关键词路由（你原始的 `need_search_words`）vs function calling。工具选择准确率差多少？——**这直接回答了"你这半年做的改造到底有没有用"，是论文里最有说服力的一张表。**
2. **模型规模**：`qwen-turbo` / `qwen-plus` / `qwen-max` 在工具选择上的拐点在哪？小模型是不是更容易漏参数？
3. **轮数上限敏感性**：`MAX_ROUNDS` 从 3 到 10，准确率和 token 成本怎么变？**存在最优值吗？** 这个结论有实用价值，也容易讲故事。

### 8.4 错误分类（做 qualitative 分析用）

把失败案例逐条归类，这是论文的"案例分析"章节：

| 类型 | 表现 | 例子 |
|---|---|---|
| 选错工具 | 该用 A 用了 B | 问文件内容却去联网 |
| 参数错误 | 工具对，参数缺/错 | 漏传 `paper_id` |
| 多跳失败 | 该先查再调，直接跳步 | 不知道 paper_id 却瞎编一个 |
| 该停不停 | 已经够了还在调 | 连调 4 次 web_search |
| 该继续却停 | 信息不足就下结论 | 只看了第 1 块就总结全文 |
| 幻觉 | 没调工具却编造事实 | 编造论文里没有的结论 |

---

## 附录 A：目标文件结构

```
D:\hy\ai\ai_robot\
├── .git/
├── .gitignore                 ← Phase 1
├── .env.example               ← Phase 1
├── start.bat
├── docs/
│   └── agent-refactor-plan.md ← 本文档
├── eval/                      ← Phase 8
│   ├── cases.jsonl
│   ├── run_eval.py
│   └── results/
├── frontend/
│   ├── index.html
│   ├── app.js                 ← Phase 6 改（删掉 isMusicReply/parseMusicSongs，加时间线）
│   └── style.css
└── backend/
    ├── main.py                ← 逐步瘦身，只留路由与鉴权
    ├── config.py
    ├── llm.py                 ← Phase 2 新建（替换 dashscope.Generation）
    ├── logging_setup.py       ← Phase 2 新建
    ├── storage.py             ← Phase 2 新建
    ├── agent.py               ← Phase 5 新建（循环本体）
    ├── memory.py              ← Phase 7 新建
    ├── database.py            ← Phase 6/7 加表
    ├── paper_agent.py         ← Phase 4 改（多论文隔离）
    ├── tools/                 ← Phase 3/4
    │   ├── __init__.py
    │   ├── base.py
    │   ├── registry.py
    │   ├── web_search.py
    │   ├── paper_search.py
    │   ├── file_reader.py
    │   └── music.py
    ├── logs/                  ← 运行时生成（已 gitignore）
    ├── uploads/               ← 已 gitignore
    └── paper_index/           ← 已 gitignore
```

---

## 附录 B：常见坑清单

按"会不会浪费你一整天"排序：

1. **`.gitignore` 写晚了，`.env` 已提交**。密钥泄露要立刻去阿里云和 Tavily 后台轮换密钥，然后 `git rm --cached .env`。改历史很麻烦，**别走到这一步**。
2. **`tool` 消息和 `assistant.tool_calls` 被裁散**。`_trim_messages` 里那段 `while kept and kept[0]["role"] == "tool": kept.pop(0)` 是必需的，否则 API 会报 400。
3. **忘了把 `assistant` 的 `tool_calls` 放回历史**。会导致模型无限重复调用同一个工具，看起来像死循环——**这个 bug 会伪装成"模型太笨"，实际是你的循环写错了。**
4. **`tool` 消息缺 `tool_call_id`**。400 错误，报错信息很隐晦。
5. **模型返回的 `arguments` 不是合法 JSON**。必须 try/except，这是真实存在的失败模式，也要计数。
6. **工具函数抛异常没接住**。`Tool.run()` 里包了，但你自己写的工具内部如果 `raise` 到 `run` 之外就会炸循环。
7. **SSE 在 nginx 后面被缓冲**。要么配 `proxy_buffering off;`，要么先走轮询。
8. **`MAX_ROUNDS` 写死在代码里**。后面做实验要改它，一定放环境变量。
9. **换 SDK 和加循环一起做**。炸了你不知道是哪边的问题。**Phase 2 单独测通 OpenAI 兼容模式，再进 Phase 3。**
10. **`base_url` 用错站点**。国内 `dashscope.aliyuncs.com`，国际 `dashscope-intl.aliyuncs.com`。用错会 401，报错信息不会告诉你是站点问题。
11. **`paper_index/` 被提交进 git**。FAISS 索引动辄几十 MB，提交后仓库会变得很慢。已 gitignore，别手动 `git add -f`。
12. **多用户共用全局 `db`**。Phase 4 必须改掉，否则两人同时用会串数据。

---

## 附录 C：验收清单

每个 Phase 做完自查。**没全打勾不要进下一个 Phase。**

### Phase 1
- [ ] `git log` 有第一次提交
- [ ] `git status` 里看不到 `.env`、`chats.db`、`uploads/` 下的真实文件
- [ ] 有 `v0.1.0-baseline` 标签
- [ ] `backend/main_backup.py` 已删除

### Phase 2
- [ ] `backend/logs/app.log` 里有请求日志
- [ ] 故意传两个同名文件，磁盘上是两个 uuid 前缀的文件，都没被覆盖
- [ ] 传一个超大 txt，`read_text_from_file` 截断生效，没有超 token 报错
- [ ] 三个接口的异常都能在日志里看到 traceback
- [ ] `/chat` 在换成 OpenAI 兼容模式后依然正常回话

### Phase 3
- [ ] `python -c "import tools; print(tools.tool_names())"` 有输出
- [ ] `tools.all_schemas()` 返回的是合法 JSON Schema
- [ ] 故意注册两个同名工具，会报 `ValueError`

### Phase 4
- [ ] 四个工具逐个手测都返回 `ok=True`
- [ ] 不配 `TAVILY_API_KEY` 时，`web_search` 返回 `ok=False` 而不是抛异常
- [ ] 传两篇不同论文，各自 `paper_search` 检索到的是各自的内容（隔离生效）
- [ ] 前端音乐卡片改成读 `data.songs` 后正常显示
- [ ] `SYSTEM_PROMPT` 里的 `[MUSIC_CARD]` 段落已删除

### Phase 5
- [ ] 需要联网的问题：`steps` 里有 `web_search`，`rounds >= 2`
- [ ] "你好"：`rounds == 1`，无 `tool_call`（没过度调用）
- [ ] 需要两跳的问题：`steps` 里出现 `list_papers` → `paper_search` 的顺序
- [ ] 把 `AGENT_MAX_ROUNDS` 改成 1，问一个需要工具的问题，返回的 `status == "max_rounds"`，且仍然有回答（降级生效）
- [ ] 断网后提问，工具失败但整体不崩，`status` 仍是 `ok`

### Phase 6
- [ ] `agent_runs` / `agent_steps` 表有数据
- [ ] `/trace/{run_id}` 能返回完整步骤
- [ ] 前端时间线展示的步骤和后端返回的一一对应
- [ ] 失败的工具有琥珀色标记

### Phase 7
- [ ] 连问"我叫什么" → "我刚才说了什么"，第二轮能答对（短期记忆生效）
- [ ] 记忆有查看/删除入口
- [ ] 记忆条数有上限
- [ ] 评测集里"需要历史信息"的题目，开/关记忆的分数差异已记录

### Phase 8
- [ ] `cases.jsonl` 至少 30 条，六类都有
- [ ] `run_eval.py` 能输出 CSV
- [ ] 消融实验（关键词路由 vs function calling）已有结果表
- [ ] 失败案例已分类，每类有具体例子

---

## 建议的推进节奏

| 周 | 做什么 | 产出 |
|---|---|---|
| 第 1 周 | Phase 1 + 2 | 有回滚能力，日志能看清错误，P0 缺陷清完 |
| 第 2 周 | Phase 3 + 4 | 四个工具独立可用，架构清晰 |
| 第 3 周 | Phase 5 | **核心成果：agent 能自己选工具、多轮调用了** |
| 第 4 周 | Phase 6 | trace 落库 + 前端时间线，可演示 |
| 第 5 周 | Phase 8（提前做） | 评测集 + 消融结果，**此时就能写论文初稿** |
| 第 6 周+ | Phase 7 | 短期 → 长期记忆 + 对比实验 |

**注意我把 Phase 8 提到了 Phase 7 前面**：因为评测集一旦建好，你每改一次都能立刻看到效果，改起来更有方向；而且消融结果出来你就有东西可写了，不必等记忆做完。记忆做不做得好是加分项，评测集有没有是及格线。

---

## 最后一句话

你的计划顺序基本是对的，我只做了两处调整：**把评测集提前**，**把短期记忆插到长期记忆前面**。

真正的分水岭不在你写了多少功能，而在于——**改造完成后，你能不能说出一句别人说不出的话**。比如：

> "我把关键词路由改成了原生 function calling，在自建的 30 题评测集上，工具选择准确率从 62% 提升到 89%；但多跳场景仍然只有 55%，主要失败模式是模型在缺少 `paper_id` 时倾向于编造而不是先调用列表工具。"

这句话能写进论文、能写进面试、能写进开题报告。**功能谁都能写，这句话需要 trace 和评测集——所以那两步别省。**
