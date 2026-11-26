"""第 15 章验收：trim_conversation() 按规则砍，不是按感觉砍。

对应正文「动手练」第 1 题：只留 user 的第一句需求 + assistant 的第一个代码块，
结果应该正好 2 条。正文给的那段对话里，「你好」「谢谢」是寒暄，
「能给个例子吗」是追问，都要被砍掉。

为什么连「不许改入参」也测：初版常见写法是在 for 循环里 `msgs.remove(m)`，
跑一次函数，原始对话就被改掉了；下一次再调用时结果完全不同。
这种 bug 不会报错，只会让「同一个函数两次结果不一样」。
"""

import copy
import sys

from _kit import expect_callable, run

RAW = [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好！请问有什么可以帮您？"},
    {"role": "user", "content": "我有个 Python 脚本，想安全读取 YAML 配置文件"},
    {"role": "assistant", "content": "推荐用 PyYAML，并加 try-except。"},
    {"role": "user", "content": "能给个例子吗？"},
    {"role": "assistant", "content": "```python\nimport yaml\ncfg = yaml.safe_load(open('config.yaml'))\n```"},
    {"role": "user", "content": "谢谢！"},
]


def two_items(module):
    fn = expect_callable(module, "trim_conversation")
    kept = fn(copy.deepcopy(RAW))
    if not isinstance(kept, list):
        return False, "返回了 %s，应该返回一个列表。" % type(kept).__name__
    if len(kept) != 2:
        roles = [str(m.get("role")) for m in kept if isinstance(m, dict)]
        return False, "留下了 %d 条（%s）。这一题只要 2 条：一句需求 + 一段代码。" % (
            len(kept), "、".join(roles))
    return True, ""


def first_is_user_need(module):
    fn = expect_callable(module, "trim_conversation")
    kept = fn(copy.deepcopy(RAW))
    if len(kept) != 2:
        return False, "条数先不对（%d 条），这一项没法判。" % len(kept)
    first = kept[0]
    if not isinstance(first, dict):
        return False, "留下的第一条不是消息字典。"
    ok = first.get("role") == "user" and "YAML" in str(first.get("content", ""))
    return ok, "第一条应该是那条提出需求的话，现在是 role=%r、content=%r" % (
        first.get("role"), str(first.get("content"))[:40])


def second_is_code(module):
    fn = expect_callable(module, "trim_conversation")
    kept = fn(copy.deepcopy(RAW))
    if len(kept) != 2:
        return False, "条数先不对（%d 条），这一项没法判。" % len(kept)
    second = kept[1]
    if not isinstance(second, dict):
        return False, "留下的第二条不是消息字典。"
    ok = second.get("role") == "assistant" and "```" in str(second.get("content", ""))
    return ok, "第二条应该是那段代码，现在是 role=%r、content=%r" % (
        second.get("role"), str(second.get("content"))[:40])


def no_mutation(module):
    """跑两次，第二次输入的原始对话不能被改过。"""
    fn = expect_callable(module, "trim_conversation")
    original = copy.deepcopy(RAW)
    passed_in = copy.deepcopy(RAW)
    fn(passed_in)
    if passed_in != original:
        return False, ("函数把传进去的列表改了（%d 条 → %d 条）。"
                       "用新列表装结果（kept = []），不要在 for 里 remove。"
                       % (len(original), len(passed_in)))
    first = fn(copy.deepcopy(RAW))
    second = fn(copy.deepcopy(RAW))
    return first == second, "同一个输入两次结果不一样——说明函数里留了跨调用的状态。"


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("只留 2 条", two_items),
        ("第一条是那句需求", first_is_user_need),
        ("第二条是代码块", second_is_code),
        ("不改入参、两次结果一致", no_mutation),
    ])
