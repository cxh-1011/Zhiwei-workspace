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
from datetime import datetime, timedelta

import database
import course as course_lib

# level 的含义与 mastery 同一套：0 未测 / 1 听过 / 2 能写 / 3 能讲。
# 诊断卷封顶 2——选择和填空能证明「认得」，证明不了「能讲」。
LEVEL_LABELS = {0: "未测", 1: "听过", 2: "能写", 3: "能讲"}
# 等级是谁给的。画像上要写清来源，否则「2 级」看起来像官方评定，
# 实际可能只是两张选择题。
SOURCE_LABELS = {"diagnostic": "摸底诊断", "quiz": "章节实测", "manual": "你自己评的"}
# 画像会过期：半年前的摸底结论再拿来跳章，是拿旧事实做新决定。
# 只做提示，不自动清空——清空等于把用户刚测出的结论偷偷作废。
STALE_DAYS = 30


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


# ---------- 画像页：10 个阶段各自什么水平 ----------

def _days_since(text):
    """SQLite 的 CURRENT_TIMESTAMP 是 UTC，这里也按 UTC 算，别混本地时间。"""
    if not text:
        return None
    try:
        stamp = datetime.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return max(0, (datetime.utcnow() - stamp).days)


def _last_diagnostic(course_id):
    try:
        last = database.latest_diagnostic(course_id)
    except Exception:
        return None  # 旧库没这张表：当作没测过，画像页照常渲染
    if not last:
        return None
    at = last.get("finished_at") or last.get("created_at")
    return {
        "id": last["id"], "at": at, "age_days": _days_since(at),
        "total": last.get("total") or 0, "correct": last.get("correct") or 0,
    }


def overview(course_id):
    """画像页要的全部事实：每个阶段判几级、凭什么、什么时候测的、实测推没推翻。

    只读不写。这里刻意**不重算等级**——等级是 `apply()` 当时写下的结论，
    重算会让「上次测出什么」随着规则调整而变，画像就没有对照价值了。
    实测的部分只作为证据并列出来（覆盖几章、过几章、哪章没过），
    要不要让它改判等级是另一件事，得单独定。
    """
    import quiz as quiz_lib  # 局部导入：quiz 顶部 import 了 course

    course = course_lib.load_course(course_id)
    chapters = course.get("chapters") or []

    try:
        skills = database.list_profile_skills(course_id)
    except Exception:
        skills = {}  # 旧库未迁移：当成全未测，页面不能因此打不开
    try:
        totals = quiz_lib.quiz_totals(course_id)
    except Exception:
        totals = {}
    try:
        answered_map = database.quiz_pass_summary(course_id)
    except Exception:
        answered_map = {}

    by_stage = {}
    for chapter in chapters:
        by_stage.setdefault(str(chapter.get("stage") or ""), []).append(chapter)

    rows = []
    for index, stage in enumerate(course.get("stages") or []):
        stage_id = str(stage.get("id") or "")
        own = by_stage.get(stage_id) or []
        with_quiz = tested = fully = failed = 0
        for chapter in own:
            total = int(totals.get(chapter["id"]) or 0)
            if total <= 0:
                continue  # 这章还没出题库，不算覆盖
            with_quiz += 1
            counts = answered_map.get(chapter["id"]) or {}
            answered = int(counts.get("answered") or 0)
            passed = int(counts.get("passed") or 0)
            if answered:
                tested += 1
            if answered >= total and passed >= total:
                fully += 1
            if answered > passed:
                failed += 1

        skill = skills.get(stage_id) or {}
        level = max(0, min(3, int(skill.get("level") or 0)))
        source = skill.get("source") or ""
        age = _days_since(skill.get("updated_at"))
        rows.append({
            "id": stage_id,
            "no": index + 1,
            "title": stage.get("title") or stage_id,
            "level": level,
            "label": LEVEL_LABELS.get(level, ""),
            "evidence": skill.get("evidence") or "",
            "source": source,
            "source_label": SOURCE_LABELS.get(source, ""),
            "updated_at": skill.get("updated_at"),
            "age_days": age,
            # 跳过线只看等级，和 course.skip_stages 同一个常量，不在别处再写一遍 2
            "skip": level >= course_lib.SKIP_LEVEL,
            "quiz": {
                "chapters": len(own),
                "with_quiz": with_quiz,
                "tested": tested,
                "fully_passed": fully,
                "failed": failed,
            },
            # 「诊断说会，实测没过」：这些章已经被 mark_skipped 排除在跳过之外，
            # 画像上必须显式标出来，否则用户看到 2 级却还被推回这一章会以为坏了
            "challenged": level >= course_lib.SKIP_LEVEL and failed > 0,
            "stale": bool(level > 0 and age is not None and age >= STALE_DAYS),
        })

    return {
        "course": {"id": course_id, "title": course.get("title") or ""},
        "stages": rows,
        "last": _last_diagnostic(course_id),
        # 画像的刻度是 0-3（能讲算满分），不是诊断卷的 0-2：诊断测不到 3 级，
        # 但章节开放题能。用 2 当满分会让「能讲」永远画成溢出。
        "max_level": 3,
        "skip_level": course_lib.SKIP_LEVEL,
        "stale_days": STALE_DAYS,
        "totals": {
            "measured": sum(1 for row in rows if row["level"] > 0),
            "skipping": sum(1 for row in rows if row["skip"]),
            "challenged": sum(1 for row in rows if row["challenged"]),
        },
    }
