import os

DATA_DIR = os.environ.get("SG_DATA_DIR", "./data")
ADMIN_PASSWORD = os.environ.get("SG_ADMIN_PASSWORD", "")
STATIC_DIR = os.environ.get("SG_STATIC_DIR", "./static")
DEFAULT_SLOTS = int(os.environ.get("SG_DEFAULT_SLOTS", "4"))
HOSTED_SLOTS = int(os.environ.get("SG_HOSTED_SLOTS", "8"))  # default concurrency per model on hosted catalogs (limits are not published)
VERSION = "2.1"
HEALTH_INTERVAL = float(os.environ.get("SG_HEALTH_INTERVAL", "30"))
LOG_RETENTION = int(os.environ.get("SG_LOG_RETENTION", "20000"))

CAPABILITIES = ["chat", "reasoning", "vision", "audio", "video", "ocr", "embedding", "tools", "tts", "image_gen"]
FALLBACK_GRACE = float(os.environ.get("SG_FALLBACK_GRACE", "3"))
STRATEGIES = ["least_busy", "weighted_round_robin", "priority_failover", "fastest", "random", "tiered"]
