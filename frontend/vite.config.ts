import { defineConfig } from "vite";
import { resolve } from "node:path";

// The dashboard is served by FastAPI from static/. Building to a fixed,
// unhashed filename keeps deployment to "copy the file", with no Node on
// the serving path and no index.html rewriting.
export default defineConfig({
  build: {
    outDir: resolve(__dirname, "../src/factorio_ai_lab/dashboard/static/map"),
    emptyOutDir: true,
    target: "es2022",
    sourcemap: false,
    lib: {
      entry: resolve(__dirname, "src/main.ts"),
      name: "FactoryMap",
      formats: ["iife"],
      fileName: () => "factory-map.js",
    },
  },
});
