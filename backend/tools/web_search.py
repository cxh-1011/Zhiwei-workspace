from tavily import TavilyClient

from config import TAVILY_API_KEY
from .base import Tool, ToolResult, register


def _search(args: dict, _context: dict) -> ToolResult:
    if not TAVILY_API_KEY:
        return ToolResult(False, "", error="尚未配置联网搜索服务")
    query = str(args.get("query", "")).strip()[:500]
    if not query:
        return ToolResult(False, "", error="搜索内容不能为空")
    count = max(1, min(int(args.get("max_results", 3)), 5))
    result = TavilyClient(api_key=TAVILY_API_KEY).search(
        query=query, max_results=count, timeout=12
    )
    items = result.get("results", [])
    sources = []
    for item in items:
        sources.append({
            "title": str(item.get("title", "网页结果"))[:240],
            "url": str(item.get("url", ""))[:1000],
            "snippet": str(item.get("content", ""))[:900],
        })
    if not sources:
        return ToolResult(True, "没有找到相关网页结果。", {"query": query, "sources": []})
    text = "\n\n".join(
        f"[{i}] {item['title']}\nURL: {item['url']}\n摘要: {item['snippet']}"
        for i, item in enumerate(sources, 1)
    )
    return ToolResult(True, text, {"query": query, "sources": sources})


register(Tool(
    name="web_search",
    description="搜索近期网页信息，适用于时效性事实、新闻、政策、产品动态和用户明确要求联网查证的问题。返回标题、链接和摘要。普通常识问题不要调用。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "精炼后的搜索关键词或问题"},
            "max_results": {"type": "integer", "description": "结果数量，1 到 5，默认 3", "minimum": 1, "maximum": 5},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    handler=_search,
))
