# -*- coding: utf-8 -*-
"""预算自测：两档预算、环境变量夹取、轮次真的能跑满、上下文裁剪不丢当前问题。

用假模型，不联网、不需要 API Key。

运行：
    cd backend
    ..\\.venv\\Scripts\\python.exe test_budget.py
"""

import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import agent  # noqa: E402

results = []


def check(label, cond, detail=""):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label
          + (("  -> " + str(detail)[:150]) if detail else ""))


def fake_model(script):
    """按调用次数依次返回预设轮次，最后一项可以纯文本收尾。"""
    state = {"round": 0}

    def fake(messages, tools=None, timeout=30.0, model=None, should_stop=None, **kwargs):
        index = min(state["round"], len(script) - 1)
        state["round"] += 1
        turn = script[index]
        for piece in turn.get("tokens", []):
            yield ("token", piece)
        yield ("message", {
            "content": "".join(turn.get("tokens", [])),
            "tool_calls": turn.get("tool_calls", []),
            "usage": turn.get("usage", 120),
            "cancelled": False,
        })

    return fake


def run(script, question="开始", **kwargs):
    agent.chat_completion_stream = fake_model(script)
    events = list(agent.run_agent_stream(question, mode="chat", **kwargs))
    return events, events[-1]


print("== 1. 两档预算的默认值 ==")
chat = agent.CHAT_BUDGET
code = agent.CODE_BUDGET
check("问答 5 轮", chat.rounds == 5, chat.rounds)
check("问答单轮 3 个工具", chat.tools_per_round == 3, chat.tools_per_round)
check("问答 60 秒", chat.seconds == 60, chat.seconds)
check("问答 token 12000", chat.tokens == 12000, chat.tokens)
check("改代码 20 轮", code.rounds == 20, code.rounds)
check("改代码单轮 6 个工具", code.tools_per_round == 6, code.tools_per_round)
check("改代码 300 秒", code.seconds == 300, code.seconds)
# token 是每次调用 usage 的累加，20 轮实测要 90–100k，60k 会在第 13 轮就截断
check("改代码 token 120000", code.tokens == 120000, code.tokens)
check("改代码 token 上限够跑满 20 轮", code.tokens >= 90000, code.tokens)
check("改代码上下文比问答宽", code.context_chars > chat.context_chars,
      (code.context_chars, chat.context_chars))
check("改代码单次模型超时更宽松", code.llm_seconds > chat.llm_seconds,
      (code.llm_seconds, chat.llm_seconds))

print("== 2. 选择逻辑与兼容别名 ==")
check("默认走问答预算", agent.budget_for(False) is agent.CHAT_BUDGET)
check("开启改代码走宽预算", agent.budget_for(True) is agent.CODE_BUDGET)
check("兼容别名指向问答档", (agent.MAX_ROUNDS, agent.MAX_TOOL_CALLS_PER_ROUND) == (5, 3),
      (agent.MAX_ROUNDS, agent.MAX_TOOL_CALLS_PER_ROUND))
check("预算可导出成扁平字典",
      set(code.as_dict()) == {"rounds", "tools_per_round", "seconds", "llm_seconds",
                              "context_chars", "tokens", "tool_output_chars"},
      sorted(code.as_dict()))
check("describe 含关键数字", "20 轮" in code.describe() and "300" in code.describe(),
      code.describe())

print("== 3. 环境变量可调且被硬上限夹住 ==")
probe = (
    "import json, sys; sys.path.insert(0, r'%s'); import agent; "
    "print(json.dumps({'c': agent.CHAT_BUDGET.as_dict(), 'k': agent.CODE_BUDGET.as_dict()}))" % BACKEND
)


def budget_with_env(extra):
    env = dict(os.environ)
    env.update(extra)
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         env=env, cwd=BACKEND, encoding="utf-8")
    assert out.returncode == 0, out.stderr[-400:]
    return json.loads(out.stdout)


got = budget_with_env({"AGENT_CODE_MAX_ROUNDS": "12"})
check("改代码轮次可调到 12", got["k"]["rounds"] == 12, got["k"]["rounds"])

got = budget_with_env({"AGENT_MAX_ROUNDS": "99"})
check("问答轮次超上限被夹到 8", got["c"]["rounds"] == 8, got["c"]["rounds"])

got = budget_with_env({"AGENT_CODE_MAX_ROUNDS": "999"})
check("改代码轮次超上限被夹到 30", got["k"]["rounds"] == 30, got["k"]["rounds"])

got = budget_with_env({"AGENT_CODE_TOTAL_TIMEOUT": "5"})
check("总耗时低于下限被夹到 60 秒", got["k"]["seconds"] == 60, got["k"]["seconds"])

got = budget_with_env({"AGENT_MAX_ROUNDS": "abc"})
check("非法值退回默认 5", got["c"]["rounds"] == 5, got["c"]["rounds"])

got = budget_with_env({"AGENT_MAX_TOKENS_PER_RUN": "999999"})
check("token 超上限被夹到 40000", got["c"]["tokens"] == 40000, got["c"]["tokens"])

print("== 4. start 事件带预算 ==")
events, _ = run([{"tokens": ["好的"]}])
start = events[0]
check("start 带 rounds_limit", start.get("rounds_limit") == 5, start.get("rounds_limit"))
check("start 带完整 budget", set(start.get("budget") or {}) >= {"rounds", "tokens"}, start.get("budget"))

events, _ = run([{"tokens": ["好的"]}], allow_code_edit=True)
check("开启改代码后 rounds_limit 变成 20", events[0].get("rounds_limit") == 20,
      events[0].get("rounds_limit"))

print("== 5. 20 轮真的能跑满（旧上限是 5）==")
call_turn = {"tool_calls": [{"id": "c1", "name": "list_uploaded_files", "arguments": "{}"}]}
events, done = run([call_turn] * 20 + [{"tokens": ["收尾完成"]}], allow_code_edit=True)
rounds = [e for e in events if e["type"] == "round"]
starts = [e for e in events if e["type"] == "tool_start"]
limits = [e for e in events if e["type"] == "limit"]
check("跑了 20 轮", done["rounds"] == 20, done["rounds"])
check("round 事件也是 20 条", len(rounds) == 20, len(rounds))
check("工具调用 20 次", len(starts) == 20, len(starts))
check("状态是 max_rounds", done["status"] == "max_rounds", done["status"])
check("轮次用尽有提示", any("20 轮" in (e.get("reason") or "") for e in limits), limits[-1:])
check("round 事件带 max_rounds=20", rounds[-1].get("max_rounds") == 20, rounds[-1])

print("== 6. 单轮 6 个工具（旧上限是 3）==")
six = {"tool_calls": [{"id": "c%d" % i, "name": "list_uploaded_files", "arguments": "{}"}
                      for i in range(6)]}
events, done = run([six, {"tokens": ["够了"]}], allow_code_edit=True)
check("6 个全部接受", len([e for e in events if e["type"] == "tool_start"]) == 6,
      len([e for e in events if e["type"] == "tool_start"]))
check("没有单轮工具数超限提示",
      not any("单轮工具数" in (e.get("reason") or "") for e in events if e["type"] == "limit"))

events, done = run([six, {"tokens": ["够了"]}], allow_code_edit=False)
check("问答模式仍然只接受 3 个",
      len([e for e in events if e["type"] == "tool_start"]) == 3,
      len([e for e in events if e["type"] == "tool_start"]))
check("问答模式给出超限提示",
      any("单轮工具数超过上限（3）" in (e.get("reason") or "")
          for e in events if e["type"] == "limit"))

print("== 7. 上下文裁剪必须保住当前问题 ==")
question = "这是用户的当前问题，绝对不能被裁掉"
filler = "x" * 2000
msgs = [{"role": "system", "content": "s" * 100}, {"role": "user", "content": question}]
for i in range(10):
    msgs.append({"role": "assistant", "content": "", "tool_calls": [
        {"id": "t%d" % i, "type": "function",
         "function": {"name": "read_file", "arguments": "{}"}}]})
    msgs.append({"role": "tool", "tool_call_id": "t%d" % i, "content": filler})

fitted = agent._fit_context(list(msgs), 3000)
texts = [str(m.get("content") or "") for m in fitted]
check("裁剪确实发生", len(fitted) < len(msgs), (len(fitted), len(msgs)))
check("当前问题还在", any(question in t for t in texts))
check("保留了 system", fitted[0].get("role") == "system")
check("第一条不是历史残留", fitted[1].get("content") == question)

# 极端场景：最近一组本身就超上限，旧实现会把问题一起丢掉
huge = [{"role": "system", "content": "s" * 100}, {"role": "user", "content": question}]
huge.append({"role": "assistant", "content": "", "tool_calls": [
    {"id": "h1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
huge.append({"role": "tool", "tool_call_id": "h1", "content": "y" * 9000})
tight = agent._fit_context(list(huge), 2000)
check("最近一组超上限时仍保住问题",
      any(str(m.get("content")) == question for m in tight), tight)

print("== 8. 未超上限时原样返回 ==")
small = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
check("不裁剪时对象原样返回", agent._fit_context(list(small), 24000) == small)

failed = sum(1 for _, ok in results if not ok)
print("\n结果：%d 通过 / %d 失败" % (len(results) - failed, failed))
sys.exit(1 if failed else 0)
