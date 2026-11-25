"""题库内容与诊断卷的自检：文件本身对不对，不涉及接口和前端。

这组测试的价值在于「内容自己判自己」：每道题拿它自己配的答案去判，必须判 100。
答案写错、选项漏字母、rubric 的要点永远点不中，都会在这里当场暴露——
这些错在人工读一遍时极难发现，但会让用户答对却得 0 分。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import course
import quiz

PASS = 0
FAIL = 0

# 现在已写题库的章节。补题库时把这个列表加长，测试会自动覆盖新文件。
WRITTEN = ("s1c1", "s3c1", "s6c2")


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, detail))


def chapters():
    return {c["id"]: c for c in course.load_course()["chapters"]}


def own_answer(item):
    """这道题「正确的作答」长什么样。判分器拿它必须给 100。"""
    kind = item.get("kind")
    if kind == "choice":
        return item.get("answer")
    if kind == "blank":
        blanks = item.get("blanks") or []
        first = blanks[0] if blanks else ""
        return first[0] if isinstance(first, (list, tuple)) else first
    if kind == "rubric":
        # 每个要点取第一个可接受词，拼成一句话——这是「全都说到」的最小样本
        words = []
        for point in item.get("must") or []:
            if isinstance(point, str):
                words.append(point)
            elif isinstance(point, dict):
                pool = point.get("any") or point.get("all") or []
                if pool:
                    words.append(str(pool[0]))
        return "，".join(words)
    return ""


def test_chapter_quizzes():
    print("\n== 章节题库 ==")
    all_chapters = chapters()
    # 目录里有哪些 json，就该覆盖哪些章；漏一个说明题库文件没被写进去
    quiz_dir = quiz.CURRICULUM_ROOT / course.DEFAULT_COURSE / "quiz"
    files = sorted(p.stem for p in quiz_dir.glob("*.json") if not p.name.startswith("_"))
    check("题库目录只放了合法章 id", all(name in all_chapters for name in files), files)
    for chapter_id in WRITTEN:
        check("%s 有题库文件" % chapter_id, chapter_id in files, files)

    for chapter_id in files:
        chapter = all_chapters[chapter_id]
        loaded = quiz.load_quiz(chapter_id)
        items = loaded["items"]
        check("%s 载入无异常" % chapter_id, bool(items))

        kinds = [item["kind"] for item in items]
        check("%s 正好 3 题" % chapter_id, len(items) == 3, len(items))
        check("%s 三种题型各一道" % chapter_id,
              sorted(kinds) == ["blank", "choice", "rubric"], kinds)

        for item in items:
            answer = own_answer(item)
            result = quiz.grade(item, answer)
            check("%s 用自己的答案判满分" % item["id"], result["score"] == 100,
                  "%s -> %s（%s）" % (answer, result["score"], result["detail"]))
            # 空作答必须是 0：不然没答也能过线，判分就失去意义
            check("%s 空作答判 0" % item["id"], quiz.grade(item, "")["score"] == 0)
            # 每题的 explain 是给用户看的，缺了就等于「错了不知道为什么」
            check("%s 有解析" % item["id"], len(str(item.get("explain") or "")) >= 20,
                  item.get("explain"))

        ids = [item["id"] for item in items]
        check("%s 题号不重复" % chapter_id, len(set(ids)) == len(ids), ids)

        # 公开版本必须把答案类字段全部剥掉
        public = quiz.public_items(loaded)
        check("%s 公开题不含答案字段" % chapter_id,
              all(not ({"answer", "blanks", "must", "forbid", "explain"} & set(row))
                  for row in public))
        check("%s 公开题保留题干" % chapter_id,
              all(row.get("stem") for row in public))
        # 填空题要告诉前端画几个输入框
        for item, row in zip(items, public):
            if item["kind"] == "blank":
                check("%s 公开题给出空数" % item["id"],
                      row.get("blank_count") == len(item.get("blanks") or []))


def test_objective_links():
    print("\n== 题目与学习目标对齐 ==")
    all_chapters = chapters()
    quiz_dir = quiz.CURRICULUM_ROOT / course.DEFAULT_COURSE / "quiz"
    for path in sorted(quiz_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        chapter_id = path.stem
        items = json.loads(path.read_text(encoding="utf-8"))["items"]
        objectives = all_chapters[chapter_id]["objectives"]
        for item in items:
            objective = item.get("objective")
            check("%s 的 objective 合法" % item["id"],
                  isinstance(objective, int) and 0 <= objective < len(objectives),
                  "%s / %d 个目标" % (objective, len(objectives)))
        # 三道题不该全挤在同一个目标上，否则另外两个目标永远测不到
        used = {item.get("objective") for item in items}
        check("%s 三个目标都覆盖到" % chapter_id, len(used) == len(objectives),
              "%s / %d" % (used, len(objectives)))


def test_diagnostic_shape():
    print("\n== 诊断卷格式 ==")
    data = quiz.load_diagnostic()
    items = data["items"]
    check("正好 20 题", len(items) == 20, len(items))
    check("声明 12 分钟内答完", data.get("minutes", 99) <= 20, data.get("minutes"))

    stages = course.load_course()["chapters"]
    stage_ids = []
    for chapter in stages:
        if chapter["stage"] not in stage_ids:
            stage_ids.append(chapter["stage"])
    check("覆盖全部 10 个阶段", len(stage_ids) == 10, stage_ids)

    counted = {}
    for item in items:
        counted[item["stage"]] = counted.get(item["stage"], 0) + 1
        check("%s 是确定性题型" % item["id"], item["kind"] in ("choice", "blank"), item["kind"])
        check("%s 出题阶段合法" % item["id"], item["stage"] in stage_ids, item["stage"])
    check("每阶段正好 2 题", sorted(counted.values()) == [2] * 10, counted)

    ids = [item["id"] for item in items]
    check("题号不重复", len(set(ids)) == len(ids))

    # 诊断卷也走同一套公开剥离，防止前端顺手把答案发出去
    public = quiz.public_items(data)
    check("公开题不含答案字段",
          all(not ({"answer", "blanks", "must", "forbid", "explain"} & set(row)) for row in public))
    check("公开题不带阶段标签",
          all("stage" not in row for row in public),
          "带了 stage 等于提前告诉用户这题属于哪个阶段，会影响后面的自我判断")


def test_diagnostic_scoring():
    print("\n== 诊断分级 ==")
    data = quiz.load_diagnostic()
    items = data["items"]

    # 全对：每阶段 1 + 2 = 3 分，封顶 2 级
    all_right = {item["id"]: own_answer(item) for item in items}
    result = quiz.score_diagnostic(data, all_right)
    check("全对时 20 题全中", result["correct"] == 20, result["correct"])
    check("全对每阶段都是 2 级",
          all(row["level"] == 2 for row in result["stages"].values()),
          {k: v["level"] for k, v in result["stages"].items()})
    check("全对时不给 3 级", result["max_level"] == 2, result["max_level"])

    # 全空：一题不中，全 0 级
    empty = quiz.score_diagnostic(data, {})
    check("空卷全 0 级", all(row["level"] == 0 for row in empty["stages"].values()))
    check("空卷也数满题量", empty["total"] == 20, empty["total"])

    # 只答对一题 → 1 级；这是「听过但没把握」的那一档
    one_right = {}
    for item in items:
        if item["stage"] == "s1" and item["kind"] == "blank":
            one_right[item["id"]] = own_answer(item)
    partial = quiz.score_diagnostic(data, one_right)
    check("只答对一题是 1 级", partial["stages"]["s1"]["level"] == 1, partial["stages"]["s1"])
    check("没答的阶段是 0 级", partial["stages"]["s2"]["level"] == 0)

    # 基础题错、进阶题对，仍然只能 1 级——这是改掉权重方案的原因：
    # 按权重算这里会得 2 分判二级，整个阶段就被跳过了，而基础题是蒙的
    basic_wrong = {}
    for item in items:
        if item["stage"] == "s2" and item["kind"] == "blank":
            basic_wrong[item["id"]] = own_answer(item)
    check("漏了基础题不给 2 级",
          quiz.score_diagnostic(data, basic_wrong)["stages"]["s2"]["level"] == 1,
          quiz.score_diagnostic(data, basic_wrong)["stages"]["s2"])

    # 答了但答错，与没答同分——诊断不支持「跳过」
    wrong = {item["id"]: "zzz" for item in items}
    check("答错与没答同分",
          quiz.score_diagnostic(data, wrong)["correct"] == quiz.score_diagnostic(data, {})["correct"])

    # 一道题错了不影响别的阶段
    one_bad = dict(all_right)
    one_bad[items[0]["id"]] = "zzz"
    partial = quiz.score_diagnostic(data, one_bad)
    check("错一道只掉本阶段",
          partial["stages"][items[0]["stage"]]["level"] == 1
          and partial["stages"][items[2]["stage"]]["level"] == 2,
          {k: v["level"] for k, v in partial["stages"].items()})


def test_diagnostic_validation_catches_breakage():
    print("\n== 诊断卷校验能挡住坏数据 ==")
    data = quiz.load_diagnostic()
    copy = json.loads(json.dumps(data))

    copy["items"][0]["kind"] = "rubric"
    check("拦下 rubric 题", any("只能是 choice/blank" in p for p in quiz.validate_diagnostic(copy)))

    copy = json.loads(json.dumps(data))
    copy["items"][0]["stage"] = "s99"
    check("拦下不存在的阶段",
          any("不在课程里" in p for p in quiz.validate_diagnostic(copy, ["s1", "s2"])))

    copy = json.loads(json.dumps(data))
    copy["items"] = copy["items"][:-1]
    check("拦下缺题", any("应该正好 2 题" in p or "一道题都没有" in p
                          for p in quiz.validate_diagnostic(copy)))

    copy = json.loads(json.dumps(data))
    copy["items"][1]["id"] = "diag-s1-q1"
    check("拦下题号重复", any("重复" in p for p in quiz.validate_diagnostic(copy)))

    copy = json.loads(json.dumps(data))
    copy["items"][0]["options"] = ["A. 只有一个选项"]
    check("拦下选项不足", any("至少要有两个选项" in p for p in quiz.validate_diagnostic(copy)))

    check("原卷本身没问题", quiz.validate_diagnostic(data, None) == [],
          quiz.validate_diagnostic(data, None))


def test_loader_guards():
    print("\n== 读取守卫 ==")
    for bad in ("../etc", "S1C1", "", "a" * 40):
        try:
            quiz.load_quiz(bad)
            check("非法章 id 被拒：%r" % bad, False, "居然没抛错")
        except quiz.QuizError:
            check("非法章 id 被拒：%r" % bad, True)
    check("题库缺失时返回空题而不是抛错",
          quiz.load_quiz("s10c2")["items"] == [])


def main_():
    test_chapter_quizzes()
    test_objective_links()
    test_diagnostic_shape()
    test_diagnostic_scoring()
    test_diagnostic_validation_catches_breakage()
    test_loader_guards()
    print("\n%s 通过 %d，失败 %d" % ("FAIL" if FAIL else "ALL PASS", PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
