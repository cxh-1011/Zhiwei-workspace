"""第 11 章验收：写一个 parse_react()，把 ReAct 文本拆成三个字段。

对应正文「动手练」第 2 题：
- 输入一段含 `Thought:` / `Action:` / `Action Input:` 的文本；
- 输出字典 `{"thought": ..., "action": ..., "action_input": ...}`；
- 大小写、前后空格、换行都要容错。

刻意不考「第 3 题的循环」：那需要调用模型，验收里跑不通，
而且会让学生为了通过检查去拼一个假的模型客户端。
"""

import sys

from _kit import expect_callable, run, why

SAMPLE = """Thought: 用户想知道明天上海的天气，需要查实时数据
Action: search
Action Input: Shanghai weather tomorrow"""


def _get(fn, text):
    result = fn(text)
    if not isinstance(result, dict):
        raise AssertionError("parse_react 返回了 %s，应该返回字典。" % type(result).__name__)
    return {str(key).lower(): ("" if value is None else str(value)) for key, value in result.items()}


def basic(module):
    fn = expect_callable(module, "parse_react")
    data = _get(fn, SAMPLE)
    problems = []
    if "查实时数据" not in data.get("thought", ""):
        problems.append("thought 没抓到（现在：%r）" % data.get("thought"))
    if data.get("action", "").strip() != "search":
        problems.append("action 应该是 search（现在：%r）" % data.get("action"))
    if "Shanghai weather tomorrow" not in data.get("action_input", ""):
        problems.append("action_input 没抓到（现在：%r）" % data.get("action_input"))
    return not problems, "；".join(problems)


def case_insensitive(module):
    """大小写和多余空格：真实模型输出 `Thought:` 和 `THOUGHT:` 都会出现。"""
    fn = expect_callable(module, "parse_react")
    text = "  thought:   先查文档\nACTION:  fetch\n \n action input :   https://example.com/docs  "
    data = _get(fn, text)
    problems = []
    if "先查文档" not in data.get("thought", ""):
        problems.append("小写 thought 没认出来")
    if data.get("action", "").strip().lower() != "fetch":
        problems.append("大写 ACTION 没认出来（现在：%r）" % data.get("action"))
    if "example.com/docs" not in data.get("action_input", ""):
        problems.append("action input（中间带空格）没认出来（现在：%r）" % data.get("action_input"))
    return not problems, "；".join(problems)


def multiline(module):
    """值里换行要能留住：Action Input 常常是一段 JSON，跨好几行。"""
    fn = expect_callable(module, "parse_react")
    text = 'Thought: 需要带参数请求\nAction: http_post\nAction Input: {"city": "北京",\n  "days": 3}'
    data = _get(fn, text)
    got = data.get("action_input", "")
    ok = '"city"' in got and '"days"' in got
    return ok, "多行 Action Input 只取到了 %r" % got[:80]


def missing_fields(module):
    """字段缺失时返回空串（或 None → 空串），不要抛 KeyError。

    正文只说了「三字段」，没说缺了怎么办；但一个真会用在循环里的解析器，
    拿到半截输出就崩会让整轮对话断掉，所以这里要求它是「软失败」。
    """
    fn = expect_callable(module, "parse_react")
    try:
        data = _get(fn, "Thought: 只有思考，没有动作")
    except AssertionError:
        raise
    except Exception as exc:
        return False, "缺字段时直接抛异常了（%s: %s）。应返回空字符串的字段。" % (
            why(exc), str(exc)[:80])
    ok = data.get("action", "") == "" and data.get("action_input", "") == ""
    return ok, "缺字段的项应该是空串，现在是 action=%r、action_input=%r" % (
        data.get("action"), data.get("action_input"))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("基本样例能拆出三个字段", basic),
        ("大小写与多余空格容错", case_insensitive),
        ("多行 Action Input 不截断", multiline),
        ("缺字段时返回空串而不是报错", missing_fields),
    ])
