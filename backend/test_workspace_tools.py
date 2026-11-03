# -*- coding: utf-8 -*-
"""工作区只读工具自测：功能 + 安全边界 + 权限门。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
BACKEND = r"D:\hy\ai\ai_robot\backend"
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import tools  # noqa: E402
from tools import get_tool  # noqa: E402
from tools import workspace as ws  # noqa: E402

results = []


def check(label, cond, detail=""):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  -> " + str(detail)) if detail else ""))


print("== 工作区根目录 ==")
print("   WORKSPACE_ROOT =", ws.WORKSPACE_ROOT)
check("根目录存在", os.path.isdir(ws.WORKSPACE_ROOT))

print("== 1. 工具注册 ==")
names = tools.tool_names()
for n in ("list_dir", "read_file", "grep_code"):
    check("注册 %s" % n, n in names)

print("== 2. 路径越界防护 ==")
bad_paths = [
    "..\\..\\Windows\\System32\\drivers\\etc\\hosts",
    "../../../Windows/win.ini",
    "C:\\Windows\\win.ini",
    "/etc/passwd",
]
for p in bad_paths:
    try:
        ws.safe_path(p)
        check("拒绝越界路径 %r" % p, False, "未被拒绝！")
    except ValueError:
        check("拒绝越界路径 %r" % p, True)

print("== 3. 敏感文件黑名单 ==")
for p in ("backend/.env", ".env", "backend/chats.db", "backend/.env.example"):
    try:
        ws.safe_path(p)
        check("拒绝 %r" % p, False, "未被拒绝！")
    except ValueError:
        check("拒绝 %r" % p, True)

print("== 4. 正常路径允许 ==")
for p in ("backend/agent.py", "backend/tools/base.py", "README.md", "."):
    try:
        ws.safe_path(p)
        check("允许 %r" % p, True)
    except ValueError as exc:
        check("允许 %r" % p, False, exc)

print("== 5. read_file ==")
rf = get_tool("read_file")
r = rf.invoke({"path": "backend/tools/base.py"}, {})
check("读取成功", r.ok, r.error)
check("返回带行号", "│" in r.content, r.content.split("\n")[1][:40] if r.ok else "")
check("行数统计正确", r.data.get("total_lines") == 74, r.data)
r2 = rf.invoke({"path": "backend/agent.py", "start_line": 114, "max_lines": 5}, {})
check("分页读取成功", r2.ok and r2.data["start_line"] == 114, r2.data)
check("分页只返回 5 行", r2.data["end_line"] - r2.data["start_line"] + 1 == 5, r2.data)

r3 = rf.invoke({"path": "backend/.env"}, {})
check("拒绝读 .env", not r3.ok, r3.error)
r4 = rf.invoke({"path": "backend/nope.py"}, {})
check("不存在的文件报错", not r4.ok, r4.error)
r5 = rf.invoke({"path": "backend/chats.db"}, {})
check("拒绝读数据库", not r5.ok, r5.error)

print("== 6. list_dir ==")
ld = get_tool("list_dir")
r6 = ld.invoke({}, {})
check("列出根目录", r6.ok, r6.content.split("\n")[0][:60])
check("不出现 .venv", ".venv" not in r6.content)
check("不出现 .git 目录", ".git/" not in r6.content)
check("出现 backend", "backend" in r6.content)
r7 = ld.invoke({"path": "backend/tools"}, {})
check("列出子目录", r7.ok and "base.py" in r7.content, r7.content.split("\n")[0][:50])

print("== 7. grep_code ==")
g = get_tool("grep_code")
r8 = g.invoke({"pattern": "def run_agent"}, {})
check("搜到 run_agent", r8.ok and "agent.py" in r8.content, r8.content.split("\n")[0][:70])
r9 = g.invoke({"pattern": "allow_writes", "path": "backend/agent.py"}, {})
check("限定文件搜索", r9.ok and r9.data["hits"] >= 1, r9.data)
r10 = g.invoke({"pattern": "zq" * 10, "path": "backend/tools"}, {})
check("无匹配时正常返回", r10.ok and "没有找到" in r10.content, r10.content[:60])
r11 = g.invoke({"pattern": "([unclosed"}, {})
check("非法正则报错", not r11.ok, r11.error)
r12 = g.invoke({"pattern": "TAVILY", "path": "backend/.env"}, {})
check("grep 也拒绝 .env", not r12.ok, r12.error)

print("== 8. 权限门：默认不注册进 schema ==")
import agent  # noqa: E402

captured = []


class FakeMsg:
    content = "ok"
    tool_calls = None


def fake_cc(messages, tools=None, timeout=None, model=None):
    captured.append({t["function"]["name"] for t in (tools or [])})
    return FakeMsg(), None


agent.chat_completion = fake_cc
agent.run_agent("你好", mode="chat", allow_workspace=False)
check("关闭时无 read_file", "read_file" not in captured[-1])
agent.run_agent("你好", mode="chat", allow_workspace=True)
check("开启时有 read_file", "read_file" in captured[-1])
check("开启时三个都在", {"list_dir", "read_file", "grep_code"} <= captured[-1])
check("写入工具仍未出现", "remember" not in captured[-1])

failed = sum(1 for _, ok in results if not ok)
print("\n结果：%d 通过 / %d 失败" % (len(results) - failed, failed))
sys.exit(1 if failed else 0)
