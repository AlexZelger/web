// ------- Element refs
const overlay = document.getElementById("menuOverlay");
const playerCountSel = document.getElementById("playerCount");
const startBtn = document.getElementById("startBtn");
const randomizeNamesBtn = document.getElementById("randomizeNamesBtn");
const namesContainer = document.getElementById("namesContainer");

const boardEl = document.getElementById("leaderboard");
const currentPlayerText = document.getElementById("currentPlayerText");
const statusText        = document.getElementById("statusText");

const field = document.getElementById("field");
const ctx = field.getContext("2d");

// Theme toggle
const themeToggle = document.getElementById("themeToggle");
const themeIcon   = document.getElementById("themeIcon");

// Scoreboard refs
const sbAtBat   = document.getElementById("sbAtBat");
const sbDist    = document.getElementById("sbDist");
const sbPlayers = document.getElementById("sbPlayers");
const sbLeader  = document.getElementById("sbLeader");
const sbRun     = document.getElementById("sbRun");
const fenceFtEl = document.getElementById("fenceFt");

const rerunBtn  = document.getElementById("rerunBtn");
const changeBtn = document.getElementById("changeBtn");
const shareBtn  = document.getElementById("shareBtn");

// ------- Physics / world units
const G_FTPS2   = 32.174;                    // gravity (ft/s^2)
const PX_PER_FT = field.width / 520;         // map ~520 ft to canvas width
const GROUND_Y  = field.height - 40;         // baseline Y in px
const SCENE_X0  = 32;                        // origin X in px

ctx.imageSmoothingEnabled = false;

// ------- State
let currentFence = 400;
let lastCount = 8;
let lastNames = [];
let lastSeed  = null;

// ------- Small utils
const delay = (ms) => new Promise(r => setTimeout(r, ms));
const pick  = (arr) => arr[Math.floor(Math.random()*arr.length)];

// Build a shareable URL with seed, count, and names
function buildShareURL(seed, numPlayers, names) {
  const u = new URL(window.location.href);
  const p = new URLSearchParams();
  p.set("seed", String(seed));
  p.set("num_players", String(numPlayers));
  (names || []).forEach(n => p.append("name", n));
  u.search = p.toString();
  u.hash = "";
  return u.toString();
}

// ------- Funny name generator
const firstParts = ["Rusty","Moose","Benny","Scooter","Duke","Lefty","Chip","Mickey","Sluggo","Ace","Boomer","Cactus","Turbo","Gonzo","Rizzo","Tex","Nacho","Goose","Beans","Ranger","Cookie","Pepper","Stubs","Wally","Chili","Spanky","Buster"];
const lastParts  = ["Thunder","McDinger","TwoBags","Laser","Barnstorm","Longball","Cannon","Krakatoa","Moonshot","Quickstep","Fireball","Nailbiter","PineTar","Screwball","Heatwave","PopFly","Fastball","Knuckle","Curve","Slider","Forkball","Rattler","Homer","TapeMeasure","Whiplash","Yardstick","Ringer"];
function generateFunnyNames(n) {
  const set = new Set(), out = [];
  while (out.length < n) {
    const name = `${pick(firstParts)} ${pick(lastParts)}`;
    if (!set.has(name)) { set.add(name); out.push(name); }
  }
  return out;
}

// ------- Lineup UI
function rebuildNameInputs(count) {
  namesContainer.innerHTML = "";
  for (let i = 1; i <= count; i++) {
    const input = document.createElement("input");
    input.type = "text";
    input.className = "nameInput";
    input.placeholder = `Player ${i}`;
    input.value = `Player ${i}`;
    input.maxLength = 32;
    namesContainer.appendChild(input);
  }
}
function getEnteredNames() {
  return Array.from(document.querySelectorAll(".nameInput")).map((inp, i) => {
    const v = inp.value.trim();
    return v || `Player ${i+1}`;
  });
}
function ensureNamesBuilt() {
  const count = parseInt(playerCountSel?.value || "8", 10);
  rebuildNameInputs(Number.isFinite(count) ? count : 8);
}

// ------- Leaderboard (only render hitters who have gone)
function renderLeaderboard(items) {
  const frag = document.createDocumentFragment();
  items.forEach((p, index) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    const dist = document.createElement("span");
    name.textContent = `${index + 1}. ${p.name}`;
    dist.textContent = `${p.distance_ft.toFixed(2)} ft`;
    dist.className = "dist";
    li.appendChild(name);
    li.appendChild(dist);
    frag.appendChild(li);
  });
  boardEl.innerHTML = "";
  boardEl.appendChild(frag);
}

// ------- Field (simple gray) + fence, ticks, plate, baseline
function drawField(fenceFt) {
  ctx.clearRect(0, 0, field.width, field.height);

  // Fence line
  const fenceX = SCENE_X0 + fenceFt * PX_PER_FT;
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(fenceX, 20);
  ctx.lineTo(fenceX, GROUND_Y);
  ctx.stroke();

  // Home plate
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(SCENE_X0 - 12, GROUND_Y - 10, 12, 10);

  // Distance ticks / labels
  ctx.fillStyle = "#ffffff";
  ctx.font = "12px system-ui, -apple-system, Segoe UI, Roboto, Arial";
  for (let ft = 100; ft <= 500; ft += 50) {
    const x = SCENE_X0 + ft * PX_PER_FT;
    ctx.fillRect(x, GROUND_Y - 6, 1, 6);
    ctx.fillText(String(ft), x - 10, GROUND_Y - 10);
  }

  // Baseline
  ctx.fillStyle = "#000000";
  ctx.fillRect(0, GROUND_Y, field.width, 4);
}

// --- Pixel batter bitmaps (cache-busted in dev)
const SPRITES = {
  stance: new Image(),
  mid:    new Image(),
  follow: new Image(),
};
const SPRITE_PATHS = {
  stance: "/static/sprites/batter2.png",
  mid:    "/static/sprites/batter1.png",
  follow: "/static/sprites/batter3.png",
};

// helper: load once
function loadSprites() {
  if (loadSprites._p) return loadSprites._p; // memoize
  const bust = Date.now(); // avoids browser caching while developing
  loadSprites._p = Promise.all([
    new Promise((res, rej) => { SPRITES.stance.onload = res; SPRITES.stance.onerror = rej; SPRITES.stance.src = `${SPRITE_PATHS.stance}?v=${bust}`; }),
    new Promise((res, rej) => { SPRITES.mid.onload    = res; SPRITES.mid.onerror    = rej; SPRITES.mid.src    = `${SPRITE_PATHS.mid}?v=${bust}`; }),
    new Promise((res, rej) => { SPRITES.follow.onload = res; SPRITES.follow.onerror = rej; SPRITES.follow.src = `${SPRITE_PATHS.follow}?v=${bust}`; }),
  ]);
  return loadSprites._p;
}

// choose which sprite to draw
let batterPose = "stance";

// scale so it fits; keep pixels crisp
function drawBatterBitmap(img) {
  if (!img || !img.complete) return;

  // target max height on your 900x300 canvas
  const TARGET_H = 130;                           // tweak if you want bigger/smaller
  const scale = Math.min(1, TARGET_H / img.height);
  const destW = Math.round(img.width  * scale);
  const destH = Math.round(img.height * scale);

  const dx = SCENE_X0;                            // left offset you already use
  const dy = GROUND_Y - destH;                    // bottom align to ground

  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(img, dx, dy, destW, destH);
}

// wrapper used everywhere you previously called drawBatter()
function drawBatter() {
  drawBatterBitmap(SPRITES[batterPose]);
}

async function animateSwing() {
  await loadSprites();                 // ensure images are ready

  const steps = [
    ["stance", 120],
    ["mid",     90],
    ["follow", 130],
  ];

  for (const [pose, ms] of steps) {
    batterPose = pose;
    drawField(currentFence);
    drawBatter();
    await new Promise(r => setTimeout(r, ms));
  }
}

function drawBall(x, y) {
  ctx.beginPath();
  ctx.arc(x, y, 4, 0, Math.PI * 2);
  ctx.fillStyle = "#0ea5e9";
  ctx.fill();
}

// Flight that lands exactly at distanceFt
function animateHit({ distanceFt, angleDeg }) {
  const theta = angleDeg * Math.PI / 180;
  const sin2theta = Math.sin(2 * theta);

  const v0 = Math.sqrt((distanceFt * G_FTPS2) / Math.max(0.01, sin2theta));
  const vx = v0 * Math.cos(theta);
  const vy = v0 * Math.sin(theta);

  const T = (2 * vy) / G_FTPS2;
  const durationMs = 2800;
  const speedup = T > 0 ? (T / (durationMs / 1000)) : 1;

  const start = performance.now();
  return new Promise(resolve => {
    function frame(now) {
      const t = Math.min((now - start) / 1000 * speedup, T);

      const x_ft = vx * t;
      const y_ft = (vy * t) - 0.5 * G_FTPS2 * t * t;

      const x = SCENE_X0 + x_ft * PX_PER_FT;
      const y = GROUND_Y - (y_ft * PX_PER_FT);

      drawField(currentFence);
      drawBatter();
      drawBall(x, y);

      if (t >= T) {
        drawBatter();
        drawBall(SCENE_X0 + distanceFt * PX_PER_FT, GROUND_Y - 2);
        return resolve();
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  });
}

// ------- Simulation (supports optional seed)
async function runSimulation(numPlayers, names, seed) {
  statusText.textContent         = "Fetching simulation...";
  currentPlayerText.textContent  = "—";
  boardEl.innerHTML = "";
  drawField(currentFence);
  drawBatter();

  const params = new URLSearchParams();
  params.set("num_players", String(numPlayers));
  (names || []).forEach(n => params.append("name", n));
  if (seed !== undefined && seed !== null) params.set("seed", String(seed));

  const res = await fetch(`/api/simulate?${params.toString()}`);
  const data = await res.json();

  lastSeed = data.seed;

  // Scoreboard/meta
  sbRun.textContent     = data.run_id;
  sbPlayers.textContent = data.players.length;
  fenceFtEl.textContent = data.fence_ft;
  currentFence          = data.fence_ft;

  // Reset leader outputs
  sbLeader.textContent  = "—";
  sbDist.textContent    = "—";
  sbAtBat.textContent   = "—";

  // Incremental leaderboard (only hitters who have completed)
  const seen = [];

  for (let i = 0; i < data.players.length; i++) {
    const p = data.players[i];

    currentPlayerText.textContent = `${p.name} — ${p.distance_ft.toFixed(2)} ft @ ${p.angle_deg}°`;
    statusText.textContent        = "Swing!";
    sbAtBat.textContent           = p.name;
    sbDist.textContent            = "—";

    batterFrame = 0;
    drawField(currentFence);
    drawBatter();
    await animateSwing();

    await animateHit({ distanceFt: p.distance_ft, angleDeg: p.angle_deg });

    seen.push(p);
    const sortedSeen = seen.slice().sort((a,b)=> b.distance_ft - a.distance_ft);
    renderLeaderboard(sortedSeen);

    const leader = sortedSeen[0];
    sbLeader.textContent = `${leader.name} (${leader.distance_ft.toFixed(0)} FT)`;
    sbDist.textContent   = `${p.distance_ft.toFixed(0)} FT`;
    statusText.textContent = "Landed";

    await delay(350);
  }

  const winner = [...seen].sort((a,b)=> b.distance_ft - a.distance_ft)[0];
  currentPlayerText.textContent = `Winner: ${winner.name} — ${winner.distance_ft.toFixed(2)} ft`;
  statusText.textContent        = "Complete";
  sbAtBat.textContent           = `WIN: ${winner.name}`;
  sbLeader.textContent          = `${winner.name} (${winner.distance_ft.toFixed(0)} FT)`;
}

// ------- Share link
async function copyShareLink() {
  const players = lastNames.length ? lastNames : getEnteredNames();
  const count   = lastNames.length ? lastCount : (parseInt(playerCountSel.value, 10) || 8);
  const seed    = lastSeed ?? (Date.now() ^ Math.floor(Math.random()*1e9));
  const link    = buildShareURL(seed, count, players);
  try {
    await navigator.clipboard.writeText(link);
    const original = shareBtn.textContent;
    shareBtn.textContent = "Copied!";
    setTimeout(()=> shareBtn.textContent = original, 1200);
  } catch {
    window.prompt("Copy this link:", link);
  }
}

// ------- Menu wiring
function syncInputsToCount() {
  const count = parseInt(playerCountSel.value, 10);
  rebuildNameInputs(count);
}
playerCountSel.addEventListener("change", syncInputsToCount);

randomizeNamesBtn.addEventListener("click", () => {
  const count = parseInt(playerCountSel.value, 10);
  const funny = generateFunnyNames(count);
  document.querySelectorAll(".nameInput").forEach((inp, i) => { inp.value = funny[i]; });
});

startBtn.addEventListener("click", () => {
  lastCount = parseInt(playerCountSel.value, 10);
  lastNames = getEnteredNames();
  overlay.classList.add("hidden");
  runSimulation(lastCount, lastNames);
});

// ------- Rerun / Change players — HARD reload (prevents any overlap)
rerunBtn.addEventListener("click", () => {
  const seed   = (Date.now() ^ Math.floor(Math.random() * 1e9));
  const names  = (lastNames && lastNames.length) ? lastNames : getEnteredNames();
  const count  = (lastNames && lastNames.length) ? lastCount : (parseInt(playerCountSel.value, 10) || 8);
  const link   = buildShareURL(seed, count, names);
  window.location.replace(link); // full navigation; no lingering animations
});

changeBtn.addEventListener("click", () => {
  const base = window.location.origin + window.location.pathname;
  window.location.replace(base); // clears seed/params so overlay opens fresh
});

shareBtn.addEventListener("click", copyShareLink);

// ------- Theme toggle
function applyTheme(mode){
  document.body.classList.toggle('theme-dark', mode === 'dark');
  if (themeIcon) themeIcon.textContent = (mode === 'dark') ? '🌙' : '☀️';
  try { localStorage.setItem('theme', mode); } catch {}
}
const savedTheme = (()=>{ try { return localStorage.getItem('theme') || 'light'; } catch { return 'light'; }})();
applyTheme(savedTheme);
if (themeToggle) {
  themeToggle.addEventListener('click', ()=>{
    const next = document.body.classList.contains('theme-dark') ? 'light' : 'dark';
    applyTheme(next);
  });
}

// ------- URL param bootstrap (auto-play for shared links)
function bootstrapFromURL() {
  const q = new URLSearchParams(window.location.search);
  const seedParam = q.get("seed");
  const npParam   = q.get("num_players");
  const urlNames  = q.getAll("name");

  if (seedParam) {
    let np = parseInt(npParam || "0", 10);
    if (!Number.isFinite(np) || np < 6 || np > 12) np = Math.min(12, Math.max(6, urlNames.length || 8));
    lastCount = np;

    lastNames = [];
    for (let i = 0; i < np; i++) lastNames.push(urlNames[i] ? String(urlNames[i]) : `Player ${i+1}`);

    // Sync overlay inputs for when user opens it later
    playerCountSel.value = String(np);
    ensureNamesBuilt();
    document.querySelectorAll(".nameInput").forEach((inp, i) => { inp.value = lastNames[i] || `Player ${i+1}`; });

    overlay.classList.add("hidden");
    runSimulation(lastCount, lastNames, parseInt(seedParam, 10));
  } else {
    ensureNamesBuilt();
  }
}

// ------- Initial draw/setup
window.addEventListener("load", () => {
  drawField(currentFence);
  batterFrame = 0;
  drawBatter();
  bootstrapFromURL();
});