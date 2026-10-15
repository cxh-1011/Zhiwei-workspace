import os
import re

from .base import Tool, ToolResult, register

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
SUPPORTED = {".txt", ".pdf", ".docx"}
MAX_FILE_CHARS = 300_000
CHUNK_SIZE = 1600
CHUNK_OVERLAP = 250


def _read_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        with open(path, "r", encoding="utf-8", errors="ignore") as source:
            return source.read(MAX_FILE_CHARS + 1)[:MAX_FILE_CHARS]
    if ext == ".pdf":
        from pypdf import PdfReader
        parts = []
        total = 0
        for page in PdfReader(path).pages:
            part = page.extract_text() or ""
            parts.append(part)
            total += len(part)
            if total >= MAX_FILE_CHARS:
                break
        return "\n".join(parts)[:MAX_FILE_CHARS]
    if ext == ".docx":
        from docx import Document
        parts = []
        total = 0
        for paragraph in Document(path).paragraphs:
            parts.append(paragraph.text)
            total += len(paragraph.text)
            if total >= MAX_FILE_CHARS:
                break
        return "\n".join(parts)[:MAX_FILE_CHARS]
    raise ValueError("文件格式不支持")


def _terms(text: str) -> set[str]:
    normalized = text.lower()
    tokens = set(re.findall(r"[a-z0-9][a-z0-9_.+-]{1,}", normalized))
    compact = re.sub(r"\s+", "", normalized)
    for index in range(max(0, len(compact) - 1)):
        pair = compact[index:index + 2]
        if any("\u3400" <= char <= "\u9fff" for char in pair):
            tokens.add(pair)
    return tokens


def _rank_chunks(text: str, query: str, limit: int = 4) -> list[tuple[int, str]]:
    query_terms = _terms(query)
    if not query_terms:
        return []
    stride = CHUNK_SIZE - CHUNK_OVERLAP
    chunks = [text[pos:pos + CHUNK_SIZE] for pos in range(0, len(text), stride)]
    scored = []
    for index, chunk in enumerate(chunks):
        chunk_terms = _terms(chunk)
        score = sum(1 for term in query_terms if term in chunk_terms)
        if score:
            scored.append((score, index, chunk))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[:limit]


def _search(args: dict, context: dict) -> ToolResult:
    query = str(args.get("query", "")).strip()[:1000]
    if not query:
        return ToolResult(False, "", error="检索问题不能为空")
    selected = context.get("filename")
    if selected:
        candidates = [selected]
    else:
        candidates = sorted(
            name for name in os.listdir(UPLOAD_DIR)
            if os.path.splitext(name)[1].lower() in SUPPORTED
        ) if os.path.isdir(UPLOAD_DIR) else []
    found = []
    for filename in candidates[:20]:
        # Resolve strictly inside uploads and reject traversal/symlink escapes.
        if not isinstance(filename, str) or os.path.basename(filename.replace("\\", "/")) != filename:
            continue
        path = os.path.realpath(os.path.join(UPLOAD_DIR, filename))
        if os.path.commonpath([path, os.path.realpath(UPLOAD_DIR)]) != os.path.realpath(UPLOAD_DIR):
            continue
        if not os.path.isfile(path) or os.path.splitext(filename)[1].lower() not in SUPPORTED:
            continue
        try:
            text = _read_text(path)
            for score, chunk_no, chunk in _rank_chunks(text, query):
                found.append({"filename": filename, "chunk": chunk_no + 1, "text": chunk, "score": score})
        except Exception as exc:
            continue
    found.sort(key=lambda item: (-item["score"], item["filename"], item["chunk"]))
    found = found[:8]
    if not found:
        reason = "没有找到与问题相关的内容。" if candidates else "还没有可检索的上传资料。"
        return ToolResult(True, reason, {"sources": []})
    output = "\n\n".join(
        f"[文件：{item['filename']} · 片段 {item['chunk']}]\n{item['text']}"
        for item in found
    )
    return ToolResult(True, output, {"sources": [{key: value for key, value in item.items() if key != "score"} for item in found]})


register(Tool(
    name="search_uploaded_files",
    description="按问题检索用户上传的 TXT、PDF、DOCX 资料，返回最相关的文本片段和文件名。图片由文件问答模式直接交给视觉模型理解，不要用此工具检索图片。",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "要从上传资料中查找的问题或关键词"}},
        "required": ["query"],
        "additionalProperties": False,
    },
    handler=_search,
))


def _list(_args: dict, _context: dict) -> ToolResult:
    names = sorted(
        name for name in os.listdir(UPLOAD_DIR)
        if os.path.splitext(name)[1].lower() in SUPPORTED | {".jpg", ".jpeg", ".png", ".webp"}
    ) if os.path.isdir(UPLOAD_DIR) else []
    return ToolResult(True, "\n".join(names) if names else "目前没有上传资料。", {"files": names})


register(Tool(
    name="list_uploaded_files",
    description="列出可供检索的已上传 TXT、PDF、DOCX 文件。用户提到某个文件但没有说文件名，或需要确认资料是否存在时使用。",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    handler=_list,
))
