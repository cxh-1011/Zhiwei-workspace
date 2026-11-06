"""按需检索记忆，而不是把记忆全部塞进上下文。

第一版的长期记忆是「最近 20 条整段拼进 system prompt」。它有个硬上限：第 21 条
永远看不到。想让它看见更多，唯一的路是提高上限——每轮对话都为「可能用得上」的
历史付 token，越往后越慢，回答也没变准。

这里换成另一条路：常驻上下文只留最近一小段，其余的按当前问题现场检索。容量不动，
够得着的范围变大。三处可检索的东西，正好对应「他以前说过什么」的三个层面：

- memories：他明确要求记下来的（长期、稳定，但条目少）；
- agent_runs：他实际问过、我实际答过的（信息最多，但没人会去翻 26 个对话）；
- course_progress：他学到哪了（最准的结构化状态，但不在记忆表里）。

打分不用模型、不用向量库：查询和文档都切成「中文二元组 + 英文数字词」求交集，
再按词的信息量加权。中文没有空格，二元组是唯一不依赖分词的稳妥切法；
按信息量加权则是为了压住泛词——一个查询里常有半句是「我的」「怎么」，
按命中个数算的话，「学习台」只要沾到「学习」就能捞回一堆无关旧对话。
"""

import math
import re

import course as course_lib

# 切完词要丢掉两类噪声，规则分开写是因为它们的毒性不一样：
# - 语气/结构助词：跟谁配对都没信息量（「好的」「了吗」「什么」），碰到就丢；
# - 代词、助动词、方位词：单独出现没意义，但能组成实词（智能、下载、上游），
#   只有**两个都是这类字**时才丢（「我的」「这个」「是在」）。
PARTICLES = set("的了吗呢吧啊嘛哦呀哈么")
WEAK_CHARS = set("我你他她它在是有没不这会要能想来说去上个下对就都也还这那把被给很太又再才一")

# 一条命中最多给多少字：检索是拿来判断「要不要继续追问」的，不是把原文搬进上下文
SNIPPET_CHARS = 260
MAX_QUERY_CHARS = 120

# 相关度下限，两道：
# - 命中了「不常见」的词（在候选里出现率 ≤10%），覆盖三分之一查询信息量就算相关；
# - 只命中泛词（「学习」「怎么」这种到处都是的），必须覆盖六成才算。
# 为什么分两道：泛词命中几乎是必然的，不分档的话，「学习台」会因为沾到「学习」
# 把一堆聊过学习的旧对话全捞回来；而只有一道高门槛，又会误杀「视频链接」这种
# 只命中其中一部分的合理查询。
MIN_RATIO = 0.28
MIN_RATIO_GENERIC = 0.6
SPECIFIC_DF_RATIO = 0.1

# 有些问法根本没有实词，检索词一个都命中不了，但它问的是确定的东西：
#   「上次我们聊到哪了」→ 最近的记录；「我学到哪了」→ 课程进度。
# 这两类各给一组触发词，命中就退化成「按时间取最近的几条」，而不是回一句「没记录」。
RECENT_MARKERS = ("上次", "最近", "刚才", "昨天", "前天", "之前聊", "聊到哪", "说到哪", "讲到哪", "接着上次")
PROGRESS_MARKERS = ("学到哪", "学到第", "进度", "下一章", "哪一章", "学完没", "学到什么")

SCOPES = ("all", "memory", "conversation", "course")
STATUS_LABELS = {"done": "已学完", "learning": "在学", "todo": "还没开始"}
SOURCE_LABELS = {"agent": "助手按你要求写入", "user": "你自己添加"}


# ---------------------------------------------------------------- 切词与打分

def _compact(text: str) -> str:
    """去掉空白并转小写，顺便干掉标点：切二元组时标点会污染相邻的字。"""
    lowered = str(text or "").lower()
    return re.sub(r"[\s\u3000]+", "", lowered)


def terms(text: str) -> set[str]:
    """把一段话切成可比较的词元：英文/数字整词 + 中文二元组（丢掉噪声二元组）。

    二元组只在「两边都是中文」或「一边中文一边数字」时成立。跨语种的组是垃圾：
    「方向 LoRA」去掉空格后会切出「向l」这种词，它在任何文档里都不存在，
    却因为「稀有」拿到最高权重，把真正命中的「方向」稀释到阈值以下——
    实测就是它让「方向 LoRA」什么都查不到。数字要留，因为「第7章」是常用说法。
    """
    lowered = str(text or "").lower()
    tokens = set(re.findall(r"[a-z0-9][a-z0-9_+#.\-]{1,}", lowered))
    compact = _compact(lowered)
    for index in range(max(0, len(compact) - 1)):
        left, right = compact[index], compact[index + 1]
        left_cjk = "\u3400" <= left <= "\u9fff"
        right_cjk = "\u3400" <= right <= "\u9fff"
        usable = (left_cjk and right_cjk) or (left_cjk and right.isdigit()) or (left.isdigit() and right_cjk)
        if not usable:
            continue
        pair = left + right
        if set(pair) & PARTICLES:
            continue
        if all(char in WEAK_CHARS for char in pair):
            continue
        tokens.add(pair)
    return tokens


def _prepare(query: str) -> tuple[set[str], str]:
    query = str(query or "").strip()[:MAX_QUERY_CHARS]
    if not query:
        raise ValueError("检索内容不能为空")
    return terms(query), _compact(query)


def _rank(query: str, texts: list[str]) -> list[tuple[int, float]]:
    """对一批文本打分，返回 [(下标, 相关度 0~1)]，按相关度降序。

    三个设计点：
    1. 权重用 idf：几乎每篇都有的词权重接近 0，稀有词权重高。这样「学习台」不会因为
       命中「学习」就把所有聊过学习的记录都算作相关；
    2. 分母是查询词的总权重，于是分数是「这次查询的信息量被覆盖了多少」，可以直接
       卡一个统一的阈值，不必按来源各调一套；
    3. 整句原样出现额外加半个分母：他问「我在做什么方向」，一条写的正是「研究方向：
       agent 开发」的记忆，应该把只共享「方向」的条目压下去。
    """
    query_terms, compact_query = _prepare(query)
    if not query_terms or not texts:
        return []

    doc_terms = [terms(text) for text in texts]
    seen: dict[str, int] = {}
    for item in doc_terms:
        for term in item & query_terms:
            seen[term] = seen.get(term, 0) + 1
    weights = {term: math.log(1 + len(texts) / (1 + seen.get(term, 0))) for term in query_terms}
    total = sum(weights.values()) or 1.0
    specific_df = max(1, int(len(texts) * SPECIFIC_DF_RATIO))

    scored = []
    for index, item in enumerate(doc_terms):
        hit = query_terms & item
        if not hit:
            continue
        score = sum(weights[term] for term in hit)
        if len(compact_query) >= 2 and compact_query in _compact(texts[index]):
            score += total * 0.5
        ratio = min(1.0, score / total)
        # 命中里有稀有词，说明这次匹配是有指向性的；只有泛词时要求更高覆盖
        specific = any(seen.get(term, 0) <= specific_df for term in hit)
        if ratio >= (MIN_RATIO if specific else MIN_RATIO_GENERIC):
            scored.append((index, ratio))
    scored.sort(key=lambda pair: -pair[1])
    return scored


def _snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _wants_recent(query: str) -> bool:
    return any(marker in str(query or "") for marker in RECENT_MARKERS)


def _wants_progress(query: str) -> bool:
    return any(marker in str(query or "") for marker in PROGRESS_MARKERS)


# ---------------------------------------------------------------- 三个来源

def search_memories(query: str, limit: int = 6) -> list[dict]:
    """在他主动存的记忆里找。条目最多 100 条，直接全扫，不打索引。"""
    from database import list_memories

    rows = list_memories(200)
    texts = ["%s %s" % (row[2], row[1]) for row in rows]
    hits = []
    for index, ratio in _rank(query, texts)[:limit]:
        row = rows[index]
        hits.append({
            "memory_id": row[0],
            "category": row[2],
            "content": str(row[1]),
            "source": row[4],
            "created_at": str(row[3] or "")[:10],
            "score": round(ratio, 2),
        })
    return hits


def search_conversations(query: str, limit: int = 4) -> list[dict]:
    """在历史问答里找。问句和回答都参与打分：他要找的常常是「我上次问过的那件事」。"""
    from database import list_runs_for_recall

    rows = list_runs_for_recall(1000)
    texts = ["%s %s" % (row[2] or "", row[3] or "") for row in rows]
    hits = [
        {
            "run_id": rows[index][0],
            "chat_id": rows[index][1],
            "question": _snippet(rows[index][2], 120),
            "reply": _snippet(rows[index][3]),
            "created_at": str(rows[index][4] or "")[:10],
            "score": round(ratio, 2),
        }
        for index, ratio in _rank(query, texts)
    ]
    # 同分时新的排前面：同一件事问过两次，他多半要的是最近那次。
    # list_runs 已经是时间倒序，用稳定排序再按分数排一次，同分保持时间序。
    hits.sort(key=lambda item: -item["score"])
    return hits[:limit]


def search_course(query: str, limit: int = 4) -> list[dict]:
    """在课程树上找「这章他学到什么程度」。课程文件缺失时安静返回空，不让检索整体失败。"""
    try:
        course = course_lib.decorate(course_lib.load_course())
    except Exception:
        return []
    chapters = course["chapters"]
    texts = [
        "第%d章 %s %s %s" % (
            chapter.get("no") or 0,
            chapter.get("title") or "",
            chapter.get("summary") or "",
            " ".join(chapter.get("objectives") or []),
        )
        for chapter in chapters
    ]
    hits = [
        _chapter_hit(chapters[index], ratio)
        for index, ratio in _rank(query, texts)[:limit]
    ]
    return hits


def _chapter_hit(chapter: dict, ratio: float = 0.0) -> dict:
    return {
        "chapter_id": chapter.get("id"),
        "no": chapter.get("no"),
        "title": chapter.get("title"),
        "status": chapter.get("status"),
        "mastery": chapter.get("mastery"),
        "notes": str(chapter.get("notes") or "")[:120],
        "score": round(ratio, 2),
    }


# ---------------------------------------------------------------- 没有实词时的退路

def recent_conversations(limit: int = 3) -> list[dict]:
    """最近问过的几件事。「上次我们聊到哪了」这种问题没有任何关键词可匹配。"""
    from database import list_runs_for_recall

    rows = list_runs_for_recall(limit)
    return [
        {
            "run_id": row[0],
            "chat_id": row[1],
            "question": _snippet(row[2], 120),
            "reply": _snippet(row[3]),
            "created_at": str(row[4] or "")[:10],
            "score": 0.0,
        }
        for row in rows
    ]


def course_progress_overview(limit: int = 4) -> list[dict]:
    """按最近动过的顺序列进度，再加一条「下一章」。

    用的是 updated_at 而不是章节号：他刚点开第 19 章又回头补第 7 章时，
    「学到哪了」的答案是第 7 章，不是第 19 章。
    """
    try:
        course = course_lib.decorate(course_lib.load_course())
    except Exception:
        return []
    from database import list_course_progress

    progress = list_course_progress(course_lib.DEFAULT_COURSE)
    by_id = {chapter["id"]: chapter for chapter in course["chapters"]}
    ordered = sorted(
        (entry for entry in progress.items() if entry[0] in by_id),
        key=lambda pair: str(pair[1].get("updated_at") or ""),
        reverse=True,
    )
    hits = [_chapter_hit(by_id[chapter_id]) for chapter_id, _entry in ordered[:limit]]
    nxt = course.get("next")
    if nxt and all(hit["chapter_id"] != nxt["id"] for hit in hits):
        hits.append(_chapter_hit(nxt))
    return hits


# ---------------------------------------------------------------- 汇总

def recall(query: str, scope: str = "all", limit: int = 6) -> dict:
    """一次检索，返回结构化命中 + 排好版的文本。"""
    scope = scope if scope in SCOPES else "all"
    query = str(query or "").strip()
    result = {
        "query": query, "scope": scope,
        "memory": [], "conversation": [], "course": [],
        "conversation_fallback": False, "course_fallback": False,
    }
    if scope in ("all", "memory"):
        result["memory"] = search_memories(query, limit)
    if scope in ("all", "conversation"):
        result["conversation"] = search_conversations(query, max(2, limit // 2 + 1))
        if not result["conversation"] and _wants_recent(query):
            result["conversation"] = recent_conversations(3)
            result["conversation_fallback"] = bool(result["conversation"])
    if scope in ("all", "course"):
        result["course"] = search_course(query, 3)
        if not result["course"] and _wants_progress(query):
            result["course"] = course_progress_overview(4)
            result["course_fallback"] = bool(result["course"])
    result["total"] = len(result["memory"]) + len(result["conversation"]) + len(result["course"])
    result["text"] = format_hits(result)
    return result


def format_hits(result: dict) -> str:
    """排成模型好读的样子。每条都带编号/章节号，方便它回答时说清来源。"""
    blocks = []

    if result["memory"]:
        lines = ["【长期记忆 · %d 条】" % len(result["memory"])]
        for index, item in enumerate(result["memory"], 1):
            lines.append(
                "%d. [编号 %d · %s · %s] %s"
                % (index, item["memory_id"], item["category"] or "记忆",
                   SOURCE_LABELS.get(item["source"], "已保存"), item["content"])
            )
        blocks.append("\n".join(lines))

    if result["conversation"]:
        title = "【最近问过的 · %d 条】（没匹配到具体主题，按时间取最近的）" % len(result["conversation"]) \
            if result.get("conversation_fallback") else "【历史对话 · %d 条】" % len(result["conversation"])
        lines = [title]
        for index, item in enumerate(result["conversation"], 1):
            lines.append(
                "%d. （%s，对话 %s）你问：%s\n   我当时答：%s"
                % (index, item["created_at"] or "日期不明", item["chat_id"],
                   item["question"], item["reply"])
            )
        blocks.append("\n".join(lines))

    if result["course"]:
        title = "【课程进度 · 按最近动过的排】" if result.get("course_fallback") \
            else "【课程进度 · %d 章】" % len(result["course"])
        lines = [title]
        for item in result["course"]:
            line = "- 第 %s 章《%s》：%s" % (
                item["no"], item["title"], STATUS_LABELS.get(item["status"], "还没开始"))
            if item.get("mastery"):
                line += "（掌握度 %d/3）" % item["mastery"]
            if item["notes"]:
                line += "；他留的笔记：%s" % item["notes"]
            lines.append(line)
        blocks.append("\n".join(lines))

    if not blocks:
        return ("没有检索到相关内容（已查：长期记忆 %d 条、历史问答、课程进度）。"
                "这说明用户确实没记过这件事，回答时要说明「我这边没有这条记录」，"
                "不要凭印象补出一段回忆。"
                % _memory_total())
    header = "以下是按「%s」检索到的历史信息（是过去的记录，不是当前事实证据）：" % result["query"]
    footer = "只把上面出现过的内容当作「用户以前说过/记过」；没出现在这里的就是没记过。"
    return header + "\n" + "\n\n".join(blocks) + "\n" + footer


def _memory_total() -> int:
    try:
        from database import count_memories
        return count_memories()
    except Exception:
        return 0
