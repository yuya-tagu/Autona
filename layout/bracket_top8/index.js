// bracket_top8 — template-based VALORANT/EVO-style TOP 8 bracket overlay.
// Uses a fixed 4-column Grid layout (WSF / WF / GF / Reset × LR1 / LQF /
// LSF / LF). Phase groups that don't populate all rounds are adapted by
// dropping unused slots and shifting GF/Reset left.

// ───── Templates ─────────────────────────────────────────────────────
//
// Each template maps data-source descriptors to grid slots:
//   source.side: "upper" (positive rounds excluding GF) | "lower" (negative)
//                | "gf" | "reset"
//   source.index: position within side's round list, 0-based.
//                 For upper/lower this is sorted outer→inner (WR1, WR2, ...).
//   setCount: expected number of sets; extra data is ignored, missing data
//             renders TBD placeholders.
//   row / col: 1-indexed grid position (row 1 = upper, row 2 = lower).

const TEMPLATES = {
  8: {
    topLabel: "TOP 8 BRACKET",
    gridCols: 4,
    slots: [
      { id: "wsf",   label: "WINNERS SEMIFINAL",   row: 1, col: 1, side: "upper", index: 0, setCount: 2 },
      { id: "wf",    label: "WINNERS FINAL",       row: 1, col: 2, side: "upper", index: 1, setCount: 1 },
      { id: "gf",    label: "GRAND FINAL",         row: 1, col: 3, side: "gf",              setCount: 1 },
      { id: "reset", label: "RESET",               row: 1, col: 4, side: "reset",           setCount: 1 },
      { id: "lr1",   label: "LOSERS R1",           row: 2, col: 1, side: "lower", index: 0, setCount: 2 },
      { id: "lqf",   label: "LOSERS QUARTERFINAL", row: 2, col: 2, side: "lower", index: 1, setCount: 2 },
      { id: "lsf",   label: "LOSERS SEMIFINAL",    row: 2, col: 3, side: "lower", index: 2, setCount: 1 },
      { id: "lf",    label: "LOSERS FINAL",        row: 2, col: 4, side: "lower", index: 3, setCount: 1 },
    ],
  },
};

// TOP-8-sized phase groups only (1..8 entrants). Larger brackets use
// bracket_top12 or bracket_top16.
function getTopN(entrants) {
  const n = Math.max(0, entrants | 0);
  if (n === 0) return null;
  if (n <= 8) return 8;
  return null;
}

function applyTournamentColors(ti) {
  if (ti && ti.themeColor) {
    document.documentElement.style.setProperty("--theme-color", ti.themeColor);
    document.documentElement.style.setProperty(
      "--theme-color-2", darken(ti.themeColor, 0.55));
    document.documentElement.style.setProperty(
      "--card-bg", lighten(ti.themeColor, 0.08));
  }
  if (ti && ti.accentColor) {
    document.documentElement.style.setProperty(
      "--accent-color", ti.accentColor);
    document.documentElement.style.setProperty(
      "--accent-ink", pickInk(ti.accentColor));
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

function isGrandFinalRound(round) {
  const name = (round && round.name) ? String(round.name) : "";
  if (!name) return false;
  const lower = name.toLowerCase();
  return lower.includes("grand final")
    || name.includes("グランドファイナル")
    || name.includes("グランド・ファイナル");
}

function isResetName(name) {
  if (!name) return false;
  const s = String(name);
  return /reset/i.test(s) || s.includes("リセット");
}

function sortedRoundNums(rounds, positive) {
  const nums = Object.keys(rounds)
    .map((k) => parseInt(k))
    .filter((n) => !Number.isNaN(n) && (positive ? n > 0 : n < 0));
  return nums.sort((a, b) => positive ? a - b : b - a);
}

function setHasBye(set) {
  if (!set) return true;
  const ids = set.playerId || [-1, -1];
  return ids.some((id) => id === -1);
}

// Return the sets of a round in key order, with BYE-bearing sets dropped.
// Start.gg includes phantom rounds where every set pairs a real seed
// against a BYE; those need to be invisible to match Autona's own view.
function sanitizeSets(round) {
  if (!round || !round.sets) return [];
  return Object.keys(round.sets)
    .sort((a, b) => parseInt(a) - parseInt(b))
    .map((k) => round.sets[k])
    .filter((s) => !setHasBye(s));
}

function isDisplayableRound(round) {
  return sanitizeSets(round).length > 0;
}

function classifyRounds(rounds) {
  const allPos = sortedRoundNums(rounds, true);
  const allNeg = sortedRoundNums(rounds, false);

  // Drop BYE-only rounds from both sides before index-based slot mapping.
  const positive = allPos.filter((n) => isDisplayableRound(rounds[String(n)]));
  const negative = allNeg.filter((n) => isDisplayableRound(rounds[String(n)]));

  // Grand Final detection. Prefer the localized round name; fall back to
  // the structural rule (last 1-2 positive rounds with exactly one real
  // set) for brackets whose round names weren't populated.
  let gfNums = positive.filter((n) => isGrandFinalRound(rounds[String(n)]));
  if (gfNums.length === 0) {
    const realSetCount = (n) => sanitizeSets(rounds[String(n)]).length;
    for (let i = positive.length - 1; i >= 0; i--) {
      if (realSetCount(positive[i]) !== 1) break;
      gfNums.unshift(positive[i]);
      if (gfNums.length === 2) break;
    }
  }

  let gfMain = null, gfReset = null;
  gfNums.forEach((n) => {
    if (isResetName(rounds[String(n)].name)) gfReset = n;
    else if (gfMain == null) gfMain = n;
  });
  // If multiple GF-named rounds but none flagged as reset, the trailing
  // one is the reset round.
  if (gfReset == null && gfNums.length >= 2) {
    gfReset = gfNums[gfNums.length - 1];
    if (gfMain === gfReset) gfMain = gfNums[0];
  }
  // If only a reset was identified (edge case), promote it to main so the
  // GF slot isn't left empty.
  if (gfMain == null && gfReset != null) {
    gfMain = gfReset;
    gfReset = null;
  }

  const gfSet = new Set(gfNums);
  const upper = positive.filter((n) => !gfSet.has(n));

  return { upper, lower: negative, gfMain, gfReset };
}

function pickRoundForSlot(slot, classified, rounds) {
  let num = null;
  if (slot.side === "upper") {
    num = classified.upper[slot.index];
  } else if (slot.side === "lower") {
    num = classified.lower[slot.index];
  } else if (slot.side === "gf") {
    num = classified.gfMain;
  } else if (slot.side === "reset") {
    num = classified.gfReset;
  }
  if (num == null) return null;
  return rounds[String(num)] || null;
}

function createSlotColumn(slot, round) {
  const col = document.createElement("div");
  col.className = "slot-column";
  col.dataset.role = slot.side;

  const label = document.createElement("div");
  label.className = "slot-label";
  label.textContent = slot.label;
  col.appendChild(label);

  const displayableSets = sanitizeSets(round);
  for (let i = 0; i < slot.setCount; i++) {
    col.appendChild(renderSet(displayableSets[i] || null));
  }

  col.style.gridRow = String(slot.row);
  col.style.gridColumn = String(slot.col);
  return col;
}

function renderSet(set) {
  const card = document.createElement("div");
  card.className = "bracket-set";

  if (!set) {
    card.dataset.empty = "true";
    card.appendChild(makePlayerRow("TBD", "", "", null));
    card.appendChild(makePlayerRow("TBD", "", "", null));
    return card;
  }

  const ids = set.playerId || [-2, -2];
  const names = set.playerName || ["", ""];
  const scores = set.score || [0, 0];
  const completed = !!set.completed;
  const winnerIdx = completed && scores[0] !== scores[1]
    ? (scores[0] > scores[1] ? 0 : 1)
    : -1;

  for (let i = 0; i < 2; i++) {
    const id = ids[i];
    const isBye = id === -1;
    const isPending = id === -2;
    const displayName = isBye
      ? "BYE"
      : (isPending ? "TBD" : (names[i] || "TBD"));
    const score = completed ? String(scores[i] ?? "") : "";
    const row = makePlayerRow(displayName, score, "", i === winnerIdx);
    if (isPending || isBye || !names[i]) {
      row.querySelector(".player-name").classList.add("is-pending");
    }
    card.appendChild(row);
  }
  return card;
}

function makePlayerRow(name, score, seed, isWinner) {
  const row = document.createElement("div");
  row.className = "player";
  if (isWinner) row.classList.add("is-winner");

  if (seed) {
    const seedEl = document.createElement("span");
    seedEl.className = "seed";
    seedEl.textContent = seed;
    row.appendChild(seedEl);
  }

  const nameEl = document.createElement("div");
  nameEl.className = "player-name";
  nameEl.textContent = name;
  row.appendChild(nameEl);

  const scoreEl = document.createElement("div");
  scoreEl.className = "player-score";
  scoreEl.textContent = score || "";
  row.appendChild(scoreEl);

  return row;
}

// Drop trailing upper/lower slots that have no backing round, then shift
// GF/Reset left to close the gap. Handles phase groups whose UB stops
// one or two rounds short of the template's assumption (e.g. a Top-8
// pool whose UR1 is Winners Semifinal with no Winners Final round).
function adaptTemplate(template, classified) {
  const upperExpected = template.slots.filter((s) => s.side === "upper").length;
  const lowerExpected = template.slots.filter((s) => s.side === "lower").length;
  const upperShift = Math.max(0, upperExpected - classified.upper.length);
  const lowerShift = Math.max(0, lowerExpected - classified.lower.length);
  if (upperShift === 0 && lowerShift === 0) return template;

  const adapted = {
    ...template,
    slots: template.slots
      .map((s) => {
        if (s.side === "upper") {
          return s.index >= classified.upper.length ? null : s;
        }
        if (s.side === "lower") {
          return s.index >= classified.lower.length ? null : s;
        }
        // GF / Reset live on the upper row; shift them left by however
        // many upper slots we dropped.
        if ((s.side === "gf" || s.side === "reset") && s.row === 1) {
          return { ...s, col: s.col - upperShift };
        }
        return s;
      })
      .filter(Boolean),
  };
  return adapted;
}

function renderBracket(template, rounds) {
  const grid = document.querySelector(".bracket-grid");
  grid.innerHTML = "";
  grid.style.gridTemplateColumns =
    `repeat(${template.gridCols}, var(--card-width))`;

  const classified = classifyRounds(rounds);
  const effective = adaptTemplate(template, classified);

  effective.slots.forEach((slot) => {
    const round = pickRoundForSlot(slot, classified, rounds);
    grid.appendChild(createSlotColumn(slot, round));
  });
}

let lastSignature = "";

LoadEverything().then(() => {
  Start = async () => {};

  Update = async (event) => {
    const data = event.data || {};
    const ti = data.tournamentInfo || {};
    const bracketRoot = (data.bracket || {}).bracket || {};
    const rounds = bracketRoot.rounds || {};

    applyTournamentColors(ti);

    // Determine the size of the *selected phase group*, not the whole
    // tournament. numEntrants is tournament-wide (hundreds), so use it
    // only as a last-resort fallback.
    //
    // `players.slot` is authoritative for the phase-group roster (it's
    // populated from Start.gg's entrants list for this pool), so prefer
    // it. UR1 set count is a poor proxy when a Top-8 pool starts from
    // WSF (2 sets → would yield entrants=4 and mis-classify as Top 6).
    const playersSlot = ((data.bracket || {}).players || {}).slot || {};
    const ur1Sets = rounds["1"]
      ? Object.keys(rounds["1"].sets || {}).length
      : 0;
    const slotCount = Object.keys(playersSlot).length;
    const entrants = slotCount
      || (ur1Sets * 2)
      || ti.numEntrants
      || 0;
    const topN = getTopN(entrants);

    const eventTag = document.querySelector(".event-tag");
    const title = document.querySelector(".stage-title");
    const grid = document.querySelector(".bracket-grid");
    const status = document.querySelector(".stage-status");

    eventTag.textContent = (ti.tournamentName || "").toUpperCase();

    const roundKeys = Object.keys(rounds);

    // Diagnostic (shown until a real render succeeds). Helps when the
    // layout looks empty — reveals whether numbers flowed in correctly.
    const diag =
      `entrants=${entrants} topN=${topN || "-"} ` +
      `rounds=[${roundKeys.join(",")}] ` +
      `ur1Sets=${ur1Sets} slots=${slotCount}`;

    const template = TEMPLATES[topN];
    if (!template || roundKeys.length === 0) {
      grid.classList.add("is-hidden");
      if (topN && !template) {
        status.textContent = `TOP ${topN} テンプレートは未実装です (${diag})`;
      } else if (!topN && entrants > 16) {
        status.textContent =
          `このブラケットは TOP16 までの表示に対応しています (現在 ${entrants} 名 / ${diag})`;
      } else if (entrants === 0) {
        status.textContent = `ブラケット未読込 (${diag})`;
      } else {
        status.textContent = diag;
      }
      return;
    }

    title.textContent = template.topLabel;

    // Avoid re-rendering when nothing relevant changed.
    const signature = JSON.stringify({ rounds, topN });
    if (signature === lastSignature) return;
    lastSignature = signature;

    grid.classList.remove("is-hidden");
    // Keep diagnostic visible until the bracket looks right — remove this
    // block once rendering is confirmed working.
    status.textContent = diag;

    renderBracket(template, rounds);
  };
});
