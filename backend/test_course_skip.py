"""诊断跳章的离线测试：跳过判定、前置传导、实测推翻、与既有行为兼容。

不联网、不调模型。画像写在临时库里，不动 chats.db。
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import course
import database

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


def fresh_chapters():
    """每次都重新读一遍：decorate 会原地改章节，复用会让上一条用例的标记漏进来。"""
    return course.load_course(COURSE)["chapters"]


def decorate():
    """学习台真实的读取路径。load_course 不带库里的进度，
    拿它直接问 recommend_next 会得到「一章没学」，那不是被测代码的问题。"""
    return course.decorate(course.load_course(COURSE), COURSE)


def set_levels(levels):
    for stage, level in levels.items():
        database.upsert_profile_skill(COURSE, stage, level, "测试判定", "diagnostic")


def test_no_profile():
    print("\n[没测过：行为与加跳章之前完全一致]")
    chapters = fresh_chapters()
    check("没画像时 skip_stages 为空", course.skip_stages(COURSE) == {},
          course.skip_stages(COURSE))
    skipped = course.mark_skipped(chapters, {})
    check("没有一章被跳过", skipped == [], skipped)
    check("每章都盖了 False（不是删键）", all(c["skipped"] is False for c in chapters))
    nxt = course.recommend_next(chapters)
    check("推荐第 1 章", nxt and nxt["no"] == 1, nxt and nxt["no"])


def test_level_one_does_not_skip():
    print("\n[1 级「听过」不跳]")
    set_levels({"s1": 1})
    chapters = fresh_chapters()
    skipped = course.mark_skipped(chapters, course.skip_stages(COURSE))
    check("判 1 级不跳", skipped == [], skipped)
    check("skip_stages 不收 1 级", course.skip_stages(COURSE) == {},
          course.skip_stages(COURSE))
    nxt = course.recommend_next(chapters)
    check("仍然推荐第 1 章", nxt and nxt["no"] == 1, nxt and nxt["no"])


def test_skip_stage_one():
    print("\n[s1 判 2 级：整段跳过，推荐直接到 s2]")
    database.DB_PATH and None
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("DELETE FROM profile_skills")
    conn.commit()
    conn.close()
    set_levels({"s1": 2})

    pages = course.skip_stages(COURSE)
    check("s1 进跳过集", set(pages) == {"s1"}, pages)
    check("带上了等级", pages["s1"]["level"] == 2, pages["s1"])

    chapters = fresh_chapters()
    skipped = course.mark_skipped(chapters, course.skip_stages(COURSE))
    check("s1 三章全跳", sorted(skipped) == ["s1c1", "s1c2", "s1c3"], skipped)
    check("s2 第一章不在里面", "s2c1" not in skipped)

    satisfied = course.satisfied_ids(chapters)
    check("三章都算满足", {"s1c1", "s1c2", "s1c3"} <= satisfied, sorted(satisfied))

    data = decorate()
    check("接口里报出 3 章跳过", sorted(data["skip"]["chapters"]) == ["s1c1", "s1c2", "s1c3"],
          data["skip"]["chapters"])
    check("progress.skipped 是 3", data["progress"]["skipped"] == 3,
          data["progress"]["skipped"])

    nxt = data["next"]
    check("推荐跳到第 4 章", nxt and nxt["no"] == 4, nxt and (nxt["no"], nxt["id"]))

    # 这是整个功能的重点：测出地基会了，推荐不能还把人拉回第 1 章
    check("不再推荐第 1 章", nxt and nxt["id"] != "s1c1")


def test_skip_needs_prereq():
    print("\n[跳过必须沿依赖传导：s2 判满分但 s1 没学]")
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("DELETE FROM profile_skills")
    conn.commit()
    conn.close()
    set_levels({"s1": 0, "s2": 2})

    chapters = fresh_chapters()
    skipped = course.mark_skipped(chapters, course.skip_stages(COURSE))
    check("s2 三章被标跳过", sorted(skipped) == ["s2c1", "s2c2", "s2c3"], skipped)

    satisfied = course.satisfied_ids(chapters)
    check("但 s2 一章都不算满足", not ({"s2c1", "s2c2", "s2c3"} & satisfied),
          sorted(satisfied))
    check("s1 也没被顶掉", "s1c1" not in satisfied)

    nxt = decorate()["next"]
    # 诊断说 s2 会、s1 不会，这在真实里几乎不可能（s2 依赖 s1），
    # 但算法不能被这种数据带偏成「越过没学的地基直接进 s2」
    check("仍然从第 1 章开始", nxt and nxt["no"] == 1, nxt and nxt["no"])


def test_chain_after_prereq_learned():
    print("\n[学完 s1 后，跳过的 s2 才开始生效]")
    for chapter_id in ("s1c1", "s1c2", "s1c3"):
        database.set_course_progress(COURSE, chapter_id, status="done", mastery=2)

    data = decorate()
    satisfied = course.satisfied_ids(data["chapters"])
    check("s2 仍标跳过", "s2c1" in data["skip"]["chapters"], data["skip"]["chapters"])
    check("s2 现在算满足", {"s2c1", "s2c2", "s2c3"} <= satisfied, sorted(satisfied))
    nxt = data["next"]
    check("推荐第 7 章（s3 开头）", nxt and nxt["no"] == 7,
          nxt and (nxt["no"], nxt["id"]))

    for chapter_id in ("s1c1", "s1c2", "s1c3"):
        database.set_course_progress(COURSE, chapter_id, status="todo", mastery=0)


def test_quiz_overrides_diagnostic():
    print("\n[实测推翻诊断：有题没过就不跳]")
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("DELETE FROM profile_skills")
    conn.commit()
    conn.close()
    set_levels({"s1": 2})

    chapters = fresh_chapters()
    counts = {"s1c1": {"answered": 3, "passed": 1}}
    skipped = course.mark_skipped(chapters, course.skip_stages(COURSE), counts)
    check("s1c1 不跳（有题没过）", "s1c1" not in skipped, skipped)
    check("s1c1 的标记是 False", next(c for c in chapters if c["id"] == "s1c1")["skipped"] is False)

    # s1c2、s1c3 的「跳过」标记还是 True——标记记的是「诊断说这段可跳」这个事实，
    # 传导（能不能真的绕过去）由 satisfied_ids 负责。两件事分开，
    # 才能在界面上说清「这段你答错了一题，所以没跳成」。
    check("s1c2 标记仍为 True", next(c for c in chapters if c["id"] == "s1c2")["skipped"] is True)
    satisfied = course.satisfied_ids(chapters)
    check("但 s1c1 断了，后面也满足不了", not ({"s1c1", "s1c2", "s1c3"} & satisfied),
          sorted(satisfied))

    # 一题没答的章不触发这条：没答不等于答错
    counts2 = {"s1c1": {"answered": 0, "passed": 0}}
    skipped2 = course.mark_skipped(fresh_chapters(), course.skip_stages(COURSE), counts2)
    check("一题没答不算没过", "s1c1" in skipped2, skipped2)

    # 全过的章不加阻力
    counts3 = {"s1c1": {"answered": 3, "passed": 3}}
    skipped3 = course.mark_skipped(fresh_chapters(), course.skip_stages(COURSE), counts3)
    check("全过照常跳过", sorted(skipped3) == ["s1c1", "s1c2", "s1c3"], skipped3)


def test_learning_wins():
    print("\n[手点「在学」优先于跳过]")
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("DELETE FROM profile_skills")
    conn.commit()
    conn.close()
    set_levels({"s1": 2})

    database.set_course_progress(COURSE, "s1c2", status="learning")
    data = decorate()
    chapter = next(c for c in data["chapters"] if c["id"] == "s1c2")
    check("s1c2 仍被标跳过", chapter["skipped"] is True)
    # 他自己点进去学了，就该推这一章：诊断是推断，手点是动作
    check("但推荐仍推 s1c2", data["next"] and data["next"]["id"] == "s1c2",
          data["next"] and data["next"]["id"])
    database.set_course_progress(COURSE, "s1c2", status="todo")


def test_blocked_by():
    print("\n[前置提示：跳过的前置不再报]")
    chapters = decorate()["chapters"]
    by_id = {c["id"]: c for c in chapters}
    missing = course.blocked_by(chapters, by_id["s2c1"])
    check("s2c1 不再提示补 s1c3", missing == [], missing)

    # 对照：没跳过的 s3c1 前置是 s2c3，s2 没跳 → 仍然报
    missing2 = course.blocked_by(chapters, by_id["s3c1"])
    check("没跳过的前置照常报", len(missing2) == 1 and missing2[0]["id"] == "s2c3",
          [m["id"] for m in missing2])


def test_all_satisfied():
    print("\n[全部满足：next 为 None，但进度条不动]")
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("DELETE FROM profile_skills")
    conn.execute("DELETE FROM course_progress")
    conn.commit()
    conn.close()
    set_levels({stage: 2 for stage in (
        "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10")})

    decorated = course.decorate(course.load_course(COURSE), COURSE)
    check("next 为 None", decorated["next"] is None, decorated["next"])
    check("33 章全标跳过", decorated["progress"]["skipped"] == 33,
          decorated["progress"]["skipped"])
    check("done 仍是 0", decorated["progress"]["done"] == 0, decorated["progress"]["done"])
    check("percent 仍是 0", decorated["progress"]["percent"] == 0,
          decorated["progress"]["percent"])
    check("skip 里有 10 个阶段", decorated["skip"] and len(decorated["skip"]["stages"]) == 10,
          decorated["skip"] and len(decorated["skip"]["stages"]))
    check("阶段带中文名",
          all(s["title"] and s["title"] != s["id"] for s in decorated["skip"]["stages"]),
          decorated["skip"]["stages"][:2])

    # 跳过绝不能写进进度表：写了进度条会凭空跳到 100%，而正文一章没读
    progress = database.list_course_progress(COURSE)
    check("跳章不写 course_progress", progress == {}, progress)


def test_out_of_order():
    print("\n[章节乱序也传导正确]")
    chapters = decorate()["chapters"]
    reversed_chapters = list(reversed(chapters))
    satisfied = course.satisfied_ids(reversed_chapters)
    check("倒序后 s1 仍全满足",
          {"s1c1", "s1c2", "s1c3", "s2c1", "s2c2", "s2c3"} <= satisfied,
          sorted(satisfied)[:8])


def test_missing_table():
    print("\n[画像表不存在时不崩]")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    old = database.DB_PATH
    database.DB_PATH = tmp.name
    try:
        # 故意不 init_db：模拟旧库还没迁移
        check("skip_stages 返回空而不是抛错", course.skip_stages(COURSE) == {},
              course.skip_stages(COURSE))
        chapters = fresh_chapters()
        check("无画像时照常推荐", (course.recommend_next(chapters) or {}).get("no") == 1)
    finally:
        database.DB_PATH = old
        try:
            os.unlink(tmp.name)
        except OSError:
            pass  # Windows 上 sqlite 句柄偶发滞后，临时文件留给系统清


def test_tutor_mentions_skip():
    print("\n[导师知道跳章]")
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("DELETE FROM profile_skills")
    conn.commit()
    conn.close()
    set_levels({"s1": 2})
    text = course.tutor_context(COURSE, "s2c1")
    check("提到了跳过", "跳过" in text, text[:200])
    check("说清了哪一段", "地基" in text)
    check("说了自己点进去仍算数", "仍然算数" in text)

    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("DELETE FROM profile_skills")
    conn.commit()
    conn.close()
    text2 = course.tutor_context(COURSE, "s1c1")
    check("没测过时不提跳过", "跳过" not in text2)


def main_():
    path, old = use_temp_db()
    try:
        test_no_profile()
        test_level_one_does_not_skip()
        test_skip_stage_one()
        test_skip_needs_prereq()
        test_chain_after_prereq_learned()
        test_quiz_overrides_diagnostic()
        test_learning_wins()
        test_blocked_by()
        test_all_satisfied()
        test_out_of_order()
        test_missing_table()
        test_tutor_mentions_skip()
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
