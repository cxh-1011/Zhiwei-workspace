from .base import Tool, ToolResult, register


def _search(args: dict, _context: dict) -> ToolResult:
    from paper_agent import search_paper

    query = str(args.get("query", "")).strip()[:1000]
    if not query:
        return ToolResult(False, "", error="检索问题不能为空")
    results = search_paper(query, k=max(1, min(int(args.get("k", 4)), 6)))
    if not results:
        return ToolResult(False, "", error="当前没有可用论文索引，请先上传并解析 PDF 论文")
    chunks = []
    for index, item in enumerate(results, 1):
        chunks.append(f"[论文片段 {index}] {item['source']}\n{item['text']}")
    return ToolResult(True, "\n\n".join(chunks), {"sources": results})


register(Tool(
    name="paper_search",
    description="在当前已解析的 PDF 论文向量索引中检索与问题相关的原文片段。用户询问上传论文内容、方法、实验、结论或概念时使用。只检索资料，不负责生成最终回答。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "适合检索论文的主题或问题"},
            "k": {"type": "integer", "description": "返回片段数，1 到 6，默认 4", "minimum": 1, "maximum": 6},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    handler=_search,
))
