"""能力画像的离线测试：10 段等级、依据、覆盖度、实测推翻、过期、接口。

不联网、不调模型。画像写在临时库里，不动 chats.db。
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import course
import database
import profile
import quiz

PASS = 0
FAIL = 0
COURSE = course.DEFAULT_COURSE


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


def reset():
    conn = sqlite3.connect(database.DB_PATH)
    for table in ("profile_skills", "diagnostics", "quiz_attempts", "course_progress"):
        conn.execute("DELETE FROM %s" % table)
    conn.commit()
    conn.close()


def _point_words(point):
    """一个要点该怎么写进答案里才命中：字符串原样；对象取 all 全量 + any 第一个。"""
    if isinstance(point, str):
        return [point]
    if not isinstance(point, dict):
        return []
    words = [str(w) for w in point.get("all") or []]
    any_words = [str(w) for w in point.get("any") or []]
    if any_words:
        words.append(any_words[0])
    return words


def correct_answer(item):
    if item.get("kind") == "choice":
        return item["answer"]
    if item.get("kind") == "blank":
        return " ".join(group[0] for group in item["blanks"])
    words = []
    for point in item.get("must") or []:
        words.extend(_point_words(point))
    return "；".join(words)


def wrong_answer(item):
    if item.get("kind") == "choice":
        return "D" if str(item["answer"]).upper() != "D" else "B"
    return "完全不会"


def take_chapter_quiz(chapter_id, right=True):
    data = quiz.load_quiz(chapter_id, COURSE)
    for item in data["items"]:
        answer = correct_answer(item) if right else wrong_answer(item)
        quiz.record(chapter_id, item, answer, 0, COURSE)


def submit_diagnostic(levels):
    """按阶段造一份诊断：levels 里的阶段两题全对，其余全错。"""
    diagnostic = quiz.load_diagnostic(COURSE)
    answers = {}
    for index, item in enumerate(diagnostic["items"]):
        public_id = "d%d" % (index + 1)
        if item.get("stage") in levels:
            answers[public_id] = (item["answer"] if item.get("kind") == "choice"
                                  else item["blanks"][0][0])
        else:
            answers[public_id] = ("D" if str(item.get("answer")).upper() != "D" else "B") \
                if item.get("kind") == "choice" else "不知道"
    mapped = quiz.diagnostic_answers(diagnostic, answers)
    scored = quiz.score_diagnostic(diagnostic, mapped)
    return profile.apply(diagnostic, scored, mapped, COURSE)


def stage_rows(data):
    return {row["id"]: row for row in data["stages"]}


def test_empty():
    print("\n[空画像：10 段都在，全是未测]")
    reset()
    data = profile.overview(COURSE)
    check("十个阶段都列出来", len(data["stages"]) == 10, len(data["stages"]))
    check("全是 0 级", all(row["level"] == 0 for row in data["stages"]))
    check("标签是未测", all(row["label"] == "未测" for row in data["stages"]))
    check("没测过时 last 为 None", data["last"] is None, data["last"])
    check("measured 为 0", data["totals"]["measured"] == 0, data["totals"])
    check("没有阶段被标跳过", data["totals"]["skipping"] == 0, data["totals"])
    check("刻度是 0-3（能讲算满分）", data["max_level"] == 3, data["max_level"])
    check("跳过线来自 course.SKIP_LEVEL", data["skip_level"] == course.SKIP_LEVEL)
    check("阶段带序号和标题", data["stages"][0]["no"] == 1 and data["stages"][0]["title"],
          data["stages"][0])
    check("画像不写 course_progress", database.list_course_progress(COURSE) == {})


def test_after_diagnostic():
    print("\n[诊断之后：等级、依据、来源、跳过标记]")
    reset()
    submit_diagnostic({"s1", "s2", "s3"})
    data = profile.overview(COURSE)
    rows = stage_rows(data)
    check("s1 判 2 级", rows["s1"]["level"] == 2, rows["s1"]["level"])
    check("s1 标签是能写", rows["s1"]["label"] == "能写", rows["s1"]["label"])
    check("来源写摸底诊断", rows["s1"]["source_label"] == "摸底诊断", rows["s1"]["source_label"])
    check("依据写清对了几题", "2/2" in rows["s1"]["evidence"], rows["s1"]["evidence"])
    check("s1 标跳过", rows["s1"]["skip"] is True)
    check("s4 仍是未测且不跳过",
          rows["s4"]["level"] == 0 and rows["s4"]["skip"] is False, rows["s4"])
    check("measured 是 3", data["totals"]["measured"] == 3, data["totals"])
    check("skipping 是 3", data["totals"]["skipping"] == 3, data["totals"])
    check("last 记录对了几题", data["last"]["correct"] == 6, data["last"])
    check("刚测完不算过期", rows["s1"]["stale"] is False)
    check("diagnostics 表有一条", len(_rows("diagnostics")) == 1)


def test_challenged():
    print("\n[章节实测推翻诊断：打「实测没过」，不再说跳过中]")
    # 接着上面的画像：s1 判 2 级。现在把 s1c1 做错。
    take_chapter_quiz("s1c1", right=False)
    rows = stage_rows(profile.overview(COURSE))
    check("s1 被标 challenged", rows["s1"]["challenged"] is True, rows["s1"])
    check("s1 等级没被改写（画像只记结论）", rows["s1"]["level"] == 2, rows["s1"]["level"])
    # s1 这一段三章现在都有题库，所以覆盖是「3 章有题、1 章答过」
    check("覆盖度记了 3 章题库 1 章答过", rows["s1"]["quiz"]["with_quiz"] == 3
          and rows["s1"]["quiz"]["tested"] == 1, rows["s1"]["quiz"])
    check("没过章节数记 1", rows["s1"]["quiz"]["failed"] == 1, rows["s1"]["quiz"])
    check("s3 没被误伤", rows["s3"]["challenged"] is False, rows["s3"])

    # 同一章全部答对：challenged 消失，全过章数变 1
    reset()
    submit_diagnostic({"s1"})
    take_chapter_quiz("s1c1", right=True)
    rows = stage_rows(profile.overview(COURSE))
    check("全过后不再 challenged", rows["s1"]["challenged"] is False, rows["s1"])
    check("全过章数记 1", rows["s1"]["quiz"]["fully_passed"] == 1, rows["s1"]["quiz"])
    check("跳过标记还在", rows["s1"]["skip"] is True)


def test_stale():
    print("\n[过期：超过 30 天打「该复核」，但不自动作废]")
    reset()
    submit_diagnostic({"s1"})
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("UPDATE profile_skills SET updated_at=datetime('now','-40 days')")
    conn.commit()
    conn.close()
    rows = stage_rows(profile.overview(COURSE))
    check("40 天前测的标 stale", rows["s1"]["stale"] is True, rows["s1"])
    check("stale 不影响等级", rows["s1"]["level"] == 2, rows["s1"]["level"])
    check("stale 不影响跳过", rows["s1"]["skip"] is True, rows["s1"])
    check("age_days 是 40", rows["s1"]["age_days"] == 40, rows["s1"]["age_days"])


def test_tutor_context():
    print("\n[导师能看到具体等级，而不只是「跳过哪几段」]")
    reset()
    submit_diagnostic({"s1", "s2"})
    take_chapter_quiz("s1c1", right=False)
    text = course.tutor_context(COURSE, "s3c1")
    check("写了能力画像", "能力画像" in text, text[:300])
    check("写了等级和来源", "2 级" in text and "摸底诊断" in text, text)
    check("点出实测推翻的那一段", "章节实测有题没过" in text, text)
    reset()
    text2 = course.tutor_context(COURSE, "s1c1")
    check("没测过时不说画像", "能力画像" not in text2, text2[:200])


def test_question_stage():
    print("\n[问题定位：关键词表，不调模型]")
    cases = {
        "ReAct 是什么？为什么 agent 需要它？": "s4",
        "token 和上下文窗口是什么关系": "s1",
        "怎么让模型调用工具": "s3",
        "RAG 怎么做": "s6",
        "怎么评测一个 agent": "s8",
        "记忆系统怎么设计": "s5",
        "多智能体值得上吗": "s9",
        "今天天气怎么样": "",
        "帮我写个 Python 函数": "",
    }
    for question, expect in cases.items():
        got = profile._stage_by_keywords(question)
        check("定位「%s」" % question, got == expect, got)
    check("英文词按词边界", profile._stage_by_keywords("reaction 是什么") == "", 
          profile._stage_by_keywords("reaction 是什么"))
    check("平票取更靠前的段", profile._stage_by_keywords("记忆和检索") in {"s5", "s6"},
          profile._stage_by_keywords("记忆和检索"))


def test_style_hint():
    print("\n[对话口径：一句话，只给本题这一档]")
    reset()
    check("没测过返回空串", profile.style_hint(COURSE) == "", profile.style_hint(COURSE))
    check("没测过也不给口径", profile.style_hint(COURSE, with_levels=False) == "")

    submit_diagnostic({"s4"})
    text = profile.style_hint(COURSE, question="ReAct 是什么？为什么 agent 需要它？")
    check("定位到本题段落", "本题属第 4 段 Agent 循环" in text, text)
    check("说了本次几级", "他 2 级" in text, text)
    check("按 2 级的格式写", "定义和适用边界" in text and "最短可跑代码" in text, text)
    check("明令禁 emoji 与表格", "不要 emoji" in text and "表格" in text, text)
    check("明令不许提等级", "也不要提等级" in text, text)
    check("一句话就说清，不列等级表", "内部记录" not in text and len(text) < 200, len(text))

    other = profile.style_hint(COURSE, question="token 和上下文窗口是什么关系")
    check("问没测过的段就按入门讲", "第 1 段 地基" in other and "还没测过" in other, other)
    check("换成入门的写法", "一段话，150 字内" in other, other)
    check("不拿 s4 的档答 s1 的问题", "他 2 级" not in other, other)

    chat = profile.style_hint(COURSE, question="今天天气怎么样")
    check("闲聊不定位", "没定位到题目属于哪一段" in chat, chat)
    check("闲聊放行为正常回答", "不是课程内容就正常回答" in chat, chat)

    same = profile.style_hint(COURSE, with_levels=False, question="ReAct 是什么")
    check("旧参数不影响输出", same == text, same[:80])

    # 诊断判 2 级、章节实测没过：这一段必须按 1 级讲，否则正好讲在他卡住的地方
    reset()
    submit_diagnostic({"s1"})
    take_chapter_quiz("s1c1", right=False)
    text2 = profile.style_hint(COURSE, question="token 是什么")
    check("实测没过就降档", "他 1 级" in text2, text2)
    check("降档后用入门写法", "一段话，150 字内" in text2, text2)


def test_style_stale():
    print("\n[画像过期：口径里点一句，不当成刚测的结论]")
    reset()
    submit_diagnostic({"s1"})
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("UPDATE profile_skills SET updated_at = datetime('now','-40 days')")
    conn.commit()
    conn.close()
    text = profile.style_hint(COURSE, question="token 是什么")
    check("点了测得较早", "测得较早" in text, text)
    check("仍然给档位", "他 2 级" in text, text)


def test_chat_context():
    print("\n[装配：问答与导师都带一句口径，没画像就不带]")
    import main as app_main

    reset()
    submit_diagnostic({"s1"})
    ctx = app_main._chat_context(app_main.Message(text="token 是什么", chat_id=999999, mode="chat"))
    check("问答模式带口径", ctx["profile_context"].startswith("【讲解格式"), ctx["profile_context"][:80])
    check("问答模式按问题定位", "本题属第 1 段" in ctx["profile_context"], ctx["profile_context"][:120])

    tutor = app_main._chat_context(app_main.Message(
        text="这一章怎么学", chat_id=999999, mode="tutor", chapter_id="s1c1"))
    check("导师模式退回当前章节", "本题属第 1 段" in tutor["profile_context"],
          tutor["profile_context"][:120])

    reset()
    empty = app_main._chat_context(app_main.Message(text="token 是什么", chat_id=999999, mode="chat"))
    check("没画像时是空串", empty["profile_context"] == "", empty["profile_context"][:120])


def test_missing_table():
    print("\n[旧库没有画像表：不崩，当全未测]")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    old = database.DB_PATH
    database.DB_PATH = tmp.name
    try:
        data = profile.overview(COURSE)  # 故意不 init_db
        check("仍能出 10 行", len(data["stages"]) == 10, len(data["stages"]))
        check("等级全 0", all(row["level"] == 0 for row in data["stages"]))
        check("last 为 None", data["last"] is None, data["last"])
        check("没有画像表时对话不注入", profile.style_hint(COURSE) == "")
    finally:
        database.DB_PATH = old
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def test_http():
    print("\n[接口：要登录，返回结构对]")
    from fastapi.testclient import TestClient

    import main as app_main

    client = TestClient(app_main.app)
    check("没登录取不到画像", client.get("/profile").status_code == 401)
    client.cookies.set(app_main.SESSION_COOKIE, app_main.create_session_token())
    resp = client.get("/profile")
    body = resp.json()
    check("登录后 200", resp.status_code == 200, resp.status_code)
    check("带 10 段", len(body.get("stages") or []) == 10, len(body.get("stages") or []))
    check("带过期天数", body.get("stale_days") == profile.STALE_DAYS, body.get("stale_days"))
    check("和库里一致",
          any(row["level"] > 0 for row in body["stages"]) == bool(
              database.list_profile_skills(COURSE)))


def _rows(table):
    conn = sqlite3.connect(database.DB_PATH)
    try:
        return conn.execute("SELECT * FROM %s" % table).fetchall()
    finally:
        conn.close()


def main_():
    path, old = use_temp_db()
    try:
        test_empty()
        test_after_diagnostic()
        test_challenged()
        test_stale()
        test_tutor_context()
        test_question_stage()
        test_style_hint()
        test_style_stale()
        test_chat_context()
        test_missing_table()
        test_http()
    finally:
        database.DB_PATH = old
        try:
            os.unlink(path)
        except OSError:
            pass
    print("\n%s 通过 %d，失败 %d" % ("FAIL" if FAIL else "ALL PASS", PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
