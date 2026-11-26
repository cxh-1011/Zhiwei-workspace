"""第 9 章（第三节）验收：一张「现在别硬刚」的红灯清单。

对应正文「动手练」第 3 题：

> 新建 `agent_limits.md`，写下三条红灯项，格式如
> `🔴 实时查未公开数据库 —— 它没权限，也没连接方式。`
> 做完：文件里有且仅有 3 条 🔴 开头的条目，每条附一句话原因。

「有且仅有 3 条」是这一题的关键：写成十条不算，那说明还没想清楚哪三件
是真做不了的；写两条也不算，漏了一条将来就会在需求会上被追问。
"""

import sys

from _kit import finish, item, read_text

MARK = "🔴"
WANT = 3
# 一行里除了标记和名字，至少还要有一点原因，否则「🔴 查数据库」等于没写
MIN_CHARS = 12


def main():
    project = sys.argv[1] if len(sys.argv) > 1 else "."
    items = []

    text, error = read_text(
        project, "agent_limits.md",
        "第 3 题要求把三条红灯项写进这个文件，每条以 🔴 开头。")
    if text is None:
        item(items, "agent_limits.md 存在", False, error)
        finish(items)
        return

    lines = [line.strip() for line in text.splitlines() if MARK in line]
    item(items, "正好三条红灯项", len(lines) == WANT,
         "数出 %d 条带 🔴 的行，题目要求有且仅有 %d 条。" % (len(lines), WANT))

    if lines:
        short = [line[:30] for line in lines if len(line) < MIN_CHARS]
        item(items, "每条都写了原因", not short,
             "这几行太短，没看出为什么做不了：%s。用「—— 原因」接一句。"
             % "；".join(short))

    finish(items)


if __name__ == "__main__":
    main()
