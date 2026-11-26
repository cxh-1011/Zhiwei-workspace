"""第 4 章（第三节）验收：三道锁——token、时间、轮次。

对应正文「动手练」第 1、3 题（第 2 题的 timeout 要真发请求，不在这里验）。
三道上限的判断合成一个函数：给定已用量和上限，返回先撞上的那一项。

优先级是固定的：token → 时间 → 轮次。
这不是随便定的——token 超了会直接报错（钱已经花了），时间超了用户已经在等，
轮次超了只是"该换个问法"。先报最贵的那个。
"""

import sys

from _kit import expect_callable, run

# (已用 token, 已用秒, 已用轮次, 期望)
CASES = [
    (0, 0, 0, None),
    (1999, 29, 2, None),
    (2000, 1, 1, "token"),
    (5000, 1, 1, "token"),
    (100, 30, 1, "time"),
    (100, 1, 3, "turns"),
    (5000, 60, 5, "token"),   # 三个都超时，报 token
    (100, 60, 5, "time"),     # 时间先于轮次
]

ALIASES = {
    "token": ("token", "tokens", "预算"),
    "time": ("time", "seconds", "超时", "时间"),
    "turns": ("turn", "轮次", "轮"),
}


def _name(result):
    if result is None:
        return None
    text = str(result).lower()
    for key, words in ALIASES.items():
        if any(word in text for word in words):
            return key
    return text


def budget(module):
    fn = expect_callable(module, "check_budget")
    wrong = []
    for tokens, seconds, turns, want in CASES:
        try:
            got = _name(fn(tokens, seconds, turns))
        except Exception as exc:
            wrong.append("(%d, %d, %d) 抛了 %s" % (tokens, seconds, turns, type(exc).__name__))
            continue
        if got != want:
            wrong.append("(%d token, %d 秒, %d 轮) 应该报 %r，实际 %r"
                         % (tokens, seconds, turns, want, got))
    return not wrong, "；".join(wrong[:3])


def custom_limits(module):
    """上限要能从参数改：写死 2000 的话，换一个便宜模型就不对了。"""
    fn = expect_callable(module, "check_budget")
    try:
        got = _name(fn(500, 1, 1, max_tokens=400))
    except TypeError:
        return False, ("check_budget 要接受 max_tokens 参数。签名照 md 里那六个，"
                       "不要把上限写死在函数里。")
    if got != "token":
        return False, "max_tokens=400 时 500 个 token 已经超了，返回了 %r" % (got,)
    # 反过来：上限放大之后，同样用量不该再报
    if _name(fn(500, 1, 1, max_tokens=10000)) is not None:
        return False, "max_tokens=10000 时 500 个 token 不该报超限"
    return True, ""


def still_running(module):
    """没超的时候必须明确返回 None，不能返回空字符串之外的东西。"""
    fn = expect_callable(module, "check_budget")
    got = fn(0, 0, 0)
    return got is None, "刚开始跑就返回了 %r，应该是 None" % (got,)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("八种用量组合都判对", budget),
        ("上限可以从参数改", custom_limits),
        ("没超时返回 None", still_running),
    ])
