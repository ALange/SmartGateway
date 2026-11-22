import os

from fastapi.testclient import TestClient

from app import config, db
from app.main import app


def test_spa_never_serves_files_outside_static_dir(tmp_path):
    db.init_db()
    c = TestClient(app)
    if not os.path.isdir(config.STATIC_DIR):
        return  # SPA route only exists when a frontend build is present
    shell = c.get("/").content
    for p in ("/%2fetc/hostname", "/%2fetc%2fpasswd", "/%2e%2e/%2e%2e/etc/passwd", "/..%2f..%2fetc%2fpasswd", "/%2e%2e%2f%2e%2e%2fetc%2fpasswd"):
        r = c.get(p)
        assert r.content == shell, p
