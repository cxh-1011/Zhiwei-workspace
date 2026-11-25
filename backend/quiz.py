"""判分规则。

分层原则，和 course.py 一样：
- 题目放文件（`curriculum/<course>/quiz/<chapter>.json`），能 git、能手改、能 diff；
- 作答与分数放数据库（quiz_attempts），那是运行时状态；
- **判分是确定性函数**：同一道题、同一份答案，任何时候都得到同一个分数。

为什么不让模型当主判据：一是不可复现——同一份答案今天 70 分明天 50 分，用户
不会信；二是每答一题都要等网络，99 道题的成本和体验都不划算。反过来，纯规则
也不行：开放题的同一个意思有很多种说法，死盯关键词会把对的判成错的。所以是
「规则优先、模型只兜模糊区间」，不是二选一。

三种题型三种判法：
- choice：认出选项字母，精确比对；
- blank：归一化后比对，长答案允许包含匹配；
- rubric：按要点命中率给分，反例命中直接判 0。

写题库的人必须注意一件事：rubric 的要点**不能写成一句解释**。用户不会逐字复述
「token 数量不同」，得写成 `{"any": ["token", "切", "编号"]}` 这种词组。
整句子串匹配几乎永远不命中，那时不是规则太严，是题写错了。
"""

import json
import re
import unicodedata
from pathlib import Path

import database
from course import CURRICULUM_ROOT, DEFAULT_COURSE

# 每题得分到这条线才算过。70 而不是 60：rubric 题按要点算分，
# 三分之二命中恰好是 67，还没到「基本都说到」的程度。
PASS_SCORE = 70

# 作答去标点后少于这么多字，一律 0 分。挡的是「不知道」三个字碰巧命中关键词。
MIN_ANSWER_CHARS = 8

# 填空「答案出现在作答里」算命中的最短长度。「a」出现在任何句子里都成立，
# 没有这个阈值，包含匹配等于没判。
MIN_CONTAIN_LEN = 4

# 章节 id 会拼进文件路径，只允许字母数字和连字符，防止 ../ 之类的穿越。
# 与 course.py 的 _ID_RE 是同一套规则，改一处就得改两处。
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")

_KINDS = ("choice", "blank", "rubric")

# 归一化时去掉的字符：空白、下划线、连字符、常见中英标点。
# 去空白对中文尤其重要：「max tokens」和「max_tokens」在填空里是同一个答案；
# 去标点则是为了让「max_tokens。」不至于因为一个句号判错。
_NOISE = re.compile(
    r"[\s\u3000_\-·.,;:!?()\[\]{}<>\"'`「」『』，。、；：！？（）【】《》“”‘’…—–]+"
)

# 独立的单个字母。选择题的作答形态比想象中杂：「B」「b」「B.」「选B」
# 「答案是 B」「B. rate limit」都要认出来，所以不能只 strip。
_SINGLE_LETTER = re.compile(r"(?<![A-Za-z])([A-Za-z])(?![A-Za-z])")


class QuizError(RuntimeError):
    pass


def _norm(text):
    """归一化：全角转半角、转小写、去掉空白与标点。判分只认归一化后的字符串。"""
    return _NOISE.sub("", unicodedata.normalize("NFKC", text or "").casefold())


def _choice_letters(answer):
    """从作答里认出选项字母，认不出返回空列表。"""
    text = unicodedata.normalize("NFKC", answer or "")
    return [m.group(1).upper() for m in _SINGLE_LETTER.finditer(text)]


def _multi_letters(answer):
    """多选题的作答：「AC」「A,C」「A C」都算选了 A 和 C。

    多选不能复用单选的识别函数——「AC」在单选的规则里不是一个「独立字母」，
    会整个认不出来。所以先把分隔符去掉，整串都是字母才逐字拆。
    """
    text = unicodedata.normalize("NFKC", answer or "")
    compact = re.sub(r"[\s,、/+;；]+", "", text)
    if compact and re.fullmatch(r"[A-Za-z]+", compact):
        return [char.upper() for char in compact]
    return _choice_letters(answer)


def _split_lines(answer):
    """多空题的作答：每空一行；也接受直接给 JSON 数组。"""
    if isinstance(answer, (list, tuple)):
        return [str(item) for item in answer]
    text = str(answer or "").strip()
    if text.startswith("["):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, list):
            return [str(item) for item in data]
    return re.split(r"[\n\r]+", text)


def _sub_hit(word, text):
    """归一化后做子串匹配。text 必须是已经归一化过的。"""
    needle = _norm(word)
    return bool(needle) and needle in text


def _point_hit(point, text):
    """一个要点是否命中。

    point 支持两种写法：
    - 字符串：整句做子串匹配。几乎不会命中，只为兼容手写的简题。
    - 对象：`{"any": [...]}` 至少出现一个词，`{"all": [...]}` 全部出现，
      两者同时给就都要满足。
    """
    if isinstance(point, str):
        return _sub_hit(point, text)
    if not isinstance(point, dict):
        return False
    any_words = point.get("any") or []
    all_words = point.get("all") or []
    if not any_words and not all_words:
        return False
    if all_words and not all(_sub_hit(w, text) for w in all_words):
        return False
    if any_words and not any(_sub_hit(w, text) for w in any_words):
        return False
    return True


def _result(score, detail, needs_model=False):
    score = max(0, min(100, int(score)))
    return {
        "score": score,
        "passed": score >= PASS_SCORE,
        "detail": detail,
        "grader": "local",
        "needs_model": bool(needs_model),
    }


def _fail(detail):
    return {"score": 0, "passed": False, "detail": detail,
            "grader": "local", "needs_model": False}


def grade(item, answer):
    """判一道题，返回 {score, passed, detail, grader, needs_model}。

    纯函数：不读库、不联网、不看时间。这条是判分能被信任的前提——
    用户重做同一道题，分数不该变。
    """
    kind = (item or {}).get("kind")
    raw = "" if answer is None else str(answer)
    if kind == "choice":
        return _grade_choice(item, raw)
    if kind == "blank":
        return _grade_blank(item, raw)
    if kind == "rubric":
        return _grade_rubric(item, raw)
    return _fail("未知题型：%s" % kind)


def _grade_choice(item, answer):
    # 多选和单选认字母的方式不一样，正确答案那一侧也要用同一套，
    # 否则多选题的 answer="AC" 会被单选规则认成「没配正确答案」
    reader = _multi_letters if item.get("multi") else _choice_letters
    expected = reader(item.get("answer") or "")
    if not expected:
        return _fail("这道题的正确答案没配好，先跳过")
    got = reader(answer)
    if not got:
        return _fail("没看出你选了哪一项")

    if item.get("multi"):
        want = sorted(set(expected))
        have = sorted(set(got))
        if have == want:
            return _result(100, "正确")
        # 多选不给部分分：少选一个在语义上就是错的，给半分反而让人误以为懂了
        return _result(0, "你选了 %s，正确答案是 %s" % ("".join(have), "".join(want)))

    picked = got[0]
    if picked == expected[0]:
        return _result(100, "正确")
    return _result(0, "你选了 %s，正确答案是 %s" % (picked, expected[0]))


def _grade_blank(item, answer):
    blanks = item.get("blanks") or []
    if not blanks:
        return _fail("这道题的答案没配好，先跳过")

    # 单空题直接用整段作答：用户可能换行写，按行切会把答案切碎
    given = [answer] if len(blanks) == 1 else _split_lines(answer)
    if len(given) < len(blanks):
        given = list(given) + [""] * (len(blanks) - len(given))
    # 多填的不管：填得比空多不是错，可能是把说明也写进去了

    hits, misses, expected_text = [], [], {}
    for index, accepted in enumerate(blanks):
        options = accepted if isinstance(accepted, (list, tuple)) else [accepted]
        text = _norm(given[index])
        ok = any(_blank_match(_norm(option), text) for option in options)
        if ok:
            hits.append(index + 1)
        else:
            misses.append(index + 1)
            expected_text[index + 1] = str(options[0])

    score = round(len(hits) * 100 / len(blanks))
    if not misses:
        return _result(100, "正确")
    if not hits:
        detail = "第 %s 空都不对（正确答案：%s）" % (
            "、".join(str(i) for i in misses),
            "、".join(expected_text[i] for i in sorted(expected_text)),
        )
        return _result(score, detail)
    detail = "第 %s 空不对（正确答案：%s）" % (
        "、".join(str(i) for i in misses),
        "、".join(expected_text[i] for i in sorted(expected_text)),
    )
    return _result(score, detail)


def _blank_match(expected, given):
    """expected 与 given 都已归一化。"""
    if not expected or not given:
        return False
    if given == expected:
        return True
    return len(expected) >= MIN_CONTAIN_LEN and expected in given


def _grade_rubric(item, answer):
    must = item.get("must") or []
    forbid = item.get("forbid") or []
    if not must:
        return _fail("这道题的要点没配好，先跳过")

    text = _norm(answer)
    if len(text) < MIN_ANSWER_CHARS:
        return _fail("作答太短（少于 %d 个字），看不出你是否真的理解" % MIN_ANSWER_CHARS)

    blocked = [str(word) for word in forbid if _sub_hit(word, text)]
    if blocked:
        # 关键词全中但结论反了，是开放题判分最常见的漏判，只能靠反例挡
        return _result(0, "结论反了：「%s」这句和正确答案相反" % blocked[0])

    hit_index, miss_point = [], []
    for index, point in enumerate(must):
        if _point_hit(point, text):
            hit_index.append(index)
        else:
            miss_point.append(point)

    score = round(len(hit_index) * 100 / len(must))
    if miss_point:
        detail = "说到 %d / %d 个要点，还差：%s" % (
            len(hit_index), len(must),
            "；".join(_point_label(point) for point in miss_point),
        )
    else:
        detail = "要点都说到了（%d / %d）" % (len(hit_index), len(must))

    # 完全没沾边就不必再问模型：模型救不回一个连关键词都没碰到的答案。
    # 只有「沾了一部分但不够」才是真模糊，值得花一次调用。
    needs_model = 0 < score < PASS_SCORE
    return _result(score, detail, needs_model=needs_model)


def _point_label(point):
    if isinstance(point, str):
        return point
    if isinstance(point, dict):
        label = point.get("point") or point.get("any") or point.get("all") or []
        if isinstance(label, str):
            return label
        return "、".join(str(word) for word in label[:3]) or "（未命名要点）"
    return str(point)


# ---------- 题库文件 ----------

def _quiz_file(chapter_id, course_id):
    if not _ID_RE.match(course_id or ""):
        raise QuizError("课程 id 不合法：%s" % course_id)
    if not _ID_RE.match(chapter_id or ""):
        raise QuizError("章节 id 不合法：%s" % chapter_id)
    return CURRICULUM_ROOT / course_id / "quiz" / ("%s.json" % chapter_id)


def load_quiz(chapter_id, course_id=DEFAULT_COURSE):
    """读一章的题库。

    文件不存在时返回空 items，**不抛异常**：题库是分批写的，只有少数章先有，
    「这一章还没出判分题」是常态，不该表现成错误页。

    内容不合规则抛 QuizError：题库写错了还能跑，是后面所有判分失真的源头。
    """
    path = _quiz_file(chapter_id, course_id)
    if not path.is_file():
        return {"chapter_id": chapter_id, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QuizError("题库读不了：%s（%s）" % (path.name, exc)) from exc
    if not isinstance(data, dict):
        raise QuizError("题库顶层必须是对象：%s" % path.name)
    data.setdefault("chapter_id", chapter_id)
    data.setdefault("items", [])
    errors = validate_quiz(data)
    if errors:
        raise QuizError("题库格式有问题（%s）：%s" % (path.name, "；".join(errors[:4])))
    return data


def validate_quiz(quiz, chapter=None):
    """检查题库，返回问题列表（空列表 = 没问题）。

    刻意不抛异常、一次返回全部问题：写题库时想一次看全，而不是改一个跑一次。
    chapter 传进来时顺带校验 objective 下标是否越界——那是最容易写错的一处。
    """
    problems = []
    chapter_id = (quiz or {}).get("chapter_id") or ""
    items = (quiz or {}).get("items")
    if not isinstance(items, list) or not items:
        return ["没有题目"]

    objectives = (chapter or {}).get("objectives") or []
    seen = set()
    for index, item in enumerate(items):
        where = "第 %d 题" % (index + 1)
        if not isinstance(item, dict):
            problems.append("%s 不是对象" % where)
            continue

        item_id = str(item.get("id") or "")
        if not item_id.startswith(chapter_id + "-"):
            problems.append("%s 的 id 应该是 %s-qN，实际是 %s" % (where, chapter_id, item_id or "空"))
        if item_id in seen:
            problems.append("%s 的 id 重复：%s" % (where, item_id))
        seen.add(item_id)

        kind = item.get("kind")
        if kind not in _KINDS:
            problems.append("%s 的 kind 不认识：%s" % (where, kind))
            continue

        if not str(item.get("stem") or "").strip():
            problems.append("%s 没有题干" % where)

        objective = item.get("objective")
        if objective is not None:
            if not isinstance(objective, int) or objective < 0:
                problems.append("%s 的 objective 必须是非负整数" % where)
            elif objectives and objective >= len(objectives):
                problems.append("%s 的 objective=%s 超出本章 %d 个学习目标"
                                % (where, objective, len(objectives)))

        if kind == "choice":
            options = item.get("options") or []
            if not isinstance(options, list) or len(options) < 2:
                problems.append("%s 至少要有两个选项" % where)
                continue
            letters = []
            for option in options:
                found = _choice_letters(str(option))
                if not found:
                    problems.append("%s 的选项没有字母编号：%s" % (where, option))
                    break
                letters.append(found[0])
            else:
                if len(set(letters)) != len(letters):
                    problems.append("%s 的选项字母重复：%s" % (where, "".join(letters)))
                answer = _choice_letters(str(item.get("answer") or ""))
                if not answer:
                    problems.append("%s 没有配正确答案" % where)
                elif answer[0] not in letters:
                    problems.append("%s 的正确答案 %s 不在选项里" % (where, answer[0]))

        elif kind == "blank":
            blanks = item.get("blanks") or []
            if not isinstance(blanks, list) or not blanks:
                problems.append("%s 没有配答案" % where)
                continue
            for position, accepted in enumerate(blanks, start=1):
                options = accepted if isinstance(accepted, (list, tuple)) else [accepted]
                if not any(str(option).strip() for option in options):
                    problems.append("%s 第 %d 空的答案是空的" % (where, position))

        elif kind == "rubric":
            must = item.get("must") or []
            if not isinstance(must, list) or not must:
                problems.append("%s 没有配要点" % where)
                continue
            for point in must:
                if isinstance(point, str):
                    continue
                if not isinstance(point, dict) or not (point.get("any") or point.get("all")):
                    problems.append("%s 的要点必须是字符串或 {any/all: [...]}" % where)
                    break

    return problems


def public_items(quiz):
    """给前端的题目：只有题干和选项。

    answer / blanks / must / forbid / explain 全部剥掉。这不是防君子——
    页面上按 F12 就能看到响应体，只要发了就等于公开了。
    """
    items = []
    for item in (quiz or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        public = {"id": item.get("id"), "kind": item.get("kind"),
                  "stem": item.get("stem")}
        if item.get("options"):
            public["options"] = list(item["options"])
        if item.get("multi"):
            public["multi"] = True
        if item.get("kind") == "blank":
            # 空的数量要给，否则前端不知道该画几个输入框。给数量不给答案。
            public["blank_count"] = len(item.get("blanks") or [])
        items.append(public)
    return items


# ---------- 与作答记录合并 ----------

def chapter_state(chapter_id, course_id=DEFAULT_COURSE, quiz=None):
    """这一章的判分状态：每题最近一次过没过，以及整章过了几题。

    「每题取最近一次」而不是「取最高分」：取最高分等于允许刷分，
    刷到一次蒙对就永久算过，那判分就不代表现在的水平了。
    """
    quiz = quiz or load_quiz(chapter_id, course_id)
    items = quiz.get("items") or []
    attempts = database.list_quiz_attempts(course_id, chapter_id, limit=300)
    latest = {}
    for attempt in attempts:  # 已经是倒序，先到的就是最近一次
        latest.setdefault(attempt["item_id"], attempt)

    rows = []
    for item in items:
        attempt = latest.get(item.get("id"))
        rows.append({
            "id": item.get("id"),
            "kind": item.get("kind"),
            "answered": bool(attempt),
            "passed": bool(attempt and attempt["passed"]),
            "score": attempt["score"] if attempt else None,
            "at": attempt["created_at"] if attempt else None,
        })

    return {
        "chapter_id": chapter_id,
        "total": len(rows),
        "answered": sum(1 for row in rows if row["answered"]),
        "passed": sum(1 for row in rows if row["passed"]),
        "latest_score": attempts[0]["score"] if attempts else None,
        "last_at": attempts[0]["created_at"] if attempts else None,
        "items": rows,
    }


def record(chapter_id, item, answer, seconds=0, course_id=DEFAULT_COURSE, result=None):
    """判分并落库，返回判分结果 + 这一章的最新状态。

    把「判分」和「写库」放在一个函数里，是为了让调用方不可能只做一半：
    判了不记，用户下次重做时系统不认账；记了不判，库里全是假分数。
    """
    result = result or grade(item, answer)
    database.add_quiz_attempt(
        course_id, chapter_id, str(item.get("id") or ""), str(item.get("kind") or ""),
        "" if answer is None else str(answer), result["score"], 1 if result["passed"] else 0,
        result["grader"], result["detail"], seconds,
    )
    database.set_course_progress(
        course_id, chapter_id,
        quiz_score=result["score"], quiz_at=database.now_text(),
    )
    return result, chapter_state(chapter_id, course_id)


# ---------- 模型兜底 ----------

def grade_with_model(item, answer, timeout=25):
    """本地判分落进模糊区间时，让模型判一次。

    只判「说到几成」，不让它重讲知识、不许它改题目。返回结构与 grade() 一致，
    grader 固定为 "model"。模型不可用或返回不合规时**退回本地结论**并注明原因：
    判分器坏了不该把用户卡在这一题上。
    """
    local = grade(item, answer)
    if not local["needs_model"]:
        return local

    from llm import chat_completion  # 延迟导入：没配模型时这个模块照样能加载

    must = item.get("must") or []
    prompt = [
        {"role": "system", "content":
            "你是一个判分器，只做一件事：判断学生的作答是否说到了给定要点。"
            "只返回 JSON，不要 Markdown、不要代码块、不要解释性段落。格式："
            '{"hit":[命中要点下标],"miss":[漏掉要点下标],"score":0到100的整数,"reason":"一句话依据"}。'
            "要点下标从 0 开始。学生答对了大意就算命中，不要求逐字一致。"
            "不要重讲知识点，不要鼓励，不要评价学生。"},
        {"role": "user", "content":
            "题目：%s\n\n要点：\n%s\n\n学生作答：\n%s"
            % (item.get("stem") or "", "\n".join(
                "%d. %s" % (index, _point_label(point)) for index, point in enumerate(must)),
               answer or "")},
    ]
    try:
        response, _ = chat_completion(prompt, tools=None, timeout=timeout)
        content = getattr(response, "content", None) or ""
    except Exception:
        local["detail"] = local["detail"] + "（模型判定不可用，按规则判）"
        return local

    payload = _first_json(content)
    score = payload.get("score") if payload else None
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        local["detail"] = local["detail"] + "（模型没给出可用的分数，按规则判）"
        return local

    score = max(0, min(100, int(round(score))))
    reason = str(payload.get("reason") or "").strip()
    hit = payload.get("hit")
    hit_count = len(hit) if isinstance(hit, list) else int(round(score * len(must) / 100))
    detail = "说了 %d / %d 个要点%s" % (
        max(0, min(len(must), hit_count)), len(must),
        ("：" + reason) if reason else "",
    )
    return {"score": score, "passed": score >= PASS_SCORE, "detail": detail,
            "grader": "model", "needs_model": False}


def _first_json(text):
    """从模型输出里取第一个能解析的 JSON 对象。

    流式分片和最终 message 可能是同一段文字的两种表示，拼接会得到两份 JSON，
    所以只要第一个能解析的。用括号配对找边界，不去暴力试所有子串——那样既慢，
    也可能把半截对象试成功。
    """
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    candidates = [fenced.group(1).strip()] if fenced else []
    candidates.append(raw)
    for candidate in candidates:
        for start, char in enumerate(candidate):
            if char != "{":
                continue
            depth, in_string, escaped = 0, False, False
            for index in range(start, len(candidate)):
                current = candidate[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif current == "\\":
                        escaped = True
                    elif current == '"':
                        in_string = False
                    continue
                if current == '"':
                    in_string = True
                elif current == "{":
                    depth += 1
                elif current == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(candidate[start:index + 1])
                        except ValueError:
                            break
                        if isinstance(data, dict):
                            return data
                        break
    return None
