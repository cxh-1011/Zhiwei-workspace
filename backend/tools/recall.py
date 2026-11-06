"""长期记忆的**检索**工具（只读）。

和 archive.py 里的 remember 是一对，但权限要求完全不同：
- remember 写入，必须用户开「允许助手记录」；
- recall_memory 只读，默认就可用。

这个区别是刻意的。写入是不可逆的（会改变以后每轮对话的背景），所以要有闸；
检索只是「把已经存在的东西找出来给他看」，不给它任何闸，否则用户在没开记录时
问「你记得我上次说什么吗」，助手只能回「我不记得」——而数据其实一直躺在库里。
"""

from .base import Tool, ToolResult, register

# 一次检索的返回上限。检索结果是要塞回上下文的，越多越挤占当前问题；
# 5 条足够判断「有没有这段历史」，不够时模型会自己再换关键词检索一次。
MAX_HITS = 6


def _recall(args: dict, _context: dict) -> ToolResult:
    import recall as recall_lib

    query = str(args.get("query", "")).strip()
    scope = str(args.get("scope", "all")).strip() or "all"
    if not query:
        return ToolResult(False, "", error="检索内容不能为空：请写清要找什么，例如「研究方向」「上次聊的 Agent 项目」")

    try:
        result = recall_lib.recall(query, scope, MAX_HITS)
    except ValueError as exc:
        return ToolResult(False, "", error=str(exc))

    # data 里只放结构化命中，不放 text：前端按 tool 结果画卡片时不需要正文，
    # 而 text 已经被 ToolResult.content 送进模型了，重复塞一遍是浪费上下文。
    return ToolResult(
        True,
        result["text"],
        {
            "scope": result["scope"],
            "total": result["total"],
            "memory": result["memory"],
            "conversation": result["conversation"],
            "course": result["course"],
        },
    )


register(Tool(
    name="recall_memory",
    description=(
        "检索用户自己的长期记忆、历史问答和课程进度。"
        "常驻上下文里只有最近一小段记忆，更早的信息**只有调用本工具才能拿到**。"
        "出现这些情况必须先调用：① 用户问「我之前说过/记过什么」「上次我们聊到哪」"
        "「我的研究方向/目标是什么」；② 用户提到「还是上次那个」「接着之前」；"
        "③ 你要给出个性化建议，但当前上下文里没有他的背景。"
        "只读，不会写入任何东西。检索不到就说明确实没记录过，如实说明即可，不要编。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要找什么，用短句或关键词，例如「研究方向」「上次的 Agent 项目」「第 7 章」",
            },
            "scope": {
                "type": "string",
                "enum": ["all", "memory", "conversation", "course"],
                "description": "检索范围：all 全部（默认）、memory 只查长期记忆、conversation 只查历史问答、course 只查课程进度",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    handler=_recall,
))
