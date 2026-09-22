/**
 * World<->screen mapping for the factory map.
 *
 * The world is measured in Factorio tiles with +y pointing south, which is
 * also the canvas convention, so no axis flip is needed. `scale` is pixels
 * per tile and is the only thing zoom changes; panning moves the centre.
 */

export interface Viewport {
  /** Visible world rectangle, in tiles. */
  left: number;
  top: number;
  right: number;
  bottom: number;
}

const MIN_SCALE = 1.5; // ~1.5 px per tile: a wide survey view
const MAX_SCALE = 96; // ~3x the native 32 px tile: close inspection

export class Camera {
  centerX = 0;
  centerY = 0;
  scale = 18;

  private width = 1;
  private height = 1;

  resize(width: number, height: number): void {
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
  }

  get viewportWidth(): number {
    return this.width;
  }

  get viewportHeight(): number {
    return this.height;
  }

  /** Half-diagonal of the visible area, in tiles: what the API calls radius. */
  get radiusTiles(): number {
    const halfW = this.width / (2 * this.scale);
    const halfH = this.height / (2 * this.scale);
    return Math.max(halfW, halfH);
  }

  viewport(): Viewport {
    const halfW = this.width / (2 * this.scale);
    const halfH = this.height / (2 * this.scale);
    return {
      left: this.centerX - halfW,
      top: this.centerY - halfH,
      right: this.centerX + halfW,
      bottom: this.centerY + halfH,
    };
  }

  worldToScreenX(worldX: number): number {
    return (worldX - this.centerX) * this.scale + this.width / 2;
  }

  worldToScreenY(worldY: number): number {
    return (worldY - this.centerY) * this.scale + this.height / 2;
  }

  screenToWorldX(screenX: number): number {
    return (screenX - this.width / 2) / this.scale + this.centerX;
  }

  screenToWorldY(screenY: number): number {
    return (screenY - this.height / 2) / this.scale + this.centerY;
  }

  panByPixels(dx: number, dy: number): void {
    this.centerX -= dx / this.scale;
    this.centerY -= dy / this.scale;
  }

  /** Zoom keeping the world point under (screenX, screenY) pinned. */
  zoomAt(factor: number, screenX: number, screenY: number): void {
    const anchorWorldX = this.screenToWorldX(screenX);
    const anchorWorldY = this.screenToWorldY(screenY);
    const next = clamp(this.scale * factor, MIN_SCALE, MAX_SCALE);
    if (next === this.scale) return;
    this.scale = next;
    // Re-solve the centre so the anchor lands on the same pixel again.
    this.centerX = anchorWorldX - (screenX - this.width / 2) / this.scale;
    this.centerY = anchorWorldY - (screenY - this.height / 2) / this.scale;
  }

  /** Frame a world rectangle with margin, without exceeding the zoom limits. */
  fit(
    left: number,
    top: number,
    right: number,
    bottom: number,
    marginTiles = 4,
    minimumSpan = 28,
  ): void {
    // A single point has zero extent. Without a floor the fit would pick the
    // maximum zoom and frame a dozen tiles around one entity, which reads as
    // a broken map rather than as a close-up.
    const spanX = Math.max(minimumSpan, right - left + marginTiles * 2);
    const spanY = Math.max(minimumSpan, bottom - top + marginTiles * 2);
    this.centerX = (left + right) / 2;
    this.centerY = (top + bottom) / 2;
    this.scale = clamp(
      Math.min(this.width / spanX, this.height / spanY),
      MIN_SCALE,
      MAX_SCALE,
    );
  }

  get minScale(): number {
    return MIN_SCALE;
  }

  get maxScale(): number {
    return MAX_SCALE;
  }
}

export function clamp(value: number, low: number, high: number): number {
  return value < low ? low : value > high ? high : value;
}
