/**
 * Center Panel (type_b): Center Panel layout (NFL-style).
 *
 * Layout: left/right character areas flank a vertical center panel that
 * contains tournament name, main icon, phase/match text, and best-of.
 * Player names are overlaid as bottom strips on each character area.
 *
 * Called from Python via page().runJavaScript("initThumbnail(payload)").
 * payload = { data, config, template }
 */

let _fontsInjected = false;

async function initThumbnail(payload) {
  try {
    const { data, config, template } = payload;

    clearPreviousRender();
    injectFonts();
    applyLayout(template);
    setBackground(data, config, template);
    await placeCharacters(data, config, template);
    setTournamentName(data, config, template);
    setMainIcon(config, template);
    setPhaseMatch(data, config, template);
    setVenueInfo(data, config, template);
    setPlayerNames(data, config, template);

    await document.fonts.ready;
    // setTimeout is more reliable than rAF in off-screen QWebEngineView:
    // rAF may fire before compositor finishes decoding background-image data URIs
    await new Promise((r) => setTimeout(r, 100));

    document.title = "RENDER_READY";
  } catch (e) {
    console.error("initThumbnail error:", e);
    document.title = "RENDER_READY";
  }
}

// --- Clear previous render ---

function clearPreviousRender() {
  $(".background").html("");
  $("#team1, #team2").html("");
  $(".tournament-name .text").text("");
  $(".phase-match .text").text("");
  $(".venue-info .text").text("");
  $(".player-name .text").html("");
  $("#mainIcon").css("background-image", "none");
  $(".tournament-name .text, .phase-match .text, .player-name .text, .venue-info .text")
    .css("transform", "");
}

// --- Font injection (once per page lifetime) ---

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
    /* Aliases used by the Standard (Center Panel) template */
    @font-face {
      font-family: "thumbnail-player-font";
      src: url("${boldPath}") format("truetype");
      font-display: block;
    }
    @font-face {
      font-family: "thumbnail-phase-font";
      src: url("${boldPath}") format("truetype");
      font-display: block;
    }
  `;
  document.head.appendChild(style);
}

// --- Layout (CSS variables from template JSON) ---

function applyLayout(template) {
  const root = document.documentElement;

  const t1 = template.team_areas.team_1;
  const t2 = template.team_areas.team_2;
  root.style.setProperty("--team1-x", `${t1.x}px`);
  root.style.setProperty("--team2-x", `${t2.x}px`);
  root.style.setProperty("--team-y", `${t1.y}px`);
  root.style.setProperty("--team-w", `${t1.width}px`);
  root.style.setProperty("--team-h", `${t1.height}px`);

  const cp = template.center_panel;
  root.style.setProperty("--panel-x", `${cp.x}px`);
  root.style.setProperty("--panel-y", `${cp.y}px`);
  root.style.setProperty("--panel-w", `${cp.width}px`);
  root.style.setProperty("--panel-h", `${cp.height}px`);
  root.style.setProperty("--panel-bg-color", cp.background_color || "#1D1B18");

  const tn = template.tournament_name;
  root.style.setProperty("--tournament-y", `${tn.y}px`);
  root.style.setProperty("--tournament-h", `${tn.height}px`);
  root.style.setProperty("--tournament-font-size", `${tn.font_size}px`);
  root.style.setProperty("--tournament-color", tn.color || "#cfd3da");

  const mi = template.main_icon;
  // Center main icon horizontally inside panel
  const mainX = cp.x + (cp.width - mi.width) / 2;
  root.style.setProperty("--main-icon-x", `${mainX}px`);
  root.style.setProperty("--main-icon-y", `${mi.y}px`);
  root.style.setProperty("--main-icon-w", `${mi.width}px`);
  root.style.setProperty("--main-icon-h", `${mi.height}px`);

  const pm = template.phase_match;
  root.style.setProperty("--phase-y", `${pm.y}px`);
  root.style.setProperty("--phase-h", `${pm.height}px`);
  // Split sizes: phase name is small, match (round) name is large
  const matchFontSize = pm.match_font_size != null ? pm.match_font_size : (pm.font_size || 128);
  const phaseFontSize = pm.phase_font_size != null ? pm.phase_font_size : Math.round(matchFontSize * 0.35);
  root.style.setProperty("--match-font-size", `${matchFontSize}px`);
  root.style.setProperty("--phase-name-font-size", `${phaseFontSize}px`);
  // Container default (used by older rules / fallbacks)
  root.style.setProperty("--phase-font-size", `${matchFontSize}px`);
  root.style.setProperty("--phase-color", pm.color || "#ffffff");
  root.style.setProperty("--phase-bg-color", pm.background_color || "rgba(0,0,0,0.35)");

  const venue = template.venue_info || {};
  root.style.setProperty("--venue-y", `${venue.y != null ? venue.y : 970}px`);
  root.style.setProperty("--venue-h", `${venue.height != null ? venue.height : 80}px`);
  root.style.setProperty("--venue-font-size", `${venue.font_size != null ? venue.font_size : 28}px`);
  root.style.setProperty("--venue-color", venue.color || "#cfd3da");
  root.style.setProperty("--venue-opacity", `${venue.opacity != null ? venue.opacity : 0.78}`);

  const pn = template.player_names;
  root.style.setProperty("--name-h", `${pn.height}px`);
  root.style.setProperty("--name-bottom", `${pn.bottom_offset || 0}px`);
  root.style.setProperty("--name-padding-bottom", `${pn.padding_bottom != null ? pn.padding_bottom : 28}px`);
  root.style.setProperty("--name-font-size", `${pn.font_size}px`);
  root.style.setProperty("--sponsor-font-size", `${pn.sponsor_font_size || Math.round(pn.font_size * 0.7)}px`);
  root.style.setProperty("--name-color", pn.color || "#ffffff");
  root.style.setProperty("--sponsor-color-1", pn.sponsor_color_1 || "#ff7a6d");
  root.style.setProperty("--sponsor-color-2", pn.sponsor_color_2 || "#29b6f6");
  // Alias sponsor colors as 1P/2P team colors — used for both the background
  // tint (horizontal gradient over the grayscale image) and the player name
  // strip backgrounds. Single source of truth keeps the whole layout coherent.
  root.style.setProperty("--p1-color", pn.sponsor_color_1 || "#ff7a6d");
  root.style.setProperty("--p2-color", pn.sponsor_color_2 || "#29b6f6");

  // Background filter (brightness + blur)
  const bg = template.background || {};
  root.style.setProperty("--bg-brightness", `${bg.brightness != null ? bg.brightness : 0.55}`);
  root.style.setProperty("--bg-blur", `${bg.blur != null ? bg.blur : 10}px`);

  // Fonts (hardcoded family aliases — actual font face injected separately)
  root.style.setProperty("--player-font", '"thumbnail-player-font", sans-serif');
  root.style.setProperty("--phase-font", '"thumbnail-phase-font", sans-serif');
}

// --- Background ---

function setBackground(data, config, template) {
  // Tint is now driven by --p1-color / --p2-color (see applyLayout) via a
  // horizontal gradient on .background::after — no per-call setup needed here.

  // Prefer the user-selected background (Thumbnail Settings → Background);
  // fall back to the template's default image when none is chosen. Either way
  // the background is forced grayscale + duotone-tinted by the CSS, so a custom
  // image still reads as the Standard "Center Panel" style.
  const bgPath = config.background_path || template.default_background;
  if (!bgPath) return;
  const resolved = resolveAssetPath(bgPath);
  // No cache buster: re-decoding a multi-MB image on every render was the main
  // cause of UI stalls during preview updates. Switching the selection changes
  // the resolved path (hence the URL), so QWebEngine re-fetches naturally
  // without one — only an in-place edit of the same file would be missed.
  $(".background").html(`<img src="${resolved}" />`);
}

// --- Character placement ---

async function placeCharacters(data, config, template) {
  const scoreboardNumber = config.scoreboardNumber || 1;
  const gameCN = _.get(data, "game.codename", "");
  const gameConfig = _.get(config, `game.${gameCN}`, {});
  const flipP1 = gameConfig.flip_p1 || false;
  const flipP2 = gameConfig.flip_p2 || false;
  const customZoom = (gameConfig.zoom || 100) / 100;
  const horizontalAlign = (gameConfig.align?.horizontal || 50) / 100;
  const verticalAlign = (gameConfig.align?.vertical || 40) / 100;

  const loads = [];

  for (let teamIdx = 0; teamIdx < 2; teamIdx++) {
    const teamNum = teamIdx + 1;
    const teamEl = $(`#team${teamNum}`);

    const players = _.get(data, `score.${scoreboardNumber}.team.${teamNum}.player`, {});
    const flip = teamIdx === 0 ? flipP1 : flipP2;

    let allChars = [];
    for (const [, player] of Object.entries(players)) {
      const characters = _.get(player, "character", {});
      for (const [, character] of Object.entries(characters)) {
        if (!character.assets || Object.keys(character.assets).length === 0) continue;
        const asset = character.assets["full"] || GetBiggestAsset(character);
        if (!asset) continue;
        allChars.push({ asset, character });
      }
    }

    // Right team: reverse so the lead character is closest to the center panel
    if (teamIdx === 0) {
      allChars.reverse();
    }

    for (let i = 0; i < allChars.length; i++) {
      const charDiv = $(`<div class="char-slot${flip ? " flipped" : ""}"><div></div></div>`);
      teamEl.append(charDiv);

      const slotW = teamEl.width() / Math.max(allChars.length, 1);
      const zoomRec = GetRecommendedZoom(allChars[i].asset, slotW, teamEl.height());

      loads.push(
        CenterImage(charDiv.children(0).get(0), allChars[i].asset, {
          custom_zoom: customZoom * zoomRec,
          custom_center: [horizontalAlign, verticalAlign],
        })
      );
    }
  }

  await Promise.allSettled(loads);
}

// --- Tournament name (top of center panel) ---

function setTournamentName(data, config, template) {
  const name = _.get(data, "tournamentInfo.tournamentName", "")
    || _.get(data, "tournamentInfo.name", "")
    || "";
  const el = $(".tournament-name");
  el.find(".text").text(name);
  requestAnimationFrame(() => FitText(el));
}

// --- Main icon (center of panel) ---

function setMainIcon(config, template) {
  const mainPath = resolveAssetPath(config.main_icon_path || "./layout/logo.png");
  if (!mainPath) return;
  // Cache buster: see setBackground for rationale.
  const cb = `${mainPath.includes("?") ? "&" : "?"}_t=${Date.now()}`;
  $("#mainIcon").css("background-image", `url("${mainPath}${cb}")`);
}

// --- Phase / match text (lower center panel) ---

// Abbreviate common round names: "Winners Semi-Final" → "WSF" etc.
// Returns the original string if no abbreviation rule matches.
function abbreviateMatchName(text) {
  if (!text) return text;
  const trimmed = text.trim();

  const exact = {
    "Grand Final Reset": "GFR",
    "Grand Final": "GF",
    "Winners Final": "WF",
    "Winners Semi-Final": "WSF",
    "Winners Semi Final": "WSF",
    "Winners Quarter-Final": "WQF",
    "Winners Quarter Final": "WQF",
    "Losers Final": "LF",
    "Losers Semi-Final": "LSF",
    "Losers Semi Final": "LSF",
    "Losers Quarter-Final": "LQF",
    "Losers Quarter Final": "LQF",
    "Losers Top 8": "L8",
  };
  if (exact[trimmed]) return exact[trimmed];

  let m = trimmed.match(/^Winners Round\s+(\d+)$/i);
  if (m) return `WR${m[1]}`;
  m = trimmed.match(/^Losers Round\s+(\d+)$/i);
  if (m) return `LR${m[1]}`;
  m = trimmed.match(/^Round\s+(\d+)$/i);
  if (m) return `R${m[1]}`;

  return trimmed;
}

function setPhaseMatch(data, config, template) {
  const scoreboardNumber = config.scoreboardNumber || 1;
  const displayPhase = config.display_phase !== false;

  const phaseMatchEl = $(".phase-match");
  const textEl = phaseMatchEl.find(".text");
  textEl.empty();

  if (!displayPhase) {
    return;
  }

  // Pool is a separate state key (score.N.pool); join it for display.
  const phase = [
    _.get(data, `score.${scoreboardNumber}.phase`, ""),
    _.get(data, `score.${scoreboardNumber}.pool`, ""),
  ].filter(Boolean).join(" - ");
  const matchRaw = _.get(data, `score.${scoreboardNumber}.match`, "");
  const match = abbreviateMatchName(matchRaw);

  // Render phase (small) and match/round (large) as separate stacked spans
  // so each can use its own font-size independently.
  if (phase) {
    textEl.append(`<span class="phase-text">${escapeHtml(phase)}</span>`);
  }
  if (match) {
    textEl.append(`<span class="match-text">${escapeHtml(match)}</span>`);
  }

  // Auto-fit width (scales both children proportionally via transform: scaleX)
  requestAnimationFrame(() => FitText(phaseMatchEl));
}

// --- Venue info (date + location, bottom of center panel) ---

// Format a tournamentInfo.startAt value (unix timestamp / ISO / locale string)
// into a compact English date like "Dec 15, 2025". Falls back to the raw
// string if it cannot be parsed.
function formatDateEn(value) {
  if (value == null || value === "") return "";
  const s = String(value).trim();
  if (!s) return "";

  let d = null;

  // Pure number → unix timestamp (seconds for start.gg, fall through to ms)
  if (/^\d+$/.test(s)) {
    const num = Number(s);
    d = new Date(num > 1e12 ? num : num * 1000);
  } else {
    // Try direct parse first (handles ISO 8601, RFC 2822, "MM/DD/YYYY", etc.)
    const tryDirect = new Date(s);
    if (!isNaN(tryDirect.getTime())) {
      d = tryDirect;
    } else {
      // Try slash/dash separated date (e.g. "2025/12/15", "15/12/2025")
      const m = s.match(/^(\d{1,4})[\/\-\.](\d{1,2})[\/\-\.](\d{1,4})$/);
      if (m) {
        let a = parseInt(m[1], 10);
        let b = parseInt(m[2], 10);
        let c = parseInt(m[3], 10);
        if (a > 31) {
          // YYYY/MM/DD
          d = new Date(a, b - 1, c);
        } else if (c > 31) {
          // DD/MM/YYYY if first part is clearly > 12, else assume MM/DD/YYYY
          if (a > 12) {
            d = new Date(c, b - 1, a);
          } else {
            d = new Date(c, a - 1, b);
          }
        }
      }
    }
  }

  if (!d || isNaN(d.getTime())) return s;
  try {
    return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
  } catch (e) {
    return s;
  }
}

function setVenueInfo(data, config, template) {
  const venueEl = $(".venue-info");
  const textEl = venueEl.find(".text");
  textEl.empty();

  const dateRaw = _.get(data, "tournamentInfo.startAt", "");
  const dateStr = formatDateEn(dateRaw);
  if (!dateStr) return;

  textEl.text(dateStr);
  requestAnimationFrame(() => FitText(venueEl));
}

// --- Player names (bottom strips) ---

function setPlayerNames(data, config, template) {
  const scoreboardNumber = config.scoreboardNumber || 1;
  const useTeamNames = config.use_team_names || false;
  const useSponsors = config.use_sponsors !== false && data.display_options?.show_sponsor !== false;

  for (let i = 0; i < 2; i++) {
    const teamNum = i + 1;
    const el = $(`#p${teamNum}name`);
    let nameHtml = "";

    if (useTeamNames) {
      const teamName = _.get(data, `score.${scoreboardNumber}.team.${teamNum}.teamName`, "");
      if (teamName) {
        nameHtml = `<span class="name-line">${escapeHtml(teamName)}</span>`;
      }
    }

    if (!nameHtml) {
      const players = _.get(data, `score.${scoreboardNumber}.team.${teamNum}.player`, {});
      const playerEntries = Object.entries(players);

      if (playerEntries.length === 1) {
        // Single player: sponsor on its own line above the name
        const [, player] = playerEntries[0];
        const sponsor = player.team || "";
        const name = player.name || "";
        const sponsorLine = (sponsor && useSponsors)
          ? `<span class="sponsor">${escapeHtml(sponsor)}</span>`
          : "";
        nameHtml = `${sponsorLine}<span class="name-line">${escapeHtml(name)}</span>`;
      } else {
        // Multiple players (doubles etc.): one player per line, sponsor inline
        const lines = playerEntries.map(([, player]) => {
          const sponsor = player.team || "";
          const name = player.name || "";
          if (sponsor && useSponsors) {
            return `<span class="name-line"><span class="sponsor-inline">${escapeHtml(sponsor)}</span> ${escapeHtml(name)}</span>`;
          }
          return `<span class="name-line">${escapeHtml(name)}</span>`;
        });
        nameHtml = lines.join("");
      }
    }

    el.find(".text").html(nameHtml);

    requestAnimationFrame(() => FitText(el));
  }
}

// --- Utilities ---

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
