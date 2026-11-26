"""第 1 章验收：估算 token 数，并比较同一段意思的中英文。

对应正文「动手练」第 1、2 题：数一数占几个 token、中英文差多少。

为什么不直接调 tiktoken
----------------------
`tiktoken` 第一次用要联网下载词表，验收环境常常没有网；而且这一章的重点
不是"背下 cl100k 的分词结果"，是「中文和英文的 token 比例不一样」这件事。

所以验收只考两件不依赖网络的事：
1. 估算时**区分了中英文**（拿 `len(text)` 当 token 数的写法会在这里翻车）；
2. 百分比算对了（`(en - zh) / zh * 100`）。

估算规则由学生自己定，只要对同一段文本稳定、且英文明显更省。真实项目里
还是该用 tiktoken，md 里已经写清楚了。
"""

import sys

from _kit import expect_callable, run

# 同一句需求的中英文。中文 30 字左右，英文 80 字符左右——按「中文一字
# 一 token、英文四字符一 token」的常见估算，英文约是中文的三分之二。
ZH = "帮我把这段 Python 代码里的变量名改成驼峰命名，逻辑一点都不要改。"
EN = "Rename the variables in this Python code to camelCase, and do not change the logic at all."

ZH2 = "这个接口超时了，先看看是不是网络的问题，再决定要不要重试。"
EN2 = "The request timed out. Check the network first, then decide whether to retry."


def _numbers(result):
    """把返回值摊平成 (zh, en, pct)。字段名容错，类型不容错。"""
    if not isinstance(result, dict):
        raise AssertionError("compare_tokens 返回了 %s，应该返回字典。"
                             % type(result).__name__)
    zh = result.get("zh", result.get("zh_tokens"))
    en = result.get("en", result.get("en_tokens"))
    pct = result.get("diff_pct", result.get("percent"))
    for label, value in (("中文 token 数", zh), ("英文 token 数", en)):
        # bool 是 int 的子类，`True` 混进来会让后面的比较看着像通过
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise AssertionError("%s 应该是正整数，现在是 %r。" % (label, value))
    if not isinstance(pct, (int, float)) or isinstance(pct, bool):
        raise AssertionError("diff_pct 应该是数字，现在是 %r。" % (pct,))
    return zh, en, float(pct)


def basic(module):
    fn = expect_callable(module, "compare_tokens")
    zh, en, pct = _numbers(fn(ZH, EN))
    problems = []
    if zh <= en:
        problems.append("中文估了 %d、英文估了 %d：这段英文明显更省 token，"
                        "如果你的估算里英文反而更多，多半是把字符数当成了 token 数" % (zh, en))
    # 百分比基准允许两种：相对中文（正文的算法），或相对英文（也有人这么写）
    by_zh = (en - zh) / float(zh) * 100
    by_en = (en - zh) / float(en) * 100
    if abs(pct - by_zh) > 1.5 and abs(pct - by_en) > 1.5:
        problems.append("diff_pct 是 %.1f，按 (英文-中文)/中文 应该是 %.1f、"
                        "按 (英文-中文)/英文 应该是 %.1f" % (pct, by_zh, by_en))
    return not problems, "；".join(problems)


def another_pair(module):
    """换一段话也要成立：不能只对第一条样例调好参数。"""
    fn = expect_callable(module, "compare_tokens")
    zh, en, pct = _numbers(fn(ZH2, EN2))
    if zh <= en:
        return False, "第二组里中文 %d、英文 %d，英文应该更省 token" % (zh, en)
    return pct < 0, "英文更省时 diff_pct 应该是负数，现在是 %.1f" % pct


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("返回两个正整数和一个百分比", basic),
        ("换一组中英文结论一致", another_pair),
    ])
