// ---- Elements
const overlay = document.getElementById("menuOverlay");
const playerCountSel = document.getElementById("playerCount");
const startBtn = document.getElementById("startBtn");
const randomizeNamesBtn = document.getElementById("randomizeNamesBtn");
const namesContainer = document.getElementById("namesContainer");

const runIdEl = document.getElementById("runId");
const seedEl = document.getElementById("seed");
const fenceFtEl = document.getElementById("fenceFt");
const boardEl = document.getElementById("leaderboard");
const currentEl = document.getElementById("currentPlayer");
const statusEl = document.getElementById("status");

const field = document.getElementById("field");
const ctx = field.getContext("2d");

const rerunBtn = document.getElementById("rerunBtn");
const changeBtn = document.getElementById("changeBtn");

// ---- Physics/visual constants
const G_FTPS2 = 32.174;              // gravity ft/s^2
const PX_PER_FT = field.width / 520; // 520 ft -> canvas width
const GROUND_Y = field.height - 40;  // ground baseline y in px

let lastCount = 8;
let lastNames = [];

// ---- Funny name generator
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

// ---- UI helpers
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

// ---- Field drawing
function drawField(fenceFt) {
  ctx.clearRect(0, 0, field.width, field.height);

  // Ground
  ctx.fillStyle = "#94a3b8";
  ctx.fillRect(0, GROUND_Y, field.width, field.height - GROUND_Y);

  // Fence
  const fenceX = fenceFt * PX_PER_FT;
  ctx.strokeStyle = "#334155";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(fenceX, 20);
  ctx.lineTo(fenceX, GROUND_Y);
  ctx.stroke();

  // Plate
  ctx.fillStyle = "#e11d48";
  ctx.fillRect(20, GROUND_Y - 10, 12, 10);

  // Distance ticks
  ctx.fillStyle = "#334155";
  ctx.font = "12px system-ui, -apple-system, Segoe UI, Roboto, Arial";
  for (let ft = 100; ft <= 500; ft += 50) {
    const x = ft * PX_PER_FT;
    ctx.fillRect(x, GROUND_Y - 6, 1, 6);
    ctx.fillText(String(ft), x - 10, GROUND_Y - 10);
  }
}

function drawBall(x, y) {
  ctx.fillStyle = "#0ea5e9";
  ctx.beginPath();
  ctx.arc(x, y, 5, 0, Math.PI * 2);
  ctx.fill();
}

function drawBat(swingPhase = 0) {
  const baseX = 34, baseY = GROUND_Y - 18;
  ctx.save();
  ctx.translate(baseX, baseY);
  ctx.rotate((-25 + swingPhase * 15) * Math.PI / 180);
  ctx.fillStyle = "#f59e0b";
  ctx.fillRect(0, -3, 28, 6);
  ctx.restore();
}

// ---- Animation (time-based stop; works reliably)
function animateHit({ distanceFt, angleDeg }, fenceFt) {
  const theta = angleDeg * Math.PI / 180;
  const sin2theta = Math.sin(2 * theta);
  const v = Math.sqrt((distanceFt * G_FTPS2) / Math.max(0.01, sin2theta));
  const vx = v * Math.cos(theta);
  const vy = v * Math.sin(theta);

  const T = (2 * vy) / G_FTPS2;   // ideal time of flight
  const durationMs = 2800;
  const speedup = T > 0 ? (T / (durationMs / 1000)) : 1;

  const start = performance.now();
  return new Promise(resolve => {
    function frame(now) {
      const t = Math.min((now - start) / 1000 * speedup, T);
      const x_ft = vx * t;
      const y_ft = (vy * t) - 0.5 * G_FTPS2 * t * t;

      const x = 32 + x_ft * PX_PER_FT;
      const y = GROUND_Y - (y_ft * PX_PER_FT);

      drawField(fenceFt);
      drawBat(1);
      drawBall(x, y);

      if (t >= T) {
        const landingX = 32 + (vx * T) * PX_PER_FT;
        drawBall(landingX, GROUND_Y - 2);
        resolve();
        return;
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  });
}

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

// ---- Simulation runner
async function runSimulation(numPlayers, names) {
  statusEl.textContent = "Fetching simulation...";
  currentEl.textContent = "—";
  boardEl.innerHTML = "";
  drawField(400);

  const params = new URLSearchParams();
  params.set("num_players", String(numPlayers));
  (names || []).forEach(n => params.append("name", n));
  const res = await fetch(`/api/simulate?${params.toString()}`);
  const data = await res.json();

  runIdEl.textContent = data.run_id;
  seedEl.textContent = data.seed;
  fenceFtEl.textContent = data.fence_ft;

  renderLeaderboard(data.players.map((p, i) => ({ ...p, place: i + 1 })));

  for (let i = 0; i < data.players.length; i++) {
    const p = data.players[i];
    currentEl.textContent = `${p.name} — ${p.distance_ft.toFixed(2)} ft @ ${p.angle_deg}°`;
    statusEl.textContent = "Swing!";
    drawField(data.fence_ft);
    drawBat(0);
    await new Promise(r => setTimeout(r, 350));
    await animateHit({ distanceFt: p.distance_ft, angleDeg: p.angle_deg }, data.fence_ft);
    statusEl.textContent = "Landed";
    await new Promise(r => setTimeout(r, 350));
  }

  renderLeaderboard(data.placements);
  const winner = data.placements[0];
  currentEl.textContent = `Winner: ${winner.name} — ${winner.distance_ft.toFixed(2)} ft`;
  statusEl.textContent = "Complete";
}

// ---- Menu wiring
function syncInputsToCount() {
  const count = parseInt(playerCountSel.value, 10);
  rebuildNameInputs(count);
}
playerCountSel.addEventListener("change", syncInputsToCount);

randomizeNamesBtn.addEventListener("click", () => {
  const count = parseInt(playerCountSel.value, 10);
  const funny = generateFunnyNames(count);
  document.querySelectorAll(".nameInput").forEach((inp, i) => {
    inp.value = funny[i];
  });
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
});

// ---- Initial draw/setup
window.addEventListener("load", () => {
  drawField(400);
  // Build default inputs for the initial selected count (8)
  rebuildNameInputs(parseInt(playerCountSel.value, 10));
});