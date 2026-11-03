"""思考过程与轮次事件的回归测试（不联网、不消耗真实 token）。

覆盖：
1. reasoning 片段被转换成独立事件，不会混进回答正文；
2. 每个模型轮次都有 round 事件，顺序正确；
3. show_thinking 会一路传到模型调用层；
4. 思考摘要写进 steps，并能存进数据库再读回来；
5. 端点不支持可选参数时的降级重试。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB = os.path.join(tempfile.gettempdir(), "ai_robot_thinking_test.db")
if os.path.exists(DB):
    os.remove(DB)

import database  # noqa: E402

database.DB_PATH = DB
database.init_db()

import agent  # noqa: E402
import llm  # noqa: E402

passed = 0


def check(name, condition, detail=""):
    global passed
    if not condition:
        raise AssertionError(f"{name} 失败 {detail}")
    passed += 1
    print(f"ok  {name}")


calls = []


def fake_stream(messages, tools=None, timeout=30.0, model=None, should_stop=None,
                provider_id=None, require_vision=False, enable_thinking=False, **kwargs):
    calls.append({"enable_thinking": enable_thinking, "tools": bool(tools)})
    if len(calls) == 1:
        yield ("reasoning", "用户想确认有没有资料，")
        yield ("reasoning", "我先调用工具看一眼。")
        yield ("token", "我先查一下。")
        yield ("message", {
            "content": "我先查一下。",
            "reasoning": "用户想确认有没有资料，我先调用工具看一眼。",
            "tool_calls": [{"id": "call_1", "name": "list_uploaded_files", "arguments": "{}"}],
            "usage": 30, "cancelled": False,
            "model": "qwen3-max", "provider": {"name": "测试服务商", "model": "qwen3-max"},
        })
    else:
        yield ("reasoning", "没有资料，直接回答。")
        yield ("token", "目前没有上传资料。")
        yield ("message", {
            "content": "目前没有上传资料。",
            "reasoning": "没有资料，直接回答。",
            "tool_calls": [], "usage": 12, "cancelled": False,
            "model": "qwen3-max", "provider": {"name": "测试服务商", "model": "qwen3-max"},
        })


agent.chat_completion_stream = fake_stream

events = list(agent.run_agent_stream(
    "我有资料吗？", mode="chat", allowed_tools=["list_uploaded_files"], show_thinking=True
))

kinds = [event["type"] for event in events]
check("事件顺序", kinds[:6] == ["start", "round", "reasoning", "reasoning", "token", "tool_start"], kinds)

reasoning_text = "".join(event["text"] for event in events if event["type"] == "reasoning")
check("思考片段独立成事件", "我先调用工具看一眼" in reasoning_text, reasoning_text)

done = events[-1]
check("done 是最后一帧", done["type"] == "done")

# 正文里不能混进思考内容，否则用户会看到两段话
check("回答不含思考内容", "我脑补" not in done["reply"] and "调用工具" not in done["reply"], done["reply"])

rounds = [event["round"] for event in events if event["type"] == "round"]
check("每轮都发 round 事件", rounds == [1, 2], rounds)

check("show_thinking 传到了模型层", all(item["enable_thinking"] for item in calls), calls)

step_kinds = [step["kind"] for step in done["steps"]]
check("步骤含 llm_call 与 tool_call", "llm_call" in step_kinds and "tool_call" in step_kinds, step_kinds)

llm_steps = [step for step in done["steps"] if step["kind"] == "llm_call"]
check("步骤里留有思考摘要", llm_steps and llm_steps[0].get("reasoning"), llm_steps)

# ---- 落库后能读回来 ----
database.save_agent_run(1, "我有资料吗？", done)
saved = database.get_agent_run(done["run_id"])
saved_llm = [step for step in saved["steps"] if step["kind"] == "llm_call"]
check("数据库保存了 reasoning", saved_llm and saved_llm[0].get("reasoning"), saved_llm)

# ---- 不开思考时不应请求思考 ----
calls.clear()
list(agent.run_agent_stream("你好", mode="chat", allowed_tools=[]))
check("关闭时 enable_thinking 为 False", all(item["enable_thinking"] is False for item in calls), calls)

# ---- 端点不支持可选参数时降级重试 ----
class _FakeCompletions:
    def __init__(self):
        self.attempts = 0

    def create(self, **kwargs):
        self.attempts += 1
        if "stream_options" in kwargs:
            # 真实端点的报错长这样：400 - unsupported parameter
            raise RuntimeError("Error code: 400 - unsupported parameter: stream_options")
        return "STREAM"


class _FakeClient:
    def __init__(self):
        self.chat = type("C", (), {"completions": _FakeCompletions()})()


client = _FakeClient()
stream = llm._open_stream(client, {"model": "x", "messages": [], "stream": True})
check("参数不支持时降级重试", stream == "STREAM" and client.chat.completions.attempts == 2,
      client.chat.completions.attempts)

# ---- reasoning 放在 model_extra 里也能读到 ----
class _ExtraDelta:
    content = None
    model_extra = {"reasoning_content": "思考中"}


check("兼容 model_extra 里的思考字段", llm._delta_field(_ExtraDelta(), "reasoning_content") == "思考中")

check("识别需要开关的模型", llm.supports_thinking("qwen3-max") and llm.supports_thinking("deepseek-reasoner"))
check("普通模型不误判", not llm.supports_thinking("deepseek-chat") and not llm._thinking_body("qwen-plus"))

print(f"\n全部通过：{passed} 项")
