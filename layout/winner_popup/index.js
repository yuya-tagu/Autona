LoadEverything().then(() => {
  gsap.config({ nullTargetWarn: false, trialWarn: false });

  const popup = document.querySelector(".winner_popup");
  let shownKey = null; // "<side>:<name>" of the winner currently on screen

  // Black or white, whichever reads better on the given #rrggbb background.
  function readableOn(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec((hex || "").trim());
    if (!m) return "#ffffff";
    const n = parseInt(m[1], 16);
    const lin = (c) => {
      c /= 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    };
    const L = 0.2126 * lin(n >> 16) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255);
    return L > 0.5 ? "#111111" : "#ffffff";
  }

  // Seconds between the score reaching first_to and the fade-in starting, so
  // the final point can land on stream first. Preview skips it.
  const SHOW_DELAY_S = 1;

  // Fades in (mirror of hide). The delay lives in the tween itself, so the
  // killTweensOf() in hide() also cancels a show still waiting: if the score
  // is taken back within the delay the popup never appears. The fromTo keeps
  // it invisible (opacity 0) while waiting; visibility flips when it starts.
  function show(delay) {
    gsap.killTweensOf(popup);
    gsap.fromTo(
      popup,
      { opacity: 0, y: 40 },
      {
        opacity: 1,
        y: 0,
        duration: 0.4,
        ease: "power2.out",
        delay,
        onStart: () => gsap.set(popup, { visibility: "visible" }),
      }
    );
  }

  function hide() {
    gsap.killTweensOf(popup);
    gsap.to(popup, {
      opacity: 0,
      y: 40,
      duration: 0.3,
      ease: "power2.in",
      onComplete: () => gsap.set(popup, { visibility: "hidden" }),
    });
  }

  async function nameOf(team) {
    const players = Object.values(team.player || {}).filter((p) => p && p.name);
    if (players.length > 1) {
      const names = [];
      for (const p of players) names.push(await Transcript(p.name));
      return { sponsor: "", name: names.join(" / ") };
    }
    const p = players[0];
    if (!p) return { sponsor: "", name: "" };
    const sponsor = p.team && data.display_options?.show_sponsor !== false ? p.team : "";
    return { sponsor, name: await Transcript(p.name) };
  }

  // Desktop preview only: the BOARD tab stacks this page over its scoreboard
  // preview as index.html#preview=winner|champion, and the popup is then shown
  // whatever the score so its look can be checked. The OBS browser source
  // loads the file without a fragment, so none of this applies there.
  function previewMode() {
    const mode = new URLSearchParams(location.hash.slice(1)).get("preview");
    return mode === "winner" || mode === "champion" ? mode : null;
  }

  // The side that reached first_to (the higher score if both did), or 0.
  function winnerSide(sb) {
    const bestOf = Number(sb.best_of) || 0;
    const firstTo = Number(sb.first_to) || (bestOf > 0 ? Math.floor(bestOf / 2) + 1 : 0);
    const s1 = Number(sb.team["1"].score) || 0;
    const s2 = Number(sb.team["2"].score) || 0;
    if (firstTo > 0) {
      if (s1 >= firstTo && s1 >= s2) return 1;
      if (s2 >= firstTo) return 2;
    }
    return 0;
  }

  // Render the popup (and show it if it was hidden); no-op when unchanged.
  function present(side, sponsor, name, isChampion, delay = SHOW_DELAY_S) {
    const key = `${side}:${sponsor}|${name}|${isChampion}`;
    if (key === shownKey) return;

    popup.classList.toggle("champion", isChampion);
    document.querySelector(".winner_label").textContent = isChampion ? "CHAMPION" : "WINNER";
    document.querySelector(".winner_name").innerHTML =
      (sponsor ? `<span class="sponsor">${sponsor}</span>` : "") + `<span>${name}</span>`;
    const first = shownKey === null;
    shownKey = key;
    if (first) show(delay);
  }

  // Preview: the decided winner if any, else the side ahead, else team 1,
  // with a placeholder while the board has no name yet.
  async function presentPreview(sb, mode) {
    const teams = sb && sb.team && sb.team["1"] && sb.team["2"] ? sb.team : null;
    let side = 1;
    if (teams) {
      side = winnerSide(sb) ||
        ((Number(teams["2"].score) || 0) > (Number(teams["1"].score) || 0) ? 2 : 1);
    }
    const { sponsor, name } = teams
      ? await nameOf(teams[String(side)])
      : { sponsor: "", name: "" };
    present(side, sponsor, name || `PLAYER ${side}`, mode === "champion", 0);
  }

  Start = async () => {};

  Update = async (event) => {
    // Same color sources as the scoreboard (colors.board overrides the
    // tournament colors), so the popup matches the board in the same scene.
    const ti = event.data.tournamentInfo || {};
    const fc = (event.data.colors || {}).board || {};
    const theme = fc.themeColor || ti.themeColor;
    const accent = fc.accentColor || ti.accentColor;
    const root = document.documentElement.style;
    if (theme) {
      root.setProperty("--theme-color", theme);
      root.setProperty("--text-color", readableOn(theme));
    }
    if (accent) root.setProperty("--accent-color", accent);

    const sb = event.data.score && event.data.score[window.scoreboardNumber];

    const mode = previewMode();
    if (mode) return presentPreview(sb, mode);

    if (!sb || !sb.team || !sb.team["1"] || !sb.team["2"]) return;

    const side = winnerSide(sb);
    if (!side) {
      if (shownKey !== null) {
        shownKey = null;
        hide();
      }
      return;
    }

    // Grand Final / Grand Final Reset decide the tournament: WINNER -> CHAMPION.
    // The round label is free text (Start.gg or hand-typed), so match loosely.
    const isChampion = /grand\s*final|グランド\s*ファイナル|グランドファイナル/i.test(sb.match || "");

    const { sponsor, name } = await nameOf(sb.team[String(side)]);
    present(side, sponsor, name, isChampion);
  };

  // The BOARD tab switches WINNER <-> CHAMPION by rewriting the fragment
  // only (a same-document navigation): re-render from the last state.
  window.addEventListener("hashchange", () => {
    Update({ data }).catch((e) => console.error("Update() failed:", e));
  });
});
