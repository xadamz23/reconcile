from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
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


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_cross_site(request: Request) -> bool:
    """CSRF guard: true when a browser says this request came from another site.

    Auth lives in Cloudflare Access, so the app must not trust a request just
    because it carries the Access cookie. Clients that send neither header
    (curl, tests) aren't browsers and can't be driven by a malicious page.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site not in ("same-origin", "none")
    origin = request.headers.get("origin")
    if origin is not None:
        return urlsplit(origin).netloc != request.headers.get("host")
    return False


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

    @app.middleware("http")
    async def reject_cross_site_writes(request: Request, call_next):
        if request.method not in SAFE_METHODS and is_cross_site(request):
            return PlainTextResponse("Cross-site request blocked", status_code=403)
        return await call_next(request)

    app.mount("/static", HashedStaticFiles(directory=config.STATIC_DIR), name="static")

    from app.routes import export, pwa, reconcile, register, spending, transactions

    for module in (register, transactions, reconcile, spending, export, pwa):
        app.include_router(module.router)
    return app


app = create_app()
