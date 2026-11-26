"""学习沙箱的离线测试：运行时切根、建项目、跑验收、学习模式的权限边界。

全部用临时目录（AGENT_WORKSPACE / AGENT_PROJECTS / 临时 db），
跑完删干净，不碰仓库和真实的 projects/。
"""

import json
import os
import shutil
import sys
import tempfile
import time

TEMP = tempfile.mkdtemp(prefix="test_sandbox_")
WS = os.path.join(TEMP, "ws")
os.makedirs(WS, exist_ok=True)
os.environ["AGENT_WORKSPACE"] = WS
os.environ["AGENT_PROJECTS"] = os.path.join(TEMP, "projects")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402

database.DB_PATH = os.path.join(TEMP, "sandbox.db")
database.init_db()

import agent  # noqa: E402
import checks  # noqa: E402
import envs  # noqa: E402
import projects  # noqa: E402
import main as app_main  # noqa: E402
from tools import _guard, get_tool  # noqa: E402

FAILED = []
PASSED = []
COURSE = "agent-dev"


def check(name, ok, detail=""):
    if ok:
        PASSED.append(name)
        return
    FAILED.append(name)
    print("  FAIL %s  → %s" % (name, detail))


def reset():
    conn = database.sqlite3.connect(database.DB_PATH)
    for table in ("projects", "course_progress", "quiz_attempts", "chats", "messages", "agent_runs"):
        try:
            conn.execute("DELETE FROM %s" % table)
        except database.sqlite3.Error:
            pass
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- 1. 运行时切根

def test_guard_roots():
    print("[1] 运行时登记根")
    check("默认活动根是基础根", os.path.normcase(_guard.active_root()) == os.path.normcase(WS),
          _guard.active_root())

    extra = os.path.join(TEMP, "extra_root")
    os.makedirs(extra, exist_ok=True)
    try:
        _guard.use_root(extra)
        check("未登记目录不能切换", False)
    except ValueError:
        check("未登记目录不能切换", True)

    _guard.register_root(extra)
    check("登记后可见于列表", any(os.path.normcase(p) == os.path.normcase(extra)
                                  for p in _guard.registered_roots()))

    token = _guard.use_root(extra)
    check("切换生效", os.path.normcase(_guard.active_root()) == os.path.normcase(extra))
    # 切到 extra 之后，基础根里的文件应该越界
    with open(os.path.join(WS, "only_in_ws.txt"), "w", encoding="utf-8") as fh:
        fh.write("x")
    # resolve 不校验存在性（那是 read_file 的事），所以用 rel_inside 看边界
    try:
        _guard.rel_inside(os.path.join(WS, "only_in_ws.txt"))
        check("切根后基础根不可见", False)
    except ValueError:
        check("切根后基础根不可见", True)
    _guard.reset_root(token)
    check("reset 后回到基础根", os.path.normcase(_guard.active_root()) == os.path.normcase(WS))
    check("reset 后可读基础根", os.path.isfile(_guard.resolve("only_in_ws.txt")))
    _guard.unregister_root(extra)
    check("注销后不再可切换", not _guard.is_registered_root(extra))


# ---------------------------------------------------------------- 2. 建项目

def test_create():
    print("[2] 建项目")
    reset()
    row = projects.create("ReAct 解析器", "s4c1", COURSE)
    check("返回项目行", bool(row and row["id"]), row)
    check("目录存在", os.path.isdir(row["path"]), row["path"])
    check("slug 是 ascii", row["slug"].isascii(), row["slug"])
    check("落点在项目根内",
          os.path.normcase(row["path"]).startswith(
              os.path.normcase(projects.PROJECTS_ROOT) + os.sep), row["path"])
    check("README 写了章节任务", "parse_react" in open(
        os.path.join(row["path"], "README.md"), encoding="utf-8").read())
    check("README 写了章节标题", "第 11 章" in open(
        os.path.join(row["path"], "README.md"), encoding="utf-8").read())
    check("建了 .gitignore", os.path.isfile(os.path.join(row["path"], ".gitignore")))

    # 同名再来一个：不能覆盖，也不能失败
    second = projects.create("ReAct 解析器", "s4c1", COURSE)
    check("同名项目另起目录", second["path"] != row["path"], second["path"])
    check("同名项目加后缀", second["slug"].endswith("-2"), second["slug"])

    decorated = projects.decorate(row)
    check("decorate 带 exists", decorated["exists"] is True)
    check("decorate 带验收标签", decorated["check_label"] == "还没验收", decorated["check_label"])
    check("decorate 不暴露绝对路径", not decorated["shown_path"].startswith("D:"),
          decorated["shown_path"])

    for args, note in (
        (("", "s4c1"), "空名字"),
        (("x" * 41, "s4c1"), "名字过长"),
        (("x", "../../etc"), "章节 id 带 .."),
        (("x", "s4c1", "../.."), "课程 id 带 .."),
    ):
        try:
            projects.create(*args)
            check("拒绝%s" % note, False)
        except projects.ProjectError:
            check("拒绝%s" % note, True)

    tree = projects.tree(row["id"])
    names = [entry["path"] for entry in tree["entries"]]
    check("目录树列出 README", "README.md" in names, names)


# ---------------------------------------------------------------- 3. 验收

GOOD = {
    "s4c1": '''
import re

_LABEL = re.compile(r"^\\s*(thought|action\\s*input|action)\\s*:\\s*(.*)$", re.IGNORECASE)

def parse_react(text):
    out = {"thought": "", "action": "", "action_input": ""}
    field = None
    for line in (text or "").splitlines():
        m = _LABEL.match(line)
        if m:
            key = m.group(1).lower().replace(" ", "")
            field = {"thought": "thought", "action": "action", "actioninput": "action_input"}[key]
            out[field] = m.group(2).strip()
        elif field:
            out[field] = (out[field] + "\\n" + line).strip()
    return out
''',
    "s5c1": '''
def trim_conversation(messages):
    kept = []
    for m in messages:
        if m["role"] == "user" and not any(k in m["content"] for k in ("你好", "谢谢")):
            kept.append(m)
            break
    for m in messages:
        if m["role"] == "assistant" and "```" in m["content"]:
            kept.append(m)
            break
    return kept
''',
    "s8c2": '''
import re

def price_check(text):
    return bool(re.search(r"\\d+(?:\\.\\d+)?元", str(text)))
''',
}

BAD = {
    "s4c1": "def parse_react(text):\n    return text.split(',')\n",
    "s5c1": "def trim_conversation(messages):\n    return messages\n",
    "s8c2": 'def price_check(text):\n    return "True"\n',
}


def write_module(project, source):
    with open(os.path.join(project["path"], "main.py"), "w", encoding="utf-8") as fh:
        fh.write(source)


def test_checks():
    print("[3] 验收执行器")
    check("s4c1 有脚本", checks.available("s4c1", COURSE))
    check("不存在的章没脚本", not checks.available("s99c9", COURSE))
    check("脚本清单不含 _kit", "_kit" not in checks.list_chapters(COURSE),
          checks.list_chapters(COURSE))
    check("33 章都有脚本", len(checks.list_chapters(COURSE)) == 33,
          checks.list_chapters(COURSE))

    for chapter, good, bad in ((c, GOOD[c], BAD[c]) for c in ("s4c1", "s5c1", "s8c2")):
        row = projects.create("check " + chapter, chapter, COURSE)
        write_module(row, good)
        passed = checks.run(row["path"], chapter)
        check("%s 正确实现通过" % chapter, passed["ok"], passed.get("items"))
        check("%s 通过时带回条数" % chapter, passed["total"] > 0, passed.get("total"))

        write_module(row, bad)
        failed = checks.run(row["path"], chapter)
        check("%s 错误实现不通过" % chapter, not failed["ok"], failed.get("items"))
        check("%s 指出具体哪条" % chapter,
              all(item["detail"] for item in failed["items"] if not item["ok"]),
              failed.get("items"))

    # 没有 main.py：只出一条「找得到你的代码」，而不是整页崩掉
    empty = projects.create("empty", "s4c1", COURSE)
    missing = checks.run(empty["path"], "s4c1")
    check("没代码时给一条明确失败", missing["total"] == 1 and not missing["items"][0]["ok"],
          missing["items"])

    # 语法错误
    broken = projects.create("broken", "s4c1", COURSE)
    write_module(broken, "def parse_react(:\n    pass\n")
    result = checks.run(broken["path"], "s4c1")
    check("语法错误不抛异常", not result["ok"], result)
    check("语法错误有提示", "import" in result["items"][0]["name"], result["items"])

    # 死循环：必须被杀掉，而不是把测试挂死
    loop = projects.create("loop", "s4c1", COURSE)
    write_module(loop, "while True:\n    pass\n")
    started = time.monotonic()
    result = checks.run(loop["path"], "s4c1", timeout=5)
    elapsed = time.monotonic() - started
    check("死循环被超时终止", result["timed_out"] is True, result)
    check("超时不是「通过」", not result["ok"])
    check("超时在 20 秒内返回", elapsed < 20, elapsed)

    # s3c1 看的是文件，不是模块
    tool_call = projects.create("toolcall", "s3c1", COURSE)
    no_file = checks.run(tool_call["path"], "s3c1")
    check("没 tool_call.json 不通过", not no_file["ok"], no_file)
    with open(os.path.join(tool_call["path"], "tool_call.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": "get_weather", "arguments": json.dumps({"city": "杭州,苏州"})},
                  fh, ensure_ascii=False)
    ok_file = checks.run(tool_call["path"], "s3c1")
    check("正确的 tool_call.json 通过", ok_file["ok"], ok_file.get("items"))
    with open(os.path.join(tool_call["path"], "tool_call.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": "get_weather", "arguments": {"city": "杭州"}}, fh, ensure_ascii=False)
    nested = checks.run(tool_call["path"], "s3c1")
    check("arguments 写成对象要判错", not nested["ok"], nested.get("items"))

    # 缺脚本
    try:
        checks.run(tool_call["path"], "s99c9")
        check("缺脚本抛 CheckError", False)
    except checks.CheckError:
        check("缺脚本抛 CheckError", True)
    try:
        checks.run(tool_call["path"], "../s4c1")
        check("章节 id 不能拼路径", False)
    except checks.CheckError:
        check("章节 id 不能拼路径", True)


# ---------------------------------------------------------------- 4. 沙箱边界

def test_sandbox_tools():
    print("[4] 沙箱内的工具边界")
    reset()
    row = projects.create("boundary", "s4c1", COURSE)
    with open(os.path.join(row["path"], "secret.txt"), "w", encoding="utf-8") as fh:
        fh.write("在项目里")
    with open(os.path.join(WS, "repo_file.txt"), "w", encoding="utf-8") as fh:
        fh.write("在仓库里")

    with projects.sandbox(row["id"]):
        check("读项目内文件", get_tool("read_file").invoke(
            {"path": "secret.txt"}, {}).ok)
        outside = get_tool("read_file").invoke({"path": "../repo_file.txt"}, {})
        check("读不到仓库文件", not outside.ok, outside.error)
        check("写项目内文件", get_tool("write_file").invoke(
            {"path": "new.py", "content": "print(1)"}, {"allow_code_edit": True}).ok)
        escape = get_tool("write_file").invoke(
            {"path": "../escape.py", "content": "x"}, {"allow_code_edit": True})
        check("写不出项目", not escape.ok, escape.error)
        check("越界提示说明在哪个根", "当前工作区" in (escape.error or ""), escape.error)
        cmd = get_tool("run_command").invoke(
            {"command": "python -c \"print(2)\""}, {"allow_command": True, "approved_commands": set()})
        # python -c 是 ask 级，没有审批就该被拒——这条验的是「沙箱没有放松审批」
        check("沙箱不绕过命令审批", not cmd.ok, cmd.content[:80])

    # 白名单命令（cat / head / ls）是免审批直接跑的，`cat ../x` 照样能读到项目外。
    # 沙箱承诺「只看得到这个目录」，所以这类参数必须挡掉。
    from tools.command import classify_command
    with projects.sandbox(row["id"]):
        check("沙箱里 cat ../ 被拒", classify_command("cat ../repo_file.txt")[0] == "deny",
              classify_command("cat ../repo_file.txt"))
        check("沙箱里绝对路径被拒", classify_command("python C:/x/a.py")[0] == "deny",
              classify_command("python C:/x/a.py"))
        check("沙箱里普通命令不受影响", classify_command("ls .")[0] == "auto",
              classify_command("ls ."))
    check("出了沙箱不再限制 ..", classify_command("cat ../repo_file.txt")[0] == "auto",
          classify_command("cat ../repo_file.txt"))

    check("出了沙箱还原活动根", os.path.normcase(_guard.active_root()) == os.path.normcase(WS))

    # 同级项目互相不可见
    other = projects.create("other", "s4c1", COURSE)
    with projects.sandbox(row["id"]):
        peek = get_tool("read_file").invoke({"path": os.path.relpath(
            os.path.join(other["path"], "README.md"), row["path"])}, {})
        check("同级项目不可见", not peek.ok, peek.error)


# ---------------------------------------------------------------- 5. 学习模式的权限

def _fake_model(captured):
    def fake(messages, tools=None, timeout=30.0, model=None, should_stop=None, **kwargs):
        captured.append({
            "tools": sorted(t["function"]["name"] for t in (tools or [])),
            "system": messages[0].get("content", ""),
        })
        yield ("message", {"content": "好的", "tool_calls": [], "usage": 1, "cancelled": False})
    return fake


def test_agent_gate():
    print("[5] 学习模式的工具边界")
    reset()
    row = projects.create("gate", "s4c1", COURSE)

    captured = []
    agent.chat_completion_stream = _fake_model(captured)
    with projects.sandbox(row["id"]):
        agent.run_agent("我在写 parse_react", mode="tutor", course_id=COURSE,
                        chapter_id="s4c1", project_id=str(row["id"]))
    used = captured[-1]["tools"]
    check("沙箱里能看到读工具", {"read_file", "grep_code", "list_dir"} <= set(used), used)
    check("沙箱里能写", "write_file" in used, used)
    check("沙箱里能跑命令", "run_command" in used, used)
    check("提示词写了项目目录", "parse_react" in captured[-1]["system"] or "ReAct" in captured[-1]["system"]
          or "gate" in captured[-1]["system"], captured[-1]["system"][-400:])
    check("提示词说明边界", "被拒绝时不要换路径" in captured[-1]["system"],
          captured[-1]["system"][-300:])

    # 没有项目：即使请求里把 allow_code_edit / allow_command 都打开，也不给文件工具
    captured.clear()
    agent.run_agent("讲讲这一章", mode="tutor", course_id=COURSE, chapter_id="s4c1",
                    allow_code_edit=True, allow_command=True)
    used = captured[-1]["tools"]
    forbidden = {"read_file", "write_file", "edit_file", "run_command", "grep_code", "list_dir"}
    check("无项目时不给文件工具", not (forbidden & set(used)), used)
    check("无项目时有记忆检索", "recall_memory" in used, used)

    # 普通对话模式仍然按开关走
    captured.clear()
    agent.run_agent("你好", mode="chat")
    check("普通模式下默认无命令", "run_command" not in captured[-1]["tools"])


# ---------------------------------------------------------------- 6. HTTP

def _client():
    from fastapi.testclient import TestClient
    client = TestClient(app_main.app)
    client.cookies.set(app_main.SESSION_COOKIE, app_main.create_session_token())
    return client


def test_http():
    print("[6] 接口")
    reset()
    anon = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app_main.app)
    check("匿名访问项目列表 401", anon.get("/projects").status_code == 401)

    api = _client()
    listing = api.get("/projects")
    check("列表 200", listing.status_code == 200, listing.text[:120])
    check("列表返回全部可验收章", set(listing.json()["checkable"]) == set(checks.list_chapters(COURSE)),
          listing.json()["checkable"])
    brief = api.get("/projects?chapter_id=s8c1").json().get("brief") or ""
    check("列表带上这一章的验收要求", "extract_test_case" in brief, brief[:60])
    check("不带章 id 时不返回验收要求", api.get("/projects").json().get("brief") == "")

    created = api.post("/projects", json={"name": "接口建的项目", "chapter_id": "s4c1"})
    check("建项目 201/200", created.status_code == 200, created.text[:160])
    project = created.json()
    check("返回 id 与路径", project.get("id") and project.get("path"), project)

    detail = api.get("/projects/%s" % project["id"])
    check("详情带 tree", detail.status_code == 200 and "tree" in detail.json(), detail.text[:120])
    check("详情带 brief", "parse_react" in detail.json()["brief"])
    check("详情带 check_available", detail.json()["check_available"] is True)

    empty_check = api.post("/projects/%s/check" % project["id"])
    check("空项目验收返回 200", empty_check.status_code == 200, empty_check.text[:160])
    check("空项目验收不通过", empty_check.json()["ok"] is False, empty_check.json())

    with open(os.path.join(project["path"], "main.py"), "w", encoding="utf-8") as fh:
        fh.write(GOOD["s4c1"])
    good = api.post("/projects/%s/check" % project["id"])
    check("填对后验收通过", good.status_code == 200 and good.json()["ok"], good.text[:200])
    check("结果写回项目行", good.json()["project"]["check_ok"] is True, good.json()["project"])
    check("列表能看到上次结果", api.get("/projects").json()["items"][0]["check_label"].startswith("验收通过"),
          api.get("/projects").json()["items"][0]["check_label"])

    no_script = api.post("/projects", json={"name": "没脚本的章", "chapter_id": "s99c9"})
    run_no = api.post("/projects/%s/check" % no_script.json()["id"])
    check("没脚本返回 404", run_no.status_code == 404, run_no.text[:120])

    # 项目环境：这里不真建（十几秒），造一个「已经存在」的 venv 验接口形状。
    # 真建 venv 与「验收跑在 venv 里」在 test_project_env.py 里验。
    fake_py = envs._candidate_pythons(project["path"])[0]
    os.makedirs(os.path.dirname(fake_py), exist_ok=True)
    open(fake_py, "w").close()

    env_resp = api.post("/projects/%s/env" % project["id"])
    check("准备环境 200", env_resp.status_code == 200, env_resp.text[:160])
    check("已有环境时 created=False", env_resp.json().get("created") is False, env_resp.json())
    check("接口返回里项目行带 env", env_resp.json()["project"]["env"]["present"] is True,
          env_resp.json()["project"])
    # 按 id 找，不要假设它是列表第一项：上面已经建了第二个项目
    listed = {item["id"]: item for item in api.get("/projects").json()["items"]}
    check("列表也带 env", listed[project["id"]]["env"]["present"] is True,
          listed[project["id"]].get("env"))

    no_req = api.post("/projects/%s/deps" % project["id"])
    check("没有清单时装依赖 400", no_req.status_code == 400, no_req.text[:160])
    check("400 里说明缺什么", "requirements.txt" in no_req.text, no_req.text[:200])

    with open(os.path.join(project["path"], "requirements.txt"), "w", encoding="utf-8") as fh:
        fh.write("-e .\n")
    bad_req = api.post("/projects/%s/deps" % project["id"])
    check("清单不合法时 400", bad_req.status_code == 400, bad_req.text[:200])
    check("400 指到第几行", "第 1 行" in bad_req.text, bad_req.text[:200])

    check("环境接口对不存在的项目 404", api.post("/projects/99999/env").status_code == 404)
    check("依赖接口对不存在的项目 404", api.post("/projects/99999/deps").status_code == 404)

    check("不存在的项目 404", api.get("/projects/99999").status_code == 404)
    check("坏名字 400", api.post("/projects", json={"name": ""}).status_code == 422)

    removed = api.delete("/projects/%s" % project["id"])
    check("移除登记 200", removed.status_code == 200, removed.text[:120])
    check("移除后目录还在", os.path.isdir(project["path"]))
    remaining = [item["id"] for item in api.get("/projects").json()["items"]]
    check("移除后不在列表里", project["id"] not in remaining, remaining)

    # 对话：项目 id 的门槛
    chat_id = database.create_chat("沙箱测试")
    check("项目 id 不存在的对话 404",
          api.post("/chat", json={"text": "在吗", "chat_id": chat_id, "mode": "tutor",
                                  "chapter_id": "s4c1", "project_id": 99999}).status_code == 404)
    fresh = api.post("/projects", json={"name": "对话项目", "chapter_id": "s4c1"}).json()
    check("章节不匹配 400",
          api.post("/chat", json={"text": "在吗", "chat_id": chat_id, "mode": "tutor",
                                  "chapter_id": "s5c1", "project_id": fresh["id"]}).status_code == 400)
    check("非学习模式带项目 400",
          api.post("/chat", json={"text": "在吗", "chat_id": chat_id, "mode": "chat",
                                  "project_id": fresh["id"]}).status_code == 400)


def test_http_chat_sandbox():
    """真实走一遍 /chat：切根发生在请求内，工具和提示词都按项目来。"""
    print("[7] 对话里的沙箱")
    reset()
    row = projects.create("chat sandbox", "s4c1", COURSE)
    chat_id = database.create_chat("沙箱对话")
    captured = []
    agent.chat_completion_stream = _fake_model(captured)
    api = _client()
    response = api.post("/chat", json={
        "text": "我在写 parse_react，帮我看看", "chat_id": chat_id,
        "mode": "tutor", "chapter_id": "s4c1", "project_id": row["id"],
    })
    check("对话 200", response.status_code == 200, response.text[:200])
    used = captured[-1]["tools"]
    check("对话里注册了写工具", "write_file" in used, used)
    check("对话里注册了命令", "run_command" in used, used)
    check("提示词带上项目名", "chat sandbox" in captured[-1]["system"], captured[-1]["system"][-300:])
    check("请求结束后活动根还原", os.path.normcase(_guard.active_root()) == os.path.normcase(WS))


def main():
    test_guard_roots()
    test_create()
    test_checks()
    test_sandbox_tools()
    test_agent_gate()
    test_http()
    test_http_chat_sandbox()
    print("\n结果：%d 通过 / %d 失败" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("失败项：%s" % FAILED)
    shutil.rmtree(TEMP, ignore_errors=True)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
