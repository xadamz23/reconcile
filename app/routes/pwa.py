import json

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app.routes.deps import render

router = APIRouter()

NO_CACHE = {"Cache-Control": "no-cache"}


@router.get("/sw.js")
def service_worker(request: Request):
    assets = request.app.state.assets
    body = request.app.state.templates.get_template("sw.js").render(
        version=assets.version,
        precache=json.dumps(assets.precache_urls() + ["/offline"]),
    )
    return Response(body, media_type="text/javascript", headers={**NO_CACHE, "Service-Worker-Allowed": "/"})


@router.get("/manifest.webmanifest")
def manifest(request: Request):
    url = request.app.state.assets.url
    data = {
        "name": "Reconcile",
        "short_name": "Reconcile",
        "description": "Check register for reconciling a checking account",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f4efe4",
        "theme_color": "#f4efe4",
        "icons": [
            {"src": url("icons/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": url("icons/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": url("icons/maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return Response(json.dumps(data), media_type="application/manifest+json", headers=NO_CACHE)


@router.get("/favicon.ico")
def favicon(request: Request):
    return RedirectResponse(request.app.state.assets.url("icons/favicon.ico"), status_code=301)


@router.get("/offline")
def offline(request: Request):
    return render(request, "offline.html")
