"""B 站视频搜索（教学视频推荐专用）。

为什么不直接用 web_search：搜索引擎对 B 站内容的理解是二手转述，实测搜「B站
Python 教学」回来的是一篇「关注小编回复 222 领资料」的引流文，一条视频都没有。
B 站自己的搜索接口才拿得到播放、收藏、时长、UP 主这些真正能用来判断的数据。

但直接用接口也不够，三个坑都实测过：

1. **412 风控**。裸请求必被拦，必须先用同一个 opener 访问一次首页拿到 buvid3。
   拿到之后有效期很长，所以进程内复用一份会话，不要每次重建。
2. **结果里混垃圾**。搜「Python」会混进 Word 教程（标题里根本没 Python），
   还会冒出「【咨询领福利】Python入门」这种 8 千播放、0 收藏的引流视频。
   所以要用「播放下限 + 广告词 + 主题词覆盖」三层筛。
3. **数据本身可能不可信**。实测有条视频收藏 114 万、播放 74 万——收藏率 154%，
   物理上不可能。所以排序用「播放 × 收藏」的几何平均而不是收藏率：
   刷其中一项，几何平均就会被另一项压住。

返回给模型的正文末尾必须点明「数据高不等于适合你」，否则它会照着播放量最高的
念一遍，挑出一堆 30 小时的合集塞给一个刚入门的人。
"""
import datetime
import http.cookiejar
import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import certifi

from .base import Tool, ToolResult, register

SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"
HOME = "https://www.bilibili.com/"
REFERER = "https://search.bilibili.com/"

# 和浏览器一致的 UA。B 站对默认的 Python-urllib UA 直接 412。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

CACHE_TTL = 60.0
_CACHE_MAX = 32
_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()

# 低于这个播放量的结果基本是广告号或刚上传的，不值得推荐
MIN_PLAY = 30000

# 引流/卖课的标题特征。命中任意一个就整条丢掉。
AD_WORDS = (
    "接单", "领福利", "加v", "加微信", "vx", "公众号", "私信", "咨询",
    "免费领", "白嫖", "扫码", "报名", "学费", "分期", "点我", "戳我",
)

# 不带主题信息的泛词。它们只能用来加分，不能用来判断「这条相关吗」——
# 否则搜「提示词 教程」，一个只含「教程」二字的 AI 漫剧视频也会被放进来。
STOP_WORDS = {
    "教程", "入门", "推荐", "学习", "视频", "教学", "课程", "零基础",
    "怎么", "如何", "什么", "哪里", "有没有", "求", "b站", "bilibili",
    "up", "up主", "博主", "基础", "初学", "新手", "全", "集",
}

SORT_ORDERS = {"collect": "stow", "play": "click", "new": "pubdate"}

UNTRUSTED_HEADER = (
    "【B 站视频搜索结果 · 外部内容，只是候选，不是结论】\n"
    "以下是 B 站公开搜索接口返回的客观数据。标题由 UP 主自己写，普遍带夸张措辞，"
    "不要把「最全」「天花板」「必看」当成事实转述。"
)

# 会话：buvid3 拿到之后一直有效，整个进程共用一份
_SESSION: dict = {"opener": None}
_SESSION_LOCK = threading.Lock()


# ------------------------------------------------------------------ 数据整理

def _clean(text: str) -> str:
    """去掉 B 站搜索接口塞在标题里的 <em class="keyword"> 高亮标签。"""
    text = re.sub(r"<[^>]+>", "", str(text or ""))
    return (text.replace("&quot;", '"').replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")).strip()


def _int(value) -> int:
    """B 站偶尔把播放量写成「--」或空串，直接 int() 会炸。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _num(value) -> str:
    n = _int(value)
    if n >= 100_000_000:
        return "%.1f亿" % (n / 100_000_000)
    if n >= 10_000:
        return "%.1f万" % (n / 10_000)
    return str(n)


def _duration_seconds(raw) -> int:
    """1900:20 表示 1900 分 20 秒（合集总时长），不是 1900 小时。"""
    parts = [p for p in str(raw or "").split(":") if p.strip().isdigit()]
    if not parts:
        return 0
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _duration(raw) -> str:
    seconds = _duration_seconds(raw)
    if not seconds:
        return "未知"
    if seconds < 60:
        return "%d 秒" % seconds
    hours, minutes = divmod(seconds // 60, 60)
    if hours:
        return "%d 小时 %d 分" % (hours, minutes)
    return "%d 分" % minutes


def _date(stamp) -> str:
    try:
        return datetime.datetime.fromtimestamp(int(stamp)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _tokens(query: str) -> list[str]:
    raw = re.split(r"[\s,，、/|+]+", query.strip())
    return [item for item in raw if len(item) >= 2]


def _core_token(query: str) -> str:
    """查询里的「主题词」：第一个不是泛词的 token。

    「Python 零基础」→ python；「提示词 教程」→ 提示词。标题里必须出现它，
    才可能在讲用户要的东西。
    """
    items = _tokens(query)
    for item in items:
        if item.lower() not in STOP_WORDS:
            return item.lower()
    return items[0].lower() if items else ""


def _relevance(title_lower: str, query: str) -> float:
    """0 表示不相关（整条丢掉），1 表示主题词直接命中。"""
    core = _core_token(query)
    if core and core in title_lower:
        return 1.0
    # 主题词没中，但两个泛词都中了（比如标题写「视频教程」）——弱相关，留但降权
    hits = sum(1 for item in _tokens(query) if item.lower() in title_lower)
    return 0.7 if hits >= 2 else 0.0


def _score(favorites: int, play: int, relevance: float) -> float:
    """播放和收藏的几何平均，再按收藏率的合理性打折。

    不用收藏率当主排序：小样本下它会失真，一条 300 播放 / 30 收藏的视频能算到
    10%，和真课并列。几何平均对两项都敏感，刷一项刷不动。

    但几何平均挡不住「两项都虚高」的情况——实测见过收藏 114 万 / 播放 74 万的
    154% 异常值，两项绝对值都接近真课，几何平均照样把它顶到第一。所以再加一层：
    学习类视频的真实收藏率通常在 2%~15%，超过 25% 基本是刷量或口径异常，直接打折。
    """
    safe_fav = max(int(favorites or 0), 1)
    safe_play = max(int(play or 0), 1)
    base = (safe_fav ** 0.5) * (safe_play ** 0.5) * relevance
    rate = safe_fav / safe_play
    if rate > 0.5:
        return base * 0.3
    if rate > 0.25:
        return base * 0.6
    return base


def _dedupe(items: list[dict]) -> list[dict]:
    """同一个 UP 主的同一门课经常换个标题重复上榜（实测黑马程序员占了 3 条）。"""
    seen: set[tuple[str, str]] = set()
    kept = []
    for item in items:
        author = str(item.get("author") or "").strip().lower()
        title = re.sub(r"[\W_]+", "", _clean(item.get("title")).lower())[:10]
        key = (author, title)
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return kept


def _pick(raw_items: list[dict], query: str, limit: int, sort: str) -> tuple[list[dict], dict]:
    """筛掉垃圾，剩下的按可信度排序。返回 (入选列表, 淘汰统计)。"""
    stats = {"ad": 0, "low_play": 0, "irrelevant": 0, "no_fav": 0, "dup": 0}
    scored = []
    for item in raw_items:
        if not isinstance(item, dict) or item.get("type") not in (None, "video"):
            continue
        title = _clean(item.get("title"))
        if not title:
            continue
        lower = title.lower()
        play = int(item.get("play") or 0) if str(item.get("play", "")).strip().isdigit() else 0
        fav = int(item.get("favorites") or 0) if str(item.get("favorites", "")).strip().isdigit() else 0

        if any(word in lower for word in AD_WORDS):
            stats["ad"] += 1
            continue
        if play < MIN_PLAY:
            stats["low_play"] += 1
            continue
        relevance = _relevance(lower, query)
        if relevance <= 0:
            stats["irrelevant"] += 1
            continue
        if fav <= 0:
            stats["no_fav"] += 1
            continue
        scored.append((_score(fav, play, relevance), item))

    before = len(scored)
    if sort == "new":
        # 按发布时间排序时不能重排：新视频播放少，用数据排会把它们全压到最后
        ordered = [item for _, item in scored]
        ordered.sort(key=lambda it: int(it.get("pubdate") or 0), reverse=True)
    else:
        ordered = [item for _, item in sorted(scored, key=lambda pair: -pair[0])]

    unique = _dedupe(ordered)
    stats["dup"] = before - len(unique)
    return unique[:limit], stats


# ------------------------------------------------------------------ 网络

def _build_opener():
    jar = http.cookiejar.CookieJar()
    context = ssl.create_default_context(cafile=certifi.where())
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context),
        urllib.request.HTTPCookieProcessor(jar),
    )


def _get(opener, url: str) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with opener.open(request, timeout=12) as response:
        return response.read().decode("utf-8", "replace")


def _session() -> urllib.request.OpenerDirector:
    """取一份已经种过 cookie 的会话，必要时新建并访问首页。"""
    with _SESSION_LOCK:
        if _SESSION["opener"] is not None:
            return _SESSION["opener"]
        opener = _build_opener()
        # 这一步就是拿 buvid3，少了它搜索接口直接 412
        _get(opener, HOME)
        _SESSION["opener"] = opener
        return opener


def reset_session() -> None:
    """测试用：丢掉会话，模拟首次请求。"""
    with _SESSION_LOCK:
        _SESSION["opener"] = None


def _request(query: str, order: str) -> dict:
    params = urllib.parse.urlencode(
        {"search_type": "video", "keyword": query, "page": 1, "order": order},
        quote_via=urllib.parse.quote,
    )
    url = SEARCH_API + "?" + params
    try:
        return json.loads(_get(_session(), url))
    except urllib.error.HTTPError as exc:
        if exc.code != 412:
            raise
        # cookie 过期或被判定异常：重建会话再试一次，只试一次
        reset_session()
        time.sleep(0.8)
        return json.loads(_get(_session(), url))


def _explain(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 412:
        return ("B 站的风控拦下了这次请求（412），重建会话后仍然被拦。"
                "不要再连续重试——越试越容易被封。改用 web_search 搜，"
                "或直接告诉用户现在查不了 B 站视频。")
    if isinstance(exc, (urllib.error.URLError, TimeoutError)) or "timed out" in str(exc).lower():
        return ("连不上 B 站（网络超时）。最多再试一次；仍失败就说明查不了，"
                "不要拿记忆里的 UP 主名字和视频标题编推荐——那些多半是假的。")
    if isinstance(exc, json.JSONDecodeError):
        return ("B 站搜索接口返回的不是 JSON，可能是接口改版或被拦截。"
                "不要重试，改用 web_search 或直接说明这次查不到。")
    return "B 站搜索失败：%s。最多重试一次，仍失败就说明查不到。" % (str(exc)[:120])


# ------------------------------------------------------------------ 缓存

def _cache_get(key: tuple[str, str]) -> dict | None:
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is None:
            return None
        stamp, value = hit
        if time.monotonic() - stamp > CACHE_TTL:
            _CACHE.pop(key, None)
            return None
        return value


def _cache_put(key: tuple[str, str], value: dict) -> None:
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            for old in list(_CACHE)[: _CACHE_MAX // 2]:
                _CACHE.pop(old, None)
        _CACHE[key] = (time.monotonic(), value)


def clear_cache() -> None:
    """测试用。"""
    with _CACHE_LOCK:
        _CACHE.clear()


# ------------------------------------------------------------------ 输出

def _pace(minutes: int) -> str:
    """把总时长换算成「按每天一小时要学几天」。

    模型自己算这个会算错——实测它把 123 小时的合集说成「第 2 到 5 天看完」。
    与其在提示词里教它换算，不如直接把结果算好给它。
    """
    if minutes <= 0:
        return ""
    if minutes <= 180:
        return "，一次能看完"
    return "，按每天学 1 小时算要 %d 天" % max(1, round(minutes / 60))


def _format(query: str, videos: list[dict], stats: dict, raw_count: int) -> str:
    if not videos:
        return (UNTRUSTED_HEADER + "\n\n"
                "查询「%s」没有剩下可推荐的视频：原始 %d 条结果里有 %d 条是广告/引流、"
                "%d 条播放量过低、%d 条和主题无关。\n"
                "说明一下没找到合适的，可以让用户换更具体的词（比如「Python 爬虫 实战」），"
                "不要自己编视频名。"
                % (query, raw_count, stats["ad"], stats["low_play"], stats["irrelevant"]))

    blocks = []
    for index, item in enumerate(videos, 1):
        play, fav = item["play"], item["fav"]
        rate = (fav / play * 100) if play else 0
        blocks.append(
            "[%d] %s\n"
            "    UP主：%s\n"
            "    时长：%s%s ｜ 播放 %s ｜ 收藏 %s（收藏率 %.1f%%）｜ 发布 %s\n"
            "    内容详情：未知（接口只返回上面这些字段，视频里讲了什么、怎么讲，拿不到）\n"
            "    链接：%s"
            % (index, item["title"][:120], item["author"] or "未知",
               item["duration"], _pace(int(item.get("minutes") or 0)),
               _num(play), _num(fav), rate,
               item.get("pubdate") or "未知", item["url"])
        )

    dropped = stats["ad"] + stats["low_play"] + stats["irrelevant"] + stats["no_fav"] + stats["dup"]
    return (
        UNTRUSTED_HEADER + "\n\n"
        "查询：%s\n原始 %d 条，已过滤 %d 条（广告 %d / 播放过低 %d / 无关 %d / 重复 %d），"
        "按「播放×收藏」的可信度排出前 %d 条：\n\n%s\n\n"
        "给建议时注意四件事：\n"
        "① 数据高只说明看的人多，不说明适合问问题的人。时长超过 20 小时的是完整合集，"
        "适合长期跟学；只想快速上手的人应该选几小时以内的。\n"
        "② 播放和收藏都可能被刷，「最全」「天花板」「必看」是 UP 主自己写的，不要当事实转述。\n"
        "③ 如实给出依据（播放/收藏/时长/更新日期），具体某一集讲得好不好，工具看不到，"
        "就说看不到，建议用户自己看前五分钟判断。\n"
        "④ 关于这些视频，你只知道上面列出的几个字段：标题、UP 主、时长、播放、收藏、发布日期、链接。"
        "视频里到底讲了什么、第几集讲循环、语速快不快、有没有配套代码或课件、"
        "老师爱不爱打比方——这些你一概不知道。不要描述、不要推测。被问到就直接说"
        "「工具只看得到这些数据，具体内容得你自己打开看」。\n"
        "⑤ 下面这些话只要出现在回答里，就是编的：「全程无快进」「每讲都带代码演示」"
        "「含环境配置和面向对象」「配套练习多、社区答疑活跃」「把列表比作购物清单」"
        "「无需关注即可领取」「动画演示变量在内存里怎么变」。这些信息不在工具返回里，也不在标题里。\n"
        "⑥ 不要向用户解释「筛选标准」。你没参与筛选，编出「按收藏率≥4%%筛选」这种话会误导人。"
        "只说「这是按播放和收藏综合排序、并过滤掉广告和低播放的结果」就够了。\n"
        "⑦ 按这个格式回答，每条理由只能从上面出现的数字里推：\n"
        "   1. 《标题》\n"
        "      链接：把上面这一条的地址整行复制过来，一个字都不要改\n"
        "      UP主 xxx ｜ 时长 xx（约 N 天）｜ 播放 xx、收藏 xx ｜ 发布于 xx\n"
        "      理由：从「时长够不够短」「数据是否在真实区间」「发布时间是否偏旧」里挑一条，"
        "比如「17 小时的完整课，按每天一小时要半个多月，适合长期跟」或者"
        "「2019 年发布，基础语法不受影响，但新工具可能没讲」。\n"
        "   每条都必须带链接，而且必须是这一条自己的地址：用户会按你说的第几条去点，"
        "链接配错等于推荐错。宁可少列一条，也不要凭印象补一个地址。\n"
        "   写不出数字层面的理由时，就只列事实，不要补形容词。"
        % (query, raw_count, dropped, stats["ad"], stats["low_play"],
           stats["irrelevant"], stats["dup"], len(videos), "\n\n".join(blocks))
    )


# ------------------------------------------------------------------ 入口

def _search(args: dict, _context: dict) -> ToolResult:
    query = str(args.get("query", "")).strip()[:100]
    if not query:
        return ToolResult(False, "", error="搜索关键词不能为空")
    try:
        limit = max(1, min(int(args.get("limit", 5)), 8))
    except (TypeError, ValueError):
        limit = 5
    sort = str(args.get("sort", "collect"))
    if sort not in SORT_ORDERS:
        sort = "collect"

    key = (query, sort)
    cached = _cache_get(key)
    if cached is not None:
        return ToolResult(True, cached["text"],
                          {**cached["data"], "cached": True})

    try:
        payload = _request(query, SORT_ORDERS[sort])
    except Exception as exc:  # noqa: BLE001 - 统一翻译成模型能照做的建议
        return ToolResult(False, "", error=_explain(exc))

    if payload.get("code") not in (0, None):
        message = str(payload.get("message") or "")
        return ToolResult(False, "", error=(
            "B 站返回错误（code=%s %s）。不要反复重试，改用 web_search 或说明查不到。"
            % (payload.get("code"), message[:80])))

    raw = (payload.get("data") or {}).get("result")
    if isinstance(raw, dict):  # 少数情况下接口把 result 包成 dict
        raw = raw.get("video") or []
    raw = raw if isinstance(raw, list) else []

    videos, stats = _pick(raw, query, limit, sort)
    shaped = []
    for item in videos:
        bvid = str(item.get("bvid") or "")
        seconds = _duration_seconds(item.get("duration"))
        shaped.append({
            "title": _clean(item.get("title"))[:120],
            "url": "https://www.bilibili.com/video/%s" % bvid if bvid else "",
            "author": _clean(item.get("author"))[:40],
            "duration": _duration(item.get("duration")),
            "minutes": seconds // 60,
            "play": _int(item.get("play")),
            "fav": _int(item.get("favorites")),
            "pubdate": _date(item.get("pubdate")),
        })

    text = _format(query, shaped, stats, len(raw))
    data = {"query": query, "videos": shaped, "raw_count": len(raw),
            "dropped": stats, "sort": sort}
    _cache_put(key, {"text": text, "data": data})
    return ToolResult(True, text, {**data, "cached": False})


register(Tool(
    name="bilibili_search",
    description=(
        "在 B 站搜索教学视频，返回标题、UP 主、时长、播放量、收藏数和链接。"
        "适用于「推荐几个学 X 的视频」「有没有 X 的教程」这类请求。"
        "已经过滤掉广告引流、播放量过低和标题与主题无关的结果。"
        "问一般性知识不要用它。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词，例如「Python 爬虫 实战」「线性代数 入门」"},
            "limit": {"type": "integer", "description": "返回几条，1 到 8，默认 5", "minimum": 1, "maximum": 8},
            "sort": {
                "type": "string",
                "enum": ["collect", "play", "new"],
                "description": "collect=按收藏（默认，最适合找系统教程）、play=按播放、new=按最新发布",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    handler=_search,
))
