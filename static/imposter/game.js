/* =========================================================================
 * static/imposter/game.js — "The Imposter" client
 * =========================================================================
 * Renders whatever public state the server broadcasts (imp_state) and shows
 * the player their private role (imp_role). The server is the sole authority;
 * this file never decides who the imposter is or what the word is — it only
 * displays what it's told and forwards the player's actions.
 * ========================================================================= */
(function () {
  "use strict";

  const IMP = window.IMP;
  const $ = (id) => document.getElementById(id);

  // ---- Local view state ---------------------------------------------------
  let socket = null;
  let state = null;          // latest public snapshot
  let role = null;           // { round, role, category, word|null } for me
  let roleRound = -1;        // which round the held role belongs to
  let requestedRoleRound = -1; // last round we asked the server for a role
  let myChoice = null;       // this vote: a suspect_id, "abstain", or null (server hides picks)
  let lastRound = -1;        // to detect a fresh round and reset local state

  // ---- Small helpers ------------------------------------------------------
  const show = (el, on) => el.classList.toggle("hidden", !on);
  const me = () => IMP.playerId;
  const isHost = () => state && state.host_id === me();
  const nameOf = (pid) => {
    const p = state && state.players.find((x) => x.player_id === pid);
    return p ? p.name : "someone";
  };

  let toastTimer = null;
  function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
  }

  // ---- Connection ---------------------------------------------------------
  function connect() {
    socket = io();
    socket.on("connect", () => {
      socket.emit("imp_join", { game_id: IMP.gameId, player_id: IMP.playerId });
    });
    socket.on("imp_state", (s) => { state = s; onRound(); ensureRole(); render(); });
    socket.on("imp_role", (r) => { role = r; roleRound = r.round; render(); });
    socket.on("imp_error", (e) => toast((e && e.message) || "That didn't work."));
  }

  function onRound() {
    // A new round number, or any return to the clue phase (a re-hint after no
    // majority), voids the previous ballot.
    if (state.round_number !== lastRound || state.status === "clues") {
      lastRound = state.round_number;
      myChoice = null;
    }
  }

  // The authoritative role fetch: if a round is live and we don't already hold
  // a role stamped with this round number, ask the server (which replies only
  // to us). This is what guarantees every crew member gets the word even across
  // reconnects or a race at round start — one imposter, everyone else the word.
  function ensureRole() {
    const active = ["clues", "voting", "guessing", "reveal"].includes(state.status);
    if (!active) { role = null; roleRound = -1; requestedRoleRound = -1; return; }
    if (roleRound !== state.round_number && requestedRoleRound !== state.round_number) {
      requestedRoleRound = state.round_number;
      emit("imp_want_role");
    }
  }

  function emit(evt, extra) {
    socket.emit(evt, Object.assign({ game_id: IMP.gameId, player_id: IMP.playerId }, extra || {}));
  }

  // ---- Rendering ----------------------------------------------------------
  function render() {
    if (!state) return;
    const st = state.status;

    $("round-label").textContent =
      st === "lobby" && state.round_number === 0 ? "" : "Round " + state.round_number;

    renderRole();
    show($("panel-lobby"),   st === "lobby");
    show($("panel-clues"),   st === "clues");
    show($("panel-voting"),  st === "voting");
    show($("panel-guessing"),st === "guessing");
    show($("panel-reveal"),  st === "reveal");

    if (st === "lobby")    renderLobby();
    if (st === "clues")    renderClues();
    if (st === "voting")   renderVoting();
    if (st === "guessing") renderGuessing();
    if (st === "reveal")   renderReveal();
    renderScores();
  }

  function renderRole() {
    const banner = $("role-banner");
    const active = role && roleRound === state.round_number &&
      ["clues", "voting", "guessing"].includes(state.status);
    show(banner, !!active);
    if (!active) return;
    const imp = role.role === "imposter";
    banner.className = "role " + (imp ? "imposter" : "crew");
    $("role-tag").textContent = imp ? "You are the imposter" : "Your secret word";
    $("role-word").textContent = imp ? "IMPOSTER" : (role.word || "");
    const cat = $("role-cat");
    show(cat, true);
    cat.innerHTML = imp
      ? "Blend in. Category: <b>" + esc(role.category) + "</b>"
      : "Category: <b>" + esc(role.category) + "</b>";
  }

  function renderLobby() {
    const enough = state.players.length >= state.min_players;
    $("lobby-sub").textContent = enough
      ? "Everyone's in? Host can start when ready."
      : "Need at least " + state.min_players + " players — share the code to fill up.";
    const wrap = $("lobby-players");
    wrap.innerHTML = "";
    state.players.forEach((p) => wrap.appendChild(playerRow(p)));

    const hostView = isHost();
    show($("start-btn"), hostView);
    show($("lobby-wait"), !hostView);
    const btn = $("start-btn");
    btn.disabled = !enough;
    btn.textContent = enough
      ? (state.round_number > 0 ? "Start next round" : "Start round")
      : "Waiting for players…";
  }

  function renderClues() {
    // After a no-majority vote the round loops back here for fresh hints.
    const hr = state.hint_round || 1;
    const hintNote = $("hint-note");
    show(hintNote, hr > 1);
    if (hr > 1) {
      hintNote.textContent = "No majority last vote — hint round " + hr +
        ". Give a new, sharper clue.";
    }
    $("clues-title").textContent = hr > 1 ? "Another clue round" : "Clue round";

    $("clues-sub").textContent = "Category: " + (state.category || "?") +
      ". One word that points at the secret — every clue stays hidden until all are in.";

    const submitted = state.submitted || [];
    const iSubmitted = submitted.includes(me());
    show($("my-clue"), !iSubmitted);
    show($("clue-locked"), iSubmitted);
    if (!iSubmitted) $("clue-input").focus();

    // Progress track — who has locked a clue in (never the words themselves).
    const track = $("submit-track");
    track.innerHTML = "";
    state.players.forEach((p) => {
      const done = submitted.includes(p.player_id);
      const row = document.createElement("div");
      row.className = "track-row" + (done ? " done" : "");
      row.innerHTML =
        '<span class="tname">' + esc(p.name) + (p.player_id === me() ? " (you)" : "") + "</span>" +
        '<span class="tmark">' + (done ? "✔ locked in" : (p.connected ? "thinking…" : "offline")) + "</span>";
      track.appendChild(row);
    });

    $("clue-wait").textContent = submitted.length + " / " + state.players.length + " clues locked in";

    // Host can reveal early (e.g. someone's idle) once at least one clue exists.
    const canReveal = isHost() && submitted.length >= 1 && submitted.length < state.players.length;
    show($("reveal-btn"), canReveal);
  }

  function renderVoting() {
    // All clues are now revealed together, in the server's shuffled order.
    const cl = $("vote-clue-list");
    cl.innerHTML = "";
    state.clues.forEach((c) => {
      const item = document.createElement("div");
      item.className = "clue-item";
      item.innerHTML =
        '<span class="cname">' + esc(c.name) + (c.player_id === me() ? " (you)" : "") + "</span>" +
        '<span class="cword">' + esc(c.clue || "—") + "</span>";
      cl.appendChild(item);
    });

    // You can change your pick (vote ↔ abstain) right up until the last
    // player acts and the round resolves.
    const grid = $("vote-grid");
    grid.innerHTML = "";
    state.players.forEach((p) => {
      if (p.player_id === me()) return; // can't vote yourself
      const b = document.createElement("button");
      const picked = myChoice === p.player_id;
      b.className = "vote-btn" + (picked ? " picked" : "");
      b.innerHTML = "<span>" + esc(p.name) + "</span>" +
        (picked ? '<span class="vote-check">✓ your vote</span>' : "");
      b.addEventListener("click", () => {
        myChoice = p.player_id;
        emit("imp_vote", { suspect: p.player_id });
      });
      grid.appendChild(b);
    });

    const abstainBtn = $("abstain-btn");
    abstainBtn.className = "btn ghost full" + (myChoice === "abstain" ? " picked" : "");
    abstainBtn.textContent = myChoice === "abstain" ? "✓ Abstaining" : "Abstain";

    const present = state.players.filter((p) => p.connected).length;
    const acted = state.voted.length + state.abstained.length;
    const needed = Math.floor(present / 2) + 1;   // strict majority of those present
    $("vote-status").textContent =
      acted + " / " + present + " in — " +
      state.voted.length + " voted, " + state.abstained.length + " abstained · " +
      needed + " needed to convict";
  }

  function renderGuessing() {
    const iAmImposter = role && role.role === "imposter";
    show($("guess-mine"), !!iAmImposter);
    show($("guess-wait"), !iAmImposter);
    if (iAmImposter) $("guess-input").focus();
  }

  function renderReveal() {
    const r = state.reveal;
    if (!r) return;
    const banner = $("reveal-banner");
    const impName = nameOf(r.imposter_id);
    let cls, head, detail;

    if (r.outcome === "crew_won") {
      cls = "crew"; head = "Crew wins! 🎉";
      detail = "The group reached a majority on " + impName + " — and the guess missed.";
    } else if (r.outcome === "imposter_stole") {
      cls = "imp"; head = "Imposter wins! 🎭";
      detail = impName + " was caught — then guessed the word and took it.";
    } else {
      cls = "imp"; head = "Imposter wins! 🕵️";
      detail = r.top_suspect && r.top_suspect !== r.imposter_id
        ? "The group convicted " + nameOf(r.top_suspect) + ", but it was " + impName + " all along."
        : impName + " slipped through the vote.";
    }
    if (r.imposter_guess) detail += "  (guessed: “" + esc(r.imposter_guess) + "”)";

    banner.className = "banner " + cls;
    $("reveal-headline").textContent = head;
    $("reveal-detail").textContent = detail;
    $("reveal-word").textContent = r.word;

    const rows = $("reveal-rows");
    rows.innerHTML = "";
    // Order by votes received, then name — imposter clearly marked.
    const ordered = state.players.slice().sort((a, b) =>
      (r.tally[b.player_id] || 0) - (r.tally[a.player_id] || 0) || a.name.localeCompare(b.name));
    ordered.forEach((p) => {
      const votes = r.tally[p.player_id] || 0;
      const delta = r.deltas[p.player_id] || 0;
      const isImp = p.player_id === r.imposter_id;
      const row = document.createElement("div");
      row.className = "rrow" + (isImp ? " imp-badge" : "");
      row.innerHTML =
        '<span class="rname">' + esc(p.name) + (p.player_id === me() ? " (you)" : "") + "</span>" +
        (isImp ? '<span class="impmark">imposter</span>' : "") +
        '<span class="rvotes">' + votes + (votes === 1 ? " vote" : " votes") + "</span>" +
        '<span class="rdelta ' + (delta > 0 ? "pos" : "") + '">' + (delta > 0 ? "+" + delta : "—") + "</span>";
      rows.appendChild(row);
    });

    show($("next-btn"), isHost());
    show($("reveal-wait"), !isHost());
  }

  function renderScores() {
    const wrap = $("score-list");
    wrap.innerHTML = "";
    state.players.slice()
      .sort((a, b) => b.score - a.score || a.name.localeCompare(b.name))
      .forEach((p) => {
        const row = playerRow(p, true);
        wrap.appendChild(row);
      });
  }

  function playerRow(p, withScore) {
    const row = document.createElement("div");
    let cls = "prow";
    if (p.player_id === me()) cls += " me";
    row.className = cls;
    row.innerHTML =
      '<span class="dot ' + (p.connected ? "" : "off") + '"></span>' +
      '<span class="pname">' + esc(p.name) + "</span>" +
      (p.is_host ? '<span class="ptag">host</span>' : "") +
      (withScore ? '<span class="pscore">' + p.score + "</span>" : "");
    return row;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ---- Actions ------------------------------------------------------------
  function wireActions() {
    $("copy-btn").addEventListener("click", () => {
      const url = window.location.origin + "/imposter/g/" + IMP.gameId;
      navigator.clipboard.writeText(url).then(
        () => { $("copy-btn").textContent = "Copied!"; setTimeout(() => $("copy-btn").textContent = "Copy link", 1500); },
        () => toast("Copy failed — link is " + url)
      );
    });

    $("start-btn").addEventListener("click", () => emit("imp_start"));
    $("reveal-btn").addEventListener("click", () => emit("imp_reveal"));
    $("next-btn").addEventListener("click", () => emit("imp_next"));
    $("abstain-btn").addEventListener("click", () => {
      myChoice = "abstain";
      emit("imp_abstain");
    });

    const clueInput = $("clue-input");
    const sendClue = () => {
      const v = clueInput.value.trim();
      if (!v) return;
      emit("imp_clue", { clue: v });
      clueInput.value = "";
    };
    $("clue-btn").addEventListener("click", sendClue);
    clueInput.addEventListener("keydown", (e) => { if (e.key === "Enter") sendClue(); });

    const guessInput = $("guess-input");
    const sendGuess = () => {
      const v = guessInput.value.trim();
      if (!v) return;
      emit("imp_guess", { guess: v });
      guessInput.value = "";
    };
    $("guess-btn").addEventListener("click", sendGuess);
    guessInput.addEventListener("keydown", (e) => { if (e.key === "Enter") sendGuess(); });
  }

  // ---- Join overlay (browsers without a seat yet) -------------------------
  function showJoinOverlay() {
    const ov = $("join-overlay");
    show(ov, true);
    const nameInput = $("ov-name");
    nameInput.focus();
    const doJoin = async () => {
      const name = nameInput.value.trim();
      $("ov-err").textContent = "";
      if (!name) { $("ov-err").textContent = "Enter a name."; return; }
      $("ov-btn").disabled = true;
      try {
        const res = await fetch("/imposter/g/" + IMP.gameId + "/join", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Could not join.");
        IMP.playerId = data.player_id;
        show(ov, false);
        connect();
      } catch (e) {
        $("ov-err").textContent = e.message;
        $("ov-btn").disabled = false;
      }
    };
    $("ov-btn").addEventListener("click", doJoin);
    nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doJoin(); });
  }

  // ---- Boot ---------------------------------------------------------------
  wireActions();
  if (IMP.playerId) {
    connect();
  } else {
    showJoinOverlay();
  }
})();
