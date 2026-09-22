/**
 * Entry point. Mounts the factory map into whatever container the dashboard
 * gives it, and exposes a small global so the legacy dashboard script can
 * hand over without importing a module.
 */

import { FactoryMap } from "./map";
import type { MapOptions } from "./types";
import "./style.css";

declare global {
  interface Window {
    FactoryMap?: {
      mount: (target: string | HTMLElement, options?: MapOptions) => FactoryMap | null;
      instance: FactoryMap | null;
    };
  }
}

let instance: FactoryMap | null = null;

function mount(
  target: string | HTMLElement,
  options: MapOptions = {},
): FactoryMap | null {
  const element =
    typeof target === "string" ? document.querySelector<HTMLElement>(target) : target;
  if (!element) {
    console.warn("[factory-map] container nao encontrado:", target);
    return null;
  }
  instance?.destroy();
  instance = new FactoryMap(element, options);
  if (window.FactoryMap) window.FactoryMap.instance = instance;
  return instance;
}

window.FactoryMap = { mount, instance: null };

// Auto-mount when the dashboard markup already provides the container, so the
// page needs no inline script.
function autoMount(): void {
  const element = document.querySelector<HTMLElement>("[data-factory-map]");
  if (element && !instance) mount(element);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", autoMount);
} else {
  autoMount();
}

export { FactoryMap, mount };
