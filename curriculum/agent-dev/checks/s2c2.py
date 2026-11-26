"""第 2 章（第二节）验收：判断一个少样本示例干不干净。

对应正文「动手练」第 2、3 题：挑出会带坏模型的示例、写干净的示例。

正文把「坏示例」的特征写得很具体，这里就按那几条判：
- 输出里混进了额外信息（湿度、建议、评价），也就是比输入长出一大截；
- 输出里带括号注释；
- 输出里带指令（"请翻译成…""注意…""不要…"）。

所以判分不要求你认出"哪句话是编的"，只要求你对这几种形态稳定地说「脏」。
这也是提示词 lint 的真实写法：先挡住明显的，剩下的交给人看。
"""

import sys

from _kit import expect_callable, run

CLEAN = [
    {"input": "查上海今天温度", "output": "22℃"},
    {"input": "上海今天几度？", "output": "22℃"},
    {"input": "temperature of Shanghai today", "output": "22"},
]

DIRTY = [
    # B 款：多出了湿度、适宜度——正文第 2 题的标准答案
    {"input": "上海今天几度？",
     "output": "上海今天气温 22℃，空气湿度 65%，适合户外活动。"},
    # 括号注释
    {"input": "translate: 我们下周见", "output": "See you next week（正式场合，不要用 see ya）"},
    # 指令混进输出
    {"input": "把这句话翻译成英文", "output": "请翻译成正式英语：Hello"},
]

# 输出长度超过输入的这么多倍，就认定混进了额外信息。
# 干净示例里最长的 "22℃" 只有输入的 1/4，留了很宽的余量。
LONG_RATIO = 2.5


def _field(example, *names):
    if not isinstance(example, dict):
        raise AssertionError("每个示例应该是 {'input': ..., 'output': ...} 字典，"
                             "现在传进来的是 %s。" % type(example).__name__)
    for name in names:
        if name in example:
            return str(example[name] or "")
    return ""


def _verdict(fn, example):
    got = fn(example)
    if not isinstance(got, bool):
        raise AssertionError("is_clean_example 应该返回 True/False，现在返回了 %r。" % (got,))
    return got


def clean_cases(module):
    fn = expect_callable(module, "is_clean_example")
    wrong = []
    for example in CLEAN:
        if not _verdict(fn, example):
            wrong.append("%r → 判成了脏" % example["output"])
    return not wrong, "；".join(wrong)


def dirty_cases(module):
    fn = expect_callable(module, "is_clean_example")
    wrong = []
    for example in DIRTY:
        if _verdict(fn, example):
            wrong.append("%r → 判成了干净" % example["output"][:40])
    return not wrong, "；".join(wrong)


def ratio_hint(module):
    """脏示例里最长的那条，长度比是判决依据；把数算出来放进 detail 好排查。"""
    fn = expect_callable(module, "is_clean_example")
    example = DIRTY[0]
    source, output = _field(example, "input"), _field(example, "output")
    ratio = len(output) / float(max(1, len(source)))
    ok = _verdict(fn, example)
    return not ok, "这条输出是输入的 %.1f 倍（阈值 %.1f），应当判脏，现在判干净了。" % (
        ratio, LONG_RATIO)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("三个干净示例都判干净", clean_cases),
        ("三个坏示例都判脏", dirty_cases),
        ("混进额外信息的那条能被拦住", ratio_hint),
    ])
