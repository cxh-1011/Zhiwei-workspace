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

from agent import run_agent, run_agent_stream, request_cancel
from llm import chat_completion

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
    save_learning_plan, list_learning_plans, set_learning_plan_status,
    delete_learning_plan, get_learning_plan, add_learning_tasks, list_learning_tasks,
    set_learning_task_status, delete_learning_task, replace_unfinished_learning_tasks,
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

    text: str = Field(..., min_length=1, max_length=12000)

    chat_id: int = Field(..., gt=0)
    mode: str = "chat"
    filename: str | None = None
    # 是否允许助手写入学习档案（长期记忆 / 勾选任务）。默认关闭。
    # 用 StrictBool：只接受真正的 true/false，避免 "yes"、1 之类的值被宽松转成真。
    allow_writes: StrictBool = False
    # 是否允许助手只读访问工作区代码（list_dir / read_file / grep_code）。默认关闭。
    allow_workspace: StrictBool = False


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

def _chat_context(message: Message) -> dict:
    """校验请求并准备上下文。/chat 与 /chat/stream 共用，保证两条路径行为一致。"""
    if message.mode not in {"chat", "file", "paper"}:
        raise HTTPException(status_code=400, detail="未知的对话模式。")
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
    memories = list_memories(20)
    memory_context = "\n".join(f"[{row[2]}] {row[1]}" for row in memories)
    active_plans = [row for row in list_learning_plans(10) if row[4] == "active"][:3]
    if active_plans:
        memory_context += "\n当前学习计划：\n" + "\n".join(f"- {row[1]}：{row[2]}" for row in active_plans)

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

    count = load_paper(path)

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
        memories = list_memories(20)
        context = "\n".join(f"[{row[2]}] {row[1]}" for row in memories)
        result = run_agent(question, history=history, mode=mode, filename=filename, memory_context=context)
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


@app.get("/memories")
def memories():
    return {"items": [
        {"id": row[0], "content": row[1], "category": row[2], "created_at": row[3],
         "source": row[4] if len(row) > 4 else "user"}
        for row in list_memories(100)
    ]}


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
