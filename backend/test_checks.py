"""33 章验收脚本的离线测试。

为什么每章要两份 fixture
------------------------
只测「正确实现能过」，一个永远返回 `ok` 的脚本也能通过；只测「错误实现不过」，
一个永远返回 `False` 的脚本也能通过。两头发卡，才能说明这个脚本真的在判。

fixture 是文件字典而不是一段 main.py 源码
------------------------------------------
因为有几章验的是产物文件（`tool_call.json`、`memories.jsonl`、
`agent_limits.md`……），只给 main.py 的 fixture 表达不了。用 {文件名: 内容}
就统一了，main.py 只是其中一个键。

这些 fixture 也是给学生看的参考：它们是最短的正确实现，没有一行是为了
迎合检查写的。
"""

import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import checks  # noqa: E402

COURSE = "agent-dev"
TEMP = tempfile.mkdtemp(prefix="test_checks_")
FAILED = []
PASSED = []
SKIPPED = []


def check(name, ok, detail=""):
    if ok:
        PASSED.append(name)
        return
    FAILED.append(name)
    print("  FAIL %s  → %s" % (name, detail))


def write(files):
    """按 {文件名: 内容} 建一个临时项目目录。"""
    folder = tempfile.mkdtemp(dir=TEMP)
    for name, content in files.items():
        path = os.path.join(folder, name)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
    return folder


# ---------------------------------------------------------------- 正确实现

GOOD = {}
GOOD["s1c1"] = {"main.py": '''
import re

_CJK = re.compile(r"[\\u4e00-\\u9fff]")


def _estimate(text):
    cjk = len(_CJK.findall(text or ""))
    rest = max(0, len(text or "") - cjk)
    return cjk + max(1, rest // 4)


def compare_tokens(zh, en):
    a, b = _estimate(zh), _estimate(en)
    return {"zh": a, "en": b, "diff_pct": round((b - a) / float(a) * 100, 1)}
'''}

GOOD["s1c2"] = {
    "output_temp0.txt": "```python\nimport pandas as pd\ndf = pd.read_csv('data.csv')\n```\n\n"
                        "```python\nimport pandas as pd\ndf = pd.read_csv('data.csv')\n```\n\n"
                        "```python\nimport pandas as pd\ndf = pd.read_csv('data.csv')\n```\n",
    "output_temp08.txt": "```python\nimport pandas as pd\ndf = pd.read_csv('data.csv')\n```\n\n"
                         "```python\nimport pandas as pd\nframe = pd.read_csv('data.csv')\n```\n\n"
                         "```python\nimport pandas as pd\n\n# 注意：确保文件存在\ndf = pd.read_csv('data.csv')\n```\n",
    "no_system.txt": "好的！下面是改好的代码：\n\n```python\nimport pandas as pd\ndf = pd.read_csv('data.csv')\n```\n",
    "truncated.txt": "```python\nimport pandas as pd\n",
}

GOOD["s1c3"] = {
    "main.py": '''
def split_and_count(text, encode):
    half = len(text) // 2
    head, tail = text[:half], text[half:]
    return len(encode(head)), len(encode(tail)), len(encode(text))
''',
    "context_limit.md": "我用的是 gpt-4-turbo: 128,000 tokens。\n",
}

GOOD["s2c1"] = {"main.py": '''
_SECTIONS = ("角色", "任务", "约束", "输出格式")


def build_prompt(role, task, constraints, output_format):
    return "## 角色\\n%s\\n## 任务\\n%s\\n## 约束\\n%s\\n## 输出格式\\n%s" % (
        role, task, constraints, output_format)


def find_missing_section(prompt):
    for name in _SECTIONS:
        if name not in prompt:
            return name
    return ""
'''}

GOOD["s2c2"] = {"main.py": '''
_RATIO = 2.5
_DIRTY_WORDS = ("请翻译", "请注意", "不要", "必须")


def is_clean_example(example):
    source = str(example.get("input") or "")
    output = str(example.get("output") or "")
    if len(output) > _RATIO * max(1, len(source)):
        return False
    if "（" in output or "(" in output:
        return False
    if any(word in output for word in _DIRTY_WORDS):
        return False
    return True
'''}

GOOD["s2c3"] = {"main.py": '''
import json


def extract_json(text):
    start = (text or "").find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:index + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def validate_weather(data):
    if not isinstance(data, dict):
        return False
    city = data.get("city")
    if not isinstance(city, str) or not city.strip():
        return False
    temp = data.get("temperature")
    if isinstance(temp, bool) or not isinstance(temp, (int, float)):
        return False
    return data.get("unit") in ("celsius", "fahrenheit")
'''}

GOOD["s3c2"] = {"main.py": '''
def get_weather_parameters():
    return {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名，比如 北京"},
            "unit": {"type": "string", "enum": ["c", "f"], "description": "温度单位"},
        },
        "required": ["city"],
    }


def query_express_schema():
    return {
        "name": "query_express",
        "description": "查询快递物流轨迹。只有用户明确说了单号和快递公司时才用。",
        "parameters": {
            "type": "object",
            "properties": {
                "number": {"type": "string", "minLength": 10, "maxLength": 20,
                           "description": "快递单号"},
                "carrier": {"type": "string", "enum": ["sf", "zto", "yto"],
                            "description": "快递公司"},
            },
            "required": ["number", "carrier"],
        },
    }
'''}

GOOD["s3c3"] = {"main.py": '''
import json


def parse_arguments(raw, required=("city",)):
    data = raw if isinstance(raw, dict) else json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("arguments 应该是一个对象")
    for name in required:
        value = data.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("缺少参数 %s" % name)
    return data
'''}

GOOD["s3c4"] = {"main.py": '''
_RULES = {
    "check_syntax": ("语法", "检查"),
    "edit_file": ("缩进", "替换", "换成", "加", "删", "改"),
    "web_search": ("版本", "最新", "搜索", "查一下"),
    "parse_python_code": ("结构", "有哪些函数", "有哪些类", "分析"),
}


def choose_tool(query, tools):
    best, best_score = None, 0
    for tool in tools:
        words = _RULES.get(tool.get("name"), ())
        score = sum(1 for word in words if word in (query or ""))
        if score > best_score:
            best, best_score = tool.get("name"), score
    return best
'''}

GOOD["s4c2"] = {"main.py": '''
_SIGNALS = ("查不到就放弃", "已拿到答案", "final answer")


def should_stop(text):
    low = (text or "").lower()
    return any(signal in low for signal in _SIGNALS)
'''}

GOOD["s4c3"] = {"main.py": '''
def check_budget(tokens_used, seconds_used, turns_used,
                 max_tokens=2000, max_seconds=30, max_turns=3):
    if tokens_used >= max_tokens:
        return "token"
    if seconds_used >= max_seconds:
        return "time"
    if turns_used >= max_turns:
        return "turns"
    return None
'''}

GOOD["s4c4"] = {"main.py": '''
import json


def extract_steps(messages):
    steps = []
    for message in messages or []:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw = function.get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                args = None
            steps.append({"tool": function.get("name"), "args": args, "id": call.get("id")})
    return steps
'''}

GOOD["s5c2"] = {
    "main.py": '''
import json
from datetime import datetime


def append_memory(path, content, usage):
    entry = {"content": content, "usage": list(usage),
             "timestamp": datetime.now().isoformat(timespec="seconds")}
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\\n")
    return entry


def search_memories(path, tag):
    found = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if tag in (entry.get("usage") or []):
                found.append(entry.get("content"))
    return found
''',
    "memories.jsonl": '{"content": "让模型帮你改代码时，必须用 black 格式化", '
                      '"usage": ["code", "format"], "timestamp": "2026-11-20T10:00:00"}\n',
}

GOOD["s5c3"] = {
    "bad_memory_example.md": """user: 帮我查下 FastAPI 的 CORS 怎么配。
model: 根据搜索结果，加 allow_origins=["*"] 就行。我已记住这条规则：allow_origins 可以写 *。
假设：你的接口是完全公开的（搜索里那句话没说这一点，模型却把它当成了结论）。
""",
}

GOOD["s6c1"] = {"main.py": '''
_ENDS = "。！？!?"


def split_by_sentence(text):
    text = (text or "").strip()
    if not text:
        return []
    out, buffer = [], ""
    for index, char in enumerate(text):
        buffer += char
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if char in _ENDS or char == "\\n" or char in "；;":
            if buffer.strip():
                out.append(buffer.strip())
            buffer = ""
        elif char == "：":
            label = buffer[:-1].strip()
            if label.startswith("第") or nxt in (" ", "\\n", ""):
                if buffer.strip():
                    out.append(buffer.strip())
                buffer = ""
    if buffer.strip():
        out.append(buffer.strip())
    return out


def is_complete_sentence(sentence):
    text = (sentence or "").strip()
    if not text or text[-1] not in _ENDS:
        return False
    return any(char not in _ENDS for char in text)
'''}

GOOD["s6c2"] = {"main.py": '''
import math


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def rank_by_similarity(texts, query, embed):
    target = embed(query)
    scored = [(index, cosine(embed(text), target)) for index, text in enumerate(texts)]
    scored.sort(key=lambda item: item[1], reverse=True)
    return [index for index, _score in scored]
'''}

GOOD["s6c3"] = {
    "main.py": '''
def top_k(ranked, k=3, threshold=0.0):
    kept = [entry for entry in ranked if entry[1] > threshold]
    return kept[:k]
''',
    "reranked_chunks.json": '{"chunks": ["猫对咖啡因极度敏感，少量就会中毒。", '
                            '"咖啡因在肝脏中代谢，猫缺乏相关酶。"]}\n',
}

GOOD["s6c4"] = {"main.py": '''
import re

_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\\s*\\(")


def find_unsupported(answer, sources):
    blob = " ".join(sources or [])
    seen, out = set(), []
    for name in _CALL.findall(answer or ""):
        if name in blob or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out
'''}

GOOD["s7c1"] = {"main.py": '''
def relay(chunks, sink):
    total = 0
    for piece in chunks:
        if not piece:
            continue
        sink(piece)
        total += len(piece)
    return total
'''}

GOOD["s7c2"] = {"main.py": '''
import os


class TempFlag:
    def __init__(self, path):
        self.path = path

    def start(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("running")

    def cancel(self):
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
'''}

GOOD["s7c3"] = {"main.py": '''
import json


def needs_approval(tool):
    return bool(tool.get("is_dangerous"))


def resume_after_approval(tool_call):
    function = tool_call.get("function") or {}
    args = json.loads(function.get("arguments") or "{}")
    return "已执行：删除用户 %s 数据" % args.get("user")
'''}

GOOD["s7c4"] = {"main.py": '''
import json


def replay_trace(trace_id, log_file):
    events = []
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("trace_id") == trace_id:
                    events.append(event)
    except OSError:
        return []
    events.sort(key=lambda event: event.get("ts") or "")
    return events
'''}

GOOD["s8c1"] = {
    "main.py": '''
def extract_test_case(log):
    return {
        "input": log.get("input", ""),
        "expected_output": log.get("expected_output", ""),
        "actual_output": log.get("output", ""),
        "failure_reason": log.get("error", ""),
    }
''',
    "test_cases.json": '[{"input": "查订单状态", "expected_output": "返回订单", '
                       '"actual_output": "KeyError", "failure_reason": "KeyError: order_id"}]\n',
}

GOOD["s8c3"] = {
    "main.py": '''
def compare_results(old, new):
    by_question = {item.get("question"): item for item in new or []}
    out = []
    for item in old or []:
        question = item.get("question")
        other = by_question.get(question)
        if other is None:
            out.append({"question": question, "changed": ["missing"]})
            continue
        changed = []
        if bool(item.get("tool_calls")) != bool(other.get("tool_calls")):
            changed.append("tool_calls")
        if (item.get("content") or "") != (other.get("content") or ""):
            changed.append("content")
        if changed:
            out.append({"question": question, "changed": changed})
    return out
''',
    "regression_questions.txt": "查上海天气\n把这段改成异步\n解释一下 GIL\n写个快排\n查快递单号\n",
    "old_results.json": '[{"question": "查上海天气", "content": "", "tool_calls": [{"name": "get_weather"}]},'
                        ' {"question": "把这段改成异步", "content": "a", "tool_calls": []},'
                        ' {"question": "解释一下 GIL", "content": "b", "tool_calls": []},'
                        ' {"question": "写个快排", "content": "c", "tool_calls": []},'
                        ' {"question": "查快递单号", "content": "d", "tool_calls": []}]\n',
    "new_results.json": '[{"question": "查上海天气", "content": "22 度", "tool_calls": []},'
                        ' {"question": "把这段改成异步", "content": "a", "tool_calls": []},'
                        ' {"question": "解释一下 GIL", "content": "b2", "tool_calls": []},'
                        ' {"question": "写个快排", "content": "c", "tool_calls": []},'
                        ' {"question": "查快递单号", "content": "d", "tool_calls": []}]\n',
}

GOOD["s9c1"] = {
    "architecture_note.md": """# 选型笔记

反例：把 Excel 转成 SQL 建表语句，一次读完就够了，中间没有要同时等的外部接口。

建议：客服对话里「查订单状态 + 安抚用户」，用单 Agent。理由是两件事共享同一段
上下文，职责可以合并；拆成两个 Agent 只会多一次交接，把订单号丢在半路。
""",
}

GOOD["s9c2"] = {"main.py": '''
def check_handoff(a_resp, b_instruction):
    answer = a_resp or ""
    instruction = b_instruction or ""
    if ("℃" in answer or "摄氏度" in answer) and "单位" not in instruction:
        return "⚠️ B 指令未覆盖单位，可能丢失精度"
    return None
'''}

GOOD["s9c3"] = {
    "agent_limits.md": """🔴 实时查未公开数据库 —— 它没权限，也没有连接方式。
🔴 保证所有历史新闻都准确 —— 训练数据有截止时间，检索源也覆盖不全。
🔴 替用户下单付款 —— 涉及资金操作，必须人工确认。
""",
}

GOOD["s10c1"] = {"main.py": '''
def _tokens(text):
    return max(1, len(text or "") // 4)


def count_gpt_cost(text):
    return _tokens(text) / 1_000_000 * 0.15


def compare_cost(text):
    tokens = _tokens(text)
    return {
        "gpt-4o-mini": tokens / 1_000_000 * 0.15,
        "claude-3-haiku-20240307": tokens / 1_000_000 * 0.25,
    }
'''}

GOOD["s10c2"] = {"main.py": '''
_SLOW = ("等", "秒", "卡", "慢", "延迟", "转圈")
_WRONG = ("错", "不对", "胡说", "答错", "算错")
_MISSING = ("能不能", "希望", "支持", "加个", "要是能")


def classify_feedback(text):
    text = text or ""
    if any(word in text for word in _SLOW):
        return "太慢"
    if any(word in text for word in _WRONG):
        return "回答错误"
    if any(word in text for word in _MISSING):
        return "功能缺失"
    return "其他"


def priority(repeat, blocks_main):
    if repeat and blocks_main:
        return "高"
    if repeat or blocks_main:
        return "中"
    return "低"
'''}

GOOD["s3c1"] = {"tool_call.json": '{"name": "get_weather", "arguments": "{\\"city\\": \\"杭州,苏州\\"}"}'}

GOOD["s4c1"] = {"main.py": '''
import re

_LABEL = re.compile(r"^\\s*(thought|action\\s*input|action)\\s*:\\s*(.*)$", re.IGNORECASE)


def parse_react(text):
    out = {"thought": "", "action": "", "action_input": ""}
    field = None
    for line in (text or "").splitlines():
        match = _LABEL.match(line)
        if match:
            key = match.group(1).lower().replace(" ", "")
            field = {"thought": "thought", "action": "action", "actioninput": "action_input"}[key]
            out[field] = match.group(2).strip()
        elif field:
            out[field] = (out[field] + "\\n" + line).strip()
    return out
'''}

GOOD["s5c1"] = {"main.py": '''
def trim_conversation(messages):
    kept = []
    for message in messages:
        if message["role"] == "user" and not any(
                word in message["content"] for word in ("你好", "谢谢")):
            kept.append(message)
            break
    for message in messages:
        if message["role"] == "assistant" and "```" in message["content"]:
            kept.append(message)
            break
    return kept
'''}

GOOD["s8c2"] = {"main.py": '''
import re


def price_check(text):
    return bool(re.search(r"\\d+(?:\\.\\d+)?元", str(text)))
'''}

# ---------------------------------------------------------------- 错误实现

BAD = {}

# 拿字符数当 token 数：英文那组会反过来比中文多
BAD["s1c1"] = {"main.py": '''
def compare_tokens(zh, en):
    return {"zh": len(zh), "en": len(en), "diff_pct": 0}
'''}

# 少一个文件
BAD["s1c2"] = {"output_temp0.txt": "```python\nprint(1)\n```\n"}

# 两半全给 0、总数照抄
BAD["s1c3"] = {
    "main.py": "def split_and_count(text, encode):\n    n = len(encode(text))\n    return 0, n, n\n",
    "context_limit.md": "gpt-4-turbo: 128,000 tokens\n",
}

# 只拼内容，不写段标题
BAD["s2c1"] = {"main.py": '''
def build_prompt(role, task, constraints, output_format):
    return " ".join([role, task, constraints, output_format])


def find_missing_section(prompt):
    return ""
'''}

# 一律判干净
BAD["s2c2"] = {"main.py": "def is_clean_example(example):\n    return True\n"}

# 直接 json.loads，不处理包装；字段也不验
BAD["s2c3"] = {"main.py": '''
import json


def extract_json(text):
    return json.loads(text)


def validate_weather(data):
    return True
'''}

# unit 有默认值却进了 required，enum 也写成大写
BAD["s3c2"] = {"main.py": '''
def get_weather_parameters():
    return {
        "type": "object",
        "properties": {"city": {"type": "string"}, "unit": {"type": "string", "enum": ["C", "F"]}},
        "required": ["city", "unit"],
    }


def query_express_schema():
    return {"name": "query_express", "description": "查快递",
            "parameters": {"type": "object", "properties": {}, "required": []}}
'''}

# 只 json.loads，不校验必需字段
BAD["s3c3"] = {"main.py": '''
import json


def parse_arguments(raw, required=("city",)):
    return json.loads(raw)
'''}

# 永远选第一个
BAD["s3c4"] = {"main.py": '''
def choose_tool(query, tools):
    return tools[0]["name"]
'''}

# 认「答案」两个字：GO_ON 里那条会被误停
BAD["s4c2"] = {"main.py": '''
def should_stop(text):
    return "答案" in (text or "") or "放弃" in (text or "")
'''}

# 只用大于，不认边界；上限写死
BAD["s4c3"] = {"main.py": '''
def check_budget(tokens_used, seconds_used, turns_used, max_tokens=2000,
                 max_seconds=30, max_turns=3):
    if tokens_used > 2000:
        return "token"
    return None
'''}

# 只看第一条消息的第一个 tool_call
BAD["s4c4"] = {"main.py": '''
import json


def extract_steps(messages):
    for message in messages or []:
        calls = message.get("tool_calls") or []
        if not calls:
            continue
        function = calls[0]["function"]
        return [{"tool": function["name"], "args": json.loads(function["arguments"]),
                 "id": calls[0]["id"]}]
    return []
'''}

# 用 "w" 打开：第二条把第一条覆盖掉
BAD["s5c2"] = {
    "main.py": '''
import json
from datetime import datetime


def append_memory(path, content, usage):
    entry = {"content": content, "usage": list(usage),
             "timestamp": datetime.now().isoformat(timespec="seconds")}
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\\n")
    return entry


def search_memories(path, tag):
    with open(path, "r", encoding="utf-8") as handle:
        line = handle.readline().strip()
    entry = json.loads(line) if line else {}
    return [entry.get("content")] if tag in (entry.get("usage") or []) else []
''',
    "memories.jsonl": '{"content": "让模型帮你改代码时，必须用 black 格式化", '
                      '"usage": ["code", "format"], "timestamp": "2026-11-20T10:00:00"}\n',
}

# 只写了两行，也没标出「假设」
BAD["s5c3"] = {"bad_memory_example.md": "user: 问点什么\nmodel: 谢谢使用\n"}

# 只按句末标点切：冒号分号那两条会挂
BAD["s6c1"] = {"main.py": '''
def split_by_sentence(text):
    out, buffer = [], ""
    for char in (text or ""):
        buffer += char
        if char in "。！？!?":
            out.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        out.append(buffer.strip())
    return [item for item in out if item]


def is_complete_sentence(sentence):
    return bool(sentence) and sentence.strip()[-1:] in ("。", "！", "？")
'''}

# 直接返回点积，没除以模长
BAD["s6c2"] = {"main.py": '''
def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


def rank_by_similarity(texts, query, embed):
    target = embed(query)
    scored = [(index, cosine(embed(text), target)) for index, text in enumerate(texts)]
    scored.sort(key=lambda item: item[1], reverse=True)
    return [index for index, _score in scored]
'''}

# 不筛阈值，全给你
BAD["s6c3"] = {
    "main.py": '''
def top_k(ranked, k=3, threshold=0.0):
    return ranked[:k]
''',
    "reranked_chunks.json": '{"chunks": ["猫对咖啡因极度敏感，少量就会中毒。"]}\n',
}

# 把所有函数调用都当编造：submit / as_completed 也报
BAD["s6c4"] = {"main.py": '''
import re

_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_.]*)\\s*\\(")


def find_unsupported(answer, sources):
    return _CALL.findall(answer or "")
'''}

# 攒完整段才往外写一次
BAD["s7c1"] = {"main.py": '''
def relay(chunks, sink):
    text = "".join(piece for piece in chunks if piece)
    sink(text)
    return len(text)
'''}

# cancel 什么也不做
BAD["s7c2"] = {"main.py": '''
import os


class TempFlag:
    def __init__(self, path):
        self.path = path

    def start(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("running")

    def cancel(self):
        return None
'''}

# 永远不需要审批
BAD["s7c3"] = {"main.py": '''
import json


def needs_approval(tool):
    return False


def resume_after_approval(tool_call):
    function = tool_call.get("function") or {}
    args = json.loads(function.get("arguments") or "{}")
    return "已执行：删除用户 %s 数据" % args.get("user")
'''}

# 按文件顺序，且不跳坏行
BAD["s7c4"] = {"main.py": '''
import json


def replay_trace(trace_id, log_file):
    events = []
    with open(log_file, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("trace_id") == trace_id:
                events.append(event)
    return events
'''}

# 缺字段直接 KeyError
BAD["s8c1"] = {"main.py": '''
def extract_test_case(log):
    return {
        "input": log["input"],
        "expected_output": log["expected_output"],
        "actual_output": log["output"],
        "failure_reason": log["error"],
    }
'''}

# 只比 content，漏掉"不再调工具"这类回归
BAD["s8c3"] = {
    "main.py": '''
def compare_results(old, new):
    by_question = {item.get("question"): item for item in new or []}
    out = []
    for item in old or []:
        other = by_question.get(item.get("question"))
        if other is None:
            continue
        if (item.get("content") or "") != (other.get("content") or ""):
            out.append({"question": item.get("question"), "changed": ["content"]})
    return out
''',
    "regression_questions.txt": "查上海天气\n把这段改成异步\n解释一下 GIL\n写个快排\n查快递单号\n",
    "old_results.json": "[]",
    "new_results.json": "[]",
}

# 只有「视情况而定」，没有结论
BAD["s9c1"] = {"architecture_note.md": "视情况而定，各有优劣。\n"}

# 永远不警告
BAD["s9c2"] = {"main.py": "def check_handoff(a_resp, b_instruction):\n    return None\n"}

# 四条红灯
BAD["s9c3"] = {
    "agent_limits.md": """🔴 实时查未公开数据库 —— 没权限。
🔴 保证历史新闻准确 —— 数据有截止时间。
🔴 替用户下单付款 —— 涉及资金。
🔴 生成图片 —— 没有图片模型。
""",
}

# 把每 token 价格算成每 token 的千分之一：量级错
BAD["s10c1"] = {"main.py": '''
def count_gpt_cost(text):
    return max(1, len(text or "")) * 0.15


def compare_cost(text):
    return {"gpt-4o-mini": 1.0, "claude-3-haiku-20240307": 2.0}
'''}

# or 吞掉了「低」
BAD["s10c2"] = {"main.py": '''
def classify_feedback(text):
    return "其他"


def priority(repeat, blocks_main):
    if repeat and blocks_main:
        return "高"
    return "中"
'''}

BAD["s3c1"] = {"tool_call.json": '{"name": "get_weather", "arguments": {"city": "杭州"}}'}

BAD["s4c1"] = {"main.py": "def parse_react(text):\n    return text.split(',')\n"}

BAD["s5c1"] = {"main.py": "def trim_conversation(messages):\n    return messages\n"}

BAD["s8c2"] = {"main.py": 'def price_check(text):\n    return "True"\n'}


# ---------------------------------------------------------------- 跑

def test():
    print("[1] 脚本清单")
    chapters = checks.list_chapters(COURSE)
    check("33 章都有验收脚本", len(chapters) == 33, chapters)
    check("脚本清单不含 _kit", "_kit" not in chapters, chapters)
    missing_fixture = [item for item in chapters if item not in GOOD or item not in BAD]
    check("每章都有正反两份 fixture", not missing_fixture, missing_fixture)

    print("[2] 每章的说明与实现")
    for chapter in chapters:
        if chapter not in GOOD:
            SKIPPED.append(chapter)
            continue
        check("%s 有任务说明" % chapter, bool(checks.brief(chapter, COURSE)))

        folder = write(GOOD[chapter])
        passed = checks.run(folder, chapter)
        check("%s 正确实现通过" % chapter, passed["ok"], passed.get("items") or passed.get("error"))
        check("%s 至少一条检查" % chapter, passed["total"] > 0, passed.get("total"))

        folder = write(BAD[chapter])
        failed = checks.run(folder, chapter)
        check("%s 错误实现不通过" % chapter, not failed["ok"],
              "既然通过了，说明这个脚本没有真的在判")
        check("%s 失败项都带原因" % chapter,
              all(entry["detail"] for entry in failed["items"] if not entry["ok"]),
              failed.get("items"))

    print("[3] 边界")
    # 空目录：不能因为「什么都没跑」而算通过
    for chapter in ("s2c3", "s7c4", "s9c3"):
        if chapter not in GOOD:
            continue
        result = checks.run(write({}), chapter)
        check("%s 空目录不通过" % chapter, not result["ok"], result.get("items"))
        check("%s 空目录指出缺了什么" % chapter,
              result["total"] > 0 and any(not entry["ok"] for entry in result["items"]),
              result.get("items"))


def main():
    test()
    print()
    print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
    if SKIPPED:
        print("还没写 fixture 的章：%s" % " ".join(sorted(SKIPPED)))
    shutil.rmtree(TEMP, ignore_errors=True)
    if FAILED:
        print("失败清单：")
        for name in FAILED:
            print("  - " + name)
        sys.exit(1)


if __name__ == "__main__":
    main()
