"""第 10 章验收：算清一段文本花多少钱，再对比两个模型。

对应正文「动手练」第 1、2 题：`count_gpt_cost(text)`、`compare_cost(text)`，
两个函数名正文里给了。

价格用正文写的：GPT-4o-mini 输入 $0.15 / 1M token、输出 $0.60 / 1M token。
这里只算输入侧——第 1 题的"处理该文本的预估费用"就是拿输入价乘 token 数。

为什么允许粗略估算 token
------------------------
`tiktoken` 要联网下载词表，验收环境没有。所以只要你的估算**随文本变长而
变大**、量级对得上（不是把 $0.15 当成 0.15 美元），就算过。
真实项目里当然该用真 tokenizer——md 里写了。
"""

import sys

from _kit import expect_callable, run

SHORT = "hello"
LONG = "帮我把这段 Python 代码改成异步，并补上异常处理和重试逻辑。" * 20


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssertionError("应该返回一个数字，现在返回了 %r（%s）"
                             % (value, type(value).__name__))
    return float(value)


def single(module):
    fn = expect_callable(module, "count_gpt_cost")
    got = _number(fn(SHORT))
    if got <= 0:
        return False, "「hello」的费用应该是一个很小的正数，现在是 %r" % (got,)
    # 1M token 才 $0.15，一段几十字的文本不可能到 1 美分
    if got > 0.01:
        return False, ("「hello」算出 %r 美元。GPT-4o-mini 是 $0.15/1M token，"
                       "几十个字符连一美分的零头都不到——是不是把每 token 价格算成每千了？"
                       % (got,))
    return True, ""


def grows(module):
    fn = expect_callable(module, "count_gpt_cost")
    small = _number(fn(SHORT))
    big = _number(fn(LONG))
    return big > small, "文本长了 20 倍，费用却从 %r 变成 %r。应该一起涨。" % (small, big)


def compares(module):
    fn = expect_callable(module, "compare_cost")
    got = fn(LONG)
    if not isinstance(got, dict):
        return False, "返回了 %s，应该返回字典" % type(got).__name__
    names = [str(key).lower() for key in got]
    problems = []
    if not any("gpt" in name for name in names):
        problems.append("没有 gpt-4o-mini 这一项")
    if not any("claude" in name for name in names):
        problems.append("没有 claude-3-haiku 这一项")
    values = []
    for value in got.values():
        try:
            values.append(_number(value))
        except AssertionError:
            problems.append("有一项的费用不是数字：%r" % (value,))
    if len(values) >= 2 and len(set(round(value, 12) for value in values)) == 1:
        problems.append("两个模型的费用完全一样。两家的价格不同，说明有一边写死了。")
    if values and any(value <= 0 for value in values):
        problems.append("有一项的费用不是正数：%r" % (values,))
    return not problems, "；".join(problems)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("短文本费用是合理的小数", single),
        ("文本变长费用变高", grows),
        ("两个模型的价格不相同", compares),
    ])
