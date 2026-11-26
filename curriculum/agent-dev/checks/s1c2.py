"""第 1 章（第二节）验收：四个输出文件，各自证明一件事。

对应正文「动手练」三道题，产出四个 txt：
- `output_temp0.txt`：temperature=0 跑 3 次；
- `output_temp08.txt`：temperature=0.8 跑 3 次；
- `no_system.txt`：删掉 system 消息跑一次；
- `truncated.txt`：max_tokens=15 跑一次。

这里能自动判的只有"文件里的差异"，判不了"你是不是真调了模型"。
所以每一项都盯住一个只有真跑过才会出现的特征：

1. temp08 的三段**彼此不完全一样**——temperature=0 的 3 次结果才是几乎一致的；
2. no_system 里出现了说明性文字——这正是 system 消息在压制的东西；
3. truncated 明显比正常输出短——max_tokens 砍在了半句上。

分开存进 4 个文件这件事本身也有意义：三次输出混在一个文件里，
就没法比较"哪几次不一样"。
"""

import os
import sys

from _kit import finish, item, read_text

FILES = [
    ("output_temp0.txt", "temperature=0 跑 3 次的输出"),
    ("output_temp08.txt", "temperature=0.8 跑 3 次的输出"),
    ("no_system.txt", "删掉 system 消息后的那次输出"),
    ("truncated.txt", "max_tokens=15 的那次输出"),
]

# 说明性文字的共同特征。正文第 2 题的"做完标准"就是这三样里的任意一种。
CHATTY = ("好的", "当然可以", "# 解释", "#解释", "以下是", "这段代码的作用")


def segments(text):
    """粗略数「几段输出」。

    学生把三次输出粘进一个文件，分隔方式无非 ``` 代码块、空行、或 ----- 。
    都认。宁可宽松一点，也不要求他为了通过检查去改粘贴格式。
    """
    fenced = text.count("```") // 2
    blank_groups = len([part for part in text.split("\n\n") if part.strip()])
    rules = sum(1 for line in text.splitlines() if line.strip() and set(line.strip()) <= set("-="))
    return max(fenced, blank_groups, rules + 1 if rules else 1)


def main():
    project = sys.argv[1] if len(sys.argv) > 1 else "."
    items = []

    texts = {}
    for filename, what in FILES:
        text, error = read_text(project, filename,
                                "这一章要求把「%s」单独存成这个文件。" % what)
        texts[filename] = text
        if text is None:
            item(items, "%s 存在" % filename, False, error)
        else:
            item(items, "%s 存在" % filename, bool(text.strip()),
                 "%s 是空的。把那次输出原样贴进去。" % filename)

    temp0 = texts["output_temp0.txt"]
    temp08 = texts["output_temp08.txt"]
    no_system = texts["no_system.txt"]
    truncated = texts["truncated.txt"]

    if temp0 and temp0.strip():
        item(items, "temp0 里有三次输出", segments(temp0) >= 3,
             "只数出 %d 段。三次输出之间用空行或 ``` 分开。" % segments(temp0))

    if temp08 and temp08.strip():
        item(items, "temp08 里有三次输出", segments(temp08) >= 3,
             "只数出 %d 段。三次输出之间用空行或 ``` 分开。" % segments(temp08))
        # 这是本章最关键的一条：temperature 调高之后，同样的输入不再给出
        # 同一段字。文件一模一样，说明那次 0.8 没生效（或者复制了同一份）。
        same = temp0 is not None and temp08.strip() == (temp0 or "").strip()
        item(items, "temp08 和 temp0 不一样", not same,
             "两个文件内容完全一致。temperature=0.8 时三次输出至少要有一处不同"
             "（哪怕只差一个空格），否则说明参数没生效。")

    if no_system and no_system.strip():
        hit = [word for word in CHATTY if word in no_system]
        item(items, "no_system 里出现了说明性文字", bool(hit),
             "没找到「好的」「当然可以」「# 解释」这类话。删掉 system 之后，"
             "模型通常会开始解释；如果它仍然只吐代码，检查一下 system 是不是没删干净。")

    if truncated and truncated.strip() and temp0 and temp0.strip():
        ratio = len(truncated.strip()) / float(max(1, len(temp0.strip())))
        item(items, "truncated 明显比正常输出短", ratio < 0.8,
             "truncated.txt 是 temp0 的 %d%%，看不出被截断。max_tokens=15 时"
             "应该只写出一两行就停。" % int(ratio * 100))

    finish(items)


if __name__ == "__main__":
    main()
