"""自测回路：改过代码，就得有一条跑成功的命令证明它没坏。

为什么不放在工具层：`write_file` 只知道自己这一次写入，「改了什么」和
「后来验证过没有」是**跨调用**的状态，只有 agent 的循环看得见。所以这里
只做一个纯状态机，由 agent 在三个位置喂事件——编辑成功、命令结束、准备收尾——
然后根据状态决定「要不要把模型拦下来逼它先去验证」。

判定分三层，缺一层就会误判：

1. **改的是不是代码**。改 README 不需要跑测试，改了 `.py` 才需要。按后缀分，
   `.md/.txt/.html/.css` 不算：这类改动物理上没法客观验证，逼模型跑命令只会
   白白消耗一轮。
2. **命令算不算验证**。`ls`、`cat`、`git status` 退出码也是 0，但什么都没证明。
   所以按首 token 与 git 子命令排除纯浏览类——这是唯一容易误判的地方，
   宁可宽一点：跑过一次脚本、测试、编译、lint 都算，只排除明确只看不做的。
3. **验证是否发生在改动之后**。先跑测试再改代码等于没验证。用单调序号比较
   先后，比时间戳可靠（同一毫秒内也能分清顺序）。

最后「只拦一次」：模型可能因为用户拒绝审批、项目确实没测试而无法验证，
这时再拦就是死循环。拦一次是提醒，拦两次是不讲理。
"""

import os

# 改动这些后缀才要求验证。刻意不含 .md/.html/.css：
# 没有构建产物的纯前端/文档改动，想验证也没有客观手段。
CODE_SUFFIXES = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue",
    ".go", ".rs", ".java", ".kt", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs",
    ".rb", ".php", ".swift", ".m", ".lua", ".sh", ".bash", ".ps1", ".sql",
    ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".gradle",
}

# 纯浏览命令：退出码 0 不代表任何东西。`find` 也在这里——它只会列出路径。
BROWSE_EXES = {
    "ls", "dir", "pwd", "cd", "cat", "type", "head", "tail", "wc",
    "where", "which", "echo", "tree", "stat", "file", "du", "df", "more", "less",
}

# git 的只读子命令：看状态不算验证。清单比 command.py 的 GIT_READ 略窄，
# 因为那边是「能不能直接跑」，这边是「跑了有没有证明力」，判据不同。
BROWSE_GIT = {
    "status", "diff", "log", "show", "branch", "blame", "shortlog",
    "describe", "reflog", "cat-file", "show-ref", "ls-files", "rev-parse",
}

# 展示时最多列几个文件名，避免提示词被一长串路径撑爆
MAX_FILES_IN_PROMPT = 4


def _exe(command: str) -> str:
    """取命令的可执行名：去路径、去 .exe。切分失败返回空串。"""
    parts = str(command or "").strip().split()
    if not parts:
        return ""
    name = os.path.basename(parts[0].replace("\\", "/")).lower()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def is_verification(command: str, exit_code: int, timed_out: bool = False) -> bool:
    """这条命令的成功能不能当作「验证过」。超时与非零一律不算。"""
    if timed_out or exit_code != 0:
        return False
    parts = str(command or "").strip().split()
    if not parts:
        return False
    if _exe(command) in BROWSE_EXES:
        return False
    if _exe(command) == "git" and len(parts) > 1 and parts[1].lower() in BROWSE_GIT:
        return False
    return True


class SelfCheck:
    """一次运行的自测状态。生命周期与 run_agent_stream 相同。"""

    __slots__ = ("_seq", "_edited_at", "_verified_at", "code_edits",
                 "commands", "interrupted")

    def __init__(self) -> None:
        self._seq = 0
        self._edited_at = None
        self._verified_at = None
        # 只记代码类文件；文档改动进来了也只会被 needs_check 过滤掉
        self.code_edits: dict[str, int] = {}
        self.commands: list[dict] = []
        self.interrupted = False

    # ------------------------------------------------------------ 事件输入

    def note_edit(self, path: str) -> None:
        """一次写入落盘成功。非代码文件也记，但不算进 code_edits。"""
        if not path:
            return
        rel = str(path).replace("\\", "/")
        suffix = os.path.splitext(rel)[1].lower()
        if suffix not in CODE_SUFFIXES:
            return
        self._seq += 1
        self._edited_at = self._seq
        self.code_edits[rel] = self._seq

    def note_command(self, command: str, exit_code: int, timed_out: bool = False) -> None:
        """一条命令跑完。成功的验证命令会把 _verified_at 推到最新序号。"""
        ok = is_verification(command, exit_code, timed_out)
        self._seq += 1
        self.commands.append({
            "command": str(command or ""),
            "exit_code": exit_code,
            "timed_out": bool(timed_out),
            "verification": ok,
        })
        if ok:
            self._verified_at = self._seq

    # ------------------------------------------------------------ 状态查询

    @property
    def verified(self) -> bool:
        """最后一次改动之后，有没有一条成功的验证命令。

        先跑测试再改代码不算数，所以比的是序号而不是「跑过没有」。
        """
        if self._verified_at is None or self._edited_at is None:
            return False
        return self._verified_at > self._edited_at

    @property
    def needs_check(self) -> bool:
        """改了代码但没验证。文档类改动会让 code_edits 为空，这里自然为 False。"""
        return bool(self.code_edits) and not self.verified

    def should_interrupt(self, allow_command: bool) -> bool:
        """是否该把「准备收尾」的模型拦下来。

        没开命令执行就绝对不拦——它根本没有验证手段，拦下来只能原地打转。
        """
        return bool(allow_command) and self.needs_check and not self.interrupted

    # ------------------------------------------------------------ 输出

    def _file_brief(self) -> str:
        files = sorted(self.code_edits)
        shown = "、".join(files[:MAX_FILES_IN_PROMPT])
        if len(files) > MAX_FILES_IN_PROMPT:
            shown += " 等 %d 个文件" % len(files)
        return shown or "（无）"

    def reminder(self) -> str:
        """拼在每轮系统提示末尾的催促。语气要短、要具体，否则模型直接忽略。"""
        return (
            "\n\n【自测提醒】本次运行你改动了 %s，但之后没有任何一条命令跑成功。"
            "收尾前必须先验证：有测试就跑对应测试文件（python -m pytest <文件> -q），"
            "没有测试就跑 python -m compileall <文件>。"
            "这两条都不需要用户确认，能直接跑通；python -c 需要用户点确认，"
            "人不在时会一直等到超时，不要用它做验证。"
            "失败就按输出修，修完再跑。跑通之后再给最终回答。"
            "确实没有可跑的验证方式时，如实说明「无法自动验证」。"
            % self._file_brief()
        )

    def interrupt_prompt(self) -> str:
        """拦下模型时追加的那条 user 消息。比 reminder 更强硬，因为它已经想收尾了。"""
        return (
            "（系统检查）你刚才准备直接结束，但本次运行改动了 %s，"
            "并且没有一条命令成功跑过。现在不能给最终回答，也不能只说「已修改」。\n"
            "请立刻调用 run_command 跑一条最小验证：\n"
            "1. 项目里有测试 → python -m pytest <对应测试文件> -q\n"
            "2. 没有测试 → python -m compileall <改动的文件>\n"
            "这两条都不需要用户确认。**不要用 python -c**：它需要用户点确认，"
            "人不在电脑前会卡到超时，白白浪费两分钟。\n"
            "3. 命令失败 → 读输出定位，修好再跑一次\n"
            "验证通过后再给出完整回答，并说明跑的是哪条命令、结果如何。"
            "如果确实没有任何可执行的验证方式，直接说明原因，我会把这次改动标注为「未验证」。"
            % self._file_brief()
        )

    def summary(self) -> dict:
        """随 done 事件下发，前端据此决定要不要在回答下面挂一条「未验证」警示。"""
        ran = [item for item in self.commands if item["verification"]]
        return {
            "edited": sorted(self.code_edits),
            "pending": self.needs_check,
            "verified": self.verified,
            "interrupted": self.interrupted,
            # 只留最近三条，前端拿来做一句话说明，不展示完整命令历史
            "proof": [item["command"] for item in ran[-3:]],
        }
