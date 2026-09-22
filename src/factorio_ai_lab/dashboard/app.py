from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from factorio_ai_lab.dashboard.rendering import resolve_official_icon
from factorio_ai_lab.dashboard.state import DashboardState

STATIC_DIR = Path(__file__).resolve().parent / "static"
state = DashboardState()


async def _telemetry_loop() -> None:
    while True:
        await asyncio.to_thread(state.record_telemetry)
        await asyncio.sleep(2.0)


@asynccontextmanager
async def lifespan(_: FastAPI):
    telemetry_task = asyncio.create_task(_telemetry_loop())
    try:
        yield
    finally:
        telemetry_task.cancel()
        with suppress(asyncio.CancelledError):
            await telemetry_task
        state.close()


app = FastAPI(
    title="Factorio AI Lab Dashboard",
    version="0.11.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class RuntimePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_interval_s: float | None = None
    astar_turn_penalty: float | None = None
    llm_provider: str | None = None
    llm_temperature: float | None = None
    llm_max_tokens: int | None = None
    ucb_exploration: float | None = None
    learning_algorithm: str | None = None
    neural_policy: str | None = None
    world_model: str | None = None


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    return state.status()


@app.get("/api/world")
async def api_world() -> dict[str, Any]:
    return await asyncio.to_thread(state.factorio.snapshot)


@app.get("/api/history")
def api_history() -> list[dict[str, Any]]:
    return state.history_data()


@app.get("/api/production")
async def api_production(precision: str = "1m") -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            state.factorio.production_statistics,
            precision,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/factory-graph")
async def api_factory_graph() -> dict[str, Any]:
    world = await asyncio.to_thread(state.factorio.snapshot)
    return state.factory_graph_data(world=world)


@app.get("/api/autonomy")
async def api_autonomy() -> dict[str, Any]:
    world = await asyncio.to_thread(state.factorio.snapshot)
    production = await asyncio.to_thread(
        state.factorio.production_statistics,
        "5s",
    )
    return state.autonomy_data(
        world=world,
        research=state.research_data(),
        production=production,
    )


@app.get("/api/learning")
def api_learning() -> dict[str, Any]:
    return state.learning_data()


@app.get("/api/run")
def api_run() -> dict[str, Any]:
    return state.active_run_data()


@app.get("/api/research")
def api_research() -> dict[str, Any]:
    return state.research_data()


@app.get("/api/progression")
async def api_progression() -> dict[str, Any]:
    world = await asyncio.to_thread(state.factorio.snapshot)
    research = state.research_data()
    return state.engineering_progression_data(
        world=world,
        research=research,
    )


@app.get("/api/production-plan")
async def production_plan() -> dict:
    return state.production_plan_data()


@app.get("/api/resource-overview")
async def api_resource_overview() -> dict[str, Any]:
    return await asyncio.to_thread(state.factorio.resource_overview)


@app.get("/api/game-graph")
async def api_game_graph() -> dict[str, Any]:
    return await asyncio.to_thread(state.factorio.game_knowledge)


@app.get("/api/game-graph/summary")
async def api_game_graph_summary() -> dict[str, Any]:
    return await asyncio.to_thread(state.game_knowledge_summary_data)


@app.get("/api/knowledge")
def api_knowledge() -> dict[str, Any]:
    return state.knowledge_data()


@app.get("/api/evolution")
def api_evolution() -> dict[str, Any]:
    return state.evolution_data()


@app.get("/api/datasets")
def api_datasets() -> dict[str, Any]:
    return state.dataset_data()


@app.get("/api/assets/icon/{entity_name}.png")
def api_official_icon(entity_name: str) -> FileResponse:
    icon = resolve_official_icon(entity_name)
    if icon is None:
        raise HTTPException(404, "official Factorio icon unavailable")
    return FileResponse(
        icon,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Asset-Source": "Factorio official demo 2.0.73",
        },
    )


@app.get("/api/world/frame.png")
async def api_world_frame(
    mode: str = "game",
    cx: float | None = None,
    cy: float | None = None,
    radius: float | None = None,
) -> Response:
    if mode not in {"game", "overview", "tactical"}:
        raise HTTPException(400, "mode must be game, overview or tactical")
    supplied = [cx is not None, cy is not None, radius is not None]
    if any(supplied) and not all(supplied):
        raise HTTPException(400, "cx, cy and radius must be supplied together")
    if radius is not None and not 6.0 <= radius <= 96.0:
        raise HTTPException(400, "radius must be in [6, 96]")
    try:
        png = await asyncio.to_thread(
            state.render_world_frame,
            mode,
            center_x=cx,
            center_y=cy,
            radius=radius,
        )
    except Exception as exc:
        raise HTTPException(503, f"world renderer unavailable: {exc}") from exc
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "X-Frame-Source": "official-asset-world-map",
        },
    )


@app.get("/api/experiments/routing")
def api_routing() -> dict[str, Any]:
    return state.routing_sweep()


@app.get("/api/config")
def api_config() -> dict[str, Any]:
    return state.config.read()


@app.put("/api/config")
def api_update_config(patch: RuntimePatch) -> dict[str, Any]:
    changes = {
        key: value
        for key, value in patch.model_dump().items()
        if value is not None
    }
    if "poll_interval_s" in changes and not 0.25 <= changes["poll_interval_s"] <= 30:
        raise HTTPException(400, "poll_interval_s must be in [0.25, 30]")
    if "astar_turn_penalty" in changes and not 0 <= changes["astar_turn_penalty"] <= 20:
        raise HTTPException(400, "astar_turn_penalty must be in [0, 20]")
    if "llm_temperature" in changes and not 0 <= changes["llm_temperature"] <= 2:
        raise HTTPException(400, "llm_temperature must be in [0, 2]")
    if "llm_max_tokens" in changes and not 32 <= changes["llm_max_tokens"] <= 32768:
        raise HTTPException(400, "llm_max_tokens must be in [32, 32768]")
    if "ucb_exploration" in changes and not 0 <= changes["ucb_exploration"] <= 20:
        raise HTTPException(400, "ucb_exploration must be in [0, 20]")
    try:
        return state.config.update(changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            payload = await asyncio.to_thread(state.sample)
            await websocket.send_json(payload)
            interval = float(state.config.read().get("poll_interval_s", 1.5))
            await asyncio.sleep(max(0.25, min(interval, 30.0)))
    except WebSocketDisconnect:
        return
