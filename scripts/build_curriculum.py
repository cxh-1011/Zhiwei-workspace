"""生成 agent 开发课程的知识树与章节正文。一次性工具，不进 agent 工具集。

主线（10 个阶段）写死在这个文件里，模型只负责填每章的具体内容——结构交给
模型发明，第一次和第二次生成出来的目录就对不上，人工校对的成本会比手写还高。

用法（在仓库根目录，Git Bash）：
    ../.venv/Scripts/python.exe scripts/build_curriculum.py --tree
    ../.venv/Scripts/python.exe scripts/build_curriculum.py --chapter s1c1
    ../.venv/Scripts/python.exe scripts/build_curriculum.py --all

幂等规则：已存在的文件默认跳过，只有加 --force 才覆盖。这是「AI 生成、
人工校」能成立的唯一前提——你改过的章节不会被下一次生成冲掉。
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from llm import chat_completion  # noqa: E402

COURSE_DIR = ROOT / "curriculum" / "agent-dev"
CHAPTER_DIR = COURSE_DIR / "chapters"

COURSE_META = {
    "id": "agent-dev",
    "title": "Agent 开发：从调通第一个接口到让它自己干活",
    "subtitle": "一条按依赖顺序排好的主线，每章都有能跑的代码和能验收的练习",
    "level": "需要有 Python 基础，不要求做过大模型",
    "audience": "想自己做 agent、但不满足于调包的研究生与工程师",
}

# 阶段标题、定位，以及**这一阶段必须覆盖的知识点**。
# 只给「阶段名」时模型会跑偏：第一次生成的 s1（地基）里出现了 prompt 拆解和
# JSON Schema——那都是 s2 的内容，同时漏掉了「token 和上下文窗口怎么算」。
# 把知识点写死之后，模型的自由只留在措辞和章标题上。
STAGES = [
    ("s1", "地基", "模型到底在做什么，一次请求里都有什么", [
        "模型本质上只在预测下一个 token，为什么看起来像在思考",
        "一次请求里你在控制什么：system/user 消息、temperature、max_tokens 的实际效果",
        "上下文窗口与 token：怎么估算、为什么长对话会忘事",
    ]),
    ("s2", "提示词", "把话说到位：结构化、少样本、强制输出格式", [
        "四段式写提示词：角色、任务、约束、输出格式",
        "少样本示例什么时候有效、什么时候反而带偏",
        "强制结构化输出：JSON 模式与解析失败后的兜底",
    ]),
    ("s3", "工具调用", "让它伸手：function calling 的原理与 schema 设计", [
        "function calling 的真相：模型不执行代码，它只输出「我想调这个」",
        "写好一个工具 schema：描述怎么写模型才会用对、参数怎么定",
        "工具报错之后：把错误信息喂回模型，让它自己修",
        "工具一多就选错：怎么让模型在二十个工具里找到该用的那个",
    ]),
    ("s4", "Agent 循环", "让它自己转：ReAct、多轮、终止条件与预算", [
        "ReAct 循环：想一步、做一步、看结果",
        "什么时候停：终止条件、防死循环",
        "轮次与预算：token、时间、次数上限怎么夹",
        "一次运行的完整状态机：从提问到交付中间都发生了什么",
    ]),
    ("s5", "记忆", "让它记得住：上下文裁剪、长期记忆、写入门控", [
        "上下文裁剪：长对话里保留什么、丢掉什么",
        "长期记忆：写什么进去、什么时候读出来",
        "写入门控：为什么不能让模型自己决定记什么",
    ]),
    ("s6", "检索增强", "让它查得到：切片、向量、召回、重排", [
        "切片：为什么按固定字数切会把一句话切断",
        "向量与召回：embedding 是什么、相似度怎么算",
        "召回太多怎么办：重排与过滤",
        "RAG 的典型失败：答得不相关，或者答得很自信但错",
    ]),
    ("s7", "工程化", "让它不出事：流式、中断、审批、可观测", [
        "流式输出：为什么要流、分片怎么拼回完整回答",
        "中断与取消：用户点停之后后台到底发生了什么",
        "危险动作的审批：哪些要拦、拦住之后怎么继续",
        "可观测：步骤日志、耗时、出了问题怎么回放",
    ]),
    ("s8", "评测", "让它可证明：造数据集、判分、回归", [
        "造数据集：从真实失败里攒题，而不是凭空编",
        "判分：规则判分与模型判分各自什么时候用",
        "回归：改完提示词，怎么知道没有把别的能力改坏",
    ]),
    ("s9", "进阶", "多智能体与当前边界", [
        "多智能体：什么时候真的需要，什么时候是过度设计",
        "协作的代价：通信开销、互相冲突、成本翻倍",
        "当前边界：哪些事今天还做不到，别对着做不到的事调提示词",
    ]),
    ("s10", "收尾", "做成产品：部署、成本、迭代", [
        "部署与成本：一次对话到底花多少钱",
        "迭代：从用户反馈走到下一版",
    ]),
]

STAGE_JSON_RULE = (
    "你是一位给初学者写教程的工程师，写的是《Agent 开发》这门课。"
    "语言必须是大白话，像给同门师弟讲东西：不要学术腔，不要「解析……协同触发……推理」"
    "这种论文摘要式的句子，不要用「解耦」「范式」「赋能」。"
    "只返回 JSON，不要 Markdown 代码块、不要解释。"
    "JSON 格式："
    '{"chapters":[{"title":"章节标题（不超过 18 字，要具体，不要写「概述」「简介」）",'
    '"summary":"这一章解决什么问题，用大白话说，不超过 40 字",'
    '"objectives":["学完能做出的具体动作，2 到 3 条，每条不超过 25 字，要能验收"],'
    '"minutes":40,"practice":"动手练习最后要做出什么，不超过 60 字"}]}'
)


def _extract_json(text: str):
    """模型常把 JSON 包在代码块里，或者前后带一句客套话。"""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    candidates = [fenced.group(1).strip()] if fenced else []
    candidates.append(raw)
    # 兜底：从第一个 { 到最后一个 }
    start, end = raw.find("{"), raw.rfind("}")
    if 0 <= start < end:
        candidates.append(raw[start:end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _ask(messages, label: str, retries: int = 2) -> str:
    last = None
    for attempt in range(retries + 1):
        try:
            response, _ = chat_completion(messages, tools=None, timeout=120)
            content = (getattr(response, "content", None) or "").strip()
            if content:
                return content
            last = RuntimeError("模型返回空内容")
        except Exception as exc:  # 网络抖动、限流都重试
            last = exc
        print("    %s 第 %d 次失败：%s" % (label, attempt + 1, last))
    raise RuntimeError("%s 生成失败：%s" % (label, last))


def build_tree(force: bool = False, only_stage: str | None = None):
    course_path = COURSE_DIR / "course.json"
    existing = {}
    if course_path.exists() and not force:
        try:
            data = json.loads(course_path.read_text(encoding="utf-8"))
            existing = {ch["id"]: ch for ch in data.get("chapters", [])}
            print("已有 %d 章，保留不动（如需重建加 --force）" % len(existing))
        except Exception:
            existing = {}

    chapters = []
    for stage_id, stage_title, stage_desc, topics in STAGES:
        count = len(topics)
        if only_stage and stage_id != only_stage:
            chapters.extend(existing.get("%sc%d" % (stage_id, i), {}) for i in range(1, count + 1))
            chapters = [c for c in chapters if c]
            continue

        # 该阶段的章节若都已存在，直接复用，不浪费一次调用
        present = [existing.get("%sc%d" % (stage_id, i)) for i in range(1, count + 1)]
        if all(present) and not force:
            print("[%s] %s：已有 %d 章，跳过" % (stage_id, stage_title, count))
            chapters.extend(present)
            continue

        print("[%s] %s：生成 %d 章…" % (stage_id, stage_title, count))
        # 把整门课的阶段列表一起给出：只说「不要涉及其他阶段」，模型并不知道
        # 其他阶段是什么，第一次生成时 s1 里就混进了 s2 的提示词和 JSON 输出。
        outline = "\n".join(
            "  %s %s —— %s" % (sid, title, desc) for sid, title, desc, _ in STAGES
        )
        prompt = [
            {"role": "system", "content": STAGE_JSON_RULE},
            {"role": "user", "content": (
                "整门课《%s》的阶段划分如下（顺序即学习顺序）：\n%s\n\n"
                "现在只写「%s（%s）」这一阶段，正好 %d 章，一章对应下面一个知识点，顺序不能变：\n%s\n\n"
                "严格要求：\n"
                "1. 不要写这一阶段之外的知识点，尤其不要把后面阶段的东西提前讲；\n"
                "2. 章节标题要说人话，读者一看就知道这章讲的什么事；\n"
                "3. 每章的 objectives 必须是「能做出什么」，不是「理解什么」；\n"
                "4. 这些知识点是骨架，可以微调措辞，但不要替换成你认为更好的主题。"
                % (COURSE_META["title"], outline, stage_title, stage_desc, count,
                   "\n".join("  %d. %s" % (i, t) for i, t in enumerate(topics, 1)))
            )},
        ]
        payload = None
        for attempt in range(3):
            text = _ask(prompt, "%s 目录" % stage_id)
            payload = _extract_json(text)
            if payload and isinstance(payload.get("chapters"), list):
                break
            print("    返回的不是合法 JSON，重试")
            payload = None
        if not payload:
            raise RuntimeError("[%s] 连续三次没拿到合法 JSON" % stage_id)

        items = [c for c in payload["chapters"] if isinstance(c, dict) and c.get("title")][:count]
        for index, item in enumerate(items, 1):
            chapter = {
                "id": "%sc%d" % (stage_id, index),
                "no": None,  # 排序后统一编号
                "stage": stage_id,
                "stage_title": stage_title,
                "title": str(item.get("title", "")).strip()[:40],
                "summary": str(item.get("summary", "")).strip()[:100],
                "objectives": [str(o).strip()[:50] for o in (item.get("objectives") or [])][:3],
                "minutes": int(item.get("minutes") or 40),
                # 这里原来截到 80 字，把「输出 mes」这种半句话写进了课程树。
                "practice": str(item.get("practice", "")).strip()[:160],
                "prereq": ["%sc%d" % (stage_id, index - 1)] if index > 1 else [],
                "path": "chapters/%sc%d.md" % (stage_id, index),
            }
            chapters.append(chapter)

    # 跨阶段依赖：每个阶段第一章依赖上一阶段最后一章
    by_stage = {}
    for chapter in chapters:
        by_stage.setdefault(chapter["stage"], []).append(chapter)
    stage_order = [s[0] for s in STAGES]
    for position, stage_id in enumerate(stage_order):
        if position == 0:
            continue
        prev_stage = stage_order[position - 1]
        prev_chapters = sorted(by_stage.get(prev_stage, []), key=lambda c: c["id"])
        current = sorted(by_stage.get(stage_id, []), key=lambda c: c["id"])
        if prev_chapters and current:
            current[0]["prereq"] = [prev_chapters[-1]["id"]]

    for number, chapter in enumerate(chapters, 1):
        chapter["no"] = number

    payload = dict(COURSE_META)
    payload["stages"] = [
        {"id": sid, "title": title, "desc": desc,
         "chapters": [c["id"] for c in chapters if c["stage"] == sid]}
        for sid, title, desc, _topics in STAGES
    ]
    payload["chapters"] = chapters

    COURSE_DIR.mkdir(parents=True, exist_ok=True)
    course_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("已写入 %s（%d 章）" % (course_path, len(chapters)))
    return payload


CHAPTER_SYSTEM = """你是给初学者写教材的工程师，写的是《Agent 开发》这门课的其中一章。

## 语气：像给同门师弟讲东西

不要学术腔，不要教材腔。下面是可以直接照做的对照：

- ❌「本章破除『模型在思考』的错觉，建立起 token 级别确定性链条的直觉」
- ✅「这一章要把这个错觉拆掉。它每一次只做一件事：看上文，猜下一个字。」
- ❌「通过解耦请求参数与生成结果的映射关系，可以观察到……」
- ✅「你把 temperature 调高，它就更敢乱猜。」
- ❌「本节将系统阐述……」「综上所述」「值得注意的是」
- ✅ 直接说事。句子短一点，一段不超过四行。

## 准确性：这一条比语气重要

写错的知识点会教坏人，而且读者没法自己发现。三个必须避开的坑：

1. **不许编具体数字**。不要写「token 编号是 1234」「大约 3.7 倍」这种看似精确的数字，
   除非它来自你可以确定的定义。宁可写「几个」「大概一倍」，或者让读者自己跑出来。
2. **代码必须和它上面那句话说的是同一件事**。标题写「看 tokenizer 怎么切分」，
   代码就得真的调 tokenizer，不能跑去调 chat 接口。
3. **不许编 API**。只用 openai>=1.0 真实存在的参数（messages / temperature /
   max_tokens / tools / logprobs / top_logprobs）。不确定的参数一个都不要写；
   接口可能不支持的功能，要写明「如果报错就跳过」。

## 结构：固定的六节，顺序不能变

用 Markdown 写，从 `## ` 二级标题开始，不要写一级标题（标题程序会补）。

`## 这一章要解决什么` —— 用一个他真会遇到的情景开场，不要从定义开始。
`## 核心概念` —— 最多 3 个，每个讲清「为什么需要它」，不要罗列名词。可以用 `###` 分小节。
`## 先跑起来` —— 一段完整可运行的 Python，用 openai>=1.0 写法，密钥从
`os.environ` 读，不要写死。代码要短，能直接复制粘贴跑。跑完要解释你会看到什么。
`## 动手练` —— 3 题，由易到难。每题写「做什么」和「怎么算做完了」，
判据必须是看得见的东西（输出、文件、数字），不能是「理解」「体会」。
`## 自测` —— 3 道题，每题下面用 `<details><summary>参考答案</summary>` 包住答案。
不要三道全是判断题，至少一题问「为什么」，答案要能被判对错。
`## 下一章为什么是它` —— 两三句话，说清依赖关系：这一章留下的问题，下一章怎么接。

全文 1200 到 1800 字（代码不计）。中文。写完不要加「希望对你有帮助」这类结尾。"""


STYLE_SAMPLE = """这是已经定稿的第 1 章开头，作为语气参照（内容不要照抄，只看它怎么说话）：

## 这一章要解决什么

你让它改一段代码，它改得能跑；你问它原理，它讲得头头是道。很容易觉得它"懂了"。

这一章要把这个错觉拆掉。它每一次只做一件事：**看上文，猜下一个字**。猜完接上去，再猜下一个。

把这件小事想透，后面所有事都顺了——为什么提示词换个说法效果差很多，为什么它会把不存在的函数名编得像真的，为什么聊久了它会忘事。
"""


def build_chapter(chapter: dict, course_title: str, force: bool = False):
    path = COURSE_DIR / chapter["path"]
    if path.exists() and not force:
        print("  %s 已存在，跳过" % chapter["id"])
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    prompt = [
        {"role": "system", "content": CHAPTER_SYSTEM},
        {"role": "user", "content": (
            "课程：《%s》\n本章：第 %d 章《%s》\n"
            "本章要解决的问题：%s\n"
            "学完应该能做到：%s\n"
            "动手练习的目标：%s\n\n"
            "补充：读者有 Python 基础，但没做过大模型应用。"
            "举例尽量用「让模型帮你改代码」「让模型查资料」这类他真会遇到的场景。\n\n"
            % (course_title, chapter["no"], chapter["title"], chapter["summary"],
               "；".join(chapter.get("objectives") or []) or "无",
               chapter.get("practice") or "无")
        ) + STYLE_SAMPLE},
    ]
    body = _ask(prompt, chapter["id"])
    body = re.sub(r"^#\s+.*\n+", "", body)  # 模型偶尔自己加一级标题
    header = "---\nid: %s\ntitle: %s\n---\n\n# 第 %d 章 %s\n\n" % (
        chapter["id"], chapter["title"], chapter["no"], chapter["title"])
    path.write_text(header + body.strip() + "\n", encoding="utf-8")
    print("  已写入 %s" % path.name)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", action="store_true", help="只生成目录树")
    parser.add_argument("--chapter", help="生成单章正文，例如 s1c1")
    parser.add_argument("--all", action="store_true", help="补齐所有缺失章节")
    parser.add_argument("--stage", help="只处理某个阶段，例如 s3")
    parser.add_argument("--force", action="store_true", help="覆盖已有文件")
    args = parser.parse_args()

    if not (args.tree or args.chapter or args.all or args.stage):
        parser.print_help()
        return

    data = build_tree(force=args.force, only_stage=args.stage if args.tree else None)
    if args.tree and not (args.chapter or args.all):
        return

    by_id = {c["id"]: c for c in data["chapters"]}
    if args.chapter:
        target = by_id.get(args.chapter)
        if not target:
            print("没有这一章：%s" % args.chapter)
            return
        build_chapter(target, data["title"], force=args.force)
        return

    targets = [c for c in data["chapters"] if args.stage is None or c["stage"] == args.stage]
    print("待生成 %d 章" % len(targets))
    for chapter in targets:
        build_chapter(chapter, data["title"], force=args.force)


if __name__ == "__main__":
    main()
