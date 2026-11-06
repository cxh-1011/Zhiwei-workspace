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

from database import list_course_progress

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
    return {
        "chapter": chapter,
        "markdown": body.strip(),
        "prev": order[index - 1] if index > 0 else None,
        "next": order[index + 1] if index + 1 < len(order) else None,
        "total": len(order),
        "index": index + 1,
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


def decorate(course: dict, course_id: str = DEFAULT_COURSE) -> dict:
    """把数据库里的进度贴到课程树上，并算出统计。"""
    progress = list_course_progress(course_id)
    chapters = course["chapters"]
    for chapter in chapters:
        entry = progress.get(chapter["id"]) or {}
        chapter["status"] = entry.get("status") or "todo"
        chapter["mastery"] = int(entry.get("mastery") or 0)
        chapter["notes"] = entry.get("notes") or ""

    done = sum(1 for c in chapters if c["status"] == "done")
    passed = sum(1 for c in chapters if _is_passed(c))
    course["progress"] = {
        "done": done,
        "passed": passed,
        "total": len(chapters),
        "percent": round(done * 100 / len(chapters)) if chapters else 0,
    }
    course["next"] = recommend_next(chapters)
    return course


def recommend_next(chapters: list) -> dict | None:
    """按依赖顺序找下一章。三条规则，顺序不能换：

    1. 手上有一章标了 learning 但没 done —— 先把它学完，不要开新的；
    2. 找第一个「前置都过关、自己还没过关」的章节；
    3. 全部过关返回 None，界面据此提示「主干学完了，去项目库」。

    刻意不看「用户最近问了什么」：跟着话头推荐会让人一直在第 3 章打转。
    """
    if not chapters:
        return None
    by_id = {c["id"]: c for c in chapters}

    for chapter in chapters:
        if chapter.get("status") == "learning" and not _is_passed(chapter):
            return chapter

    for chapter in chapters:
        if _is_passed(chapter):
            continue
        prereqs = chapter.get("prereq") or []
        if all(_is_passed(by_id.get(p, {})) for p in prereqs if p in by_id):
            return chapter

    return None


def blocked_by(chapters: list, chapter: dict) -> list:
    """这一章的前置里还有谁没过关。用来在他直接点开第 20 章时说清该先补什么。"""
    by_id = {c["id"]: c for c in chapters}
    return [
        {"id": p, "title": by_id[p].get("title", ""), "no": by_id[p].get("no")}
        for p in (chapter.get("prereq") or [])
        if p in by_id and not _is_passed(by_id[p])
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

    nxt = course.get("next")
    if nxt:
        lines.append("按依赖顺序，下一章应该是第 %d 章《%s》。"
                     % (nxt["no"], nxt["title"]))
    return "\n".join(lines)


def recent_questions(course_id: str, chapter_id: str, limit: int = 5) -> list:
    from database import list_course_questions
    return list_course_questions(course_id, chapter_id, limit)
