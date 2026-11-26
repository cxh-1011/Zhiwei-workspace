"""第 3 章（第二节）验收：把函数签名翻译成 parameters。

对应正文「动手练」第 2、3 题：
2. `def get_weather(city: str, unit: str = "c")` 补全 parameters；
3. `def query_express(number: str, carrier: str)` 写完整 function schema。

这两题考的其实是同一件事：**类型信息要落到 schema 里，模型才看得见**。
`unit` 有默认值 → 不该进 required；`carrier` 只有三种取值 → 该进 enum；
`number` 有长度范围 → 该进 minLength/maxLength。这些约束在 Python 类型注解里
是写不出来的，只能写进 schema。

第 1 题（改错题）不在这里验：它要跑模型看 `arguments` 里是字符串还是整数。
"""

import sys

from _kit import expect_callable, run


def _parameters(data):
    """从返回值里取 parameters。三种摆法都接受：
    只给 parameters、给完整 function schema、给 OpenAI 的
    {"type": "function", "function": {...}} 包装。"""
    if not isinstance(data, dict):
        raise AssertionError("返回了 %s，应该返回字典。" % type(data).__name__)
    if isinstance(data.get("function"), dict):
        data = data["function"]
    if isinstance(data.get("parameters"), dict):
        return data["parameters"]
    return data


def _props(params):
    props = params.get("properties")
    if not isinstance(props, dict):
        raise AssertionError("parameters 里没有 properties 字典。模型就是靠它知道有哪些参数的。")
    return props


def weather(module):
    fn = expect_callable(module, "get_weather_parameters")
    params = _parameters(fn())
    props = _props(params)
    problems = []
    for name in ("city", "unit"):
        if name not in props:
            problems.append("properties 里少了 %s" % name)
    if "city" in props and (props["city"] or {}).get("type") != "string":
        problems.append("city 的 type 不是 string（现在是 %r）" % (props["city"] or {}).get("type"))
    unit = props.get("unit") or {}
    if set(unit.get("enum") or []) != {"c", "f"}:
        problems.append("unit 的 enum 应该是 [\"c\", \"f\"]，现在是 %r" % (unit.get("enum"),))

    required = params.get("required")
    if not isinstance(required, list):
        problems.append("required 应该是列表，现在是 %r" % (required,))
    else:
        if "city" not in required:
            problems.append("city 是必填的，required 里却没有它")
        if "unit" in required:
            problems.append("unit 有默认值 \"c\"，不该出现在 required 里")
    return not problems, "；".join(problems)


def express(module):
    fn = expect_callable(module, "query_express_schema")
    data = fn()
    if not isinstance(data, dict):
        raise AssertionError("返回了 %s，应该返回字典。" % type(data).__name__)
    if isinstance(data.get("function"), dict):
        data = data["function"]

    problems = []
    if data.get("name") != "query_express":
        problems.append("name 应该是 \"query_express\"，现在是 %r" % (data.get("name"),))
    description = str(data.get("description") or "")
    for word in ("单号", "快递"):
        if word not in description:
            problems.append("description 里要出现「%s」，让模型知道这个工具是干什么的" % word)

    params = _parameters(data)
    props = _props(params)
    number = props.get("number") or {}
    if number.get("type") != "string":
        problems.append("number 的 type 应该是 string")
    if number.get("minLength") != 10:
        problems.append("number 的 minLength 应该是 10，现在是 %r" % (number.get("minLength"),))
    if number.get("maxLength") != 20:
        problems.append("number 的 maxLength 应该是 20，现在是 %r" % (number.get("maxLength"),))

    carrier = props.get("carrier") or {}
    if set(carrier.get("enum") or []) != {"sf", "zto", "yto"}:
        problems.append("carrier 的 enum 应该是 [\"sf\", \"zto\", \"yto\"]，现在是 %r"
                        % (carrier.get("enum"),))

    required = params.get("required")
    if not isinstance(required, list) or set(required) != {"number", "carrier"}:
        problems.append("required 应该正好是 number 和 carrier，现在是 %r" % (required,))
    return not problems, "；".join(problems)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else ".", [
        ("get_weather 的 parameters 补全正确", weather),
        ("query_express 的 schema 完整", express),
    ])
