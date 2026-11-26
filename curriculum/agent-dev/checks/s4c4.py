"""第 4 章（第四节）验收：把一条 messages 链还原成「调了哪些工具、带什么参数」。

对应正文「动手练」第 3 题：手动补一条 assistant 的 tool_calls 历史，再跑一轮，
看模型第二轮怎么答。

正文的做法是"看输出"，验收改成"看结构"——因为要看懂一次运行，最先要能
从 messages 里读出「第几步调了谁、参数是什么」。这也是日志和 trace 回放的
基础（第 26 章会用到同一个形状）。

只认 assistant 消息里的 tool_calls；tool 消息里的内容属于工具返回，不在这里。
"""

import json
import sys

from _kit import expect_callable, run

ONE = [
    {"role": "user", "content": "帮我查下今天北京天气"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "get_weather", "arguments": '{"city": "北京"}'}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": '{"temp": 30}'},
    {"role": "assistant", "content": "北京今天 30 度"},
]

TWO = [
    {"role": "user", "content": "查北京和上海的天气"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "get_weather", "arguments": '{"city": "北京"}'}},
        {"id": "call_2", "type": "function",
         "function": {"name": "get_weather", "arguments": '{"city": "上海"}'}}]},
]

BROKEN = [
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_9", "type": "function",
         "function": {"name": "get_weather", "arguments": '{"city": '}}]},
]


def _check_steps(steps, expect, label):
    if not isinstance(steps, list):
        raise AssertionError("返回了 %s，应该返回列表。" % type(steps).__name__)
    if len(steps) != len(expect):
        return False, "%s：应该有 %d 步，实际 %d 步（%r）" % (
            label, len(expect), len(steps), steps)
    problems = []
    for index, (step, want) in enumerate(zip(steps, expect)):
        if not isinstance(step, dict):
            problems.append("第 %d 步不是字典（%r）" % (index + 1, step))
            continue
        name = step.get("tool", step.get("name"))
        if name != want[0]:
            problems.append("第 %d 步的工具是 %r，应该是 %r" % (index + 1, name, want[0]))
        if step.get("id") != want[1]:
            problems.append("第 %d 步的 id 是 %r，应该是 %r" % (index + 1, step.get("id"), want[1]))
        args = step.get("args", step.get("arguments"))
        if args != want[2]:
            problems.append("第 %d 步的参数是 %r，应该是 %r" % (index + 1, args, want[2]))
    return not problems, "%s：%s" % (label, "；".join(problems))


def single(module):
    fn = expect_callable(module, "extract_steps")
    return _check_steps(fn(ONE), [("get_weather", "call_1", {"city": "北京"})], "单次调用")


def parallel(module):
    """一条消息里两个 tool_call：要出两步，不是一个。"""
    fn = expect_callable(module, "extract_steps")
    return _check_steps(fn(TWO), [
        ("get_weather", "call_1", {"city": "北京"}),
        ("get_weather", "call_2", {"city": "上海"}),
    ], "并行调用")


def broken_args(module):
    """arguments 是半截 JSON：args 给 None，整条链不能因此崩掉。

    真实的 trace 里这种记录一定有——模型输出被截断、日志写了一半。
    一个会在解析失败时抛异常的还原函数，等于在最需要它的时候不能用。
    """
    fn = expect_callable(module, "extract_steps")
    try:
        steps = fn(BROKEN)
    except Exception as exc:
        return False, "参数是半截 JSON 时抛了 %s。应该把 args 设为 None 继续往下走。" % (
            type(exc).__name__)
    if not isinstance(steps, list) or len(steps) != 1:
        return False, "应该还原出 1 步，实际是 %r" % (steps,)
    args = steps[0].get("args", steps[0].get("arguments"))
    return args is None, "解析不了时 args 应该是 None，现在是 %r" % (args,)


def no_tool(module):
    fn = expect_callable(module, "extract_steps")
    steps = fn([{"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好，有什么可以帮你？"}])
    return steps == [], "没有工具调用的对话应该返回空列表，现在返回 %r" % (steps,)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("单次调用还原一步", single),
        ("一条消息里两个调用还原两步", parallel),
        ("半截参数不崩、args 为 None", broken_args),
        ("没有工具调用返回空列表", no_tool),
    ])
