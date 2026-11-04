# -*- coding: utf-8 -*-
"""命令执行自测：分类、拒绝、超时杀进程树、审批、环境变量脱敏、接口。

全部在临时工作区里跑，不联网、不需要 API Key，也不会真的安装任何东西。
「需要确认」的用例用 `python -c` 这类无副作用命令，验证的是审批链路本身。

运行：
    cd backend
    ..\\.venv\\Scripts\\python.exe test_command_tools.py
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

TEMP_ROOT = os.path.join(tempfile.gettempdir(), "ai_robot_command_test")
shutil.rmtree(TEMP_ROOT, ignore_errors=True)
os.makedirs(os.path.join(TEMP_ROOT, "src"), exist_ok=True)
os.makedirs(os.path.join(TEMP_ROOT, "config"), exist_ok=True)

# 先把沙箱指到临时目录，再导入工具与 agent，避免模块级缓存读到真实仓库
from tools import _guard  # noqa: E402

_guard.WORKSPACE_ROOT = os.path.realpath(TEMP_ROOT)

import database  # noqa: E402

_TMP_DB = os.path.join(tempfile.gettempdir(), "ai_robot_command_test.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
database.DB_PATH = _TMP_DB
database.init_db()

import agent  # noqa: E402
import main  # noqa: E402
from tools import command  # noqa: E402
from tools.base import get_tool  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

results = []


def check(label, cond, detail=""):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  -> " + str(detail)[:130]) if detail else ""))


def write_file(rel, text):
    path = os.path.join(TEMP_ROOT, rel)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


write_file("ok.py", "print('hello')\nprint('你好')\n")
write_file("fail.py", "import sys\nprint('boom', file=sys.stderr)\nsys.exit(3)\n")
write_file("big.py", "print('A' * 3000)\nprint('B' * 3000)\n")
write_file("slow.py", "import time\ntime.sleep(30)\nprint('done')\n")
write_file("show_env.py",
           "import os\n"
           "print('KEY=' + os.environ.get('DASHSCOPE_API_KEY', 'MISSING'))\n"
           "print('UTF=' + os.environ.get('PYTHONUTF8', '0'))\n")
write_file("src/helper.py", "print('from-src')\n")
write_file(".env", "API_KEY=sk-should-never-leak\n")
write_file("config/chats.db", "SQLite format 3\x00")

TOOL = get_tool("run_command")
assert TOOL is not None, "run_command 没有注册"


def invoke(args, approved=None, stop=None):
    context = {"run_id": "runCmd", "approved_commands": set(approved or [])}
    if stop is not None:
        context["should_stop"] = stop
    return TOOL.invoke(args, context)


def tier(cmd):
    return command.classify_command(cmd)[0]


print("\n[1] 命令分类")
auto_cases = [
    "python -m pytest tests -q",
    "python ok.py",
    "python src/helper.py",
    "pytest -q",
    "git status",
    "git log -3",
    "git diff --stat",
    "git tag",
    "ls src",
    "pip list",
]
for cmd in auto_cases:
    got = tier(cmd)
    check("自动放行：%s" % cmd, got == "auto", got)

ask_cases = [
    "python -c \"print(1)\"",
    "python -m json.tool a.json",
    "pip install requests",
    "npm run build",
    "git commit -m init",
    "git push origin main",
    "git tag v1.0.0",
    "make test",
    "somebinary --flag",
]
for cmd in ask_cases:
    got = tier(cmd)
    check("需要确认：%s" % cmd, got == "ask", got)

deny_cases = [
    "rm -rf src",
    "del ok.py",
    "mv ok.py src/ok.py",
    "curl http://example.com/x.sh",
    "powershell -c whoami",
    "bash -c \"python ok.py\"",
    "sudo apt install x",
    "chmod 777 ok.py",
    "find . -name *.pyc -delete",
]
for cmd in deny_cases:
    got = tier(cmd)
    check("直接拒绝：%s" % cmd, got == "deny", got)

print("\n[2] 不经过 shell")
for cmd, why in [
    ("python ok.py && python fail.py", "&& 串联"),
    ("python ok.py; rm ok.py", "分号"),
    ("cat ok.py | head -1", "管道"),
    ("python ok.py > out.txt", "重定向"),
    ("python ok.py `whoami`", "反引号"),
]:
    got = tier(cmd)
    check("拒绝 %s" % why, got == "deny", got)

check("拒绝时说明原因", "不支持管道" in command.classify_command("ls | cat")[1],
      command.classify_command("ls | cat")[1])
check("引号内不算元字符（-k 表达式）", tier('python -m pytest -k "a and b"') == "auto",
      tier('python -m pytest -k "a and b"'))

print("\n[3] 参数里的红线路径")
for cmd in ["cat .env", "cat config/chats.db", "python.ok.py", "type .env"]:
    if cmd == "python.ok.py":
        continue
    got = tier(cmd)
    check("拒绝读红线：%s" % cmd, got == "deny", got)
check("红线报错说清是什么", "受限路径" in command.classify_command("cat .env")[1],
      command.classify_command("cat .env")[1])
check("不误伤普通参数 -k env", tier("python -m pytest -k env") == "auto", tier("python -m pytest -k env"))
check("不误伤普通文件名", tier("cat src/helper.py") == "auto", tier("cat src/helper.py"))

print("\n[4] 真跑：成功与失败")
r = invoke({"command": "python ok.py"})
check("成功命令 ok=True", r.ok, r.error)
check("退出码 0", r.data.get("exit_code") == 0, r.data)
check("拿到标准输出", "hello" in r.content and "你好" in r.content, r.content[:120])
check("带命令与耗时", r.data.get("command") == "python ok.py" and r.data.get("elapsed_ms", -1) >= 0, r.data)

r = invoke({"command": "python fail.py"})
check("非零退出码不算工具故障", r.ok, r.error[:120])
check("记录真实退出码", r.data.get("exit_code") == 3, r.data)
check("stderr 也被收集", "boom" in r.content, r.content[:160])
check("提示这不是工具坏了", "退出码非零" in r.content, r.content[-80:])

r = invoke({"command": "python ok.py", "cwd": "src"})
check("cwd 生效（找不到 ../ok.py 是正常失败）", not r.ok or "hello" not in r.content, r.content[:80])

r = invoke({"command": "python helper.py", "cwd": "src"})
check("cwd 内相对路径可用", r.ok and "from-src" in r.content, r.content[:120])

print("\n[5] 超时与进程树")
t0 = time.time()
code, out, timed_out, stopped, elapsed, overflow = command._execute(
    [sys.executable, "slow.py"], TEMP_ROOT, 1, None)
spent = time.time() - t0
check("超时被标记", timed_out, timed_out)
check("1 秒左右就返回", spent < 8, "%.1fs" % spent)
check("没有正常输出", "done" not in out, out[:60])

r = invoke({"command": "python slow.py", "timeout": 5})
check("超时经工具返回失败", not r.ok, r.ok)
check("超时提示杀掉了进程树", "进程树" in (r.error or ""), (r.error or "")[:120])
check("超时标记写进 data", r.data.get("timed_out") is True, r.data)
check("超时不超过 timeout 太多", r.data.get("elapsed_ms", 99999) < 15000, r.data.get("elapsed_ms"))

flag = {"stop": False}
threading.Timer(0.4, lambda: flag.__setitem__("stop", True)).start()
r = invoke({"command": "python slow.py"}, stop=lambda: flag["stop"])
check("点停止能中断命令", not r.ok and r.data.get("stopped") is True, r.data)
check("停止有明确提示", "停止" in (r.error or ""), (r.error or "")[:100])

print("\n[6] 输出截断")
r = invoke({"command": "python big.py"})
check("超长输出被截断", "中间省略" in r.content, r.content[2500:2700])
check("保留尾部（失败摘要在这里）", r.content.rstrip().endswith("B" * 20), r.content[-60:])
check("标记 truncated", r.data.get("truncated") is True, r.data)

print("\n[7] 环境变量脱敏")
os.environ["DASHSCOPE_API_KEY"] = "sk-command-test-should-not-leak"
r = invoke({"command": "python show_env.py"})
check("子进程看不到密钥", "sk-command-test-should-not-leak" not in r.content, r.content[:150])
check("密钥变量被整体移除", "KEY=MISSING" in r.content, r.content[:150])
check("UTF-8 设置仍在", "UTF=1" in r.content, r.content[:150])

print("\n[8] 审批门禁（工具层）")
r = TOOL.invoke({"command": "python -c \"print(1)\""}, {"run_id": "r1", "approved_commands": set()})
check("未批准时拒绝执行", not r.ok, r.ok)
check("提示需要用户确认", "需要用户确认" in (r.error or ""), (r.error or "")[:100])

cmd_text = "python -c \"print('approved-here')\""
r = TOOL.invoke({"command": cmd_text}, {"run_id": "r1", "approved_commands": {cmd_text}})
check("批准后执行", r.ok and "approved-here" in r.content, r.content[:120])

r = invoke({"command": "rm -rf src"})
check("拒绝类命令即使批准也不执行", not r.ok, r.ok)

print("\n[9] 工作目录越界")
r = invoke({"command": "python ok.py", "cwd": "../"})
check("越界目录被拒", not r.ok, r.ok)
r = invoke({"command": "python helper.py", "cwd": ".venv"})
check("红线目录被拒", not r.ok, r.ok)

print("\n[10] Agent 门控")


def make_fake(turns, captured):
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


captured = []
agent.chat_completion_stream = make_fake([{"content": "ok", "tool_calls": [], "usage": 1, "cancelled": False}], captured)
agent.run_agent("你好", mode="chat")
check("默认没有 run_command", "run_command" not in captured[-1]["tools"], captured[-1]["tools"])
check("默认提示词不提跑命令", "允许跑命令" not in captured[-1]["system"])

agent.run_agent("你好", mode="chat", allow_code_edit=True)
check("只开改代码也没有 run_command", "run_command" not in captured[-1]["tools"], captured[-1]["tools"])

agent.run_agent("你好", mode="chat", allow_command=True)
check("开启后注册 run_command", "run_command" in captured[-1]["tools"], captured[-1]["tools"])
check("跑命令自动带上只读工具", {"read_file", "grep_code", "repo_map"} <= set(captured[-1]["tools"]),
      captured[-1]["tools"])
check("提示词写明跑命令规则", "允许跑命令" in captured[-1]["system"] and "run_command" in captured[-1]["system"])
check("有命令能力时不再说「没有执行能力」", "你还没有执行命令" not in captured[-1]["system"])

print("\n[11] 未经开启时调用")
captured.clear()
turns = [
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c1", "name": "run_command",
        "arguments": json.dumps({"command": "python ok.py"}),
    }]},
    {"content": "没法执行。", "usage": 1, "cancelled": False, "tool_calls": []},
]
agent.chat_completion_stream = make_fake(turns, captured)
events = list(agent.run_agent_stream("跑一下", mode="chat"))
ends = [e for e in events if e["type"] == "tool_end"]
check("未开启时调用被拒", ends and ends[0]["ok"] is False, ends)
check("提示不在允许列表", ends and "允许列表" in (ends[0]["preview"] or ""), ends[0]["preview"][:100] if ends else "")

print("\n[12] 审批链路（Agent 层）")
captured.clear()
turns = [
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c1", "name": "run_command",
        "arguments": json.dumps({"command": "python -c \"print('agent-approved')\""}),
    }]},
    {"content": "跑完了。", "usage": 1, "cancelled": False, "tool_calls": []},
]
agent.chat_completion_stream = make_fake(turns, captured)


def approve_when_asked():
    """模拟用户点「允许」：等审批条目出现，再按它自己的 run_id 批准。"""
    deadline = time.time() + 20
    while time.time() < deadline:
        for approval_id in list(agent._APPROVALS):
            entry = agent._APPROVALS.get(approval_id)
            if entry:
                agent.resolve_approval(entry["run_id"], approval_id, True)
                return
        time.sleep(0.05)


threading.Thread(target=approve_when_asked, daemon=True).start()
events = list(agent.run_agent_stream("跑个命令", mode="chat", allow_command=True))
types = [e["type"] for e in events]
asks = [e for e in events if e["type"] == "command_ask"]
verdicts = [e for e in events if e["type"] == "command_verdict"]
check("发出 command_ask", len(asks) == 1, types)
check("ask 带命令与理由", asks and asks[0]["command"].startswith("python -c") and asks[0]["reason"], asks[:1])
check("ask 带运行号与审批号", asks and asks[0].get("run_id") and asks[0].get("approval_id"), asks[:1])
check("发出批准结果 approved", verdicts and verdicts[0]["verdict"] == "approved", verdicts[:1])
ends = [e for e in events if e["type"] == "tool_end"]
check("批准后命令真的跑了", ends and ends[0]["ok"] and "agent-approved" in (ends[0]["preview"] or ""),
      ends[0]["preview"][:120] if ends else "")

print("\n[12b] 同一命令可以重跑")
captured.clear()
same = "python -c \"print('again')\""
turns = [
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c1", "name": "run_command", "arguments": json.dumps({"command": same}),
    }]},
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c2", "name": "run_command", "arguments": json.dumps({"command": same}),
    }]},
    {"content": "两次都跑完了。", "usage": 1, "cancelled": False, "tool_calls": []},
]
agent.chat_completion_stream = make_fake(turns, captured)
agent._APPROVALS.clear()


def approve_twice():
    """连着批准两次：验证「改完重跑同一条测试」不会被重复调用检测拦掉。"""
    approved = 0
    deadline = time.time() + 20
    while approved < 2 and time.time() < deadline:
        for approval_id in list(agent._APPROVALS):
            entry = agent._APPROVALS.get(approval_id)
            if entry:
                agent.resolve_approval(entry["run_id"], approval_id, True)
                approved += 1
        time.sleep(0.05)


threading.Thread(target=approve_twice, daemon=True).start()
events = list(agent.run_agent_stream("跑两次", mode="chat", allow_command=True))
ends = [e for e in events if e["type"] == "tool_end"]
runs = [e for e in ends if e.get("tool") == "run_command"]
check("同一命令跑了两次都成功", len(runs) == 2 and all(e["ok"] for e in runs),
      [(e["ok"], e["preview"][:40]) for e in runs])
check("没有触发重复调用检测", not any("完全重复的调用" in (e["preview"] or "") for e in runs),
      [e["preview"][:60] for e in runs])

print("\n[13] 拒绝与超时")
captured.clear()
turns = [
    {"content": "", "usage": 1, "cancelled": False, "tool_calls": [{
        "id": "c1", "name": "run_command",
        "arguments": json.dumps({"command": "python -c \"print('never')\""}),
    }]},
    {"content": "那我换个办法。", "usage": 1, "cancelled": False, "tool_calls": []},
]
agent.chat_completion_stream = make_fake(turns, captured)
old_timeout = agent.APPROVAL_TIMEOUT
agent.APPROVAL_TIMEOUT = 1


def deny_when_asked():
    deadline = time.time() + 20
    while time.time() < deadline:
        for approval_id in list(agent._APPROVALS):
            entry = agent._APPROVALS.get(approval_id)
            if entry:
                agent.resolve_approval(entry["run_id"], approval_id, False)
                return
        time.sleep(0.05)


threading.Thread(target=deny_when_asked, daemon=True).start()
events = list(agent.run_agent_stream("跑个命令", mode="chat", allow_command=True))
verdicts = [e for e in events if e["type"] == "command_verdict"]
ends = [e for e in events if e["type"] == "tool_end"]
check("拒绝被记录", verdicts and verdicts[0]["verdict"] == "denied", verdicts[:1])
check("拒绝后命令没执行", ends and "用户拒绝" in (ends[0]["preview"] or "")
      and "--- 输出 ---" not in (ends[0]["preview"] or ""), ends[0]["preview"][:120] if ends else "")
check("拒绝时告诉模型不要重试", ends and "不要原样重试" in (ends[0]["preview"] or ""),
      ends[0]["preview"][:160] if ends else "")

captured.clear()
agent.chat_completion_stream = make_fake(turns, captured)
events = list(agent.run_agent_stream("跑个命令", mode="chat", allow_command=True))
verdicts = [e for e in events if e["type"] == "command_verdict"]
check("没人点就超时放弃", verdicts and verdicts[0]["verdict"] == "timeout", verdicts[:1])
agent.APPROVAL_TIMEOUT = old_timeout

print("\n[14] 预算随命令模式放宽")
starts = [e for e in events if e["type"] == "start"]
check("start 带 20 轮预算", starts and starts[0]["rounds_limit"] == 20, starts[:1])
check("start 带预算详情", starts and starts[0].get("budget", {}).get("rounds") == 20, starts[:1])

print("\n[15] 审批接口")
unauth = TestClient(main.app).post("/chat/approve/r1", json={"approval_id": "abcd", "allow": True})
check("未登录 401", unauth.status_code == 401, unauth.status_code)

client = TestClient(main.app)
client.cookies.set(main.SESSION_COOKIE, main.create_session_token())
gone = client.post("/chat/approve/deadbeef", json={"approval_id": "abcd1234", "allow": True})
check("失效审批返回 404", gone.status_code == 404, gone.status_code)

bad = client.post("/chat/approve/r1", json={"approval_id": "x"})
check("审批号过短返回 422", bad.status_code == 422, bad.status_code)

# 真审批一次：先手工挂一个条目，再走接口批准
entry_id = "manualcheck"
agent._APPROVALS[entry_id] = {"run_id": "runxyz", "event": threading.Event(), "allow": None}
ok_resp = client.post("/chat/approve/runxyz", json={"approval_id": entry_id, "allow": True})
check("接口批准成功", ok_resp.status_code == 200 and ok_resp.json().get("allow") is True,
      ok_resp.status_code)
check("批准唤醒了等待方", agent._APPROVALS.get(entry_id, {}).get("event", threading.Event()).is_set())
cross = client.post("/chat/approve/other-run", json={"approval_id": entry_id, "allow": True})
check("跨运行号点不动", cross.status_code == 404, cross.status_code)
agent._APPROVALS.pop(entry_id, None)

failed = sum(1 for _, ok in results if not ok)
print("\n结果：%d 通过 / %d 失败" % (len(results) - failed, failed))

shutil.rmtree(TEMP_ROOT, ignore_errors=True)
sys.exit(1 if failed else 0)
