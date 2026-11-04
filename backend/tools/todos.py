"""任务清单：把「打算做几件事、做到第几步」变成界面上看得见的进度。

它不碰文件系统，也不执行任何东西——只维护本次运行的一份内存清单，
由 Agent 转成 todos 事件交给前端。放在 tools/ 里是为了复用同一条
参数校验与调用链，模型看到的就是一个普通工具。

为什么不按「一次一条」的增量方式设计：
增量接口看起来更省参数，但模型漏传、重传、顺序错乱都会让清单跟实际状态
对不上，而且每次调用都要多烧一个工具槽。整体提交（一次提交全部条目）
是幂等的，重传同一份清单不会有副作用，出错也只看这一次调用的内容。
"""

from .base import Tool, ToolResult, register

MAX_TODOS = 12
MAX_TEXT_CHARS = 160

# 状态只留三种：太多了界面上分不清，也容易让模型在「进行中/处理中/开始」
# 这类同义词上反复出错。
STATUS_ALIASES = {
    "pending": "pending", "todo": "pending", "open": "pending",
    "waiting": "pending", "queued": "pending", "next": "pending",
    "active": "active", "in_progress": "active", "in-progress": "active",
    "inprogress": "active", "doing": "active", "running": "active",
    "current": "active", "working": "active",
    "done": "done", "complete": "done", "completed": "done",
    "finished": "done", "ok": "done",
}

# 状态推进顺序：同一条待办重复出现时保留更靠后的状态，避免「跑测试」同时
# 被写成 pending 和 done，进度条算出 1/2 这种自相矛盾的结果。
_STATUS_RANK = {"pending": 0, "active": 1, "done": 2}

_EMPTY_ERROR = ("items 不能为空：至少写一条待办。全部完成时也要把条目用 done 标出来，"
                "不要传空数组——清空清单会让用户看不到这次做了什么。")


def _update_todos(arguments: dict, context: dict) -> ToolResult:
    raw_items = arguments.get("items")
    if not isinstance(raw_items, list):
        return ToolResult(
            False, "",
            error='items 必须是数组，每项形如 {"text": "跑测试确认失败", "status": "pending"}。',
        )
    if not raw_items:
        return ToolResult(False, "", error=_EMPTY_ERROR)
    if len(raw_items) > MAX_TODOS:
        return ToolResult(
            False, "",
            error="待办最多 %d 条，当前给了 %d 条。清单要能一眼看完，请合并相近的条目，"
                  "只保留用户关心的步骤。" % (MAX_TODOS, len(raw_items)),
        )

    items: list[dict] = []
    index_of: dict[str, int] = {}
    truncated = 0

    for index, raw in enumerate(raw_items, 1):
        if isinstance(raw, str):
            # 模型偶尔会简写成字符串数组，直接收下当待办，不为此浪费一轮
            text, status_raw = raw, "pending"
        elif isinstance(raw, dict):
            text = raw.get("text")
            status_raw = raw.get("status")
        else:
            return ToolResult(False, "", error="第 %d 条格式不对，应该是带 text 与 status 的对象。" % index)

        text = " ".join(str(text or "").split())
        if not text:
            return ToolResult(False, "", error="第 %d 条没有 text 内容，每条待办都要写清做什么。" % index)
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS - 1] + "…"
            truncated += 1

        status_key = str(status_raw or "pending").strip().lower()
        status = STATUS_ALIASES.get(status_key)
        if status is None:
            return ToolResult(
                False, "",
                error="第 %d 条 status「%s」无效，只能是 pending（待办）/ active（正在做）/ "
                      "done（已完成）。" % (index, status_key),
            )

        if text in index_of:
            # 同一条出现两次：保留更靠后的状态，不让进度条自相矛盾
            slot = index_of[text]
            if _STATUS_RANK[status] > _STATUS_RANK[items[slot]["status"]]:
                items[slot]["status"] = status
            continue

        index_of[text] = len(items)
        items.append({"text": text, "status": status})

    # 同时有多条 active 时只认第一条：界面上并排两个「正在做」会让人以为
    # 它并行推进，实际上 Agent 是串行的。这里不报错（报错要重来一轮），
    # 直接降级成待办，并在回执里说清楚。
    demoted = 0
    seen_active = False
    for item in items:
        if item["status"] != "active":
            continue
        if seen_active:
            item["status"] = "pending"
            demoted += 1
        seen_active = True

    total = len(items)
    done = sum(1 for item in items if item["status"] == "done")
    currents = [item["text"] for item in items if item["status"] == "active"]
    pending = [item["text"] for item in items if item["status"] == "pending"]

    context["todos"] = items
    data = {
        "kind": "todos",
        "items": items,
        "total": total,
        "done": done,
        "active": currents[:1],
        "pending": len(pending),
    }

    head = "任务清单已更新：%d/%d 完成" % (done, total)
    if done == total:
        head += "，全部完成"
    elif currents:
        head += "，正在做「%s」" % currents[0]
    elif pending:
        head += "，下一步「%s」" % pending[0]

    notes = []
    if demoted:
        notes.append("同时只允许一条进行中，多出的 %d 条已改回待办" % demoted)
    if truncated:
        notes.append("%d 条过长已截断到 %d 字" % (truncated, MAX_TEXT_CHARS))
    if notes:
        head += "（%s）" % "；".join(notes)

    tail = "继续推进，不要只更新清单不动手。" if done < total else "可以收尾并说明结果。"
    return ToolResult(True, head + "。" + tail, data)


register(Tool(
    name="update_todos",
    description=(
        "维护本次任务的待办清单，用户靠它看进度。"
        "只要这次要改 2 个以上文件、或要跨多次工具调用才能做完，**第一步就调用本工具**列出 3–7 条计划；"
        "每完成一步再调用一次，整体重写整份清单并把该条改成 done。"
        "每次提交的是完整清单，不是增量追加；同时只能有一条 active。"
        "只改一处、一句话能说清的小改动不要用它，直接做。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "按执行顺序排列的完整清单（每次提交全部条目）",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "一句话说明要做的事，不超过 60 字",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "active", "done"],
                            "description": "pending 待办 / active 正在做 / done 已完成",
                        },
                    },
                    "required": ["text", "status"],
                },
            },
        },
        "required": ["items"],
    },
    handler=_update_todos,
))
