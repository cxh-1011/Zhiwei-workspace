"""第 2 章（第三节）验收：从一段话里抠出 JSON，再验字段。

对应正文「动手练」第 1、3 题：
1. 让模型输出 `{"city": ..., "temperature": ..., "unit": ...}`，`json.loads` 不报错；
3. 去掉 response_format，靠正则提取 `{...}`，再手动检查字段类型。

第 2 题（失败重试）不在这里验——它要真的发两次请求。
但「提取 + 校验」正是重试逻辑里判断"要不要重发"的那一步，所以这两条
做对了，第 2 题的循环才有东西可循环。

为什么要求提取而不是直接 json.loads
------------------------------------
真实模型总会加一句"好的，结果如下"或包一层 ```json。第 3 题让你手工提取，
就是练这个。所以验收里给出的输入**全都带包装**，纯 JSON 只是其中一种。
"""

import json
import sys

from _kit import expect_callable, run

GOOD = {"city": "北京", "temperature": 25, "unit": "celsius"}

# (原始文本, 说明)。每一条都要能提出 GOOD。
WRAPPED = [
    (json.dumps(GOOD, ensure_ascii=False), "纯 JSON"),
    ("```json\n%s\n```" % json.dumps(GOOD, ensure_ascii=False), "```json 代码块"),
    ("好的，结果如下：\n%s\n还需要别的城市吗？" % json.dumps(GOOD, ensure_ascii=False),
     "前后有解释文字"),
    ("\n\n  %s  \n\n" % json.dumps(GOOD, ensure_ascii=False, indent=2), "带缩进和空行"),
]

# (数据, 是否合法, 说明)
VALIDATE = [
    ({"city": "北京", "temperature": 25, "unit": "celsius"}, True, "整数温度"),
    ({"city": "上海", "temperature": 25.5, "unit": "fahrenheit"}, True, "小数温度"),
    ({"temperature": 25, "unit": "celsius"}, False, "缺 city"),
    ({"city": "", "temperature": 25, "unit": "celsius"}, False, "city 是空串"),
    ({"city": "北京", "temperature": "25", "unit": "celsius"}, False, "温度是字符串"),
    ({"city": "北京", "temperature": 25, "unit": "C"}, False, "unit 不在枚举里"),
    ({"city": "北京", "temperature": 25}, False, "缺 unit"),
]


def extract(module):
    fn = expect_callable(module, "extract_json")
    problems = []
    for text, label in WRAPPED:
        try:
            got = fn(text)
        except Exception as exc:
            problems.append("%s：抛了 %s" % (label, type(exc).__name__))
            continue
        if not isinstance(got, dict):
            problems.append("%s：返回了 %s，应该返回字典" % (label, type(got).__name__))
            continue
        if got.get("city") != GOOD["city"] or got.get("temperature") != GOOD["temperature"]:
            problems.append("%s：解析出来是 %r" % (label, got))
    return not problems, "；".join(problems[:3])


def validate(module):
    fn = expect_callable(module, "validate_weather")
    problems = []
    for data, want, label in VALIDATE:
        try:
            got = fn(data)
        except Exception as exc:
            problems.append("%s：抛了 %s" % (label, type(exc).__name__))
            continue
        if got is not want:
            problems.append("%s：应该 %s，返回了 %r（要布尔值，不要字符串）"
                            % (label, want, got))
    return not problems, "；".join(problems[:4])


def no_json(module):
    """完全没有 JSON 时不要返回 {}。

    返回空字典最坑：它在 `if data:` 里是假的，但在 `data["city"]` 上会
    KeyError，两步之外才炸。这里要求抛异常或者返回 None。
    """
    fn = expect_callable(module, "extract_json")
    try:
        got = fn("抱歉，我无法回答这个问题。")
    except Exception:
        return True, ""
    return got is None, "没有 JSON 的文本应该返回 None（或抛异常），现在返回了 %r" % (got,)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("四种包装都能提出 JSON", extract),
        ("七种字段组合都判对", validate),
        ("没有 JSON 时不返回空字典", no_json),
    ])
