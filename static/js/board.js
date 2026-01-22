/*
darts4you/static/js/board.js

Dartboard canvas with:
 - animated zoom-to-bull centered transform
 - transform-aware click mapping (so normalized coords are correct under zoom)
 - bulls-decider canvas overlay with Zoom / Cancel controls
 - animated markers for darts and bulls-decider clicks
 - optional click sound (uses WebAudio when available)
 - compatibility helper: only defines window.resizeBoard if not provided by template

This file is self-contained and intended to be loaded before or after the page-level JS.
If the page supplies its own `resizeBoard` function, that will be used; otherwise this file
defines a fallback `window.resizeBoard` so the board sizes/initializes correctly.
*/

// Sector order clockwise starting at top (20)
const SECTOR_ORDER = [
  20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5,
];

const canvas = document.getElementById("board");
const ctx = canvas && canvas.getContext ? canvas.getContext("2d") : null;

let W = (canvas && canvas.width) || 640;
let H = (canvas && canvas.height) || 640;
let cx = W / 2;
let cy = H / 2;

// geometric sizes (computed based on canvas size)
let outerRadius = (Math.min(W, H) / 2) * 0.78;
let tripleInner = outerRadius * 0.58;
let tripleOuter = outerRadius * 0.66;
let doubleInner = outerRadius * 0.88;
let doubleOuter = outerRadius;
let bullOuter = outerRadius * 0.06;
let bullInner = outerRadius * 0.03;
let OUTER_MISS_RING = outerRadius * 1.35;

// Marker layers
let markerLayer = []; // official play markers (animated objects)
let bullsMarkers = []; // bulls-decider markers (animated objects)

// Zoom / view state
const zoomState = {
  scale: 1,
  centerX: 0.5,
  centerY: 0.5,
  animStart: null,
  animDuration: 300,
  animFromScale: 1,
  animFromCX: 0.5,
  animFromCY: 0.5,
  targetScale: 1,
  targetCX: 0.5,
  targetCY: 0.5,
};

// Bulls-decider overlay state
let bullsDeciderActive = false;
let bullsDeciderOrder = []; // [{id,name},...]
let bullsDeciderCurrent = 0;
let bullsDeciderZoomed = false; // currently zoomed due to decider
let bullsDeciderOverlayVisible = null; // overlay hit bounds when drawn

// Audio (lazy)
let _audioCtx = null;
function ensureAudioContext() {
  if (_audioCtx) return;
  try {
    _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  } catch (e) {
    _audioCtx = null;
  }
}
function playClickSoundSimple(freq = 880, dur = 0.12) {
  // best-effort, fails silently if AudioContext not available or blocked
  try {
    ensureAudioContext();
    if (!_audioCtx) return;
    const now = _audioCtx.currentTime;
    const o = _audioCtx.createOscillator();
    const g = _audioCtx.createGain();
    o.type = "sine";
    o.frequency.setValueAtTime(freq, now);
    g.gain.setValueAtTime(0.0001, now);
    g.gain.exponentialRampToValueAtTime(0.12, now + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, now + dur);
    o.connect(g);
    g.connect(_audioCtx.destination);
    o.start(now);
    o.stop(now + dur + 0.02);
  } catch (e) {
    // ignore
  }
}

/* Compute geometric sizes based on canvas size */
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

/* Rounded rectangle helper */
function roundedRect(ctxLocal, x, y, width, height, radius) {
  ctxLocal.beginPath();
  ctxLocal.moveTo(x + radius, y);
  ctxLocal.arcTo(x + width, y, x + width, y + height, radius);
  ctxLocal.arcTo(x + width, y + height, x, y + height, radius);
  ctxLocal.arcTo(x, y + height, x, y, radius);
  ctxLocal.arcTo(x, y, x + width, y, radius);
  ctxLocal.closePath();
}

/* Apply current zoom transform to ctx. */
function applyZoomTransform() {
  if (!ctx) return;
  if (!zoomState || zoomState.scale === 1) return;
  const zx = zoomState.centerX * canvas.width;
  const zy = zoomState.centerY * canvas.height;
  ctx.translate(zx, zy);
  ctx.scale(zoomState.scale, zoomState.scale);
  ctx.translate(-zx, -zy);
}

/* Animate zoom state to target center/scale */
function animateZoomTo(centerX, centerY, targetScale, duration = 300) {
  centerX = Math.max(0, Math.min(1, centerX));
  centerY = Math.max(0, Math.min(1, centerY));
  targetScale = Math.max(1, targetScale || 1);
  duration = Math.max(0, duration || 0);

  const now = performance.now();
  // capture start values
  zoomState.animStart = now;
  zoomState.animDuration = duration;
  zoomState.animFromScale = zoomState.scale;
  zoomState.animFromCX = zoomState.centerX;
  zoomState.animFromCY = zoomState.centerY;
  zoomState.targetScale = targetScale;
  zoomState.targetCX = centerX;
  zoomState.targetCY = centerY;

  if (duration === 0) {
    zoomState.scale = targetScale;
    zoomState.centerX = centerX;
    zoomState.centerY = centerY;
    zoomState.animStart = null;
    redrawAll();
    return;
  }

  function step(ts) {
    const start = zoomState.animStart;
    const dur = zoomState.animDuration;
    const t = Math.min(1, (ts - start) / dur);
    const ease = 1 - Math.pow(1 - t, 3); // cubic ease out
    zoomState.scale =
      zoomState.animFromScale +
      (zoomState.targetScale - zoomState.animFromScale) * ease;
    zoomState.centerX =
      zoomState.animFromCX + (zoomState.targetCX - zoomState.animFromCX) * ease;
    zoomState.centerY =
      zoomState.animFromCY + (zoomState.targetCY - zoomState.animFromCY) * ease;
    redrawAll();
    if (t < 1) requestAnimationFrame(step);
    else zoomState.animStart = null;
  }
  requestAnimationFrame(step);
}

/* Draw the dartboard */
function drawBoard() {
  if (!ctx) return;
  computeSizes();
  ctx.save();
  ctx.clearRect(0, 0, W, H);

  // Background
  ctx.fillStyle = "#071826";
  ctx.fillRect(0, 0, W, H);

  // Apply zoom transform for board drawing & markers
  applyZoomTransform();

  const slice = (Math.PI * 2) / 20;
  for (let i = 0; i < 20; i++) {
    const start = i * slice - Math.PI / 2 - slice / 2;
    const end = start + slice;

    // base singles area
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, doubleOuter, start, end);
    ctx.closePath();
    ctx.fillStyle = i % 2 === 0 ? "#0b1220" : "#f5f1dc";
    ctx.fill();

    // triple ring
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, tripleOuter, start, end);
    ctx.arc(cx, cy, tripleInner, end, start, true);
    ctx.closePath();
    ctx.fillStyle = i % 2 === 0 ? "#c00" : "#006400";
    ctx.fill();

    // double ring
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, doubleOuter, start, end);
    ctx.arc(cx, cy, doubleInner, end, start, true);
    ctx.closePath();
    ctx.fillStyle = i % 2 === 0 ? "#c00" : "#006400";
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

  // outer miss rings
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

  // markers (drawn under zoom transform)
  drawMarkersLayer();

  ctx.restore(); // back to screen coordinates

  // overlay (zoom hint + bulls decider info) drawn in screen coords
  drawBullsOverlay();
}

/* Map stored hit data (value/multiplier or normalized coords) to pixel coords in logical canvas coordinates */
function hitToCoord(hit) {
  computeSizes();
  // If stored normalized coords (0..1)
  if (typeof hit.x === "number" && typeof hit.y === "number") {
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
  if (hit.multiplier === 3) r = (tripleInner + tripleOuter) / 2;
  else if (hit.multiplier === 2) r = (doubleInner + doubleOuter) / 2;
  else if (hit.multiplier === 1)
    r = (tripleOuter + doubleInner) / 2 - outerRadius * 0.03;
  else r = OUTER_MISS_RING;
  const x = cx + Math.cos(mid) * r;
  const y = cy + Math.sin(mid) * r;
  return { x, y };
}

/* Draw markers: both official game markers and bulls-decider markers.
   Markers support a short pulse animation when created; animation driven by presence of animStart/animDur on marker objects.
*/
function drawMarkersLayer() {
  if (!ctx) return;
  const now = performance.now();

  // Official markers (played darts)
  for (let i = 0; i < markerLayer.length; i++) {
    const m = markerLayer[i];
    const coord = hitToCoord(m);
    const baseRadius = Math.max(6, Math.floor(canvas.width / 80));
    const t = m.animStart
      ? Math.min(1, (now - m.animStart) / (m.animDur || 380))
      : 1;
    const ease = 1 - Math.pow(1 - t, 2);
    const radius = baseRadius * (1 + 0.6 * (1 - ease));
    ctx.beginPath();
    ctx.arc(coord.x, coord.y, radius, 0, Math.PI * 2);
    ctx.fillStyle = m.color || "rgba(255,255,50,0.95)";
    ctx.save();
    ctx.shadowColor = m.color || "rgba(255,255,50,0.95)";
    ctx.shadowBlur = Math.floor(12 * (1 - ease) + 2);
    ctx.globalAlpha = 0.9 + 0.1 * (1 - ease);
    ctx.fill();
    ctx.restore();
    ctx.beginPath();
    ctx.arc(coord.x, coord.y, Math.max(2, Math.floor(radius)), 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(0,0,0,0.6)";
    ctx.lineWidth = 1;
    ctx.stroke();
    // Remove animation metadata once finished to free fields
    if (m.animStart && t >= 1) {
      delete m.animStart;
      delete m.animDur;
    }
  }

  // Bulls-decider markers (distinct color)
  for (let j = 0; j < bullsMarkers.length; j++) {
    const m = bullsMarkers[j];
    const coord = hitToCoord(m);
    const baseRadius = Math.max(5, Math.floor(canvas.width / 110));
    const t = m.animStart
      ? Math.min(1, (now - m.animStart) / (m.animDur || 420))
      : 1;
    const ease = 1 - Math.pow(1 - t, 2);
    const radius = baseRadius * (1 + 0.8 * (1 - ease));
    ctx.beginPath();
    ctx.arc(coord.x, coord.y, radius, 0, Math.PI * 2);
    ctx.fillStyle = m.color || "rgba(32,201,151,0.95)";
    ctx.save();
    ctx.shadowColor = "rgba(32,201,151,0.95)";
    ctx.shadowBlur = Math.floor(18 * (1 - ease) + 2);
    ctx.globalAlpha = 0.95;
    ctx.fill();
    ctx.restore();
    ctx.beginPath();
    ctx.arc(coord.x, coord.y, Math.max(2, Math.floor(radius)), 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(0,0,0,0.6)";
    ctx.lineWidth = 1;
    ctx.stroke();
    if (m.animStart && t >= 1) {
      delete m.animStart;
      delete m.animDur;
    }
  }
}

/* Draw bulls-decider overlay in screen coordinates (non-transformed) */
function drawBullsOverlay() {
  if (!ctx) return;
  // show overlay if decider is active OR if zoom is active (so user sees hint)
  if (!bullsDeciderActive && zoomState.scale === 1) {
    bullsDeciderOverlayVisible = null;
    return;
  }

  const padding = 10;
  const boxW = 260;
  const boxH = 86;
  const x = canvas.width - boxW - 12;
  const y = 12;

  ctx.save();
  ctx.globalAlpha = 0.95;
  ctx.fillStyle = "rgba(6,20,27,0.92)";
  roundedRect(ctx, x, y, boxW, boxH, 8);
  ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.04)";
  ctx.lineWidth = 1;
  ctx.stroke();

  ctx.fillStyle = "#e6eef6";
  ctx.font = "14px sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";

  const title = bullsDeciderActive
    ? bullsDeciderZoomed
      ? "Bulls decider active"
      : "Bulls decider (paused zoom)"
    : "Zoomed";
  ctx.fillText(title, x + padding, y + 8);

  if (bullsDeciderActive && bullsDeciderOrder && bullsDeciderOrder.length) {
    const idx = Math.max(
      0,
      Math.min(bullsDeciderOrder.length - 1, bullsDeciderCurrent),
    );
    const cur = bullsDeciderOrder[idx] || {};
    const name = cur.name || "-";
    ctx.fillStyle = "rgba(226,238,246,0.95)";
    ctx.font = "12px sans-serif";
    ctx.fillText(`Player to click: ${name}`, x + padding, y + 34);
  } else {
    ctx.fillStyle = "rgba(226,238,246,0.9)";
    ctx.font = "12px sans-serif";
    ctx.fillText("Click board to set bulls", x + padding, y + 34);
  }

  // Buttons (Zoom / Cancel)
  const btnH = 30;
  const btnW = 82;
  const gap = 8;
  const btnZoomX = x + boxW - padding - btnW * 2 - gap;
  const btnCancelX = x + boxW - padding - btnW;
  const btnY = y + boxH - padding - btnH;

  // Zoom button
  ctx.fillStyle = "rgba(32,201,151,0.12)";
  roundedRect(ctx, btnZoomX, btnY, btnW, btnH, 6);
  ctx.fill();
  ctx.fillStyle = "rgba(32,201,151,0.95)";
  ctx.font = "12px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const zoomLabel = bullsDeciderZoomed ? "Zoom" : "Re-zoom";
  ctx.fillText(zoomLabel, btnZoomX + btnW / 2, btnY + btnH / 2);

  // Cancel button
  ctx.fillStyle = "rgba(255,90,90,0.12)";
  roundedRect(ctx, btnCancelX, btnY, btnW, btnH, 6);
  ctx.fill();
  ctx.fillStyle = "rgba(255,90,90,0.95)";
  ctx.fillText("Cancel", btnCancelX + btnW / 2, btnY + btnH / 2);

  // Expose overlay bounds for click detection
  bullsDeciderOverlayVisible = {
    x,
    y,
    w: boxW,
    h: boxH,
    btnZoom: { x: btnZoomX, y: btnY, w: btnW, h: btnH },
    btnCancel: { x: btnCancelX, y: btnY, w: btnW, h: btnH },
  };

  ctx.restore();
}

/* Redraw convenience */
function redrawAll() {
  drawBoard();
}

/* Exposed API: showMarkers / clearMarkers */
window.showMarkers = function (hits, color = "rgba(255,255,50,0.95)") {
  const now = performance.now();
  markerLayer = (hits || []).map((h) => {
    const copy = {
      value: h.value,
      multiplier: h.multiplier,
      label: h.label,
      color,
      x: h.x,
      y: h.y,
      animStart: now,
      animDur: 380,
    };
    return copy;
  });
  try {
    playClickSoundSimple(880, 0.12);
  } catch (e) {}
  redrawAll();
};

window.clearMarkers = function () {
  markerLayer = [];
  redrawAll();
};

/* Zoom API */
function setZoomAnimated(centerX, centerY, targetScale, duration = 300) {
  animateZoomTo(centerX, centerY, targetScale, duration);
}
window.zoomTo = function (centerX, centerY, scale, duration) {
  setZoomAnimated(centerX, centerY, scale, duration || 300);
};
window.zoomToBull = function (scale = 3, duration = 300) {
  const clamped = Math.max(1, Math.min(6, scale));
  setZoomAnimated(0.5, 0.5, clamped, duration);
};
window.resetZoom = function (duration = 300) {
  setZoomAnimated(0.5, 0.5, 1, duration);
};

/* Bulls-decider visual helpers */
window.bullsDeciderStartVisual = function (order) {
  bullsDeciderActive = true;
  bullsDeciderZoomed = true;
  bullsDeciderOrder = (order || []).map((o) => ({ id: o.id, name: o.name }));
  bullsDeciderCurrent = 0;
  bullsMarkers = [];
  try {
    window.zoomToBull(3, 350);
  } catch (e) {}
  redrawAll();
};
window.bullsDeciderSetCurrent = function (idx) {
  bullsDeciderCurrent = idx || 0;
  redrawAll();
};
window.bullsDeciderAddMarker = function (x_norm, y_norm, player_id) {
  const now = performance.now();
  const marker = {
    x: x_norm,
    y: y_norm,
    color: "rgba(32,201,151,0.95)",
    value: 0,
    multiplier: 0,
    animStart: now,
    animDur: 420,
  };
  bullsMarkers.push(marker);
  try {
    playClickSoundSimple(720, 0.12);
  } catch (e) {}
  redrawAll();
};
window.bullsDeciderEnd = function () {
  bullsDeciderActive = false;
  bullsDeciderOrder = [];
  bullsDeciderCurrent = 0;
  bullsMarkers = [];
  bullsDeciderZoomed = false;
  try {
    window.resetZoom(250);
  } catch (e) {}
  redrawAll();
};
window.bullsDeciderTempUnzoom = function () {
  if (!bullsDeciderActive) return;
  bullsDeciderZoomed = false;
  try {
    window.resetZoom(200);
  } catch (e) {}
  redrawAll();
};
window.bullsDeciderRezoom = function (scale = 3, duration = 300) {
  if (!bullsDeciderActive) return;
  bullsDeciderZoomed = true;
  try {
    window.zoomToBull(scale, duration);
  } catch (e) {}
};
window.bullsDeciderTogglePauseZoom = function () {
  if (!bullsDeciderActive) return;
  if (bullsDeciderZoomed) {
    bullsDeciderZoomed = false;
    try {
      window.resetZoom(220);
    } catch (e) {}
  } else {
    bullsDeciderZoomed = true;
    try {
      window.zoomToBull(3, 300);
    } catch (e) {}
  }
  redrawAll();
};
window.bullsDeciderCancel = function () {
  // Full cancel => end visuals
  window.bullsDeciderEnd();
};

/* Click handling:
   - If overlay buttons clicked, handle them
   - Otherwise map screen coords to logical coords using inverse transform and compute sector
*/
if (canvas) {
  canvas.addEventListener("click", function (e) {
    const rect = canvas.getBoundingClientRect();
    const pixelX = e.clientX - rect.left;
    const pixelY = e.clientY - rect.top;

    // If overlay visible, check buttons first (screen coords)
    if (bullsDeciderOverlayVisible) {
      const ov = bullsDeciderOverlayVisible;
      const bz = ov.btnZoom;
      if (
        pixelX >= bz.x &&
        pixelX <= bz.x + bz.w &&
        pixelY >= bz.y &&
        pixelY <= bz.y + bz.h
      ) {
        if (bullsDeciderActive) {
          // toggle pause (temp unzoom / re-zoom)
          window.bullsDeciderTogglePauseZoom();
        } else {
          try {
            window.zoomToBull(3, 300);
          } catch (e) {}
        }
        return;
      }
      const bc = ov.btnCancel;
      if (
        pixelX >= bc.x &&
        pixelX <= bc.x + bc.w &&
        pixelY >= bc.y &&
        pixelY <= bc.y + bc.h
      ) {
        if (bullsDeciderActive) {
          // temporary unzoom (pause) rather than full cancellation
          window.bullsDeciderTempUnzoom();
        } else {
          window.bullsDeciderCancel();
        }
        return;
      }
    }

    // Map screen pixel coords back to logical (inverse of transform)
    let logicalX = pixelX;
    let logicalY = pixelY;
    if (zoomState && zoomState.scale && zoomState.scale !== 1) {
      const zx = zoomState.centerX * canvas.width;
      const zy = zoomState.centerY * canvas.height;
      logicalX = zx + (pixelX - zx) / zoomState.scale;
      logicalY = zy + (pixelY - zy) / zoomState.scale;
    }

    const dx = logicalX - cx;
    const dy = logicalY - cy;
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
      // outside active area -> ignore
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

    const x_norm = logicalX / canvas.width;
    const y_norm = logicalY / canvas.height;

    // audio + visual feedback for every dart click
    try {
      playClickSoundSimple(750, 0.12);
    } catch (e) {}

    // If decider active, add to bullsMarkers (we also call bullsDeciderAddMarker elsewhere)
    if (bullsDeciderActive) {
      const now = performance.now();
      bullsMarkers.push({
        x: x_norm,
        y: y_norm,
        color: "rgba(32,201,151,0.95)",
        value: sector,
        multiplier,
        animStart: now,
        animDur: 420,
      });
      redrawAll();
    } else {
      // official game markers: push animated marker
      const now = performance.now();
      markerLayer.push({
        x: x_norm,
        y: y_norm,
        value: sector,
        multiplier,
        label,
        color: "rgba(32,201,151,0.95)",
        animStart: now,
        animDur: 420,
      });
      if (markerLayer.length > 80) markerLayer.shift();
      redrawAll();
    }

    // call app handler (board integration)
    if (typeof window.onDartHit === "function") {
      try {
        window.onDartHit({
          value: sector,
          multiplier,
          label,
          x: x_norm,
          y: y_norm,
          px: logicalX,
          py: logicalY,
        });
      } catch (e) {
        // app handler errored; ignore here
        console.warn("onDartHit handler threw", e);
      }
    }
  });
}

/* Provide a safe resizeBoard if the page hasn't defined one.
   The template normally defines resizeBoard; only create ours if absent.
*/
if (typeof window.resizeBoard === "undefined") {
  window.resizeBoard = function () {
    const canvasEl = document.getElementById("board");
    if (!canvasEl) return;
    const wrap = canvasEl.parentElement || document.body;
    const size = Math.min(
      Math.max(160, wrap.clientWidth - 36),
      Math.max(160, window.innerHeight - 240),
    );
    canvasEl.width = size;
    canvasEl.height = size;
    if (typeof window.redrawBoard === "function") window.redrawBoard();
  };
}

// If page hasn't already called resizeBoard, do an initial sizing on load
window.addEventListener("load", function () {
  try {
    if (typeof window.resizeBoard === "function") window.resizeBoard();
    else if (canvas) {
      // fallback sizing
      const wrap = canvas.parentElement || document.body;
      const size = Math.min(
        Math.max(160, wrap.clientWidth - 36),
        Math.max(160, window.innerHeight - 240),
      );
      canvas.width = size;
      canvas.height = size;
      redrawAll();
    }
  } catch (e) {
    // ignore init errors
    console.warn("board init error", e);
  }
});

/* Debug helpers */
window._boardDebug = {
  getZoomState: () => ({
    scale: zoomState.scale,
    centerX: zoomState.centerX,
    centerY: zoomState.centerY,
  }),
  setZoomState: (cX, cY, s, dur) => setZoomAnimated(cX, cY, s, dur),
  getCanvasSize: () => ({
    width: canvas ? canvas.width : 0,
    height: canvas ? canvas.height : 0,
  }),
  startBullsVisual: (order) => window.bullsDeciderStartVisual(order),
  endBullsVisual: () => window.bullsDeciderEnd(),
};
