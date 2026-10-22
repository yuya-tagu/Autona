// bracket_top8 — fixed FGC-standard Top 8 double elimination overlay.
//
// Renders the `top8.*` state written by the desktop Top 8 panel
// (AutonaTop8Widget / core/Top8Model): 8 slot-fixed players and 11 fixed
// match ids. All topology lives in the desktop app — this file is a dumb
// "match id -> card DOM" renderer plus static connector lines.
//
// ───── Geometry (single source of truth) ─────────────────────────────
// Cards are absolutely positioned on the 1920x1080 stage and the SVG
// connectors are derived from the same table, so layout tweaks only ever
// touch these constants.

const CARD_W = 420;
const CARD_H = 120;
const ROW_H = CARD_H / 2;

// Columns: winners rounds spread over 3 stops, losers over 4, symmetric
// 60px side margins (last column 1440 + 420 = 1860 = 1920 - 60).
const MATCHES = {
  wsf1:     { x: 60,   y: 230 },
  wsf2:     { x: 60,   y: 400 },
  wf:       { x: 750,  y: 315 },
  gf:       { x: 1440, y: 315 },
  gf_reset: { x: 1440, y: 485 },
  lr1a:     { x: 60,   y: 700 },
  lr1b:     { x: 60,   y: 870 },
  lqf1:     { x: 520,  y: 700 },
  lqf2:     { x: 520,  y: 870 },
  lsf:      { x: 980,  y: 785 },
  lf:       { x: 1440, y: 785 },
};

// Winner-advancement lines. `row` selects the target card's slot row the
// line plugs into (0 = top, 1 = bottom). The losers-drop routes (WSF loser
// -> LQF etc.) and the LF -> GF hop are intentionally not drawn — they
// cross the whole stage and the reference designs leave them implicit.
const CONNECTORS = [
  { from: "wsf1", to: "wf",       row: 0 },
  { from: "wsf2", to: "wf",       row: 1 },
  { from: "wf",   to: "gf",       row: 0 },
  { from: "lr1a", to: "lqf1",     row: 0 },
  { from: "lr1b", to: "lqf2",     row: 0 },
  { from: "lqf1", to: "lsf",      row: 0 },
  { from: "lqf2", to: "lsf",      row: 1 },
  { from: "lsf",  to: "lf",       row: 0 },
  { from: "gf",   to: "gf_reset", row: 0, vertical: true },
];

// ───── Theme colors (colors.top8, then tournamentInfo) ───────────────
//
// The top8-scoped override is only present in the state when the operator
// set it, so an unset feature falls back to the tournament colors.

function resolveColors(data) {
  const ti = (data && data.tournamentInfo) || {};
  const fc = ((data && data.colors) || {}).top8 || {};
  return {
    themeColor: fc.themeColor || ti.themeColor,
    accentColor: fc.accentColor || ti.accentColor,
  };
}

function applyTournamentColors(colors) {
  if (colors && colors.themeColor) {
    const cardBg = lighten(colors.themeColor, 0.08);
    document.documentElement.style.setProperty(
      "--theme-color", colors.themeColor);
    document.documentElement.style.setProperty(
      "--theme-color-2", darken(colors.themeColor, 0.55));
    document.documentElement.style.setProperty("--card-bg", cardBg);
    // Match the card body text to the card background's luminance.
    document.documentElement.style.setProperty("--card-ink", pickInk(cardBg));
  }
  if (colors && colors.accentColor) {
    document.documentElement.style.setProperty(
      "--accent-color", colors.accentColor);
    document.documentElement.style.setProperty(
      "--accent-ink", pickInk(colors.accentColor));
  }
}

function parseHex(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return null;
  const v = parseInt(m[1], 16);
  return { r: (v >> 16) & 0xff, g: (v >> 8) & 0xff, b: v & 0xff };
}

function toHex(r, g, b) {
  const h = (x) => Math.max(0, Math.min(255, x | 0)).toString(16).padStart(2, "0");
  return `#${h(r)}${h(g)}${h(b)}`;
}

function darken(hex, amount) {
  const rgb = parseHex(hex);
  if (!rgb) return hex;
  return toHex(rgb.r * (1 - amount), rgb.g * (1 - amount), rgb.b * (1 - amount));
}

function lighten(hex, amount) {
  const rgb = parseHex(hex);
  if (!rgb) return hex;
  return toHex(
    rgb.r + (255 - rgb.r) * amount,
    rgb.g + (255 - rgb.g) * amount,
    rgb.b + (255 - rgb.b) * amount);
}

function pickInk(hex) {
  const rgb = parseHex(hex);
  if (!rgb) return "#0a0a0a";
  const luma = 0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b;
  return luma > 160 ? "#0a0a0a" : "#ffffff";
}

// ───── Static DOM construction ────────────────────────────────────────

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function resolveAsset(path) {
  if (!path) return null;
  return "../../" + String(path).replace(/^\.\//, "");
}

function buildCards() {
  const root = document.querySelector(".bracket");
  root.innerHTML = "";
  for (const [matchId, pos] of Object.entries(MATCHES)) {
    const card = document.createElement("div");
    card.className = "match-card";
    card.dataset.match = matchId;
    card.style.left = `${pos.x}px`;
    card.style.top = `${pos.y}px`;
    card.style.width = `${CARD_W}px`;
    card.style.height = `${CARD_H}px`;

    const label = document.createElement("div");
    label.className = "match-label";
    card.appendChild(label);

    for (const i of [0, 1]) {
      const slot = document.createElement("div");
      slot.className = "slot";
      slot.dataset.slot = String(i);
      slot.innerHTML = `
        <div class="flag"></div>
        <div class="who">
          <span class="sponsor"></span>
          <div class="name"></div>
        </div>
        <div class="char"></div>
        <div class="score"></div>
      `;
      card.appendChild(slot);
    }
    root.appendChild(card);
  }
}

function buildConnectors() {
  const svg = document.querySelector(".connectors");
  svg.innerHTML = "";
  const ns = "http://www.w3.org/2000/svg";

  for (const c of CONNECTORS) {
    const from = MATCHES[c.from];
    const to = MATCHES[c.to];
    const path = document.createElementNS(ns, "path");
    path.setAttribute("class", "connector");
    path.dataset.to = c.to;

    let d;
    if (c.vertical) {
      // GF -> Reset: straight drop from the card's bottom centre.
      const x = from.x + CARD_W / 2;
      d = `M ${x} ${from.y + CARD_H} V ${to.y}`;
    } else {
      // Elbow: out of the source's right-middle, across, into the target
      // row's centre on its left edge.
      const x1 = from.x + CARD_W;
      const y1 = from.y + CARD_H / 2;
      const x2 = to.x;
      const y2 = to.y + ROW_H / 2 + c.row * ROW_H;
      const midX = Math.round((x1 + x2) / 2);
      d = `M ${x1} ${y1} H ${midX} V ${y2} H ${x2}`;
    }
    path.setAttribute("d", d);
    svg.appendChild(path);
  }
}

// ───── Per-update rendering ───────────────────────────────────────────

function playerFor(top8, slotNumber) {
  if (!slotNumber) return null;
  return ((top8.players || {})[String(slotNumber)]) || null;
}

function charIconPath(player) {
  const char = ((player || {}).characters || {})["1"];
  if (!char || !char.assets) return null;
  const assetObj = char.assets["base_files/icon"]
    || char.assets["full"]
    || GetBiggestAsset(char);
  if (!assetObj) return null;
  const assetPath = typeof assetObj === "string" ? assetObj : assetObj.asset;
  return resolveAsset(assetPath);
}

function renderSlot(card, index, player, score, showScore, state) {
  const slot = card.querySelector(`.slot[data-slot="${index}"]`);
  slot.classList.toggle("is-winner", state === "winner");
  slot.classList.toggle("is-loser", state === "loser");
  slot.classList.toggle("is-tbd", !player);

  const name = player ? (player.name || "").trim() : "";
  const sponsor = player ? (player.sponsor || "").trim() : "";
  SetInnerHtml(
    $(slot).find(".name"),
    player
      ? `${sponsor ? `<span class="sponsor-text">${escapeHtml(sponsor)}</span>` : ""}
         ${escapeHtml(name || "—")}`
      : "TBD");

  const flag = slot.querySelector(".flag");
  const flagAsset = player && player.country && player.country.asset
    ? resolveAsset(String(player.country.asset).toLowerCase())
    : null;
  const flagUrl = flagAsset ? `url('${flagAsset}')` : "";
  if (flag.style.backgroundImage !== flagUrl) {
    flag.style.backgroundImage = flagUrl;
  }
  flag.classList.toggle("is-empty", !flagAsset);

  const charEl = slot.querySelector(".char");
  const icon = player ? charIconPath(player) : null;
  const iconUrl = icon ? `url('${icon}')` : "";
  if (charEl.style.backgroundImage !== iconUrl) {
    charEl.style.backgroundImage = iconUrl;
  }
  charEl.classList.toggle("is-empty", !icon);

  const scoreEl = slot.querySelector(".score");
  const text = showScore ? String(score) : "";
  if (scoreEl.textContent !== text) scoreEl.textContent = text;
}

function renderMatch(top8, matchId) {
  const card = document.querySelector(`.match-card[data-match="${matchId}"]`);
  const match = ((top8.matches || {})[matchId]) || {};
  const slots = match.slots || [0, 0];
  const score = match.score || [0, 0];
  const finished = !!match.finished;
  const players = [playerFor(top8, slots[0]), playerFor(top8, slots[1])];

  const label = card.querySelector(".match-label");
  const labelText = match.name || "";
  if (label.textContent !== labelText) label.textContent = labelText;

  const bothKnown = !!(players[0] && players[1]);
  const winnerIdx = finished && score[0] !== score[1]
    ? (score[0] > score[1] ? 0 : 1)
    : -1;

  for (const i of [0, 1]) {
    let state = "";
    if (winnerIdx === i) state = "winner";
    else if (winnerIdx === 1 - i) state = "loser";
    renderSlot(card, i, players[i], score[i], bothKnown, state);
  }

  if (matchId === "gf_reset") {
    const active = !!match.active;
    card.classList.toggle("is-hidden", !active);
    document
      .querySelectorAll(`.connector[data-to="gf_reset"]`)
      .forEach((p) => p.classList.toggle("is-hidden", !active));
  }
}

let built = false;
let lastSignature = "";

LoadEverything().then(() => {
  Start = async () => {
    if (!built) {
      built = true;
      buildCards();
      buildConnectors();
    }
  };

  Update = async (event) => {
    const data = event.data || {};
    const ti = data.tournamentInfo || {};
    const top8 = data.top8 || null;

    if (!built) {
      buildCards();
      buildConnectors();
      built = true;
    }

    const colors = resolveColors(data);
    applyTournamentColors(colors);

    const status = document.querySelector(".stage-status");
    const stage = document.querySelector(".stage");
    if (!top8 || !top8.matches) {
      stage.classList.add("is-empty");
      status.textContent =
        "Autona の TOP 8 タブでブラケットを設定してください";
      return;
    }
    stage.classList.remove("is-empty");
    status.textContent = "";

    const signature = JSON.stringify({
      top8,
      name: ti.tournamentName,
      theme: colors.themeColor,
      accent: colors.accentColor,
    });
    if (signature === lastSignature) return;
    lastSignature = signature;

    SetInnerHtml($(".event-tag"), escapeHtml(ti.tournamentName || ""));
    SetInnerHtml(
      $(".stage-title"),
      escapeHtml(top8.phaseLabel || "TOP 8"));

    for (const matchId of Object.keys(MATCHES)) {
      renderMatch(top8, matchId);
    }
  };
});
