"""第 10 章（第二节）验收：把用户反馈归类，再定优先级。

对应正文「动手练」第 1、2 题：按「功能缺失 / 回答错误 / 太慢」三类填表，
按「是否多人重复提 + 是否阻断主流程」打高/中/低。

这两件事平时靠人做，但把它们写成函数有实际好处：反馈一多，分类靠手感就会
不一致，同一个问题今天算「太慢」明天算「回答错误」，排序也就没意义了。

判定规则（md 里也写了）：
- 太慢：等、秒、卡、慢、延迟、转圈；
- 回答错误：错、不对、胡说、答错、算错；
- 功能缺失：能不能、希望、支持、加个、要是能。
只命中一类就返回它；都没命中返回 `"其他"`。
"""

import sys

from _kit import expect_callable, run, why

CASE = [
    ("每次点运行都要等 30 秒，太慢了", "太慢"),
    ("它把 7*8 算成了 54，明显算错了", "回答错误"),
    ("能不能加一个导出 PDF 的按钮", "功能缺失"),
    ("希望支持批量上传文件", "功能缺失"),
    ("回答里引用的文档根本不存在，胡说的", "回答错误"),
    ("转圈转了快一分钟才出来", "太慢"),
    ("界面挺好看的", "其他"),
]

# (重复提, 阻断主流程, 期望)
LEVELS = [
    (True, True, "高"),
    (True, False, "中"),
    (False, True, "中"),
    (False, False, "低"),
]


def _category(value):
    text = str(value or "").strip()
    for name in ("功能缺失", "回答错误", "太慢", "其他"):
        if name in text:
            return name
    return text


def _level(value):
    text = str(value or "").strip()
    for name in ("高", "中", "低"):
        if name in text:
            return name
    return text


def classifies(module):
    fn = expect_callable(module, "classify_feedback")
    wrong = []
    for text, want in CASE:
        try:
            got = _category(fn(text))
        except Exception as exc:
            wrong.append("%r 抛了 %s" % (text[:16], why(exc)))
            continue
        if got != want:
            wrong.append("%r → %r，应该是 %s" % (text[:16], got, want))
    return not wrong, "；".join(wrong[:3])


def levels(module):
    fn = expect_callable(module, "priority")
    wrong = []
    for repeat, blocks, want in LEVELS:
        try:
            got = _level(fn(repeat, blocks))
        except Exception as exc:
            wrong.append("(%s, %s) 抛了 %s" % (repeat, blocks, why(exc)))
            continue
        if got != want:
            wrong.append("(重复=%s, 阻断=%s) → %r，应该是 %s"
                         % (repeat, blocks, got, want))
    return not wrong, "；".join(wrong)


def two_false(module):
    """两样都不占的时候必须是「低」，不能含糊成「中」。

    这条单独列，是因为最常见的写法是 `if repeat or blocks: 中`，
    它把「低」也吞掉了——最后每条反馈都至少是「中」，排序等于没排。
    """
    fn = expect_callable(module, "priority")
    got = _level(fn(False, False))
    return got == "低", "两样都不占时应该是「低」，返回了 %r" % (got,)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("七条反馈都归对类", classifies),
        ("四组优先级判断正确", levels),
        ("两样都不占是低优先级", two_false),
    ])
