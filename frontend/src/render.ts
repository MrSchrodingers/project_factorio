/**
 * Canvas drawing for the factory map.
 *
 * Everything is drawn in layers from the ground up, and every layer culls
 * against the visible viewport first: at survey zoom the scene can contain a
 * few thousand ore tiles, and drawing the ones off-screen costs the same as
 * drawing the ones on it.
 *
 * Sprites are seated using the prototype footprint (tile_width/tile_height),
 * not the raw image size, so a 2x2 drill covers exactly two tiles by two
 * regardless of how much empty shadow space its sheet carries.
 */

import type { AssetLibrary } from "./assets";
import type { Camera } from "./camera";
import {
  GROUND_VARIANTS,
  ORE_COLORS,
  ROLE_COLORS,
  STATUS_COLORS,
  TERRAIN_COLORS,
  humanize,
  roleOf,
  statusClass,
  tileHash,
} from "./palette";
import type {
  EntityPrototype,
  FactoryGraph,
  OverlayKey,
  Scene,
  SceneEntity,
} from "./types";

const DIRECTION_VECTORS: Record<number, [number, number]> = {
  0: [0, -1],
  4: [1, 0],
  8: [0, 1],
  12: [-1, 0],
};

function cardinal(direction: number): number {
  const d = ((direction | 0) % 16 + 16) % 16;
  const candidates = [0, 4, 8, 12];
  let best = 0;
  let bestDistance = 99;
  for (const value of candidates) {
    const distance = Math.min(Math.abs(d - value), 16 - Math.abs(d - value));
    if (distance < bestDistance) {
      bestDistance = distance;
      best = value;
    }
  }
  return best;
}

export interface RenderInput {
  scene: Scene | null;
  graph: FactoryGraph | null;
  camera: Camera;
  assets: AssetLibrary;
  overlays: Set<OverlayKey>;
  hovered: SceneEntity | null;
  selected: SceneEntity | null;
  animationTick: number;
}

/** Footprint of an entity in world tiles, from its prototype when known. */
export function footprint(
  entity: SceneEntity,
  prototypes: Record<string, EntityPrototype>,
): { width: number; height: number } {
  const proto = prototypes[entity.name];
  if (!proto) return { width: 1, height: 1 };
  const direction = cardinal(entity.direction);
  const rotated = direction === 4 || direction === 12;
  const width = Math.max(1, proto.tile_width || 1);
  const height = Math.max(1, proto.tile_height || 1);
  // Factorio rotates the footprint for east/west placements of non-square
  // entities (a 3x2 boiler is 2x3 when facing east).
  return rotated && width !== height
    ? { width: height, height: width }
    : { width, height };
}

export class SceneRenderer {
  private readonly ctx: CanvasRenderingContext2D;

  constructor(canvas: HTMLCanvasElement) {
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("2D canvas context unavailable");
    this.ctx = context;
  }

  draw(input: RenderInput): void {
    const { ctx } = this;
    const { camera } = input;
    const width = camera.viewportWidth;
    const height = camera.viewportHeight;

    ctx.save();
    ctx.fillStyle = "#14170f";
    ctx.fillRect(0, 0, width, height);

    if (!input.scene) {
      this.drawWaiting(width, height);
      ctx.restore();
      return;
    }

    this.drawGround(input);
    this.drawTerrain(input);
    if (input.overlays.has("resources")) this.drawResources(input);
    this.drawNatural(input);
    if (input.overlays.has("grid")) this.drawGrid(input);
    if (input.overlays.has("power")) this.drawPowerNetwork(input);
    if (input.overlays.has("topology")) this.drawTopology(input);
    this.drawEntities(input);
    if (input.overlays.has("flow")) this.drawFlow(input);
    this.drawCharacter(input);
    if (input.overlays.has("labels")) this.drawLabels(input);
    this.drawHighlight(input);

    ctx.restore();
  }

  private drawWaiting(width: number, height: number): void {
    const { ctx } = this;
    ctx.fillStyle = "#6f7a82";
    ctx.font = "500 13px ui-monospace, SFMono-Regular, Consolas, monospace";
    ctx.textAlign = "center";
    ctx.fillText("aguardando estado do mundo", width / 2, height / 2);
    ctx.textAlign = "left";
  }

  /**
   * Ground is drawn as tiles only while they are big enough to be visible as
   * texture; below that it collapses to a flat fill, which is both faster and
   * less noisy than a moire of 2px squares.
   */
  private drawGround(input: RenderInput): void {
    const { ctx } = this;
    const { camera } = input;
    const view = camera.viewport();
    const scale = camera.scale;

    ctx.fillStyle = GROUND_VARIANTS[0];
    ctx.fillRect(0, 0, camera.viewportWidth, camera.viewportHeight);
    if (scale < 5) return;

    // Patch size grows as we zoom out so the texture keeps a constant
    // on-screen frequency. Per-tile variation at survey zoom looks like
    // static, not like terrain.
    const patch = scale >= 20 ? 2 : scale >= 10 ? 4 : 8;
    const x0 = Math.floor(view.left / patch) * patch;
    const x1 = Math.ceil(view.right / patch) * patch;
    const y0 = Math.floor(view.top / patch) * patch;
    const y1 = Math.ceil(view.bottom / patch) * patch;
    const size = Math.ceil(scale * patch) + 1;

    for (let y = y0; y <= y1; y += patch) {
      const screenY = Math.floor(camera.worldToScreenY(y));
      for (let x = x0; x <= x1; x += patch) {
        const variant = tileHash(x / patch, y / patch) % GROUND_VARIANTS.length;
        if (variant === 0) continue; // base fill already covers it
        ctx.fillStyle = GROUND_VARIANTS[variant];
        ctx.fillRect(Math.floor(camera.worldToScreenX(x)), screenY, size, size);
      }
    }
  }

  private drawTerrain(input: RenderInput): void {
    const { ctx } = this;
    const { camera, scene } = input;
    if (!scene) return;
    const view = camera.viewport();
    const size = Math.ceil(camera.scale) + 1;

    for (const run of scene.terrain_runs) {
      if (run.y < view.top - 1 || run.y > view.bottom + 1) continue;
      if (run.x2 < view.left - 1 || run.x1 > view.right + 1) continue;
      const color = TERRAIN_COLORS[run.name];
      if (!color) continue;
      const left = Math.floor(camera.worldToScreenX(run.x1));
      const right = Math.floor(camera.worldToScreenX(run.x2 + 1));
      ctx.fillStyle = color;
      ctx.fillRect(
        left,
        Math.floor(camera.worldToScreenY(run.y)),
        Math.max(1, right - left),
        size,
      );
    }

    // Plate joints on paved tiles: a flat grey block reads as missing data.
    if (camera.scale >= 8) {
      ctx.strokeStyle = "rgba(0,0,0,.16)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (const run of scene.terrain_runs) {
        if (!TERRAIN_COLORS[run.name] || run.name.includes("water")) continue;
        if (run.y < view.top - 1 || run.y > view.bottom + 1) continue;
        const y = Math.floor(camera.worldToScreenY(run.y)) + 0.5;
        ctx.moveTo(Math.floor(camera.worldToScreenX(run.x1)), y);
        ctx.lineTo(Math.floor(camera.worldToScreenX(run.x2 + 1)), y);
      }
      ctx.stroke();
    }
  }

  private drawResources(input: RenderInput): void {
    const { ctx } = this;
    const { camera, scene } = input;
    if (!scene) return;
    const view = camera.viewport();
    const size = Math.max(1, Math.ceil(camera.scale));

    // Richness modulates alpha so a depleting patch visibly fades.
    for (const resource of scene.resources) {
      const { x, y } = resource.position;
      if (x < view.left - 1 || x > view.right + 1) continue;
      if (y < view.top - 1 || y > view.bottom + 1) continue;
      const color = ORE_COLORS[resource.name];
      if (!color) continue;
      const richness = Math.min(1, Math.max(0.25, resource.amount / 1500));
      const screenX = Math.floor(camera.worldToScreenX(x - 0.5));
      const screenY = Math.floor(camera.worldToScreenY(y - 0.5));

      ctx.globalAlpha = 0.4 + richness * 0.4;
      ctx.fillStyle = color;
      ctx.fillRect(screenX, screenY, size, size);

      // Grain: ore is a deposit, not a painted rectangle. Two deterministic
      // specks per tile give the patch texture without any per-frame cost.
      if (camera.scale >= 9) {
        const h = tileHash(x, y);
        ctx.globalAlpha = 0.30 + richness * 0.35;
        ctx.fillStyle = "rgba(255,255,255,.85)";
        const grain = Math.max(1, Math.round(camera.scale * 0.16));
        ctx.fillRect(
          screenX + (h % Math.max(1, size - grain)),
          screenY + ((h >> 7) % Math.max(1, size - grain)),
          grain,
          grain,
        );
        ctx.fillStyle = "rgba(0,0,0,.55)";
        ctx.fillRect(
          screenX + ((h >> 13) % Math.max(1, size - grain)),
          screenY + ((h >> 19) % Math.max(1, size - grain)),
          grain,
          grain,
        );
      }
    }
    ctx.globalAlpha = 1;
  }

  private drawNatural(input: RenderInput): void {
    const { ctx } = this;
    const { camera, scene } = input;
    if (!scene || camera.scale < 7) return;
    const view = camera.viewport();
    ctx.globalAlpha = 0.5;
    for (const item of scene.natural) {
      const { x, y } = item.position;
      if (x < view.left || x > view.right || y < view.top || y > view.bottom) {
        continue;
      }
      const screenX = camera.worldToScreenX(x);
      const screenY = camera.worldToScreenY(y);
      const radius = Math.max(1.5, camera.scale * 0.32);
      ctx.fillStyle = item.name.includes("tree") ? "#2f4526" : "#4a4336";
      ctx.beginPath();
      ctx.arc(screenX, screenY, radius, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  private drawGrid(input: RenderInput): void {
    const { ctx } = this;
    const { camera } = input;
    const view = camera.viewport();
    // Keep grid lines roughly 40px apart whatever the zoom.
    const step = niceStep(40 / camera.scale);
    ctx.strokeStyle = "rgba(255,255,255,.05)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = Math.ceil(view.left / step) * step; x <= view.right; x += step) {
      const sx = Math.floor(camera.worldToScreenX(x)) + 0.5;
      ctx.moveTo(sx, 0);
      ctx.lineTo(sx, camera.viewportHeight);
    }
    for (let y = Math.ceil(view.top / step) * step; y <= view.bottom; y += step) {
      const sy = Math.floor(camera.worldToScreenY(y)) + 0.5;
      ctx.moveTo(0, sy);
      ctx.lineTo(camera.viewportWidth, sy);
    }
    ctx.stroke();
  }

  private drawEntities(input: RenderInput): void {
    const { camera, scene, assets } = input;
    if (!scene) return;
    const view = camera.viewport();

    // Paint back to front so southern machines overlap northern ones, which
    // is how the game stacks its sprites.
    const visible = scene.entities
      .filter(
        (entity) =>
          entity.x > view.left - 4 &&
          entity.x < view.right + 4 &&
          entity.y > view.top - 4 &&
          entity.y < view.bottom + 4,
      )
      .sort((a, b) => a.y - b.y);

    for (const entity of visible) {
      this.drawEntity(entity, input);
    }
    void assets;
  }

  private drawEntity(entity: SceneEntity, input: RenderInput): void {
    const { ctx } = this;
    const { camera, assets, scene } = input;
    if (!scene) return;

    const size = footprint(entity, scene.prototypes);
    const screenX = camera.worldToScreenX(entity.x);
    const screenY = camera.worldToScreenY(entity.y);
    const tileW = size.width * camera.scale;
    const tileH = size.height * camera.scale;
    const role = roleOf(entity.name, entity.type);

    // Ground shadow: cheap, and it stops machines from floating on the grass.
    ctx.globalAlpha = 0.28;
    ctx.fillStyle = "#000";
    ctx.beginPath();
    ctx.ellipse(
      screenX,
      screenY + tileH * 0.34,
      tileW * 0.46,
      tileH * 0.2,
      0,
      0,
      Math.PI * 2,
    );
    ctx.fill();
    ctx.globalAlpha = 1;

    const direction = cardinal(entity.direction);
    const phase = Math.abs(((entity.x * 7 + entity.y * 13) | 0) % 16);
    const frame = assets.frameFor(entity.name, input.animationTick, phase);
    const sprite = assets.sprite(entity.name, direction, frame);

    if (sprite && sprite.width > 0) {
      // Scale by footprint width, preserving the sheet's aspect ratio, and
      // anchor the bottom of the art near the bottom of the tile so tall
      // machines lean north the way they do in game.
      const info = assets.spriteInfo(entity.name);
      const extra = info?.scale ?? 1;
      const drawW = tileW * extra;
      const drawH = (sprite.height / sprite.width) * drawW;
      ctx.drawImage(
        sprite,
        screenX - drawW / 2,
        screenY + tileH / 2 - drawH,
        drawW,
        drawH,
      );
    } else {
      this.drawChassis(screenX, screenY, tileW, tileH, role);
      const icon = assets.icon(entity.name);
      if (icon && icon.width > 0) {
        const iconSize = Math.min(tileW, tileH) * 0.72;
        ctx.drawImage(
          icon,
          screenX - iconSize / 2,
          screenY - iconSize / 2,
          iconSize,
          iconSize,
        );
      }
    }

    if (input.overlays.has("status")) {
      this.drawStatusBadge(entity, screenX, screenY, tileW, tileH);
    }
  }

  private drawChassis(
    screenX: number,
    screenY: number,
    tileW: number,
    tileH: number,
    role: ReturnType<typeof roleOf>,
  ): void {
    const { ctx } = this;
    const color = ROLE_COLORS[role];
    const x = screenX - tileW / 2;
    const y = screenY - tileH / 2;
    ctx.fillStyle = "rgba(20,23,26,.82)";
    ctx.fillRect(x, y, tileW, tileH);
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.75;
    ctx.lineWidth = Math.max(1, tileW * 0.045);
    ctx.strokeRect(x + 0.5, y + 0.5, tileW - 1, tileH - 1);
    ctx.globalAlpha = 1;
  }

  private drawStatusBadge(
    entity: SceneEntity,
    screenX: number,
    screenY: number,
    tileW: number,
    tileH: number,
  ): void {
    const cls = statusClass(entity.status);
    if (cls === "neutral" || cls === "working") {
      // Only faults and idleness earn ink; a healthy factory stays quiet.
      if (cls !== "working") return;
    }
    const { ctx } = this;
    const radius = Math.max(2, Math.min(6, tileW * 0.13));
    const x = screenX + tileW / 2 - radius * 1.1;
    const y = screenY - tileH / 2 + radius * 1.1;
    if (cls === "fault") {
      // Pulse only the faults, so the eye is pulled to what is broken.
      const pulse = 0.55 + 0.45 * Math.sin(Date.now() / 260);
      ctx.globalAlpha = pulse;
    }
    ctx.fillStyle = STATUS_COLORS[cls];
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = "rgba(0,0,0,.55)";
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  /** Animated chevrons showing which way each belt actually moves. */
  private drawFlow(input: RenderInput): void {
    const { ctx } = this;
    const { camera, scene } = input;
    if (!scene || camera.scale < 9) return;
    const view = camera.viewport();
    const phase = (Date.now() / 520) % 1;

    ctx.strokeStyle = "rgba(255,214,140,.85)";
    ctx.lineWidth = Math.max(1, camera.scale * 0.09);
    ctx.lineCap = "round";

    for (const entity of scene.entities) {
      const role = roleOf(entity.name, entity.type);
      if (role !== "logistics") continue;
      if (!entity.name.includes("belt")) continue;
      if (
        entity.x < view.left ||
        entity.x > view.right ||
        entity.y < view.top ||
        entity.y > view.bottom
      ) {
        continue;
      }
      const vector = DIRECTION_VECTORS[cardinal(entity.direction)];
      if (!vector) continue;
      const [dx, dy] = vector;
      const travel = (phase - 0.5) * camera.scale * 0.8;
      const cx = camera.worldToScreenX(entity.x) + dx * travel;
      const cy = camera.worldToScreenY(entity.y) + dy * travel;
      const arm = camera.scale * 0.22;
      // Chevron pointing along the belt direction.
      const px = -dy;
      const py = dx;
      ctx.beginPath();
      ctx.moveTo(cx - px * arm - dx * arm, cy - py * arm - dy * arm);
      ctx.lineTo(cx + dx * arm, cy + dy * arm);
      ctx.lineTo(cx + px * arm - dx * arm, cy + py * arm - dy * arm);
      ctx.stroke();
    }
  }

  /**
   * Electric poles with their supply area. This is proximity, same as the
   * backend graph: it shows reach, not the actual wire state, and the legend
   * says so.
   */
  private drawPowerNetwork(input: RenderInput): void {
    const { ctx } = this;
    const { camera, scene } = input;
    if (!scene) return;
    const view = camera.viewport();
    const poles = scene.entities.filter(
      (entity) => entity.type === "electric-pole" || entity.name.includes("pole"),
    );

    ctx.save();
    ctx.globalAlpha = 0.13;
    ctx.fillStyle = "#5dd589";
    for (const pole of poles) {
      if (
        pole.x < view.left - 8 ||
        pole.x > view.right + 8 ||
        pole.y < view.top - 8 ||
        pole.y > view.bottom + 8
      ) {
        continue;
      }
      const supply = 2.5; // small-electric-pole supply half-width, in tiles
      ctx.fillRect(
        camera.worldToScreenX(pole.x - supply),
        camera.worldToScreenY(pole.y - supply),
        supply * 2 * camera.scale,
        supply * 2 * camera.scale,
      );
    }
    ctx.restore();

    // Wires between poles within reach.
    ctx.strokeStyle = "rgba(93,213,137,.55)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i < poles.length; i++) {
      for (let j = i + 1; j < poles.length; j++) {
        const a = poles[i];
        const b = poles[j];
        const distance = Math.hypot(a.x - b.x, a.y - b.y);
        if (distance > 7.5) continue;
        ctx.moveTo(camera.worldToScreenX(a.x), camera.worldToScreenY(a.y));
        ctx.lineTo(camera.worldToScreenX(b.x), camera.worldToScreenY(b.y));
      }
    }
    ctx.stroke();
  }

  /** Material topology edges from the backend factory graph. */
  private drawTopology(input: RenderInput): void {
    const { ctx } = this;
    const { camera, graph, scene } = input;
    if (!graph || !scene || !graph.edges || !graph.nodes) return;

    const byId = new Map<string, { x: number; y: number }>();
    for (const node of graph.nodes) {
      if (node.position) {
        byId.set(String(node.id), node.position);
      }
    }
    if (byId.size === 0) return;

    ctx.save();
    ctx.lineWidth = Math.max(1.2, camera.scale * 0.055);
    ctx.setLineDash([camera.scale * 0.35, camera.scale * 0.28]);
    ctx.lineDashOffset = -(Date.now() / 90) % 1000;
    for (const edge of graph.edges) {
      const source = byId.get(String(edge.source));
      const target = byId.get(String(edge.target));
      if (!source || !target) continue;
      ctx.strokeStyle =
        edge.kind === "transfer"
          ? "rgba(238,108,104,.85)"
          : "rgba(106,181,247,.75)";
      ctx.beginPath();
      ctx.moveTo(
        camera.worldToScreenX(source.x),
        camera.worldToScreenY(source.y),
      );
      ctx.lineTo(
        camera.worldToScreenX(target.x),
        camera.worldToScreenY(target.y),
      );
      ctx.stroke();
    }
    ctx.restore();
  }

  private drawCharacter(input: RenderInput): void {
    const { ctx } = this;
    const { camera, scene } = input;
    const character = scene?.character;
    if (!character) return;
    const x = camera.worldToScreenX(character.x);
    const y = camera.worldToScreenY(character.y);
    const radius = Math.max(4, camera.scale * 0.34);
    const pulse = 0.5 + 0.5 * Math.sin(Date.now() / 420);

    ctx.globalAlpha = 0.22 * pulse + 0.1;
    ctx.fillStyle = "#ffd077";
    ctx.beginPath();
    ctx.arc(x, y, radius * 2.6, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;

    ctx.fillStyle = "#ffd077";
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#15120a";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  private drawLabels(input: RenderInput): void {
    const { ctx } = this;
    const { camera, scene } = input;
    if (!scene || camera.scale < 14) return;
    const view = camera.viewport();

    ctx.font = "600 10px ui-monospace, SFMono-Regular, Consolas, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";

    // Collapse identical adjacent names (a belt run) into one label per group.
    const drawn: Array<{ x: number; y: number }> = [];
    for (const entity of scene.entities) {
      if (
        entity.x < view.left ||
        entity.x > view.right ||
        entity.y < view.top ||
        entity.y > view.bottom
      ) {
        continue;
      }
      const x = camera.worldToScreenX(entity.x);
      const y = camera.worldToScreenY(entity.y);
      if (drawn.some((p) => Math.hypot(p.x - x, p.y - y) < 44)) continue;
      drawn.push({ x, y });
      const size = footprint(entity, scene.prototypes);
      const label = humanize(entity.name);
      const textY = y - (size.height * camera.scale) / 2 - 6;
      ctx.lineWidth = 3;
      ctx.strokeStyle = "rgba(10,12,13,.85)";
      ctx.strokeText(label, x, textY);
      ctx.fillStyle = "#d7dde1";
      ctx.fillText(label, x, textY);
    }
    ctx.textAlign = "left";
  }

  private drawHighlight(input: RenderInput): void {
    const { ctx } = this;
    const { camera, scene } = input;
    if (!scene) return;
    for (const [entity, color] of [
      [input.hovered, "rgba(255,255,255,.55)"] as const,
      [input.selected, "#eaa23a"] as const,
    ]) {
      if (!entity) continue;
      const size = footprint(entity, scene.prototypes);
      const w = size.width * camera.scale;
      const h = size.height * camera.scale;
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(
        camera.worldToScreenX(entity.x) - w / 2,
        camera.worldToScreenY(entity.y) - h / 2,
        w,
        h,
      );
    }
  }
}

/** Round a tile step up to 1, 2, 5, 10, 25, 50... so grid labels stay sane. */
export function niceStep(raw: number): number {
  const steps = [1, 2, 5, 10, 25, 50, 100, 250, 500];
  for (const step of steps) if (raw <= step) return step;
  return 1000;
}
