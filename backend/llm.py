import os

from openai import OpenAI

from config import DASHSCOPE_API_KEY


MODEL = os.getenv("AGENT_MODEL", "qwen-plus")
VISION_MODEL = os.getenv("AGENT_VISION_MODEL", "qwen3-vl-plus")
BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
client = OpenAI(api_key=DASHSCOPE_API_KEY, base_url=BASE_URL, timeout=30.0, max_retries=0)


def _base_kwargs(
    messages: list[dict],
    tools: list[dict] | None,
    timeout: float,
    model: str | None,
):
    kwargs = {
        "model": model or MODEL,
        "messages": messages,
        "timeout": max(1.0, timeout),
        "max_tokens": max(256, min(int(os.getenv("AGENT_MAX_OUTPUT_TOKENS", "1800")), 4000)),
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    return kwargs


def chat_completion(
    messages: list[dict],
    tools: list[dict] | None = None,
    timeout: float = 30.0,
    model: str | None = None,
):
    """一次性返回整段回答。保留给不需要流式的调用方（例如学习计划生成）。"""
    response = client.chat.completions.create(
        **_base_kwargs(messages, tools, timeout, model)
    )
    return response.choices[0].message, response.usage


def chat_completion_stream(
    messages: list[dict],
    tools: list[dict] | None = None,
    timeout: float = 30.0,
    model: str | None = None,
    should_stop=None,
):
    """流式调用模型。

    逐段 yield ("token", 文本片段)，最后 yield 一次 ("message", payload)，其中
    payload = {"content", "tool_calls": [{"id","name","arguments"}], "usage", "cancelled"}。

    工具调用的参数是分片到达的，必须按 index 累加，不能只读某一帧。
    should_stop 返回 True 时立即关闭连接并返回已收到的内容（供「停止」按钮使用）。
    """

    kwargs = _base_kwargs(messages, tools, timeout, model)
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
    })
