from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from factorio_ai_lab.dashboard.state import DashboardState

STATIC_DIR = Path(__file__).resolve().parent / "static"
state = DashboardState()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    state.close()


app = FastAPI(
    title="Factorio AI Lab Dashboard",
    version="0.2.0",
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


@app.get("/api/learning")
def api_learning() -> dict[str, Any]:
    return state.learning_data()


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
