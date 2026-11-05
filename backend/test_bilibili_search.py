# -*- coding: utf-8 -*-
"""B 站视频搜索的自测：不联网、不花额度。

这个工具的价值全在「过滤」和「排序」上——接口本身没什么难度，难的是
从 20 条里挑出真正能推荐给零基础的人的那几条。所以测试重点不是「能不能
拿到数据」，而是：

1. 广告、无关、低播放、零收藏能不能被剔掉；
2. 刷量的视频会不会被顶到第一（实测见过收藏率 154% 的）；
3. 同一位 UP 主的同一门课换了标题重复上榜，会不会去重；
4. 查不到合适的时候，会不会老老实实说查不到而不是编。

运行：
    cd backend
    ..\\.venv\\Scripts\\python.exe test_bilibili_search.py
"""

import json
import os
import sys
import urllib.error

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

import agent  # noqa: E402
from tools import bilibili_search as bs  # noqa: E402
from tools.base import get_tool  # noqa: E402

results = []


def check(label, cond, detail=""):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  -> " + str(detail)[:130]) if detail else ""))


# ---------------------------------------------------------------- 假数据
def video(title, play, fav, bvid, author="某UP主", duration="10:00", pubdate=1700000000):
    return {"type": "video", "title": title, "play": play, "favorites": fav,
            "bvid": bvid, "author": author, "duration": duration, "pubdate": pubdate}


RAW = [
    # 正常好课：4% 收藏率，普通区间
    video("Python 零基础入门 完整教程", 2_000_000, 80_000, "BVgood", "正经课堂", "20:00:00"),
    # 引流广告：8 千播放、0 收藏、标题带「接单」
    video("【咨询领福利】Python入门+AI爬虫项目接单", 8_446, 0, "BVad", "某某", "1:00"),
    # 完全无关：搜 Python 却混进 Word 教程（真实发生过）
    video("【全套】Word自学教程从小白到高手", 6_389_450, 432_237, "BVword", "爱知趣", "229:57"),
    # 播放太低：新号或者小号
    video("Python 教程 三天速成", 5_000, 300, "BVlow"),
    # 有人看但没人存：多半是娱乐向或纯导流
    video("Python 入门 第1课", 900_000, 0, "BVnofav"),
    # 数据异常：收藏 114 万 > 播放 74 万，收藏率 154%，物理上不可能
    video("Python 天花板教程", 742_965, 1_149_982, "BVweird", "海鸽", "50:00:00"),
    # 同一门课换了个标题重复上榜
    video("Python 零基础入门 完整教程（2026版）", 1_000_000, 40_000, "BVdup", "正经课堂", "20:00:00"),
]


print("\n[1] 标题清洗与数字格式")
check("去掉 <em> 高亮标签",
      bs._clean('【全748集】<em class="keyword">Python</em>教程') == "【全748集】Python教程")
check("还原 HTML 实体", bs._clean("A&amp;B &quot;引号&quot;") == 'A&B "引号"')
check("万位取整", bs._num(2_000_000) == "200.0万", bs._num(2_000_000))
check("亿位取整", bs._num(123_456_789) == "1.2亿", bs._num(123_456_789))
check("一万以下原样", bs._num(5_000) == "5000")
check("空值不炸", bs._num(None) == "0")

print("\n[2] 时长格式化")
# 1900:20 是 1900 分钟，不是 1900 小时，这是最容易看错的一处
check("分:秒 转小时", bs._duration("1900:20") == "31 小时 40 分", bs._duration("1900:20"))
check("时:分:秒", bs._duration("20:00:00") == "20 小时 0 分", bs._duration("20:00:00"))
check("不足一分钟", bs._duration("45") == "45 秒")
check("脏值兜底", bs._duration("") == "未知")

print("\n[3] 相关性：主题词必须命中")
check("主题词直接命中", bs._relevance("python 零基础入门", "Python 零基础") == 1.0)
# Word 教程里一个 Python 字样都没有，只有「教程」这个泛词命中，必须判无关
check("只有泛词命中不算相关", bs._relevance("【全套】word自学教程从小白到高手", "Python 入门 教程") == 0.0,
      bs._relevance("【全套】word自学教程从小白到高手", "Python 入门 教程"))
# 搜「提示词」结果里冒出 AI 漫剧制作教程，实测真实发生过
check("AI 漫剧教程不算提示词相关", bs._relevance("ai 漫剧 制作 教程", "提示词 教程") == 0.0)
check("真讲提示词的留下", bs._relevance("提示词 大全 视频教程", "提示词 教程") == 1.0)
check("泛词顺序不影响主题词识别", bs._core_token("教程 Python") == "python", bs._core_token("教程 Python"))

print("\n[4] 打分：刷量不能顶到第一")
good = bs._score(80_000, 2_000_000, 1.0)
weird = bs._score(1_149_982, 742_965, 1.0)
check("正常数据得分高于异常数据", good > weird, "正常 %.0f / 异常 %.0f" % (good, weird))
# 收藏数更多、但比例异常（30%）的那条，排名要低于收藏数更少、比例正常（20%）的那条。
# 这正好说明「刷收藏」抬不动排名——否则营销号只要刷收藏就能霸榜。
normal = bs._score(200_000, 1_000_000, 1.0)   # 收藏率 20%，正常上限内
skewed = bs._score(300_000, 1_000_000, 1.0)   # 收藏率 30%，触发 0.6 折扣
check("异常收藏率被打折：收藏更多反而排后面", skewed < normal,
      "正常 %.0f / 异常 %.0f" % (normal, skewed))
check("只刷收藏抬不高排名",
      bs._score(500_000, 100_000, 1.0) < bs._score(50_000, 1_000_000, 1.0),
      "刷收藏的 vs 真实高播放的")

print("\n[5] 过滤：五类都该被剔掉")
picked, stats = bs._pick(RAW, "Python 零基础", limit=8, sort="collect")
bvs = [item["bvid"] for item in picked]
check("广告被剔除", stats["ad"] == 1, stats)
check("无关的被剔除", stats["irrelevant"] == 1, stats)
check("播放过低被剔除", stats["low_play"] == 1, stats)
check("零收藏被剔除", stats["no_fav"] == 1, stats)
check("重复的被剔除", stats["dup"] == 1, stats)
check("最后只剩两条", len(picked) == 2, bvs)
check("正常课排在异常数据前面", bvs[:1] == ["BVgood"], bvs)
check("异常数据的条目没进前列", "BVweird" not in bvs[:1], bvs)
check("入选条目都带链接", all(i.get("bvid") for i in picked))

print("\n[6] 重复识别")
same = bs._dedupe([
    video("Python 零基础入门 完整教程", 100, 10, "A", "同一个UP"),
    video("Python 零基础入门 完整教程（2026版）", 100, 10, "B", "同一个UP"),
    video("Python 零基础入门 完整教程", 100, 10, "C", "另一个UP"),
])
check("同 UP 同标题只留一条", len(same) == 2, [i["bvid"] for i in same])
check("不同 UP 的相似标题不误杀", "C" in [i["bvid"] for i in same])

print("\n[7] 按最新排序时不重排")
new_picked, _ = bs._pick(RAW, "Python 零基础", limit=8, sort="new")
stamps = [int(i.get("pubdate") or 0) for i in new_picked]
check("按发布时间倒序", stamps == sorted(stamps, reverse=True), stamps)

print("\n[8] 空结果要说清原因")
empty_text = bs._format("冷门词汇", [], {"ad": 3, "low_play": 2, "irrelevant": 9,
                                        "no_fav": 1, "dup": 0}, 15)
check("说明没有可推荐的", "没有剩下可推荐" in empty_text)
check("交代各类剔除了多少", "广告/引流" in empty_text and "播放量过低" in empty_text)
check("提醒不要编视频名", "不要自己编视频名" in empty_text)

print("\n[9] 正常输出：数据齐全且带警示")
ok_text = bs._format("Python 零基础", [{
    "title": "Python 零基础入门 完整教程", "url": "https://www.bilibili.com/video/BVgood",
    "author": "正经课堂", "duration": "20 小时 0 分", "minutes": 1200, "play": 2_000_000,
    "fav": 80_000, "pubdate": "2024-01-01"}],
    {"ad": 1, "low_play": 1, "irrelevant": 1, "no_fav": 0, "dup": 0}, 20)
check("带上了 UP 主", "UP主：正经课堂" in ok_text)
check("带上了时长和播放", "20 小时 0 分" in ok_text and "200.0万" in ok_text)
check("带上了链接", "https://www.bilibili.com/video/BVgood" in ok_text)
check("标明是外部内容", "只是候选，不是结论" in ok_text)
check("提醒数据不等于适合", "不说明适合问问题的人" in ok_text)
check("提醒标题是 UP 主自己写的", "UP 主自己写的" in ok_text)
check("让模型承认看不到内容", "工具看不到" in ok_text)
# 模型自己算时长会算错（实测把 123 小时说成「第 2 到 5 天看完」），
# 所以把「按每天一小时要几天」直接算好写进去
check("时长换算成学习天数", "要 20 天" in ok_text, ok_text[ok_text.find("时长"):][:60])
check("短课程不提天数", bs._pace(120) == "，一次能看完", bs._pace(120))
check("长课程按每天一小时折算", bs._pace(7420) == "，按每天学 1 小时算要 124 天", bs._pace(7420))
check("时长未知不硬算", bs._pace(0) == "")
# 这条是实测暴露的问题：模型会编「全程无快进」「每讲都带代码演示」
check("明确禁止编造视频内容", "一概不知道" in ok_text and "不要描述" in ok_text)
check("给出被追问时的标准说法", "具体内容得你自己打开看" in ok_text)
# 抽象规则压不住幻觉，所以给具体反例：模型看到「这些话写了就是编的」，比读十条原则有用
check("每条都标注内容详情未知", ok_text.count("内容详情：未知") == 1, ok_text.count("内容详情：未知"))
check("给了具体的反例句", "全程无快进" in ok_text and "社区答疑活跃" in ok_text)
check("反例说明为什么是编的", "不在工具返回里" in ok_text)
# 实测里模型编过「按收藏率≥4%筛选」——它压根没参与筛选，纯猜
check("禁止解释筛选标准", "不要向用户解释「筛选标准」" in ok_text)
check("给了只许引用数字的格式模板", "按这个格式回答" in ok_text and "理由：从" in ok_text)

print("\n[10] 缓存：同一关键词不重复打接口")
bs.clear_cache()
calls = {"n": 0}


def fake_request(query, order):
    calls["n"] += 1
    return {"code": 0, "data": {"result": RAW}}


origin_request = bs._request
bs._request = fake_request
tool = get_tool("bilibili_search")
first = tool.invoke({"query": "Python 零基础", "limit": 5}, {})
second = tool.invoke({"query": "Python 零基础", "limit": 5}, {})
check("第一次是真请求", first.ok and first.data.get("cached") is False)
check("第二次命中缓存", second.ok and second.data.get("cached") is True, second.data.get("cached"))
check("只打了一次接口", calls["n"] == 1, calls)
check("返回了结构化的视频列表", isinstance(first.data.get("videos"), list), type(first.data.get("videos")))
check("视频条目字段齐全",
      set(first.data["videos"][0]) == {"title", "url", "author", "duration", "minutes",
                                        "play", "fav", "pubdate"},
      set(first.data["videos"][0]) if first.data.get("videos") else None)
check("时长换算成分钟数", first.data["videos"][0]["minutes"] == 1200,
      first.data["videos"][0]["minutes"])

print("\n[11] 接口出错不能只报「失败」")
check("412 说清风控且别重试", "412" in bs._explain(
    urllib.error.HTTPError("http://x", 412, "Precondition Failed", {}, None)), "412")
check("超时说要说明查不到", "连不上" in bs._explain(TimeoutError("timed out")))
check("返回非 JSON 提示接口改版", "不是 JSON" in bs._explain(json.JSONDecodeError("x", "y", 0)))
# 最关键的一条：拿不到数据时绝不能靠记忆编推荐
check("失败提示禁止编造", "编" in bs._explain(TimeoutError("timed out")), bs._explain(TimeoutError("x")))


def fake_code_error(query, order):
    return {"code": -412, "message": "请求被拦截"}


bs._request = fake_code_error
bs.clear_cache()
err = tool.invoke({"query": "随便问问"}, {})
check("业务错误码也当失败处理", not err.ok, err.content[:60])
check("业务错误不让人死磕重试", "不要反复重试" in err.error, err.error)

bs._request = fake_request
bs.clear_cache()
check("空关键词直接拒绝", not tool.invoke({"query": "   "}, {}).ok)
check("limit 超范围被夹住", tool.invoke({"query": "Python 零基础", "limit": 99}, {}).ok)
check("非法 sort 退回默认", tool.invoke({"query": "Python 零基础", "sort": "乱写"}, {}).data.get("sort") == "collect")

print("\n[12] 接入 agent：工具集、提示词、次数上限")
bs.clear_cache()
captured = []


def make_fake(turns):
    def fake(messages, tools=None, timeout=30.0, model=None, should_stop=None, **kwargs):
        captured.append({
            "tools": [t["function"]["name"] for t in (tools or [])],
            "system": messages[0].get("content", ""),
        })
        turn = turns[min(len(captured) - 1, len(turns) - 1)]
        for piece in turn.get("stream") or []:
            yield ("token", piece)
        yield ("message", dict(turn))
    return fake


agent.chat_completion_stream = make_fake([{"content": "好的", "tool_calls": [], "usage": 1, "cancelled": False}])
agent.run_agent("你好", mode="chat")
check("聊天模式带上了 bilibili_search", "bilibili_search" in captured[-1]["tools"], captured[-1]["tools"])
check("提示词说明了什么时候用它", "bilibili_search" in captured[-1]["system"])
check("提示词禁止凭记忆编 UP 主", "不要凭记忆写 UP 主名字" in captured[-1]["system"])
check("提示词要求只用工具返回的条目", "只能引用 bilibili_search 实际返回的条目" in captured[-1]["system"])
# 实测里模型真的编了「全程无快进」「每讲都带代码演示」
check("提示词禁止描述视频内容", "不要描述视频内容" in captured[-1]["system"])

ctx = {}
for _ in range(3):
    agent._web_quota("bilibili_search", ctx)
fourth = agent._web_quota("bilibili_search", ctx)
check("和 web_search 共用搜索次数上限", fourth is not None and "上限" in fourth.error, ctx)

print("\n[13] 端到端：视频能出现在来源卡片里")
bs.clear_cache()
turns = [
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c1", "name": "bilibili_search",
        "arguments": json.dumps({"query": "Python 零基础"}, ensure_ascii=False)}]},
    {"content": "给你找了两个。", "tool_calls": [], "usage": 1, "cancelled": False},
]
captured.clear()
agent.chat_completion_stream = make_fake(turns)
events = list(agent.run_agent_stream("推荐几个学 Python 的视频", mode="chat"))
types = [e["type"] for e in events]
source_events = [e for e in events if e["type"] == "sources"]
check("发出了 sources 事件", len(source_events) == 1, types[:10])
items = source_events[0]["items"] if source_events else []
check("视频进了来源卡片", len(items) >= 2, len(items))
check("标题带「视频：」前缀好区分", all(i["title"].startswith("视频：") for i in items),
      [i["title"][:20] for i in items])
check("链接指向 B 站", all("bilibili.com/video/" in i["url"] for i in items),
      [i["url"] for i in items][:2])
done = [e for e in events if e["type"] == "done"][0]
check("回答正常收尾", done["status"] == "ok", done["status"])

bs._request = origin_request
bs.clear_cache()

passed = sum(1 for _, ok in results if ok)
total = len(results)
print("\n" + "-" * 46)
print("通过 %d / %d" % (passed, total))
if passed != total:
    print("失败项：")
    for label, ok in results:
        if not ok:
            print("  - " + label)
sys.exit(0 if passed == total else 1)
