/**
 * Colour vocabulary for the map.
 *
 * Ore colours follow the in-game tint so the survey view reads the same way
 * the game does. Status colours are a separate, deliberately small scale:
 * green means the machine is doing its job, amber means it is idle for a
 * supply reason, red means it is starved of fuel or power. Anything else is
 * neutral, so the eye is drawn only to real faults.
 */

export const ORE_COLORS: Record<string, string> = {
  "iron-ore": "#7ea6c4",
  "copper-ore": "#cf7a4c",
  coal: "#4a4f55",
  stone: "#bda77c",
  "uranium-ore": "#66c24d",
  "crude-oil": "#2f2823",
};

export const ORE_LABELS: Record<string, string> = {
  "iron-ore": "Ferro",
  "copper-ore": "Cobre",
  coal: "Carvão",
  stone: "Pedra",
  "uranium-ore": "Urânio",
  "crude-oil": "Petróleo",
};

export const TERRAIN_COLORS: Record<string, string> = {
  water: "#1d3b52",
  deepwater: "#152c40",
  "water-green": "#1f4048",
  "deepwater-green": "#16313a",
  "water-shallow": "#27506b",
  "water-mud": "#33413f",
  "stone-path": "#5b5750",
  concrete: "#5d6166",
  "refined-concrete": "#54585c",
  "hazard-concrete-left": "#8a7734",
  "hazard-concrete-right": "#8a7734",
  "refined-hazard-concrete-left": "#7d6c31",
  "refined-hazard-concrete-right": "#7d6c31",
};

export const GROUND_BASE = "#3f4430";
/* Low-contrast neighbours of the base. Wider steps read as pixel noise at
   survey zoom instead of as ground. */
export const GROUND_VARIANTS = ["#3f4430", "#424733", "#3c412d", "#444935"];

/** Status buckets. Everything not listed is treated as neutral. */
const FAULT_STATUS = new Set([
  "no_fuel",
  "no_power",
  "low_power",
  "not_plugged_in_electric_network",
  "disabled",
  "no_minable_resources",
]);

const IDLE_STATUS = new Set([
  "no_ingredients",
  "full_output",
  "waiting_for_space_in_destination",
  "waiting_for_source_items",
  "item_ingredient_shortage",
  "fluid_ingredient_shortage",
  "no_research_in_progress",
  "no_recipe",
]);

export type StatusClass = "working" | "idle" | "fault" | "neutral";

export function statusClass(status: string | null): StatusClass {
  if (!status) return "neutral";
  if (status === "working" || status === "normal") return "working";
  if (FAULT_STATUS.has(status)) return "fault";
  if (IDLE_STATUS.has(status)) return "idle";
  return "neutral";
}

export const STATUS_COLORS: Record<StatusClass, string> = {
  working: "#5dd589",
  idle: "#eaa23a",
  fault: "#ee6c68",
  neutral: "#8b949b",
};

export const STATUS_LABELS: Record<string, string> = {
  working: "operando",
  normal: "normal",
  no_fuel: "sem combustível",
  no_power: "sem energia",
  low_power: "energia baixa",
  not_plugged_in_electric_network: "fora da rede elétrica",
  no_ingredients: "sem insumo",
  item_ingredient_shortage: "falta insumo",
  fluid_ingredient_shortage: "falta fluido",
  full_output: "saída cheia",
  waiting_for_space_in_destination: "destino cheio",
  no_minable_resources: "recurso esgotado",
  no_research_in_progress: "sem pesquisa",
  disabled: "desligado",
};

/** Broad role of an entity, used for the vector chassis and the legend. */
export type Role = "extraction" | "processing" | "logistics" | "power" | "storage" | "other";

export function roleOf(name: string, type: string | null): Role {
  const n = name.toLowerCase();
  const t = (type ?? "").toLowerCase();
  if (t === "mining-drill" || n.includes("mining-drill") || t === "pumpjack") {
    return "extraction";
  }
  if (
    t === "furnace" ||
    t === "assembling-machine" ||
    t === "lab" ||
    t === "rocket-silo" ||
    n.includes("furnace") ||
    n.includes("assembling") ||
    n.includes("refinery") ||
    n.includes("chemical-plant")
  ) {
    return "processing";
  }
  if (
    t === "transport-belt" ||
    t === "underground-belt" ||
    t === "splitter" ||
    t === "inserter" ||
    t === "pipe" ||
    t === "pipe-to-ground" ||
    n.includes("belt") ||
    n.includes("inserter") ||
    n.includes("pipe") ||
    n.includes("splitter")
  ) {
    return "logistics";
  }
  if (
    t === "electric-pole" ||
    t === "boiler" ||
    t === "generator" ||
    t === "solar-panel" ||
    t === "accumulator" ||
    t === "offshore-pump" ||
    n.includes("pole") ||
    n.includes("boiler") ||
    n.includes("steam") ||
    n.includes("solar") ||
    n.includes("accumulator")
  ) {
    return "power";
  }
  if (t === "container" || t === "logistic-container" || n.includes("chest")) {
    return "storage";
  }
  return "other";
}

export const ROLE_COLORS: Record<Role, string> = {
  extraction: "#c9a227",
  processing: "#6ab5f7",
  logistics: "#eaa23a",
  power: "#5dd589",
  storage: "#b993f6",
  other: "#8b949b",
};

export const ROLE_LABELS: Record<Role, string> = {
  extraction: "Extração",
  processing: "Processamento",
  logistics: "Logística",
  power: "Energia",
  storage: "Estoque",
  other: "Outros",
};

/** Deterministic small hash, used to vary ground tiles without randomness. */
export function tileHash(x: number, y: number): number {
  let h = (x | 0) * 374761393 + (y | 0) * 668265263;
  h = (h ^ (h >> 13)) * 1274126177;
  return (h ^ (h >> 16)) >>> 0;
}

export function humanize(name: string): string {
  return name
    .split("-")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/**
 * Escape before interpolating into markup. Entity and recipe names come from
 * the Factorio runtime rather than from a user, but they still reach the DOM
 * as a string, and the cost of being wrong about that is an injected node.
 */
export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
