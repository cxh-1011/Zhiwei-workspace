"""自动选功能的离线测试：一句话该走哪条链路，以及缺材料时怎么提示。

不联网、不调模型。规则是纯函数，这里把边界逐条钉住——尤其是三类容易判错的：
「帮我写论文」不是读论文、「这张图」没有图、「这份 PDF」不必再走论文解析。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import router

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s %s" % (name, detail))


def decide(text, **kwargs):
    return router.decide(text, **kwargs)


def is_(name, result, mode, need=""):
    check(name, result["mode"] == mode and result["need"] == need,
          "%s need=%r reason=%r" % (result["mode"], result["need"], result["reason"]))


def test_plain():
    print("\n[没提材料：普通对话]")
    is_("打招呼", decide("你好"), "chat")
    is_("问技术概念", decide("ReAct 是什么？"), "chat")
    is_("写代码", decide("帮我写个 Python 函数，把列表去重"), "chat")
    is_("空文本", decide(""), "chat")
    is_("只有空白", decide("   \u3000  "), "chat")
    check("默认不是手动指定", decide("你好")["auto"] is True)
    check("普通对话给了理由", bool(decide("你好")["reason"]))


def test_paper():
    print("\n[读论文：要解析过的 PDF]")
    is_("提到论文且已解析", decide("这篇论文的方法部分讲了什么", paper_ready=True), "paper")
    is_("提到论文但没解析", decide("这篇论文的方法部分讲了什么"), "paper", "paper")
    is_("只说 paper 也算", decide("What does this paper do", paper_ready=True), "paper")
    is_("弱词加指代才算", decide("这篇的实验结论可靠吗", paper_ready=True), "paper")
    is_("弱词单独出现不算",
        decide("实验结果一般怎么写进报告", paper_ready=True), "chat")
    is_("解析过但没指代，不硬套",
        decide("你好，今天状态不错", paper_ready=True), "chat")
    is_("点名论文助手", decide("用论文助手看看这个"), "paper", "paper")
    is_("点名且已就绪", decide("用论文助手看看这个", paper_ready=True), "paper")


def test_paper_offtopic():
    print("\n[写论文/找论文：不能被拉去论文助手]")
    is_("写引言", decide("帮我写论文的引言"), "chat")
    is_("论文排版", decide("论文排版有什么要求"), "chat")
    is_("找几篇", decide("帮我找几篇 RAG 的论文"), "chat")
    is_("文献综述", decide("文献综述该怎么组织"), "chat")
    is_("查重", decide("论文查重怎么降"), "chat")
    is_("投稿信", decide("写一封投稿信"), "chat")
    is_("但指这一篇时仍按论文",
        decide("这篇论文的引用格式对吗", paper_ready=True), "paper")


def test_file():
    print("\n[文件问答：有没有材料决定提示还是直接发]")
    is_("有图问图", decide("这张图里是什么", has_file=True), "file")
    is_("没图问图", decide("这张图里是什么"), "file", "file")
    is_("问附件", decide("附件里的第三条是什么"), "file", "file")
    is_("问简历", decide("这份简历有什么问题"), "file", "file")
    is_("提到 docx", decide("docx 里的表格能读吗"), "file", "file")
    is_("已选文件就问别的", decide("总结一下", has_file=True), "file")
    is_("点名文件问答", decide("用文件问答读一下"), "file", "file")
    is_("点名且已有文件", decide("用文件问答读一下", has_file=True), "file")


def test_pdf_edge():
    print("\n[PDF 边界：已有的 PDF 不必再解析一次]")
    is_("选了 PDF 但没解析",
        decide("这篇论文讲了什么", has_file=True, filename="attention.pdf"), "file")
    is_("选了 PDF 且已解析",
        decide("这篇论文讲了什么", has_file=True, paper_ready=True,
               filename="attention.pdf"), "paper")
    is_("选的是 txt，仍提示解析",
        decide("这篇论文讲了什么", has_file=True, filename="notes.txt"), "paper", "paper")
    is_("文件名大小写不影响",
        decide("这篇论文讲了什么", has_file=True, filename="A.PDF"), "file")
    is_("文件名带路径也能认出 PDF",
        decide("这篇论文讲了什么", has_file=True, filename="papers/x.pdf"), "file")


def test_http():
    print("\n[接口：要登录，参数正确]")
    from fastapi.testclient import TestClient

    import main as app_main

    client = TestClient(app_main.app)
    check("没登录不能路由", client.post("/route", json={"text": "你好"}).status_code == 401)
    client.cookies.set(app_main.SESSION_COOKIE, app_main.create_session_token())

    resp = client.post("/route", json={"text": "这篇论文讲了什么"})
    body = resp.json()
    check("登录后 200", resp.status_code == 200, resp.status_code)
    check("没解析时提示上传", body.get("mode") == "paper" and body.get("need") == "paper", body)
    check("带理由", bool(body.get("reason")), body)

    resp = client.post("/route", json={"text": "你好"})
    check("闲聊回 chat", resp.json().get("mode") == "chat", resp.text[:120])

    resp = client.post("/route", json={"text": "这张图里是什么", "has_file": True})
    check("带文件参数生效", resp.json().get("mode") == "file", resp.text[:120])

    resp = client.post("/route", json={"text": "x" * 5000})
    check("超长文本被拒", resp.status_code == 422, resp.status_code)


def main_():
    test_plain()
    test_paper()
    test_paper_offtopic()
    test_file()
    test_pdf_edge()
    test_http()
    print("\n%s 通过 %d，失败 %d" % ("FAIL" if FAIL else "ALL PASS", PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_())
