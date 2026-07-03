// commentators — caster lineup overlay (bottom bar, 1-4 casters).
//
// Renders the `commentary.{1..8}` state written by the desktop Commentary
// panel (AutonaCommentaryWidget / AutonaCommentaryModel): the slots with
// display > 0 and a non-empty name, left to right by display order (the
// order the operator checked them), capped at 4. The desktop panel
// enforces the same rule — the client-side filter is a second line of
// defense against hand-edited state.

const MAX_DISPLAY = 4;

// ───── Theme colors (from tournamentInfo, mirrors bracket_top8) ───────

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

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ───── Rendering ──────────────────────────────────────────────────────

function displayedCasters(commentary) {
  const candidates = [];
  const source = commentary || {};
  for (let i = 1; i <= 8; i++) {
    const caster = source[String(i)];
    if (!caster || typeof caster !== "object") continue;
    // display: 0 = hidden, 1.. = overlay position (legacy true -> 1).
    const order = caster.display === true ? 1 : Number(caster.display) || 0;
    if (order <= 0) continue;
    if (!String(caster.name || "").trim()) continue;
    candidates.push({ order, slot: i, caster });
  }
  candidates.sort((a, b) => a.order - b.order || a.slot - b.slot);
  return candidates.slice(0, MAX_DISPLAY).map((c) => c.caster);
}

async function renderCasters(casters) {
  const bar = document.querySelector(".comms-bar");
  bar.dataset.count = String(casters.length);
  bar.classList.toggle("is-empty", casters.length === 0);
  bar.innerHTML = "";

  for (const caster of casters) {
    const role = String(caster.role || "").trim();
    const team = String(caster.team || "").trim();
    const realName = String(caster.real_name || "").trim();
    const twitter = String(caster.twitter || "").trim().replace(/^@/, "");
    const note = String(caster.note || "").trim();

    const card = document.createElement("div");
    card.className = "caster-card";
    card.innerHTML = `
      <div class="main-line">
        ${role ? `<span class="role-badge">${escapeHtml(role)}</span>` : ""}
        <span class="who">
          ${team ? `<span class="team">${escapeHtml(team)}</span>` : ""}
          <span class="name"></span>
        </span>
      </div>
      ${realName || twitter ? `
        <div class="sub-line">
          ${realName ? `<span class="real-name">${escapeHtml(realName)}</span>` : ""}
          ${twitter ? `<span class="twitter">@${escapeHtml(twitter)}</span>` : ""}
        </div>` : ""}
      ${note ? `<div class="note">${escapeHtml(note)}</div>` : ""}
    `;
    bar.appendChild(card);

    // Transcript adds the romaji reading for JP tags (kuroshiro).
    SetInnerHtml($(card).find(".name"), await Transcript(caster.name));
  }
}

let lastSignature = "";

LoadEverything().then(() => {
  Start = async () => {};

  Update = async (event) => {
    const data = event.data || {};
    const ti = data.tournamentInfo || {};

    applyTournamentColors(ti);

    const casters = displayedCasters(data.commentary);
    const signature = JSON.stringify({
      casters,
      theme: ti.themeColor,
      accent: ti.accentColor,
    });
    if (signature === lastSignature) return;
    lastSignature = signature;

    await renderCasters(casters);
  };
});
