"""第 5 章（第二节）验收：往 jsonl 里追加一条记忆，再按用处捞回来。

对应正文「动手练」第 1、2 题：写一条带 usage 的记忆、读所有 usage 含 code 的条目。

为什么是 jsonl 而不是 json
--------------------------
追加一条不用重写整个文件。记忆是越攒越多的，每加一条就 `json.load` 一遍
全量再 `dump` 回去，文件大起来之后会明显卡。一行一条还有个附带好处：
某一行写坏了，其它行还能读。

为什么用临时文件测
------------------
验收不能动学生自己的 `memories.jsonl`——那是他做第 1 题的手工产物。
函数行为写在系统临时目录里，学生那份单独作为最后一条检查。
"""

import json
import os
import sys
import tempfile

from _kit import expect_callable, read_text, run

# 正文第 1 题要存的那条
CONTENT = "让模型帮你改代码时，必须用 black 格式化"
USAGE = ["code", "format"]


def _temp_path():
    handle, path = tempfile.mkstemp(prefix="mem_", suffix=".jsonl")
    os.close(handle)
    os.remove(path)          # 让 append_memory 自己创建，顺便验证它能建文件
    return path


def _drop(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _entries(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        lines = [line for line in handle.read().splitlines() if line.strip()]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            raise AssertionError("文件里有一行不是合法 JSON：%r" % line[:80])
    return out


def writes(module, project_dir):
    path = _temp_path()
    try:
        fn = expect_callable(module, "append_memory")
        fn(path, CONTENT, USAGE)
        fn(path, "回复要先给结论", ["chat"])
        entries = _entries(path)
        if len(entries) != 2:
            return False, "写了两次，文件里应该有 2 行，实际 %d 行（是追加，不是覆盖）" % len(entries)
        first = entries[0]
        if not isinstance(first, dict):
            return False, "每一行应该是一个 JSON 对象，第一行是 %s" % type(first).__name__
        if first.get("content") != CONTENT:
            return False, "content 不对：%r" % (first.get("content"),)
        usage = first.get("usage")
        if sorted(usage or []) != sorted(USAGE):
            return False, "usage 应该是 %r，现在是 %r" % (USAGE, usage)
        stamp = first.get("timestamp") or first.get("time") or first.get("created_at")
        if not isinstance(stamp, str) or not stamp.strip():
            return False, "缺时间戳。字段名叫 timestamp / time / created_at 都行，但要有。"
        return True, ""
    finally:
        _drop(path)


def searches(module, project_dir):
    path = _temp_path()
    try:
        fn = expect_callable(module, "append_memory")
        fn(path, CONTENT, USAGE)
        fn(path, "回复要先给结论", ["chat"])
        fn(path, "格式化前先备份", ["code"])
        search = expect_callable(module, "search_memories")
        got = search(path, "code")
        if not isinstance(got, list):
            return False, "search_memories 应该返回列表，现在返回 %s" % type(got).__name__
        if got != [CONTENT, "格式化前先备份"]:
            return False, "usage 含 code 的两条应该按写入顺序返回，现在返回 %r" % (got,)
        return True, ""
    finally:
        _drop(path)


def tolerates_blank(module, project_dir):
    """文件里有空行不该炸：手工编辑 jsonl 时末尾多一个换行是常态。"""
    path = _temp_path()
    try:
        fn = expect_callable(module, "append_memory")
        fn(path, CONTENT, USAGE)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n\n")
        search = expect_callable(module, "search_memories")
        try:
            got = search(path, "code")
        except Exception as exc:
            return False, "遇到空行抛了 %s。空行应该跳过。" % type(exc).__name__
        return got == [CONTENT], "空行之后读出来是 %r" % (got,)
    finally:
        _drop(path)


def hand_written(module, project_dir):
    """学生自己那份 memories.jsonl：第 1 题的手工产物。"""
    text, error = read_text(
        project_dir, "memories.jsonl",
        "第 1 题要求把那条「必须用 black 格式化」存进这个文件。")
    if text is None:
        return False, error
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            return False, "memories.jsonl 里有一行不是合法 JSON：%r" % line[:80]
    if not entries:
        return False, "文件是空的。"
    hit = [entry for entry in entries
           if isinstance(entry, dict) and "code" in (entry.get("usage") or [])]
    if not hit:
        return False, "没有一条的 usage 里含 \"code\"。第 1 题要求 usage 同时含 code 和 format。"
    return True, ""


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [], extra=[
        ("写两条是追加不是覆盖", writes),
        ("按 usage 捞得回来", searches),
        ("空行不影响读取", tolerates_blank),
        ("memories.jsonl 里有那条 code 记忆", hand_written),
    ])
