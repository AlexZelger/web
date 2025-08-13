// ------- Element refs
const overlay = document.getElementById("menuOverlay");
const playerCountSel = document.getElementById("playerCount");
const startBtn = document.getElementById("startBtn");
const randomizeNamesBtn = document.getElementById("randomizeNamesBtn");
const namesContainer = document.getElementById("namesContainer");

const boardEl = document.getElementById("leaderboard");
const currentEl = document.getElementById("currentPlayer");
const statusEl = document.getElementById("status");

const field = document.getElementById("field");
const ctx = field.getContext("2d");

// Scoreboard refs
const sbAtBat   = document.getElementById("sbAtBat");
const sbDist    = document.getElementById("sbDist");
const sbPlayers = document.getElementById("sbPlayers");
const sbSeed    = document.getElementById("sbSeed");
const sbRun     = document.getElementById("sbRun");
const fenceFtEl = document.getElementById("fenceFt");

const rerunBtn  = document.getElementById("rerunBtn");
const changeBtn = document.getElementById("changeBtn");

// ------- Physics / world units
const G_FTPS2  = 32.174;                 // gravity (ft/s^2)
const PX_PER_FT = field.width / 520;     // 520 ft maps to canvas width
const GROUND_Y  = field.height - 40;     // baseline y (px)

// One shared horizontal origin so ticks, fence, plate, and ball all align
const SCENE_X0 = 32;                     // left margin (px)

// Keep pixel art crisp
ctx.imageSmoothingEnabled = false;

// ------- State
let currentFence = 400;
let lastCount = 8;
let lastNames = [];

const delay = (ms) => new Promise(r => setTimeout(r, ms));

// ------- Funny name generator
const firstParts = ["Rusty","Moose","Benny","Scooter","Duke","Lefty","Chip","Mickey","Sluggo","Ace","Boomer","Cactus","Turbo","Gonzo","Rizzo","Tex","Nacho","Goose","Beans","Ranger","Cookie","Pepper","Stubs","Wally","Chili","Spanky","Buster"];
const lastParts  = ["Thunder","McDinger","TwoBags","Laser","Barnstorm","Longball","Cannon","Krakatoa","Moonshot","Quickstep","Fireball","Nailbiter","PineTar","Screwball","Heatwave","PopFly","Fastball","Knuckle","Curve","Slider","Forkball","Rattler","Homer","TapeMeasure","Whiplash","Yardstick","Ringer"];
function rand(arr){ return arr[Math.floor(Math.random()*arr.length)]; }
function generateFunnyNames(n) {
  const set = new Set(), out = [];
  while (out.length < n) {
    const name = `${rand(firstParts)} ${rand(lastParts)}`;
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

// ------- Leaderboard
function renderLeaderboard(items) {
  boardEl.innerHTML = "";
  items.forEach(p => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    const dist = document.createElement("span");
    name.textContent = `${p.place}. ${p.name}`;
    dist.textContent = `${p.distance_ft.toFixed(2)} ft`;
    dist.className = "dist";
    li.appendChild(name);
    li.appendChild(dist);
    boardEl.appendChild(li);
  });
}

// ------- Field (minimal gray) with ticks, fence, plate, and a visible baseline
function drawField(fenceFt) {
  ctx.clearRect(0, 0, field.width, field.height);

  // Fence (aligns with same origin as ticks and ball)
  const fenceX = SCENE_X0 + fenceFt * PX_PER_FT;
  ctx.strokeStyle = "#334155";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(fenceX, 20);
  ctx.lineTo(fenceX, GROUND_Y);
  ctx.stroke();

  // Home plate marker
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

  // Visible baseline (acts as ground)
  ctx.fillStyle = "#6b7280"; // medium gray line
  ctx.fillRect(0, GROUND_Y, field.width, 4);
}

// ------- Pixel batter (procedural sprite)
const SPRITE_SCALE = 4;
const COLOR = {
  ".": null,
  "S": "#ffd7b1", // skin
  "U": "#1e3a8a", // uniform blue
  "K": "#0b1020", // outline
  "B": "#8b5a2b"  // bat
};
// 16x16-ish grids; very simple 3-frame swing
const FRAMES = [
  [
    "................",
    "......KKKKK.....",
    "......KKKK......",
    ".......KK.......",
    "......UUUU......",
    "......UUUU......",
    "......UUUU......",
    "......UUUU......",
    "......UUUU......",
    "......UUUU......",
    "......UUUU......",
    "......UUUU......",
    ".....UU..U......",
    ".....K...K......",
    "....KK...KK.....",
    "................",
  ],
  [
    "................",
    "......KKKKK.....",
    "......KKKK......",
    ".......KK.....B.",
    "......UUUU..BBB.",
    "......UUUU.BBB..",
    "......UUUU.BB...",
    "......UUUU.B....",
    "......UUUU......",
    "......UUUU......",
    "......UUUU......",
    "......UUUU......",
    ".....UU..U......",
    ".....K...K......",
    "....KK...KK.....",
    "................",
  ],
  [
    "................",
    "......KKKKK.....",
    "......KKKK......",
    ".......KK....B..",
    "......UUUU..BB..",
    "......UUUU.BB...",
    "......UUUUB.....",
    "......UUUU......",
    "......UUUU......",
    "......UUUU......",
    "......UUUU......",
    "......UUUU......",
    ".....UU..U......",
    ".....K...K......",
    "....KK...KK.....",
    "................",
  ],
];

let batterFrame = 0; // 0=stance, 1=mid, 2=follow-through

// Batter X aligned near the origin; sprite width is 16*scale=64px
const BATTER_X = SCENE_X0 - 24; // slightly left of the plate
function BATTER_Y() { return GROUND_Y; } // feet on baseline

function drawPixelSprite(frame, x, y, scale) {
  const grid = FRAMES[frame];
  for (let r = 0; r < grid.length; r++) {
    const row = grid[r];
    for (let c = 0; c < row.length; c++) {
      const ch = row[c];
      const fill = COLOR[ch];
      if (!fill) continue;
      ctx.fillStyle = fill;
      // y is baseline; subtract full sprite height so feet sit on baseline
      ctx.fillRect(x + c*scale, (y - 16*scale) + r*scale, scale, scale);
    }
  }
}
function drawBatter() {
  drawPixelSprite(batterFrame, BATTER_X, BATTER_Y(), SPRITE_SCALE);
}

// Small pre-hit swing; leaves batter on frame 2
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

  // Choose speed so ideal range equals distanceFt (no drag, y0=0)
  const v  = Math.sqrt((distanceFt * G_FTPS2) / Math.max(0.01, sin2theta));
  const vx = v * Math.cos(theta);
  const vy = v * Math.sin(theta);

  // Time of flight for y0 = 0
  const T = (2 * vy) / G_FTPS2;

  // Normalize visual duration (~2.8s)
  const durationMs = 2800;
  const speedup = T > 0 ? (T / (durationMs / 1000)) : 1;

  const start = performance.now();
  return new Promise(resolve => {
    function frame(now) {
      const t = Math.min((now - start) / 1000 * speedup, T);

      // Projectile (feet)
      const x_ft = vx * t;
      const y_ft = (vy * t) - 0.5 * G_FTPS2 * t * t;

      // Map to pixels with SAME origin as ticks/fence
      const x = SCENE_X0 + x_ft * PX_PER_FT;
      const y = GROUND_Y - (y_ft * PX_PER_FT);

      drawField(currentFence);
      drawBatter();      // keep batter visible
      drawBall(x, y);

      if (t >= T) {
        // Land exactly at the reported distance along baseline
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
  statusEl.textContent = "Fetching simulation...";
  currentEl.textContent = "—";
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
  sbSeed.textContent    = data.seed;
  sbPlayers.textContent = data.players.length;
  fenceFtEl.textContent = data.fence_ft;
  currentFence          = data.fence_ft;

  // Pre-populate board so it's not empty during play
  renderLeaderboard(data.players.map((p, i) => ({ ...p, place: i + 1 })));

  // Each hitter
  for (let i = 0; i < data.players.length; i++) {
    const p = data.players[i];

    currentEl.textContent = `${p.name} — ${p.distance_ft.toFixed(2)} ft @ ${p.angle_deg}°`;
    statusEl.textContent  = "Swing!";
    sbAtBat.textContent   = p.name;
    sbDist.textContent    = "—";

    // Pre-swing stance, then swing animation (batter persists in frame 2)
    batterFrame = 0;
    drawField(currentFence);
    drawBatter();
    await animateSwing();

    // Flight and landing
    await animateHit({ distanceFt: p.distance_ft, angleDeg: p.angle_deg });

    sbDist.textContent = `${p.distance_ft.toFixed(0)} FT`;
    await delay(350);
  }

  // Final placements
  renderLeaderboard(data.placements);
  const winner = data.placements[0];
  currentEl.textContent = `Winner: ${winner.name} — ${winner.distance_ft.toFixed(2)} ft`;
  statusEl.textContent  = "Complete";
  sbAtBat.textContent   = `WIN: ${winner.name}`;
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
changeBtn.addEventListener("click", () => { overlay.classList.remove("hidden"); });

// ------- Initial draw/setup
window.addEventListener("load", () => {
  drawField(currentFence);
  batterFrame = 0;
  drawBatter();
  rebuildNameInputs(parseInt(playerCountSel.value, 10)); // default inputs for selected count
});