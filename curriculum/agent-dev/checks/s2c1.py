"""第 2 章验收：四段式提示词的拼装与缺失判断。

对应正文「动手练」第 1、2 题：
1. 把一句口语需求改写成「角色 / 任务 / 约束 / 输出格式」四段；
2. 判断一段提示词缺了哪一段。

为什么考函数而不是考文本
------------------------
「写一段好提示词」没法自动判——但「四段齐不齐」可以。这两个函数也是真实
项目里会写的东西：提示词模板要拼装，上线前要跑一遍 lint 看有没有漏段。

顺序要求
--------
正文给的四段是有顺序的（先定身份，再给任务，再收边界，最后定形状）。
验收按首次出现的位置检查，允许段标题用 `##`、`**`、或直接 `角色：`。
"""

import sys

from _kit import expect_callable, run

ROLE = "资深后端工程师，专做支付系统"
TASK = "把这段同步调用改成 asyncio 并发"
LIMIT = "不要改变函数签名；失败要重试两次"
FORMAT = "只返回改完的代码，不要解释"

SECTIONS = ("角色", "任务", "约束", "输出格式")

# 三段各缺一段的提示词。四段都写了段名——验收靠「角色 / 任务 / 约束 / 输出格式」
# 这四个词定位段落，"你是资深后端工程师"这种写法机器认不出来。
PROMPTS = [
    ("角色：资深后端工程师。\n任务：把这段 SQL 改成参数化查询。\n输出格式：只返回改写后的 SQL。",
     "约束"),
    ("角色：文案。\n任务：写一条产品发布微博。\n约束：不超过 100 字。",
     "输出格式"),
    ("角色：测试工程师。\n约束：只指出问题，不要改代码。\n输出格式：一张三列表格。",
     "任务"),
]


def build(module):
    fn = expect_callable(module, "build_prompt")
    result = fn(ROLE, TASK, LIMIT, FORMAT)
    if not isinstance(result, str):
        raise AssertionError("build_prompt 返回了 %s，应该返回字符串。" % type(result).__name__)
    problems = []
    missing = [name for name in SECTIONS if name not in result]
    if missing:
        problems.append("没有出现这几个段标题：%s" % "、".join(missing))
    for label, value in (("角色", ROLE), ("任务", TASK), ("约束", LIMIT), ("输出格式", FORMAT)):
        if value not in result:
            problems.append("「%s」的内容没被拼进去（找不到：%s）" % (label, value))
    # 顺序：四段的首次出现位置必须递增
    positions = [result.find(name) for name in SECTIONS]
    if all(pos >= 0 for pos in positions) and positions != sorted(positions):
        problems.append("四段的顺序不对，现在依次出现在位置 %s。"
                        "正文的顺序是角色 → 任务 → 约束 → 输出格式。" % positions)
    return not problems, "；".join(problems)


def find_missing(module):
    fn = expect_callable(module, "find_missing_section")
    problems = []
    for prompt, want in PROMPTS:
        got = fn(prompt)
        if not isinstance(got, str):
            problems.append("返回了 %s，应该返回段名的字符串" % type(got).__name__)
            break
        if want not in got:
            problems.append("缺的是「%s」，返回了 %r" % (want, got))
    return not problems, "；".join(problems[:3])


def complete_prompt(module):
    """四段齐全时返回空串——不能见谁都报缺。"""
    fn = expect_callable(module, "find_missing_section")
    full = "角色：你是资深后端工程师\n任务：改写这段 SQL\n约束：不要用字符串拼接\n输出格式：只返回 SQL"
    got = fn(full)
    if not isinstance(got, str):
        return False, "返回了 %s，应该返回字符串" % type(got).__name__
    return got.strip() == "", "四段都在，应该返回空串，现在返回了 %r" % got


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("四段式能拼齐且顺序正确", build),
        ("三段残缺提示词都判对", find_missing),
        ("四段齐全时返回空串", complete_prompt),
    ])
