"""GA家园系统 · 路径与运行常量（唯一入口，勿散落硬编码）"""
import os
from pathlib import Path

# 目录
SERVER_DIR = Path(__file__).resolve().parent.parent   # server/
PROJECT_ROOT = SERVER_DIR.parent                       # GA家园系统/
ASSETS_ROOT = PROJECT_ROOT / "恋与深空故事素材"          # 只读红线：代码层禁写
WEB_ROOT = PROJECT_ROOT / "web"                          # M1 静态便签墙

# 数据库（可用环境变量 HOMESTEAD_DB 覆盖，测试/多实例用）
DB_PATH = Path(os.environ.get("HOMESTEAD_DB", SERVER_DIR / "homestead.db"))
SCHEMA_PATH = SERVER_DIR / "schema.sql"

# 服务
HOST = "127.0.0.1"
PORT = 7842            # 开发方案 §5 拍板，Tailscale 手机通道
