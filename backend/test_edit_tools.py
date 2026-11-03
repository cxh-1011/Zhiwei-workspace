# -*- coding: utf-8 -*-
"""写入工具自测：沙箱、锚点、备份、预算、回滚、开关门控、file_edit 事件。

全部在临时工作区里跑，不碰仓库里的任何真实文件；备份也落在临时目录。
不联网、不需要 API Key。

运行：
    cd backend
    ..\\.venv\\Scripts\\python.exe test_edit_tools.py
"""

import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)

TEMP_ROOT = os.path.join(tempfile.gettempdir(), "ai_robot_edit_test")
if os.path.isdir(TEMP_ROOT):
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
os.makedirs(os.path.join(TEMP_ROOT, "src"), exist_ok=True)

# 先把沙箱指到临时目录，再导入工具：edits.backup_root() 是运行时读的，所以顺序无妨，
# 但 agent / main 里可能有模块级缓存，统一起见先改再导。
from tools import _guard  # noqa: E402

_guard.WORKSPACE_ROOT = os.path.realpath(TEMP_ROOT)

from tools import edits  # noqa: E402
from tools.base import get_tool  # noqa: E402

results = []


def check(label, cond, detail=""):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  -> " + str(detail)[:130]) if detail else ""))


def invoke(name, args=None, run_id="runA"):
    tool = get_tool(name)
    assert tool is not None, "工具未注册：%s" % name
    return tool.invoke(args or {}, {"run_id": run_id})


def read(rel):
    with open(os.path.join(TEMP_ROOT, rel), "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def write_raw(rel, text):
    path = os.path.join(TEMP_ROOT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


print("== 0. 环境 ==")
check("沙箱已指向临时目录", os.path.normcase(_guard.WORKSPACE_ROOT) == os.path.normcase(os.path.realpath(TEMP_ROOT)),
      _guard.WORKSPACE_ROOT)
check("两个写入工具都已注册", get_tool("write_file") is not None and get_tool("edit_file") is not None)

print("\n== 1. write_file 新建 ==")
r = invoke("write_file", {"path": "src/hello.py", "content": "def hello():\n    return 1\n"})
check("新建成功", r.ok, r.error)
check("文件真的存在", os.path.isfile(os.path.join(TEMP_ROOT, "src/hello.py")))
check("内容正确", read("src/hello.py") == "def hello():\n    return 1\n", read("src/hello.py"))
check("标记为新建", r.data.get("created") is True, r.data)
check("新建没有备份", r.data.get("backup") in (None, ""), r.data.get("backup"))
check("带 diff 且是新增行", r.data.get("diff", "").count("\n+") >= 2, r.data.get("diff", "")[:120])
check("统计 +2 行", r.data.get("added") == 2, r.data.get("added"))
check("结果文本提示已新建", "已新建" in r.content, r.content[:60])

print("\n== 2. write_file 覆盖与备份 ==")
r2 = invoke("write_file", {"path": "src/hello.py", "content": "def hello():\n    return 2\n"})
check("覆盖成功", r2.ok, r2.error)
check("内容已更新", "return 2" in read("src/hello.py"))
check("这次有备份", bool(r2.data.get("backup")), r2.data.get("backup"))
backup_rel = r2.data.get("backup") or ""
check("备份在 agent-edits 下", backup_rel.startswith("backups/agent-edits/"), backup_rel)
backup_abs = os.path.join(TEMP_ROOT, backup_rel)
check("备份内容是旧版本", os.path.isfile(backup_abs) and "return 1" in open(
    backup_abs, encoding="utf-8", newline="").read())
check("diff 同时含删除与新增行", "-    return 1" in r2.data.get("diff", "") and "+    return 2" in r2.data.get("diff", ""))

r_same = invoke("write_file", {"path": "src/hello.py", "content": "def hello():\n    return 2\n"})
check("内容一致时不重复写盘", r_same.ok and r_same.data.get("changed") is False, r_same.data)

print("\n== 3. write_file 边界 ==")
check("空 path 被拒", not invoke("write_file", {"path": "", "content": "x"}).ok)
check("content 不是字符串被拒", not invoke("write_file", {"path": "src/a.txt", "content": 123}).ok)
for bad, label in [
    ("../evil.txt", "越界"),
    ("C:/Windows/evil.txt", "盘符绝对路径"),
    (".env", ".env"),
    ("backups/agent-edits/x.txt", "备份目录"),
    ("src/__nope__/a.txt", "父目录不存在"),
    ("src", "目标是目录"),
]:
    res = invoke("write_file", {"path": bad, "content": "x"})
    check("拒绝写入 " + label, not res.ok, res.error)

big = "a" * (edits.MAX_WRITE_BYTES + 10)
r_big = invoke("write_file", {"path": "src/big.txt", "content": big})
check("超过 200KB 被拒", not r_big.ok and "上限" in r_big.error, r_big.error)

print("\n== 4. edit_file 锚点 ==")
write_raw("src/anchor.py", "def a():\n    return 1\n\n\ndef b():\n    return 1\n")
r3 = invoke("edit_file", {"path": "src/anchor.py", "old_string": "def a():\n    return 1", "new_string": "def a():\n    return 10"})
check("唯一锚点替换成功", r3.ok, r3.error)
expected_after_first = "def a():\n    return 10\n\n\ndef b():\n    return 1\n"
check("只改了目标那处", read("src/anchor.py") == expected_after_first, read("src/anchor.py"))
check("标注替换处数", r3.data.get("replacements") == 1, r3.data)

r_dup = invoke("edit_file", {"path": "src/anchor.py", "old_string": "    return 1", "new_string": "    return 2"})
check("重复锚点被拒", not r_dup.ok, r_dup.error)
check("拒绝时给出行号", "行" in r_dup.error, r_dup.error)
check("被拒时文件没变", read("src/anchor.py") == expected_after_first, read("src/anchor.py"))

r_all = invoke("edit_file", {"path": "src/anchor.py", "old_string": "    return 1", "new_string": "    return 2",
                             "replace_all": True})
check("replace_all 批量替换", r_all.ok and read("src/anchor.py").count("return 2") == 2, r_all.data)

r_miss = invoke("edit_file", {"path": "src/anchor.py", "old_string": "def c():\n    pass", "new_string": "x"})
check("锚点不存在被拒", not r_miss.ok, r_miss.error)
check("提示先 read_file", "read_file" in r_miss.error, r_miss.error)

r_same2 = invoke("edit_file", {"path": "src/anchor.py", "old_string": "    return 2", "new_string": "    return 2"})
check("新旧相同被拒", not r_same2.ok, r_same2.error)

r_no = invoke("edit_file", {"path": "src/none.py", "old_string": "a", "new_string": "b"})
check("文件不存在被拒", not r_no.ok and "write_file" in r_no.error, r_no.error)

r_empty = invoke("edit_file", {"path": "src/anchor.py", "old_string": "", "new_string": "x"})
check("空锚点被拒", not r_empty.ok, r_empty.error)

print("\n== 5. CRLF 保持 ==")
write_raw("src/crlf.py", "a = 1\r\nb = 2\r\n")
r4 = invoke("edit_file", {"path": "src/crlf.py", "old_string": "b = 2", "new_string": "b = 3"})
check("CRLF 文件可编辑", r4.ok, r4.error)
check("仍然全是 CRLF", read("src/crlf.py") == "a = 1\r\nb = 3\r\n", repr(read("src/crlf.py")))
check("diff 只动一行，没有整文件改写", r4.data.get("added") == 1 and r4.data.get("removed") == 1, r4.data)

print("\n== 6. 单次运行预算 ==")
old_files_limit, old_bytes_limit = edits.MAX_FILES_PER_RUN, edits.MAX_BYTES_PER_RUN
edits.MAX_FILES_PER_RUN = 2
edits._RUN_STATE.clear()
try:
    ok1 = invoke("write_file", {"path": "src/p1.txt", "content": "1"}, run_id="budget")
    ok2 = invoke("write_file", {"path": "src/p2.txt", "content": "2"}, run_id="budget")
    bad3 = invoke("write_file", {"path": "src/p3.txt", "content": "3"}, run_id="budget")
    again = invoke("write_file", {"path": "src/p1.txt", "content": "1b"}, run_id="budget")
    check("前两个文件写入成功", ok1.ok and ok2.ok, (ok1.error, ok2.error))
    check("第三个文件被上限拦下", not bad3.ok and "上限" in bad3.error, bad3.error)
    check("改已计数的文件不重复占用名额", again.ok, again.error)
    other = invoke("write_file", {"path": "src/p4.txt", "content": "4"}, run_id="another")
    check("换一次运行不受影响", other.ok, other.error)
finally:
    edits.MAX_FILES_PER_RUN, edits.MAX_BYTES_PER_RUN = old_files_limit, old_bytes_limit

edits._RUN_STATE.clear()
edits.MAX_BYTES_PER_RUN = 10
try:
    b1 = invoke("write_file", {"path": "src/bytes1.txt", "content": "0123456789"}, run_id="bytes")
    b2 = invoke("write_file", {"path": "src/bytes2.txt", "content": "0123456789"}, run_id="bytes")
    check("累计字节超限被拦", b1.ok and not b2.ok and "KB" in b2.error, b2.error)
finally:
    edits.MAX_BYTES_PER_RUN = old_bytes_limit
    edits._RUN_STATE.clear()

print("\n== 7. 回滚 ==")
write_raw("src/undo.txt", "v1\n")
u1 = invoke("write_file", {"path": "src/undo.txt", "content": "v2\n"}, run_id="undo")
u2 = invoke("write_file", {"path": "src/undo.txt", "content": "v3\n"}, run_id="undo")
u3 = invoke("write_file", {"path": "src/created.txt", "content": "new\n"}, run_id="undo")
steps = [
    {"kind": "tool_call", "tool": "write_file", "ok": True, "result_data": u1.data},
    {"kind": "tool_call", "tool": "write_file", "ok": True, "result_data": u2.data},
    {"kind": "tool_call", "tool": "write_file", "ok": True, "result_data": u3.data},
]
res = edits.restore_edits(steps)
check("改过的文件被还原", "src/undo.txt" in res["restored"], res)
check("还原到最初内容 v1", read("src/undo.txt") == "v1\n", repr(read("src/undo.txt")))
check("新建的文件被删除", "src/created.txt" in res["removed"]
      and not os.path.exists(os.path.join(TEMP_ROOT, "src/created.txt")), res)
check("没有跳过项", not res["skipped"], res["skipped"])

write_raw("src/guard.txt", "base\n")
g1 = invoke("write_file", {"path": "src/guard.txt", "content": "agent\n"}, run_id="guard")
write_raw("src/guard.txt", "user edited later\n")
res2 = edits.restore_edits([{"kind": "tool_call", "tool": "write_file", "ok": True, "result_data": g1.data}])
check("运行后又被改过的文件不覆盖", read("src/guard.txt") == "user edited later\n", repr(read("src/guard.txt")))
check("跳过原因写清楚", bool(res2["skipped"]) and "又被修改" in res2["skipped"][0]["reason"], res2["skipped"])

res3 = edits.restore_edits([])
check("没有改动时是空结果", res3["changed"] is False and not res3["restored"], res3)

print("\n== 8. Agent 门控与 file_edit 事件 ==")
import agent  # noqa: E402

captured = []


def make_fake(turns):
    state = {"i": 0}

    def fake(messages, tools=None, timeout=None, model=None, should_stop=None, **kwargs):
        captured.append({"tools": {t["function"]["name"] for t in (tools or [])},
                         "system": messages[0]["content"] if messages else ""})
        turn = turns[min(state["i"], len(turns) - 1)]
        state["i"] += 1
        yield ("message", dict(turn))
    return fake


agent.chat_completion_stream = make_fake([{"content": "ok", "tool_calls": [], "usage": 1, "cancelled": False}])
agent.run_agent("你好", mode="chat")
check("默认没有写入工具", not ({"write_file", "edit_file"} & captured[-1]["tools"]))
check("默认提示词不提改代码", "允许改代码" not in captured[-1]["system"])

agent.run_agent("你好", mode="chat", allow_workspace=True)
check("只开读代码时仍不可写", not ({"write_file", "edit_file"} & captured[-1]["tools"]))

agent.run_agent("改一下", mode="chat", allow_code_edit=True)
tools_on = captured[-1]["tools"]
check("开启后可写", {"write_file", "edit_file"} <= tools_on, sorted(tools_on))
check("改代码自动带上只读工具", {"read_file", "grep_code", "repo_map"} <= tools_on, sorted(tools_on))
check("提示词写明改代码规则", "允许改代码" in captured[-1]["system"] and "edit_file" in captured[-1]["system"],
      captured[-1]["system"][-200:])

edits._RUN_STATE.clear()
turns = [
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c1", "name": "write_file",
        "arguments": json.dumps({"path": "src/agent_write.py", "content": "VALUE = 42\n"}, ensure_ascii=False),
    }]},
    {"content": "已经写好了。", "usage": 1, "cancelled": False, "tool_calls": []},
]
agent.chat_completion_stream = make_fake(turns)
events = list(agent.run_agent_stream("新建一个文件", mode="chat", allow_code_edit=True))
types = [event["type"] for event in events]
file_edits = [event for event in events if event["type"] == "file_edit"]
check("产生 file_edit 事件", len(file_edits) == 1, types)
check("file_edit 在 tool_end 之前", "file_edit" in types and types.index("file_edit") < types.index("tool_end"), types)
fe = file_edits[0] if file_edits else {}
check("事件带路径", fe.get("path") == "src/agent_write.py", fe.get("path"))
check("事件带 diff", "+VALUE = 42" in (fe.get("diff") or ""), (fe.get("diff") or "")[:80])
check("确实落盘", os.path.isfile(os.path.join(TEMP_ROOT, "src/agent_write.py")))
done = [event for event in events if event["type"] == "done"][-1]
step = [s for s in done["steps"] if s.get("tool") == "write_file"][0]
check("步骤里存了 diff 供回看", bool(step["result_data"].get("diff")), sorted(step["result_data"]))
check("步骤里存了备份路径字段", "backup" in step["result_data"], sorted(step["result_data"]))

print("\n== 9. 撤销接口 ==")
import database  # noqa: E402

_TMP_DB = os.path.join(tempfile.gettempdir(), "ai_robot_edit_test.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
database.DB_PATH = _TMP_DB
database.init_db()

import main  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

chat_id = database.create_chat("撤销测试")
write_raw("src/api.txt", "before\n")
api_edit = invoke("write_file", {"path": "src/api.txt", "content": "after\n"}, run_id="api")
database.save_agent_run(chat_id, "改文件", {
    "run_id": "run_api_test", "reply": "改好了", "status": "ok", "rounds": 1,
    "duration_ms": 10, "total_tokens": 1,
    "steps": [{"kind": "tool_call", "round_no": 1, "tool": "write_file", "ok": True,
               "args": {}, "result_data": api_edit.data}],
})

client = TestClient(main.app)
resp_401 = client.post("/code/undo/run_api_test")
check("未登录被拦", resp_401.status_code == 401, resp_401.status_code)

client.cookies.set(main.SESSION_COOKIE, main.create_session_token())
resp = client.post("/code/undo/run_api_test")
check("撤销成功", resp.status_code == 200, resp.text[:120])
body = resp.json()
check("返回还原列表", "src/api.txt" in body.get("restored", []), body)
check("文件回到 before", read("src/api.txt") == "before\n", repr(read("src/api.txt")))

resp_404 = client.post("/code/undo/no_such_run")
check("未知运行返回 404", resp_404.status_code == 404, resp_404.status_code)

failed = sum(1 for _, ok in results if not ok)
print("\n结果：%d 通过 / %d 失败" % (len(results) - failed, failed))

shutil.rmtree(TEMP_ROOT, ignore_errors=True)
sys.exit(1 if failed else 0)
