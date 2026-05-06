/**
 * Wolf Pack Evolution Simulator
 * A genetic algorithm where wolves learn to hunt through natural selection.
 *
 * Architecture overview:
 * - Each wolf has a genome of 6 behavioral weight genes (no hardcoded hunting logic).
 * - Every tick, wolves compute a movement vector by summing weighted attractions/repulsions
 *   toward everything they can sense within their perception radius.
 * - Food is earned purely by proximity — no "decide to eat" logic.
 * - At generation end, the top 50% by food collected (the "elite") are selected as parents.
 *   Their genes are mutated and passed to the next generation.
 * - Pack hunting emerges naturally: wolves with high wolfAttract + deerAttract
 *   cluster near deer together, triggering the pack kill condition.
 */

// ── Canvas setup ────────────────────────────────────────────────────────────
const CV  = document.getElementById('c');
const ctx = CV.getContext('2d');
const W   = CV.width;
const H   = CV.height;

// ── Simulation config ────────────────────────────────────────────────────────
const C = {
  WOLVES:           10,
  RABBITS:          3,
  DEER:             7,
  TICKS:            1000,  // ticks per generation
  RABBIT_FOOD:      15,    // food reward for catching a rabbit (solo)
  DEER_FOOD:        150,   // food reward for a deer kill (split among pack)
  DEER_PACK:        3,     // minimum wolves within DEER_RADIUS to trigger a deer kill
  DEER_RADIUS:      55,    // px — proximity radius for pack kill check
  BEAR_KILL_R:      22,    // px — wolf dies instantly if within this range of the bear
  // Energy is spent only when moving. Drain per tick =
  //   moved * (1 + speed_gene * ENERGY_SPEED_FACTOR) * ENERGY_PER_DIST
  // Faster wolves both cover more ground per tick AND pay more per unit distance.
  ENERGY_PER_DIST:    0.25, // base energy cost per unit of distance moved
  ENERGY_SPEED_FACTOR: 0.30, // extra per-distance penalty scaled by speed gene
  MUTATION:         0.12,  // mutation scale factor applied to all genes
  ELITE_COUNT:      3,     // ONLY the top 3 wolves by food collected reproduce
  WANDER_SPD:       0.7,   // base wander speed — always active, not a gene
  WANDER_TURN:      0.18,  // max wander angle change per tick (radians)
};

// ── Gene system ──────────────────────────────────────────────────────────────
/**
 * Genes are pure behavioral weights — none of them hardcode a specific behavior.
 *
 * wolfAttract    (0–1)    : attraction toward nearby wolves
 * deerAttract    (0–1)    : attraction toward deer
 * rabbitAttract  (0–1)    : attraction toward rabbits
 * bearRepel      (0–1)    : repulsion from the bear
 * speed          (0.3–3)  : max movement per tick
 * perception     (10–140) : radius in px within which the wolf senses anything
 *
 * All genes start near 0 (x*x distribution) so the population must earn
 * competence through selection rather than starting already capable.
 */
function lowRand(scale) {
  return Math.random() * Math.random() * scale;
}

function randGene() {
  return {
    wolfAttract:   lowRand(1),
    deerAttract:   lowRand(1),
    rabbitAttract: lowRand(1),
    bearRepel:     lowRand(1),
    speed:         0.3 + lowRand(1.2),  // 0.3–1.5 skewed low
    perception:    45  + lowRand(30),   // 15–45px skewed low
  };
}

/**
 * Produces a child genome by applying Gaussian-like mutations to a parent genome.
 * Each gene is nudged by a random amount scaled by C.MUTATION, then clamped.
 */
function mutate(g) {
  const m = C.MUTATION;
  function mg(v, lo, hi, s) {
    return Math.max(lo, Math.min(hi, v + (Math.random() - 0.5) * 2 * s * m));
  }
  return {
    wolfAttract:   mg(g.wolfAttract,   0,   1,    0.8),
    deerAttract:   mg(g.deerAttract,   0,   1,    0.8),
    rabbitAttract: mg(g.rabbitAttract, 0,   1,    0.8),
    bearRepel:     mg(g.bearRepel,     0,   1,    0.8),
    speed:         mg(g.speed,         0.1, 3.0,  0.35),
    perception:    mg(g.perception,    10,  140,  12),
  };
}

// ── State ────────────────────────────────────────────────────────────────────
let wolves   = [];
let rabbits  = [];
let deer     = [];
let bear     = null;
let gen      = 1;
let tick     = 0;
let paused   = false;
let simSpeed = 3;

// Per-generation counters (reset in initGen)
let bearKills = 0;
let deerKills = 0;
let foodEaten = 0;

// ── Utility ──────────────────────────────────────────────────────────────────
function dist(a, b) {
  return Math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2);
}
function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}
function rand(lo, hi) {
  return lo + Math.random() * (hi - lo);
}

// ── Entity factories ─────────────────────────────────────────────────────────
function makeWolf(g) {
  return {
    x: rand(20, W - 20),
    y: rand(20, H - 20),
    vx: 0,
    vy: 0,
    // wanderAngle is NOT a gene — it's baseline locomotion.
    // Every wolf always moves; genes only bias the direction.
    wanderAngle: Math.random() * Math.PI * 2,
    g: g || randGene(),
    energy: 200,
    food: 0,
    alive: true,
    dead_tick: -1,
    killedByBear: false,
  };
}

function makePrey(type) {
  return {
    x: rand(20, W - 20),
    y: rand(20, H - 20),
    type,
    alive: true,
    vx: rand(-0.5, 0.5),
    vy: rand(-0.5, 0.5),
  };
}

function makeBear() {
  return {
    x: rand(60, W - 60),
    y: rand(60, H - 60),
    vx: 0,
    vy: 0,
    angle: Math.random() * Math.PI * 2,
    timer: 0,
  };
}

// ── Generation init ──────────────────────────────────────────────────────────
/**
 * Starts a new generation. If an elite gene pool is provided, each wolf
 * is created from a mutated copy of a parent gene. Otherwise genes are random.
 */
function initGen(pool) {
  wolves = [];
  if (pool && pool.length) {
    for (let i = 0; i < C.WOLVES; i++) {
      wolves.push(makeWolf(mutate(pool[i % pool.length])));
    }
  } else {
    for (let i = 0; i < C.WOLVES; i++) {
      wolves.push(makeWolf());
    }
  }
  rabbits   = Array.from({ length: C.RABBITS }, () => makePrey('rabbit'));
  deer      = Array.from({ length: C.DEER },    () => makePrey('deer'));
  bear      = makeBear();
  tick      = 0;
  bearKills = 0;
  deerKills = 0;
  foodEaten = 0;
}

// ── Simulation step ──────────────────────────────────────────────────────────
function step() {
  tick++;

  // Bear: pure smooth random wander — no wolf awareness whatsoever.
  // Wolves learn to avoid it purely because wandering into it kills them.
  bear.timer--;
  if (bear.timer <= 0) {
    bear.angle += rand(-0.45, 0.45);
    bear.timer  = Math.floor(rand(25, 70));
  }
  bear.vx = bear.vx * 0.9 + Math.cos(bear.angle) * 0.85 * 0.1;
  bear.vy = bear.vy * 0.9 + Math.sin(bear.angle) * 0.85 * 0.1;
  bear.x  = clamp(bear.x + bear.vx, 10, W - 10);
  bear.y  = clamp(bear.y + bear.vy, 10, H - 10);
  if (bear.x <= 10 || bear.x >= W - 10) { bear.vx *= -1; bear.angle = Math.atan2(bear.vy, bear.vx); }
  if (bear.y <= 10 || bear.y >= H - 10) { bear.vy *= -1; bear.angle = Math.atan2(bear.vy, bear.vx); }

  // Prey: gentle random wander
  for (const r of rabbits) {
    if (!r.alive) continue;
    r.x = clamp(r.x + r.vx, 5, W - 5);
    r.y = clamp(r.y + r.vy, 5, H - 5);
    if (r.x <= 5 || r.x >= W - 5) r.vx *= -1;
    if (r.y <= 5 || r.y >= H - 5) r.vy *= -1;
    if (Math.random() < 0.04) { r.vx = rand(-0.6, 0.6); r.vy = rand(-0.6, 0.6); }
  }
  for (const d of deer) {
    if (!d.alive) continue;
    d.x = clamp(d.x + d.vx * 0.45, 5, W - 5);
    d.y = clamp(d.y + d.vy * 0.45, 5, H - 5);
    if (d.x <= 5 || d.x >= W - 5) d.vx *= -1;
    if (d.y <= 5 || d.y >= H - 5) d.vy *= -1;
    if (Math.random() < 0.02) { d.vx = rand(-0.45, 0.45); d.vy = rand(-0.45, 0.45); }
  }

  const alive = wolves.filter(w => w.alive);

  for (const w of alive) {
    // Bear contact = instant death (bear doesn't chase — wolf wandered in)
    if (dist(w, bear) < C.BEAR_KILL_R) {
      w.alive        = false;
      w.dead_tick    = tick;
      w.killedByBear = true;
      bearKills++;
      continue;
    }

    // ── Wander drive (not a gene — always active) ──────────────────────────
    // Drift the wander angle slightly each tick for organic movement.
    w.wanderAngle += rand(-C.WANDER_TURN, C.WANDER_TURN);

    // Nudge angle away from walls so wolves don't pile up in corners
    const margin = 35;
    if (w.x < margin)     w.wanderAngle = rand(-Math.PI * 0.3, Math.PI * 0.3);
    if (w.x > W - margin) w.wanderAngle = Math.PI + rand(-Math.PI * 0.3, Math.PI * 0.3);
    if (w.y < margin)     w.wanderAngle = Math.PI * 0.5 + rand(-Math.PI * 0.3, Math.PI * 0.3);
    if (w.y > H - margin) w.wanderAngle = -Math.PI * 0.5 + rand(-Math.PI * 0.3, Math.PI * 0.3);

    const wx = Math.cos(w.wanderAngle) * C.WANDER_SPD;
    const wy = Math.sin(w.wanderAngle) * C.WANDER_SPD;

    // ── Gene-driven attraction/repulsion bias ──────────────────────────────
    // For each thing within perception radius, add a directional force weighted
    // by the relevant gene. Gene = 0 → no contribution. Gene = 1 → full pull.
    // This is the only place genes influence behavior.
    let bx = 0, by = 0;
    const p = w.g.perception;

    // Wolf attraction (with short-range repulsion to prevent stacking).
    // Also count nearby packmates here so we can scale deer attraction below.
    let nearbyPack = 0;
    for (const o of alive) {
      if (o === w) continue;
      const d = dist(w, o);
      if (d === 0 || d >= p) continue;
      nearbyPack++;
      const nx = (o.x - w.x) / d;
      const ny = (o.y - w.y) / d;
      const wt = d < 12 ? -0.3 : w.g.wolfAttract;
      bx += nx * wt;
      by += ny * wt;
    }

    // Pack readiness: how close this wolf is to having a viable hunt party.
    // 0 packmates → 0× deer pull (lone wolves ignore deer entirely).
    // 1 packmate  → 0.5× (mild interest, helps the trio converge).
    // 2+ packmates → 1× (full chase — pack kill is in reach).
    // The denominator is DEER_PACK - 1 because the wolf itself counts.
    const packReadiness = C.DEER_PACK > 1
      ? Math.min(1, nearbyPack / (C.DEER_PACK - 1))
      : 1;

    // Deer attraction (gated by pack readiness so lone wolves don't get stuck)
    for (const d of deer) {
      if (!d.alive) continue;
      const dd = dist(w, d);
      if (dd >= p) continue;
      const nx = (d.x - w.x) / dd;
      const ny = (d.y - w.y) / dd;
      bx += nx * w.g.deerAttract * 2.0 * packReadiness;
      by += ny * w.g.deerAttract * 2.0 * packReadiness;
    }

    // Rabbit attraction
    for (const r of rabbits) {
      if (!r.alive) continue;
      const rd = dist(w, r);
      if (rd >= p) continue;
      const nx = (r.x - w.x) / rd;
      const ny = (r.y - w.y) / rd;
      bx += nx * w.g.rabbitAttract * 2.0;
      by += ny * w.g.rabbitAttract * 2.0;
    }

    // Bear repulsion
    const bd = dist(w, bear);
    if (bd < p) {
      const nx = (w.x - bear.x) / bd;
      const ny = (w.y - bear.y) / bd;
      bx += nx * w.g.bearRepel * 3.5 / (bd * 0.03 + 1);
      by += ny * w.g.bearRepel * 3.5 / (bd * 0.03 + 1);
    }

    // Blend wander + gene bias, cap at wolf's speed gene
    const tx  = wx + bx;
    const ty  = wy + by;
    const tl  = Math.sqrt(tx * tx + ty * ty) || 1;
    const spd = Math.max(C.WANDER_SPD, w.g.speed);
    w.vx = w.vx * 0.4 + (tx / tl) * spd * 0.6;
    w.vy = w.vy * 0.4 + (ty / tl) * spd * 0.6;
    const sl = Math.sqrt(w.vx ** 2 + w.vy ** 2) || 1;
    if (sl > spd) { w.vx = w.vx / sl * spd; w.vy = w.vy / sl * spd; }
    const prevX = w.x, prevY = w.y;
    w.x = clamp(w.x + w.vx, 5, W - 5);
    w.y = clamp(w.y + w.vy, 5, H - 5);

    // ── Energy cost of movement ───────────────────────────────────────────
    // Pay only for actual distance covered (after wall clamping). Higher speed
    // gene means a higher per-distance multiplier, so fast wolves burn through
    // energy faster on top of just covering more ground per tick.
    const moved = Math.sqrt((w.x - prevX) ** 2 + (w.y - prevY) ** 2);
    w.energy -= moved * (1 + w.g.speed * C.ENERGY_SPEED_FACTOR) * C.ENERGY_PER_DIST;
    if (w.energy <= 0) {
      w.alive = false;
      w.dead_tick = tick;
      continue;
    }

    // ── Eating (proximity-triggered only) ─────────────────────────────────
    // Wolves don't "decide" to eat — physical contact with prey triggers it.

    // Rabbit: solo kill
    for (const r of rabbits) {
      if (!r.alive) continue;
      if (dist(w, r) < 11) {
        r.alive   = false;
        w.food   += C.RABBIT_FOOD;
        w.energy  = Math.min(100, w.energy + 20);
        foodEaten += C.RABBIT_FOOD;
      }
    }

    // Deer: pack kill only — wolves can ONLY attack deer when at least
    // C.DEER_PACK (3) wolves are within DEER_RADIUS of the deer simultaneously.
    // A single wolf, or even two, will bounce off a deer with no effect.
    for (const d of deer) {
      if (!d.alive) continue;
      if (dist(w, d) < 13) {
        // Count every wolf within DEER_RADIUS, including this one.
        const pack = alive.filter(o => dist(o, d) < C.DEER_RADIUS);
        if (pack.length >= C.DEER_PACK) {
          d.alive    = false;
          deerKills++;
          const share = C.DEER_FOOD / pack.length;
          for (const h of pack) {
            h.food   += share;
            h.energy  = Math.min(100, h.energy + 30);
          }
          foodEaten += C.DEER_FOOD;
        }
      }
    }
  }
}

// ── Natural selection ────────────────────────────────────────────────────────
/**
 * Called at the end of each generation.
 * Ranks ALL wolves by food collected (alive or not). Only the top ELITE_COUNT
 * (3) reproduce — every wolf in the next generation is a mutated copy of one
 * of these three. Distribution is round-robin via pool[i % pool.length], so
 * with 10 wolves the top three get roughly 4/3/3 children respectively.
 */
function endGen() {
  const all     = [...wolves].sort((a, b) => b.food - a.food);
  const keepN   = Math.min(C.ELITE_COUNT, all.length);
  const elite   = all.slice(0, keepN).map(w => w.g);

  const survived = wolves.filter(w => w.alive).length;
  const bk       = wolves.filter(w => w.killedByBear).length;
  const avgWA    = (elite.reduce((s, g) => s + g.wolfAttract,   0) / elite.length).toFixed(2);
  const avgDA    = (elite.reduce((s, g) => s + g.deerAttract,   0) / elite.length).toFixed(2);
  const avgRA    = (elite.reduce((s, g) => s + g.rabbitAttract, 0) / elite.length).toFixed(2);
  const topFood  = Math.round(all[0]?.food || 0);

  log(`Gen ${gen}: survived ${survived}/${C.WOLVES} | deer ${deerKills} | bear ${bk} | top food ${topFood} | wA:${avgWA} dA:${avgDA} rA:${avgRA}`);
  gen++;
  initGen(elite);
}

// ── Logging ──────────────────────────────────────────────────────────────────
function log(msg) {
  const el = document.getElementById('log');
  el.textContent += (el.textContent ? '\n' : '') + msg;
  el.scrollTop = el.scrollHeight;
}

// ── Panel update ─────────────────────────────────────────────────────────────
function updatePanel() {
  const alive = wolves.filter(w => w.alive);

  document.getElementById('gn').textContent = gen;
  document.getElementById('tk').textContent = tick;
  document.getElementById('aw').textContent = alive.length;
  document.getElementById('fe').textContent = Math.round(foodEaten);
  document.getElementById('dk').textContent = deerKills;
  document.getElementById('bk').textContent = bearKills;

  const src = alive.length ? alive : wolves;
  if (!src.length) return;

  const avg = k => src.reduce((s, w) => s + w.g[k], 0) / src.length;
  const wa = avg('wolfAttract');
  const da = avg('deerAttract');
  const ra = avg('rabbitAttract');
  const ba = avg('bearRepel');
  const sp = avg('speed');
  const pe = avg('perception');

  document.getElementById('ga').textContent  = wa.toFixed(2);
  document.getElementById('gd').textContent  = da.toFixed(2);
  document.getElementById('gr').textContent  = ra.toFixed(2);
  document.getElementById('gb2').textContent = ba.toFixed(2);
  document.getElementById('gs').textContent  = sp.toFixed(2);
  document.getElementById('gp').textContent  = Math.round(pe);

  document.getElementById('ba').style.width    = (wa * 100) + '%';
  document.getElementById('bd').style.width    = (da * 100) + '%';
  document.getElementById('br').style.width    = (ra * 100) + '%';
  document.getElementById('bb').style.width    = (ba * 100) + '%';
  document.getElementById('bspd').style.width  = ((sp - 0.1) / 2.9 * 100) + '%';
  document.getElementById('bprc').style.width  = ((pe - 10) / 130 * 100) + '%';

  const best = src.reduce((a, b) => b.food > a.food ? b : a, src[0]);
  document.getElementById('bf').textContent = Math.round(best.food);
}

// ── Renderer ─────────────────────────────────────────────────────────────────
function draw() {
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#111827';
  ctx.fillRect(0, 0, W, H);

  // Subtle background grid
  ctx.strokeStyle = 'rgba(255,255,255,0.02)';
  for (let x = 0; x < W; x += 40) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
  for (let y = 0; y < H; y += 40) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }

  // Deer + pack radius ring
  for (const d of deer) {
    if (!d.alive) continue;
    ctx.beginPath();
    ctx.arc(d.x, d.y, C.DEER_RADIUS, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(147,197,253,0.06)';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(d.x, d.y, 8, 0, Math.PI * 2);
    ctx.fillStyle = '#93c5fd';
    ctx.fill();
  }

  // Rabbits
  for (const r of rabbits) {
    if (!r.alive) continue;
    ctx.beginPath();
    ctx.arc(r.x, r.y, 5, 0, Math.PI * 2);
    ctx.fillStyle = '#fde68a';
    ctx.fill();
  }

  // Bear: kill zone ring + body
  ctx.beginPath();
  ctx.arc(bear.x, bear.y, C.BEAR_KILL_R, 0, Math.PI * 2);
  ctx.strokeStyle = 'rgba(251,146,60,0.28)';
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.lineWidth = 1;

  ctx.beginPath(); ctx.arc(bear.x, bear.y, 13, 0, Math.PI * 2);
  ctx.fillStyle = '#fb923c'; ctx.fill();
  // Ears
  ctx.beginPath();
  ctx.arc(bear.x - 7, bear.y - 9, 4, 0, Math.PI * 2);
  ctx.arc(bear.x + 7, bear.y - 9, 4, 0, Math.PI * 2);
  ctx.fillStyle = '#fb923c'; ctx.fill();

  // Wolves
  for (const w of wolves) {
    if (!w.alive) {
      // Briefly show a faded marker where the wolf died
      if (tick - w.dead_tick < 60) {
        ctx.beginPath();
        ctx.arc(w.x, w.y, 4, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(248,113,113,0.28)';
        ctx.fill();
      }
      continue;
    }

    // Perception ring (faint — shows the gene's current radius)
    ctx.beginPath();
    ctx.arc(w.x, w.y, w.g.perception, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(134,239,172,0.07)';
    ctx.stroke();

    // Body — color encodes wolfAttract: green = loner, warm = pack-oriented
    ctx.beginPath();
    ctx.arc(w.x, w.y, 7, 0, Math.PI * 2);
    const wa = w.g.wolfAttract;
    ctx.fillStyle = `rgb(${Math.floor(130 + wa * 90)},${Math.floor(239 - wa * 55)},${Math.floor(172 - wa * 65)})`;
    ctx.fill();

    // Energy bar above wolf
    const ef = clamp(w.energy / 100, 0, 1);
    ctx.fillStyle = 'rgba(255,255,255,0.09)';
    ctx.fillRect(w.x - 8, w.y - 17, 16, 3);
    ctx.fillStyle = `hsl(${ef * 120},60%,52%)`;
    ctx.fillRect(w.x - 8, w.y - 17, 16 * ef, 3);

    // Low-energy warning flash (about to die from energy depletion)
    if (w.energy < 20) {
      ctx.beginPath();
      ctx.arc(w.x, w.y, 11, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(252,165,165,${0.25 + 0.45 * Math.sin(tick * 0.3)})`;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.lineWidth = 1;
    }
  }

  // HUD
  ctx.fillStyle = 'rgba(255,255,255,0.28)';
  ctx.font = '11px monospace';
  const aliveCount = wolves.filter(w => w.alive).length;
  ctx.fillText(`gen ${gen}   tick ${tick}/${C.TICKS}   alive ${aliveCount}/${C.WOLVES}`, 12, 18);
}

// ── Main loop ────────────────────────────────────────────────────────────────
let accum = 0;
let animFrame = null;

function loop() {
  if (!paused) {
    accum += simSpeed;
    const steps = Math.floor(accum);
    accum -= steps;
    for (let i = 0; i < steps; i++) {
      if (tick < C.TICKS) step();
      else endGen();
    }
    updatePanel();
  }
  draw();
  animFrame = requestAnimationFrame(loop);
}

// ── Controls ─────────────────────────────────────────────────────────────────
document.getElementById('btnp').addEventListener('click', function () {
  paused = !paused;
  this.textContent = paused ? 'Resume' : 'Pause';
});

document.getElementById('btnn').addEventListener('click', () => {
  tick = C.TICKS; // force generation end on next step
});

document.getElementById('btnr').addEventListener('click', () => {
  cancelAnimationFrame(animFrame);
  gen    = 1;
  tick   = 0;
  paused = false;
  document.getElementById('btnp').textContent = 'Pause';
  document.getElementById('log').textContent  = '';
  initGen(null);
  log('Reset — fresh random genes near zero.');
  accum     = 0;
  animFrame = requestAnimationFrame(loop);
});

document.getElementById('spdrange').addEventListener('input', function () {
  simSpeed = parseInt(this.value);
  document.getElementById('spdlbl').textContent = simSpeed + 'x';
});

// ── Start ────────────────────────────────────────────────────────────────────
initGen(null);
log('Gen 1 — all genes near zero. Wolves wander randomly.');
log('Food attraction must be discovered through survival.');
animFrame = requestAnimationFrame(loop);
