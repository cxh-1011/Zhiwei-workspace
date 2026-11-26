"""课程内容读写与「下一步学什么」。

分层原则：
- 课程正文放文件（curriculum/<course>/chapters/*.md），能进 git、能手改、能 diff；
- 学到哪了放数据库（course_progress），因为那是随时变的运行时状态；
- 「下一步学哪章」是**确定性算法**，不交给模型决定。模型做推荐时会有两个毛病：
  爱顺着用户最近的话头走、爱跳前置。菜鸟教程的价值就在「下一步」是确定的。
"""

import json
import re
from pathlib import Path

from database import list_course_progress, list_profile_skills, quiz_pass_summary

CURRICULUM_ROOT = Path(__file__).resolve().parent.parent / "curriculum"
DEFAULT_COURSE = "agent-dev"
# 课程 id 与章节 id 都会拼进文件路径，只允许字母数字和连字符，防止 ../ 之类的穿越。
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


class CourseError(RuntimeError):
    pass


def _course_dir(course_id: str) -> Path:
    if not _ID_RE.match(course_id or ""):
        raise CourseError("课程 id 不合法：%s" % course_id)
    path = CURRICULUM_ROOT / course_id
    if not path.is_dir():
        raise CourseError("没有这门课：%s" % course_id)
    return path


def load_course(course_id: str = DEFAULT_COURSE) -> dict:
    """读课程树。返回的 chapters 已按 no 排序，并带上文件是否存在。"""
    root = _course_dir(course_id)
    course_file = root / "course.json"
    if not course_file.exists():
        raise CourseError("课程缺少 course.json：%s" % course_id)
    data = json.loads(course_file.read_text(encoding="utf-8"))
    chapters = sorted(data.get("chapters") or [], key=lambda c: c.get("no") or 0)
    for chapter in chapters:
        chapter["written"] = (root / chapter.get("path", "")).is_file()
    data["chapters"] = chapters
    return data


# 章节里的自测段。只有「这一章已经有判分题库」时才切掉它：
# 题库的题就是从这段改写的，留着等于把答案摊在页面上，用户先看见答案再答题，
# 判分就白做了。没出题库的章照旧显示——那是他唯一的自测。
_SELFTEST_RE = re.compile(r"(?ms)^##(?!#)[^\n]*自测[^\n]*\n.*?(?=^##(?!#)\s|\Z)")


def strip_self_test(markdown: str) -> str:
    """去掉「## 自测」这一整段。切在服务端做，不让前端拿正则切 md——
    前端切坏了会连后面的小节一起吞掉，而且没人看得见。"""
    return _SELFTEST_RE.sub("", markdown or "").rstrip() + "\n"


def load_chapter(course_id: str, chapter_id: str) -> dict:
    if not _ID_RE.match(chapter_id or ""):
        raise CourseError("章节 id 不合法：%s" % chapter_id)
    course = load_course(course_id)
    by_id = {c["id"]: c for c in course["chapters"]}
    chapter = by_id.get(chapter_id)
    if not chapter:
        raise CourseError("没有这一章：%s" % chapter_id)
    path = _course_dir(course_id) / chapter["path"]
    if not path.is_file():
        raise CourseError("这一章还没写出来：%s" % chapter_id)
    markdown = path.read_text(encoding="utf-8")
    # front-matter 里的标题程序已经知道，正文从一级标题开始给前端渲染
    body = re.sub(r"^---\n.*?\n---\n+", "", markdown, count=1, flags=re.S)

    order = [c["id"] for c in course["chapters"]]
    index = order.index(chapter_id)
    quiz = chapter_quiz(course_id, chapter_id)
    if quiz.get("items"):
        body = strip_self_test(body)
    # 目录里显示成跳过，点进来就必须也说跳过。mark_skipped 逐章独立，
    # 不依赖前置传导，所以单章调用和整份调用给的是同一个结论。
    mark_skipped([chapter], skip_stages(course_id), quiz_pass_summary(course_id))
    return {
        "chapter": chapter,
        "markdown": body.strip(),
        "prev": order[index - 1] if index > 0 else None,
        "next": order[index + 1] if index + 1 < len(order) else None,
        "total": len(order),
        "index": index + 1,
        "quiz": quiz,
    }


def chapter_quiz(course_id: str, chapter_id: str) -> dict:
    """这一章的判分题（已剥掉答案）与作答状态。

    题库还没写的章返回空列表——那是常态，不是错误。题库写坏了则把错误原样带出去：
    静默当成「这章没题」，写题的人永远发现不了，而用户会以为自己答对了没记录。
    """
    import quiz as quiz_lib  # 局部导入：quiz 依赖本模块，写在顶部会循环

    try:
        data = quiz_lib.load_quiz(chapter_id, course_id)
    except quiz_lib.QuizError as exc:
        return {"items": [], "state": None, "error": str(exc)}
    return {
        "items": quiz_lib.public_items(data),
        "state": quiz_lib.chapter_state(chapter_id, course_id, data),
    }


# ---------- 进度 ----------

DONE_STATUSES = {"done"}
# mastery: 0 没评过 / 1 看懂了 / 2 自己写出来了 / 3 能讲给别人听
# 过关只看 status：用户点了「学完」却没评掌握度是常态，若这里要求 mastery>=2，
# 推荐会一直卡在同一章不动，用户会以为坏了。mastery 只用来给复习建议加权。
PASS_MASTERY = 0


def _is_passed(entry: dict) -> bool:
    if entry.get("status") not in DONE_STATUSES:
        return False
    return int(entry.get("mastery") or 0) >= PASS_MASTERY


# ---------- 诊断跳章 ----------

# 诊断判到这个等级才允许跳过。1 级「听过」跳过去肯定学不动；
# 2 级「能写」是诊断卷能给出的最高级（选择和填空证明不了「能讲」），
# 所以跳过线只能画在这里——按 1 级跳会把整个地基段抹掉。
SKIP_LEVEL = 2


def skip_stages(course_id: str = DEFAULT_COURSE) -> dict:
    """诊断判定可以跳过的阶段：{stage: {"level", "evidence"}}。

    只读画像表，不读 course_progress——诊断刻意不写进度（理由见 profile.py），
    所以「跳过」只能是推荐层的一个过滤条件，绝不能表现成「这些章学完了」。
    没测过时返回空字典，推荐行为与加这个功能之前完全一致。
    """
    try:
        rows = list_profile_skills(course_id)
    except Exception:
        # 画像表还没建好（旧库未迁移）：当作没测过，推荐照旧。
        # 这里必须兜住——decorate 是刷目录的必经路径，抛一次整页就打不开。
        return {}
    return {
        stage: {"level": int(row.get("level") or 0), "evidence": row.get("evidence") or ""}
        for stage, row in rows.items()
        if int(row.get("level") or 0) >= SKIP_LEVEL
    }


def mark_skipped(chapters: list, skipped_stages: dict, answered: dict | None = None) -> list:
    """给每章盖一个 skipped 标记，返回真正被判跳过的章 id。

    三条限制：
    - 只跳诊断判到 SKIP_LEVEL 的阶段；
    - 该章自己答过题但有过不去的 → 不跳。诊断是 20 道题的粗测，章节自测是
      逐章实测，实测必须能推翻诊断；否则用户答错三题，推荐还绕开它，
      等于系统不听人话；
    - 每章都写 True/False，不删键。缺键和 False 行为一样，但写全了，
      调试时能一眼看出「这章算过了，判定是不跳」。
    """
    answered = answered or {}
    stages = skipped_stages or {}
    ids = []
    for chapter in chapters:
        stage = str(chapter.get("stage") or "")
        info = stages.get(stage) or {}
        decided = int(info.get("level") or 0) >= SKIP_LEVEL
        if decided:
            counts = answered.get(chapter["id"]) or {}
            if int(counts.get("answered") or 0) > int(counts.get("passed") or 0):
                decided = False
        chapter["skipped"] = bool(decided)
        # 连依据一起贴上：界面要能说清「为什么不推荐这一章」，
        # 只说「跳过」而不说「诊断判了 2 级」，用户会以为是系统漏了。
        chapter["skip_level"] = int(info.get("level") or 0) if decided else None
        chapter["skip_evidence"] = (info.get("evidence") or "") if decided else ""
        if decided:
            ids.append(chapter["id"])
    return ids


def satisfied_ids(chapters: list) -> set:
    """哪些章可以当成「不用再学」：过关的，加上被诊断跳过且前置也满足的。

    跳过必须逐级传导。s2 整段被判跳过、但 s2 的第一章前置是 s1c3 而 s1c3 没学，
    这时 s2 里没有一章真正满足——不传导的话会从 s2 直接跳到 s3，中间完全空着。
    用不动点迭代而不是单趟遍历：章节列表现在正好是拓扑序，但那只是内容碰巧这么排的，
    迭代到不再变化为止，顺序换了也对。
    """
    by_id = {c["id"]: c for c in chapters}
    done = set()
    for _ in range(len(chapters) + 1):
        changed = False
        for chapter in chapters:
            if chapter["id"] in done:
                continue
            if _is_passed(chapter):
                done.add(chapter["id"])
                changed = True
                continue
            if not chapter.get("skipped"):
                continue
            prereqs = [p for p in (chapter.get("prereq") or []) if p in by_id]
            if all(p in done for p in prereqs):
                done.add(chapter["id"])
                changed = True
        if not changed:
            break
    return done


def decorate(course: dict, course_id: str = DEFAULT_COURSE) -> dict:
    """把数据库里的进度贴到课程树上，并算出统计。"""
    import quiz as quiz_lib  # 局部导入：quiz 顶部 import 了本模块

    progress = list_course_progress(course_id)
    # 自测灯要两个数据源：一共几题（题库文件）、过了几题（作答记录）。
    # 两个都一次查全，不在循环里查——目录每刷一次都会走这条路径。
    totals = quiz_lib.quiz_totals(course_id)
    passed_map = quiz_pass_summary(course_id)
    chapters = course["chapters"]
    for chapter in chapters:
        entry = progress.get(chapter["id"]) or {}
        chapter["status"] = entry.get("status") or "todo"
        chapter["mastery"] = int(entry.get("mastery") or 0)
        chapter["notes"] = entry.get("notes") or ""
        # 没有题库的章给 None，不是给 {total: 0}：前端要能区分
        # 「这章还没出题」和「有题但一题没答」，两者的圆点长得不一样。
        total = int(totals.get(chapter["id"]) or 0)
        if total:
            counts = passed_map.get(chapter["id"]) or {}
            chapter["quiz"] = {
                "total": total,
                "answered": min(int(counts.get("answered") or 0), total),
                "passed": min(int(counts.get("passed") or 0), total),
            }
        else:
            chapter["quiz"] = None

    # 诊断跳章：必须在 quiz 数据之后算，mark_skipped 要用「这章有没有题没过」；
    # 也必须在 recommend_next 之前，推荐读的就是这个标记。
    skipped_stages = skip_stages(course_id)
    skipped = mark_skipped(chapters, skipped_stages, passed_map)

    done = sum(1 for c in chapters if c["status"] == "done")
    passed = sum(1 for c in chapters if _is_passed(c))
    course["progress"] = {
        "done": done,
        "passed": passed,
        "total": len(chapters),
        "percent": round(done * 100 / len(chapters)) if chapters else 0,
        # skipped 不进 percent：跳过的意思是「这段不用学」，不是「这段学完了」。
        # 拿它顶进度条，用户会看到 60% 而正文一章没读。
        "skipped": len(skipped),
    }
    stage_titles = {s.get("id"): s.get("title") for s in course.get("stages") or []}
    course["skip"] = {
        "level": SKIP_LEVEL,
        "chapters": skipped,
        "stages": [
            {"id": stage, "title": stage_titles.get(stage) or stage,
             "level": info.get("level"), "evidence": info.get("evidence")}
            for stage, info in (skipped_stages or {}).items()
        ],
    } if skipped_stages else None
    course["next"] = recommend_next(chapters)
    return course


def recommend_next(chapters: list) -> dict | None:
    """按依赖顺序找下一章。三条规则，顺序不能换：

    1. 手上有一章标了 learning 但没 done —— 先把它学完，不要开新的；
    2. 找第一个「前置都满足、自己还没过关」的章节；
    3. 全部满足返回 None，界面据此提示「主干学完了，去项目库」。

    「满足」不等于「过关」：诊断判到 2 级的阶段里的章也算满足，
    否则定制路线等于白做——测出地基会了，推荐还是把你拉回第 1 章。
    规则 1 故意仍用 _is_passed 而不是 satisfied：手点「在学」是他自己要学，
    比诊断的推断优先，被跳过的章他想看就让他看。
    刻意不看「用户最近问了什么」：跟着话头推荐会让人一直在第 3 章打转。
    """
    if not chapters:
        return None
    by_id = {c["id"]: c for c in chapters}
    satisfied = satisfied_ids(chapters)

    for chapter in chapters:
        if chapter.get("status") == "learning" and not _is_passed(chapter):
            return chapter

    for chapter in chapters:
        if chapter["id"] in satisfied:
            continue
        prereqs = chapter.get("prereq") or []
        if all(p in satisfied for p in prereqs if p in by_id):
            return chapter

    return None


def blocked_by(chapters: list, chapter: dict) -> list:
    """这一章的前置里还有谁没满足。用来在他直接点开第 20 章时说清该先补什么。

    用 satisfied 而不是 _is_passed：被诊断跳过的前置不该再被报成「没过关」，
    否则用户刚做完摸底，点开第 20 章还是被提示「先去补第 3 章」。
    """
    satisfied = satisfied_ids(chapters)
    by_id = {c["id"]: c for c in chapters}
    return [
        {"id": p, "title": by_id[p].get("title", ""), "no": by_id[p].get("no")}
        for p in (chapter.get("prereq") or [])
        if p in by_id and p not in satisfied
    ]


def tutor_context(course_id: str, chapter_id: str | None = None) -> str:
    """拼给导师模型看的当前位置。故意只给事实，不给建议——建议要模型自己判断。"""
    course = decorate(load_course(course_id), course_id)
    chapters = course["chapters"]
    lines = ["课程：《%s》" % course.get("title", "")]
    lines.append("总进度：%d / %d 章标了学完（其中 %d 章达到「能自己写出来」）"
                 % (course["progress"]["done"], course["progress"]["total"],
                    course["progress"]["passed"]))

    current = None
    if chapter_id:
        current = next((c for c in chapters if c["id"] == chapter_id), None)
    if current:
        lines.append("他现在打开的是第 %d 章《%s》。" % (current["no"], current["title"]))
        lines.append("这一章要解决的问题：%s" % current.get("summary", ""))
        if current.get("objectives"):
            lines.append("学完应该能做到：%s" % "；".join(current["objectives"]))
        if current.get("practice"):
            lines.append("动手练习：%s" % current["practice"])
        missing = blocked_by(chapters, current)
        if missing:
            lines.append("注意：这一章的前置还没过关——%s。"
                         % "、".join("第 %d 章《%s》" % (m["no"], m["title"]) for m in missing))
        if current.get("status") == "done":
            lines.append("这一章他已经标过学完，掌握度 %d/3。" % current["mastery"])

    skip = course.get("skip")
    if skip:
        # 必须主动说，否则导师看到「总进度 0/33」会劝他从第 1 章开始，
        # 而推荐算法已经把这些阶段绕开了，两边说的话互相打脸。
        stages = "、".join("%s（判 %d 级）" % (s["title"], s["level"]) for s in skip["stages"])
        lines.append(
            "摸底诊断判定这几个阶段他已经会了：%s。共 %d 章按「可跳过」处理，"
            "推荐会绕开它们；但他自己点进去学仍然算数。"
            % (stages, len(skip["chapters"]))
        )

    nxt = course.get("next")
    if nxt:
        lines.append("按依赖顺序，下一章应该是第 %d 章《%s》。"
                     % (nxt["no"], nxt["title"]))
    return "\n".join(lines)


def recent_questions(course_id: str, chapter_id: str, limit: int = 5) -> list:
    from database import list_course_questions
    return list_course_questions(course_id, chapter_id, limit)
