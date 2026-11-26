"""第 4 章（第二节）验收：判断这一轮该不该停。

对应正文「动手练」第 2 题：在循环里加一行终止检测。

正文给的判据是 `"查不到就放弃" in content or "已拿到答案" in content`，
验收在这两条之外加了 ReAct 的 `Final Answer:`——三个都是正文出现过的
「这一轮结束了」的信号。

为什么单独考这个小函数
----------------------
终止检测写错有两种后果，都很难查：
- 太松：认了半句「我已经拿到答案了，但是……」→ 提前退出，用户拿到半截答案；
- 太严：模型说了「查不到就放弃」你却没认出来 → 转圈直到轮次上限。

所以验收里既有该停的，也有**看着像但不该停**的（"答案已经拿到了"）。
"""

import sys

from _kit import expect_callable, run

STOP = [
    "查不到就放弃",
    "已拿到答案",
    "Final Answer: 是，明天上海 32 度",
    "Thought: 已经查到\nFinal Answer: 查不到就放弃",
]

GO_ON = [
    "Thought: 还需要再查一次天气接口",
    "Action: search\nAction Input: Shanghai weather",
    "答案已经拿到了，但我要再确认一下",   # 没有「已拿到答案」这四个字的连续匹配
    "",
]


def stops(module):
    fn = expect_callable(module, "should_stop")
    wrong = []
    for text in STOP:
        try:
            got = fn(text)
        except Exception as exc:
            wrong.append("%r 抛了 %s" % (text[:20], type(exc).__name__))
            continue
        if not got:
            wrong.append("%r 应该停" % text[:20])
    return not wrong, "；".join(wrong)


def goes_on(module):
    fn = expect_callable(module, "should_stop")
    wrong = []
    for text in GO_ON:
        try:
            got = fn(text)
        except Exception as exc:
            wrong.append("%r 抛了 %s" % (text[:20], type(exc).__name__))
            continue
        if got:
            wrong.append("%r 不该停（这轮还要继续）" % text[:20])
    return not wrong, "；".join(wrong)


def returns_bool(module):
    fn = expect_callable(module, "should_stop")
    bad = [text[:16] for text in STOP + GO_ON if not isinstance(fn(text), bool)]
    return not bad, "返回值不是布尔：%r" % (bad[:3],)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("四个终止信号都认", stops),
        ("四个继续信号不误停", goes_on),
        ("返回的是布尔值", returns_bool),
    ])
