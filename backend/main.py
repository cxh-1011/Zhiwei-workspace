from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import hashlib
import hmac
import os
import time


# ======================
# 路径（基于本文件计算，跟你在哪个目录启动无关）
# ======================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))


# ======================
# AI助手角色设定
# ======================

SYSTEM_PROMPT = """
你是一个智能AI助手。

你同时具备音乐推荐能力。


规则：

如果用户询问音乐、歌曲、歌单，
或者表达自己的情绪：

例如：
我很难过
我压力很大
我睡不着
推荐几首歌
适合学习听什么歌


请进入音乐助手模式。


必须严格输出以下格式：


[MUSIC_CARD]

歌曲名: 歌曲名称

歌手: 歌手名称

理由: 推荐理由

[/MUSIC_CARD]



一次输出3首歌曲。


注意：

1. [MUSIC_CARD] 必须出现。
2. 不允许改成其他格式。
3. 不要输出：
歌曲名：
然后下一行写内容。

必须同一行。


正确：

歌曲名: 晚安

错误：

歌曲名:
晚安



如果用户不是音乐相关问题：

正常回答即可。

"""


import dashscope
from dashscope import Generation
from tavily import TavilyClient

from config import (
    DASHSCOPE_API_KEY,
    TAVILY_API_KEY,
    APP_PASSWORD_HASH,
    APP_SECRET_KEY,
    APP_COOKIE_SECURE
)

from paper_agent import load_paper, ask_paper, ensure_db_loaded

from database import (

    init_db,

    create_chat,

    get_chats,

    delete_chat,

    save_message,

    get_messages,

    update_chat_title

)


# ======================
# 千问API
# ======================

dashscope.api_key = DASHSCOPE_API_KEY


# ======================
# 创建APP
# ======================

app = FastAPI(title="AI智能机器人")


# 联网搜索客户端
# 没配置 TAVILY_API_KEY 时用占位符，避免启动就报错；
# 真正要用的时候会在 chat() 里判断，没有 key 就跳过联网。
tavily = TavilyClient(
    api_key=TAVILY_API_KEY or "tvly-not-configured"
)


# ======================
# 跨域
# ======================

app.add_middleware(

    CORSMiddleware,

    allow_origins=["*"],

    # 页面与 API 部署在同一来源时，登录 Cookie 会由浏览器自动携带。
    # 保留通配 CORS 兼容本地调试；跨来源请求不会使用登录 Cookie。
    allow_credentials=False,

    allow_methods=["*"],

    allow_headers=["*"],

)


# ======================
# /api 前缀兼容
# ======================
# 前端统一请求 /api/xxx。这里加一个中间件，把开头的 /api 剥掉，
# 于是 /api/upload_paper 会自动对上下面的 @app.post("/upload_paper")。
#
# 好处：不管服务器上 nginx 有没有配 "/api 反向代理并去前缀"，
#       接口都能正常工作，部署方式随便选。

@app.middleware("http")
async def strip_api_prefix(request, call_next):

    path = request.scope.get("path", "")

    if path.startswith("/api/"):

        request.scope["path"] = path[4:]

        raw = request.scope.get("raw_path")

        if raw:

            request.scope["raw_path"] = raw[4:]

    return await call_next(request)


# Login uses a signed, short-lived, HTTP-only cookie. Every data/API route and
# the /uploads mount is protected; only the login shell and its assets are public.
SESSION_COOKIE = "ai_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
PUBLIC_PATHS = {"/", "/index.html", "/style.css", "/app.js", "/auth/status", "/auth/login"}


def password_matches(candidate: str) -> bool:
    try:
        salt_hex, expected_hex = APP_PASSWORD_HASH.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
    except (ValueError, AttributeError):
        return False

    actual = hashlib.pbkdf2_hmac("sha256", candidate.encode("utf-8"), salt, 310_000)
    return hmac.compare_digest(actual, expected)


def create_session_token() -> str:
    expires = str(int(time.time()) + SESSION_TTL_SECONDS)
    signature = hmac.new(APP_SECRET_KEY.encode("utf-8"), expires.encode("ascii"), hashlib.sha256).hexdigest()
    return expires + "." + signature


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    try:
        expires, signature = token.split(".", 1)
        if int(expires) <= int(time.time()):
            return False
    except (ValueError, TypeError):
        return False
    expected = hmac.new(APP_SECRET_KEY.encode("utf-8"), expires.encode("ascii"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.scope.get("path", "")
    if path.startswith("/api/"):
        path = path[4:]
    if path in PUBLIC_PATHS or valid_session(request.cookies.get(SESSION_COOKIE)):
        return await call_next(request)
    return JSONResponse(status_code=401, content={"detail": "请先登录或重新登录。"})


# ======================
# 初始化数据库
# ======================

init_db()


# ======================
# 文件目录
# ======================

os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount(
    "/uploads",
    StaticFiles(directory=UPLOAD_DIR),
    name="uploads"
)


# 启动时尝试恢复上次上传的论文索引（重启后不用重新上传）
ensure_db_loaded()


# ======================
# 数据模型
# ======================

class Message(BaseModel):

    text: str

    chat_id: int


class PaperQuestion(BaseModel):

    question: str
    chat_id: int | None = None


class LoginRequest(BaseModel):
    password: str


@app.get("/auth/status")
def auth_status(request: Request):
    return {"authenticated": valid_session(request.cookies.get(SESSION_COOKIE))}


@app.post("/auth/login")
def login(data: LoginRequest, response: Response):
    if not password_matches(data.password):
        raise HTTPException(status_code=401, detail="密码不正确。")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_token(),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=APP_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return {"message": "登录成功"}


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"message": "已退出登录"}


# ======================
# 聊天
# ======================

@app.post("/chat")
def chat(message: Message):

    try:

        # ======================
        # 判断是否需要联网
        # ======================

        need_search_words = [

            "新闻",

            "今天",

            "最新",

            "最近",

            "现在",

            "价格",

            "天气",

            "股票",

            "比赛"

        ]

        need_search = any(

            word in message.text

            for word in need_search_words

        )

        # 没有配置 Tavily key 时，直接跳过联网，保证聊天本身可用
        if need_search and not TAVILY_API_KEY:

            need_search = False

        # ======================
        # 联网搜索
        # ======================

        if need_search:

            search_result = tavily.search(

                query=message.text,

                max_results=3

            )

            web_content = ""

            for item in search_result["results"]:

                web_content += item["content"] + "\n"

            prompt = f"""

{SYSTEM_PROMPT}


联网搜索内容：

{web_content}


用户问题：

{message.text}


请结合以上内容回答。

"""

        else:

            prompt = f"""

{SYSTEM_PROMPT}


用户问题：

{message.text}


请回答用户。

"""

        # ======================
        # 调用千问
        # ======================

        response = Generation.call(

            model="qwen-plus",

            prompt=prompt

        )

        ai_text = response.output.text

        # 自动修改聊天标题
        update_chat_title(

            message.chat_id,

            message.text[:15]

        )

        # 保存用户消息
        save_message(

            message.chat_id,

            "user",

            message.text

        )

        # 保存AI消息
        save_message(

            message.chat_id,

            "ai",

            ai_text

        )

        return {

            "reply": ai_text

        }

    except Exception as e:
        raise HTTPException(status_code=502, detail="AI 对话暂时不可用，请稍后重试。") from e


# ======================
# 创建聊天
# ======================

@app.post("/create_chat")
def new_chat(title: str = "新聊天"):

    chat_id = create_chat(title)

    return {

        "message": "创建成功",

        "id": chat_id

    }


# ======================
# 获取聊天列表
# ======================

@app.get("/chats")
def chats():

    return {

        "data": get_chats()

    }


# ======================
# 删除聊天
# ======================

@app.delete("/delete_chat/{id}")
def remove_chat(id: int):

    delete_chat(id)

    return {

        "message": "删除成功"

    }


# ======================
# 获取历史消息
# ======================

@app.get("/messages/{chat_id}")
def messages(chat_id: int):

    return {

        "data": get_messages(chat_id)

    }


# ======================
# 文件上传
# ======================

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):

    filename = os.path.basename(file.filename or "upload.bin")

    if os.path.splitext(filename)[1].lower() not in {".txt", ".pdf", ".docx"}:
        raise HTTPException(status_code=415, detail="仅支持 TXT、PDF 和 DOCX 文件。")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空。")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件不能超过 20 MB。")

    path = os.path.join(UPLOAD_DIR, filename)

    with open(path, "wb") as f:

        f.write(content)

    return {

        "filename": filename,

        "message": "上传成功"

    }


# ======================
# 上传论文
# ======================

@app.post("/upload_paper")
async def upload_paper(file: UploadFile = File(...)):

    filename = os.path.basename(file.filename or "paper.pdf")

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="论文助手仅支持 PDF 文件。")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="PDF 文件内容为空。")
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="论文不能超过 50 MB。")

    path = os.path.join(UPLOAD_DIR, filename)

    with open(path, "wb") as f:

        f.write(content)

    count = load_paper(path)

    return {

        "message": "论文解析完成",

        "filename": filename,

        "chunks": count

    }


# ======================
# 论文问答
# ======================

@app.post("/paper_chat")
def paper_chat(data: PaperQuestion):

    answer = ask_paper(

        data.question

    )

    if data.chat_id is not None:
        update_chat_title(data.chat_id, data.question[:15])
        save_message(data.chat_id, "user", data.question)
        save_message(data.chat_id, "ai", answer)

    return {

        "reply": answer

    }


@app.get("/paper_status")
def paper_status():
    return {"ready": ensure_db_loaded()}


# ======================
# 文件列表
# ======================

@app.get("/files")
def files():

    return {

        "files": sorted(os.listdir(UPLOAD_DIR))

    }


# ======================
# 文件读取
# ======================

def read_file(path):

    if not os.path.isfile(path):
        raise FileNotFoundError("没有找到这个文件，请检查文件名或先上传文件。")

    text = ""

    extension = os.path.splitext(path)[1].lower()

    if extension == ".txt":

        with open(

            path,

            "r",

            encoding="utf-8",

            errors="ignore"

        ) as f:

            text = f.read()

    elif extension == ".pdf":

        from pypdf import PdfReader

        reader = PdfReader(path)

        for page in reader.pages:

            result = page.extract_text()

            if result:

                text += result

    elif extension == ".docx":

        from docx import Document

        doc = Document(path)

        for p in doc.paragraphs:

            text += p.text

    else:
        raise ValueError("目前只支持读取 TXT、PDF 和 DOCX 文件。")

    if not text.strip():
        raise ValueError("没有从文件中读取到可用文字。扫描版 PDF 请先进行 OCR。")

    return text


# ======================
# 文件问答
# ======================

@app.post("/chat_file")
def chat_file(filename: str, question: str, chat_id: int | None = None):

    try:

        safe_name = os.path.basename(filename)

        content = read_file(

            os.path.join(UPLOAD_DIR, safe_name)

        )

        prompt = f"""

你是一个文件分析助手。


文件内容：

{content}



用户问题：

{question}



请根据文件内容回答。

"""

        response = Generation.call(

            model="qwen-plus",

            prompt=prompt

        )

        answer = response.output.text
        if chat_id is not None:
            update_chat_title(chat_id, question[:15])
            save_message(chat_id, "user", question)
            save_message(chat_id, "ai", answer)

        return {

            "reply": answer

        }

    except Exception as e:
        raise HTTPException(status_code=422, detail="文件分析失败：" + str(e)) from e


# ======================
# 前端页面（必须放在最后）
# ======================
# 把 frontend 目录挂在根路径上，这样：
#   /            -> index.html
#   /app.js      -> 前端脚本
#   /debug.html  -> 接口自检页
# 一个服务（8000 端口）就把网页和后端接口全包了，不需要另起前端服务。

if os.path.isdir(FRONTEND_DIR):

    app.mount(

        "/",

        StaticFiles(directory=FRONTEND_DIR, html=True),

        name="frontend"

    )

else:

    @app.get("/")
    def index_placeholder():

        return {

            "message": "没找到 frontend 目录，接口已就绪，可访问 /docs 查看接口文档",

            "frontend_dir": FRONTEND_DIR

        }
