import os

DATA_DIR = os.environ.get("SG_DATA_DIR", "./data")
ADMIN_PASSWORD = os.environ.get("SG_ADMIN_PASSWORD", "")
# The management API controls upstream keys and sees all traffic, so it fails closed: without SG_ADMIN_PASSWORD a random password is
# generated once, kept in the data volume and printed in the start-up log. Only SG_ALLOW_OPEN_ADMIN=1 (trusted dev machines) keeps it open.
ALLOW_OPEN_ADMIN = os.environ.get("SG_ALLOW_OPEN_ADMIN", "").lower() in ("1", "true", "yes")
GENERATED_ADMIN_PASSWORD = False


def ensure_admin_password() -> None:
    global ADMIN_PASSWORD, GENERATED_ADMIN_PASSWORD
    if ADMIN_PASSWORD or ALLOW_OPEN_ADMIN:
        return
    import secrets
    path = os.path.join(DATA_DIR, "admin_password")
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(18))
    with open(path) as f:
        ADMIN_PASSWORD = f.read().strip()
    GENERATED_ADMIN_PASSWORD = True
STATIC_DIR = os.environ.get("SG_STATIC_DIR", "./static")
DEFAULT_SLOTS = int(os.environ.get("SG_DEFAULT_SLOTS", "4"))
HOSTED_SLOTS = int(os.environ.get("SG_HOSTED_SLOTS", "8"))  # default concurrency per model on hosted catalogs (limits are not published)
VERSION = "2.1"
HEALTH_INTERVAL = float(os.environ.get("SG_HEALTH_INTERVAL", "30"))
LOG_RETENTION = int(os.environ.get("SG_LOG_RETENTION", "20000"))

CAPABILITIES = ["chat", "reasoning", "vision", "audio", "video", "ocr", "embedding", "tools", "tts", "image_gen"]
FALLBACK_GRACE = float(os.environ.get("SG_FALLBACK_GRACE", "3"))
STRATEGIES = ["least_busy", "weighted_round_robin", "priority_failover", "fastest", "random", "tiered"]
