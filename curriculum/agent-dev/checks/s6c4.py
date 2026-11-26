"""第 6 章（第四节）验收：找出答案里「资料没说过」的函数名。

对应正文「动手练」第 1、2 题：只看一句 retrieved，看回答里有没有冒出
`result()`、`as_completed()` 这种原文没有的调用。

这一章的坑是**看起来最像真的那种错**：模型把 `ThreadPoolExecutor.submit()`
记成了 `future.result()`，语法完全正确、说得通，就是资料里没有。人眼很难
逐词对照，所以让程序先把「答案里出现的函数调用」和「资料里出现的字符串」
对一遍。

为什么只查函数调用
------------------
中文答案里的普通词没法可靠对比（同义词、语序都在变），但 `foo()` 这种带括号
的标识符是硬信号：它要么在资料里，要么就是编的。这也是正文第 2 题让人
"标出第一个不在原文里出现的动词"的可执行版本。
"""

import re
import sys

from _kit import expect_callable, run

# 带括号的标识符。允许 future.result 这种带点号的写法。
_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\s*\(")

ANSWER = "先用 submit() 提交任务，再用 future.result() 取结果，最后 as_completed() 遍历。"
SOURCES = ["ThreadPoolExecutor.submit() 提交单个任务，返回 Future 对象。",
           "as_completed() 在任务完成时逐个产出。"]


def finds_made_up(module):
    fn = expect_callable(module, "find_unsupported")
    got = fn(ANSWER, SOURCES)
    if not isinstance(got, list):
        return False, "返回了 %s，应该返回列表" % type(got).__name__
    joined = " ".join(str(item) for item in got).lower()
    problems = []
    # result 一定没出现过：资料里只有 Future 对象，没有 result() 这个方法
    if "result" not in joined:
        problems.append("没报出 result()——这一段资料里从头到尾没有这个方法")
    # 反过来：submit 和 as_completed 资料里有，不该被报成编造
    for name in ("submit", "as_completed"):
        if name in joined:
            problems.append("把 %s 报成编造了，但资料里就有它" % name)
    return not problems, "；".join(problems)


def clean_answer(module):
    fn = expect_callable(module, "find_unsupported")
    got = fn("调用 submit() 把任务丢进线程池。", ["submit() 提交单个任务。"])
    return got == [] or not got, "全都在资料里，应该返回空列表，现在返回 %r" % (got,)


def no_calls(module):
    fn = expect_callable(module, "find_unsupported")
    got = fn("这个功能很好用，建议多用。", ["随便一段资料"])
    return not got, "答案里没有函数调用，不该报任何东西，现在返回 %r" % (got,)


def dedupes(module):
    fn = expect_callable(module, "find_unsupported")
    got = fn("result() 一下，再 result() 一次。", ["Future 对象"])
    names = [str(item).lower() for item in got]
    return names.count("result") == 1, "同一个函数报了多次：%r" % (got,)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("报出资料里没有的 result()", finds_made_up),
        ("资料里有的不误报", clean_answer),
        ("没有函数调用时返回空", no_calls),
        ("同一个名字只报一次", dedupes),
    ])
