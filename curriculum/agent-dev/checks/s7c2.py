"""第 7 章（第二节）验收：取消之后临时文件要清掉。

对应正文「动手练」第 1 题：在 `ask_slowly()` 开头写一个临时文件，在
`except asyncio.CancelledError` 里把它删掉。

验收不跑 asyncio——异步取消要真发请求、真等，测起来又慢又不稳。测的是那个
小对象本身：开始写、取消删、重复取消不炸。真实代码里它被 `try/finally`
包着：

    flag.start()
    try:
        await ask_slowly()
    finally:
        flag.cancel()

为什么这件事值得单独立一章
--------------------------
"点了停止但进程还在跑"是 Agent 最常见的一类脏状态：临时文件没删、端口没放、
线程池没关。第一次不明显，跑上一周磁盘就满了，或者下次启动报「地址已占用」。
"""

import os
import sys
import tempfile

from _kit import Missing, run


def _flag_class(module):
    cls = getattr(module, "TempFlag", None)
    if not isinstance(cls, type):
        raise Missing("找不到类 TempFlag。这一章的练习要求它叫这个名字。"
                      "（写成函数也行，但至少要有 start / cancel 两个动作。）")
    return cls


def _temp_path():
    handle, path = tempfile.mkstemp(prefix="flag_")
    os.close(handle)
    os.remove(path)
    return path


def starts(module):
    cls = _flag_class(module)
    path = _temp_path()
    flag = cls(path)
    flag.start()
    if not os.path.isfile(path):
        return False, "start() 之后 %s 没被创建" % os.path.basename(path)
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        content = handle.read().strip()
    os.remove(path)
    return content == "running", "文件内容应该是 running，现在是 %r" % (content,)


def cancels(module):
    cls = _flag_class(module)
    path = _temp_path()
    flag = cls(path)
    flag.start()
    flag.cancel()
    gone = not os.path.exists(path)
    if not gone:
        os.remove(path)
    return gone, "cancel() 之后文件还在。正文第 1 题的「做完标准」就是 ls 报错。"


def cancel_twice(module):
    """重复取消不能抛：任务被用户连点两次停止是常态。"""
    cls = _flag_class(module)
    path = _temp_path()
    flag = cls(path)
    flag.start()
    flag.cancel()
    try:
        flag.cancel()
    except Exception as exc:
        return False, "第二次 cancel() 抛了 %s。文件已经没了，直接返回就行。" % (
            type(exc).__name__)
    return True, ""


def not_started(module):
    """没 start 就 cancel，也不该抛。"""
    cls = _flag_class(module)
    path = _temp_path()
    flag = cls(path)
    try:
        flag.cancel()
    except Exception as exc:
        return False, "没 start 就 cancel 抛了 %s。这时文件本来就不存在。" % (
            type(exc).__name__)
    return True, ""


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("start 写出 running", starts),
        ("cancel 删掉文件", cancels),
        ("重复 cancel 不报错", cancel_twice),
        ("没 start 就 cancel 不报错", not_started),
    ])
