from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config, db
from app.assets import Assets
from app.money import format_cents, plain_amount


class HashedStaticFiles(StaticFiles):
    """Long-lived caching for ?v=<hash> URLs; revalidate anything else."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if b"v=" in scope.get("query_string", b""):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


def _pretty_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def create_app(db_path: Path | str | None = None) -> FastAPI:
    path = db_path or config.db_path()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.connect(path)
        db.migrate(conn)
        app.state.conn = conn
        yield
        conn.close()

    app = FastAPI(title="Reconcile", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    assets = Assets(config.STATIC_DIR)
    templates = Jinja2Templates(directory=config.TEMPLATES_DIR)
    templates.env.globals.update(static_url=assets.url, asset_version=assets.version)
    templates.env.filters.update(money=format_cents, plain_amount=plain_amount, pretty_date=_pretty_date)
    app.state.assets = assets
    app.state.templates = templates

    app.mount("/static", HashedStaticFiles(directory=config.STATIC_DIR), name="static")

    from app.routes import export, pwa, reconcile, register, transactions

    for module in (register, transactions, reconcile, export, pwa):
        app.include_router(module.router)
    return app


app = create_app()
