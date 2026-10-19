import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"


def db_path() -> Path:
    return Path(os.environ.get("RECONCILE_DB_PATH", BASE_DIR / "data" / "reconcile.db"))
