"""流式 Agent 的离线回归测试。

用假模型替换 dashscope 调用，验证三件事：
1. 没有工具调用时，事件顺序正确、结论落在 done 里；
2. 需要工具时，tool_start / tool_end 成对出现，且第二轮能继续作答；
3. 取消标记生效时，状态是 cancelled 而不是死等。

运行：
    cd backend
    ..\\.venv\\Scripts\\python.exe test_streaming.py
"""

import agent


def _fake_stream(script):
    """把预设的每一轮内容伪装成 chat_completion_stream 的产出。"""
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
            "usage": 120,
            "cancelled": False,
        })

    return fake


def _run(script, question="你好"):
    agent.chat_completion_stream = _fake_stream(script)
    events = list(agent.run_agent_stream(question, mode="chat"))
    return events, events[-1]


def test_plain_answer():
    events, done = _run([{"tokens": ["今天", "天气", "不错。"]}])
    types = [event["type"] for event in events]
    assert types[0] == "start", types
    assert types[-1] == "done", types
    assert done["reply"] == "今天天气不错。", done["reply"]
    assert done["status"] == "ok", done["status"]
    assert done["rounds"] == 1, done["rounds"]
    tokens = "".join(event["text"] for event in events if event["type"] == "token")
    assert tokens == "今天天气不错。", tokens
    assert done["total_tokens"] == 120, done["total_tokens"]
    print("ok  普通回答流式输出")


def test_tool_call_round():
    events, done = _run([
        {
            "tokens": ["我看看你上传了什么。"],
            "tool_calls": [{"id": "c1", "name": "list_uploaded_files", "arguments": "{}"}],
        },
        {"tokens": ["目前没有", "上传资料。"]},
    ])
    starts = [event for event in events if event["type"] == "tool_start"]
    ends = [event for event in events if event["type"] == "tool_end"]
    assert len(starts) == 1 and len(ends) == 1, (starts, ends)
    assert starts[0]["tool"] == "list_uploaded_files", starts[0]
    assert ends[0]["ok"] is True, ends[0]
    assert done["status"] == "ok", done["status"]
    assert done["rounds"] == 2, done["rounds"]
    tool_steps = [step for step in done["steps"] if step["kind"] == "tool_call"]
    assert len(tool_steps) == 1 and tool_steps[0]["ok"] is True, tool_steps
    assert done["reply"].startswith("我看看你上传了什么。"), done["reply"]
    assert done["reply"].endswith("目前没有上传资料。"), done["reply"]
    print("ok  工具调用与第二轮作答")


def test_bad_tool_arguments():
    events, done = _run([
        {"tool_calls": [{"id": "c1", "name": "list_uploaded_files", "arguments": "{不是JSON"}]},
        {"tokens": ["刚才的参数有问题。"]},
    ])
    ends = [event for event in events if event["type"] == "tool_end"]
    assert ends and ends[0]["ok"] is False, ends
    assert done["status"] == "ok", done["status"]
    print("ok  非法工具参数被拦下")


def test_cancel():
    agent.chat_completion_stream = _fake_stream([{"tokens": ["不该被调用"]}])
    original = agent.is_cancelled
    agent.is_cancelled = lambda run_id: True
    try:
        events = list(agent.run_agent_stream("停一下", mode="chat"))
    finally:
        agent.is_cancelled = original
    done = events[-1]
    assert done["type"] == "done", done["type"]
    assert done["status"] == "cancelled", done["status"]
    assert not done["reply"], done["reply"]
    print("ok  取消后立即返回")


if __name__ == "__main__":
    test_plain_answer()
    test_tool_call_round()
    test_bad_tool_arguments()
    test_cancel()
    print("\n全部通过")
