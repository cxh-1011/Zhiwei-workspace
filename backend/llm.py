import os

from openai import OpenAI

from config import DASHSCOPE_API_KEY


MODEL = os.getenv("AGENT_MODEL", "qwen-plus")
VISION_MODEL = os.getenv("AGENT_VISION_MODEL", "qwen3-vl-plus")
BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
client = OpenAI(api_key=DASHSCOPE_API_KEY, base_url=BASE_URL, timeout=30.0, max_retries=0)


def chat_completion(
    messages: list[dict],
    tools: list[dict] | None = None,
    timeout: float = 30.0,
    model: str | None = None,
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
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message, response.usage
