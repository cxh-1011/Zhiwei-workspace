# -*- coding: utf-8 -*-
"""生成组会汇报用 Word 文档：学习档案（长期记忆 + 任务规划）代码实现说明。"""
import io
import json
import os

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BASE = r"D:\hy\ai\ai_robot\.deliverables\learning-hub"
OUT_DOCX = r"D:\hy\ai\ai_robot\docs\学习档案_长期记忆与任务规划_代码实现说明.docx"

INK = RGBColor(0x1F, 0x23, 0x2B)
DIM = RGBColor(0x5E, 0x66, 0x72)
ACCENT = RGBColor(0xB4, 0x7C, 0x08)
LINE = "E8E2D4"

cards = json.load(io.open(os.path.join(BASE, "cards.json"), encoding="utf-8"))
CARDS = {c["no"]: c for c in cards["cards"]}


# ------------------------------------------------------------------ 基础工具

def set_run(run, size=10.5, bold=False, color=None, cn="微软雅黑", en="Calibri"):
    run.font.name = en
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    fonts.set(qn("w:ascii"), en)
    fonts.set(qn("w:hAnsi"), en)
    fonts.set(qn("w:eastAsia"), cn)


def para(doc, text="", size=10.5, bold=False, color=INK, align=None,
         space_before=0, space_after=6, line=1.5, indent=None):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    pf.line_spacing = line
    if indent is not None:
        pf.left_indent = Cm(indent)
    if align is not None:
        p.alignment = align
    if text:
        set_run(p.add_run(text), size=size, bold=bold, color=color)
    return p


def heading(doc, text, level=1, page_break=False):
    if page_break:
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(16 if level == 1 else 12)
    pf.space_after = Pt(8 if level == 1 else 5)
    pf.keep_with_next = True
    sizes = {1: 16, 2: 13, 3: 11.5}
    set_run(p.add_run(text), size=sizes.get(level, 11), bold=True,
            color=INK if level > 1 else RGBColor(0x14, 0x17, 0x1D),
            cn="微软雅黑", en="Calibri")
    if level == 1:
        pPr = p._element.get_or_add_pPr()
        borders = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "10")
        bottom.set(qn("w:space"), "4")
        bottom.set(qn("w:color"), "C79A2E")
        borders.append(bottom)
        pPr.append(borders)
    return p


def bullet(doc, text, size=10.5):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.45
    p.paragraph_format.left_indent = Cm(0.7)
    set_run(p.add_run(text), size=size)
    return p


def shade(cell, hex_fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcPr.append(shd)


def table_borders(table, color="E2DCCD", sz="4"):
    tbl = table._tbl
    tblPr = tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement("w:%s" % edge)
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), sz)
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)
    tblPr.append(borders)


def _set_tbl_width(tbl, widths_cm):
    """python-docx 的 cell.width 不会写 tblGrid，也不会锁死总宽；
    必须补 tblW + tblLayout(fixed)，否则 Word/LibreOffice 会按内容重新排版。"""
    tblPr = tbl.tblPr
    total_tw = int(sum(widths_cm) * 567)          # 1cm = 567 twips
    for old in tblPr.findall(qn("w:tblW")):
        tblPr.remove(old)
    tblW = OxmlElement("w:tblW")
    tblW.set(qn("w:w"), str(total_tw))
    tblW.set(qn("w:type"), "dxa")
    tblPr.append(tblW)
    for old in tblPr.findall(qn("w:tblLayout")):
        tblPr.remove(old)
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    grid = tbl.find(qn("w:tblGrid"))
    if grid is not None:
        tbl.remove(grid)
    grid = OxmlElement("w:tblGrid")
    for w in widths_cm:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(int(w * 567)))
        grid.append(col)
    tbl.insert(list(tbl).index(tblPr) + 1, grid)


def add_table(doc, header, rows, widths, header_fill="1E2229",
              header_color=RGBColor(0xF4, 0xF1, 0xEA), size=9.5):
    t = doc.add_table(rows=1, cols=len(header))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    table_borders(t)
    _set_tbl_width(t._tbl, widths)
    for i, text in enumerate(header):
        cell = t.rows[0].cells[i]
        cell.width = Cm(widths[i])
        cell.text = ""
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.space_before = Pt(3)
        set_run(p.add_run(text), size=size, bold=True, color=header_color)
        shade(cell, header_fill)
    for r_i, row in enumerate(rows):
        cells = t.add_row().cells
        for i, text in enumerate(row):
            cells[i].width = Cm(widths[i])
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            p.paragraph_format.space_after = Pt(3)
            p.paragraph_format.space_before = Pt(3)
            p.paragraph_format.line_spacing = 1.3
            set_run(p.add_run(str(text)), size=size)
            if r_i % 2 == 1:
                shade(cells[i], "FAF8F3")
    return t


def code_block(doc, text, size=8.5):
    """等宽代码块：浅底 + 细边框，避免依赖高亮。"""
    t = doc.add_table(rows=1, cols=1)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    table_borders(t, color="D9D3C4")
    _set_tbl_width(t._tbl, [16.0])
    cell = t.rows[0].cells[0]
    cell.width = Cm(16.0)
    cell.text = ""
    shade(cell, "F6F4EE")
    first = True
    for line in text.split("\n"):
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.line_spacing = 1.15
        set_run(p.add_run(line if line else " "), size=size,
                cn="宋体", en="Consolas")
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def add_code_card(doc, no):
    c = CARDS[no]
    # 标题行：编号 + 标题 + 位置
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.keep_with_next = True
    set_run(p.add_run("卡片 %s  " % c["no"]), size=11, bold=True, color=ACCENT)
    set_run(p.add_run(c["title"]), size=11, bold=True)
    p2 = doc.add_paragraph()
    p2.paragraph_format.space_after = Pt(5)
    p2.paragraph_format.keep_with_next = True
    set_run(p2.add_run("位置：%s · 第 %d–%d 行" %
                       (c["path"], c["first"], c["last"])), size=9, color=DIM)

    # 一句话说明（与图片保持同页）
    dp = para(doc, c["desc"], size=10, color=RGBColor(0x33, 0x38, 0x40), space_after=6)
    dp.paragraph_format.keep_with_next = True

    # 代码截图
    pic = doc.add_paragraph()
    pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pic.paragraph_format.space_after = Pt(4)
    pic.paragraph_format.keep_with_next = True
    pic.add_run().add_picture(c["file"], width=Cm(15.6))

    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(8)
    set_run(cap.add_run("图 %s　%s（原图可放大查看）" % (c["no"], c["title"])),
            size=8.5, color=DIM)

    # 逐条备注
    para(doc, "代码备注", size=10, bold=True, color=ACCENT, space_after=4)
    for n in c["notes"]:
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(0.6)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = 1.4
        set_run(p.add_run("%d. " % n["n"]), size=10, bold=True, color=ACCENT)
        set_run(p.add_run("[第 %d 行] " % n["line"]), size=9, color=DIM)
        set_run(p.add_run(n["text"]), size=10)

    # 讲什么
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.6)
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(10)
    p.paragraph_format.line_spacing = 1.5
    set_run(p.add_run("汇报时这样讲　"), size=9.5, bold=True,
            color=RGBColor(0x4C, 0x9A, 0x6A))
    set_run(p.add_run(c["talk"]), size=10, color=RGBColor(0x33, 0x38, 0x40))


def add_footer_pagenum(section):
    # 封面不计入编号：正文这一节从 1 重新开始计页
    sectPr = section._sectPr
    for old in sectPr.findall(qn("w:pgNumType")):
        sectPr.remove(old)
    pg_num = OxmlElement("w:pgNumType")
    pg_num.set(qn("w:start"), "1")
    sectPr.append(pg_num)

    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run(p.add_run("— "), size=8, color=DIM)
    for instr in ("PAGE",):
        run = p.add_run()
        set_run(run, size=8, color=DIM)
        fld_begin = OxmlElement("w:fldChar")
        fld_begin.set(qn("w:fldCharType"), "begin")
        instr_el = OxmlElement("w:instrText")
        instr_el.set(qn("xml:space"), "preserve")
        instr_el.text = " %s " % instr
        fld_sep = OxmlElement("w:fldChar")
        fld_sep.set(qn("w:fldCharType"), "separate")
        fld_end = OxmlElement("w:fldChar")
        fld_end.set(qn("w:fldCharType"), "end")
        run._element.append(fld_begin)
        run._element.append(instr_el)
        run._element.append(fld_sep)
        run._element.append(fld_end)
        set_run(p.add_run(" —"), size=8, color=DIM)


# ------------------------------------------------------------------ 文档

def main():
    doc = Document()

    # 正文默认字体
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")

    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.0)
    sec.bottom_margin = Cm(2.0)
    sec.left_margin = Cm(2.4)
    sec.right_margin = Cm(2.4)

    # ---------------- 封面 ----------------
    for _ in range(5):
        para(doc, "", space_after=0)
    para(doc, "知微工作空间", size=11, color=ACCENT, bold=True,
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
    para(doc, "学习档案", size=26, bold=True,
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=4)
    para(doc, "长期记忆 与 任务规划", size=18, bold=True,
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=14)
    para(doc, "功能实现说明 · 代码与逐行备注", size=11.5, color=DIM,
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=34)

    add_table(
        doc,
        ["项目", "内容"],
        [
            ["文档用途", "组会汇报：讲清两个功能是怎么实现的"],
            ["代码来源", "backend/database.py、main.py、agent.py、frontend/index.html、app.js"],
            ["说明图数量", "15 张卡片，均为真实源码按行号抽取后渲染"],
            ["配套文件", "agent-refactor-plan.md（改造方案）、README.md（项目说明）"],
        ],
        widths=[3.2, 12.6],
        size=9.5,
    )

    para(doc, "", space_after=0)
    para(doc, "提示：文中代码图为真实源码截图，网页版可放大查看；"
              "完整可放大版本见同目录 combined.html。",
         size=9, color=DIM, align=WD_ALIGN_PARAGRAPH.CENTER, space_before=18)

    # ---------------- 正文（新的一节，页脚显示页码） ----------------
    body = doc.add_section(WD_SECTION.NEW_PAGE)
    body.page_width = Cm(21.0)
    body.page_height = Cm(29.7)
    body.top_margin = Cm(2.0)
    body.bottom_margin = Cm(1.8)
    body.left_margin = Cm(2.4)
    body.right_margin = Cm(2.4)
    add_footer_pagenum(body)

    # ========== 一、这一页在讲什么 ==========
    heading(doc, "一、先给结论：这两个功能做了什么", 1)
    para(doc, "「学习档案」是一个弹窗页面，里面放了两件事：左边的「长期记忆」和右边的「任务规划」。"
              "它们解决的是同一类问题——让 AI 记住你，并且帮你把大目标拆成今天能动手的事。")
    para(doc, "")
    add_table(
        doc,
        ["功能", "用户视角怎么用", "系统内部做了什么"],
        [
            ["长期记忆",
             "在「学习档案」里写下自己的研究方向、基础水平、偏好，保存。",
             "存进 memories 表；每次聊天前取出最近 20 条，拼进系统提示词，"
             "让千问在回答时把「你是谁」考虑进去。"],
            ["任务规划",
             "写下学习目标（例如「一周内理解 Transformer」），点「生成任务计划」。",
             "调用千问按约定的 JSON 结构生成 5–12 条可勾选任务，存进 "
             "learning_plans（主表）和 learning_tasks（子表），前端渲染成带进度条的清单。"],
            ["重新规划",
             "完成部分任务后，写一句「这周只剩两天，帮我压缩计划」，点「重新规划」。",
             "只删除未完成任务，已完成的任务作为学习记录保留；"
             "再把目标、进度和调整说明一起交给模型，生成新的待办接在后面。"],
        ],
        widths=[2.4, 6.4, 7.0],
    )
    para(doc, "")
    para(doc, "汇报时可以先把这句话说出来：这两个功能都不是「让 AI 多说几句话」，"
              "而是给模型补了两样它天生缺的东西——跨会话的持久背景，和对任务状态的追踪。",
         size=10, color=RGBColor(0x33, 0x38, 0x40))

    # ========== 二、整体架构 ==========
    heading(doc, "二、整体架构：数据存在哪，怎么流到模型那里", 1)
    para(doc, "整个系统是一个 FastAPI 应用，前端是原生 HTML + JS，数据存在本地 SQLite 文件 "
              "backend/chats.db 里。学习档案的两个功能，走的是同一条五段式链路：")
    para(doc, "")
    add_table(
        doc,
        ["① 前端", "② 接口", "③ 数据层", "④ 模型", "⑤ 回写"],
        [
            ["app.js 收集输入\nopenLearningHub()\nloadMemories()",
             "main.py 定义路由\n/memories\n/learning_plans",
             "database.py 读写\nmemories\nlearning_plans / learning_tasks",
             "agent.py 把记忆拼进\n系统提示词；\nmain.py 调千问生成计划",
             "结果落库后返回，\n前端重新拉取列表\n刷新界面"],
        ],
        widths=[3.2, 3.0, 3.2, 3.4, 3.2],
        size=9,
    )
    para(doc, "")
    para(doc, "下面这张表是本篇后面所有代码卡片的位置索引。组会上如果老师追问某一段，"
              "可以直接翻到对应卡片。")
    para(doc, "")
    add_table(
        doc,
        ["卡片", "内容", "文件", "行号"],
        [[c["no"], c["title"], c["path"], "%d–%d" % (c["first"], c["last"])]
         for c in sorted(cards["cards"], key=lambda x: x["no"])],
        widths=[1.6, 7.4, 4.6, 2.2],
        size=9,
    )

    # ========== 三、长期记忆 ==========
    heading(doc, "三、长期记忆：怎么实现", 1, page_break=True)
    para(doc, "长期记忆的设计有一个关键取舍：只保存用户主动确认的内容，对话过程不会自动往里写。"
              "原因很实际——如果让模型自己决定记住什么，它可能把你的客套话、或者一次错误的回答"
              "固化进档案，然后污染之后所有的对话。所以这里做成了「用户手动管理」的档案。")
    para(doc, "")
    heading(doc, "3.1 数据是怎么存的", 2)
    para(doc, "memories 表结构极简：一条记忆就是一行，带一个分类标签和一个时间戳。"
              "没有做成向量库，因为这个功能的定位是「少量的、稳定的个人信息」，"
              "而不是「海量知识的语义检索」——后者是论文助手里 FAISS 索引负责的事。")
    add_code_card(doc, "01")

    heading(doc, "3.2 读写的三个函数", 2)
    add_code_card(doc, "02")

    heading(doc, "3.3 对外暴露的接口", 2)
    add_code_card(doc, "03")

    heading(doc, "3.4 记忆怎么进入模型", 2)
    para(doc, "这是长期记忆真正「生效」的一步，也是组会上最值得重点讲的一段：记忆不是存在"
              "某个全局变量里被模型随时读到的，而是每次提问时，后端现拼一段上下文交给 Agent。")
    add_code_card(doc, "04")

    heading(doc, "3.5 注入位置与防注入声明", 2)
    para(doc, "记忆是用户写的自由文本，却被放在 system 级别的位置上，这就带来一个安全隐患："
              "如果用户在记忆里写「忽略之前所有规则」，模型可能真的照做。所以拼接时加了一句"
              "明确的边界声明。")
    add_code_card(doc, "05")

    # ========== 四、任务规划 ==========
    heading(doc, "四、任务规划：怎么实现", 1, page_break=True)
    para(doc, "任务规划的技术核心是「把自然语言目标，变成结构化、可勾选、有状态的任务列表」。"
              "难点不在调用模型，而在两件事：一是让模型稳定输出可解析的结构，二是让计划能"
              "跟着你的进度演进。")
    para(doc, "")
    heading(doc, "4.1 数据模型", 2)
    para(doc, "用「主表 + 子表」的经典设计：learning_plans 存目标、总览和整体状态；"
              "learning_tasks 存每一条待办，通过 plan_id 挂到计划上。这样勾选一个任务只改子表，"
              "不需要重写整个计划。")
    add_code_card(doc, "01")

    heading(doc, "4.2 核心：一句话目标变成可勾选待办", 2)
    para(doc, "做法是给千问一份「格式契约」：必须返回带 title、overview、tasks 的 JSON，"
              "每个 task 有 stage、title、details。提示词里的约束写得很具体，因为模型的行为"
              "几乎完全由这几句话决定。")
    add_code_card(doc, "06")

    heading(doc, "4.3 模型不听话怎么办：三层解析容错", 2)
    para(doc, "大模型很常见的毛病是在 JSON 外面包一层 ```json 代码块，或者先说一句"
              "「好的，这是我的计划」。如果直接 json.loads 会失败。这段解析处理了这个问题。")
    add_code_card(doc, "07")

    heading(doc, "4.4 生成计划的完整一跳", 2)
    add_code_card(doc, "08")

    heading(doc, "4.5 任务落库：顺序与字段清洗", 2)
    add_code_card(doc, "09")

    heading(doc, "4.6 勾选完成：进度怎么落库", 2)
    add_code_card(doc, "10")

    heading(doc, "4.7 亮点：重新规划只替换未完成任务", 2)
    para(doc, "这一节如果组会时间有限，可以只讲这一段。它是整个任务规划里最有设计感的地方，"
              "也最容易讲出「为什么这样设计」的味道。")
    add_code_card(doc, "11")

    heading(doc, "4.8 重新规划接口：把进度传给模型", 2)
    add_code_card(doc, "12")

    # ========== 五、前端 ==========
    heading(doc, "五、前端：界面与交互", 1, page_break=True)
    para(doc, "前端没有用任何框架，是原生 HTML + CSS + JavaScript。两个功能都在一个 "
              "dialog 弹窗里，左右分栏。")
    add_code_card(doc, "13")
    add_code_card(doc, "14")
    add_code_card(doc, "15")

    # ========== 六、亮点总结 ==========
    heading(doc, "六、组会可以直接讲的五个亮点", 1, page_break=True)
    para(doc, "如果只有五分钟，按这个顺序讲；每个亮点都配了一句「为什么这样设计」，"
              "这比单纯说「我做了什么」更有说服力。")
    para(doc, "")
    points = [
        ("用户可控的记忆边界",
         "记忆只由用户手动添加，对话不自动写入。",
         "避免模型把错误理解固化进档案，污染后续所有回答。这是一个产品层面的风险控制，"
         "而不是技术限制。" ),
        ("分层的数据模型",
         "计划主表 + 任务子表，任务自带 status、sort_order、completed_at。",
         "勾选单个任务只改子表；整体完成改主表；排序字段保证模型给的任务顺序不被破坏。"),
        ("只替换未完成的重新规划",
         "replace_unfinished_learning_tasks() 只删 status='todo' 的任务。",
         "已完成的工作作为学习记录保留，新任务从当前最大顺序号往后接，"
         "让计划跟着进度演进，而不是每次推倒重来。"),
        ("三层防御的结构化输出",
         "格式契约（提示词）→ 解析容错（代码块/全文双候选）→ 调用方降级（兜底单条任务）。",
         "大模型输出不稳定是常态，工程上必须假设它会失败，并且失败时不能给用户看到 500。"),
        ("可回滚的交互",
         "勾选后立即禁用防连点；请求失败把勾选状态翻回去并提示。",
         "不会出现「界面显示完成了、数据库其实没改」的假象，这是前端状态一致性的基本要求。"),
    ]
    for i, (title, what, why) in enumerate(points, 1):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(2)
        set_run(p.add_run("%d. %s" % (i, title)), size=11.5, bold=True)
        for label, text in (("做法", what), ("为什么", why)):
            q = doc.add_paragraph()
            q.paragraph_format.left_indent = Cm(0.8)
            q.paragraph_format.space_after = Pt(2)
            q.paragraph_format.line_spacing = 1.45
            set_run(q.add_run("%s：" % label), size=9.5, bold=True, color=ACCENT)
            set_run(q.add_run(text), size=10)

    # ========== 七、演示脚本 ==========
    heading(doc, "七、十分钟汇报脚本（可直接照着说）", 1, page_break=True)
    script = [
        ("0:00–1:00　开场",
         "我的项目叫「知微工作空间」，是一个面向研究生学习和科研阅读的个人 AI 助手。"
         "今天重点汇报学习档案里的两个功能：长期记忆和任务规划。",
         "先交代定位，不要一上来讲代码。"),
        ("1:00–2:00　为什么做这两个",
         "大模型有两个天生的缺陷：第一，它不记得跨会话的事情，每次对话都是陌生人；"
         "第二，它不知道你的任务做到哪一步了。这两个功能就是分别补这两个缺口。",
         "讲问题，不讲功能。"),
        ("2:00–3:00　现场演示",
         "打开学习档案 → 添加一条记忆「我是研一，跨考，正在学 Transformer」→ 保存；"
         "然后回到对话问「根据我的情况，推荐我先补什么基础」→ 模型会参考这条记忆回答。",
         "演示要一次成功：先提前录屏或准备好稳定的网络。"),
        ("3:00–4:30　记忆的实现",
         "翻到第三部分。重点讲三句：数据是一张极简的 memories 表；"
         "记忆是在每次请求时现拼成 memory_context 传给 Agent 的，所以加了立刻生效；"
         "只允许用户手动写入，防止模型污染档案。",
         "对着卡片 02、04 讲。"),
        ("4:30–6:30　任务规划的实现",
         "翻到第四部分。核心是让千问按 JSON 契约输出，代码侧三层容错解析成任务列表，"
         "主表存计划、子表存任务。勾选一个任务就是改子表的一行。",
         "对着卡片 06、07、08 讲。"),
        ("6:30–8:00　重点讲重新规划",
         "这是我认为设计得最好的地方：重新规划时只删除未完成任务，已完成的作为学习记录保留。"
         "所以计划是跟着你的进度往前走的，不是每次推倒重来。",
         "这是全场最值得多讲 90 秒的地方。"),
        ("8:00–9:30　不足与下一步",
         "主动说不足，会显得你真的想过。三个：记忆是取最近 20 条，不区分相关性；"
         "重新规划喂给模型的是逐条任务列表，不是「我从这段时间学到了什么」的总结，"
         "长期下去可能变成任务堆砌；计划完成度只统计任务勾选数，"
         "没有衡量掌握程度。",
         "详见第八部分，别等老师问。"),
        ("9:30–10:00　收尾",
         "一句话总结：这两个功能让助手从「一次性的问答工具」变成「有记忆、能追踪进度的学习伙伴」。"
         "下一步打算把记忆检索改成按相关性召回，并给计划加上掌握度自评。",
         "收尾要落到下一步，显示还在推进。"),
    ]
    for title, text, tip in script:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(9)
        p.paragraph_format.space_after = Pt(2)
        set_run(p.add_run(title), size=11, bold=True)
        q = doc.add_paragraph()
        q.paragraph_format.left_indent = Cm(0.8)
        q.paragraph_format.space_after = Pt(2)
        q.paragraph_format.line_spacing = 1.5
        set_run(q.add_run(text), size=10)
        t = doc.add_paragraph()
        t.paragraph_format.left_indent = Cm(0.8)
        t.paragraph_format.space_after = Pt(2)
        set_run(t.add_run("提醒：" + tip), size=9, color=DIM)

    # ========== 八、不足与下一步 ==========
    heading(doc, "八、已知不足与改进方向", 1, page_break=True)
    para(doc, "这一节建议主动讲。老师最可能从下面这些地方提问，提前想好答案，"
              "比被问到答不上来要好得多。")
    para(doc, "")
    add_table(
        doc,
        ["现存问题", "具体表现", "可以怎么改"],
        [
            ["记忆检索不分相关性",
             "取的是最近 20 条，不是最相关的 20 条。记忆多了以后，早期的重要信息会被挤掉。",
             "给 memory 加 embedding，按当前问题做相关性召回；或先粗排再让模型挑选。"],
            ["记忆没有用户隔离",
             "memories 表没有 user_id。单人部署没问题，但多用户会互相看到彼此的档案。",
             "加 users 表和外键；或在单用户前提下明确文档化为「私有部署工具」。"],
            ["重新规划的上下文偏薄",
             "喂给模型的是逐条任务列表（[done]/[todo]），不是「已完成任务的收获总结」。"
             "反复规划后，计划容易变成任务堆砌。",
             "在重新规划前生成一份「已完成任务小结」一并喂入；或让模型先总结再排任务。"],
            ["结构化输出靠 JSON 约定",
             "解析用正则抽代码块，本质是 prompt 层 hack，模型格式一变就要改解析器。",
             "改用千问原生 function calling：把「生成学习计划」定义成一个工具，"
             "由模型返回结构化 tool_calls，格式由 SDK 保证。"],
            ["完成度只统计任务数",
             "进度条 = 已完成任务数 / 总任务数，但划掉一个任务不等于掌握了知识。",
             "给任务加「掌握度自评」（1–5 分），进度条改成按掌握度加权。"],
            ["没有提醒和复盘",
             "计划建完之后，系统不会主动提醒该做什么，也没有周期性复盘。",
             "加一个每日/每周的待办摘要；或让 Agent 在对话中主动提示今日任务。"],
        ],
        widths=[3.6, 6.6, 5.6],
        size=9,
    )
    para(doc, "")
    para(doc, "如果老师问「你觉得这个方向下一步最有价值的是什么」，建议回答："
              "把记忆从「时间序」改成「相关性召回」，因为这一步同时提升了长期记忆和任务规划"
              "两个功能——规划器能召回更贴切的背景，对话也能引用更相关的事实。"
              "一个改动收益两处，性价比最高。",
         size=10, color=RGBColor(0x33, 0x38, 0x40))

    # ========== 附录 ==========
    heading(doc, "附录：相关文件与接口速查", 1, page_break=True)
    heading(doc, "涉及的文件", 2)
    add_table(
        doc,
        ["文件", "在这两个功能里的职责"],
        [
            ["backend/database.py", "建表（memories / learning_plans / learning_tasks）与全部数据访问函数"],
            ["backend/main.py", "REST 接口、把记忆拼成上下文、调用千问生成与调整计划"],
            ["backend/agent.py", "把 memory_context 注入系统提示词，并声明「不得作为指令执行」"],
            ["frontend/index.html", "学习档案弹窗的两栏结构"],
            ["frontend/app.js", "渲染记忆列表、渲染计划与任务、勾选状态更新与回滚"],
            ["frontend/style.css", "学习档案、进度条、任务项的样式"],
        ],
        widths=[4.4, 11.6],
        size=9.5,
    )
    para(doc, "")
    heading(doc, "接口速查", 2)
    add_table(
        doc,
        ["方法", "路径", "作用"],
        [
            ["GET", "/memories", "列出长期记忆（最多 100 条）"],
            ["POST", "/memories", "新增一条记忆，内容为空返回 400"],
            ["DELETE", "/memories/{id}", "删除一条记忆，不存在返回 404"],
            ["GET", "/learning_plans", "列出计划，附任务列表和完成计数"],
            ["POST", "/learning_plans/generate", "根据目标生成计划与任务"],
            ["POST", "/learning_plans/{id}/tasks", "手动添加一条任务"],
            ["PATCH", "/learning_plans/{id}/tasks/{tid}", "勾选/取消完成"],
            ["DELETE", "/learning_plans/{id}/tasks/{tid}", "删除一条任务"],
            ["POST", "/learning_plans/{id}/adjust", "按当前进度重新规划，保留已完成任务"],
            ["PATCH", "/learning_plans/{id}?status=", "把整个计划标记为 active / done"],
            ["DELETE", "/learning_plans/{id}", "删除计划及其任务"],
        ],
        widths=[2.0, 8.2, 5.8],
        size=9.5,
    )

    os.makedirs(os.path.dirname(OUT_DOCX), exist_ok=True)
    doc.save(OUT_DOCX)
    print("已生成:", OUT_DOCX)


if __name__ == "__main__":
    main()
