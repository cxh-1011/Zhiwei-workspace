"""第 5 章（第三节）验收：一份「模型擅自记错事」的对话记录。

对应正文「动手练」第 3 题：写 3 行，展示模型把假设当结论记下来的典型行为，
存成 `bad_memory_example.md`。

这一章没有代码产物——三道题分别是改提示词、加确认开关、造错误例子。
前两道要跑模型；第三道的产物是一段文字，所以验收就看这份文件有没有把
「哪句是假设、哪句被记成了事实」摆出来。

为什么要求标出「假设」和「记下」
--------------------------------
只写一段普通对话没有意义。这一题的价值在于让人看见：模型说"我记住了"
的时候，记住的可能是上下文里一句没验证的猜测。所以文件里必须同时出现
「假设/可能/据说」和「记住/已存/记下」两类词，才算把这件事演示出来。
"""

import sys

from _kit import read_text, run

# 两类词各出现一个，才说明这句话里既有"没验证的前提"又有"被当成事实存下来"
GUESS_WORDS = ("假设", "可能", "据说", "应该是", "猜测", "未验证")
MEMORY_WORDS = ("记住", "记下", "已存", "存入", "记忆", "memory")

MIN_LINES = 3


def example_note(module, project_dir):
    text, error = read_text(
        project_dir, "bad_memory_example.md",
        "第 3 题要求把那段 3 行对话存成这个文件：一行 user，一行 model，"
        "model 里要出现「我已记住…」。")
    if text is None:
        return False, error

    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < MIN_LINES:
        return False, "只有 %d 行非空内容，题目要求 3 行（user 一句 + model 的回答）。" % len(lines)

    guess = [word for word in GUESS_WORDS if word in text]
    memory = [word for word in MEMORY_WORDS if word in text]

    problems = []
    if not guess:
        problems.append("没看到「假设 / 可能 / 据说」这类词：这句错事实是怎么来的，要写出来")
    if not memory:
        problems.append("没看到「记住 / 记下 / 已存」这类词：这一题要演示的就是「擅自记下」")
    if not any(key in text.lower() for key in ("user", "用户", "model", "assistant", "模型")):
        problems.append("没标出谁说的：写清 user 和 model 两行，否则看不出是模型记错了")
    return not problems, "；".join(problems)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [],
        extra=[("bad_memory_example.md 演示了「擅自记下」", example_note)],
        need_module=False)
