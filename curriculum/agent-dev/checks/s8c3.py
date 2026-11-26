"""第 8 章（第三节）验收：新旧两版结果并排看，找出行为变了的那几道。

对应正文「动手练」第 3 题：并排看「是否调工具 / 字段是否齐全 / 代码是否有语法错误」，
输出 `regression_report.md`。

真跑两版提示词要连模型，验收连不上。但「比较」这一步能测：给定两份结果，
找出行为和内容变了的题。这也是回归脚本里最容易写错的一段——写错了会报
"没变化"，然后你就放心地把一个坏提示词发布了。

第 1、2 题的三份产物（`regression_questions.txt`、`old_results.json`、
`new_results.json`）作为文件检查。
"""

import json
import os
import sys

from _kit import expect_callable, read_json, run

OLD = [
    {"question": "查上海天气", "content": "", "tool_calls": [{"name": "get_weather"}]},
    {"question": "把这段改成异步", "content": "用 asyncio.gather", "tool_calls": []},
    {"question": "解释一下 GIL", "content": "全局解释器锁", "tool_calls": []},
]
NEW = [
    # 第一题：不再调工具了
    {"question": "查上海天气", "content": "上海今天 22 度", "tool_calls": []},
    # 第二题：没变
    {"question": "把这段改成异步", "content": "用 asyncio.gather", "tool_calls": []},
    # 第三题：内容变了
    {"question": "解释一下 GIL", "content": "GIL 是 CPython 的一把大锁", "tool_calls": []},
]

WANT = {"查上海天气": "tool_calls", "解释一下 GIL": "content"}


def _index(result):
    out = {}
    for entry in result or []:
        if isinstance(entry, dict):
            out[entry.get("question")] = [str(word) for word in entry.get("changed") or []]
    return out


def differences(module):
    fn = expect_callable(module, "compare_results")
    got = fn(OLD, NEW)
    if not isinstance(got, list):
        return False, "返回了 %s，应该返回列表" % type(got).__name__
    index = _index(got)
    problems = []
    if "把这段改成异步" in index:
        problems.append("第二题两版完全一样，不该出现在差异里")
    for question, word in WANT.items():
        if question not in index:
            problems.append("「%s」变了（%s），没被报出来" % (question, word))
        elif not any(word in item for item in index[question]):
            problems.append("「%s」的 changed 里应该有 %s，现在是 %r"
                            % (question, word, index[question]))
    return not problems, "；".join(problems)


def no_change(module):
    fn = expect_callable(module, "compare_results")
    got = fn(OLD, list(OLD))
    return got == [], "两版一样时应该返回空列表，现在返回 %r" % (got,)


def different_length(module):
    """新版少了一道题，不能 IndexError。"""
    fn = expect_callable(module, "compare_results")
    try:
        got = fn(OLD, NEW[:2])
    except Exception as exc:
        return False, "新版比旧版短时抛了 %s。少一道题本身也是一种差异，不该崩。" % (
            type(exc).__name__)
    return isinstance(got, list), "返回了 %s，应该返回列表" % type(got).__name__


def questions_file(module, project_dir):
    path = os.path.join(project_dir, "regression_questions.txt")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        return False, "没找到 regression_questions.txt（第 1 题要求存 5 道题）。%s" % exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return len(lines) == 5, "应该正好 5 行（每题一行），现在是 %d 行" % len(lines)


def results_files(module, project_dir):
    problems = []
    for name in ("old_results.json", "new_results.json"):
        data, error = read_json(project_dir, name,
                                "第 2 题要求把两版结果各存一份，每条含 question。")
        if data is None:
            problems.append(error)
            continue
        if not isinstance(data, list) or len(data) != 5:
            problems.append("%s 应该是 5 条记录的数组，现在是 %r"
                            % (name, type(data).__name__ if not isinstance(data, list) else len(data)))
            continue
        if not all(isinstance(item, dict) and "question" in item for item in data):
            problems.append("%s 里每条都要有 question 字段" % name)
    return not problems, "；".join(problems)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("两处变化都能找出来", differences),
        ("没变化时返回空列表", no_change),
        ("新旧数量不同也不崩", different_length),
    ], extra=[
        ("regression_questions.txt 正好 5 行", questions_file),
        ("新旧结果各 5 条", results_files),
    ])
