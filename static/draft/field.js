/* ============================================================================
 * field.js — Fantasy Draft Order Randomizer: canvas footrace + live sync
 * ----------------------------------------------------------------------------
 * Reads window.DRAFT = { run, isHost, serverTime } injected by race.html.
 * Every viewer renders the identical race from the deterministic run data;
 * SocketIO keeps the host and live spectators aligned to the same clock.
 * ========================================================================== */
(function () {
  "use strict";

  const DRAFT = window.DRAFT;
  const RUN = DRAFT.run;
  const RUNNERS = RUN.runners;
  const N = RUNNERS.length;
  const DURATION = RUN.duration;          // seconds
  const RUN_ID = RUN.run_id;

  // ── Element refs ──────────────────────────────────────────────────────────
  const canvas = document.getElementById("field");
  const ctx = canvas.getContext("2d");
  const statusLine = document.getElementById("statusLine");
  const runBtn = document.getElementById("runBtn");        // host only
  const podium = document.getElementById("podium");
  const orderList = document.getElementById("orderList");
  const replayBtn = document.getElementById("replayBtn");

  // ── Layout ────────────────────────────────────────────────────────────────
  const W = 1000;
  const NAME_W = 150;
  const TOP_PAD = 40;
  const BOT_PAD = 26;
  const X0 = NAME_W + 16;
  const X_GOAL = W - 54;
  const TRACK = X_GOAL - X0;

  const laneH = Math.max(11, Math.min(44, Math.floor(560 / N)));
  const H = TOP_PAD + N * laneH + BOT_PAD;
  canvas.width = W;
  canvas.height = H;

  // ── Clock sync ────────────────────────────────────────────────────────────
  // skew ≈ serverEpoch − clientEpoch (one-way latency ignored; fine for a race)
  let skew = DRAFT.serverTime - Date.now();
  let startedAt = RUN.started_at_ms;      // server epoch ms, or null

  // mode: "idle" | "synced" | "replay" | "done"
  let mode = "idle";
  let replayStart = 0;
  let rafId = null;
  let safetyTimer = null;
  let endedEmitted = false;

  function serverNow() { return Date.now() + skew; }

  function currentElapsed() {
    if (mode === "synced" && startedAt) return (serverNow() - startedAt) / 1000;
    if (mode === "replay") return (performance.now() - replayStart) / 1000;
    return null;
  }

  // ── Progress curve — MUST match draft_manager.progress() ──────────────────
  function progress(r, elapsed) {
    if (r.finish_time <= 0) return 1;
    let tau = elapsed / r.finish_time;
    if (tau < 0) tau = 0; else if (tau > 1) tau = 1;
    const PI = Math.PI;
    let p = tau
      + r.a1 * Math.sin(PI * tau)
      + r.a2 * Math.sin(2 * PI * tau)
      + r.a3 * Math.sin(3 * PI * tau);
    return p < 0 ? 0 : (p > 1 ? 1 : p);
  }

  // ── Drawing ───────────────────────────────────────────────────────────────
  function laneCenter(i) { return TOP_PAD + i * laneH + laneH / 2; }

  function drawField() {
    // turf
    const g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, "#1c6b34");
    g.addColorStop(1, "#145026");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);

    // name column
    ctx.fillStyle = "rgba(0,0,0,0.30)";
    ctx.fillRect(0, 0, NAME_W, H);

    // yard lines every 10% of the track
    ctx.strokeStyle = "rgba(255,255,255,0.16)";
    ctx.lineWidth = 1;
    for (let k = 0; k <= 10; k++) {
      const x = X0 + (TRACK * k) / 10;
      ctx.beginPath();
      ctx.moveTo(x, TOP_PAD - 6);
      ctx.lineTo(x, H - BOT_PAD + 4);
      ctx.stroke();
    }

    // start line + goal line
    ctx.strokeStyle = "rgba(255,255,255,0.55)";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(X0, TOP_PAD - 6); ctx.lineTo(X0, H - BOT_PAD + 4); ctx.stroke();

    ctx.strokeStyle = "#f2c14e";
    ctx.lineWidth = 4;
    ctx.beginPath(); ctx.moveTo(X_GOAL, TOP_PAD - 8); ctx.lineTo(X_GOAL, H - BOT_PAD + 6); ctx.stroke();

    // end-zone hash beyond the goal
    ctx.fillStyle = "rgba(242,193,78,0.14)";
    ctx.fillRect(X_GOAL, 0, W - X_GOAL, H);

    // labels
    ctx.fillStyle = "rgba(245,247,242,0.85)";
    ctx.font = "700 13px 'IBM Plex Mono', monospace";
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText("RUNNERS", 12, TOP_PAD / 2);
    ctx.save();
    ctx.translate(W - 14, H / 2);
    ctx.rotate(Math.PI / 2);
    ctx.textAlign = "center";
    ctx.fillStyle = "#f2c14e";
    ctx.font = "700 13px 'Bebas Neue', 'IBM Plex Mono', monospace";
    ctx.fillText("GOAL", 0, 0);
    ctx.restore();
  }

  function drawRunner(r, p, elapsed, finished) {
    const cy = laneCenter(r.lane);
    const x = X0 + p * TRACK;
    const s = laneH;
    const running = !finished && elapsed !== null && p < 1 && elapsed > 0;

    // name (left column)
    ctx.fillStyle = "#f5f7f2";
    ctx.font = `${Math.max(10, Math.min(14, s * 0.42))}px 'IBM Plex Mono', monospace`;
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    let nm = r.name;
    const maxChars = Math.floor((NAME_W - 20) / (Math.max(10, Math.min(14, s * 0.42)) * 0.6));
    if (nm.length > maxChars) nm = nm.slice(0, maxChars - 1) + "…";
    ctx.fillText(nm, 10, cy);

    // shadow
    ctx.fillStyle = "rgba(0,0,0,0.22)";
    ctx.beginPath();
    ctx.ellipse(x, cy + s * 0.34, s * 0.30, s * 0.10, 0, 0, Math.PI * 2);
    ctx.fill();

    const helmetR = Math.max(4, Math.min(13, s * 0.26));

    if (s >= 16) {
      // legs (running gait)
      const phase = running ? (elapsed * 9 + r.lane * 0.7) : 0;
      const swing = Math.sin(phase) * s * 0.18;
      const hipY = cy + s * 0.06;
      const legLen = s * 0.30;
      ctx.strokeStyle = "#20140a";
      ctx.lineWidth = Math.max(1.5, s * 0.07);
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(x, hipY); ctx.lineTo(x - swing, hipY + legLen); ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(x, hipY); ctx.lineTo(x + swing, hipY + legLen); ctx.stroke();

      // jersey
      const bw = s * 0.42, bh = s * 0.40;
      ctx.fillStyle = r.color;
      roundRect(x - bw / 2, cy - bh * 0.5, bw, bh, s * 0.08);
      ctx.fill();

      // number
      if (s >= 22) {
        ctx.fillStyle = "#ffffff";
        ctx.font = `700 ${Math.round(s * 0.24)}px 'IBM Plex Mono', monospace`;
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(String(r.number), x, cy - bh * 0.02);
      }

      // helmet
      ctx.fillStyle = shade(r.color, -18);
      ctx.beginPath();
      ctx.arc(x, cy - bh * 0.5 - helmetR * 0.7, helmetR, 0, Math.PI * 2);
      ctx.fill();
      // facemask
      ctx.strokeStyle = "rgba(255,255,255,0.7)";
      ctx.lineWidth = Math.max(1, s * 0.03);
      ctx.beginPath();
      ctx.arc(x + helmetR * 0.4, cy - bh * 0.5 - helmetR * 0.7, helmetR * 0.6, -0.9, 0.9);
      ctx.stroke();
    } else {
      // compact marker: helmet dot + tiny body
      ctx.fillStyle = r.color;
      roundRect(x - s * 0.18, cy - s * 0.16, s * 0.36, s * 0.32, 2);
      ctx.fill();
      ctx.fillStyle = shade(r.color, -18);
      ctx.beginPath();
      ctx.arc(x, cy - s * 0.28, helmetR, 0, Math.PI * 2);
      ctx.fill();
    }

    // finishing-place tag
    if (finished && r.place != null) {
      ctx.fillStyle = "#f2c14e";
      ctx.font = `700 ${Math.max(10, Math.min(16, s * 0.5))}px 'Bebas Neue','IBM Plex Mono',monospace`;
      ctx.textAlign = "left"; ctx.textBaseline = "middle";
      ctx.fillText("#" + r.place, X_GOAL + 6, cy);
    }
  }

  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function shade(hex, amt) {
    const m = hex.replace("#", "");
    const num = parseInt(m.length === 3 ? m.split("").map(c => c + c).join("") : m, 16);
    let r = (num >> 16) + amt, g = ((num >> 8) & 0xff) + amt, b = (num & 0xff) + amt;
    r = Math.max(0, Math.min(255, r)); g = Math.max(0, Math.min(255, g)); b = Math.max(0, Math.min(255, b));
    return `rgb(${r},${g},${b})`;
  }

  // ── Frame ─────────────────────────────────────────────────────────────────
  function render() {
    const elapsed = currentElapsed();
    const finished = mode === "done";
    drawField();

    // Draw slower runners first so leaders paint on top.
    const draw = finished
      ? [...RUNNERS].sort((a, b) => b.place - a.place)
      : RUNNERS;

    for (const r of draw) {
      let p;
      if (elapsed === null) p = finished ? 1 : 0;
      else p = progress(r, elapsed);
      drawRunner(r, p, elapsed, finished);
    }
  }

  function loop() {
    render();
    const elapsed = currentElapsed();
    if ((mode === "synced" || mode === "replay") && elapsed !== null && elapsed >= DURATION) {
      finishAnimation();
      return;
    }
    rafId = requestAnimationFrame(loop);
  }

  function startLoop() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(loop);
    // requestAnimationFrame is fully paused while the tab is hidden, so a
    // backgrounded viewer's race would never register its finish. setTimeout
    // keeps running (throttled) when hidden, so use it as a completion safety.
    if (safetyTimer) clearTimeout(safetyTimer);
    const elapsed = currentElapsed();
    const remainingMs = Math.max(0, (DURATION - (elapsed || 0)) * 1000) + 200;
    safetyTimer = setTimeout(() => {
      if (mode === "synced" || mode === "replay") finishAnimation();
    }, remainingMs);
  }

  function finishAnimation() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
    if (safetyTimer) { clearTimeout(safetyTimer); safetyTimer = null; }
    const wasReplay = mode === "replay";
    mode = "done";
    render();
    showPodium();
    if (!wasReplay) {
      statusLine.textContent = "Race complete — draft order locked in!";
      if (DRAFT.isHost && !endedEmitted) {
        endedEmitted = true;
        socket.emit("draft_ended", { run_id: RUN_ID });
      }
    }
  }

  // ── Podium / order list ───────────────────────────────────────────────────
  function showPodium() {
    const order = [...RUNNERS].sort((a, b) => a.place - b.place);
    orderList.innerHTML = order.map(r => `
      <li class="order-row">
        <span class="pk">${r.place}</span>
        <span class="sw" style="background:${r.color}"></span>
        <span class="nm">${escapeHtml(r.name)}</span>
      </li>`).join("");
    podium.classList.add("show");
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ── Share links ───────────────────────────────────────────────────────────
  (function setupShare() {
    const origin = window.location.origin;
    const watch = document.getElementById("watchLink");
    const results = document.getElementById("resultsLink");
    if (watch) watch.value = `${origin}/draft/watch/${RUN_ID}`;
    if (results) results.value = `${origin}/draft/results/${RUN_ID}`;
    document.querySelectorAll(".copy-btn").forEach(btn => {
      btn.onclick = async () => {
        const el = document.getElementById(btn.dataset.target);
        try { await navigator.clipboard.writeText(el.value); }
        catch { el.select(); document.execCommand("copy"); }
        const t = btn.textContent; btn.textContent = "Copied!";
        setTimeout(() => btn.textContent = t, 1300);
      };
    });
  })();

  // ── Replay (local, no server) ─────────────────────────────────────────────
  if (replayBtn) {
    replayBtn.onclick = (e) => {
      e.preventDefault();
      podium.classList.remove("show");
      endedEmitted = true;         // replays never re-broadcast
      mode = "replay";
      replayStart = performance.now();
      statusLine.textContent = "Replaying…";
      startLoop();
    };
  }

  // ── Socket wiring ─────────────────────────────────────────────────────────
  const socket = io();

  socket.on("connect", () => {
    socket.emit("draft_join", { run_id: RUN_ID });
  });

  socket.on("draft_state", (state) => {
    if (typeof state.server_time === "number") skew = state.server_time - Date.now();
    if (state.status === "running" && state.started_at_ms) {
      startedAt = state.started_at_ms;
      const elapsed = (serverNow() - startedAt) / 1000;
      if (elapsed >= DURATION) { mode = "done"; finishAnimation(); }
      else { mode = "synced"; statusLine.textContent = "Racing…"; if (runBtn) runBtn.disabled = true; startLoop(); }
    } else if (state.status === "finished") {
      mode = "done"; render(); showPodium();
      statusLine.textContent = "Race complete — draft order locked in!";
      if (runBtn) runBtn.disabled = true;
    } else {
      mode = "idle"; render();
      statusLine.textContent = DRAFT.isHost
        ? "Ready — press Run Race when everyone's watching."
        : "Waiting for the host to start the race…";
    }
  });

  socket.on("draft_started", (data) => {
    skew = data.server_time - Date.now();
    startedAt = data.started_at_ms;
    endedEmitted = false;
    mode = "synced";
    statusLine.textContent = "Racing…";
    if (runBtn) runBtn.disabled = true;
    podium.classList.remove("show");
    startLoop();
  });

  socket.on("draft_finished", () => {
    if (mode !== "done") finishAnimation();
  });

  socket.on("draft_error", (d) => {
    statusLine.textContent = (d && d.message) || "Connection error.";
  });

  socket.on("connect_error", () => {
    statusLine.textContent = "Can't reach the race server — retrying…";
  });

  // ── Host: start the race ──────────────────────────────────────────────────
  if (runBtn) {
    runBtn.onclick = () => {
      runBtn.disabled = true;
      statusLine.textContent = "Starting…";
      socket.emit("draft_start", { run_id: RUN_ID });
    };
  }

  // First paint before any socket traffic.
  render();
})();
