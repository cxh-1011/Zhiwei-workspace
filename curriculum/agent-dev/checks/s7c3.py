"""第 7 章（第三节）验收：危险工具先停下来问人。

对应正文「动手练」第 1、2、3 题：
1. 给 `delete_user_data` 加上 `"is_dangerous": True`；
2. 拿到 tool_call 后先查这个标记，有就不执行，打印审批提示；
3. 写 `resume_after_approval(tool_call)`，审批通过后再真的执行。

第 3 题的函数名正文里给了，验收就按这个名字。

为什么拆成两个函数
------------------
`needs_approval` 决定"要不要停"，`resume_after_approval` 决定"批了之后怎么走"。
合在一起写的人，通常会在"停了之后怎么恢复"这一步卡住——因为参数已经被
解析过一次，恢复时又要再解析一遍。
"""

import sys

from _kit import expect_callable, run

DANGEROUS = {"name": "delete_user_data", "description": "删除用户数据",
             "is_dangerous": True}
SAFE = {"name": "get_weather", "description": "查天气"}

CALL = {"id": "call_1", "type": "function",
        "function": {"name": "delete_user_data", "arguments": '{"user": "u789"}'}}


def approval_needed(module):
    fn = expect_callable(module, "needs_approval")
    problems = []
    for tool, want, label in ((DANGEROUS, True, "带 is_dangerous 的工具"),
                              (SAFE, False, "普通工具"),
                              ({"name": "x", "is_dangerous": False}, False, "显式 False")):
        got = fn(tool)
        if got is not want:
            problems.append("%s：应该 %s，返回 %r（要布尔值）" % (label, want, got))
    return not problems, "；".join(problems)


def resumes(module):
    fn = expect_callable(module, "resume_after_approval")
    got = fn(CALL)
    if not isinstance(got, str):
        return False, "返回了 %s，应该返回一句执行结果（字符串）" % type(got).__name__
    problems = []
    if "已执行" not in got:
        problems.append("返回值里要有「已执行」，让人一眼看出这步真跑了")
    if "u789" not in got:
        problems.append("返回值里要带上参数里的用户 id（u789），否则不知道删的是谁")
    return not problems, "；".join(problems)


def bad_arguments(module):
    """参数是半截 JSON 时不能静默「执行成功」。"""
    fn = expect_callable(module, "resume_after_approval")
    bad = {"id": "call_2", "function": {"name": "delete_user_data",
                                        "arguments": '{"user": '}}
    try:
        got = fn(bad)
    except Exception:
        return True, ""
    text = str(got)
    return "已执行" not in text, "参数解析不了却返回了「已执行」：%r" % (text[:80],)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("三种工具的审批判断正确", approval_needed),
        ("审批后能执行并带上参数", resumes),
        ("参数坏了不假装执行成功", bad_arguments),
    ])
