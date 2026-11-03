import os

from dotenv import load_dotenv

load_dotenv()

# 模型的 Key 不再是启动的硬性条件：可以在页面右上角「模型与 API Key」里添加。
# 这里仍然读取，仅用于首次启动时把老配置播种成一条默认服务商（见 model_store）。
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
APP_PASSWORD_HASH = os.getenv("APP_PASSWORD_HASH")
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY")
APP_COOKIE_SECURE = os.getenv("APP_COOKIE_SECURE", "false").lower() == "true"

if not APP_PASSWORD_HASH or not APP_SECRET_KEY:
    raise ValueError("登录验证尚未配置，请在 backend/.env 中设置 APP_PASSWORD_HASH 和 APP_SECRET_KEY")
