/**
 * Sprite and icon loading.
 *
 * Every frame is a separate immutable URL on the server, so the browser
 * cache does the heavy lifting: the first pass over an animation costs one
 * request per frame and every later pass is free. We keep decoded images in
 * memory and never block a draw on a pending load — a missing sprite simply
 * falls back to the inventory icon, and a missing icon to a vector chassis.
 */

import type { SpriteManifest, SpriteManifestEntry } from "./types";

type LoadState = "pending" | "ready" | "failed";

interface Slot {
  state: LoadState;
  image?: HTMLImageElement;
}

export class AssetLibrary {
  private readonly slots = new Map<string, Slot>();
  private manifest: SpriteManifest = { sprites: {}, count: 0 };
  private readonly apiBase: string;
  private onLoad: (() => void) | null = null;

  constructor(apiBase: string) {
    this.apiBase = apiBase;
  }

  setInvalidationCallback(callback: () => void): void {
    this.onLoad = callback;
  }

  async loadManifest(): Promise<void> {
    try {
      const response = await fetch(`${this.apiBase}/api/assets/sprites.json`, {
        cache: "no-store",
      });
      if (!response.ok) return;
      const payload = (await response.json()) as SpriteManifest;
      if (payload && payload.sprites) this.manifest = payload;
    } catch {
      // Sprites are an enhancement; icons still work without the manifest.
    }
  }

  spriteInfo(name: string): SpriteManifestEntry | null {
    return this.manifest.sprites[name] ?? null;
  }

  hasSprite(name: string): boolean {
    return Boolean(this.manifest.sprites[name]);
  }

  /**
   * Animation frame for an entity at a given game tick. Entities are offset
   * by position so a row of identical machines does not pulse in lockstep,
   * which reads as a rendering artefact rather than as a working factory.
   */
  frameFor(name: string, tick: number, phase: number): number {
    const info = this.spriteInfo(name);
    if (!info || info.frames <= 1) return 0;
    const step = Math.max(1, info.ticks_per_frame);
    return (Math.floor(tick / step) + phase) % info.frames;
  }

  sprite(name: string, direction: number, frame: number): HTMLImageElement | null {
    if (!this.hasSprite(name)) return null;
    const key = `s:${name}:${direction}:${frame}`;
    return this.image(
      key,
      `${this.apiBase}/api/assets/sprite/${encodeURIComponent(name)}.png` +
        `?direction=${direction}&frame=${frame}`,
    );
  }

  icon(name: string): HTMLImageElement | null {
    const key = `i:${name}`;
    return this.image(
      key,
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
      this.onLoad?.();
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
