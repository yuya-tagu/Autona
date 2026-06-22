/**
 * scoreboard_Universal — shared utilities
 *
 * Provides: game-box management, swap detection,
 * theme-aware color adaptation, and display helpers.
 */

// ─── Game Box Management ────────────────────────────────────────

function scoreBoxDisplayToggle() {
  const boxes = document.querySelector(".score_boxes");
  if (data.score[window.scoreboardNumber].best_of > 0) {
    boxes.classList.add("unhidden");
  } else {
    boxes.classList.remove("unhidden");
  }
}

function createGameBoxes(savedBestOf) {
  const bestOf = data.score[window.scoreboardNumber].best_of;

  if (bestOf > 0 && bestOf !== savedBestOf) {
    let word = "", red = "", blue = "", dark = "";
    for (let i = 1; i <= bestOf; i++) {
      word += `<div class="game${i} box"></div>\n`;
      red  += `<div class="game${i} box p1_won hidden"></div>\n`;
      blue += `<div class="game${i} box p2_won hidden"></div>\n`;
      dark += `<div class="game${i} box neither_won"></div>\n`;
    }
    SetInnerHtml($(".word.score_boxes"), word);
    SetInnerHtml($(".red.score_boxes"), red);
    SetInnerHtml($(".blue.score_boxes"), blue);
    SetInnerHtml($(".dark.score_boxes"), dark);
  } else if (bestOf === 0) {
    SetInnerHtml($(".word.score_boxes"), "");
    SetInnerHtml($(".red.score_boxes"), "");
    SetInnerHtml($(".blue.score_boxes"), "");
    SetInnerHtml($(".dark.score_boxes"), "");
  }
  return bestOf;
}

function colorInBoxes(savedGameArray) {
  const bestOf = data.score[window.scoreboardNumber].best_of;
  for (let i = 0; i < bestOf; i++) {
    const p1Box = document.querySelector(`.game${i + 1}.p1_won`);
    const p2Box = document.querySelector(`.game${i + 1}.p2_won`);
    if (!p2Box) continue;

    if (savedGameArray[i] === 1) {
      p1Box.classList.add("unhidden");
      p2Box.classList.remove("unhidden");
    } else if (savedGameArray[i] === 2) {
      p1Box.classList.remove("unhidden");
      p2Box.classList.add("unhidden");
    } else {
      p1Box.classList.remove("unhidden");
      p2Box.classList.remove("unhidden");
    }
  }
}

function scheduleColorInBoxes(savedGameArray) {
  let count = 0;
  const interval = setInterval(() => {
    colorInBoxes(savedGameArray);
    if (++count >= 10) clearInterval(interval);
  }, 100);
}

// ─── Score / Game Array Tracking ────────────────────────────────

function updateGameArray(savedGameArray, newP1, newP2, oldP1, oldP2) {
  const bestOf = data.score[window.scoreboardNumber].best_of;
  let arr = JSON.parse(localStorage.getItem("output")) || savedGameArray;

  const teams = [
    data.score[window.scoreboardNumber].team["1"],
    data.score[window.scoreboardNumber].team["2"],
  ];
  newP1 = teams[0].score;
  newP2 = teams[1].score;
  const gameNum = newP1 + newP2;

  if (gameNum === 0) arr = [];

  // Score increased — record wins
  if (newP1 > oldP1) {
    for (let i = 0; i < newP1 - oldP1; i++)
      arr[gameNum - (newP1 - oldP1) + i] = 1;
  }
  if (newP2 > oldP2) {
    for (let i = 0; i < newP2 - oldP2; i++)
      arr[gameNum - (newP2 - oldP2) + i] = 2;
  }

  // Score decreased — remove most recent wins
  if (newP1 < oldP1 && oldP1 < 100) {
    for (let i = 0; i < oldP1 - newP1; i++) {
      const idx = arr.lastIndexOf(1);
      if (idx !== -1) arr.splice(idx, 1);
    }
  }
  if (newP2 < oldP2 && oldP2 < 100) {
    for (let i = 0; i < oldP2 - newP2; i++) {
      const idx = arr.lastIndexOf(2);
      if (idx !== -1) arr.splice(idx, 1);
    }
  }

  localStorage.setItem("output", JSON.stringify(arr));
  return {
    savedGameArray: arr,
    newP1Score: newP1,
    newP2Score: newP2,
    p1Score: newP1,
    p2Score: newP2,
  };
}

// ─── Swap Detection ─────────────────────────────────────────────

function comparePlayerObjects(obj1, obj2, skipKeys = ["character", "mains", "id", ""]) {
  for (const key of Object.keys(obj1).sort()) {
    if (skipKeys.includes(key)) continue;
    if (!obj2.hasOwnProperty(key)) return false;
    if (typeof obj1[key] === "object" && obj1[key] && obj2[key]) {
      if (!comparePlayerObjects(obj1[key], obj2[key], skipKeys)) return false;
    } else if (obj1[key] !== obj2[key]) {
      return false;
    }
  }
  return true;
}

function createSwapDetector(isTeams) {
  let player1, player2;
  const skipKeys = isTeams
    ? ["seed", "character", "mains", "id", ""]
    : ["character", "mains", "id", ""];

  return function detect(savedGameArray, scores) {
    const teams = [
      data.score[window.scoreboardNumber].team["1"],
      data.score[window.scoreboardNumber].team["2"],
    ];
    const newP1 = teams[0].player["1"];
    const newP2 = teams[1].player["1"];

    let swapped = false;
    if (
      player1 && player2 &&
      comparePlayerObjects(player2, newP1, skipKeys) &&
      comparePlayerObjects(player1, newP2, skipKeys)
    ) {
      swapped = true;
      for (let i = 0; i < savedGameArray.length; i++) {
        if (savedGameArray[i] === 1) savedGameArray[i] = 2;
        else if (savedGameArray[i] === 2) savedGameArray[i] = 1;
      }
      localStorage.setItem("output", JSON.stringify(savedGameArray));
      [scores.p1Score, scores.p2Score] = [scores.p2Score, scores.p1Score];
    }

    player1 = newP1;
    player2 = newP2;
    return swapped;
  };
}

// ─── Theme-Aware Color Adaptation ───────────────────────────────

function adaptColorsForTheme(t) {
  const stylesheet = document.styleSheets[1];
  const side = t + 1;
  const container = document.querySelector(`.p${side}.container`);
  const scoreContainer = document.querySelector(`.p${side}.score_container`);
  const scoreEl = scoreContainer.querySelector(".score");
  const nameEl = container.querySelector(".name");
  const chips = document.querySelector(`.p${side}.chips`);
  const lightEl = document.querySelector(`.p${side}.light`);

  // Measure intensity from the container itself (theme color)
  const color = getComputedStyle(container).backgroundColor;
  const m = color.match(/^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/);
  if (!m) return;

  const intensity = parseInt(m[1]) * 0.299 + parseInt(m[2]) * 0.587 + parseInt(m[3]) * 0.114;

  const noShadow = "none";
  const darkShadow = "0em 0em 0.2em #000";
  const noFilter = "none";
  const glowFilter = "drop-shadow(0 0px 2px rgba(255, 255, 255, 0.85))";
  const noStroke = "unset";
  const thinStroke = "0.5px rgb(24, 24, 27)";

  let textColor, iconVariant, shadow, filter, showLight, stroke;

  if (intensity > 142) {
    // Bright theme: dark text, slight stroke to compensate thin appearance
    textColor = "rgb(24, 24, 27)";
    iconVariant = "black"; shadow = noShadow; filter = noFilter; showLight = false; stroke = thinStroke;
  } else if (intensity > 75) {
    // Medium theme: white text
    textColor = "white";
    iconVariant = "white"; shadow = darkShadow; filter = noFilter; showLight = false; stroke = noStroke;
  } else {
    // Dark theme: white text, glow
    textColor = "white";
    iconVariant = "white"; shadow = darkShadow; filter = glowFilter; showLight = true; stroke = noStroke;
  }

  scoreEl.style.color = textColor;
  nameEl.style.color = textColor;

  // Round info is shared/centered — update once on the p1 pass (t===0)
  if (t === 0) {
    const roundInfo = document.querySelector(".round_info");
    if (roundInfo) roundInfo.style.color = textColor;
    setRule(stylesheet, ".tournament_name", "-webkit-text-stroke", stroke);
    setRule(stylesheet, ".phase_label",     "-webkit-text-stroke", stroke);
    setRule(stylesheet, ".match_label",     "-webkit-text-stroke", stroke);
  }

  setRule(stylesheet, `.p${side} .twitter_logo`, "background-image", `url(./x_twitter_${iconVariant}.png)`);
  setRule(stylesheet, `.p${side} .location_logo`, "background-image", `url(./map_pin_${iconVariant}.png)`);
  setRule(stylesheet, `.p${side} .chip .text:not(.text_empty)`, "text-shadow", shadow);
  setRule(stylesheet, `.p${side}.container`, "filter", filter);
  setRule(stylesheet, `.p${side}.score_container`, "filter", filter);
  setRule(stylesheet, `.p${side}.container .name`, "-webkit-text-stroke", stroke);
  setRule(stylesheet, `.p${side} .score`, "-webkit-text-stroke", stroke);
  setRule(stylesheet, `.p${side} .chip .text:not(.text_empty)`, "-webkit-text-stroke", stroke);

  if (lightEl) {
    lightEl.classList.toggle("unhidden", showLight);
  }

  for (const el of chips.getElementsByClassName("chip")) {
    el.style.color = textColor;
  }
}

function setRule(stylesheet, selector, property, value) {
  selector = selector.toLowerCase();
  property = property.toLowerCase();
  value = value.toLowerCase();
  for (const rule of stylesheet.cssRules) {
    if (rule.selectorText === selector) {
      rule.style[property] = value;
      return;
    }
  }
  stylesheet.insertRule(`${selector} { ${property}: ${value}; }`, 0);
}

// ─── Display Helpers ────────────────────────────────────────────

async function displayMatchPhase() {
  const sb = data.score[window.scoreboardNumber];
  const phase = sb.phase || "";
  const match = sb.match || "";
  const tournamentName = data.tournamentInfo?.tournamentName || "";
  const hasInfo = phase || match || tournamentName;

  document.querySelector(".tournament_name").textContent = tournamentName;
  document.querySelector(".phase_label").textContent = phase;
  document.querySelector(".match_label").textContent = match;
  document.querySelector(".round_separator").textContent = (phase && match) ? "|" : "";

  gsap.to(".round_info", { duration: 0.5, autoAlpha: hasInfo ? 1 : 0 });
}

async function renderPlayerInfo(t, team, player) {
  const side = t + 1;

  SetInnerHtml(
    $(`.p${side}.container .sponsor_icon`),
    player.sponsor_logo
      ? `<div style="background-image: url('../../${player.sponsor_logo}')"></div>`
      : ""
  );

  SetInnerHtml(
    $(`.p${side}.container .name`),
    `<span>
      ${(player.team && data.display_options?.show_sponsor !== false) ? `<span class="sponsor">${player.team}</span>` : ""}${player.name ? await Transcript(player.name) : ""}${team.losers ? " (L)" : ""}
    </span>`
  );

  SetInnerHtml(
    $(`.p${side}.player_flag_container`),
    player.country.asset
      ? `<div class='flag' style="background-image: url('../../${player.country.asset.toLowerCase()}')"></div>`
      : ""
  );

  SetInnerHtml($(`.p${side} .score`), String(team.score));
  SetInnerHtml($(`.p${side} .seed`), player.seed ? `SEED ${player.seed}` : "");
  SetInnerHtml($(`.p${side} .pronoun`), player.pronoun || "");
  SetInnerHtml(
    $(`.p${side} .flagstate`),
    player.state.name
      ? `<div class="location_logo symbol"></div>${player.state.name}`
      : ""
  );
  SetInnerHtml(
    $(`.p${side} .twitter`),
    player.twitter
      ? `<div class="twitter_logo symbol"></div>${player.twitter}`
      : ""
  );
}

async function renderTeamInfo(t, team) {
  const side = t + 1;
  const names = [];
  for (const player of Object.values(team.player)) {
    if (player && player.name) names.push(await Transcript(player.name));
  }
  const roster = names.join(" / ");

  // Team layout: headline the team name with the player roster beneath it.
  // Falls back to roster-only when no team name is set (matches upstream
  // Universal behaviour).
  const teamName = team.teamName || "";
  SetInnerHtml(
    $(`.p${side}.container .name`),
    teamName
      ? `<span class="team_title">${teamName}${team.losers ? " (L)" : ""}</span>` +
        `<span class="team_roster">${roster}</span>`
      : `<span>${roster}${team.losers ? " (L)" : ""}</span>`
  );

  SetInnerHtml($(`.p${side} .score`), String(team.score));
  SetInnerHtml($(`.p${side} .seed`), "");
  SetInnerHtml($(`.p${side} .flagcountry`), "");
  SetInnerHtml($(`.p${side} .pronoun`), "");
  SetInnerHtml($(`.p${side} .flagstate`), "");
  SetInnerHtml($(`.p${side} .twitter`), "");
  SetInnerHtml($(`.p${side}.container .sponsor_icon`), "");
}

// ─── Starting Animation (reusable timeline) ─────────────────────

function buildStartingAnimation() {
  return gsap.timeline({ paused: true })
    .to("body", { duration: 0.5, opacity: 1, ease: "power2.out" }, 0);
}
