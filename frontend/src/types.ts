/** Wire contract of GET /api/world/scene. */

export interface Point {
  x: number;
  y: number;
}

export interface Bounds {
  left_top: Point;
  right_bottom: Point;
}

export interface SceneEntity {
  name: string;
  type: string | null;
  x: number;
  y: number;
  direction: number;
  status: string | null;
  recipe: string | null;
  energy: number | null;
  coal_fuel: number | null;
}

export interface SceneResource {
  name: string;
  amount: number;
  position: Point;
}

export interface SceneNatural {
  name: string;
  direction: number;
  position: Point;
}

/** A horizontal run of identical tiles: [x1, x2] inclusive on row y. */
export interface TerrainRun {
  name: string;
  y: number;
  x1: number;
  x2: number;
}

export interface EntityPrototype {
  name: string;
  type: string;
  tile_width: number;
  tile_height: number;
  /** [left, top, right, bottom] relative to the entity position. */
  selection_box: [number, number, number, number];
  collision_box: [number, number, number, number];
}

export interface Scene {
  connected: boolean;
  tick: number | null;
  bounds: Bounds | null;
  center: Point | null;
  entities: SceneEntity[];
  entity_count: number;
  character: SceneEntity | null;
  resources: SceneResource[];
  natural: SceneNatural[];
  terrain_runs: TerrainRun[];
  water_tile_count: number;
  prototypes: Record<string, EntityPrototype>;
  prototype_count: number;
  error: string | null;
}

export interface SpriteManifestEntry {
  frames: number;
  ticks_per_frame: number;
  directional: boolean;
  scale: number;
}

export interface SpriteManifest {
  sprites: Record<string, SpriteManifestEntry>;
  count: number;
  /** Frames the server packs into one strip; long loops are sampled down. */
  strip_frames?: number;
}

/** Physical topology from GET /api/factory-graph, drawn as an overlay. */
export interface GraphEdge {
  source: number | string;
  target: number | string;
  kind: string;
}

export interface GraphNode {
  id: number | string;
  name: string;
  role?: string;
  position?: Point;
}

export interface FactoryGraph {
  nodes?: GraphNode[];
  edges?: GraphEdge[];
  metrics?: Record<string, number>;
}

export type OverlayKey =
  | "labels"
  | "status"
  | "flow"
  | "power"
  | "topology"
  | "resources"
  | "grid";

export interface MapOptions {
  /** Base URL for the dashboard API; empty string means same origin. */
  apiBase?: string;
  /** Seconds between scene refreshes while the tab is visible. */
  refreshSeconds?: number;
}
