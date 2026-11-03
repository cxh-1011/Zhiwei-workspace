"""流式接口的集成测试：走真实 FastAPI 路由，模型用假的替身。

验证四件事：
1. 没登录时 /chat/stream 返回 401；
2. 登录后能拿到 text/event-stream，并按帧解析出 start / token / done；
3. 流结束后消息与 agent_runs 正常落库；
4. /chat/cancel/{run_id} 能登记取消。

测试用临时数据库，不会污染 backend/chats.db。

运行：
    cd backend
    ..\\.venv\\Scripts\\python.exe test_stream_sse.py
"""

import json
import os
import tempfile

import database

_TMP_DB = os.path.join(tempfile.gettempdir(), "ai_robot_stream_test.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
database.DB_PATH = _TMP_DB
database.init_db()

import agent  # noqa: E402
import main  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


def _fake_stream(messages, tools=None, timeout=30.0, model=None, should_stop=None, **kwargs):
    for piece in ["流式", "回答", "成功。"]:
        yield ("token", piece)
    yield ("message", {
        "content": "流式回答成功。",
        "tool_calls": [],
        "usage": 88,
        "cancelled": False,
    })


agent.chat_completion_stream = _fake_stream


def _events(response):
    parsed = []
    for line in response.iter_lines():
        if not line:
            continue
        if line.startswith("data:"):
            parsed.append(json.loads(line[5:].strip()))
    return parsed


def test_requires_login():
    client = TestClient(main.app)
    response = client.post("/chat/stream", json={"text": "你好", "chat_id": 1})
    assert response.status_code == 401, response.status_code
    print("ok  未登录被拦下")


def test_stream_end_to_end():
    client = TestClient(main.app)
    client.cookies.set(main.SESSION_COOKIE, main.create_session_token())
    chat_id = database.create_chat("流式测试")

    with client.stream("POST", "/chat/stream", json={
        "text": "测试一下流式输出",
        "chat_id": chat_id,
        "mode": "chat",
    }) as response:
        assert response.status_code == 200, response.status_code
        assert response.headers["content-type"].startswith("text/event-stream"), response.headers
        events = _events(response)

    types = [event["type"] for event in events]
    assert types[0] == "start", types
    assert types[-1] == "done", types
    assert "token" in types, types
    text = "".join(event["text"] for event in events if event["type"] == "token")
    assert text == "流式回答成功。", text
    done = events[-1]
    assert done["reply"] == "流式回答成功。", done
    assert done["status"] == "ok", done
    print("ok  SSE 事件顺序与内容")

    messages = database.get_messages(chat_id)
    assert len(messages) == 2, messages
    assert messages[0][2] == "user" and messages[1][2] == "ai", messages
    runs = database.get_agent_runs_for_chat(chat_id)
    assert len(runs) == 1, runs
    assert runs[0]["run_id"] == done["run_id"], runs[0]
    print("ok  消息与运行记录已落库")


def test_cancel_endpoint():
    client = TestClient(main.app)
    client.cookies.set(main.SESSION_COOKIE, main.create_session_token())
    response = client.post("/chat/cancel/abc123")
    assert response.status_code == 200, response.status_code
    assert response.json()["cancelled"] is True, response.json()
    assert agent.is_cancelled("abc123") is True
    agent.clear_cancel("abc123")
    assert agent.is_cancelled("abc123") is False
    print("ok  取消接口登记与清理")


if __name__ == "__main__":
    test_requires_login()
    test_stream_end_to_end()
    test_cancel_endpoint()
    print("\n全部通过")
