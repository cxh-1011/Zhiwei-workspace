"""第 7 章（第四节）验收：从一个 JSON 行日志里回放一条链路。

对应正文「动手练」第 3 题：写 `replay_trace(trace_id, log_file)`，把该
trace_id 的所有步骤按时间排序取出来。

第 1 题（改成 jsonlogger）和第 2 题（给 run_step 加超时）不单独验：
前者要装第三方包，后者在 Windows 上没有 `signal.alarm`。但第 3 题能跑的前提
就是日志已经是「一行一个 JSON」，所以日志格式这一条被包含在回放里了。

为什么要求跳过坏行
------------------
日志文件是边写边读的，最后一行经常是半截（进程被杀、磁盘满）。一条坏行
就让整个回放报错，等于在最需要看日志的时候看不了。
"""

import json
import os
import sys
import tempfile

from _kit import expect_callable, run, why

LINES = [
    {"ts": "2026-11-20T10:00:01", "trace_id": "test-123", "step": "read_file",
     "status": "error", "error": "No such file"},
    {"ts": "2026-11-20T10:00:00", "trace_id": "test-123", "step": "parse_args",
     "status": "ok"},
    {"ts": "2026-11-20T10:00:02", "trace_id": "other-9", "step": "write_file",
     "status": "ok"},
]


def _write(lines, extra=""):
    handle, path = tempfile.mkstemp(prefix="trace_", suffix=".log")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        for line in lines:
            stream.write(json.dumps(line, ensure_ascii=False) + "\n")
        stream.write(extra)
    return path


def replays(module):
    fn = expect_callable(module, "replay_trace")
    path = _write(LINES)
    try:
        got = fn("test-123", path)
    finally:
        os.remove(path)
    if not isinstance(got, list):
        return False, "返回了 %s，应该返回列表" % type(got).__name__
    if len(got) != 2:
        return False, "test-123 有 2 条记录，返回了 %d 条" % len(got)
    steps = [entry.get("step") if isinstance(entry, dict) else None for entry in got]
    if steps != ["parse_args", "read_file"]:
        return False, ("应该按时间升序：parse_args（10:00:00）在前，read_file 在后，"
                       "实际 %r。日志里是先写的 read_file，所以不能只看文件顺序。" % (steps,))
    return True, ""


def survives_broken_line(module):
    fn = expect_callable(module, "replay_trace")
    path = _write(LINES, extra='{"ts": "2026-11-20T10:00:03", "trace_id": "test-1')
    try:
        got = fn("test-123", path)
    finally:
        os.remove(path)
    return len(got) == 2, "最后一行是半截 JSON，应该跳过它继续读，实际返回 %d 条" % len(got)


def no_match(module):
    fn = expect_callable(module, "replay_trace")
    path = _write(LINES)
    try:
        got = fn("nobody", path)
    finally:
        os.remove(path)
    return got == [], "没有匹配的 trace_id 应该返回空列表，实际 %r" % (got,)


def missing_file(module):
    """文件不存在时返回空列表，别把 traceback 甩给用户。"""
    fn = expect_callable(module, "replay_trace")
    try:
        got = fn("test-123", os.path.join(tempfile.gettempdir(), "no_such_trace.log"))
    except Exception as exc:
        return False, "文件不存在时抛了 %s。日志滚掉、路径写错都很常见，应该返回空列表。" % (
            why(exc))
    return got == [], "文件不存在应该返回空列表，实际 %r" % (got,)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("按时间升序回放两条", replays),
        ("跳过半截 JSON", survives_broken_line),
        ("没有匹配返回空列表", no_match),
        ("日志文件不存在不崩", missing_file),
    ])
