import os
from dotenv import load_dotenv

load_dotenv()

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
APP_PASSWORD_HASH = os.getenv("APP_PASSWORD_HASH")
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY")
APP_COOKIE_SECURE = os.getenv("APP_COOKIE_SECURE", "false").lower() == "true"

if not DASHSCOPE_API_KEY:
    raise ValueError("没有找到 DASHSCOPE_API_KEY，请检查 .env 文件")

if not APP_PASSWORD_HASH or not APP_SECRET_KEY:
    raise ValueError("登录验证尚未配置，请在 backend/.env 中设置 APP_PASSWORD_HASH 和 APP_SECRET_KEY")
