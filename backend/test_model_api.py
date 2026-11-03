"""模型配置接口的回归测试：不联网，验证鉴权、增删改查、Key 不回传。

跑法：cd backend && ../.venv/Scripts/python.exe test_model_api.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402

DB = os.path.join(tempfile.gettempdir(), "ai_robot_model_api_test.db")
if os.path.exists(DB):
    os.remove(DB)
database.DB_PATH = DB
database.init_db()

import main  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

# main 导入时 config 会把 .env 里的 Key 读进环境，这里清掉以测「一条都没配」的状态
os.environ.pop("DASHSCOPE_API_KEY", None)

client = TestClient(main.app)


def check(name, condition, extra=""):
    if not condition:
        raise AssertionError(f"{name} 失败 {extra}")
    print("ok ", name)


# 1. 未登录不能读配置，否则 Key 提示和模型名谁都能看
check("未登录返回 401", client.get("/models").status_code == 401)

client.cookies.set(main.SESSION_COOKIE, main.create_session_token())

# 2. 空配置 + 预设列表
data = client.get("/models").json()
check("初始没有服务商", data["providers"] == [])
check("预设包含千问与 DeepSeek", {"qwen", "deepseek"} <= {item["id"] for item in data["presets"]})
check("未配置时 active 为空", data["active"] is None)

# 3. 新增（第一条自动激活）
qwen_payload = {
    "provider": "qwen", "name": "我的千问",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "sk-qwen-api-test-11112222", "model": "qwen-plus", "vision_model": "qwen3-vl-plus",
}
resp = client.post("/models", json=qwen_payload)
check("新增千问成功", resp.status_code == 200, resp.text[:120])
qwen_id = resp.json()["provider"]["id"]
check("千问自动成为当前模型", resp.json()["active"]["model"] == "qwen-plus")

deepseek_payload = {
    "provider": "deepseek", "name": "我的 DeepSeek",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "sk-deepseek-api-test-33334444", "model": "deepseek-chat", "vision_model": "",
}
resp = client.post("/models", json=deepseek_payload)
check("新增 DeepSeek 成功", resp.status_code == 200, resp.text[:120])
deepseek_id = resp.json()["provider"]["id"]
check("新增的不抢占激活位", resp.json()["active"]["model"] == "qwen-plus")

# 4. 任何响应里都不能出现明文 Key
listed = client.get("/models")
check("列表不含明文 Key", "11112222" not in listed.text and "sk-qwen-api-test" not in listed.text)
check("列表带后四位提示", listed.text.count("****") >= 2, listed.text[:200])

# 5. 切换
resp = client.post(f"/models/{deepseek_id}/activate")
check("切换接口返回新模型", resp.json()["active"]["model"] == "deepseek-chat")
active = [item for item in client.get("/models").json()["providers"] if item["is_active"]]
check("切换后只有一条处于使用中", len(active) == 1 and active[0]["id"] == deepseek_id)

# 6. 编辑时 Key 留空 = 保持原 Key
resp = client.patch(f"/models/{deepseek_id}", json={**deepseek_payload, "name": "DeepSeek V3", "api_key": ""})
check("编辑保留原 Key", resp.status_code == 200 and resp.json()["provider"]["key_hint"].endswith("4444"), resp.text[:160])
check("编辑改名生效", resp.json()["provider"]["name"] == "DeepSeek V3")

# 7. 参数校验
bad = client.post("/models", json={**qwen_payload, "base_url": "ftp://example.com", "api_key": "x"})
check("非法地址被拒", bad.status_code == 400, bad.text[:120])
missing_key = client.post("/models", json={**qwen_payload, "name": "无 Key", "api_key": ""})
check("缺 Key 被拒", missing_key.status_code == 400, missing_key.text[:120])

# 8. 连接测试（替换掉真实网络调用）
main.test_connection = lambda base_url, api_key, model, timeout=15.0: {
    "ok": True, "latency_ms": 12, "model": model, "sample": "pong", "message": "连接正常，耗时 12 ms",
}
resp = client.post("/models/test", json={"provider_id": deepseek_id, "base_url": "", "api_key": "", "model": ""})
check("用已保存的 Key 测试连接", resp.status_code == 200 and resp.json()["ok"] is True, resp.text[:160])
check("测试走的是已保存的模型", resp.json()["model"] == "deepseek-chat")

# 9. 删除正在使用的服务商，自动把剩下的一条顶上
resp = client.delete(f"/models/{deepseek_id}")
check("删除成功", resp.status_code == 200, resp.text[:120])
check("删除后自动接管", resp.json()["active"]["model"] == "qwen-plus", resp.text[:160])
check("删除不存在返回 404", client.delete("/models/9999").status_code == 404)

print("\n全部通过：鉴权、增删改查、Key 掩码、切换、参数校验、自动接管")
