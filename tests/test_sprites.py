"""Every sprite spec must resolve to a real sheet and divide it exactly.

Two failure modes motivated this: a spec whose filename token did not exist
(steam-engine uses -H/-V, not -N/-E/-S/-W) and a frame size that does not
divide its sheet (165x391 against 1800x1564 is 10.909 columns), which crops
across animation frames instead of onto one.
"""

from __future__ import annotations

import pytest
from PIL import Image

from factorio_ai_lab.dashboard.sprites import (
    EAST,
    NORTH,
    SOUTH,
    SPRITE_SPECS,
    WEST,
    SpriteLibrary,
    sprite_manifest,
)

CARDINALS = (NORTH, EAST, SOUTH, WEST)

library = SpriteLibrary()

_assets_present = any(
    library._resolve_path(spec, NORTH) is not None
    and library._resolve_path(spec, NORTH).is_file()
    for spec in SPRITE_SPECS.values()
)

requires_assets = pytest.mark.skipif(
    not _assets_present,
    reason="local Factorio graphics not installed on this host",
)


def test_manifest_covers_every_spec() -> None:
    manifest = sprite_manifest()
    assert manifest["count"] == len(SPRITE_SPECS)
    assert set(manifest["sprites"]) == set(SPRITE_SPECS)
    for entry in manifest["sprites"].values():
        assert entry["frames"] >= 1
        assert entry["ticks_per_frame"] >= 1


@requires_assets
@pytest.mark.parametrize("name", sorted(SPRITE_SPECS))
def test_every_direction_resolves_to_an_existing_sheet(name: str) -> None:
    spec = SPRITE_SPECS[name]
    for direction in CARDINALS:
        path = library._resolve_path(spec, direction)
        assert path is not None, f"{name} dir={direction} sem caminho"
        assert path.is_file(), f"{name} dir={direction}: arquivo ausente {path}"


@requires_assets
@pytest.mark.parametrize("name", sorted(SPRITE_SPECS))
def test_frame_size_divides_the_sheet_exactly(name: str) -> None:
    spec = SPRITE_SPECS[name]
    for direction in CARDINALS:
        path = library._resolve_path(spec, direction)
        frame_w, frame_h = library._frame_size(spec, direction)
        with Image.open(path) as sheet:
            width, height = sheet.size
        assert width % frame_w == 0, (
            f"{name} dir={direction}: {width} nao divide por {frame_w} "
            f"({width / frame_w:.3f} colunas)"
        )
        assert height % frame_h == 0, (
            f"{name} dir={direction}: {height} nao divide por {frame_h} "
            f"({height / frame_h:.3f} linhas)"
        )


@requires_assets
@pytest.mark.parametrize("name", sorted(SPRITE_SPECS))
def test_declared_frames_fit_inside_the_grid(name: str) -> None:
    spec = SPRITE_SPECS[name]
    path = library._resolve_path(spec, NORTH)
    frame_w, frame_h = library._frame_size(spec, NORTH)
    with Image.open(path) as sheet:
        columns = sheet.width // frame_w
        rows = sheet.height // frame_h
    if spec.layout == "row":
        assert spec.frames <= columns, f"{name}: {spec.frames} > {columns} colunas"
    elif spec.layout in {"grid", "file"}:
        assert spec.frames <= columns * rows, (
            f"{name}: {spec.frames} frames > {columns * rows} celulas"
        )


@requires_assets
@pytest.mark.parametrize("name", sorted(SPRITE_SPECS))
def test_render_returns_a_png_of_the_declared_frame_size(name: str) -> None:
    spec = SPRITE_SPECS[name]
    for direction in CARDINALS:
        payload = library.render(name, direction, frame=1)
        assert payload, f"{name} dir={direction}: render vazio"
        assert payload[:8] == b"\x89PNG\r\n\x1a\n"
        expected = library._frame_size(spec, direction)
        with Image.open(__import__("io").BytesIO(payload)) as image:
            assert image.size == expected, (
                f"{name} dir={direction}: {image.size} != {expected}"
            )


def test_unknown_entity_has_no_sprite() -> None:
    assert library.render("nao-existe-no-jogo", 0, 0) is None
