"""按用户这一句话决定走哪条链路：智能对话 / 文件问答 / 论文助手。

为什么不再让用户先选
--------------------
首页原来摆三张卡，必须先点一张才能打字。可用户开口时说的往往是「帮我看看这篇
论文的方法部分」「这份简历有什么问题」，功能其实已经写在句子里了——让他先把
这句话翻译成「我该点哪个卡」，是把系统的分类负担转给了他。三张卡留着，但降级
成「也可以直接指定」，默认路径是一个能直接打字的输入框。

为什么用规则而不是让模型分类
----------------------------
三类意图的词面差异足够大（论文/文献、这份文件/附件、其它），规则能给出确定的
答案，且是零延迟、零成本、可单测的。换成模型分类会多一次往返，还会在边界上
飘：同一句话今天判文件、明天判闲聊，用户看到的是「这个网站时好时坏」。

规则只回答「该用哪个功能」，不回答「这个问题难不难」。判断错了的代价也设计得
很小：前端会把判断结果显示出来，一点就能改；需要材料而没材料时只提示上传，
不会把一句话发进错误的链路里。
"""

# 出现这些词，基本就是在说「我手上这篇论文」。
# 刻意不含「实验」「方法」这类通用词：它们单独出现时更可能是在问怎么写论文，
# 而不是在问某一篇论文里的实验。弱词只在有指代词（这篇/本文）时才算数。
PAPER_STRONG = ("论文", "paper", "文献", "arxiv")
PAPER_WEAK = ("摘要", "引言", "相关工作", "实验", "结论", "审稿", "通讯作者")
PAPER_ANAPHOR = ("这篇", "本文", "该文", "文中", "文章里", "这份论文")

# 这些是「写论文 / 找论文」，不是「读手上这篇」。
# 不做这层排除，「帮我写论文的引言」会被拉去论文助手，然后要求上传 PDF。
PAPER_OFFTOPIC = (
    "写论文", "论文写作", "论文模板", "论文排版", "论文格式", "论文润色",
    "查重", "投稿信", "开题报告", "文献综述", "找几篇", "搜一下", "推荐几篇",
    "下载", "引用格式",
)

# 指向「某份材料」的说法。没有文件时命中这些词，说明他要的东西还没上传，
# 这时该提示上传，而不是把问题当普通闲聊答掉。
FILE_WORDS = (
    "这份文件", "这个文件", "这份资料", "这个资料", "这份文档", "这个文档",
    "附件", "上传的", "这张图", "这个图", "图片里", "截图", "表格里",
    "简历", "报告里", "文档里", "文件里", "这份 pdf", "这个 pdf",
    "excel", "docx", "csv", "txt",
)


def _norm(text):
    """统一成小写并压掉多余空白。全角空格也当分隔符，粘贴来的句子常带。"""
    return " ".join(str(text or "").lower().replace("\u3000", " ").split())


def _has(text, words):
    return any(word in text for word in words)


def _result(mode, reason, need="", auto=True):
    return {"mode": mode, "reason": reason, "need": need, "auto": auto}


def decide(text, has_file=False, paper_ready=False, filename=""):
    """返回 {mode, reason, need, auto}。纯函数，不读库、不联网。

    has_file   前端是否已选中一份上传好的文件（不是「选了个本地文件还没上传」）
    paper_ready 是否已解析过一篇 PDF
    filename   已选文件名，用来判断「这份就是 PDF，不必再走论文解析」
    """
    t = _norm(text)
    name = _norm(filename)

    # 用户直接点名功能时，按他说的来，不再猜。
    if "论文助手" in t:
        return _result("paper", "你点名要用论文助手", "" if paper_ready else "paper")
    if "文件问答" in t:
        return _result("file", "你点名要用文件问答", "" if has_file else "file")

    paper_word = _has(t, PAPER_STRONG)
    paper_weak = _has(t, PAPER_WEAK) and _has(t, PAPER_ANAPHOR)

    # 「帮我写论文」「找几篇论文」是写作/检索任务，不是读某篇。
    # 只有在他确实解析过论文、并且话里指的是「这一篇」时，才仍然按论文助手走。
    if _has(t, PAPER_OFFTOPIC) and not (paper_ready and _has(t, PAPER_ANAPHOR)):
        return _result("chat", "这是写作或检索类问题，按普通对话处理")

    if paper_word or paper_weak:
        if paper_ready:
            return _result("paper", "提到论文，按已解析的那篇回答")
        # 已经选了一份 PDF，但还没解析：文件问答本身就能读 PDF，
        # 没必要让他再上传一次。论文助手要的是索引，不是文件本身。
        if has_file and name.endswith(".pdf"):
            return _result("file", "这份 PDF 还没解析，先用文件问答读它")
        return _result("paper", "要读论文，得先上传并解析 PDF", "paper")

    if has_file:
        return _result("file", "已经选了资料，按文件问答回答")

    if _has(t, FILE_WORDS):
        return _result("file", "像是在问某份资料，先添加文件", "file")

    return _result("chat", "没提到具体材料，按普通对话回答")
