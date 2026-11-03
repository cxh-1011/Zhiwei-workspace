# -*- coding: utf-8 -*-
"""红线自测：分类拒绝、二进制识别、体积上限、写入侧红线、不误伤。

全部在临时工作区里跑，不碰仓库真实文件；不联网、不需要 API Key。

运行：
    cd backend
    ..\\.venv\\Scripts\\python.exe test_redlines.py
"""

import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

TEMP_ROOT = os.path.join(tempfile.gettempdir(), "ai_robot_redline_test")
if os.path.isdir(TEMP_ROOT):
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
os.makedirs(TEMP_ROOT, exist_ok=True)

# 先把沙箱指到临时目录再导入工具：resolve() 是运行时读模块全局的。
from tools import _guard  # noqa: E402

_guard.WORKSPACE_ROOT = os.path.realpath(TEMP_ROOT)

from tools import edits  # noqa: E402,F401
from tools.base import get_tool  # noqa: E402

results = []


def check(label, cond, detail=""):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label
          + (("  -> " + str(detail)[:140]) if detail else ""))


def abspath(rel):
    return os.path.join(TEMP_ROOT, rel)


def touch(rel, text=""):
    path = abspath(rel)
    os.makedirs(os.path.dirname(path) or TEMP_ROOT, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def touch_bytes(rel, data):
    path = abspath(rel)
    os.makedirs(os.path.dirname(path) or TEMP_ROOT, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def invoke(name, args=None, run_id="runR"):
    tool = get_tool(name)
    assert tool is not None, "工具未注册：%s" % name
    return tool.invoke(args or {}, {"run_id": run_id})


# ---------------------------------------------------------------- 准备素材
touch("src/app.py", "def main():\n    return 0\n")
touch("src/env.py", "# 名字里有 env，但不是环境变量文件\n")
touch("README.md", "SECRET_KEY=this_is_demo_only\n")   # 内容像密钥，但路径不敏感
touch(".gitignore", "*.pyc\n")
touch("notes.txt", "普通文本\n")
touch("empty.txt", "")

# 红线目标（只需要存在，用来验证「按路径拒绝」；不需要真的有敏感内容）
touch(".env", "API_KEY=demo\n")
touch(".env.local", "API_KEY=demo\n")
touch("chats.db", "")
touch("chats.db-wal", "")
touch("chats.db-shm", "")
touch("id_rsa", "")
touch(".npmrc", "")
touch("secrets.json", "{}")
touch("node_modules/pkg/index.js", "module.exports = 1;\n")
touch(".venv/Lib/os.py", "# fake\n")
touch(".git/config", "[core]\n")
touch(".workbuddy/memory/log.md", "# 运行数据\n")
touch("logs/app.log", "boom\n")
touch("uploads/doc.txt", "hi\n")
touch("backups/agent-edits/r1/001__a.py", "# 备份\n")
touch("deep/nested/.ssh/config", "Host *\n")

# 二进制：带 NUL 且有可搜索标记
touch_bytes("src/blob.bin", b"HEADER\x00NEEDLE_INSIDE_BINARY\x00" + b"\x01\x02" * 64)
touch_bytes("src/mystery", b"PK\x00NEEDLE_INSIDE_BINARY" + bytes(range(0, 64)))
# 8 KB 边界：让探针恰好切在一个中文字符中间，验证不误判
touch_bytes("src/cjk_edge.txt", b"a" * 8191 + "中".encode("utf-8") + b"tail\n")
# 纯中文长文本：不应被当成二进制
touch("src/chinese.md", "中文内容" * 500 + "\n")

# 体积素材
touch("big_3mb.txt", "line\n" * 620000)              # 约 3.1 MB
touch("mid_600kb.txt", "line\n" * 120000)            # 约 600 KB
touch("mid_600kb.txt", ("line\n" * 120000) + "UNIQUE_MARKER_XYZ\n")

print("== 0. 沙箱指向临时目录 ==")
check("WORKSPACE_ROOT 已切换", os.path.realpath(_guard.WORKSPACE_ROOT) == os.path.realpath(TEMP_ROOT),
      _guard.WORKSPACE_ROOT)

print("== 1. 红线分类：配置文件与密钥 ==")
secret_cases = [
    (".env", "环境变量"),
    ("backend/.env", "环境变量"),
    (".env.local", "环境变量"),
    (".env.example", "环境变量"),          # 宁严不漏：示例文件也挡
    ("id_rsa", "密钥"),
    (".npmrc", "密钥"),
    ("secrets.json", "密钥"),
    ("deep/nested/.ssh/config", "SSH"),
]
for rel, keyword in secret_cases:
    reason = _guard.block_reason(rel)
    check("拒绝 %s（%s）" % (rel, keyword), bool(reason) and keyword in reason, reason)

print("== 2. 红线分类：数据库（含 SQLite 附属文件）==")
for rel in ("chats.db", "data/app.sqlite", "chats.db-wal", "chats.db-shm", "chats.db-journal", "x.sqlite3"):
    reason = _guard.block_reason(rel)
    check("拒绝 %s" % rel, "数据库" in reason, reason)

print("== 3. 红线分类：目录整棵子树 ==")
dir_cases = [
    ("node_modules/pkg/index.js", "依赖"),
    (".venv/Lib/os.py", "虚拟环境"),
    ("__pycache__/x.pyc", "缓存"),
    (".git/config", "版本库"),
    ("backups/agent-edits/r1/001__a.py", "备份"),
    ("uploads/doc.txt", "上传"),
    ("logs/app.log", "日志"),
    (".workbuddy/memory/log.md", "助手运行数据"),
]
for rel, keyword in dir_cases:
    reason = _guard.block_reason(rel)
    check("拒绝 %s（%s）" % (rel, keyword), keyword in reason, reason)

print("== 4. 不误伤：正常文件照常放行 ==")
allow = ["src/app.py", "src/env.py", "README.md", ".gitignore", "notes.txt",
         "data/sample.csv", "docs/guide.md", "src/loop.py", "config/settings.yaml"]
for rel in allow:
    check("允许 %s" % rel, _guard.block_reason(rel) == "", _guard.block_reason(rel))
check("README 内容含 SECRET_KEY 仍可读（红线只判路径，不猜内容）",
      invoke("read_file", {"path": "README.md"}).ok)

print("== 5. 拒绝话术要点：说清原因，并劝阻换路径试探 ==")
try:
    _guard.resolve(".env")
    check("resolve 拒绝 .env", False, "未被拒绝！")
except ValueError as exc:
    msg = str(exc)
    check("resolve 拒绝 .env", True)
    check("话术含「不可访问」", "不可访问" in msg, msg)
    check("话术含具体类别", "环境变量" in msg, msg)
    check("话术劝阻换路径试探", "换个路径" in msg or "用户直接告诉你" in msg, msg)

print("== 6. 二进制识别 ==")
r = invoke("read_file", {"path": "src/blob.bin"})
check("拒绝读 .bin", not r.ok and "二进制" in r.error, r.error)
r = invoke("read_file", {"path": "src/mystery"})
check("拒绝无后缀的二进制（内容嗅探）", not r.ok and "NUL" in r.error, r.error)
check("纯文本正常读", invoke("read_file", {"path": "notes.txt"}).ok)
check("中文长文本不误判为二进制", invoke("read_file", {"path": "src/chinese.md"}).ok)
check("8 KB 探针切在多字节字符中间也不误判",
      invoke("read_file", {"path": "src/cjk_edge.txt"}).ok)
check("空文件可读", invoke("read_file", {"path": "empty.txt"}).ok)

g = invoke("grep_code", {"pattern": "NEEDLE_INSIDE_BINARY"})
check("grep 不扫二进制内容", g.ok and g.data["hits"] == 0, g.content[:80])

print("== 7. 体积上限 ==")
r = invoke("read_file", {"path": "big_3mb.txt"})
check("3 MB 文件被拒", not r.ok, r.error[:60])
check("提示改用分片读", "start_line" in r.error and "分段读" in r.error, r.error)
r = invoke("read_file", {"path": "mid_600kb.txt", "start_line": 1, "max_lines": 10})
check("600 KB 文本可读（上限提高到 2 MB）", r.ok, r.error)
check("大文件可分页读到尾部",
      invoke("read_file", {"path": "mid_600kb.txt", "start_line": 120000, "max_lines": 5}).ok)
g2 = invoke("grep_code", {"pattern": "UNIQUE_MARKER_XYZ"})
check("grep 跳过超过 512 KB 的文件", g2.ok and g2.data["hits"] == 0, g2.content[:80])

print("== 8. list_dir 隐藏计数：说数量不说名字 ==")
listed = invoke("list_dir", {})
check("列出成功", listed.ok, listed.content[:60])
check("提示有隐藏项", "另有" in listed.content, listed.content.split("\n")[-1])
check("不暴露 .env 文件名", ".env" not in listed.content)
check("不暴露 chats.db 文件名", "chats.db" not in listed.content)
check("正常文件仍然可见", "README.md" in listed.content)
check("hidden 计数进 data", listed.data.get("hidden", 0) >= 3, listed.data)

print("== 9. 写入侧红线 ==")
w = invoke("write_file", {"path": "chats.db", "content": "x"})
check("不能写数据库", not w.ok and "数据库" in w.error, w.error)
w = invoke("write_file", {"path": ".env", "content": "API_KEY=x"})
check("不能写 .env", not w.ok and "环境变量" in w.error, w.error)
w = invoke("write_file", {"path": "node_modules/x.js", "content": "x"})
check("不能写依赖目录", not w.ok, w.error)
w = invoke("write_file", {"path": "logo.png", "content": "not really png"})
check("不能生成二进制文件", not w.ok and "只能写文本" in w.error, w.error)
w = invoke("write_file", {"path": "no/such/dir/a.txt", "content": "x"})
check("父目录不存在被拒", not w.ok and "上级目录" in w.error, w.error)
w = invoke("write_file", {"path": "id_rsa", "content": "x"})
check("不能写私钥文件", not w.ok and "密钥" in w.error, w.error)

e = invoke("edit_file", {"path": "src/blob.bin", "old_string": "HEADER", "new_string": "X"})
check("不能编辑二进制", not e.ok and "二进制" in e.error, e.error)
e = invoke("edit_file", {"path": "src/mystery", "old_string": "PK", "new_string": "X"})
check("不能编辑无后缀二进制", not e.ok and "二进制" in e.error, e.error)

w = invoke("write_file", {"path": "src/env.py", "content": "# 改一行\n"})
check("正常文本可以写（不误伤）", w.ok, w.error)

print("== 10. 三处判断同一套 ==")
for rel in (".env", "chats.db", ".git/config", "src/app.py"):
    same = bool(_guard.block_reason(rel)) == _guard.is_blocked(rel)
    check("block_reason 与 is_blocked 一致：%s" % rel, same)
by_resolve = True
try:
    _guard.resolve(".git/config", write=True)
    by_resolve = False
except ValueError:
    by_resolve = True
check("resolve(write=True) 与 is_blocked 一致", by_resolve)
check("edits 与 workspace 共用同一 WORKSPACE_ROOT",
      os.path.realpath(edits._guard.WORKSPACE_ROOT) == os.path.realpath(_guard.WORKSPACE_ROOT))

print("== 11. 符号链接越界（环境不支持则跳过）==")
link = abspath("escape_link.py")
try:
    os.symlink(os.path.join(tempfile.gettempdir(), "outside_target.py"), link)
except (OSError, NotImplementedError, AttributeError):
    print("  SKIP  当前环境不允许创建符号链接（Windows 需开发者模式）")
else:
    try:
        _guard.resolve("escape_link.py")
        check("拒绝指向工作区外的符号链接", False, "未被拒绝！")
    except ValueError as exc:
        check("拒绝指向工作区外的符号链接", "越界" in str(exc), str(exc))
    os.remove(link)

failed = sum(1 for _, ok in results if not ok)
print("\n结果：%d 通过 / %d 失败" % (len(results) - failed, failed))

shutil.rmtree(TEMP_ROOT, ignore_errors=True)
sys.exit(1 if failed else 0)
