from __future__ import annotations

import hashlib
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

ASSET_ROOT = Path(
    "/home/ti/.local/share/factorio-ai/assets/demo-2.0.73/"
    "factorio/data/base/graphics"
)
ICON_DIR = ASSET_ROOT / "icons"
ENTITY_DIR = ASSET_ROOT / "entity"
TERRAIN_GRASS = ASSET_ROOT / "terrain/grass-2.png"
TERRAIN_DIRT = ASSET_ROOT / "terrain/dirt-1.png"
FLE_ICON_DIR = Path("/home/ti/.fle/spritemaps/__base__/graphics/icons")
FLE_RESOURCE_DIR = Path("/home/ti/.fle/spritemaps/__base__/graphics/resources")

ICON_ALIASES = {
    "character": "light-armor",
    "entity-ghost": "blueprint",
}

RESOURCE_COLORS = {
    "iron-ore": (91, 132, 160),
    "copper-ore": (178, 91, 52),
    "coal": (48, 50, 52),
    "stone": (160, 143, 105),
    "uranium-ore": (80, 162, 66),
    "crude-oil": (42, 36, 31),
}

DRILL_SPECS = {
    0: ("N", 173, 188),
    4: ("E", 185, 168),
    8: ("S", 174, 174),
    12: ("W", 180, 176),
}

BELT_ROW_BY_DIRECTION = {
    0: 2,   # north
    4: 0,   # east
    8: 3,   # south
    12: 1,  # west
}

DIRECTION_VECTORS = {
    0: (0.0, -1.0),
    4: (1.0, 0.0),
    8: (0.0, 1.0),
    12: (-1.0, 0.0),
}


def _safe_name(name: str) -> bool:
    return bool(name) and all(
        character in "abcdefghijklmnopqrstuvwxyz0123456789-"
        for character in name
    )


def _cardinal_direction(raw: Any) -> int:
    try:
        direction = int(raw or 0) % 16
    except (TypeError, ValueError):
        return 0
    return min((0, 4, 8, 12), key=lambda value: min(
        abs(direction - value),
        16 - abs(direction - value),
    ))


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
    """Readable tactical world view using local Factorio 2.0.73 art assets."""

    def __init__(self, viewport_radius: float = 34.0) -> None:
        self.viewport_radius = viewport_radius
        self._lock = threading.Lock()
        self._cache_key: tuple[Any, ...] | None = None
        self._cache_png: bytes | None = None
        self._last_error: str | None = None
        self._icon_cache: dict[tuple[str, int], Image.Image] = {}
        self._sprite_cache: dict[tuple[Any, ...], Image.Image] = {}
        self._terrain_tiles: dict[str, list[Image.Image]] = {}
        self._background_cache: Image.Image | None = None
        self._asset_count_checked_at = 0.0
        self._asset_count = 0
        self._last_radius = viewport_radius

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
            "renderer": "official-asset-world-map-v2",
            "asset_count": self._asset_count_now(),
            "sprite_count": self._asset_count_now(),
            "viewport_radius": round(self._last_radius, 2),
            "last_error": self._last_error,
            "official_assets": assets,
        }

    def _key(
        self,
        world: dict[str, Any],
        run: dict[str, Any],
        map_context: dict[str, Any],
        mode: str,
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
        return (
            mode,
            world.get("tick"),
            entities,
            len(map_context.get("resources", [])),
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
            base = min(image.height, image.width)
            image = image.crop((0, 0, base, base))
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
        except (OSError, ValueError):
            return None
        self._icon_cache[cache_key] = image
        return image

    def _resource_sprite(
        self,
        name: str,
        amount: float,
        x: float,
        y: float,
        tile_pixels: float,
    ) -> Image.Image | None:
        path = FLE_RESOURCE_DIR / name / f"{name}.png"
        if not path.is_file():
            return None

        thresholds = (15000, 9500, 5500, 2900, 1300, 400, 150, 80)
        stage = 7
        for index, threshold in enumerate(thresholds):
            if amount >= threshold:
                stage = index
                break

        digest = hashlib.sha1(
            f"{name}:{x:.1f}:{y:.1f}".encode()
        ).digest()
        variation = digest[0] % 8
        cache_key = (
            "resource",
            name,
            stage,
            variation,
            round(tile_pixels, 1),
        )
        if cache_key in self._sprite_cache:
            return self._sprite_cache[cache_key]

        try:
            sheet = Image.open(path).convert("RGBA")
            frame_w = sheet.width // 8
            frame_h = sheet.height // 8
            sprite = sheet.crop(
                (
                    stage * frame_w,
                    variation * frame_h,
                    (stage + 1) * frame_w,
                    (variation + 1) * frame_h,
                )
            )
            target = max(6, round(tile_pixels * 1.04))
            sprite = sprite.resize(
                (target, target),
                Image.Resampling.LANCZOS,
            )
        except (OSError, ValueError):
            return None

        self._sprite_cache[cache_key] = sprite
        return sprite

    def _terrain_samples(self, kind: str) -> list[Image.Image]:
        if kind in self._terrain_tiles:
            return self._terrain_tiles[kind]

        source = TERRAIN_GRASS if kind == "grass" else TERRAIN_DIRT
        tiles: list[Image.Image] = []
        try:
            sheet = Image.open(source).convert("RGBA")
            for y in range(0, sheet.height - 63, 64):
                for x in range(0, sheet.width - 63, 64):
                    tile = sheet.crop((x, y, x + 64, y + 64))
                    if tile.getchannel("A").getextrema() != (255, 255):
                        continue
                    mean = tile.convert("RGB").resize(
                        (1, 1),
                        Image.Resampling.BILINEAR,
                    ).getpixel((0, 0))
                    brightness = sum(mean) / 3
                    if 24 <= brightness <= 125:
                        tile = ImageEnhance.Brightness(
                            tile.convert("RGB")
                        ).enhance(0.62)
                        tile = ImageEnhance.Color(tile).enhance(0.78)
                        tiles.append(tile.convert("RGBA"))
                    if len(tiles) >= 72:
                        break
                if len(tiles) >= 72:
                    break
        except OSError:
            pass

        if not tiles:
            color = (55, 62, 39, 255) if kind == "grass" else (79, 63, 43, 255)
            tiles = [Image.new("RGBA", (64, 64), color)]

        self._terrain_tiles[kind] = tiles
        return tiles

    def _background(self, width: int, height: int) -> Image.Image:
        if self._background_cache is not None:
            return self._background_cache.copy()

        grass = self._terrain_samples("grass")
        dirt = self._terrain_samples("dirt")
        canvas = Image.new("RGBA", (width, height), (38, 43, 30, 255))

        block = 64
        for gy, y in enumerate(range(0, height, block)):
            for gx, x in enumerate(range(0, width, block)):
                seed = gx * 73856093 ^ gy * 19349663
                use_dirt = seed % 17 in {0, 1}
                source = dirt if use_dirt else grass
                tile = source[seed % len(source)]
                canvas.alpha_composite(tile, (x, y))

        # Integrate the repeated tile samples so the terrain reads as one surface.
        canvas = canvas.filter(ImageFilter.GaussianBlur(radius=0.35))
        veil = Image.new("RGBA", canvas.size, (7, 10, 8, 32))
        canvas = Image.alpha_composite(canvas, veil)
        self._background_cache = canvas.copy()
        return canvas

    @staticmethod
    def _paste_center(
        canvas: Image.Image,
        sprite: Image.Image,
        x: int,
        y: int,
    ) -> None:
        canvas.alpha_composite(
            sprite,
            (x - sprite.width // 2, y - sprite.height // 2),
        )

    @staticmethod
    def _resize_factorio_sprite(
        image: Image.Image,
        tile_pixels: float,
        *,
        high_res_scale: float = 0.5,
        logical_pixel_tile: float = 32.0,
    ) -> Image.Image:
        factor = high_res_scale * tile_pixels / logical_pixel_tile
        width = max(2, round(image.width * factor))
        height = max(2, round(image.height * factor))
        return image.resize((width, height), Image.Resampling.LANCZOS)

    def _burner_drill_sprite(
        self,
        direction: int,
        tick: int,
        tile_pixels: float,
    ) -> Image.Image | None:
        direction = _cardinal_direction(direction)
        suffix, frame_width, frame_height = DRILL_SPECS[direction]
        frame = (max(0, tick) // 8) % 32
        cache_key = ("burner-drill", direction, frame, round(tile_pixels, 1))
        if cache_key in self._sprite_cache:
            return self._sprite_cache[cache_key]

        path = (
            ENTITY_DIR / "burner-mining-drill"
            / f"burner-mining-drill-{suffix}.png"
        )
        try:
            sheet = Image.open(path).convert("RGBA")
            col = frame % 4
            row = frame // 4
            crop = sheet.crop((
                col * frame_width,
                row * frame_height,
                (col + 1) * frame_width,
                (row + 1) * frame_height,
            ))
            crop = self._resize_factorio_sprite(crop, tile_pixels)
        except OSError:
            return None
        self._sprite_cache[cache_key] = crop
        return crop

    def _belt_sprite(
        self,
        direction: int,
        tick: int,
        tile_pixels: float,
    ) -> Image.Image | None:
        direction = _cardinal_direction(direction)
        row = BELT_ROW_BY_DIRECTION.get(direction, 2)
        frame = (max(0, tick) // 2) % 16
        cache_key = ("belt", direction, frame, round(tile_pixels, 1))
        if cache_key in self._sprite_cache:
            return self._sprite_cache[cache_key]
        path = ENTITY_DIR / "transport-belt" / "transport-belt.png"
        try:
            sheet = Image.open(path).convert("RGBA")
            crop = sheet.crop((
                frame * 128,
                row * 128,
                (frame + 1) * 128,
                (row + 1) * 128,
            ))
            target = max(12, round(tile_pixels * 1.18))
            crop = crop.resize((target, target), Image.Resampling.LANCZOS)
        except OSError:
            return None
        self._sprite_cache[cache_key] = crop
        return crop

    def _static_world_sprite(
        self,
        name: str,
        tile_pixels: float,
    ) -> Image.Image | None:
        cache_key = ("static", name, round(tile_pixels, 1))
        if cache_key in self._sprite_cache:
            return self._sprite_cache[cache_key]

        candidates = {
            "wooden-chest": ENTITY_DIR / "wooden-chest" / "wooden-chest.png",
            "stone-furnace": ENTITY_DIR / "stone-furnace" / "stone-furnace.png",
        }
        path = candidates.get(name)
        if path is None:
            return None
        try:
            sprite = Image.open(path).convert("RGBA")
            sprite = self._resize_factorio_sprite(sprite, tile_pixels)
        except OSError:
            return None
        self._sprite_cache[cache_key] = sprite
        return sprite

    @staticmethod
    def _factory_view(
        entities: list[dict[str, Any]],
        map_context: dict[str, Any],
        max_radius: float,
    ) -> tuple[tuple[float, float], float]:
        built_positions = [
            pos
            for item in entities
            if item.get("name") != "character"
            if (pos := _position(item)) is not None
        ]
        if built_positions:
            center = (
                sum(pos[0] for pos in built_positions) / len(built_positions),
                sum(pos[1] for pos in built_positions) / len(built_positions),
            )
            extent = max(
                max(abs(pos[0] - center[0]), abs(pos[1] - center[1]))
                for pos in built_positions
            )
            radius = min(max_radius, max(12.0, extent + 7.5))
            return center, radius

        center_raw = map_context.get("center")
        if isinstance(center_raw, dict):
            try:
                return (
                    float(center_raw.get("x", 0.0)),
                    float(center_raw.get("y", 0.0)),
                ), min(max_radius, 18.0)
            except (TypeError, ValueError):
                pass
        return (0.0, 0.0), min(max_radius, 18.0)

    def render(
        self,
        world: dict[str, Any],
        run: dict[str, Any],
        map_context: dict[str, Any] | None = None,
        *,
        mode: str = "game",
    ) -> bytes:
        if mode not in {"game", "tactical"}:
            raise ValueError(f"unsupported renderer mode: {mode}")
        map_context = map_context or {}
        key = self._key(world, run, map_context, mode)
        with self._lock:
            if key == self._cache_key and self._cache_png is not None:
                return self._cache_png
            try:
                png = self._render_uncached(world, run, map_context, mode=mode)
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
        *,
        mode: str,
    ) -> bytes:
        width = 1024
        height = 1024
        tick = int(world.get("tick") or 0)

        entities = [
            item
            for item in world.get("entities", [])
            if isinstance(item, dict) and _position(item) is not None
        ]
        center, radius = self._factory_view(
            entities,
            map_context,
            self.viewport_radius,
        )
        self._last_radius = radius

        canvas = self._background(width, height)
        scale_x = width / (radius * 2)
        scale_y = height / (radius * 2)
        tile_pixels = min(scale_x, scale_y)

        def project(x: float, y: float) -> tuple[int, int]:
            return (
                round((x - center[0] + radius) * scale_x),
                round((y - center[1] + radius) * scale_y),
            )

        # Water is a continuous layer, not one rectangle per tile.
        water_mask = Image.new("L", (width, height), 0)
        water_draw = ImageDraw.Draw(water_mask)
        water_radius = max(2, round(tile_pixels * 0.62))
        for tile in map_context.get("water_tiles", []):
            if not isinstance(tile, dict):
                continue
            try:
                px, py = project(float(tile["x"]), float(tile["y"]))
            except (KeyError, TypeError, ValueError):
                continue
            water_draw.ellipse((
                px - water_radius,
                py - water_radius,
                px + water_radius,
                py + water_radius,
            ), fill=235)
        if water_mask.getbbox():
            water_mask = water_mask.filter(
                ImageFilter.GaussianBlur(radius=max(1.0, tile_pixels * 0.34))
            )
            water_layer = Image.new("RGBA", (width, height), (38, 92, 127, 0))
            water_layer.putalpha(water_mask.point(lambda value: int(value * 0.88)))
            canvas = Image.alpha_composite(canvas, water_layer)

        # Game View uses the actual resource sheets from the local FLE
        # sprite cache. Tactical View adds a subtle map-color field below them.
        resources = [
            item
            for item in map_context.get("resources", [])
            if isinstance(item, dict) and _position(item) is not None
        ]
        by_resource: dict[str, list[dict[str, Any]]] = {}
        for resource in resources:
            name = str(resource.get("name", "resource"))
            by_resource.setdefault(name, []).append(resource)

        if mode == "tactical":
            for name, items in by_resource.items():
                mask = Image.new("L", (width, height), 0)
                mask_draw = ImageDraw.Draw(mask)
                patch_radius = max(2, round(tile_pixels * 0.56))
                for item in items:
                    pos = _position(item)
                    if pos is None:
                        continue
                    px, py = project(*pos)
                    mask_draw.ellipse(
                        (
                            px - patch_radius,
                            py - patch_radius,
                            px + patch_radius,
                            py + patch_radius,
                        ),
                        fill=170,
                    )
                mask = mask.filter(
                    ImageFilter.GaussianBlur(
                        radius=max(1.0, tile_pixels * 0.35)
                    )
                )
                rgb = RESOURCE_COLORS.get(name, (137, 122, 80))
                layer = Image.new("RGBA", (width, height), (*rgb, 0))
                layer.putalpha(mask.point(lambda value: int(value * 0.42)))
                canvas = Image.alpha_composite(canvas, layer)

        for resource in resources:
            pos = _position(resource)
            if pos is None:
                continue
            name = str(resource.get("name", "resource"))
            try:
                amount = float(resource.get("amount", 1.0))
            except (TypeError, ValueError):
                amount = 1.0
            sprite = self._resource_sprite(
                name,
                amount,
                pos[0],
                pos[1],
                tile_pixels,
            )
            if sprite is None:
                continue
            px, py = project(*pos)
            self._paste_center(canvas, sprite, px, py)

        draw = ImageDraw.Draw(canvas, "RGBA")

        if mode == "tactical":
            grid_step = max(1.0, tile_pixels)
            x = 0.0
            while x <= width:
                draw.line(
                    (round(x), 0, round(x), height),
                    fill=(221, 229, 233, 22),
                    width=1,
                )
                x += grid_step
            y = 0.0
            while y <= height:
                draw.line(
                    (0, round(y), width, round(y)),
                    fill=(221, 229, 233, 22),
                    width=1,
                )
                y += grid_step

        # Belts are infrastructure first: draw a connected animated lane under the sprites.
        belts = [
            item for item in entities
            if "transport-belt" in str(item.get("name", ""))
            and "underground" not in str(item.get("name", ""))
        ]
        phase = (tick // 2) % 12
        for belt in belts:
            pos = _position(belt)
            if pos is None:
                continue
            direction = _cardinal_direction(belt.get("direction"))
            vx, vy = DIRECTION_VECTORS[direction]
            px, py = project(*pos)
            dx = vx * tile_pixels * 0.54
            dy = vy * tile_pixels * 0.54
            draw.line(
                (px - dx, py - dy, px + dx, py + dy),
                fill=(33, 28, 20, 245),
                width=max(5, round(tile_pixels * 0.80)),
            )
            draw.line(
                (px - dx, py - dy, px + dx, py + dy),
                fill=(169, 112, 42, 215),
                width=max(2, round(tile_pixels * 0.58)),
            )
            offset = ((phase / 12.0) - 0.5) * tile_pixels
            cx = px + vx * offset
            cy = py + vy * offset
            nx, ny = -vy, vx
            arrow = max(2.0, tile_pixels * 0.12)
            draw.polygon(
                [
                    (cx + vx * arrow * 1.8, cy + vy * arrow * 1.8),
                    (cx - vx * arrow + nx * arrow, cy - vy * arrow + ny * arrow),
                    (cx - vx * arrow - nx * arrow, cy - vy * arrow - ny * arrow),
                ],
                fill=(246, 195, 86, 220),
            )

        if mode == "tactical":
            latest_path: list[dict[str, Any]] = []
            for event in reversed(run.get("events", [])):
                if (
                    isinstance(event, dict)
                    and event.get("type") == "plan"
                    and isinstance(event.get("path"), list)
                ):
                    latest_path = event["path"]
                    break
            route_points: list[tuple[int, int]] = []
            for raw in latest_path:
                try:
                    route_points.append(
                        project(float(raw["x"]), float(raw["y"]))
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            if len(route_points) >= 2:
                draw.line(
                    route_points,
                    fill=(246, 184, 69, 210),
                    width=max(3, round(tile_pixels * 0.12)),
                    joint="curve",
                )
                for px, py in route_points:
                    rr = max(2, round(tile_pixels * 0.08))
                    draw.ellipse(
                        (px - rr, py - rr, px + rr, py + rr),
                        fill=(255, 219, 132, 225),
                    )

            for entity in entities:
                if entity.get("name") == "character":
                    continue
                pos = _position(entity)
                if pos is None:
                    continue
                px, py = project(*pos)
                name = str(entity.get("name", "entity"))
                footprint = 2.0 if name in {
                    "burner-mining-drill",
                    "stone-furnace",
                } else 1.0
                half = max(5, round(tile_pixels * footprint * 0.48))
                draw.rectangle(
                    (px - half, py - half, px + half, py + half),
                    outline=(103, 199, 255, 96),
                    width=max(1, round(tile_pixels * 0.035)),
                )

        # World entities. Real entity frames are preferred over inventory icons.
        for entity in entities:
            pos = _position(entity)
            if pos is None:
                continue
            px, py = project(*pos)
            name = str(entity.get("name", "entity"))
            direction = _cardinal_direction(entity.get("direction"))

            if name == "character":
                radius_px = max(8, round(tile_pixels * 0.38))
                draw.ellipse(
                    (
                        px - radius_px,
                        py - radius_px,
                        px + radius_px,
                        py + radius_px,
                    ),
                    fill=(240, 163, 58, 32),
                    outline=(255, 197, 90, 205),
                    width=max(2, round(tile_pixels * 0.06)),
                )
                inner = max(3, round(radius_px * 0.25))
                draw.ellipse(
                    (px - inner, py - inner, px + inner, py + inner),
                    fill=(255, 205, 105, 230),
                )
                continue

            sprite: Image.Image | None = None
            if name == "burner-mining-drill":
                sprite = self._burner_drill_sprite(direction, tick, tile_pixels)
            elif name == "transport-belt":
                sprite = self._belt_sprite(direction, tick, tile_pixels)
            else:
                sprite = self._static_world_sprite(name, tile_pixels)

            if sprite is None:
                size = max(22, round(tile_pixels * 1.15))
                sprite = self._load_icon(name, size)

            if sprite is not None:
                # Local contact shadow makes the entity visually sit on the surface.
                shadow_w = max(8, round(sprite.width * 0.58))
                shadow_h = max(4, round(sprite.height * 0.16))
                draw.ellipse(
                    (
                        px - shadow_w // 2,
                        py + round(tile_pixels * 0.22) - shadow_h // 2,
                        px + shadow_w // 2,
                        py + round(tile_pixels * 0.22) + shadow_h // 2,
                    ),
                    fill=(0, 0, 0, 78),
                )
                self._paste_center(canvas, sprite, px, py)
            else:
                half = max(8, round(tile_pixels * 0.44))
                draw.rounded_rectangle(
                    (px - half, py - half, px + half, py + half),
                    radius=max(3, half // 4),
                    fill=(57, 73, 83, 230),
                    outline=(190, 205, 214, 150),
                )

        # Gentle vignette gives hierarchy without a debug grid.
        vignette = Image.new("L", (width, height), 0)
        vignette_draw = ImageDraw.Draw(vignette)
        vignette_draw.ellipse(
            (-width * 0.12, -height * 0.12, width * 1.12, height * 1.12),
            fill=0,
            outline=68,
            width=150,
        )
        vignette = vignette.filter(ImageFilter.GaussianBlur(radius=60))
        shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shade.putalpha(vignette)
        canvas = Image.alpha_composite(canvas, shade)

        buffer = BytesIO()
        canvas.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
