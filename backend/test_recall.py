"""长期记忆「检索」的离线测试：查得到、查得准、没偷偷扩容量。

不联网、不调模型。所有数据写临时库，不动 chats.db 里用户的真实记忆。

这里最要紧的两组断言：
1. 相关的排前面、无关的检索不出来——检索不准比没有检索更糟，它会给出「错误但看起来
   有出处」的回答；
2. 记忆上限仍是 100、注入上下文的仍是最近 20 条——这次改的是「怎么取」，不是「取多少」。
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database
import recall

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, detail))


def fresh_db():
    """每个小节一个干净的临时库，互不干扰。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    database.DB_PATH = tmp.name
    database.init_db()
    return tmp.name


def add_run(chat_id, question, reply, created_at):
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute(
        "INSERT INTO agent_runs(run_id,chat_id,question,reply,status,rounds,duration_ms,total_tokens,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("run-%s-%s" % (chat_id, created_at), chat_id, question, reply, "ok", 1, 10, 100, created_at),
    )
    conn.commit()
    conn.close()


def seed_basic():
    database.add_memory("研究方向是 agent 开发，具体做多智能体协作和工具调用", "研究方向")
    database.add_memory("我习惯用 VS Code 写代码，终端用 PowerShell", "偏好")
    database.add_memory("实验室服务器只有一张 4090 显卡，跑不动 70B 的模型", "环境")
    database.add_memory("每周四晚上要和导师汇报进度", "安排")
    add_run(1, "怎么用 LoRA 微调 Qwen3-8B", "用 peft 库配置 LoraConfig，r 取 8 到 16 ...", "2026-11-01 10:00:00")
    add_run(2, "训练集和验证集怎么划分才合理", "按时间切分，别随机切，否则会泄露未来信息 ...", "2026-11-03 20:00:00")
    add_run(3, "PyTorch DataLoader 的 num_workers 怎么设", "设成 CPU 核数的 2 到 4 倍，Windows 上先设 0 ...", "2026-11-05 09:00:00")


# ---------------------------------------------------------------- 切词

def test_terms():
    print("\n[切词]")
    tokens = recall.terms("我在做 agent 开发")
    check("英文词整词保留", "agent" in tokens, tokens)
    check("中文切出二元组", "开发" in tokens, tokens)
    check("停用字组合被丢掉", "我在" not in tokens and "我的" not in recall.terms("我的方向"), tokens)
    check("空串没有词元", recall.terms("") == set())
    check("纯标点没有词元", recall.terms("，。！") == set())
    # 中文没有空格，二元组是唯一不依赖分词的稳妥切法：三个字的词留下两段
    three = recall.terms("智能体")
    check("长词切成相邻二元组", {"智能", "能体"} <= three, three)
    # 「能」「下」「上」这类字单看没用，但组成实词时必须留住，
    # 否则「怎么下载模型」会切出一个空查询，什么都查不到
    check("实词里的弱字不丢", "下载" in recall.terms("怎么下载模型"), recall.terms("怎么下载模型"))


# ---------------------------------------------------------------- 记忆

def test_memory_search():
    print("\n[记忆检索]")
    fresh_db()
    seed_basic()

    hits = recall.search_memories("我的研究方向是什么")
    check("问到研究方向能命中", bool(hits), hits)
    check("命中的就是那条方向", hits and "agent 开发" in hits[0]["content"], hits[:1])
    check("带上分类", hits and hits[0]["category"] == "研究方向", hits[:1])
    check("带上记忆编号", hits and isinstance(hits[0]["memory_id"], int), hits[:1])

    hits = recall.search_memories("显卡 服务器")
    check("换关键词能查到环境", any("4090" in item["content"] for item in hits), hits)

    check("完全无关的查不到", recall.search_memories("量子纠缠与贝尔不等式") == [])
    check("limit 生效", len(recall.search_memories("方向", 1)) <= 1)
    check("空查询报错", _raises(recall.search_memories, "  "))

    # 两条都沾「导师」和「汇报」，但一条是连着说的（导师汇报），一条是分开说的，
    # 连着说的那条更贴题意，应该排第一
    database.add_memory("导师喜欢看图表，汇报时多放实验曲线", "偏好")
    hits = recall.search_memories("导师 汇报")
    check("多词命中排前面",
          hits and all(word in hits[0]["content"] for word in ("导师", "汇报")), hits[:2])


def _raises(func, *args):
    try:
        func(*args)
    except ValueError:
        return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------- 历史对话

def test_conversation_search():
    print("\n[历史问答]")
    fresh_db()
    seed_basic()

    hits = recall.search_conversations("LoRA 微调")
    check("能找到以前问过的 LoRA", bool(hits), hits)
    check("命中问句原文", hits and "LoRA" in hits[0]["question"], hits[:1])
    check("带上我当时的回答", hits and "peft" in hits[0]["reply"], hits[:1])
    check("带上日期", hits and hits[0]["created_at"] == "2026-11-01", hits[:1])

    # 同一个词命中多条时，新的排前面：同一件事问过两次，他要的多半是最近那次
    add_run(4, "数据集重新划分了一次", "先按时间排序再切 ...", "2026-11-06 08:00:00")
    hits = recall.search_conversations("数据集划分")
    dates = [item["created_at"] for item in hits]
    check("同分时新的在前", dates == sorted(dates, reverse=True), dates)
    check("无关问题查不到", recall.search_conversations("红烧肉怎么做") == [])


# ---------------------------------------------------------------- 课程进度

def test_course_search():
    print("\n[课程进度]")
    fresh_db()
    import course

    data = course.load_course()
    first = data["chapters"][0]
    database.set_course_progress("agent-dev", first["id"], status="done", mastery=2, notes="自己写了一遍")
    hits = recall.search_course(first["title"])
    check("按章节标题能查到进度", any(item["no"] == first["no"] for item in hits), hits[:2])
    check("带上学完状态", any(item["status"] == "done" for item in hits), hits[:2])
    check("带上掌握度", any(item["mastery"] == 2 for item in hits), hits[:2])
    check("带上笔记", any("自己写了一遍" in item["notes"] for item in hits), hits[:2])
    check("没有这章时返回空", recall.search_course("量子色动力学") == [])


# ---------------------------------------------------------------- 汇总与范围

def test_recall_scopes():
    print("\n[检索范围]")
    fresh_db()
    seed_basic()

    everything = recall.recall("方向 LoRA", "all")
    check("all 同时给三类结果", everything["total"] >= 2, everything["total"])
    check("文本里有长期记忆段", "长期记忆" in everything["text"])
    check("文本里有历史对话段", "历史对话" in everything["text"])
    check("提醒这是过去记录", "过去的记录" in everything["text"])
    check("提醒没出现就是没记过", "没记过" in everything["text"])

    only_memory = recall.recall("方向", "memory")
    check("memory 范围不给历史对话", only_memory["conversation"] == [])
    only_talk = recall.recall("LoRA", "conversation")
    check("conversation 范围不给记忆", only_talk["memory"] == [])

    unknown = recall.recall("方向", "随便写的范围")
    check("非法范围退回 all", unknown["scope"] == "all", unknown["scope"])

    empty = recall.recall("量子纠缠", "all")
    check("查不到时 total 为 0", empty["total"] == 0, empty["total"])
    check("查不到时明说没记录", "没有检索到" in empty["text"], empty["text"][:40])


# ---------------------------------------------------------------- 工具层

def test_tool():
    print("\n[工具层]")
    fresh_db()
    seed_basic()

    from tools import get_tool
    tool = get_tool("recall_memory")
    check("工具已注册", tool is not None)
    check("描述里讲了为什么先检索", "只有调用本工具才能拿到" in tool.description)
    check("query 必填", "query" in tool.parameters.get("required", []))

    before = database.count_memories()
    result = tool.invoke({"query": "研究方向"}, {"mode": "chat"})
    check("能正常返回", result.ok, result.error)
    check("返回里带上命中", result.data.get("memory"), result.data)
    check("检索不写库", database.count_memories() == before, database.count_memories())

    rejected = tool.invoke({"query": "方向", "limit": 50}, {})
    check("多余参数被拒", not rejected.ok, rejected.error)

    missing = tool.invoke({}, {})
    check("缺参数被拒", not missing.ok, missing.error)

    blank = tool.invoke({"query": "   "}, {})
    check("空白查询被拒", not blank.ok, blank.error)


# ---------------------------------------------------------------- 不扩容量

def test_no_capacity_change():
    print("\n[容量没变]")
    fresh_db()
    for index in range(120):
        database.add_memory("第 %d 条测试记忆：主题 %d" % (index, index % 7), "测试")

    check("记忆上限仍是 100", database.count_memories() == 100, database.count_memories())
    check("最多能列 100 条", len(database.list_memories(200)) == 100, len(database.list_memories(200)))

    # 第 100 条以前的确实被淘汰了，所以「查得到」必须靠检索近 100 条 + 会话与课程，
    # 而不是靠把池子改大
    hits = recall.search_memories("主题 3")
    check("淘汰区之外的仍能检索", bool(hits), hits[:1])


def test_inject_context():
    print("\n[注入上下文]")
    fresh_db()
    for index in range(25):
        database.add_memory("记忆 %d：方向 %d" % (index, index), "测试")

    import main

    context = main._memory_context(include_plans=False)
    lines = [line for line in context.splitlines() if line.startswith("[")]
    check("只注入最近 20 条", len(lines) == 20, len(lines))
    check("最新的一定在里面", "记忆 24" in context)
    check("最早的没被塞进来", "记忆 0：" not in context)
    check("报了总数", "共 25 条" in context, context[-80:])
    check("指路到检索工具", "recall_memory" in context)

    # 上下文长度不随条数增长：这才是「补检索而不是扩容量」的可测含义。
    # 用同样体量的条目往里灌，免得把「条目本身变长」误当成「上下文在膨胀」。
    short = len(main._memory_context(include_plans=False))
    for index in range(25, 130):
        database.add_memory("记忆 %d：方向 %d" % (index, index % 10), "测试")
    tall = len(main._memory_context(include_plans=False))
    check("条数从 25 涨到上限，上下文几乎不变", abs(tall - short) < 40, (short, tall))
    check("上限仍是 100 条", database.count_memories() == 100, database.count_memories())
    check("最早的已被淘汰", "记忆 0：" not in main._memory_context(include_plans=False))


def test_agent_wiring():
    print("\n[接进 Agent]")
    fresh_db()
    seed_basic()
    import agent

    captured = {}

    def fake_model(messages, tools=None, timeout=30.0, model=None, should_stop=None, **kwargs):
        captured.setdefault("prompt", messages[0]["content"])
        captured["tools"] = [item["function"]["name"] for item in (tools or [])]
        turn = captured.get("turn", 0)
        captured["turn"] = turn + 1
        if turn == 0:
            yield ("token", "")
            yield ("message", {
                "content": "",
                "tool_calls": [{"id": "c1", "name": "recall_memory",
                                "arguments": '{"query": "研究方向"}'}],
                "usage": 10, "cancelled": False,
            })
        else:
            yield ("token", ["你之前说研究方向是 agent 开发。"])
            yield ("message", {
                "content": "你之前说研究方向是 agent 开发。",
                "tool_calls": [], "usage": 10, "cancelled": False,
            })

    original = agent.chat_completion_stream
    agent.chat_completion_stream = fake_model
    try:
        events = list(agent.run_agent_stream("我之前说的研究方向是什么", mode="chat"))
    finally:
        agent.chat_completion_stream = original

    done = events[-1]
    check("默认就带上检索工具", "recall_memory" in captured["tools"], captured["tools"])
    check("不用开任何开关", "remember" not in captured["tools"], captured["tools"])
    check("提示词要求先检索再回答", "先调用 recall_memory 再回答" in captured["prompt"])
    check("提示词禁止编回忆", "不要顺着他的话编" in captured["prompt"])

    ends = [event for event in events if event["type"] == "tool_end"]
    check("工具真的被调用并成功", ends and ends[0].get("tool") == "recall_memory" and ends[0].get("ok"),
          ends[:1])
    check("回答用上了检索结果", "agent 开发" in done["reply"], done["reply"])

    # 指定文件问答不该掺历史：那是「只看这一个文件」的场景
    agent.chat_completion_stream = fake_model
    try:
        captured.clear()
        list(agent.run_agent_stream("这个文件讲什么", mode="file", filename="x.txt"))
    finally:
        agent.chat_completion_stream = original
    check("文件模式不注册检索", "recall_memory" not in captured.get("tools", []), captured.get("tools"))

    # 学习模式要能查「上次我卡在哪」，所以给检索但不给联网
    agent.chat_completion_stream = fake_model
    try:
        captured.clear()
        list(agent.run_agent_stream("上次我卡在这", mode="tutor",
                                    course_id="agent-dev", chapter_id="s1c1"))
    finally:
        agent.chat_completion_stream = original
    check("学习模式有检索", "recall_memory" in captured.get("tools", []), captured.get("tools"))
    check("学习模式仍然不联网", "web_search" not in captured.get("tools", []), captured.get("tools"))


def main_():
    test_terms()
    test_memory_search()
    test_conversation_search()
    test_course_search()
    test_recall_scopes()
    test_tool()
    test_no_capacity_change()
    test_inject_context()
    test_agent_wiring()
    print("\n%s 通过 %d，失败 %d" % ("FAIL" if FAIL else "ALL PASS", PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
