"""工作区写入工具：write_file（新建/整体重写）与 edit_file（按锚点局部修改）。

三条底线，缺一不可：
1. 路径判定复用 _guard.py 的同一套沙箱——只读、写入用的必须是同一个边界，
   否则会出现「读被拦了、写没拦」。
2. 落盘前先备份到 backups/agent-edits/<run_id>/NNN__<path>，所以任何一次改动都能整轮回滚。
   backups/ 本身在沙箱黑名单里，模型读不到，也就没法把备份内容当成源码再改一遍。
3. 每次写入都产出一份统一 diff，前端直接渲染成红绿行；模型自己也拿一小段
   用来核对缩进（完整 diff 只给用户看，不塞进上下文）。

行尾处理：原文件是 CRLF 就继续写 CRLF，避免整文件「每一行都变了」的假 diff。
"""

import difflib
import hashlib
import os
import re
import shutil
import time

from . import _guard
from .base import Tool, ToolResult, register

# 单次运行（一次提问）的写入预算：文件数、总字节、单文件字节
MAX_FILES_PER_RUN = 20
MAX_BYTES_PER_RUN = 1024 * 1024
MAX_WRITE_BYTES = _guard.MAX_WRITE_BYTES

MAX_DIFF_LINES = 160
MAX_DIFF_CHARS = 6000
MAX_DIFF_IN_TOOL_RESULT = 1400

# 一次运行的计数状态。生命周期只有一次请求，超过窗口自动清理。
_RUN_STATE: dict[str, dict] = {}
_RUN_TTL = 6 * 3600.0

CODE_EDIT_TOOLS = ("write_file", "edit_file")


# ---------------------------------------------------------------- 运行预算

def _run_state(run_id: str) -> dict:
    now = time.time()
    if len(_RUN_STATE) > 40:
        for key, item in list(_RUN_STATE.items()):
            if now - item["stamp"] > _RUN_TTL:
                _RUN_STATE.pop(key, None)
    state = _RUN_STATE.get(run_id)
    if state is None:
        state = {"files": set(), "edits": 0, "bytes": 0, "seq": 0, "stamp": now}
        _RUN_STATE[run_id] = state
    state["stamp"] = now
    return state


def _kb(size: int) -> int:
    """向上取整到 KB：刚超 200KB 时显示 201 而不是「200 超过 200」。"""
    return max(1, (size + 1023) // 1024)


def _budget_error(state: dict, rel: str, size: int) -> str:
    if rel not in state["files"] and len(state["files"]) >= MAX_FILES_PER_RUN:
        return ("本次运行已改动 %d 个文件，达到上限（%d）。"
                "请先停下来说明还差哪些改动，由用户发起新一轮。" % (len(state["files"]), MAX_FILES_PER_RUN))
    if state["bytes"] + size > MAX_BYTES_PER_RUN:
        return ("本次运行累计写入将达 %d KB，超过 %d KB 上限。请缩小改动范围。"
                % (_kb(state["bytes"] + size), _kb(MAX_BYTES_PER_RUN)))
    return ""


# ---------------------------------------------------------------- 备份

def backup_root() -> str:
    """备份目录的绝对路径。用函数而不是常量，便于测试指向临时工作区。"""
    return os.path.join(_guard.WORKSPACE_ROOT, "backups", "agent-edits")


def _safe_run_id(run_id: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_-]", "", str(run_id or ""))[:40]
    return cleaned or "manual"


def _backup_file(run_id: str, target: str, rel: str, seq: int) -> str:
    """把即将被覆盖的文件复制一份，返回工作区相对路径（写进 result_data 供回滚用）。"""
    root = os.path.join(backup_root(), _safe_run_id(run_id))
    os.makedirs(root, exist_ok=True)
    flat = rel.replace("/", "__").replace("\\", "__")
    if len(flat) > 120:
        stem, ext = os.path.splitext(flat)
        flat = stem[:110] + "_" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8] + ext
    dest = os.path.join(root, "%03d__%s" % (seq, flat))
    shutil.copy2(target, dest)
    return _guard.display(os.path.relpath(dest, _guard.WORKSPACE_ROOT))


# ---------------------------------------------------------------- 文本与 diff

def _read_text(path: str) -> str:
    # newline="" 保留原始 \r\n，方便后面按原样写回
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _match_newlines(old_text: str, new_text: str) -> str:
    """新内容沿用原文件的行尾，避免 CRLF 文件被改成 LF 后出现整文件 diff。"""
    flat = _normalize(new_text)
    if "\r\n" in old_text:
        return flat.replace("\n", "\r\n")
    return flat


def _text_hash(text: str) -> str:
    return hashlib.sha1(_normalize(text).encode("utf-8")).hexdigest()[:16]


def build_diff(rel: str, old_text: str, new_text: str) -> tuple[str, int, int, bool]:
    """返回 (diff 文本, 新增行数, 删除行数, 是否被截断)。"""
    old_lines = _normalize(old_text).splitlines()
    new_lines = _normalize(new_text).splitlines()
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines, fromfile="a/" + rel, tofile="b/" + rel, lineterm="", n=3,
    ))
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

    truncated = False
    if len(diff_lines) > MAX_DIFF_LINES:
        diff_lines = diff_lines[:MAX_DIFF_LINES]
        truncated = True
    text = "\n".join(diff_lines)
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS]
        truncated = True
    if truncated:
        text += "\n…（diff 过长，已截断；完整内容以文件为准）"
    return text, added, removed, truncated


def _hunk_preview(diff: str) -> str:
    if len(diff) <= MAX_DIFF_IN_TOOL_RESULT:
        return diff
    return diff[:MAX_DIFF_IN_TOOL_RESULT] + "\n…（此处只给片段，完整 diff 已展示在页面上）"


def _apply_write(run_id: str, rel: str, target: str, old_text: str, new_text: str, existed: bool,
                 state: dict) -> ToolResult:
    """备份 → 写盘 → 生成 diff。写入失败时把备份还原回去，不留半截文件。"""
    backup_rel = None
    seq = state["seq"] + 1
    if existed:
        try:
            backup_rel = _backup_file(run_id, target, rel, seq)
        except OSError as exc:
            return ToolResult(False, "", error="备份失败，已取消写入（不会在没有备份的情况下改文件）：%s" % exc)

    try:
        _write_text(target, new_text)
    except OSError as exc:
        if backup_rel:
            try:
                shutil.copy2(os.path.join(_guard.WORKSPACE_ROOT, backup_rel), target)
            except OSError:
                pass
        return ToolResult(False, "", error="写入失败：%s" % exc)

    state["seq"] = seq
    state["edits"] += 1
    state["bytes"] += len(new_text.encode("utf-8"))
    state["files"].add(rel)

    diff, added, removed, truncated = build_diff(rel, old_text, new_text)
    data = {
        "path": rel,
        "created": not existed,
        "added": added,
        "removed": removed,
        "bytes": len(new_text.encode("utf-8")),
        "backup": backup_rel,
        "after_hash": _text_hash(new_text),
        "diff": diff,
        "diff_truncated": truncated,
    }

    head = ("已新建 %s（%d 行，%d 字节，+%d）" % (rel, new_text.count("\n") + (0 if new_text.endswith("\n") else 1), data["bytes"], added)
            if not existed else
            "已写入 %s（+%d −%d 行，%d 字节，已备份）" % (rel, added, removed, data["bytes"]))
    body = _hunk_preview(diff)
    return ToolResult(True, head + "\n" + body, data)


# ---------------------------------------------------------------- write_file

def _write_file(args: dict, context: dict) -> ToolResult:
    rel = str(args.get("path", "")).strip()
    content = args.get("content")
    if not rel:
        return ToolResult(False, "", error="需要提供 path 参数")
    if not isinstance(content, str):
        return ToolResult(False, "", error="content 必须是字符串")

    try:
        target = _guard.resolve(rel, write=True)
    except ValueError as exc:
        return ToolResult(False, "", error=str(exc))

    rel = _guard.display(_guard.rel_inside(target))
    existed = os.path.isfile(target)

    if not existed:
        # 新建时只能写文本：.png / .exe 这类后缀不该由 write_file 产生，
        # 模型想「贴一段 base64 造图片」会得到一张坏文件，不如直接拒绝。
        ext = os.path.splitext(target)[1].lower()
        if ext in _guard.SKIP_SUFFIXES:
            return ToolResult(False, "", error="write_file 只能写文本，不能生成 %s 文件" % ext)

    old_text = ""
    if existed:
        denied = _guard.reject_non_text(target)
        if denied:
            return ToolResult(False, "", error="%s：%s，拒绝整体重写" % (rel, denied))
        try:
            if os.path.getsize(target) > _guard.MAX_FILE_BYTES:
                return ToolResult(False, "", error="目标文件过大（超过 %d KB），请改用 edit_file 局部修改"
                                  % _kb(_guard.MAX_FILE_BYTES))
            old_text = _read_text(target)
        except UnicodeDecodeError:
            return ToolResult(False, "", error="目标不是 UTF-8 文本文件，拒绝改写（避免破坏编码）")
        except OSError as exc:
            return ToolResult(False, "", error="读取原文件失败：%s" % exc)

    new_text = _match_newlines(old_text, content) if existed else _normalize(content)
    size = len(new_text.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        return ToolResult(False, "", error="内容过大（%d KB），单文件上限 %d KB。请拆分或改用 edit_file 局部修改"
                          % (_kb(size), _kb(MAX_WRITE_BYTES)))

    if existed and _normalize(old_text) == _normalize(new_text):
        return ToolResult(True, "内容与磁盘上完全一致，未写盘：%s" % rel,
                          {"path": rel, "changed": False, "added": 0, "removed": 0})

    state = _run_state(str(context.get("run_id") or ""))
    budget = _budget_error(state, rel, size)
    if budget:
        return ToolResult(False, "", error=budget)

    return _apply_write(str(context.get("run_id") or ""), rel, target, old_text, new_text, existed, state)


# ---------------------------------------------------------------- edit_file

def _occurrence_lines(text: str, needle: str, limit: int = 5) -> list[int]:
    lines, start = [], 0
    while True:
        index = text.find(needle, start)
        if index < 0 or len(lines) >= limit:
            return lines
        lines.append(text.count("\n", 0, index) + 1)
        start = index + max(1, len(needle))


def _edit_file(args: dict, context: dict) -> ToolResult:
    rel = str(args.get("path", "")).strip()
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    replace_all = bool(args.get("replace_all", False))

    if not rel:
        return ToolResult(False, "", error="需要提供 path 参数")
    if not isinstance(old_string, str) or not isinstance(new_string, str):
        return ToolResult(False, "", error="old_string 与 new_string 必须是字符串")
    if not old_string:
        return ToolResult(False, "", error="old_string 不能为空；新建文件请用 write_file")

    try:
        target = _guard.resolve(rel, write=True)
    except ValueError as exc:
        return ToolResult(False, "", error=str(exc))

    if not os.path.isfile(target):
        return ToolResult(False, "", error="文件不存在：%s。新建文件请用 write_file" % rel)
    rel = _guard.display(_guard.rel_inside(target))

    denied = _guard.reject_non_text(target)
    if denied:
        return ToolResult(False, "", error="%s：%s，无法按锚点编辑" % (rel, denied))

    try:
        if os.path.getsize(target) > _guard.MAX_FILE_BYTES:
            return ToolResult(False, "", error="目标文件过大（%s），超过 %s，无法整体读入后替换"
                              % (_guard.human_size(os.path.getsize(target)),
                                 _guard.human_size(_guard.MAX_FILE_BYTES)))
        old_text = _read_text(target)
    except UnicodeDecodeError:
        return ToolResult(False, "", error="目标不是 UTF-8 文本文件，拒绝修改")
    except OSError as exc:
        return ToolResult(False, "", error="读取文件失败：%s" % exc)

    current = _normalize(old_text)
    needle = _normalize(old_string)
    replacement = _normalize(new_string)
    if needle == replacement:
        return ToolResult(False, "", error="old_string 与 new_string 相同，没有任何改动")

    count = current.count(needle)
    if count == 0:
        return ToolResult(
            False, "",
            error=("没有找到 old_string。请先用 read_file 看清原文，再逐字复制（包括缩进与空行）；"
                   "不要凭记忆拼写，也不要传入整段带省略号的内容。"),
        )
    if count > 1 and not replace_all:
        lines = _occurrence_lines(current, needle)
        return ToolResult(
            False, "",
            error=("old_string 在文件中出现了 %d 次（第 %s 行），锚点不唯一，无法确定改哪一处。"
                   "请带上更多上下文让它唯一，或确认后再用 replace_all=true。"
                   % (count, "、".join(str(n) for n in lines))),
        )

    updated = current.replace(needle, replacement) if replace_all else current.replace(needle, replacement, 1)
    new_text = updated.replace("\n", "\r\n") if "\r\n" in old_text else updated
    size = len(new_text.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        return ToolResult(False, "", error="修改后文件过大（%d KB），单文件上限 %d KB"
                          % (_kb(size), _kb(MAX_WRITE_BYTES)))

    state = _run_state(str(context.get("run_id") or ""))
    budget = _budget_error(state, rel, max(0, size - len(old_text.encode("utf-8"))))
    if budget:
        return ToolResult(False, "", error=budget)

    result = _apply_write(str(context.get("run_id") or ""), rel, target, old_text, new_text, True, state)
    if result.ok:
        replaced = count if replace_all else 1
        result.data["replacements"] = replaced
        result.content = ("已修改 %s（替换 %d 处，+%d −%d 行，已备份）\n%s"
                          % (rel, replaced, result.data["added"], result.data["removed"],
                             _hunk_preview(result.data["diff"])))
    return result


# ---------------------------------------------------------------- 回滚

def restore_edits(steps: list[dict]) -> dict:
    """按 agent_steps 逆序把一次运行里的改动全部还原。

    逆序是关键：同一个文件被改两次时，先还第二次的备份、再还第一次的备份，
    结果才是会话开始前的样子。新建的文件没有备份，直接删掉。
    """
    root = os.path.realpath(backup_root())
    restored, removed, skipped = [], [], []

    edits = [
        step for step in steps
        if step.get("kind") == "tool_call" and step.get("tool") in CODE_EDIT_TOOLS and step.get("ok")
    ]

    for step in reversed(edits):
        data = step.get("result_data") or {}
        if not isinstance(data, dict):
            data = {}
        rel = str(data.get("path") or "").strip()
        if not rel:
            skipped.append({"path": "", "reason": "这条记录缺少文件路径"})
            continue
        try:
            target = _guard.resolve(rel, write=True)
        except ValueError as exc:
            skipped.append({"path": rel, "reason": str(exc)})
            continue

        expected = data.get("after_hash")
        if expected and os.path.isfile(target):
            try:
                if _text_hash(_read_text(target)) != expected:
                    skipped.append({"path": rel, "reason": "改动之后文件又被修改过，为避免覆盖没有还原"})
                    continue
            except (OSError, UnicodeDecodeError):
                skipped.append({"path": rel, "reason": "当前文件无法读取，跳过"})
                continue

        if data.get("created"):
            try:
                if os.path.isfile(target):
                    os.remove(target)
                    removed.append(rel)
            except OSError as exc:
                skipped.append({"path": rel, "reason": "删除失败：%s" % exc})
            continue

        backup_rel = str(data.get("backup") or "").strip()
        if not backup_rel:
            skipped.append({"path": rel, "reason": "没有备份记录（可能是早期版本）"})
            continue
        backup_abs = os.path.realpath(os.path.join(_guard.WORKSPACE_ROOT, backup_rel))
        if not backup_abs.startswith(root + os.sep) or not os.path.isfile(backup_abs):
            skipped.append({"path": rel, "reason": "备份文件不存在或不在备份目录内"})
            continue
        try:
            shutil.copy2(backup_abs, target)
            restored.append(rel)
        except OSError as exc:
            skipped.append({"path": rel, "reason": "还原失败：%s" % exc})

    return {
        "restored": sorted(set(restored)),
        "removed": sorted(set(removed)),
        "skipped": skipped,
        "changed": bool(restored or removed),
    }


# ---------------------------------------------------------------- 注册（默认不可见）

register(Tool(
    name="write_file",
    description=(
        "在工作区内新建文件，或用完整内容整体覆盖某个文件。写入前会自动备份，改动会以 diff 展示给用户。"
        "已有文件的小改动请优先用 edit_file，不要整文件重写。不能改 .env、数据库、依赖目录和备份目录。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "工作区相对路径，例如 backend/tools/example.py"},
            "content": {"type": "string", "description": "完整文件内容；行尾会自动沿用原文件风格"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
    handler=_write_file,
))

register(Tool(
    name="edit_file",
    description=(
        "对工作区内某个文本文件做局部修改：把唯一的 old_string 替换成 new_string。"
        "old_string 必须逐字复制原文（含缩进），且在文件中只出现一次，否则会报错并给出行号。"
        "写入前自动备份，改动会以 diff 展示给用户。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "工作区相对路径"},
            "old_string": {"type": "string", "description": "要被替换的原文片段，必须唯一且逐字一致"},
            "new_string": {"type": "string", "description": "替换后的内容，不需要缩进对齐以外的额外格式"},
            "replace_all": {
                "type": "boolean",
                "description": "是否替换全部匹配项，默认 false；只有确实要批量替换时才设为 true",
            },
        },
        "required": ["path", "old_string", "new_string"],
        "additionalProperties": False,
    },
    handler=_edit_file,
))
