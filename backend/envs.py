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
"""

import glob
import os
import re
import shutil
import signal
import subprocess
import sys
import threading

VENV_DIR = ".venv"

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
            lock = threading.Lock()
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


def install(project_dir, timeout=INSTALL_TIMEOUT):
    """按 requirements.txt 往项目 venv 里装依赖。

    联网动作，只从接口进来（用户点了按钮），不给 agent 一个工具去做——
    模型能写 requirements.txt，装不装、什么时候装由用户决定。
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

    env = ensure(path, with_pip=True)
    python = env["python"]
    argv = [python, "-m", "pip", "install",
            "--disable-pip-version-check", "--no-input",
            "-r", "requirements.txt"]

    code, out, err, timed_out = _run(argv, path, timeout)
    text = (out or "") + ("\n" + err if err else "")
    if timed_out:
        raise EnvError("装依赖超过 %d 秒没结束，已中止（可能是网络不可达）。输出末尾：%s"
                       % (timeout, _tail(text, 600)))
    if code != 0:
        raise EnvError("装依赖失败（退出码 %s）。输出末尾：%s" % (code, _tail(text, 1200)))
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
        "requirements": {"present": False, "count": 0, "error": ""},
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

    packages, problem = read_requirements(project_dir)
    result["requirements"] = {
        "present": os.path.isfile(os.path.join(str(project_dir), "requirements.txt")),
        "count": len(packages),
        "error": problem,
    }
    return result
