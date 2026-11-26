"""排期器测试：纯算术一层，接口一层。

纯函数这层最值钱：日期、分钟、掉队策略都是「看起来对但差一天」的东西，
浏览器里点十次也未必撞得到边界。接口层只验鉴权、字段和「画像真的接上了」。

和别的课程测试一样，import main 之前必须把 DB_PATH 指到临时库。
"""

import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
database.DB_PATH = _TMP.name
database.init_db()

import course  # noqa: E402
import schedule  # noqa: E402

COURSE = "agent-dev"
TODAY = date(2026, 11, 26)  # 周四

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  ->  %r" % (name, detail))


def reset():
    conn = sqlite3.connect(database.DB_PATH)
    for table in ("course_progress", "quiz_attempts", "profile_skills",
                  "diagnostics", "plan_slots", "plan_settings"):
        conn.execute("DELETE FROM %s" % table)
    conn.commit()
    conn.close()


def decorated():
    return course.decorate(course.load_course(COURSE), COURSE)


# ---------- 设置 ----------

def test_settings():
    print("\n[设置：开始日、周几、分钟]")
    s = schedule.normalize_settings({"start_date": "2020-01-01"}, today=TODAY)
    check("过去的开始日被推到今天", s["start_date"] == "2026-11-26", s["start_date"])
    s = schedule.normalize_settings({"start_date": "2026-12-01", "weekdays": "1,3,5"},
                                    today=TODAY)
    check("开始日按原样保留", s["start_date"] == "2026-12-01", s["start_date"])
    check("周几解析成列表", s["weekdays"] == [1, 3, 5], s["weekdays"])
    s = schedule.normalize_settings({"weekdays": "六 日 3"}, today=TODAY)
    check("中文空格也能拆", s["weekdays"] == [3, 6, 7], s["weekdays"])
    s = schedule.normalize_settings({"weekdays": "9,0,abc"}, today=TODAY)
    check("全非法时回退到每天", s["weekdays"] == [1, 2, 3, 4, 5, 6, 7], s["weekdays"])
    check("分钟低于下限被夹住", schedule.normalize_settings({"minutes": 1})["minutes"] == 10)
    check("分钟高于上限被夹住", schedule.normalize_settings({"minutes": 9999})["minutes"] == 480)
    check("默认每天 90 分钟", schedule.normalize_settings({})["minutes"] == 90)
    check("不认识的策略回退 shift",
          schedule.normalize_settings({"catch_up": "magic"})["catch_up"] == "shift")

    days = list(schedule.iter_days(TODAY, [1, 2, 3, 4, 5], limit=5))
    check("只给工作日", [d.isoweekday() for d in days] == [4, 5, 1, 2, 3],
          [d.isoformat() for d in days])
    check("11/28 是周六被跳过", days[1] == date(2026, 11, 27) and days[2] == date(2026, 11, 30),
          [d.isoformat() for d in days])


# ---------- 分类 ----------

def test_classify():
    print("\n[哪些章要排：画像与进度]")
    reset()
    info = schedule.classify(decorated())
    check("没进度没画像时 33 章全要学", len(info["learnable"]) == 33, len(info["learnable"]))
    check("学完 0 章", len(info["done"]) == 0)
    check("跳过 0 章", len(info["skipped"]) == 0)

    database.set_course_progress(COURSE, "s1c1", status="done", mastery=1)
    info = schedule.classify(decorated())
    check("学完的章不再排", len(info["learnable"]) == 32, len(info["learnable"]))
    check("学完单独一堆", [c["id"] for c in info["done"]] == ["s1c1"],
          [c["id"] for c in info["done"]])

    reset()
    for stage in ("s1", "s2"):
        database.upsert_profile_skill(COURSE, stage, 2, "诊断卷 2/2 题答对", "diagnostic")
    info = schedule.classify(decorated())
    check("诊断判过的两段不进排期", len(info["learnable"]) == 27, len(info["learnable"]))
    check("跳过的共 6 章", len(info["skipped"]) == 6, len(info["skipped"]))
    check("跳过章的前置也传导到了", "s3c1" in [c["id"] for c in info["learnable"]],
          [c["id"] for c in info["learnable"]][:2])

    info2 = schedule.classify(decorated(), include_skipped=True)
    check("勾了复习就把跳过的章排回来", len(info2["learnable"]) == 33,
          len(info2["learnable"]))
    check("跳过的章仍单独记着", len(info2["skipped"]) == 6, len(info2["skipped"]))


def test_basis():
    print("\n[依据：画像省了多少分钟]")
    reset()
    basis = schedule.basis(decorated())
    check("全学要 1320 分钟", basis["all_minutes"] == 1320, basis["all_minutes"])
    check("没画像时一分钟没省", basis["saved_minutes"] == 0, basis["saved_minutes"])

    for stage in ("s1", "s2", "s3"):
        database.upsert_profile_skill(COURSE, stage, 2, "诊断卷 2/2 题答对", "diagnostic")
    basis = schedule.basis(decorated())
    check("跳过 10 章", basis["skipped"] == 10, basis["skipped"])
    check("要学 23 章", basis["learnable"] == 23, basis["learnable"])
    check("省下 400 分钟", basis["saved_minutes"] == 400, basis["saved_minutes"])
    check("按阶段报出省了多少",
          {row["id"]: row["minutes"] for row in basis["skipped_stages"]}
          == {"s1": 120, "s2": 120, "s3": 160}, basis["skipped_stages"])
    check("复习模式下不算省", schedule.basis(decorated(), True)["saved_minutes"] == 0,
          schedule.basis(decorated(), True)["saved_minutes"])


# ---------- 铺日期 ----------

def test_assign():
    print("\n[把章塞进日子]")
    items = [{"chapter_id": "a", "minutes": 40}, {"chapter_id": "b", "minutes": 40},
             {"chapter_id": "c", "minutes": 40}]
    slots, days_used = schedule.assign(items, schedule.iter_days(TODAY, [1, 2, 3, 4, 5, 6, 7]), 90)
    check("90 分钟放两章", slots[0]["day"] == "2026-11-26" and slots[1]["day"] == "2026-11-26",
          slots)
    check("第三章落到第二天", slots[2]["day"] == "2026-11-27", slots[2])
    check("用了 2 天", days_used == 2, days_used)
    check("同一天序号递增", [s["seq"] for s in slots[:2]] == [0, 1], slots[:2])

    big = [{"chapter_id": "x", "minutes": 100}]
    slots, _ = schedule.assign(big, schedule.iter_days(TODAY, [1], ), 60)
    check("单章比一天还长就独占一天", len(slots) == 1 and slots[0]["minutes"] == 100, slots)

    slots, days_used = schedule.assign([], schedule.iter_days(TODAY, [1]), 90)
    check("空列表排 0 天", slots == [] and days_used == 0, (slots, days_used))


def test_safe_order():
    print("\n[模型给的顺序不能违反依赖]")
    chapters = decorated()["chapters"]
    learnable = [c for c in chapters if c["id"] in {"s1c1", "s1c2", "s1c3", "s10c2"}]
    satisfied = set()
    ordered = schedule.safe_order(learnable, ["s10c2", "s1c3", "s1c1", "s1c2"], satisfied)
    check("最后一章不会因为模型偏爱就排第一", ordered[0]["id"] == "s1c1",
          [c["id"] for c in ordered])
    check("依赖链顺序 s1c1→s1c2→s1c3 保住",
          [c["id"] for c in ordered] == ["s1c1", "s1c2", "s1c3", "s10c2"],
          [c["id"] for c in ordered])
    ordered = schedule.safe_order(learnable, ["s1c2"], satisfied)
    check("模型漏掉的章按原顺序补在后面",
          [c["id"] for c in ordered][-1] == "s10c2", [c["id"] for c in ordered])
    check("顺序里没有重复", len({c["id"] for c in ordered}) == len(ordered))


def test_model_parse():
    print("\n[模型返回的 JSON 解析]")
    check("纯 JSON", schedule._first_json('{"order":["s1c1"]}')["order"] == ["s1c1"])
    check("代码块包着",
          schedule._first_json('```json\n{"order":["s1c2"]}\n```')["order"] == ["s1c2"])
    check("前后带解释",
          schedule._first_json('好的：{"order":["s1c3"],"reason":"x"} 以上')["order"] == ["s1c3"])
    check("裸数组也认", schedule._first_json('["s1c1","s1c2"]')["order"] == ["s1c1", "s1c2"])
    check("垃圾输入返回空", schedule._first_json("我不确定") == {}, schedule._first_json("我不确定"))


def test_build():
    print("\n[整门课排期]")
    reset()
    result = schedule.build(decorated(), {}, today=TODAY)
    check("33 章一个不落", len(result["slots"]) == 33, len(result["slots"]))
    check("默认 90 分钟排 17 天", result["days"] == 17, result["days"])
    check("第一天两章", result["slots"][0]["day"] == "2026-11-26"
          and result["slots"][1]["day"] == "2026-11-26", result["slots"][:2])
    check("第一天是第 1 章", result["slots"][0]["chapter_id"] == "s1c1", result["slots"][0])
    check("每章只排一次", len({s["chapter_id"] for s in result["slots"]}) == 33)

    reset()
    for stage in ("s1", "s2", "s3"):
        database.upsert_profile_skill(COURSE, stage, 2, "诊断卷 2/2 题答对", "diagnostic")
    result = schedule.build(decorated(), {}, today=TODAY)
    check("跳过的段不占日子", len(result["slots"]) == 23, len(result["slots"]))
    check("第一课从第 11 章开始", result["slots"][0]["chapter_id"] == "s4c1",
          result["slots"][0])
    check("天数也少了", result["days"] == 12, result["days"])
    check("依据里写了省 400 分钟", result["basis"]["saved_minutes"] == 400,
          result["basis"]["saved_minutes"])

    result = schedule.build(decorated(), {"minutes": 60}, today=TODAY)
    check("每天 60 分钟一天只能一章", result["days"] == 23, result["days"])

    result = schedule.build(decorated(), {"weekdays": "1,3,5"}, today=TODAY)
    weekdays = {schedule.parse_day(s["day"]).isoweekday() for s in result["slots"]}
    check("只在选中的周几排", weekdays <= {1, 3, 5}, weekdays)


def test_reschedule():
    print("\n[掉队：三种策略]")
    reset()
    # 造一份「按周一开始、全工作日」的旧计划，然后把今天推到三天后
    old = schedule.build(decorated(), {"start_date": "2026-11-23"}, today=date(2026, 11, 23))
    slots = [dict(s, id=index) for index, s in enumerate(old["slots"])]
    later = date(2026, 11, 26)

    result = schedule.reschedule(decorated(), slots, {"start_date": "2026-11-23"},
                                 "shift", today=later)
    check("顺延不丢章", len(result["slots"]) == 33, len(result["slots"]))
    check("顺延从今天开始", result["slots"][0]["day"] == "2026-11-26", result["slots"][0])
    check("顺延没丢任何章", result["dropped"] == [], result["dropped"])

    result = schedule.reschedule(decorated(), slots, {"start_date": "2026-11-23"},
                                 "compress", today=later)
    check("压缩也要学完 33 章", len(result["slots"]) == 33, len(result["slots"]))
    check("压缩把每天顶高", result["capacity"] > 90, result["capacity"])
    check("压缩不丢章", result["dropped"] == [])

    result = schedule.reschedule(decorated(), slots, {"start_date": "2026-11-23"},
                                 "drop", today=later)
    check("放弃后章变少", len(result["slots"]) < 33, len(result["slots"]))
    check("放弃的是过期没学的章", len(result["dropped"]) == 6, result["dropped"])
    dropped = set(result["dropped"])
    check("放弃的章不在新排期里",
          not dropped & {s["chapter_id"] for s in result["slots"]}, dropped)

    try:
        schedule.reschedule(decorated(), slots, {}, "magic", today=later)
        check("非法策略要报错", False)
    except schedule.PlanError:
        check("非法策略要报错", True)


def test_summarize():
    print("\n[统计：完成、过期、收尾日]")
    slots = [
        {"chapter_id": "s1c1", "day": "2026-11-24", "minutes": 40},
        {"chapter_id": "s1c2", "day": "2026-11-25", "minutes": 40},
        {"chapter_id": "s1c3", "day": "2026-11-26", "minutes": 40},
    ]
    progress = {"s1c1": {"status": "done"}}
    stats = schedule.summarize(slots, progress, today=TODAY)
    check("完成 1 章", stats["done"] == 1, stats)
    check("过期的是没学完的那章", stats["overdue"] == ["s1c2"], stats["overdue"])
    check("过期分钟数对", stats["overdue_minutes"] == 40, stats["overdue_minutes"])
    check("今天有 1 章", stats["today"] == ["s1c3"], stats["today"])
    check("收尾日取最后一天", stats["end"] == "2026-11-26", stats["end"])
    check("剩余分钟数对", stats["minutes_left"] == 80, stats["minutes_left"])


# ---------- 接口 ----------

def test_api():
    print("\n[接口：鉴权、重建、画像联动、掉队]")
    from fastapi.testclient import TestClient
    import main as app_main

    api = TestClient(app_main.app)
    check("没登录看不了排期", api.get("/plan").status_code == 401)
    api.cookies.set(app_main.SESSION_COOKIE, app_main.create_session_token())

    reset()
    body = api.get("/plan").json()
    check("没排过时 planned 为假", body["planned"] is False, body["planned"])
    check("没排过也报待学章数", body["basis"]["learnable"] == 33, body["basis"])
    check("没排过时默认每天 90 分钟", body["settings"]["minutes"] == 90, body["settings"])

    built = api.post("/plan/build", json={"weekdays": "1,2,3,4,5,6,7", "minutes": 90})
    check("重建成功", built.status_code == 200, built.text[:200])
    data = built.json()
    check("排期有 33 章", len(data["slots"]) == 33, len(data["slots"]))
    check("planned 变真", data["planned"] is True)
    check("第一组是今天", data["days"][0]["is_today"] is True, data["days"][0])
    check("设置了 7 天", data["settings"]["weekdays"] == [1, 2, 3, 4, 5, 6, 7],
          data["settings"])
    check("槽里带章节标题", data["slots"][0]["title"] and data["slots"][0]["no"] == 1,
          data["slots"][0])

    again = api.get("/plan").json()
    check("重新拉到的和刚建的一致", len(again["slots"]) == 33, len(again["slots"]))

    api.patch("/course/progress", json={"chapter_id": "s1c1", "status": "done"})
    after = api.get("/plan").json()
    check("学完一章后统计变了", after["stats"]["done"] == 1, after["stats"])
    check("那章在槽里标已学完",
          [s["done"] for s in after["slots"] if s["chapter_id"] == "s1c1"] == [True],
          after["slots"][0])

    rebuilt = api.post("/plan/build", json={"weekdays": "1,2,3,4,5,6,7", "minutes": 90})
    check("重建后学完的章不再排", len(rebuilt.json()["slots"]) == 32,
          len(rebuilt.json()["slots"]))

    # 接上画像：判 s1-s3 会了，重排应该直接跳到第 11 章
    reset()
    api.post("/plan/build", json={"weekdays": "1,2,3,4,5,6,7", "minutes": 90})
    for stage in ("s1", "s2", "s3"):
        database.upsert_profile_skill(COURSE, stage, 2, "诊断卷 2/2 题答对", "diagnostic")
    check("画像变了但没重排时提示 stale",
          api.get("/plan").json()["stale"] is True, api.get("/plan").json()["stale"])
    api.post("/plan/build", json={"weekdays": "1,2,3,4,5,6,7", "minutes": 90})
    plan = api.get("/plan").json()
    check("重排后不再提示 stale", plan["stale"] is False, plan["stale"])
    check("画像跳过 10 章", len(plan["slots"]) == 23, len(plan["slots"]))
    check("排期从 s4c1 开始", plan["slots"][0]["chapter_id"] == "s4c1", plan["slots"][0])
    check("依据里报了省下 400 分钟", plan["basis"]["saved_minutes"] == 400,
          plan["basis"]["saved_minutes"])
    check("依据里列出跳过哪几段",
          [x["id"] for x in plan["basis"]["skipped_stages"]] == ["s1", "s2", "s3"],
          plan["basis"]["skipped_stages"])

    # 勾上复习：跳过章回来，但依据里不再算「省下」
    review = api.post("/plan/build", json={"weekdays": "1,2,3,4,5,6,7",
                                           "minutes": 90, "include_skipped": True}).json()
    check("复习模式排满 33 章", len(review["slots"]) == 33, len(review["slots"]))
    check("复习模式不算省时间", review["basis"]["saved_minutes"] == 0,
          review["basis"]["saved_minutes"])

    bad = api.post("/plan/reschedule", json={"strategy": "magic"})
    check("非法策略 400", bad.status_code == 400, bad.status_code)
    good = api.post("/plan/reschedule", json={"strategy": "shift"})
    check("顺延返回 200", good.status_code == 200, good.text[:200])
    check("顺延返回每天容量", good.json()["capacity"] == 90, good.json()["capacity"])

    database.set_course_progress(COURSE, "s1c1", status="done", mastery=1)
    cleared = api.delete("/plan")
    check("清空排期 200", cleared.status_code == 200)
    check("清空后 planned 为假", api.get("/plan").json()["planned"] is False)
    check("清空排期不动学习记录",
          (database.list_course_progress(COURSE).get("s1c1") or {}).get("status") == "done",
          database.list_course_progress(COURSE).get("s1c1"))

    check("没排过时掉队接口 404",
          api.post("/plan/reschedule", json={"strategy": "shift"}).status_code == 404)


def main():
    test_settings()
    test_classify()
    test_basis()
    test_assign()
    test_safe_order()
    test_model_parse()
    test_build()
    test_reschedule()
    test_summarize()
    test_api()
    print("\n%d 通过，%d 失败" % (PASS, FAIL))
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
