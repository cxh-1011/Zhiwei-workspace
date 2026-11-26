"""第 1 章（第三节）验收：切两半数 token，再查出模型的上限。

对应正文「动手练」第 2、3 题。

第 2 题的 `encode` 由验收传进来，不让脚本自己 import tiktoken：
- 换成「每字符一 token」的假 encode，两半之和应该精确等于总数；
- 换成「每 3 字符一 token」，切点不齐时会有 1~2 的偏差，正好对上正文的容差。

两个 encode 都跑一遍，才能区分「他真的切了再分别数」和「他直接把总数
除以二」。后者在第一种 encode 下也能凑出近似值，但在第二种下会露馅。

第 3 题是一份查证记录 `context_limit.md`，格式正文里已经给了：
`gpt-4-turbo: 128,000 tokens`。
"""

import re
import sys

from _kit import expect_callable, read_text, run

SAMPLE = ("帮我看下这个接口为什么超时：requests.get(url, timeout=3) 一直抛异常，"
          "日志里只有 TimeoutError。")

# 正文的格式：模型名 + 冒号 + 数字 + 单位。逗号、全角冒号、"128k" 都认。
_LIMIT = re.compile(
    r"([A-Za-z][\w.\-]*)\s*[:：]\s*(\d[\d,，]*)\s*([kK])?\s*(?:tokens?|个?\s*token)",
    re.IGNORECASE,
)


def _triple(result):
    if isinstance(result, dict):
        values = [result.get("first"), result.get("second"), result.get("total")]
    elif isinstance(result, (list, tuple)):
        values = list(result)
    else:
        raise AssertionError("split_and_count 返回了 %s，应该返回 (前半, 后半, 全部)。"
                             % type(result).__name__)
    if len(values) != 3:
        raise AssertionError("应该返回三个数，现在返回了 %d 个。" % len(values))
    for value in values:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise AssertionError("三个数都应该是非负整数，现在有 %r。" % (value,))
    return values


def _case(module, encode, label):
    fn = expect_callable(module, "split_and_count")
    first, second, total = _triple(fn(SAMPLE, encode))
    if first <= 0 or second <= 0:
        return False, "%s：切出来的两半有一半是空的（%d / %d）。" % (label, first, second)
    if abs(first + second - total) > 2:
        return False, ("%s：前半 %d + 后半 %d = %d，但全长算出来是 %d。"
                       "应当分别对两半调 encode，而不是拿总数除以二。"
                       % (label, first, second, first + second, total))
    return True, ""


def plain_count(module):
    """每字符一 token：两半之和应当精确等于全长。"""
    return _case(module, lambda text: list(range(len(text))), "每字符一 token")


def chunky_count(module):
    """每 3 字符一 token：切点不齐时会有 1~2 的偏差。"""
    return _case(module, lambda text: list(range(max(1, len(text) // 3))),
                 "每 3 字符一 token")


def limit_note(module, project_dir):
    text, error = read_text(
        project_dir, "context_limit.md",
        "第 3 题要求把查到的上限写成这个文件，格式：`模型名: 128,000 tokens`。")
    if text is None:
        return False, error

    best = None
    for match in _LIMIT.finditer(text):
        name, digits, kilo = match.group(1), match.group(2), match.group(3)
        try:
            number = int(digits.replace(",", "").replace("，", ""))
        except ValueError:
            continue
        if kilo:
            number *= 1000
        if best is None or number > best[1]:
            best = (name, number)

    if best is None:
        return False, ("没找到「模型名: 数字 tokens」这样的一行。比如：\n"
                       "  gpt-4-turbo: 128,000 tokens\n"
                       "  qwen-max: 32,000 tokens")
    name, number = best
    # 4000 是个很低的门槛：现在能用的对话模型没有比这更小的。
    # 卡这一条是为了挡住「1 token」这种占位写法。
    if number < 4000:
        return False, "读出的是 %s: %d tokens。这个数字太小了，八成是没查到真实值。" % (
            name, number)
    return True, ""


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("每字符一 token 时两半之和正确", plain_count),
        ("换一种 encode 仍然正确", chunky_count),
        # 这一章要把一个 encode 函数传进你的代码：跨语言传不了
    ], extra=[("context_limit.md 写出了模型上限", limit_note)],
        languages=("python",))
