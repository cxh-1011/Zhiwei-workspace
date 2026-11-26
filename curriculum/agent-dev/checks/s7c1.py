"""第 7 章验收：把流式来的块一块块送出去，而不是攒完再送。

对应正文「动手练」第 3 题：把 `print(...)` 换成
`f.write(chunk.choices[0].delta.content)`。

为什么不测「是不是真流式」
--------------------------
真流式要连模型，验收连不上。但流式最核心的接口约定可以测：
**每来一块就往外写一次**。这就是 `print(..., flush=True)` 和攒到列表再
`"\n".join` 的区别——后者在用户眼里，就是转了半天突然一次性弹出全文。

所以验收的 `sink` 是一个记录调用次数的函数：写三次，它就该被调三次。
"""

import sys

from _kit import expect_callable, run

CHUNKS = ["今天", "天气", "不错。"]


def _sink():
    seen = []
    return seen, lambda piece: seen.append(piece)


def sends_each(module):
    fn = expect_callable(module, "relay")
    seen, sink = _sink()
    total = fn(iter(CHUNKS), sink)
    if len(seen) != len(CHUNKS):
        return False, ("三块应该调用 sink 三次（每来一块送一次），实际 %d 次。"
                       "是不是先 join 成整段再写？" % len(seen))
    if seen != CHUNKS:
        return False, "送出去的内容不对：%r" % (seen,)
    if total != len("".join(CHUNKS)):
        return False, "返回值应该是总字符数 %d，实际 %r" % (len("".join(CHUNKS)), total)
    return True, ""


def skip_empty(module):
    """空块不写：模型偶尔会吐 `delta.content = ""`，写进去只会多一次空刷新。"""
    fn = expect_callable(module, "relay")
    seen, sink = _sink()
    fn(iter(["a", "", None, "b"]), sink)
    return seen == ["a", "b"], "空块和 None 应该跳过，实际送出 %r" % (seen,)


def generator(module):
    """块是生成器也要一块块走：真实 SDK 给的就是个可迭代对象。"""
    fn = expect_callable(module, "relay")

    def stream():
        yield "第一"
        yield "第二"

    seen, sink = _sink()
    total = fn(stream(), sink)
    ok = seen == ["第一", "第二"] and total == 4
    return ok, "生成器输入下送出 %r，返回值 %r" % (seen, total)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("三块分三次送出去", sends_each),
        ("空块和 None 跳过", skip_empty),
        ("生成器输入也逐块处理", generator),
    ])
