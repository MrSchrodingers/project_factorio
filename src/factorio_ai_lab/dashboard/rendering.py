from __future__ import annotations

import hashlib
import math
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

ASSET_ROOT = Path(
    "/home/ti/.local/share/factorio-ai/assets/demo-2.0.73/factorio/data/base/graphics"
)
ICON_DIR = ASSET_ROOT / "icons"
TERRAIN_GRASS = ASSET_ROOT / "terrain/grass-2.png"
TERRAIN_DIRT = ASSET_ROOT / "terrain/dirt-1.png"
FLE_ICON_DIR = Path("/home/ti/.fle/spritemaps/__base__/graphics/icons")

ICON_ALIASES = {
    "character": "light-armor",
    "entity-ghost": "blueprint",
}

RESOURCE_COLORS = {
    "iron-ore": (92, 123, 150, 215),
    "copper-ore": (190, 108, 62, 215),
    "coal": (42, 43, 46, 220),
    "stone": (167, 150, 116, 220),
    "uranium-ore": (83, 166, 73, 220),
    "crude-oil": (36, 32, 29, 230),
}


def _safe_name(name: str) -> bool:
    return bool(name) and all(
        character in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in name
    )


def resolve_official_icon(entity_name: str) -> Path | None:
    name = ICON_ALIASES.get(entity_name, entity_name)
    if not _safe_name(name):
        return None
    for root in (ICON_DIR, FLE_ICON_DIR):
        candidate = root / f"{name}.png"
        try:
            resolved = candidate.resolve()
            root_resolved = root.resolve()
        except OSError:
            continue
        if root_resolved not in resolved.parents:
            continue
        if resolved.is_file():
            return resolved
    return None


def official_asset_status() -> dict[str, Any]:
    try:
        icon_count = sum(1 for _ in ICON_DIR.rglob("*.png"))
    except OSError:
        icon_count = 0
    try:
        runtime_png_count = sum(1 for _ in FLE_ICON_DIR.glob("*.png"))
    except OSError:
        runtime_png_count = 0
    return {
        "ready": icon_count > 0,
        "icon_count": icon_count,
        "runtime_icon_count": runtime_png_count,
        "icon_dir": str(ICON_DIR),
        "source": "Factorio 2.0.73 official Linux demo (runtime only)",
        "version": "2.0.73",
        "redistributed": False,
    }


def _position(item: dict[str, Any]) -> tuple[float, float] | None:
    raw = item.get("position")
    if not isinstance(raw, dict):
        return None
    try:
        return float(raw["x"]), float(raw["y"])
    except (KeyError, TypeError, ValueError):
        return None


class WorldFrameRenderer:
    """Raster world view using locally installed official Factorio art assets."""

    def __init__(self, viewport_radius: float = 42.0) -> None:
        self.viewport_radius = viewport_radius
        self._lock = threading.Lock()
        self._cache_key: tuple[Any, ...] | None = None
        self._cache_png: bytes | None = None
        self._last_error: str | None = None
        self._icon_cache: dict[tuple[str, int], Image.Image] = {}
        self._terrain_cache: dict[str, Image.Image] = {}
        self._asset_count_checked_at = 0.0
        self._asset_count = 0

    def _asset_count_now(self) -> int:
        now = time.monotonic()
        if now - self._asset_count_checked_at < 5:
            return self._asset_count
        status = official_asset_status()
        self._asset_count = int(status["icon_count"])
        self._asset_count_checked_at = now
        return self._asset_count

    def status(self) -> dict[str, Any]:
        assets = official_asset_status()
        return {
            "ready": bool(assets["ready"]),
            "renderer": "official-asset-world-map",
            "asset_count": self._asset_count_now(),
            "sprite_count": self._asset_count_now(),
            "viewport_radius": self.viewport_radius,
            "last_error": self._last_error,
            "official_assets": assets,
        }

    def _key(
        self,
        world: dict[str, Any],
        run: dict[str, Any],
        map_context: dict[str, Any],
    ) -> tuple[Any, ...]:
        entities = tuple(
            (
                item.get("name"),
                item.get("direction"),
                item.get("position", {}).get("x"),
                item.get("position", {}).get("y"),
            )
            for item in world.get("entities", [])
            if isinstance(item, dict)
        )
        center = map_context.get("center")
        return (
            world.get("tick"),
            entities,
            repr(center),
            len(map_context.get("resources", [])),
            len(map_context.get("natural", [])),
            len(map_context.get("water_tiles", [])),
            repr(run.get("stage")) if isinstance(run, dict) else None,
        )

    def _load_icon(self, name: str, size: int) -> Image.Image | None:
        cache_key = (name, size)
        if cache_key in self._icon_cache:
            return self._icon_cache[cache_key]
        path = resolve_official_icon(name)
        if path is None:
            return None
        try:
            image = Image.open(path).convert("RGBA")
            # Factorio icons contain the main mip at the left; height is the base size.
            base = min(image.height, image.width)
            image = image.crop((0, 0, base, base))
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
        except (OSError, ValueError):
            return None
        self._icon_cache[cache_key] = image
        return image

    def _terrain_patch(self, kind: str, size: int = 128) -> Image.Image:
        key = f"{kind}:{size}"
        if key in self._terrain_cache:
            return self._terrain_cache[key]

        source = TERRAIN_GRASS if kind == "grass" else TERRAIN_DIRT
        try:
            sheet = Image.open(source).convert("RGB")
            # These official sheets contain tile variations. A deterministic crop
            # gives us real Factorio ground texture without shipping the source file.
            x = 192 if sheet.width >= 320 else 0
            y = 64 if sheet.height >= 192 else 0
            patch = sheet.crop((x, y, min(x + 256, sheet.width), min(y + 256, sheet.height)))
            patch = patch.resize((size, size), Image.Resampling.BILINEAR)
            patch = ImageEnhance.Brightness(patch).enhance(0.62)
        except OSError:
            color = (62, 70, 46) if kind == "grass" else (82, 67, 48)
            patch = Image.new("RGB", (size, size), color)
        self._terrain_cache[key] = patch
        return patch

    @staticmethod
    def _paste_center(canvas: Image.Image, icon: Image.Image, x: int, y: int) -> None:
        canvas.alpha_composite(icon, (x - icon.width // 2, y - icon.height // 2))

    def render(
        self,
        world: dict[str, Any],
        run: dict[str, Any],
        map_context: dict[str, Any] | None = None,
    ) -> bytes:
        map_context = map_context or {}
        key = self._key(world, run, map_context)
        with self._lock:
            if key == self._cache_key and self._cache_png is not None:
                return self._cache_png
            try:
                png = self._render_uncached(world, run, map_context)
                self._cache_key = key
                self._cache_png = png
                self._last_error = None
                return png
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                raise

    def _render_uncached(
        self,
        world: dict[str, Any],
        run: dict[str, Any],
        map_context: dict[str, Any],
    ) -> bytes:
        width = 1024
        height = 1024
        radius = float(self.viewport_radius)

        entities = [
            item for item in world.get("entities", [])
            if isinstance(item, dict) and _position(item) is not None
        ]
        center_raw = map_context.get("center")
        if isinstance(center_raw, dict):
            center = (
                float(center_raw.get("x", 0.0)),
                float(center_raw.get("y", 0.0)),
            )
        else:
            character = next(
                (item for item in entities if item.get("name") == "character"),
                None,
            )
            center = _position(character) if character else (0.0, 0.0)
        if center is None:
            center = (0.0, 0.0)

        canvas = Image.new("RGBA", (width, height), (30, 33, 24, 255))
        grass = self._terrain_patch("grass")
        dirt = self._terrain_patch("dirt")
        tile_px = grass.width
        for y in range(0, height, tile_px):
            for x in range(0, width, tile_px):
                # Deterministic variation based on tile coordinate.
                use_dirt = ((x // tile_px) * 17 + (y // tile_px) * 31) % 11 == 0
                patch = dirt if use_dirt else grass
                canvas.alpha_composite(patch.convert("RGBA"), (x, y))

        draw = ImageDraw.Draw(canvas, "RGBA")
        scale_x = width / (radius * 2)
        scale_y = height / (radius * 2)

        def project(x: float, y: float) -> tuple[int, int]:
            return (
                int((x - center[0] + radius) * scale_x),
                int((y - center[1] + radius) * scale_y),
            )

        # Water is background terrain, so draw it before resources/entities.
        for tile in map_context.get("water_tiles", []):
            if not isinstance(tile, dict):
                continue
            try:
                tx, ty = float(tile["x"]), float(tile["y"])
            except (KeyError, TypeError, ValueError):
                continue
            px, py = project(tx, ty)
            half_x = max(2, int(scale_x * 0.52))
            half_y = max(2, int(scale_y * 0.52))
            draw.rectangle(
                (px - half_x, py - half_y, px + half_x, py + half_y),
                fill=(38, 91, 127, 230),
            )

        # Resource patches: colored tile + real Factorio item icon at sparse intervals.
        resources = [
            item for item in map_context.get("resources", [])
            if isinstance(item, dict) and _position(item) is not None
        ]
        resource_icons: dict[str, Image.Image | None] = {}
        for index, resource in enumerate(resources):
            pos = _position(resource)
            if pos is None:
                continue
            name = str(resource.get("name", "resource"))
            px, py = project(*pos)
            color = RESOURCE_COLORS.get(name, (145, 128, 82, 190))
            half = max(2, int(min(scale_x, scale_y) * 0.42))
            draw.rectangle((px - half, py - half, px + half, py + half), fill=color)
            # Keep dense fields legible while visibly using actual game art.
            if index % 9 == 0:
                icon = resource_icons.get(name)
                if name not in resource_icons:
                    icon = self._load_icon(name, max(12, int(min(scale_x, scale_y) * 1.7)))
                    resource_icons[name] = icon
                if icon is not None:
                    self._paste_center(canvas, icon, px, py)

        # Natural objects (trees/rocks) from the live map.
        natural = [
            item for item in map_context.get("natural", [])
            if isinstance(item, dict) and _position(item) is not None
        ]
        for item in natural[:900]:
            pos = _position(item)
            if pos is None:
                continue
            px, py = project(*pos)
            name = str(item.get("name", "tree"))
            icon = self._load_icon(name, 18)
            if icon is not None:
                self._paste_center(canvas, icon, px, py)
            elif "tree" in name:
                draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill=(38, 77, 34, 220))
            else:
                draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=(92, 83, 67, 210))

        # World grid at 4-tile intervals.
        grid_step = 4
        start_x = math.floor((center[0] - radius) / grid_step) * grid_step
        end_x = center[0] + radius
        x = start_x
        while x <= end_x:
            px, _ = project(x, center[1])
            draw.line((px, 0, px, height), fill=(255, 255, 255, 18), width=1)
            x += grid_step
        start_y = math.floor((center[1] - radius) / grid_step) * grid_step
        end_y = center[1] + radius
        y = start_y
        while y <= end_y:
            _, py = project(center[0], y)
            draw.line((0, py, width, py), fill=(255, 255, 255, 18), width=1)
            y += grid_step

        # Player-built entities use the real high-resolution Factorio icons.
        for entity in entities:
            pos = _position(entity)
            if pos is None:
                continue
            px, py = project(*pos)
            name = str(entity.get("name", "entity"))
            if name == "character":
                draw.ellipse(
                    (px - 13, py - 13, px + 13, py + 13),
                    fill=(238, 159, 52, 75),
                    outline=(255, 195, 84, 255),
                    width=3,
                )
                continue
            icon_size = 44 if "drill" in name or "furnace" in name else 34
            icon = self._load_icon(name, icon_size)
            if icon is not None:
                self._paste_center(canvas, icon, px, py)
            else:
                draw.rounded_rectangle(
                    (px - 12, py - 12, px + 12, py + 12),
                    radius=4,
                    fill=(61, 107, 139, 230),
                    outline=(199, 219, 232, 200),
                )
            direction = int(entity.get("direction", 0) or 0)
            angle = math.radians((direction / 16.0) * 360.0 - 90.0)
            ex = px + int(math.cos(angle) * 18)
            ey = py + int(math.sin(angle) * 18)
            draw.line((px, py, ex, ey), fill=(255, 190, 70, 235), width=2)

        font = ImageFont.load_default()
        draw.rounded_rectangle((12, 12, 340, 68), radius=7, fill=(8, 10, 12, 195))
        draw.text((23, 22), "LIVE FACTORIO WORLD", fill=(236, 239, 241, 255), font=font)
        draw.text(
            (23, 40),
            f"center ({center[0]:.1f}, {center[1]:.1f}) · tick {world.get('tick', '--')}",
            fill=(177, 188, 196, 255),
            font=font,
        )

        output = world.get("production", {}).get("output", {})
        if isinstance(output, dict) and output:
            top = sorted(output.items(), key=lambda item: float(item[1]), reverse=True)[:3]
            text = " · ".join(f"{value:g} {name}" for name, value in top)
            draw.rounded_rectangle((12, height - 45, min(width - 12, 420), height - 12), radius=7, fill=(8, 10, 12, 195))
            draw.text((23, height - 34), text, fill=(123, 222, 143, 255), font=font)

        digest = hashlib.sha1(repr(self._key(world, run, map_context)).encode()).hexdigest()[:8]
        draw.text((width - 120, height - 24), f"frame {digest}", fill=(190, 190, 190, 180), font=font)

        buffer = BytesIO()
        canvas.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
