"""模型服务商配置：API Key 的保存、切换与解析。

三条约定：
1. Key 只存本机 SQLite（backend/chats.db，已被 .gitignore 排除），不写日志、不回传前端；
   接口只返回「后四位」这种提示，避免截图或分享页面时泄露。
2. 同一时刻只有一个 is_active=1。对话、论文、学习计划全部跟随它，
   所以「切换 API」就是改这一行，不用重启服务。
3. 表里只存用户配置过的服务商。第一次启动时用 .env 里的 DASHSCOPE_API_KEY
   播种一条「通义千问（.env）」，保证老配置升级后不用重新填 Key。
"""

from __future__ import annotations

import os
import sqlite3
import time

import database

PROVIDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_providers(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL DEFAULT 'custom',
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key TEXT NOT NULL,
    model TEXT NOT NULL,
    vision_model TEXT DEFAULT '',
    is_active INTEGER DEFAULT 0,
    created_at TEXT
)
"""

# 预设只作为「填表助手」：选中后自动带出接口地址和默认模型，用户仍可改。
# base_url 一律填到 OpenAI 兼容层的 /v1（智谱是 /v4），这是各家文档给的标准地址。
PRESETS: dict[str, dict] = {
    "qwen": {
        "name": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "vision_model": "qwen3-vl-plus",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen3-max"],
        "hint": "在阿里云百炼控制台创建 API Key，可用模型还包括 qwen3 系列。",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "vision_model": "",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "hint": "deepseek-chat 支持工具调用；deepseek-reasoner 不支持，需要检索时会自动降级为纯对话。",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "vision_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
        "hint": "需要可访问 OpenAI 接口的网络环境。",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "vision_model": "glm-4v-plus",
        "models": ["glm-4-flash", "glm-4-plus", "glm-4v-plus"],
        "hint": "智谱的 OpenAI 兼容地址以 /v4 结尾，不要手动改成 /v1。",
    },
    "moonshot": {
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "vision_model": "moonshot-v1-8k-vision-preview",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "kimi-k2-0905-preview"],
        "hint": "Kimi 的视觉模型需要单独开通，不填也能正常对话。",
    },
    "custom": {
        "name": "自定义（OpenAI 兼容）",
        "base_url": "",
        "model": "",
        "vision_model": "",
        "models": [],
        "hint": "任何兼容 OpenAI 协议的网关（vLLM、Ollama、OpenRouter、中转站）都填在这里。",
    },
}


def presets() -> list[dict]:
    return [{"id": key, **value} for key, value in PRESETS.items()]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_ready() -> None:
    """建表 + 首次播种。幂等，可以在任意入口反复调用。"""
    conn = _connect()
    try:
        conn.execute(PROVIDER_SCHEMA)
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM model_providers").fetchone()[0]
        if count == 0:
            _seed_from_env(conn)
            conn.commit()
    finally:
        conn.close()


def _seed_from_env(conn: sqlite3.Connection) -> None:
    api_key = (os.getenv("DASHSCOPE_API_KEY") or "").strip()
    if not api_key:
        return
    preset = PRESETS["qwen"]
    conn.execute(
        "INSERT INTO model_providers(provider,name,base_url,api_key,model,vision_model,is_active,created_at)"
        " VALUES(?,?,?,?,?,?,1,?)",
        (
            "qwen",
            "通义千问（.env）",
            os.getenv("DASHSCOPE_BASE_URL", preset["base_url"]),
            api_key,
            os.getenv("AGENT_MODEL", preset["model"]),
            os.getenv("AGENT_VISION_MODEL", preset["vision_model"]),
            _now(),
        ),
    )


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def mask_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:3] + "****" + key[-4:]


def _public(row: sqlite3.Row) -> dict:
    preset = PRESETS.get(row["provider"] or "custom", PRESETS["custom"])
    return {
        "id": row["id"],
        "provider": row["provider"],
        "preset_name": preset["name"],
        "name": row["name"],
        "base_url": row["base_url"],
        "model": row["model"],
        "vision_model": row["vision_model"] or "",
        "is_active": bool(row["is_active"]),
        "key_hint": mask_key(row["api_key"]),
        "has_key": bool(row["api_key"]),
        "created_at": row["created_at"],
    }


def public_provider(provider: dict) -> dict:
    """把内部行转成可以安全返回给前端的结构（Key 只留后四位）。"""
    return _public(provider)


def list_providers() -> list[dict]:
    ensure_ready()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM model_providers ORDER BY is_active DESC, id ASC"
        ).fetchall()
        return [_public(row) for row in rows]
    finally:
        conn.close()


def get_provider(provider_id: int) -> dict | None:
    """内部使用，包含明文 Key。绝不要直接把返回值交给接口层。"""
    ensure_ready()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM model_providers WHERE id=?", (provider_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def active_provider() -> dict | None:
    ensure_ready()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM model_providers WHERE is_active=1 ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM model_providers ORDER BY id ASC LIMIT 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def vision_capable_provider(exclude_id: int | None = None) -> dict | None:
    ensure_ready()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM model_providers WHERE COALESCE(vision_model,'')<>'' AND id<>?"
            " ORDER BY is_active DESC, id ASC LIMIT 1",
            (exclude_id or -1,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def public_active() -> dict | None:
    provider = active_provider()
    if not provider:
        return None
    preset = PRESETS.get(provider["provider"] or "custom", PRESETS["custom"])
    return {
        "id": provider["id"],
        "name": provider["name"],
        "provider": provider["provider"],
        "preset_name": preset["name"],
        "model": provider["model"],
        "vision_model": provider["vision_model"] or "",
        "key_hint": mask_key(provider["api_key"]),
    }


def create_provider(data: dict) -> dict:
    ensure_ready()
    conn = _connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM model_providers").fetchone()[0]
        cursor = conn.execute(
            "INSERT INTO model_providers(provider,name,base_url,api_key,model,vision_model,is_active,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (
                data["provider"],
                data["name"],
                data["base_url"],
                data["api_key"],
                data["model"],
                data.get("vision_model") or "",
                1 if count == 0 else 0,
                _now(),
            ),
        )
        conn.commit()
        provider_id = cursor.lastrowid
    finally:
        conn.close()
    return get_provider(provider_id)


def update_provider(provider_id: int, data: dict) -> dict | None:
    ensure_ready()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM model_providers WHERE id=?", (provider_id,)).fetchone()
        if row is None:
            return None
        # 前端编辑时 Key 输入框是空的（只回显后四位提示），空值代表「不改原来的 Key」
        api_key = (data.get("api_key") or "").strip() or row["api_key"]
        conn.execute(
            "UPDATE model_providers SET provider=?,name=?,base_url=?,api_key=?,model=?,vision_model=?"
            " WHERE id=?",
            (
                data["provider"],
                data["name"],
                data["base_url"],
                api_key,
                data["model"],
                data.get("vision_model") or "",
                provider_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_provider(provider_id)


def delete_provider(provider_id: int) -> bool:
    ensure_ready()
    conn = _connect()
    try:
        row = conn.execute("SELECT is_active FROM model_providers WHERE id=?", (provider_id,)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM model_providers WHERE id=?", (provider_id,))
        # 删掉的正好是当前在用的，就把最早添加的那条顶上来，避免全站突然没模型可用
        if row["is_active"]:
            next_row = conn.execute("SELECT id FROM model_providers ORDER BY id ASC LIMIT 1").fetchone()
            if next_row:
                conn.execute("UPDATE model_providers SET is_active=1 WHERE id=?", (next_row["id"],))
        conn.commit()
        return True
    finally:
        conn.close()


def activate_provider(provider_id: int) -> bool:
    ensure_ready()
    conn = _connect()
    try:
        exists = conn.execute("SELECT 1 FROM model_providers WHERE id=?", (provider_id,)).fetchone()
        if not exists:
            return False
        conn.execute("UPDATE model_providers SET is_active=0")
        conn.execute("UPDATE model_providers SET is_active=1 WHERE id=?", (provider_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def embedding_api_key() -> str | None:
    """论文索引用的 DashScope Embedding Key。

    切到 DeepSeek 之类不提供 Embedding 的服务商时，仍然可以从
    .env 或任意一条通义千问配置里取到 Key；都没有就返回 None，由调用方给出明确提示。
    """
    env_key = (os.getenv("DASHSCOPE_API_KEY") or "").strip()
    if env_key:
        return env_key
    ensure_ready()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT api_key FROM model_providers WHERE provider='qwen' AND api_key<>'' ORDER BY is_active DESC, id ASC LIMIT 1"
        ).fetchone()
        return row["api_key"] if row else None
    finally:
        conn.close()
