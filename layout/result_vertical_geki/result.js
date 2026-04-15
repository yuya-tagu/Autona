/**
 * result_vertical_geki: Top 8 result layout (dark grey + gold theme).
 *
 * Slot 1 (1st place): full character art with gold cinematic lighting.
 * Slots 2–8: dark card layout, character shown as small icon in top-right.
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

    clearPreviousRender();
    injectFonts();
    applyConfig(config);
    setBackground(config);
    setHeader(data, config);
    await placePlayers(data, config);

    await document.fonts.ready;
    await new Promise((r) => setTimeout(r, 100));

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
  const gekiPath    = "../../assets/font/TamanegiKaisho/TamanegiKaishoGeki.ttf";
  const boldPath    = "../../assets/font/LINESeedJP/LINESeedJP-Bold.ttf";
  const regularPath = "../../assets/font/LINESeedJP/LINESeedJP-Regular.ttf";

  style.textContent = `
    @font-face {
      font-family: "Tamanegi Kaisho Geki";
      src: url("${gekiPath}") format("truetype");
      font-weight: 400;
      font-display: block;
    }
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
  root.style.setProperty("--accent-color", config.accent_color || "#c9a227");
}

function setBackground(config) {
  const bgPath = resolveAssetPath(config.background_path || "./assets/thumbnail_base/default_banner.png");
  if (!bgPath) return;
  $(".background").html(`<img src="${bgPath}" />`);
}

function setHeader(data, config) {
  const name = data.tournamentName || "";
  $(".tournament-name .text").text(name);

  const parts = [];
  if (data.eventName)    parts.push(data.eventName);
  if (data.startAt)      parts.push(formatDate(data.startAt));
  if (data.numEntrants)  parts.push(data.numEntrants + " Entrants");
  $("#eventInfo .text").text(parts.join("  |  "));

  const iconPath = resolveAssetPath(config.main_icon_path || "./layout/logo.png");
  if (iconPath) {
    const cb = `${iconPath.includes("?") ? "&" : "?"}_t=${Date.now()}`;
    $("#headerIcon").css("background-image", `url("${iconPath}${cb}")`);
  }
}

function formatDate(timestamp) {
  if (!timestamp) return "";
  const d = new Date(timestamp * 1000);
  const months = ["Jan","Feb","Mar","Apr","May","Jun",
                  "Jul","Aug","Sep","Oct","Nov","Dec"];
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

    // Main character full art
    const charDiv = $(`<div class="char-image"></div>`);
    slot.append(charDiv);

    // Cinematic layers for all slots
    slot.append(`<div class="lighting"></div>`);
    slot.append(`<div class="vignette"></div>`);
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
        // Prefer stock icon (base_files/icon), fall back to full art
        // Asset objects have shape: {asset: "path/to/img", ...}
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

    // Place main character full art via canvas centering
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
  if (
    path.startsWith("./") ||
    path.startsWith("assets/") ||
    path.startsWith("user_data/") ||
    path.startsWith("layout/")
  ) {
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
