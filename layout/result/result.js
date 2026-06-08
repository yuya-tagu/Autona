/**
 * result: Top 8 result image layout.
 *
 * Called from Python via page().runJavaScript("initResult(payload)").
 * payload = { data, config }
 *   data.players = [{ rank, name, sponsor, character: { assets, ... } }, ...]
 *   data.tournamentName, data.eventName, data.startAt, data.numEntrants
 *   config.main_icon_path, config.background_path, config.accent_color
 */

let _fontsInjected = false;

async function initResult(payload) {
  try {
    const { data, config } = payload;
    const layout = payload.layout || "";

    clearPreviousRender();
    injectFonts();
    applyConfig(config);
    setBackground(config);
    setHeader(data, config);
    // Team / Crew layouts (team, team_split, ...): each rank slot holds a
    // roster of main fighters. Grid arrangement differs per layout (CSS).
    if (layout.startsWith("team")) {
      await placeTeams(data, config, layout);
    } else {
      await placePlayers(data, config);
    }

    await document.fonts.ready;
    await new Promise((r) => setTimeout(r, 100));

    fitLongText();

    document.title = "RENDER_READY";
  } catch (e) {
    console.error("initResult error:", e);
    document.title = "RENDER_READY";
  }
}

function clearPreviousRender() {
  $(".background").html("");
  $("#players").html("");
  $(".tournament-name .text").text("");
  $("#eventInfo .text").text("");
  $("#headerIcon").css("background-image", "none");
}

function injectFonts() {
  if (_fontsInjected) return;
  _fontsInjected = true;

  const style = document.createElement("style");
  const boldPath = "../../assets/font/LINESeedJP/LINESeedJP-Bold.ttf";
  const regularPath = "../../assets/font/LINESeedJP/LINESeedJP-Regular.ttf";

  style.textContent = `
    @font-face {
      font-family: "LINE Seed JP";
      src: url("${regularPath}") format("truetype");
      font-weight: 400;
      font-display: block;
    }
    @font-face {
      font-family: "LINE Seed JP";
      src: url("${boldPath}") format("truetype");
      font-weight: 700;
      font-display: block;
    }
  `;
  document.head.appendChild(style);
}

function applyConfig(config) {
  const root = document.documentElement;
  root.style.setProperty("--accent-color", config.accent_color || "#f0a500");
}

function setBackground(config) {
  const bgPath = resolveAssetPath(config.background_path || "./assets/thumbnail_base/default_banner.png");
  if (!bgPath) return;
  $(".background").html(`<img src="${bgPath}" />`);
}

function setHeader(data, config) {
  const name = data.tournamentName || "";
  $(".tournament-name .text").text(name);

  // Build info line: event name + date + entrants. Each part is only added
  // when it resolves to a non-empty value, so missing info leaves a clean
  // line rather than placeholder/garbage text.
  const parts = [];
  if (data.eventName) parts.push(data.eventName);
  const dateStr = formatDate(data.startAt);
  if (dateStr) parts.push(dateStr);
  if (data.numEntrants) parts.push(data.numEntrants + " Entrants");
  $("#eventInfo .text").text(parts.join("  |  "));

  const iconPath = resolveAssetPath(config.main_icon_path || "./layout/logo.png");
  if (iconPath) {
    const cb = `${iconPath.includes("?") ? "&" : "?"}_t=${Date.now()}`;
    $("#headerIcon").css("background-image", `url("${iconPath}${cb}")`);
  }
}

// tournamentInfo.startAt is stored as a localized short-format date STRING
// (set from QDateEdit.dateChanged in AutonaTournamentInfoWidget), but may also
// arrive as a Unix timestamp. Parse defensively and return "" when the value is
// absent or unparseable so the info line stays clean (no "undefined NaN, NaN").
function formatDate(value) {
  if (value == null || value === "") return "";
  const s = String(value).trim();
  if (!s) return "";

  let d = null;
  if (/^\d+$/.test(s)) {
    // Pure number → Unix timestamp (seconds for start.gg, ms otherwise)
    const num = Number(s);
    d = new Date(num > 1e12 ? num : num * 1000);
  } else {
    // Try direct parse first (ISO 8601, "yyyy/MM/dd", "M/d/yyyy", etc.)
    const direct = new Date(s);
    if (!isNaN(direct.getTime())) {
      d = direct;
    } else {
      // Slash/dash/dot separated date that the engine couldn't parse directly
      const m = s.match(/^(\d{1,4})[\/\-.](\d{1,2})[\/\-.](\d{1,4})$/);
      if (m) {
        const a = parseInt(m[1], 10);
        const b = parseInt(m[2], 10);
        const c = parseInt(m[3], 10);
        if (a > 31) {
          d = new Date(a, b - 1, c);            // YYYY/MM/DD
        } else if (c > 31) {
          // DD/MM/YYYY when first part is clearly a day, else MM/DD/YYYY
          d = a > 12 ? new Date(c, b - 1, a) : new Date(c, a - 1, b);
        }
      }
    }
  }

  if (!d || isNaN(d.getTime())) return "";
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
}

async function placePlayers(data, config) {
  const players = data.players || [];
  const container = $("#players");
  const loads = [];

  for (let i = 0; i < players.length; i++) {
    const p = players[i];
    const rank = p.rank || (i + 1);
    const slotIndex = i + 1;
    const rankLabel = getRankLabel(rank);

    const slot = $(`<div class="player-slot slot-${slotIndex} rank-${rank}"></div>`);

    // Accent bar
    slot.append(`<div class="accent-bar"></div>`);

    // Character image
    const charDiv = $(`<div class="char-image"></div>`);
    slot.append(charDiv);

    // Cinematic lighting overlay
    slot.append(`<div class="lighting"></div>`);

    // Dark overlay for text
    slot.append(`<div class="overlay"></div>`);

    // Secondary character icons (characters[1+]) — top-right stack
    const characters = p.characters && p.characters.length > 0
      ? p.characters
      : (p.character ? [p.character] : []);
    const secondaries = characters.slice(1);
    if (secondaries.length > 0) {
      const iconsDiv = $(`<div class="secondary-icons"></div>`);
      secondaries.forEach((char) => {
        if (!char || !char.assets) return;
        const assetObj = char.assets["base_files/icon"]
          || char.assets["full"]
          || GetBiggestAsset(char);
        if (!assetObj) return;
        const assetPath = typeof assetObj === "string" ? assetObj : assetObj.asset;
        if (!assetPath) return;
        const resolvedPath = resolveAssetPath(assetPath);
        if (!resolvedPath) return;
        iconsDiv.append(`<div class="secondary-icon"><img src="${resolvedPath}" /></div>`);
      });
      slot.append(iconsDiv);
    }

    // Rank badge
    slot.append(`<div class="rank-badge">${escapeHtml(rankLabel)}</div>`);

    // Player info
    const sponsorHtml = p.sponsor
      ? `<div class="player-sponsor">${escapeHtml(p.sponsor)}</div>`
      : "";
    slot.append(`
      <div class="player-info">
        ${sponsorHtml}
        <div class="player-name">${escapeHtml(p.name || "???")}</div>
      </div>
    `);

    container.append(slot);

    // Place character image (main = characters[0], fallback to p.character)
    const mainChar = characters[0] || p.character;
    if (mainChar && mainChar.assets) {
      const asset = mainChar.assets["full"] || GetBiggestAsset(mainChar);
      if (asset) {
        loads.push(
          CenterImage(charDiv.get(0), asset, {
            custom_zoom: 1,
            custom_center: [0.5, 0.3],
          })
        );
      }
    }
  }

  await Promise.allSettled(loads);
}

/**
 * Team / Crew standings: each rank slot is a team card whose members each show
 * a single main fighter with a name caption, anchored by the team name plate.
 * Reuses the base Top-8 grid (slot-N classes) and rank auras from result.css;
 * the per-member arrangement lives in result_team/result.css.
 */
async function placeTeams(data, config, layout) {
  const teams = data.players || [];
  const container = $("#players");
  const loads = [];

  for (let i = 0; i < teams.length; i++) {
    const t = teams[i];
    const rank = t.rank || (i + 1);
    const slotIndex = i + 1;
    // team_split convention: slot-4 is a tied 3rd (shares the bronze aura
    // with slot-3); slots 5-8 share a tied 5th place. Use effectiveRank for
    // both the rank-N CSS class (aura color) and the displayed label.
    let effectiveRank = rank;
    if (layout === "team_split") {
      if (slotIndex >= 5) effectiveRank = 5;
      else if (slotIndex === 4) effectiveRank = 3;
    }
    const rankLabel = getRankLabel(effectiveRank);

    const slot = $(`<div class="player-slot team-slot slot-${slotIndex} rank-${effectiveRank}"></div>`);
    slot.append(`<div class="accent-bar"></div>`);

    // Prefer an explicit members[] roster; fall back to the characters[] array
    // (no per-member names) so the slot still renders fighters if only that is
    // present.
    let members = (t.members && t.members.length > 0) ? t.members : null;
    if (!members) {
      const chars = (t.characters && t.characters.length > 0)
        ? t.characters
        : (t.character ? [t.character] : []);
      members = chars.map((c) => ({ name: "", character: c }));
    }

    const count = Math.max(1, Math.min(members.length, 5));
    const membersDiv = $(`<div class="team-members members-${count}"></div>`);

    members.forEach((mem) => {
      const cell = $(`<div class="member-cell"></div>`);
      const portrait = $(`<div class="member-portrait"></div>`);
      cell.append(portrait);
      const nm = mem && mem.name ? mem.name : "";
      cell.append(`<div class="member-name">${escapeHtml(nm)}</div>`);
      membersDiv.append(cell);

      const ch = mem ? mem.character : null;
      if (ch && ch.assets) {
        const asset = ch.assets["full"] || GetBiggestAsset(ch);
        if (asset) {
          loads.push(
            CenterImage(portrait.get(0), asset, {
              custom_zoom: 1,
              custom_center: [0.5, 0.28],
            })
          );
        }
      }
    });

    slot.append(membersDiv);

    // Rank badge + team name plate (anchored at the slot bottom).
    slot.append(`<div class="rank-badge">${escapeHtml(rankLabel)}</div>`);
    slot.append(`
      <div class="team-info">
        <div class="team-name">${escapeHtml(t.name || "???")}</div>
      </div>
    `);

    container.append(slot);
  }

  await Promise.allSettled(loads);
}

function getRankLabel(rank) {
  switch (rank) {
    case 1: return "1st";
    case 2: return "2nd";
    case 3: return "3rd";
    default: return rank + "th";
  }
}

function resolveAssetPath(path) {
  if (!path) return "";
  if (path.startsWith("./") || path.startsWith("assets/") || path.startsWith("user_data/") || path.startsWith("layout/")) {
    return "../../" + path;
  }
  if (/^[A-Z]:/i.test(path)) {
    return "file:///" + path.replace(/\\/g, "/");
  }
  return path;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

/**
 * Compress text horizontally (scaleX) when it would overflow its container,
 * so names/titles stay on a single line without truncation.
 */
function fitLongText() {
  const header = document.querySelector(".header");
  const tname = document.querySelector(".tournament-name .text");
  if (header && tname) {
    const s = getComputedStyle(header);
    const padL = parseFloat(s.paddingLeft) || 0;
    const padR = parseFloat(s.paddingRight) || 0;
    const max = header.clientWidth - padL - padR - 8;
    tname.style.transform = "";
    tname.style.display = "inline-block";
    tname.style.transformOrigin = "center";
    const actual = tname.scrollWidth;
    if (actual > max && actual > 0) {
      const scale = Math.max(0.5, max / actual);
      tname.style.transform = `scaleX(${scale})`;
    }
  }

  document.querySelectorAll(".player-slot").forEach((slot) => {
    const info = slot.querySelector(".player-info");
    if (!info) return;
    const style = getComputedStyle(info);
    const padL = parseFloat(style.paddingLeft) || 0;
    const padR = parseFloat(style.paddingRight) || 0;
    const maxWidth = info.clientWidth - padL - padR - 4;
    slot.querySelectorAll(".player-name, .player-sponsor").forEach((el) => {
      fitToWidth(el, maxWidth, "left");
    });
  });

  // Team slots: fit the team name plate and each member name caption.
  document.querySelectorAll(".team-slot").forEach((slot) => {
    const info = slot.querySelector(".team-info");
    if (info) {
      const style = getComputedStyle(info);
      const padL = parseFloat(style.paddingLeft) || 0;
      const padR = parseFloat(style.paddingRight) || 0;
      const maxWidth = info.clientWidth - padL - padR - 4;
      const tn = slot.querySelector(".team-name");
      if (tn) fitToWidth(tn, maxWidth, "left");
    }
    slot.querySelectorAll(".member-cell").forEach((cell) => {
      const nameEl = cell.querySelector(".member-name");
      if (!nameEl) return;
      // Horizontal caption: shrink long 全角 names via scaleX to keep them on
      // one line. The caption is left/right pinned with a small inset, so the
      // cell width minus that inset is the usable space.
      const maxW = cell.clientWidth - 10;
      fitToWidth(nameEl, maxW, "center");
    });
  });
}

function fitToWidth(el, maxWidth, origin) {
  if (!el || !maxWidth) return;
  el.style.transform = "";
  const actual = el.scrollWidth;
  if (actual > maxWidth && actual > 0) {
    const scale = Math.max(0.5, maxWidth / actual);
    el.style.transformOrigin = origin;
    el.style.transform = `scaleX(${scale})`;
  }
}
