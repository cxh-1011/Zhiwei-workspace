"""掌握度回写的离线测试。

这是第一次改到「已有进度」的规则，所以验的不只是「答对能升 2」，
更要验「判分不会把进度打回去」：status 和 mastery 是两件事，
答错一题该降的是掌握度，不是「学没学过」。
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import course
import database
import quiz

PASS = 0
FAIL = 0
COURSE = "agent-dev"
CHAPTER = "s1c1"      # 有题库：选择 + 填空 + 要点
EMPTY = "s1c2"        # 还没写题库


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


def reset_db():
    """每个用例前清空作答与进度。

    只重置 status/mastery 不够：quiz_attempts 里留着上一条用例的作答，
    chapter_state 会把它们算进来，于是「没答过题」的用例拿到的却是全过的状态。
    这种残留会让用例之间互相污染，而且失败信息完全指不到原因。
    """
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("DELETE FROM quiz_attempts")
    conn.execute("DELETE FROM course_progress")
    conn.commit()
    conn.close()


def own(item):
    """这道题的正确作答。"""
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
        elif isinstance(point, str):
            words.append(point)
    return "，".join(words)


def items():
    return quiz.load_quiz(CHAPTER)["items"]


def answer_all(correct=True):
    """把三题都答一遍，返回最后一次的 state。"""
    state = None
    for item in items():
        _, state = quiz.record(CHAPTER, item, own(item) if correct else "zzz")
    return state


def test_no_attempt_no_change():
    print("\n[没有作答时不动]")
    reset_db()
    state = quiz.chapter_state(CHAPTER)
    check("状态显示 0 题已答", state["answered"] == 0, state["answered"])
    check("推不出掌握度", quiz.mastery_target(state) is None)
    result = quiz.apply_mastery(CHAPTER, state)
    check("不改动", result["changed"] is False and result["after"] == 0, result)
    check("不给没答过的章建行", database.list_course_progress(COURSE).get(CHAPTER, {}).get("mastery", 0) == 0)


def test_partial_keeps_level():
    print("\n[只答对一题：不升不降]")
    reset_db()
    first = items()[0]
    quiz.record(CHAPTER, first, own(first))
    state = quiz.chapter_state(CHAPTER)
    check("已答 1 题", state["answered"] == 1, state["answered"])
    check("一题不足以升级", quiz.mastery_target(state) is None, quiz.mastery_target(state))
    check("掌握度还是 0", database.list_course_progress(COURSE)[CHAPTER]["mastery"] == 0)


def test_all_pass_promotes():
    print("\n[三题全过：升到 2]")
    reset_db()
    state = answer_all(correct=True)
    check("三题都算过", state["passed"] == 3 and state["answered"] == 3, state)
    check("掌握度升到 2", state["mastery"]["after"] == 2, state["mastery"])
    check("记录里写了变更", state["mastery"]["changed"] is True)
    check("库里是 2", database.list_course_progress(COURSE)[CHAPTER]["mastery"] == 2)
    # 判分不该顺手改「学没学过」
    check("不动 status", (database.list_course_progress(COURSE)[CHAPTER]["status"]) == "todo")


def test_fail_demotes_but_keeps_progress():
    print("\n[全过之后答错：降掌握度，不退进度]")
    reset_db()
    database.set_course_progress(COURSE, CHAPTER, status="done", mastery=2)
    # 直接给一个「已过关」的章节字典：load_course 不带数据库进度，
    # 拿它去问 _is_passed 一定是 False，那是测试写错了，不是代码错了
    check("前提：这一章是过关状态", course._is_passed({"status": "done", "mastery": 2}))

    wrong = items()[1]
    _, state = quiz.record(CHAPTER, wrong, "zzz")
    check("答错一题后掌握度降到 1", state["mastery"]["after"] == 1, state["mastery"])
    check("掌握度确实写了库", database.list_course_progress(COURSE)[CHAPTER]["mastery"] == 1)

    decorated = course.decorate(course.load_course(), COURSE)
    target = next(c for c in decorated["chapters"] if c["id"] == CHAPTER)
    check("仍然算过关", course._is_passed(target), target)
    check("进度条没有回退", decorated["progress"]["done"] >= 1, decorated["progress"])
    check("推荐不会退回到这一章", (decorated["next"] or {}).get("id") != CHAPTER, decorated["next"])


def test_retry_and_relapse():
    print("\n[重做覆盖上一次结论]")
    reset_db()
    answer_all(correct=True)
    bad = items()[2]
    _, state = quiz.record(CHAPTER, bad, "我不知道")
    check("重做答错后算没过", state["passed"] == 2, state["passed"])
    check("掌握度降回 1", state["mastery"]["after"] == 1, state["mastery"])

    quiz.record(CHAPTER, bad, own(bad))
    state = quiz.chapter_state(CHAPTER)
    check("再答对又回 2", state["mastery"]["after"] == 2 if "mastery" in state else
          quiz.mastery_target(state) == 2, state.get("mastery"))
    # 这一条断言的是「最近一次」，不是「历史最高」
    check("三题仍然全过", state["passed"] == 3, state["passed"])


def test_level3_not_downgraded():
    print("\n[人评的 3 级不被判分压低]")
    reset_db()
    database.set_course_progress(COURSE, CHAPTER, status="done", mastery=3)
    state = answer_all(correct=True)
    check("全过时保持 3", state["mastery"]["after"] == 3, state["mastery"])
    check("没写库也不报错", database.list_course_progress(COURSE)[CHAPTER]["mastery"] == 3)


def test_manual_done_cap():
    print("\n[手点「学完」只能拿到 1]")
    reset_db()
    check("没答过题：1 级", quiz.manual_done_mastery(CHAPTER) == 1)
    answer_all(correct=True)
    check("三题全过：2 级", quiz.manual_done_mastery(CHAPTER) == 2)

    reset_db()
    first = items()[0]
    quiz.record(CHAPTER, first, own(first))
    check("只答对一题：仍是 1 级", quiz.manual_done_mastery(CHAPTER) == 1)


def test_chapter_without_quiz():
    print("\n[没有题库的章]")
    reset_db()
    # 33 章现在都有题库，拿真实章 id 当「没题库」的样本已经不成立了。
    # 把题库根指到一个空目录，才是真的在验这条分支。
    root = quiz.CURRICULUM_ROOT
    quiz.CURRICULUM_ROOT = Path(tempfile.mkdtemp())
    quiz._TOTALS_CACHE.clear()
    try:
        state = quiz.chapter_state(EMPTY)
        check("题目数为 0", state["total"] == 0, state["total"])
        check("推不出掌握度", quiz.mastery_target(state) is None)
        check("手点学完给 1 级", quiz.manual_done_mastery(EMPTY) == 1)
        check("不改库里的掌握度", quiz.apply_mastery(EMPTY, state)["changed"] is False)
    finally:
        quiz.CURRICULUM_ROOT = root
        quiz._TOTALS_CACHE.clear()


def test_cross_course_isolation():
    print("\n[跨课程不串]")
    reset_db()
    other = "another-course"
    item = items()[0]
    database.add_quiz_attempt(other, CHAPTER, item["id"], item["kind"], own(item), 100, 1, "local", "")
    state = quiz.chapter_state(CHAPTER)
    check("另一门课的成绩不算进本课", state["answered"] == 0, state["answered"])
    check("另一门课不触发升级", quiz.apply_mastery(CHAPTER, state)["changed"] is False)


def test_return_shape():
    print("\n[record 的返回结构]")
    reset_db()
    item = items()[0]
    result, state = quiz.record(CHAPTER, item, own(item), seconds=9)
    check("判分结果没变", result["score"] == 100 and result["passed"] is True, result)
    check("状态里带掌握度三段", set(state["mastery"]) >= {"before", "after", "changed", "reason"},
          state.get("mastery"))
    check("状态里带题目明细", len(state["items"]) == 3, len(state.get("items") or []))


def main_():
    path, old = use_temp_db()
    try:
        test_no_attempt_no_change()
        test_partial_keeps_level()
        test_all_pass_promotes()
        test_fail_demotes_but_keeps_progress()
        test_retry_and_relapse()
        test_level3_not_downgraded()
        test_manual_done_cap()
        test_chapter_without_quiz()
        test_cross_course_isolation()
        test_return_shape()
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
