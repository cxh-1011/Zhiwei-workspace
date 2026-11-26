"""工作区路径沙箱：读、写、执行三类工具共用的唯一一道边界。

为什么要单独一个模块：只读工具、写入工具和命令执行工具必须用同一套判断，
否则很容易出现「读被拦了、写没拦」这种漏洞。以后新增工具只 import 这里，
不要自己再写一份 realpath 校验。

五类规则：
1. 路径：只接受「相对工作区」的路径，绝对路径与盘符路径一律拒绝；
   解析后（realpath，含符号链接）必须落在**当前活动根**之内。
2. 根：活动根不是常量。代码仓库是默认根，学习沙箱创建的每个项目目录
   都是运行时登记进来的根（`register_root`）。切换只能用 `use_root`，
   而且传进去的目录必须已经登记过——否则等于把「切根」变成「任意目录可读写」。
3. 红线：配置、密钥、数据库、依赖、备份、上传数据、其它项目一律不可访问，
   并且要说清命中的是哪一类，让模型知道「换个路径再试」没有意义。
4. 二进制：含 NUL 或解码失败的文件不按文本处理，避免把乱码塞进上下文。
5. 体积：单个文件读取、单次写入都有硬上限，超限提示改用分片或局部修改。

为什么要 contextvar 而不是给每个函数加 root 参数：`resolve()` 有十几处调用点，
漏改一处的后果是**静默回落到仓库根**——学生项目里写文件写到了仓库里，
而且不报错。活动根只在一个地方设置、所有调用点自动跟随，才是这类 bug 的正解。
"""

import contextvars
import os
import re

_DEFAULT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)

# 基础根：代码仓库本身。允许用 AGENT_WORKSPACE 指向别的目录（例如专门的代码工作区）。
# 这个变量仍然可以被测试直接改（现有测试都这么干），但它现在只决定「没有活动根时用哪个」，
# 以及「备份往哪写」，不再直接参与每次解析。
WORKSPACE_ROOT = os.path.realpath(os.getenv("AGENT_WORKSPACE") or _DEFAULT_ROOT)

# 运行时登记过的额外根（学习沙箱的项目目录）。只有登记过的目录才能被切换成活动根。
_EXTRA_ROOTS: dict[str, str] = {}

# 当前活动根。None 表示用 WORKSPACE_ROOT。
_ACTIVE_ROOT: contextvars.ContextVar = contextvars.ContextVar("ai_active_root", default=None)


def base_root() -> str:
    """仓库根。备份目录固定写在这里：项目目录里出现 `backups/` 会污染学生项目。"""
    return WORKSPACE_ROOT


def active_root() -> str:
    """当前解析用的根。默认是仓库，沙箱里是那个项目目录。"""
    current = _ACTIVE_ROOT.get()
    return os.path.realpath(current) if current else WORKSPACE_ROOT


def register_root(path: str) -> str:
    """登记一个可以当活动根使用的目录，返回规范化后的绝对路径。

    登记不是「信任」：目录仍然受红线清单约束（比如项目里不能叫 .git）。
    它的作用是让 `use_root` 只认应用自己创建/打开过的项目，
    而不是「模型说切到 C:/ 就切到 C:/」。
    """
    real = os.path.realpath(str(path or ""))
    if not os.path.isdir(real):
        raise ValueError("要登记的目录不存在：%s" % real)
    _EXTRA_ROOTS[os.path.normcase(real)] = real
    return real


def unregister_root(path: str) -> None:
    _EXTRA_ROOTS.pop(os.path.normcase(os.path.realpath(str(path or ""))), None)


def registered_roots() -> list[str]:
    return sorted(_EXTRA_ROOTS.values())


def is_registered_root(path: str) -> bool:
    real = os.path.realpath(str(path or ""))
    if os.path.normcase(real) == os.path.normcase(WORKSPACE_ROOT):
        return True
    return os.path.normcase(real) in _EXTRA_ROOTS


def use_root(path: str):
    """把活动根切成 `path`，返回 token，用于 finally 里 reset。

    未登记的目录直接拒绝。这是「切根」唯一的入口，别在别处直接 set contextvar。
    """
    real = os.path.realpath(str(path or ""))
    if not is_registered_root(real):
        raise ValueError("拒绝切换到未登记的目录：%s" % real)
    return _ACTIVE_ROOT.set(real)


def reset_root(token) -> None:
    try:
        _ACTIVE_ROOT.reset(token)
    except (ValueError, LookupError):
        # token 属于别的 context（比如跨线程用了）：退化成清空，不要让它把整轮请求炸掉
        _ACTIVE_ROOT.set(None)

# ---------------------------------------------------------------- 红线清单
# 三类红线分开写，是为了报错时能说清「为什么不行」，而不是一句笼统的「不可访问」。

# 目录名命中即整棵子树不可见（读、写、执行都不可）
BLOCKED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
    ".deliverables", "backups", "uploads", "logs", ".idea", ".vscode",
    ".ssh", ".aws", ".gnupg", ".workbuddy",
    # 学习沙箱的项目目录：从仓库根看过去必须不可见，否则「打开 A 项目」时
    # 能顺手读到 B 项目（以及别人的答案）。切进项目后相对路径里没有这一层，
    # 所以不影响项目内部的正常读写。
    "projects",
}

_DIR_REASONS = {
    ".git": "版本库内部数据",
    ".venv": "Python 虚拟环境",
    "venv": "Python 虚拟环境",
    "env": "Python 虚拟环境",
    "__pycache__": "Python 字节码缓存",
    "node_modules": "Node 依赖目录",
    ".deliverables": "生成产物目录",
    "backups": "自动备份目录",
    "uploads": "用户上传文件",
    "logs": "运行日志目录",
    ".idea": "IDE 配置",
    ".vscode": "IDE 配置",
    ".ssh": "SSH 密钥目录",
    ".aws": "云服务凭证目录",
    ".gnupg": "GPG 密钥目录",
    ".workbuddy": "助手运行数据",
    "projects": "项目沙箱目录（需在某个项目内访问）",
}

# 精确文件名命中即不可访问：私钥与凭证
BLOCKED_NAMES = {
    ".git-credentials", ".npmrc", ".pypirc", ".netrc", ".htpasswd",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "credentials", "credentials.json", "secrets.json", "secrets.yml",
}

# 文件名前缀命中即不可访问（.env / .env.local / .env.example 都挡掉，宁严不漏）
BLOCKED_PREFIXES = (".env",)

# 后缀命中即不可访问：数据库、密钥、证书
BLOCKED_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".db3", ".sqlitedb",
    ".key", ".pem", ".pfx", ".p12", ".keystore", ".jks", ".kdbx",
}

# SQLite 运行时会生成 -wal / -shm / -journal 附属文件，后缀不是 .db，
# 必须先剥掉再判断，否则这类文件会漏过去。
_SQLITE_TAILS = ("-wal", "-shm", "-journal")

# ---------------------------------------------------------------- 体积与类型上限

# 单文件「读取」上限：超过则拒绝，提示用 start_line / max_lines 分片。
# 与写入上限分开，因为读一个 2 MB 的日志是合理的，写 2 MB 才危险。
MAX_FILE_BYTES = 2 * 1024 * 1024

# 扫描类工具（grep / repo_map）的单文件上限：超了直接跳过，避免一个日志拖垮整个搜索。
MAX_SCAN_BYTES = 512 * 1024

MAX_LINE_CHARS = 400

# 写入类工具的单次写入上限（单文件）
MAX_WRITE_BYTES = 200 * 1024

# 二进制探针只读前 8 KB：足够命中 NUL 字节，又不会为了判断类型读整个大文件。
PROBE_BYTES = 8192

# 二进制/无需读取的后缀，检索与地图一律跳过
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svgz",
    ".pdf", ".zip", ".gz", ".tar", ".tgz", ".xz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".o", ".obj", ".lib",
    ".class", ".jar", ".pyc", ".faiss", ".pkl",
    ".woff", ".woff2", ".ttf", ".otf",
    ".mp4", ".mov", ".avi", ".webm", ".mp3", ".wav",
    ".xlsx", ".docx", ".pptx",
}

# 已知的文本后缀：命中的文件跳过二进制探针，省掉一次 open + 8 KB 读。
TEXT_SUFFIXES = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue",
    ".json", ".jsonc", ".md", ".markdown", ".txt", ".rst", ".log",
    ".html", ".htm", ".css", ".scss", ".less", ".sass",
    ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".properties",
    ".sh", ".bash", ".bat", ".ps1", ".psm1", ".sql", ".xml", ".csv", ".tsv",
    ".java", ".kt", ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs",
    ".rb", ".php", ".swift", ".m", ".lua", ".r", ".jl", ".pl", ".gradle",
    ".gitignore", ".gitattributes", ".editorconfig", ".dockerignore",
}


def _suffix(name: str) -> str:
    """取后缀，但先剥掉 SQLite 的附属尾巴：chats.db-wal → .db。"""
    low = name.lower()
    for tail in _SQLITE_TAILS:
        if low.endswith(tail):
            low = low[: -len(tail)]
            break
    return os.path.splitext(low)[1]


def block_reason(rel_path: str) -> str:
    """返回命中的红线说明（中文）；不在红线内返回空字符串。

    单独成一个函数，是因为工具报错需要「哪一类红线」，而不是笼统的不可访问。
    """
    parts = [p for p in rel_path.replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        return ""

    # 目录链上任一层命中，整棵子树都不可见
    for part in parts[:-1]:
        low = part.lower()
        if low in BLOCKED_DIRS:
            return _DIR_REASONS.get(low, "依赖或缓存目录")

    name = parts[-1]
    low = name.lower()
    if low in BLOCKED_DIRS:
        return _DIR_REASONS.get(low, "依赖或缓存目录")
    if low in BLOCKED_NAMES:
        return "密钥或凭证文件"
    if low.startswith(BLOCKED_PREFIXES):
        return "环境变量配置（通常含 API Key）"
    if _suffix(low) in BLOCKED_SUFFIXES:
        return "数据库或密钥文件"
    return ""


def is_blocked(rel_path: str) -> bool:
    """判断相对路径是否命中红线。"""
    return bool(block_reason(rel_path))


def rel_inside(path: str) -> str:
    """把绝对路径转成相对**活动根**的路径；越界直接抛 ValueError。

    边界判断用 normcase（Windows 下大小写不敏感），但返回的路径保留原始大小写，
    否则显示出来会变成 readme.md 这种小写形式。
    """
    root = active_root()
    target = os.path.realpath(path)
    root_key = os.path.normcase(root)
    target_key = os.path.normcase(target)
    if target_key != root_key and not target_key.startswith(root_key + os.sep):
        # 报错里带上当前根：沙箱里读仓库文件时，模型需要知道「你现在在项目里」，
        # 否则它会一直换路径重试。
        raise ValueError("路径越界：只能访问当前工作区（%s）内的文件" % display(os.path.basename(root) or root))
    return os.path.relpath(target, root)


def resolve(rel: str, write: bool = False) -> str:
    """把模型给的相对路径解析成安全的绝对路径。

    write=True 时额外拒绝目录目标，并保证父目录存在（写入不创造中间目录，
    免得模型一次手滑就在奇怪的位置建出一堆文件夹）。
    """
    rel = str(rel or ".").strip().replace("\\", "/")
    if rel in ("", "."):
        rel = "."
    if os.path.isabs(rel) or re.match(r"^[A-Za-z]:", rel):
        raise ValueError("请使用相对工作区的路径，不要用绝对路径")

    root = active_root()
    resolved = os.path.realpath(os.path.join(root, rel))
    inside = rel_inside(resolved)
    reason = block_reason(inside)
    if reason:
        # 明确告诉模型「换路径没用」，否则它会绕圈试探 .env.bak、config/.env 之类。
        raise ValueError(
            "不可访问：%s 属于「%s」范围。配置、密钥、数据库、依赖、备份与上传数据一律拒绝，"
            "换个路径或改大小写也不会通过；确需其中的信息请让用户直接告诉你。"
            % (display(inside), reason)
        )

    if write:
        if os.path.isdir(resolved):
            raise ValueError("目标是一个目录，不能写入：%s" % rel)
        parent = os.path.dirname(resolved) or root
        if not os.path.isdir(parent):
            raise ValueError("上级目录不存在：%s" % rel)
    return resolved


# ---------------------------------------------------------------- 二进制判定

def needs_probe(path: str) -> bool:
    """未知后缀才值得嗅探；已知文本后缀直接放行，省一次 IO。"""
    return os.path.splitext(path)[1].lower() not in TEXT_SUFFIXES


def looks_binary(path: str) -> bool:
    """判断文件是否应视为二进制（只读前 8 KB）。

    判据用 NUL 字节 + UTF-8 解码失败比例。截断可能切在多字节字符中间，
    所以先用「去掉尾部 4 字节能否解码」排除这种误判。
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(PROBE_BYTES)
    except OSError:
        return False
    if not head:
        return False
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")
        return False
    except UnicodeDecodeError:
        pass
    try:
        head[:-4].decode("utf-8")
        return False          # 只是探针截断，不是二进制
    except UnicodeDecodeError:
        text = head.decode("utf-8", "replace")
        bad = text.count("\ufffd")
        return bad > max(2, len(text) * 0.05)


def reject_non_text(path: str) -> str:
    """返回拒绝理由；可当文本处理时返回空字符串。供读、写两类工具共用。"""
    if os.path.splitext(path)[1].lower() in SKIP_SUFFIXES:
        return "这是二进制或打包文件（%s），不能按文本处理" % os.path.splitext(path)[1].lower()
    if looks_binary(path):
        return "内容看起来是二进制（含 NUL 字节或非 UTF-8 编码），不能按文本处理"
    return ""


def human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return "%.1f MB" % (size / 1024.0 / 1024.0)
    if size >= 1024:
        return "%d KB" % ((size + 1023) // 1024)
    return "%d B" % size


def display(rel: str) -> str:
    """把 Windows 反斜杠统一成正斜杠，便于在回答里引用。"""
    return rel.replace("\\", "/")
