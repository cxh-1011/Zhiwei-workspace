"""验收执行器：把 `checks/<chapter>.py` 跑在学生的项目目录里，收回一份结构化结果。

为什么必须是独立进程
--------------------
学生写的代码会崩、会死循环、会 `sys.exit()`、会改全局状态。在同一进程里
import 它，一次 `while True` 就把整个后端拖死，而且没法超时。所以验收脚本一律
用子进程跑，带超时、带进程树清理。

为什么用同一个解释器
--------------------
项目有 `.venv` 就用它，没有才落回 `sys.executable`（跑后端的那个 Python）。
这不是「顺便」：学生在项目里 `pip install` 之后，验收必须能看到那个包，
否则「明明装了却 import 不到」会变成他完全无法诊断的失败。反过来，用系统
python 也会踩到「学生用 3.11 写的语法，验收跑在 3.8 上」。两条都由
`envs.python_for()` 兜住，这里不要自己拼解释器路径。

脚本契约（故意做得笨）
----------------------
1. 参数：第一个参数是项目目录的绝对路径，脚本不要自己猜路径；
2. 输出：**最后一行**是一个 JSON 对象：
   `{"items": [{"name": "...", "ok": true, "detail": "..."}]}`
   `passed` / `total` 由执行器数，脚本不用自己算——自己算就会算错。
3. 退出码不重要：非 0 但输出了合法 JSON 也算成功，因为「验收不通过」本身
   就应该用非 0 退出码表达。

没有脚本的章不报错，返回 `available=False`。假装验收通过了才是最坏的结果。
"""

import json
import os

import signal
import subprocess
import time

import envs
from course import CURRICULUM_ROOT, DEFAULT_COURSE

DEFAULT_TIMEOUT = 90
MAX_TIMEOUT = 300
MAX_OUTPUT = 32 * 1024
MAX_ITEMS = 40

# 子进程环境里剔掉的变量名片段，和 tools/command.py 同一套规则：
# 验收脚本没有任何理由看到 API Key，而学生代码 print(os.environ) 是常见操作。
_SECRET_PARTS = {"KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD",
                 "CREDENTIAL", "APIKEY", "SESSION"}
# 这些变量名包含 KEY，但它们是 Python 自己的行为开关，剔掉会让输出编码乱掉
_KEEP = {"PYTHONIOENCODING", "PYTHONUTF8", "PYTHONPATH", "PYTHONHOME"}


class CheckError(Exception):
    """验收跑不起来（脚本缺失、解释器不在）——和「验收没过」不是一回事。"""


def checks_dir(course_id=DEFAULT_COURSE):
    return CURRICULUM_ROOT / course_id / "checks"


def script_path(chapter_id, course_id=DEFAULT_COURSE):
    """章节 id 只允许 [a-z0-9]，防止 '../..' 拼出别的路径。"""
    text = str(chapter_id or "").strip()
    if not text or not text.isalnum() or not text.isascii() or len(text) > 12:
        raise CheckError("章节 id 不合法：%s" % chapter_id)
    return checks_dir(course_id) / ("%s.py" % text)


def available(chapter_id, course_id=DEFAULT_COURSE):
    try:
        return script_path(chapter_id, course_id).is_file()
    except CheckError:
        return False


def brief(chapter_id, course_id=DEFAULT_COURSE):
    """章节的任务说明（可选）。没有就返回空串。"""
    path = checks_dir(course_id) / ("%s.md" % str(chapter_id or "").strip())
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def list_chapters(course_id=DEFAULT_COURSE):
    """哪些章有验收脚本。前端据此标「这一章能跑验收」。"""
    folder = checks_dir(course_id)
    if not folder.is_dir():
        return []
    return sorted(
        item.stem for item in folder.glob("*.py")
        if not item.stem.startswith("_")
    )


def _clean_env():
    env = {}
    for name, value in os.environ.items():
        if name in _KEEP:
            env[name] = value
            continue
        if any(part in name.upper() for part in _SECRET_PARTS):
            continue
        env[name] = value
    # 固定输出行为：不缓冲，否则超时被杀时拿不到已经打出来的行
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _kill_tree(proc):
    """杀掉整棵进程树。

    `python main.py` 里再 `subprocess.Popen` 一个服务是学生项目的常见写法，
    只杀父进程会留下孤儿占着端口，下一次验收直接「地址已被占用」。
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _parse_result(text):
    """从输出里找出最后一个合法 JSON 对象。

    为什么容忍前面有噪音：学生代码里一句 `print("debug")` 是常态，
    要求「只能输出 JSON」等于让第一次验收必然失败。同时也不放宽到「全文搜」——
    只认最后一行/最后一段，避免把日志里随便一个 `{}` 当结果。
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in reversed(lines[-40:]):
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _normalize(data):
    items = []
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raw_items = []
    for entry in raw_items[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()[:120] or "未命名检查"
        ok = bool(entry.get("ok"))
        detail = str(entry.get("detail") or "").strip()[:400]
        items.append({"name": name, "ok": ok, "detail": detail})
    total = len(items)
    passed = sum(1 for item in items if item["ok"])
    return {
        "items": items,
        "passed": passed,
        "total": total,
        # 「零条检查」不能算通过：脚本坏了、什么都没跑，也是最该被看见的失败
        "ok": total > 0 and passed == total,
    }


def run(project_path, chapter_id, course_id=DEFAULT_COURSE, timeout=DEFAULT_TIMEOUT):
    """跑一次验收。返回结构化结果，任何失败都变成结果里的字段而不是异常。

    唯一会抛的是「跑不起来」（脚本缺失、目录不存在）——那种情况调用方
    要区分对待：脚本缺失是「这章没出验收」，不是「你没通过」。
    """
    script = script_path(chapter_id, course_id)
    if not script.is_file():
        raise CheckError("第 %s 章还没有验收脚本" % chapter_id)

    target = os.path.realpath(str(project_path or ""))
    if not os.path.isdir(target):
        raise CheckError("项目目录不存在：%s" % target)

    timeout = max(5, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    # 项目有自己的 venv 就用它：学生在里面装的包，验收必须看得见
    python, python_source = envs.python_for(target)
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            [python, "-X", "utf8", str(script), target],
            cwd=target,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_clean_env(),
            text=True,
            encoding="utf-8",
            errors="replace",
            # 新进程组：超时时能整组杀掉，不然学生起的服务会活下来
            start_new_session=(os.name != "nt"),
        )
    except OSError as exc:
        raise CheckError("启动验收脚本失败：%s" % exc) from exc

    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = "", ""

    elapsed = int((time.monotonic() - started) * 1000)
    out = (out or "")[:MAX_OUTPUT]
    err = (err or "")[:MAX_OUTPUT]

    if timed_out:
        return {
            "available": True, "ok": False, "passed": 0, "total": 0,
            "items": [], "elapsed_ms": elapsed, "timed_out": True,
            "python_source": python_source,
            "error": "验收超过 %d 秒没结束，已终止。检查一下有没有死循环或阻塞的网络请求。" % timeout,
            "stdout": out, "stderr": err[-2000:],
        }

    data = _parse_result(out)
    if data is None:
        return {
            "available": True, "ok": False, "passed": 0, "total": 0,
            "items": [], "elapsed_ms": elapsed, "timed_out": False,
            "python_source": python_source,
            "error": "验收脚本没有输出结果（最后一行不是合法 JSON）。"
                     "这是验收脚本的问题，不是你代码的问题；下面是对应输出。",
            "stdout": out[-4000:], "stderr": err[-2000:],
        }

    result = _normalize(data)
    result.update({
        "available": True,
        "elapsed_ms": elapsed,
        "timed_out": False,
        # 验收跑在哪个解释器上：前端据此显示「项目环境 / 默认环境」，
        # 也是排查「装了包却说没装」时第一个要看的东西
        "python_source": python_source,
        "error": "",
        # 只有在没过的时候才把原始输出带回去：过了还塞 4KB 日志纯属噪音
        "stdout": "" if result["ok"] else out[-4000:],
        "stderr": "" if result["ok"] else err[-2000:],
    })
    return result
