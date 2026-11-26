"""桌面操作工具：真去动鼠标键盘，替用户打开浏览器、输网址、点按钮。

为什么不用 pyautogui
--------------------
1. pyautogui 在 150% 缩放的屏幕上会有坐标漂移：截图按物理像素、点击按逻辑像素，
   2560×1600 的屏幕点下去会偏 1.5 倍。这里在导入时显式声明 DPI 感知，全链路统一用
   物理像素，截图的坐标和点击的坐标是同一个坐标系。
2. 少一层依赖就少一处「pip 装不上 / 版本冲突」的故障点。Win32 API 是系统自带的。

安全边界（四道）
----------------
1. **工具层自查授权**：context 里没有 allow_desktop=True 就拒绝，不依赖调用方。
2. **单会话锁**：同一台机器同一时间只允许一个 run 动鼠标键盘。两个会话同时操作会
   互相抢前台——实测撞过：网页端在点抖音、本地会话在点百度，截图拍到的全是对方窗口，
   视觉模型连续三次答「这不是百度页面」，点下去还会点错。锁按 run_id 记，90 秒没有
   新动作就算对方已经结束。
3. **动作预算**：单次运行最多 MAX_ACTIONS 次桌面动作，防止模型在界面里原地打转。
4. **危险键位与非法参数直接拒绝**：Alt+F4、Win+R、Win+L 这类组合，以及越界的坐标。

所有动作都写进 desktop_shots/actions.log，出事后能回看到底点了哪里。
"""

import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import subprocess
import sys
import time

from . import _guard
from .base import Tool, ToolResult, register

IS_WINDOWS = sys.platform == "win32"
# 一次运行最多做多少次桌面动作。40 次已经够「开浏览器 → 搜 → 点结果 → 翻两页」，
# 再多基本是模型没看懂屏幕在乱试，那种时候应该停下来问人，而不是继续点。
MAX_ACTIONS = 40
# 单次粘贴的文本上限：超过这个长度多半是模型把整篇文章塞进输入框了
MAX_TYPE_CHARS = 2000
# 截图落盘目录（不含项目源码，禁止提交）
# 截图固定落在仓库根（而不是活动根）：沙箱里截图不该混进学生项目
SHOT_DIR = os.path.join(_guard.base_root(), "desktop_shots")

if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # pragma: no cover - 本项目只在 Windows 上跑
    _user32 = None
    _kernel32 = None


# ---------------------------------------------------------------- 基础设施

def _enable_dpi_awareness() -> None:
    """让本进程按物理像素工作。

    这一步必须在任何窗口/截图/坐标调用之前完成，否则会出现「截图 2560 宽、
    点击按 1707 宽算」的错位——点下去落在离目标 1.5 倍远的地方。
    本机是 150% 缩放（DPI 144），不处理这个问题，所有坐标点击都不可用。
    """
    if not IS_WINDOWS:
        return
    try:
        # PER_MONITOR_AWARE_V2 = -4，Win10 1703+ 可用
        _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        _user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi_awareness()

# 虚拟屏幕（多屏合并后的整体区域）。左上角未必是 (0,0)，副屏常在负坐标。
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


def _screen_size() -> tuple[int, int, int, int]:
    if not IS_WINDOWS:
        return (0, 0, 0, 0)
    return (
        _user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        _user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def _log(action: str, detail: str) -> None:
    """动作流水。宁可多写一行，也不要事后靠回忆猜它点了哪。"""
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
        path = os.path.join(SHOT_DIR, "actions.log")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), action, detail))
    except Exception:
        pass


# ---------------------------------------------------------------- 会话锁
#
# 为什么需要锁：鼠标键盘是整台机器共享的一份，没有「每个会话一套」。两个会话同时
# 操作时，甲切了窗口、乙截图就拍到甲的界面，甲点 (339,205) 可能落在乙刚打开的页面上。
# 实测过一次（16:27）：网页端在点抖音，本地会话在点百度，截图连续三次被对方抢走。
#
# 为什么不直接用文件独占句柄：会话可能被用户中途关掉，句柄不会自己释放；用
# 「run_id + 最后动作时间」这种软锁，卡住的会话最多 90 秒后自动让位。

# 超过这么久没有新动作，就认为那个会话已经结束，可以接管
SESSION_LEASE = 90


def _lock_path() -> str:
    return os.path.join(SHOT_DIR, "active-session.json")


def _read_session() -> dict:
    try:
        with open(_lock_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_session(run_id: str) -> None:
    """记下当前会话并刷新时间戳。写不进去就不写——锁坏了不该拦住用户。"""
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
        with open(_lock_path(), "w", encoding="utf-8") as handle:
            json.dump({"run": run_id, "pid": os.getpid(), "ts": time.time()}, handle)
    except OSError:
        pass


def _session_key(context: dict) -> str:
    return str(context.get("run_id") or "").strip() or "anonymous"


def _claim_session(context: dict) -> ToolResult | None:
    """抢占桌面。别人正占着就拒绝，返回 None 表示拿到手了。"""
    key = _session_key(context)
    data = _read_session()
    owner = str(data.get("run") or "")
    if owner and owner != key:
        try:
            age = time.time() - float(data.get("ts") or 0)
        except (TypeError, ValueError):
            age = SESSION_LEASE + 1
        if age < SESSION_LEASE:
            return ToolResult(
                False, "",
                error="另一个会话正在操作桌面（%s，%d 秒前还有动作）。"
                "两个会话同时动鼠标会互相打断：截图可能是对方的界面，点下去会点错。"
                "等它结束再来；如果那个会话已经关了，最多 %d 秒后会自动让位。"
                % (owner[:16], int(age), SESSION_LEASE),
            )
    _write_session(key)
    return None


def release_desktop_session(run_id: str = "") -> None:
    """跑完主动放锁。

    只放自己的：如果传了 run_id 而锁在别人手里，说明那个会话还在跑，不能碰它的锁。
    不主动放锁的话，用户刚跑完一次，90 秒内换个会话再操作会被自己上次的锁挡住，
    看起来像「莫名其妙用不了」。
    """
    data = _read_session()
    owner = str(data.get("run") or "")
    if run_id and owner and owner != str(run_id):
        return
    try:
        os.remove(_lock_path())
    except OSError:
        pass


def _gate(context: dict) -> ToolResult | None:
    """权限、会话锁与预算的公共闸门。返回非 None 表示应当直接拒绝。"""
    if not context.get("allow_desktop"):
        return ToolResult(
            False, "", error="没有开启「允许操作桌面」，拒绝执行。"
            "这项能力会真的移动鼠标和键盘，必须由用户在界面上单独打开。",
        )
    if not IS_WINDOWS:
        return ToolResult(False, "", error="桌面操作目前只支持 Windows。")
    blocked = _claim_session(context)
    if blocked:
        return blocked
    used = int(context.get("desktop_actions") or 0)
    if used >= MAX_ACTIONS:
        return ToolResult(
            False, "", error="本次运行已经做了 %d 次桌面操作，到达上限。"
            "请停下来向用户说明当前进展，让他接手下一步。" % MAX_ACTIONS,
        )
    context["desktop_actions"] = used + 1
    return None


# ---------------------------------------------------------------- 打开网址 / 应用

_CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
_EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

# 只允许这几种应用。开放「随便指定一个 exe」等于把 run_command 的黑名单绕过去了。
APP_ALIASES = {
    "chrome": _CHROME_PATHS,
    "chrome浏览器": _CHROME_PATHS,
    "谷歌浏览器": _CHROME_PATHS,
    "edge": _EDGE_PATHS,
    "browser": _EDGE_PATHS,
    "notepad": (r"C:\Windows\System32\notepad.exe",),
    "记事本": (r"C:\Windows\System32\notepad.exe",),
    "calc": (r"C:\Windows\System32\calc.exe",),
    "计算器": (r"C:\Windows\System32\calc.exe",),
    "explorer": (r"C:\Windows\explorer.exe",),
    "资源管理器": (r"C:\Windows\explorer.exe",),
    "mspaint": (r"C:\Windows\System32\mspaint.exe",),
    "画图": (r"C:\Windows\System32\mspaint.exe",),
}

_BROWSER_ALIASES = {"chrome", "chrome浏览器", "谷歌浏览器", "edge", "browser"}


def _first_existing(paths) -> str | None:
    for path in paths:
        if os.path.isfile(path):
            return path
    return None


def _launch_url(url: str, browser: str = "") -> str:
    """真正打开网址的一步，单独拆出来是为了测试能替换掉，不会真弹窗口。"""
    browser = (browser or "").strip().lower()
    if browser and browser in _BROWSER_ALIASES:
        exe = _first_existing(APP_ALIASES[browser])
        if not exe:
            raise RuntimeError("没找到 %s 的可执行文件，可以换默认浏览器打开" % browser)
        subprocess.Popen([exe, url], close_fds=True)
        return os.path.basename(exe)
    os.startfile(url)  # noqa: S606 - 系统级打开，交给默认浏览器
    return "系统默认程序"


def _normalize_url(raw: str) -> str:
    """补协议、挡掉非 http(s)。"""
    url = (raw or "").strip().strip('"').strip("'")
    if not url:
        raise ValueError("网址为空")
    lowered = url.lower()
    if re.match(r"^(javascript|data|vbscript|file|about):", lowered):
        raise ValueError("只支持 http/https 网址；本地文件请用 open_app 打开")
    if not re.match(r"^[a-z]+://", lowered):
        # 「www.baidu.com」这种省了协议的写法很常见，自动补 https
        url = "https://" + url
    if not re.match(r"^https?://", url.lower()):
        raise ValueError("只支持 http/https 网址")
    return url


def _open_url(args: dict, context: dict) -> ToolResult:
    blocked = _gate(context)
    if blocked:
        return blocked
    try:
        url = _normalize_url(str(args.get("url", "")))
        target = _launch_url(url, str(args.get("browser", "")))
    except (ValueError, RuntimeError, OSError) as error:
        return ToolResult(False, "", error=str(error))
    _log("open_url", "%s -> %s" % (url, target))
    # 记一笔：浏览器打开网址后焦点停在地址栏。后面按页面键或输入时据此先按 esc。
    context["desktop_fresh_open"] = True
    return ToolResult(
        True,
        "已用 %s 打开：%s\n浏览器可能需要一两秒才渲染完。"
        "如果下一步要在这个页面里输入，先等它加载，必要时用 list_windows 确认窗口标题已经变了。"
        % (target, url),
        data={"url": url},
    )


def _open_app(args: dict, context: dict) -> ToolResult:
    blocked = _gate(context)
    if blocked:
        return blocked
    target = str(args.get("app", "")).strip()
    if not target:
        return ToolResult(False, "", error="缺少要打开的应用名或路径")
    key = target.lower()
    if key in APP_ALIASES:
        exe = _first_existing(APP_ALIASES[key])
        if not exe:
            return ToolResult(False, "", error="找不到 %s 的安装路径" % target)
        try:
            subprocess.Popen([exe], close_fds=True)
        except OSError as error:
            return ToolResult(False, "", error="启动失败：%s" % error)
        _log("open_app", exe)
        return ToolResult(True, "已启动：%s" % os.path.basename(exe))

    # 工作区内的文件或文件夹允许直接打开（用默认程序），其余一律拒绝：
    # 放行任意路径等于给了「打开任何 exe」的能力。
    root = _guard.active_root()
    candidate = os.path.realpath(os.path.join(root, target))
    if candidate.startswith(os.path.realpath(root) + os.sep) and os.path.exists(candidate):
        if candidate.lower().endswith((".exe", ".bat", ".cmd", ".ps1", ".msi")):
            return ToolResult(False, "", error="出于安全考虑，不能从工作区启动可执行文件")
        try:
            os.startfile(candidate)  # noqa: S606
        except OSError as error:
            return ToolResult(False, "", error="打开失败：%s" % error)
        _log("open_app", candidate)
        return ToolResult(True, "已用默认程序打开：%s" % candidate)
    return ToolResult(
        False, "",
        error="只支持这些应用：%s；或者项目目录内已存在的文件。"
        % "、".join(sorted(k for k in APP_ALIASES if not k.isascii())),
    )


# ---------------------------------------------------------------- 窗口

def _enum_windows() -> list[dict]:
    """列出可见且有标题的顶层窗口。"""
    items: list[dict] = []
    if not IS_WINDOWS:
        return items

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _callback(hwnd, _lparam):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            length = _user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            if not title:
                return True
            rect = wt.RECT()
            if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            if rect.right - rect.left < 40 or rect.bottom - rect.top < 40:
                return True
            items.append({
                "handle": int(hwnd),
                "title": title,
                "rect": [rect.left, rect.top, rect.right, rect.bottom],
                "minimized": bool(_user32.IsIconic(hwnd)),
            })
        except Exception:
            pass
        return True

    try:
        _user32.EnumWindows(_callback, 0)
    except Exception:
        return []
    if items:
        try:
            foreground = int(_user32.GetForegroundWindow())
            for item in items:
                item["foreground"] = item["handle"] == foreground
        except Exception:
            pass
    return items


def _list_windows(args: dict, context: dict) -> ToolResult:
    blocked = _gate(context)
    if blocked:
        return blocked
    keyword = str(args.get("keyword", "")).strip().lower()
    windows = _enum_windows()
    if keyword:
        windows = [w for w in windows if keyword in w["title"].lower()]
    if not windows:
        return ToolResult(True, "没有匹配的窗口。可能是应用还没启动，或者窗口标题里没有这个关键词。")
    lines = ["共 %d 个窗口：" % len(windows)]
    for item in windows[:25]:
        left, top, right, bottom = item["rect"]
        lines.append(
            "- %s%s｜%d×%d @(%d,%d)｜handle=%d"
            % ("[前台] " if item.get("foreground") else "", item["title"][:60],
               right - left, bottom - top, left, top, item["handle"])
        )
    _log("list_windows", keyword or "(all)")
    return ToolResult(True, "\n".join(lines), data={"windows": windows[:25]})


def _focus_handle(handle: int) -> bool:
    """把窗口切到前台。

    Windows 限制后台进程抢焦点，SetForegroundWindow 常常直接返回 0。可行的做法是
    把自己的线程输入队列临时挂到当前前台窗口的线程上（AttachThreadInput），
    让它认为这次切换是「同一个输入线程发起的」。
    """
    if not IS_WINDOWS or not handle:
        return False
    try:
        if _user32.IsIconic(handle):
            _user32.ShowWindow(handle, 9)  # SW_RESTORE
        foreground = _user32.GetForegroundWindow()
        target_thread = _user32.GetWindowThreadProcessId(handle, None)
        current_thread = _kernel32.GetCurrentThreadId()
        front_thread = _user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        attached = False
        if front_thread and front_thread != current_thread:
            attached = bool(_user32.AttachThreadInput(front_thread, current_thread, True))
        try:
            _user32.BringWindowToTop(handle)
            _user32.SetForegroundWindow(handle)
            _user32.SetActiveWindow(handle)
        finally:
            if attached:
                _user32.AttachThreadInput(front_thread, current_thread, False)
        time.sleep(0.12)
        return int(_user32.GetForegroundWindow()) == int(handle)
    except Exception:
        return False


def _focus_window(args: dict, context: dict) -> ToolResult:
    blocked = _gate(context)
    if blocked:
        return blocked
    title = str(args.get("title", "")).strip()
    if not title:
        return ToolResult(False, "", error="缺少窗口标题关键词")
    windows = _enum_windows()
    matched = [w for w in windows if title.lower() in w["title"].lower()]
    if not matched:
        return ToolResult(
            False, "",
            error="没有标题包含「%s」的窗口。先调用 list_windows 看看现在有哪些窗口，"
            "很可能应用还没启动完。" % title,
        )
    target = matched[0]
    ok = _focus_handle(target["handle"])
    # 换过窗口，键盘的落点已经由用户/这条指令重新确定了，不再当作「刚打开的页面」
    context["desktop_fresh_open"] = False
    _log("focus_window", "%s -> %s" % (title, target["title"][:60]))
    if not ok:
        return ToolResult(
            False, "",
            error="找到了窗口「%s」，但系统拒绝把它切到前台。可以请用户手动点一下该窗口再重试。"
            % target["title"][:40],
        )
    return ToolResult(True, "已切到前台：%s" % target["title"][:60], data={"handle": target["handle"]})


# ---------------------------------------------------------------- 键盘

# 组合键黑名单：这些一按就可能关掉用户正在做的事，或者把系统锁住。
BLOCKED_COMBOS = {
    "alt+f4", "ctrl+shift+esc", "win+r", "win+l", "win+d", "win+x",
    "ctrl+alt+delete", "ctrl+alt+del", "ctrl+shift+delete",
}

VK_CODES = {
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}

KEYEVENTF_KEYUP = 0x0002


def _vk_of(token: str) -> int | None:
    token = token.strip().lower()
    if token in VK_CODES:
        return VK_CODES[token]
    if len(token) == 1:
        char = token.upper()
        if char.isalnum():
            return ord(char)
    return None


def _press_combo(combo: str) -> str:
    """真正按键，单独拆出来便于测试替换。"""
    parts = [p for p in re.split(r"\s*\+\s*", combo.strip().lower()) if p]
    codes = []
    for part in parts:
        code = _vk_of(part)
        if code is None:
            raise ValueError("不认识的键：%s" % part)
        codes.append(code)

    modifiers = [c for c in codes if c in (0x11, 0x12, 0x10, 0x5B)]
    for code in modifiers:
        _user32.keybd_event(code, 0, 0, 0)
        time.sleep(0.01)
    for code in [c for c in codes if c not in modifiers]:
        _user32.keybd_event(code, 0, 0, 0)
        time.sleep(0.02)
        _user32.keybd_event(code, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
    for code in reversed(modifiers):
        _user32.keybd_event(code, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.01)
    return combo


# 这些键的接收者应该是网页本身。焦点落在地址栏时按下去等于白按。
PAGE_KEYS = {"pagedown", "pageup", "home", "end", "space", "up", "down"}

# 地址栏（omnibox）在不同 Chromium 版本里的窗口类名
ADDRESS_BAR_CLASSES = {"edit", "chrome_omniboxview", "chrome_omniboxviewviews"}


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hwndActive", wt.HWND),
        ("hwndFocus", wt.HWND),
        ("hwndCapture", wt.HWND),
        ("hwndMenuOwner", wt.HWND),
        ("hwndMoveSize", wt.HWND),
        ("hwndCaret", wt.HWND),
        ("rcCaret", wt.RECT),
    ]


def _focused_control() -> dict:
    """读当前焦点控件的类名，以及它和所在窗口的矩形。读不到返回空 dict。"""
    if not IS_WINDOWS:
        return {}
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return {}
        thread = _user32.GetWindowThreadProcessId(hwnd, None)
        info = _GUITHREADINFO()
        info.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if not _user32.GetGUIThreadInfo(thread, ctypes.byref(info)):
            return {}
        focus = info.hwndFocus
        if not focus:
            return {}
        buffer = ctypes.create_unicode_buffer(128)
        _user32.GetClassNameW(focus, buffer, 128)
        rect, window = wt.RECT(), wt.RECT()
        if not _user32.GetWindowRect(focus, ctypes.byref(rect)):
            return {}
        if not _user32.GetWindowRect(hwnd, ctypes.byref(window)):
            return {}
        return {
            "class": buffer.value,
            "rect": (rect.left, rect.top, rect.right, rect.bottom),
            "window": (window.left, window.top, window.right, window.bottom),
        }
    except Exception:
        return {}


def _focus_in_address_bar() -> bool:
    """焦点是不是在一个「像地址栏」的编辑框里。

    注意：这条判据对 Chromium 无效——Chrome/Edge 的地址栏不是独立子窗口，而是自己
    画的 Views 控件，GetGUIThreadInfo 拿到的 hwndFocus 始终是主窗口
    （Chrome_WidgetWin_1）。实测按了 ctrl+l、地址栏明明已经选中，这个函数仍返回 False。
    所以真正管用的是 open_url 留下的「刚打开过页面」信号，见 _press_keys。

    这里保留这个判据是为了非 Chromium 的程序（旧版 IE、Win32 浏览器），它的判据是：
    类名像编辑框，且贴在窗口顶部、横跨大半个窗口宽度——地址栏就是这个形状，
    页面里的输入框在窗口中部、宽度也窄。
    """
    info = _focused_control()
    if not info:
        return False
    if info["class"].lower() not in ADDRESS_BAR_CLASSES:
        return False
    win_left, win_top, win_right, win_bottom = info["window"]
    left, top, right, _bottom = info["rect"]
    win_width = win_right - win_left
    if win_width <= 0:
        return False
    near_top = (top - win_top) <= max(80, int((win_bottom - win_top) * 0.12))
    wide = (right - left) >= win_width * 0.3
    return bool(near_top and wide)


def _maybe_leave_address_bar(context: dict, what: str) -> str:
    """刚打开过页面时，焦点还在地址栏：先按 esc 交还给页面，再做下一步。

    实测（16:30）：open_url 之后按 pagedown / alt+left 全部无效——工具返回「已按下」，
    页面一动不动。模型据此以为翻过页了，继续在旧截图上算坐标，越走越远。更糟的是
    type_text：文字会直接粘进地址栏，把刚打开的网址改掉。

    esc 在这一步是安全的：焦点在地址栏时它只是收起地址栏下拉、把焦点还给页面；
    只有在焦点已经在页面上时它才会去中止加载，而那时桌面刚打开过这件事已经被清掉了。
    """
    if not context.get("desktop_fresh_open"):
        return ""
    try:
        _press_combo("escape")
        time.sleep(0.1)
    except ValueError:
        return ""
    context["desktop_fresh_open"] = False
    _log("press_keys", "escape（自动：刚打开页面，焦点在地址栏）")
    return "\n注意：刚打开页面时焦点在地址栏，已先按 esc 把焦点还给页面，再%s。" % what


def _press_keys(args: dict, context: dict) -> ToolResult:
    blocked = _gate(context)
    if blocked:
        return blocked
    combo = str(args.get("keys", "")).strip().lower().replace(" ", "")
    if not combo:
        return ToolResult(False, "", error="缺少要按的键")
    if combo in BLOCKED_COMBOS:
        return ToolResult(False, "", error="组合键 %s 会关闭窗口或锁定系统，已被禁止" % combo)
    if len(combo) > 40 or not re.match(r"^[a-z0-9+\s]+$", combo):
        return ToolResult(False, "", error="只支持形如 ctrl+l、enter、ctrl+shift+esc 的组合键")
    # 先在这里逐个校验键名，再交给执行函数。校验和落键分开，好处是「不认识的键」
    # 根本不会碰到系统：拼错的 ctrl+ftw 不会被当成某个真实按键按下去。
    unknown = [part for part in re.split(r"\s*\+\s*", combo) if _vk_of(part) is None]
    if unknown:
        return ToolResult(False, "", error="不认识的键：%s。可用的有 ctrl/alt/shift/win、"
                                          "enter/tab/esc/space/backspace/delete、方向键、f1–f12、字母数字。"
                          % "、".join(unknown))

    # 页面类按键要发给网页，不是地址栏。刚打开过页面时先按 esc 把焦点交还页面，
    # 否则 pagedown / home / 方向键会被地址栏吃掉，看起来「按了但没反应」。
    note = ""
    parts = [p for p in re.split(r"\s*\+\s*", combo) if p]
    if len(parts) == 1 and parts[0] in PAGE_KEYS:
        note = _maybe_leave_address_bar(context, "按 %s" % combo)

    try:
        _press_combo(combo)
    except ValueError as error:
        return ToolResult(False, "", error=str(error))
    # 按过键之后，焦点归谁已经明确了，后续动作不再需要这个提示
    context["desktop_fresh_open"] = False
    _log("press_keys", combo)
    return ToolResult(True, "已按下：%s%s" % (combo, note))


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _set_clipboard(text: str) -> None:
    """把文本放进剪贴板。

    中文没法用 keybd_event 逐字打——那套 API 只认虚拟键码，没有汉字。
    剪贴板 + Ctrl+V 是唯一稳的做法，也是所有中文输入法的实际路径。
    """
    if not IS_WINDOWS:
        raise RuntimeError("剪贴板只在 Windows 上可用")
    data = text.encode("utf-16-le") + b"\x00\x00"
    # restype 必须显式声明：默认按 int 返回，64 位下指针会被截断成负数
    _kernel32.GlobalAlloc.restype = ctypes.c_void_p
    _kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise RuntimeError("分配剪贴板内存失败")
    pointer = _kernel32.GlobalLock(handle)
    if not pointer:
        _kernel32.GlobalFree(handle)
        raise RuntimeError("锁定剪贴板内存失败")
    try:
        ctypes.memmove(pointer, data, len(data))
    finally:
        _kernel32.GlobalUnlock(handle)
    if not _user32.OpenClipboard(None):
        _kernel32.GlobalFree(handle)
        raise RuntimeError("剪贴板被其它程序占用，请稍后重试")
    try:
        _user32.EmptyClipboard()
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            _kernel32.GlobalFree(handle)
            raise RuntimeError("写入剪贴板失败")
        # 写入成功后所有权归系统，不能再 GlobalFree
    finally:
        _user32.CloseClipboard()


def _paste_text(text: str) -> None:
    """剪贴板 + Ctrl+V。拆出来便于测试替换。"""
    _set_clipboard(text)
    time.sleep(0.05)
    _press_combo("ctrl+v")


def _type_text(args: dict, context: dict) -> ToolResult:
    blocked = _gate(context)
    if blocked:
        return blocked
    text = str(args.get("text", ""))
    if not text:
        return ToolResult(False, "", error="缺少要输入的文字")
    if len(text) > MAX_TYPE_CHARS:
        return ToolResult(
            False, "",
            error="一次最多输入 %d 个字符（这次 %d）。请拆成几次，或者只输入关键词。"
            % (MAX_TYPE_CHARS, len(text)),
        )
    # 刚打开页面就输入，文字会粘进地址栏，把刚打开的网址改掉——这是比「按了没反应」
    # 更隐蔽的错：页面确实变了，但变的是网址。先 esc 把焦点交还页面。
    note = _maybe_leave_address_bar(context, "粘贴")
    try:
        _paste_text(text)
    except RuntimeError as error:
        return ToolResult(False, "", error=str(error))
    context["desktop_fresh_open"] = False
    _log("type_text", text[:80].replace("\n", " "))
    return ToolResult(
        True,
        "已把 %d 个字符粘到当前焦点处：%s%s"
        % (len(text), text[:40].replace("\n", " ") + ("…" if len(text) > 40 else ""), note),
    )


# ---------------------------------------------------------------- 鼠标

MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010


def _click_at(x: int, y: int, button: str = "left", times: int = 1) -> None:
    """真实点击，拆出来便于测试替换。"""
    if not _user32.SetCursorPos(int(x), int(y)):
        raise RuntimeError("移动光标失败（坐标可能超出屏幕）")
    time.sleep(0.08)
    down, up = (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
    if button == "right":
        down, up = (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
    for _ in range(max(1, times)):
        _user32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.03)
        _user32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.08)


def _click_screen(args: dict, context: dict) -> ToolResult:
    blocked = _gate(context)
    if blocked:
        return blocked
    left, top, width, height = _screen_size()
    if width <= 0:
        return ToolResult(False, "", error="读不到屏幕尺寸")
    raw_x, raw_y = args.get("x"), args.get("y")
    if raw_x is None or raw_y is None:
        return ToolResult(False, "", error="缺少 x / y 坐标")
    try:
        x, y = int(raw_x), int(raw_y)
    except (TypeError, ValueError):
        return ToolResult(False, "", error="坐标必须是整数")
    if not (left <= x < left + width and top <= y < top + height):
        return ToolResult(
            False, "",
            error="坐标 (%d,%d) 不在屏幕范围内（当前屏幕 %d×%d）。"
            "坐标要用 screenshot 返回的那张图的像素坐标。" % (x, y, width, height),
        )
    button = str(args.get("button", "left")).lower()
    if button not in ("left", "right"):
        return ToolResult(False, "", error="只支持 left 或 right")
    try:
        times = int(args.get("times", 1) or 1)
    except (TypeError, ValueError):
        times = 1
    times = max(1, min(2, times))
    try:
        _click_at(x, y, button, times)
    except RuntimeError as error:
        return ToolResult(False, "", error=str(error))
    # 点一下，焦点就落到被点的地方了（页面、输入框、别的窗口），不再是地址栏
    context["desktop_fresh_open"] = False
    _log("click_screen", "%s x%d @(%d,%d)" % (button, times, x, y))
    return ToolResult(True, "已在 (%d,%d) %s键点击 %d 次" % (x, y, "左" if button == "left" else "右", times))


# ---------------------------------------------------------------- 截图 / 看屏幕

def _grab_screen(path: str) -> tuple[int, int]:
    """截图存盘，返回像素宽高。"""
    try:
        import mss
        import mss.tools
    except ImportError:
        raise RuntimeError("缺少 mss 库，无法截图")
    with mss.mss() as sct:
        monitor = sct.monitors[0]  # 0 是所有屏幕合并
        shot = sct.grab(monitor)
        mss.tools.to_png(shot.rgb, shot.size, output=path)
        return shot.size.width, shot.size.height


def _encode_for_vision(path: str) -> tuple[str, str]:
    """把截图压成 JPEG 再给视觉模型。

    原图 2560×1600 的 PNG 动辄 3 MB，base64 之后更大；压到 JPEG 80 质量通常在
    300 KB 以内，模型看图的效果几乎没差别，但请求时间和费用差一个量级。
    """
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError("缺少 Pillow，无法压缩截图")
    import io
    with Image.open(path) as image:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=80, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii"), "image/jpeg"


def _parse_vision_json(raw: str) -> dict:
    """从模型回答里抠出 JSON。

    模型偶尔会包一层 ```json 或前后加解释，甚至把同一段 JSON 重复两遍（流式分片
    和最终消息都带正文），所以取第一个能解析的 {...}，解析失败就返回空。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:index + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return {}


def _to_pixels(value, total: int) -> int:
    """0–1000 归一化 → 像素。超出范围按边界夹紧，不抛异常。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return -1
    if number < 0 or number > 1000:
        return -1
    return max(0, min(total - 1, int(round(number / 1000.0 * total))))


def _ask_vision(image_b64: str, mime: str, question: str, width: int, height: int):
    """把截图交给视觉模型，要回「元素名 + 坐标」。

    这里有一个实测出来的关键点：**Qwen-VL 返回的坐标不是像素，是 0–1000 的归一化值**。
    早先让它「按原始像素给坐标」，它给的是一组长得像像素、实际是归一化值的数字
    （真值 [700,400] 它答 [583,570]），直接拿去点会偏掉半屏。所以：
    ① 明确要求它用 0–1000 归一化坐标并输出 JSON；
    ② 换算成像素这一步放在代码里做，不让模型算。
    实测这样换算回来的坐标误差在几个像素内。

    返回 (给人看的文本, 元素列表)。元素坐标已经是截图像素，可直接给 click_screen。
    """
    import llm

    prompt = (
        "这是一张 Windows 桌面截图，尺寸 %d×%d 像素。\n"
        "用户的问题：%s\n\n"
        "只输出 JSON，不要任何解释、不要代码块标记，格式：\n"
        '{"window":"当前前台窗口或页面标题","elements":[{"name":"元素名","box":[x1,y1,x2,y2]}],'
        '"note":"看不懂就写原因"}\n'
        "坐标一律使用 **0-1000 的归一化坐标**（x 相对图片宽度、y 相对图片高度），"
        "不要输出像素值。最多给 8 个与问题相关的元素。\n"
        "看不清、找不到就返回空 elements 并在 note 里说明，**不要猜坐标**。"
        % (width, height, question or "描述这个屏幕上有什么")
    )
    content = [
        {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, image_b64)}},
        {"type": "text", "text": prompt},
    ]
    pieces: list[str] = []
    final = ""
    for kind, payload in llm.chat_completion_stream(
        [{"role": "user", "content": content}],
        tools=None,
        timeout=45.0,
        require_vision=True,
    ):
        if kind == "token":
            pieces.append(payload)
        elif kind == "message":
            final = payload.get("content") or ""
    # 流式分片和最终消息是同一段文字的两种表示，拼在一起会得到两份 JSON
    raw = (final or "".join(pieces)).strip()

    data = _parse_vision_json(raw)
    elements: list[dict] = []
    for item in (data.get("elements") or [])[:8]:
        if not isinstance(item, dict):
            continue
        box = item.get("box") or []
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        x1, y1 = _to_pixels(box[0], width), _to_pixels(box[1], height)
        x2, y2 = _to_pixels(box[2], width), _to_pixels(box[3], height)
        if min(x1, y1, x2, y2) < 0 or x2 <= x1 or y2 <= y1:
            continue
        elements.append({
            "name": str(item.get("name") or "未命名")[:40],
            "box": [x1, y1, x2, y2],
            "center": [(x1 + x2) // 2, (y1 + y2) // 2],
        })

    if not data:
        return ("视觉模型没有按约定返回 JSON，原文如下（坐标不可靠，不要直接拿去点）：\n" + raw[:500]), []

    lines = []
    window = str(data.get("window") or "").strip()
    if window:
        lines.append("前台窗口：%s" % window[:80])
    if elements:
        lines.append("识别到的元素（坐标已换算成截图像素，可直接给 click_screen）：")
        for item in elements:
            lines.append("  · %s → 点 (%d, %d)，范围 [%d,%d,%d,%d]"
                         % (item["name"], item["center"][0], item["center"][1], *item["box"]))
    note = str(data.get("note") or "").strip()
    if note:
        lines.append("备注：%s" % note[:200])
    if not elements and not note:
        lines.append("没有识别出可用元素。")
    return "\n".join(lines), elements


def _screenshot(args: dict, context: dict) -> ToolResult:
    blocked = _gate(context)
    if blocked:
        return blocked
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
        name = "shot-%s.png" % time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(SHOT_DIR, name)
        width, height = _grab_screen(path)
    except RuntimeError as error:
        return ToolResult(False, "", error=str(error))
    _log("screenshot", "%s %dx%d" % (path, width, height))

    question = str(args.get("question", "")).strip()
    header = "已截图 %d×%d，保存在 %s（坐标原点左上角，可直接作为 click_screen 的 x/y）。" % (
        width, height, path,
    )
    if not question:
        return ToolResult(True, header + "\n没有提问题，所以没有让视觉模型解读。要让模型看，传 question。",
                          data={"path": path, "width": width, "height": height})

    try:
        image_b64, mime = _encode_for_vision(path)
        answer, elements = _ask_vision(image_b64, mime, question, width, height)
    except Exception as error:  # 视觉链路失败不该影响「截图已成功」这件事
        return ToolResult(
            True,
            header + "\n但视觉模型解读失败：%s\n可以直接把截图路径告诉用户，或改用键盘操作（press_keys / type_text）。"
            % error,
            data={"path": path, "width": width, "height": height},
        )
    return ToolResult(
        True,
        header + "\n" + (answer or "（视觉模型给了空回答）"),
        data={"path": path, "width": width, "height": height,
              "elements": elements, "vision": answer},
    )


# ---------------------------------------------------------------- 注册

register(Tool(
    name="open_url",
    description=(
        "用浏览器打开一个网址。**凡是「打开某个网站 / 去某站搜某内容」的需求，优先用这个**，"
        "它比模拟鼠标可靠得多，也不会点偏。"
        "搜索类需求直接拼结果页地址，不要先开首页再去点搜索框，例如："
        "百度 https://www.baidu.com/s?wd=<关键词> 、必应 https://www.bing.com/search?q=<关键词>、"
        "知乎 https://www.zhihu.com/search?type=content&q=<关键词>；关键词要做 URL 编码，中文直接用也可以。"
        "只支持 http/https。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "完整网址，没写协议会自动补 https"},
            "browser": {
                "type": "string",
                "enum": ["", "chrome", "edge"],
                "description": "用哪个浏览器打开，留空用系统默认",
            },
        },
        "required": ["url"],
    },
    handler=_open_url,
))

register(Tool(
    name="open_app",
    description=(
        "启动一个本机应用。只支持白名单：chrome、edge、notepad（记事本）、calc（计算器）、"
        "explorer（资源管理器）、mspaint（画图），以及项目目录内已存在的文件。"
        "不能启动任意 exe/bat/ps1——那等于绕过命令黑名单。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "app": {"type": "string", "description": "应用名（chrome/notepad/calc…）或项目内的相对路径"},
        },
        "required": ["app"],
    },
    handler=_open_app,
))

register(Tool(
    name="list_windows",
    description=(
        "列出当前所有可见窗口（标题、位置、尺寸、是否前台）。"
        "要操作一个已经开着的程序之前，先调这个确认它在不在、标题是什么，不要凭猜。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "只看标题包含这个词的窗口，可留空"},
        },
        "required": [],
    },
    handler=_list_windows,
))

register(Tool(
    name="focus_window",
    description=(
        "把标题包含指定关键词的窗口切到前台。之后才能对它按键或输入——"
        "键盘事件只会发给当前焦点窗口，切不过去就不要盲打。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "窗口标题里的关键词，例如「百度」「Chrome」"},
        },
        "required": ["title"],
    },
    handler=_focus_window,
))

register(Tool(
    name="press_keys",
    description=(
        "按一个组合键，例如 ctrl+l（定位地址栏）、enter、tab、esc、ctrl+t（新标签页）、ctrl+w（关标签）。"
        "一次只按一个组合键，形如 ctrl+shift+t。Alt+F4、Win+R、Win+L 等会关窗口或锁屏的组合被禁止。"
        "刚用 open_url 打开过页面时按翻页键（pagedown/pageup/home/end/方向键），"
        "会自动先按一次 esc 把焦点从地址栏还给页面——不这么做这些键会被地址栏吃掉，按了等于没按。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "keys": {"type": "string", "description": "组合键，小写，用 + 连接，例如 ctrl+l"},
        },
        "required": ["keys"],
    },
    handler=_press_keys,
))

register(Tool(
    name="type_text",
    description=(
        "往**当前焦点**处输入文字（通过剪贴板粘贴，中文和标点都能输）。"
        "输入前必须先用 focus_window 确保目标窗口在前台、光标已经在输入框里；"
        "否则文字会粘到别的地方。每次最多 2000 字。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要输入的文字"},
        },
        "required": ["text"],
    },
    handler=_type_text,
))

register(Tool(
    name="click_screen",
    description=(
        "在屏幕的绝对像素坐标上点鼠标。x/y 必须来自 screenshot 返回的截图坐标系"
        "（原点左上角，与屏幕像素 1:1），不要自己估算。点之前先 screenshot 看一眼，看再点。"
        "点错了没法撤销，所以一次只点一下，点完再截图确认结果。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "横坐标（像素）"},
            "y": {"type": "integer", "description": "纵坐标（像素）"},
            "button": {"type": "string", "enum": ["left", "right"], "description": "左键或右键，默认左键"},
            "times": {"type": "integer", "description": "点击次数 1 或 2，默认 1（双击用 2）"},
        },
        "required": ["x", "y"],
    },
    handler=_click_screen,
))

register(Tool(
    name="screenshot",
    description=(
        "截当前整个屏幕。带 question 时会把图交给视觉模型，返回「当前是什么界面」以及相关元素的"
        "**像素坐标**（模型给的是 0-1000 归一化值，已由代码换算成像素，可直接给 click_screen 用）。"
        "不确定界面上有什么、不知道点哪里、或者点完要确认结果时，都应该先截图看一眼。"
        "视觉模型找不到目标时会返回空列表——那种情况不要硬猜坐标，改用键盘或问用户。"
        "不带 question 只存图，不解读。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "要问视觉模型的问题，例如「搜索框在哪」「现在页面加载出来了吗」。留空只截图。",
            },
        },
        "required": [],
    },
    handler=_screenshot,
))
