"""桌面操作工具的离线测试：闸门严不严、参数挡不挡得住、Agent 有没有真接上。

最重要的一条约束：**这个文件跑起来不能真的动鼠标键盘**。所有落地点
（_launch_url / _press_combo / _paste_text / _click_at / _grab_screen / _ask_vision）
都在测试里被替换成记录器，断言的是「该调用的被调用了、不该调的没被调用」。

真正需要人眼确认的部分（浏览器有没有弹出来、点得准不准）只能手动验，
这里不假装测过。
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database
import tools
from tools import _guard, desktop

PASS = 0
FAIL = 0
# 每个动作落到哪里，测试结束时能看清顺序
CALLS = []


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, detail))


def fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    database.DB_PATH = tmp.name
    database.init_db()


def invoke(name, args=None, **flags):
    """直接调工具，模拟「绕过 agent 的调用方」。默认给足授权，除非测试显式关掉。"""
    clear_session()
    context = {"run_id": "test-desktop"}
    context.update(flags)
    context.setdefault("allow_desktop", True)
    tool = tools.get_tool(name)
    assert tool is not None, "工具未注册：%s" % name
    return tool.invoke(args or {}, context)


def clear_session():
    """每个用例从干净状态开始。

    真实使用里会话锁靠「跑完主动释放 + 90 秒超时」管理；测试里各用例用不同的 run_id，
    不清的话后一个用例会被前一个的锁挡住，测的就不是它本来要测的东西。
    锁本身的语义由 test_session_lock 验，那里不走这个 helper。
    """
    try:
        os.remove(desktop._lock_path())
    except OSError:
        pass


# 真正的视觉函数在 stub_backends 之前存一份：那个测试要验的是换算逻辑，
# 不能用假桩，否则测的是桩自己。
REAL_ASK_VISION = desktop._ask_vision


def stub_backends():
    """把所有真实动作换成记录器。"""
    # 截图、动作日志、会话锁全部落到临时目录：跑测试不该往真实的 desktop_shots 里写，
    # 更不能在那里留下锁文件——那会把用户自己的桌面操作挡在门外。
    desktop.SHOT_DIR = tempfile.mkdtemp(prefix="desk-shots-")
    desktop._launch_url = lambda url, browser="": (
        CALLS.append(("open_url", url, browser)) or "假浏览器"
    )
    desktop._press_combo = lambda combo: (CALLS.append(("press", combo)) or combo)
    desktop._paste_text = lambda text: CALLS.append(("paste", text))
    desktop._click_at = lambda x, y, button="left", times=1: CALLS.append(("click", x, y, button, times))
    desktop._grab_screen = lambda path: (CALLS.append(("grab", path)) or (1920, 1080))
    desktop._encode_for_vision = lambda path: ("ZmFrZQ==", "image/jpeg")
    desktop._ask_vision = lambda b64, mime, question, w, h: (
        CALLS.append(("vision", question))
        or ("前台窗口：百度\n  · 搜索框 → 点 (960, 300)", [{"name": "搜索框", "box": [800, 280, 1120, 320],
                                                          "center": [960, 300]}])
    )
    desktop._enum_windows = lambda: [
        {"handle": 101, "title": "百度一下，你就知道 - Google Chrome",
         "rect": [0, 0, 1920, 1080], "minimized": False, "foreground": True},
        {"handle": 202, "title": "微信", "rect": [10, 10, 400, 700], "minimized": False},
    ]
    desktop._focus_handle = lambda handle: (CALLS.append(("focus", handle)) or True)


# ---------------------------------------------------------------- 闸门

def test_gate():
    print("\n[授权闸门]")
    expected = set(agent_desktop_tools())
    check("八个桌面工具都已注册", expected <= set(tools.tool_names()), sorted(expected - set(tools.tool_names())))

    # 未授权时逐个试，一个都不能放过
    cases = [
        ("open_url", {"url": "https://www.baidu.com"}),
        ("open_app", {"app": "chrome"}),
        ("list_windows", {}),
        ("focus_window", {"title": "百度"}),
        ("press_keys", {"keys": "ctrl+l"}),
        ("type_text", {"text": "天气"}),
        ("click_screen", {"x": 100, "y": 100}),
        ("screenshot", {}),
    ]
    for name, args in cases:
        result = invoke(name, args, allow_desktop=False)
        check("%s 未授权被拒" % name, not result.ok, result.content[:60])
        check("%s 报错点明原因" % name, "允许操作桌面" in (result.error or ""), result.error)

    # 缺键也算未授权，不能默认放行
    tool = tools.get_tool("open_url")
    result = tool.invoke({"url": "https://www.baidu.com"}, {"run_id": "no-flag"})
    check("context 里没有这个键也拒绝", not result.ok, result.error)
    check("没有真的打开", not [c for c in CALLS if c[0] == "open_url"], CALLS)


def agent_desktop_tools():
    import agent
    return list(agent.DESKTOP_TOOLS)


# ---------------------------------------------------------------- 打开网址

def test_open_url():
    print("\n[打开网址]")
    CALLS.clear()
    result = invoke("open_url", {"url": "www.baidu.com/s?wd=天气"})
    check("没写协议自动补 https", result.ok and CALLS and CALLS[-1][1] == "https://www.baidu.com/s?wd=天气", CALLS)
    check("提示浏览器要加载", "一两秒" in result.content, result.content)

    CALLS.clear()
    invoke("open_url", {"url": "http://127.0.0.1:8000/"})
    check("http 原样保留", CALLS[-1][1] == "http://127.0.0.1:8000/", CALLS)

    for bad in ("javascript:alert(1)", "data:text/html,<b>x</b>", "file:///C:/Windows/win.ini"):
        result = invoke("open_url", {"url": bad})
        check("%s 被拒" % bad.split(":")[0], not result.ok, result.content[:50])
    check("拒绝时给替代方案", "open_app" in (invoke("open_url", {"url": "file:///c/x"}).error or ""))

    check("空网址被拒", not invoke("open_url", {"url": "   "}).ok)

    CALLS.clear()
    invoke("open_url", {"url": "https://example.com", "browser": "chrome"})
    check("指定浏览器时传进去了", CALLS[-1] == ("open_url", "https://example.com", "chrome"), CALLS)


# ---------------------------------------------------------------- 打开应用

def test_open_app():
    print("\n[启动应用]")
    CALLS.clear()
    result = invoke("open_app", {"app": "notepad"})
    check("白名单应用放行", result.ok, result.content[:60])
    check("启动的是记事本", "notepad" in result.content.lower(), result.content)

    result = invoke("open_app", {"app": "c:\\windows\\system32\\cmd.exe"})
    check("任意 exe 被拒", not result.ok, result.content[:60])
    check("拒绝时列出可用项", "chrome" in (result.error or ""), result.error)

    # 工作区里的脚本不能借 open_app 启动：那等于绕过 run_command 的黑名单
    probe = os.path.join(_guard.active_root(), "_probe_desktop.bat")
    with open(probe, "w", encoding="utf-8") as handle:
        handle.write("echo hi")
    try:
        result = invoke("open_app", {"app": "_probe_desktop.bat"})
        check("工作区内的脚本也被拒", not result.ok, result.content[:60])
        check("报错说明原因", "不能从工作区启动可执行文件" in (result.error or ""), result.error)
    finally:
        os.remove(probe)


# ---------------------------------------------------------------- 按键

def test_press_keys():
    print("\n[按键]")
    CALLS.clear()
    for combo in ("ctrl+l", "enter", "tab", "ctrl+shift+t", "Ctrl + L"):
        result = invoke("press_keys", {"keys": combo})
        check("%s 可以按" % combo, result.ok, result.content[:60])
    check("大小写和空格被归一化", CALLS[-1][1] == "ctrl+l", CALLS[-1])

    for combo in ("alt+f4", "win+r", "win+l", "ctrl+shift+esc", "ctrl+alt+delete"):
        result = invoke("press_keys", {"keys": combo})
        check("%s 被禁止" % combo, not result.ok, result.content[:40])
    check("禁止理由说清后果", "关闭窗口或锁定系统" in (invoke("press_keys", {"keys": "alt+f4"}).error or ""))

    check("不认识的键被拒", not invoke("press_keys", {"keys": "ctrl+ftw"}).ok)
    check("中文键名被拒", not invoke("press_keys", {"keys": "回车"}).ok)
    check("空值被拒", not invoke("press_keys", {"keys": ""}).ok)


# ---------------------------------------------------------------- 输入

def test_type_text():
    print("\n[输入文字]")
    CALLS.clear()
    result = invoke("type_text", {"text": "今天天气怎么样"})
    check("中文原样粘进去", result.ok and CALLS[-1] == ("paste", "今天天气怎么样"), CALLS)
    check("返回字符数", "7 个字符" in result.content, result.content)

    check("空文本被拒", not invoke("type_text", {"text": ""}).ok)
    long_text = "字" * 2001
    result = invoke("type_text", {"text": long_text})
    check("超长被拒", not result.ok, result.content[:50])
    check("超长报出上限", "2000" in (result.error or ""), result.error)
    check("刚好 2000 可以", invoke("type_text", {"text": "字" * 2000}).ok)


# ---------------------------------------------------------------- 点击

def test_click():
    print("\n[点击]")
    CALLS.clear()
    result = invoke("click_screen", {"x": 960, "y": 300})
    check("正常坐标可以点", result.ok, result.content[:60])
    check("点了左键一次", CALLS[-1] == ("click", 960, 300, "left", 1), CALLS)

    result = invoke("click_screen", {"x": -50, "y": 10})
    check("负坐标被拒", not result.ok, result.content[:50])
    check("提示坐标来源", "screenshot" in (result.error or ""), result.error)

    check("超出屏幕宽度被拒", not invoke("click_screen", {"x": 99999, "y": 10}).ok)
    check("非整数坐标被拒", not invoke("click_screen", {"x": "左边", "y": 10}).ok)
    check("缺 y 被框架拦下", not invoke("click_screen", {"x": 10}).ok)

    CALLS.clear()
    invoke("click_screen", {"x": 10, "y": 10, "times": 9})
    check("连击次数被截到 2", CALLS[-1][4] == 2, CALLS[-1])

    CALLS.clear()
    invoke("click_screen", {"x": 10, "y": 10, "button": "right"})
    check("右键可用", CALLS[-1][3] == "right", CALLS[-1])
    check("不支持 middle", not invoke("click_screen", {"x": 10, "y": 10, "button": "middle"}).ok)


# ---------------------------------------------------------------- 截图

def test_screenshot():
    print("\n[截图]")
    CALLS.clear()
    result = invoke("screenshot", {})
    check("不带问题也能截", result.ok, result.content[:60])
    check("没让视觉模型解读", not [c for c in CALLS if c[0] == "vision"], CALLS)
    check("报了尺寸", result.data.get("width") == 1920, result.data)

    CALLS.clear()
    result = invoke("screenshot", {"question": "搜索框在哪"})
    check("带问题会调视觉", [c for c in CALLS if c[0] == "vision"], CALLS)
    check("回答里带视觉结论", "960, 300" in result.content, result.content)
    check("视觉结论也进 data", "960" in str(result.data.get("vision")), result.data)

    # 视觉链路坏了不该连累「截图成功」这件事
    original = desktop._ask_vision
    desktop._ask_vision = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("没配视觉模型"))
    try:
        result = invoke("screenshot", {"question": "现在什么界面"})
    finally:
        desktop._ask_vision = original
    check("视觉失败仍返回截图成功", result.ok, result.content[:80])
    check("失败原因写清", "没配视觉模型" in result.content, result.content)
    check("给了退路", "press_keys" in result.content, result.content)


# ---------------------------------------------------------------- 坐标换算

def test_vision_math():
    print("\n[坐标换算与解析]")
    # 这是整条链路最容易出错的一步：模型给 0-1000，屏幕是 1920×1080。
    # 换算错一点，点下去就落到别处，而且看起来「点了但没反应」。
    check("左边界", desktop._to_pixels(0, 1920) == 0, desktop._to_pixels(0, 1920))
    check("右边界不越界", desktop._to_pixels(1000, 1920) == 1919, desktop._to_pixels(1000, 1920))
    check("正中间", desktop._to_pixels(500, 1920) == 960, desktop._to_pixels(500, 1920))
    check("纵向按高度换算", desktop._to_pixels(500, 1080) == 540, desktop._to_pixels(500, 1080))
    check("超出 0-1000 判为无效", desktop._to_pixels(1200, 1920) == -1)
    check("负数判为无效", desktop._to_pixels(-5, 1920) == -1)
    check("非数字判为无效", desktop._to_pixels("left", 1920) == -1)

    check("纯 JSON 能解析", desktop._parse_vision_json('{"a":1}') == {"a": 1})
    check("带 ```json 围栏也能解析",
          desktop._parse_vision_json('```json\n{"a":2}\n```') == {"a": 2},
          desktop._parse_vision_json('```json\n{"a":2}\n```'))
    # 流式分片和最终消息会各带一份正文，拼起来是两段 JSON
    doubled = '{"elements":[{"name":"x","box":[1,1,2,2]}]}{"elements":[{"name":"x","box":[1,1,2,2]}]}'
    check("重复的 JSON 取第一段", desktop._parse_vision_json(doubled).get("elements"), doubled[:40])
    check("没有 JSON 返回空", desktop._parse_vision_json("图里有个按钮") == {})
    check("残缺 JSON 不炸", desktop._parse_vision_json('{"elements":[') == {})


def test_vision_pipeline():
    print("\n[视觉 → 像素]")
    import llm

    desktop._ask_vision = REAL_ASK_VISION
    captured = {}

    def fake_vision(messages, tools=None, timeout=30.0, model=None, should_stop=None, **kwargs):
        captured["prompt"] = messages[0]["content"][1]["text"]
        captured["require_vision"] = kwargs.get("require_vision")
        yield ("message", {"content": '{"window":"百度一下","elements":['
                                     '{"name":"搜索框","box":[500,500,600,600]},'
                                     '{"name":"坏元素","box":[9,9]}]}'})

    original = llm.chat_completion_stream
    llm.chat_completion_stream = fake_vision
    try:
        text, elements = desktop._ask_vision("abc", "image/jpeg", "搜索框在哪", 1920, 1080)
    finally:
        llm.chat_completion_stream = original

    check("走的是视觉请求", captured.get("require_vision") is True, captured.get("require_vision"))
    check("提示词要求归一化坐标", "0-1000 的归一化坐标" in captured["prompt"], captured["prompt"][:80])
    check("提示词禁止猜坐标", "不要猜坐标" in captured["prompt"])
    check("只保留结构合法的元素", len(elements) == 1, elements)
    check("归一化换算成像素", elements[0]["box"] == [960, 540, 1152, 648], elements[0])
    check("中心点按框算", elements[0]["center"] == [1056, 594], elements[0])
    check("文本里带像素坐标", "点 (1056, 594)" in text, text)

    # 模型不按约定回 JSON：不能当成功用，也不能让坐标混进去
    llm.chat_completion_stream = lambda *a, **k: iter([
        ("message", {"content": "我看到一个搜索框，大概在中间偏上。"})])
    try:
        text, elements = desktop._ask_vision("abc", "image/jpeg", "搜索框在哪", 1920, 1080)
    finally:
        llm.chat_completion_stream = original
    check("非 JSON 时元素为空", elements == [], elements)
    check("明确提示坐标不可靠", "坐标不可靠" in text, text[:80])


def test_budget():
    print("\n[动作预算]")
    clear_session()
    context = {"run_id": "budget", "allow_desktop": True}
    tool = tools.get_tool("list_windows")
    for _ in range(desktop.MAX_ACTIONS):
        check_once = tool.invoke({}, context)
        if not check_once.ok:
            break
    check("预算用完后第 N+1 次被拒", context["desktop_actions"] == desktop.MAX_ACTIONS, context["desktop_actions"])
    result = tool.invoke({}, context)
    check("超限拒绝", not result.ok, result.content[:40])
    check("超限后计数不再涨", context["desktop_actions"] == desktop.MAX_ACTIONS, context["desktop_actions"])
    check("让模型停下来问人", "让" in (result.error or "") or "接手" in (result.error or ""), result.error)


# ---------------------------------------------------------------- 会话锁

def test_session_lock():
    print("\n[会话锁]")
    clear_session()
    tool = tools.get_tool("list_windows")
    a = {"run_id": "sess-A", "allow_desktop": True}
    b = {"run_id": "sess-B", "allow_desktop": True}

    check("甲先拿到桌面", tool.invoke({}, a).ok)
    check("锁文件落盘", os.path.exists(desktop._lock_path()))

    result = tool.invoke({}, b)
    check("乙被挡住", not result.ok, result.content[:40])
    check("报错指出是哪个会话", "sess-A" in (result.error or ""), result.error)
    check("说清后果是会点错", "点错" in (result.error or ""), result.error)
    check("被挡住不算一次动作", b.get("desktop_actions") in (None, 0), b.get("desktop_actions"))
    check("甲自己可以连续调用", tool.invoke({}, a).ok)

    # 卡死的会话不能永久占着：超过 SESSION_LEASE 没有动作就当它已经结束
    data = desktop._read_session()
    data["ts"] = time.time() - desktop.SESSION_LEASE - 1
    with open(desktop._lock_path(), "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    check("超时后乙可以接管", tool.invoke({}, b).ok)
    check("锁换成了乙", desktop._read_session().get("run") == "sess-B", desktop._read_session())

    desktop.release_desktop_session("sess-A")
    check("甲放不掉乙的锁", os.path.exists(desktop._lock_path()))

    desktop.release_desktop_session("sess-B")
    check("乙跑完主动放锁", not os.path.exists(desktop._lock_path()))
    check("放锁后甲立刻可用", tool.invoke({}, a).ok)


# ---------------------------------------------------------------- 刚打开页面后的焦点

def test_fresh_open_focus():
    print("\n[打开页面后的焦点]")
    clear_session()
    ctx = {"run_id": "focus-A", "allow_desktop": True}

    CALLS.clear()
    tools.get_tool("open_url").invoke({"url": "https://example.com"}, ctx)
    check("打开网址后记下「刚打开过」", ctx.get("desktop_fresh_open") is True, ctx.get("desktop_fresh_open"))

    CALLS.clear()
    result = tools.get_tool("press_keys").invoke({"keys": "pagedown"}, ctx)
    check("翻页键前先按 esc", [c[1] for c in CALLS] == ["escape", "pagedown"], CALLS)
    check("说明为什么多按一下", "地址栏" in result.content, result.content)
    check("标记已清掉", ctx.get("desktop_fresh_open") is False, ctx.get("desktop_fresh_open"))

    CALLS.clear()
    tools.get_tool("press_keys").invoke({"keys": "pagedown"}, ctx)
    check("第二次翻页不再插 esc", [c[1] for c in CALLS] == ["pagedown"], CALLS)

    # ctrl+l 本来就是要进地址栏，不能先 esc 把它踢出去
    ctx["desktop_fresh_open"] = True
    CALLS.clear()
    tools.get_tool("press_keys").invoke({"keys": "ctrl+l"}, ctx)
    check("ctrl+l 不插 esc", [c[1] for c in CALLS] == ["ctrl+l"], CALLS)

    # 输入同理，而且更隐蔽：文字会粘进地址栏，把刚打开的网址改掉
    ctx["desktop_fresh_open"] = True
    CALLS.clear()
    result = tools.get_tool("type_text").invoke({"text": "天气"}, ctx)
    check("粘字前先按 esc", [c[1] for c in CALLS] == ["escape", "天气"], CALLS)
    check("粘字结果里也说明", "地址栏" in result.content, result.content)

    # 点过一下页面，焦点已经明确，不需要再插 esc
    ctx["desktop_fresh_open"] = True
    CALLS.clear()
    tools.get_tool("click_screen").invoke({"x": 400, "y": 300}, ctx)
    check("点击后标记清掉", ctx.get("desktop_fresh_open") is False, ctx.get("desktop_fresh_open"))
    CALLS.clear()
    tools.get_tool("press_keys").invoke({"keys": "home"}, ctx)
    check("点过之后直接按键", [c[1] for c in CALLS] == ["home"], CALLS)

    desktop.release_desktop_session("focus-A")


# ---------------------------------------------------------------- 接进 Agent

def test_agent_wiring():
    print("\n[接进 Agent]")
    fresh_db()
    import agent

    captured = {}

    def fake_model(messages, tools=None, timeout=30.0, model=None, should_stop=None, **kwargs):
        captured.setdefault("prompt", messages[0]["content"])
        captured["tools"] = [item["function"]["name"] for item in (tools or [])]
        turn = captured.get("turn", 0)
        captured["turn"] = turn + 1
        if turn == 0:
            yield ("token", "")
            yield ("message", {
                "content": "",
                "tool_calls": [{"id": "c1", "name": "open_url",
                                "arguments": json.dumps({"url": "https://www.baidu.com/s?wd=天气"},
                                                        ensure_ascii=False)}],
                "usage": 10, "cancelled": False,
            })
        else:
            yield ("token", ["已经打开百度的天气搜索结果。"])
            yield ("message", {"content": "已经打开百度的天气搜索结果。",
                               "tool_calls": [], "usage": 10, "cancelled": False})

    original = agent.chat_completion_stream
    agent.chat_completion_stream = fake_model
    CID = "桌面"
    try:
        # 1) 不开「操作桌面」：模型连工具都看不见
        CALLS.clear()
        captured.clear()
        list(agent.run_agent_stream("帮我去百度搜天气", mode="chat"))
        check("未开启时工具不在列表里", "open_url" not in captured["tools"], captured["tools"])
        check("未开启时不提桌面规则", "允许操作桌面" not in captured["prompt"])

        # 2) 开了：工具齐全，且一句话就能撮合
        CALLS.clear()
        captured.clear()
        events = list(agent.run_agent_stream("帮我去百度搜天气", mode="chat", allow_desktop=True))
        check("开启后八个工具都在", set(agent.DESKTOP_TOOLS) <= set(captured["tools"]),
              sorted(set(agent.DESKTOP_TOOLS) - set(captured["tools"])))
        check("提示词要求先拼 URL", "直接用 open_url 拼结果页地址" in captured["prompt"])
        check("提示词要求看再点", "点之前必须 screenshot" in captured["prompt"])
        check("提示词禁止替他输密码", "绝不替他输密码" in captured["prompt"])

        launches = [c for c in CALLS if c[0] == "open_url"]
        check("真的调了 open_url", launches, CALLS)
        check("关键词进了结果页地址", launches and "wd=天气" in launches[0][1], launches[:1])
        ends = [e for e in events if e["type"] == "tool_end"]
        check("过程面板能看到这一步", ends and ends[0]["tool"] == "open_url", ends[:1])
        check("最后给出了回答", "天气搜索结果" in events[-1]["reply"], events[-1]["reply"])

        # 3) 键盘路径：切窗口 → 敲地址 → 输入 → 回车
        CALLS.clear()
        captured.clear()

        def fake_keyboard(messages, tools=None, timeout=30.0, model=None, should_stop=None, **kwargs):
            captured.setdefault("prompt", messages[0]["content"])
            turn = captured.get("turn", 0)
            captured["turn"] = turn + 1
            if turn == 0:
                calls = [
                    {"id": "k1", "name": "list_windows", "arguments": "{}"},
                    {"id": "k2", "name": "focus_window", "arguments": '{"title": "Chrome"}'},
                    {"id": "k3", "name": "press_keys", "arguments": '{"keys": "ctrl+l"}'},
                    {"id": "k4", "name": "type_text", "arguments": '{"text": "天气"}'},
                ]
                yield ("token", "")
                yield ("message", {"content": "", "tool_calls": calls, "usage": 10, "cancelled": False})
            elif turn == 1:
                yield ("token", "")
                yield ("message", {"content": "",
                                   "tool_calls": [{"id": "k5", "name": "press_keys",
                                                   "arguments": '{"keys": "enter"}'}],
                                   "usage": 10, "cancelled": False})
            else:
                yield ("token", ["已在地址栏搜索天气。"])
                yield ("message", {"content": "已在地址栏搜索天气。", "tool_calls": [],
                                   "usage": 10, "cancelled": False})

        agent.chat_completion_stream = fake_keyboard
        try:
            events = list(agent.run_agent_stream("在浏览器里搜一下天气", mode="chat", allow_desktop=True))
        finally:
            agent.chat_completion_stream = original
        order = [c[0] for c in CALLS]
        check("第一步是把窗口切到前台", order and order[0] == "focus", CALLS)
        check("键盘路径没有另开新窗口", "open_url" not in order, CALLS)
        presses = [c[1] for c in CALLS if c[0] == "press"]
        check("先按 ctrl+l", presses and presses[0] == "ctrl+l", presses)
        check("再按 enter", presses[-1] == "enter", presses)
        check("中文通过剪贴板输入", ("paste", "天气") in CALLS, CALLS)
        check("末尾有回答", events[-1]["reply"], events[-1])
        fail_round = [e for e in events if e["type"] == "tool_end" and not e.get("ok")]
        check("五步全部成功", not fail_round, fail_round[:2])
    finally:
        agent.chat_completion_stream = original


def main_():
    stub_backends()
    test_gate()
    test_open_url()
    test_open_app()
    test_press_keys()
    test_type_text()
    test_click()
    test_screenshot()
    test_vision_math()
    test_vision_pipeline()
    test_budget()
    test_session_lock()
    test_fresh_open_focus()
    test_agent_wiring()
    print("\n%s 通过 %d，失败 %d" % ("FAIL" if FAIL else "ALL PASS", PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
