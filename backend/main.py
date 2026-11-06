from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StrictBool

import hashlib
import hmac
import json
import os
import time
import logging
import re


# ======================
# 路径（基于本文件计算，跟你在哪个目录启动无关）
# ======================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))
IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


# ======================
# AI助手角色设定
# ======================

from agent import run_agent, run_agent_stream, request_cancel, resolve_approval
from llm import ModelConfigError, chat_completion, test_connection
import course as course_lib
import model_store
from tools.edits import restore_edits

from config import (
    APP_PASSWORD_HASH,
    APP_SECRET_KEY,
    APP_COOKIE_SECURE
)

from paper_agent import load_paper, ensure_db_loaded, PAPER_SOURCE_PATH, get_paper_chunk_count

from database import (
    init_db, create_chat, get_chats, delete_chat, save_message,
    get_messages, update_chat_title, get_recent_messages, save_agent_run,
    get_agent_run, get_agent_runs_for_chat, add_memory, list_memories, delete_memory,
    count_memories,
    save_learning_plan, list_learning_plans, set_learning_plan_status,
    delete_learning_plan, get_learning_plan, add_learning_tasks, list_learning_tasks,
    set_learning_task_status, delete_learning_task, replace_unfinished_learning_tasks,
    list_course_progress, set_course_progress, add_course_question, list_course_questions,
)


logger = logging.getLogger("ai_robot")


# ======================
# 创建APP
# ======================

app = FastAPI(title="AI智能机器人")


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
PUBLIC_PATHS = {"/", "/index.html", "/style.css", "/app.js", "/auth/status", "/auth/login",
                # 学习台是另一张页面，它的静态资源必须放行，否则未登录时连登录框都看不到；
                # 真正的数据接口（/course）仍然要登录，页面自己在 boot 里查 /auth/status。
                "/learn.html", "/learn.css", "/learn.js", "/marked.min.js"}


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

    text: str = Field(..., min_length=1, max_length=12000)

    chat_id: int = Field(..., gt=0)
    mode: str = "chat"
    filename: str | None = None
    # 是否允许助手写入学习档案（长期记忆 / 勾选任务）。默认关闭。
    # 用 StrictBool：只接受真正的 true/false，避免 "yes"、1 之类的值被宽松转成真。
    allow_writes: StrictBool = False
    # 是否允许助手只读访问工作区代码（list_dir / read_file / grep_code）。默认关闭。
    allow_workspace: StrictBool = False
    # 是否允许助手修改工作区代码（write_file / edit_file）。默认关闭。
    # 开启后自动带上只读工具：改代码前必须先能读代码。每次写入都会先备份。
    allow_code_edit: StrictBool = False
    # 是否允许助手在工作区内执行命令（run_command）。默认关闭，且与「允许改代码」
    # 分开：跑命令比改文件更危险。需要确认的命令会走 /chat/approve 等用户点击。
    allow_command: StrictBool = False
    # 是否请求模型的思考过程（仅部分服务商支持，例如 Qwen3 系列）。默认关闭。
    show_thinking: StrictBool = False
    # 学习模式用：他在哪门课、哪一章。只影响系统提示词里「你现在在哪」这一段，
    # 不决定回答内容——正文由前端直接渲染，模型不重复讲。
    course_id: str = Field("", max_length=32)
    chapter_id: str = Field("", max_length=32)


class PaperQuestion(BaseModel):

    question: str
    chat_id: int | None = None


class LoginRequest(BaseModel):
    password: str


class MemoryRequest(BaseModel):
    content: str
    category: str = "偏好"


class PlanRequest(BaseModel):
    goal: str


class TaskRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=180)
    details: str = Field("", max_length=1200)
    stage: str = Field("", max_length=80)


class TaskStatusRequest(BaseModel):
    status: str


class PlanAdjustmentRequest(BaseModel):
    note: str = Field("", max_length=1000)


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

MEMORY_INJECT_LIMIT = 20


def _memory_context(include_plans: bool = True) -> str:
    """拼进 system prompt 的记忆片段。

    只带最近 20 条，且**不随记忆条数增长而变长**。这是刻意的：把 100 条全塞进去，
    每轮对话都要为「可能用得上」付 token，条数越多越挤占当前问题，模型反而更容易
    漏掉真正相关的那条。更早的记忆改成按问题检索（recall_memory）。

    末尾那句总数不是装饰：模型看不见「还有多少条没带进来」，就会把「不在上下文」
    当成「没记过」，一口回绝「你之前没跟我说过这个」。
    """
    memories = list_memories(MEMORY_INJECT_LIMIT)
    context = "\n".join(f"[{row[2]}] {row[1]}" for row in memories)
    total = count_memories()
    if total > len(memories):
        context += (
            "\n（以上只是最近 %d 条，共 %d 条；更早的没有放进上下文，"
            "需要时用 recall_memory 检索）" % (len(memories), total)
        )
    if include_plans:
        active_plans = [row for row in list_learning_plans(10) if row[4] == "active"][:3]
        if active_plans:
            context += "\n当前学习计划：\n" + "\n".join(f"- {row[1]}：{row[2]}" for row in active_plans)
    return context


def _chat_context(message: Message) -> dict:
    """校验请求并准备上下文。/chat 与 /chat/stream 共用，保证两条路径行为一致。"""
    if message.mode not in {"chat", "file", "paper", "tutor"}:
        raise HTTPException(status_code=400, detail="未知的对话模式。")
    selected_filename = None
    image_path = None
    # 学习模式必须带着章节进来：没有章节的「导师」会退化成普通问答，
    # 与其让它瞎答，不如在这里直接挡掉，前端能立刻提示。
    if message.mode == "tutor":
        if not message.chapter_id:
            raise HTTPException(status_code=400, detail="学习模式需要先选择一章。")
        try:
            course_lib.load_chapter(
                message.course_id or course_lib.DEFAULT_COURSE, message.chapter_id
            )
        except course_lib.CourseError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    selected_filename = None
    image_path = None
    if message.mode == "file":
        selected_filename = os.path.basename((message.filename or "").replace("\\", "/"))
        if not selected_filename or selected_filename != (message.filename or "").replace("\\", "/"):
            raise HTTPException(status_code=400, detail="请选择有效的上传文件。")
        if not os.path.isfile(os.path.join(UPLOAD_DIR, selected_filename)):
            raise HTTPException(status_code=404, detail="指定资料不存在，请重新选择或上传。")
        extension = os.path.splitext(selected_filename)[1].lower()
        if extension in IMAGE_MIME_TYPES:
            if os.path.getsize(os.path.join(UPLOAD_DIR, selected_filename)) > MAX_IMAGE_BYTES:
                raise HTTPException(status_code=413, detail="图片不能超过 10 MB，请压缩后重新上传。")
            image_path = os.path.join(UPLOAD_DIR, selected_filename)

    history = [
        {"role": "assistant" if role == "ai" else "user", "content": content}
        for role, content in get_recent_messages(message.chat_id, 12)
        if role in {"user", "ai"}
    ]
    memory_context = _memory_context()

    return {
        "history": history,
        "filename": selected_filename,
        "image_path": image_path,
        "memory_context": memory_context,
    }


def _persist_chat(chat_id: int, question: str, result: dict) -> None:
    update_chat_title(chat_id, question[:15])
    save_message(chat_id, "user", question)
    save_message(chat_id, "ai", result["reply"])
    save_agent_run(chat_id, question, result)


@app.post("/chat")
def chat(message: Message):
    ctx = _chat_context(message)
    try:
        result = run_agent(
            message.text, history=ctx["history"], mode=message.mode,
            filename=ctx["filename"], image_path=ctx["image_path"],
            memory_context=ctx["memory_context"],
            allow_writes=message.allow_writes,
            allow_workspace=message.allow_workspace,
            allow_code_edit=message.allow_code_edit,
            allow_command=message.allow_command,
            show_thinking=message.show_thinking,
        )
        _persist_chat(message.chat_id, message.text, result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Agent 聊天接口失败 | chat_id=%s", message.chat_id)
        raise HTTPException(status_code=502, detail="AI 对话暂时不可用，请稍后重试。") from exc


def _sse(event: dict) -> str:
    """一条 SSE 帧。前端按 \\n\\n 切分，再取 data: 后面的 JSON。"""
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


@app.post("/chat/stream")
def chat_stream(message: Message):
    """流式聊天：边跑边推 token 和工具事件，可用 /chat/cancel/{run_id} 停止。

    请求校验放在返回响应之前，这样参数错误仍然是正常的 HTTP 4xx，而不是流里的一声报错。
    落库放在流末尾的 finally 里：前端中途断开也不会丢记录。
    """
    ctx = _chat_context(message)

    def event_stream():
        result = None
        try:
            for event in run_agent_stream(
                message.text, history=ctx["history"], mode=message.mode,
                filename=ctx["filename"], image_path=ctx["image_path"],
                memory_context=ctx["memory_context"],
                allow_writes=message.allow_writes,
                allow_workspace=message.allow_workspace,
                allow_code_edit=message.allow_code_edit,
                allow_command=message.allow_command,
                show_thinking=message.show_thinking,
                course_id=message.course_id,
                chapter_id=message.chapter_id,
            ):
                if event.get("type") == "done":
                    result = event
                yield _sse(event)
        except Exception:
            logger.exception("流式对话中断 | chat_id=%s", message.chat_id)
            yield _sse({"type": "error", "message": "流式对话中断，请重试。"})
            return
        finally:
            if result is not None:
                try:
                    _persist_chat(message.chat_id, message.text, result)
                except Exception:
                    logger.exception("保存流式对话记录失败 | chat_id=%s", message.chat_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        # 关掉 Nginx 等反向代理的缓冲，否则 token 会被攒成一坨再下发
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    })


@app.post("/chat/cancel/{run_id}")
def cancel_chat(run_id: str):
    request_cancel(run_id)
    return {"cancelled": True, "run_id": run_id}


class ApprovalPayload(BaseModel):
    approval_id: str = Field(..., min_length=4, max_length=64)
    allow: StrictBool = False


@app.post("/chat/approve/{run_id}")
def approve_command(run_id: str, payload: ApprovalPayload):
    """批准或拒绝一条等待确认的命令。

    审批按「运行号 + 审批号」双键匹配：拿别的运行的审批号点不动；超时后条目
    已从注册表移除，这里如实返回 404，而不是悄悄放行。
    """
    if not resolve_approval(run_id, payload.approval_id, bool(payload.allow)):
        raise HTTPException(status_code=404, detail="这条确认已失效（可能已超时或已被处理）")
    return {"ok": True, "allow": bool(payload.allow), "approval_id": payload.approval_id}


# ======================
# 模型与 API Key
# ======================

class ProviderPayload(BaseModel):
    provider: str = "custom"
    name: str = Field(..., min_length=1, max_length=40)
    base_url: str = Field(..., min_length=1, max_length=300)
    api_key: str = Field("", max_length=400)
    model: str = Field(..., min_length=1, max_length=120)
    vision_model: str = Field("", max_length=120)


class ProviderTestPayload(BaseModel):
    provider_id: int | None = None
    base_url: str = Field("", max_length=300)
    api_key: str = Field("", max_length=400)
    model: str = Field("", max_length=120)


def _clean_provider(data: ProviderPayload) -> dict:
    preset_id = data.provider if data.provider in model_store.PRESETS else "custom"
    preset = model_store.PRESETS[preset_id]
    base_url = (data.base_url or "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="接口地址必须以 http:// 或 https:// 开头。")
    return {
        "provider": preset_id,
        "name": (data.name or "").strip() or preset["name"],
        "base_url": base_url,
        "api_key": (data.api_key or "").strip(),
        "model": (data.model or "").strip(),
        "vision_model": (data.vision_model or "").strip(),
    }


@app.get("/models")
def list_models():
    return {
        "providers": model_store.list_providers(),
        "active": model_store.public_active(),
        "presets": model_store.presets(),
    }


@app.post("/models")
def create_model_provider(data: ProviderPayload):
    payload = _clean_provider(data)
    if not payload["api_key"]:
        raise HTTPException(status_code=400, detail="请填写 API Key。")
    row = model_store.create_provider(payload)
    logger.info("新增模型服务商 | id=%s | %s | %s", row["id"], row["name"], row["model"])
    return {"provider": model_store.public_provider(row), "active": model_store.public_active()}


@app.patch("/models/{provider_id}")
def update_model_provider(provider_id: int, data: ProviderPayload):
    payload = _clean_provider(data)
    row = model_store.update_provider(provider_id, payload)
    if row is None:
        raise HTTPException(status_code=404, detail="这个服务商不存在或已被删除。")
    logger.info("更新模型服务商 | id=%s | %s | %s", provider_id, row["name"], row["model"])
    return {"provider": model_store.public_provider(row), "active": model_store.public_active()}


@app.delete("/models/{provider_id}")
def delete_model_provider(provider_id: int):
    if not model_store.delete_provider(provider_id):
        raise HTTPException(status_code=404, detail="这个服务商不存在或已被删除。")
    return {"deleted": True, "active": model_store.public_active()}


@app.post("/models/{provider_id}/activate")
def activate_model_provider(provider_id: int):
    if not model_store.activate_provider(provider_id):
        raise HTTPException(status_code=404, detail="这个服务商不存在或已被删除。")
    active = model_store.public_active()
    logger.info("切换模型服务商 | id=%s | %s", provider_id, (active or {}).get("model"))
    return {"active": active}


@app.post("/models/test")
def test_model_provider(data: ProviderTestPayload):
    """测试连接。允许测还没保存的草稿；Key 留空时用已保存的那条。"""
    api_key = (data.api_key or "").strip()
    base_url = (data.base_url or "").strip()
    model = (data.model or "").strip()
    if data.provider_id:
        stored = model_store.get_provider(data.provider_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="这个服务商不存在或已被删除。")
        api_key = api_key or stored["api_key"]
        base_url = base_url or stored["base_url"]
        model = model or stored["model"]

    try:
        return test_connection(base_url, api_key, model)
    except ModelConfigError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:
        # 只回传一条精简的原因，避免把整段堆栈或请求细节丢给页面
        logger.warning("模型连接测试失败 | %s | %s", model or "-", type(exc).__name__)
        return {"ok": False, "message": f"连接失败：{str(exc)[:180]}"}


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

    filename = os.path.basename((file.filename or "upload.bin").replace("\\", "/"))

    extension = os.path.splitext(filename)[1].lower()
    if extension not in {".txt", ".pdf", ".docx", *IMAGE_MIME_TYPES}:
        raise HTTPException(status_code=415, detail="支持 TXT、PDF、DOCX、JPG、PNG 和 WEBP 文件。")

    content = await file.read(20 * 1024 * 1024 + 1)
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空。")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件不能超过 20 MB。")
    if extension in IMAGE_MIME_TYPES and len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="图片不能超过 10 MB。")

    path, duplicate = _store_uploaded_content(filename, content)

    return {

        "filename": os.path.basename(path),

        "duplicate": duplicate,

        "message": "检测到相同文件，已复用已有文件" if duplicate else "上传成功"

    }


# ======================
# 上传论文
# ======================

@app.post("/upload_paper")
async def upload_paper(file: UploadFile = File(...)):

    filename = os.path.basename((file.filename or "paper.pdf").replace("\\", "/"))

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="论文助手仅支持 PDF 文件。")

    content = await file.read(50 * 1024 * 1024 + 1)
    if not content:
        raise HTTPException(status_code=400, detail="PDF 文件内容为空。")
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="论文不能超过 50 MB。")

    digest = hashlib.sha256(content).hexdigest()

    # If this exact PDF is already the active index, reuse both the uploaded
    # file and its vectors instead of paying for embeddings again.
    if ensure_db_loaded() and _active_paper_matches(digest):
        with open(PAPER_SOURCE_PATH, "r", encoding="utf-8") as source_file:
            indexed_name = os.path.basename(json.load(source_file).get("name", filename))
        return {
            "message": "检测到相同论文，已复用已上传文件和解析索引",
            "filename": indexed_name,
            "chunks": get_paper_chunk_count(),
            "duplicate": True,
            "reused_index": True,
        }

    path, duplicate = _store_uploaded_content(filename, content)

    try:
        count = load_paper(path)
    except RuntimeError as exc:
        # load_paper 的 RuntimeError 都是能给用户看的说明（缺少 Embedding Key、扫描版 PDF 等）
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {

        "message": "检测到相同文件，已复用文件并重新建立论文索引" if duplicate else "论文解析完成",

        "filename": os.path.basename(path),

        "chunks": count,

        "duplicate": duplicate,

        "reused_index": False

    }


# ======================
# 论文问答
# ======================

@app.post("/paper_chat")
def paper_chat(data: PaperQuestion):
    return _run_legacy_agent(data.question, data.chat_id, mode="paper")


@app.get("/paper_status")
def paper_status():
    return {"ready": ensure_db_loaded()}


# ======================
# 文件列表
# ======================

@app.get("/files")
def files():

    return {

        "files": sorted(
            name for name in os.listdir(UPLOAD_DIR)
            if os.path.splitext(name)[1].lower() in {".txt", ".pdf", ".docx", *IMAGE_MIME_TYPES}
        )

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
    safe_name = os.path.basename(filename.replace("\\", "/"))
    if safe_name != filename.replace("\\", "/"):
        raise HTTPException(status_code=400, detail="文件名无效。")
    if not os.path.isfile(os.path.join(UPLOAD_DIR, safe_name)):
        raise HTTPException(status_code=404, detail="没有找到这份资料。")
    return _run_legacy_agent(question, chat_id, mode="file", filename=safe_name)


def _unique_upload_path(filename: str) -> str:
    stem, ext = os.path.splitext(filename)
    candidate = os.path.join(UPLOAD_DIR, filename)
    number = 2
    while os.path.exists(candidate):
        candidate = os.path.join(UPLOAD_DIR, f"{stem} ({number}){ext}")
        number += 1
    return candidate


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as uploaded_file:
        for block in iter(lambda: uploaded_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _find_existing_upload(content: bytes, extension: str) -> str | None:
    """Find byte-identical uploads while keeping existing filenames stable."""
    expected_size = len(content)
    expected_digest = hashlib.sha256(content).hexdigest()
    for name in sorted(os.listdir(UPLOAD_DIR)):
        path = os.path.join(UPLOAD_DIR, name)
        if os.path.splitext(name)[1].lower() != extension.lower() or not os.path.isfile(path):
            continue
        try:
            if os.path.getsize(path) == expected_size and _file_sha256(path) == expected_digest:
                return path
        except OSError:
            continue
    return None


def _store_uploaded_content(filename: str, content: bytes) -> tuple[str, bool]:
    existing = _find_existing_upload(content, os.path.splitext(filename)[1])
    if existing:
        return existing, True
    path = _unique_upload_path(filename)
    with open(path, "wb") as uploaded_file:
        uploaded_file.write(content)
    return path, False


def _active_paper_matches(digest: str) -> bool:
    try:
        with open(PAPER_SOURCE_PATH, "r", encoding="utf-8") as source_file:
            source_name = os.path.basename(json.load(source_file).get("name", ""))
        source_path = os.path.join(UPLOAD_DIR, source_name)
        return bool(source_name and os.path.isfile(source_path) and _file_sha256(source_path) == digest)
    except (OSError, ValueError, TypeError):
        return False


def _run_legacy_agent(question: str, chat_id: int | None, mode: str, filename: str | None = None):
    try:
        history = []
        result = run_agent(question, history=history, mode=mode, filename=filename,
                           memory_context=_memory_context())
        if chat_id is not None:
            update_chat_title(chat_id, question[:15])
            save_message(chat_id, "user", question)
            save_message(chat_id, "ai", result["reply"])
            save_agent_run(chat_id, question, result)
        return result
    except Exception as exc:
        logger.exception("兼容问答接口失败")
        raise HTTPException(status_code=502, detail="AI 对话暂时不可用，请稍后重试。") from exc


@app.get("/agent_runs/{run_id}")
def agent_run(run_id: str):
    result = get_agent_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="没有找到这次 Agent 运行记录。")
    return result


@app.get("/agent_runs")
def agent_runs(chat_id: int):
    return {"items": get_agent_runs_for_chat(chat_id)}


# ======================
# 代码改动的撤销
# ======================

@app.post("/code/undo/{run_id}")
def undo_code_edits(run_id: str):
    """把某次运行里的文件改动整体还原。

    依据 agent_steps 里记录的备份路径**逆序**还原（同一文件改两次时，先还后一次再还前一次，
    结果才是运行前的样子）；新建的文件直接删除。
    如果某个文件在这次改动之后又被改过，跳过并在 skipped 里说明，绝不覆盖用户后来的编辑。
    """
    record = get_agent_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="没有找到这次运行记录。")
    try:
        result = restore_edits(record["steps"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(
        "撤销代码改动 | run_id=%s | 还原 %d | 删除 %d | 跳过 %d",
        run_id, len(result["restored"]), len(result["removed"]), len(result["skipped"]),
    )
    if not result["changed"] and not result["skipped"]:
        return {**result, "message": "这次运行没有需要撤销的文件改动。"}
    return result


@app.get("/memories")
def memories():
    """记忆列表。顺带告诉前端「每轮只带最近几条」，界面上就能把机制说清楚，
    省得用户以为存进来 100 条就全都会被记住。"""
    items = [
        {"id": row[0], "content": row[1], "category": row[2], "created_at": row[3],
         "source": row[4] if len(row) > 4 else "user"}
        for row in list_memories(100)
    ]
    return {"items": items, "total": len(items), "inject_limit": MEMORY_INJECT_LIMIT, "cap": 100}


@app.post("/memories")
def create_memory(data: MemoryRequest):
    content = data.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="记忆内容不能为空。")
    memory_id = add_memory(content, data.category.strip() or "偏好")
    return {"id": memory_id, "message": "已保存到长期记忆。"}


@app.delete("/memories/{memory_id}")
def remove_memory(memory_id: int):
    if not delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="没有找到这条记忆。")
    return {"message": "记忆已删除。"}


@app.get("/learning_plans")
def learning_plans():
    items = []
    for row in list_learning_plans(50):
        tasks = _serialize_plan_tasks(row[0])
        completed = sum(task["status"] == "done" for task in tasks)
        items.append({
            "id": row[0], "title": row[1], "goal": row[2], "content": row[3],
            "status": row[4], "created_at": row[5], "tasks": tasks,
            "completed_tasks": completed, "total_tasks": len(tasks),
        })
    return {"items": items}


def _serialize_plan_tasks(plan_id: int):
    return [
        {"id": row[0], "title": row[1], "details": row[2], "stage": row[3], "status": row[4],
         "sort_order": row[5], "created_at": row[6], "completed_at": row[7]}
        for row in list_learning_tasks(plan_id)
    ]


def _extract_task_payload(text: str):
    """Read the model's JSON while keeping a useful fallback when it deviates."""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    candidates = [fenced.group(1).strip()] if fenced else []
    candidates.append(raw)
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            continue
        normalized = []
        for task in tasks[:21]:
            if not isinstance(task, dict):
                continue
            title = str(task.get("title", "")).strip()
            if title:
                normalized.append({
                    "title": title,
                    "details": str(task.get("details", "")).strip(),
                    "stage": str(task.get("stage", "")).strip(),
                })
        if normalized:
            overview = str(payload.get("overview", "")).strip()[:4000]
            title = str(payload.get("title", "")).strip()[:160]
            return title, overview, normalized
    return "", raw[:4000], []


def _generate_plan_payload(goal: str, context: str, completed_tasks=None, note: str = ""):
    completed_tasks = completed_tasks or []
    progress = "\n".join(
        f"- [{task['status']}] {task['stage']} {task['title']}" for task in completed_tasks
    ) or "暂无任务进度。"
    prompt = [
        {"role": "system", "content": "你是一位适合研究生新手的学习规划助手。请按用户目标生成现实、循序渐进、可检查的待办。只返回 JSON，不要 Markdown 或代码块。JSON 格式：{\"title\":\"计划标题\",\"overview\":\"简短说明\",\"tasks\":[{\"stage\":\"第 1 天\",\"title\":\"具体动作\",\"details\":\"完成标准或操作说明\"}]}。任务数量为 5 到 12 条，每条都必须可勾选完成。不要假设用户已掌握高级知识。"},
        {"role": "user", "content": f"学习目标：{goal}\n个人背景与记忆：\n{context or '暂未提供'}\n已有进度：\n{progress}\n调整说明：{note or '无'}"},
    ]
    response, _ = chat_completion(prompt, tools=None, timeout=35)
    content = getattr(response, "content", None) or ""
    title, overview, tasks = _extract_task_payload(content)
    if not tasks:
        tasks = [{"stage": "开始", "title": goal[:180], "details": "完成后在学习档案中勾选，并补充下一步学习目标。"}]
    return title, overview or "已为你创建可勾选的学习待办。", tasks


@app.post("/learning_plans/generate")
def generate_learning_plan(data: PlanRequest):
    goal = data.goal.strip()[:1000]
    if not goal:
        raise HTTPException(status_code=400, detail="请先写下学习目标。")
    context = "\n".join(f"[{row[2]}] {row[1]}" for row in list_memories(20))
    try:
        generated_title, content, tasks = _generate_plan_payload(goal, context)
    except ModelConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("生成学习计划失败")
        raise HTTPException(status_code=502, detail="学习计划暂时无法生成，请稍后重试。") from exc
    title = generated_title or "一周学习计划：" + goal[:60]
    plan_id = save_learning_plan(title, goal, content)
    created_tasks = add_learning_tasks(plan_id, tasks)
    return {"id": plan_id, "title": title, "goal": goal, "content": content, "status": "active", "tasks": created_tasks}


@app.post("/learning_plans/{plan_id}/tasks")
def create_learning_task(plan_id: int, data: TaskRequest):
    if not get_learning_plan(plan_id):
        raise HTTPException(status_code=404, detail="学习计划不存在。")
    tasks = add_learning_tasks(plan_id, [{"title": data.title, "details": data.details, "stage": data.stage}])
    if not tasks:
        raise HTTPException(status_code=400, detail="任务标题不能为空。")
    return tasks[0]


@app.patch("/learning_plans/{plan_id}/tasks/{task_id}")
def update_learning_task(plan_id: int, task_id: int, data: TaskStatusRequest):
    if not set_learning_task_status(plan_id, task_id, data.status):
        raise HTTPException(status_code=404, detail="任务不存在或状态不正确。")
    return {"message": "任务状态已更新。"}


@app.delete("/learning_plans/{plan_id}/tasks/{task_id}")
def remove_learning_task(plan_id: int, task_id: int):
    if not delete_learning_task(plan_id, task_id):
        raise HTTPException(status_code=404, detail="任务不存在。")
    return {"message": "任务已删除。"}


@app.post("/learning_plans/{plan_id}/adjust")
def adjust_learning_plan(plan_id: int, data: PlanAdjustmentRequest):
    plan = get_learning_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="学习计划不存在。")
    task_items = _serialize_plan_tasks(plan_id)
    context = "\n".join(f"[{row[2]}] {row[1]}" for row in list_memories(20))
    try:
        _, overview, tasks = _generate_plan_payload(plan[2], context, task_items, data.note.strip())
    except Exception as exc:
        logger.exception("调整学习计划失败")
        raise HTTPException(status_code=502, detail="暂时无法调整计划，请稍后重试。") from exc
    created = replace_unfinished_learning_tasks(plan_id, tasks)
    return {"message": "已保留完成任务，并替换未完成任务。", "overview": overview, "tasks": created}


@app.patch("/learning_plans/{plan_id}")
def update_learning_plan(plan_id: int, status: str):
    if not set_learning_plan_status(plan_id, status):
        raise HTTPException(status_code=404, detail="计划不存在或状态不正确。")
    return {"message": "学习计划状态已更新。"}


@app.delete("/learning_plans/{plan_id}")
def remove_learning_plan(plan_id: int):
    if not delete_learning_plan(plan_id):
        raise HTTPException(status_code=404, detail="没有找到这份学习计划。")
    return {"message": "学习计划已删除。"}


# ======================
# 前端页面（必须放在最后）
# ======================
# 把 frontend 目录挂在根路径上，这样：
#   /            -> index.html
#   /app.js      -> 前端脚本
#   /debug.html  -> 接口自检页
# 一个服务（8000 端口）就把网页和后端接口全包了，不需要另起前端服务。

# ======================
# 课程学习台
# ======================
# 课程正文是文件，进度是数据库。这几个接口只做搬运，不生成内容——
# 内容由 scripts/build_curriculum.py 离线生成、人工校对，运行时不再让模型现编，
# 否则同一门课每次打开目录都不一样。

class CourseProgressRequest(BaseModel):
    chapter_id: str = Field(..., min_length=2, max_length=32)
    status: str | None = None
    mastery: int | None = None
    notes: str | None = Field(None, max_length=4000)


@app.get("/course")
def get_course(course_id: str = course_lib.DEFAULT_COURSE):
    try:
        return course_lib.decorate(course_lib.load_course(course_id), course_id)
    except course_lib.CourseError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/course/chapter/{chapter_id}")
def get_course_chapter(chapter_id: str, course_id: str = course_lib.DEFAULT_COURSE):
    try:
        payload = course_lib.load_chapter(course_id, chapter_id)
        chapters = course_lib.load_course(course_id)["chapters"]
    except course_lib.CourseError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # 他可能直接点开了第 20 章，这里把「还差哪些前置」一起给他，前端画黄条
    payload["blocked_by"] = course_lib.blocked_by(chapters, payload["chapter"])
    return payload


@app.patch("/course/progress")
def update_course_progress(
    data: CourseProgressRequest, course_id: str = course_lib.DEFAULT_COURSE
):
    if data.status is not None and data.status not in {"todo", "learning", "done"}:
        raise HTTPException(status_code=400, detail="status 只能是 todo / learning / done")
    if data.mastery is not None and not 0 <= data.mastery <= 3:
        raise HTTPException(status_code=400, detail="mastery 只能是 0 到 3")
    try:
        course_lib.load_course(course_id)  # 先确认课程存在，别往库里写脏 id
    except course_lib.CourseError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    progress = set_course_progress(
        course_id, data.chapter_id,
        status=data.status, mastery=data.mastery, notes=data.notes,
    )
    decorated = course_lib.decorate(course_lib.load_course(course_id), course_id)
    return {"progress": progress, "summary": decorated["progress"], "next": decorated["next"]}


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
