# -*- coding: utf-8 -*-
"""仓库感知工具自测：路径沙箱 + repo_map + git 三件套 + 开关门控。

不联网、不需要 API Key，也不改仓库里的任何文件（除一个临时探针文件，用完即删）。
"""

import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BACKEND)
sys.path.insert(0, BACKEND)
os.chdir(ROOT_DIR)

from tools import get_tool  # noqa: E402
from tools._guard import WORKSPACE_ROOT, resolve  # noqa: E402

results = []


def check(label, cond, detail=""):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  -> " + str(detail)[:110]) if detail else ""))


def invoke(name, args=None):
    tool = get_tool(name)
    assert tool is not None, "工具未注册：%s" % name
    return tool.invoke(args or {}, {})


print("== 工作区根目录 ==")
print("   WORKSPACE_ROOT =", WORKSPACE_ROOT)
check("根目录与仓库一致", os.path.normcase(WORKSPACE_ROOT) == os.path.normcase(ROOT_DIR), WORKSPACE_ROOT)

print("\n== 1. 路径沙箱 ==")
check("正常相对路径可解析", os.path.basename(resolve("backend/agent.py")) == "agent.py")
check("带 . 的路径归一化", resolve("./backend/../backend/main.py").endswith("main.py"))

for bad, label in [
    ("../outside.txt", "上级目录"),
    ("..\\..\\Windows\\win.ini", "反斜杠越界"),
    ("C:/Windows/win.ini", "盘符绝对路径"),
    ("/etc/passwd", "Unix 绝对路径"),
    (".env", ".env 文件"),
    ("backend/.env", "子目录里的 .env"),
    ("backend/chats.db", "数据库文件"),
    (".venv/Lib/os.py", "依赖目录"),
    ("backups/streaming-before-20261103/agent.py", "备份目录"),
]:
    try:
        resolve(bad)
        check("拒绝 " + label, False, "竟然解析成功：%s" % bad)
    except ValueError as exc:
        check("拒绝 " + label, True, str(exc))

# 写入模式：目录不能写、父目录不存在不能写
try:
    resolve("backend", write=True)
    check("写模式拒绝目录", False)
except ValueError:
    check("写模式拒绝目录", True)

try:
    resolve("backend/__nope__/a.txt", write=True)
    check("写模式拒绝不存在的父目录", False)
except ValueError:
    check("写模式拒绝不存在的父目录", True)

check("写模式允许已存在文件", os.path.basename(resolve("backend/agent.py", write=True)) == "agent.py")

print("\n== 2. repo_map 内容 ==")
r = invoke("repo_map", {"path": "backend/tools", "max_chars": 2500})
check("调用成功", r.ok, r.error)
check("包含 repo.py", "repo.py" in r.content)
check("包含顶层符号", "_repo_map" in r.content or "_git_status" in r.content, r.content[:120])
check("包含行数信息", re.search(r"\d+ 行", r.content) is not None)
check("不乱入被拉黑的目录", ".venv" not in r.content and "__pycache__" not in r.content)
check("返回结构完整", {"path", "files", "shown"} <= set(r.data), sorted(r.data))

r_all = invoke("repo_map", {"path": ".", "max_chars": 4000})
check("整仓地图包含 agent.py", "agent.py" in r_all.content)
check("整仓地图包含 run_agent_stream", "run_agent_stream" in r_all.content, r_all.content[:200])
check("整仓地图列出前端目录", "frontend/" in r_all.content, r_all.content[-160:])
check("整仓地图列出 app.js", "app.js" in r_all.content)
agent_row = [line for line in r_all.content.splitlines() if "agent.py" in line]
check("agent.py 带顶层符号", bool(agent_row) and "run_agent_stream" in agent_row[0], agent_row[:1])
check("整仓地图不含 .git 内部", ".git/" not in r_all.content)

# 地图负责「有哪些文件」，具体符号靠 grep_code 兜底，两者配合才算完整
grep_send = invoke("grep_code", {"pattern": "function sendMessage", "path": "frontend/app.js"})
check("grep_code 能补到深层函数", grep_send.ok and "sendMessage" in grep_send.content, grep_send.content[:120])

r_short = invoke("repo_map", {"path": ".", "max_chars": 400})
check("小预算下仍返回", r_short.ok)
check("小预算会被截断并说明", "未展示" in r_short.content or len(r_short.content) < 700, len(r_short.content))
check("截断后不超过预算太多", len(r_short.content) <= 900, len(r_short.content))
check("data 标记了截断", r_short.data.get("truncated") is True, r_short.data)

r_bad = invoke("repo_map", {"path": "../.."})
check("repo_map 拒绝越界", not r_bad.ok, r_bad.error)

r_file = invoke("repo_map", {"path": "backend/agent.py"})
check("可以只对单个文件出图", r_file.ok and "agent.py" in r_file.content, r_file.content[:80])

print("\n== 3. git_status ==")
g = invoke("git_status")
check("调用成功", g.ok, g.error)
check("显示分支", "分支" in g.content)
check("HEAD 是短哈希", re.search(r"[0-9a-f]{7}", g.content) is not None, g.content[:80])
check("dirty 是布尔值", isinstance(g.data.get("dirty"), bool), g.data)
check("分支名是字符串而不是布尔值", isinstance(g.data.get("branch"), str) and g.data["branch"] != "", g.data)
check("干净时给结论", (not g.data.get("dirty")) and "工作区干净" in g.content or g.data.get("dirty"))
# 回归：git 默认 core.quotepath 会把中文文件名转成 \344\270\211 这类八进制
check("中文文件名没有被转义", "\\3" not in g.content, g.content[:120])

print("\n== 4. git_diff ==")
d_stat = invoke("git_diff", {"stat_only": True})
check("stat 模式成功", d_stat.ok, d_stat.error)
check("标题标明 --stat", "--stat" in d_stat.content or "没有未提交的改动" in d_stat.content, d_stat.content[:80])

probe = os.path.join(ROOT_DIR, "probe_untracked.tmp")
try:
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("probe\n")
    d_all = invoke("git_diff")
    check("列出未跟踪文件", "probe_untracked.tmp" in d_all.content, d_all.content[-200:])
finally:
    if os.path.exists(probe):
        os.remove(probe)

d_path = invoke("git_diff", {"path": "backend/tools/_guard.py"})
check("限定文件不报错", d_path.ok, d_path.error)
d_bad = invoke("git_diff", {"path": "../../etc/passwd"})
check("git_diff 拒绝越界", not d_bad.ok, d_bad.error)

print("\n== 5. git_log ==")
lg = invoke("git_log", {"limit": 3})
check("调用成功", lg.ok, lg.error)
check("条数符合 limit", lg.data.get("commits") == 3, lg.data)
check("包含提交哈希", re.search(r"^[0-9a-f]{7}", lg.content.splitlines()[-1]) is not None, lg.content[-60:])

lg_path = invoke("git_log", {"limit": 2, "path": "backend/agent.py"})
check("限定路径不报错", lg_path.ok, lg_path.error)
check("限定路径条数不超过 2", lg_path.data.get("commits", 0) <= 2, lg_path.data)

print("\n== 6. 开关门控 ==")
import agent  # noqa: E402

captured = []


def fake_stream(messages, tools=None, timeout=None, model=None, should_stop=None, **kwargs):
    captured.append({t["function"]["name"] for t in (tools or [])})
    yield ("message", {"content": "ok", "tool_calls": [], "usage": 1, "cancelled": False})


agent.chat_completion_stream = fake_stream
agent.run_agent("你好", mode="chat", allow_workspace=False)
check("关闭时没有 repo_map", "repo_map" not in captured[-1])
check("关闭时没有 git_status", "git_status" not in captured[-1])

agent.run_agent("你好", mode="chat", allow_workspace=True)
EXPECTED = {"repo_map", "list_dir", "read_file", "grep_code", "git_status", "git_diff", "git_log"}
check("开启后七个只读工具齐全", EXPECTED <= captured[-1], sorted(EXPECTED - captured[-1]))
check("仍然没有写入工具", not ({"write_file", "edit_file", "run_command"} & captured[-1]))

failed = sum(1 for _, ok in results if not ok)
print("\n结果：%d 通过 / %d 失败" % (len(results) - failed, failed))
sys.exit(1 if failed else 0)
