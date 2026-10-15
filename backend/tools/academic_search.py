"""Live search over openly accessible scholarly metadata APIs.

This tool discovers papers and metadata; it does not bypass publisher access
controls or fetch subscription full text.
"""
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .base import Tool, ToolResult, register


USER_AGENT = "ZhiweiResearchAssistant/1.0 (academic metadata search)"
TIMEOUT_SECONDS = 9


def _get_json(url: str, headers: dict | None = None) -> dict:
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read(2_000_000)
    return json.loads(raw.decode("utf-8"))


def _year(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_abstract(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()[:1200]


def _semantic_scholar(query: str, count: int, year_from: int | None, year_to: int | None) -> list[dict]:
    params = {
        "query": query,
        "limit": str(count),
        "fields": "title,authors,year,abstract,venue,externalIds,openAccessPdf,url,citationCount",
    }
    if year_from or year_to:
        params["year"] = f"{year_from or ''}-{year_to or ''}"
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)
    headers = {}
    key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if key:
        headers["x-api-key"] = key
    payload = _get_json(url, headers)
    papers = []
    for item in payload.get("data", []):
        authors = [str(author.get("name", "")) for author in (item.get("authors") or [])[:8]]
        external = item.get("externalIds") or {}
        doi = str(external.get("DOI") or "")
        oa = item.get("openAccessPdf") or {}
        papers.append({
            "title": str(item.get("title") or "Untitled")[:500],
            "authors": authors,
            "year": _year(item.get("year")),
            "venue": str(item.get("venue") or "")[:240],
            "abstract": _clean_abstract(item.get("abstract") or "")[:700],
            "doi": doi,
            "url": str(item.get("url") or ("https://doi.org/" + doi if doi else ""))[:1000],
            "open_access_url": str(oa.get("url") or "")[:1000],
            "citation_count": _year(item.get("citationCount")),
            "source": "Semantic Scholar",
        })
    return papers


def _crossref(query: str, count: int, year_from: int | None, year_to: int | None) -> list[dict]:
    params = {"query": query, "rows": str(count)}
    mailto = os.getenv("CROSSREF_MAILTO", "").strip()
    if mailto:
        params["mailto"] = mailto
    date_filters = []
    if year_from:
        date_filters.append(f"from-pub-date:{year_from}")
    if year_to:
        date_filters.append(f"until-pub-date:{year_to}")
    if date_filters:
        params["filter"] = ",".join(date_filters)
    payload = _get_json("https://api.crossref.org/works?" + urllib.parse.urlencode(params))
    papers = []
    for item in payload.get("message", {}).get("items", []):
        titles = item.get("title") or []
        authors = [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in (item.get("author") or [])[:8]]
        date_parts = (item.get("published-print") or item.get("published-online") or item.get("created") or {}).get("date-parts") or []
        year = _year(date_parts[0][0]) if date_parts and date_parts[0] else None
        doi = str(item.get("DOI") or "")
        links = item.get("link") or []
        oa_url = next((str(link.get("URL", "")) for link in links if "pdf" in str(link.get("content-type", "")).lower()), "")
        papers.append({
            "title": str(titles[0] if titles else "Untitled")[:500],
            "authors": authors,
            "year": year,
            "venue": str((item.get("container-title") or [""])[0])[:240],
            "abstract": _clean_abstract(item.get("abstract") or "")[:700],
            "doi": doi,
            "url": "https://doi.org/" + doi if doi else str(item.get("URL") or ""),
            "open_access_url": oa_url[:1000],
            "citation_count": _year(item.get("is-referenced-by-count")),
            "source": "Crossref",
        })
    return papers


def _search(args: dict, _context: dict) -> ToolResult:
    query = str(args.get("query", "")).strip()[:500]
    if not query:
        return ToolResult(False, "", error="论文检索关键词不能为空")
    count = max(1, min(int(args.get("max_results", 5)), 5))
    year_from = _year(args.get("year_from"))
    year_to = _year(args.get("year_to"))
    if year_from and year_to and year_from > year_to:
        return ToolResult(False, "", error="起始年份不能晚于结束年份")

    found, failures = [], []
    for name, provider in (("Semantic Scholar", _semantic_scholar), ("Crossref", _crossref)):
        try:
            found.extend(provider(query, count, year_from, year_to))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
            failures.append(f"{name}: {type(exc).__name__}")

    # Merge duplicate DOI/title results while preserving both provider names.
    unique = {}
    for paper in found:
        key = paper["doi"].lower().strip() or re.sub(r"\W+", " ", paper["title"].lower()).strip()
        if not key:
            continue
        if key in unique:
            existing = unique[key]
            existing["source"] += ", " + paper["source"]
            for field in ("abstract", "open_access_url", "venue", "url"):
                if not existing.get(field) and paper.get(field):
                    existing[field] = paper[field]
            if not existing.get("authors") and paper.get("authors"):
                existing["authors"] = paper["authors"]
        else:
            unique[key] = paper
    papers = list(unique.values())[:count]
    if not papers and failures:
        return ToolResult(False, "", error="开放学术检索服务暂时不可用（" + "; ".join(failures) + "）")
    if not papers:
        return ToolResult(True, "没有找到匹配的论文元数据。", {"query": query, "papers": [], "warnings": failures})

    blocks = []
    for index, paper in enumerate(papers, 1):
        blocks.append(
            f"[{index}] {paper['title']} ({paper.get('year') or '年份未知'})\n"
            f"作者: {', '.join(paper['authors']) or '未提供'}\n"
            f"期刊/会议: {paper['venue'] or '未提供'}\nDOI: {paper['doi'] or '未提供'}\n"
            f"来源: {paper['source']}\n链接: {paper['url'] or '未提供'}\n"
            f"开放获取链接: {paper['open_access_url'] or '未发现'}\n"
            f"摘要: {paper['abstract'] or '数据库未提供摘要'}"
        )
    content = "\n\n".join(blocks)
    content += "\n\n说明：这是论文元数据检索；开放获取链接仅在数据库提供时列出，不代表已读取全文。"
    data = {"query": query, "papers": papers, "warnings": failures}
    return ToolResult(True, content, data)


register(Tool(
    name="scholarly_search",
    description="实时检索 Semantic Scholar 和 Crossref 的学术论文元数据，适用于查找英语/中文论文、标题、作者、年份、摘要、DOI 和可能的开放获取链接。它不等于访问 IEEE Xplore、ScienceDirect 或知网，也不提供付费全文；需要阅读全文时优先查找开放获取链接或使用本地 paper_search。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "论文主题、题名、作者或精炼后的学术检索式"},
            "max_results": {"type": "integer", "description": "返回结果数，1 到 5，默认 5", "minimum": 1, "maximum": 5},
            "year_from": {"type": "integer", "description": "可选的起始年份"},
            "year_to": {"type": "integer", "description": "可选的结束年份"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    handler=_search,
))
