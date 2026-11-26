"""第 27 章验收：写一个规则判分函数 price_check()。

对应正文「动手练」第 1 题：判断字符串是不是「含『元』字、且元字前面是数字」
的价格描述。正文给的三个反例是全篇的重点——规则判分的价值不在于判对正例，
而在于**能稳定地判错错例**，所以这里正例只放三条，反例放六条。

不测第 2、3 题：那两道要调模型打分，验收里没有模型可用。
"""

import sys

from _kit import expect_callable, run

# (输入, 期望)。正例故意包含小数（0.5 元是真实价格），
# 反例覆盖「只有元」「没有元」「中文数字」「元在数字前面」四种常见漏判。
CASES = [
    ("总价3999元", True),
    ("价格：99元", True),
    ("单价 0.5元", True),
    ("3999", False),
    ("元", False),
    ("99块", False),
    ("九十九元", False),
    ("元3999", False),
    ("", False),
]


def price_check(module):
    fn = expect_callable(module, "price_check")
    wrong = []
    for text, want in CASES:
        try:
            got = fn(text)
        except Exception as exc:
            wrong.append("%r 抛异常（%s）" % (text, type(exc).__name__))
            continue
        if bool(got) != want:
            wrong.append("%r 应该是 %s，返回了 %r" % (text, want, got))
    return not wrong, "；".join(wrong[:5])


def returns_bool(module):
    """返回值要真是布尔，不是字符串——`return "True"` 在 if 里永远为真。

    这是规则判分最常见的隐形坑：判错逻辑没错，但函数永远返回真值。
    """
    fn = expect_callable(module, "price_check")
    bad = []
    for text, want in CASES:
        got = fn(text)
        if not isinstance(got, bool) or got != want:
            bad.append("%r → %r（%s）" % (text, got, type(got).__name__))
    return not bad, "返回值不是布尔或判断不对：" + "；".join(bad[:4])


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("九条正反例全判对", price_check),
        ("返回的是布尔值", returns_bool),
    ])
