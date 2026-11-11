"""项目配置：路径、检索关键词、筛选阈值。

所有可调参数优先从 .env 读取，未设置则用这里的默认值。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env() -> None:
    """读取项目根目录 .env（仅补全未设置的环境变量，不覆盖已有值）。"""
    p = BASE_DIR / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env()


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


# ---- 目标账号 ----
TARGET_UID = os.environ.get("WEIBO_UID", "3866429224").strip()
TARGET_SCREEN_NAME = os.environ.get("WEIBO_SCREEN_NAME", "比格钱钱").strip()
SNAPSHOT_CUTOFF = os.environ.get("SNAPSHOT_CUTOFF", "2026-11-09").strip()

# ---- 迷因关键词（命中即入选迷因合集）----
MEME_KEYWORDS = [
    "dsnmd",
    "DSNMD",
    "吃屎",
    "垃圾桶",
    "抢小孩零食",
    "抢零食",
]

# ---- 互动账号（正文或转发源中出现即判为互动）----
INTERACT_KEYWORDS = [
    "比格犬受害者联盟",
]

# ---- 高清图筛选：转评总数（转+评）> 阈值 ----
PHOTO_MIN_ENGAGEMENT = env_int("PHOTO_MIN_ENGAGEMENT", 100)

# ---- 反爬 ----
RATE_MIN_SEC = env_float("RATE_MIN_SEC", 4.0)
RATE_MAX_SEC = env_float("RATE_MAX_SEC", 8.0)
PAGES_PER_BREAK = env_int("PAGES_PER_BREAK", 30)
BREAK_MIN_SEC = env_int("BREAK_MIN_SEC", 120)
BREAK_MAX_SEC = env_int("BREAK_MAX_SEC", 300)
DAILY_MAX_REQUESTS = env_int("DAILY_MAX_REQUESTS", 500)
FETCH_LONGTEXT = os.environ.get("FETCH_LONGTEXT", "1") == "1"
FETCH_PIC_INFO = os.environ.get("FETCH_PIC_INFO", "1") == "1"

# ---- 公网视频代理 ----
# 默认开启按需刷新：仅当用户播放视频且直链过期时才请求微博接口。
ENABLE_VIDEO_REFRESH = os.environ.get("ENABLE_VIDEO_REFRESH", "1") == "1"

# ---- 存储 ----
_DB_RAW = os.environ.get("DB_PATH") or str(BASE_DIR / "data" / "weibo.db")
DB_PATH = Path(_DB_RAW)
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

_LEDGER_DB_RAW = os.environ.get("REQUEST_LEDGER_DB") or str(DB_PATH)
LEDGER_DB_PATH = Path(_LEDGER_DB_RAW)
if not LEDGER_DB_PATH.is_absolute():
    LEDGER_DB_PATH = BASE_DIR / LEDGER_DB_PATH

SAVE_RAW = os.environ.get("SAVE_RAW", "1") == "1"
