# -*- coding: utf-8 -*-
"""B 站视频链接对账测试。

背景（真实事故）：用户问「0 基础学 Agent 开发，找几个 B 站视频」，模型一次工具都没调，
直接编了三个视频，还给了三个像模像样的 BV 号。点开分别是「18 岁的成人礼」
「视频不存在」「征服卡包」——描述和跳到的地方完全无关，用户直接来问「为什么点进去
和描述的不一样」。

BV 号格式规整（BV + 十位字母数字），所以「编一个」从来不是难事，难的是拦住它。
提示词管不住（模型不调工具时，工具返回值里的约束根本没进上下文），
所以在交付前对账：只放行本次工具真实返回过的链接。

重点盯四件事：
1. 工具返回过的链接一个字都不许动；
2. 编造链接必须摘掉可点性，但不能把整段话删残（标题要留着）；
3. markdown 链接和裸地址两种写法都要覆盖；
4. 别的域名不受影响——这道闸只针对 B 站视频。

运行：
    cd backend
    ..\\.venv\\Scripts\\python.exe test_video_links.py
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import _drop_unverified_video_links, _verified_video_ids  # noqa: E402

PASS = 0
FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s\n       期望: %r\n       实际: %r" % (name, want, got))


# 真实存在的两条（搜索引擎实测返回过），和三个编造的
REAL = "BV1qW4y1a7fU"
FAKE = "BV1Xm4y1T7vF"


def steps_with(*urls):
    return [{
        "kind": "tool_call", "tool": "bilibili_search",
        "result_data": {"videos": [{"title": "t", "url": u} for u in urls]},
    }]


print("== 从工具结果里取 BV 号 ==")
ids = _verified_video_ids(steps_with("https://www.bilibili.com/video/%s" % REAL,
                                     "https://www.bilibili.com/video/%s?p=2" % FAKE))
check("两条都收进来", ids, {REAL.upper(), FAKE.upper()})
check("size=3 时只看前两页", len(ids), 2)
check("空 steps 不炸", _verified_video_ids([]), set())
check("result_data 是 None 不炸", _verified_video_ids([{"result_data": None}]), set())
check("videos 不是列表不炸", _verified_video_ids([{"result_data": {"videos": "x"}}]), set())


print("\n== 工具返回过的链接：原样保留 ==")
good = "看这个 https://www.bilibili.com/video/%s 挺好" % REAL
text, dropped = _drop_unverified_video_links(good, {REAL.upper()})
check("正文不变", text, good)
check("没有摘掉任何东西", dropped, 0)


print("\n== 编造的裸链接：摘掉可点性 ==")
bad = "推荐 https://www.bilibili.com/video/%s 这门课" % FAKE
text, dropped = _drop_unverified_video_links(bad, {REAL.upper()})
check("计数 1", dropped, 1)
check("链接消失", FAKE not in text, True)
check("留了说明", "没核实过" in text, True)
check("前后文还在", text.startswith("推荐 ") and text.endswith(" 这门课"), True)


print("\n== 编造的 markdown 链接：去链接、留标题 ==")
bad_md = "- [LangChain 实战教程](https://www.bilibili.com/video/%s)（12 集）" % FAKE
text, dropped = _drop_unverified_video_links(bad_md, set())
check("计数 1", dropped, 1)
check("标题保留", "LangChain 实战教程" in text, True)
check("链接消失", FAKE not in text, True)
check("没有留下空括号", "](" not in text, True)


print("\n== 真假混排：只摘编的那条 ==")
mixed = ("1. [真课](https://www.bilibili.com/video/%s)\n"
         "2. [假课](https://www.bilibili.com/video/%s)\n"
         "3. 裸的 https://www.bilibili.com/video/BV1Qp4y1W7Kz" % (REAL, FAKE))
text, dropped = _drop_unverified_video_links(mixed, {REAL.upper()})
check("摘掉两条", dropped, 2)
check("真链接保留", REAL in text, True)
check("假链接没了", FAKE not in text, True)
check("第二条也假链接没了", "BV1Qp4y1W7Kz" not in text, True)
check("真课标题还在", "真课" in text, True)


print("\n== 大小写：模型把小写贴上去了也算同一条 ==")
text, dropped = _drop_unverified_video_links(
    "https://www.bilibili.com/video/%s" % REAL.lower(), {REAL.upper()})
check("不误伤", dropped, 0)


print("\n== 别的域名不受影响 ==")
other = ("https://example.com/video/BV1Xm4y1T7vF "
         "https://www.bilibili.com/read/cv12345 "
         "https://www.bilibili.com/video/BV1qW4y1a7fU")
text, dropped = _drop_unverified_video_links(other, {REAL.upper()})
check("摘掉 0 条", dropped, 0)
check("非视频页原样", text, other)


print("\n== 带查询参数的链接：整体摘掉，不留半截 ==")
text, dropped = _drop_unverified_video_links(
    "https://www.bilibili.com/video/BV1rpWjevEip?p=3&t=10", set())
check("计数 1", dropped, 1)
check("参数没残留", "?p=3" not in text, True)


print("\n== 空正文与空集合 ==")
check("空正文", _drop_unverified_video_links("", set()), ("", 0))
check("无关正文", _drop_unverified_video_links("今天天气不错", set()), ("今天天气不错", 0))


print("\n== 端到端：模型没调工具就编链接，交付前被摘掉 ==")


def _fake_stream(script):
    """把预设轮次伪装成 chat_completion_stream 的产出，不碰真实模型。"""
    state = {"round": 0}

    def fake(messages, tools=None, timeout=30.0, model=None, should_stop=None, **kwargs):
        index = min(state["round"], len(script) - 1)
        state["round"] += 1
        turn = script[index]
        for piece in turn.get("tokens", []):
            yield ("token", piece)
        yield ("message", {
            "content": "".join(turn.get("tokens", [])),
            "tool_calls": turn.get("tool_calls", []),
            "usage": 10,
            "cancelled": False,
        })

    return fake


import agent as agent_module  # noqa: E402

original = agent_module.chat_completion_stream
try:
    agent_module.chat_completion_stream = _fake_stream([{
        "tokens": [
            "推荐这门课 [LangChain 实战](https://www.bilibili.com/video/BV1Xm4y1T7vF)，",
            "还有 https://www.bilibili.com/video/BV1rG411x7jY 。",
        ],
    }])
    events = list(agent_module.run_agent_stream("推荐几个 B 站 Agent 教程", mode="chat"))
    done = events[-1]
finally:
    agent_module.chat_completion_stream = original

reply = done["reply"]
check("状态 ok", done["status"], "ok")
check("标题留着", "LangChain 实战" in reply, True)
check("第一个编造链接被摘", "BV1Xm4y1T7vF" not in reply, True)
check("第二个编造链接被摘", "BV1rG411x7jY" not in reply, True)
check("末尾有说明", "不是这次检索返回的" in reply, True)
check("说明里报了条数", "2 条" in reply, True)

print("\n%s 通过 %d，失败 %d" % ("FAIL" if FAIL else "ALL PASS", PASS, FAIL))
sys.exit(1 if FAIL else 0)
