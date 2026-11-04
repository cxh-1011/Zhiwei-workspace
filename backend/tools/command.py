"""受限命令执行（run_command）：让助手能跑测试、跑脚本、看 git。

先把话说清楚：**这不是沙箱**。真隔离需要容器或系统级限制；在用户本机上跑
`python -m pytest`，本质上就是允许执行代码。这里做的是「降低误伤」，不是
「提供安全边界」，所以默认关闭，必须用户勾选才注册。

四道措施：

1. **分类执行**。白名单里的只读/自测命令直接跑；其余一律等用户点「允许」
   （审批在 agent 层，这里兜底校验）；删除、移动、下载、改权限这类直接拒绝。
2. **不经过 shell**。自己分词，管道、重定向、命令串联、反引号一律拒绝。
   既防注入，也避免 `cmd /c`、`sh -c` 这类隐式外壳把白名单架空。
3. **路径红线**。参数里出现 .env、*.db、私钥这类路径直接拒绝，和读写工具
   共用 `_guard` 的同一份清单。
4. **少给东西**。子进程环境里剔除密钥类变量（否则 `python -c "print(os.environ)"`
   就能把 API Key 抄进上下文），输出只回传首尾片段。

每次执行都有超时，超时后杀整棵进程树：Windows 用 `taskkill /T /F`，
POSIX 用 `killpg`，因为 `python -m pytest` 会拉起子进程，只杀父进程会留下孤儿。
"""

import os
import shutil
import signal
import subprocess
import sys
import threading
import time

from . import _guard
from .base import Tool, ToolResult, register

# 命令字符串与参数数量上限：正常命令远小于这个数，
# 超过基本是要么粘贴了一整段脚本，要么在拼奇怪的东西。
MAX_COMMAND_CHARS = 600
MAX_ARGV = 32

# 采集上限与回传上限分开：先最多采集 40 KB，再取「头 2.6k + 尾 1.6k」。
# 尾段很重要——pytest 的失败摘要都在最后几行，只留开头等于没留。
MAX_OUTPUT_BYTES = 40 * 1024
RETURN_HEAD = 2600
RETURN_TAIL = 1600

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300

# 拒绝在命令里出现的字符：这些都需要 shell 参与，而我们不启 shell
_META_CHARS = {
    ";": "分号",
    "&": "&",
    "|": "竖线（管道）",
    ">": "输出重定向",
    "<": "输入重定向",
    "`": "反引号",
    "\n": "换行（多条命令）",
    "\r": "换行（多条命令）",
}

# 一律拒绝：删除、移动、下载、改权限、起外壳、改系统
DENY_EXES = {
    "rm", "rmdir", "rd", "del", "erase", "unlink", "shred",
    "mv", "move", "ren", "rename", "copy", "cp", "xcopy", "robocopy",
    "dd", "mkfs", "format", "diskpart", "fdisk",
    "shutdown", "reboot", "restart", "halt", "logoff",
    "sudo", "su", "runas", "doas",
    "chmod", "chown", "chgrp", "attrib", "icacls", "takeown", "cacls",
    "taskkill", "tasklist", "kill", "killall", "pkill",
    "reg", "regedit", "sc", "netsh", "net", "wmic", "schtasks",
    "powershell", "pwsh", "cmd", "command", "bash", "sh", "zsh", "fish",
    "cscript", "wscript", "mshta", "rundll32", "regsvr32", "bitsadmin", "certutil",
    "curl", "wget", "ssh", "scp", "sftp", "ftp", "telnet", "nc", "ncat", "socat",
    "env", "export", "set", "setx", "source", "eval", "exec",
    # find / xargs 名字人畜无害，但 `find . -delete`、`xargs rm` 都是真删除
    "find", "xargs",
    "git-lfs", "gitk", "git-gui",
}

# 只读/自测：直接执行。
# 这里刻意不放 `find`：`find . -delete` 是货真价实的删除命令，而它的名字看着人畜无害。
AUTO_EXES = {"ls", "dir", "pwd", "cat", "type", "head", "tail", "wc", "where", "which"}

PYTHON_EXES = {"python", "python3", "py", "pythonw"}

# 需要用户点确认：会装东西、会改仓库状态、会执行任意模块
ASK_EXES = {
    "npm", "npx", "yarn", "pnpm", "node", "deno", "bun",
    "make", "cmake", "cargo", "rustc", "go", "mvn", "gradle", "dotnet",
    "composer", "bundle", "rake", "poetry", "conda", "pyenv",
}

# git 里只读的子命令
GIT_READ = {
    "status", "diff", "log", "show", "branch", "rev-parse", "ls-files",
    "blame", "shortlog", "describe", "grep", "cat-file", "show-ref",
    "reflog", "whatchanged", "tag",  # tag 无参数时只是列出，带了参数会建标签，下面再判
}
# git 里会改仓库状态的子命令：确认后才跑
GIT_WRITE = {"add", "commit", "checkout", "switch", "restore", "reset", "merge",
             "rebase", "stash", "clean", "push", "pull", "fetch", "clone", "init",
             "tag", "cherry-pick", "revert"}

# 子进程环境里剔除的变量名片段：DASHSCOPE_API_KEY、GITHUB_TOKEN 这类
_SECRET_PARTS = {"KEY", "TOKEN", "SECRET", "SECRETS", "PASSWORD", "PASSWD",
                 "CREDENTIAL", "CREDENTIALS", "APIKEY", "SESSION"}


def _exe_name(token: str) -> str:
    """取可执行文件名并去掉 .exe/.cmd 后缀，用于分类。"""
    name = os.path.basename(str(token).replace("\\", "/")).lower()
    for ext in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def _tokenize(command: str) -> list[str]:
    """自己分词：按空白切分、支持引号，遇到 shell 元字符直接报错。

    不用 shlex 是因为 Windows 下 posix=False 会把引号留在 token 里，
    而 posix=True 又会把反斜杠路径吃掉（D:\\a\\b → D:ab）。
    """
    tokens: list[str] = []
    buf: list[str] = []
    quote = ""
    for ch in command:
        if quote:
            if ch == quote:
                quote = ""
            else:
                buf.append(ch)
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch.isspace():
            if buf:
                tokens.append("".join(buf))
                buf = []
            continue
        if ch in _META_CHARS:
            raise ValueError(
                "命令里出现了%s：不支持管道、重定向、命令串联或反引号，"
                "一次只跑一条简单命令（需要多步请分多次调用）" % _META_CHARS[ch]
            )
        buf.append(ch)
    if quote:
        raise ValueError("引号没有配对，无法解析命令")
    if buf:
        tokens.append("".join(buf))
    return tokens


def _redline_in_tokens(tokens: list[str]) -> str:
    """参数里出现红线路径就拒绝，和读写工具共用同一份清单。

    只看「像路径」的 token（含分隔符或点），否则 `-k env` 这类正常参数
    会被误判成环境变量目录。
    """
    for token in tokens:
        if not any(mark in token for mark in ("/", "\\", ".")):
            continue
        reason = _guard.block_reason(os.path.basename(token.replace("\\", "/")))
        if reason:
            return "命令参数里出现了受限路径 %s（%s）。配置、密钥、数据库、依赖与备份数据" \
                   "不能被命令读取，确需其中的信息请让用户直接告诉你。" % (token, reason)
    return ""


def classify_command(command: str, cwd: str = ".") -> tuple[str, str]:
    """把命令分成 auto / ask / deny 三档，返回 (档位, 原因)。

    deny 时原因即拒绝理由，会原样回给模型；ask 时原因用来告诉用户
    「为什么这条需要你确认」。cwd 参与脚本路径解析：`python helper.py`
    在 cwd=src 时指向 src/helper.py，不传 cwd 会把正常命令误判成「脚本不存在」。
    """
    text = str(command or "").strip()
    if not text:
        return "deny", "命令不能为空。"
    if len(text) > MAX_COMMAND_CHARS:
        return "deny", "命令过长（%d 字符，上限 %d），请拆成几条简单命令。" % (len(text), MAX_COMMAND_CHARS)
    try:
        tokens = _tokenize(text)
    except ValueError as exc:
        return "deny", str(exc)
    if not tokens:
        return "deny", "命令不能为空。"
    if len(tokens) > MAX_ARGV:
        return "deny", "参数过多（%d 个，上限 %d），请简化命令。" % (len(tokens), MAX_ARGV)

    redline = _redline_in_tokens(tokens)
    if redline:
        return "deny", redline

    exe = _exe_name(tokens[0])
    rest = tokens[1:]

    if exe in DENY_EXES:
        return "deny", ("`%s` 属于破坏性或有外联风险的命令，一律不执行。"
                        "删除、移动、下载、改权限、起外壳都请你自己在终端里做。" % exe)

    if exe in PYTHON_EXES:
        return _classify_python(rest, cwd)

    if exe in ("pytest", "py.test"):
        return "auto", ""

    if exe == "git":
        return _classify_git(rest)

    if exe in ("pip", "pip3"):
        sub = next((t for t in rest if not t.startswith("-")), "")
        if sub in ("install", "uninstall", "download", "install-requirements"):
            return "ask", "pip %s 会联网安装依赖并执行安装脚本，需要你确认" % sub
        if sub in ("list", "show", "freeze", "check", "config"):
            return "auto", ""
        return "ask", "未识别的 pip 子命令（%s），需要你确认" % (sub or "空")

    if exe in AUTO_EXES:
        return "auto", ""

    if exe in ASK_EXES:
        return "ask", "`%s` 会执行项目构建或脚本，需要你确认" % exe

    return "ask", "`%s` 不在自动放行清单里，需要你确认" % exe


def _classify_python(rest: list[str], cwd: str = ".") -> tuple[str, str]:
    if not rest:
        return "deny", "启动交互式解释器没有意义，请直接给出命令或让我读文件。"
    first = rest[0]
    if first == "-m" and len(rest) >= 2:
        module = rest[1]
        # compileall / py_compile 只把源码编译一遍，不执行其中的代码，是「验证语法
        # 有没有坏」最便宜的手段。自测回路要求模型跑一条验证命令，如果连它们都要
        # 等人点确认，无人值守时整条回路就卡在审批上——实测两轮各等满 120 秒。
        if module in ("pytest", "unittest", "compileall", "py_compile"):
            return "auto", ""
        return "ask", "python -m %s 会执行任意模块，需要你确认" % module
    if first in ("-c", "-"):
        return "ask", "python -c 会执行任意代码，需要你确认"
    if first.startswith("-"):
        return "ask", "未识别的 python 参数组合（%s），需要你确认" % first
    script = first
    if cwd and cwd not in (".", ""):
        script = os.path.join(cwd.replace("\\", "/"), first)
    try:
        target = _guard.resolve(script)
    except ValueError as exc:
        return "deny", str(exc)
    if not os.path.isfile(target):
        return "ask", "脚本不存在：%s" % first
    if not target.lower().endswith(".py"):
        return "ask", "要执行的文件不是 .py（%s），需要你确认" % first
    return "auto", ""


def _classify_git(rest: list[str]) -> tuple[str, str]:
    # 跳过 -C <dir>、--no-pager 这类前置选项，第一个非选项才是子命令
    sub = ""
    index = 0
    while index < len(rest):
        token = rest[index]
        if token == "-C":
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        sub = token
        break
    if not sub:
        return "ask", "没有指定 git 子命令，需要你确认"
    if sub in ("tag",) and len([t for t in rest[index + 1:] if not t.startswith("-")]) > 0:
        return "ask", "创建或修改标签会改动仓库，需要你确认"
    if sub in GIT_READ:
        return "auto", ""
    if sub in GIT_WRITE:
        return "ask", "git %s 会改动仓库状态，需要你确认" % sub
    return "ask", "git %s 不在只读清单里，需要你确认" % sub


# ---------------------------------------------------------------- 执行

def _child_env() -> dict[str, str]:
    """子进程环境：剔除密钥类变量，其余照抄。

    不这么做的话，`python -c "print(os.environ)"` 一句就能把 API Key
    抄进上下文——模型看到的每一行都可能被写进回答和历史记录。
    """
    env = {}
    for name, value in os.environ.items():
        parts = {p.upper() for p in name.split("_")}
        if parts & _SECRET_PARTS:
            continue
        env[name] = value
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "NO_COLOR": "1",
        "CI": "1",
    })
    return env


def _resolve_argv(tokens: list[str]) -> list[str]:
    """把 python / pytest 换成当前解释器，免得模型调到一个没装依赖的系统 Python。"""
    exe = _exe_name(tokens[0])
    if exe in PYTHON_EXES:
        return [sys.executable] + tokens[1:]
    if exe in ("pytest", "py.test") and shutil.which(tokens[0]) is None:
        return [sys.executable, "-m", "pytest"] + tokens[1:]
    return list(tokens)


def _kill_tree(proc: subprocess.Popen) -> None:
    """杀整棵进程树。只 kill 父进程的话，pytest 拉起的子进程会继续占用端口和文件。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass


def _decode(raw: bytes) -> str:
    """UTF-8 优先，失败退 GBK（Windows 原生命令常是本地编码），最后替换非法字节。"""
    for encoding in ("utf-8", "gbk", "utf-16"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", "replace")


def _trim(text: str) -> tuple[str, bool]:
    """保留头尾，中间省略：pytest 的失败摘要都在尾部，只留开头等于没留。"""
    if len(text) <= RETURN_HEAD + RETURN_TAIL + 40:
        return text, False
    omitted = len(text) - RETURN_HEAD - RETURN_TAIL
    return (text[:RETURN_HEAD]
            + "\n…（中间省略 %d 字符）…\n" % omitted
            + text[-RETURN_TAIL:]), True


def _execute(argv: list[str], cwd: str, timeout: int, stop_check) :
    """跑一条命令，返回 (exit_code, output, timed_out, stopped, elapsed_ms, truncated)。"""
    kwargs = {
        "cwd": cwd,
        "env": _child_env(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        # 新建进程组，才能在超时时用 taskkill /T 干净地杀掉整棵树
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True

    started = time.monotonic()
    proc = subprocess.Popen(argv, **kwargs)

    buffer = bytearray()
    overflow = [False]

    def pump() -> None:
        assert proc.stdout is not None
        while True:
            try:
                chunk = proc.stdout.read(8192)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            room = MAX_OUTPUT_BYTES - len(buffer)
            if room > 0:
                buffer.extend(chunk[:room])
            if len(chunk) > max(room, 0):
                overflow[0] = True

    reader = threading.Thread(target=pump, name="run-command-pump", daemon=True)
    reader.start()

    deadline = started + timeout
    timed_out = stopped = False
    while True:
        if proc.poll() is not None:
            break
        if stop_check is not None and stop_check():
            stopped = True
            _kill_tree(proc)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _kill_tree(proc)
            break
        time.sleep(0.05)

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
    reader.join(2)
    if proc.stdout is not None:
        try:
            proc.stdout.close()
        except OSError:
            pass

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return proc.returncode, _decode(bytes(buffer)), timed_out, stopped, elapsed_ms, overflow[0]


def _header(command: str, cwd_rel: str) -> str:
    return "$ %s\n（工作目录 %s）\n" % (command, cwd_rel or ".")


def run_command(arguments: dict, context: dict) -> ToolResult:
    command = str(arguments.get("command") or "").strip()
    cwd_rel = str(arguments.get("cwd") or ".").strip().replace("\\", "/")

    tier, reason = classify_command(command, cwd_rel)
    if tier == "deny":
        return ToolResult(False, "", error=reason)
    if tier == "ask" and command not in (context.get("approved_commands") or set()):
        # 兜底：审批在 agent 层完成，这里再拦一次，防止别的调用方绕过
        return ToolResult(False, "", error="这条命令需要用户确认后才能执行：%s" % reason)

    try:
        cwd = _guard.resolve(cwd_rel)
    except ValueError as exc:
        return ToolResult(False, "", error=str(exc))
    if not os.path.isdir(cwd):
        return ToolResult(False, "", error="工作目录不存在：%s" % cwd_rel)

    try:
        timeout = int(arguments.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    timeout = max(5, min(timeout, MAX_TIMEOUT))

    tokens = _tokenize(command)
    argv = _resolve_argv(tokens)
    shown = " ".join(tokens)

    try:
        exit_code, output, timed_out, stopped, elapsed_ms, overflow = _execute(
            argv, cwd, timeout, context.get("should_stop"),
        )
    except FileNotFoundError:
        return ToolResult(False, "", error="找不到命令 `%s`：它没有安装，或者不在 PATH 里。" % tokens[0])
    except PermissionError:
        return ToolResult(False, "", error="没有执行 `%s` 的权限。" % tokens[0])
    except OSError as exc:
        return ToolResult(False, "", error="启动命令失败：%s" % exc)

    output, trimmed = _trim(output.rstrip())
    payload = {
        "command": shown,
        "cwd": cwd_rel or ".",
        "exit_code": exit_code,
        "elapsed_ms": elapsed_ms,
        "timed_out": timed_out,
        "stopped": stopped,
        "truncated": overflow or trimmed,
    }

    if stopped:
        return ToolResult(False, _header(shown, cwd_rel) + "已按你的要求停止，进程树已终止。",
                          data=payload, error="用户停止了本次运行")
    if timed_out:
        tail = ("\n已有输出（可能不完整）：\n" + output) if output else ""
        return ToolResult(
            False, "",
            data=payload,
            error="命令在 %d 秒内没有结束，已终止整棵进程树。如果是长驻服务（dev server、watch），"
                  "不要用 run_command 启动；如果是测试太慢，请加 -x 或缩小范围。%s" % (timeout, tail),
        )

    body = output or "（命令没有任何输出）"
    note = "（输出过长，已保留首尾）" if (overflow or trimmed) else ""
    head = _header(shown, cwd_rel) + "退出码 %s，耗时 %.1fs%s\n" % (
        exit_code, elapsed_ms / 1000.0, ("，" + note) if note else "")
    if exit_code == 0:
        return ToolResult(True, head + "--- 输出 ---\n" + body, data=payload)

    # 非零退出码不是「工具坏了」，是命令自己失败——这条结果对模型是有效证据，
    # 所以 ok=True，把失败信息放在正文里，并给出怎么继续。
    return ToolResult(
        True,
        head + "--- 输出 ---\n" + body
        + "\n（命令执行完成，但退出码非零：请据此判断哪里失败，需要的话修改后重跑。）",
        data=payload,
    )


register(Tool(
    name="run_command",
    description=(
        "在项目工作区内执行一条命令，用于跑测试、跑脚本、查看 git 状态。"
        "会自动定位当前 Python 解释器。限制：不经过 shell——不能用管道、重定向、"
        "&& 串联、反引号；删除/移动/下载/改权限类命令一律拒绝；"
        "安装依赖、构建、执行任意代码需要用户确认。"
        "返回退出码、耗时与输出（过长时保留首尾）。默认超时 60 秒。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的命令，例如 `python -m pytest tests -q`、`python scripts/check.py`、`git status`",
            },
            "cwd": {
                "type": "string",
                "description": "工作目录（相对项目根目录），默认当前目录",
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数，默认 60，最多 300",
            },
        },
        "required": ["command"],
    },
    handler=run_command,
))
