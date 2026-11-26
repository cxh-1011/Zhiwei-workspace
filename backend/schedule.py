"""把「还没学会的章」铺到具体日期上。

三件事分得很清：

- `course.py` 决定**哪些章该学**（学完的、诊断判过且前置满足的都算 satisfied）；
- `profile.py` 决定**哪一段几级**（画像的口径，排期只读结论，不重判）；
- 本模块只做算术：几章 × 多少分钟，填进哪几天。

**日期和分钟必须由代码算。** 让模型排日程，它会给出「周三学两小时」这种执行
不了的安排——它不知道今天周几、不知道昨天已经掉队、也不知道一章 40 分钟塞不进
剩下的 25 分钟。模型最多决定「同时能选的几章里先动哪章」，顺序还要再过一遍
依赖检查（见 `safe_order`），否则它会把第 20 章排到第一周。

排期只排「未来」，不记历史：`plan_slots` 里只有还没学完的章。学完一章由
`course_progress` 表达，排期表再存一份「我完成了」就是两套真相。
"""

import json
import math
import re
from datetime import date, timedelta

import course as course_lib

DEFAULT_MINUTES = 90         # 每天可投入。一章 40 分钟，90 分钟正好两章，不浪费整天
DEFAULT_WEEKDAYS = (1, 2, 3, 4, 5, 6, 7)  # 1=周一
DEFAULT_CHAPTER_MINUTES = 40
MIN_MINUTES = 10
MAX_MINUTES = 480
MIN_CHAPTER_MINUTES = 5
MAX_CHAPTER_MINUTES = 240
# 一个人一天学 8 小时已经是上限。压缩策略最多把每天顶到这个值，
# 再高就不是排期，是把课表糊在墙上。
MAX_COMPRESS_MINUTES = 240
MAX_HORIZON_DAYS = 400        # 400 个可用日还排不完，说明设置有问题
STRATEGIES = ("shift", "compress", "drop")
WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class PlanError(RuntimeError):
    pass


# ---------- 设置 ----------

def parse_day(text):
    """把 YYYY-MM-DD 变成 date。空值/坏格式返回 None，不抛错——
    设置是用户填的，填错该回到默认值，不该让整页打不开。"""
    if isinstance(text, date):
        return text
    try:
        return date.fromisoformat(str(text)[:10])
    except (ValueError, TypeError):
        return None


def day_text(day):
    return day.isoformat()


def weekday_label(day):
    return WEEKDAY_LABELS[day.isoweekday() - 1]


def _parse_weekdays(raw):
    cn = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
    if isinstance(raw, (list, tuple, set)):
        parts = [str(item) for item in raw]
    else:
        parts = re.split(r"[,\s，]+", str(raw or ""))
    days = set()
    for part in parts:
        token = str(part).strip()
        if token.isdigit() and 1 <= int(token) <= 7:
            days.add(int(token))
        elif token in cn:
            days.add(cn[token])
    return sorted(days) or list(DEFAULT_WEEKDAYS)


def normalize_settings(raw=None, today=None):
    """把用户/库里的一行设置修成可用的形状。

    开始日不能早于今天：从上周一开始排，第一天就全是「欠账」，用户一打开
    看到七条红色过期，会以为系统在怪他。真要补之前的，走 reschedule。
    """
    raw = dict(raw or {})
    today = today or date.today()
    start = parse_day(raw.get("start_date")) or today
    if start < today:
        start = today
    weekdays = _parse_weekdays(raw.get("weekdays"))
    try:
        minutes = int(raw.get("minutes") or DEFAULT_MINUTES)
    except (TypeError, ValueError):
        minutes = DEFAULT_MINUTES
    minutes = max(MIN_MINUTES, min(MAX_MINUTES, minutes))
    strategy = str(raw.get("catch_up") or "shift")
    if strategy not in STRATEGIES:
        strategy = "shift"
    return {
        "start_date": day_text(start),
        "weekdays": weekdays,
        "minutes": minutes,
        "include_skipped": bool(raw.get("include_skipped")),
        "catch_up": strategy,
    }


def iter_days(start, weekdays, limit=MAX_HORIZON_DAYS):
    """从 start 起，按周几过滤，逐个给出可用的日子。

    用满 limit 就正常结束（StopIteration），不在这里抛错：调用方拿到的
    「没日子了」和「设置有问题」是同一件事，但错误信息该由它补上下文。
    """
    allowed = set(weekdays or DEFAULT_WEEKDAYS)
    day = start
    produced = 0
    while produced < limit:
        if day.isoweekday() in allowed:
            produced += 1
            yield day
        day += timedelta(days=1)


# ---------- 哪些章要学 ----------

def chapter_minutes(chapter):
    try:
        value = int(chapter.get("minutes") or DEFAULT_CHAPTER_MINUTES)
    except (TypeError, ValueError):
        value = DEFAULT_CHAPTER_MINUTES
    return max(MIN_CHAPTER_MINUTES, min(MAX_CHAPTER_MINUTES, value))


def classify(course, include_skipped=False):
    """把 33 章分成四堆：已学完 / 诊断判会 / 正文没写 / 要排。

    「诊断判会」只算**真正满足**的章——`satisfied_ids` 会沿依赖链传导，
    某段被判 2 级但前置没学，那几章其实还得学，不能算进省下的时间。
    这正是「画像接上排期」的地方：省掉多少分钟，按实际能绕过的章算。
    """
    chapters = course.get("chapters") or []
    satisfied = course_lib.satisfied_ids(chapters)
    done, skipped, unwritten, learnable = [], [], [], []
    extra = []  # include_skipped 时排进来的已跳过章

    for chapter in chapters:
        passed = int(chapter.get("status") == "done")
        if passed:
            done.append(chapter)
            continue
        if not chapter.get("written"):
            unwritten.append(chapter)
            continue
        if chapter["id"] in satisfied:
            skipped.append(chapter)
            if include_skipped:
                extra.append(chapter)
            continue
        learnable.append(chapter)

    learnable = learnable + extra
    learnable.sort(key=lambda c: (c.get("no") or 0))
    return {
        "learnable": learnable,
        "done": done,
        "skipped": skipped,
        "unwritten": unwritten,
        "satisfied": satisfied,
    }


def _basis(info, stages, include_skipped):
    # include_skipped 时 learnable 里已经含了 skipped，按 id 去重再算总量，
    # 否则总分钟会凭空多出一整段。
    seen = set()
    all_chapters = []
    for group in (info["done"], info["skipped"], info["unwritten"], info["learnable"]):
        for chapter in group:
            if chapter["id"] not in seen:
                seen.add(chapter["id"])
                all_chapters.append(chapter)

    total_minutes = sum(chapter_minutes(c) for c in all_chapters)
    planned = sum(chapter_minutes(c) for c in info["learnable"])
    # 省下的时间只报「真的不用排」的部分：学完的 + 被诊断真正绕过的。
    # 正文没写的章也不排，但那是内容没写完，不是画像省下的，不能算进来。
    saved = sum(chapter_minutes(c) for c in info["done"])
    if not include_skipped:
        saved += sum(chapter_minutes(c) for c in info["skipped"])
    stage_titles = {s.get("id"): s.get("title") for s in stages or []}
    skipped_by_stage = {}
    for chapter in info["skipped"]:
        stage = str(chapter.get("stage") or "")
        skipped_by_stage.setdefault(stage, []).append(chapter["id"])
    return {
        "total_chapters": len(all_chapters),
        "learnable": len(info["learnable"]),
        "done": len(info["done"]),
        "skipped": len(info["skipped"]),
        "unwritten": len(info["unwritten"]),
        "minutes": planned,
        "all_minutes": total_minutes,
        "saved_minutes": saved,
        "include_skipped": bool(include_skipped),
        "skipped_stages": [
            {
                "id": stage,
                "title": stage_titles.get(stage) or stage,
                "chapters": len(ids),
                "minutes": sum(
                    chapter_minutes(c) for c in info["skipped"]
                    if str(c.get("stage") or "") == stage
                ),
            }
            for stage, ids in skipped_by_stage.items()
        ],
    }


def basis(course, include_skipped=False):
    """排期页上「画像帮你省了多少」的依据。不排期也应该看得到——
    用户得先知道「33 章里有 10 章不用学」，才明白为什么是 14 天而不是 22 小时。"""
    return _basis(classify(course, include_skipped), course.get("stages"), include_skipped)


def signature(course, include_skipped=False):
    """这份排期是按哪张画像排的。诊断重测会改它，学完一章不会。

    只记「哪些阶段被判可跳过」，不记整份画像：等级从 2 变 3、依据文字变了，
    都不改变要排哪些章，没必要提示用户重排。复习模式（include_skipped）下
    跳过章也在排期里，画像怎么变都不影响内容，返回空签名。
    """
    if include_skipped:
        return ""
    skip = course.get("skip") or {}
    return ",".join(sorted(str(row.get("id")) for row in skip.get("stages") or []))


# ---------- 顺序与铺日期 ----------

def safe_order(chapters, preferred, satisfied):
    """按模型给的偏好顺序排，但绝不违反依赖。

    模型只该决定「同时能选的几章里先动哪章」。它看到「第 20 章最重要」就可能
    把它排第一，而第 20 章的前置一章没学——排出来的计划第一天就执行不了。
    所以每放一章都检查前置是否已满足；模型没提到的章按原顺序补在后面。
    """
    by_id = {c["id"]: c for c in chapters}
    pending = []
    seen = set()
    for cid in list(preferred or []) + [c["id"] for c in chapters]:
        if cid in by_id and cid not in seen:
            seen.add(cid)
            pending.append(cid)

    done = set(satisfied)
    ordered = []
    while pending:
        for cid in list(pending):
            chapter = by_id[cid]
            # 前置不看「在不在本次待学列表」：不在待学列表只可能是它已经满足
            # （done/satisfied）或数据不一致。按后者处理更安全——宁可不排，
            # 也不要把第 20 章放到第一天，让用户点开就撞一堵墙。
            prereqs = chapter.get("prereq") or []
            if all(p in done for p in prereqs):
                ordered.append(chapter)
                done.add(cid)
                pending.remove(cid)
                break
        else:
            # 有环或前置缺失：按原顺序收尾，不丢章
            ordered.extend(by_id[cid] for cid in pending)
            break
    return ordered


def assign(items, days, minutes_per_day):
    """把章按顺序塞进日子。一章不拆——半章没法验收，也没法说「今天学了半章」。

    规则：当天剩余容量放得下就放；放不下就换下一天。
    单章比一整天的容量还长（比如某章 90 分钟、每天 60 分钟）时独占一天，
    宁可那天超一点，也不把它劈成两天。
    """
    slots = []
    used = 0
    seq = 0
    current = next(days, None)
    if current is None:
        raise PlanError("没有可用的学习日，检查每周哪几天")
    days_used = 0

    for item in items:
        need = int(item["minutes"])
        while current is not None and used and used + need > minutes_per_day:
            current = next(days, None)
            used = 0
            seq = 0
        if current is None:
            raise PlanError("排期超出最长范围，检查每天分钟数或周几设置")
        slots.append({
            "day": day_text(current),
            "seq": seq,
            "chapter_id": item["chapter_id"],
            "minutes": need,
        })
        if used == 0:
            days_used += 1
        used += need
        seq += 1
    return slots, days_used


def build(course, settings=None, today=None, order=None):
    """按当前画像重排整门课。返回槽位、依据和统计所需的天数。"""
    today = today or date.today()
    normalized = normalize_settings(settings, today)
    info = classify(course, normalized["include_skipped"])
    chapters = safe_order(info["learnable"], order, info["satisfied"])
    items = [{"chapter_id": c["id"], "minutes": chapter_minutes(c)} for c in chapters]
    days = iter_days(parse_day(normalized["start_date"]), normalized["weekdays"])
    slots, days_used = assign(items, days, normalized["minutes"])
    return {
        "settings": normalized,
        "slots": slots,
        "days": days_used,
        "basis": _basis(info, course.get("stages"), normalized["include_skipped"]),
        "chapters": [c["id"] for c in chapters],
    }


def reschedule(course, slots, settings, strategy="shift", today=None):
    """掉队之后重排。三种策略是三种态度，别合成一个「智能调整」：

    - shift（顺延）：该学的都学，结束日往后推。默认。
    - compress（压缩）：该学的都学，结束日尽量不动，每天多塞一点。
    - drop（放弃）：过期没学的这几章本轮不排了。要的就是「这几天没空，
      那几章先欠着」，硬把它们塞进后面只会让新的计划一开始就超载。
    """
    today = today or date.today()
    normalized = normalize_settings(settings, today)
    if strategy not in STRATEGIES:
        raise PlanError("不认识的掉队策略：%s" % strategy)

    info = classify(course, normalized["include_skipped"])
    done = {c["id"] for c in info["done"]}
    pending = [c for c in info["learnable"] if c["id"] not in done]

    dropped = []
    overdue_ids = {
        slot["chapter_id"] for slot in slots
        if parse_day(slot.get("day")) and parse_day(slot["day"]) < today
        and slot["chapter_id"] not in done
    }
    if strategy == "drop":
        dropped = [c["id"] for c in pending if c["id"] in overdue_ids]
        pending = [c for c in pending if c["id"] not in overdue_ids]

    # 压缩要回答「在原结束日之前排完，每天至少要多少分钟」
    capacity = normalized["minutes"]
    if strategy == "compress":
        original_end = max((parse_day(s["day"]) for s in slots), default=None)
        remaining = sum(chapter_minutes(c) for c in pending)
        if original_end and remaining:
            avail = sum(
                1 for day in iter_days(today, normalized["weekdays"], MAX_HORIZON_DAYS)
                if day <= original_end
            ) or 1
            capacity = min(MAX_COMPRESS_MINUTES,
                           max(capacity, int(math.ceil(remaining / avail))))

    items = [{"chapter_id": c["id"], "minutes": chapter_minutes(c)} for c in pending]
    days = iter_days(today, normalized["weekdays"])
    new_slots, days_used = assign(items, days, capacity)
    return {
        "settings": dict(normalized, minutes=normalized["minutes"]),
        "slots": new_slots,
        "days": days_used,
        "capacity": capacity,
        "dropped": dropped,
        "basis": _basis(info, course.get("stages"), normalized["include_skipped"]),
    }


# ---------- 统计 ----------

def summarize(slots, progress, today=None):
    """排期页顶部要的数字。`progress` 是 {chapter_id: {"status": ...}}。"""
    today = today or date.today()
    total = len(slots)
    done_count = 0
    minutes_total = 0
    minutes_done = 0
    overdue = []
    today_slots = []
    end = None

    for slot in slots:
        minutes = int(slot.get("minutes") or 0)
        minutes_total += minutes
        is_done = (progress.get(slot["chapter_id"]) or {}).get("status") == "done"
        if is_done:
            done_count += 1
            minutes_done += minutes
        day = parse_day(slot.get("day"))
        if day:
            end = day if end is None or day > end else end
        if day == today:
            today_slots.append(slot["chapter_id"])
        if day and day < today and not is_done:
            overdue.append(slot["chapter_id"])

    return {
        "slots": total,
        "done": done_count,
        "left": total - done_count,
        "minutes_total": minutes_total,
        "minutes_done": minutes_done,
        "minutes_left": minutes_total - minutes_done,
        "days": len({s["day"] for s in slots}),
        "end": day_text(end) if end else None,
        "today": today_slots,
        "overdue": overdue,
        "overdue_minutes": sum(
            int(s.get("minutes") or 0) for s in slots
            if s["chapter_id"] in overdue
        ),
    }


# ---------- 模型：只定先动哪几章 ----------

def model_order(chapters, note="", timeout=20):
    """让模型给一个偏好顺序。失败一律返回 ok=False，由调用方回退到原顺序。

    不给它日期、不给它分钟、不让它增删章——它只回答「这些章里先学哪几章」。
    排期是能执行的东西，不是让模型发挥的作文。
    """
    if not chapters:
        return {"ok": True, "order": [], "reason": ""}

    from llm import chat_completion  # 延迟导入：没配模型时本模块照样能加载

    listing = "\n".join(
        "%s 第%d章《%s》（%s，约%d分钟）"
        % (c["id"], c.get("no") or 0, c.get("title") or "",
           c.get("stage_title") or "", chapter_minutes(c))
        for c in chapters
    )
    prompt = [
        {"role": "system", "content":
            "你在给一门 Agent 开发课排先后顺序。只决定「先学哪几章」，"
            "不决定日期、不决定每天学多久、不增删章节。"
            "只返回 JSON，不要 Markdown、不要代码块。格式："
            '{"order":["章节id",...],"reason":"一句话"}。'
            "order 必须包含给你的全部章节 id，按你建议的学习先后排列。"},
        {"role": "user", "content":
            "待学章节（按课程原顺序）：\n%s\n\n学习者补充：%s\n\n"
            "排序原则：前置知识先学；被章节实测打回的章优先补；"
            "同一阶段连着学完再换阶段。"
            % (listing, note or "无")},
    ]
    try:
        response, _ = chat_completion(prompt, tools=None, timeout=timeout)
        content = getattr(response, "content", None) or ""
    except Exception as exc:  # 没配模型、超时、网络不通，都走同一条回退
        return {"ok": False, "order": [], "reason": "模型不可用：%s" % exc}

    payload = _first_json(content)
    order = payload.get("order") if isinstance(payload, dict) else None
    if not isinstance(order, list):
        return {"ok": False, "order": [], "reason": "模型没有按格式返回顺序"}

    known = {c["id"] for c in chapters}
    cleaned = []
    for cid in order:
        cid = str(cid).strip()
        if cid in known and cid not in cleaned:
            cleaned.append(cid)
    if not cleaned:
        return {"ok": False, "order": [], "reason": "模型返回的章节 id 都不在待学列表里"}
    return {
        "ok": True,
        "order": cleaned,
        "reason": str(payload.get("reason") or "")[:300],
        "missing": len(known - set(cleaned)),
    }


def _first_json(text):
    """模型偶尔会在 JSON 外面包一层代码块或一句解释。取第一段能解析的对象。"""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    for candidate in ([fenced.group(1)] if fenced else []) + [raw]:
        try:
            value = json.loads(candidate.strip())
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            return {"order": value}
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group(0))
        except ValueError:
            return {}
    return {}
