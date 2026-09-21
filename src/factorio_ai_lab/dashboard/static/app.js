const $ = (id) => document.getElementById(id);

const state = {
  world: null,
  learning: { history: [], summary: {} },
  routing: { rows: [] },
  history: [],
  socket: null,
};

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function setHealth(id, ok, up = "online", down = "offline") {
  const el = $(id);
  if (!el) return;
  el.textContent = ok ? up : down;
  el.classList.toggle("good", !!ok);
  el.classList.toggle("bad", !ok);
}

function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}

function getPosition(entity) {
  if (!entity || typeof entity !== "object") return null;
  const p = entity.position || entity.pos || entity.location;
  if (p && Number.isFinite(Number(p.x)) && Number.isFinite(Number(p.y))) {
    return { x: Number(p.x), y: Number(p.y) };
  }
  if (Number.isFinite(Number(entity.x)) && Number.isFinite(Number(entity.y))) {
    return { x: Number(entity.x), y: Number(entity.y) };
  }
  return null;
}

function getName(entity) {
  if (!entity || typeof entity !== "object") return "entity";
  if (typeof entity.name === "string") return entity.name;
  if (typeof entity.prototype === "string") return entity.prototype;
  if (entity.prototype && typeof entity.prototype.name === "string") return entity.prototype.name;
  if (typeof entity.type === "string") return entity.type;
  return "entity";
}

function categoryFor(name) {
  const n = name.toLowerCase();
  if (n.includes("belt") || n.includes("splitter") || n.includes("inserter") || n.includes("pipe")) return "belt";
  if (n.includes("pole") || n.includes("solar") || n.includes("accumulator") || n.includes("boiler") || n.includes("steam")) return "power";
  if (n.includes("furnace") || n.includes("assembling") || n.includes("drill") || n.includes("lab") || n.includes("refinery") || n.includes("plant")) return "machine";
  return "other";
}

function colorFor(category) {
  return {
    belt: "#f0a33a",
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

function drawWorld() {
  const canvas = $("worldCanvas");
  const { ctx, width, height } = prepareCanvas(canvas);
  ctx.clearRect(0, 0, width, height);

  const entities = Array.isArray(state.world?.entities) ? state.world.entities : [];
  const positioned = entities
    .map((entity) => ({ entity, position: getPosition(entity), name: getName(entity) }))
    .filter((item) => item.position);

  $("worldEmpty").style.display = positioned.length ? "none" : "grid";

  if (!positioned.length) {
    setText("boundsText", "bounds --");
    $("entityTypes").innerHTML = "";
    return;
  }

  const xs = positioned.map((item) => item.position.x);
  const ys = positioned.map((item) => item.position.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = Math.max(maxX - minX, 8);
  const spanY = Math.max(maxY - minY, 8);
  const padding = 34;
  const scale = Math.min((width - padding * 2) / spanX, (height - padding * 2) / spanY);

  const project = (p) => ({
    x: padding + (p.x - minX) * scale + (width - padding * 2 - spanX * scale) / 2,
    y: height - padding - (p.y - minY) * scale - (height - padding * 2 - spanY * scale) / 2,
  });

  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(255,255,255,.04)";
  const gridStep = Math.max(1, Math.ceil(24 / Math.max(scale, 1)));
  for (let gx = Math.floor(minX / gridStep) * gridStep; gx <= maxX; gx += gridStep) {
    const a = project({ x: gx, y: minY });
    const b = project({ x: gx, y: maxY });
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  for (let gy = Math.floor(minY / gridStep) * gridStep; gy <= maxY; gy += gridStep) {
    const a = project({ x: minX, y: gy });
    const b = project({ x: maxX, y: gy });
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }

  const counts = {};
  positioned.forEach(({ position, name }) => {
    const category = categoryFor(name);
    counts[name] = (counts[name] || 0) + 1;
    const p = project(position);
    const size = category === "machine"
      ? Math.max(5, Math.min(11, scale * 1.5))
      : Math.max(3, Math.min(7, scale * .75));
    ctx.fillStyle = colorFor(category);
    ctx.globalAlpha = category === "belt" ? .82 : .94;
    if (category === "power") {
      ctx.beginPath();
      ctx.arc(p.x, p.y, size * .55, 0, Math.PI * 2);
      ctx.fill();
    } else {
      ctx.fillRect(p.x - size / 2, p.y - size / 2, size, size);
    }
  });
  ctx.globalAlpha = 1;

  setText(
    "boundsText",
    "x " + formatNumber(minX) + "…" + formatNumber(maxX)
      + " · y " + formatNumber(minY) + "…" + formatNumber(maxY)
  );
  const topTypes = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 6);
  $("entityTypes").innerHTML = topTypes
    .map(([name, count]) => "<span>" + name + " × " + count + "</span>")
    .join("");
}

function drawAxes(ctx, width, height, padding) {
  ctx.strokeStyle = "rgba(255,255,255,.10)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padding, padding / 2);
  ctx.lineTo(padding, height - padding);
  ctx.lineTo(width - padding / 2, height - padding);
  ctx.stroke();
}

function drawLineChart(canvasId, rows, xAccessor, series) {
  const canvas = $(canvasId);
  const { ctx, width, height } = prepareCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  const padding = 34;
  drawAxes(ctx, width, height, padding);
  if (!rows.length) return;

  const xs = rows.map(xAccessor).filter(Number.isFinite);
  if (!xs.length) return;
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const spanX = Math.max(maxX - minX, 1);

  series.forEach((item) => {
    const values = rows.map(item.value).filter(Number.isFinite);
    if (!values.length) return;
    const minY = item.zero ? 0 : Math.min(...values);
    const maxY = Math.max(...values);
    const spanY = Math.max(maxY - minY, 1e-9);

    ctx.strokeStyle = item.color;
    ctx.lineWidth = item.width || 1.6;
    ctx.beginPath();
    let started = false;
    rows.forEach((row) => {
      const xValue = xAccessor(row);
      const yValue = item.value(row);
      if (!Number.isFinite(xValue) || !Number.isFinite(yValue)) return;
      const x = padding + ((xValue - minX) / spanX) * (width - padding * 1.5);
      const y = height - padding
        - ((yValue - minY) / spanY) * (height - padding * 1.6);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
  });

  ctx.fillStyle = "#7f8a93";
  ctx.font = "10px ui-monospace, monospace";
  ctx.fillText(formatNumber(minX, 0), padding, height - 10);
  ctx.fillText(formatNumber(maxX, 0), width - 52, height - 10);
}

function drawLearning() {
  const rows = state.learning?.history || [];
  const enriched = rows.map((row, index) => {
    const from = Math.max(0, index - 19);
    const windowRows = rows.slice(from, index + 1);
    const rolling = windowRows.reduce(
      (sum, r) => sum + Number(r.reward || 0),
      0
    ) / windowRows.length;
    return { ...row, rolling };
  });
  drawLineChart("learningCanvas", enriched, (r) => Number(r.episode), [
    { value: (r) => Number(r.reward), color: "rgba(106,181,247,.35)", width: 1 },
    { value: (r) => Number(r.rolling), color: "#f0a33a", width: 2.2 },
  ]);
}

function drawSweep() {
  const canvas = $("sweepCanvas");
  const { ctx, width, height } = prepareCanvas(canvas);
  ctx.clearRect(0, 0, width, height);
  const rows = state.routing?.rows || [];
  const padding = 38;
  drawAxes(ctx, width, height, padding);
  if (!rows.length) return;

  const xs = rows.map((r) => Number(r.turn_penalty));
  const ys = rows.map((r) => Number(r.mean_expanded_nodes));
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = Math.max(maxX - minX, 1);
  const spanY = Math.max(maxY - minY, 1);

  rows.forEach((row) => {
    const x = padding
      + ((Number(row.turn_penalty) - minX) / spanX) * (width - padding * 1.6);
    const y = height - padding
      - ((Number(row.mean_expanded_nodes) - minY) / spanY)
        * (height - padding * 1.7);
    const turns = Number(row.mean_turns);
    const radius = 4 + Math.max(0, Math.min(5, turns - 4));
    ctx.fillStyle = "#6ab5f7";
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#9aa4ac";
    ctx.font = "9px ui-monospace, monospace";
    ctx.fillText("β=" + row.turn_penalty, x + 7, y - 7);
    ctx.fillText(formatNumber(turns, 2) + " turns", x + 7, y + 5);
  });
}

function drawHistory() {
  const rows = state.history || [];
  drawLineChart("historyCanvas", rows, (r) => Number(r.timestamp), [
    { value: (r) => Number(r.entity_count), color: "#64d98b", width: 2, zero: true },
    {
      value: (r) => Number(r.probe_latency_ms),
      color: "rgba(240,163,58,.72)",
      width: 1.4,
      zero: true,
    },
  ]);
}

function applyPayload(payload) {
  state.world = payload.world || state.world;
  state.learning = payload.learning || state.learning;
  state.history = payload.history || state.history;

  const status = payload.status || {};
  const factorio = status.factorio || {};
  const llm = status.llm || {};
  const memory = status.memory || {};

  setHealth("factorioStatus", factorio.connected, "online", "offline");
  setText(
    "factorioDetail",
    factorio.connected
      ? "RCON " + factorio.host + ":" + factorio.rcon_port
      : "RCON unavailable"
  );
  setHealth("llmStatus", llm.connected, "ready", "offline");
  setText(
    "llmDetail",
    llm.connected && llm.models?.length
      ? llm.models.join(", ")
      : "llama.cpp :18081"
  );
  setText("routeLocal", llm.connected ? "ready" : "offline");
  $("routeLocal").className = llm.connected ? "good" : "bad";

  setText("commitBadge", (status.branch || "--") + " · " + (status.git_sha || "--"));
  setText("entityCount", formatNumber(state.world?.entity_count || 0, 0));
  setText("worldTick", "tick " + formatNumber(state.world?.tick, 0));
  setText("probeLatency", formatNumber(state.world?.latency_ms, 1) + " ms");
  setText(
    "memoryUsed",
    memory.used_mib ? formatNumber(memory.used_mib / 1024, 1) + " GiB" : "--"
  );
  setText(
    "memoryDetail",
    memory.total_mib
      ? formatNumber(memory.available_mib / 1024, 1) + " GiB available"
      : "--"
  );

  const summary = state.learning?.summary || {};
  if (summary.best_observed !== undefined) {
    setText("learnerBest", "β " + summary.best_observed);
    setText("learnerEpisodes", (summary.episodes || 0) + " learning episodes");
    setText("learningSummary", JSON.stringify(summary, null, 2));
  }

  drawWorld();
  drawLearning();
  drawHistory();
}

async function loadRouting() {
  try {
    const response = await fetch("/api/experiments/routing");
    state.routing = await response.json();
    setText("sweepSource", state.routing.source || "no sweep");
    drawSweep();
  } catch (_) {}
}

async function loadConfig() {
  const response = await fetch("/api/config");
  const config = await response.json();
  const form = $("configForm");
  Object.entries(config).forEach(([key, value]) => {
    if (form.elements[key]) form.elements[key].value = value;
  });
}

async function loadInitialState() {
  const [statusResponse, worldResponse, historyResponse, learningResponse] =
    await Promise.all([
      fetch("/api/status"),
      fetch("/api/world"),
      fetch("/api/history"),
      fetch("/api/learning"),
    ]);

  const [status, world, history, learning] = await Promise.all([
    statusResponse.json(),
    worldResponse.json(),
    historyResponse.json(),
    learningResponse.json(),
  ]);

  applyPayload({
    status,
    world,
    history,
    learning,
  });
}

$("configForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const patch = {};
  const numeric = new Set([
    "poll_interval_s",
    "astar_turn_penalty",
    "ucb_exploration",
    "llm_temperature",
    "llm_max_tokens",
  ]);
  for (const element of form.elements) {
    if (!element.name) continue;
    patch[element.name] = numeric.has(element.name)
      ? Number(element.value)
      : element.value;
  }
  const response = await fetch("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!response.ok) {
    setText("configSaved", "error " + response.status);
    return;
  }
  setText("configSaved", "saved");
  setTimeout(() => setText("configSaved", ""), 1800);
});

function connectSocket() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(protocol + "//" + location.host + "/ws/live");
  state.socket = socket;

  socket.addEventListener("open", () => {
    $("socketBadge").textContent = "live";
    $("socketBadge").className = "badge live";
  });

  socket.addEventListener("message", (event) => {
    try {
      applyPayload(JSON.parse(event.data));
    } catch (error) {
      console.error(error);
    }
  });

  socket.addEventListener("close", () => {
    $("socketBadge").textContent = "reconnecting";
    $("socketBadge").className = "badge dead";
    setTimeout(connectSocket, 1500);
  });

  socket.addEventListener("error", () => socket.close());
}

window.addEventListener("resize", () => {
  drawWorld();
  drawLearning();
  drawSweep();
  drawHistory();
});

Promise.all([loadRouting(), loadConfig(), loadInitialState()])
  .catch((error) => {
    console.error("dashboard bootstrap failed", error);
    $("socketBadge").textContent = "degraded";
    $("socketBadge").className = "badge dead";
  })
  .finally(connectSocket);

setInterval(loadRouting, 30000);
