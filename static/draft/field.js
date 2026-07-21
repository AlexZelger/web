/* ============================================================================
 * field.js — Fantasy Draft Order Randomizer: canvas footrace + broadcast FX
 * ----------------------------------------------------------------------------
 * Reads window.DRAFT = { run, isHost, serverTime } injected by race.html.
 * Every viewer renders the identical race from the deterministic run data;
 * SocketIO keeps the host and live spectators aligned to the same clock.
 *
 * Effects: 3s countdown/kickoff, motion trails, leader crown + glow, live
 * reordering leaderboard, deterministic announcer callouts, confetti + winner
 * celebration + screen shake, photo-finish slow-mo zoom, stadium dressing,
 * and synthesized WebAudio SFX with a mute toggle.
 *
 * Determinism: the race clock uses elapsed = (serverNow − startedAt)/1000 − 3.
 * The −3 makes `elapsed` run from −3→0 during the countdown, so every viewer
 * (and every replay) sees the same countdown, lead changes and finish.
 * ========================================================================== */
(function () {
  "use strict";

  const DRAFT = window.DRAFT;
  const RUN = DRAFT.run;
  const RUNNERS = RUN.runners;
  const N = RUNNERS.length;
  const DURATION = RUN.duration;          // race seconds (after kickoff)
  const RUN_ID = RUN.run_id;
  const WEATHER = RUN.weather || "clear"; // clear | rain | snow | mud
  const COUNTDOWN = 3;                     // seconds of pre-race countdown
  const PHOTO_MARGIN = 1.0;               // 1st/2nd finish gap that triggers photo finish

  // ── Element refs ──────────────────────────────────────────────────────────
  const canvas = document.getElementById("field");
  const ctx = canvas.getContext("2d");
  const statusLine = document.getElementById("statusLine");
  const runBtn = document.getElementById("runBtn");        // host only
  const muteBtn = document.getElementById("muteBtn");
  const podium = document.getElementById("podium");
  const orderList = document.getElementById("orderList");
  const replayBtn = document.getElementById("replayBtn");
  const announcerCall = document.getElementById("announcerCall");
  const lbRows = document.getElementById("lbRows");
  const fieldShell = document.getElementById("fieldShell");
  const pfBanner = document.getElementById("pfBanner");

  // ── Layout ────────────────────────────────────────────────────────────────
  const W = 1000;
  const NAME_W = 150;
  const STAND_H = 26;                      // crowd strip at top
  const TOP_PAD = 20 + STAND_H;
  const BOT_PAD = 30;
  const X0 = NAME_W + 16;
  const X_GOAL = W - 60;
  const TRACK = X_GOAL - X0;

  const laneH = Math.max(11, Math.min(44, Math.floor(560 / N)));
  const H = TOP_PAD + N * laneH + BOT_PAD;
  canvas.width = W;
  canvas.height = H;

  // ── Winner / photo-finish (deterministic) ─────────────────────────────────
  const ordered = [...RUNNERS].sort((a, b) => a.place - b.place);
  const WINNER = ordered[0];
  const PHOTO_FINISH = ordered.length >= 2 &&
    (ordered[1].finish_time - ordered[0].finish_time) < PHOTO_MARGIN;

  // ── Clock sync ────────────────────────────────────────────────────────────
  let skew = DRAFT.serverTime - Date.now();
  let startedAt = RUN.started_at_ms;

  // mode: "idle" | "synced" | "replay" | "cinematic" | "celebrate" | "done"
  let mode = "idle";
  let replayStart = 0;
  let rafId = null;
  let safetyTimer = null;
  let endedEmitted = false;

  // FX state
  let kickoffPlayed = false;
  let lastCountShown = null;
  let confetti = [];
  let celebrateStart = 0;
  let shakeDone = false;
  let lbBuilt = false;

  function serverNow() { return Date.now() + skew; }

  function currentElapsed() {
    if (mode === "synced" && startedAt) return (serverNow() - startedAt) / 1000 - COUNTDOWN;
    if (mode === "replay") return (performance.now() - replayStart) / 1000 - COUNTDOWN;
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
    if (r.stumble) {
      const z = (tau - r.stumble.c) / r.stumble.w;
      p -= r.stumble.a * Math.exp(-z * z);
    }
    return p < 0 ? 0 : (p > 1 ? 1 : p);
  }

  // How deep into a stumble a runner is right now (0 = none, 1 = deepest),
  // used to drive the trip animation. Matches the Gaussian above.
  function stumbleAmount(r, elapsed) {
    if (!r.stumble || elapsed <= 0) return 0;
    const tau = Math.min(Math.max(elapsed / r.finish_time, 0), 1);
    const z = (tau - r.stumble.c) / r.stumble.w;
    return Math.exp(-z * z);   // 0..1 bell centered on the stumble
  }

  // ── Announcer callouts (precomputed, deterministic) ───────────────────────
  const CALLOUTS = (function build() {
    const evts = [{ t: 0, text: "🏈 And they're off!" }];
    const maxFt = Math.max.apply(null, RUNNERS.map(r => r.finish_time));
    let prevLeader = null, lastAt = -1;
    for (let t = 0; t <= maxFt; t += 0.1) {
      let best = -1, bi = null;
      for (const r of RUNNERS) { const p = progress(r, t); if (p > best) { best = p; bi = r; } }
      if (bi !== prevLeader) {
        if (prevLeader !== null && (t - lastAt) > 0.6) {
          evts.push({ t: +t.toFixed(2), text: `${bi.name} takes the lead!` });
          lastAt = t;
        }
        prevLeader = bi;
      }
    }
    // Late surge shout-out from whoever is 2nd at the very end.
    const near = ordered[1];
    if (near && !PHOTO_FINISH) {
      evts.push({ t: +(WINNER.finish_time - 0.9).toFixed(2), text: `${WINNER.name} pulling away!` });
    } else if (PHOTO_FINISH) {
      evts.push({ t: +(WINNER.finish_time - 0.9).toFixed(2), text: "Too close to call!" });
    }
    evts.push({ t: WINNER.finish_time, text: `${WINNER.name} wins it! 🏆` });
    evts.sort((a, b) => a.t - b.t);
    return evts;
  })();

  function currentCallout(elapsed) {
    if (elapsed === null || elapsed < 0) return "Get set…";
    let text = CALLOUTS[0].text;
    for (const e of CALLOUTS) { if (e.t <= elapsed) text = e.text; else break; }
    return text;
  }

  // ══════════════════════════════════════════════════════════════════════════
  //  WebAudio SFX (synthesized — no asset files)
  // ══════════════════════════════════════════════════════════════════════════
  const Sound = (function () {
    let ac = null, muted = false;
    function rawCtx() {
      if (!ac) { try { ac = new (window.AudioContext || window.webkitAudioContext)(); } catch { return null; } }
      return ac;
    }
    function ensure() {
      if (muted) return null;
      const c = rawCtx(); if (!c) return null;
      if (c.state === "suspended") c.resume();
      return c;
    }

    // Reaction MP3s (served from /static/draft/sounds/), decoded once into buffers.
    const REACTION_FILES = { "💩": "fart.mp3", "😂": "laughing.mp3", "😭": "crying.mp3" };
    const _samples = {};   // emoji -> AudioBuffer
    let _preloaded = false;
    function preload() {
      if (_preloaded) return;
      const c = rawCtx(); if (!c) return;   // decode works even while suspended
      _preloaded = true;
      for (const [emoji, file] of Object.entries(REACTION_FILES)) {
        fetch("/static/draft/sounds/" + file)
          .then(r => r.arrayBuffer())
          .then(b => c.decodeAudioData(b))
          .then(buf => { _samples[emoji] = buf; })
          .catch(() => {});   // fall back to synth if a file is missing
      }
    }
    function playSample(buf, gain) {
      const c = ensure(); if (!c) return;
      const src = c.createBufferSource(); src.buffer = buf;
      const g = c.createGain(); g.gain.value = gain == null ? 0.7 : gain;
      src.connect(g); g.connect(c.destination); src.start();
    }
    function tone(freq, dur, type, gain, glideTo) {
      const c = ensure(); if (!c) return;
      const o = c.createOscillator(), g = c.createGain();
      o.type = type || "sine"; o.frequency.setValueAtTime(freq, c.currentTime);
      if (glideTo) o.frequency.linearRampToValueAtTime(glideTo, c.currentTime + dur);
      g.gain.setValueAtTime(0.0001, c.currentTime);
      g.gain.exponentialRampToValueAtTime(gain || 0.2, c.currentTime + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + dur);
      o.connect(g); g.connect(c.destination);
      o.start(); o.stop(c.currentTime + dur + 0.05);
    }
    function noise(dur, gain, freq) {
      const c = ensure(); if (!c) return;
      const buf = c.createBuffer(1, c.sampleRate * dur, c.sampleRate);
      const d = buf.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
      const src = c.createBufferSource(); src.buffer = buf;
      const bp = c.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = freq || 1000; bp.Q.value = 0.7;
      const g = c.createGain();
      g.gain.setValueAtTime(0.0001, c.currentTime);
      g.gain.linearRampToValueAtTime(gain || 0.12, c.currentTime + dur * 0.4);
      g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + dur);
      src.connect(bp); bp.connect(g); g.connect(c.destination);
      src.start(); src.stop(c.currentTime + dur);
    }
    // Play the emoji's MP3 clip; fall back to a synth blip if it isn't loaded.
    function reaction(emoji) {
      if (muted) return;
      preload();
      const buf = _samples[emoji];
      if (buf) { playSample(buf, 0.8); return; }
      if (emoji === "😂") { tone(760, 0.08, "square", 0.12); setTimeout(() => tone(1000, 0.09, "square", 0.12), 85); }
      else if (emoji === "😭") { tone(520, 0.32, "sine", 0.16, 200); }
      else { tone(680, 0.12, "triangle", 0.12); }
    }
    return {
      tick() { tone(880, 0.09, "square", 0.08); },
      whistle() { tone(1900, 0.18, "sine", 0.16, 2300); setTimeout(() => tone(2100, 0.14, "sine", 0.13, 1900), 90); },
      airhorn() { tone(180, 0.5, "sawtooth", 0.18); tone(184, 0.5, "square", 0.10); },
      cheer() { noise(1.3, 0.16, 1200); },
      reaction,
      preload,
      resume() { ensure(); preload(); },
      toggle() { muted = !muted; if (muted && ac) { try { ac.suspend(); } catch {} } else { ensure(); } return muted; },
      isMuted() { return muted; },
    };
  })();

  if (muteBtn) {
    muteBtn.onclick = () => {
      const m = Sound.toggle();
      muteBtn.textContent = m ? "🔇" : "🔊";
    };
  }
  // Browsers gate audio behind a gesture — resume on the first interaction.
  document.addEventListener("pointerdown", () => Sound.resume(), { once: true });

  // ══════════════════════════════════════════════════════════════════════════
  //  Drawing
  // ══════════════════════════════════════════════════════════════════════════
  function laneCenter(i) { return TOP_PAD + i * laneH + laneH / 2; }

  function drawStands(elapsed) {
    // Crowd strip at the top — little bobbing dots doing a "wave".
    const wave = (elapsed || 0);
    const cols = Math.floor((W - NAME_W) / 12);
    for (let r = 0; r < 3; r++) {
      for (let c = 0; c < cols; c++) {
        const x = NAME_W + 6 + c * 12;
        const baseY = 6 + r * 8;
        const bob = Math.sin(wave * 4 + c * 0.5 + r) * (mode === "celebrate" ? 3 : 1.2);
        const hue = (c * 37 + r * 90) % 360;
        ctx.fillStyle = `hsl(${hue}, 45%, ${52 - r * 6}%)`;
        ctx.beginPath();
        ctx.arc(x, baseY + bob, 3, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.fillStyle = "rgba(0,0,0,0.25)";
    ctx.fillRect(NAME_W, STAND_H - 2, W - NAME_W, 2);
  }

  function drawGoalposts() {
    const cx = X_GOAL + (W - X_GOAL) / 2;
    const topY = TOP_PAD - 8, botY = H - BOT_PAD + 6;
    ctx.strokeStyle = "#f5d76e"; ctx.lineWidth = 3; ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(cx, topY); ctx.lineTo(cx, botY); ctx.stroke();       // main post
    ctx.beginPath(); ctx.moveTo(cx - 10, topY + 10); ctx.lineTo(cx + 10, topY + 10); ctx.stroke(); // crossbar
    ctx.beginPath(); ctx.moveTo(cx - 10, topY + 10); ctx.lineTo(cx - 10, topY - 6); ctx.stroke();  // uprights
    ctx.beginPath(); ctx.moveTo(cx + 10, topY + 10); ctx.lineTo(cx + 10, topY - 6); ctx.stroke();
  }

  function drawField(elapsed) {
    const g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, "#1c6b34"); g.addColorStop(1, "#145026");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);

    // alternating turf mow-stripes
    for (let k = 0; k < 10; k++) {
      if (k % 2 === 0) {
        ctx.fillStyle = "rgba(255,255,255,0.03)";
        ctx.fillRect(X0 + (TRACK * k) / 10, TOP_PAD - 6, TRACK / 10, H - TOP_PAD - BOT_PAD + 10);
      }
    }

    drawStands(elapsed);

    // name column
    ctx.fillStyle = "rgba(0,0,0,0.30)";
    ctx.fillRect(0, 0, NAME_W, H);

    // yard lines + numbers
    ctx.strokeStyle = "rgba(255,255,255,0.16)";
    ctx.fillStyle = "rgba(255,255,255,0.30)";
    ctx.font = "700 11px 'IBM Plex Mono', monospace";
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    ctx.lineWidth = 1;
    const yards = ["", "10", "20", "30", "40", "50", "40", "30", "20", "10", ""];
    for (let k = 0; k <= 10; k++) {
      const x = X0 + (TRACK * k) / 10;
      ctx.beginPath(); ctx.moveTo(x, TOP_PAD - 6); ctx.lineTo(x, H - BOT_PAD + 4); ctx.stroke();
      if (yards[k]) ctx.fillText(yards[k], x, H - BOT_PAD + 8);
    }

    // start + goal lines
    ctx.strokeStyle = "rgba(255,255,255,0.55)"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(X0, TOP_PAD - 6); ctx.lineTo(X0, H - BOT_PAD + 4); ctx.stroke();
    ctx.strokeStyle = "#f2c14e"; ctx.lineWidth = 4;
    ctx.beginPath(); ctx.moveTo(X_GOAL, TOP_PAD - 8); ctx.lineTo(X_GOAL, H - BOT_PAD + 6); ctx.stroke();

    // right end zone
    ctx.fillStyle = "rgba(242,193,78,0.14)";
    ctx.fillRect(X_GOAL, 0, W - X_GOAL, H);
    drawGoalposts();

    // weather tint over the field
    if (WEATHER === "mud") {
      ctx.fillStyle = "rgba(74,50,30,0.34)";
      ctx.fillRect(X0, TOP_PAD - 6, TRACK, H - TOP_PAD - BOT_PAD + 12);
    } else if (WEATHER === "rain") {
      ctx.fillStyle = "rgba(40,60,90,0.18)";
      ctx.fillRect(0, 0, W, H);
    } else if (WEATHER === "snow") {
      ctx.fillStyle = "rgba(200,215,235,0.12)";
      ctx.fillRect(0, 0, W, H);
    }

    // labels
    ctx.fillStyle = "rgba(245,247,242,0.85)";
    ctx.font = "700 13px 'IBM Plex Mono', monospace";
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText("RUNNERS", 12, STAND_H + (TOP_PAD - STAND_H) / 2);
  }

  // Precipitation overlay drawn on top of the runners (rain/snow only).
  function drawWeather() {
    if (WEATHER !== "rain" && WEATHER !== "snow") return;
    const t = performance.now() / 1000;
    if (WEATHER === "rain") {
      ctx.strokeStyle = "rgba(180,205,235,0.35)";
      ctx.lineWidth = 1;
      for (let i = 0; i < 90; i++) {
        const seed = i * 97.13;
        const x = ((seed * 13.7 + t * 620) % (W + 40)) - 20;
        const y = ((seed * 29.3 + t * 900) % (H + 30)) - 15;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x - 4, y + 12); ctx.stroke();
      }
    } else {  // snow
      ctx.fillStyle = "rgba(255,255,255,0.75)";
      for (let i = 0; i < 70; i++) {
        const seed = i * 53.7;
        const drift = Math.sin(t * 1.1 + seed) * 10;
        const x = ((seed * 17.1 + drift) % (W + 20)) - 10;
        const y = ((seed * 31.9 + t * 150) % (H + 20)) - 10;
        ctx.beginPath(); ctx.arc(x, y, 1.6, 0, Math.PI * 2); ctx.fill();
      }
    }
  }

  function drawRunner(r, p, elapsed, finished, isLeader) {
    const cy = laneCenter(r.lane);
    const x = X0 + p * TRACK;
    const s = laneH;
    const running = !finished && elapsed !== null && elapsed > 0 && p < 1;
    const won = finished && r.place === 1;
    const stum = running ? stumbleAmount(r, elapsed) : 0;
    const stumbling = stum > 0.2;
    const feetY = cy + s * 0.36;

    // name (left column)
    const fs = Math.max(10, Math.min(14, s * 0.42));
    ctx.fillStyle = isLeader && running ? "#f2c14e" : "#f5f7f2";
    ctx.font = `${fs}px 'IBM Plex Mono', monospace`;
    ctx.textAlign = "left"; ctx.textBaseline = "middle";
    let nm = r.name;
    const maxChars = Math.floor((NAME_W - 20) / (fs * 0.6));
    if (nm.length > maxChars) nm = nm.slice(0, maxChars - 1) + "…";
    ctx.fillText(nm, 10, cy);

    // motion trail (speed lines)
    if (running) {
      const dt = 0.12;
      const pPrev = progress(r, Math.max(0, elapsed - dt));
      const speed = Math.max(0, p - pPrev);          // progress per dt
      const trailPx = Math.min(46, speed * TRACK * 1.6);
      if (trailPx > 2) {
        const grad = ctx.createLinearGradient(x - trailPx, 0, x, 0);
        grad.addColorStop(0, "rgba(255,255,255,0)");
        grad.addColorStop(1, hexToRgba(r.color, 0.5));
        ctx.strokeStyle = grad; ctx.lineWidth = Math.max(2, s * 0.16); ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(x - trailPx, cy + s * 0.02); ctx.lineTo(x - 2, cy + s * 0.02); ctx.stroke();
      }
    }

    // shadow
    ctx.fillStyle = "rgba(0,0,0,0.22)";
    ctx.beginPath();
    ctx.ellipse(x, cy + s * 0.34, s * 0.30, s * 0.10, 0, 0, Math.PI * 2);
    ctx.fill();

    // stumble: kicked-up dust, then tip the sprite forward from the feet
    if (stumbling) {
      ctx.fillStyle = WEATHER === "mud" ? "rgba(90,64,40,0.5)" : "rgba(210,205,190,0.45)";
      for (let d = 0; d < 3; d++) {
        const dx = x - s * 0.2 - d * s * 0.16;
        ctx.beginPath(); ctx.arc(dx, feetY, s * (0.10 + d * 0.03), 0, Math.PI * 2); ctx.fill();
      }
      ctx.save();
      ctx.translate(x, feetY); ctx.rotate(stum * 0.5); ctx.translate(-x, -feetY);
    }

    const helmetR = Math.max(4, Math.min(13, s * 0.26));

    // leader glow
    if (isLeader && running) { ctx.save(); ctx.shadowColor = "rgba(242,193,78,0.9)"; ctx.shadowBlur = 14; }

    if (s >= 16) {
      const bw = s * 0.42, bh = s * 0.40;

      // legs (or celebration jump)
      const phase = running ? (elapsed * 9 + r.lane * 0.7) : 0;
      const swing = Math.sin(phase) * s * 0.18;
      const hipY = cy + s * 0.06;
      const legLen = s * 0.30;
      ctx.strokeStyle = "#20140a"; ctx.lineWidth = Math.max(1.5, s * 0.07); ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(x, hipY); ctx.lineTo(x - swing, hipY + legLen); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x, hipY); ctx.lineTo(x + swing, hipY + legLen); ctx.stroke();

      // jersey
      ctx.fillStyle = r.color;
      roundRect(x - bw / 2, cy - bh * 0.5, bw, bh, s * 0.08); ctx.fill();

      // arms — raised if celebrating winner
      ctx.strokeStyle = shade(r.color, -30); ctx.lineWidth = Math.max(1.5, s * 0.06);
      if (won) {
        const bounce = Math.abs(Math.sin(performance.now() / 160)) * s * 0.12;
        ctx.beginPath(); ctx.moveTo(x - bw * 0.3, cy - bh * 0.2); ctx.lineTo(x - bw * 0.55, cy - bh * 0.7 - bounce); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(x + bw * 0.3, cy - bh * 0.2); ctx.lineTo(x + bw * 0.55, cy - bh * 0.7 - bounce); ctx.stroke();
      }

      // number
      if (s >= 22) {
        ctx.fillStyle = "#ffffff";
        ctx.font = `700 ${Math.round(s * 0.24)}px 'IBM Plex Mono', monospace`;
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(String(r.number), x, cy - bh * 0.02);
      }

      // helmet + facemask
      ctx.fillStyle = shade(r.color, -18);
      ctx.beginPath(); ctx.arc(x, cy - bh * 0.5 - helmetR * 0.7, helmetR, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.7)"; ctx.lineWidth = Math.max(1, s * 0.03);
      ctx.beginPath(); ctx.arc(x + helmetR * 0.4, cy - bh * 0.5 - helmetR * 0.7, helmetR * 0.6, -0.9, 0.9); ctx.stroke();
    } else {
      ctx.fillStyle = r.color;
      roundRect(x - s * 0.18, cy - s * 0.16, s * 0.36, s * 0.32, 2); ctx.fill();
      ctx.fillStyle = shade(r.color, -18);
      ctx.beginPath(); ctx.arc(x, cy - s * 0.28, helmetR, 0, Math.PI * 2); ctx.fill();
    }

    if (isLeader && running) ctx.restore();

    if (stumbling) {
      ctx.restore();
      if (s >= 14) {   // dizzy stars above the head
        ctx.font = `${Math.max(11, s * 0.42)}px serif`;
        ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
        ctx.fillText("💫", x, cy - s * 0.6);
      }
    }

    // leader crown
    if (isLeader && running && s >= 14) {
      ctx.font = `${Math.max(12, s * 0.5)}px serif`;
      ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
      ctx.fillText("👑", x, cy - s * 0.62);
    }

    // finishing-place tag
    if (finished && r.place != null) {
      ctx.fillStyle = "#f2c14e";
      ctx.font = `700 ${Math.max(10, Math.min(16, s * 0.5))}px 'Bebas Neue','IBM Plex Mono',monospace`;
      ctx.textAlign = "left"; ctx.textBaseline = "middle";
      ctx.fillText("#" + r.place, X_GOAL + 4, cy);
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
    r = clamp255(r); g = clamp255(g); b = clamp255(b);
    return `rgb(${r},${g},${b})`;
  }
  function hexToRgba(hex, a) {
    const m = hex.replace("#", "");
    const num = parseInt(m.length === 3 ? m.split("").map(c => c + c).join("") : m, 16);
    return `rgba(${num >> 16},${(num >> 8) & 0xff},${num & 0xff},${a})`;
  }
  function clamp255(v) { return v < 0 ? 0 : v > 255 ? 255 : v; }

  // ── Leader helper ─────────────────────────────────────────────────────────
  function leaderLane(elapsed) {
    if (elapsed === null || elapsed <= 0) return -1;
    let best = -1, bl = -1;
    for (const r of RUNNERS) { const p = progress(r, elapsed); if (p > best) { best = p; bl = r.lane; } }
    return bl;
  }

  // ── Countdown overlay (canvas) ────────────────────────────────────────────
  function drawCountdown(elapsed) {
    const remaining = -elapsed;                 // 3 → 0
    let label, frac;
    if (remaining > 0) { const n = Math.ceil(remaining); label = String(n); frac = 1 - (n - remaining); }
    else { label = "HIKE!"; frac = -elapsed; }  // brief after gun
    const scale = 1 + 0.5 * (1 - Math.min(1, Math.abs(frac)));
    ctx.save();
    ctx.fillStyle = "rgba(0,0,0,0.35)"; ctx.fillRect(0, 0, W, H);
    ctx.translate(W / 2, H / 2); ctx.scale(scale, scale);
    ctx.fillStyle = "#f2c14e"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = `700 ${Math.min(120, H * 0.5)}px 'Bebas Neue', sans-serif`;
    ctx.fillText(label, 0, 0);
    ctx.restore();
  }

  // ── Confetti ──────────────────────────────────────────────────────────────
  function spawnConfetti() {
    confetti = [];
    const cols = ["#f2c14e", "#e74c3c", "#3498db", "#2ecc71", "#ffffff", "#9b59b6"];
    for (let i = 0; i < 140; i++) {
      confetti.push({
        x: X_GOAL - 40 + Math.random() * 100, y: -10 - Math.random() * H * 0.3,
        vx: (Math.random() - 0.5) * 2.2, vy: 1.5 + Math.random() * 2.5,
        rot: Math.random() * 6.28, vr: (Math.random() - 0.5) * 0.3,
        s: 3 + Math.random() * 4, c: cols[i % cols.length],
      });
    }
  }
  function drawConfetti(dt) {
    for (const p of confetti) {
      p.x += p.vx; p.y += p.vy * dt * 60; p.rot += p.vr;
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot);
      ctx.fillStyle = p.c; ctx.fillRect(-p.s / 2, -p.s / 2, p.s, p.s * 0.6);
      ctx.restore();
    }
  }

  // ══════════════════════════════════════════════════════════════════════════
  //  Frame
  // ══════════════════════════════════════════════════════════════════════════
  function render() {
    const elapsed = currentElapsed();
    const finished = mode === "done" || mode === "celebrate";
    drawField(elapsed);

    const lead = finished ? -1 : leaderLane(elapsed);

    // slower runners first so leaders paint on top
    const draw = finished
      ? [...RUNNERS].sort((a, b) => b.place - a.place)
      : RUNNERS;

    for (const r of draw) {
      let p;
      if (elapsed === null) p = finished ? 1 : 0;
      else p = progress(r, elapsed);
      drawRunner(r, p, elapsed, finished, r.lane === lead);
    }

    drawWeather();
    if (mode === "celebrate") drawConfetti(1 / 60);
    if ((mode === "synced" || mode === "replay") && elapsed !== null && elapsed < 0) drawCountdown(elapsed);

    updateLeaderboard(elapsed, finished);
    updateAnnouncer(elapsed);
    handleAudio(elapsed);
  }

  // ── Live leaderboard ──────────────────────────────────────────────────────
  const LB_ROW_H = Math.max(22, Math.min(30, Math.floor(560 / N)));
  let lastLbTs = -1e9;   // ensure the very first positioning pass is never throttled

  function buildLeaderboard() {
    lbRows.style.height = (N * LB_ROW_H) + "px";
    lbRows.innerHTML = "";
    for (const r of RUNNERS) {
      const row = document.createElement("div");
      row.className = "lb-row";
      row.dataset.lane = r.lane;
      row.style.height = (LB_ROW_H - 4) + "px";
      row.style.animationDelay = (r.lane * 0.05) + "s";
      row.innerHTML =
        `<span class="lb-rank"></span>` +
        `<span class="lb-sw" style="background:${r.color}"></span>` +
        `<span class="lb-name">${escapeHtml(r.name)} <span class="lb-team">${escapeHtml(r.abbr || "")}</span></span>` +
        `<span class="lb-pct"></span>`;
      lbRows.appendChild(row);
    }
    lbBuilt = true;
  }

  function updateLeaderboard(elapsed, finished) {
    if (!lbBuilt) buildLeaderboard();
    const now = performance.now();
    if (!finished && now - lastLbTs < 90) return;   // throttle to ~11fps
    lastLbTs = now;

    const ranked = RUNNERS.map(r => {
      let p;
      if (finished) p = 1 - r.place * 1e-6;          // preserve final order
      else if (elapsed === null || elapsed < 0) p = 0;
      else p = progress(r, elapsed);
      return { r, p };
    }).sort((a, b) => b.p - a.p);

    ranked.forEach((item, idx) => {
      const row = lbRows.children[item.r.lane];
      if (!row) return;
      row.style.transform = `translateY(${idx * LB_ROW_H}px)`;
      row.querySelector(".lb-rank").textContent = idx + 1;
      row.querySelector(".lb-pct").textContent =
        finished ? "#" + item.r.place : Math.round(item.p * 100) + "%";
      row.classList.toggle("leader", idx === 0 && !finished && elapsed !== null && elapsed > 0);
    });
  }

  function updateAnnouncer(elapsed) {
    const text = (mode === "done" || mode === "celebrate")
      ? `${WINNER.name} wins it! 🏆`
      : currentCallout(elapsed);
    if (announcerCall.textContent !== text) announcerCall.textContent = text;
  }

  // ── Audio triggers ────────────────────────────────────────────────────────
  function handleAudio(elapsed) {
    if (elapsed === null) return;
    if (elapsed < 0) {
      const n = Math.ceil(-elapsed);
      if (n !== lastCountShown && n >= 1 && n <= COUNTDOWN) { lastCountShown = n; Sound.tick(); }
    } else if (!kickoffPlayed) {
      kickoffPlayed = true; Sound.whistle();
    }
  }

  // ══════════════════════════════════════════════════════════════════════════
  //  Loop / lifecycle
  // ══════════════════════════════════════════════════════════════════════════
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
    kickoffPlayed = false; lastCountShown = null;
    rafId = requestAnimationFrame(loop);
    // rAF is fully paused while the tab is hidden — setTimeout keeps running,
    // so use it as a completion safety so a backgrounded race still finishes.
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

    if (!wasReplay && DRAFT.isHost && !endedEmitted) {
      endedEmitted = true;
      socket.emit("draft_ended", { run_id: RUN_ID });
    }
    statusLine.textContent = "Race complete — draft order locked in!";

    const finish = () => { Sound.airhorn(); Sound.cheer(); shakeField(); showPodium(); startCelebration(); };
    if (PHOTO_FINISH && !document.hidden) playPhotoFinish(finish);
    else finish();
  }

  function shakeField() {
    if (shakeDone) return; shakeDone = true;
    fieldShell.classList.add("shake");
    setTimeout(() => fieldShell.classList.remove("shake"), 450);
  }

  function startCelebration() {
    spawnConfetti();
    mode = "celebrate";
    celebrateStart = performance.now();
    (function celebrate() {
      render();
      if (performance.now() - celebrateStart < 3800) requestAnimationFrame(celebrate);
      else { mode = "done"; render(); }
    })();
  }

  // ── Photo finish: zoomed slow-mo of the final stretch ─────────────────────
  function playPhotoFinish(cb) {
    pfBanner.classList.add("show");
    // Run the window until BOTH lead runners have crossed (the runner-up
    // finishes just after the winner), plus a little pad, then freeze on it.
    const tStart = Math.max(0, ordered[0].finish_time - 0.9);
    const tEnd = Math.min(RUN.duration, ordered[1].finish_time + 0.18);
    const SLOWMO = 0.32;                       // playback speed (lower = slower)
    const durMs = Math.max(1400, (tEnd - tStart) / SLOWMO * 1000);
    const HOLD_MS = 750;                       // freeze-frame on the crossing before the podium
    const focusY = (laneCenter(ordered[0].lane) + laneCenter(ordered[1].lane)) / 2;
    const regionW = 320, z = W / regionW;
    const start = performance.now();
    let done = false;
    const finishUp = () => { if (done) return; done = true; pfBanner.classList.remove("show"); cb(); };
    const safety = setTimeout(finishUp, durMs + HOLD_MS + 500);

    function drawFrame(e) {
      ctx.save();
      ctx.fillStyle = "#0c2415"; ctx.fillRect(0, 0, W, H);
      // zoom transform focused on the goal line + leaders
      ctx.translate(W / 2, H / 2); ctx.scale(z, z);
      ctx.translate(-(X_GOAL - regionW * 0.35), -focusY);
      drawField(e);
      const lead = leaderLane(e);
      for (const r of [...RUNNERS].sort((a, b) => progress(a, e) - progress(b, e))) {
        drawRunner(r, progress(r, e), e, false, r.lane === lead);
      }
      ctx.restore();
    }

    (function frame() {
      const t = (performance.now() - start) / durMs;
      if (t >= 1) {
        drawFrame(tEnd);                        // hold the final crossing
        clearTimeout(safety);
        setTimeout(finishUp, HOLD_MS);
        return;
      }
      drawFrame(tStart + (tEnd - tStart) * t);
      requestAnimationFrame(frame);
    })();
  }

  // ── Podium ────────────────────────────────────────────────────────────────
  function showPodium() {
    const order = [...RUNNERS].sort((a, b) => a.place - b.place);
    orderList.innerHTML = order.map(r => `
      <li class="order-row">
        <span class="pk">${r.place}</span>
        <span class="sw" style="background:${r.color}"></span>
        <span class="nm">${escapeHtml(r.name)} <span class="nm-team">${escapeHtml(r.team || "")}</span></span>
      </li>`).join("");
    renderStats();
    podium.classList.add("show");
  }

  // ── Post-race stats (computed from the deterministic curves) ──────────────
  function renderStats() {
    const statsEl = document.getElementById("statsPanel");
    if (!statsEl) return;
    const maxFt = Math.max.apply(null, RUNNERS.map(r => r.finish_time));
    const dt = 0.1;

    let leadChanges = 0, prevLeader = null;
    let winnerLeadSamples = 0, samples = 0;
    let topSpeed = 0, topSpeedRunner = WINNER;
    const worstRank = new Map(RUNNERS.map(r => [r.lane, 1]));   // worst (highest) rank ever held

    for (let t = dt; t <= maxFt + 1e-9; t += dt) {
      const ps = RUNNERS.map(r => ({ r, p: progress(r, t) })).sort((a, b) => b.p - a.p);
      const leader = ps[0].r;
      if (prevLeader !== null && leader !== prevLeader) leadChanges++;
      prevLeader = leader;
      if (leader.lane === WINNER.lane) winnerLeadSamples++;
      samples++;
      ps.forEach((item, idx) => {
        const rk = idx + 1;
        if (rk > worstRank.get(item.r.lane)) worstRank.set(item.r.lane, rk);
      });
      for (const r of RUNNERS) {
        const sp = progress(r, t) - progress(r, t - dt);
        if (sp > topSpeed) { topSpeed = sp; topSpeedRunner = r; }
      }
    }

    // biggest comeback = worst rank ever held minus final place (most climbed)
    let comeback = { r: WINNER, spots: 0 };
    for (const r of RUNNERS) {
      const climbed = worstRank.get(r.lane) - r.place;
      if (climbed > comeback.spots) comeback = { r, spots: climbed };
    }

    const winnerPct = samples ? Math.round(winnerLeadSamples / samples * 100) : 0;
    const tiles = [
      { icon: "⚡", label: "Top gear", val: topSpeedRunner.name },
      { icon: "📈", label: "Biggest comeback", val: comeback.spots > 0 ? `${comeback.r.name} (+${comeback.spots})` : "—" },
      { icon: "🔀", label: "Lead changes", val: String(leadChanges) },
      { icon: "👑", label: "Led the race", val: `${WINNER.name} · ${winnerPct}%` },
    ];
    statsEl.innerHTML = tiles.map(t =>
      `<div class="stat-tile"><div class="stat-ico">${t.icon}</div>` +
      `<div class="stat-body"><div class="stat-lbl">${t.label}</div>` +
      `<div class="stat-val">${escapeHtml(String(t.val))}</div></div></div>`).join("");
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

  // ── Weather badge ─────────────────────────────────────────────────────────
  (function setupWeatherBadge() {
    const el = document.getElementById("weatherBadge");
    if (!el) return;
    const map = { clear: ["☀️", "Clear"], rain: ["🌧️", "Rain"], snow: ["❄️", "Snow"], mud: ["🟤", "Muddy"] };
    const [icon, label] = map[WEATHER] || map.clear;
    el.textContent = `${icon} ${label}`;
  })();

  // ── Replay (local, no server) ─────────────────────────────────────────────
  if (replayBtn) {
    replayBtn.onclick = (e) => {
      e.preventDefault();
      podium.classList.remove("show");
      endedEmitted = true; shakeDone = false; confetti = [];
      Sound.resume();
      mode = "replay";
      replayStart = performance.now();
      statusLine.textContent = "Replaying…";
      startLoop();
    };
  }

  // ══════════════════════════════════════════════════════════════════════════
  //  Social: claim-a-roster-spot, predictions, live reactions
  // ══════════════════════════════════════════════════════════════════════════
  const VIEWER_ID = (function () {
    let v = localStorage.getItem("draft_vid");
    if (!v) { v = "v" + Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem("draft_vid", v); }
    return v;
  })();

  let myClaimLane = null, myPrediction = null, predsLocked = false;
  const claimsMap = {};   // lane -> name

  const claimCurrent = document.getElementById("claimCurrent");
  const claimToggle = document.getElementById("claimToggle");
  const claimPicker = document.getElementById("claimPicker");
  const predictItem = document.getElementById("predictItem");
  const predictCurrent = document.getElementById("predictCurrent");
  const predictToggle = document.getElementById("predictToggle");
  const predictPicker = document.getElementById("predictPicker");
  const predCountEl = document.getElementById("predCount");
  const reactBar = document.getElementById("reactBar");
  const reactionsLayer = document.getElementById("reactionsLayer");
  const predictReveal = document.getElementById("predictReveal");
  const REACT_EMOJIS = ["💩", "😂", "😭"];   // poop / cry-laughing / crying

  function chipHTML(r) {
    return `<button class="pick-chip" data-lane="${r.lane}"><span class="cdot" style="background:${r.color}"></span>${escapeHtml(r.name)}</button>`;
  }
  function buildClaimPicker() {
    if (!claimPicker) return;
    claimPicker.innerHTML = RUNNERS.map(chipHTML).join("");
    claimPicker.querySelectorAll(".pick-chip").forEach(chip => {
      chip.onclick = () => socket.emit("draft_claim", { run_id: RUN_ID, viewer_id: VIEWER_ID, lane: +chip.dataset.lane });
    });
  }
  function buildPredictPicker() {
    if (!predictPicker) return;
    predictPicker.innerHTML = RUNNERS.map(chipHTML).join("");
    predictPicker.querySelectorAll(".pick-chip").forEach(chip => {
      chip.onclick = () => { if (!predsLocked) socket.emit("draft_predict", { run_id: RUN_ID, viewer_id: VIEWER_ID, lane: +chip.dataset.lane }); };
    });
  }
  function buildReactBar() {
    if (!reactBar) return;
    REACT_EMOJIS.forEach(em => {
      const b = document.createElement("button");
      b.className = "react-btn"; b.textContent = em;
      b.onclick = () => socket.emit("draft_react", { run_id: RUN_ID, viewer_id: VIEWER_ID, emoji: em });
      reactBar.appendChild(b);
    });
  }
  function refreshClaimChips() {
    if (!claimPicker) return;
    claimPicker.querySelectorAll(".pick-chip").forEach(chip => {
      const lane = +chip.dataset.lane;
      const taken = lane in claimsMap;
      chip.classList.toggle("mine", lane === myClaimLane);
      chip.classList.toggle("taken", taken && lane !== myClaimLane);
      chip.disabled = taken && lane !== myClaimLane;
    });
  }
  function applyClaims(claims) {
    for (const k in claimsMap) delete claimsMap[k];
    for (const [lane, name] of Object.entries(claims || {})) claimsMap[+lane] = name;
    refreshClaimChips();
  }
  function setMyClaim(lane) {
    myClaimLane = lane;
    const r = RUNNERS[lane];
    if (claimCurrent) claimCurrent.textContent = r ? `${r.name} · ${r.abbr}` : "—";
    if (claimToggle) claimToggle.textContent = "Change";
    if (claimPicker) claimPicker.hidden = true;
    if (predictItem) predictItem.hidden = false;
    if (reactBar) reactBar.hidden = false;
    refreshClaimChips();
  }
  function setMyPrediction(lane) {
    myPrediction = lane;
    const r = RUNNERS[lane];
    if (predictCurrent) predictCurrent.textContent = r ? r.name : "none";
    if (predictPicker) {
      predictPicker.hidden = true;
      predictPicker.querySelectorAll(".pick-chip").forEach(c => c.classList.toggle("mine", +c.dataset.lane === lane));
    }
  }
  function lockPredictions() {
    predsLocked = true;
    if (predictToggle) predictToggle.disabled = true;
    if (predictPicker) predictPicker.hidden = true;
    if (myPrediction === null && predictCurrent) predictCurrent.textContent = "no pick";
  }
  function floatReaction(lane, emoji, name) {
    if (!reactionsLayer) return;
    const runner = RUNNERS[lane];
    if (!runner) return;
    const rect = canvas.getBoundingClientRect();
    const shellRect = fieldShell.getBoundingClientRect();
    const sx = rect.width / W, sy = rect.height / H;
    const elapsed = currentElapsed();
    const p = elapsed === null ? ((mode === "done" || mode === "celebrate") ? 1 : 0) : progress(runner, elapsed);
    const cx = (X0 + p * TRACK) * sx + (rect.left - shellRect.left);
    const cy = (laneCenter(lane) - laneH * 0.6) * sy + (rect.top - shellRect.top);
    const el = document.createElement("div");
    el.className = "reaction-pop";
    el.innerHTML = `${emoji}<span class="rname">${escapeHtml(name || "")}</span>`;
    el.style.left = cx + "px";
    el.style.top = cy + "px";
    reactionsLayer.appendChild(el);
    setTimeout(() => el.remove(), 1650);
  }
  function renderPredictions(preds, winnerLane) {
    if (!predictReveal) return;
    preds = preds || {};
    if (Object.keys(preds).length === 0) { predictReveal.hidden = true; return; }
    let correct = 0, total = 0;
    const rows = RUNNERS.map(r => {
      const pl = preds[r.lane];
      if (pl !== undefined) {
        total++;
        const ok = pl === winnerLane;
        if (ok) correct++;
        const pickName = RUNNERS[pl] ? RUNNERS[pl].name : "?";
        return `<div class="pr-row"><span>${escapeHtml(r.name)}</span><span class="pr-pick">picked ${escapeHtml(pickName)}</span><span class="pr-mark ${ok ? "ok" : "no"}">${ok ? "✔" : "✘"}</span></div>`;
      }
      const label = (r.lane in claimsMap) ? "no pick" : "didn't watch";
      return `<div class="pr-row"><span>${escapeHtml(r.name)}</span><span class="pr-pick">${label}</span><span class="pr-mark none">—</span></div>`;
    }).join("");
    predictReveal.hidden = false;
    predictReveal.innerHTML = `<div class="pr-title">Predictions · ${correct}/${total} correct</div>${rows}`;
  }
  function updatePredCount(count, totalMaybe) {
    if (!predCountEl) return;
    predCountEl.textContent = totalMaybe != null ? `${count}/${totalMaybe} predicted` : `${count} predicted`;
  }

  if (claimToggle) claimToggle.onclick = () => { claimPicker.hidden = !claimPicker.hidden; if (predictPicker) predictPicker.hidden = true; };
  if (predictToggle) predictToggle.onclick = () => { if (!predsLocked) { predictPicker.hidden = !predictPicker.hidden; if (claimPicker) claimPicker.hidden = true; } };
  buildClaimPicker(); buildPredictPicker(); buildReactBar();
  if (predictReveal) predictReveal.hidden = true;

  // ── Socket wiring ─────────────────────────────────────────────────────────
  const socket = io();

  socket.on("connect", () => socket.emit("draft_join", { run_id: RUN_ID, viewer_id: VIEWER_ID }));

  socket.on("draft_state", (state) => {
    if (typeof state.server_time === "number") skew = state.server_time - Date.now();

    // Social state
    if (state.claims) applyClaims(state.claims);
    if (typeof state.your_claim === "number") setMyClaim(state.your_claim);
    if (typeof state.your_prediction === "number") setMyPrediction(state.your_prediction);
    if (state.pred_count != null) updatePredCount(state.pred_count);
    if (state.status !== "ready") lockPredictions();
    if (state.predictions) renderPredictions(state.predictions, state.winner_lane);

    if (state.status === "running" && state.started_at_ms) {
      startedAt = state.started_at_ms;
      const elapsed = (serverNow() - startedAt) / 1000 - COUNTDOWN;
      if (elapsed >= DURATION) { mode = "done"; finishAnimation(); }
      else { mode = "synced"; statusLine.textContent = "Racing…"; if (runBtn) runBtn.disabled = true; startLoop(); }
    } else if (state.status === "finished") {
      mode = "done"; render(); showPodium();
      statusLine.textContent = "Race complete — draft order locked in!";
      announcerCall.textContent = `${WINNER.name} wins it! 🏆`;
      if (runBtn) runBtn.disabled = true;
    } else {
      mode = "idle"; render();
      statusLine.textContent = DRAFT.isHost
        ? "Ready — press Run Race when everyone's watching."
        : "Waiting for the host to start the race…";
    }
  });

  socket.on("draft_claims", (data) => applyClaims(data && data.claims));
  socket.on("draft_your_claim", (data) => { if (data && typeof data.lane === "number") setMyClaim(data.lane); });
  socket.on("draft_claim_rejected", (data) => {
    if (!claimCurrent) return;
    const prev = claimCurrent.textContent;
    claimCurrent.textContent = "⚠ " + ((data && data.message) || "taken");
    setTimeout(() => { if (myClaimLane === null) claimCurrent.textContent = "— pick your spot —"; else claimCurrent.textContent = prev; }, 2200);
  });
  socket.on("draft_your_prediction", (data) => { if (data && typeof data.lane === "number") setMyPrediction(data.lane); });
  socket.on("draft_pred_count", (data) => { if (data) updatePredCount(data.count, data.total); });
  socket.on("draft_reaction", (data) => { if (data) { floatReaction(data.lane, data.emoji, data.name); Sound.reaction(data.emoji); } });

  socket.on("draft_started", (data) => {
    skew = data.server_time - Date.now();
    startedAt = data.started_at_ms;
    endedEmitted = false; shakeDone = false;
    Sound.resume();
    lockPredictions();
    mode = "synced";
    statusLine.textContent = "Racing…";
    if (runBtn) runBtn.disabled = true;
    podium.classList.remove("show");
    startLoop();
  });

  socket.on("draft_finished", (data) => {
    lockPredictions();
    if (data && data.predictions) renderPredictions(data.predictions, data.winner_lane);
    if (mode !== "done" && mode !== "celebrate") finishAnimation();
  });
  socket.on("draft_error", (d) => { statusLine.textContent = (d && d.message) || "Connection error."; });
  socket.on("connect_error", () => { statusLine.textContent = "Can't reach the race server — retrying…"; });

  // ── Host: start the race ──────────────────────────────────────────────────
  if (runBtn) {
    runBtn.onclick = () => {
      runBtn.disabled = true;
      statusLine.textContent = "Starting…";
      Sound.resume();
      socket.emit("draft_start", { run_id: RUN_ID });
    };
  }

  // First paint.
  render();
})();
