"""项目级 Python 环境：一个项目一个 venv，验收、跑命令、装依赖共用它。

为什么现在做（此前是刻意不做的）
--------------------------------
早先记下的两条理由：建 venv 要十几秒；验收和「学生自己跑」会用两个不同的
Python。第一条靠「不自动建」解决——只有用户点「准备环境」时才建，建一次就
一直在；第二条恰恰是要做这件事的理由：学生在项目里 `pip install requests`，
验收却用后端那个 Python 跑，于是「明明装了却 import 不到」。这类失败对刚学
写代码的人几乎无法自己诊断，所以必须由工具保证两边是同一个解释器。

三条边界
--------
1. **不自动建**。建项目时建会让「新建」卡十几秒，跑验收时建会让每次验收都卡。
   只有用户点「准备环境」、或者他要装依赖时才建。
2. **装了就用**。`.venv` 存在 → 验收脚本、`run_command` 的 python/pip/pytest
   全用它；不存在 → 落回跑后端的解释器，行为和加这个功能之前完全一样。
3. **requirements.txt 不是脚本**。pip 的 `-e .`、`git+https://…`、`--index-url`
   都能执行任意代码或指向任意源，所以这里只放行「包名 + 版本约束」。
   要装别的地方来的东西，让学生自己在终端里做——这里不做「帮他绕过检查」的事。

残缺的 .venv
------------
`python -m venv` 中途被打断（磁盘满、超时被杀）会留下一个「目录在、python
不在」的 .venv。之后再建会报「目标目录已存在」，而 has_venv 又是假的，
用户看到的是「准备环境失败」重复一整天。所以 ensure() 在建之前会先清掉
这种残缺目录——只清我们自己那层 `.venv`。

清单改了怎么办
--------------
装过什么记在 `<项目>/.venv/.zhiwei-env.json`，不记在数据库里：这个文件是
环境的附属物，`.venv` 被谁删掉，记录跟着一起消失，不会出现「库里说装好了、
实际没有」。`state()` 把清单指纹和这条记录一比，就知道该不该重装：

- `changed`：清单改过 → 跑验收前自动装一次（`sync()`）；
- `unrecorded`：有环境有清单、但没有记录（可能是学生自己在终端装的）→
  同样算「不一致」，装一次就有了记录。pip 对已满足的包是秒过的。
- `failed`：这份清单上次装失败了 → **不再自动重试**，只在页面上标出来等
  人点。断网时每次验收都重试一次，只会让每次验收都多等十几秒。

失败也要留下痕迹，这一点比成功时更要紧：不记的话，断网环境下每点一次
「跑验收」都会重新撞一次网络超时。
"""

import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

VENV_DIR = ".venv"

# 装过什么的记录，放在 .venv 里面：它描述的是这个环境，环境没了记录也该没
STAMP_NAME = ".zhiwei-env.json"
STAMP_VERSION = 1

ENSURE_TIMEOUT = 180
INSTALL_TIMEOUT = 300

MAX_REQUIREMENTS_LINES = 30
MAX_REQUIREMENT_CHARS = 120

# 子进程里剔掉的变量名片段，和 checks.py / command.py 同一套规则：
# 装依赖不需要看到 API Key，而 pip 会把环境变量透给它拉起的构建脚本。
_SECRET_PARTS = {"KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD",
                 "CREDENTIAL", "APIKEY", "SESSION"}
_KEEP = {"PYTHONIOENCODING", "PYTHONUTF8", "PYTHONHOME", "PYTHONPATH"}

# 每个项目一把锁：同时点两下「准备环境」会建出两个 venv，
# 后一个覆盖前一个，前者正在跑的 pip 就炸了。锁表本身用一把全局锁保护。
# 用 RLock 是因为 install() 持锁时还要调 ensure()——同一线程重入，
# 换成普通 Lock 会在这里自己把自己锁死。
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class EnvError(Exception):
    """环境操作失败（目录不在、venv 建不起来、requirements 不合法）。

    消息直接给用户看，所以要说清「哪一步、为什么」，不要只抛 traceback。
    """


def _lock_for(key):
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _candidate_pythons(project_dir):
    """按平台顺序列出可能存在的 venv 解释器。

    两种布局都认：Windows 是 Scripts/python.exe，POSIX 是 bin/python。
    也认反过来的——项目目录被跨平台拷贝过（WSL 里建、Windows 里打开）时，
    另一个布局里可能真有一个能跑的解释器。
    """
    root = os.path.join(project_dir, VENV_DIR)
    if os.name == "nt":
        folders = ("Scripts", "bin")
    else:
        folders = ("bin", "Scripts")
    candidates = []
    for folder in folders:
        for name in ("python.exe", "python", "python3"):
            candidates.append(os.path.join(root, folder, name))
    return candidates


def venv_python(project_dir):
    """项目 venv 的解释器路径；没有就返回空串。不启动任何进程。"""
    if not project_dir:
        return ""
    for path in _candidate_pythons(str(project_dir)):
        if os.path.isfile(path):
            return path
    return ""


def has_venv(project_dir):
    return bool(venv_python(project_dir))


def python_for(project_dir):
    """返回 (解释器路径, 来源)。来源是 "venv" 或 "system"，给前端和结果用。

    这是「验收」和「学生自己跑」共用同一个解释器的唯一出口：调用方不要
    自己拼 `sys.executable`，否则新加的入口迟早会漏掉项目环境。
    """
    found = venv_python(project_dir)
    if found:
        return found, "venv"
    return sys.executable, "system"


def _env():
    """子进程环境：剔密钥，固定编码，不写 .pyc。"""
    env = {}
    for name, value in os.environ.items():
        if name in _KEEP:
            env[name] = value
            continue
        if any(part in name.upper() for part in _SECRET_PARTS):
            continue
        env[name] = value
    env.update({
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        # pip 在无 tty 时本来就不问，这里再明确一次：没人看着它，不能等输入
        "PIP_NO_INPUT": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    })
    return env


def _kill_tree(proc):
    """杀掉整棵进程树。

    pip 装带 C 扩展的包会拉起编译子进程，只杀父进程会留下孤儿占着文件句柄，
    下一次装同一个包就报「另一个程序正在使用此文件」。
    和 checks.py 里那套是同样的做法（不能互相 import：checks 依赖本模块）。
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _tail(text, limit=2000):
    text = text or ""
    return text if len(text) <= limit else "…" + text[-limit:]


# 断网和「包名写错」在 pip 眼里长得几乎一样：最后都是
#   ERROR: Could not find a version that satisfies the requirement X
#   ERROR: No matching distribution found for X
# 只有前面那几行 WARNING 能分开。所以判断必须先看连接类标记，再看「找不到包」,
# 否则断网会被报成「你包名拼错了」，学生照着改包名只会更糊涂。
_NETWORK_MARKS = (
    "newconnectionerror", "failed to establish a new connection",
    "connection refused", "connection reset", "connection broken",
    "getaddrinfo failed", "temporary failure in name resolution",
    "network is unreachable", "read timed out", "retrying (retry",
    "proxyerror", "sslerror", "积极拒绝",
)
_MISSING_MARKS = ("no matching distribution", "could not find a version")

# pip 自己的重试策略在断网时要试五次、每次 15 秒，加上退避能拖到几分钟。
# 学习台等不起：断网就早说，网络好时这点超时完全够用。
PIP_NET_ARGS = ["--retries", "1", "--timeout", "15"]


def failure_kind(text):
    """把 pip 的输出粗分成几类，用来给人一句能行动的话。"""
    low = (text or "").lower()
    if any(mark in low for mark in _NETWORK_MARKS):
        return "offline"
    if any(mark in low for mark in _MISSING_MARKS):
        return "missing"
    if "permission denied" in low or "winerror 5" in low:
        return "permission"
    return "other"


def _fail_message(kind, code, text):
    """失败时给用户看的话。先说人话，再附 pip 原话——只有原话是查不下去的。"""
    if kind == "offline":
        return ("连不上 PyPI —— 装依赖要联网，这次的包没装进去。已经装过的包不受影响；"
                "网络恢复后点「重试装依赖」就行。\n\npip 的原话：%s" % _tail(text, 800))
    if kind == "missing":
        return ("PyPI 上找不到这个包（名字拼错，或者没有这个版本）。\n\npip 的原话：%s"
                % _tail(text, 800))
    if kind == "permission":
        return ("往项目环境里写文件被拒绝了，可能被占用或没有权限"
                "（杀毒软件、另一个 python 正在跑）。\n\npip 的原话：%s" % _tail(text, 800))
    return "装依赖失败（退出码 %s）。输出末尾：%s" % (code, _tail(text, 1200))


# pip 在项目根下建的三类临时目录。被超时杀掉时不会自己清，留在学生项目里
# 又长又难懂（`pip-build-tracker-b8gb7pv7`），而 tree() 会把它当成他的文件。
_PIP_LEFTOVERS = ("pip-build-tracker-", "pip-install-", "pip-ephem-wheel-cache-",
                  "pip-unpack-", "pip-download-")


def _clean_pip_leftovers(project_dir):
    """清掉 pip 被杀后留下的临时目录。只认这几类前缀，且只删目录。"""
    try:
        names = os.listdir(str(project_dir))
    except OSError:
        return
    for name in names:
        if not name.startswith(_PIP_LEFTOVERS):
            continue
        target = os.path.join(str(project_dir), name)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)


def _run(argv, cwd, timeout):
    """跑一条命令，带超时和整树清理。返回 (code, out, err, timed_out)。"""
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=_env(), text=True, encoding="utf-8", errors="replace",
            start_new_session=(os.name != "nt"),
        )
    except OSError as exc:
        raise EnvError("启动失败：%s" % exc) from exc

    try:
        out, err = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = "", ""
    return proc.returncode, out, err, timed_out


def _rm_venv(project_dir):
    """清掉残缺的 .venv。只删这一层，且只删叫 .venv 的目录。"""
    target = os.path.join(str(project_dir), VENV_DIR)
    if os.path.basename(target) != VENV_DIR or not os.path.isdir(target):
        return
    shutil.rmtree(target, ignore_errors=True)


def ensure(project_dir, with_pip=True, timeout=ENSURE_TIMEOUT):
    """为项目建 venv（幂等）。返回 {created, python, source}。

    `with_pip=False` 只建不带 pip 的最小环境，给测试用：真建一份带 pip 的
    venv 要十几秒，测试里没必要每次都付这个代价。
    """
    path = os.path.abspath(str(project_dir or ""))
    if not os.path.isdir(path):
        raise EnvError("项目目录不存在：%s" % path)

    with _lock_for(path):
        existing = venv_python(path)
        if existing:
            return {"created": False, "python": existing, "source": "venv"}

        # 目录在但解释器不在 = 上次建到一半。venv 会拒绝在非空目录上建，
        # 所以先清掉；不清的话用户会一直看到「准备环境失败」。
        _rm_venv(path)

        argv = [sys.executable, "-m", "venv"]
        if not with_pip:
            argv.append("--without-pip")
        argv.append(os.path.join(path, VENV_DIR))

        code, out, err, timed_out = _run(argv, path, timeout)
        found = venv_python(path)
        if timed_out:
            _rm_venv(path)
            raise EnvError("准备环境超过 %d 秒没结束，已中止。"
                           "可能是磁盘太慢或杀毒软件在扫这个目录。" % timeout)
        if code != 0 or not found:
            _rm_venv(path)
            raise EnvError("准备环境失败（退出码 %s）。%s"
                           % (code, _tail(err or out, 600)))
        return {"created": True, "python": found, "source": "venv"}


def version(python):
    """拿解释器版本号（"3.11.9"）。拿不到返回空串，不抛异常。"""
    try:
        code, out, _err, timed_out = _run([python, "-c",
                                           "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
                                          os.getcwd(), 15)
    except EnvError:
        return ""
    if timed_out or code != 0:
        return ""
    return (out or "").strip().splitlines()[-1] if (out or "").strip() else ""


# ---------------------------------------------------------------- 依赖

# 允许的包名/版本约束。刻意不写成一个巨型正则：分步判断能说清「第几行、
# 哪一段不合法」，而一条正则只能回一句「格式不对」。
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,\s-]*\])?$")
_SPEC_RE = re.compile(r"^(==|>=|<=|!=|~=|>|<)[A-Za-z0-9.*+!_-]+$")
_BANNED_MARKS = ("://", "/", "\\", "@", ";", "&", "|", "$", "`", "'", '"')


def _check_requirement(line, number):
    """校验一行。合法返回空串，不合法返回原因。"""
    text = line.strip()
    if not text or text.startswith("#"):
        return ""
    if len(text) > MAX_REQUIREMENT_CHARS:
        return "第 %d 行太长（%d 字符，上限 %d）" % (number, len(text), MAX_REQUIREMENT_CHARS)
    if text.startswith("-"):
        return ("第 %d 行是 pip 选项（%s）。这里只装 requirements.txt 里列出的包，"
                "不支持 --index-url / -e / -r 这类选项；需要的话请自己在终端里执行"
                % (number, text[:40]))
    for mark in _BANNED_MARKS:
        if mark in text:
            return ("第 %d 行包含 %r：只支持「包名 + 版本约束」，"
                    "不接受 URL、本地路径、环境标记或 shell 符号" % (number, mark))
    # 去掉行内注释（`requests  # 解释`），再按逗号拆多个约束
    head = text.split("#", 1)[0].strip()
    if not head:
        return ""
    if any(ch.isspace() for ch in head):
        return "第 %d 行含空格：一行只写一个包，例如 requests>=2.31" % number

    # 名字和约束的分界是第一个比较运算符
    split_at = len(head)
    for index, ch in enumerate(head):
        if ch in "<>=!~":
            split_at = index
            break
    name = head[:split_at]
    spec_text = head[split_at:]

    if not _NAME_RE.match(name):
        return "第 %d 行的包名不合法：%r" % (number, name[:40])
    if spec_text:
        for part in spec_text.split(","):
            part = part.strip()
            if not _SPEC_RE.match(part):
                return "第 %d 行的版本约束不合法：%r（只支持 == >= <= != ~= > <）" % (number, part[:40])
    return ""


def read_requirements(project_dir):
    """读并校验 requirements.txt。返回 (行列表, 错误说明)。

    文件不存在不算错误——返回空列表，调用方据此提示「先写要装什么」。
    """
    path = os.path.join(str(project_dir or ""), "requirements.txt")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return [], ""
    except OSError as exc:
        return [], "读不了 requirements.txt：%s" % exc

    lines = raw.splitlines()
    if len(lines) > MAX_REQUIREMENTS_LINES:
        return [], ("requirements.txt 有 %d 行，上限 %d 行。"
                    "学习项目不需要这么多依赖，先删掉暂时用不到的。"
                    % (len(lines), MAX_REQUIREMENTS_LINES))

    kept = []
    for index, line in enumerate(lines, 1):
        problem = _check_requirement(line, index)
        if problem:
            return [], problem
        text = line.split("#", 1)[0].strip()
        if text:
            kept.append(text)
    return kept, ""


def _stamp_path(project_dir):
    return os.path.join(str(project_dir or ""), VENV_DIR, STAMP_NAME)


def _normalize(packages):
    """归一化清单：去空、去行内注释、忽略大小写。

    只用来判断「和上次装的是不是同一份」，不拿去喂 pip——`Requests` 和
    `requests` 是同一个包，不该因为改了个大小写就重装一遍。
    """
    cleaned = set()
    for line in packages or []:
        text = str(line).split("#", 1)[0].strip().lower()
        if text:
            cleaned.add(text)
    return cleaned


def fingerprint(packages):
    """清单指纹。只看「装什么」，不看顺序、注释、大小写。"""
    text = "\n".join(sorted(_normalize(packages)))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def read_stamp(project_dir):
    """上次装成功的记录；没有、读不懂、版本对不上都返回 None。"""
    try:
        with open(_stamp_path(project_dir), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != STAMP_VERSION:
        return None
    return data


def _write_stamp(project_dir, data):
    """写记录。写不进去不算错：下次只会被当成「没记录」，多装一次而已。"""
    try:
        with open(_stamp_path(project_dir), "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _record_success(project_dir, packages, source="ui"):
    _write_stamp(project_dir, {
        "version": STAMP_VERSION,
        "hash": fingerprint(packages),
        "packages": list(packages),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python_version": _cfg_version(project_dir),
        "source": source,
        "attempted_hash": "",
        "attempted_at": "",
        "error": "",
    })


def _record_failure(project_dir, packages, error, kind=""):
    """记下「这份清单试过、失败了」。

    成功那份记录原样保留：环境里装的还是上次那批包，报「已装 X」不能变成谎话。
    `kind` 是「断网 / 找不到包 / 没权限」，前端据此换一句话说，不用每次都
    把 pip 的英文原话顶在最前面。
    """
    data = read_stamp(project_dir) or {
        "version": STAMP_VERSION, "hash": "", "packages": [],
        "installed_at": "", "python_version": "", "source": "",
    }
    data["version"] = STAMP_VERSION
    data["attempted_hash"] = fingerprint(packages)
    data["attempted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    data["error"] = str(error)[:800]
    data["error_kind"] = kind or failure_kind(str(error))
    _write_stamp(project_dir, data)


def state(project_dir):
    """清单和已装的东西是否一致。只看磁盘，不跑进程。

    state 的七种取值，前端据此决定显示什么：
      none        没有清单或里头没包——不需要装
      invalid     清单本身不合法——先改清单
      no_env      还没有项目环境——先准备环境
      unrecorded  有环境有清单，但没有装过的记录（可能是自己在终端装的）
      changed     清单和上次装过的不一样
      failed      这份清单上次装失败了，不再自动重试
      ok          装的就是这份
    """
    result = {"state": "none", "dirty": False, "count": 0,
              "added": [], "removed": [], "error": "", "kind": ""}
    if not project_dir or not os.path.isdir(str(project_dir)):
        return result

    packages, problem = read_requirements(project_dir)
    result["count"] = len(packages)
    if problem:
        result.update({"state": "invalid", "error": problem})
        return result
    if not packages:
        return result
    if not has_venv(project_dir):
        result["state"] = "no_env"
        return result

    stamp = read_stamp(project_dir)
    current = fingerprint(packages)
    if not stamp:
        result.update({"state": "unrecorded", "dirty": True})
        return result

    installed = _normalize(stamp.get("packages") or [])
    wanted = _normalize(packages)
    result["added"] = sorted(wanted - installed)
    result["removed"] = sorted(installed - wanted)
    result["state"] = "ok"

    if stamp.get("hash") == current:
        return result
    # 同一份清单刚失败过：不再自动撞一次网络。人点了「重试」才装。
    if stamp.get("attempted_hash") == current and stamp.get("error"):
        result.update({"state": "failed", "error": stamp["error"],
                       "kind": stamp.get("error_kind") or ""})
        return result
    result.update({"state": "changed", "dirty": True})
    return result


def sync(project_dir, timeout=INSTALL_TIMEOUT):
    """清单改过就重装一次。**不抛异常**，失败写进返回值。

    调用点是「跑验收」：装依赖失败不该让验收这件事本身失败——学生还是想看到
    那几条检查的结果，只是结果可能因为缺包而不准。所以他拿到的是一句话加
    一份照跑的结果，而不是一个 500。
    """
    before = state(project_dir)
    if not before["dirty"]:
        return {
            "changed": False,
            "installed": False,
            "state": before["state"],
            "packages": [],
            "added": before["added"],
            "removed": before["removed"],
            # 上次失败过的话照样带出来：验收页面要能解释「为什么可能缺包」
            "error": before["error"] if before["state"] == "failed" else "",
            "kind": before["kind"] if before["state"] == "failed" else "",
            "elapsed_ms": 0,
        }

    started = time.monotonic()
    try:
        done = install(project_dir, timeout=timeout)
    except EnvError as exc:
        packages, _problem = read_requirements(project_dir)
        return {
            "changed": True,
            "installed": False,
            "state": "failed",
            "packages": packages,
            "added": before["added"],
            "removed": before["removed"],
            "error": str(exc),
            "kind": (read_stamp(project_dir) or {}).get("error_kind") or failure_kind(str(exc)),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    return {
        "changed": True,
        "installed": True,
        "state": "ok",
        "packages": done["packages"],
        "added": before["added"],
        "removed": before["removed"],
        "error": "",
        "kind": "",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def install(project_dir, timeout=INSTALL_TIMEOUT, source="ui"):
    """按 requirements.txt 往项目 venv 里装依赖。

    联网动作，只从接口进来（用户点了按钮），不给 agent 一个工具去做——
    模型能写 requirements.txt，装不装、什么时候装由用户决定。唯一例外是
    sync()：它由「跑验收」触发，但只在清单确实改过时动手。

    成功失败都写记录：失败那条是为了让下一次不再自动重试同一份清单。
    """
    path = os.path.abspath(str(project_dir or ""))
    if not os.path.isdir(path):
        raise EnvError("项目目录不存在：%s" % path)

    packages, problem = read_requirements(path)
    if problem:
        raise EnvError(problem)
    if not packages:
        raise EnvError("requirements.txt 里没有可安装的包（文件不存在或只有注释）。"
                       "先让它写清要装什么，每行一个包名，例如 requests>=2.31")

    # 验收自动装和用户手点可能同时发生。同一项目串行，两个 pip 同时改一个
    # site-packages 会互相踩。
    with _lock_for(path):
        try:
            env = ensure(path, with_pip=True)
            python = env["python"]
            argv = [python, "-m", "pip", "install",
                    "--disable-pip-version-check", "--no-input"] + PIP_NET_ARGS + [
                    "-r", "requirements.txt"]

            code, out, err, timed_out = _run(argv, path, timeout)
        except EnvError as exc:
            # 解释器根本没启动（.venv 里的 python 被删了半截、被占用）也要留记录：
            # 不留的话，每次点「跑验收」都会再撞一次同样的失败。
            _record_failure(path, packages, str(exc), kind="other")
            raise
        finally:
            # 超时被杀时 pip 不会自己清临时目录，它们会留在学生项目里
            _clean_pip_leftovers(path)
    text = (out or "") + ("\n" + err if err else "")
    if timed_out:
        message = ("装依赖超过 %d 秒没结束，已中止。网络可能很慢或者根本没通；"
                   "已经装过的包不受影响。输出末尾：%s" % (timeout, _tail(text, 600)))
        _record_failure(path, packages, message, kind="offline")
        raise EnvError(message)
    if code != 0:
        kind = failure_kind(text)
        message = _fail_message(kind, code, text)
        _record_failure(path, packages, message, kind=kind)
        raise EnvError(message)
    _record_success(path, packages, source=source)
    return {
        "python": python,
        "source": "venv",
        "packages": packages,
        "output": _tail(text, 1500),
        "created": env["created"],
    }


def _cfg_version(project_dir):
    """从 .venv/pyvenv.cfg 读 Python 版本。读不到返回空串。"""
    path = os.path.join(str(project_dir or ""), VENV_DIR, "pyvenv.cfg")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                key, _, value = line.partition("=")
                if key.strip() in ("version", "version_info"):
                    return value.strip()
    except OSError:
        pass
    return ""


def info(project_dir):
    """给接口和前端看的现状。只看磁盘，不启动进程。"""
    python = venv_python(project_dir)
    result = {
        "present": bool(python),
        "python": "venv" if python else "system",
        "version": "",
        "requirements": {"present": False, "count": 0, "error": "", "state": "none",
                         "dirty": False, "added": [], "removed": [], "last_error": "",
                         "kind": ""},
    }
    if not project_dir or not os.path.isdir(str(project_dir)):
        return result

    if python:
        # 版本从 pyvenv.cfg 读，不启动进程：页面刷新时每个项目都拉起一次解释器
        # 太贵，而 venv 自己就写好了这个字段
        result["version"] = _cfg_version(project_dir)
        # pip 是否可用：看文件而不跑进程（页面刷新不该拉起解释器）
        venv_root = os.path.join(str(project_dir), VENV_DIR)
        if os.name == "nt":
            marks = (os.path.join(venv_root, "Scripts", "pip.exe"),
                     os.path.join(venv_root, "Lib", "site-packages", "pip"))
            result["pip"] = any(os.path.exists(path) for path in marks)
        else:
            pattern = os.path.join(venv_root, "lib", "python*", "site-packages", "pip")
            result["pip"] = bool(glob.glob(pattern))

    status = state(project_dir)
    result["requirements"] = {
        "present": os.path.isfile(os.path.join(str(project_dir), "requirements.txt")),
        "count": status["count"],
        # error 只放「清单本身不合法」：那是要人去改文件的，和「装失败了」不是一类事
        "error": status["error"] if status["state"] == "invalid" else "",
        "state": status["state"],
        "dirty": status["dirty"],
        "added": status["added"],
        "removed": status["removed"],
        "last_error": status["error"] if status["state"] == "failed" else "",
        "kind": status["kind"],
    }
    return result
