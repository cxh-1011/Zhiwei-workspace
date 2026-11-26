"""第 6 章验收：按句子切分，并判断切出来的是不是完整句子。

对应正文「动手练」第 1、2 题。

第 1 题的「做完标准」给了一个很具体的映射：

    "第一步：打开电源；第二步：按下启动键。"
    → ["第一步：", "打开电源；", "第二步：", "按下启动键。"]

这不是随便写的：冒号和分号在中文里既可能是句内标点（"价格：12元"），
也可能是步骤之间的分隔（"第一步："后面直接换行）。正文的提示是「先找带
空格/换行的分号/冒号」——所以验收里同时放了「该切」和「不该切」两类输入。

第 2 题是一个配套的小判断：切出来的东西得是完整句子，否则下游做检索时
会拿到一堆残句。
"""

import sys

from _kit import expect_callable, run

SENTENCES = [
    ("你好！今天天气不错。你呢？", ["你好！", "今天天气不错。", "你呢？"], "三个句末标点"),
    ("第一行\n第二行。", ["第一行", "第二行。"], "换行也是边界"),
    ("", [], "空字符串"),
]

# 冒号/分号细分。单独一项，挂掉不影响上面三条。
STEP = ("第一步：打开电源；第二步：按下启动键。",
        ["第一步：", "打开电源；", "第二步：", "按下启动键。"])

# 不该在「价格：12元」这里切开
INLINE = ("价格：12元，库存：3件。", ["价格：12元，库存：3件。"])

COMPLETE = [
    ("你好！", True),
    ("第一行。", True),
    ("你好", False),
    ("", False),
    ("。", False),
    ("价格：12元", False),
]


def _split(module, text):
    fn = expect_callable(module, "split_by_sentence")
    got = fn(text)
    if not isinstance(got, list):
        raise AssertionError("split_by_sentence 返回了 %s，应该返回列表。"
                             % type(got).__name__)
    return [str(item) for item in got]


def sentences(module):
    problems = []
    for text, want, label in SENTENCES:
        got = _split(module, text)
        if got != want:
            problems.append("%s：得到 %r，应该是 %r" % (label, got, want))
    return not problems, "；".join(problems)


def steps(module):
    text, want = STEP
    got = _split(module, text)
    return got == want, "得到 %r。期望 %r——「第一步：」后面要断开，" \
                        "因为它后面跟的是内容而不是同一句话。" % (got, want)


def inline(module):
    """句内冒号不能切：「价格：12元，库存：3件。」是一句话。"""
    text, want = INLINE
    got = _split(module, text)
    return got == want, "得到 %r。这一句里的冒号是句内标点，不该切——" \
                        "正文的提示是「先找带空格/换行的分号/冒号」。" % (got,)


def complete(module):
    fn = expect_callable(module, "is_complete_sentence")
    problems = []
    for text, want in COMPLETE:
        got = fn(text)
        if got is not want:
            problems.append("%r 应该 %s，返回了 %r" % (text, want, got))
    return not problems, "；".join(problems)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("三种文本切分正确", sentences),
        ("步骤间的冒号分号要切开", steps),
        ("句内冒号不切", inline),
        ("六个样例的完整性判断正确", complete),
    ])
