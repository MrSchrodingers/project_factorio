/**
 * The live factory map: camera control, data fetching and the HUD.
 *
 * Two behaviours are deliberate and worth stating, because both were bugs in
 * the previous map:
 *
 *  1. Panning and zooming never wait on the server. The camera is local; the
 *     scene is refetched only when the camera leaves the area already loaded.
 *     The old map re-rendered a PNG server-side for every interaction.
 *
 *  2. A scene that comes back empty does not erase the last good one. The
 *     experiment resets the world between generations, so entity_count drops
 *     to 1 (the character) on purpose. We keep drawing the last populated
 *     scene and say why, instead of blanking the panel.
 */

import { AssetLibrary } from "./assets";
import { Camera } from "./camera";
import { SceneRenderer, footprint } from "./render";
import {
  ORE_COLORS,
  ORE_LABELS,
  ROLE_COLORS,
  ROLE_LABELS,
  STATUS_LABELS,
  escapeHtml,
  humanize,
  roleOf,
  statusClass,
} from "./palette";
import type {
  FactoryGraph,
  MapOptions,
  OverlayKey,
  Scene,
  SceneEntity,
} from "./types";

const DEFAULT_OVERLAYS: OverlayKey[] = [
  "status",
  "flow",
  "resources",
  "grid",
  "labels",
];

/** How long the previous entity layer survives an empty scene, in ms. */
const RESET_GRACE_MS = 20000;

interface LoadedArea {
  centerX: number;
  centerY: number;
  radius: number;
}

export class FactoryMap {
  private readonly root: HTMLElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly renderer: SceneRenderer;
  private readonly camera = new Camera();
  private readonly assets: AssetLibrary;
  private readonly apiBase: string;
  private readonly refreshMs: number;

  private scene: Scene | null = null;
  private graph: FactoryGraph | null = null;
  private loaded: LoadedArea | null = null;
  private overlays = new Set<OverlayKey>(DEFAULT_OVERLAYS);

  private hovered: SceneEntity | null = null;
  private selected: SceneEntity | null = null;

  private dragging = false;
  private dragMoved = false;
  private lastPointer: { x: number; y: number } | null = null;

  private animationTick = 0;
  private lastTick: number | null = null;
  private lastTickAt = 0;
  private ticksPerSecond = 60;

  private fetching = false;
  private destroyed = false;
  private userHasMoved = false;
  private lastPopulatedAt = 0;
  private resetNoticeUntil = 0;

  private hudEls: Record<string, HTMLElement> = {};

  constructor(root: HTMLElement, options: MapOptions = {}) {
    this.root = root;
    this.apiBase = options.apiBase ?? "";
    this.refreshMs = Math.max(500, (options.refreshSeconds ?? 1.5) * 1000);
    this.assets = new AssetLibrary(this.apiBase);

    this.root.classList.add("fmap-root");
    this.root.innerHTML = "";
    this.canvas = document.createElement("canvas");
    this.canvas.className = "fmap-canvas";
    this.root.appendChild(this.canvas);
    this.renderer = new SceneRenderer(this.canvas);

    this.buildHud();
    this.installEvents();

    void this.assets.loadManifest();
    void this.refresh(true);
    this.loop();
    this.scheduleRefresh();
  }

  destroy(): void {
    this.destroyed = true;
  }

  // ---------------------------------------------------------------- HUD

  private buildHud(): void {
    const hud = document.createElement("div");
    hud.className = "fmap-hud";
    hud.innerHTML = `
      <div class="fmap-topbar">
        <span class="fmap-chip" data-el="status">conectando</span>
        <span class="fmap-chip fmap-mono" data-el="viewport">--</span>
        <span class="fmap-chip fmap-mono" data-el="counts">--</span>
        <span class="fmap-chip fmap-warn" data-el="reset" hidden>geracao reiniciou o mundo</span>
      </div>
      <div class="fmap-toolbar" role="group" aria-label="Camadas do mapa">
        ${overlayButton("status", "Status")}
        ${overlayButton("flow", "Fluxo")}
        ${overlayButton("resources", "Minerio")}
        ${overlayButton("power", "Energia")}
        ${overlayButton("topology", "Topologia")}
        ${overlayButton("labels", "Rotulos")}
        ${overlayButton("grid", "Grade")}
      </div>
      <div class="fmap-zoom" role="group" aria-label="Zoom">
        <button type="button" data-act="out" title="Afastar" aria-label="Afastar">&minus;</button>
        <button type="button" data-act="fit" title="Enquadrar a fabrica">fit</button>
        <button type="button" data-act="in" title="Aproximar" aria-label="Aproximar">+</button>
        <button type="button" data-act="full" title="Tela cheia" aria-label="Tela cheia">${EXPAND_ICON}</button>
      </div>
      <div class="fmap-legend" data-el="legend"></div>
      <div class="fmap-scale"><i data-el="scalebar"></i><span data-el="scaletext">--</span></div>
      <aside class="fmap-inspector" data-el="inspector" hidden></aside>
    `;
    this.root.appendChild(hud);

    for (const el of hud.querySelectorAll<HTMLElement>("[data-el]")) {
      this.hudEls[el.dataset.el as string] = el;
    }

    for (const button of hud.querySelectorAll<HTMLButtonElement>("[data-overlay]")) {
      const key = button.dataset.overlay as OverlayKey;
      button.classList.toggle("active", this.overlays.has(key));
      button.addEventListener("click", () => {
        if (this.overlays.has(key)) this.overlays.delete(key);
        else this.overlays.add(key);
        button.classList.toggle("active", this.overlays.has(key));
        this.renderLegend();
      });
    }

    for (const button of hud.querySelectorAll<HTMLButtonElement>("[data-act]")) {
      button.addEventListener("click", () => {
        const act = button.dataset.act;
        const cx = this.camera.viewportWidth / 2;
        const cy = this.camera.viewportHeight / 2;
        if (act === "in") {
          this.camera.zoomAt(1.3, cx, cy);
          this.userHasMoved = true;
        }
        if (act === "out") {
          this.camera.zoomAt(1 / 1.3, cx, cy);
          this.userHasMoved = true;
        }
        if (act === "fit") {
          this.userHasMoved = false;
          this.fitToFactory();
        }
        if (act === "full") void this.toggleFullscreen();
        this.maybeRefetch();
      });
    }

    this.renderLegend();
  }

  private renderLegend(): void {
    const legend = this.hudEls.legend;
    if (!legend) return;
    const parts: string[] = [];
    for (const [role, label] of Object.entries(ROLE_LABELS)) {
      parts.push(
        `<span class="fmap-key"><i style="background:${
          ROLE_COLORS[role as keyof typeof ROLE_COLORS]
        }"></i>${label}</span>`,
      );
    }
    if (this.overlays.has("resources")) {
      for (const [ore, label] of Object.entries(ORE_LABELS)) {
        if (!this.sceneHasOre(ore)) continue;
        parts.push(
          `<span class="fmap-key"><i style="background:${ORE_COLORS[ore]}"></i>${label}</span>`,
        );
      }
    }
    if (this.overlays.has("power")) {
      parts.push(
        `<span class="fmap-key fmap-key-note"><i style="background:#5dd589"></i>alcance do poste (proximidade, nao estado do fio)</span>`,
      );
    }
    legend.innerHTML = parts.join("");
  }

  private sceneHasOre(ore: string): boolean {
    if (!this.scene) return false;
    for (const resource of this.scene.resources) {
      if (resource.name === ore) return true;
    }
    return false;
  }

  // ------------------------------------------------------------- events

  private installEvents(): void {
    const canvas = this.canvas;

    const observer = new ResizeObserver(() => this.resize());
    observer.observe(this.root);
    this.resize();

    canvas.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        const rect = canvas.getBoundingClientRect();
        const factor = event.deltaY < 0 ? 1.16 : 1 / 1.16;
        this.camera.zoomAt(
          factor,
          event.clientX - rect.left,
          event.clientY - rect.top,
        );
        this.userHasMoved = true;
        this.maybeRefetch();
      },
      { passive: false },
    );

    canvas.addEventListener("pointerdown", (event) => {
      canvas.setPointerCapture(event.pointerId);
      this.dragging = true;
      this.dragMoved = false;
      this.lastPointer = { x: event.clientX, y: event.clientY };
      canvas.classList.add("dragging");
    });

    canvas.addEventListener("pointermove", (event) => {
      const rect = canvas.getBoundingClientRect();
      if (this.dragging && this.lastPointer) {
        const dx = event.clientX - this.lastPointer.x;
        const dy = event.clientY - this.lastPointer.y;
        if (Math.abs(dx) + Math.abs(dy) > 2) this.dragMoved = true;
        this.camera.panByPixels(dx, dy);
        this.lastPointer = { x: event.clientX, y: event.clientY };
        this.userHasMoved = true;
      } else {
        this.hovered = this.pick(
          event.clientX - rect.left,
          event.clientY - rect.top,
        );
        canvas.style.cursor = this.hovered ? "pointer" : "grab";
      }
    });

    const endDrag = () => {
      if (this.dragging) {
        this.dragging = false;
        this.lastPointer = null;
        canvas.classList.remove("dragging");
        this.maybeRefetch();
      }
    };
    canvas.addEventListener("pointerup", (event) => {
      const rect = canvas.getBoundingClientRect();
      const wasDrag = this.dragMoved;
      endDrag();
      if (!wasDrag) {
        this.selected = this.pick(
          event.clientX - rect.left,
          event.clientY - rect.top,
        );
        this.renderInspector();
      }
    });
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("pointerleave", () => {
      this.hovered = null;
      endDrag();
    });
    canvas.addEventListener("dblclick", () => {
      this.userHasMoved = false;
      this.fitToFactory();
      this.maybeRefetch();
    });

    document.addEventListener("keydown", (event) => {
      if (!this.root.isConnected) return;
      if (event.key === "Escape" && this.selected) {
        this.selected = null;
        this.renderInspector();
      }
    });
  }

  private async toggleFullscreen(): Promise<void> {
    try {
      if (document.fullscreenElement === this.root) await document.exitFullscreen();
      else await this.root.requestFullscreen();
    } catch {
      // Fullscreen can be blocked by policy; the map stays usable inline.
    }
  }

  private resize(): void {
    const rect = this.root.getBoundingClientRect();
    const dpr = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    this.canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    this.canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    this.canvas.style.width = `${rect.width}px`;
    this.canvas.style.height = `${rect.height}px`;
    const ctx = this.canvas.getContext("2d");
    ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.camera.resize(rect.width, rect.height);
  }

  private pick(screenX: number, screenY: number): SceneEntity | null {
    if (!this.scene) return null;
    const worldX = this.camera.screenToWorldX(screenX);
    const worldY = this.camera.screenToWorldY(screenY);
    let best: SceneEntity | null = null;
    let bestArea = Infinity;
    for (const entity of this.scene.entities) {
      const size = footprint(entity, this.scene.prototypes);
      const halfW = size.width / 2;
      const halfH = size.height / 2;
      if (
        worldX >= entity.x - halfW &&
        worldX <= entity.x + halfW &&
        worldY >= entity.y - halfH &&
        worldY <= entity.y + halfH
      ) {
        const area = size.width * size.height;
        // Prefer the smallest hit so an inserter on top of a belt wins.
        if (area < bestArea) {
          bestArea = area;
          best = entity;
        }
      }
    }
    return best;
  }

  // --------------------------------------------------------------- data

  private scheduleRefresh(): void {
    if (this.destroyed) return;
    window.setTimeout(() => {
      if (document.visibilityState === "visible") void this.refresh(false);
      this.scheduleRefresh();
    }, this.refreshMs);
  }

  private maybeRefetch(): void {
    if (!this.loaded) {
      void this.refresh(false);
      return;
    }
    const view = this.camera.viewport();
    const dx = Math.max(
      Math.abs(view.left - this.loaded.centerX),
      Math.abs(view.right - this.loaded.centerX),
    );
    const dy = Math.max(
      Math.abs(view.top - this.loaded.centerY),
      Math.abs(view.bottom - this.loaded.centerY),
    );
    // Refetch once the viewport approaches the edge of what we hold.
    if (Math.max(dx, dy) > this.loaded.radius * 0.8) void this.refresh(false);
  }

  private async refresh(initial: boolean): Promise<void> {
    if (this.fetching || this.destroyed) return;
    this.fetching = true;
    try {
      const radius = Math.min(
        96,
        Math.max(8, Math.ceil(this.camera.radiusTiles * 1.35)),
      );
      const params = new URLSearchParams();
      if (this.userHasMoved) {
        params.set("cx", this.camera.centerX.toFixed(2));
        params.set("cy", this.camera.centerY.toFixed(2));
        params.set("radius", String(radius));
      }
      const query = params.toString();
      const [sceneResponse, graphResponse] = await Promise.all([
        fetch(`${this.apiBase}/api/world/scene${query ? `?${query}` : ""}`, {
          cache: "no-store",
        }),
        fetch(`${this.apiBase}/api/factory-graph`, { cache: "no-store" }).catch(
          () => null,
        ),
      ]);
      if (!sceneResponse.ok) throw new Error(`HTTP ${sceneResponse.status}`);
      const scene = (await sceneResponse.json()) as Scene;
      this.applyScene(scene, initial);
      if (graphResponse && graphResponse.ok) {
        this.graph = (await graphResponse.json()) as FactoryGraph;
      }
      if (query) {
        this.loaded = {
          centerX: this.camera.centerX,
          centerY: this.camera.centerY,
          radius,
        };
      } else if (scene.bounds) {
        const b = scene.bounds;
        this.loaded = {
          centerX: (b.left_top.x + b.right_bottom.x) / 2,
          centerY: (b.left_top.y + b.right_bottom.y) / 2,
          radius:
            Math.max(
              b.right_bottom.x - b.left_top.x,
              b.right_bottom.y - b.left_top.y,
            ) / 2,
        };
      }
    } catch {
      this.setChip("status", "sem resposta do observador", "fmap-bad");
    } finally {
      this.fetching = false;
    }
  }

  /**
   * Replace the scene, but never let a generation reset blank the panel.
   * The runner wipes the world between generations, so an almost-empty
   * scene is expected and temporary; we keep the previous geometry visible
   * and label what happened.
   */
  private applyScene(scene: Scene, initial: boolean): void {
    const populated = scene.entity_count > 0;
    const previous = this.scene;
    const hadPopulated = previous !== null && previous.entity_count > 0;

    if (populated) {
      this.lastPopulatedAt = Date.now();
    } else if (hadPopulated) {
      this.resetNoticeUntil = Date.now() + RESET_GRACE_MS;
    }

    this.scene = scene;

    // Keep the old entity layer while the new generation is bootstrapping,
    // so the map does not flash empty at every generation boundary.
    if (!populated && hadPopulated && previous) {
      const age = Date.now() - this.lastPopulatedAt;
      if (age < RESET_GRACE_MS) {
        this.scene = {
          ...scene,
          entities: previous.entities,
          entity_count: previous.entity_count,
          prototypes: Object.keys(scene.prototypes).length
            ? scene.prototypes
            : previous.prototypes,
        };
      }
    }

    if (initial && !this.userHasMoved) this.fitToFactory();
    this.updateHud();
    this.renderLegend();
    if (this.selected) {
      const match = this.scene.entities.find(
        (entity) =>
          entity.name === this.selected?.name &&
          entity.x === this.selected?.x &&
          entity.y === this.selected?.y,
      );
      this.selected = match ?? null;
      this.renderInspector();
    }
  }

  /**
   * Frame whatever the world actually has. Between generations the runner
   * wipes every entity, so falling back to ore patches and then to the
   * surveyed bounds keeps the view on the work area instead of zooming to
   * maximum on a lone character.
   */
  private fitToFactory(): void {
    const scene = this.scene;
    if (!scene) return;

    const points: Array<{ x: number; y: number }> = scene.entities.map(
      (entity) => ({ x: entity.x, y: entity.y }),
    );
    if (points.length === 0) {
      for (const resource of scene.resources) {
        points.push({ x: resource.position.x, y: resource.position.y });
      }
      for (const run of scene.terrain_runs) {
        points.push({ x: run.x1, y: run.y });
        points.push({ x: run.x2, y: run.y });
      }
    }
    if (scene.character) {
      points.push({ x: scene.character.x, y: scene.character.y });
    }

    if (points.length === 0) {
      if (scene.bounds) {
        this.camera.fit(
          scene.bounds.left_top.x,
          scene.bounds.left_top.y,
          scene.bounds.right_bottom.x,
          scene.bounds.right_bottom.y,
        );
      }
      return;
    }

    let left = Infinity;
    let top = Infinity;
    let right = -Infinity;
    let bottom = -Infinity;
    for (const point of points) {
      if (point.x < left) left = point.x;
      if (point.x > right) right = point.x;
      if (point.y < top) top = point.y;
      if (point.y > bottom) bottom = point.y;
    }
    this.camera.fit(left, top, right, bottom, 6);
  }

  // ---------------------------------------------------------- HUD update

  private updateHud(): void {
    const scene = this.scene;
    if (!scene) return;

    if (scene.connected) {
      this.setChip("status", "Factorio ao vivo", "fmap-good");
    } else {
      this.setChip("status", "Factorio offline", "fmap-bad");
    }

    const view = this.camera.viewport();
    this.setText(
      "viewport",
      `x ${this.camera.centerX.toFixed(0)} y ${this.camera.centerY.toFixed(0)} | ` +
        `${(view.right - view.left).toFixed(0)}x${(view.bottom - view.top).toFixed(0)} tiles`,
    );

    const byRole = new Map<string, number>();
    for (const entity of scene.entities) {
      const role = roleOf(entity.name, entity.type);
      byRole.set(role, (byRole.get(role) ?? 0) + 1);
    }
    const faults = scene.entities.filter(
      (entity) => statusClass(entity.status) === "fault",
    ).length;
    this.setText(
      "counts",
      `${scene.entity_count} entidades | ${byRole.get("logistics") ?? 0} logistica | ` +
        `${byRole.get("processing") ?? 0} processo` +
        (faults ? ` | ${faults} em falha` : ""),
    );

    const resetChip = this.hudEls.reset;
    if (resetChip) resetChip.hidden = Date.now() > this.resetNoticeUntil;

    // Tick rate, used to advance sprite animation smoothly between polls.
    if (typeof scene.tick === "number") {
      const now = performance.now();
      if (this.lastTick !== null && now > this.lastTickAt) {
        const deltaTicks = scene.tick - this.lastTick;
        const deltaSeconds = (now - this.lastTickAt) / 1000;
        if (deltaTicks > 0 && deltaSeconds > 0.2) {
          const rate = deltaTicks / deltaSeconds;
          if (rate > 1 && rate < 600) {
            this.ticksPerSecond = this.ticksPerSecond * 0.7 + rate * 0.3;
          }
        }
      }
      this.lastTick = scene.tick;
      this.lastTickAt = now;
      this.animationTick = scene.tick;
    }
  }

  private renderInspector(): void {
    const inspector = this.hudEls.inspector;
    if (!inspector) return;
    const entity = this.selected;
    if (!entity || !this.scene) {
      inspector.hidden = true;
      inspector.innerHTML = "";
      return;
    }
    const size = footprint(entity, this.scene.prototypes);
    const cls = statusClass(entity.status);
    const statusText = entity.status
      ? STATUS_LABELS[entity.status] ?? entity.status.replace(/_/g, " ")
      : "sem status";
    const role = roleOf(entity.name, entity.type);
    const iconUrl = `${this.apiBase}/api/assets/icon/${encodeURIComponent(entity.name)}.png`;
    inspector.hidden = false;
    inspector.innerHTML = `
      <button class="fmap-close" type="button" aria-label="Fechar">&times;</button>
      <header>
        <img src="${escapeHtml(iconUrl)}" alt="">
        <div>
          <strong>${escapeHtml(humanize(entity.name))}</strong>
          <small>${ROLE_LABELS[role]} | ${size.width}x${size.height} tiles</small>
        </div>
      </header>
      <dl>
        <div><dt>Estado</dt><dd class="fmap-${cls}">${escapeHtml(statusText)}</dd></div>
        <div><dt>Posicao</dt><dd>x ${entity.x.toFixed(1)} y ${entity.y.toFixed(1)}</dd></div>
        ${entity.recipe ? `<div><dt>Receita</dt><dd>${escapeHtml(humanize(entity.recipe))}</dd></div>` : ""}
        ${
          entity.coal_fuel !== null && entity.coal_fuel !== undefined
            ? `<div><dt>Carvao</dt><dd>${entity.coal_fuel}</dd></div>`
            : ""
        }
        ${
          entity.energy !== null && entity.energy !== undefined
            ? `<div><dt>Energia</dt><dd>${Math.round(entity.energy)} J</dd></div>`
            : ""
        }
      </dl>
    `;
    inspector.querySelector(".fmap-close")?.addEventListener("click", () => {
      this.selected = null;
      this.renderInspector();
    });
  }

  private updateScaleBar(): void {
    const bar = this.hudEls.scalebar;
    const text = this.hudEls.scaletext;
    if (!bar || !text) return;
    const tiles = niceTiles(90 / this.camera.scale);
    bar.style.width = `${Math.round(tiles * this.camera.scale)}px`;
    text.textContent = `${tiles} tiles`;
  }

  private setChip(key: string, label: string, cls: string): void {
    const el = this.hudEls[key];
    if (!el) return;
    el.textContent = label;
    el.className = `fmap-chip ${cls}`;
  }

  private setText(key: string, value: string): void {
    const el = this.hudEls[key];
    if (el) el.textContent = value;
  }

  // --------------------------------------------------------------- loop

  private loop = (): void => {
    if (this.destroyed) return;
    // Advance the animation clock between polls using the measured tick rate,
    // so belts keep moving instead of stepping once per fetch.
    if (this.lastTickAt) {
      const elapsed = (performance.now() - this.lastTickAt) / 1000;
      this.animationTick = (this.lastTick ?? 0) + elapsed * this.ticksPerSecond;
    }
    this.renderer.draw({
      scene: this.scene,
      graph: this.graph,
      camera: this.camera,
      assets: this.assets,
      overlays: this.overlays,
      hovered: this.hovered,
      selected: this.selected,
      animationTick: Math.floor(this.animationTick),
    });
    this.updateScaleBar();
    requestAnimationFrame(this.loop);
  };
}

const EXPAND_ICON =
  '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">' +
  '<path fill="currentColor" d="M1 1h6v2H3v4H1V1zm14 0v6h-2V3H9V1h6zM1 9h2v4h4v2H1V9zm12 0h2v6H9v-2h4V9z"/>' +
  "</svg>";

function overlayButton(key: OverlayKey, label: string): string {
  return `<button type="button" data-overlay="${key}">${label}</button>`;
}

function niceTiles(raw: number): number {
  const steps = [1, 2, 5, 10, 20, 50, 100, 200, 500];
  for (const step of steps) if (raw <= step) return step;
  return 1000;
}
