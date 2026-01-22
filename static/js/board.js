// Simple dartboard canvas mapping. Emits global `onDartHit` when user clicks.
// Sector order clockwise starting at top (20) is:
const SECTOR_ORDER = [
  20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5,
];

const canvas = document.getElementById("board");
const ctx = canvas.getContext("2d");
let W = canvas.width,
  H = canvas.height;
let cx = W / 2,
  cy = H / 2;
// shrink board so numbers and outer ring fit
let outerRadius = (Math.min(W, H) / 2) * 0.78;
let tripleInner = outerRadius * 0.58;
let tripleOuter = outerRadius * 0.66;
let doubleInner = outerRadius * 0.88;
let doubleOuter = outerRadius;
let bullOuter = outerRadius * 0.06;
let bullInner = outerRadius * 0.03;
// bigger outer miss ring
let OUTER_MISS_RING = outerRadius * 1.35;

let markerLayer = []; // {value,multiplier,label, color, x:pixel, y:pixel, x_norm, y_norm}

/* Compute geometric sizes based on current canvas size */
function computeSizes() {
  W = canvas.width;
  H = canvas.height;
  cx = W / 2;
  cy = H / 2;
  outerRadius = (Math.min(W, H) / 2) * 0.78;
  tripleInner = outerRadius * 0.58;
  tripleOuter = outerRadius * 0.66;
  doubleInner = outerRadius * 0.88;
  doubleOuter = outerRadius;
  bullOuter = outerRadius * 0.06;
  bullInner = outerRadius * 0.03;
  OUTER_MISS_RING = outerRadius * 1.35;
}

/* Draw the dartboard */
function drawBoard() {
  computeSizes();
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#071826";
  ctx.fillRect(0, 0, W, H);

  const slice = (Math.PI * 2) / 20; // wedge angle
  for (let i = 0; i < 20; i++) {
    const start = i * slice - Math.PI / 2 - slice / 2;
    const end = start + slice;
    // base single areas
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, doubleOuter, start, end);
    ctx.closePath();
    ctx.fillStyle = i % 2 == 0 ? "#0b1220" : "#f5f1dc";
    ctx.fill();
    // triple ring
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, tripleOuter, start, end);
    ctx.arc(cx, cy, tripleInner, end, start, true);
    ctx.closePath();
    ctx.fillStyle = i % 2 == 0 ? "#c00" : "#006400";
    ctx.fill();
    // double ring
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, doubleOuter, start, end);
    ctx.arc(cx, cy, doubleInner, end, start, true);
    ctx.closePath();
    ctx.fillStyle = i % 2 == 0 ? "#c00" : "#006400";
    ctx.fill();
    // number ring
    const mid = (start + end) / 2;
    const numRadius = outerRadius + Math.max(14, W * 0.028);
    const tx = cx + Math.cos(mid) * numRadius;
    const ty = cy + Math.sin(mid) * numRadius;
    const padX = Math.max(6, Math.floor(W / 140));
    const padY = Math.max(4, Math.floor(W / 320));
    const text = String(SECTOR_ORDER[i]);
    ctx.font = `${Math.max(12, Math.floor(W / 38))}px sans-serif`;
    const metrics = ctx.measureText(text);
    const textW = metrics.width;
    const rectW = textW + padX * 2;
    const rectH = Math.max(16, Math.floor(W / 46)) + padY;
    const rx = tx - rectW / 2;
    const ry = ty - rectH / 2;
    const rRadius = Math.min(8, rectH / 2);
    ctx.beginPath();
    roundedRect(ctx, rx, ry, rectW, rectH, rRadius);
    ctx.fillStyle = "rgba(0,0,0,0.6)";
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.03)";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = "#f3f7fb";
    ctx.shadowColor = "rgba(0,0,0,0.9)";
    ctx.shadowBlur = 6;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, tx, ty);
    ctx.shadowBlur = 0;
  }
  // bull
  ctx.beginPath();
  ctx.arc(cx, cy, bullOuter, 0, Math.PI * 2);
  ctx.fillStyle = "#006400";
  ctx.fill();
  ctx.beginPath();
  ctx.arc(cx, cy, bullInner, 0, Math.PI * 2);
  ctx.fillStyle = "#c00";
  ctx.fill();

  // outer miss ring
  ctx.beginPath();
  ctx.arc(cx, cy, OUTER_MISS_RING, 0, Math.PI * 2);
  ctx.strokeStyle = "rgba(255,255,255,0.06)";
  ctx.lineWidth = Math.max(3, Math.floor(W / 160));
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(cx, cy, OUTER_MISS_RING, 0, Math.PI * 2);
  ctx.strokeStyle = "rgba(255,255,50,0.025)";
  ctx.lineWidth = Math.max(12, Math.floor(W / 60));
  ctx.stroke();
}

/* helper to draw rounded rectangle */
function roundedRect(ctx, x, y, width, height, radius) {
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + width, y, x + width, y + height, radius);
  ctx.arcTo(x + width, y + height, x, y + height, radius);
  ctx.arcTo(x, y + height, x, y, radius);
  ctx.arcTo(x, y, x + width, y, radius);
  ctx.closePath();
}

/* Given a hit, compute pixel coordinates. If x_norm/y_norm provided, use that (exact clicked position). */
function hitToCoord(hit) {
  computeSizes();
  if (typeof hit.x === "number" && typeof hit.y === "number") {
    // server stored normalized coords: convert to pixels
    if (hit.x <= 1 && hit.y <= 1) {
      return { x: hit.x * canvas.width, y: hit.y * canvas.height };
    }
    // fallback: assume already pixel coords
    return { x: hit.x, y: hit.y };
  }

  if (hit.value === 25) {
    return { x: cx, y: cy };
  }
  const slice = (Math.PI * 2) / 20;
  const idx = SECTOR_ORDER.indexOf(hit.value);
  const start = idx * slice - Math.PI / 2 - slice / 2;
  const mid = start + slice / 2;
  let r;
  if (hit.multiplier === 3) {
    r = (tripleInner + tripleOuter) / 2;
  } else if (hit.multiplier === 2) {
    r = (doubleInner + doubleOuter) / 2;
  } else if (hit.multiplier === 1) {
    r = (tripleOuter + doubleInner) / 2 - outerRadius * 0.03;
  } else {
    r = OUTER_MISS_RING;
  }
  const x = cx + Math.cos(mid) * r;
  const y = cy + Math.sin(mid) * r;
  return { x, y };
}

/* Draw markers (array of hits) */
function drawMarkersLayer() {
  markerLayer.forEach((m) => {
    const coord = hitToCoord(m);
    const radius = Math.max(6, Math.floor(canvas.width / 80));
    ctx.beginPath();
    ctx.arc(coord.x, coord.y, radius, 0, Math.PI * 2);
    ctx.fillStyle = m.color || "rgba(255,255,50,0.95)";
    ctx.save();
    ctx.shadowColor = m.color || "rgba(255,255,50,0.95)";
    ctx.shadowBlur = 12;
    ctx.fill();
    ctx.restore();
    ctx.beginPath();
    ctx.arc(coord.x, coord.y, radius, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(0,0,0,0.6)";
    ctx.lineWidth = 1;
    ctx.stroke();
  });
}

/* clear and redraw board + markers */
function redrawAll() {
  drawBoard();
  drawMarkersLayer();
}

window.redrawBoard = redrawAll;

/* Show markers for hits; hits can include x,y normalized (0..1) or not */
window.showMarkers = function (hits, color = "rgba(255,255,50,0.95)") {
  markerLayer = (hits || []).map((h) => {
    const copy = {
      value: h.value,
      multiplier: h.multiplier,
      label: h.label,
      color,
    };
    if (h.x !== undefined && h.x !== null) ((copy.x = h.x), (copy.y = h.y));
    return copy;
  });
  redrawAll();
};

window.clearMarkers = function () {
  markerLayer = [];
  redrawAll();
};

// initial draw
drawBoard();

/* Click handling */
canvas.addEventListener("click", function (e) {
  const rect = canvas.getBoundingClientRect();
  const pixelX = e.clientX - rect.left;
  const pixelY = e.clientY - rect.top;
  const dx = pixelX - cx;
  const dy = pixelY - cy;
  const r = Math.sqrt(dx * dx + dy * dy);
  const ang = Math.atan2(dy, dx);
  let normalized = ang + Math.PI / 2;
  if (normalized < 0) normalized += Math.PI * 2;

  const slice = (Math.PI * 2) / 20;
  let adjusted = normalized + slice / 2;
  if (adjusted >= Math.PI * 2) adjusted -= Math.PI * 2;
  const sectorIdx = Math.floor((adjusted / (Math.PI * 2)) * 20);
  let sector = SECTOR_ORDER[sectorIdx % 20];
  let multiplier = 1;
  let label = `S${sector}`;

  if (r > OUTER_MISS_RING) {
    return;
  }

  if (r > outerRadius && r <= OUTER_MISS_RING) {
    multiplier = 0;
    label = "OUT";
    sector = 0;
  } else if (r <= bullInner) {
    multiplier = 2;
    label = "BULL";
    sector = 25;
  } else if (r <= bullOuter) {
    multiplier = 1;
    label = "SBULL";
    sector = 25;
  } else if (r >= tripleInner && r <= tripleOuter) {
    multiplier = 3;
    label = `T${sector}`;
  } else if (r >= doubleInner && r <= doubleOuter) {
    multiplier = 2;
    label = `D${sector}`;
  } else {
    multiplier = 1;
    label = `S${sector}`;
  }

  // normalized coordinates (0..1)
  const x_norm = pixelX / canvas.width;
  const y_norm = pixelY / canvas.height;

  if (typeof window.onDartHit === "function") {
    window.onDartHit({
      value: sector,
      multiplier: multiplier,
      label: label,
      x: x_norm,
      y: y_norm,
      px: pixelX,
      py: pixelY,
    });
  }
});
