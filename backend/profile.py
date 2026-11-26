"""摸底诊断的落库与能力画像。

两件事分开：
- `quiz.score_diagnostic()` 只算分，纯函数，不碰库；
- 本模块负责把分数变成「档案」：存这次诊断、按阶段写画像。

刻意**不写 course_progress**。诊断判 level 2 的意思是「这一段可以跳过」，
一旦把它写成「这些章学完了」，进度条会瞬间跳到 60%，而用户一章正文都没读。
真正该做的是让排期器绕开这些章（第三优先），不是伪造学习记录。
什么时候写、怎么写，得单独决定一次。
"""

import json

import database

# level 的含义与 mastery 同一套：0 未测 / 1 听过 / 2 能写 / 3 能讲。
# 诊断卷封顶 2——选择和填空能证明「认得」，证明不了「能讲」。
LEVEL_LABELS = {0: "未测", 1: "听过", 2: "能写", 3: "能讲"}


def apply(diagnostic, scored, answers, course_id):
    """存一次诊断，并按阶段覆盖画像。返回给界面看的摘要。

    payload 存每题作答而不是只存分数：以后判分规则改了，要能用同一份作答
    重跑一遍，看结论差在哪。只存分数的话，历史数据永远没法复核。
    """
    payload = json.dumps(
        {"answers": answers, "stages": scored.get("stages") or {}},
        ensure_ascii=False,
    )
    diagnostic_id = database.save_diagnostic(
        course_id, scored.get("total") or 0, scored.get("correct") or 0, payload,
    )

    levels = {}
    for stage, row in (scored.get("stages") or {}).items():
        level = int(row.get("level") or 0)
        evidence = "诊断卷 %d/%d 题答对" % (row.get("correct") or 0, row.get("total") or 0)
        database.upsert_profile_skill(course_id, stage, level, evidence, "diagnostic")
        levels[stage] = {
            "level": level,
            "label": LEVEL_LABELS.get(level, ""),
            "correct": int(row.get("correct") or 0),
            "total": int(row.get("total") or 0),
            "evidence": evidence,
        }

    return {
        "diagnostic_id": diagnostic_id,
        "title": (diagnostic or {}).get("title") or "摸底诊断",
        "total": scored.get("total") or 0,
        "correct": scored.get("correct") or 0,
        "max_level": scored.get("max_level") or 0,
        "levels": levels,
    }


def summary(course_id):
    """最近一次诊断的概况 + 当前画像。没测过时 last 为 None，界面据此显示入口。

    阶段分数不重算，只回画像里的现值：存档里的每题作答是用来复核规则的，
    拿它重算会让「上次测出什么」跟着规则改动变，失去对照价值。
    """
    last = database.latest_diagnostic(course_id)
    skills = database.list_profile_skills(course_id)
    levels = {
        stage: {
            "level": int(row.get("level") or 0),
            "label": LEVEL_LABELS.get(int(row.get("level") or 0), ""),
            "evidence": row.get("evidence") or "",
            "updated_at": row.get("updated_at"),
        }
        for stage, row in skills.items()
    }
    return {
        "last": None if not last else {
            "id": last["id"], "at": last.get("finished_at") or last.get("created_at"),
            "total": last["total"], "correct": last["correct"],
        },
        "levels": levels,
    }
