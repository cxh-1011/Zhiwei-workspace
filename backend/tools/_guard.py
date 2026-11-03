"""工作区路径沙箱：读、写、执行三类工具共用的唯一一道边界。

为什么要单独一个模块：只读工具、写入工具和命令执行工具必须用同一套判断，
否则很容易出现「读被拦了、写没拦」这种漏洞。以后新增工具只 import 这里，
不要自己再写一份 realpath 校验。

三条规则：
1. 只接受「相对工作区」的路径，绝对路径与盘符路径一律拒绝。
2. 解析后（realpath，含符号链接）必须落在 AGENT_WORKSPACE 之内。
3. 命中敏感黑名单的一律不可访问：配置、数据库、密钥、依赖、上传数据、备份。
"""

import os
import re

_DEFAULT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)

# 允许用 AGENT_WORKSPACE 指向别的目录（例如专门的代码工作区）。
WORKSPACE_ROOT = os.path.realpath(os.getenv("AGENT_WORKSPACE") or _DEFAULT_ROOT)

# 目录名命中即整棵子树不可见（读、写都不可）
BLOCKED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
    ".deliverables", "backups", "uploads", "logs", ".idea", ".vscode",
}

# 文件名前缀命中即不可访问（.env / .env.example 都挡掉，宁严不漏）
BLOCKED_PREFIXES = (".env",)

# 后缀命中即不可访问：数据库、密钥、证书
BLOCKED_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".key", ".pem", ".pfx", ".p12", ".keystore",
}

# 单文件读取上限与单行展示上限
MAX_FILE_BYTES = 512 * 1024
MAX_LINE_CHARS = 400

# 预留：写入类工具的单次写入上限（下一步的 write_file / edit_file 用）
MAX_WRITE_BYTES = 200 * 1024

# 二进制/无需读取的后缀，检索与地图一律跳过
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz",
    ".exe", ".dll", ".pyc", ".faiss", ".pkl", ".woff", ".woff2", ".ttf",
    ".mp4", ".mp3", ".wav", ".xlsx", ".docx", ".pptx",
}


def rel_inside(path: str) -> str:
    """把绝对路径转成相对工作区的路径；越界直接抛 ValueError。

    边界判断用 normcase（Windows 下大小写不敏感），但返回的路径保留原始大小写，
    否则显示出来会变成 readme.md 这种小写形式。
    """
    root = WORKSPACE_ROOT
    target = os.path.realpath(path)
    root_key = os.path.normcase(root)
    target_key = os.path.normcase(target)
    if target_key != root_key and not target_key.startswith(root_key + os.sep):
        raise ValueError("路径越界：只能访问工作区内的文件")
    return os.path.relpath(target, root)


def is_blocked(rel_path: str) -> bool:
    """判断相对路径是否命中黑名单（目录、前缀、后缀三类）。"""
    parts = [p for p in rel_path.replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        return False
    for part in parts[:-1]:
        if part.lower() in BLOCKED_DIRS:
            return True
    name = parts[-1]
    low = name.lower()
    if low in BLOCKED_DIRS:
        return True
    if low.startswith(BLOCKED_PREFIXES):
        return True
    return os.path.splitext(low)[1] in BLOCKED_SUFFIXES


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

    resolved = os.path.realpath(os.path.join(WORKSPACE_ROOT, rel))
    inside = rel_inside(resolved)
    if is_blocked(inside):
        raise ValueError("该路径属于不可访问范围（配置/数据/依赖目录）")

    if write:
        if os.path.isdir(resolved):
            raise ValueError("目标是一个目录，不能写入：%s" % rel)
        parent = os.path.dirname(resolved) or WORKSPACE_ROOT
        if not os.path.isdir(parent):
            raise ValueError("上级目录不存在：%s" % rel)
    return resolved


def display(rel: str) -> str:
    """把 Windows 反斜杠统一成正斜杠，便于在回答里引用。"""
    return rel.replace("\\", "/")
