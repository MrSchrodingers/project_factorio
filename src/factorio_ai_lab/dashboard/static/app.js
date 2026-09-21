const $ = (id) => document.getElementById(id);

const state = {
  status: {},
  world: {},
  learning: { history: [], summary: {} },
  history: [],
  run: {},
  datasets: {},
  research: {},
  progression: { achieved: [], frontier: [], next_goal: null },
  knowledge: { count: 0, lessons: [] },
  socket: null,
  frameTick: null,
  frameLoadedAt: null,
  frameLoading: false,
  frameRequestId: 0,
  worldZoom: 1,
  worldPanX: 0,
  worldPanY: 0,
  worldDragging: false,
  worldDragStart: null,
  worldViewMode: "game",
  production: { precision: "1m", series: {} },
  productionPrecision: "1m",
  productionLoading: false,
};

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function setClassText(id, value, className) {
  const el = $(id);
  if (!el) return;
  el.textContent = value;
  el.className = className;
}

function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function getPosition(entity) {
  const p = entity && entity.position;
  if (!p) return null;
  const x = Number(p.x);
  const y = Number(p.y);
  return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
}

function getName(entity) {
  return entity && typeof entity.name === "string" ? entity.name : "entity";
}

function categoryFor(name) {
  const n = String(name).toLowerCase();
  if (n.includes("belt") || n.includes("splitter") || n.includes("inserter") || n.includes("pipe")) return "logistics";
  if (n.includes("pole") || n.includes("solar") || n.includes("accumulator") || n.includes("boiler") || n.includes("steam")) return "power";
  if (n.includes("furnace") || n.includes("assembling") || n.includes("drill") || n.includes("lab") || n.includes("refinery") || n.includes("plant")) return "machine";
  return "other";
}

function categoryColor(category) {
  return {
    logistics: "#f0a33a",
    machine: "#6ab5f7",
    power: "#64d98b",
    other: "#b993f6",
  }[category] || "#b993f6";
}

function prepareCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  const width = Math.max(1, Math.floor(rect.width * dpr));
  const height = Math.max(1, Math.floor(rect.height * dpr));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, width: rect.width, height: rect.height };
}

function updateEntityMix() {
  const entities = Array.isArray(state.world && state.world.entities) ? state.world.entities : [];
  const counts = {};
  const positions = [];

  for (const entity of entities) {
    const name = getName(entity);
    counts[name] = (counts[name] || 0) + 1;
    const p = getPosition(entity);
    if (p) positions.push(p);
  }

  const types = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 10);
  const beltCount = Object.entries(counts)
    .filter(([name]) => name.includes("belt"))
    .reduce((sum, [, count]) => sum + count, 0);
  const pills = types
    .map(([name, count]) => "<span>" + escapeHtml(name) + " × " + count + "</span>");
  if (beltCount === 0) {
    pills.push('<span class="next-stage-pill">0 belts</span>');
  }
  $("entityTypes").innerHTML = pills.join("");

  if (!positions.length) {
    setText("boundsText", "bounds --");
    return;
  }
  const xs = positions.map((p) => p.x);
  const ys = positions.map((p) => p.y);
  setText(
    "boundsText",
    "x " + formatNumber(Math.min(...xs)) + "…" + formatNumber(Math.max(...xs))
      + " · y " + formatNumber(Math.min(...ys)) + "…" + formatNumber(Math.max(...ys))
  );
}

function renderWorldHotspots() {
  const overlay = $("worldAssetOverlay");
  if (!overlay) return;
  overlay.innerHTML = "";

  const entities = Array.isArray(state.world && state.world.entities)
    ? state.world.entities
    : [];
  const built = entities
    .filter((entity) => getName(entity) !== "character")
    .map((entity) => ({ entity, position: getPosition(entity) }))
    .filter((item) => item.position);

  if (!built.length) return;

  const center = {
    x: built.reduce((sum, item) => sum + item.position.x, 0) / built.length,
    y: built.reduce((sum, item) => sum + item.position.y, 0) / built.length,
  };
  const extent = Math.max(
    ...built.map((item) =>
      Math.max(
        Math.abs(item.position.x - center.x),
        Math.abs(item.position.y - center.y)
      )
    )
  );
  const radius = Math.min(34, Math.max(12, extent + 7.5));

  const stage = $("worldStage");
  const rect = stage.getBoundingClientRect();
  const square = Math.min(rect.width, rect.height);
  const offsetX = (rect.width - square) / 2;
  const offsetY = (rect.height - square) / 2;
  const span = radius * 2;

  for (const { entity, position } of built) {
    const dx = position.x - center.x;
    const dy = position.y - center.y;
    if (Math.abs(dx) > radius || Math.abs(dy) > radius) continue;

    const name = getName(entity);
    const hotspot = document.createElement("button");
    hotspot.type = "button";
    hotspot.className = "entity-hotspot " + categoryFor(name);
    hotspot.dataset.label = name;
    hotspot.setAttribute("aria-label", "Inspect " + name);
    hotspot.style.left = (
      offsetX + ((dx + radius) / span) * square
    ) + "px";
    hotspot.style.top = (
      offsetY + ((dy + radius) / span) * square
    ) + "px";

    hotspot.addEventListener("click", (event) => {
      event.stopPropagation();
      const inspector = $("worldInspector");
      $("worldInspectorIcon").src =
        "/api/assets/icon/" + encodeURIComponent(name) + ".png";
      setText("worldInspectorName", name);
      const direction = Number(entity.direction || 0);
      const type = entity.type || categoryFor(name);
      setText(
        "worldInspectorMeta",
        type + " · x " + formatNumber(position.x)
          + " · y " + formatNumber(position.y)
          + " · dir " + direction
      );
      inspector.hidden = false;
    });

    overlay.appendChild(hotspot);
  }
}



function drawStructuredFallback() {
  const canvas = $("worldFallback");
  const prepared = prepareCanvas(canvas);
  const ctx = prepared.ctx;
  const width = prepared.width;
  const height = prepared.height;
  ctx.clearRect(0, 0, width, height);

  const raw = (state.world && state.world.entities) || [];
  const entities = raw
    .map((entity) => ({ entity, p: getPosition(entity), name: getName(entity) }))
    .filter((item) => item.p);

  if (!entities.length) return;

  const xs = entities.map((e) => e.p.x);
  const ys = entities.map((e) => e.p.y);
  const minX = Math.min(...xs) - 5;
  const maxX = Math.max(...xs) + 5;
  const minY = Math.min(...ys) - 5;
  const maxY = Math.max(...ys) + 5;
  const spanX = Math.max(maxX - minX, 12);
  const spanY = Math.max(maxY - minY, 12);
  const pad = 34;
  const scale = Math.min((width - pad * 2) / spanX, (height - pad * 2) / spanY);
  const project = (p) => ({
    x: pad + (p.x - minX) * scale + (width - pad * 2 - spanX * scale) / 2,
    y: pad + (p.y - minY) * scale + (height - pad * 2 - spanY * scale) / 2,
  });

  ctx.strokeStyle = "rgba(255,255,255,.045)";
  ctx.lineWidth = 1;
  for (let x = Math.ceil(minX); x <= Math.floor(maxX); x += 1) {
    const a = project({ x, y: minY });
    const b = project({ x, y: maxY });
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  for (let y = Math.ceil(minY); y <= Math.floor(maxY); y += 1) {
    const a = project({ x: minX, y });
    const b = project({ x: maxX, y });
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }

  for (const item of entities) {
    const p = project(item.p);
    const category = categoryFor(item.name);
    const size = category === "machine" ? Math.max(8, Math.min(18, scale * 1.5)) : Math.max(5, Math.min(11, scale));
    ctx.fillStyle = categoryColor(category);
    ctx.globalAlpha = .9;
    ctx.fillRect(p.x - size / 2, p.y - size / 2, size, size);
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#d3d8dc";
    ctx.font = "9px ui-monospace, monospace";
    ctx.fillText(item.name, p.x + size / 2 + 4, p.y + 3);
  }
}

function refreshWorldFrame(force = false) {
  const render = (state.status && state.status.render) || {};
  const tick = state.world && state.world.tick;
  const key = String(tick ?? "none") + ":" + String((state.world && state.world.entity_count) || 0)
    + ":" + String(render.sprite_count || 0)
    + ":" + state.worldViewMode;
  if (!force && state.frameTick === key) return;
  if (state.frameLoading && !force) return;

  state.frameLoading = true;
  const requestId = ++state.frameRequestId;
  const requestedMode = state.worldViewMode;
  const probe = new Image();
  probe.onload = () => {
    if (requestId !== state.frameRequestId || requestedMode !== state.worldViewMode) {
      return;
    }
    const frame = $("worldFrame");
    frame.classList.add("frame-updating");
    frame.src = probe.src;
    frame.style.display = "block";
    requestAnimationFrame(() => {
      requestAnimationFrame(() => frame.classList.remove("frame-updating"));
    });
    $("worldFallback").style.display = "none";
    $("worldEmpty").style.display = "none";
    state.frameTick = key;
    state.frameLoadedAt = Date.now();
    state.frameLoading = false;
    setClassText("renderBadge", "live world", "badge live");
    const official = render.official_assets || {};
    setText(
      "frameSource",
      official.ready
        ? "Factorio 2.0.73 · live world sprites"
        : "structured map · local assets pending"
    );
    const rendererName = render.renderer || "";
    if ((rendererName.startsWith("official-asset-world-map") || rendererName.startsWith("full-factorio-world-map"))) {
      renderWorldHotspots();
    } else {
      renderWorldHotspots();
    }
  };
  probe.onerror = () => {
    if (requestId !== state.frameRequestId || requestedMode !== state.worldViewMode) {
      return;
    }
    $("worldFrame").style.display = "none";
    $("worldFallback").style.display = "block";
    drawStructuredFallback();
    renderWorldHotspots();
    $("worldEmpty").style.display = ((state.world && state.world.entity_count) || 0) ? "none" : "grid";
    state.frameTick = key;
    state.frameLoading = false;
    setClassText("renderBadge", render.ready ? "render degraded" : "sprites loading", "badge warn");
  };
  probe.src = "/api/world/frame.png?mode=" + encodeURIComponent(requestedMode) + "&t=" + encodeURIComponent(key) + "&ts=" + Date.now();
}

function applyWorldView() {
  const viewport = $("worldViewport");
  if (!viewport) return;
  viewport.style.transform =
    "translate(" + state.worldPanX + "px, " + state.worldPanY + "px) scale(" + state.worldZoom + ")";
  setText("zoomReset", state.worldZoom.toFixed(state.worldZoom < 2 ? 1 : 0) + "×");
}

function setWorldZoom(nextZoom, anchorX = null, anchorY = null) {
  const stage = $("worldStage");
  const rect = stage.getBoundingClientRect();
  const oldZoom = state.worldZoom;
  const next = Math.max(0.75, Math.min(5, nextZoom));
  if (anchorX !== null && anchorY !== null && oldZoom > 0) {
    const localX = anchorX - rect.left - rect.width / 2;
    const localY = anchorY - rect.top - rect.height / 2;
    const factor = next / oldZoom;
    state.worldPanX = localX - (localX - state.worldPanX) * factor;
    state.worldPanY = localY - (localY - state.worldPanY) * factor;
  }
  state.worldZoom = next;
  applyWorldView();
}

function resetWorldView() {
  state.worldZoom = 1;
  state.worldPanX = 0;
  state.worldPanY = 0;
  applyWorldView();
}

function installWorldModeControls() {
  for (const button of document.querySelectorAll("[data-world-mode]")) {
    button.addEventListener("click", () => {
      const mode = button.dataset.worldMode;
      if (!mode || mode === state.worldViewMode) return;
      state.worldViewMode = mode;
      for (const option of document.querySelectorAll("[data-world-mode]")) {
        option.classList.toggle("active", option.dataset.worldMode === mode);
      }
      $("worldStage").dataset.mode = mode;
      state.frameTick = null;
      refreshWorldFrame(true);
    });
  }
  $("worldStage").dataset.mode = state.worldViewMode;
}

function installWorldControls() {
  const stage = $("worldStage");
  const viewport = $("worldViewport");
  if (!stage || !viewport) return;

  $("zoomIn").addEventListener("click", () => setWorldZoom(state.worldZoom * 1.25));
  $("zoomOut").addEventListener("click", () => setWorldZoom(state.worldZoom / 1.25));
  $("zoomReset").addEventListener("click", resetWorldView);
  $("worldInspectorClose").addEventListener("click", () => {
    $("worldInspector").hidden = true;
  });
  stage.addEventListener("click", (event) => {
    if (!event.target.closest(".entity-hotspot")
        && !event.target.closest("#worldInspector")) {
      $("worldInspector").hidden = true;
    }
  });

  $("worldFullscreen").addEventListener("click", async () => {
    try {
      if (document.fullscreenElement === stage) {
        await document.exitFullscreen();
      } else {
        await stage.requestFullscreen();
      }
    } catch (error) {
      console.warn("fullscreen unavailable", error);
    }
  });

  stage.addEventListener("wheel", (event) => {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.14 : 1 / 1.14;
    setWorldZoom(state.worldZoom * factor, event.clientX, event.clientY);
  }, { passive: false });

  viewport.addEventListener("pointerdown", (event) => {
    state.worldDragging = true;
    state.worldDragStart = {
      x: event.clientX,
      y: event.clientY,
      panX: state.worldPanX,
      panY: state.worldPanY,
    };
    viewport.classList.add("dragging");
    viewport.setPointerCapture(event.pointerId);
  });

  viewport.addEventListener("pointermove", (event) => {
    if (!state.worldDragging || !state.worldDragStart) return;
    state.worldPanX = state.worldDragStart.panX + event.clientX - state.worldDragStart.x;
    state.worldPanY = state.worldDragStart.panY + event.clientY - state.worldDragStart.y;
    applyWorldView();
  });

  const stopDrag = () => {
    state.worldDragging = false;
    state.worldDragStart = null;
    viewport.classList.remove("dragging");
  };
  viewport.addEventListener("pointerup", stopDrag);
  viewport.addEventListener("pointercancel", stopDrag);
  viewport.addEventListener("dblclick", resetWorldView);
  applyWorldView();
}

function updateFrameAge() {
  if (!state.frameLoadedAt) {
    setText("frameAge", "frame --");
    return;
  }
  const age = Math.max(0, (Date.now() - state.frameLoadedAt) / 1000);
  setText("frameAge", "frame " + age.toFixed(age < 10 ? 1 : 0) + "s ago");
}

const oreSeriesColors = {
  "iron-ore": "#6ab5f7",
  "copper-ore": "#d67a4e",
  coal: "#8c949b",
  stone: "#c6b28a",
  "uranium-ore": "#72c957",
};

function humanizePrototype(value) {
  return String(value || "")
    .split("-")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function compactFactorioNumber(value) {
  const n = Number(value || 0);
  const abs = Math.abs(n);
  if (abs >= 1000000) return (n / 1000000).toFixed(abs >= 10000000 ? 0 : 1) + "M";
  if (abs >= 1000) return (n / 1000).toFixed(abs >= 10000 ? 0 : 1) + "k";
  if (abs >= 100) return n.toFixed(0);
  if (abs >= 10) return n.toFixed(1).replace(/\.0$/, "");
  return n.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

function productionDurationLabel(seconds) {
  const s = Number(seconds || 0);
  if (s < 60) return s + " seconds";
  if (s < 3600) return (s / 60) + " minute" + (s === 60 ? "" : "s");
  return (s / 3600) + " hour" + (s === 3600 ? "" : "s");
}

function smoothSeries(samples, alpha = 0.24) {
  if (!samples.length) return [];
  const output = [samples[0]];
  for (let i = 1; i < samples.length; i += 1) {
    output.push(alpha * samples[i] + (1 - alpha) * output[i - 1]);
  }
  return output;
}

function drawNativeProductionChart(canvasId, mode) {
  const canvas = $(canvasId);
  const prepared = prepareCanvas(canvas);
  const ctx = prepared.ctx;
  const width = prepared.width;
  const height = prepared.height;
  ctx.clearRect(0, 0, width, height);

  const series = (state.production && state.production.series) || {};
  const field = mode === "produced" ? "produced" : "consumed";
  const entries = Object.entries(series)
    .map(([name, value]) => [name, Array.isArray(value[field]) ? value[field].map(Number) : []])
    .filter(([, samples]) => samples.length);

  const padLeft = 44;
  const padRight = 12;
  const padTop = 12;
  const padBottom = 25;
  const graphWidth = Math.max(1, width - padLeft - padRight);
  const graphHeight = Math.max(1, height - padTop - padBottom);
  const preparedEntries = entries.map(([name, samples]) => [
    name,
    samples,
    smoothSeries(samples),
  ]);
  const maxY = Math.max(
    1,
    ...preparedEntries.flatMap(([, , smoothed]) => smoothed)
  ) * 1.08;

  ctx.strokeStyle = "rgba(255,255,255,.07)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = padTop + (graphHeight * i) / 4;
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(width - padRight, y);
    ctx.stroke();
  }
  for (let i = 0; i <= 6; i += 1) {
    const x = padLeft + (graphWidth * i) / 6;
    ctx.beginPath();
    ctx.moveTo(x, padTop);
    ctx.lineTo(x, height - padBottom);
    ctx.stroke();
  }

  ctx.fillStyle = "#707a82";
  ctx.font = "9px ui-monospace, monospace";
  ctx.textAlign = "right";
  ctx.fillText(compactFactorioNumber(maxY), padLeft - 5, padTop + 4);
  ctx.fillText(compactFactorioNumber(maxY / 2), padLeft - 5, padTop + graphHeight / 2 + 3);
  ctx.fillText("0", padLeft - 5, height - padBottom + 3);

  for (const [name, samples, smoothed] of preparedEntries) {
    if (!samples.some((value) => value > 0)) continue;
    const color = oreSeriesColors[name] || "#b993f6";

    ctx.save();
    ctx.globalAlpha = 0.20;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i < samples.length; i += 1) {
      const x = padLeft + (i / Math.max(samples.length - 1, 1)) * graphWidth;
      const raw = Math.min(maxY, Math.max(0, samples[i]));
      const y = padTop + graphHeight - (raw / maxY) * graphHeight;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.restore();

    ctx.strokeStyle = color;
    ctx.lineWidth = 2.25;
    ctx.beginPath();
    for (let i = 0; i < smoothed.length; i += 1) {
      const x = padLeft + (i / Math.max(smoothed.length - 1, 1)) * graphWidth;
      const y = padTop + graphHeight - (Math.max(0, smoothed[i]) / maxY) * graphHeight;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  const duration = Number((state.production && state.production.duration_seconds) || 0);
  ctx.fillStyle = "#707a82";
  ctx.font = "9px ui-monospace, monospace";
  ctx.textAlign = "left";
  ctx.fillText("-" + productionDurationLabel(duration), padLeft, height - 8);
  ctx.textAlign = "right";
  ctx.fillText("now", width - padRight, height - 8);
}

function renderNativeProductionBars(containerId, mode) {
  const container = $(containerId);
  const series = (state.production && state.production.series) || {};
  const rateField = mode === "produced" ? "produced_rate" : "consumed_rate";
  const countField = mode === "produced" ? "produced_count" : "consumed_count";
  const entries = Object.entries(series)
    .map(([name, value]) => ({
      name,
      rate: Math.max(0, Number(value[rateField] || 0)),
      count: Math.max(0, Number(value[countField] || 0)),
    }))
    .sort((a, b) => b.rate - a.rate);
  const maxRate = Math.max(0, ...entries.map((item) => item.rate));
  setText(
    mode === "produced" ? "producedPeak" : "consumedPeak",
    "avg " + compactFactorioNumber(maxRate) + "/min"
  );

  container.innerHTML = entries.map((item) => {
    const color = oreSeriesColors[item.name] || "#b993f6";
    const width = maxRate > 0 ? Math.max(0, Math.min(100, (item.rate / maxRate) * 100)) : 0;
    return '<div class="factorio-stat-row" title="' + escapeHtml(item.name) + '">'
      + '<div class="factorio-item">'
      + '<img src="/api/assets/icon/' + encodeURIComponent(item.name) + '.png" alt="">'
      + '<div class="factorio-item-meta"><strong>' + escapeHtml(humanizePrototype(item.name))
      + '</strong><small>' + compactFactorioNumber(item.count) + ' in window</small></div>'
      + '</div>'
      + '<div class="factorio-bar-track"><div class="factorio-bar-fill" style="width:'
      + width.toFixed(2) + '%;background:' + color + ';color:' + color + '"></div></div>'
      + '<div class="factorio-rate">' + compactFactorioNumber(item.rate) + '/min</div>'
      + '</div>';
  }).join("");
}

function renderProductionStatistics() {
  drawNativeProductionChart("producedCanvas", "produced");
  drawNativeProductionChart("consumedCanvas", "consumed");
  renderNativeProductionBars("producedBars", "produced");
  renderNativeProductionBars("consumedBars", "consumed");

  const duration = Number((state.production && state.production.duration_seconds) || 0);
  const samples = Number((state.production && state.production.sample_count) || 0);
  const period = Number((state.production && state.production.sample_period_seconds) || 0);
  setText(
    "productionWindow",
    productionDurationLabel(duration) + " · " + samples + " native samples · "
      + compactFactorioNumber(period) + " s/sample"
  );

  for (const button of document.querySelectorAll("[data-production-precision]")) {
    button.classList.toggle(
      "active",
      button.dataset.productionPrecision === state.productionPrecision
    );
  }
}

async function loadProduction(precision = state.productionPrecision) {
  if (state.productionLoading) return;
  state.productionLoading = true;
  try {
    const response = await fetch(
      "/api/production?precision=" + encodeURIComponent(precision),
      { cache: "no-store" }
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || ("HTTP " + response.status));
    state.production = payload;
    state.productionPrecision = precision;
    renderProductionStatistics();
    setText(
      "productionSource",
      payload.connected
        ? "native LuaFlowStatistics · items/min · live Factorio data"
        : "production statistics unavailable"
    );
  } catch (error) {
    console.error("production statistics failed", error);
    setText("productionSource", "production statistics unavailable");
  } finally {
    state.productionLoading = false;
  }
}

function installProductionControls() {
  for (const button of document.querySelectorAll("[data-production-precision]")) {
    button.addEventListener("click", () => {
      const precision = button.dataset.productionPrecision;
      if (!precision || precision === state.productionPrecision) return;
      state.productionPrecision = precision;
      loadProduction(precision);
    });
  }
}

function drawAxes(ctx, width, height, pad) {
  ctx.strokeStyle = "rgba(255,255,255,.09)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad, 14);
  ctx.lineTo(pad, height - pad);
  ctx.lineTo(width - 12, height - pad);
  ctx.stroke();
}

function drawLineChart(canvasId, rows, xAccessor, series, emptyMessage) {
  const canvas = $(canvasId);
  const prepared = prepareCanvas(canvas);
  const ctx = prepared.ctx;
  const width = prepared.width;
  const height = prepared.height;
  ctx.clearRect(0, 0, width, height);
  const pad = 34;
  drawAxes(ctx, width, height, pad);

  if (!Array.isArray(rows) || !rows.length) {
    ctx.fillStyle = "#737d85";
    ctx.font = "10px ui-monospace, monospace";
    ctx.fillText(emptyMessage || "waiting for samples", pad + 10, height / 2);
    return;
  }

  const xs = rows.map(xAccessor).map(Number).filter(Number.isFinite);
  if (!xs.length) return;
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const spanX = Math.max(maxX - minX, 1);

  series.forEach((item) => {
    const pairs = rows
      .map((row) => [Number(xAccessor(row)), Number(item.value(row))])
      .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
    if (!pairs.length) return;
    const ys = pairs.map((pair) => pair[1]);
    const minY = item.zero ? 0 : Math.min(...ys);
    const maxY = Math.max(...ys);
    const spanY = Math.max(maxY - minY, 1e-9);

    ctx.strokeStyle = item.color;
    ctx.lineWidth = item.width || 1.6;
    ctx.beginPath();
    let started = false;
    for (const pair of pairs) {
      const xv = pair[0];
      const yv = pair[1];
      const x = pad + ((xv - minX) / spanX) * (width - pad - 16);
      const y = height - pad - ((yv - minY) / spanY) * (height - pad - 24);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
  });

  ctx.fillStyle = "#737d85";
  ctx.font = "9px ui-monospace, monospace";
  ctx.fillText(formatNumber(minX, 0), pad, height - 10);
  ctx.fillText(formatNumber(maxX, 0), width - 48, height - 10);
}

function drawOfflineLearning() {
  const rows = (state.learning && state.learning.history) || [];
  const enriched = rows.map((row, i) => {
    const start = Math.max(0, i - 19);
    const windowRows = rows.slice(start, i + 1);
    const rolling = windowRows.reduce((sum, r) => sum + Number(r.reward || 0), 0) / windowRows.length;
    return Object.assign({}, row, { rolling });
  });
  drawLineChart("learningCanvas", enriched, (r) => r.episode, [
    { value: (r) => r.reward, color: "rgba(106,181,247,.34)", width: 1 },
    { value: (r) => r.rolling, color: "#f0a33a", width: 2.1 },
  ], "offline UCB1 artifact not found");
}

function drawOnlineLearning() {
  const online = (state.research && state.research.online_learning) || {};
  const rows = online.history || [];
  drawLineChart("onlineLearningCanvas", rows, (r) => r.episode ?? r.trial ?? r.index, [
    { value: (r) => r.reward, color: "#64d98b", width: 2.1 },
    { value: (r) => r.output ?? r.iron_output, color: "rgba(106,181,247,.6)", width: 1.35 },
  ], "waiting for real Factorio trials");
  setText(
    "onlineLearningSource",
    online.algorithm ? online.algorithm + " · " + rows.length + " trials" : "no online learner"
  );
}

function drawHistory() {
  drawLineChart("historyCanvas", state.history || [], (r) => r.timestamp, [
    { value: (r) => r.entity_count, color: "#64d98b", width: 2, zero: true },
    { value: (r) => r.probe_latency_ms, color: "rgba(240,163,58,.72)", width: 1.25, zero: true },
  ], "waiting for live telemetry");
}

function renderCurriculum() {
  const list = $("curriculumList");
  const curriculum = Array.isArray(state.research && state.research.curriculum)
    ? state.research.curriculum
    : [];
  if (!curriculum.length) {
    list.innerHTML = '<div class="placeholder-row">No curriculum state yet.</div>';
    return;
  }

  list.innerHTML = curriculum.map((stage, index) => {
    const status = String(stage.status || "pending");
    const cls = status === "completed" || status === "success"
      ? "done"
      : status === "running" || status === "learning" || status === "validating"
        ? "active"
        : "";
    const detail = stage.detail || stage.objective || "";
    return '<div class="stage-row ' + cls + '">'
      + '<span class="stage-index">' + (index + 1) + '</span>'
      + '<div class="stage-copy"><strong>' + escapeHtml(stage.name || ("stage " + (index + 1)))
      + '</strong><small>' + escapeHtml(detail) + '</small></div>'
      + '<span class="stage-status">' + escapeHtml(status) + '</span>'
      + '</div>';
  }).join("");
}

function eventTime(event) {
  return event.at || event.timestamp || event.time || "";
}

function renderTimeline() {
  const events = [];
  for (const event of ((state.research && state.research.events) || [])) {
    events.push(Object.assign({}, event, { source: "research" }));
  }
  for (const event of ((state.run && state.run.events) || [])) {
    events.push(Object.assign({}, event, { source: "run" }));
  }
  events.sort((a, b) => String(eventTime(a)).localeCompare(String(eventTime(b))));
  const recent = events.slice(-40).reverse();
  const timeline = $("timeline");

  if (!recent.length) {
    timeline.innerHTML = '<div class="placeholder-row">No execution events yet.</div>';
    return;
  }

  timeline.innerHTML = recent.map((event) => {
    const type = String(event.type || "event").toLowerCase();
    const message = event.message || event.observation || event.lesson || type;
    const when = eventTime(event);
    return '<div class="timeline-row ' + escapeHtml(type) + '">'
      + '<strong>' + escapeHtml(message) + '</strong>'
      + '<small>' + escapeHtml(type) + (when ? " · " + escapeHtml(when) : "") + '</small>'
      + '</div>';
  }).join("");
}

function renderKnowledge() {
  const lessons = Array.isArray(state.knowledge && state.knowledge.lessons)
    ? state.knowledge.lessons
    : [];
  const count = Number((state.knowledge && state.knowledge.count) || lessons.length || 0);
  setText("knowledgeCount", count + " lesson" + (count === 1 ? "" : "s"));

  if (!lessons.length) {
    setText("knowledgeLatest", "no learned lesson yet");
    $("knowledgeList").innerHTML = '<div class="placeholder-row">No knowledge artifacts yet.</div>';
    return;
  }

  const latest = lessons[lessons.length - 1];
  setText("knowledgeLatest", latest.lesson || latest.observation || latest.stage || "latest lesson available");

  $("knowledgeList").innerHTML = lessons.slice().reverse().map((lesson) => {
    return '<article class="knowledge-item">'
      + '<header><span>' + escapeHtml(lesson.stage || lesson.type || "lesson") + '</span>'
      + '<span>' + escapeHtml(lesson.at || lesson.timestamp || "") + '</span></header>'
      + '<strong>' + escapeHtml(lesson.lesson || lesson.observation || "Structured experiment result") + '</strong>'
      + (lesson.next_hypothesis
        ? '<p>Next hypothesis: ' + escapeHtml(lesson.next_hypothesis) + '</p>'
        : "")
      + '</article>';
  }).join("");
}

function renderTruthTable() {
  const offlineEpisodes = Number((state.learning && state.learning.summary && state.learning.summary.episodes) || 0);
  const onlineHistory = (state.research && state.research.online_learning && state.research.online_learning.history) || [];
  const knowledgeCount = Number((state.knowledge && state.knowledge.count) || 0);
  const capabilities = (state.research && state.research.capabilities) || {};
  const datasets = state.datasets || {};
  const spatialDemos = Number(datasets.spatial_demonstrations || 0);

  setClassText(
    "truthOffline",
    offlineEpisodes > 0 ? offlineEpisodes + " episodes" : "not trained",
    offlineEpisodes > 0 ? "good" : "muted"
  );
  setClassText(
    "truthOnline",
    onlineHistory.length ? onlineHistory.length + " trials" : "not started",
    onlineHistory.length ? "good" : "warn"
  );
  setClassText(
    "truthKnowledge",
    knowledgeCount ? knowledgeCount + " lessons" : "not started",
    knowledgeCount ? "good" : "warn"
  );

  setClassText(
    "truthDataset",
    spatialDemos + " demo" + (spatialDemos === 1 ? "" : "s")
      + (datasets.training_ready ? " · ready" : " · collecting"),
    datasets.training_ready ? "good" : spatialDemos ? "warn" : "muted"
  );

  const neuralStatus = capabilities.neural_policy && capabilities.neural_policy.status;
  const worldModelStatus = capabilities.world_model && capabilities.world_model.status;
  setClassText(
    "truthNeural",
    neuralStatus || "not trained",
    neuralStatus === "trained" ? "good" : "muted"
  );
  setClassText(
    "truthWorldModel",
    worldModelStatus || "explicit only",
    worldModelStatus === "trained" ? "good" : "muted"
  );
}

function updateMission() {
  const research = state.research || {};
  const curriculum = Array.isArray(research.curriculum) ? research.curriculum : [];
  const current = research.current_stage
    || curriculum.find((stage) => ["running", "learning", "validating"].includes(stage.status))
    || null;

  setText("missionTitle", research.objective || (state.run && state.run.objective) || "Waiting for research loop…");
  setText(
    "missionDetail",
    research.detail
      || ((state.run && state.run.status) ? "Last validated run: " + state.run.status : "No active curriculum stage.")
  );
  setText("stageName", (current && current.name) || research.stage || "stage --");
  setText("nextAction", research.next_action || "await curriculum runner");

  let progress = Number(research.progress ?? 0);
  if (!Number.isFinite(progress)) progress = 0;
  if (progress <= 1) progress *= 100;
  progress = Math.max(0, Math.min(100, progress));
  $("stageProgressBar").style.width = progress + "%";
  setText("stageProgressText", formatNumber(progress, 0) + "%");

  const status = String(research.status || "idle");
  const badgeClass = ["running", "learning", "validating"].includes(status)
    ? "badge live"
    : status === "completed"
      ? "badge good"
      : status === "error" || status === "failed"
        ? "badge dead"
        : "badge neutral";
  setClassText("researchBadge", "research " + status, badgeClass);
}

function updateKpis() {
  const world = state.world || {};
  const status = state.status || {};
  const factorio = status.factorio || {};
  const llm = status.llm || {};
  const render = status.render || {};
  const production = world.production || {};
  const outputs = production.output || {};
  const inputs = production.input || {};
  const online = (state.research && state.research.online_learning) || {};
  const onlineRows = online.history || [];

  setText("entityCount", formatNumber(world.entity_count || 0, 0));
  const tickText = "game " + formatNumber(world.tick, 0)
    + (world.experiment_tick !== undefined
      ? " · FLE " + formatNumber(world.experiment_tick, 0)
      : "");
  setText("worldTick", tickText);
  setText("probeLatency", formatNumber(world.latency_ms, 1) + " ms");

  const primaryOutput = Object.entries(outputs).sort((a, b) => Number(b[1]) - Number(a[1]))[0];
  if (primaryOutput) {
    setText("productionPrimary", formatNumber(primaryOutput[1], 0) + " " + primaryOutput[0]);
    const primaryInput = Object.entries(inputs).sort((a, b) => Number(b[1]) - Number(a[1]))[0];
    setText(
      "productionSecondary",
      primaryInput
        ? formatNumber(primaryInput[1], 0) + " " + primaryInput[0] + " consumed"
        : "measured game production"
    );
  } else {
    setText("productionPrimary", "--");
    setText("productionSecondary", "no measured flow");
  }

  const runner = status.research_runner || {};
  const research = state.research || {};
  const updatedAt = Date.parse(research.updated_at || "");
  const researchAgeS = Number.isFinite(updatedAt)
    ? Math.max(0, (Date.now() - updatedAt) / 1000)
    : null;
  const researchStatus = String(research.status || "idle");
  const runnerStalled = !!runner.active
    && ["running", "learning", "validating"].includes(researchStatus)
    && researchAgeS !== null
    && researchAgeS > 45;
  const loopLabel = runnerStalled
    ? "stalled"
    : runner.active
      ? "active"
      : researchStatus === "completed"
        ? "completed · idle"
        : "idle";
  setClassText(
    "researchLoop",
    loopLabel,
    runnerStalled ? "bad" : runner.active ? "good" : "muted"
  );
  setText(
    "researchLoopDetail",
    runnerStalled
      ? "process active · no state update for " + formatNumber(researchAgeS, 0) + " s"
      : runner.active
        ? (research.stage || "working") + " · updated "
          + (researchAgeS === null ? "--" : formatNumber(researchAgeS, 0) + " s") + " ago"
        : researchStatus === "completed"
          ? "last run completed · next: " + String(research.next_action || "--")
          : "no active agent process"
  );

  const progression = state.progression || {};
  const nextGoal = progression.next_goal || null;
  const frontier = Array.isArray(progression.frontier) ? progression.frontier : [];
  const achievedGoals = Array.isArray(progression.achieved) ? progression.achieved : [];
  if (nextGoal) {
    setText("engineeringGoal", nextGoal.label || nextGoal.goal_id || "next capability");
    const alternatives = frontier
      .slice(1, 3)
      .map((candidate) => candidate.label || candidate.goal_id)
      .filter(Boolean);
    setText(
      "engineeringGoalDetail",
      String(nextGoal.kind || "engineering")
        + " · " + achievedGoals.length + " capabilities achieved"
        + (alternatives.length ? " · alternatives: " + alternatives.join(" / ") : "")
    );
  } else {
    setText("engineeringGoal", "frontier complete");
    setText(
      "engineeringGoalDetail",
      achievedGoals.length + " capabilities achieved · expand goal catalog"
    );
  }

  const onlineStatus = online.status || (onlineRows.length ? "learning" : "idle");
  setText("onlineLearner", online.algorithm ? online.algorithm + " · " + onlineStatus : onlineStatus);
  setText(
    "onlineLearnerDetail",
    onlineRows.length
      ? onlineRows.length + " real-world trials · best " + String(online.best_arm ?? "--")
      : "no real-world trials yet"
  );

  const recentTicks = (state.history || [])
    .slice(-4)
    .map((point) => Number(point.tick))
    .filter(Number.isFinite);
  const simulating = recentTicks.length >= 2
    && new Set(recentTicks).size > 1;
  const factorioLabel = !factorio.connected
    ? "Factorio offline"
    : simulating
      ? "Factorio · simulating"
      : "Factorio · paused between actions";
  setClassText(
    "factorioStatus",
    factorioLabel,
    factorio.connected ? "hud-chip good" : "hud-chip bad"
  );
  setClassText("llmStatus", llm.connected ? "Qwen inference" : "offline", llm.connected ? "good" : "bad");
  setText(
    "llmDetail",
    llm.connected
      ? ((llm.models && llm.models.length ? llm.models.join(", ") : "qwen") + " · weights static · knowledge memory learns")
      : "llama.cpp :18081"
  );
  setClassText("routeLocal", llm.connected ? "ready" : "offline", llm.connected ? "good" : "bad");
  setText("commitBadge", (status.branch || "--") + " · " + (status.git_sha || "--"));

  if (render.ready) {
    setClassText("renderBadge", formatNumber(render.sprite_count, 0) + " sprites", "badge live");
  } else {
    setClassText("renderBadge", "sprites loading", "badge warn");
  }

  const runStatus = String((state.run && state.run.status) || "--");
  setClassText(
    "runStatus",
    runStatus,
    ["success", "completed"].includes(runStatus) ? "badge good"
      : ["running", "starting", "learning", "validating"].includes(runStatus) ? "badge live"
      : runStatus === "--" ? "badge neutral" : "badge warn"
  );
  setText("runId", (state.run && state.run.run_id) || "no run");
  setText(
    "runSummary",
    state.run && Object.keys(state.run).length ? JSON.stringify(state.run, null, 2) : "No active run yet."
  );
}

function applyPayload(payload) {
  if (payload.status) state.status = payload.status;
  if (payload.world) state.world = payload.world;
  if (payload.learning) state.learning = payload.learning;
  if (payload.history) state.history = payload.history;
  if (payload.run) state.run = payload.run;
  if (payload.research) state.research = payload.research;
  if (payload.progression) state.progression = payload.progression;
  if (payload.knowledge) state.knowledge = payload.knowledge;
  if (payload.datasets) state.datasets = payload.datasets;

  updateKpis();
  updateMission();
  updateEntityMix();
  renderWorldHotspots();
  renderCurriculum();
  renderTimeline();
  renderKnowledge();
  renderTruthTable();
  drawOfflineLearning();
  drawOnlineLearning();
  drawHistory();
  refreshWorldFrame();
}

async function loadConfig() {
  const response = await fetch("/api/config");
  const config = await response.json();
  const form = $("configForm");
  for (const [key, value] of Object.entries(config)) {
    if (form.elements[key]) form.elements[key].value = value;
  }
}

async function loadInitialState() {
  const paths = [
    "/api/status",
    "/api/world",
    "/api/history",
    "/api/learning",
    "/api/run",
    "/api/research",
    "/api/progression",
    "/api/knowledge",
    "/api/datasets",
  ];
  const responses = await Promise.all(paths.map((path) => fetch(path)));
  const payloads = await Promise.all(responses.map((response) => response.json()));
  applyPayload({
    status: payloads[0],
    world: payloads[1],
    history: payloads[2],
    learning: payloads[3],
    run: payloads[4],
    research: payloads[5],
    progression: payloads[6],
    knowledge: payloads[7],
    datasets: payloads[8],
  });
}

$("configForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const numeric = new Set([
    "poll_interval_s",
    "astar_turn_penalty",
    "ucb_exploration",
    "llm_temperature",
    "llm_max_tokens",
  ]);
  const patch = {};
  for (const element of form.elements) {
    if (!element.name) continue;
    patch[element.name] = numeric.has(element.name) ? Number(element.value) : element.value;
  }

  const response = await fetch("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  setText("configSaved", response.ok ? "saved" : "error " + response.status);
  if (response.ok) setTimeout(() => setText("configSaved", ""), 1600);
});

function connectSocket() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(protocol + "//" + location.host + "/ws/live");
  state.socket = socket;

  socket.addEventListener("open", () => {
    setClassText("socketBadge", "live", "badge live");
  });

  socket.addEventListener("message", (event) => {
    try {
      applyPayload(JSON.parse(event.data));
    } catch (error) {
      console.error("live payload error", error);
    }
  });

  socket.addEventListener("close", () => {
    setClassText("socketBadge", "reconnecting", "badge dead");
    setTimeout(connectSocket, 1500);
  });
  socket.addEventListener("error", () => socket.close());
}

window.addEventListener("resize", () => {
  drawStructuredFallback();
  renderWorldHotspots();
  drawOfflineLearning();
  drawOnlineLearning();
  drawHistory();
  renderProductionStatistics();
});

installWorldControls();
installWorldModeControls();
installProductionControls();

Promise.all([loadConfig(), loadInitialState(), loadProduction()])
  .catch((error) => {
    console.error("dashboard bootstrap failed", error);
    setClassText("socketBadge", "degraded", "badge dead");
  })
  .finally(connectSocket);

setInterval(updateFrameAge, 1000);
setInterval(() => refreshWorldFrame(true), 2000);
setInterval(() => loadProduction(state.productionPrecision), 4000);
