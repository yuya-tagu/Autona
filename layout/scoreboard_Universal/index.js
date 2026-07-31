LoadEverything().then(() => {
  gsap.config({ nullTargetWarn: false, trialWarn: false });

  let p1Score = 100, p2Score = 100;
  let newP1Score = 0, newP2Score = 0;
  let savedBestOf = 0;
  let savedGameArray = [];
  let swapDetected = false;

  const startingAnimation = buildStartingAnimation();
  const detectSwap = createSwapDetector(false);
  const detectSwapTeam = createSwapDetector(true);
  const scores = { p1Score, p2Score };

  Start = async () => {
    gsap.set("body", { opacity: 0 });
    startingAnimation.restart();
  };

  Update = async (event) => {
    // Apply theme/accent colors BEFORE the score-data guard below, so colors
    // still apply even when the score subtree isn't populated yet (e.g. game
    // not selected). Also BEFORE adaptColorsForTheme.
    // colors.board is the scoreboard-scoped override; it is only present when
    // the operator set it, so an unset feature falls back to the tournament
    // colors on its own.
    const ti = event.data.tournamentInfo || {};
    const fc = (event.data.colors || {}).board || {};
    const themeColor = fc.themeColor || ti.themeColor;
    const accentColor = fc.accentColor || ti.accentColor;
    if (themeColor) {
      document.documentElement.style.setProperty("--theme-color", themeColor);
    }
    if (accentColor) {
      document.documentElement.style.setProperty("--accent-color", accentColor);
    }

    // The first state event (or a partial delta) can arrive before this
    // board's score subtree is populated; bail until team/player data exists
    // instead of throwing "reading 'team'" on undefined.
    const sb = event.data.score && event.data.score[window.scoreboardNumber];
    if (!sb || !sb.team || !sb.team["1"] || !sb.team["2"] || !sb.team["1"].player) return;
    const teams = [sb.team["1"], sb.team["2"]];
    const isTeams = Object.keys(teams[0].player).length > 1;

    for (const [t, team] of teams.entries()) {
      // team["2"].player isn't covered by the guard above, so this can be
      // undefined for a partial/asymmetric state — guard the lookup itself.
      const player = team.player && team.player["1"];
      if (!player) continue;
      const side = t + 1;

      if (!isTeams) {
        await renderPlayerInfo(t, team, player);
        adaptColorsForTheme(t);
      } else {
        await renderTeamInfo(t, team);
        adaptColorsForTheme(t);
      }

      if (team.color) {
        document.documentElement.style.setProperty(`--p${side}-score-bg-color`, team.color);
      }
    }

    await displayMatchPhase();

    // Swap detection
    const detector = isTeams ? detectSwapTeam : detectSwap;
    swapDetected = detector(savedGameArray, scores);
    p1Score = scores.p1Score;
    p2Score = scores.p2Score;

    // Game boxes
    scoreBoxDisplayToggle();
    savedBestOf = createGameBoxes(savedBestOf);

    if (!swapDetected) {
      ({ savedGameArray, newP1Score, newP2Score, p1Score, p2Score } =
        updateGameArray(savedGameArray, newP1Score, newP2Score, p1Score, p2Score));
      scores.p1Score = p1Score;
      scores.p2Score = p2Score;
    }

    scheduleColorInBoxes(savedGameArray);
  };
});
