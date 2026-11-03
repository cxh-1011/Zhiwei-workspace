"""模型服务商切换的回归测试：不联网，用假客户端验证解析与降级逻辑。

跑法：cd backend && ../.venv/Scripts/python.exe test_providers.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402

DB = os.path.join(tempfile.gettempdir(), "ai_robot_provider_test.db")
if os.path.exists(DB):
    os.remove(DB)
database.DB_PATH = DB
database.init_db()

import llm  # noqa: E402
import model_store  # noqa: E402
import agent  # noqa: E402

# 上面的模块会触发 config.load_dotenv()，把 .env 里的 Key 读回环境变量；
# 这里显式清掉，才能测到「一条都没配」的冷启动状态。
os.environ.pop("DASHSCOPE_API_KEY", None)


def check(name, condition, extra=""):
    if not condition:
        raise AssertionError(f"{name} 失败 {extra}")
    print("ok ", name)


# 1. 一条都没配时，错误要说清「去哪里配」
check("空表初始状态", model_store.list_providers() == [])
try:
    llm.resolve_provider()
except llm.ModelConfigError as exc:
    check("未配置时的提示指向设置面板", "模型" in str(exc), str(exc))
else:
    raise AssertionError("空配置应当报错")

# 2. 新增两个服务商，第一个自动激活
qwen = model_store.create_provider({
    "provider": "qwen", "name": "我的千问",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "sk-qwen-test-12345678", "model": "qwen-plus", "vision_model": "qwen3-vl-plus",
})
deepseek = model_store.create_provider({
    "provider": "deepseek", "name": "我的 DeepSeek",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "sk-deepseek-test-87654321", "model": "deepseek-chat", "vision_model": "",
})
check("第一条默认激活", qwen["is_active"] == 1 and deepseek["is_active"] == 0)

public = model_store.list_providers()
check("Key 不外泄", all("api_key" not in item for item in public))
check("Key 只显示后四位", public[0]["key_hint"].endswith("5678"), public[0]["key_hint"])

# 3. 切换后，解析结果立刻跟着变
model_store.activate_provider(deepseek["id"])
check("切换到 DeepSeek", llm.resolve_provider()["model"] == "deepseek-chat")

# 4. 图片请求自动挑有视觉模型的那家，而不是拿 DeepSeek 硬跑
vision = llm.resolve_provider(need_vision=True)
check("图片请求自动降级到千问", vision["vision_model"] == "qwen3-vl-plus", vision["name"])

# 5. 编辑时 Key 留空 = 不修改原来的 Key
updated = model_store.update_provider(deepseek["id"], {
    "provider": "deepseek", "name": "DeepSeek V3",
    "base_url": "https://api.deepseek.com/v1", "api_key": "",
    "model": "deepseek-chat", "vision_model": "",
})
check("空 Key 保留原值", updated["api_key"] == "sk-deepseek-test-87654321")
check("改名生效", updated["name"] == "DeepSeek V3")

# 6. 只有不支持视觉的服务商时，图片请求要明确报错
model_store.delete_provider(qwen["id"])
try:
    llm.resolve_provider(need_vision=True)
except llm.ModelConfigError as exc:
    check("无视觉模型时明确报错", "图片" in str(exc), str(exc))
else:
    raise AssertionError("没有视觉模型时应当报错")

# 7. 删掉正在用的服务商，会自动把剩下的一条顶上
qwen2 = model_store.create_provider({
    "provider": "qwen", "name": "千问备用",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "sk-qwen-backup-9999", "model": "qwen-max", "vision_model": "qwen3-vl-plus",
})
model_store.delete_provider(deepseek["id"])  # deepseek 是当前激活项
active = model_store.active_provider()
check("删除激活项后自动接管", active is not None and active["name"] == "千问备用")

# 8. 全链路：Agent 真的把当前服务商的模型名发给了客户端
captured = {}


class _Delta:
    content = "好的"
    tool_calls = None


class _Choice:
    delta = _Delta()


class _Chunk:
    choices = [_Choice()]
    usage = None


class _Stream:
    def __iter__(self):
        yield _Chunk()

    def close(self):
        pass


class _Completions:
    def create(self, **kwargs):
        captured.update(kwargs)
        return _Stream()


class _FakeClient:
    class chat:
        completions = _Completions()


class _FakeOpenAI:
    def __init__(self, **kwargs):
        captured["client"] = kwargs


def _fake_client_for(provider):
    _FakeOpenAI(api_key=provider["api_key"], base_url=provider["base_url"])
    return _FakeClient()


llm._client_for = _fake_client_for

result = agent.run_agent("你好", mode="chat")
check("Agent 使用了当前服务商的模型", captured.get("model") == "qwen-max", str(captured.get("model")))
check("客户端指向当前服务商地址", "dashscope" in captured["client"]["base_url"], str(captured["client"]))
check("运行结果记录模型", result.get("model") == "qwen-max", str(result.get("model")))
check("运行结果记录服务商", result.get("provider") == "千问备用", str(result.get("provider")))
check("回答正常返回", result["reply"] == "好的", result["reply"])

# 9. reasoner 这类不支持工具的模型，不能把 tools 发过去炸 400
check("不支持工具的模型被识别", llm._supports_tools("deepseek-reasoner") is False)
check("普通模型仍带工具", llm._supports_tools("deepseek-chat") is True)

print("\n全部通过：服务商切换、Key 掩码、视觉降级、自动接管、模型透传")
