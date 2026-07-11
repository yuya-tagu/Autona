// commentators — caster lineup overlay (bottom bar, 1-4 casters).
//
// Renders the `commentary.{1..12}` state written by the desktop Commentary
// panel (AutonaCommentaryWidget / AutonaCommentaryModel): the slots with
// display > 0 and a non-empty name, left to right by slot order (the
// panel's card order — cards are drag-reorderable), capped at 4. The
// desktop panel enforces the same rule — the client-side filter is a
// second line of defense against hand-edited state. Legacy states carry
// check-order values (1..4) in display; sorting by (display, slot) keeps
// those rendering in their saved order too.

const MAX_DISPLAY = 4;

// ───── Theme colors (from tournamentInfo, mirrors bracket_top8) ───────

function applyTournamentColors(ti) {
  if (ti && ti.themeColor) {
    const cardBg = lighten(ti.themeColor, 0.08);
    document.documentElement.style.setProperty("--theme-color", ti.themeColor);
    document.documentElement.style.setProperty(
      "--theme-color-2", darken(ti.themeColor, 0.55));
    document.documentElement.style.setProperty("--card-bg", cardBg);
    // Match the card body text to the card background's luminance.
    document.documentElement.style.setProperty("--card-ink", pickInk(cardBg));
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

// Icon badge glyphs, keyed by the stem stored in `icon` (same tokens as
// AutonaCommentaryModel.ICONS; glyphs mirror assets/icons/*.svg).
const ICON_SVGS = {
  mic: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.91-3c-.49 0-.9.36-.98.85C16.52 14.2 14.47 16 12 16s-4.52-1.8-4.93-4.15C6.99 11.36 6.58 11 6.09 11c-.61 0-1.09.54-1 1.14.49 3 2.89 5.35 5.91 5.78V20c0 .55.45 1 1 1s1-.45 1-1v-2.08c3.02-.43 5.42-2.78 5.91-5.78.1-.6-.39-1.14-1-1.14z"/></svg>',
  megaphone: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M18 11v2h4v-2h-4zm-2 6.61c.96.71 2.21 1.65 3.2 2.39.4-.53.8-1.07 1.2-1.6-.99-.74-2.24-1.68-3.2-2.4-.4.54-.8 1.08-1.2 1.61zM20.4 5.6c-.4-.53-.8-1.07-1.2-1.6-.99.74-2.24 1.68-3.2 2.4.4.53.8 1.07 1.2 1.6.96-.72 2.21-1.65 3.2-2.4zM4 9c-1.1 0-2 .9-2 2v2c0 1.1.9 2 2 2h1v4h2v-4h1l5 3V6L8 9H4zm11.5 3c0-1.33-.58-2.53-1.5-3.35v6.69c.92-.81 1.5-2.01 1.5-3.34z"/></svg>',
  phone: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M6.62 10.79c1.44 2.83 3.76 5.14 6.59 6.59l2.2-2.2c.27-.27.67-.36 1.02-.24 1.12.37 2.33.57 3.57.57.55 0 1 .45 1 1V20c0 .55-.45 1-1 1-9.39 0-17-7.61-17-17 0-.55.45-1 1-1h3.5c.55 0 1 .45 1 1 0 1.25.2 2.45.57 3.57.11.35.03.74-.25 1.02l-2.2 2.2z"/></svg>',
};

// Legacy shapes stored the icon as role text (exact label match).
const LEGACY_ICON_LABELS = {
  "マイク": "mic",
  "拡声器": "megaphone",
  "電話": "phone",
};

function roleBadges(caster) {
  // Current shape: `role` free text + `icon` stem. Legacy fallbacks
  // (second line of defense): a `roles` array, and icon labels typed
  // as role text.
  let iconStem =
    typeof caster.icon === "string" && ICON_SVGS[caster.icon]
      ? caster.icon : "";
  const list = Array.isArray(caster.roles)
    ? caster.roles
    : (caster.role ? [caster.role] : []);
  const texts = [];
  for (const raw of list) {
    const role = String(raw || "").trim();
    if (!role) continue;
    if (LEGACY_ICON_LABELS[role]) {
      if (!iconStem) iconStem = LEGACY_ICON_LABELS[role];
    } else {
      texts.push(role);
    }
  }
  const badges = [];
  if (iconStem) {
    badges.push(
      `<span class="role-badge role-badge-icon">${ICON_SVGS[iconStem]}</span>`);
  }
  for (const text of texts) {
    badges.push(`<span class="role-badge">${escapeHtml(text)}</span>`);
  }
  return badges.join("");
}

// ───── Rendering ──────────────────────────────────────────────────────

function displayedCasters(commentary) {
  const candidates = [];
  const source = commentary || {};
  for (let i = 1; i <= 12; i++) {
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
    const badges = roleBadges(caster);
    const team = String(caster.team || "").trim();
    const twitter = String(caster.twitter || "").trim().replace(/^@/, "");
    const note = String(caster.note || "").trim();

    const card = document.createElement("div");
    card.className = "caster-card";
    card.innerHTML = `
      <div class="main-line">
        ${badges ? `<span class="role-badges">${badges}</span>` : ""}
        <span class="who">
          ${team ? `<span class="team">${escapeHtml(team)}</span>` : ""}
          <span class="name"></span>
        </span>
      </div>
      ${twitter ? `
        <div class="sub-line">
          <span class="twitter">@${escapeHtml(twitter)}</span>
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
