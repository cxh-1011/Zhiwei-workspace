# -*- coding: utf-8 -*-
"""任务清单自测：参数校验、状态归一、事件顺序、门控与回看。

不需要 API Key，也不碰真实工作区——清单工具只维护内存里的一份数组，
这里的重点是「模型给的脏数据不会把界面搞乱」以及「事件顺序对了，
前端才能把卡片画出来」。

运行：
    cd backend
    ..\\.venv\\Scripts\\python.exe test_todo_tools.py
"""

import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

import agent  # noqa: E402
import database  # noqa: E402
from tools import todos as todo_module  # noqa: E402
from tools.base import get_tool  # noqa: E402

_TMP_DB = os.path.join(tempfile.gettempdir(), "ai_robot_todo_test.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
database.DB_PATH = _TMP_DB
database.init_db()

results = []


def check(label, cond, detail=""):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  -> " + str(detail)[:130]) if detail else ""))


TOOL = get_tool("update_todos")
assert TOOL is not None, "update_todos 没有注册"


def invoke(items, context=None):
    return TOOL.invoke({"items": items}, context if context is not None else {})


def snapshot(items):
    """把 items 压成 (文本, 状态) 列表，断言时好比对。"""
    return [(item["text"], item["status"]) for item in items]


print("\n[1] 参数校验")
r = invoke("不是数组")
check("items 不是数组被拒", not r.ok, r.error)
check("提示正确的形状", "数组" in r.error, r.error)

r = invoke([])
check("空清单被拒", not r.ok, r.error)
check("空清单说明为什么不能清空", "看不到" in r.error, r.error)

r = invoke([{"text": "第 %d 步" % i, "status": "pending"} for i in range(todo_module.MAX_TODOS + 1)])
check("超过条数上限被拒", not r.ok, r.error)
check("报错带上上限数字", str(todo_module.MAX_TODOS) in r.error, r.error)

r = invoke([{"text": "  ", "status": "pending"}])
check("空白文本被拒", not r.ok, r.error)
check("指出是第几条", "第 1 条" in r.error, r.error)

r = invoke([{"text": "跑测试", "status": "doing-ish"}])
check("未知状态被拒", not r.ok, r.error)
check("列出允许的状态", "pending" in r.error and "active" in r.error and "done" in r.error, r.error)

r = invoke([{"text": "跑测试", "status": "pending"}, "写代码"])
check("字符串简写也能收下", r.ok and len(r.data["items"]) == 2, r.data)
check("简写默认是待办", r.ok and r.data["items"][1]["status"] == "pending", r.data)

r = TOOL.invoke({"items": [{"text": "x", "status": "pending"}], "extra": 1}, {})
check("多余参数被拒", not r.ok, r.error)

print("\n[2] 状态归一")
cases = [
    ("todo", "pending"), ("open", "pending"),
    ("in_progress", "active"), ("in-progress", "active"), ("doing", "active"), ("RUNNING", "active"),
    ("completed", "done"), ("Finished", "done"),
]
for raw, want in cases:
    r = invoke([{"text": "某一步", "status": raw}])
    got = r.data["items"][0]["status"] if r.ok else "ERR"
    check("状态 %s → %s" % (raw, want), got == want, got)

r = invoke([{"text": "一步"}])
check("省略 status 默认待办", r.ok and r.data["items"][0]["status"] == "pending", r.data)

print("\n[3] 进度与文案")
r = invoke([
    {"text": "跑测试确认失败", "status": "done"},
    {"text": "改 add 函数", "status": "active"},
    {"text": "重跑测试", "status": "pending"},
])
check("统计完成数", r.data["done"] == 1 and r.data["total"] == 3, r.data)
check("data 带 kind（前端据此识别）", r.data.get("kind") == "todos", r.data)
check("回执报进度", "1/3 完成" in r.content, r.content)
check("回执点名当前项", "改 add 函数" in r.content, r.content)
check("提醒继续动手", "不要只更新清单" in r.content, r.content)

r = invoke([
    {"text": "跑测试", "status": "done"},
    {"text": "改代码", "status": "done"},
])
check("全完成时明说", "全部完成" in r.content, r.content)
check("全完成时不再催动手", "不要只更新清单" not in r.content, r.content)

r = invoke([{"text": "跑测试", "status": "pending"}, {"text": "改代码", "status": "pending"}])
check("没有进行中时提示下一步", "下一步「跑测试」" in r.content, r.content)

r = invoke([{"text": "甲", "status": "active"}, {"text": "乙", "status": "active"}])
check("多条进行中只留第一条", r.data["items"][0]["status"] == "active"
      and r.data["items"][1]["status"] == "pending", snapshot(r.data["items"]))
check("降级写进回执", "同时只允许一条进行中" in r.content, r.content)

long_text = "很长的步骤" * 60
r = invoke([{"text": long_text, "status": "pending"}])
check("超长文本被截断", r.ok and len(r.data["items"][0]["text"]) == todo_module.MAX_TEXT_CHARS,
      len(r.data["items"][0]["text"]) if r.ok else r.error)
check("截断留省略号", r.ok and r.data["items"][0]["text"].endswith("…"), r.data["items"][0]["text"][-6:])
check("截断写进回执", "已截断" in r.content, r.content)

r = invoke([{"text": "跑测试", "status": "pending"}, {"text": "  跑测试  ", "status": "pending"}])
check("重复条目只留一条", len(r.data["items"]) == 1, snapshot(r.data["items"]))
check("空白折叠后也算重复", r.data["items"][0]["text"] == "跑测试", r.data["items"][0]["text"])

r = invoke([{"text": "跑测试", "status": "pending"}, {"text": "跑测试", "status": "done"}])
check("重复条目取更靠后的状态", r.data["items"][0]["status"] == "done", snapshot(r.data["items"]))

r = invoke([{"text": "含换行\n的步骤", "status": "pending"}])
check("换行折叠成空格", r.data["items"][0]["text"] == "含换行 的步骤", r.data["items"][0]["text"])

print("\n[4] 清单写进上下文")
context = {}
r = invoke([{"text": "第一步", "status": "active"}], context)
check("上下文里留下清单", context.get("todos") and context["todos"][0]["text"] == "第一步", context)
check("每次整体覆盖", (invoke([{"text": "只剩这条", "status": "pending"}], context),
                  context["todos"][0]["text"] == "只剩这条")[1], context)


print("\n[5] 门控与提示词")
captured = []


def make_fake(turns):
    def fake(messages, tools=None, timeout=30.0, model=None, should_stop=None, **kwargs):
        captured.append({
            "tools": [t["function"]["name"] for t in (tools or [])],
            "system": messages[0].get("content", ""),
            "tool_messages": [m.get("content", "") for m in messages if m.get("role") == "tool"],
        })
        turn = turns[min(len(captured) - 1, len(turns) - 1)]
        for piece in turn.get("stream") or []:
            yield ("token", piece)
        yield ("message", dict(turn))
    return fake


plain = [{"content": "好的", "tool_calls": [], "usage": 1, "cancelled": False}]

captured.clear()
agent.chat_completion_stream = make_fake(plain)
agent.run_agent("你好", mode="chat")
check("默认没有清单工具", "update_todos" not in captured[-1]["tools"], captured[-1]["tools"])
check("默认提示词不提清单", "任务清单" not in captured[-1]["system"])

agent.run_agent("你好", mode="chat", allow_workspace=True)
check("只读代码也有清单工具", "update_todos" in captured[-1]["tools"], captured[-1]["tools"])
check("提示词写明整体提交", "完整清单" in captured[-1]["system"])
check("提示词要求列完就动手", "立刻动手" in captured[-1]["system"])

agent.run_agent("你好", mode="chat", allow_code_edit=True)
check("改代码时有清单工具", "update_todos" in captured[-1]["tools"], captured[-1]["tools"])

agent.run_agent("你好", mode="chat", allow_command=True)
check("跑命令时有清单工具", "update_todos" in captured[-1]["tools"], captured[-1]["tools"])
check("只开跑命令也有清单规则", "任务清单" in captured[-1]["system"])
check("只开跑命令也有读代码说明", "七个只读工具" in captured[-1]["system"])

print("\n[6] 事件顺序与回看")
plan = [{"text": "读代码", "status": "active"}, {"text": "改代码", "status": "pending"}]
progress = [{"text": "读代码", "status": "done"}, {"text": "改代码", "status": "active"}]
turns = [
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c1", "name": "update_todos", "arguments": json.dumps({"items": plan}, ensure_ascii=False)}]},
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c2", "name": "update_todos", "arguments": json.dumps({"items": progress}, ensure_ascii=False)}]},
    {"content": "做完了。", "tool_calls": [], "usage": 1, "cancelled": False},
]
captured.clear()
agent.chat_completion_stream = make_fake(turns)
events = list(agent.run_agent_stream("做点事", mode="chat", allow_code_edit=True))
types = [e["type"] for e in events]

todo_events = [e for e in events if e["type"] == "todos"]
check("两次更新发出两个 todos 事件", len(todo_events) == 2, types)
check("事件带全部条目", todo_events and len(todo_events[0]["items"]) == 2, todo_events[:1])
check("事件带完成数", todo_events and todo_events[0]["done"] == 0 and todo_events[1]["done"] == 1,
      [e["done"] for e in todo_events])
check("事件带总数", todo_events and todo_events[0]["total"] == 2, todo_events[:1])

first_todo = types.index("todos")
first_end = types.index("tool_end")
check("todos 排在 tool_end 之前", first_todo < first_end, types[:8])
check("todos 排在 tool_start 之后", types.index("tool_start") < first_todo, types[:5])

done_event = [e for e in events if e["type"] == "done"][0]
todo_steps = [s for s in done_event["steps"] if s.get("tool") == "update_todos"]
check("步骤里存了清单（历史可回看）", len(todo_steps) == 2
      and todo_steps[-1]["result_data"].get("items"), todo_steps[-1].get("result_data"))
check("步骤里是最后状态", todo_steps[-1]["result_data"]["done"] == 1, todo_steps[-1]["result_data"])

print("\n[7] 同一份清单可以重复提交")
same = [{"text": "读代码", "status": "active"}]
turns = [
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c1", "name": "update_todos", "arguments": json.dumps({"items": same}, ensure_ascii=False)}]},
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c2", "name": "update_todos", "arguments": json.dumps({"items": same}, ensure_ascii=False)}]},
    {"content": "好了。", "tool_calls": [], "usage": 1, "cancelled": False},
]
captured.clear()
agent.chat_completion_stream = make_fake(turns)
events = list(agent.run_agent_stream("再看一次", mode="chat", allow_code_edit=True))
ends = [e for e in events if e["type"] == "tool_end"]
check("相同清单不被判重复调用", all(e["ok"] for e in ends), [e["ok"] for e in ends])
check("两次都发出 todos 事件", len([e for e in events if e["type"] == "todos"]) == 2,
      [e["type"] for e in events])

print("\n[8] 未开启时调用")
turns = [
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c1", "name": "update_todos",
        "arguments": json.dumps({"items": same}, ensure_ascii=False)}]},
    {"content": "没法列。", "tool_calls": [], "usage": 1, "cancelled": False},
]
captured.clear()
agent.chat_completion_stream = make_fake(turns)
events = list(agent.run_agent_stream("列个清单", mode="chat"))
ends = [e for e in events if e["type"] == "tool_end"]
check("未开启时被拒", ends and ends[0]["ok"] is False, ends)
check("提示不在允许列表", ends and "允许列表" in (ends[0]["preview"] or ""),
      ends[0]["preview"][:90] if ends else "")
check("未开启时不发 todos 事件", not [e for e in events if e["type"] == "todos"],
      [e["type"] for e in events])

print("\n[9] 落库后能读回（刷新页面还要能看）")
result = {
    "run_id": "run_todo_persist", "reply": "好了", "status": "ok", "rounds": 2,
    "duration_ms": 1200, "total_tokens": 10,
    "steps": [{
        "kind": "tool_call", "round_no": 1, "tool": "update_todos", "args": {"items": progress},
        "ok": True, "elapsed_ms": 3, "result_preview": "任务清单已更新：1/2 完成",
        "result_data": {"kind": "todos", "items": progress, "total": 2, "done": 1},
    }],
}
database.save_agent_run(1, "做点事", result)
saved = database.get_agent_run("run_todo_persist")
saved_steps = [s for s in saved["steps"] if s.get("tool") == "update_todos"]
check("步骤落库成功", len(saved_steps) == 1, len(saved_steps))
check("读回的清单是列表", isinstance(saved_steps[0]["result_data"].get("items"), list),
      saved_steps[0]["result_data"])
check("读回的状态完整", saved_steps[0]["result_data"]["items"][1]["status"] == "active",
      saved_steps[0]["result_data"]["items"])

print("\n[10] 清单提醒只发给模型，不进界面")
import shutil  # noqa: E402

from tools import _guard as guard  # noqa: E402

WS = os.path.join(tempfile.gettempdir(), "ai_robot_todo_ws")
shutil.rmtree(WS, ignore_errors=True)
os.makedirs(os.path.join(WS, "src"), exist_ok=True)
with open(os.path.join(WS, "src", "calc.py"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write("def add(a, b):\n    return a - b\n")
guard.WORKSPACE_ROOT = os.path.realpath(WS)

EDIT = {"path": "src/calc.py", "old_string": "    return a - b", "new_string": "    return a + b"}
open_todos = [{"text": "改 add", "status": "active"}, {"text": "重跑测试", "status": "pending"}]

captured.clear()
turns = [
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c1", "name": "update_todos", "arguments": json.dumps({"items": open_todos}, ensure_ascii=False)}]},
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c2", "name": "edit_file", "arguments": json.dumps(EDIT)}]},
    {"content": "改好了。", "tool_calls": [], "usage": 1, "cancelled": False},
]
agent.chat_completion_stream = make_fake(turns)
events = list(agent.run_agent_stream("改一下", mode="chat", allow_code_edit=True))
edit_step = [s for s in [e for e in events if e["type"] == "done"][0]["steps"] if s.get("tool") == "edit_file"][0]
check("改完文件后模型收到清单提醒", "清单：" in captured[-1]["tool_messages"][-1],
      captured[-1]["tool_messages"][-1][-80:])
check("提醒不进步骤预览", "清单：" not in (edit_step["result_preview"] or ""),
      edit_step["result_preview"][-80:])

with open(os.path.join(WS, "src", "calc.py"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write("def add(a, b):\n    return a - b\n")

captured.clear()
turns[0]["tool_calls"][0]["arguments"] = json.dumps(
    {"items": [{"text": "改 add", "status": "done"}]}, ensure_ascii=False)
turns[1]["tool_calls"][0]["arguments"] = json.dumps(EDIT)
agent.chat_completion_stream = make_fake(turns)
list(agent.run_agent_stream("再改一下", mode="chat", allow_code_edit=True))
check("清单已全完成时不再提醒", "清单：" not in captured[-1]["tool_messages"][-1],
      captured[-1]["tool_messages"][-1][-60:])

captured.clear()
with open(os.path.join(WS, "src", "calc.py"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write("def add(a, b):\n    return a - b\n")
# 这次完全不列清单，直接改：不该凭空冒出清单提醒
no_plan = [
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c1", "name": "edit_file", "arguments": json.dumps(EDIT)}]},
    {"content": "改好了。", "tool_calls": [], "usage": 1, "cancelled": False},
]
agent.chat_completion_stream = make_fake(no_plan)
list(agent.run_agent_stream("没列清单就改", mode="chat", allow_code_edit=True))
check("没有清单时不会多嘴", "清单：" not in captured[-1]["tool_messages"][-1],
      captured[-1]["tool_messages"][-1][-60:] if captured[-1]["tool_messages"] else "")

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
