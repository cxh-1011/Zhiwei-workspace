"""第 6 章（第三节）验收：按阈值筛掉不相关的，再取前三。

对应正文「动手练」第 2、3 题：加一句阈值过滤、把 top3 存成 JSON。

正文那行是 `ranked = [(t, s) for t, s in ranked if s > 0.6]`。这里把它包成
函数，是因为两个参数在不同场景下会变：查代码要 0.6，查闲聊 0.4 才够。
写死阈值的实现，换一个语料就得改代码。

返回值要求「原样返回 ranked 里的元素」，不要重新组装成字典或字符串——
下游可能还要用分数做展示（"匹配度 92%"）。
"""

import json
import sys

from _kit import expect_callable, read_json, run

RANKED = [
    ("猫对咖啡因极度敏感，少量就会中毒。", 0.92),
    ("咖啡因在肝脏中代谢，猫缺乏相关酶。", 0.88),
    ("火箭燃料的比冲决定了运载能力。", 0.31),
    ("Falcon 9 一级可以回收复用。", 0.22),
]


def _texts(items):
    out = []
    for entry in items or []:
        if isinstance(entry, (tuple, list)) and entry:
            out.append(entry[0])
        else:
            out.append(entry)
    return out


def threshold(module):
    fn = expect_callable(module, "top_k")
    got = fn(RANKED, 3, 0.6)
    if not isinstance(got, list):
        return False, "返回了 %s，应该返回列表" % type(got).__name__
    if len(got) != 2:
        return False, ("阈值 0.6 应该只剩两条（0.92 和 0.88），实际剩 %d 条：%r"
                       % (len(got), _texts(got)))
    if any("火箭" in text or "Falcon" in text for text in _texts(got)):
        return False, "0.31 和 0.22 应该被 0.6 的阈值筛掉，结果里还有它们"
    return True, ""


def cap(module):
    """k 是上限，不是目标：不够 k 条就返回现有的，不能补空位。"""
    fn = expect_callable(module, "top_k")
    problems = []
    got = fn(RANKED, 3)
    if len(got) != 3:
        problems.append("k=3、无阈值时应该正好 3 条，实际 %d 条" % len(got))
    got = fn(RANKED, 10, 0.6)
    if len(got) != 2:
        problems.append("k=10 但只有 2 条过阈值，应该返回 2 条，实际 %d 条" % len(got))
    got = fn([], 3)
    if got != []:
        problems.append("空输入应该返回空列表，实际 %r" % (got,))
    return not problems, "；".join(problems)


def keeps_order(module):
    """筛完不能重新排序：上游已经按相似度排好了。"""
    fn = expect_callable(module, "top_k")
    got = fn(RANKED, 3, 0.0)
    texts = _texts(got)
    want = [entry[0] for entry in RANKED[:3]]
    return texts == want, "顺序被打乱了：得到 %r，应该是 %r" % (texts, want)


def chunks_file(module, project_dir):
    data, error = read_json(
        project_dir, "reranked_chunks.json",
        "第 3 题要求把 top3 存成这个文件，格式 {\"chunks\": [\"...\", \"...\", \"...\"]}。")
    if data is None:
        return False, error
    if not isinstance(data, dict):
        return False, "顶层应该是对象，现在是 %s" % type(data).__name__
    chunks = data.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        return False, "chunks 应该是非空列表，现在是 %r" % (chunks,)
    if not all(isinstance(item, str) and item.strip() for item in chunks):
        return False, "chunks 里每一项都应该是非空字符串"
    if len(chunks) > 3:
        return False, "题目要求 top3，现在存了 %d 条" % len(chunks)
    return True, ""


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("阈值筛掉了低分段落", threshold),
        ("k 是上限、空输入返回空", cap),
        ("筛完保持原顺序", keeps_order),
    ], extra=[("reranked_chunks.json 结构正确", chunks_file)])
