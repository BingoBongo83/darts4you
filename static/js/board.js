// Simple dartboard canvas mapping with animated zoom-to-bull, transform-aware click handling,
// and UI overlay for the Bulls decider. Emits global `onDartHit` when user clicks.
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

// geometric sizes (computed based on canvas size)
let outerRadius = (Math.min(W, H) / 2) * 0.78;
let tripleInner = outerRadius * 0.58;
let tripleOuter = outerRadius * 0.66;
let doubleInner = outerRadius * 0.88;
let doubleOuter = outerRadius;
let bullOuter = outerRadius * 0.06;
let bullInner = outerRadius * 0.03;
// bigger outer miss ring
let OUTER_MISS_RING = outerRadius * 1.35;

let markerLayer = []; // official hit markers
let bullsMarkers = []; // markers from bulls-decider clicks (kept separate)

/* Zoom state: centerX/centerY are normalized (0..1), scale >= 1 */
const zoomState = {
  scale: 1,
  targetScale: 1,
  centerX: 0.5,
  centerY: 0.5,
  animStart: null,
  animDuration: 300, // ms
  animFromScale: 1,
  animFromCX: 0.5,
  animFromCY: 0.5,
};

/* Bulls-decider overlay state */
let bullsDeciderActive = false;
let bullsDeciderOrder = []; // array of {id,name}
let bullsDeciderCurrent = 0; // index
let bullsDeciderResults = []; // {x,y,dist2,player_id}
let bullsDeciderOverlayVisible = false;
let bullsDeciderZoomPaused = false; // when true, overlay remains but board is temporarily unzoomed

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

/* Apply current zoom transform to ctx.
   We animate scale/center over time. */
function applyZoomTransform() {
  // compute current animated state
  const s = zoomState.scale;
  const zx = zoomState.centerX * canvas.width;
  const zy = zoomState.centerY * canvas.height;
  ctx.translate(zx, zy);
  ctx.scale(s, s);
  ctx.translate(-zx, -zy);
}

/* Start an animated zoom to centerX/centerY (normalized) and targetScale over duration ms.
   If duration is 0, jump immediately. */
function animateZoomTo(centerX, centerY, targetScale, duration = 300) {
  // clamp inputs
  centerX = Math.max(0, Math.min(1, centerX));
  centerY = Math.max(0, Math.min(1, centerY));
  targetScale = Math.max(1, targetScale || 1);
  duration = Math.max(0, duration);

  // cancel any existing animation by capturing current computed values
  // if an animation is ongoing, compute intermediate value
  const now = performance.now();
  let currentScale = zoomState.scale;
  let currentCX = zoomState.centerX;
  let currentCY = zoomState.centerY;

  // init animation state
  zoomState.animStart = now;
  zoomState.animDuration = duration;
  zoomState.animFromScale = currentScale;
  zoomState.animFromCX = currentCX;
  zoomState.animFromCY = currentCY;
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
    // ease (cubic out)
    const ease = 1 - Math.pow(1 - t, 3);
    zoomState.scale =
      zoomState.animFromScale +
      (zoomState.targetScale - zoomState.animFromScale) * ease;
    zoomState.centerX =
      zoomState.animFromCX + (zoomState.targetCX - zoomState.animFromCX) * ease;
    zoomState.centerY =
      zoomState.animFromCY + (zoomState.targetCY - zoomState.animFromCY) * ease;
    redrawAll();
    if (t < 1) {
      requestAnimationFrame(step);
    } else {
      zoomState.animStart = null;
    }
  }
  requestAnimationFrame(step);
}

/* Draw the dartboard */
function drawBoard() {
  computeSizes();
  ctx.save();
  ctx.clearRect(0, 0, W, H);

  // Fill background first (no zoom)
  ctx.fillStyle = "#071826";
  ctx.fillRect(0, 0, W, H);

  // Apply zoom for board drawing and markers so everything scales consistently
  applyZoomTransform();

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

  // Draw official markers and bulls-decider markers while under zoom transform
  drawMarkersLayer();

  ctx.restore(); // restore to non-transformed state for overlay UI
  // draw Bulls-decider overlay (non-transformed)
  drawBullsOverlay();
}

/* helper to draw rounded rectangle */
function roundedRect(ctxParam, x, y, width, height, radius) {
  ctxParam.moveTo(x + radius, y);
  ctxParam.arcTo(x + width, y, x + width, y + height, radius);
  ctxParam.arcTo(x + width, y + height, x, y + height, radius);
  ctxParam.arcTo(x, y + height, x, y, radius);
  ctxParam.arcTo(x, y, x + width, y, radius);
  ctxParam.closePath();
}

/* Given a hit, compute pixel coordinates. If x_norm/y_norm provided, use that (exact clicked position).
   Returned coordinates are in canvas pixel space BEFORE applying zoom transforms. The drawing functions
   apply the zoom transform themselves so markers line up with board drawing.
*/
function hitToCoord(hit) {
  computeSizes();
  // server stored normalized coords: convert to pixels (unzoomed logical)
  if (typeof hit.x === "number" && typeof hit.y === "number") {
    if (hit.x <= 1 && hit.y <= 1) {
      return { x: hit.x * canvas.width, y: hit.y * canvas.height };
    }
    // fallback: assume pixel coords already
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
  // official markers
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

  // bulls-decider markers (distinct style)
  bullsMarkers.forEach((m) => {
    const coord = hitToCoord(m);
    const radius = Math.max(5, Math.floor(canvas.width / 110));
    ctx.beginPath();
    ctx.arc(coord.x, coord.y, radius, 0, Math.PI * 2);
    ctx.fillStyle = m.color || "rgba(32,201,151,0.95)";
    ctx.fill();
    ctx.beginPath();
    ctx.arc(coord.x, coord.y, radius, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(0,0,0,0.6)";
    ctx.lineWidth = 1;
    ctx.stroke();
  });
}

/* Bulls-decider overlay drawing (non-transformed) */
function drawBullsOverlay() {
  // Only draw overlay when decider is active or zoomed
  if (!bullsDeciderActive && zoomState.scale === 1) return;

  const padding = 10;
  const boxW = 260;
  const boxH = 86;
  const x = canvas.width - boxW - 12;
  const y = 12;

  // background
  ctx.save();
  ctx.globalAlpha = 0.95;
  ctx.fillStyle = "rgba(6,20,27,0.92)";
  roundedRect(ctx, x, y, boxW, boxH, 8);
  ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.04)";
  ctx.lineWidth = 1;
  ctx.stroke();

  // text
  ctx.fillStyle = "#e6eef6";
  ctx.font = "14px sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  const title = bullsDeciderActive
    ? bullsDeciderZoomPaused
      ? "Bulls decider (paused zoom)"
      : "Bulls decider active"
    : "Zoomed";
  ctx.fillText(title, x + padding, y + 8);

  // current player hint (always visible while decider active even if zoom paused)
  if (bullsDeciderActive && bullsDeciderOrder && bullsDeciderOrder.length) {
    const cur =
      bullsDeciderOrder[
        Math.max(0, Math.min(bullsDeciderOrder.length - 1, bullsDeciderCurrent))
      ];
    const name = cur ? cur.name : "-";
    ctx.fillStyle = "rgba(226,238,246,0.95)";
    ctx.font = "12px sans-serif";
    ctx.fillText(`Player to click: ${name}`, x + padding, y + 34);
  } else {
    ctx.fillStyle = "rgba(226,238,246,0.9)";
    ctx.font = "12px sans-serif";
    ctx.fillText("Click board to set bulls", x + padding, y + 34);
  }

  // small buttons: [Zoom/Rezoom] [Cancel]
  const btnH = 30;
  const btnW = 82;
  const gap = 8;
  const btnZoomX = x + boxW - padding - btnW * 2 - gap;
  const btnCancelX = x + boxW - padding - btnW;
  const btnY = y + boxH - padding - btnH;

  // Zoom / Re-zoom button
  ctx.fillStyle = "rgba(32,201,151,0.12)";
  roundedRect(ctx, btnZoomX, btnY, btnW, btnH, 6);
  ctx.fill();
  ctx.fillStyle = "rgba(32,201,151,0.95)";
  ctx.font = "12px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const zoomLabel = bullsDeciderZoomPaused ? "Re-zoom" : "Zoom";
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

/* clear and redraw board + markers */
function redrawAll() {
  drawBoard();
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

/* Zoom API (animated)
   - zoomTo(centerX, centerY, scale, duration)
   - zoomToBull(scale, duration)
   - resetZoom(duration)
*/
function setZoomAnimated(centerX, centerY, targetScale, duration = 300) {
  animateZoomTo(centerX, centerY, targetScale, duration);
}
window.zoomTo = function (centerX, centerY, scale, duration) {
  setZoomAnimated(centerX, centerY, scale, duration || 300);
};
window.zoomToBull = function (scale = 3, duration = 300) {
  const clamped = Math.max(1, Math.min(6, scale));
  // allow repeated calls while bulls decider active
  setZoomAnimated(0.5, 0.5, clamped, duration);
};
window.resetZoom = function (duration = 200) {
  setZoomAnimated(0.5, 0.5, 1, duration);
};

/* Bulls-decider helpers for overlay and markers */
window.bullsDeciderStartVisual = function (order) {
  bullsDeciderActive = true;
  bullsDeciderZoomPaused = false;
  bullsDeciderOrder = (order || []).map((o) => ({ id: o.id, name: o.name }));
  bullsDeciderCurrent = 0;
  bullsDeciderResults = new Array(bullsDeciderOrder.length).fill(null);
  bullsMarkers = [];
  // ensure zoomed for precision
  try {
    window.zoomToBull(3, 350);
  } catch (e) {
    /* ignore if missing */
  }
  redrawAll();
};
window.bullsDeciderSetCurrent = function (idx) {
  bullsDeciderCurrent = idx || 0;
  redrawAll();
};
window.bullsDeciderAddMarker = function (x_norm, y_norm, player_id) {
  // add to bullsMarkers so it's drawn distinct
  bullsMarkers.push({
    x: x_norm,
    y: y_norm,
    color: "rgba(32,201,151,0.95)",
    value: 0,
    multiplier: 0,
  });
  redrawAll();
};
window.bullsDeciderTogglePauseZoom = function () {
  // Toggle temporary unzoom while keeping the decider active and overlay visible
  if (!bullsDeciderActive) return;
  bullsDeciderZoomPaused = !bullsDeciderZoomPaused;
  if (bullsDeciderZoomPaused) {
    // temporarily unzoom to allow board to be seen full
    try {
      window.resetZoom(200);
    } catch (e) {}
  } else {
    // re-zoom for precise clicks
    try {
      window.zoomToBull(3, 300);
    } catch (e) {}
  }
  redrawAll();
};
window.bullsDeciderEnd = function () {
  bullsDeciderActive = false;
  bullsDeciderZoomPaused = false;
  bullsDeciderOrder = [];
  bullsDeciderCurrent = 0;
  bullsDeciderResults = [];
  bullsMarkers = [];
  // reset zoom
  try {
    window.resetZoom(250);
  } catch (e) {}
  redrawAll();
};
window.bullsDeciderCancel = function () {
  // Full end / cancel of the visual decider (keeps behavior consistent with modal cancel)
  window.bullsDeciderEnd();
};

/* Click handling (transform-aware + overlay detection)
   When the canvas is zoomed via the internal zoomState, clicks received from the browser are in
   post-transform (screen) canvas pixel coordinates. We must map them back through the inverse zoom
   transform to find the logical board coordinates to compute sector/multiplier.

   Additionally, if the user clicks the overlay [Zoom] / [Cancel] buttons, handle those.
*/
canvas.addEventListener("click", function (e) {
  const rect = canvas.getBoundingClientRect();
  const pixelX = e.clientX - rect.left;
  const pixelY = e.clientY - rect.top;

  // If overlay visible, check for overlay button hits first (overlay is drawn in screen coords)
  if (bullsDeciderOverlayVisible) {
    const ov = bullsDeciderOverlayVisible;
    // Zoom button
    const bz = ov.btnZoom;
    if (
      pixelX >= bz.x &&
      pixelX <= bz.x + bz.w &&
      pixelY >= bz.y &&
      pixelY <= bz.y + bz.h
    ) {
      // re-zoom into bull (allow repeated zooms while decider active)
      if (bullsDeciderActive) window.zoomToBull(3, 300);
      return;
    }
    // Cancel button
    const bc = ov.btnCancel;
    if (
      pixelX >= bc.x &&
      pixelX <= bc.x + bc.w &&
      pixelY >= bc.y &&
      pixelY <= bc.y + bc.h
    ) {
      // If decider is active, this acts as a temporary unzoom (pause) so users can inspect board;
      // re-clicking the Zoom button will re-zoom. If decider is not active, perform full cancel.
      if (bullsDeciderActive) {
        if (typeof window.bullsDeciderTogglePauseZoom === "function")
          window.bullsDeciderTogglePauseZoom();
      } else {
        if (typeof window.bullsDeciderCancel === "function")
          window.bullsDeciderCancel();
      }
      // Also call server-side cancel if needed - template-level handlers will still run as appropriate.
      return;
    }
  }

  // Map screen pixel coords back to the unzoomed logical coordinates
  let logicalX = pixelX;
  let logicalY = pixelY;
  if (zoomState.targetScale && zoomState.scale !== 1) {
    const zx = zoomState.centerX * canvas.width;
    const zy = zoomState.centerY * canvas.height;
    // inverse transform: x_logical = zx + (x_screen - zx) / scale
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

  // normalized coordinates (0..1) - logical positions before zoom
  const x_norm = logicalX / canvas.width;
  const y_norm = logicalY / canvas.height;

  // If bulls-decider is active, add a visual marker and pass through to app via onDartHit
  if (bullsDeciderActive) {
    // store this marker visually
    bullsMarkers.push({
      x: x_norm,
      y: y_norm,
      color: "rgba(32,201,151,0.95)",
      value: sector,
      multiplier,
    });
    redrawAll();
  }

  if (typeof window.onDartHit === "function") {
    window.onDartHit({
      value: sector,
      multiplier: multiplier,
      label: label,
      x: x_norm,
      y: y_norm,
      px: logicalX,
      py: logicalY,
    });
  }
});

/* Board sizing & redraw - make sure bottom profiles remain visible */
function resizeBoard() {
  const canvasEl = document.getElementById("board");
  const wrap = canvasEl.parentElement;
  const size = Math.min(wrap.clientWidth - 36, window.innerHeight - 240);
  canvasEl.width = size;
  canvasEl.height = size;
  if (typeof window.redrawBoard === "function") window.redrawBoard();
  // re-show markers (they will redraw respecting current zoom)
  if (markerLayer && markerLayer.length)
    window.showMarkers(markerLayer, "rgba(255,255,50,0.95)");
}
window.addEventListener("resize", resizeBoard);

// Utility escape (used elsewhere in app)
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (m) {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[m];
  });
}

// Expose some internal state for debugging if needed
window._boardDebug = {
  getZoomState: () => ({
    scale: zoomState.scale,
    centerX: zoomState.centerX,
    centerY: zoomState.centerY,
  }),
  setZoomState: (cX, cY, s, dur) => setZoomAnimated(cX, cY, s, dur),
  getCanvasSize: () => ({ width: canvas.width, height: canvas.height }),
  startBullsVisual: (order) => window.bullsDeciderStartVisual(order),
  endBullsVisual: () => window.bullsDeciderEnd(),
};

// Init
(function init() {
  resizeBoard();
})();
