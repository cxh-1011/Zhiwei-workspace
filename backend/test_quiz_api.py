"""答题接口的 HTTP 层测试：走真实路由与中间件，不绕过鉴权。

为什么值得单独跑一层：判分函数测得再细，也测不出「接口把答案一起返回了」
「字段校验漏了」「没登录也能调」这类问题。这三个在浏览器里都要点一遍才发现。

注意：main 一 import 就会 backup_db() + init_db()，所以必须在 import 之前
把 database.DB_PATH 指到临时库，否则会动真实 chats.db。
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
database.DB_PATH = _TMP.name

import main as app_main  # noqa: E402  必须在改完 DB_PATH 之后导入
from fastapi.testclient import TestClient  # noqa: E402

PASS = 0
FAIL = 0
CHAPTER = "s1c1"


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, detail))


def client(authenticated=True):
    """默认带一张自签的会话 cookie：验的是业务，不是登录。"""
    instance = TestClient(app_main.app)
    if authenticated:
        instance.cookies.set(app_main.SESSION_COOKIE, app_main.create_session_token())
    return instance


def reset():
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("DELETE FROM quiz_attempts")
    conn.execute("DELETE FROM course_progress")
    conn.commit()
    conn.close()


def items():
    chapter = client().get("/course/chapter/%s" % CHAPTER).json()
    return chapter["quiz"]["items"]


def submit(api, index, answer, seconds=10):
    rows = items()
    return api.post("/quiz/%s/submit" % CHAPTER,
                    json={"item_id": rows[index]["id"], "answer": answer, "seconds": seconds})


def own_answer(row):
    """从公开题里推不出答案，所以按题号去后端拿一份带答案的。"""
    import quiz
    full = quiz.load_quiz(CHAPTER)["items"]
    item = next(i for i in full if i["id"] == row["id"])
    if item["kind"] == "choice":
        return item["answer"]
    if item["kind"] == "blank":
        first = item["blanks"][0]
        return first[0] if isinstance(first, list) else first
    words = []
    for point in item.get("must") or []:
        pool = point.get("any") or point.get("all") if isinstance(point, dict) else None
        if pool:
            words.append(pool[0])
    return "，".join(words)


def test_auth():
    print("\n[鉴权]")
    reset()
    check("没登录调接口 401", TestClient(app_main.app).get("/course/chapter/s1c1").status_code == 401)
    check("登录后 200", client().get("/course/chapter/s1c1").status_code == 200)
    check("页面本身不用登录", TestClient(app_main.app).get("/learn.html").status_code in (200, 404))


def test_public_payload():
    print("\n[公开题目不含答案]")
    reset()
    response = client().get("/course/chapter/%s" % CHAPTER)
    body = response.json()
    check("章节接口带上 quiz", "quiz" in body, list(body.keys()))
    rows = body["quiz"]["items"]
    check("三题都在", len(rows) == 3, len(rows))
    text = response.text
    check("响应体里没有 answer 字段", '"answer"' not in text)
    check("响应体里没有 must/forbid", '"must"' not in text and '"forbid"' not in text)
    check("响应体里没有 explain", '"explain"' not in text)
    check("填空题给出空数", any(row.get("blank_count") for row in rows))
    check("作答状态一起给", body["quiz"]["state"]["total"] == 3)


def test_submit_scoring():
    print("\n[提交与判分]")
    reset()
    api = client()
    rows = items()

    right = submit(api, 0, own_answer(rows[0]))
    body = right.json()
    check("答对 200 且 100 分", right.status_code == 200 and body["score"] == 100, body)
    check("返回解析", len(body["explain"]) > 20)
    check("返回掌握度变更", "mastery" in body and body["mastery"]["changed"] is False, body.get("mastery"))
    check("返回整章状态", body["quiz"]["answered"] == 1, body["quiz"]["answered"])

    wrong = submit(api, 0, "Z")
    check("答错是 0 分而不是 4xx", wrong.status_code == 200 and wrong.json()["score"] == 0, wrong.status_code)
    check("答错后该题算没过", wrong.json()["quiz"]["passed"] == 0)

    check("提交后能查到这个记录", len(database.list_quiz_attempts("agent-dev", CHAPTER)) == 2)


def test_mastery_flow():
    print("\n[掌握度：全过 → 2，错一题 → 1]")
    reset()
    api = client()
    rows = items()
    last = None
    for index, row in enumerate(rows):
        last = submit(api, index, own_answer(row)).json()
    check("三题全过后升到 2", last["mastery"]["after"] == 2, last["mastery"])
    check("状态里三题都过", last["quiz"]["passed"] == 3, last["quiz"]["passed"])

    # 判分不该改「学没学过」
    catalog = api.get("/course").json()
    chapter = next(c for c in catalog["chapters"] if c["id"] == CHAPTER)
    check("判分不动 status", chapter["status"] == "todo", chapter["status"])
    check("课程接口带出 mastery=2", chapter["mastery"] == 2, chapter["mastery"])

    relapsed = submit(api, 0, "Z").json()
    check("重做答错降回 1", relapsed["mastery"]["after"] == 1, relapsed["mastery"])


def test_manual_done_cap():
    print("\n[手点学完的掌握度封顶]")
    reset()
    api = client()
    body = api.patch("/course/progress", json={"chapter_id": CHAPTER, "status": "done"}).json()
    check("没答过题：只能是 1", body["progress"]["mastery"] == 1, body["progress"])

    reset()
    rows = items()
    for index, row in enumerate(rows):
        submit(api, index, own_answer(row))
    body = api.patch("/course/progress", json={"chapter_id": CHAPTER, "status": "done"}).json()
    check("三题全过：允许 2", body["progress"]["mastery"] == 2, body["progress"])

    reset()
    body = api.patch("/course/progress",
                     json={"chapter_id": CHAPTER, "status": "done", "mastery": 3}).json()
    check("自称 3 级被压到 1", body["progress"]["mastery"] == 1, body["progress"])


def test_bad_requests():
    print("\n[坏请求]")
    reset()
    api = client()
    check("不存在的题号 404", api.post("/quiz/%s/submit" % CHAPTER,
                                   json={"item_id": "s1c1-q9", "answer": "B"}).status_code == 404)
    check("没题库的章 404", api.post("/quiz/s1c2/submit",
                                 json={"item_id": "s1c2-q1", "answer": "B"}).status_code == 404)
    check("非法章 id 404", api.post("/quiz/A_B/submit",
                                json={"item_id": "A_B-q1", "answer": "B"}).status_code == 404)
    check("负数秒数 422", api.post("/quiz/%s/submit" % CHAPTER,
                               json={"item_id": "s1c1-q1", "answer": "B", "seconds": -1}).status_code == 422)
    check("超长作答 422", api.post("/quiz/%s/submit" % CHAPTER,
                               json={"item_id": "s1c1-q1", "answer": "x" * 5000}).status_code == 422)
    check("不存在的课程 404", api.post("/quiz/%s/submit?course_id=nope" % CHAPTER,
                                   json={"item_id": "s1c1-q1", "answer": "B"}).status_code == 404)


def main_():
    try:
        test_auth()
        test_public_payload()
        test_submit_scoring()
        test_mastery_flow()
        test_manual_done_cap()
        test_bad_requests()
    finally:
        try:
            os.unlink(_TMP.name)
        except OSError:
            pass
    print("\n%s 通过 %d，失败 %d" % ("FAIL" if FAIL else "ALL PASS", PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
