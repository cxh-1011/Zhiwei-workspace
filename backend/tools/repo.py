"""仓库感知工具：git 状态 / 差异 / 历史，以及一份压缩后的项目地图。

为什么需要这一层：模型要改对代码，先得看对。逐个 list_dir 会把上下文预算
浪费在文件名上；repo_map 用一次调用交出「目录结构 + 每个文件的行数与顶层符号」，
让它能直接跳到该读的文件和行号，而不是先把整仓扫一遍。

四个工具全部只读，且都走 _guard 的路径沙箱。git 用 subprocess 直接调用，
不经 shell（避免命令拼接注入），有超时和输出截断。
"""

import ast
import os
import re
import subprocess

from ._guard import (
    MAX_FILE_BYTES,
    SKIP_SUFFIXES,
    WORKSPACE_ROOT,
    display,
    is_blocked,
    rel_inside,
    resolve,
)
from .base import Tool, ToolResult, register

GIT_TIMEOUT = 15
MAX_GIT_CHARS = 6000

# 会被收进项目地图的后缀。刻意不含 .md / .json：配置文件对「读懂代码」帮助有限，
# 但会占掉不少预算。
CODE_SUFFIXES = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".go", ".rs",
    ".java", ".c", ".cpp", ".h", ".css", ".html",
}

MAX_MAP_FILES = 400
MAX_SYMBOLS_PER_FILE = 12
SYMBOL_MAX_CHARS = 28


# ---------------------------------------------------------------- git 调用

def _git(args: list[str], max_chars: int = MAX_GIT_CHARS):
    """在工作区根目录执行 git，返回 (状态, 文本)。

    状态含义：True=成功，False=git 明确报错（例如不是仓库），None=环境问题
    （没装 git / 超时）。调用方据此给出不同措辞，不要把「没装 git」说成「没有改动」。
    """
    try:
        proc = subprocess.run(
            # core.quotepath=false：否则中文文件名会被转成 \344\270\211 这种八进制，
            # 模型看到的就是一堆转义码，等于没列出文件名。
            ["git", "-c", "core.quotepath=false"] + list(args),
            cwd=WORKSPACE_ROOT,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
        )
    except FileNotFoundError:
        return None, "系统里没有找到 git 命令，无法查看仓库信息。"
    except subprocess.TimeoutExpired:
        return None, "git 命令超过 %d 秒未返回，已放弃。" % GIT_TIMEOUT
    except OSError as exc:
        return None, "无法执行 git：%s" % exc

    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip()
        return False, message[:600] or "git 返回了非零状态码。"

    out = (proc.stdout or "").replace("\r\n", "\n").strip()
    if len(out) > max_chars:
        out = out[:max_chars] + "\n……（输出过长已截断，可用 path 参数缩小范围）"
    return True, out


def _git_or_error(args: list[str]):
    """跑一次 git，把失败/环境问题直接转成 ToolResult 的错误信息。"""
    ok, text = _git(args)
    if ok is None:
        return None, ToolResult(False, "", error=text)
    if ok is False:
        return None, ToolResult(False, "", error="git 执行失败：%s" % text)
    return text, None


# ---------------------------------------------------------------- git_status

def _git_status(_args: dict, _context: dict) -> ToolResult:
    head, err = _git_or_error(["rev-parse", "--short", "HEAD"])
    if err:
        return err
    branch_ok, branch_text = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch_text.strip() if branch_ok else "?"
    status_text, err = _git_or_error(["status", "--short", "--branch"])
    if err:
        return err

    entries = [line for line in status_text.splitlines() if line and not line.startswith("##")]
    dirty = bool(entries)

    lines = ["分支 %s ｜ HEAD %s ｜ %s" % (
        branch,
        head,
        "%d 个未提交条目" % len(entries) if dirty else "工作区干净",
    )]
    if dirty:
        lines.append(status_text)
        lines.append(
            "提示：这些是尚未提交的改动，可能是用户自己的。修改同一文件前先看清内容，"
            "不要整文件覆盖；如需了解具体变化用 git_diff。"
        )
    return ToolResult(True, "\n".join(lines), {
        "branch": branch,
        "head": head,
        "dirty": dirty,
        "changed": len(entries),
    })


# ---------------------------------------------------------------- git_diff

def _git_diff(args: dict, _context: dict) -> ToolResult:
    rel = str(args.get("path", "")).strip()
    target = None
    if rel:
        try:
            target = rel_inside(resolve(rel))
        except ValueError as exc:
            return ToolResult(False, "", error=str(exc))

    staged = bool(args.get("staged"))
    stat_only = bool(args.get("stat_only"))

    cmd = ["diff"]
    if staged:
        cmd.append("--cached")
    if stat_only:
        cmd.append("--stat")
    if target:
        cmd += ["--", target]

    text, err = _git_or_error(cmd)
    if err:
        return err

    if not text:
        scope = "（暂存区）" if staged else ""
        scope += " %s" % display(target) if target else ""
        return ToolResult(True, "没有未提交的改动%s。" % scope, {
            "path": target or "", "staged": staged, "empty": True,
        })

    # git diff 不含未跟踪文件，单独列一下，避免模型以为「没有新文件」
    untracked_note = ""
    # 先取全量再排序：如果先按小额度截断，字典序靠后的浅层文件会先被切掉
    others_ok, others_text = _git(
        ["ls-files", "--others", "--exclude-standard"], max_chars=8000
    )
    if others_ok and others_text.strip():
        all_untracked = [line for line in others_text.splitlines() if line.strip()]
        # 浅层文件优先：根目录下新写的文件通常比某个深层目录里的更值得先看到
        all_untracked.sort(key=lambda p: (p.count("/"), p))
        files = all_untracked[:30]
        untracked_note = "\n\n未跟踪的新文件（不在上面的 diff 里，共 %d 个）：\n%s" % (
            len(all_untracked), "\n".join(files),
        )
        if len(all_untracked) > len(files):
            untracked_note += "\n……（还有 %d 个未列出）" % (len(all_untracked) - len(files))

    header = "git diff%s%s" % (
        " --cached" if staged else "",
        " --stat" if stat_only else "",
    )
    return ToolResult(True, header + "\n" + text + untracked_note, {
        "path": target or "",
        "staged": staged,
        "stat_only": stat_only,
        "chars": len(text),
    })


# ---------------------------------------------------------------- git_log

def _git_log(args: dict, _context: dict) -> ToolResult:
    limit = max(1, min(int(args.get("limit", 10)), 30))
    rel = str(args.get("path", "")).strip()
    target = None
    if rel:
        try:
            target = rel_inside(resolve(rel))
        except ValueError as exc:
            return ToolResult(False, "", error=str(exc))

    cmd = ["log", "--oneline", "--decorate", "-n", str(limit)]
    if target:
        cmd += ["--", target]

    text, err = _git_or_error(cmd)
    if err:
        return err
    if not text:
        return ToolResult(True, "当前分支还没有提交记录。", {"limit": limit, "commits": 0})

    commits = len(text.splitlines())
    return ToolResult(True, "最近 %d 条提交%s：\n%s" % (
        commits, "（限定 %s）" % display(target) if target else "", text,
    ), {"limit": limit, "commits": commits, "path": target or ""})


# ---------------------------------------------------------------- repo_map

_PY_SYMBOL_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

_JS_PATTERNS = (
    re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(r"^(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", re.M),
    re.compile(
        r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
        r"(?:async\s*)?(?:function|\()",
        re.M,
    ),
)


def _py_symbols(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    names = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.append("%s(类)" % node.name)
        elif isinstance(node, _PY_SYMBOL_NODES):
            names.append(node.name)
    return names


def _js_symbols(text: str) -> list[str]:
    names: list[str] = []
    for pattern in _JS_PATTERNS:
        names.extend(match.group(1) for match in pattern.finditer(text))
    # 去重并保持出现顺序
    seen, ordered = set(), []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _prioritize(names: list[str]) -> list[str]:
    """公开符号排在前面。

    一个文件里 `_helper` 往往比入口函数多得多，按定义顺序截断会把 run_agent_stream
    这种真正要找的名字挤掉。这里只是把下划线开头的挪到后面，组内顺序不变。
    """
    public = [n for n in names if not n.startswith("_")]
    private = [n for n in names if n.startswith("_")]
    return public + private


def _symbols_for(path: str, text: str) -> list[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        return _prioritize(_py_symbols(text))
    if ext in {".js", ".ts", ".jsx", ".tsx", ".vue"}:
        return _prioritize(_js_symbols(text))
    return []


def _iter_source_files(base: str):
    """遍历 base 下的源码文件，跳过黑名单目录与二进制后缀。"""
    if os.path.isfile(base):
        if os.path.splitext(base)[1].lower() in CODE_SUFFIXES:
            yield base
        return

    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(
            d for d in dirnames
            if not is_blocked(rel_inside(os.path.join(dirpath, d)))
        )
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() not in CODE_SUFFIXES:
                continue
            if os.path.splitext(name)[1].lower() in SKIP_SUFFIXES:
                continue
            yield os.path.join(dirpath, name)


def _repo_map(args: dict, _context: dict) -> ToolResult:
    rel = str(args.get("path", ".")).strip() or "."
    max_chars = max(400, min(int(args.get("max_chars", 4000)), 8000))
    try:
        base = resolve(rel)
    except ValueError as exc:
        return ToolResult(False, "", error=str(exc))
    if not os.path.exists(base):
        return ToolResult(False, "", error="路径不存在：%s" % rel)

    files: list[tuple[str, int, list[str]]] = []
    total_lines = 0
    skipped = 0

    for full in _iter_source_files(base):
        if len(files) >= MAX_MAP_FILES:
            skipped += 1
            continue
        try:
            if os.path.getsize(full) > MAX_FILE_BYTES:
                skipped += 1
                continue
            with open(full, "r", encoding="utf-8", errors="ignore") as handle:
                text = handle.read()
        except OSError:
            skipped += 1
            continue
        line_count = text.count("\n") + 1 if text else 0
        total_lines += line_count
        files.append((rel_inside(full), line_count, _symbols_for(full, text)))

    if not files:
        return ToolResult(True, "在 %s 下没有找到可分析的源码文件（%s）。"
                          % (display(rel_inside(base)), "、".join(sorted(CODE_SUFFIXES)[:6]) + " 等"),
                          {"path": rel, "files": 0})

    # 按目录分组，目录按深度再按名字排，让 backend/ 这类主目录先出现
    grouped: dict[str, list[tuple[str, int, list[str]]]] = {}
    for item in files:
        parent = os.path.dirname(item[0]).replace("\\", "/") or "."
        grouped.setdefault(parent, []).append(item)

    header = "项目地图：%s（%d 个源码文件，约 %d 行）" % (
        display(rel_inside(base)), len(files), total_lines,
    )
    hint = "（先用本图定位文件，再用 grep_code 找符号、read_file(start_line=) 读段落）"

    # 预算分配：先保证每个文件都被列出（广度优先），再按剩余空间给符号。
    # 反过来做会出现「backend 看全了、frontend 整块消失」，而「有哪些文件」
    # 恰恰是地图最主要的用途；符号少一点还能用 grep_code 补。
    order = sorted(grouped, key=lambda p: (p.count("/") + (0 if p == "." else 1), p))

    def render(cap: int):
        blocks, cost = [], 0
        for parent in order:
            entries = grouped[parent]
            head = "%s/  ·  %d 个文件" % (parent, len(entries))
            rows = [head]
            block_cost = len(head) + 1
            for relpath, line_count, symbols in entries:
                name = os.path.basename(relpath)
                if cap and symbols:
                    trimmed = [s if len(s) <= SYMBOL_MAX_CHARS else s[:SYMBOL_MAX_CHARS] + "…"
                               for s in symbols[:cap]]
                    extra = len(symbols) - cap
                    row = "  %-22s %5d 行  %s%s" % (
                        name, line_count, "、".join(trimmed),
                        ("  +%d" % extra) if extra > 0 else "",
                    )
                else:
                    row = "  %-22s %5d 行" % (name, line_count)
                rows.append(row)
                block_cost += len(row) + 1
            blocks.append("\n".join(rows))
            cost += block_cost + 1
        return blocks, cost

    budget = max_chars - len(header) - len(hint) - 2
    blocks, used, omitted = [], 0, 0

    for cap in (MAX_SYMBOLS_PER_FILE, 8, 6, 4, 2, 1, 0):
        blocks, used = render(cap)
        if used <= budget:
            break

    if used > budget:
        # 连「文件名 + 行数」都放不下（仓库特别大）：按顺序裁剪，并如实说明少了多少
        blocks, used, omitted = [], 0, 0
        for parent in order:
            entries = grouped[parent]
            head = "%s/  ·  %d 个文件" % (parent, len(entries))
            rows, block_cost = [head], len(head) + 1
            for relpath, line_count, _symbols in entries:
                row = "  %-22s %5d 行" % (os.path.basename(relpath), line_count)
                if used + block_cost + len(row) + 1 > budget:
                    omitted += 1
                    continue
                rows.append(row)
                block_cost += len(row) + 1
            if len(rows) > 1:
                blocks.append("\n".join(rows))
                used += block_cost + 1
            else:
                omitted += len(entries)

    shown = sum(len(block.split("\n")) - 1 for block in blocks)
    parts = [header, "\n".join(blocks)]
    if omitted:
        parts.append("……（还有 %d 个文件未展示，可用 path 缩小范围或看 list_dir）" % omitted)
    parts.append(hint)

    return ToolResult(True, "\n".join(parts), {
        "path": rel,
        "files": len(files),
        "shown": shown,
        "total_lines": total_lines,
        "truncated": bool(omitted or skipped),
    })


# ---------------------------------------------------------------- 注册（默认不可见）

register(Tool(
    name="repo_map",
    description=(
        "生成一份压缩的项目地图：目录结构 + 每个源码文件的行数与顶层函数/类名。"
        "不熟悉仓库时第一步就该调用它，比逐个 list_dir 高效得多。只读。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "限定范围的子目录或文件，默认整个工作区"},
            "max_chars": {"type": "integer", "description": "输出字符上限，默认 4000，最大 8000",
                          "minimum": 400, "maximum": 8000},
        },
        "required": [],
        "additionalProperties": False,
    },
    handler=_repo_map,
))

register(Tool(
    name="git_status",
    description=(
        "查看当前分支、HEAD 与未提交的改动清单。改代码前先调用它，"
        "避免覆盖用户尚未提交的工作。只读。"
    ),
    parameters={"type": "object", "properties": {}, "required": [],
                "additionalProperties": False},
    handler=_git_status,
))

register(Tool(
    name="git_diff",
    description=(
        "查看未提交的差异（默认工作区相对暂存区）。用于确认「刚才那步到底改了什么」，"
        "或了解用户已有的改动。只读。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "只看这个文件或目录的差异"},
            "staged": {"type": "boolean", "description": "看已暂存的差异（相当于 --cached）"},
            "stat_only": {"type": "boolean", "description": "只看统计（相当于 --stat），省上下文"},
        },
        "required": [],
        "additionalProperties": False,
    },
    handler=_git_diff,
))

register(Tool(
    name="git_log",
    description=(
        "查看最近的提交记录（oneline + 标签）。想了解某个文件的历史时传 path。只读。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "条数，默认 10，最多 30",
                      "minimum": 1, "maximum": 30},
            "path": {"type": "string", "description": "限定某个文件或目录的历史"},
        },
        "required": [],
        "additionalProperties": False,
    },
    handler=_git_log,
))
