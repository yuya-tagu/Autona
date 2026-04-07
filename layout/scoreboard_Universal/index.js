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
    startingAnimation.restart();
  };

  Update = async (event) => {
    const sb = event.data.score[window.scoreboardNumber];
    const teams = [sb.team["1"], sb.team["2"]];
    const isTeams = Object.keys(teams[0].player).length > 1;

    for (const [t, team] of teams.entries()) {
      const player = team.player["1"];
      if (!player) continue;
      const side = t + 1;

      if (!isTeams) {
        await renderPlayerInfo(t, team, player);
        document.querySelector(`.p${side}.bg`).classList.add("unhidden");
        adaptColorsForTheme(t);
      } else {
        document.querySelector(`.p${side}.bg`).classList.remove("unhidden");
        document.querySelector(`.p${side}.light`)?.classList.remove("unhidden");
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
