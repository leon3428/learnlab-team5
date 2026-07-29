from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from learnlab_team5.app import create_app


def test_api_and_slash_containing_subject_id(dataset_dir: Path, tmp_path: Path) -> None:
    app = create_app(dataset_dir, index_path=tmp_path / "history.sqlite3")

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assignments = await client.get("/api/assignments")
            assert assignments.status_code == 200
            assert assignments.json()[0]["title"] == "Demonstration Lab"

            students = await client.get("/api/students", params={"assignment_id": "demoLab"})
            assert students.status_code == 200
            assert len(students.json()) == 2

            history = await client.get(
                "/api/history",
                params={"assignment_id": "demoLab", "subject_id": "student/one"},
            )
            assert history.status_code == 200
            assert history.json()["snapshot_count"] == 4

            first, second = history.json()["steps"][:2]
            snapshot = await client.get(
                "/api/snapshot",
                params={
                    "code_state_id": second["code_state_id"],
                    "previous_code_state_id": first["code_state_id"],
                },
            )
            assert snapshot.status_code == 200
            assert snapshot.json()["changed_lines"]

    asyncio.run(scenario())


def test_api_not_found_responses(dataset_dir: Path, tmp_path: Path) -> None:
    app = create_app(dataset_dir, index_path=tmp_path / "history.sqlite3")

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (
                await client.get("/api/students", params={"assignment_id": "missing"})
            ).status_code == 404
            assert (
                await client.get(
                    "/api/history",
                    params={"assignment_id": "demoLab", "subject_id": "missing"},
                )
            ).status_code == 404
            assert (
                await client.get("/api/snapshot", params={"code_state_id": "missing"})
            ).status_code == 404

    asyncio.run(scenario())


def test_frontend_assets_are_served(dataset_dir: Path, tmp_path: Path) -> None:
    app = create_app(dataset_dir, index_path=tmp_path / "history.sqlite3")

    async def scenario() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return (
                await client.get("/"),
                await client.get("/assets/app.js"),
                await client.get("/assets/styles.css"),
            )

    page, script, stylesheet = asyncio.run(scenario())
    assert page.status_code == script.status_code == stylesheet.status_code == 200
    assert "LearnLab History Explorer" in page.text
    assert "history-scrubber" in page.text
    assert "previous_code_state_id" in script.text
    assert ".timeline-step.is-active" in stylesheet.text
