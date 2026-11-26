"""第 3 章（第三节）验收：解析 arguments 并校验必需字段。

对应正文「动手练」第 1、2 题：
1. 让第一次调用必报错，第二次输出里补上 `unit`；
2. 在 `response2` 后加一层 `json.loads` 校验，确认它输出的是合法 JSON。

这两题合起来就是「拿到模型给的参数后，先验再调」这一步。正文第 3 题让模型
把「上海」改成「shanghai」，也是靠这层校验发现「参数不合法」。

为什么要求抛出异常而不是返回 None
----------------------------------
这个函数在循环里的用法是 `args = parse_arguments(...)` 然后直接 `fn(**args)`。
返回 None 会在下一行变成一个看不出源头的 TypeError；抛 ValueError 能让
「解析失败」和「参数缺失」都被 except 接住，然后决定是重试还是放弃。
"""

import json
import sys

from _kit import expect_callable, run

CASES_OK = [
    ('{"city": "北京"}', {"city": "北京"}, "只有一个参数"),
    ('{"city": "上海", "unit": "c"}', {"city": "上海", "unit": "c"}, "两个参数"),
    ({"city": "上海"}, {"city": "上海"}, "已经是字典"),
]

CASES_BAD = [
    ('{"unit": "c"}', "缺 city"),
    ('{"city": ""}', "city 是空串"),
    ("{'city': '北京'}", "用了单引号"),
    ('{"city": ', "截断的 JSON"),
    ("", "空字符串"),
]


def _raised(fn, raw):
    try:
        fn(raw, required=("city",))
    except Exception as exc:  # noqa: BLE001 —— 抛什么类型不重要，抛了就行
        return True, "%s" % type(exc).__name__
    return False, ""


def accepts(module):
    fn = expect_callable(module, "parse_arguments")
    problems = []
    for raw, want, label in CASES_OK:
        try:
            got = fn(raw, required=("city",))
        except Exception as exc:
            problems.append("%s：抛了 %s（%s）" % (label, type(exc).__name__, str(exc)[:60]))
            continue
        if got != want:
            problems.append("%s：返回 %r，应该是 %r" % (label, got, want))
    return not problems, "；".join(problems)


def rejects(module):
    fn = expect_callable(module, "parse_arguments")
    problems = []
    for raw, label in CASES_BAD:
        ok, name = _raised(fn, raw)
        if not ok:
            problems.append("%s：应该报错，却安静地返回了" % label)
    return not problems, "；".join(problems)


def extra_required(module):
    """required 要真的被用上：传 ("city", "unit") 时缺 unit 也算不合格。"""
    fn = expect_callable(module, "parse_arguments")
    try:
        fn('{"city": "北京"}', required=("city", "unit"))
    except Exception:
        return True, ""
    return False, ('required=("city", "unit") 时，只有 city 的输入应该报错。'
                   '别把 required 写死成 ["city"]。')


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("三种合法输入都能解析", accepts),
        ("五种坏输入都报错", rejects),
        ("required 参数真的起作用", extra_required),
    ])
