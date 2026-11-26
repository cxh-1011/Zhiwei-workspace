"""第 6 章（第二节）验收：算余弦相似度，并按相似度排序。

对应正文「动手练」第 1、2 题：换 5 段文本算排序、打印第一个向量看看。

`embed` 由验收传进来，不让脚本自己加载 `sentence-transformers`：
那个模型有几百 MB，验收环境装不上，装上了也要下载权重。这一章的重点是
「向量就是数字，比大小就完了」——向量从哪来是下一章的事。

所以验收给一个写着查表的假 embed，只考三件真本事：
1. 余弦公式对不对（含零向量的处理）；
2. 是不是按相似度从高到低排；
3. 返回的是**原始下标**，不是排完的文本。
"""

import sys

from _kit import expect_callable, run, why

# 受控向量：甲和乙都朝 x 轴，丙朝 y 轴，"查询" 也是 x 轴。
# 夹角越小越像，所以 甲(0) → 乙(1) → 丙(2)。
VECTORS = {
    "甲": [1.0, 0.0, 0.0],
    "乙": [0.8, 0.2, 0.0],
    "丙": [0.0, 1.0, 0.0],
    "查询": [1.0, 0.0, 0.0],
}


def fake_embed(text):
    return VECTORS[text]


def cosine_cases(module):
    fn = expect_callable(module, "cosine")
    cases = [
        ([1, 0], [2, 0], 1.0, "同向"),
        ([1, 0], [0, 1], 0.0, "垂直"),
        ([1, 0], [-1, 0], -1.0, "反向"),
        ([0, 0], [1, 0], 0.0, "零向量不除零"),
    ]
    problems = []
    for a, b, want, label in cases:
        try:
            got = fn(a, b)
        except Exception as exc:
            problems.append("%s：抛了 %s" % (label, why(exc)))
            continue
        if not isinstance(got, (int, float)) or abs(float(got) - want) > 1e-6:
            problems.append("%s：%r，应该是 %s" % (label, got, want))
    return not problems, "；".join(problems)


def ranks(module):
    fn = expect_callable(module, "rank_by_similarity")
    problems = []
    for texts, want, label in (
        (["甲", "乙", "丙"], [0, 1, 2], "正序"),
        (["丙", "乙", "甲"], [2, 1, 0], "输入的文本顺序被打乱"),
    ):
        got = fn(texts, "查询", fake_embed)
        if not isinstance(got, list):
            problems.append("%s：返回了 %s，应该返回下标列表" % (label, type(got).__name__))
            continue
        # 允许两种写法：只给下标，或者给 (下标, 分数) 元组
        cleaned = [entry[0] if isinstance(entry, tuple) else entry for entry in got]
        if cleaned != want:
            problems.append("%s：得到 %r，应该是 %r（按相似度从高到低）" % (label, cleaned, want))
    return not problems, "；".join(problems)


def keeps_all(module):
    """每段文本都要出现一次：排完只剩前两名的实现，会让后面几段永远召不回来。"""
    fn = expect_callable(module, "rank_by_similarity")
    texts = ["甲", "乙", "丙", "乙", "甲"]
    got = fn(texts, "查询", fake_embed)
    cleaned = [entry[0] if isinstance(entry, tuple) else entry for entry in got]
    if sorted(cleaned) != list(range(len(texts))):
        return False, "下标应该正好是 0~%d 各一次，现在是 %r" % (len(texts) - 1, cleaned)
    return True, ""


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("余弦四种情形都对", cosine_cases),
        ("按相似度降序返回下标", ranks),
        ("所有下标都保留、不重不漏", keeps_all),
        # embed 是以参数传进去的函数，JS 那边接不住
    ], languages=("python",))
