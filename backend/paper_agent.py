import os
import json

import pymupdf

from langchain_text_splitters import RecursiveCharacterTextSplitter

from langchain_community.vectorstores import FAISS

from langchain_community.embeddings import DashScopeEmbeddings

import dashscope

from dashscope import Generation

from config import DASHSCOPE_API_KEY


# 设置千问key

dashscope.api_key = DASHSCOPE_API_KEY


# 索引存放目录（基于本文件定位，跟启动目录无关）

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

INDEX_DIR = os.path.join(BASE_DIR, "paper_index")


# 全局知识库

db = None
PAPER_SOURCE_PATH = os.path.join(INDEX_DIR, "source.json")


def _get_embeddings():

    """
    创建 Embedding
    """

    return DashScopeEmbeddings(

        model="text-embedding-v3",

        dashscope_api_key=DASHSCOPE_API_KEY

    )


def load_paper(pdf_path):

    """
    读取论文、切片、建立向量库，并持久化到磁盘
    """

    global db

    # 读取PDF

    doc = pymupdf.open(pdf_path)

    text = ""

    for page in doc:

        text += page.get_text()

    doc.close()

    print("论文字符数量:", len(text))

    # 文本切片

    splitter = RecursiveCharacterTextSplitter(

        chunk_size=1000,

        chunk_overlap=200

    )

    chunks = splitter.split_text(text)

    print("切片数量:", len(chunks))

    if not chunks:

        raise ValueError("这个 PDF 里没有提取到任何文字（可能是扫描版图片PDF）")

    # 创建FAISS

    db = FAISS.from_texts(

        chunks,

        _get_embeddings()

    )

    # 持久化，重启后不用重新上传论文
    db.save_local(INDEX_DIR)

    with open(PAPER_SOURCE_PATH, "w", encoding="utf-8") as source_file:
        json.dump({"name": os.path.basename(pdf_path)}, source_file, ensure_ascii=False)

    print("论文索引已保存到:", INDEX_DIR)

    return len(chunks)


def ensure_db_loaded():

    """
    启动时调用：如果磁盘上有上次的论文索引，就自动加载进来
    """

    global db

    if db is not None:

        return True

    if not os.path.isdir(INDEX_DIR):

        return False

    try:

        db = FAISS.load_local(

            INDEX_DIR,

            _get_embeddings(),

            allow_dangerous_deserialization=True

        )

        print("已恢复上次的论文索引:", INDEX_DIR)

        return True

    except Exception as e:

        print("恢复论文索引失败（需要重新上传论文）:", e)

        return False


def search_paper(question, k=4):

    """只检索并返回论文原文片段，供 Agent 工具调用。"""

    global db
    ensure_db_loaded()
    if db is None:
        return []

    try:
        with open(PAPER_SOURCE_PATH, "r", encoding="utf-8") as source_file:
            source_name = json.load(source_file).get("name", "当前论文索引")
    except (OSError, ValueError, TypeError):
        source_name = "当前论文索引（旧索引未记录文件名）"

    docs = db.similarity_search(question, k=max(1, min(int(k), 6)))
    return [{"source": source_name, "text": doc.page_content} for doc in docs]


def ask_paper(question):

    """
    论文问答
    """

    global db

    results = search_paper(question, k=4)
    if not results:
        return "请先上传论文"

    context = "\n\n".join(item["text"] for item in results)

    prompt = f"""

你是一名专业的AI论文阅读助手。


请根据论文内容回答用户问题。


论文内容：

{context}



用户问题：

{question}



要求：

1. 只能依据论文内容回答

2. 不要编造

3. 给出详细解释


"""

    response = Generation.call(

        model="qwen-plus",

        prompt=prompt

    )

    return response.output.text
