"""答题记录与能力画像的离线测试：建表、迁移、读写、边界。

不联网、不调模型。每个用例用自己的临时库，不碰 chats.db 里的真实数据。

这一步只验证「事实能不能正确存取」。判分规则（quiz.py）与分级规则（profile.py）
不在本文件范围内——那两层要能在不重建表的前提下反复调阈值，所以先钉死表的行为。
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database

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


def restore(old, path):
    database.DB_PATH = old
    try:
        os.unlink(path)
    except OSError:
        pass


def _columns(path, table):
    conn = sqlite3.connect(path)
    rows = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
    conn.close()
    return [r[1] for r in rows]


def _tables(path):
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    return {r[0] for r in rows}


# ---------- 建表 ----------

def test_schema():
    print("\n[建表]")
    path, old = use_temp_db()
    try:
        tables = _tables(path)
        check("quiz_attempts 建好了", "quiz_attempts" in tables)
        check("profile_skills 建好了", "profile_skills" in tables)
        check("diagnostics 建好了", "diagnostics" in tables)

        columns = _columns(path, "quiz_attempts")
        for name in ("course_id", "chapter_id", "item_id", "kind", "answer",
                     "score", "passed", "grader", "detail", "seconds", "flagged"):
            check("quiz_attempts 有 %s 列" % name, name in columns, columns)

        # passed 必须单独成列：阈值会调，历史结论不能跟着变
        check("passed 不与 score 合并", "passed" in columns)
        check("profile_skills 是阶段粒度", "skill" in _columns(path, "profile_skills"))

        conn = sqlite3.connect(path)
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        conn.close()
        check("按章查作答有索引", "idx_quiz_attempts_ch" in indexes, indexes)
        check("按题查作答有索引", "idx_quiz_attempts_item" in indexes)
        check("诊断按课程查有索引", "idx_diagnostics_course" in indexes)

        # init_db 要能反复跑（每次启动都会调一次）
        database.init_db()
        database.init_db()
        check("init_db 可重复执行", "quiz_attempts" in _tables(path))
    finally:
        restore(old, path)


# ---------- 旧库迁移 ----------

def test_migration():
    print("\n[旧库迁移]")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = tmp.name
    old = database.DB_PATH
    try:
        # 造一个上线前的库：course_progress 只有老四列，且已经有真实进度
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE course_progress(
                course_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                status TEXT DEFAULT 'todo',
                mastery INTEGER DEFAULT 0,
                notes TEXT DEFAULT '',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (course_id, chapter_id)
            )
        """)
        conn.execute(
            "INSERT INTO course_progress(course_id,chapter_id,status,mastery,notes) "
            "VALUES('agent-dev','s1c1','done',3,'早先手点的')"
        )
        conn.commit()
        conn.close()

        database.DB_PATH = path

        # 还没跑 init_db 就读：必须不抛，缺列当「不知道」。
        # 这条是给「静默吞异常」兜底的——tutor_context 有个 try/except，
        # 查询一抛错就退化成「不知道你在第几章」，症状很隐蔽。
        before = database.list_course_progress("agent-dev")
        check("旧库未迁移也能读", before.get("s1c1", {}).get("status") == "done", before)
        check("缺列读成空而不是报错", before["s1c1"]["quiz_score"] is None, before)

        database.init_db()

        columns = _columns(path, "course_progress")
        check("补上了 quiz_score 列", "quiz_score" in columns, columns)
        check("补上了 quiz_at 列", "quiz_at" in columns)

        rows = database.list_course_progress("agent-dev")
        check("旧进度没丢", rows.get("s1c1", {}).get("status") == "done", rows)
        check("旧 mastery 没被改写", rows["s1c1"]["mastery"] == 3, rows["s1c1"])
        check("旧 notes 没被改写", rows["s1c1"]["notes"] == "早先手点的")
        check("新列默认是空", rows["s1c1"]["quiz_score"] is None)
        check("新列时间也是空", rows["s1c1"]["quiz_at"] is None)

        # 迁移后立刻就能写新字段
        result = database.set_course_progress("agent-dev", "s1c1", quiz_score=80, quiz_at="2026-11-25")
        check("迁移后可写 quiz_score", result["quiz_score"] == 80, result)
        check("迁移后 status 不被顺手改掉", result["status"] == "done", result)
    finally:
        restore(old, path)


# ---------- 作答记录 ----------

def test_attempts():
    print("\n[作答记录]")
    path, old = use_temp_db()
    try:
        new_id = database.add_quiz_attempt(
            "agent-dev", "s1c3", "s1c3-q2", "blank", "max_tokens", 100, True, "local", "全对", 42)
        check("返回新行 id", isinstance(new_id, int) and new_id > 0, new_id)

        rows = database.list_quiz_attempts("agent-dev", "s1c3")
        check("能读回来", len(rows) == 1, rows)
        row = rows[0]
        check("answer 原样保存", row["answer"] == "max_tokens", row["answer"])
        check("score 是整数", row["score"] == 100)
        check("passed 落成布尔", row["passed"] is True)
        check("grader 记下来源", row["grader"] == "local")
        check("detail 保留依据", row["detail"] == "全对")
        check("seconds 记下用时", row["seconds"] == 42)
        check("flagged 默认是假", row["flagged"] is False)

        # 越界与类型兜底：前端传什么都不能把库写坏
        database.add_quiz_attempt("agent-dev", "s1c3", "s1c3-q1", "choice", "B", 168, 1, "local")
        database.add_quiz_attempt("agent-dev", "s1c3", "s1c3-q1", "choice", "A", -20, 0, "local")
        scores = [r["score"] for r in database.list_quiz_attempts("agent-dev", "s1c3", "s1c3-q1")]
        check("score 上限夹到 100", max(scores) == 100, scores)
        check("score 下限夹到 0", min(scores) == 0, scores)

        # 长答案不截断（要点判分要看完整作答）
        long_answer = "token " * 400
        database.add_quiz_attempt("agent-dev", "s1c3", "s1c3-q3", "rubric", long_answer, 70, 1, "local")
        stored = database.list_quiz_attempts("agent-dev", "s1c3", "s1c3-q3")
        check("长答案不被截断", stored[0]["answer"] == long_answer, len(stored[0]["answer"]))

        # 过滤
        database.add_quiz_attempt("agent-dev", "s4c1", "s4c1-q1", "choice", "C", 100, 1, "local")
        check("按章过滤只回本章", len(database.list_quiz_attempts("agent-dev", "s1c3")) == 4)
        check("不传章就是全课", len(database.list_quiz_attempts("agent-dev")) == 5)
        check("按题过滤", len(database.list_quiz_attempts("agent-dev", item_id="s1c3-q1")) == 2)
        check("limit 生效", len(database.list_quiz_attempts("agent-dev", limit=2)) == 2)

        # 倒序：最新的在最前
        ordered = database.list_quiz_attempts("agent-dev")
        check("按时间倒序", ordered[0]["chapter_id"] == "s4c1", ordered[0])

        # 别的课程不串
        check("跨课程隔离", database.list_quiz_attempts("other-course") == [])
    finally:
        restore(old, path)


def test_flags():
    print("\n[申诉标记]")
    path, old = use_temp_db()
    try:
        attempt_id = database.add_quiz_attempt(
            "agent-dev", "s1c3", "s1c3-q3", "rubric", "答了但被判错", 30, 0, "model", "漏了要点")
        check("能标为判错", database.flag_quiz_attempt(attempt_id) is True)
        row = database.list_quiz_attempts("agent-dev", "s1c3")[0]
        check("标记落库", row["flagged"] is True)
        check("标记不改分数", row["score"] == 30, row["score"])
        check("能取消标记", database.flag_quiz_attempt(attempt_id, 0) is True)
        check("取消后回落", database.list_quiz_attempts("agent-dev", "s1c3")[0]["flagged"] is False)
        check("不存在的记录返回假", database.flag_quiz_attempt(999999) is False)
    finally:
        restore(old, path)


def test_latest_scores():
    print("\n[每章最近成绩]")
    path, old = use_temp_db()
    try:
        check("空库返回空字典", database.latest_quiz_scores("agent-dev") == {})

        database.add_quiz_attempt("agent-dev", "s1c3", "s1c3-q1", "choice", "A", 100, 1, "local")
        database.add_quiz_attempt("agent-dev", "s1c3", "s1c3-q2", "blank", "x", 40, 0, "local")
        database.add_quiz_attempt("agent-dev", "s4c1", "s4c1-q1", "choice", "B", 100, 1, "local")

        scores = database.latest_quiz_scores("agent-dev")
        check("每章一行", len(scores) == 2, scores)
        check("取最近一次而不是最高分", scores["s1c3"]["score"] == 40, scores["s1c3"])
        check("别的章不受影响", scores["s4c1"]["score"] == 100)
        check("带时间戳", bool(scores["s1c3"]["at"]))
        check("跨课程隔离", database.latest_quiz_scores("other-course") == {})
    finally:
        restore(old, path)


# ---------- 画像 ----------

def test_profile():
    print("\n[能力画像]")
    path, old = use_temp_db()
    try:
        check("没测过返回空", database.list_profile_skills("agent-dev") == {})

        database.upsert_profile_skill("agent-dev", "s3", 2, "诊断卷 6/8", "diagnostic")
        skills = database.list_profile_skills("agent-dev")
        check("写入成功", skills["s3"]["level"] == 2, skills)
        check("留存依据", skills["s3"]["evidence"] == "诊断卷 6/8")
        check("来源记下", skills["s3"]["source"] == "diagnostic")

        # 重测覆盖上一次：画像是「现在」，不是档案
        database.upsert_profile_skill("agent-dev", "s3", 3, "重测 8/8", "diagnostic")
        skills = database.list_profile_skills("agent-dev")
        check("重测覆盖水平", skills["s3"]["level"] == 3, skills)
        check("重测覆盖依据", skills["s3"]["evidence"] == "重测 8/8")
        check("不会写成两行", len(skills) == 1, skills)

        database.upsert_profile_skill("agent-dev", "s5", 9)
        check("level 上限夹到 3", database.list_profile_skills("agent-dev")["s5"]["level"] == 3)
        database.upsert_profile_skill("agent-dev", "s6", -4)
        check("level 下限夹到 0", database.list_profile_skills("agent-dev")["s6"]["level"] == 0)

        database.upsert_profile_skill("other-course", "s3", 1)
        check("跨课程隔离", len(database.list_profile_skills("agent-dev")) == 3)
    finally:
        restore(old, path)


def test_diagnostics():
    print("\n[诊断记录]")
    path, old = use_temp_db()
    try:
        check("没测过返回 None", database.latest_diagnostic("agent-dev") is None)

        first = database.save_diagnostic("agent-dev", 20, 9, '{"q1":"A"}')
        check("返回新行 id", isinstance(first, int) and first > 0)
        second = database.save_diagnostic("agent-dev", 20, 16, '{"q1":"B"}')

        latest = database.latest_diagnostic("agent-dev")
        check("取的是最近一次", latest["id"] == second, latest)
        check("总分记下", latest["total"] == 20)
        check("答对数记下", latest["correct"] == 16)
        check("payload 能回溯", latest["payload"] == '{"q1":"B"}')
        check("完成时间有值", bool(latest["finished_at"]))
        check("跨课程隔离", database.latest_diagnostic("other-course") is None)
    finally:
        restore(old, path)


# ---------- 与课程进度的衔接 ----------

def test_progress_link():
    print("\n[与课程进度衔接]")
    path, old = use_temp_db()
    try:
        result = database.set_course_progress("agent-dev", "s1c1", status="learning")
        check("只传 status 时不写成绩", result["quiz_score"] is None, result)
        check("返回结构完整", set(result) >= {"status", "mastery", "notes", "quiz_score", "quiz_at"})

        result = database.set_course_progress("agent-dev", "s1c1", quiz_score=85, quiz_at="2026-11-25 10:00")
        check("只传成绩时不改 status", result["status"] == "learning", result)
        check("成绩写进去", result["quiz_score"] == 85)
        check("时间写进去", result["quiz_at"] == "2026-11-25 10:00")

        result = database.set_course_progress("agent-dev", "s1c1", quiz_score=130)
        check("成绩同样夹到 100", result["quiz_score"] == 100, result)

        rows = database.list_course_progress("agent-dev")
        check("列表里带成绩", rows["s1c1"]["quiz_score"] == 100, rows["s1c1"])

        # 手点「学完」和「答过题」互不覆盖，这是两条独立事实
        result = database.set_course_progress("agent-dev", "s1c1", status="done", mastery=2)
        check("标学完不清空成绩", result["quiz_score"] == 100, result)
        check("成绩还在", database.list_course_progress("agent-dev")["s1c1"]["quiz_score"] == 100)
    finally:
        restore(old, path)


def main_():
    test_schema()
    test_migration()
    test_attempts()
    test_flags()
    test_latest_scores()
    test_profile()
    test_diagnostics()
    test_progress_link()
    print("\n%s 通过 %d，失败 %d" % ("FAIL" if FAIL else "ALL PASS", PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
