/**
 * Thumbnail rendering entry point.
 * Called from Python via page().runJavaScript("initThumbnail(payload)").
 *
 * The HTML page is loaded ONCE; this function is called repeatedly
 * with new data (like scoreboard's Update pattern). Previous DOM
 * content is cleared at the start of each call.
 *
 * payload = { data, config, template }
 *   data: program_state content
 *   config: thumbnail_config from settings
 *   template: type_a.json content
 */

let _fontsInjected = false;

async function initThumbnail(payload) {
  try {
    const { data, config, template } = payload;

    // 0. Clear previous render (page persists between calls)
    clearPreviousRender();

    // 1. Inject @font-face (only once per page lifetime)
    injectFonts();

    // 2. Apply CSS variables from template
    applyLayout(template, data);

    // 3. Background image
    setBackground(config, template);

    // 4. Character images
    await placeCharacters(data, config, template);

    // 5. Separator lines
    drawSeparators(data, config, template);

    // 6. Player names
    setPlayerNames(data, config, template);

    // 7. Phase / match text
    setInfoText(data, config, template);

    // 8. Icons
    setIcons(config, template);

    // 9. Wait for fonts + image rasterization in off-screen view
    await document.fonts.ready;
    // setTimeout is more reliable than rAF in off-screen QWebEngineView:
    // rAF may fire before compositor finishes decoding background-image data URIs
    await new Promise((r) => setTimeout(r, 100));

    document.title = "RENDER_READY";
  } catch (e) {
    console.error("initThumbnail error:", e);
    // Signal ready even on error so Python doesn't hang
    document.title = "RENDER_READY";
  }
}

// --- Clear previous render ---

function clearPreviousRender() {
  $(".background").html("");
  $("#team1, #team2").html("");
  $("#separators").html("");
  $(".player-name .text").html("");
  $(".phase .text, .match .text").text("");
  $("#mainIcon, #sideLeft, #sideRight").css("background-image", "none");
  // Reset text scale transforms
  $(".player-name .text").css("transform", "");
  $(".info-text-container .text").css("transform", "");
  // Ensure info text is visible (may have been hidden)
  $("#infoText").show();
}

// --- Font injection (once per page lifetime) ---

function injectFonts() {
  if (_fontsInjected) return;
  _fontsInjected = true;

  const style = document.createElement("style");
  const fontPath = "../../assets/font/GenEiMGothic/GenEiMGothic2-Black.ttf";

  style.textContent = `
    @font-face {
      font-family: "thumbnail-player-font";
      src: url("${fontPath}") format("truetype");
      font-display: block;
    }
    @font-face {
      font-family: "thumbnail-phase-font";
      src: url("${fontPath}") format("truetype");
      font-display: block;
    }
  `;
  document.head.appendChild(style);
}

// --- Layout from template ---

function applyLayout(template, payload_data) {
  const root = document.documentElement;
  const t = template;

  // Character area
  root.style.setProperty("--char-x", `${t.character_images.position.x}px`);
  root.style.setProperty("--char-y", `${t.character_images.position.y}px`);
  root.style.setProperty("--char-w", `${t.character_images.dimensions.x}px`);
  root.style.setProperty("--char-h", `${t.character_images.dimensions.y}px`);

  // Player text
  const ptH = t.player_text.dimensions.y;
  const ptTop = t.player_text.height_center - ptH / 2;
  root.style.setProperty("--player-text-h", `${ptH}px`);
  root.style.setProperty("--player-text-top", `${ptTop}px`);
  root.style.setProperty("--player-text-x-offset", `${t.player_text.x_offset}px`);
  root.style.setProperty("--initial-font-size", `${t.initial_font_size}px`);

  // Info text
  const itH = t.info_text.dimensions.y;
  const itTop = t.info_text.height_center - itH / 2;
  root.style.setProperty("--info-text-h", `${itH}px`);
  root.style.setProperty("--info-text-top", `${itTop}px`);
  root.style.setProperty("--info-text-x", `${t.info_text.x_position}px`);
  root.style.setProperty("--info-text-w", `${t.info_text.dimensions.x}px`);

  // Fonts (hardcoded)
  root.style.setProperty("--player-font", '"thumbnail-player-font", sans-serif');
  root.style.setProperty("--phase-font", '"thumbnail-phase-font", sans-serif');

  // Colors (hardcoded)
  root.style.setProperty("--player-color", "#FFFFFF");
  root.style.setProperty("--phase-color", "#FFFFFF");
  root.style.setProperty("--player-outline-color", "#000000");
  root.style.setProperty("--phase-outline-color", "#000000");
  root.style.setProperty("--sponsor-color-1", "#ff7a6d");
  root.style.setProperty("--sponsor-color-2", "#29b6f6");
  root.style.setProperty("--outline-w", "3px");

  // Foreground bar color from tournament theme
  const themeColor = _.get(payload_data, "tournamentInfo.themeColor", "#2a2a2a");
  root.style.setProperty("--fg-bar-color", themeColor);

  // Icons
  const ip = t.icons_position;
  root.style.setProperty("--main-icon-w", `${ip.main.dimensions.x}px`);
  root.style.setProperty("--main-icon-h", `${ip.main.dimensions.y}px`);
  root.style.setProperty("--side-icon-w", `${ip.side.dimensions.x}px`);
  root.style.setProperty("--side-icon-h", `${ip.side.dimensions.y}px`);

  // Always enable outlines
  $("#p1name, #p2name").addClass("has-outline");
  $("#infoText").addClass("has-outline");
}

// --- Background ---

function setBackground(config, template) {
  let bgPath = config.background_path || template.default_background;
  if (bgPath) {
    bgPath = resolveAssetPath(bgPath);
    $(".background").html(`<img src="${bgPath}" />`);
  }
}

// --- Character placement ---

async function placeCharacters(data, config, template) {
  const scoreboardNumber = config.scoreboardNumber || 1;
  const gameCN = _.get(data, "game.codename", "");
  const gameConfig = _.get(config, `game.${gameCN}`, {});
  const flipP1 = gameConfig.flip_p1 || false;
  const flipP2 = gameConfig.flip_p2 || false;
  const hideSeparators = gameConfig.hideSeparators || false;
  const flipSeparators = gameConfig.flipSeparators || false;
  const customZoom = (gameConfig.zoom || 100) / 100;
  const horizontalAlign = (gameConfig.align?.horizontal || 50) / 100;
  const verticalAlign = (gameConfig.align?.vertical || 40) / 100;

  const loads = [];

  for (let teamIdx = 0; teamIdx < 2; teamIdx++) {
    const teamNum = teamIdx + 1;
    const teamEl = $(`#team${teamNum}`);

    const players = _.get(data, `score.${scoreboardNumber}.team.${teamNum}.player`, {});
    const flip = teamIdx === 0 ? flipP1 : flipP2;

    // Collect all characters for this team
    let allChars = [];
    for (const [pKey, player] of Object.entries(players)) {
      const characters = _.get(player, "character", {});
      for (const [cKey, character] of Object.entries(characters)) {
        if (!character.assets || Object.keys(character.assets).length === 0) continue;
        // Prefer "full" asset, fall back to biggest
        const asset = character.assets["full"] || GetBiggestAsset(character);
        if (!asset) continue;
        allChars.push({ asset, character });
      }
    }

    if (hideSeparators && allChars.length > 1) {
      // No-separator (radial) mode
      const positions = GenerateMulticharacterPositions(
        allChars.length, [horizontalAlign, verticalAlign]
      );

      for (let i = 0; i < allChars.length; i++) {
        const charDiv = $(`<div class="char-slot${flip ? ' flipped' : ''}"><div></div></div>`);
        charDiv.css({ position: "absolute", width: "100%", height: "100%" });
        teamEl.append(charDiv);

        const zoomRec = GetRecommendedZoom(
          allChars[i].asset, teamEl.width(), teamEl.height()
        );

        loads.push(
          CenterImage(charDiv.children(0).get(0), allChars[i].asset, {
            custom_zoom: customZoom * zoomRec,
            custom_center: positions[i],
            use_dividers: false,
            z_index: allChars.length - i,
          })
        );
      }
    } else {
      // Separator (grid) mode
      if (teamIdx === 0 && !flipSeparators) {
        allChars.reverse();
      }

      for (let i = 0; i < allChars.length; i++) {
        const charDiv = $(`<div class="char-slot${flip ? ' flipped' : ''}"><div></div></div>`);
        teamEl.append(charDiv);

        const zoomRec = GetRecommendedZoom(
          allChars[i].asset, teamEl.width() / allChars.length, teamEl.height()
        );

        loads.push(
          CenterImage(charDiv.children(0).get(0), allChars[i].asset, {
            custom_zoom: customZoom * zoomRec,
            custom_center: [horizontalAlign, verticalAlign],
          })
        );
      }
    }
  }

  await Promise.allSettled(loads);
}

// --- Separators ---

function drawSeparators(data, config, template) {
  const gameCN = _.get(data, "game.codename", "");
  const gameConfig = _.get(config, `game.${gameCN}`, {});
  const hideSeparators = gameConfig.hideSeparators || false;
  if (hideSeparators) return;

  const sepEl = $("#separators");

  const charW = template.character_images.dimensions.x;

  // Separators between characters within each team (no center line)
  const scoreboardNumber = config.scoreboardNumber || 1;
  for (let teamIdx = 0; teamIdx < 2; teamIdx++) {
    const teamNum = teamIdx + 1;
    const players = _.get(data, `score.${scoreboardNumber}.team.${teamNum}.player`, {});
    let charCount = 0;
    for (const [, player] of Object.entries(players)) {
      const chars = _.get(player, "character", {});
      for (const [, c] of Object.entries(chars)) {
        if (c.assets && Object.keys(c.assets).length > 0) charCount++;
      }
    }

    if (charCount > 1) {
      const teamW = charW / 2;
      const teamOffsetX = teamIdx === 0 ? 0 : charW / 2;
      for (let i = 1; i < charCount; i++) {
        const x = teamOffsetX + (teamW / charCount) * i;
        const line = $('<div class="separator-line vertical"></div>');
        line.css({ left: `${x}px` });
        sepEl.append(line);
      }
    }
  }
}

// --- Player names ---

function setPlayerNames(data, config, template) {
  const scoreboardNumber = config.scoreboardNumber || 1;
  const useTeamNames = config.use_team_names || false;
  const useSponsors = config.use_sponsors || false;

  for (let i = 0; i < 2; i++) {
    const teamNum = i + 1;
    const el = $(`#p${teamNum}name`);
    let nameHtml = "";

    if (useTeamNames) {
      const teamName = _.get(data, `score.${scoreboardNumber}.team.${teamNum}.teamName`, "");
      if (teamName) {
        nameHtml = escapeHtml(teamName);
      }
    }

    if (!nameHtml) {
      const players = _.get(data, `score.${scoreboardNumber}.team.${teamNum}.player`, {});
      const parts = [];

      for (const [, player] of Object.entries(players)) {
        const sponsor = player.team || "";
        const name = player.name || "";

        if (sponsor && useSponsors) {
          parts.push(`<span class="sponsor">${escapeHtml(sponsor)}</span> ${escapeHtml(name)}`);
        } else {
          parts.push(escapeHtml(name));
        }
      }

      nameHtml = parts.join(" / ");
    }

    el.find(".text").html(nameHtml);

    // Auto-scale text to fit
    requestAnimationFrame(() => FitText(el));
  }
}

// --- Info text ---

function setInfoText(data, config, template) {
  const scoreboardNumber = config.scoreboardNumber || 1;
  const displayPhase = config.display_phase !== false;

  if (!displayPhase) {
    $("#infoText").hide();
    return;
  }

  const phase = _.get(data, `score.${scoreboardNumber}.phase`, "");
  const match = _.get(data, `score.${scoreboardNumber}.match`, "");

  $(".phase .text").text(phase);
  $(".match .text").text(match);
}

// --- Icons ---

function setIcons(config, template) {
  const ip = template.icons_position;
  const charArea = template.character_images;

  // Main icon: centered on screen
  const mainW = ip.main.dimensions.x;
  const mainH = ip.main.dimensions.y;
  const mainX = (1920 - mainW) / 2;
  const mainY = (1080 - mainH) / 2;

  const mainPath = resolveAssetPath(config.main_icon_path || "./layout/logo.png");
  if (mainPath) {
    $("#mainIcon").css({
      left: `${mainX}px`,
      top: `${mainY}px`,
      "background-image": `url("${mainPath}")`,
    });
  }

  // Side icons
  const sideW = ip.side.dimensions.x;
  const sideH = ip.side.dimensions.y;
  const sideY = mainY + (mainH - sideH) / 2;

  const sideIcons = [
    { el: "#sideLeft", path: _.get(config, "side_icon_list.L", ""), x: ip.side.x_offset },
    { el: "#sideRight", path: _.get(config, "side_icon_list.R", ""), x: charArea.dimensions.x - sideW - ip.side.x_offset + charArea.position.x },
  ];

  for (const si of sideIcons) {
    if (si.path) {
      $(si.el).css({
        left: `${si.x}px`,
        top: `${sideY}px`,
        "background-image": `url("${resolveAssetPath(si.path)}")`,
      });
    }
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
