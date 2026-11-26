"""第 3 章（第四节）验收：从一句请求里挑出唯一该用的工具。

对应正文「动手练」第 1、2 题：改描述让模型别选错、加一个干扰工具看它会不会
被抢走。验收把「让模型选」换成了「写一个规则选」——因为验收里没有模型。

为什么这样换不算跑题
--------------------
正文第 1 题说得很清楚：把 `run_python_script` 的描述从「运行一个 Python 脚本」
改成一句带限制的命令之后，模型就不乱选了。这说明选错的原因在**描述的边界
不清**，不在模型。自己写一遍规则版，会逼你把「哪个词对应哪个工具」讲明白——
这正是写好描述的前提。

第 3 题（10 个请求测召回率）要调模型，不在这里验。
"""

import sys

from _kit import expect_callable, run, why

TOOLS = [
    {"name": "check_syntax",
     "description": "检查 Python 文件的语法是否正确，只报告错误位置，不修改文件"},
    {"name": "edit_file",
     "description": "修改文件内容：调整缩进、替换文本、增删某几行"},
    {"name": "web_search",
     "description": "联网搜索最新信息，比如查某个库的最新版本号、官方文档更新"},
    {"name": "parse_python_code",
     "description": "分析 Python 代码结构，列出里面有哪些函数和类，不修改文件"},
]

# (请求, 应该选谁, 这一条在考什么)
CASES = [
    ("检查 config.py 语法是否正确", "check_syntax", "「语法」直接指向 check_syntax"),
    ("把 main.py 第 3 行缩进加 4 个空格", "edit_file", "「缩进」「加」都是改文件"),
    ("查 pandas 最新版本号", "web_search", "「最新版本」要联网"),
    ("这段代码里有哪些函数？只看结构，别改", "parse_python_code",
     "「有哪些函数」「只看结构」——这是干扰项最容易抢走的一条"),
    ("给 utils.py 里的 return 换成 yield", "edit_file", "「换成」是替换文本"),
]


def picks(module):
    fn = expect_callable(module, "choose_tool")
    wrong = []
    for query, want, why in CASES:
        try:
            got = fn(query, TOOLS)
        except Exception as exc:
            wrong.append("%r 抛了 %s" % (query, why(exc)))
            continue
        if got != want:
            wrong.append("%r 选了 %r，应该选 %s（%s）" % (query, got, want, why))
    return not wrong, "；".join(wrong[:3])


def only_known(module):
    """不许瞎编工具名：返回值必须是列表里有的，或者 None。"""
    fn = expect_callable(module, "choose_tool")
    names = {tool["name"] for tool in TOOLS}
    bad = []
    for query, _want, _why in CASES:
        got = fn(query, TOOLS)
        if got is not None and got not in names:
            bad.append("%r → %r" % (query, got))
    return not bad, "返回了工具列表里没有的名字：" + "；".join(bad)


def refuses(module):
    """完全不相关的请求应该给 None，而不是硬选一个。

    真实场景里这对应「模型直接回答，不调工具」。硬选一个会白花一次调用，
    还可能改错文件。
    """
    fn = expect_callable(module, "choose_tool")
    got = fn("今天星期几？", TOOLS)
    return got is None, "「今天星期几？」不该匹配任何工具，现在选了 %r" % (got,)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("五条请求都选对", picks),
        ("不返回列表外的工具名", only_known),
        ("无关请求返回 None", refuses),
    ])
