"""学习台页面要用到的接口：目录自测灯、盲测卷、交卷。

为什么单开一个文件：这三处都是「界面上看到的东西」，判分和存取本身在别的
测试里已经测过；这里要盯的是四件界面才会暴露的事——
1. 目录灯的数字能不能对上章节页；
2. 盲测卷有没有把 stage 和答案漏出去（漏了就不是盲测了）；
3. 交卷会不会顺手写 course_progress（写了进度条就凭空涨）；
4. 没登录能不能调。
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database
import quiz

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
database.DB_PATH = _TMP.name

import main as app_main  # noqa: E402  必须在改完 DB_PATH 之后导入
from fastapi.testclient import TestClient  # noqa: E402

PASS = 0
FAIL = 0
COURSE = "agent-dev"


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, detail))


def client(authenticated=True):
    instance = TestClient(app_main.app)
    if authenticated:
        instance.cookies.set(app_main.SESSION_COOKIE, app_main.create_session_token())
    return instance


def reset():
    conn = sqlite3.connect(database.DB_PATH)
    for table in ("quiz_attempts", "course_progress", "diagnostics", "profile_skills"):
        conn.execute("DELETE FROM %s" % table)
    conn.commit()
    conn.close()


def chapter_of(course, chapter_id):
    return next(c for c in course["chapters"] if c["id"] == chapter_id)


def correct_answers():
    """按真值造一份满分答卷：对外 id 是 d1/d2…，所以要按下标配对。"""
    diagnostic = quiz.load_diagnostic(COURSE)
    answers = {}
    for index, item in enumerate(diagnostic["items"]):
        if item["kind"] == "choice":
            answers["d%d" % (index + 1)] = item["answer"]
        else:
            answers["d%d" % (index + 1)] = "\n".join(group[0] for group in item["blanks"])
    return answers


def test_catalog_lights():
    print("\n[目录自测灯]")
    reset()
    course = client().get("/course").json()

    first = chapter_of(course, "s1c1")
    check("有题库的章带 quiz 摘要", first.get("quiz") == {"total": 3, "answered": 0, "passed": 0}, first.get("quiz"))
    check("没题库的章是 None", chapter_of(course, "s1c2").get("quiz") is None,
          chapter_of(course, "s1c2").get("quiz"))

    # 答对一道：目录要立刻能反映，否则用户答完回目录看还是灰的
    api = client()
    body = api.get("/course/chapter/s1c1").json()
    item = body["quiz"]["items"][0]
    answer = "B" if item["kind"] == "choice" else "x"
    api.post("/quiz/s1c1/submit", json={"item_id": item["id"], "answer": answer})
    after = chapter_of(api.get("/course").json(), "s1c1")
    check("答一题后 answered=1", after["quiz"]["answered"] == 1, after["quiz"])

    # 同一题重答错误：answered 不该涨到 2（每题只算一次）
    api.post("/quiz/s1c1/submit", json={"item_id": item["id"], "answer": ""})
    again = chapter_of(api.get("/course").json(), "s1c1")
    check("重答同一题不重复计数", again["quiz"]["answered"] == 1, again["quiz"])
    check("重答错误后 passed 归零", again["quiz"]["passed"] == 0, again["quiz"])

    # 手动做过关不出现在灯里：灯只看题，不看 status
    api.patch("/course/progress", json={"chapter_id": "s1c1", "status": "done"})
    manual = chapter_of(api.get("/course").json(), "s1c1")
    check("手点学完不改自测灯", manual["quiz"]["passed"] == 0 and manual["status"] == "done",
          {"quiz": manual["quiz"], "status": manual["status"]})


def test_diagnostic_paper():
    print("\n[盲测卷]")
    reset()
    data = client().get("/diagnostic").json()
    check("20 题", len(data["items"]) == 20, len(data["items"]))
    check("有标题和时长", bool(data["title"]) and data["minutes"] > 0, data.get("minutes"))

    ids = [item["id"] for item in data["items"]]
    check("对外 id 是 d1…d20", ids == ["d%d" % (i + 1) for i in range(20)], ids[:3])

    text = str(data["items"])
    check("不泄漏 stage", "'stage'" not in text and "diag-s1" not in text)
    check("不泄漏答案", "'answer'" not in text and "'blanks'" not in text and "'explain'" not in text)

    check("选择与填空都带得动", {i["kind"] for i in data["items"]} == {"choice", "blank"})
    check("填空题给了空数", all("blank_count" in i for i in data["items"] if i["kind"] == "blank"))
    check("没测过时 last 为空", data.get("last") is None, data.get("last"))


def test_diagnostic_grade():
    print("\n[交卷]")
    reset()
    api = client()
    full = correct_answers()

    result = api.post("/diagnostic/submit", json={"answers": full}).json()
    check("满分 20/20", result["correct"] == 20 and result["total"] == 20, result["correct"])
    check("十个阶段都在", len(result["levels"]) == 10, len(result["levels"]))
    check("全对判 2 级", all(row["level"] == 2 for row in result["levels"].values()),
          {k: v["level"] for k, v in result["levels"].items()})
    check("封顶不超过 2", result["max_level"] == 2, result["max_level"])
    check("逐题回看 20 条", len(result["review"]) == 20, len(result["review"]))
    check("回看带解析与正确答案",
          bool(result["review"][0]["explain"]) and result["review"][0]["answer"],
          result["review"][0])

    # 画像要落库，否则「定制路线」还是靠猜
    levels = result["levels"]
    stored = database.list_profile_skills(COURSE)
    check("画像落库", len(stored) == 10, len(stored))
    check("画像等级与结果一致", all(stored[s]["level"] == levels[s]["level"] for s in levels))
    check("诊断存档一行", database.latest_diagnostic(COURSE) is not None)

    # 关键一条：不能顺手把进度写成「学完了」
    progress = database.list_course_progress(COURSE)
    check("诊断不写 course_progress", progress == {}, len(progress))

    # 留空 = 不会。全空交卷不报错，但等级都是 0
    empty = api.post("/diagnostic/submit", json={"answers": {}}).json()
    check("空卷 0/20", empty["correct"] == 0, empty["correct"])
    check("空卷全 0 级", all(row["level"] == 0 for row in empty["levels"].values()))

    # 只答一个阶段的两题：那一阶段 2 级，别的 0 级
    one_stage = {key: value for key, value in full.items() if key in ("d1", "d2")}
    partial = api.post("/diagnostic/submit", json={"answers": one_stage}).json()
    filled = {s: row["level"] for s, row in partial["levels"].items() if row["level"]}
    check("只答一段只升一段", len(filled) == 1 and list(filled.values()) == [2], filled)

    # 交卷后卡片要能显示上次成绩
    again = api.get("/diagnostic").json()
    check("交卷后 last 有成绩", again["last"]["total"] == 20, again["last"])
    check("交卷后画像跟着回来", len(again["levels"]) == 10, len(again["levels"]))

    # 乱造的 id 一律丢掉，不能让一份脏答卷把画像冲成 0
    dirty = api.post("/diagnostic/submit", json={"answers": {"xx": "A", "d999": "B"}}).json()
    check("脏 key 被忽略", dirty["correct"] == 0 and dirty["total"] == 20, dirty["correct"])


def test_auth():
    print("\n[鉴权]")
    anon = client(authenticated=False)
    check("没登录取不到诊断卷", anon.get("/diagnostic").status_code == 401)
    check("没登录不能交卷", anon.post("/diagnostic/submit", json={"answers": {}}).status_code == 401)


def main():
    test_catalog_lights()
    test_diagnostic_paper()
    test_diagnostic_grade()
    test_auth()
    print("\n%s 通过 %d，失败 %d" % ("ALL PASS" if not FAIL else "有失败", PASS, FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
