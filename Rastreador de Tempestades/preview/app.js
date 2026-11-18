const config = window.RASTREMENTO_PREVIEW_DATA;
const $ = id => document.getElementById(id);
const colors = ["#5df4ff", "#ffcf5a", "#ff7b7b", "#66f2a3", "#8ba7ff", "#f48fff"];
const ns = "http://www.w3.org/2000/svg";
const state = {sequence: [], index: 0, selected: null, frame: null, cache: new Map(),
  playing: false, timer: null, request: 0, busy: false, operational: true,
  bounds: {west: -55, east: -36, north: -15, south: -36},
  layers: {radar: true, support: false, cores: true, trends: true, history: false, labels: true}};
const color = id => colors[(id - 1) % colors.length];
const timeLabel = time => time.slice(11, 16);
const statusNames = {birth: "Novo núcleo", overlap: "Sobreposição do núcleo",
  support_translation: "Deslocamento pela área de apoio", history_translation: "Deslocamento pelo histórico"};
const supportConflict = system => state.directionMode === "support_checked" &&
  system.motion_steps?.some(step => step.assessment === "core_translation_conflict");
function directionStatus(system) {
  if (system.trend) return "direção disponível";
  if (supportConflict(system)) return "apoio e núcleo divergem";
  if (system.lineage.split || system.lineage.merge) return "movimento reiniciado";
  if (system.motion) return "direção inconsistente";
  if (system.history_lonlat.length >= 3) return "deslocamento insuficiente";
  return "acumulando histórico";
}
let basemap;

function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(ns, tag);
  Object.entries(attrs).forEach(([name, value]) => node.setAttribute(name, value));
  if (text !== undefined) node.textContent = text;
  return node;
}
function project([lon, lat]) {
  const b = state.bounds;
  return [(lon - b.west) / (b.east - b.west) * 1000,
          (b.north - lat) / (b.north - b.south) * 1000];
}
function path(geometry) {
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  return polygons.map(p => p.map(r => r.map((point, i) => (i ? "L" : "M") +
    project(point).map(v => v.toFixed(2)).join(" ")).join(" ") + "Z").join(" ")).join(" ");
}
function visibleSystems() {
  if (!state.frame) return [];
  const bounds = state.bounds;
  return state.frame.metadata.cores.filter(s => {
    // Include partially visible footprints, even when their centroid is outside.
    const polygons = s.geometry.type === "Polygon" ? [s.geometry.coordinates] : s.geometry.coordinates;
    const coordinates = polygons.flatMap(p => p[0]);
    const extent = coordinates.reduce((b, [lon, lat]) => [Math.min(b[0], lon), Math.min(b[1], lat),
      Math.max(b[2], lon), Math.max(b[3], lat)], [Infinity, Infinity, -Infinity, -Infinity]);
    return extent[2] >= bounds.west && extent[0] <= bounds.east &&
      extent[3] >= bounds.south && extent[1] <= bounds.north &&
      ($("statusFilter").value === "all" || s.status === $("statusFilter").value) &&
      s.intensity_max >= Number($("peakFilter").value);
  }).sort((a, b) => b.intensity_max - a.intensity_max || b.area_km2 - a.area_km2);
}
function select(id) { state.selected = id; render(); }
function renderMap(systems) {
  const root = $("map");
  root.replaceChildren();
  const defs = svg("defs");
  defs.append(svg("marker", {id: "arrow", viewBox: "0 0 10 10", refX: "8", refY: "5",
    markerWidth: "6", markerHeight: "6", orient: "auto"}, undefined));
  defs.firstChild.append(svg("path", {d: "M0 0 L10 5 L0 10Z", fill: "#eef4ff"}));
  root.append(defs);
  basemap.features.forEach(f => root.append(svg("path", {d: path(f.geometry), class: "basemap", "fill-rule": "evenodd"})));
  config.mapLabels.forEach(label => {
    const [x, y] = project([label.lon, label.lat]);
    root.append(svg("text", {x: x + 6, y: y - 5, class: "map-label"}, label.name));
  });
  const {manifest, radarPath} = state.frame;
  if (state.layers.radar) {
    const [[south, west], [north, east]] = manifest.bounds;
    const [x, y] = project([west, north]), [right, bottom] = project([east, south]);
    root.append(svg("image", {href: radarPath, x, y, width: right - x, height: bottom - y, opacity: ".82",
      preserveAspectRatio: "none", "pointer-events": "none"}));
  }
  if (state.layers.support) state.frame.metadata.support_areas.forEach(area =>
    root.append(svg("path", {d: path(area.geometry), class: "core", "fill-rule": "evenodd"})));
  for (const s of systems) {
    const c = color(s.core_id);
    if (state.layers.trends && s.trend) {
      root.append(svg("path", {d: path(s.trend.geometry), class: "trend", fill: c, stroke: c}));
      root.append(svg("polyline", {points: s.trend.axis.coordinates.map(project).map(p => p.join(",")).join(" "),
        fill: "none", stroke: "#eef4ff", "stroke-width": "1.4", "marker-end": "url(#arrow)",
        "vector-effect": "non-scaling-stroke", "pointer-events": "none"}));
    }
    if (state.layers.history && s.history_lonlat.length > 1) {
      root.append(svg("polyline", {points: s.history_lonlat.map(p => project([p.lon, p.lat]).join(",")).join(" "),
        class: "history", stroke: c, "pointer-events": "none"}));
    }
    if (state.layers.cores) {
      const polygon = svg("path", {d: path(s.geometry), class: "system" + (s.track_uid === state.selected ? " selected" : ""),
        fill: c, stroke: c, "fill-rule": "evenodd", tabindex: "0", role: "button",
        "aria-label": "Selecionar núcleo " + s.core_id});
      polygon.addEventListener("click", () => select(s.track_uid));
      polygon.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(s.track_uid); }
      });
      root.append(polygon);
    }
    if (state.layers.labels) {
      const [x, y] = project([s.centroid_lonlat.lon, s.centroid_lonlat.lat]);
      root.append(svg("text", {x: x + 4, y: y - 4, class: "system-label"}, "#" + s.core_id));
    }
  }
}
function render() {
  if (!state.frame) return;
  const systems = visibleSystems(), ids = new Set(systems.map(s => s.track_uid));
  if (!ids.has(state.selected)) state.selected = systems[0]?.track_uid ?? null;
  $("countSystems").textContent = systems.length;
  $("countCores").textContent = systems.filter(c => c.trend).length;
  $("countNew").textContent = systems.filter(s => s.status === "new").length;
  $("peak").textContent = systems.length ? Math.max(...systems.map(s => s.intensity_max)).toFixed(0) : "—";
  $("listCount").textContent = systems.length + " visíveis";
  $("systemList").replaceChildren();
  if (!systems.length) {
    const empty = document.createElement("p");
    empty.textContent = "Nenhum núcleo atende aos filtros neste recorte.";
    $("systemList").append(empty);
  }
  for (const system of systems) {
    const button = document.createElement("button");
    button.className = "system-card" + (system.track_uid === state.selected ? " selected" : "");
    button.setAttribute("aria-pressed", String(system.track_uid === state.selected));
    const formattedArea = system.area_km2.toLocaleString("pt-BR", {maximumFractionDigits: 0});
    button.innerHTML = `<span class="id-dot" style="background:${color(system.core_id)}"></span>
      <span><strong>Núcleo #${system.core_id}</strong><small>${formattedArea} km² · ${directionStatus(system)}</small></span>
      <span class="peak">${system.intensity_max.toFixed(0)} dBZ<small>${system.status === "new" ? "novo núcleo" : "ID mantido"}</small></span>`;
    button.addEventListener("click", () => select(system.track_uid));
    $("systemList").append(button);
  }
  renderMap(systems);
  renderDetail(systems.find(s => s.track_uid === state.selected));
}
function renderDetail(system) {
  if (!system) {
    $("detail").innerHTML = '<div class="section-title">Sem núcleo no recorte</div><p>Ajuste os filtros ou avance o horário.</p>';
    return;
  }
  const motion = system.motion, lineage = system.lineage;
  const detail = (label, value) => `<div><dt>${label}</dt><dd>${value}</dd></div>`;
  const event = lineage.merge ? "Fusão" : lineage.split ? "Divisão" : "Continuidade";
  $("detail").innerHTML = `<div class="section-title">Núcleo #${system.core_id}<small>${event}</small></div>
    <dl class="details">${detail("Associação", statusNames[system.association.method])}
    ${detail("Sobreposição", system.association.overlap === null ? "—" : (100 * system.association.overlap).toFixed(0) + "%")}
    ${detail("Área do núcleo", system.area_km2.toLocaleString("pt-BR", {maximumFractionDigits: 0}) + " km²")}
    ${detail("Observações na trajetória", system.history_lonlat.length)}
    ${detail("Direção média", motion ? motion.bearing_deg.toFixed(0) + "°" : "—")}
    ${detail("Velocidade estimada", motion ? motion.speed_kmh.toFixed(1) + " km/h" : "—")}
    ${detail("Fonte dos dois vetores", motion ? (motion.interval_sources || ["centroid", "centroid"]).map(source =>
      source === "support_translation" ? "área de apoio" : "centro do núcleo").join(" / ") : "—")}
    ${detail("Comprimento do trapézio", system.trend ? system.trend.length_km.toFixed(0) + " km" : "—")}
    ${detail("Larguras: base / frente", system.trend ? system.trend.near_width_km + " / " + system.trend.far_width_km + " km" : "—")}
    ${detail("Intervalos observados", motion ? motion.observed_intervals : "0")}</dl>
    <p class="motion-note">${system.trend ?
      `Trapézio com dimensões fixas. Só a posição e a direção mudam; direção calculada com t, t−1 e t−2. A área de ${state.frame.metadata.thresholds_dbz.storm} dBZ auxilia o deslocamento. Sem horizonte de previsão.` :
      lineage.split || lineage.merge ? "Movimento reiniciado após divisão ou fusão do núcleo. São necessárias três novas observações." :
      supportConflict(system) ? "O deslocamento do apoio não acompanha o núcleo. Trapézio oculto enquanto houver conflito nos dois intervalos recentes." :
      motion ? "Direção pouco consistente; trapézio oculto até o movimento estabilizar." :
      system.history_lonlat.length >= 3 ? "O deslocamento recente é insuficiente para publicar uma direção." :
      "São necessárias três observações e deslocamento suficiente para estimar a direção."}</p>`;
}
async function loadFrame(entry) {
  if (state.cache.has(entry.frame.frame_name)) return state.cache.get(entry.frame.frame_name);
  const timestamp = entry.frame.timestamp;
  const slug = timestamp.slice(0, 10).replaceAll("-", "") + "_" + timestamp.slice(11, 16).replace(":", "");
  const folder = config.sequencePath.slice(0, config.sequencePath.lastIndexOf("/") + 1) + slug;
  const data = await Promise.all(["metadata.json", "manifest.json"].map(async file => {
    const response = await fetch(folder + "/" + file);
    if (!response.ok) throw new Error("Produto indisponível: " + file);
    return response.json();
  }));
  if (data[0].processing_mode !== "cores") {
    throw new Error("Este horário ainda usa o formato anterior. Aguarde o processamento dos núcleos e recarregue.");
  }
  const frame = {metadata: data[0], manifest: data[1], radarPath: folder + "/radar.png"};
  state.cache.set(entry.frame.frame_name, frame);
  return frame;
}
async function setFrame(index) {
  if (!state.sequence.length) return;
  const request = ++state.request;
  state.busy = true;
  state.index = (index + state.sequence.length) % state.sequence.length;
  $("timeline").value = state.index;
  $("status").textContent = "Carregando horário…";
  try {
    const entry = state.sequence[state.index], data = await loadFrame(entry);
    if (request !== state.request) return;
    state.frame = data;
    $("timestamp").textContent = entry.frame.timestamp.replace("T", " ").slice(0, 16);
    $("date").textContent = entry.frame.timestamp.slice(0, 10).split("-").reverse().join("/");
    $("currentTime").textContent = timeLabel(entry.frame.timestamp);
    $("frameCounter").textContent = `${state.index + 1} / ${state.sequence.length}`;
    render();
    $("status").classList.remove("error");
    $("status").textContent = state.playing ? "Reproduzindo observações" : "Observação carregada · Rainviewer";
    const next = state.sequence[(state.index + 1) % state.sequence.length];
    loadFrame(next).catch(() => {});
  } catch (error) {
    if (request === state.request) {
      stop(); $("status").classList.add("error"); $("status").textContent = error.message;
    }
  } finally { if (request === state.request) state.busy = false; }
}
function stop() {
  state.playing = false; clearTimeout(state.timer); state.timer = null;
  $("play").textContent = "▶"; $("play").setAttribute("aria-label", "Reproduzir sequência");
}
async function tick() {
  if (!state.playing) return;
  if (!state.busy) await setFrame(state.index + 1);
  if (state.playing) state.timer = setTimeout(tick, config.playbackIntervalMs);
}
$("play").addEventListener("click", () => {
  if (state.playing) { stop(); $("status").textContent = "Reprodução pausada"; }
  else {
    state.playing = true; $("play").textContent = "Ⅱ"; $("play").setAttribute("aria-label", "Pausar sequência");
    tick();
  }
});
$("prev").addEventListener("click", () => { stop(); setFrame(state.index - 1); });
$("next").addEventListener("click", () => { stop(); setFrame(state.index + 1); });
$("timeline").addEventListener("input", () => { stop(); setFrame(Number($("timeline").value)); });
["statusFilter", "peakFilter"].forEach(id => $(id).addEventListener("change", render));
document.querySelectorAll("[data-layer]").forEach(button => button.addEventListener("click", () => {
  const layer = button.dataset.layer; state.layers[layer] = !state.layers[layer];
  button.setAttribute("aria-pressed", String(state.layers[layer])); render();
}));
function zoom(factor) {
  const b = state.bounds, centerLon = (b.east + b.west) / 2, centerLat = (b.north + b.south) / 2;
  const lonSpan = Math.max(1, Math.min(80, (b.east - b.west) * factor));
  const latSpan = Math.max(1, Math.min(80, (b.north - b.south) * factor));
  state.bounds = {west: centerLon - lonSpan / 2, east: centerLon + lonSpan / 2,
                  south: centerLat - latSpan / 2, north: centerLat + latSpan / 2};
  render();
}
$("zoomIn").addEventListener("click", () => zoom(.75));
$("zoomOut").addEventListener("click", () => zoom(1.333333));
$("focus").addEventListener("click", () => {
  state.operational = !state.operational;
  state.bounds = state.operational ? {west: -55, east: -36, north: -15, south: -36} : {...config.mapBounds};
  $("focus").textContent = state.operational ? "Recorte" : "Brasil"; render();
});
async function init() {
  const [mapResponse, summaryResponse] = await Promise.all([fetch(config.basemapPath), fetch(config.sequencePath)]);
  if (!mapResponse.ok || !summaryResponse.ok) throw new Error("Sequência indisponível. Execute Processar Radar.");
  basemap = await mapResponse.json();
  const summary = await summaryResponse.json(); state.sequence = summary.frames;
  state.directionMode = summary.config.direction_mode || "centroid";
  document.querySelector('[data-layer="support"]').textContent = `Apoio ${summary.config.storm_threshold} dBZ`;
  $("supportDescription").textContent = `O ID e o polígono acompanham o núcleo intenso. A área de ${summary.config.storm_threshold} dBZ auxilia a estimativa de deslocamento.`;
  if (!state.sequence.length) throw new Error("Sequência sem observações.");
  $("timeline").max = state.sequence.length - 1;
  $("firstTime").textContent = timeLabel(state.sequence[0].frame.timestamp);
  $("lastTime").textContent = timeLabel(state.sequence.at(-1).frame.timestamp);
  const index = state.sequence.findIndex(entry => entry.frame.frame_name === config.initialFrameName);
  await setFrame(index >= 0 ? index : 0);
}
init().catch(error => { $("status").textContent = error.message; $("status").classList.add("error"); });
