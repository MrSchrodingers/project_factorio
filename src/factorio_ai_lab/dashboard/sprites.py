"""Frame-accurate sprite extraction from the local Factorio graphics.

Every spec here divides its sheet exactly (``sheet % frame == 0``) and was
checked against a rendered contact sheet, because a grid that merely divides
can still straddle two animation frames. The previous inline specs in
``rendering.py`` carried one arithmetically impossible entry
(``steam-engine-V`` at 165x391 against a 1800x1564 sheet, i.e. 10.909
columns), which cropped the engine across frame boundaries.

Entities without a spec fall back to the official inventory icon, drawn by
the client at the prototype's real tile footprint.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Any

from PIL import Image

from factorio_ai_lab.dashboard.rendering import (
    ENTITY_DIR,
    _cardinal_direction,
    resolve_official_icon,
)

# Factorio direction constants for the four cardinals (2.0 uses 16 steps).
NORTH, EAST, SOUTH, WEST = 0, 4, 8, 12

_DIRECTION_SUFFIX = {NORTH: "N", EAST: "E", SOUTH: "S", WEST: "W"}


@dataclass(frozen=True)
class SpriteSpec:
    """How to cut one animation frame out of an official sheet.

    ``layout`` says where the direction lives:
      * ``"file"``   - one sheet per direction, ``{d}`` in the path
      * ``"row"``    - direction selects the row, animation the column
      * ``"grid"``   - a single directionless animation, row-major
      * ``"single"`` - the whole file is one frame
    """

    path: str
    frame_width: int
    frame_height: int
    columns: int
    rows: int
    layout: str
    frames: int = 1
    direction_rows: dict[int, int] | None = None
    direction_files: dict[int, str] | None = None
    # Direction -> filename token. Sheets are not uniformly named: boilers use
    # N/E/S/W, offshore pumps use North/East/..., steam engines only have a
    # horizontal and a vertical sheet.
    direction_tokens: dict[int, str] | None = None
    ticks_per_frame: int = 4
    scale: float = 1.0


# Verified against the real sheets; see docs/sprite-specs.md for the audit.
SPRITE_SPECS: dict[str, SpriteSpec] = {
    "transport-belt": SpriteSpec(
        path="transport-belt/transport-belt.png",
        frame_width=128,
        frame_height=128,
        columns=16,
        rows=20,
        layout="row",
        frames=16,
        direction_rows={NORTH: 2, EAST: 0, SOUTH: 3, WEST: 1},
        ticks_per_frame=2,
        scale=1.18,
    ),
    "burner-mining-drill": SpriteSpec(
        path="burner-mining-drill/burner-mining-drill-{d}.png",
        frame_width=0,  # per-direction, see direction_files
        frame_height=0,
        columns=4,
        rows=8,
        layout="file",
        frames=32,
        direction_files={
            NORTH: "173x188",
            EAST: "185x168",
            SOUTH: "174x174",
            WEST: "180x176",
        },
        ticks_per_frame=8,
    ),
    "electric-mining-drill": SpriteSpec(
        path="electric-mining-drill/electric-mining-drill.png",
        frame_width=162,
        frame_height=156,
        columns=6,
        rows=5,
        layout="grid",
        frames=30,
        ticks_per_frame=4,
    ),
    "assembling-machine-1": SpriteSpec(
        path="assembling-machine-1/assembling-machine-1.png",
        frame_width=214,
        frame_height=226,
        columns=8,
        rows=4,
        layout="grid",
        frames=32,
        ticks_per_frame=3,
    ),
    "lab": SpriteSpec(
        path="lab/lab.png",
        frame_width=194,
        frame_height=174,
        columns=11,
        rows=3,
        layout="grid",
        frames=33,
        ticks_per_frame=4,
    ),
    "small-electric-pole": SpriteSpec(
        path="small-electric-pole/small-electric-pole.png",
        frame_width=72,
        frame_height=220,
        columns=4,
        rows=1,
        layout="row",
        frames=1,
        direction_rows={NORTH: 0, EAST: 0, SOUTH: 0, WEST: 0},
    ),
    "steam-engine": SpriteSpec(
        path="steam-engine/steam-engine-{d}.png",
        frame_width=0,
        frame_height=0,
        columns=8,
        rows=4,
        layout="file",
        frames=32,
        # Horizontal sheet for E/W, vertical for N/S. The vertical frame is
        # 225 wide, not 165: 1800/165 is not an integer.
        direction_files={
            NORTH: "225x391",
            SOUTH: "225x391",
            EAST: "352x257",
            WEST: "352x257",
        },
        direction_tokens={NORTH: "V", SOUTH: "V", EAST: "H", WEST: "H"},
        ticks_per_frame=3,
    ),
    "offshore-pump": SpriteSpec(
        path="offshore-pump/offshore-pump_{d}.png",
        frame_width=0,
        frame_height=0,
        columns=8,
        rows=4,
        layout="file",
        frames=32,
        # Measured per sheet: East/West 992x408, North 720x648, South 736x768,
        # all 8 columns by 4 rows.
        direction_files={
            NORTH: "90x162",
            EAST: "124x102",
            SOUTH: "92x192",
            WEST: "124x102",
        },
        direction_tokens={
            NORTH: "North",
            EAST: "East",
            SOUTH: "South",
            WEST: "West",
        },
        ticks_per_frame=3,
    ),
    "boiler": SpriteSpec(
        path="boiler/boiler-{d}-idle.png",
        frame_width=0,
        frame_height=0,
        columns=1,
        rows=1,
        layout="file",
        frames=1,
        # Single idle frame per direction; each sheet has its own size.
        direction_files={
            NORTH: "269x221",
            EAST: "216x301",
            SOUTH: "260x192",
            WEST: "196x273",
        },
    ),
    "stone-furnace": SpriteSpec(
        path="stone-furnace/stone-furnace.png",
        frame_width=151,
        frame_height=146,
        columns=1,
        rows=1,
        layout="single",
    ),
    "wooden-chest": SpriteSpec(
        path="wooden-chest/wooden-chest.png",
        frame_width=62,
        frame_height=72,
        columns=1,
        rows=1,
        layout="single",
    ),
    "iron-chest": SpriteSpec(
        path="iron-chest/iron-chest.png",
        frame_width=66,
        frame_height=76,
        columns=1,
        rows=1,
        layout="single",
    ),
}

class SpriteLibrary:
    """Cuts and caches one PNG frame per (entity, direction, frame)."""

    def __init__(self, max_entries: int = 512) -> None:
        self._cache: dict[tuple[str, int, int], bytes] = {}
        self._lock = Lock()
        self._max_entries = max_entries

    def available(self) -> list[str]:
        return sorted(SPRITE_SPECS)

    def status(self) -> dict[str, Any]:
        return {
            "sprite_entities": len(SPRITE_SPECS),
            "cached_frames": len(self._cache),
            "entity_dir": str(ENTITY_DIR),
        }

    def frame_count(self, name: str) -> int:
        spec = SPRITE_SPECS.get(name)
        return spec.frames if spec else 1

    def _resolve_path(self, spec: SpriteSpec, direction: int) -> Path | None:
        if spec.layout != "file":
            return ENTITY_DIR / spec.path
        tokens = spec.direction_tokens or _DIRECTION_SUFFIX
        token = tokens.get(direction, _DIRECTION_SUFFIX[direction])
        return ENTITY_DIR / spec.path.replace("{d}", token)

    @staticmethod
    def _frame_size(spec: SpriteSpec, direction: int) -> tuple[int, int]:
        if spec.layout == "file" and spec.direction_files:
            width, _, height = spec.direction_files[direction].partition("x")
            return int(width), int(height)
        return spec.frame_width, spec.frame_height

    def render(self, name: str, direction: int, frame: int) -> bytes | None:
        """Return one PNG frame, or None when there is no verified spec."""
        spec = SPRITE_SPECS.get(name)
        if spec is None:
            return None
        cardinal = _cardinal_direction(direction)
        index = max(0, frame) % max(1, spec.frames)
        key = (name, cardinal, index)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached

        path = self._resolve_path(spec, cardinal)
        if path is None or not path.is_file():
            return None
        frame_w, frame_h = self._frame_size(spec, cardinal)
        if frame_w <= 0 or frame_h <= 0:
            return None

        try:
            with Image.open(path) as sheet:
                image = sheet.convert("RGBA")
                if image.width % frame_w or image.height % frame_h:
                    # Refuse to crop across frame boundaries.
                    return None
                columns = image.width // frame_w
                if spec.layout == "single":
                    crop = image.copy()
                elif spec.layout == "row":
                    row = (spec.direction_rows or {}).get(cardinal, 0)
                    column = index % columns
                    crop = image.crop((
                        column * frame_w,
                        row * frame_h,
                        (column + 1) * frame_w,
                        (row + 1) * frame_h,
                    ))
                else:
                    column = index % columns
                    row = index // columns
                    crop = image.crop((
                        column * frame_w,
                        row * frame_h,
                        (column + 1) * frame_w,
                        (row + 1) * frame_h,
                    ))
        except (OSError, ValueError):
            return None

        buffer = BytesIO()
        crop.save(buffer, format="PNG", optimize=True)
        payload = buffer.getvalue()
        with self._lock:
            if len(self._cache) >= self._max_entries:
                self._cache.clear()
            self._cache[key] = payload
        return payload

    @staticmethod
    def icon_path(name: str) -> Path | None:
        return resolve_official_icon(name)


def sprite_manifest() -> dict[str, Any]:
    """What the client needs to know to request and place each sprite."""
    entries: dict[str, Any] = {}
    for name, spec in SPRITE_SPECS.items():
        entries[name] = {
            "frames": spec.frames,
            "ticks_per_frame": spec.ticks_per_frame,
            "directional": spec.layout in {"file", "row"},
            "scale": spec.scale,
        }
    return {"sprites": entries, "count": len(entries)}
