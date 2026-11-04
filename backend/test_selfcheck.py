# -*- coding: utf-8 -*-
"""自测回路自测：判据、时序、拦截次数、门控与事件。

不调真实模型（chat_completion_stream 被替换），但**命令是真的跑**——
这条回路的全部意义就是相信真实退出码，用假数据测它等于什么都没测。

重点盯三件事：
1. 什么算「验证过」——`ls`、`git status` 退出码也是 0，但什么都没证明；
2. 先跑测试再改代码不算数，用序号而不是布尔量记状态；
3. 只拦一次——用户拒绝审批、项目确实没测试时，拦第二次只是白烧轮次。

运行：
    cd backend
    ..\\.venv\\Scripts\\python.exe test_selfcheck.py
"""

import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

TEMP_ROOT = os.path.join(tempfile.gettempdir(), "ai_robot_selfcheck_test")
if os.path.isdir(TEMP_ROOT):
    shutil.rmtree(TEMP_ROOT, ignore_errors=True)
os.makedirs(os.path.join(TEMP_ROOT, "src"), exist_ok=True)

# 沙箱先指向临时目录再导入：端到端用例会真的写文件、真的跑命令。
from tools import _guard  # noqa: E402

_guard.WORKSPACE_ROOT = os.path.realpath(TEMP_ROOT)

import agent  # noqa: E402
import database  # noqa: E402
from selfcheck import SelfCheck, is_verification  # noqa: E402
from tools import command as command_module  # noqa: E402

_TMP_DB = os.path.join(tempfile.gettempdir(), "ai_robot_selfcheck_test.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)
database.DB_PATH = _TMP_DB
database.init_db()

# 端到端用例里把命令一律放行：这里测的是「改完有没有验证」，
# 命令分级本身由 test_command_tools.py 覆盖，不需要在这里重复等审批。
command_module.classify_command = lambda text, cwd=".": ("auto", "")
agent.classify_command = lambda text, cwd=".": ("auto", "")

results = []


def check(label, cond, detail=""):
    results.append((label, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  -> " + str(detail)[:140]) if detail else ""))


print("\n[1] 什么算验证过")
check("测试跑通算", is_verification("python -m pytest tests -q", 0))
check("跑脚本退出 0 算", is_verification("python scripts/check.py", 0))
check("编译检查算", is_verification("python -m compileall src/a.py", 0))
check("npm test 算", is_verification("npm test", 0))
check("ls 不算", not is_verification("ls -l", 0))
check("cat 不算", not is_verification("cat src/a.py", 0))
check("git status 不算", not is_verification("git status", 0))
check("git diff 不算", not is_verification("git diff", 0))
check("git log 不算", not is_verification("git log --oneline", 0))
check("非零退出码不算", not is_verification("python -m pytest tests -q", 1))
check("超时不算", not is_verification("python -m pytest", 0, timed_out=True))
check("空命令不算", not is_verification("   ", 0))

print("\n[2] 状态时序")
box = SelfCheck()
check("没改代码不需要验证", not box.needs_check)
box.note_edit("src/a.py")
check("改了代码就需要验证", box.needs_check)
check("还没验证", not box.verified)
box.note_command("ls", 0)
check("跑 ls 不算验证", box.needs_check)
box.note_command("python -m compileall src/a.py", 0)
check("跑通编译后不再需要验证", not box.needs_check)
check("标记为已验证", box.verified)
box.note_edit("src/b.py")
check("又改了新文件，需要重新验证", box.needs_check)
box.note_command("python -m pytest", 1)
check("测试失败不算验证", box.needs_check)

print("\n[3] 先验证后改代码不算数")
early = SelfCheck()
early.note_command("python -m pytest -q", 0)
early.note_edit("src/a.py")
check("改动晚于验证，仍需验证", early.needs_check)
early.note_command("python -m pytest -q", 0)
check("改动后再跑一次才算", not early.needs_check)

print("\n[4] 哪些改动才要验证")
docs = SelfCheck()
docs.note_edit("README.md")
docs.note_edit("docs/guide.html")
check("只改文档不要求验证", not docs.needs_check)
check("文档改动不记进文件清单", not docs.code_edits, docs.code_edits)
mixed = SelfCheck()
mixed.note_edit("frontend/style.css")
mixed.note_edit("backend/api.py")
check("混着改时按代码算", mixed.needs_check)
check("文件清单只留代码文件", list(mixed.code_edits) == ["backend/api.py"], mixed.code_edits)
check("路径分隔符归一", "backend/api.py" in mixed.code_edits)

print("\n[5] 提醒文本")
many = SelfCheck()
for index in range(6):
    many.note_edit("src/m%d.py" % index)
brief = many.reminder()
check("提醒里点名改了什么文件", "src/m0.py" in brief, brief[:90])
check("文件多时折叠", "等 6 个文件" in brief, brief[:120])
check("提醒明确要求先验证", "收尾前必须先验证" in brief)
# 实测模型的第一选择是 python -c：它要人点确认，人不在就白等满 120 秒。
# 提醒里必须点名哪条路免确认，否则回路能走通但要多花两分钟。
check("提醒点名免确认的命令", "compileall" in brief and "不需要用户确认" in brief, brief[-160:])
check("提醒劝退 python -c", "不要用它做验证" in brief, brief[-160:])
check("拦截消息更强硬", "不能给最终回答" in many.interrupt_prompt())
check("拦截消息劝退 python -c", "不要用 python -c" in many.interrupt_prompt())
check("拦截消息说明兜底", "未验证" in many.interrupt_prompt())

print("\n[6] 门控：没开命令执行就绝不拦")
bare = SelfCheck()
bare.note_edit("src/a.py")
check("未开命令执行不拦", not bare.should_interrupt(False))
check("开了命令执行就拦", bare.should_interrupt(True))
bare.interrupted = True
check("拦过一次不再拦", not bare.should_interrupt(True))
check("没改代码不拦", not SelfCheck().should_interrupt(True))
done_box = SelfCheck()
done_box.note_edit("src/a.py")
done_box.note_command("python -m pytest -q", 0)
check("已验证不拦", not done_box.should_interrupt(True))

print("\n[7] summary 结构")
sum_box = SelfCheck()
sum_box.note_edit("src/a.py")
sum_box.note_edit("src/b.py")
sum_box.note_command("python -m compileall src", 0)
sum_box.note_command("ls", 0)
summary = sum_box.summary()
check("带已改文件", summary["edited"] == ["src/a.py", "src/b.py"], summary["edited"])
check("带是否待验证", summary["pending"] is False, summary)
check("带验证结论", summary["verified"] is True, summary)
check("证据只留验证类命令", summary["proof"] == ["python -m compileall src"], summary["proof"])
check("未拦过时 interrupted 为 False", summary["interrupted"] is False)


# ---------------------------------------------------------------- 端到端

def make_fake(turns, captured=None):
    box = captured if captured is not None else []

    def fake(messages, tools=None, timeout=30.0, model=None, should_stop=None, **kwargs):
        box.append({
            "system": messages[0].get("content", ""),
            "roles": [m.get("role") for m in messages],
        })
        turn = turns[min(len(box) - 1, len(turns) - 1)]
        yield ("message", dict(turn))
    return fake


def call(name, args, call_id="c1"):
    return {"id": call_id, "name": name, "arguments": json.dumps(args, ensure_ascii=False)}


def turn(calls, content=""):
    return {"content": content, "usage": 1, "cancelled": False, "tool_calls": calls}


print("\n[8] 端到端：改完不验证会被拦回去")
turns = [
    turn([call("write_file", {"path": "src/ok.py", "content": "def a():\n    return 1\n"})]),
    turn([], "改好了，可以用了。"),
    turn([call("run_command", {"command": "python -m compileall src/ok.py"}, "c2")]),
    turn([], "已验证：compileall 退出码 0。"),
]
captured = []
agent.chat_completion_stream = make_fake(turns, captured)
events = list(agent.run_agent_stream("改一下 ok.py", mode="chat", allow_code_edit=True, allow_command=True))
types = [e["type"] for e in events]
done = [e for e in events if e["type"] == "done"][0]

gates = [e for e in events if e["type"] == "verify_required"]
check("发了 verify_required 事件", len(gates) == 1, types)
check("事件点名改过的文件", gates and gates[0]["files"] == ["src/ok.py"], gates[:1])
check("事件排在 tool_end 之后", types.index("verify_required") > types.index("tool_end"), types)
check("拦下后模型重新调用工具", types.count("tool_start") == 2, types)
check("步骤里有 verify_gate", any(s.get("kind") == "verify_gate" for s in done["steps"]),
      [s.get("kind") for s in done["steps"]])
check("最终状态是 ok", done["status"] == "ok", done["status"])
check("self_check 说已不再待验证", done["self_check"]["pending"] is False, done["self_check"])
check("self_check 记下验证命令", done["self_check"]["proof"] == ["python -m compileall src/ok.py"],
      done["self_check"]["proof"])
check("草稿没进最终回答", "改好了，可以用了" not in done["reply"], done["reply"][:80])
check("最终回答是验证后那版", "退出码 0" in done["reply"], done["reply"][:80])
check("被拦那轮的系统提示里有自测提醒", any("自测提醒" in item["system"] for item in captured),
      [item["system"][-40:] for item in captured])

print("\n[9] 端到端：第二次还是不验证就放行并标注")
turns = [
    turn([call("write_file", {"path": "src/lazy.py", "content": "def b():\n    return 2\n"})]),
    turn([], "改完了。"),
    turn([], "项目里没有可跑的命令，就这样。"),
]
agent.chat_completion_stream = make_fake(turns, [])
events = list(agent.run_agent_stream("改 lazy.py", mode="chat", allow_code_edit=True, allow_command=True))
done = [e for e in events if e["type"] == "done"][0]
check("只拦一次", len([e for e in events if e["type"] == "verify_required"]) == 1,
      [e["type"] for e in events])
check("如实标注待验证", done["self_check"]["pending"] is True, done["self_check"])
check("标注拦过一次", done["self_check"]["interrupted"] is True, done["self_check"])
check("回答照常给出", "就这样" in done["reply"], done["reply"][:80])

print("\n[10] 端到端：没开命令执行时不拦")
turns = [
    turn([call("write_file", {"path": "src/nocmd.py", "content": "def c():\n    return 3\n"})]),
    turn([], "改好了，但我没法跑测试。"),
]
agent.chat_completion_stream = make_fake(turns, [])
events = list(agent.run_agent_stream("改 nocmd.py", mode="chat", allow_code_edit=True))
done = [e for e in events if e["type"] == "done"][0]
check("不发 verify_required", not [e for e in events if e["type"] == "verify_required"],
      [e["type"] for e in events])
check("仍然标注未验证", done["self_check"]["pending"] is True, done["self_check"])
check("一轮就收尾", done["rounds"] == 2, done["rounds"])

print("\n[11] 端到端：只改文档不触发")
turns = [
    turn([call("write_file", {"path": "NOTES.md", "content": "# 笔记\n"})]),
    turn([], "写好了。"),
]
agent.chat_completion_stream = make_fake(turns, [])
events = list(agent.run_agent_stream("写个笔记", mode="chat", allow_code_edit=True, allow_command=True))
done = [e for e in events if e["type"] == "done"][0]
check("不发 verify_required", not [e for e in events if e["type"] == "verify_required"],
      [e["type"] for e in events])
check("文档不算待验证", done["self_check"]["pending"] is False, done["self_check"])
check("文件清单为空", done["self_check"]["edited"] == [], done["self_check"])

print("\n[12] 端到端：命令失败后修好再验证")
turns = [
    turn([call("write_file", {"path": "src/bad.py", "content": "def d(:\n    pass\n"})]),
    turn([call("run_command", {"command": "python -m compileall src/bad.py"}, "c2")]),
    turn([call("write_file", {"path": "src/bad.py", "content": "def d():\n    pass\n"}, "c3")]),
    turn([call("run_command", {"command": "python -m compileall src/bad.py"}, "c4")]),
    turn([], "语法错误已修，compileall 通过。"),
]
agent.chat_completion_stream = make_fake(turns, [])
events = list(agent.run_agent_stream("修 bad.py", mode="chat", allow_code_edit=True, allow_command=True))
done = [e for e in events if e["type"] == "done"][0]
check("没有误拦（它自己验证了）", not [e for e in events if e["type"] == "verify_required"],
      [e["type"] for e in events])
check("修好后编译通过，不再待验证", done["self_check"]["pending"] is False,
      done["self_check"])
check("证据是最后那次成功", done["self_check"]["proof"] == ["python -m compileall src/bad.py"],
      done["self_check"]["proof"])


passed = sum(1 for _, ok in results if ok)
total = len(results)
print("\n" + "-" * 46)
print("通过 %d / %d" % (passed, total))
if passed != total:
    print("失败项：")
    for label, ok in results:
        if not ok:
            print("  - " + label)
sys.exit(0 if passed == total else 1)
