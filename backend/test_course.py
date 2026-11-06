"""课程学习台的离线测试：目录读取、依赖推荐、前置提示、接口边界。

不联网、不调模型。进度写在自己的临时库里，不会动 chats.db 里用户的真实进度。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database
import course

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, detail))


def use_temp_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    old = database.DB_PATH
    database.DB_PATH = tmp.name
    database.init_db()
    return tmp.name, old


def test_course_tree():
    print("\n[课程树]")
    data = course.load_course()
    check("读到课程标题", bool(data.get("title")), data.get("title"))
    check("章节数 33", len(data["chapters"]) == 33, len(data["chapters"]))
    check("阶段数 10", len(data["stages"]) == 10, len(data["stages"]))

    numbers = [c["no"] for c in data["chapters"]]
    check("章节编号连续", numbers == list(range(1, len(numbers) + 1)), numbers[:5])
    check("每章都有标题", all(c.get("title") for c in data["chapters"]))
    check("每章都有 path", all(c.get("path") for c in data["chapters"]))
    # 阶段挂的章节 id 必须都能在 chapters 里找到，否则前端目录会漏条目
    ids = {c["id"] for c in data["chapters"]}
    stage_ids = [cid for s in data["stages"] for cid in s["chapters"]]
    check("阶段引用的章节都存在", set(stage_ids) <= ids,
          set(stage_ids) - ids)
    check("每个阶段都分到了章节", all(s["chapters"] for s in data["stages"]))


def test_recommend():
    print("\n[依赖推荐]")
    data = course.load_course()
    chapters = data["chapters"]
    for c in chapters:
        c["status"] = "todo"
        c["mastery"] = 0

    nxt = course.recommend_next(chapters)
    check("没学过时推荐第 1 章", nxt and nxt["no"] == 1, nxt and nxt["no"])

    chapters[0]["status"] = "done"
    nxt = course.recommend_next(chapters)
    check("学完第 1 章推荐第 2 章", nxt and nxt["no"] == 2, nxt and nxt["no"])

    # 用户直接跳到第 10 章标记「在学」，应该先回到那一章，而不是继续推第 3 章
    chapters[9]["status"] = "learning"
    nxt = course.recommend_next(chapters)
    check("标了在学的章节优先返回", nxt and nxt["no"] == 10, nxt and nxt["no"])

    chapters[9]["status"] = "todo"
    # 中间挖空：只学完第 1、2 章，后面的都 todo，应该推到第 3 章
    chapters[1]["status"] = "done"
    nxt = course.recommend_next(chapters)
    check("按顺序推进到第 3 章", nxt and nxt["no"] == 3, nxt and nxt["no"])

    for c in chapters:
        c["status"] = "done"
    check("全部学完返回 None", course.recommend_next(chapters) is None)

    check("空课程返回 None", course.recommend_next([]) is None)

    # mastery 不该影响过关判定：用户只点「学完」不评掌握度是常态，
    # 若这里要求 mastery>=2，推荐会一直卡住不动
    for c in chapters:
        c["status"] = "todo"
        c["mastery"] = 0
    chapters[0]["status"] = "done"
    chapters[0]["mastery"] = 0
    nxt = course.recommend_next(chapters)
    check("只看 status 不看 mastery", nxt and nxt["no"] == 2, nxt and nxt["no"])


def test_blocked_by():
    print("\n[前置提示]")
    data = course.load_course()
    chapters = data["chapters"]
    for c in chapters:
        c["status"] = "todo"
        c["mastery"] = 0

    by_id = {c["id"]: c for c in chapters}
    # 第 4 章（s2c1）的前置是第 3 章
    target = by_id["s2c1"]
    missing = course.blocked_by(chapters, target)
    check("跳着学会提示前置", len(missing) == 1 and missing[0]["no"] == 3,
          [m["no"] for m in missing])

    by_id["s1c3"]["status"] = "done"
    check("前置学完后不再提示", course.blocked_by(chapters, target) == [])


def test_load_chapter():
    print("\n[章节读取]")
    payload = course.load_chapter("agent-dev", "s1c1")
    check("拿到章节元信息", payload["chapter"]["id"] == "s1c1")
    check("拿到正文", len(payload["markdown"]) > 500, len(payload["markdown"]))
    check("正文不含 front-matter", not payload["markdown"].startswith("---"))
    check("第 1 章没有上一章", payload["prev"] is None)
    check("第 1 章有下一章", payload["next"] == "s1c2")
    check("序号从 1 开始", payload["index"] == 1)

    second = course.load_chapter("agent-dev", "s1c2")
    check("第 2 章上一章是 s1c1", second["prev"] == "s1c1")

    # 自测答案依赖 details，正文必须保留这两个标签
    check("正文保留 details 折叠块", "<details>" in payload["markdown"])


def test_safety():
    print("\n[路径与 id 校验]")
    for bad in ["../main", "../../etc/passwd", "S1C1", "s1c1/../x", ""]:
        try:
            course.load_chapter("agent-dev", bad)
            check("拒绝非法章节 id: %r" % bad, False)
        except course.CourseError:
            check("拒绝非法章节 id: %r" % bad, True)

    try:
        course.load_course("../backend")
        check("拒绝非法课程 id", False)
    except course.CourseError:
        check("拒绝非法课程 id", True)

    try:
        course.load_course("no-such-course")
        check("不存在的课程报错", False)
    except course.CourseError:
        check("不存在的课程报错", True)


def test_progress_api():
    print("\n[进度与接口]")
    db_path, old_path = use_temp_db()
    try:
        from fastapi.testclient import TestClient
        import main

        client = TestClient(main.app)
        check("未登录访问 /course 被拒", client.get("/course").status_code == 401)

        client.cookies.set(main.SESSION_COOKIE, main.create_session_token())

        response = client.get("/course")
        check("登录后拿到课程", response.status_code == 200, response.status_code)
        body = response.json()
        check("接口带进度字段", "progress" in body and "next" in body)
        check("初始进度为 0", body["progress"]["done"] == 0, body["progress"])
        check("初始推荐第 1 章", body["next"]["id"] == "s1c1", body["next"]["id"])

        response = client.get("/course/chapter/s1c1")
        check("拿到章节接口", response.status_code == 200)
        check("接口带 blocked_by", "blocked_by" in response.json())

        response = client.patch("/course/progress",
                                json={"chapter_id": "s1c1", "status": "done", "mastery": 2})
        check("标记学完成功", response.status_code == 200, response.status_code)
        body = response.json()
        check("进度变成 1", body["summary"]["done"] == 1, body["summary"])
        check("推荐推进到第 2 章", body["next"]["id"] == "s1c2", body["next"]["id"])

        response = client.get("/course")
        check("重新读取进度保持", response.json()["progress"]["done"] == 1)

        response = client.patch("/course/progress",
                                json={"chapter_id": "s1c1", "status": "todo"})
        check("撤销学完", response.json()["summary"]["done"] == 0)

        check("非法 status 被拒",
              client.patch("/course/progress",
                           json={"chapter_id": "s1c1", "status": "finished"}).status_code == 400)
        check("非法 mastery 被拒",
              client.patch("/course/progress",
                           json={"chapter_id": "s1c1", "mastery": 9}).status_code == 400)
        check("不存在的章节 404", client.get("/course/chapter/zzz9").status_code == 404)
        check("穿越路径 404",
              client.get("/course/chapter/..%2F..%2Fmain").status_code == 404)
    finally:
        database.DB_PATH = old_path
        try:
            os.unlink(db_path)
        except OSError:
            pass


def test_tutor_prompt():
    print("\n[导师提示词]")
    import agent

    prompt = agent._tutor_prompt("agent-dev", "s2c1")
    check("提示词非空", len(prompt) > 500, len(prompt))
    check("带上所在章节", "第 4 章" in prompt)
    check("带上本章目标", "提示词拆成角色" in prompt or "四段" in prompt)
    check("说明三类意图", "概念没懂" in prompt and "练习卡住" in prompt and "要代码" in prompt)
    check("禁止直接给答案", "不要直接给答案" in prompt)
    check("禁止鼓励话术", "鼓励话术" in prompt)
    check("禁用废弃 API", "ChatCompletion.create" in prompt)

    empty = agent._tutor_prompt("agent-dev", "")
    check("没有章节时也能用", len(empty) > 300 and "第 4 章" not in empty)

    broken = agent._tutor_prompt("no-such-course", "s1c1")
    check("课程不存在时不崩", "学习模式" in broken)


def main_():
    test_course_tree()
    test_recommend()
    test_blocked_by()
    test_load_chapter()
    test_safety()
    test_progress_api()
    test_tutor_prompt()
    print("\n%s 通过 %d，失败 %d" % ("FAIL" if FAIL else "ALL PASS", PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
