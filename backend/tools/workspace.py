"""工作区只读工具：让助手能看代码，但不能改、不能执行。

设计约束（三个都必须满足）：
1. 路径限制：所有路径经 realpath 解析后必须落在 AGENT_WORKSPACE 之内，
   防止通过 .. 或符号链接跳出工作区。
2. 敏感文件黑名单：.env、数据库、密钥、依赖目录、上传数据一律不可读，
   避免把 API Key 或用户隐私读进模型上下文。
3. 默认不注册：只有用户显式开启「允许读项目文件」才进入 tool schema。

这三个工具都是只读的，不提供写入和执行能力。
"""

import os
import re

from ._guard import (
    MAX_FILE_BYTES,
    MAX_LINE_CHARS,
    MAX_SCAN_BYTES,
    SKIP_SUFFIXES,
    WORKSPACE_ROOT,
    display as _display,
    human_size as _human,
    is_blocked as _is_blocked,
    looks_binary as _looks_binary,
    needs_probe as _needs_probe,
    reject_non_text as _reject_non_text,
    rel_inside as _rel_inside,
    resolve as safe_path,
)
from .base import Tool, ToolResult, register


# ---------------------------------------------------------------- 工作区与安全边界
# 所有路径判断都集中在 _guard.py：只读、写入、执行三类工具共用同一套规则，
# 避免出现「读被拦了、写没拦」。这里仅保留别名，别的模块仍按老名字调用。

# ---------------------------------------------------------------- list_dir

def _list_dir(args: dict, _context: dict) -> ToolResult:
    rel = str(args.get("path", ".")).strip() or "."
    try:
        target = safe_path(rel)
    except ValueError as exc:
        return ToolResult(False, "", error=str(exc))

    if not os.path.isdir(target):
        return ToolResult(False, "", error="不是目录：%s" % rel)

    lines, dirs, files, hidden = [], 0, 0, 0
    try:
        entries = sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError as exc:
        return ToolResult(False, "", error="读取目录失败：%s" % exc)

    for entry in entries:
        if len(lines) >= 200:
            lines.append("……（条目过多，仅显示前 200 项）")
            break
        try:
            entry_rel = _rel_inside(entry.path)
        except ValueError:
            hidden += 1          # 符号链接指到工作区外，一样算不可访问
            continue
        if _is_blocked(entry_rel):
            hidden += 1
            continue
        if entry.is_dir():
            dirs += 1
            lines.append("%s/" % entry.name)
        else:
            files += 1
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            lines.append("%s  (%d B)" % (entry.name, size))

    if not lines:
        return ToolResult(True, "目录 %s 里没有可访问的内容（隐藏 %d 项）。" % (rel, hidden),
                          {"path": rel, "entries": [], "hidden": hidden})

    header = "工作区相对路径 %s/  （%d 个目录，%d 个文件）" % (_display(_rel_inside(target)), dirs, files)
    body = "\n".join(lines)
    if hidden:
        # 只说数量不说名字：让模型知道目录不是空的，又不暴露 .env 这类文件名
        body += "\n（另有 %d 项在不可访问范围内，已隐藏）" % hidden
    return ToolResult(True, header + "\n" + body,
                      {"path": rel, "dirs": dirs, "files": files, "hidden": hidden})


# ---------------------------------------------------------------- read_file

def _read_file(args: dict, _context: dict) -> ToolResult:
    rel = str(args.get("path", "")).strip()
    if not rel:
        return ToolResult(False, "", error="需要提供 path 参数")
    try:
        target = safe_path(rel)
    except ValueError as exc:
        return ToolResult(False, "", error=str(exc))

    if not os.path.isfile(target):
        return ToolResult(False, "", error="文件不存在：%s" % rel)

    # 先判类型再判体积：对一张 3 MB 的图片应该报「这是二进制」，而不是「文件太大」
    denied = _reject_non_text(target)
    if denied:
        return ToolResult(False, "", error="%s：%s" % (_display(_rel_inside(target)), denied))

    try:
        size = os.path.getsize(target)
    except OSError as exc:
        return ToolResult(False, "", error="无法读取文件信息：%s" % exc)
    if size > MAX_FILE_BYTES:
        return ToolResult(
            False, "",
            error=("文件过大（%s），超过单文件 %s 上限。请用 grep_code 定位目标行，"
                   "再用 read_file(start_line=, max_lines=) 分段读；不要试图一次读完。"
                   % (_human(size), _human(MAX_FILE_BYTES))),
        )

    try:
        with open(target, "r", encoding="utf-8", errors="ignore") as handle:
            # 用 splitlines()：文件以换行结尾时不会多算一个空行，
            # 也能正确处理 CRLF，行号与编辑器一致。
            all_lines = handle.read().splitlines()
    except OSError as exc:
        return ToolResult(False, "", error="读取失败：%s" % exc)

    total = len(all_lines)
    start = max(1, min(int(args.get("start_line", 1)), max(1, total)))
    limit = max(1, min(int(args.get("max_lines", 200)), 400))
    chunk = all_lines[start - 1: start - 1 + limit]

    numbered = []
    for offset, text in enumerate(chunk):
        line_no = start + offset
        text = text.rstrip("\r")
        if len(text) > MAX_LINE_CHARS:
            text = text[:MAX_LINE_CHARS] + " …[本行已截断]"
        numbered.append("%5d│%s" % (line_no, text))

    shown_to = start + len(chunk) - 1
    head = "文件 %s（共 %d 行，显示第 %d–%d 行）" % (_display(_rel_inside(target)), total, start, shown_to)
    body = "\n".join(numbered)
    if shown_to < total:
        body += "\n……（还有 %d 行未显示，可用 start_line=%d 继续读）" % (total - shown_to, shown_to + 1)

    return ToolResult(True, head + "\n" + body, {
        "path": rel, "total_lines": total, "start_line": start, "end_line": shown_to,
    })


# ---------------------------------------------------------------- grep_code

_SKIP_EXT = SKIP_SUFFIXES


def _grep(args: dict, _context: dict) -> ToolResult:
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        return ToolResult(False, "", error="需要提供 pattern 参数")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return ToolResult(False, "", error="正则表达式无效：%s" % exc)

    rel = str(args.get("path", ".")).strip() or "."
    try:
        base = safe_path(rel)
    except ValueError as exc:
        return ToolResult(False, "", error=str(exc))
    if not os.path.exists(base):
        return ToolResult(False, "", error="路径不存在：%s" % rel)

    max_results = max(1, min(int(args.get("max_results", 50)), 100))
    hits = []
    scanned = 0

    def scan_file(path: str):
        nonlocal scanned
        if len(hits) >= max_results:
            return
        ext = os.path.splitext(path)[1].lower()
        if ext in _SKIP_EXT:
            return
        try:
            if os.path.getsize(path) > MAX_SCAN_BYTES:
                return
            # 未知后缀再嗅探一次：避免把 .bin、无后缀的二进制逐行当文本读
            if _needs_probe(path) and _looks_binary(path):
                return
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                scanned += 1
                for line_no, text in enumerate(handle, 1):
                    if len(hits) >= max_results:
                        return
                    if regex.search(text):
                        snippet = text.strip()
                        if len(snippet) > 200:
                            snippet = snippet[:200] + " …"
                        hits.append("%s:%d: %s" % (_display(_rel_inside(path)), line_no, snippet))
        except OSError:
            return

    if os.path.isfile(base):
        scan_file(base)
    else:
        for dirpath, dirnames, filenames in os.walk(base):
            # 就地裁剪，避免走进 .venv 这类大目录
            dirnames[:] = [
                d for d in dirnames
                if not _is_blocked(_rel_inside(os.path.join(dirpath, d)))
            ]
            for name in sorted(filenames):
                if len(hits) >= max_results:
                    break
                full = os.path.join(dirpath, name)
                try:
                    if _is_blocked(_rel_inside(full)):
                        continue
                except ValueError:
                    continue
                scan_file(full)
            if len(hits) >= max_results:
                break

    if not hits:
        return ToolResult(True, "没有找到匹配 %r 的内容（扫描了 %d 个文件）。" % (pattern, scanned),
                          {"pattern": pattern, "scanned": scanned, "hits": 0})

    note = "" if len(hits) < max_results else "\n（已达到 %d 条上限，结果可能不完整）" % max_results
    return ToolResult(True, "匹配 %r，扫描 %d 个文件，命中 %d 条：\n%s%s"
                      % (pattern, scanned, len(hits), "\n".join(hits), note),
                      {"pattern": pattern, "scanned": scanned, "hits": len(hits)})


# ---------------------------------------------------------------- 注册（默认不可见）

register(Tool(
    name="list_dir",
    description=(
        "列出工作区内某个目录的文件与子目录。用于了解项目结构、找到目标文件。"
        "只读。路径必须是工作区相对路径，例如 backend 或 backend/tools。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "工作区相对目录，默认「.」表示根目录"},
        },
        "required": [],
        "additionalProperties": False,
    },
    handler=_list_dir,
))

register(Tool(
    name="read_file",
    description=(
        "读取工作区内某个文本文件的内容，返回带行号的代码。"
        "文件较长时间用 start_line / max_lines 分页读取。只读，不能修改文件。"
        "配置、密钥、数据库、依赖与备份目录，以及二进制文件一律不可访问。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "工作区相对文件路径，例如 backend/agent.py"},
            "start_line": {"type": "integer", "description": "从第几行开始读，默认 1", "minimum": 1},
            "max_lines": {"type": "integer", "description": "最多读多少行，默认 200，上限 400",
                          "minimum": 1, "maximum": 400},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    handler=_read_file,
))

register(Tool(
    name="grep_code",
    description=(
        "在工作区文件中用正则搜索代码，返回「文件:行号: 内容」。"
        "用于定位某个函数、变量或字符串出现在哪里，比逐个读文件高效。"
        "只读。自动跳过红线目录、二进制文件、超过 512 KB 的大文件与数据文件。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式，例如 def run_agent 或 allow_writes"},
            "path": {"type": "string", "description": "限定搜索的目录或文件，默认整个工作区"},
            "max_results": {"type": "integer", "description": "最多返回条数，默认 50，上限 100",
                            "minimum": 1, "maximum": 100},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    handler=_grep,
))
