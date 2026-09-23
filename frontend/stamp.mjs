/**
 * Stamp the built asset hashes into the pages that load them.
 *
 * The bundle filename is fixed so deployment stays a file copy, which means
 * the only thing telling a browser that the file changed is the query string.
 * It was hand-written and went stale: the bundle was rebuilt six times while
 * the pages still asked for `?v=0.12.0`, so a phone that had cached the old
 * one never saw a single fix. Hashing the content removes the chance of
 * forgetting.
 */

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = resolve(here, "../src/factorio_ai_lab/dashboard/static");

function shortHash(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex").slice(0, 12);
}

const assets = [
  { file: "map/factory-map.js", pattern: /map\/factory-map\.js\?v=[^"']*/g },
  { file: "map/style.css", pattern: /map\/style\.css\?v=[^"']*/g },
  // The legacy dashboard script and stylesheet carry hand-written versions
  // too, and went stale for exactly the same reason.
  { file: "app.js", pattern: /app\.js\?v=[^"']*/g },
  { file: "styles.css", pattern: /styles\.css\?v=[^"']*/g },
];

const pages = ["index.html", "map.html"];
const stamps = assets.map((asset) => ({
  ...asset,
  hash: shortHash(resolve(staticDir, asset.file)),
}));

let touched = 0;
for (const page of pages) {
  const path = resolve(staticDir, page);
  let html;
  try {
    html = readFileSync(path, "utf8");
  } catch {
    continue; // page is optional
  }
  const before = html;
  for (const stamp of stamps) {
    html = html.replace(stamp.pattern, `${stamp.file}?v=${stamp.hash}`);
  }
  if (html !== before) {
    writeFileSync(path, html);
    touched += 1;
  }
}

for (const stamp of stamps) {
  console.log(`${stamp.file} -> ${stamp.hash}`);
}
console.log(`paginas atualizadas: ${touched}`);
