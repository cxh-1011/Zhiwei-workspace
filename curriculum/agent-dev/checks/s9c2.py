"""第 9 章（第二节）验收：交接时有没有把关键限定丢掉。

对应正文「动手练」第 2 题：

> 如果 `a_resp` 包含「℃」或「摄氏度」，但 `b_msg` 的 instruction 里没提
> 「温度单位」，就 print「⚠️ B 指令未覆盖单位，可能丢失精度」。

这道题的场景很具体，但它指向的是一类通病：A 算出了带单位/带精度/带前提的
结果，B 只拿到一句话，润色完单位没了，下游当成纯数字用。

所以验收把它包成一个函数：给 A 的回答和 B 的指令，返回警告或 None。
"""

import sys

from _kit import expect_callable, run, why

CASES = [
    ("上海明天 32℃，多云", "把这段话润色得更口语一点", "单位", "A 给了摄氏度，B 的指令没说保留单位"),
    ("气温 32摄氏度", "润色一下", "单位", "「摄氏度」也算温度单位"),
    ("上海明天 32℃，多云", "润色一下，保留温度和单位", None, "指令里提到了单位"),
    ("上海明天很热，注意防暑", "润色一下", None, "A 的回答里没有具体温度"),
    ("", "润色", None, "空回答"),
]


def warns(module):
    fn = expect_callable(module, "check_handoff")
    problems = []
    for a_resp, b_instruction, want, label in CASES:
        try:
            got = fn(a_resp, b_instruction)
        except Exception as exc:
            problems.append("%s：抛了 %s" % (label, why(exc)))
            continue
        if want is None:
            if got is not None:
                problems.append("%s：不该警告，返回了 %r" % (label, got))
        else:
            if not isinstance(got, str) or want not in got:
                problems.append("%s：应该返回一句含「%s」的警告，返回了 %r"
                                % (label, want, got))
    return not problems, "；".join(problems[:3])


def no_warning_is_none(module):
    """没问题时必须返回 None，不能返回空字符串——`if warning:` 判不出来。"""
    fn = expect_callable(module, "check_handoff")
    got = fn("上海很热", "润色")
    return got is None, "没有问题时应该返回 None，现在返回 %r" % (got,)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("五组交接判断正确", warns),
        ("没问题时返回 None", no_warning_is_none),
    ])
