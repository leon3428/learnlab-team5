"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

from .database import Repository, ensure_index

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    data_dir: Path | str,
    *,
    rebuild_index: bool = False,
    index_path: Path | str | None = None,
) -> FastAPI:
    index = ensure_index(data_dir, force=rebuild_index, index_path=index_path)
    repository = Repository(index.path)

    app = FastAPI(
        title="LearnLab Development History Explorer",
        docs_url=None,
        redoc_url=None,
    )
    app.state.repository = repository
    app.state.index_result = index
    assets = {
        "index.html": (STATIC_DIR / "index.html").read_bytes(),
        "styles.css": (STATIC_DIR / "styles.css").read_bytes(),
        "app.js": (STATIC_DIR / "app.js").read_bytes(),
    }

    @app.get("/", include_in_schema=False)
    async def index_page() -> Response:
        return Response(assets["index.html"], media_type="text/html")

    @app.get("/assets/{asset_name}", include_in_schema=False)
    async def static_asset(asset_name: str) -> Response:
        media_types = {
            "styles.css": "text/css",
            "app.js": "text/javascript",
        }
        if asset_name not in media_types:
            raise HTTPException(status_code=404, detail="Asset not found")
        return Response(assets[asset_name], media_type=media_types[asset_name])

    @app.get("/api/assignments")
    async def assignments() -> list[dict]:
        return repository.assignments()

    @app.get("/api/students")
    async def students(
        assignment_id: str = Query(min_length=1),
        q: str = Query(default="", max_length=200),
    ) -> list[dict]:
        if not repository.assignment_exists(assignment_id):
            raise HTTPException(status_code=404, detail="Assignment not found")
        return repository.students(assignment_id, q)

    @app.get("/api/history")
    async def history(
        assignment_id: str = Query(min_length=1),
        subject_id: str = Query(min_length=1),
    ) -> dict:
        steps = repository.history(assignment_id, subject_id)
        if not steps:
            raise HTTPException(status_code=404, detail="Student history not found")
        return {
            "assignment_id": assignment_id,
            "subject_id": subject_id,
            "snapshot_count": len(steps),
            "steps": steps,
        }

    @app.get("/api/snapshot")
    async def snapshot(
        code_state_id: str = Query(min_length=1),
        previous_code_state_id: str | None = Query(default=None),
    ) -> dict:
        result = repository.snapshot(code_state_id, previous_code_state_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Code state not found")
        return result

    return app
