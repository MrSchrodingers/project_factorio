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
from factorio_ai_lab.dashboard.sprites import (
    SpriteLibrary,
    TileLibrary,
    sprite_manifest,
    tile_manifest,
)
from factorio_ai_lab.dashboard.state import DashboardState, json_finite

STATIC_DIR = Path(__file__).resolve().parent / "static"
state = DashboardState()
sprites = SpriteLibrary()
tiles = TileLibrary()


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
class _VersionedStatic(StaticFiles):
    """Static files that are safe to cache only when the URL is versioned.

    The pages reference the bundle with a content hash (?v=<hash>, stamped by
    frontend/stamp.mjs), so a hashed URL can be cached hard. Without the hash
    the browser has to revalidate, otherwise a rebuild is invisible to anyone
    holding the previous copy - which is exactly what happened: the bundle was
    rebuilt repeatedly while phones kept serving a stale one from cache.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        query = scope.get("query_string", b"").decode("latin-1")
        if "v=" in query:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


app.mount("/static", _VersionedStatic(directory=STATIC_DIR), name="static")


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
    # The page carries the content hashes of everything else, so it is the one
    # file that must never be served stale.
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


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
async def production_plan() -> dict[str, Any]:
    # The plan is differential: what the world already holds decides which
    # steps are still missing, so the snapshot is read before planning. Both
    # reads are off-loop because both talk to RCON.
    world = await asyncio.to_thread(state.factorio.snapshot)
    return json_finite(
        await asyncio.to_thread(state.production_plan_data, world=world)
    )


@app.get("/api/machine-diagnostics")
async def api_machine_diagnostics() -> dict[str, Any]:
    return json_finite(await asyncio.to_thread(state.machine_diagnostics_data))


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


@app.get("/api/evolution/survival")
async def api_evolution_survival() -> dict[str, Any]:
    return json_finite(await asyncio.to_thread(state.survival_data))


@app.get("/api/evolution/discoveries")
async def api_evolution_discoveries() -> dict[str, Any]:
    return json_finite(await asyncio.to_thread(state.discovery_data))


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


@app.get("/api/assets/tiles.json")
def api_tile_manifest() -> dict[str, Any]:
    manifest = tile_manifest(tiles)
    manifest["library"] = tiles.status()
    return manifest


@app.get("/api/assets/tile/{family}/{kind}.png")
def api_ground_tile(family: str, kind: str, variant: int = 0) -> Response:
    if family not in {"terrain", "resource"}:
        raise HTTPException(400, "family must be terrain or resource")
    png = tiles.render(family, kind, variant)
    if png is None:
        raise HTTPException(404, "no local texture for this tile")
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
            "X-Asset-Source": "Factorio official graphics 2.0.73 (local only)",
        },
    )


@app.get("/api/assets/sprites.json")
def api_sprite_manifest() -> dict[str, Any]:
    manifest = sprite_manifest()
    manifest["strip_frames"] = SpriteLibrary.MAX_STRIP_FRAMES
    manifest["library"] = sprites.status()
    return manifest


@app.get("/api/assets/strip/{entity_name}.png")
def api_entity_strip(entity_name: str, direction: int = 0) -> Response:
    """Whole animation as one image, to keep the request count low."""
    result = sprites.strip(entity_name, direction)
    if result is None:
        raise HTTPException(404, "no verified sprite spec for this entity")
    png, frames = result
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
            "X-Frame-Count": str(frames),
            "X-Asset-Source": "Factorio official graphics 2.0.73 (local only)",
        },
    )


@app.get("/api/assets/sprite/{entity_name}.png")
def api_entity_sprite(
    entity_name: str,
    direction: int = 0,
    frame: int = 0,
) -> Response:
    png = sprites.render(entity_name, direction, frame)
    if png is None:
        raise HTTPException(404, "no verified sprite spec for this entity")
    return Response(
        content=png,
        media_type="image/png",
        headers={
            # Deterministic crop of a fixed asset: safe to cache hard.
            "Cache-Control": "public, max-age=86400, immutable",
            "X-Asset-Source": "Factorio official graphics 2.0.73 (local only)",
        },
    )


@app.get("/api/world/scene")
async def api_world_scene(
    cx: float | None = None,
    cy: float | None = None,
    radius: float | None = None,
) -> dict[str, Any]:
    supplied = [cx is not None, cy is not None, radius is not None]
    if any(supplied) and not all(supplied):
        raise HTTPException(400, "cx, cy and radius must be supplied together")
    if radius is not None and not 6.0 <= radius <= 96.0:
        raise HTTPException(400, "radius must be in [6, 96]")
    scene = await asyncio.to_thread(
        state.world_scene,
        center_x=cx,
        center_y=cy,
        radius=radius,
    )
    return json_finite(scene)


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
            await websocket.send_json(json_finite(payload))
            interval = float(state.config.read().get("poll_interval_s", 1.5))
            await asyncio.sleep(max(0.25, min(interval, 30.0)))
    except WebSocketDisconnect:
        return
