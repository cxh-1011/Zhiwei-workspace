"""第 8 章验收：从一条失败日志里抽出一道测试题。

对应正文「动手练」第 2、3 题：
2. 用 `extract_test_case` 把一条日志变成结构化 JSON；
3. 把结果加进 `test_cases.json`（数组）。

正文第 1 题（翻日志找 3 条）没有产物，不在这里验。

为什么缺字段要给空串
--------------------
真实日志的字段是残缺的：有的只有 input 没有 expected，有的是老版本格式。
一个缺 `expected_output` 就 KeyError 的抽取器，面对一个几百 MB 的日志目录
根本跑不完第二遍。
"""

import json
import sys

from _kit import expect_callable, read_json, run

KEYS = ("input", "expected_output", "actual_output", "failure_reason")

LOG = {
    "input": "把这段同步请求改成异步",
    "output": "import requests\nresp = requests.get(url)",
    "expected_output": "应该用 aiohttp 或 httpx.AsyncClient",
    "error": "TimeoutError: 请求超过 30 秒",
}

PARTIAL = {"input": "查一下订单状态", "error": "KeyError: 'order_id'"}


def extracts(module):
    fn = expect_callable(module, "extract_test_case")
    got = fn(dict(LOG))
    if not isinstance(got, dict):
        return False, "返回了 %s，应该返回字典" % type(got).__name__
    missing = [key for key in KEYS if key not in got]
    if missing:
        return False, "少了这几个键：%s" % "、".join(missing)
    problems = []
    if got.get("input") != LOG["input"]:
        problems.append("input 没带过来")
    if got.get("actual_output") != LOG["output"]:
        problems.append("actual_output 应该来自日志的 output")
    if got.get("expected_output") != LOG["expected_output"]:
        problems.append("expected_output 没带过来")
    if "TimeoutError" not in str(got.get("failure_reason")):
        problems.append("failure_reason 应该来自日志的 error")
    return not problems, "；".join(problems)


def tolerates_missing(module):
    """缺字段用空串补，不要 KeyError。"""
    fn = expect_callable(module, "extract_test_case")
    try:
        got = fn(dict(PARTIAL))
    except Exception as exc:
        return False, "日志缺 output 和 expected_output 时抛了 %s。应该用空串补上。" % (
            type(exc).__name__)
    problems = []
    if not isinstance(got, dict):
        return False, "返回了 %s，应该返回字典" % type(got).__name__
    for key in KEYS:
        if key not in got:
            problems.append("缺键 %s" % key)
    if got.get("expected_output") != "":
        problems.append("缺 expected_output 时应该是空串，现在是 %r" % (got.get("expected_output"),))
    return not problems, "；".join(problems)


def no_mutation(module):
    """不要改传进来的 log：抽题是只读操作。"""
    fn = expect_callable(module, "extract_test_case")
    original = dict(LOG)
    fn(original)
    return original == LOG, "传进去的日志被改了：%r" % (original,)


def cases_file(module, project_dir):
    data, error = read_json(
        project_dir, "test_cases.json",
        "第 3 题要求把抽出来的题存成这个文件，最外层是数组。")
    if data is None:
        return False, error
    if not isinstance(data, list):
        return False, "最外层应该是数组（[...]），现在是 %s" % type(data).__name__
    if not data:
        return False, "数组是空的，至少要有 1 道题。"
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            return False, "第 %d 项不是对象" % (index + 1)
        missing = [key for key in KEYS if key not in entry]
        if missing:
            return False, "第 %d 项少了：%s" % (index + 1, "、".join(missing))
    return True, ""


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("完整日志抽出四个字段", extracts),
        ("缺字段补空串不报错", tolerates_missing),
        ("不改传进来的日志", no_mutation),
    ], extra=[("test_cases.json 是合法的题目数组", cases_file)])
