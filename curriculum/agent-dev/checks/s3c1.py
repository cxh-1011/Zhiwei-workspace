"""第 7 章验收：把模型的 tool_call 写成一份能被程序读的结构。

对应正文「动手练」的三道题：
1. 手写一段 tool_calls 结构，能 json.loads；
2. arguments 里要有 city，且值是字符串；
3. 找出 `{"arguments": "{"city": "广州"}"}` 那处错——根子在于 arguments
   必须是一个**字符串**（一个被转义的 JSON 文本），不是一个嵌套对象。

所以这里不检查特定城市，只检查结构：换城市不该算错。
"""

import json
import os
import sys

from _kit import finish, item

MAX_FILE = 20000


def load_json(project_dir):
    path = os.path.join(project_dir, "tool_call.json")
    if not os.path.isfile(path):
        return None, "没找到 tool_call.json。把写好的那条 tool_call 存成这个文件名，放在项目根目录。"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read(MAX_FILE)
    except OSError as exc:
        return None, "读不了 tool_call.json：%s" % exc
    try:
        return json.loads(raw), ""
    except ValueError as exc:
        return None, "tool_call.json 不是合法 JSON（%s）。注意 JSON 里不能用单引号，也不能有结尾逗号。" % exc


def main():
    project = sys.argv[1] if len(sys.argv) > 1 else "."
    items = []

    data, error = load_json(project)
    if data is None:
        item(items, "tool_call.json 能被 json.loads", False, error)
        finish(items)
        return
    item(items, "tool_call.json 能被 json.loads", True)

    if not isinstance(data, dict):
        item(items, "顶层是一个对象", False, "解析出来是 %s，应该是一条 tool_call 对象。" % type(data).__name__)
        finish(items)
        return
    item(items, "顶层是一个对象", True)

    # 模型返回的 tool_call 展开在消息里时，常见两种摆法：平铺，或者包在
    # function 里（{"type":"function","function":{...}}）。两种都算对——
    # 练习考的是字段结构，不是某一家 SDK 的包装格式。
    body = data
    if isinstance(data.get("function"), dict):
        body = data["function"]

    missing = [key for key in ("name", "arguments") if key not in body]
    item(items, "有 name 和 arguments 两个字段", not missing,
         "缺了：%s。一条 tool_call 至少要有函数名和参数。" % "、".join(missing))

    name = body.get("name")
    item(items, "name 是字符串", isinstance(name, str) and bool(name.strip()),
         "name 现在是 %s。它应该是函数名的字符串，比如 get_weather。" % type(name).__name__)

    args = body.get("arguments")
    # 这是第 3 题那处错的核心：arguments 是「一段 JSON 文本」，
    # 直接写成一个对象的话，真实的 API 会报解析失败。
    if isinstance(args, dict):
        item(items, "arguments 是字符串而不是对象", False,
             "arguments 现在是一个对象。真实接口里它是**被转义的 JSON 文本**：\n"
             '  "arguments": "{\\"city\\": \\"广州\\"}"\n'
             "把它整个写成字符串，再用 json.dumps 生成，不要手拼引号。")
        parsed = args
    elif isinstance(args, str):
        item(items, "arguments 是字符串而不是对象", True)
        try:
            parsed = json.loads(args)
        except ValueError as exc:
            item(items, "arguments 里的文本能再 json.loads", False,
                 "arguments 是字符串，但里面不是合法 JSON（%s）。检查引号有没有被转义。" % exc)
            finish(items)
            return
        item(items, "arguments 里的文本能再 json.loads", True)
    else:
        item(items, "arguments 是字符串而不是对象", False,
             "arguments 是 %s。它必须是字符串。" % type(args).__name__)
        finish(items)
        return

    if not isinstance(parsed, dict):
        item(items, "参数里是键值对", False, "解析出来是 %s，应该是一个对象。" % type(parsed).__name__)
        finish(items)
        return
    item(items, "参数里是键值对", True)

    city = parsed.get("city")
    item(items, "参数里有 city 且是字符串", isinstance(city, str) and bool(city.strip()),
         "city 现在是 %s。第 2 题要求它是字符串（\"杭州和苏州\" 或 \"杭州,苏州\" 都行），"
         "不能是列表——一个函数调用只传一个 city。" % ("缺失" if city is None else type(city).__name__))

    finish(items)


if __name__ == "__main__":
    main()
