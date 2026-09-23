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
    ASSET_ROOT,
    ENTITY_DIR,
    FLE_RESOURCE_DIR,
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
        self._strip_cache: dict[tuple[str, int, int], tuple[bytes, int]] = {}
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



    # Frames served one request at a time saturate the browser's per-origin
    # connection limit: a dozen machines at 32 frames each is hundreds of
    # requests, which starves the ground textures and makes the map flicker
    # between textured and flat while it loads. A strip is one request.
    MAX_STRIP_FRAMES = 12

    def strip(self, name: str, direction: int) -> tuple[bytes, int] | None:
        """All frames of one animation, side by side, plus the frame count.

        Long animations are sampled down to MAX_STRIP_FRAMES evenly spaced
        frames: past roughly a dozen the motion is indistinguishable at map
        zoom, and the bytes are not.
        """
        spec = SPRITE_SPECS.get(name)
        if spec is None:
            return None
        cardinal = _cardinal_direction(direction)
        key = (name, cardinal, -1)
        with self._lock:
            cached = self._strip_cache.get(key)
            if cached is not None:
                return cached

        total = max(1, spec.frames)
        count = min(total, self.MAX_STRIP_FRAMES)
        indices = [round(i * total / count) % total for i in range(count)]

        frames: list[Image.Image] = []
        for index in indices:
            payload = self.render(name, cardinal, index)
            if payload is None:
                return None
            frames.append(Image.open(BytesIO(payload)).convert("RGBA"))

        width = frames[0].width
        height = frames[0].height
        sheet = Image.new("RGBA", (width * len(frames), height), (0, 0, 0, 0))
        for position, frame in enumerate(frames):
            sheet.alpha_composite(frame, (position * width, 0))
        buffer = BytesIO()
        sheet.save(buffer, format="PNG", optimize=True)
        result = (buffer.getvalue(), len(frames))
        with self._lock:
            if len(self._strip_cache) >= 64:
                self._strip_cache.clear()
            self._strip_cache[key] = result
        return result

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

# --------------------------------------------------------------- ground tiles

TERRAIN_SHEETS: dict[str, str] = {
    "grass": "terrain/grass-1.png",
    "grass-2": "terrain/grass-2.png",
    "dirt": "terrain/dirt-1.png",
    "dry-dirt": "terrain/dry-dirt.png",
    "water": "terrain/water/water1.png",
    "deepwater": "terrain/deepwater/deepwater1.png",
    "concrete": "terrain/concrete/concrete.png",
    "refined-concrete": "terrain/concrete/refined-concrete.png",
    "stone-path": "terrain/stone-path/stone-path-1.png",
}

# Resource sprites live in the FLE spritemap tree, as a grid of 32px tiles
# whose columns run from a sparse scattering to a dense pile. Picking the
# column by richness is how the game itself shows a patch depleting.
RESOURCE_SHEETS: dict[str, str] = {
    "iron-ore": "iron-ore/iron-ore.png",
    "copper-ore": "copper-ore/copper-ore.png",
    "coal": "coal/coal.png",
    "stone": "stone/stone.png",
    "crude-oil": "crude-oil/crude-oil.png",
}

TILE_PIXELS = 32


class TileLibrary:
    """Single 32px tiles cut from the official terrain and resource sheets.

    The map used to paint terrain and ore as flat coloured rectangles, which
    is legible but reads as a diagram rather than as the game. These are the
    real textures, served one tile at a time so the browser can cache each
    variant forever.
    """

    def __init__(self, max_entries: int = 256) -> None:
        self._cache: dict[tuple[str, str, int], bytes] = {}
        self._lock = Lock()
        self._max_entries = max_entries

    def status(self) -> dict[str, Any]:
        return {
            "terrain_kinds": sorted(TERRAIN_SHEETS),
            "resource_kinds": sorted(RESOURCE_SHEETS),
            "cached_tiles": len(self._cache),
        }

    @staticmethod
    def _sheet_path(family: str, kind: str) -> Path | None:
        if family == "terrain":
            relative = TERRAIN_SHEETS.get(kind)
            return ASSET_ROOT / relative if relative else None
        if family == "resource":
            relative = RESOURCE_SHEETS.get(kind)
            return FLE_RESOURCE_DIR / relative if relative else None
        return None

    def variants(self, family: str, kind: str) -> int:
        """How many distinct tiles the sheet offers on its first row."""
        path = self._sheet_path(family, kind)
        if path is None or not path.is_file():
            return 0
        try:
            with Image.open(path) as sheet:
                return max(1, sheet.width // TILE_PIXELS)
        except (OSError, ValueError):
            return 0

    def render(self, family: str, kind: str, variant: int) -> bytes | None:
        path = self._sheet_path(family, kind)
        if path is None or not path.is_file():
            return None
        key = (family, kind, variant)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        try:
            with Image.open(path) as sheet:
                image = sheet.convert("RGBA")
                columns = max(1, image.width // TILE_PIXELS)
                column = max(0, variant) % columns
                # Only row 0 is used: later rows are edge transitions, which
                # would show as seams when repeated across open ground.
                crop = image.crop((
                    column * TILE_PIXELS,
                    0,
                    (column + 1) * TILE_PIXELS,
                    TILE_PIXELS,
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


def tile_manifest(library: TileLibrary) -> dict[str, Any]:
    terrain = {
        kind: library.variants("terrain", kind) for kind in sorted(TERRAIN_SHEETS)
    }
    resources = {
        kind: library.variants("resource", kind) for kind in sorted(RESOURCE_SHEETS)
    }
    return {
        "tile_pixels": TILE_PIXELS,
        "terrain": {k: v for k, v in terrain.items() if v > 0},
        "resources": {k: v for k, v in resources.items() if v > 0},
    }
