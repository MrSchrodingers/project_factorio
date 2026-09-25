const $ = (id) => document.getElementById(id);

const state = {
  status: {},
  experimentContext: {},
  world: {},
  learning: { history: [], summary: {} },
  history: [],
  run: {},
  datasets: {},
  research: {},
  progression: { achieved: [], frontier: [], next_goal: null },
  productionPlan: {},
  machineDiagnostics: {},
  autonomy: {},
  resourceOverview: { cells: [], points: [], totals: {}, nearest: {} },
  factoryGraph: { nodes: [], edges: [], metrics: {} },
  gameGraphSummary: {},
  productionChartScale: {},
  evolution: {},
  knowledge: { count: 0, lessons: [] },
  socket: null,
  frameTick: null,
  frameLoadedAt: null,
  frameLoading: false,
  frameRequestId: 0,
  frameLastRequestedAt: 0,
  worldZoom: 1,
  worldPanX: 0,
  worldPanY: 0,
  worldCenterX: null,
  worldCenterY: null,
  worldRadius: null,
  worldBaseRadius: null,
  worldViewManual: false,
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

const stageLabelsPt = {
  "Raw bootstrap": "Bootstrap de recursos",
  "Technology triggers": "Gatilhos tecnológicos",
  "Coal commissioning": "Comissionamento de carvão",
  "Steam commissioning": "Comissionamento a vapor",
  "Lab bootstrap": "Bootstrap do laboratório",
  "Powered red science": "Ciência vermelha energizada",
  "Electric mining transition": "Transição para mineração elétrica",
  "Logistic-science unlock": "Desbloqueio da ciência logística",
  "Green-science industry": "Indústria de ciência verde",
  "Logistics research": "Pesquisa de Logística",
  "Autonomy soak": "Teste prolongado de autonomia",
  "Baseline iron mining": "Mineração de ferro baseline",
  "Online placement learning": "Aprendizado online de posicionamento",
  "Scale mining": "Escala de mineração",
  "Smelting probe": "Teste de fundição",
  "A* belt logistics": "Logística de esteiras A*",
};

const actionLabelsPt = {
  build: "construção",
  craft: "fabricação",
  logistics: "logística",
  move: "movimento",
  research: "pesquisa",
  wait: "espera",
  other: "ação",
  idle: "ocioso",
};

const statusLabelsPt = {
  starting: "iniciando",
  running: "executando",
  learning: "aprendendo",
  validating: "validando",
  completed: "concluído",
  generation_complete: "geração concluída",
  generation_rejected: "geração rejeitada",
  partial_success: "sucesso parcial",
  validation_pass: "validação aprovada",
  error: "erro",
  failed: "falhou",
  idle: "ocioso",
};

function stageLabel(value) {
  const raw = String(value || "");
  return stageLabelsPt[raw] || raw || "--";
}

function actionLabel(value) {
  const raw = String(value || "idle");
  return actionLabelsPt[raw] || raw;
}

function statusLabel(value) {
  const raw = String(value || "idle");
  return statusLabelsPt[raw] || raw.replaceAll("_", " ");
}

function cortexOperationalView() {
  const context = state.experimentContext || {};
  const cortexPhase = context.cortex_phase || {};
  const runner = ((state.status || {}).research_runner) || {};
  const phase = String(cortexPhase.phase || "");
  const phase4Checkpoint = String(cortexPhase.phase4_checkpoint || "");
  const globalCortex = context.kind === "global" && ["F3", "F4"].includes(phase);
  const liveAgent = !!runner.active;
  return {
    context,
    cortexPhase,
    phase,
    phase4Checkpoint,
    liveAgent,
    historicalEvidenceMode: globalCortex && !liveAgent,
  };
}

function repairReasonLabel(value) {
  return {
    route_capex_counterexample: "redução de CAPEX/rota antes de aumentar material",
    electric_transition_material_budget: "ajuste de orçamento material medido",
    electric_research_power_budget: "reforço do orçamento energético",
    structural_autonomy_counterexample: "mutação estrutural por falha de autonomia",
    electric_backbone_layout_counterexample: "variação de layout do backbone",
    power_group_connection_counterexample: "reuso do grupo elétrico existente",
    bootstrap_collection_retry: "retry de coleta sem inflar alvo",
  }[String(value || "")] || String(value || "reparo");
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
    const world = state.world || {};
    const operational = cortexOperationalView();
    setText(
      "boundsText",
      world.connected
        ? (operational.historicalEvidenceMode
          ? "WORLD LIVE · 0 entidades · nenhum executor Cortex ativo"
          : "WORLD LIVE · 0 entidades")
        : "bounds --"
    );
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

function automaticWorldGeometry() {
  const entities = Array.isArray(state.world && state.world.entities)
    ? state.world.entities
    : [];
  const built = entities
    .filter((entity) => getName(entity) !== "character")
    .map((entity) => getPosition(entity))
    .filter(Boolean);
  if (built.length) {
    const center = {
      x: built.reduce((sum, pos) => sum + pos.x, 0) / built.length,
      y: built.reduce((sum, pos) => sum + pos.y, 0) / built.length,
    };
    const extent = Math.max(
      ...built.map((pos) =>
        Math.max(Math.abs(pos.x - center.x), Math.abs(pos.y - center.y))
      )
    );
    return {
      center,
      radius: Math.min(34, Math.max(12, extent + 7.5)),
    };
  }
  const character = entities
    .map((entity) => ({ name: getName(entity), pos: getPosition(entity) }))
    .find((row) => row.name === "character" && row.pos);
  return {
    center: character ? character.pos : { x: 0, y: 0 },
    radius: 18,
  };
}

function currentWorldGeometry() {
  if (
    state.worldViewManual
    && Number.isFinite(Number(state.worldCenterX))
    && Number.isFinite(Number(state.worldCenterY))
    && Number.isFinite(Number(state.worldRadius))
  ) {
    return {
      center: {
        x: Number(state.worldCenterX),
        y: Number(state.worldCenterY),
      },
      radius: Number(state.worldRadius),
    };
  }
  return automaticWorldGeometry();
}

function ensureManualWorldGeometry() {
  const geometry = currentWorldGeometry();
  if (!state.worldViewManual) {
    state.worldCenterX = geometry.center.x;
    state.worldCenterY = geometry.center.y;
    state.worldRadius = geometry.radius;
    state.worldBaseRadius = geometry.radius;
    state.worldViewManual = true;
    state.worldZoom = 1;
  }
  return {
    center: {
      x: Number(state.worldCenterX),
      y: Number(state.worldCenterY),
    },
    radius: Number(state.worldRadius),
  };
}

// The canvas map (static/map/factory-map.js) owns the world stage when it is
// present. These legacy helpers drove the server-rendered PNG and its CSS
// transform overlay; leaving them running would fight the new renderer for
// the same DOM and throw on elements that no longer exist.
const LEGACY_MAP_DISABLED = !!document.querySelector("[data-factory-map]");

function renderWorldHotspots() {
  if (LEGACY_MAP_DISABLED) return;

  const overlay = $("worldAssetOverlay");
  if (!overlay) return;
  overlay.innerHTML = "";
  if (state.worldViewMode === "overview") return;

  const entities = Array.isArray(state.world && state.world.entities)
    ? state.world.entities
    : [];
  const built = entities
    .filter((entity) => getName(entity) !== "character")
    .map((entity) => ({ entity, position: getPosition(entity) }))
    .filter((item) => item.position);

  if (!built.length) return;

  const geometry = currentWorldGeometry();
  const center = geometry.center;
  const radius = geometry.radius;

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
      const status = String(entity.status || "unknown").replaceAll("_", " ");
      const coalFuel = Number(entity.coal_fuel);
      const fuelLabel = Number.isFinite(coalFuel)
        ? " · coal " + formatNumber(coalFuel, 0)
        : "";
      setText(
        "worldInspectorMeta",
        type + " · " + status
          + fuelLabel
          + " · x " + formatNumber(position.x)
          + " · y " + formatNumber(position.y)
          + " · dir " + direction
      );
      inspector.hidden = false;
    });

    overlay.appendChild(hotspot);
  }
}



function renderResourceLegend() {
  const container = $("resourceLegend");
  if (!container) return;
  const overview = state.resourceOverview || {};
  const nearest = overview.nearest || {};
  const totals = overview.totals || {};
  const colors = {
    "iron-ore": "#6ab5f7",
    "copper-ore": "#d67a4e",
    coal: "#8c949b",
    stone: "#c6b28a",
    "crude-oil": "#594a42",
    "uranium-ore": "#72c957",
  };
  const important = ["iron-ore", "copper-ore", "coal", "stone", "crude-oil"];
  const radius = Number(overview.radius || 0);

  container.innerHTML = important.map((name) => {
    const row = nearest[name];
    const total = totals[name];
    const color = colors[name] || "#aaa";
    if (!row) {
      return '<span class="resource-chip missing">'
        + '<i class="swatch" style="--resource-color:' + color + '"></i>'
        + escapeHtml(humanizePrototype(name))
        + ' · not observed' + (radius ? ' ≤ ' + formatNumber(radius, 0) + ' tiles' : '')
        + '</span>';
    }
    const distance = Number(row.distance || 0);
    const count = total && Number(total.count || 0);
    return '<span class="resource-chip">'
      + '<i class="swatch" style="--resource-color:' + color + '"></i>'
      + escapeHtml(humanizePrototype(name))
      + ' · ' + formatNumber(distance, 0) + ' tiles'
      + (count ? ' · ' + formatNumber(count, 0) + ' nodes' : '')
      + '</span>';
  }).join("");
}


function drawStructuredFallback() {
  if (LEGACY_MAP_DISABLED) return;

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
  if (LEGACY_MAP_DISABLED) return;

  const render = (state.status && state.status.render) || {};
  const now = Date.now();
  if (!force && now - state.frameLastRequestedAt < 5000) return;
  const tick = state.world && state.world.tick;
  const geometry = currentWorldGeometry();
  const viewportKey = state.worldViewManual && state.worldViewMode !== "overview"
    ? ":" + geometry.center.x.toFixed(2)
      + ":" + geometry.center.y.toFixed(2)
      + ":" + geometry.radius.toFixed(2)
    : ":auto";
  const key = String(tick ?? "none") + ":" + String((state.world && state.world.entity_count) || 0)
    + ":" + String(render.sprite_count || 0)
    + ":" + state.worldViewMode
    + viewportKey;
  if (!force && state.frameTick === key) return;
  if (state.frameLoading && !force) return;

  state.frameLoading = true;
  state.frameLastRequestedAt = now;
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
    const sourceLabel = state.worldViewMode === "overview"
      ? "live resource coordinates · survey overview"
      : state.worldViewMode === "tactical"
        ? "live coordinates · tactical overlay"
        : "live coordinates · reconstructed terrain";
    setText(
      "frameSource",
      official.ready ? sourceLabel : "structured map · local assets pending"
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
  const params = new URLSearchParams({
    mode: requestedMode,
    t: key,
    ts: String(Date.now()),
  });
  if (state.worldViewManual && requestedMode !== "overview") {
    params.set("cx", geometry.center.x.toFixed(4));
    params.set("cy", geometry.center.y.toFixed(4));
    params.set("radius", geometry.radius.toFixed(4));
  }
  probe.src = "/api/world/frame.png?" + params.toString();
}

function applyWorldView() {
  if (LEGACY_MAP_DISABLED) return;

  const viewport = $("worldViewport");
  if (!viewport) return;
  viewport.style.transform =
    "translate(" + state.worldPanX + "px, " + state.worldPanY + "px)";
  viewport.classList.toggle(
    "can-pan",
    state.worldViewMode !== "overview"
  );
  const geometry = currentWorldGeometry();
  const zoom = state.worldViewManual
    ? Number(state.worldZoom || 1)
    : 1;
  setText(
    "zoomReset",
    zoom.toFixed(zoom < 2 ? 1 : 0) + "×"
  );
  const viewportLabel =
    "x " + geometry.center.x.toFixed(1)
    + " · y " + geometry.center.y.toFixed(1)
    + " · raio " + geometry.radius.toFixed(1);
  setText("viewportStatus", viewportLabel);
  const stage = $("worldStage");
  if (stage) stage.dataset.viewport = viewportLabel;
}

function setWorldZoom(nextZoom, anchorX = null, anchorY = null) {
  if (LEGACY_MAP_DISABLED) return;

  if (state.worldViewMode === "overview") return;
  const stage = $("worldStage");
  const rect = stage.getBoundingClientRect();
  const square = Math.max(1, Math.min(rect.width, rect.height));
  const geometry = ensureManualWorldGeometry();
  const baseRadius = Number(state.worldBaseRadius || geometry.radius);
  const requestedZoom = Math.max(0.25, Math.min(6, Number(nextZoom)));
  const nextRadius = Math.max(
    6,
    Math.min(96, baseRadius / requestedZoom)
  );
  const effectiveZoom = baseRadius / nextRadius;

  if (anchorX !== null && anchorY !== null) {
    const centerPxX = rect.left + rect.width / 2;
    const centerPxY = rect.top + rect.height / 2;
    const nx = (Number(anchorX) - centerPxX) * 2 / square;
    const ny = (Number(anchorY) - centerPxY) * 2 / square;
    const worldAnchorX = geometry.center.x + nx * geometry.radius;
    const worldAnchorY = geometry.center.y + ny * geometry.radius;
    state.worldCenterX = worldAnchorX - nx * nextRadius;
    state.worldCenterY = worldAnchorY - ny * nextRadius;
  }

  state.worldRadius = nextRadius;
  state.worldZoom = effectiveZoom;
  state.worldPanX = 0;
  state.worldPanY = 0;
  applyWorldView();
  renderWorldHotspots();
  state.frameTick = null;
  refreshWorldFrame(true);
}

function resetWorldView() {
  if (LEGACY_MAP_DISABLED) return;

  state.worldZoom = 1;
  state.worldPanX = 0;
  state.worldPanY = 0;
  state.worldCenterX = null;
  state.worldCenterY = null;
  state.worldRadius = null;
  state.worldBaseRadius = null;
  state.worldViewManual = false;
  applyWorldView();
  renderWorldHotspots();
  state.frameTick = null;
  refreshWorldFrame(true);
}

function installWorldModeControls() {
  if (LEGACY_MAP_DISABLED) return;

  for (const button of document.querySelectorAll("[data-world-mode]")) {
    button.addEventListener("click", () => {
      const mode = button.dataset.worldMode;
      if (!mode || mode === state.worldViewMode) return;
      state.worldViewMode = mode;
      for (const option of document.querySelectorAll("[data-world-mode]")) {
        option.classList.toggle("active", option.dataset.worldMode === mode);
      }
      $("worldStage").dataset.mode = mode;
      resetWorldView();
      renderResourceLegend();
      renderWorldHotspots();
      state.frameTick = null;
      refreshWorldFrame(true);
    });
  }
  $("worldStage").dataset.mode = state.worldViewMode;
  renderResourceLegend();
}

function installWorldControls() {
  if (LEGACY_MAP_DISABLED) return;

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
    const zoomGesture = event.ctrlKey || event.metaKey || document.fullscreenElement === stage;
    if (!zoomGesture) return;
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.14 : 1 / 1.14;
    setWorldZoom(state.worldZoom * factor, event.clientX, event.clientY);
  }, { passive: false });

  viewport.addEventListener("pointerdown", (event) => {
    const middleMouse = event.pointerType === "mouse" && event.button === 1;
    if (state.worldViewMode === "overview") return;
    if (event.pointerType === "mouse" && event.button !== 0 && !middleMouse) return;
    event.preventDefault();
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

  const stopDrag = (commit) => {
    if (commit && state.worldDragging) {
      const rect = stage.getBoundingClientRect();
      const square = Math.max(1, Math.min(rect.width, rect.height));
      const geometry = ensureManualWorldGeometry();
      state.worldCenterX = geometry.center.x
        - (state.worldPanX / square) * geometry.radius * 2;
      state.worldCenterY = geometry.center.y
        - (state.worldPanY / square) * geometry.radius * 2;
      state.worldPanX = 0;
      state.worldPanY = 0;
      state.frameTick = null;
      renderWorldHotspots();
      refreshWorldFrame(true);
    } else {
      state.worldPanX = 0;
      state.worldPanY = 0;
    }
    state.worldDragging = false;
    state.worldDragStart = null;
    viewport.classList.remove("dragging");
    applyWorldView();
  };
  viewport.addEventListener("pointerup", () => stopDrag(true));
  viewport.addEventListener("pointercancel", () => stopDrag(false));
  viewport.addEventListener("dblclick", resetWorldView);
  applyWorldView();
}

function updateFrameAge() {
  if (LEGACY_MAP_DISABLED) return;

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

function aggregateRateSamples(samples, samplePeriodSeconds) {
  if (!samples.length) return [];
  const period = Math.max(1e-6, Number(samplePeriodSeconds || 0));
  const binSize = Math.max(1, Math.ceil(1 / period));
  const output = [];
  for (let i = 0; i < samples.length; i += binSize) {
    const chunk = samples.slice(i, i + binSize).map(Number);
    output.push(
      chunk.reduce((sum, value) => sum + value, 0) / Math.max(1, chunk.length)
    );
  }
  return output;
}

// LuaFlowStatistics returns each sample already normalised to items/min over
// a bucket of duration/300. Items are discrete, so at 1m precision (0.2 s
// buckets) a single item reads as 300/min while the real rate is ~11/min:
// the raw series is dominated by sampling quantisation, not by production.
// A centred moving average over a window wide enough to contain several
// events recovers the sustained rate, which is the quantity being asked for.
function movingAverage(samples, window) {
  if (!samples.length) return [];
  const half = Math.max(1, Math.floor(window / 2));
  const output = new Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) {
    const from = Math.max(0, i - half);
    const to = Math.min(samples.length - 1, i + half);
    let sum = 0;
    for (let j = from; j <= to; j += 1) sum += samples[j];
    output[i] = sum / (to - from + 1);
  }
  return output;
}

function sustainedRateSeries(aggregated) {
  // At least a fifth of the window, never fewer than 5 bins. Two passes of a
  // box filter approximate a triangular one, which removes the square
  // shoulders a single pass leaves around an isolated burst.
  const window = Math.max(5, Math.round(aggregated.length * 0.2));
  return movingAverage(movingAverage(aggregated, window), window);
}

function stableProductionScale(key, values) {
  const positive = values.filter((value) => Number.isFinite(value) && value > 0);
  const candidate = positive.length ? Math.max(1, ...positive) * 1.12 : 1;
  const previous = Number(state.productionChartScale[key] || 0);
  const next = previous <= 0
    ? candidate
    : candidate > previous
      ? candidate
      : Math.max(candidate, previous * 0.92);
  state.productionChartScale[key] = next;
  return next;
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
  const samplePeriod = Number(
    (state.production && state.production.sample_period_seconds) || 0
  );
  const preparedEntries = entries.map(([name, samples]) => {
    const aggregated = aggregateRateSamples(samples, samplePeriod);
    return [name, aggregated, sustainedRateSeries(aggregated)];
  });
  const maxY = stableProductionScale(
    state.productionPrecision + ":" + mode,
    preparedEntries.flatMap(([, , smoothed]) => smoothed)
  );

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

  // Window average from the API (produced_rate/consumed_rate). This is the
  // sustained rate and does not depend on how the window was bucketed.
  const rateField = mode === "produced" ? "produced_rate" : "consumed_rate";
  let windowAverage = 0;
  for (const [name] of preparedEntries) {
    const row = series[name] || {};
    windowAverage = Math.max(windowAverage, Math.max(0, Number(row[rateField] || 0)));
  }
  if (windowAverage > 0 && windowAverage <= maxY) {
    const y = padTop + graphHeight - (windowAverage / maxY) * graphHeight;
    ctx.save();
    ctx.strokeStyle = "rgba(240,163,58,.45)";
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(width - padRight, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(240,163,58,.85)";
    ctx.font = "9px ui-monospace, monospace";
    ctx.textAlign = "left";
    ctx.fillText(
      "media da janela " + compactFactorioNumber(windowAverage) + "/min",
      padLeft + 6,
      Math.max(padTop + 9, y - 4)
    );
    ctx.restore();
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

    const pointX = (i, length) =>
      padLeft + (i / Math.max(length - 1, 1)) * graphWidth;
    const pointY = (value) =>
      padTop + graphHeight - (Math.max(0, value) / maxY) * graphHeight;

    const gradient = ctx.createLinearGradient(0, padTop, 0, padTop + graphHeight);
    gradient.addColorStop(0, color + "44");
    gradient.addColorStop(1, color + "05");
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.moveTo(pointX(0, smoothed.length), padTop + graphHeight);
    for (let i = 0; i < smoothed.length; i += 1) {
      ctx.lineTo(pointX(i, smoothed.length), pointY(smoothed[i]));
    }
    ctx.lineTo(pointX(smoothed.length - 1, smoothed.length), padTop + graphHeight);
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = color;
    ctx.lineWidth = 2.25;
    ctx.lineJoin = "round";
    ctx.beginPath();
    for (let i = 0; i < smoothed.length; i += 1) {
      const x = pointX(i, smoothed.length);
      const y = pointY(smoothed[i]);
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
    .filter((item) => item.rate > 0 || item.count > 0)
    .sort((a, b) => b.rate - a.rate);
  const maxRate = Math.max(0, ...entries.map((item) => item.rate));
  setText(
    mode === "produced" ? "producedPeak" : "consumedPeak",
    "avg " + compactFactorioNumber(maxRate) + "/min"
  );

  if (!entries.length) {
    container.innerHTML = '<div class="placeholder-row">No '
      + (mode === "produced" ? "production" : "consumption")
      + ' in this window.</div>';
    return;
  }

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

  renderCapabilityHealth();

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

function productionRate(name, mode = "produced") {
  const row = ((state.production && state.production.series) || {})[name] || {};
  const key = mode === "produced" ? "produced_rate" : "consumed_rate";
  return Math.max(0, Number(row[key] || 0));
}

function renderCapabilityHealth() {
  const container = $("capabilityHealth");
  if (!container) return;

  const metrics = (state.research && state.research.metrics) || {};
  const entities = Array.isArray(state.world && state.world.entities)
    ? state.world.entities
    : [];
  const researchWorld = (state.research && state.research.world) || {};

  function centerForCapability(key) {
    const raw = key === "iron"
      ? researchWorld.patch_center
      : key === "coal"
        ? (researchWorld.coal_patch || {}).center
        : key === "copper"
          ? (researchWorld.copper_patch || {}).center
          : null;
    if (!raw) return null;
    const x = Number(raw.x);
    const y = Number(raw.y);
    return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
  }

  function entitiesNear(center, names, radius = 24) {
    if (!center) return [];
    const allowed = new Set(names);
    return entities.filter((entity) => {
      if (!allowed.has(String(entity.name || ""))) return false;
      const pos = getPosition(entity);
      if (!pos) return false;
      const dx = pos.x - center.x;
      const dy = pos.y - center.y;
      return Math.hypot(dx, dy) <= radius;
    });
  }

  function physicalFault(key) {
    if (["iron", "coal", "copper"].includes(key)) {
      const machines = entitiesNear(
        centerForCapability(key),
        ["burner-mining-drill", "electric-mining-drill"]
      );
      if (!machines.length) return "";
      const noFuel = machines.filter((e) => String(e.status) === "no_fuel").length;
      const noPower = machines.filter((e) => [
        "no_power",
        "low_power",
        "not_plugged_in_electric_network",
        "not_connected",
      ].includes(String(e.status))).length;
      if (noFuel) return noFuel + "/" + machines.length + " drills no fuel";
      if (noPower) return noPower + "/" + machines.length + " drills no power";
      const blocked = machines.filter((e) => [
        "full_output",
        "no_minable_resources",
        "waiting_for_space_in_destination",
      ].includes(String(e.status))).length;
      if (blocked) return blocked + "/" + machines.length + " drills blocked";
      return machines.length + " drills present";
    }

    if (key === "power") {
      const engines = entities.filter((e) => String(e.name) === "steam-engine");
      if (!engines.length) return "no steam engine";
      const powered = engines.filter((e) => Number(e.energy || 0) > 0).length;
      return powered
        ? powered + "/" + engines.length + " engines energized"
        : engines.length + " steam engine(s) unenergized";
    }

    if (key === "science") {
      const scienceMachines = entities.filter((e) =>
        String(e.recipe || "") === "automation-science-pack"
        || String(e.name || "") === "lab"
      );
      if (!scienceMachines.length) return "no science machine";
      const faults = scienceMachines
        .map((e) => String(e.status || "unknown"))
        .filter((value) => value && value !== "working");
      return faults.length ? faults.slice(0, 2).join(" · ") : "science machines ready";
    }

    return "";
  }
  const achieved = new Set(
    Array.isArray(state.progression && state.progression.achieved)
      ? state.progression.achieved
      : []
  );
  const coalProduced = productionRate("coal");
  const coalConsumed = productionRate("coal", "consumed");
  const accounting = (
    state.research
    && state.research.resource_accounting
    && state.research.resource_accounting.exogenous_inputs
  ) || {};
  const coalAccounting = accounting.coal || {};
  const coalOrigin = String(coalAccounting.status || "external");
  const coalExternal = !["retired", "internal", "self_sufficient"].includes(
    coalOrigin
  );
  const coalReserve = Number(
    metrics.survival_coal_reserve
      ?? metrics.coal_endogenous_stockpile
      ?? metrics.open_play_coal_stockpile
      ?? coalAccounting.endogenous_stockpile
      ?? 0
  );
  const coalSafetyStock = Number(
    metrics.coal_safety_stock_target
      ?? coalAccounting.safety_stock_target
      ?? 4
  );

  const autonomy = state.autonomy || {};
  const validationConfig = (
    state.research
    && state.research.evolution
    && state.research.evolution.validation_configuration
  ) || {};
  setText(
    "autonomyGenome",
    "layout " + String(validationConfig.autonomy_layout_variant ?? "--")
      + " · coal " + String(validationConfig.autonomy_commissioning_coal ?? "--")
      + " · belt margin " + String(validationConfig.autonomy_belt_margin ?? "--")
      + " · pole margin " + String(validationConfig.autonomy_pole_margin ?? "--")
  );

  const topology = autonomy.topology || {};
  const closedLoop = !!autonomy.closed_loop;

  const capabilities = [
    {
      key: "iron",
      label: "Iron backbone",
      liveRate: Math.max(
        productionRate("iron-ore"),
        productionRate("iron-plate")
      ),
      proven: achieved.has("iron_backbone")
        || Number(metrics.belt_smelting_plate_rate_per_s || 0) > 0
        || Number(metrics.iron_plate_output || 0) > 0,
      unit: "/min",
      autonomous: !!topology.iron_chain_live
        && !!topology.smelting_distribution
        && !!topology.zero_manual_logistics,
    },
    {
      key: "copper",
      label: "Copper chain",
      liveRate: Math.max(
        productionRate("copper-ore"),
        productionRate("copper-plate")
      ),
      proven: achieved.has("copper_mining")
        || Number(metrics.copper_ore_output || 0) > 0,
      unit: "/min",
      autonomous: !!topology.copper_chain_live
        && !!topology.smelting_distribution
        && !!topology.zero_manual_logistics,
    },
    {
      key: "coal",
      label: "Coal supply",
      liveRate: coalProduced,
      proven: achieved.has("coal_mining")
        || Number(metrics.coal_output || 0) > 0,
      unit: "/min",
      external: coalExternal && coalConsumed > 0,
      origin: coalOrigin,
      reserve: coalReserve,
      safetyStock: coalSafetyStock,
      autonomous: !!topology.coal_chain_live
        && !!topology.fuel_distribution
        && !!topology.zero_manual_logistics,
    },
    {
      key: "power",
      label: "Steam power",
      liveRate: Number(
        metrics.open_play_steam_energy
          ?? metrics.steam_energy
          ?? 0
      ),
      proven: achieved.has("steam_power")
        || Number(
          metrics.open_play_steam_energy
            ?? metrics.steam_energy
            ?? 0
        ) > 0,
      unit: " J",
      autonomous: !!topology.electric_distribution
        && !!topology.healthy_fuel
        && !!topology.healthy_power
        && !!topology.zero_manual_logistics,
    },
    {
      key: "science",
      label: "Automation science",
      liveRate: productionRate("automation-science-pack"),
      proven: achieved.has("automation_science")
        || Number(metrics.automation_science_output || 0) > 0,
      unit: "/min",
      autonomous: closedLoop && !!topology.producing_industry,
    },
  ];

  container.innerHTML = capabilities.map((item) => {
    let cls = "unproven";
    let detail = "not validated";
    if (item.external) {
      cls = "external";
      detail = compactFactorioNumber(coalConsumed) + "/min consumed · external bootstrap";
    } else if (item.liveRate > 0) {
      cls = item.autonomous ? "live" : "dormant";
      detail = compactFactorioNumber(item.liveRate) + item.unit
        + (item.autonomous ? " · autonomous" : " · live · autonomy unverified");
    } else if (item.proven) {
      const fault = physicalFault(item.key);
      cls = fault && !fault.includes("present") && !fault.includes("ready")
        ? "regressed"
        : "dormant";
      detail = "commissioned"
        + (item.autonomous ? " · autonomous evidence retained" : " · not closed-loop")
        + (fault ? " · " + fault : "");
    } else {
      const fault = physicalFault(item.key);
      if (fault && (
        fault.includes("no fuel")
        || fault.includes("no power")
        || fault.includes("unenergized")
      )) {
        cls = "regressed";
        detail = fault;
      }
    }
    return '<span class="capability-chip ' + cls + '">'
      + '<strong>' + escapeHtml(item.label) + '</strong>'
      + '<small>' + escapeHtml(detail) + '</small>'
      + '</span>';
  }).join("");
}

function fitnessSummary(fitness) {
  if (!fitness || typeof fitness !== "object") return "no fitness baseline";
  const caps = Array.isArray(fitness.capabilities) ? fitness.capabilities.length : 0;
  const total = Number(fitness.total_rate_per_s || 0);
  const dependencies = Number(fitness.external_dependencies || 0);
  const autonomy = fitness.autonomy_score;
  const manual = fitness.manual_logistics_calls;
  return caps + " capabilities · "
    + formatNumber(total, 3) + "/s aggregate · "
    + dependencies + " external dep."
    + (autonomy !== null && autonomy !== undefined
      ? " · autonomy " + formatNumber(Number(autonomy) * 100, 0) + "%"
      : " · autonomy n/a")
    + (manual !== null && manual !== undefined
      ? " · manual " + String(manual)
      : " · manual n/a");
}

function genomeSummary(configuration) {
  if (!configuration || typeof configuration !== "object") {
    return "genome not recorded";
  }
  const turn = Number(configuration.routing_turn_penalty);
  const exploration = Number(
    configuration.placement_exploration ?? configuration.ucb_exploration
  );
  const parts = [];
  if (Number.isFinite(turn)) parts.push("A* turn " + formatNumber(turn, 3));
  if (Number.isFinite(exploration)) {
    parts.push("UCB explore " + formatNumber(exploration, 2));
  }
  if (configuration.placement_best_arm) {
    parts.push("placement " + String(configuration.placement_best_arm));
  }
  const layout = Number(configuration.autonomy_layout_variant);
  if (Number.isFinite(layout)) parts.push("layout " + layout);
  const coal = Number(configuration.autonomy_commissioning_coal);
  if (Number.isFinite(coal)) parts.push("coal seed " + coal);
  const beltMargin = Number(configuration.autonomy_belt_margin);
  const poleMargin = Number(configuration.autonomy_pole_margin);
  if (Number.isFinite(beltMargin) || Number.isFinite(poleMargin)) {
    parts.push(
      "margin B" + (Number.isFinite(beltMargin) ? beltMargin : "--")
      + "/P" + (Number.isFinite(poleMargin) ? poleMargin : "--")
    );
  }
  return parts.length ? parts.join(" · ") : "genome not recorded";
}

function contenderSummary(record, fallbackFitness) {
  const fitness = record && record.fitness ? record.fitness : fallbackFitness;
  return fitnessSummary(fitness) + " · " + genomeSummary(
    record && record.configuration
  );
}

function renderLearningObservatory() {
  const datasets = state.datasets || {};
  const recurrent = datasets.recurrent_world_model || {};
  const evolution = state.evolution || {};
  const artifacts = evolution.learning_artifacts || {};
  const strategy = artifacts.strategy || {};
  const robustness = artifacts.robustness || {};
  const counterexamples = Array.isArray(artifacts.counterexamples)
    ? artifacts.counterexamples.slice().reverse()
    : [];
  const execution = (state.status && state.status.execution) || {};

  const labeledSamples = Number(
    datasets.action_labeled_samples
      ?? recurrent.action_labeled_samples
      ?? 0
  );
  const labeledRuns = Number(
    datasets.action_labeled_runs
      ?? recurrent.action_labeled_runs
      ?? 0
  );
  const sampleTarget = 500;
  const runTarget = 3;
  const sampleRatio = Math.min(1, labeledSamples / sampleTarget);
  const runRatio = Math.min(1, labeledRuns / runTarget);
  const causalProgress = Math.min(sampleRatio, runRatio) * 100;
  const causalReady = labeledSamples >= sampleTarget && labeledRuns >= runTarget;

  setText("causalSamples", formatNumber(labeledSamples, 0) + " / " + sampleTarget);
  setText("causalRuns", formatNumber(labeledRuns, 0) + " / " + runTarget);
  setText(
    "causalAction",
    execution.action_active
      ? actionLabel((execution.action || {}).action_kind)
      : execution.writer_active
        ? "entre ações"
        : "ocioso"
  );
  const causalBar = $("causalProgressBar");
  if (causalBar) causalBar.style.width = causalProgress + "%";
  const operational = cortexOperationalView();
  const historical = operational.historicalEvidenceMode;
  setClassText(
    "causalLearningBadge",
    historical ? "FROZEN DATASET" : causalReady ? "elegível para treino" : "coletando",
    historical ? "badge neutral" : causalReady ? "badge good" : "badge live"
  );

  const holdout = recurrent.generation_holdout || {};
  const modelSelection = recurrent.model_selection || {};
  setText(
    "causalLearningDetail",
    historical
      ? "evidência histórica preservada · nenhum treino/holdout ativo agora · F4-C exige novo protocolo causal"
      : causalReady
        ? (
          holdout.usable
            ? "Dados mínimos atingidos · holdout entre gerações disponível · "
              + String(modelSelection.reason || "aguardando seleção")
            : "Dados mínimos atingidos · aguardando holdout entre gerações suficiente"
        )
      : "Gate causal: "
        + Math.max(0, sampleTarget - labeledSamples)
        + " amostras e "
        + Math.max(0, runTarget - labeledRuns)
        + " run(s) ainda faltando. Snapshots antigos não promovem o GRU."
  );

  const requiredPasses = Number(robustness.required_passes || 3);
  const passCount = Number(
    robustness.distinct_pass_seed_count
      ?? robustness.pass_count
      ?? 0
  );
  const qualified = !!robustness.qualified;
  setText("robustnessPasses", passCount + " / " + requiredPasses);
  const robustnessBar = $("robustnessProgressBar");
  if (robustnessBar) {
    robustnessBar.style.width = (
      Math.min(100, (passCount / Math.max(1, requiredPasses)) * 100)
    ) + "%";
  }
  setClassText(
    "robustnessBadge",
    historical
      ? "HISTÓRICO · FROZEN"
      : qualified
        ? "qualificado"
        : passCount
          ? "em qualificação"
          : "aguardando primeiro passe",
    historical ? "badge neutral" : qualified ? "badge good" : passCount ? "badge live" : "badge neutral"
  );
  const passSeeds = Array.isArray(robustness.distinct_pass_seeds)
    ? robustness.distinct_pass_seeds
    : [];
  setText(
    "robustnessDetail",
    qualified
      ? "Campeão qualificado em seeds independentes: " + passSeeds.join(", ")
      : (
          passSeeds.length
            ? "Seeds aprovadas: " + passSeeds.join(", ")
              + " · candidato congelado até completar o gate."
            : "Nenhuma seed aprovada ainda para esta configuração. "
              + "Uma única run não promove o campeão."
        )
  );

  const configuration = strategy.configuration || {};
  const strategyContainer = $("strategyMetrics");
  if (strategyContainer) {
    const rows = [
      ["Fe", configuration.open_play_iron_target],
      ["Cu", configuration.open_play_copper_target],
      ["Madeira", configuration.open_play_wood_target],
      ["Margem belt", configuration.autonomy_belt_margin],
      ["Detour", configuration.autonomy_route_detour_margin],
      ["Margem poste", configuration.autonomy_pole_margin],
    ];
    strategyContainer.innerHTML = rows.map(([label, value]) =>
      '<div class="strategy-metric">'
        + '<span>' + escapeHtml(label) + '</span>'
        + '<strong>' + escapeHtml(value ?? "--") + '</strong>'
        + '</div>'
    ).join("");
  }
  setClassText(
    "strategyBadge",
    historical
      ? "HISTÓRICO · " + (strategy.counterexample_run ? "adaptada" : "baseline")
      : strategy.counterexample_run ? "adaptada" : "baseline",
    historical ? "badge neutral" : strategy.counterexample_run ? "badge live" : "badge neutral"
  );

  const repairs = Array.isArray(strategy.deterministic_repairs)
    ? strategy.deterministic_repairs
    : [];
  const repairContainer = $("strategyRepair");
  if (repairContainer) {
    repairContainer.innerHTML = repairs.length
      ? repairs.slice(0, 3).map((repair) =>
          '<div><strong>' + escapeHtml(repairReasonLabel(repair.reason)) + '</strong>'
          + (
              repair.shortfall_ratio !== undefined
                ? ' · déficit ' + formatNumber(Number(repair.shortfall_ratio) * 100, 1) + '%'
                : ''
            )
          + (
              repair.detour_margin
                ? ' · detour ' + escapeHtml(repair.detour_margin.before)
                  + '→' + escapeHtml(repair.detour_margin.after)
                : ''
            )
          + (
              repair.belt_margin
                ? ' · belt ' + escapeHtml(repair.belt_margin.before)
                  + '→' + escapeHtml(repair.belt_margin.after)
                : ''
            )
          + '</div>'
        ).join("")
      : '<span class="placeholder-row">Sem reparo estrutural registrado.</span>';
  }

  setText(
    "counterexampleCount",
    String(Number(artifacts.counterexample_count || counterexamples.length || 0))
  );
  const memoryContainer = $("counterexampleList");
  if (memoryContainer) {
    memoryContainer.innerHTML = counterexamples.length
      ? counterexamples.slice(0, 4).map((row) => {
          const repair = row.repair || {};
          const deterministic = Array.isArray(repair.deterministic_repairs)
            ? repair.deterministic_repairs
            : [];
          const reasons = deterministic
            .map((item) => repairReasonLabel(item.reason))
            .filter(Boolean);
          return '<article class="counterexample-row">'
            + '<header><strong>' + escapeHtml(stageLabel(row.stage))
            + '</strong><b>' + escapeHtml(row.phase || "--") + '</b></header>'
            + '<small>' + escapeHtml(reasons.join(" · ") || row.detail || "falha registrada")
            + '</small></article>';
        }).join("")
      : '<span class="placeholder-row">Nenhum counterexample persistido.</span>';
  }
}


function renderExperimentContext() {
  const context = state.experimentContext || {};
  const summary = context.result_summary || {};
  const baseline = context.kind === "baseline_seed";
  const seed = context.seed == null ? "--" : String(context.seed);
  const release = context.baseline_release || {};
  const releaseCommit = String(release.commit || "--").slice(0, 8);
  const failed = Array.isArray(summary.failed_stages) ? summary.failed_stages : [];
  const science = summary.logistic_science_output;
  const worldEntities = Number((state.world || {}).entity_count || 0);

  setText(
    "experimentContextTitle",
    baseline
      ? "Baseline isolada · seed " + seed + " · " + String(context.status || "--")
      : String(context.label || "Global / Cortex")
  );
  setText(
    "experimentContextDetail",
    baseline
      ? "evidência: sandbox da seed · runtime " + releaseCommit
        + " · mundo: RCON ao vivo · " + worldEntities + " entidades"
        + (failed.length ? " · falhou: " + failed.join(", ") : "")
        + (science !== null && science !== undefined
          ? " · logistic science output " + formatNumber(science, 0)
          : "")
        + (context.status === "completed"
          ? " · mundo continua tickando após o snapshot final"
          : "")
      : "estado global do Cortex · baselines permanecem isoladas como evidência"
  );
  setClassText(
    "evidenceTruthBadge",
    baseline ? "EVIDENCE · SEED " + seed : "EVIDENCE · GLOBAL",
    baseline ? "badge good" : "badge neutral"
  );
  const operational = cortexOperationalView();
  const worldEmpty = Number((state.world || {}).entity_count || 0) === 0;
  setClassText(
    "worldTruthBadge",
    worldEmpty ? "WORLD · LIVE RCON · EMPTY" : "WORLD · LIVE RCON",
    worldEmpty ? "badge warn" : "badge live"
  );

  const modeNotice = $("operationalModeNotice");
  if (modeNotice) {
    modeNotice.hidden = !operational.historicalEvidenceMode;
    if (operational.historicalEvidenceMode) {
      setText(
        "operationalModeTitle",
        "CORTEX SHADOW · nenhum executor controla o mundo"
      );
      setText(
        "operationalModeDetail",
        "F4-B está fechado. Gerações, curriculum, UCB, modelos e timelines abaixo são evidência histórica congelada; evolution está OFF. O próximo trabalho é F4-C protocolar/causal, não uma geração live."
      );
      setClassText(
        "operationalModeBadge",
        "IDLE INTENCIONAL · HISTÓRICO PRESERVADO",
        "badge warn"
      );
    }
  }
  const worldNotice = $("worldStateNotice");
  if (worldNotice) {
    worldNotice.hidden = !(operational.historicalEvidenceMode && worldEmpty);
    if (!worldNotice.hidden) {
      worldNotice.textContent = "WORLD LIVE conectado, porém vazio: 0 entidades é o estado físico observado do player force. Não há runner/evolution ativo construindo neste momento; o mapa não está travado.";
    }
  }

  if (baseline || context.kind === "global") {
    const series = context.series_progress || {};
    const completed = Number(series.completed || 0);
    const configured = Number(series.configured || 0);
    const runningCount = Number(series.running || 0);
    const cortexPhase = context.cortex_phase || {};
    const seriesComplete = baseline
      ? configured > 0 && completed >= configured && runningCount === 0
      : true;
    const phase2Checkpoint = String(cortexPhase.phase2_checkpoint || "F2");
    const phase3Checkpoint = String(cortexPhase.phase3_checkpoint || "");
    const phase4Checkpoint = String(cortexPhase.phase4_checkpoint || "");
    const executiveShadowPhase = phase3Checkpoint === "F3-A";
    const verificationCreditPhase = phase3Checkpoint === "F3-B";
    const pairedShadowPhase = phase3Checkpoint === "F3-C";
    const memorySubstratePhase = phase4Checkpoint === "F4-A";
    const memoryRetrievalPhase = phase4Checkpoint === "F4-B";
    const phase4ShadowActive = memorySubstratePhase || memoryRetrievalPhase;
    const phase3ShadowActive = executiveShadowPhase
      || verificationCreditPhase
      || pairedShadowPhase;
    const cortexShadowState = phase3ShadowActive || phase4ShadowActive;
    const executiveShadow = cortexPhase.phase3_executive_shadow_kernel || {};
    const verificationCredit = cortexPhase.phase3_verification_credit_ledger || {};
    const pairedShadow = cortexPhase.phase3_paired_shadow_comparison || {};
    const memorySubstrate = cortexPhase.phase4_memory_substrate || {};
    const memoryRetrieval = cortexPhase.phase4_memory_retrieval || {};
    const functionalCanary = phase2Checkpoint === "F2-F3";
    const deliveryActuatorPhase = phase2Checkpoint.startsWith("F2-F4");
    const deliveryActuatorRunner = phase2Checkpoint === "F2-F4B";
    const deliveryActuatorCanary = phase2Checkpoint === "F2-F4C";
    const runnerIndependencePhase = phase2Checkpoint === "F2-G1";
    const optionCompositionPhase = phase2Checkpoint === "F2-G2";
    const optionExecutionPhase = phase2Checkpoint === "F2-G3";
    const persistentAuthorityPhase = phase2Checkpoint === "F2-G4A";
    const liveOptionCanaryPhase = phase2Checkpoint === "F2-G4B";
    const baselineOnlyPhase = phase2Checkpoint === "F2-G5";
    const useLiveOptionEvidence = cortexShadowState
      || liveOptionCanaryPhase
      || baselineOnlyPhase;
    const useDeliveryCanaryEvidence = deliveryActuatorCanary
      || runnerIndependencePhase
      || optionCompositionPhase
      || optionExecutionPhase
      || persistentAuthorityPhase;
    const useFunctionalCanaryEvidence = functionalCanary || deliveryActuatorPhase;
    const canary = useLiveOptionEvidence
      ? (cortexPhase.phase2_live_option_canary || {})
      : (useDeliveryCanaryEvidence
      ? (cortexPhase.phase2_delivery_actuator_canary || {})
      : (useFunctionalCanaryEvidence
        ? (cortexPhase.phase2_functional_canary || {})
        : (cortexPhase.phase2_canary || {})));
    const candidate = canary.candidate_after || {};
    const controlledExecution = phase2Checkpoint.startsWith("F2-E")
      || functionalCanary;
    const dependencyComposed = phase2Checkpoint === "F2-F2";
    const actionStatus = String(canary.action_status || canary.status || "--");
    const canaryRejected = actionStatus === "rejected";
    const canaryAccepted = actionStatus === "accepted";
    const canaryUnsustained = (useDeliveryCanaryEvidence || useLiveOptionEvidence)
      && canaryAccepted
      && canary.sustained_operation === false;

    let phaseTitle;
    let phaseBadge;
    if (!seriesComplete) {
      phaseTitle = "F1-B · baseline corrigida em execução · "
        + (context.mode === "exploratory"
          ? "exploratória"
          : String(context.mode || ""));
      phaseBadge = configured
        ? "F1-B · " + completed + "/" + configured
        : "F1-B · seed " + seed;
    } else if (memoryRetrievalPhase) {
      phaseTitle = "F4-B · Hybrid Retrieval + Consolidation · SHADOW";
      phaseBadge = "F4-B · retrieval/decay validados · ablation ainda aberta";
    } else if (memorySubstratePhase) {
      phaseTitle = "F4-A · Typed Memory Substrate · SHADOW";
      phaseBadge = "F4-A · memória tipada · retrieval/ablation ainda abertos";
    } else if (pairedShadowPhase) {
      phaseTitle = "F3 · COMPLETE · F3-C paired shadow comparison";
      phaseBadge = "F3 COMPLETE · escolhas explícitas · no live authority";
    } else if (verificationCreditPhase) {
      phaseTitle = "F3-B · Verification + Credit Ledger · SHADOW";
      phaseBadge = "F3-B · outcomes verificados · ledger persistente";
    } else if (executiveShadowPhase) {
      phaseTitle = "F3-A · Executive Shadow Kernel · SHADOW";
      phaseBadge = "F3-A · alternativas explícitas · no live authority";
    } else if (baselineOnlyPhase) {
      phaseTitle = "F2 · COMPLETE · G5 baseline-only enforcement";
      phaseBadge = "F2 COMPLETE · generic Option API · legacy baseline-only";
    } else if (liveOptionCanaryPhase) {
      phaseTitle = "F2-G4B · live Option functional accept · epoch auditado";
      phaseBadge = "F2-G4B · one-shot live PASS · sustentabilidade aberta";
    } else if (persistentAuthorityPhase) {
      phaseTitle = "F2-G4A · persistent one-shot authority · DRY-RUN";
      phaseBadge = "F2-G4A · durable ledger · no live EXECUTE";
    } else if (optionExecutionPhase) {
      phaseTitle = "F2-G3 · Option execution boundary · FAKE/REPLAY";
      phaseBadge = "F2-G3 · boundary universal · no live EXECUTE";
    } else if (optionCompositionPhase) {
      phaseTitle = "F2-G2 · processing-chain Option · SHADOW";
      phaseBadge = "F2-G2 · typed Option · tick budget explicit";
    } else if (runnerIndependencePhase) {
      phaseTitle = "F2-G1 · runner independence · SHADOW";
      phaseBadge = "F2-G1 · generic runtime instrumentation";
    } else if (deliveryActuatorCanary) {
      phaseTitle = canaryUnsustained
        ? "F2-F4C · functional accept · sustentabilidade não provada"
        : "F2-F4C · delivery actuator canary evidence";
      phaseBadge = canaryUnsustained
        ? "F2-F4C · output funcional · final no_fuel"
        : "F2-F4C · canary evidence · v3";
    } else if (deliveryActuatorRunner) {
      phaseTitle = "F2-F4B · runner integrado · NO LIVE EXECUTE";
      phaseBadge = "F2-F4B · v3 runner · dry-run only";
    } else if (deliveryActuatorPhase) {
      phaseTitle = "F2-F4A · delivery actuator dependency · SHADOW";
      phaseBadge = "F2-F4A · actuator energy · v3";
    } else if (functionalCanary) {
      phaseTitle = "F2-F3 · dependency-complete canary evidence";
      phaseBadge = "F2-F3 · rollback evidence";
    } else if (controlledExecution) {
      phaseTitle = phase2Checkpoint + " · controlled transaction evidence";
      phaseBadge = phase2Checkpoint + " · evidence";
    } else if (dependencyComposed) {
      phaseTitle = "F2-F2 · fuel dependency composed · canary pending";
      phaseBadge = "F2-F2 · typed fuel · v2";
    } else {
      phaseTitle = phase2Checkpoint + " · Cortex research · SHADOW";
      phaseBadge = phase2Checkpoint + " · shadow";
    }

    setText("cortexPhaseTitle", phaseTitle);
    setClassText(
      "cortexPhaseBadge",
      phaseBadge,
      seriesComplete || context.status === "completed"
        ? "badge good"
        : "badge live"
    );

    setText(
      "cortexCanaryLabel",
      cortexShadowState || baselineOnlyPhase
        ? "F2-G4B · ÚLTIMA EVIDÊNCIA LIVE VIA OPTION"
        : (liveOptionCanaryPhase
          ? "F2-G4B · CANÁRIO LIVE VIA OPTION"
          : (useDeliveryCanaryEvidence
        ? "F2-F4C · ÚLTIMO CANÁRIO FUNCIONAL"
        : (useFunctionalCanaryEvidence
          ? "F2-F3 · ÚLTIMO CANÁRIO FUNCIONAL"
          : "F2-E · TRANSAÇÃO CONTROLADA")))
    );

    if (canary.exists) {
      setClassText(
        "cortexCanaryStatus",
        actionStatus.toUpperCase()
          + (canary.refusal ? " · " + String(canary.refusal) : ""),
        canaryUnsustained
          ? "warn"
          : (canaryAccepted ? "good" : (canaryRejected ? "warn" : "neutral"))
      );
      setText(
        "cortexCanaryDetail",
        "candidate: coverage "
          + formatNumber(candidate.physical_processing_coverage, 2)
          + " · processor "
          + String(candidate.processor_status || "--")
          + " · output "
          + formatNumber(candidate.processor_output, 2)
          + (useDeliveryCanaryEvidence && canary.actuator
            ? " · actuator " + String(canary.actuator)
              + (canary.actuator_fuel ? " · fuel " + String(canary.actuator_fuel) : "")
              + (canaryUnsustained
                ? " · sustentabilidade não provada"
                : "")
            : (useFunctionalCanaryEvidence && canary.fuel
              ? " · fuel " + String(canary.fuel)
                + " x" + String(canary.fuel_units == null ? "--" : canary.fuel_units)
              : ""))
      );
      setText(
        "cortexCanaryRollback",
        "commit "
          + (canary.transaction_committed ? "SIM" : "NÃO")
          + " · rollback "
          + (canary.rollback_observed ? "PASS" : "--")
          + " · run "
          + String(canary.run_id || "--")
      );
      let authorityDetail;
      if (memoryRetrievalPhase) {
        const retrievals = memoryRetrieval.retrievals || {};
        const consolidation = memoryRetrieval.consolidation || {};
        authorityDetail = "F4-B SHADOW: hybrid structural+lexical retrieval + consolidation + non-destructive decay. "
          + "Artifact " + String(memoryRetrieval.status || "--").toUpperCase()
          + " · queries " + String(Object.keys(retrievals).length || "--")
          + " · repeated semantic "
          + String(consolidation.repeated_semantic_items ?? "--")
          + " · repeated counterexamples "
          + String(consolidation.repeated_counterexample_items ?? "--")
          + " · DB read-only "
          + (memoryRetrieval.database_read_only_replay ? "PASS" : "--")
          + " · causal ablation/transfer ainda não provados · continuous authority OFF.";
      } else if (memorySubstratePhase) {
        const memorySnapshot = memorySubstrate.store?.snapshot || {};
        authorityDetail = "F4-A SHADOW: working + episodic + semantic + procedural + counterexample memory. "
          + "Artifact " + String(memorySubstrate.status || "--").toUpperCase()
          + " · episodic " + String(memorySnapshot.episodic?.items || "--")
          + " · semantic " + String(memorySnapshot.semantic?.items || "--")
          + " · procedural " + String(memorySnapshot.procedural?.items || "--")
          + " · counterexamples " + String(memorySnapshot.counterexample?.items || "--")
          + " · DB " + String(memorySubstrate.live_quick_check || "--")
          + " · retrieval/ablation ainda não provados · continuous authority OFF.";
      } else if (pairedShadowPhase) {
        authorityDetail = "F3-C SHADOW: comparação pareada de escolhas sobre os mesmos objetivos históricos. "
          + "Artifact " + String(pairedShadow.status || "--").toUpperCase()
          + " · pairs "
          + String((pairedShadow.comparison || {}).paired_episode_count || "--")
          + " · legacy agreement "
          + String((pairedShadow.comparison || {}).legacy_policy_agreement || "--")
          + " · policy divergence "
          + String((pairedShadow.comparison || {}).policy_divergence_pairs || "--")
          + " · F3 Exit Gate completo sem claim de superiority · G4B segue última evidência live.";
      } else if (verificationCreditPhase) {
        authorityDetail = "F3-B SHADOW: verification-after-action + credit fail-closed + ledger persistente. "
          + "Artifact " + String(verificationCredit.status || "--").toUpperCase()
          + " · episodes "
          + String((verificationCredit.verification || {}).selected_episode_count || "--")
          + " · ledger "
          + String(verificationCredit.ledger_live_quick_check || "--")
          + " · G4B permanece a última evidência live · continuous authority OFF.";
      } else if (executiveShadowPhase) {
        authorityDetail = "F3-A SHADOW: BeliefState/GoalStack + múltiplas alternativas + hard feasibility + policy/prediction-before-action. "
          + "Artifact " + String(executiveShadow.status || "--").toUpperCase()
          + " · candidates "
          + String((executiveShadow.counterfactual_expansion || {}).candidate_count || "--")
          + " · G4B permanece a última evidência live · continuous authority OFF.";
      } else if (baselineOnlyPhase) {
        authorityDetail = "F2-G5: runner legado fail-closed fora de baseline; F2 Exit Gate completo. G4B permanece a última evidência live · continuous authority OFF.";
      } else if (liveOptionCanaryPhase) {
        authorityDetail = "F2-G4B: uma única Option live aceita sob grant persistente + WorldLease atestado; temporal epoch auditado · continuous authority OFF.";
      } else if (persistentAuthorityPhase) {
        authorityDetail = "F2-G4A: grant one-shot persistente/restart-safe validado em dry-run; live Option EXECUTE bloqueado · continuous authority OFF.";
      } else if (optionExecutionPhase) {
        authorityDetail = "F2-G3 validado apenas em fake/replay; live Option EXECUTE bloqueado · continuous authority OFF.";
      } else if (optionCompositionPhase) {
        authorityDetail = "F2-G2 permanece em SHADOW/replay; último EXECUTE pertence ao canário F2-F4C · continuous authority OFF.";
      } else if (runnerIndependencePhase) {
        authorityDetail = "F2-G1 permanece em SHADOW; último EXECUTE pertence ao canário F2-F4C · continuous authority OFF.";
      } else {
        authorityDetail = "EXECUTE concedido somente ao canário registrado"
          + " · continuous authority "
          + (canary.continuous_authority ? "ON" : "OFF")
          + " · baseline/holdout separados.";
      }
      setText("cortexAuthorityDetail", authorityDetail);
    } else {
      setText("cortexCanaryStatus", "canário não executado");
      setText(
        "cortexCanaryDetail",
        "nenhuma evidência transacional persistida neste checkpoint"
      );
      setText("cortexCanaryRollback", "commit -- · rollback --");
      setText(
        "cortexAuthorityDetail",
        controlledExecution
          ? "EXECUTE disponível somente por chamada explícita; sem authority persistente."
          : "Cortex permanece em SHADOW; runner herdado é baseline."
      );
    }
    setClassText(
      "baselineRuntimeBadge",
      "runtime " + releaseCommit,
      "badge neutral"
    );
  }
}


function renderBaselineEvolution(context, evolution) {
  const summary = context.result_summary || {};
  const challenger = evolution.challenger || {};
  const fitness = challenger.fitness || {};
  const promotion = evolution.promotion || null;
  const seed = context.seed == null ? "--" : String(context.seed);

  setClassText("generationBadge", "seed " + seed, "badge live");
  setClassText("validationBadge", "BASELINE · isolated", "badge good");
  setText(
    "evolutionKpi",
    seed + " · " + String(context.status || challenger.status || "--")
  );
  setText(
    "evolutionKpiDetail",
    "cold-start independente · sem champion herdado · runtime "
      + String(((context.baseline_release || {}).commit) || "--").slice(0, 8)
  );
  setText("championLabel", "none · cold start");
  setText("championFitness", "G37 retired · no incumbent inherited");
  setText(
    "challengerLabel",
    "seed " + seed + " · " + String(challenger.run_id || summary.run_id || "--")
  );
  setText(
    "challengerFitness",
    challenger.fitness
      ? contenderSummary(challenger)
      : "collecting isolated baseline evidence"
  );

  if (promotion) {
    setClassText(
      "promotionLabel",
      promotion.promoted ? "accepted" : "baseline observation retained",
      promotion.promoted ? "good" : "warn"
    );
    const regressions = Array.isArray(promotion.regressions) ? promotion.regressions : [];
    setText(
      "promotionDetail",
      regressions.length ? regressions.join(" · ") : String(promotion.reason || "--")
    );
  } else {
    setClassText("promotionLabel", String(context.status || "running"), "warn");
    setText("promotionDetail", "independent seed · no cross-seed promotion");
  }

  const gates = [
    ["closed loop", summary.closed_loop_autonomy === true ? "pass" : "fail"],
    [
      "physical processing "
        + formatNumber(Number(summary.physical_processing_coverage || 0) * 100, 0) + "%",
      Number(summary.physical_processing_coverage || 0) >= 0.5 ? "pass" : "fail",
    ],
    [
      "manual logistics "
        + (summary.manual_logistics_calls == null ? "--" : summary.manual_logistics_calls),
      Number(summary.manual_logistics_calls || 0) === 0 ? "pass" : "fail",
    ],
    [
      "logistic science "
        + (summary.logistic_science_output == null
          ? "--"
          : formatNumber(summary.logistic_science_output, 0)),
      Number(summary.logistic_science_output || 0) > 0 ? "pass" : "fail",
    ],
    ["isolation", "pass"],
  ];
  const gateContainer = $("survivalGates");
  if (gateContainer) {
    gateContainer.innerHTML = gates.map(([label, cls]) =>
      '<span class="survival-gate ' + cls + '">' + escapeHtml(label) + '</span>'
    ).join("");
  }

  const historyContainer = $("generationHistory");
  if (historyContainer) {
    historyContainer.innerHTML =
      '<article class="generation-node '
      + (context.status === "completed" ? "rejected" : "evaluating")
      + '"><span>seed ' + escapeHtml(seed) + '</span><strong>'
      + escapeHtml(String(context.status || "--"))
      + '</strong><small>'
      + escapeHtml(
        String(summary.completed_stage_count ?? "--") + " stages · "
        + (summary.bottleneck || "running")
        + " · autonomy "
        + (summary.autonomy_score == null
          ? "--"
          : formatNumber(Number(summary.autonomy_score) * 100, 0) + "%")
      )
      + '</small></article>';
  }
}


function renderEvolution() {
  const researchEvolution = (state.research && state.research.evolution) || {};
  const evolution = Object.assign(
    {},
    state.evolution || {},
    researchEvolution
  );
  const generation = Number(evolution.generation || 0);
  const continuousLoop = evolution.continuous_loop || null;
  const experimentContext = state.experimentContext || {};
  if (experimentContext.kind === "baseline_seed") {
    renderBaselineEvolution(experimentContext, evolution);
    return;
  }
  const operational = cortexOperationalView();
  if (operational.historicalEvidenceMode) {
    const champion = evolution.champion;
    const challenger = evolution.challenger || {};
    const history = Array.isArray(evolution.history) ? evolution.history.slice(-10) : [];
    setText("evolutionArenaLabel", "EVIDÊNCIA HISTÓRICA · EVOLUTION OFF");
    setText("evolutionArenaTitle", "Arena histórica de gerações");
    setText(
      "evolutionArenaDetail",
      "dados preservados para comparação · nenhum challenger/champion está executando ou sendo promovido agora"
    );
    setClassText(
      "generationBadge",
      generation ? "HISTÓRICO · G" + generation : "HISTÓRICO · generation --",
      "badge neutral"
    );
    setClassText("validationBadge", "FROZEN · EVOLUTION OFF", "badge neutral");
    setText("evolutionKpi", generation ? "HISTÓRICO · G" + generation : "HISTÓRICO");
    setText(
      "evolutionKpiDetail",
      "último challenger registrado: " + String(challenger.run_id || "--")
        + " · não está avaliando · source preservado para evidência"
    );
    setText(
      "championLabel",
      champion && champion.run_id
        ? "histórico · G" + String(champion.generation || "?") + " · " + String(champion.run_id)
        : "histórico · nenhum champion selecionado"
    );
    setText("championFitness", champion ? contenderSummary(champion) : "evolution OFF");
    setText(
      "challengerLabel",
      challenger.run_id
        ? "histórico · G" + String(generation || "?") + " · " + String(challenger.run_id)
        : "histórico · no challenger"
    );
    setText(
      "challengerFitness",
      challenger.fitness
        ? contenderSummary(challenger)
        : "último estado persistido · não coletando fitness"
    );
    setClassText("promotionLabel", "frozen", "muted");
    setText(
      "promotionDetail",
      "evolution inactive+disabled · nenhuma promoção em curso · F4-B/F4-C usam SHADOW/offline"
    );
    const gateContainer = $("survivalGates");
    if (gateContainer) {
      gateContainer.innerHTML =
        '<span class="survival-gate proven">HISTÓRICO</span>'
        + '<span class="survival-gate">não controla o mundo</span>';
    }
    const historyContainer = $("generationHistory");
    if (historyContainer) {
      historyContainer.innerHTML = history.length
        ? history.map((row) => {
            const candidate = row.challenger || {};
            const decision = row.decision || {};
            const fitness = candidate.fitness || {};
            const caps = Array.isArray(fitness.capabilities) ? fitness.capabilities.length : 0;
            return '<article class="generation-node rejected">'
              + '<span>G' + escapeHtml(String(row.generation ?? "?")) + '</span>'
              + '<strong>histórico · ' + (decision.promoted ? "promoted" : "rejected") + '</strong>'
              + '<small>' + caps + ' caps · frozen evidence</small>'
              + '</article>';
          }).join("")
        : '<span class="generation-empty">No historical generations recorded.</span>';
    }
    return;
  }
  setClassText(
    "generationBadge",
    generation ? "generation " + generation : "generation --",
    generation ? "badge live" : "badge neutral"
  );

  const champion = evolution.champion;
  const validatedChampion = evolution.validated_champion || null;
  const challenger = evolution.challenger || {};
  const promotion = evolution.promotion;

  const validationMatches = !!(
    validatedChampion
    && champion
    && validatedChampion.run_id
    && champion.run_id
    && validatedChampion.run_id === champion.run_id
  );
  setClassText(
    "validationBadge",
    validationMatches
      ? "OPEN PLAY · validated"
      : champion
        ? "OPEN PLAY · pending"
        : "OPEN PLAY · no champion",
    validationMatches ? "badge good" : "badge neutral"
  );

  const selectionLabel = promotion
    ? (promotion.promoted ? "promoted" : "incumbent retained")
    : String(challenger.status || "evaluating");
  setText(
    "evolutionKpi",
    (generation ? "G" + generation : "G--") + " · " + selectionLabel
  );
  setText(
    "evolutionKpiDetail",
    "Champion "
      + (champion && champion.run_id ? "G" + String(champion.generation || "?") : "--")
      + " · Challenger "
      + String(challenger.run_id || (state.run && state.run.run_id) || "--")
      + (
          continuousLoop
            ? " · loop "
              + String(continuousLoop.status || "unknown")
              + " "
              + Number(continuousLoop.completed_generations || 0)
              + "/" + Number(continuousLoop.requested_generations || 0)
            : ""
        )
  );

  if (champion && typeof champion === "object") {
    setText(
      "championLabel",
      "G" + String(champion.generation ?? "?") + " · "
        + String(champion.run_id || "champion")
    );
    setText("championFitness", contenderSummary(champion));
  } else {
    setText("championLabel", "not selected");
    setText("championFitness", "first surviving generation becomes baseline");
  }

  setText(
    "challengerLabel",
    challenger.run_id
      ? "G" + String(generation || "?") + " · " + String(challenger.run_id)
      : ((state.run && state.run.run_id) || "current run")
  );
  setText(
    "challengerFitness",
    challenger.fitness
      ? contenderSummary(challenger)
      : (
          continuousLoop && continuousLoop.status === "running"
            ? "continuous loop · "
              + Number(continuousLoop.completed_generations || 0)
              + "/" + Number(continuousLoop.requested_generations || 0)
              + " generations closed · "
            : "collecting live fitness evidence · "
        ) + genomeSummary(challenger.configuration)
  );

  if (promotion && typeof promotion === "object") {
    const promoted = !!promotion.promoted;
    setClassText(
      "promotionLabel",
      promoted ? "promoted" : "incumbent retained",
      promoted ? "good" : "warn"
    );
    const regressions = Array.isArray(promotion.regressions)
      ? promotion.regressions
      : [];
    const improvements = Array.isArray(promotion.improvements)
      ? promotion.improvements
      : [];
    setText(
      "promotionDetail",
      regressions.length
        ? regressions.join(" · ")
        : improvements.length
          ? improvements.join(" · ")
          : String(promotion.reason || "selection complete")
    );
  } else {
    setClassText("promotionLabel", "evaluating", "warn");
    setText(
      "promotionDetail",
      "challenger must preserve incumbent capabilities before promotion"
    );
  }

  const achieved = new Set(
    Array.isArray(state.progression && state.progression.achieved)
      ? state.progression.achieved
      : []
  );
  const debt = Array.isArray(state.progression && state.progression.dependency_debt)
    ? state.progression.dependency_debt
    : [];
  const accounting = (
    state.research
    && state.research.resource_accounting
    && state.research.resource_accounting.exogenous_inputs
  ) || {};
  const coalOrigin = String(((accounting.coal || {}).status) || "external");
  const coalExternal = !["retired", "internal", "self_sufficient"].includes(
    coalOrigin
  );

  const gates = [
    {
      label: "iron retention",
      cls: productionRate("iron-ore") > 0 || productionRate("iron-plate") > 0
        ? "live"
        : achieved.has("iron_backbone") ? "warn" : "",
    },
    {
      label: "coal independence",
      cls: achieved.has("coal_mining") && !coalExternal
        ? "pass"
        : coalExternal ? "fail" : "",
    },
    {
      label: "copper retention",
      cls: productionRate("copper-ore") > 0
        || productionRate("copper-plate") > 0
        ? "live"
        : achieved.has("copper_mining") ? "warn" : "",
    },
    {
      label: "power frontier",
      cls: achieved.has("steam_power") ? "pass" : "",
    },
    {
      label: "science frontier",
      cls: achieved.has("automation_science") ? "pass" : "",
    },
    {
      label: "dependency debt " + debt.length,
      cls: debt.length ? "fail" : "pass",
    },
    {
      label: "retention ≥ "
        + formatNumber(Number(evolution.retention_ratio || 0.80) * 100, 0)
        + "%",
      cls: "proven",
    },
  ];
  const gateContainer = $("survivalGates");
  if (gateContainer) {
    gateContainer.innerHTML = gates.map((gate) =>
      '<span class="survival-gate ' + gate.cls + '">'
        + escapeHtml(gate.label)
        + '</span>'
    ).join("");
  }

  const historyContainer = $("generationHistory");
  if (historyContainer) {
    const history = Array.isArray(evolution.history)
      ? evolution.history.slice(-10)
      : [];
    const currentRunId = challenger.run_id || null;
    const historyRows = history.map((row) => {
      const candidate = row.challenger || {};
      const decision = row.decision || {};
      const fitness = candidate.fitness || {};
      const caps = Array.isArray(fitness.capabilities)
        ? fitness.capabilities.length
        : 0;
      const rate = Number(fitness.total_rate_per_s || 0);
      const deps = Number(fitness.external_dependencies || 0);
      const failures = Number(fitness.failures || 0);
      const autonomy = fitness.autonomy_score;
      const manual = fitness.manual_logistics_calls;
      const status = decision.promoted ? "promoted" : "rejected";
      const cls = decision.promoted ? "promoted" : "rejected";
      return '<article class="generation-node ' + cls + '">'
        + '<span>G' + escapeHtml(String(row.generation ?? "?")) + '</span>'
        + '<strong>' + status + '</strong>'
        + '<small>' + caps + ' caps · '
        + formatNumber(rate, 2) + '/s · '
        + deps + ' ext · ' + failures + ' fail'
        + (autonomy !== null && autonomy !== undefined
          ? ' · A ' + formatNumber(Number(autonomy) * 100, 0) + '%'
          : ' · A --')
        + (manual !== null && manual !== undefined
          ? ' · M ' + String(manual)
          : ' · M --')
        + '</small>'
        + '</article>';
    });
    if (
      currentRunId
      && !history.some((row) =>
        row.challenger && row.challenger.run_id === currentRunId
      )
    ) {
      historyRows.push(
        '<article class="generation-node evaluating">'
          + '<span>G' + escapeHtml(String(generation || "?")) + '</span>'
          + '<strong>evaluating</strong>'
          + '<small>' + escapeHtml(genomeSummary(challenger.configuration)) + '</small>'
          + '</article>'
      );
    }
    historyContainer.innerHTML = historyRows.length
      ? historyRows.join("")
      : '<span class="generation-empty">No completed generations yet.</span>';
  }
}


function signedPercent(value) {
  if (!Number.isFinite(value)) return "--";
  const sign = value > 0 ? "+" : "";
  return sign + formatNumber(value, 1) + "%";
}

function renderResearchCockpit() {
  const research = state.research || {};
  const evolution = Object.assign(
    {},
    state.evolution || {},
    research.evolution || {}
  );
  const progression = state.progression || {};
  const datasets = state.datasets || {};
  const curriculum = Array.isArray(research.curriculum)
    ? research.curriculum
    : [];
  const generation = Number(evolution.generation || 0);
  const champion = evolution.champion || null;
  const validated = evolution.validated_champion || null;
  const promotion = evolution.promotion || null;

  const completedStages = curriculum.filter((stage) =>
    ["completed", "success"].includes(String(stage.status || ""))
  ).length;
  const activeIndex = curriculum.findIndex((stage) =>
    ["running", "learning", "validating"].includes(String(stage.status || ""))
  );
  const failedStage = curriculum.find((stage) =>
    String(stage.status || "") === "failed"
  );
  const activeStage = activeIndex >= 0 ? curriculum[activeIndex] : null;
  const totalStages = curriculum.length;

  const experimentContext = state.experimentContext || {};
  const operational = cortexOperationalView();
  const historical = operational.historicalEvidenceMode;
  const baselineSeed = experimentContext.kind === "baseline_seed"
    ? experimentContext.seed
    : null;
  setText(
    "generationHealthTitle",
    historical
      ? "HISTÓRICO · " + (generation ? "G" + generation + " · " : "")
        + (activeStage ? String(activeStage.name) : String(research.status || "idle"))
      : baselineSeed !== null && baselineSeed !== undefined
        ? "seed " + String(baselineSeed) + " · "
          + (activeStage ? String(activeStage.name) : String(research.status || "idle"))
        : generation
          ? "G" + generation + " · "
            + (activeStage ? String(activeStage.name) : String(research.status || "idle"))
          : "G-- · waiting"
  );
  const running = !historical && ["starting", "running", "learning", "validating"].includes(
    String(research.status || "")
  );
  setClassText(
    "generationHealthBadge",
    historical
      ? "FROZEN · NOT RUNNING"
      : running ? "live challenger" : promotion
        ? (promotion.promoted ? "promoted" : "rejected")
        : String(research.status || "--"),
    historical
      ? "badge neutral"
      : running
        ? "badge live"
        : promotion && promotion.promoted
          ? "badge good"
          : promotion
            ? "badge warn"
            : "badge neutral"
  );
  setText(
    "generationStageText",
    historical
      ? "last recorded · " + (activeStage ? String(activeStage.name) : String(research.stage || "--"))
      : activeStage
        ? String(activeStage.name)
        : failedStage
          ? "failed · " + String(failedStage.name)
          : "generation closed"
  );
  setText(
    "generationStageProgress",
    completedStages + " / " + totalStages
  );
  const progress = totalStages
    ? Math.min(100, Math.max(0, (completedStages / totalStages) * 100))
    : Number(research.progress || 0) * 100;
  const stageBar = $("generationStageBar");
  if (stageBar) stageBar.style.width = progress + "%";

  setText(
    "generationChampion",
    baselineSeed !== null && baselineSeed !== undefined
      ? "none · cold start"
      : champion
        ? "G" + String(champion.generation || "?")
        : "none"
  );

  const currentRoute = Number((research.metrics || {}).logistics_route_cost);
  const championRoute = Number(
    champion && champion.fitness && champion.fitness.route_cost
  );
  if (
    Number.isFinite(currentRoute)
    && Number.isFinite(championRoute)
    && championRoute > 0
  ) {
    const delta = ((currentRoute - championRoute) / championRoute) * 100;
    setClassText(
      "generationRouteDelta",
      signedPercent(delta),
      delta < -0.05 ? "good" : delta > 0.05 ? "warn" : "muted"
    );
  } else {
    setClassText("generationRouteDelta", "--", "muted");
  }

  const validatedCaps = Array.isArray(progression.validated_achieved)
    ? progression.validated_achieved
    : Array.isArray(progression.achieved)
      ? progression.achieved
      : [];
  setText("generationCapabilities", String(validatedCaps.length));
  setClassText(
    "generationOpenPlay",
    validated
      ? "validated"
      : champion
        ? "pending"
        : "no champion",
    validated ? "good" : champion ? "warn" : "muted"
  );

  const healthDetail = historical
    ? "evidência histórica congelada · nenhum processo G" + String(generation || "?")
      + " está ativo · current Cortex work = F4-C paired harness validation"
    : running
      ? (baselineSeed !== null && baselineSeed !== undefined
        ? "seed " + String(baselineSeed) + " is collecting isolated evidence · "
        : "G" + String(generation || "?") + " is collecting evidence · ")
      + (activeStage
        ? "current gate: " + String(activeStage.name)
        : "initializing")
      + (baselineSeed === null && champion
        ? " · incumbent G" + String(champion.generation || "?")
        : "")
    : promotion
      ? String(promotion.reason || "selection complete")
      : "collecting generation evidence";
  setText("generationHealthDetail", healthDetail);

  const recurrent = datasets.recurrent_world_model || null;
  const spatial = datasets.spatial_policy || null;
  const knowledge = state.knowledge || {};

  let acceptedModels = 0;
  if (recurrent) {
    const holdout = recurrent.generation_holdout || {};
    const folds = Number(holdout.valid_folds || 0);
    const wins = Number(holdout.wins || 0);
    const winRate = Number(holdout.win_rate || 0);
    const usable = !!recurrent.usable;
    if (usable) acceptedModels += 1;
    setText(
      "worldModelArenaTitle",
      (recurrent.active_model
        ? String(recurrent.active_model)
        : "GRU world model")
    );
    setText(
      "worldModelArenaDetail",
      Number(recurrent.sample_count || 0) + " temporal samples"
        + (folds ? " · holdout " + wins + "/" + folds : "")
        + (folds ? " · win " + formatNumber(winRate * 100, 0) + "%" : "")
    );
    setClassText(
      "worldModelArenaStatus",
      usable ? "accepted" : "advisory",
      usable ? "good" : "warn"
    );
  } else {
    setText("worldModelArenaDetail", "no trained recurrent model");
    setClassText("worldModelArenaStatus", "collecting", "muted");
  }

  if (spatial) {
    const controlEligible = !!spatial.control_eligible;
    const usable = !!spatial.usable;
    const attention = (
      spatial.candidates
      && spatial.candidates.attention
    ) || null;
    const active = String(spatial.active_model || "none");
    const activeCost = Number(spatial.mean_cost_ratio_to_astar || 0);
    const attentionCost = Number(
      attention && attention.mean_cost_ratio_to_astar
    );
    if (controlEligible) acceptedModels += 1;
    setText(
      "spatialModelArenaTitle",
      "Spatial " + active
        + (attention ? " + attention challenger" : "")
    );
    setText(
      "spatialModelArenaDetail",
      "rollout " + formatNumber(
        Number(spatial.rollout_success_rate || 0) * 100,
        0
      ) + "%"
        + " · active cost " + formatNumber(activeCost, 3) + "× A*"
        + (Number.isFinite(attentionCost) && attentionCost > 0
          ? " · attention " + formatNumber(attentionCost, 3) + "×"
          : "")
    );
    setClassText(
      "spatialModelArenaStatus",
      controlEligible
        ? "control"
        : usable
          ? "proposal"
          : "advisory",
      controlEligible ? "good" : usable ? "warn" : "muted"
    );
  } else {
    setText("spatialModelArenaDetail", "no trained spatial policy");
    setClassText("spatialModelArenaStatus", "collecting", "muted");
  }

  const verified = Number(knowledge.verified_count || 0);
  const fallback = Number(knowledge.fallback_count || 0);
  const audited = verified + fallback;
  const verifiedRatio = audited
    ? verified / audited
    : Number(knowledge.verified_ratio || 0);
  if (verified > 0) acceptedModels += 1;
  setText(
    "knowledgeVerifierDetail",
    audited
      ? verified + " verified · " + fallback + " deterministic fallback"
      : Number(knowledge.count || 0) + " legacy/unclassified lessons"
  );
  setClassText(
    "knowledgeVerifierStatus",
    audited
      ? formatNumber(verifiedRatio * 100, 0) + "% verified"
      : "legacy",
    audited && verifiedRatio >= 0.70
      ? "good"
      : audited
        ? "warn"
        : "muted"
  );
  setClassText(
    "modelArenaBadge",
    (historical ? "HISTÓRICO · " : "") + acceptedModels + "/3 passed",
    historical ? "badge neutral" : acceptedModels >= 2 ? "badge good" : "badge warn"
  );

  const history = Array.isArray(evolution.history)
    ? evolution.history
    : [];
  const lastClosed = history.length ? history[history.length - 1] : null;
  if (lastClosed) {
    const decision = lastClosed.decision || {};
    const closedGeneration = lastClosed.generation ?? "?";
    const promoted = !!decision.promoted;
    setText(
      "lastGenerationDecision",
      "G" + String(closedGeneration) + " · "
        + (promoted ? "promoted" : "rejected")
    );
    setClassText(
      "researchSummaryBadge",
      promoted ? "last promoted" : "last rejected",
      promoted ? "badge good" : "badge warn"
    );
    setText(
      "researchSummaryTitle",
      historical
        ? "Historical last selection: G" + String(closedGeneration)
        : running
          ? "G" + String(generation || "?") + " is challenging G"
            + String(champion && champion.generation || "?")
          : "Last selection: G" + String(closedGeneration)
    );

    const improvements = Array.isArray(decision.improvements)
      ? decision.improvements
      : [];
    const regressions = Array.isArray(decision.regressions)
      ? decision.regressions
      : [];
    const deltaList = $("generationDeltaList");
    if (deltaList) {
      const chips = [];
      for (const item of improvements.slice(0, 3)) {
        chips.push(
          '<span class="delta-chip up">↑ '
          + escapeHtml(item)
          + '</span>'
        );
      }
      for (const item of regressions.slice(0, 3)) {
        chips.push(
          '<span class="delta-chip down">↓ '
          + escapeHtml(item)
          + '</span>'
        );
      }
      deltaList.innerHTML = chips.length
        ? chips.join("")
        : '<span class="delta-chip info">no comparable deltas</span>';
    }
    setText(
      "researchSummaryDetail",
      String(decision.reason || "selection complete")
    );
  } else {
    setText("lastGenerationDecision", "none");
    setClassText("researchSummaryBadge", "collecting", "badge neutral");
    setText("researchSummaryTitle", "First selection not closed yet");
    const deltaList = $("generationDeltaList");
    if (deltaList) deltaList.innerHTML = "";
  }

  setText(
    "currentBottleneck",
    historical
      ? "historical last stage · " + (activeStage ? String(activeStage.name) : String(research.stage || "--"))
      : activeStage
        ? String(activeStage.name)
        : failedStage
          ? String(failedStage.name)
          : "no active failure"
  );
  const nextGoal = progression.next_goal;
  setText(
    "summaryNextFrontier",
    historical
      ? "F4-C · freeze diverse held-out memory-ablation protocol"
      : nextGoal && nextGoal.label
        ? String(nextGoal.label)
        : String(research.next_action || "--")
  );
}


function formatDuration(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "--";
  if (value < 60) return formatNumber(value, 0) + " s";
  const minutes = Math.floor(value / 60);
  const remainder = Math.round(value % 60);
  return minutes + "m " + remainder + "s";
}

function generationFitnessRows() {
  const evolution = Object.assign(
    {},
    state.evolution || {},
    (state.research && state.research.evolution) || {}
  );
  const history = Array.isArray(evolution.history) ? evolution.history : [];
  return history
    .map((row) => {
      const fitness = (row.challenger && row.challenger.fitness) || {};
      const autonomyRaw = fitness.autonomy_score;
      const manualRaw = fitness.manual_logistics_calls;
      return {
        generation: Number(row.generation),
        route: Number(fitness.route_cost),
        capabilities: Array.isArray(fitness.capabilities)
          ? fitness.capabilities.length
          : 0,
        autonomy: autonomyRaw === null || autonomyRaw === undefined
          ? Number.NaN
          : Number(autonomyRaw) * 100,
        manual: manualRaw === null || manualRaw === undefined
          ? Number.NaN
          : Number(manualRaw),
        failures: Number(fitness.failures || 0),
        promoted: !!(row.decision && row.decision.promoted),
      };
    })
    .filter((row) => Number.isFinite(row.generation));
}

function drawGenerationTrends() {
  const canvas = $("generationTrendCanvas");
  if (!canvas) return;
  const { ctx, width, height } = prepareCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "rgba(255,255,255,.015)";
  ctx.fillRect(0, 0, width, height);

  const rows = generationFitnessRows().slice(-12);
  const evolution = state.evolution || {};
  const report = evolution.latest_report || null;
  setText(
    "generationDuration",
    report && Number.isFinite(Number(report.duration_s))
      ? "G" + String(report.generation || "?") + " · "
        + formatDuration(report.duration_s)
      : "duration --"
  );

  if (rows.length < 2) {
    ctx.fillStyle = "#78838d";
    ctx.font = '10px "SFMono-Regular", Consolas, monospace';
    ctx.fillText("Need at least two closed generations.", 14, 28);
    return;
  }

  const left = 42;
  const right = 12;
  const top = 14;
  const bottom = 22;
  const plotWidth = Math.max(1, width - left - right);
  const bandGap = 8;
  const bandHeight = (height - top - bottom - bandGap * 3) / 4;
  const bands = [
    {
      label: "route",
      values: rows.map((row) => row.route),
      color: "#f0a33a",
      better: "lower",
    },
    {
      label: "auton%",
      values: rows.map((row) => row.autonomy),
      color: "#5dd589",
      better: "higher",
    },
    {
      label: "manual",
      values: rows.map((row) => row.manual),
      color: "#6ab5f7",
      better: "lower",
    },
    {
      label: "fails",
      values: rows.map((row) => row.failures),
      color: "#ee6c68",
      better: "lower",
    },
  ];

  ctx.font = '8px "SFMono-Regular", Consolas, monospace';
  bands.forEach((band, bandIndex) => {
    const y0 = top + bandIndex * (bandHeight + bandGap);
    const finite = band.values.filter(Number.isFinite);
    if (!finite.length) {
      ctx.fillStyle = "#78838d";
      ctx.fillText(band.label, 8, y0 + 10);
      ctx.fillText("n/a", 8, y0 + 22);
      return;
    }
    const min = Math.min(...finite);
    const max = Math.max(...finite);
    const span = Math.max(1e-9, max - min);
    ctx.strokeStyle = "rgba(255,255,255,.07)";
    ctx.beginPath();
    ctx.moveTo(left, y0 + bandHeight);
    ctx.lineTo(width - right, y0 + bandHeight);
    ctx.stroke();
    ctx.fillStyle = "#78838d";
    ctx.fillText(band.label, 8, y0 + 10);
    ctx.fillText(formatNumber(max, 2), 8, y0 + 22);
    ctx.fillText(formatNumber(min, 2), 8, y0 + bandHeight - 2);

    ctx.strokeStyle = band.color;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    band.values.forEach((value, index) => {
      const x = left + (index / Math.max(1, rows.length - 1)) * plotWidth;
      const normalized = Number.isFinite(value) ? (value - min) / span : 0;
      const y = y0 + bandHeight - normalized * (bandHeight - 8) - 4;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    rows.forEach((row, index) => {
      if (!row.promoted) return;
      const value = band.values[index];
      const x = left + (index / Math.max(1, rows.length - 1)) * plotWidth;
      const normalized = Number.isFinite(value) ? (value - min) / span : 0;
      const y = y0 + bandHeight - normalized * (bandHeight - 8) - 4;
      ctx.fillStyle = band.color;
      ctx.beginPath();
      ctx.arc(x, y, 2.8, 0, Math.PI * 2);
      ctx.fill();
    });
  });

  ctx.fillStyle = "#78838d";
  rows.forEach((row, index) => {
    if (index % Math.max(1, Math.ceil(rows.length / 6)) !== 0
        && index !== rows.length - 1) return;
    const x = left + (index / Math.max(1, rows.length - 1)) * plotWidth;
    ctx.fillText("G" + row.generation, x - 6, height - 6);
  });
}

function renderGameKnowledgeGraph() {
  const summary = state.gameGraphSummary || {};
  const graph = summary.frontier_dependency_graph || null;
  const canvas = $("gameKnowledgeCanvas");
  if (!canvas) return;
  const { ctx, width, height } = prepareCanvas(canvas);
  ctx.clearRect(0, 0, width, height);

  const counts = [
    ["receitas", summary.recipe_count || 0],
    ["produtos", summary.product_count || 0],
    ["tecnologias", summary.technology_count || 0],
    ["máquinas", summary.machine_count || 0],
    ["receitas ativas", summary.enabled_recipe_count || 0],
    ["pesquisadas", summary.researched_technology_count || 0],
  ];
  const metrics = $("gameKnowledgeMetrics");
  if (metrics) {
    metrics.innerHTML = counts.map(([label, value]) =>
      '<span><b>' + escapeHtml(value) + '</b>' + escapeHtml(label) + '</span>'
    ).join("");
  }

  setClassText(
    "gameKnowledgeBadge",
    summary.connected ? "runtime canônico" : "indisponível",
    summary.connected ? "badge good" : "badge warn"
  );
  setText(
    "gameKnowledgeTitle",
    summary.frontier_target_item
      ? "Dependências · " + humanizePrototype(summary.frontier_target_item)
      : "Receitas e tecnologias do runtime"
  );

  if (!graph || !Array.isArray(graph.nodes) || !graph.nodes.length) {
    ctx.fillStyle = "#78838d";
    ctx.font = '10px "SFMono-Regular", Consolas, monospace';
    ctx.fillText(
      summary.connected
        ? "Fronteira atual sem alvo produtivo mapeado."
        : "Aguardando snapshot dos prototypes do Factorio.",
      14,
      28
    );
    setText(
      "gameKnowledgeDetail",
      summary.connected
        ? "O catálogo canônico está carregado; o grafo muda com a fronteira de engenharia."
        : String(summary.error || "runtime graph unavailable")
    );
    return;
  }

  const nodes = graph.nodes;
  const edges = Array.isArray(graph.edges) ? graph.edges : [];
  const byId = new Map(nodes.map((node) => [String(node.id), node]));
  const target = String(graph.target || "");
  const depths = new Map([[target, 0]]);
  let changed = true;
  for (let pass = 0; pass < nodes.length + 2 && changed; pass += 1) {
    changed = false;
    for (const edge of edges) {
      const td = depths.get(String(edge.target));
      if (td === undefined) continue;
      const source = String(edge.source);
      const next = td + 1;
      if (depths.get(source) === undefined || next > depths.get(source)) {
        depths.set(source, next);
        changed = true;
      }
    }
  }
  const maxDepth = Math.max(0, ...Array.from(depths.values()));
  const groups = new Map();
  for (const node of nodes) {
    const depth = depths.get(String(node.id)) ?? maxDepth + 1;
    if (!groups.has(depth)) groups.set(depth, []);
    groups.get(depth).push(node);
  }

  const padX = 36;
  const padY = 24;
  const positions = new Map();
  for (const [depth, rows] of groups.entries()) {
    rows.sort((a, b) => String(a.id).localeCompare(String(b.id)));
    const x = maxDepth
      ? padX + ((maxDepth - Math.min(depth, maxDepth)) / maxDepth)
        * (width - padX * 2)
      : width / 2;
    rows.forEach((node, index) => {
      const y = rows.length === 1
        ? height / 2
        : padY + (index / Math.max(1, rows.length - 1)) * (height - padY * 2);
      positions.set(String(node.id), { x, y });
    });
  }

  ctx.lineWidth = 1.4;
  ctx.strokeStyle = "rgba(240,163,58,.55)";
  for (const edge of edges) {
    const a = positions.get(String(edge.source));
    const b = positions.get(String(edge.target));
    if (!a || !b) continue;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }

  for (const node of nodes) {
    const pos = positions.get(String(node.id));
    if (!pos) continue;
    const raw = node.kind === "raw_or_unresolved";
    ctx.fillStyle = raw ? "#6ab5f7" : node.enabled ? "#64d98b" : "#f0a33a";
    ctx.beginPath();
    ctx.arc(pos.x, pos.y, 5, 0, Math.PI * 2);
    ctx.fill();

    const label = humanizePrototype(node.id);
    ctx.fillStyle = "#cbd1d5";
    ctx.font = '8px "SFMono-Regular", Consolas, monospace';
    ctx.textAlign = pos.x > width * 0.74 ? "right" : "left";
    ctx.fillText(
      label.length > 25 ? label.slice(0, 24) + "…" : label,
      pos.x + (ctx.textAlign === "left" ? 8 : -8),
      pos.y + 3
    );
  }
  ctx.textAlign = "left";

  const unlocks = nodes.flatMap((node) =>
    Array.isArray(node.unlock_technologies) ? node.unlock_technologies : []
  );
  setText(
    "gameKnowledgeDetail",
    nodes.length + " nós · " + edges.length + " dependências"
      + (unlocks.length
        ? " · tecnologias relevantes: " + Array.from(new Set(unlocks)).join(", ")
        : "")
      + " · fatos do jogo, não inferências aprendidas."
  );
}


function renderFactoryTopology() {
  const graph = state.factoryGraph || {};
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph.edges) ? graph.edges : [];
  const metrics = graph.metrics || {};
  const canvas = $("factoryTopologyCanvas");
  if (!canvas) return;
  const { ctx, width, height } = prepareCanvas(canvas);
  ctx.clearRect(0, 0, width, height);

  if (!nodes.length) {
    ctx.fillStyle = "#78838d";
    ctx.font = '10px "SFMono-Regular", Consolas, monospace';
    ctx.fillText("Nenhuma entidade física observada.", 14, 28);
    setClassText("topologyBadge", "sem grafo", "badge neutral");
    setText("topologyDetail", "Aguardando entidades físicas no mundo.");
    const target = $("topologyMetrics");
    if (target) target.innerHTML = "";
    return;
  }

  const xs = nodes.map((node) => Number(node.x)).filter(Number.isFinite);
  const ys = nodes.map((node) => Number(node.y)).filter(Number.isFinite);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const pad = 30;
  const spanX = Math.max(4, maxX - minX);
  const spanY = Math.max(4, maxY - minY);
  const scale = Math.min(
    (width - pad * 2) / spanX,
    (height - pad * 2) / spanY
  );
  const project = (node) => ({
    x: pad + (Number(node.x) - minX) * scale
      + (width - pad * 2 - spanX * scale) / 2,
    y: pad + (Number(node.y) - minY) * scale
      + (height - pad * 2 - spanY * scale) / 2,
  });
  const byId = new Map(nodes.map((node) => [String(node.id), node]));

  const relationColors = {
    belt_flow: "#f0a33a",
    pickup: "#dfb65b",
    drop: "#f1c76d",
    material_output: "#6ab5f7",
    power_backbone: "#64d98b",
    power_supply: "#64d98b",
    fluid_link: "#63d7d4",
  };
  for (const edge of edges) {
    const source = byId.get(String(edge.source));
    const target = byId.get(String(edge.target));
    if (!source || !target) continue;
    const a = project(source);
    const b = project(target);
    ctx.strokeStyle = relationColors[edge.relation] || "#6f7980";
    ctx.globalAlpha = String(edge.relation).startsWith("power_") ? 0.38 : 0.78;
    ctx.lineWidth = edge.relation === "belt_flow" ? 2.1 : 1.4;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  const categoryColors = {
    extraction: "#6ab5f7",
    transport: "#f0a33a",
    transfer: "#e3be69",
    processing: "#b993f6",
    buffer: "#aab3b9",
    power: "#64d98b",
    fluid_transport: "#63d7d4",
    energy: "#ef6b73",
    research: "#63d7d4",
    agent: "#ffffff",
    other: "#69737c",
  };
  for (const node of nodes) {
    const point = project(node);
    ctx.fillStyle = categoryColors[node.category] || categoryColors.other;
    ctx.beginPath();
    ctx.arc(
      point.x,
      point.y,
      node.category === "transport" ? 2.4 : 4.2,
      0,
      Math.PI * 2
    );
    ctx.fill();
  }

  const producers = Number(metrics.producer_count || 0);
  const processed = Number(metrics.producers_reaching_processor || 0);
  const buffered = Number(metrics.producers_reaching_buffer || 0);
  const coverage = Number(metrics.physical_processing_coverage || 0);
  const fuelStarved = Number(metrics.fuel_starved_entities || 0);
  const powerStarved = Number(metrics.power_starved_entities || 0);
  const unhealthy = fuelStarved > 0
    || powerStarved > 0
    || (producers > 0 && coverage < 0.5);
  setClassText(
    "topologyBadge",
    producers
      ? formatNumber(coverage * 100, 0) + "% processado"
        + (fuelStarved ? " · " + fuelStarved + " sem combustível" : "")
      : "sem produtores",
    unhealthy
      ? "badge bad"
      : producers && coverage >= 0.8
        ? "badge good"
        : producers
          ? "badge warn"
          : "badge neutral"
  );
  const target = $("topologyMetrics");
  if (target) {
    const rows = [
      ["nós", metrics.node_count || 0],
      ["arestas", metrics.edge_count || 0],
      ["produtores", producers],
      ["→ processo", processed],
      ["→ buffer", buffered],
      ["isolados", metrics.isolated_producers || 0],
      ["sem combustível", fuelStarved],
      ["sem energia", powerStarved],
      ["vapor", metrics.steam_path_live ? "ok" : "--"],
    ];
    target.innerHTML = rows.map(([label, value]) =>
      '<span><b>' + escapeHtml(value) + '</b>' + escapeHtml(label) + '</span>'
    ).join("");
  }
  setText(
    "topologyDetail",
    producers
      ? processed + "/" + producers
        + " produtores possuem caminho físico inferido até processamento; "
        + buffered + "/" + producers + " chegam a buffers"
        + (fuelStarved ? " · " + fuelStarved + " entidades sem combustível" : "")
        + (powerStarved ? " · " + powerStarved + " entidades sem energia" : "")
        + "."
      : "A topologia ainda não contém cadeia de extração."
  );
}


function blockerLabel(blocker) {
  const technologies = Array.isArray(blocker.missing_technologies)
    ? blocker.missing_technologies
    : [];
  return String(blocker.item || "?")
    + " · " + String(blocker.kind || "blocked")
    + (technologies.length ? " · " + technologies.join(" > ") : "");
}

function renderValidatedRecord(record) {
  const el = $("productionDagRecord");
  if (!el) return;
  if (!record) {
    el.textContent = "";
    return;
  }
  // The record is evidence from a run that already finished, not the plan
  // being served. Saying which is which is the reason it is on screen.
  const dag = record.record && record.record.dag ? record.record.dag : {};
  const stamp = record.recorded_at || record.artifact_updated_at;
  const target = dag.target_item
    ? String(dag.target_item)
      + " · " + formatNumber(dag.target_rate_per_s, 3) + "/s"
    : "alvo não declarado";
  el.textContent = "registro de execução validada (não é o plano servido): "
    + String(record.plan_id || "?")
    + " · " + target
    + " · artefato " + String(record.artifact || "?")
    + " · gravado " + (stamp ? String(stamp) : "não medido")
    + (record.run_id ? " · run " + String(record.run_id) : "");
}

function renderProductionDag() {
  const plan = state.productionPlan || {};
  const dag = plan.dag || null;
  const dependency = plan.plan || null;
  const blockers = dependency && Array.isArray(dependency.blockers)
    ? dependency.blockers
    : [];
  renderValidatedRecord(plan.validated_plan || null);
  if (!dag) {
    setText(
      "productionDagTarget",
      plan.goal_id ? String(plan.goal_id) : "Waiting for frontier"
    );
    setText(
      "productionDagSource",
      [plan.source, plan.catalog_source].filter(Boolean).join(" · ") || "--"
    );
    const nodes = $("productionDagNodes");
    if (nodes) {
      // A planner that refused the target says why. Rendering the generic
      // placeholder instead would read as "no plan needed".
      nodes.innerHTML = '<span class="placeholder-row">'
        + escapeHtml(
          plan.plan_error
            ? "planner refused the target: " + plan.plan_error
            : "No craftable DAG for this frontier."
        )
        + '</span>';
    }
    const raw = $("productionDagRaw");
    if (raw) raw.innerHTML = "";
    return;
  }

  setText(
    "productionDagTarget",
    String(plan.goal_label || dag.target_item || "production target")
      + " · " + formatNumber(dag.target_rate_per_s, 3) + "/s"
  );
  setText(
    "productionDagSource",
    [
      String(plan.source || "planner"),
      String(plan.catalog_source || "static catalog"),
      "Factorio " + String(plan.factorio_data_version || ""),
      dependency
        ? (dependency.feasible
          ? "exequivel"
          : blockers.length + " bloqueio(s)")
        : null,
      plan.validated_plan ? "registro validado ao lado" : null,
    ].filter(Boolean).join(" · ")
  );
  const nodes = Array.isArray(dag.nodes) ? dag.nodes : [];
  const container = $("productionDagNodes");
  if (container) {
    container.innerHTML = nodes.map((node) => {
      const ingredients = Array.isArray(node.ingredients)
        ? node.ingredients.map((item) =>
            formatNumber(item.count, 2) + " " + item.item
          ).join(" + ")
        : "";
      const technologies = Array.isArray(node.blocked_by_technologies)
        ? node.blocked_by_technologies
        : [];
      // craftable_now === null means the plan never had to make this item.
      // That is not the same as a recipe found runnable, so it says neither.
      const gate = node.craftable_now === false
        ? "bloqueado: " + (technologies.join(" > ") || "receita desabilitada")
        : node.craftable_now === true
          ? "receita liberada"
          : "";
      const machine = node.machine
        ? String(node.machine)
          + (node.machines === null || node.machines === undefined
            ? " · dimensionamento indeterminado"
            : " ×" + formatNumber(node.machines, 0))
        : "";
      return '<article class="dag-node'
        + (node.craftable_now === false ? ' blocked' : '') + '">'
        + '<strong>' + escapeHtml(node.item) + '</strong>'
        + '<small>' + formatNumber(node.target_rate_per_s, 3) + '/s'
        + ' · min ' + formatNumber(node.minimum_machines_at_speed_1, 0)
        + ' machine(s)</small>'
        + '<small>' + escapeHtml(ingredients || "raw input") + '</small>'
        + (machine ? '<small>' + escapeHtml(machine) + '</small>' : '')
        + (gate ? '<small>' + escapeHtml(gate) + '</small>' : '')
        + '</article>';
    }).join("");
  }
  const rawRequirements = dag.raw_requirements_per_s || {};
  const raw = $("productionDagRaw");
  if (raw) {
    raw.innerHTML = Object.entries(rawRequirements).map(([item, rate]) =>
      '<span class="raw-requirement">'
        + escapeHtml(item) + ' · ' + formatNumber(rate, 3) + '/s'
        + '</span>'
    ).join("")
    + blockers.map((blocker) =>
      '<span class="raw-requirement blocked">'
        + escapeHtml(blockerLabel(blocker))
        + '</span>'
    ).join("");
  }
}

function renderAutonomy() {
  const autonomy = state.autonomy || {};
  const level = String(autonomy.level || "unknown");
  const closedLoop = !!autonomy.closed_loop;
  const levelClass = closedLoop
    ? "badge good"
    : level === "semi_autonomous"
      ? "badge warn"
      : "badge bad";
  setClassText(
    "autonomyLevel",
    level.replaceAll("_", " "),
    levelClass
  );

  const score = Number(autonomy.score);
  setText(
    "autonomyScore",
    Number.isFinite(score) ? formatNumber(score * 100, 0) + "%" : "--"
  );
  setText(
    "autonomySoak",
    formatDuration(Number(autonomy.soak_runtime_s || 0))
  );

  const instrumented = !!autonomy.intervention_instrumented;
  const manual = autonomy.manual_logistics_calls;
  setText(
    "autonomyManual",
    instrumented && manual !== null && manual !== undefined
      ? String(manual)
      : "unknown"
  );
  setText(
    "autonomyManualDetail",
    instrumented
      ? "committed harvest + item-transfer calls"
      : "legacy run · executor counters unavailable"
  );

  const noFuel = Number(autonomy.no_fuel_entities || 0);
  const noPower = Number(autonomy.no_power_entities || 0);
  setText("autonomyStarved", noFuel + " / " + noPower);
  setText(
    "autonomySource",
    String(autonomy.measurement_source || "physical evidence unavailable")
  );

  const topology = autonomy.topology || {};
  const gates = [
    ["Fuel distribution", "fuel_distribution"],
    ["Electric distribution", "electric_distribution"],
    ["Smelting logistics", "smelting_distribution"],
    ["Plate output buffers", "smelting_output_distribution"],
    ["Material producing", "producing_material"],
    ["Zero manual logistics", "zero_manual_logistics"],
    ["Fuel healthy", "healthy_fuel"],
    ["Power healthy", "healthy_power"],
    ["60s+ soak", "soak_long_enough"],
  ];
  const container = $("autonomyGates");
  if (container) {
    container.innerHTML = gates.map(([label, key]) => {
      let value = !!topology[key];
      let status = value ? "pass" : "fail";
      let text = value ? "pass" : "fail";
      if (key === "zero_manual_logistics" && !instrumented) {
        status = "unknown";
        text = "unknown";
      }
      return '<div class="autonomy-gate ' + status + '">'
        + '<span>' + escapeHtml(label) + '</span>'
        + '<b>' + text + '</b>'
        + '</div>';
    }).join("");
  }
}

function renderWipHealth() {
  const metrics = (state.research && state.research.metrics) || {};
  const entities = Array.isArray(state.world && state.world.entities)
    ? state.world.entities
    : [];
  const statusCounts = {};
  for (const entity of entities) {
    const key = String(entity.status || "unknown");
    statusCounts[key] = (statusCounts[key] || 0) + 1;
  }
  const coalReserve = Number(
    metrics.survival_coal_reserve
      ?? metrics.coal_endogenous_stockpile
      ?? 0
  );
  const coalSafety = Number(metrics.coal_safety_stock_target ?? 0);
  const ironRate = productionRate("iron-ore") + productionRate("iron-plate");
  const copperRate = productionRate("copper-ore") + productionRate("copper-plate");
  const circuitCounterexample = metrics.electronic_circuit_counterexample || {};
  const ironBuffer = Number(
    circuitCounterexample.circuit_iron_ore
      ?? circuitCounterexample.circuit_iron
      ?? 0
  );
  const copperBuffer = Number(
    circuitCounterexample.circuit_copper_ore
      ?? circuitCounterexample.circuit_copper_buffer_before
      ?? 0
  );
  const noFuel = Number(statusCounts.no_fuel || 0);
  const noPower = Number(statusCounts.no_power || 0)
    + Number(statusCounts.low_power || 0)
    + Number(statusCounts.not_plugged_in_electric_network || 0)
    + Number(statusCounts.not_connected || 0);
  const cells = [
    {
      label: "Coal reserve",
      value: formatNumber(coalReserve, 0) + " / " + formatNumber(coalSafety, 0),
      detail: "reserve / safety stock",
      cls: coalSafety > 0 && coalReserve < coalSafety ? "bad" : coalReserve ? "good" : "warn",
    },
    {
      label: "Iron flow",
      value: formatNumber(ironRate, 1) + "/min",
      detail: "ore + plate live flow",
      cls: ironRate > 0 ? "good" : "warn",
    },
    {
      label: "Copper flow",
      value: formatNumber(copperRate, 1) + "/min",
      detail: "ore + plate live flow",
      cls: copperRate > 0 ? "good" : "warn",
    },
    {
      label: "Circuit feed",
      value: "Fe " + formatNumber(ironBuffer, 0)
        + " · Cu " + formatNumber(copperBuffer, 0),
      detail: "latest typed counterexample buffers",
      cls: ironBuffer > 0 && copperBuffer > 0 ? "good" : "bad",
    },
    {
      label: "No fuel",
      value: String(noFuel),
      detail: "entities physically starved",
      cls: noFuel ? "bad" : "good",
    },
    {
      label: "No power",
      value: String(noPower),
      detail: "entities without electric supply",
      cls: noPower ? "bad" : "good",
    },
  ];
  const diagnostics = state.machineDiagnostics || {};
  const probes = Array.isArray(diagnostics.machines) ? diagnostics.machines : [];
  for (const probe of probes) {
    // "cable: 0.0" alone was on screen for four generations and was
    // compatible with four incompatible failures. The cause the machine
    // itself reported now travels beside the number.
    const cause = probe.stall_cause;
    const output = probe.output_after;
    cells.push({
      label: String(probe.machine || "machine").replaceAll("_", " "),
      value: output === null || output === undefined
        ? "unmeasured"
        : formatNumber(output, 0) + " un",
      detail: [
        cause ? "causa " + cause : "causa nao medida",
        probe.status_after ? "status " + probe.status_after : null,
        probe.energy_after === null || probe.energy_after === undefined
          ? null
          : "energia " + formatNumber(probe.energy_after, 0),
        probe.network_id === null || probe.network_id === undefined
          ? null
          : "rede " + formatNumber(probe.network_id, 0),
      ].filter(Boolean).join(" · "),
      cls: cause === "producing" ? "good" : cause ? "bad" : "warn",
    });
  }
  const container = $("wipGrid");
  if (container) {
    container.innerHTML = cells.map((cell) =>
      '<div class="wip-cell ' + cell.cls + '">'
        + '<strong>' + escapeHtml(cell.label) + '</strong>'
        + '<span>' + escapeHtml(cell.value) + '</span>'
        + '<small>' + escapeHtml(cell.detail) + '</small>'
        + '</div>'
    ).join("");
  }
  const unhealthy = cells.filter((cell) => cell.cls === "bad").length;
  setClassText(
    "wipStatus",
    unhealthy ? unhealthy + " constraint" + (unhealthy === 1 ? "" : "s") : "healthy",
    unhealthy ? "badge warn" : "badge good"
  );
}

function renderModelMatrix() {
  const datasets = state.datasets || {};
  const recurrent = datasets.recurrent_world_model || {};
  const spatial = datasets.spatial_policy || {};
  const knowledge = state.knowledge || {};
  const gru = (recurrent.candidates && recurrent.candidates.gru) || {};
  const esn = (recurrent.baselines && recurrent.baselines.echo_state_network) || {};
  const holdout = recurrent.generation_holdout || {};
  const attention = (spatial.candidates && spatial.candidates.attention) || {};
  const mlp = (spatial.baselines && spatial.baselines.mlp) || {};
  const verified = Number(knowledge.verified_count || 0);
  const fallback = Number(knowledge.fallback_count || 0);
  const audited = verified + fallback;
  const rows = [
    {
      model: "GRU world",
      score: Number.isFinite(Number(holdout.mean_validation_mse))
        ? formatNumber(holdout.mean_validation_mse, 3) + " MSE"
        : "--",
      baseline: Number.isFinite(Number(holdout.mean_persistence_mse))
        ? formatNumber(holdout.mean_persistence_mse, 3) + " persistence"
        : "--",
      gate: recurrent.usable ? "control eligible" : "holdout rejected",
      cls: recurrent.usable ? "good" : "warn",
    },
    {
      model: "ESN world",
      score: Number.isFinite(Number(esn.validation_mse))
        ? formatNumber(esn.validation_mse, 3) + " MSE"
        : "--",
      baseline: Number.isFinite(Number(esn.persistence_baseline_mse))
        ? formatNumber(esn.persistence_baseline_mse, 3) + " persistence"
        : "--",
      gate: esn.beats_persistence ? "baseline pass" : "baseline fail",
      cls: esn.beats_persistence ? "good" : "bad",
    },
    {
      model: "Spatial MLP",
      score: Number.isFinite(Number(mlp.mean_cost_ratio_to_astar))
        ? formatNumber(mlp.mean_cost_ratio_to_astar, 3) + "× A*"
        : "--",
      baseline: formatNumber(Number(mlp.rollout_success_rate || 0) * 100, 0)
        + "% rollout",
      gate: spatial.control_eligible
        ? "control"
        : spatial.usable ? "proposal" : "advisory",
      cls: spatial.control_eligible ? "good" : spatial.usable ? "warn" : "bad",
    },
    {
      model: "Attention",
      score: Number.isFinite(Number(attention.mean_cost_ratio_to_astar))
        ? formatNumber(attention.mean_cost_ratio_to_astar, 3) + "× A*"
        : "--",
      baseline: Number.isFinite(Number(attention.validation_accuracy))
        ? formatNumber(attention.validation_accuracy * 100, 1) + "% accuracy"
        : "--",
      gate: attention.control_eligible ? "control" : "challenger",
      cls: attention.control_eligible ? "good" : "warn",
    },
    {
      model: "Qwen knowledge",
      score: audited
        ? formatNumber((verified / audited) * 100, 1) + "% verified"
        : "--",
      baseline: audited
        ? verified + " verified / " + fallback + " fallback"
        : Number(knowledge.count || 0) + " legacy",
      gate: audited && verified / audited >= 0.70 ? "verified" : "grounding",
      cls: audited && verified / audited >= 0.70 ? "good" : "warn",
    },
  ];
  const container = $("modelMatrix");
  if (!container) return;
  container.innerHTML =
    '<div class="model-matrix-row header"><span>model</span><span>score</span><span>baseline</span><span>gate</span></div>'
    + rows.map((row) =>
      '<div class="model-matrix-row">'
        + '<strong>' + escapeHtml(row.model) + '</strong>'
        + '<span>' + escapeHtml(row.score) + '</span>'
        + '<span>' + escapeHtml(row.baseline) + '</span>'
        + '<b class="model-gate ' + row.cls + '">' + escapeHtml(row.gate) + '</b>'
        + '</div>'
    ).join("");
}

function renderResearchAnalytics() {
  drawGenerationTrends();
  renderGameKnowledgeGraph();
  renderFactoryTopology();
  renderProductionDag();
  renderAutonomy();
  renderWipHealth();
  renderModelMatrix();
}


function renderCurriculum() {
  const list = $("curriculumList");
  const operational = cortexOperationalView();
  const historical = operational.historicalEvidenceMode;
  setText("curriculumLabel", historical ? "EVIDÊNCIA HISTÓRICA" : "EXECUÇÃO AUTÔNOMA");
  setText("curriculumTitle", historical ? "Último curriculum baseline preservado" : "Curriculum");
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
      : !historical && (status === "running" || status === "learning" || status === "validating")
        ? "active"
        : "";
    const displayStatus = historical ? "histórico · " + status : status;
    const detail = stage.detail || stage.objective || "";
    return '<div class="stage-row ' + cls + '">'
      + '<span class="stage-index">' + (index + 1) + '</span>'
      + '<div class="stage-copy"><strong>' + escapeHtml(stage.name || ("stage " + (index + 1)))
      + '</strong><small>' + escapeHtml(detail) + '</small></div>'
      + '<span class="stage-status">' + escapeHtml(displayStatus) + '</span>'
      + '</div>';
  }).join("");
}

function eventTime(event) {
  return event.at || event.timestamp || event.time || "";
}

function renderTimeline() {
  const operational = cortexOperationalView();
  const historical = operational.historicalEvidenceMode;
  setText("timelineLabel", historical ? "EVIDÊNCIA HISTÓRICA" : "RASTRO DE DECISÕES");
  setText("timelineTitle", historical ? "Última timeline persistida" : "Linha do tempo");
  const events = [];
  const seen = new Set();
  const appendUnique = (event, source) => {
    const when = String(eventTime(event));
    const type = String(event.type || "event");
    const message = String(event.message || event.observation || event.lesson || type);
    const key = when + "|" + type + "|" + message;
    if (seen.has(key)) return;
    seen.add(key);
    events.push(Object.assign({}, event, { source }));
  };
  for (const event of ((state.research && state.research.events) || [])) {
    appendUnique(event, "research");
  }
  for (const event of ((state.run && state.run.events) || [])) {
    appendUnique(event, "run");
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
    let message = event.message || event.observation || event.lesson || type;
    const baselineContext = (state.experimentContext || {}).kind === "baseline_seed";
    if (
      baselineContext
      && type === "selection"
      && String(message) === "Incumbent champion retained."
    ) {
      message = "Baseline seed closed; no champion was inherited or selected.";
    }
    const when = eventTime(event);
    return '<div class="timeline-row ' + escapeHtml(type) + '">'
      + '<strong>' + escapeHtml(message) + '</strong>'
      + '<small>' + escapeHtml((historical ? "histórico · " : "") + type)
        + (when ? " · " + escapeHtml(when) : "") + '</small>'
      + '</div>';
  }).join("");
}

function renderKnowledge() {
  const lessons = Array.isArray(state.knowledge && state.knowledge.lessons)
    ? state.knowledge.lessons
    : [];
  const count = Number((state.knowledge && state.knowledge.count) || lessons.length || 0);
  const historical = cortexOperationalView().historicalEvidenceMode;
  setText(
    "knowledgeCount",
    count + " lesson" + (count === 1 ? "" : "s")
      + (historical ? " · histórico baseline" : "")
  );

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
  const historical = cortexOperationalView().historicalEvidenceMode;
  setClassText(
    "truthOnline",
    onlineHistory.length
      ? (historical ? "histórico · " : "") + onlineHistory.length + " trials"
      : "not started",
    historical ? "muted" : onlineHistory.length ? "good" : "warn"
  );
  setClassText(
    "truthKnowledge",
    knowledgeCount ? knowledgeCount + " lessons" : "not started",
    knowledgeCount ? "good" : "warn"
  );

  const evolution = (
    state.research && state.research.evolution
      ? state.research.evolution
      : state.evolution
  ) || {};
  const champion = evolution.champion || null;
  const challenger = evolution.challenger || null;
  const validatedChampion = (
    state.evolution && state.evolution.validated_champion
  ) || null;
  setClassText(
    "truthEvolution",
    validatedChampion
      ? "G" + String(validatedChampion.generation || "?")
        + " open-play validated"
      : champion
        ? "G" + String(champion.generation || "?")
          + " lab champion · autonomy unvalidated"
        : challenger
          ? (historical ? "histórico · G" : "G") + String(evolution.generation || "?")
            + (historical ? " frozen" : " evaluating")
          : "no champion",
    historical ? "muted" : validatedChampion ? "good" : champion || challenger ? "warn" : "muted"
  );
  const progression = state.progression || {};
  const technologyMode = String(progression.technology_mode || "unknown");
  const validatedCapabilities = Array.isArray(progression.validated_achieved)
    ? progression.validated_achieved.length
    : 0;
  const planningAssumptions = Array.isArray(progression.planning_assumptions)
    ? progression.planning_assumptions.length
    : 0;
  setClassText(
    "truthTechTree",
    technologyMode === "factorio_2_real_triggers"
      ? "open-play · real tech tree · " + validatedCapabilities + " validated"
      : technologyMode === "pre_unlocked"
        ? "lab · " + validatedCapabilities + " validated · "
          + planningAssumptions + " planning assumptions"
        : technologyMode,
    technologyMode === "factorio_2_real_triggers" ? "good" : "warn"
  );

  const autonomyState = state.autonomy || {};
  const autonomyLevel = String(autonomyState.level || "unknown");
  const autonomyInstrumented = !!autonomyState.intervention_instrumented;
  setClassText(
    "truthAutonomy",
    autonomyState.closed_loop
      ? "closed-loop validated"
      : autonomyLevel.replaceAll("_", " ")
        + (autonomyInstrumented ? "" : " · legacy intervention unknown"),
    autonomyState.closed_loop
      ? "good"
      : autonomyLevel === "semi_autonomous" ? "warn" : "bad"
  );

  setClassText(
    "truthDataset",
    (historical ? "histórico · " : "")
      + spatialDemos + " demo" + (spatialDemos === 1 ? "" : "s")
      + (historical ? " · frozen" : datasets.training_ready ? " · ready" : " · collecting"),
    historical ? "muted" : datasets.training_ready ? "good" : spatialDemos ? "warn" : "muted"
  );

  const neuralStatus = capabilities.neural_policy && capabilities.neural_policy.status;
  const spatialPolicy = datasets.spatial_policy || null;
  const recurrentModel = datasets.recurrent_world_model || null;
  const telemetrySamples = Number(datasets.telemetry_samples || 0);
  if (spatialPolicy) {
    const active = String(spatialPolicy.active_model || "none");
    const usable = !!spatialPolicy.usable;
    const controlEligible = !!spatialPolicy.control_eligible;
    const attention = (
      spatialPolicy.candidates
      && spatialPolicy.candidates.attention
    ) || null;
    const cost = Number(
      (attention && attention.mean_cost_ratio_to_astar)
      ?? spatialPolicy.mean_cost_ratio_to_astar
      ?? 0
    );
    const success = Number(
      (attention && attention.rollout_success_rate)
      ?? spatialPolicy.rollout_success_rate
      ?? 0
    );
    setClassText(
      "truthNeural",
      (controlEligible
        ? active + " · control eligible"
        : usable
          ? active + " · proposal eligible"
          : "trained · advisory only")
        + " · rollout " + formatNumber(success * 100, 0) + "%"
        + " · cost " + formatNumber(cost, 3) + "× A*",
      controlEligible ? "good" : usable ? "warn" : "muted"
    );
  } else {
    setClassText(
      "truthNeural",
      neuralStatus || "not trained",
      neuralStatus === "trained" ? "good" : "muted"
    );
  }

  if (recurrentModel) {
    const usable = !!recurrentModel.usable;
    const active = String(recurrentModel.active_model || "none");
    const holdout = recurrentModel.generation_holdout || {};
    const gru = (
      recurrentModel.candidates
      && recurrentModel.candidates.gru
    ) || {};
    const wins = Number(holdout.wins || 0);
    const folds = Number(holdout.valid_folds || 0);
    const mse = Number(
      holdout.mean_validation_mse
      ?? gru.validation_mse
      ?? 0
    );
    const baseline = Number(
      holdout.mean_persistence_mse
      ?? gru.persistence_baseline_mse
      ?? 0
    );
    const samples = Number(recurrentModel.sample_count || telemetrySamples);
    setClassText(
      "truthWorldModel",
      (usable
        ? active + " · cross-generation accepted"
        : gru.validation_mse !== undefined
          ? "GRU · holdout rejected"
          : "collecting")
        + " · " + samples + " samples"
        + (folds ? " · " + wins + "/" + folds + " generation wins" : "")
        + (mse ? " · mse " + formatNumber(mse, 3)
          + " vs " + formatNumber(baseline, 3) : ""),
      usable ? "good" : "warn"
    );
  } else {
    setClassText(
      "truthWorldModel",
      telemetrySamples + " temporal samples · collecting",
      telemetrySamples >= 24 ? "warn" : "muted"
    );
  }
}

function updateMission() {
  const research = state.research || {};
  const curriculum = Array.isArray(research.curriculum) ? research.curriculum : [];
  const current = research.current_stage
    || curriculum.find((stage) => ["running", "learning", "validating"].includes(stage.status))
    || null;
  const operational = cortexOperationalView();

  if (operational.historicalEvidenceMode && operational.phase4Checkpoint === "F4-B") {
    const blocker = operational.cortexPhase.phase4_blocker || {};
    setText("missionTitle", "F4-C — causal memory ablation + held-out transfer");
    setText(
      "missionDetail",
      "Pré-registro causal F4-C congelado e elegível. Nenhum outcome F4-C foi observado; validar o paired evaluation harness antes de qualquer pilot seed."
    );
    setText("stageName", "F4-C · protocol frozen · harness validation");
    setText("nextAction", operational.cortexPhase.resume?.action
      || "validate paired evaluation harness before any pilot seed");
    $("stageProgressBar").style.width = "0%";
    setText("stageProgressText", String(blocker.status || "blocked").toUpperCase());
    setClassText("researchBadge", "CORTEX SHADOW · IDLE INTENCIONAL", "badge warn");
    return;
  }

  setText(
    "missionTitle",
    research.objective
      || (state.run && state.run.objective)
      || "Aguardando ciclo de pesquisa…"
  );
  setText(
    "missionDetail",
    research.detail
      || (
        (state.run && state.run.status)
          ? "Último estado observado: " + statusLabel(state.run.status)
          : "Nenhum estágio ativo."
      )
  );
  setText(
    "stageName",
    stageLabel((current && current.name) || research.stage || "estágio --")
  );
  setText(
    "nextAction",
    research.next_action || "aguardando executor do currículo"
  );

  let progress = Number(research.progress ?? 0);
  if (!Number.isFinite(progress)) progress = 0;
  if (progress <= 1) progress *= 100;
  progress = Math.max(0, Math.min(100, progress));
  $("stageProgressBar").style.width = progress + "%";
  setText("stageProgressText", formatNumber(progress, 0) + "%");

  const status = String(research.status || "idle");
  const frontierOpen = !!(state.progression && state.progression.next_goal);
  const promotion = (research.evolution && research.evolution.promotion) || null;
  const semanticStatus = status === "completed" && frontierOpen
    ? "generation_complete"
    : status === "partial_success" && promotion && !promotion.promoted
      ? "generation_rejected"
      : status;
  const badgeClass = ["running", "learning", "validating"].includes(semanticStatus)
    ? "badge live"
    : ["completed", "generation_complete"].includes(semanticStatus)
      ? "badge good"
      : semanticStatus === "generation_rejected"
        ? "badge warn"
        : semanticStatus === "error" || semanticStatus === "failed"
          ? "badge dead"
          : "badge neutral";
  setClassText(
    "researchBadge",
    semanticStatus === "generation_complete"
      ? "geração concluída"
      : semanticStatus === "generation_rejected"
        ? "geração rejeitada"
        : "pesquisa · " + statusLabel(semanticStatus),
    badgeClass
  );
}

function updateKpis() {
  const world = state.world || {};
  const status = state.status || {};
  const factorio = status.factorio || {};
  const llm = status.llm || {};
  const render = status.render || {};
  const production = world.production || {};
  const produced = production.produced || production.output || {};
  const consumed = production.consumed || production.input || {};
  const online = (state.research && state.research.online_learning) || {};
  const onlineRows = online.history || [];

  setText("entityCount", formatNumber(world.entity_count || 0, 0));
  const tickText = "game " + formatNumber(world.tick, 0)
    + (world.experiment_tick !== undefined
      ? " · FLE " + formatNumber(world.experiment_tick, 0)
      : "");
  setText("worldTick", tickText);
  setText("probeLatency", formatNumber(world.latency_ms, 1) + " ms");

  const health = {};
  for (const entity of (world.entities || [])) {
    const statusName = String(entity.status || "");
    if (!statusName || ["normal", "working"].includes(statusName)) {
      if (statusName === "working") health.working = (health.working || 0) + 1;
      continue;
    }
    health[statusName] = (health[statusName] || 0) + 1;
  }
  const healthContainer = $("entityHealth");
  if (healthContainer) {
    const rows = Object.entries(health).sort((a, b) => {
      if (a[0] === "working") return -1;
      if (b[0] === "working") return 1;
      return Number(b[1]) - Number(a[1]);
    });
    healthContainer.innerHTML = rows.length
      ? rows.map(([name, count]) => {
          const bad = ["no_fuel", "no_power", "disabled"].includes(name);
          const warn = ["no_ingredients", "full_output", "low_power"].includes(name);
          return '<span class="entity-health-pill '
            + (bad ? "bad" : warn ? "warn" : "good") + '">'
            + escapeHtml(name.replaceAll("_", " ")) + " × " + count
            + '</span>';
        }).join("")
      : '<span class="entity-health-pill muted">no operational status</span>';
  }

  const primaryOutput = Object.entries(produced).sort((a, b) => Number(b[1]) - Number(a[1]))[0];
  if (primaryOutput) {
    setText("productionPrimary", formatNumber(primaryOutput[1], 0) + " " + primaryOutput[0]);
    const primaryInput = Object.entries(consumed).sort((a, b) => Number(b[1]) - Number(a[1]))[0];
    const coalConsumed = Number(consumed.coal || 0);
    const coalProduced = Number(produced.coal || 0);
    const consumptionLabel = primaryInput
      ? formatNumber(primaryInput[1], 0) + " " + primaryInput[0] + " consumed"
      : "no measured consumption";
    const resourceAccounting = (
      state.research
      && state.research.resource_accounting
      && state.research.resource_accounting.exogenous_inputs
    ) || {};
    const coalOrigin = String(((resourceAccounting.coal || {}).status) || "external");
    setText(
      "productionSecondary",
      coalConsumed > 0 && coalProduced <= 0
        ? consumptionLabel + " · coal source: " + (
            ["retired", "internal", "self_sufficient"].includes(coalOrigin)
              ? "internal buffer"
              : "bootstrap inventory"
          )
        : consumptionLabel
    );
  } else {
    setText("productionPrimary", "--");
    setText("productionSecondary", "no measured flow");
  }

  const runner = status.research_runner || {};
  const research = state.research || {};
  const arena = research.arena || {};
  const context = state.experimentContext || {};
  const baselineContext = context.kind === "baseline_seed";
  const operational = cortexOperationalView();
  const arenaMode = String(arena.mode || "unknown");
  const arenaLabel = operational.historicalEvidenceMode
    ? "CORTEX SHADOW · " + (operational.phase4Checkpoint || operational.phase || "--")
    : baselineContext
      ? "BASELINE · seed " + String(context.seed ?? "--")
      : arenaMode === "open_play"
        ? "OPEN PLAY · tech tree real"
        : arenaMode === "lab_play"
          ? "LAB ARENA · accelerated"
          : "arena --";
  setClassText(
    "arenaBadge",
    arenaLabel,
    operational.historicalEvidenceMode
      ? "badge warn"
      : baselineContext
        ? "badge good"
        : arenaMode === "open_play"
          ? "badge live"
          : arenaMode === "lab_play"
            ? "badge neutral"
            : "badge neutral"
  );
  const updatedAt = Date.parse(research.updated_at || "");
  const researchAgeS = Number.isFinite(updatedAt)
    ? Math.max(0, (Date.now() - updatedAt) / 1000)
    : null;
  const researchStatus = String(research.status || "idle");
  const runnerPhase = String(runner.phase || "");
  const runnerStalled = !!runner.active
    && runnerPhase === "lab_generation"
    && ["running", "learning", "validating"].includes(researchStatus)
    && researchAgeS !== null
    && researchAgeS > 45;
  const frontierOpen = !!(state.progression && state.progression.next_goal);
  const generationDone = ["completed", "generation_complete"].includes(researchStatus)
    && frontierOpen;
  const researchPromotion = (
    research.evolution
    && research.evolution.promotion
  ) || null;
  const generationRejected = researchStatus === "partial_success"
    && researchPromotion
    && !researchPromotion.promoted;
  const baselineCompleted = baselineContext && context.status === "completed";
  const loopLabel = operational.historicalEvidenceMode
    ? "idle · by design"
    : baselineCompleted
      ? "baseline seed completed"
      : runnerStalled
      ? "stalled"
      : runner.active
        ? runnerPhase === "model_training"
        ? "model training"
        : runnerPhase === "open_play_validation"
          ? "open-play validation"
          : runnerPhase === "selection_transition"
            ? "selection / transition"
            : "generation active"
        : generationRejected
          ? "generation rejected"
          : generationDone
          ? "generation complete"
          : researchStatus === "completed"
            ? "research complete"
            : "idle";
  setClassText(
    "researchLoop",
    loopLabel,
    runnerStalled ? "bad" : runner.active ? "good" : "muted"
  );
  setText(
    "researchLoopDetail",
    operational.historicalEvidenceMode
      ? "no active agent process · evolution OFF · F4-C paired harness validation is the current research task"
      : baselineCompleted
        ? "seed " + String(context.seed ?? "--")
        + " closed · " + String(research.status || context.status || "--")
        + " · next: " + String(research.next_action || "--")
      : runnerStalled
        ? "process active · no state update for " + formatNumber(researchAgeS, 0) + " s"
        : runner.active
        ? runnerPhase === "model_training"
          ? "generation closed · ESN/GRU holdout training and model selection"
          : runnerPhase === "open_play_validation"
            ? "champion transfer test · empty inventory + real technology tree"
            : runnerPhase === "selection_transition"
              ? "selection closed · preparing next arena/strategy"
              : (research.stage || "working") + " · updated "
                + (researchAgeS === null ? "--" : formatNumber(researchAgeS, 0) + " s") + " ago"
        : generationRejected
          ? "challenger failed survival gate · incumbent retained · next: "
            + String(research.next_action || "--")
          : generationDone
            ? "champion/challenger generation closed · next frontier: "
              + String(research.next_action || "--")
            : researchStatus === "completed"
              ? "finite research objective complete"
              : "no active agent process"
  );

  const progression = state.progression || {};
  const nextGoal = progression.next_goal || null;
  const frontier = Array.isArray(progression.frontier) ? progression.frontier : [];
  const achievedGoals = Array.isArray(progression.achieved) ? progression.achieved : [];
  if (operational.historicalEvidenceMode) {
    setText("engineeringGoal", "F4-C · causal memory transfer benchmark");
    setText(
      "engineeringGoalDetail",
      "protocol not frozen · memory ON vs explicit ablation · held-out non-confirmatory tasks · leakage control + paired inference"
    );
  } else if (nextGoal) {
    setText("engineeringGoal", nextGoal.label || nextGoal.goal_id || "next capability");
    const alternatives = frontier
      .slice(1, 3)
      .map((candidate) => candidate.label || candidate.goal_id)
      .filter(Boolean);
    const debt = Array.isArray(progression.dependency_debt)
      ? progression.dependency_debt
      : [];
    const debtLabel = debt.length
      ? " · dependency debt: "
        + debt.map((item) => {
          const missing = Array.isArray(item.missing_prerequisites)
            ? item.missing_prerequisites.join("+")
            : "?";
          return (item.goal_id || "goal") + " needs " + missing;
        }).join(" / ")
      : "";
    setText(
      "engineeringGoalDetail",
      String(nextGoal.kind || "engineering")
        + " · " + achievedGoals.length + " capabilities achieved"
        + debtLabel
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
  if (operational.historicalEvidenceMode) {
    setText(
      "onlineLearner",
      onlineRows.length
        ? "HISTÓRICO · " + String(online.algorithm || "online learner")
        : "HISTÓRICO · sem learner ativo"
    );
    setText(
      "onlineLearnerDetail",
      onlineRows.length
        ? onlineRows.length + " trials preservados · último best " + String(online.best_arm ?? "--") + " · não está aprendendo agora"
        : "nenhum processo de aprendizado online ativo"
    );
  } else {
    setText("onlineLearner", online.algorithm ? online.algorithm + " · " + onlineStatus : onlineStatus);
    setText(
      "onlineLearnerDetail",
      onlineRows.length
        ? onlineRows.length + " real-world trials · best " + String(online.best_arm ?? "--")
        : "no real-world trials yet"
    );
  }

  if (operational.historicalEvidenceMode && Number(world.entity_count || 0) === 0) {
    setText("productionPrimary", "no active factory");
    setText(
      "productionSecondary",
      "WORLD LIVE vazio · qualquer série abaixo é observação/estatística preservada, não produção autônoma atual"
    );
  }

  const recentTicks = (state.history || [])
    .slice(-4)
    .map((point) => Number(point.tick))
    .filter(Number.isFinite);
  const simulating = recentTicks.length >= 2
    && new Set(recentTicks).size > 1;
  const execution = status.execution || {};
  const activeAction = execution.action || {};
  const actionKind = String(activeAction.action_kind || "action");
  const actionStage = String(activeAction.stage || "");
  const factorioLabel = !factorio.connected
    ? "Factorio offline"
    : execution.action_active
      ? "Factorio · executando " + actionLabel(actionKind)
      : execution.writer_active
        ? "Factorio · entre ações"
        : simulating
          ? "Factorio · simulando"
          : "Factorio · ocioso";
  setClassText(
    "factorioStatus",
    factorioLabel,
    factorio.connected ? "hud-chip good" : "hud-chip bad"
  );
  const factorioStatusNode = $("factorioStatus");
  if (factorioStatusNode) {
    factorioStatusNode.title = execution.action_active
      ? [
          actionStage || "active stage",
          activeAction.action_id || "",
          formatNumber(activeAction.elapsed_s || 0, 1) + " s",
        ].filter(Boolean).join(" · ")
      : execution.writer_active
        ? "executor experimental possui o lease exclusivo do mundo"
        : "nenhum executor experimental possui o lease do mundo";
  }
  setClassText("llmStatus", llm.connected ? "Qwen inference" : "offline", llm.connected ? "good" : "bad");
  setText(
    "llmDetail",
    llm.connected
      ? ((llm.models && llm.models.length ? llm.models.join(", ") : "qwen")
        + (operational.historicalEvidenceMode
          ? " · weights static · inference available · no active Cortex writer"
          : " · weights static · knowledge memory learns"))
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
  if (operational.historicalEvidenceMode) {
    setClassText("runStatus", "FROZEN · " + statusLabel(runStatus), "badge neutral");
    setText(
      "runId",
      state.run && state.run.run_id
        ? "última run · " + String(state.run.run_id)
        : "no active run"
    );
  } else {
    setClassText(
      "runStatus",
      statusLabel(runStatus),
      ["success", "completed", "generation_complete"].includes(runStatus) ? "badge good"
        : ["running", "starting", "learning", "validating"].includes(runStatus) ? "badge live"
        : runStatus === "--" ? "badge neutral" : "badge warn"
    );
    setText("runId", (state.run && state.run.run_id) || "no run");
  }
  setText(
    "runSummary",
    state.run && Object.keys(state.run).length ? JSON.stringify(state.run, null, 2) : "No active run yet."
  );
}

function applyPayload(payload) {
  if (payload.status) state.status = payload.status;
  if (payload.experiment_context) state.experimentContext = payload.experiment_context;
  if (payload.world) state.world = payload.world;
  if (payload.learning) state.learning = payload.learning;
  if (payload.history) state.history = payload.history;
  if (payload.run) state.run = payload.run;
  if (payload.research) state.research = payload.research;
  if (payload.progression) state.progression = payload.progression;
  if (payload.production_plan) state.productionPlan = payload.production_plan;
  if (payload.machine_diagnostics) {
    state.machineDiagnostics = payload.machine_diagnostics;
  }
  if (payload.autonomy) state.autonomy = payload.autonomy;
  if (payload.resource_overview) state.resourceOverview = payload.resource_overview;
  if (payload.factory_graph) state.factoryGraph = payload.factory_graph;
  if (payload.game_graph_summary) state.gameGraphSummary = payload.game_graph_summary;
  if (payload.evolution) state.evolution = payload.evolution;
  if (payload.knowledge) state.knowledge = payload.knowledge;
  if (payload.datasets) state.datasets = payload.datasets;

  updateKpis();
  renderExperimentContext();
  updateMission();
  updateEntityMix();
  renderWorldHotspots();
  renderResourceLegend();
  renderCapabilityHealth();
  renderEvolution();
  renderLearningObservatory();
  renderResearchCockpit();
  renderResearchAnalytics();
  renderCurriculum();
  renderTimeline();
  renderKnowledge();
  renderTruthTable();
  drawOfflineLearning();
  drawOnlineLearning();
  drawHistory();
  drawGenerationTrends();
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
  const endpoints = [
    ["/api/status", "status"],
    ["/api/world", "world"],
    ["/api/history", "history"],
    ["/api/learning", "learning"],
    ["/api/run", "run"],
    ["/api/research", "research"],
    ["/api/progression", "progression"],
    ["/api/production-plan", "production_plan"],
    ["/api/machine-diagnostics", "machine_diagnostics"],
    ["/api/autonomy", "autonomy"],
    ["/api/resource-overview", "resource_overview"],
    ["/api/factory-graph", "factory_graph"],
    ["/api/game-graph/summary", "game_graph_summary"],
    ["/api/evolution", "evolution"],
    ["/api/knowledge", "knowledge"],
    ["/api/datasets", "datasets"],
    ["/api/context", "experiment_context"],
  ];
  const settled = await Promise.allSettled(
    endpoints.map(async ([path, key]) => {
      const response = await fetch(path);
      if (!response.ok) {
        throw new Error(path + " returned HTTP " + response.status);
      }
      return [key, await response.json()];
    })
  );
  const payload = {};
  const failures = [];
  settled.forEach((result, index) => {
    if (result.status === "fulfilled") {
      payload[result.value[0]] = result.value[1];
    } else {
      failures.push(endpoints[index][0] + ": " + String(result.reason));
    }
  });
  if (Object.keys(payload).length) {
    applyPayload(payload);
  }
  if (failures.length) {
    console.warn("dashboard partial bootstrap", failures);
    setClassText("socketBadge", "degraded · partial", "badge dead");
  }
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
      const payload = JSON.parse(event.data);
      if (payload.stream_error) {
        console.error("live sample degraded", payload.stream_error);
        setClassText("socketBadge", "degraded · live", "badge dead");
      } else {
        setClassText("socketBadge", "live", "badge live");
      }
      applyPayload(payload);
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

Promise.allSettled([loadConfig(), loadInitialState(), loadProduction()])
  .then((results) => {
    const failures = results.filter((result) => result.status === "rejected");
    if (failures.length) {
      console.error("dashboard bootstrap degraded", failures);
      setClassText("socketBadge", "degraded", "badge dead");
    }
  })
  .finally(connectSocket);

setInterval(updateFrameAge, 1000);
setInterval(() => {
  if (document.visibilityState === "visible") refreshWorldFrame(false);
}, 3000);
setInterval(() => {
  if (document.visibilityState === "visible") {
    loadProduction(state.productionPrecision);
  }
}, 6000);
