"""Serve the built React Command Center from FastAPI (one origin, one Render service).

Routing contract:

* ``/api/*``                -> the API. Unknown API paths return a JSON 404 for every method,
                               never the SPA's HTML.
* ``/docs``, ``/redoc``, ``/openapi.json`` -> FastAPI's own docs (registered first, so they win).
* ``/assets/*``             -> hashed Vite bundles (long-lived cache).
* ``/<file>`` in dist       -> that file (favicon, samples, ...), path-traversal safe.
* anything else (GET/HEAD)  -> ``index.html`` so client-side routes such as
                               ``/checkout/return`` survive a refresh.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

_RESERVED_PREFIXES = ("docs", "redoc", "openapi.json")
_ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


class _ImmutableStaticFiles(StaticFiles):
    """Vite's ``/assets`` filenames carry a content hash, so they can be cached forever."""

    async def get_response(self, path: str, scope: dict) -> Response:  # type: ignore[override]
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def mount_spa(app: FastAPI, dist: Path, *, api_prefix: str) -> None:
    dist = dist.resolve()
    index = dist / "index.html"
    api_root = api_prefix.strip("/").split("/", 1)[0] or "api"  # "/api/v1" -> "api"

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", _ImmutableStaticFiles(directory=assets), name="spa-assets")

    async def _unknown_api_route() -> None:
        raise HTTPException(status_code=404, detail="Not Found")

    # Both "/api" and "/api/<anything>": otherwise a POST to an unknown API path would hit the
    # GET-only SPA route below and get a misleading 405 (or HTML) instead of a JSON 404.
    for path in (f"/{api_root}", f"/{api_root}/{{rest:path}}"):
        app.add_api_route(path, _unknown_api_route, methods=_ALL_METHODS, include_in_schema=False)

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def _spa(full_path: str) -> Response:
        first = full_path.split("/", 1)[0]
        if first == api_root or first in _RESERVED_PREFIXES:
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            candidate = (dist / full_path).resolve()
            # Only files that really live inside dist; "../" tricks fall through to index.html.
            if candidate.is_relative_to(dist) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
