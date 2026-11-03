"""模型调用层。

服务商（接口地址 + API Key + 模型名）全部来自 model_store，不再写死在模块里：
- 每次调用时解析「当前激活的服务商」，所以在页面上切换后，下一条消息立刻生效；
- 客户端按 (base_url, api_key) 缓存，切换来回切不会反复建连接；
- 图片问答会自动挑一个配了视觉模型的服务商，没有就明确报错，而不是丢给模型去猜。
"""

from __future__ import annotations

import logging
import os
import time

from openai import OpenAI

import model_store

logger = logging.getLogger("ai_robot")

# 仅作为 .env 播种时的默认值；真正调用以当前激活的服务商配置为准
MODEL = os.getenv("AGENT_MODEL", "qwen-plus")
VISION_MODEL = os.getenv("AGENT_VISION_MODEL", "qwen3-vl-plus")

# 这几个模型不支持 function calling，带工具会直接 400，这里提前剥掉并降级为纯对话
NO_TOOL_HINTS = ("reasoner", "thinking", "o1-mini", "o1-preview", "o1-", "o3-")


class ModelConfigError(RuntimeError):
    """配置类错误（没填 Key、模型不支持图片等）。这类错误要原样告诉用户，不能吞成 502。"""


_clients: dict[tuple[str, str], OpenAI] = {}
MAX_CACHED_CLIENTS = 8


def _client_for(provider: dict) -> OpenAI:
    base_url = (provider.get("base_url") or "").strip()
    api_key = (provider.get("api_key") or "").strip()
    if not base_url or not api_key:
        raise ModelConfigError("服务商配置不完整：接口地址和 API Key 都不能为空。")
    cache_key = (base_url, api_key)
    client = _clients.get(cache_key)
    if client is None:
        if len(_clients) >= MAX_CACHED_CLIENTS:
            _clients.clear()
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=30.0, max_retries=0)
        _clients[cache_key] = client
    return client


def resolve_provider(need_vision: bool = False, provider_id: int | None = None) -> dict:
    """返回本次请求该用的服务商（含明文 Key）。"""
    provider = (
        model_store.get_provider(provider_id) if provider_id
        else model_store.active_provider()
    )
    if not provider:
        raise ModelConfigError(
            "还没有配置模型 API Key。请点右上角的模型标签，添加一个服务商（通义千问、DeepSeek 等）。"
        )

    if need_vision and not (provider.get("vision_model") or "").strip():
        fallback = model_store.vision_capable_provider(exclude_id=provider["id"])
        if fallback is None:
            raise ModelConfigError(
                f"当前模型「{provider['name']} · {provider['model']}」不支持图片输入。"
                "请切换到支持视觉的模型（例如通义千问），或在该服务商下填写视觉模型。"
            )
        logger.info(
            "当前服务商不支持视觉，图片请求改用 %s · %s",
            fallback["name"], fallback["vision_model"],
        )
        provider = fallback

    if not (provider.get("api_key") or "").strip():
        raise ModelConfigError(f"服务商「{provider['name']}」还没有填写 API Key。")
    return provider


def describe(provider: dict) -> dict:
    """给前端/运行记录用的精简信息，不含 Key。"""
    return {
        "id": provider.get("id"),
        "name": provider.get("name"),
        "provider": provider.get("provider"),
        "model": provider.get("model"),
        "vision_model": provider.get("vision_model") or "",
    }


def _supports_tools(model: str) -> bool:
    lowered = (model or "").lower()
    return not any(hint in lowered for hint in NO_TOOL_HINTS)


def _base_kwargs(
    messages: list[dict],
    tools: list[dict] | None,
    timeout: float,
    model: str,
):
    kwargs = {
        "model": model,
        "messages": messages,
        "timeout": max(1.0, timeout),
        "max_tokens": max(256, min(int(os.getenv("AGENT_MAX_OUTPUT_TOKENS", "1800")), 4000)),
    }
    if tools:
        if _supports_tools(model):
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        else:
            logger.warning("模型 %s 不支持工具调用，本次按纯对话处理", model)
    return kwargs


def chat_completion(
    messages: list[dict],
    tools: list[dict] | None = None,
    timeout: float = 30.0,
    model: str | None = None,
    provider_id: int | None = None,
):
    """一次性返回整段回答。保留给不需要流式的调用方（例如学习计划生成）。"""
    provider = resolve_provider(provider_id=provider_id)
    selected = model or provider["model"]
    response = _client_for(provider).chat.completions.create(
        **_base_kwargs(messages, tools, timeout, selected)
    )
    return response.choices[0].message, response.usage


def chat_completion_stream(
    messages: list[dict],
    tools: list[dict] | None = None,
    timeout: float = 30.0,
    model: str | None = None,
    should_stop=None,
    provider_id: int | None = None,
    require_vision: bool = False,
):
    """流式调用模型。

    逐段 yield ("token", 文本片段)，最后 yield 一次 ("message", payload)，其中
    payload = {"content", "tool_calls": [{"id","name","arguments"}], "usage", "cancelled",
               "model", "provider"}。

    工具调用的参数是分片到达的，必须按 index 累加，不能只读某一帧。
    should_stop 返回 True 时立即关闭连接并返回已收到的内容（供「停止」按钮使用）。
    require_vision=True 时优先使用配了视觉模型的服务商（图片问答）。
    """

    provider = resolve_provider(need_vision=require_vision, provider_id=provider_id)
    selected = model or (provider["vision_model"] if require_vision else provider["model"])
    if not selected:
        raise ModelConfigError(f"服务商「{provider['name']}」还没有填写模型名称。")

    client = _client_for(provider)
    kwargs = _base_kwargs(messages, tools, timeout, selected)
    kwargs["stream"] = True

    try:
        stream = client.chat.completions.create(
            stream_options={"include_usage": True}, **kwargs
        )
    except Exception:
        # 部分兼容端点不支持 stream_options，退回普通流式（此时 usage 可能为 0）。
        stream = client.chat.completions.create(**kwargs)

    content_parts: list[str] = []
    calls: dict[int, dict] = {}
    total_tokens = 0
    cancelled = False

    try:
        for chunk in stream:
            if should_stop is not None and should_stop():
                cancelled = True
                break

            usage = getattr(chunk, "usage", None)
            if usage is not None:
                total_tokens = int(getattr(usage, "total_tokens", 0) or 0)

            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue

            text = getattr(delta, "content", None)
            if text:
                content_parts.append(text)
                yield ("token", text)

            for call in getattr(delta, "tool_calls", None) or []:
                index = getattr(call, "index", 0)
                index = 0 if index is None else int(index)
                slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if getattr(call, "id", None) and not slot["id"]:
                    slot["id"] = call.id
                function = getattr(call, "function", None)
                if function is not None:
                    if getattr(function, "name", None) and not slot["name"]:
                        slot["name"] = function.name
                    if getattr(function, "arguments", None):
                        slot["arguments"] += function.arguments
    finally:
        try:
            stream.close()
        except Exception:
            pass

    yield ("message", {
        "content": "".join(content_parts),
        "tool_calls": [calls[index] for index in sorted(calls)],
        "usage": total_tokens,
        "cancelled": cancelled,
        "model": selected,
        "provider": describe(provider),
    })


def test_connection(
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 15.0,
) -> dict:
    """用一次极短的对话验证 Key / 地址 / 模型名是否匹配。

    故意不用 /models 列表接口：不少中转网关没实现它，但对话是可用的。
    """
    base_url = (base_url or "").strip()
    api_key = (api_key or "").strip()
    model = (model or "").strip()
    if not base_url or not api_key or not model:
        raise ModelConfigError("测试连接需要接口地址、API Key 和模型名都填写完整。")

    client = _client_for({"base_url": base_url, "api_key": api_key})
    started = time.monotonic()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=8,
        timeout=max(3.0, timeout),
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    content = ""
    try:
        content = (response.choices[0].message.content or "").strip()
    except (AttributeError, IndexError):
        pass
    return {
        "ok": True,
        "latency_ms": elapsed_ms,
        "model": model,
        "sample": content[:60],
        "message": f"连接正常，耗时 {elapsed_ms} ms",
    }
