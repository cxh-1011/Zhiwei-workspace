"""第 9 章验收：一份「该不该上多 Agent」的选型意见。

这一章没有代码产物——三道题分别是找反例、画草图、写选型建议。
验收就看 `architecture_note.md` 有没有把结论和理由写清楚。

为什么要求「明确二选一」
------------------------
选型意见最常见的废稿是「视情况而定，各有优劣」。这种话没有决策价值。
正文第 3 题要求「明确写了单 Agent 或多 Agent」，验收就卡这一条。

为什么要求「指到具体理由」
--------------------------
理由必须落在正文给的三条之一（职责不可合并 / 能力隔离 / 失败可局部化），
或者第 1 题那三条反例（不需要等 / 一次读完 / 顺序执行就够了）。
写「因为多 Agent 更先进」不算。
"""

import re
import sys

from _kit import finish, item, read_text

CHOICES = ("单Agent", "单 Agent", "单agent", "多Agent", "多 Agent", "多agent")
REASONS = ("职责不可合并", "能力隔离", "失败可局部化", "不需要等",
           "一次读完", "顺序执行")

# 正文要求建议本身 ≤200 字。加上标题、表格行，整份文件给到 260 个字符的余量。
MAX_CHARS = 260


def main():
    project = sys.argv[1] if len(sys.argv) > 1 else "."
    items = []

    text, error = read_text(
        project, "architecture_note.md",
        "这一章不写代码：把第 1 题的反例和第 3 题的选型建议写进这个文件。")
    if text is None:
        item(items, "architecture_note.md 存在", False, error)
        finish(items)
        return

    body = re.sub(r"\s", "", text)
    item(items, "architecture_note.md 不是空的", bool(body), "文件里没有实际内容。")

    hit = [word for word in CHOICES if word in text]
    item(items, "明确选了单 Agent 还是多 Agent", bool(hit),
         "没看到「单Agent」或「多Agent」。写「视情况而定」拿不到分——"
         "这一题要的是一个能执行的结论。")

    reason = [word for word in REASONS if word in text]
    item(items, "理由落在具体机制上", bool(reason),
         "没看到这几条里的任何一条：%s。\n"
         "「更先进」「更灵活」这种理由不算——要说清是哪种开销被省掉了。"
         % "、".join(REASONS))

    item(items, "篇幅没超过一屏", len(body) <= MAX_CHARS,
         "去掉空白后是 %d 个字符，正文要求建议本身 ≤200 字（整份文件给了 260 的余量）。"
         % len(body))

    finish(items)


if __name__ == "__main__":
    main()
