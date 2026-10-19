"""Cache busting: hash every static file at startup and expose static_url() to templates."""

import hashlib
from pathlib import Path


class Assets:
    def __init__(self, static_dir: Path, prefix: str = "/static"):
        self.static_dir = static_dir
        self.prefix = prefix
        self.hashes: dict[str, str] = {}
        for path in sorted(static_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(static_dir).as_posix()
                self.hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
        combined = hashlib.sha256("".join(f"{k}:{v}" for k, v in self.hashes.items()).encode())
        # Changes whenever any static file changes; used as the service worker cache name.
        self.version = combined.hexdigest()[:10]

    def url(self, rel: str) -> str:
        rel = rel.lstrip("/")
        digest = self.hashes.get(rel)
        if digest is None:
            raise KeyError(f"Unknown static asset: {rel}")
        return f"{self.prefix}/{rel}?v={digest}"

    def precache_urls(self) -> list[str]:
        """Hashed URLs the service worker caches on install (skip large icon variants)."""
        return [self.url(rel) for rel in self.hashes if not rel.startswith("icons/") or rel.endswith(".svg")]
