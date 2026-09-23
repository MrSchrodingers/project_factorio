/**
 * Sprite, icon and ground-tile loading.
 *
 * Every frame and every tile is a separate immutable URL on the server, so the
 * browser cache does the heavy lifting: the first pass over an animation costs
 * one request per frame and every later pass is free. Decoded images are kept
 * in memory and a draw never blocks on a pending load.
 *
 * Animation only starts once every frame of a sprite is decoded. Advancing the
 * frame counter while frames are still arriving makes a machine flicker
 * between its sprite and its fallback on the first pass, which reads as a
 * rendering fault rather than as a working factory.
 */

import type { SpriteManifest, SpriteManifestEntry } from "./types";

/** Ground variety cap; see terrainVariants. */
const MAX_TERRAIN_VARIANTS = 8;

type LoadState = "pending" | "ready" | "failed";

interface Slot {
  state: LoadState;
  image?: HTMLImageElement;
}

export interface TileManifest {
  tile_pixels: number;
  terrain: Record<string, number>;
  resources: Record<string, number>;
}

export class AssetLibrary {
  private readonly slots = new Map<string, Slot>();
  private manifest: SpriteManifest = { sprites: {}, count: 0 };
  private tiles: TileManifest = { tile_pixels: 32, terrain: {}, resources: {} };
  private readonly apiBase: string;

  constructor(apiBase: string) {
    this.apiBase = apiBase;
  }

  async loadManifest(): Promise<void> {
    await Promise.all([
      this.fetchJson<SpriteManifest>("/api/assets/sprites.json").then((payload) => {
        if (payload?.sprites) this.manifest = payload;
      }),
      this.fetchJson<TileManifest>("/api/assets/tiles.json").then((payload) => {
        if (payload?.terrain) this.tiles = payload;
      }),
    ]);
  }

  private async fetchJson<T>(path: string): Promise<T | null> {
    try {
      const response = await fetch(`${this.apiBase}${path}`, { cache: "no-store" });
      if (!response.ok) return null;
      return (await response.json()) as T;
    } catch {
      // Textures are an enhancement; the map degrades to flat colour.
      return null;
    }
  }

  get tilePixels(): number {
    return this.tiles.tile_pixels || 32;
  }

  /**
   * Distinct tiles actually used per terrain kind.
   *
   * The sheets carry up to 128 columns, but past the first handful they are
   * near-duplicates, and hashing freely across all of them turned one screen
   * of ground into ~240 separate requests. Capping the variety keeps the
   * texture from tiling visibly while loading a small, cacheable set.
   */
  terrainVariants(kind: string): number {
    return Math.min(this.tiles.terrain[kind] ?? 0, MAX_TERRAIN_VARIANTS);
  }

  resourceVariants(kind: string): number {
    return this.tiles.resources[kind] ?? 0;
  }

  /** One 32px ground tile. `variant` wraps, so any hash can be passed in. */
  terrainTile(kind: string, variant: number): HTMLImageElement | null {
    const count = this.terrainVariants(kind);
    if (!count) return null;
    const index = ((variant % count) + count) % count;
    return this.image(
      `t:${kind}:${index}`,
      `${this.apiBase}/api/assets/tile/terrain/${encodeURIComponent(kind)}.png?variant=${index}`,
    );
  }

  /** Ore tile. Columns run sparse to dense, so richness picks the column. */
  resourceTile(kind: string, variant: number): HTMLImageElement | null {
    const count = this.resourceVariants(kind);
    if (!count) return null;
    const index = ((variant % count) + count) % count;
    return this.image(
      `r:${kind}:${index}`,
      `${this.apiBase}/api/assets/tile/resource/${encodeURIComponent(kind)}.png?variant=${index}`,
    );
  }

  spriteInfo(name: string): SpriteManifestEntry | null {
    return this.manifest.sprites[name] ?? null;
  }

  hasSprite(name: string): boolean {
    return Boolean(this.manifest.sprites[name]);
  }

  /**
   * The whole animation as one image. One request per entity and direction,
   * instead of one per frame: the per-frame version saturated the browser's
   * per-origin connection limit and starved the ground textures, which showed
   * up as the map flickering between textured and flat while it loaded.
   */
  strip(name: string, direction: number): HTMLImageElement | null {
    if (!this.hasSprite(name)) return null;
    return this.image(
      `p:${name}:${direction}`,
      `${this.apiBase}/api/assets/strip/${encodeURIComponent(name)}.png` +
        `?direction=${direction}`,
    );
  }

  /** Frames actually present in the strip (the server samples long loops). */
  stripFrames(name: string): number {
    const info = this.spriteInfo(name);
    if (!info) return 1;
    return Math.max(1, Math.min(info.frames, this.manifest.strip_frames ?? 12));
  }

  /**
   * Animation frame index within the strip. Entities are offset by position
   * so a row of identical machines does not pulse in lockstep, which reads as
   * a rendering artefact rather than as a working factory.
   */
  frameFor(name: string, tick: number, phase: number): number {
    const frames = this.stripFrames(name);
    if (frames <= 1) return 0;
    const info = this.spriteInfo(name);
    const step = Math.max(1, info?.ticks_per_frame ?? 4);
    return (Math.floor(tick / step) + phase) % frames;
  }

  sprite(name: string, direction: number, frame: number): HTMLImageElement | null {
    if (!this.hasSprite(name)) return null;
    return this.image(
      `s:${name}:${direction}:${frame}`,
      `${this.apiBase}/api/assets/sprite/${encodeURIComponent(name)}.png` +
        `?direction=${direction}&frame=${frame}`,
    );
  }

  icon(name: string): HTMLImageElement | null {
    return this.image(
      `i:${name}`,
      `${this.apiBase}/api/assets/icon/${encodeURIComponent(name)}.png`,
    );
  }

  private image(key: string, url: string): HTMLImageElement | null {
    const existing = this.slots.get(key);
    if (existing) {
      return existing.state === "ready" ? existing.image ?? null : null;
    }
    const slot: Slot = { state: "pending" };
    this.slots.set(key, slot);
    const image = new Image();
    image.decoding = "async";
    image.onload = () => {
      slot.state = "ready";
      slot.image = image;
    };
    image.onerror = () => {
      slot.state = "failed";
    };
    image.src = url;
    return null;
  }

  stats(): { cached: number; ready: number; sprites: number } {
    let ready = 0;
    for (const slot of this.slots.values()) if (slot.state === "ready") ready++;
    return { cached: this.slots.size, ready, sprites: this.manifest.count };
  }
}
