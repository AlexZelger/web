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

// ------- Physics / world units
const G_FTPS2   = 32.174;                 // gravity (ft/s^2)
const PX_PER_FT = field.width / 520;      // 520 ft maps to canvas width
const GROUND_Y  = field.height - 40;      // baseline y (px)
const SCENE_X0  = 32;                     // shared origin (px)

// Keep pixel art crisp
ctx.imageSmoothingEnabled = false;

// ------- State
let currentFence = 400;
let lastCount = 8;
let lastNames = [];
let leaderName = "—";
let leaderDist = 0;

const delay = (ms) => new Promise(r => setTimeout(r, ms));

function ensureNamesBuilt() {
  const count = parseInt(playerCountSel?.value || "8", 10);
  rebuildNameInputs(Number.isFinite(count) ? count : 8);
  // optional: focus first input so it’s obvious they’re there
  const first = namesContainer.querySelector(".nameInput");
  if (first) first.focus();
}

// build immediately (don’t wait for onload)
ensureNamesBuilt();
// ------- Funny name generator
const firstParts = ["Rusty","Moose","Benny","Scooter","Duke","Lefty","Chip","Mickey","Sluggo","Ace","Boomer","Cactus","Turbo","Gonzo","Rizzo","Tex","Nacho","Goose","Beans","Ranger","Cookie","Pepper","Stubs","Wally","Chili","Spanky","Buster"];
const lastParts  = ["Thunder","McDinger","TwoBags","Laser","Barnstorm","Longball","Cannon","Krakatoa","Moonshot","Quickstep","Fireball","Nailbiter","PineTar","Screwball","Heatwave","PopFly","Fastball","Knuckle","Curve","Slider","Forkball","Rattler","Homer","TapeMeasure","Whiplash","Yardstick","Ringer"];
function rand(arr){ return Math.floor(Math.random()*arr.length); }
function generateFunnyNames(n) {
  const set = new Set(), out = [];
  while (out.length < n) {
    const name = `${["Rusty","Moose","Benny","Scooter","Duke","Lefty","Chip","Mickey","Sluggo","Ace","Boomer","Cactus","Turbo","Gonzo","Rizzo","Tex","Nacho","Goose","Beans","Ranger","Cookie","Pepper","Stubs","Wally","Chili","Spanky","Buster"][rand(firstParts)]} ${["Thunder","McDinger","TwoBags","Laser","Barnstorm","Longball","Cannon","Krakatoa","Moonshot","Quickstep","Fireball","Nailbiter","PineTar","Screwball","Heatwave","PopFly","Fastball","Knuckle","Curve","Slider","Forkball","Rattler","Homer","TapeMeasure","Whiplash","Yardstick","Ringer"][rand(lastParts)]}`;
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

// ------- Leaderboard (sorted items only for hitters who have gone)
function renderLeaderboard(items) {
  boardEl.innerHTML = "";
  items.forEach((p, index) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    const dist = document.createElement("span");
    name.textContent = `${index + 1}. ${p.name}`;
    dist.textContent = `${p.distance_ft.toFixed(2)} ft`;
    dist.className = "dist";
    li.appendChild(name);
    li.appendChild(dist);
    boardEl.appendChild(li);
  });
}

// ------- Field (minimal gray) + fence, ticks, plate, baseline
function drawField(fenceFt) {
  ctx.clearRect(0, 0, field.width, field.height);

  // Fence
  const fenceX = SCENE_X0 + fenceFt * PX_PER_FT;
  ctx.strokeStyle = "#334155";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(fenceX, 20);
  ctx.lineTo(fenceX, GROUND_Y);
  ctx.stroke();

  // Home plate
  ctx.fillStyle = "#4b5563";
  ctx.fillRect(SCENE_X0 - 12, GROUND_Y - 10, 12, 10);

  // Distance ticks / labels
  ctx.fillStyle = "#334155";
  ctx.font = "12px system-ui, -apple-system, Segoe UI, Roboto, Arial";
  for (let ft = 100; ft <= 500; ft += 50) {
    const x = SCENE_X0 + ft * PX_PER_FT;
    ctx.fillRect(x, GROUND_Y - 6, 1, 6);
    ctx.fillText(String(ft), x - 10, GROUND_Y - 10);
  }

  // Baseline
  ctx.fillStyle = "#6b7280";
  ctx.fillRect(0, GROUND_Y, field.width, 4);
}

// ------- Pixel batter (procedural)
const SPRITE_SCALE = 4;
const COLOR = {
  ".": null,
  "S": "#ffd7b1", // skin
  "U": "#1e3a8a", // uniform blue
  "K": "#0b1020", // outline
  "B": "#8b5a2b"  // bat
};
const FRAMES = [
  [
    "................","......KSSK......","......KSSK......",".......KK.......",
    "....UUUKKK......","...UUUUSSK......","...UUUUSSK......","....UUUSSK......",
    "......KSSK......","......KSSK......","......KSSK......",".....KUUUK......",
    "....KUUUUUK.....",".....KUUUK......","......KKK.......","................",
  ],
  [
    "................","......KSSK......","......KSSK......",".......KK.......",
    "....UUUKKK......","...UUUUSSK..BBB.","...UUUUSSK.BBB..","....UUUSSK.B....",
    "......KSSK......","......KSSK......","......KSSK......",".....KUUUK......",
    "....KUUUUUK.....",".....KUUUK......","......KKK.......","................",
  ],
  [
    "................","......KSSK......","......KSSK......",".......KK....B..",
    "....UUUKKK..BB..","...UUUUSSK.BB...","...UUUUSSKB.....","....UUUSSK......",
    "......KSSK......","......KSSK......","......KSSK......",".....KUUUK......",
    "....KUUUUUK.....",".....KUUUK......","......KKK.......","................",
  ],
];

let batterFrame = 0; // 0=stance, 1=mid, 2=follow-through
const BATTER_X = SCENE_X0 - 24;
function BATTER_Y() { return GROUND_Y; }

function drawPixelSprite(frame, x, y, scale) {
  const grid = FRAMES[frame];
  for (let r = 0; r < grid.length; r++) {
    const row = grid[r];
    for (let c = 0; c < row.length; c++) {
      const ch = row[c];
      const fill = COLOR[ch];
      if (!fill) continue;
      ctx.fillStyle = fill;
      ctx.fillRect(x + c*scale, (y - 16*scale) + r*scale, scale, scale);
    }
  }
}
function drawBatter() { drawPixelSprite(batterFrame, BATTER_X, BATTER_Y(), SPRITE_SCALE); }

async function animateSwing() {
  batterFrame = 0; drawField(currentFence); drawBatter(); await delay(100);
  batterFrame = 1; drawField(currentFence); drawBatter(); await delay(100);
  batterFrame = 2; drawField(currentFence); drawBatter(); await delay(100);
}

// ------- Ball
function drawBall(x, y) {
  ctx.beginPath();
  ctx.arc(x, y, 4, 0, Math.PI * 2);
  ctx.fillStyle = "#0ea5e9";
  ctx.fill();
}

// ------- Flight animation (y0 = 0; lands exactly at distanceFt)
function animateHit({ distanceFt, angleDeg }) {
  const theta = angleDeg * Math.PI / 180;
  const sin2theta = Math.sin(2 * theta);

  // Speed so range equals distanceFt (ideal, no drag)
  const v  = Math.sqrt((distanceFt * G_FTPS2) / Math.max(0.01, sin2theta));
  const vx = v * Math.cos(theta);
  const vy = v * Math.sin(theta);

  const T = (2 * vy) / G_FTPS2; // y0 = 0
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
        const landingX = SCENE_X0 + distanceFt * PX_PER_FT;
        drawBatter();
        drawBall(landingX, GROUND_Y - 2);
        resolve();
        return;
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  });
}

// ------- Simulation (GET version to match backend Option B)
async function runSimulation(numPlayers, names) {
  statusText.textContent         = "Fetching simulation...";
  currentPlayerText.textContent  = "—";
  boardEl.innerHTML = "";
  drawField(currentFence);
  drawBatter();

  // Build ?num_players=..&name=..&name=..
  const params = new URLSearchParams();
  params.set("num_players", String(numPlayers));
  (names || []).forEach(n => params.append("name", n));

  const res = await fetch(`/api/simulate?${params.toString()}`);
  const data = await res.json();

  // Scoreboard/meta
  sbRun.textContent     = data.run_id;
  sbPlayers.textContent = data.players.length;
  fenceFtEl.textContent = data.fence_ft;
  currentFence          = data.fence_ft;

  // Reset leader outputs
  leaderName = "—";
  leaderDist = 0;
  sbLeader.textContent  = "—";
  sbDist.textContent    = "—";
  sbAtBat.textContent   = "—";

  // Incremental leaderboard
  const seen = [];

  for (let i = 0; i < data.players.length; i++) {
    const p = data.players[i];

    currentPlayerText.textContent = `${p.name} — ${p.distance_ft.toFixed(2)} ft @ ${p.angle_deg}°`;
    statusText.textContent        = "Swing!";
    sbAtBat.textContent           = p.name;
    sbDist.textContent            = "—";

    // Pre-swing stance, then swing animation
    batterFrame = 0;
    drawField(currentFence);
    drawBatter();
    await animateSwing();

    // Flight and landing
    await animateHit({ distanceFt: p.distance_ft, angleDeg: p.angle_deg });

    // Seen + sort + render only hitters who have gone
    seen.push(p);
    const sortedSeen = seen.slice().sort((a,b)=> b.distance_ft - a.distance_ft);
    renderLeaderboard(sortedSeen);

    // Update leader row
    leaderName = sortedSeen[0].name;
    leaderDist = sortedSeen[0].distance_ft;
    sbLeader.textContent = `${leaderName} (${leaderDist.toFixed(0)} FT)`;

    sbDist.textContent   = `${p.distance_ft.toFixed(0)} FT`;
    statusText.textContent = "Landed";
    await delay(350);
  }

  // Final summary
  const winner = seen[0];
  currentPlayerText.textContent = `Winner: ${winner.name} — ${winner.distance_ft.toFixed(2)} ft`;
  statusText.textContent        = "Complete";
  sbAtBat.textContent           = `WIN: ${winner.name}`;
  sbLeader.textContent          = `${winner.name} (${winner.distance_ft.toFixed(0)} FT)`;
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

rerunBtn.addEventListener("click", () => runSimulation(lastCount, lastNames));
changeBtn.addEventListener("click", () => {
  overlay.classList.remove("hidden");
  // rebuild in case DOM was cleared or user changed count earlier
  ensureNamesBuilt();
});

// ------- Theme toggle wiring

// Update: applyTheme should update BOTH icons
function applyTheme(mode){
  document.body.classList.toggle('theme-dark', mode === 'dark');
  const icon = (mode === 'dark') ? '🌙' : '☀️';
  if (themeIcon) themeIcon.textContent = icon;
  try { localStorage.setItem('theme', mode); } catch {}
}

// Init from storage (existing)
const savedTheme = (()=>{ try { return localStorage.getItem('theme') || 'light'; } catch { return 'light'; }})();
applyTheme(savedTheme);

// Click wiring (existing top-right button)
if (themeToggle) {
  themeToggle.addEventListener('click', ()=>{
    const next = document.body.classList.contains('theme-dark') ? 'light' : 'dark';
    applyTheme(next);
  });
}