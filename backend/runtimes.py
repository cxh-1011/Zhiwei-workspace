"""本机有哪些语言运行时，以及学生这个项目是用哪种语言写的。

为什么单独一个模块
------------------
验收要跑的是**学生写的代码**，它可能是 JavaScript。发现运行时的规则只该有
一份——验收执行器在父进程里用它决定给子进程什么，`_kit` 在子进程里用它，
两处必须同源。散开写迟早出现「验收说没 node，命令行却能跑」这种自相矛盾的
提示，而学生没有办法判断该信哪一句。

只接 Node，不接 Go / Deno
-------------------------
33 章的练习都是「给数据、还数据」的纯函数，JS 能直接对上：一门带 JSON 的
动态语言就够用。Go 要先编译、产物放哪和增量编译都得定；Deno 要额外拉运行时。
这两件事都不是「加两个文件」的量级，所以这里不列它们——宁可只支持一门做完整，
也不要三门都半成品。要加的时候，`RULES` 加一条、`student_language` 加一个
扩展名判断，验收执行器和 _kit 自动跟着变。
"""

import os
import shutil
import subprocess
import threading
import time

# 版本号查一次缓存几分钟：每点一次验收都 spawn 一个 node 只为读版本太亏。
CACHE_SECONDS = 300

_PROBE_TIMEOUT = 10

# 只认 node。`node.exe` 是为了 Windows 上 which() 找不到无扩展名版本的情况。
_NAMES = ("node", "node.exe")

_LOCK = threading.Lock()
_CACHE = {}

PY_EXTS = (".py",)
JS_EXTS = (".js", ".mjs", ".cjs")


def _probe():
    """找 node 并读版本。找不到、跑不起来都返回 None。"""
    exe = ""
    for name in _NAMES:
        found = shutil.which(name)
        if found:
            exe = found
            break
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, "-v"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=_PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    version = text.splitlines()[0].strip() if text else ""
    if version.startswith("v"):
        version = version[1:]
    return {"path": exe, "version": version}


def get(name="node"):
    """返回 {"path", "version"}，没有就返回 None。"""
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(name)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    info = _probe() if name == "node" else None
    with _LOCK:
        _CACHE[name] = (now, info)
    return info


def clear_cache():
    """测试和「刚装完 node 想立刻用」时用。"""
    with _LOCK:
        _CACHE.clear()


def student_language(project_dir):
    """这个项目里学生代码是什么语言："python" 或 "node"。

    **有 .py 就按 Python 算**。33 章的正文示例都是 Python，两种文件混着放时，
    他要交的那份是 Python 版；反过来按 JS 验，会把一个写对的作业判成「没交」。
    只有当项目里一个 .py 都没有、却有 .js 时，才认为他选了 JS。

    只看项目根目录：学习项目的代码就在根上，扫全量会把 `.venv` 里的
    几十万个 .py 也数进来（那条判断永远成立，等于没判断）。
    """
    try:
        names = os.listdir(str(project_dir or ""))
    except OSError:
        return "python"
    has_py = any(name.endswith(PY_EXTS) for name in names)
    has_js = any(name.endswith(JS_EXTS) for name in names)
    if has_py or not has_js:
        return "python"
    return "node"
