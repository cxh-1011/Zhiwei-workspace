"""摸底诊断的落库与能力画像。

两件事分开：
- `quiz.score_diagnostic()` 只算分，纯函数，不碰库；
- 本模块负责把分数变成「档案」：存这次诊断、按阶段写画像。

画像有两个去处：排期绕开已会的阶段，对话按他的基础决定讲多深（`style_hint`）。
后者只影响措辞，不改任何学习记录。

刻意**不写 course_progress**。诊断判 level 2 的意思是「这一段可以跳过」，
一旦把它写成「这些章学完了」，进度条会瞬间跳到 60%，而用户一章正文都没读。
真正该做的是让排期器绕开这些章（第三优先），不是伪造学习记录。
什么时候写、怎么写，得单独决定一次。
"""

import json
import re
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


# ---------- 对话里的讲解口径 ----------

# 画像的用处不止「跳过哪几段」：同一句「token 是什么」，对 1 级的人要从
# 「你每天打字发微信」说起，对 3 级的人一句「切词后的最小单位，决定上下文长度」
# 就够了。
#
# 这段是被真机反复打回来才定成这样的，结论有两条：
#
# ① 档位必须由程序定。把三档并列交给模型，它不会按「这个人会不会」选，而是按
#    「这题看起来基础不基础」选——问 s1 的 token 时，明明 s1 没测过，它照样用
#    术语讲，还顺手把「你是 2 级」写进回答。
# ② 指令必须短。同一段 600 字的讲解要求，接在 SYSTEM_PROMPT 后面，模型照样
#    输出 1300 字、带 emoji 和表格；压成「不要 emoji、表格、小标题，200 字内」
#    这一句，立刻变成 185 字的白话。长指令会被长提示淹没，短指令才落地。
#
# 所以这里只留一句格式要求，不列等级表、不写理由、不给示例。等级只在句首出现
# 一次，并且明令不许念给用户。
LEVEL_RULES = {
    0: "一段话，150 字内：先说它解决什么问题，配一个他日常见过的场景，术语第一次出现就解释。",
    2: "先一句定义和适用边界，再给机制或最短可跑代码，500 字内；不打比方、不复述入门概念。",
    3: "一句话结论，再最多三条取舍或坑，200 字内；不解释定义、不铺垫。",
}
# 1 级按 0 级写：听过和没听过，在「要不要解释术语」上没有区别。
LEVEL_RULES[1] = LEVEL_RULES[0]

# 问题定位不用章节文本打分：试过 recall 的二元组匹配，短问句命中率太低——
# 「怎么让模型调用工具」一章都匹配不到，「ReAct 是什么」还会被《多智能体真有用吗》
# 抢走。关键词表是手写的，但可测、可解释、零延迟；换课程时要跟着换。
STAGE_KEYWORDS = {
    "s1": ("token", "上下文窗口", "上下文", "embedding", "向量", "采样", "temperature",
           "注意力", "幻觉", "词表"),
    "s2": ("提示词", "prompt", "system prompt", "few-shot", "少样本", "结构化输出", "json"),
    "s3": ("工具调用", "调用工具", "function calling", "tool call", "工具", "参数 schema"),
    "s4": ("react", "agent 循环", "循环", "规划", "plan", "反思", "观察", "推理链"),
    "s5": ("记忆", "memory", "长期记忆", "召回", "遗忘"),
    "s6": ("rag", "检索", "知识库", "切块", "chunk", "重排", "rerank", "向量库"),
    "s7": ("工程化", "部署", "重试", "超时", "并发", "成本", "日志", "可观测", "缓存"),
    "s8": ("评测", "评估", "eval", "benchmark", "准确率", "回归", "打分"),
    "s9": ("多智能体", "multi-agent", "编排", "workflow", "langgraph"),
    "s10": ("收尾", "作品", "上线", "项目演示"),
}


def _hit(word, text):
    """英文词按词边界，中文按子串：否则 react 会在 reaction、plan 会在 planet 里命中。"""
    word = word.lower()
    if word.isascii():
        return re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(word), text) is not None
    return word in text


def _stage_by_keywords(question):
    """命中词最多的那一段；平票取更靠前的（更基础，宁可讲细一点）。"""
    text = str(question or "").lower()
    if not text.strip():
        return ""
    best, best_hits = "", 0
    for stage, words in STAGE_KEYWORDS.items():
        hits = sum(1 for word in words if _hit(word, text))
        if hits > best_hits:
            best, best_hits = stage, hits
    return best


def _stage_for_question(question="", chapter_id="", course_id=None):
    """问题属于哪一段：先看关键词，问不出来再用他打开的那一章。"""
    stage = _stage_by_keywords(question)
    if stage:
        return stage
    if chapter_id:
        try:
            chapters = course_lib.decorate(course_lib.load_course(course_id))["chapters"]
        except Exception:
            return ""
        chapter = next((c for c in chapters if c.get("id") == chapter_id), None)
        if chapter:
            return str(chapter.get("stage") or "")
    return ""


def style_hint(course_id, with_levels=True, question="", chapter_id=""):
    """一句话说明这次该怎么讲。没测过（全 0）返回空串，不做任何猜测。

    没测过就不注入，是这条功能的边界：模型宁愿中性作答，也不能因为「看不出来」
    就自己给用户安一个水平——那等于凭语气猜学历。

    with_levels 只为兼容调用方保留：等级表已经整段删掉，它既稀释那句关键的
    格式要求，也没有实际作用（模型只需要本次这一档）。导师模式的等级事实由
    course.tutor_context 提供。
    """
    try:
        data = overview(course_id)
    except Exception:
        return ""  # 旧库没画像表：当作没测过，对话照常
    if not any(int(row.get("level") or 0) > 0 for row in data["stages"]):
        return ""

    target = _stage_for_question(question, chapter_id, course_id)
    row = next((item for item in data["stages"] if item["id"] == target), None) if target else None
    if row is None:
        # 闲聊、写作、改代码不该被套上教学口吻；只有明显在问课程概念时才退回入门档。
        return ("【讲解格式 · 内部，别念出来】没定位到题目属于哪一段：如果是课程概念，"
                "按入门讲——" + LEVEL_RULES[0] + "不是课程内容就正常回答。"
                "不要 emoji、表格、小标题。")

    level = int(row.get("level") or 0)
    # 诊断说会、实测没过的那几段按 1 级讲：他听过，但没写出来过。
    if row.get("challenged"):
        level = min(level, 1)
    if level <= 0:
        head = "本题属第 %s 段 %s，这一段还没测过。" % (row["no"], row["title"])
    else:
        head = "本题属第 %s 段 %s，他 %d 级。" % (row["no"], row["title"], level)
    if row.get("stale"):
        # 只加四个字：过期结论不该当成刚测的，但也不值得为此多写一句。
        head += "（这条测得较早）"
    return ("【讲解格式 · 内部，别念出来】" + head + "按这个写："
            + LEVEL_RULES.get(level, LEVEL_RULES[0])
            + "不要 emoji、表格、小标题，也不要提等级。")
