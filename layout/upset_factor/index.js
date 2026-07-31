LoadEverything().then(() => {
  gsap.config({ nullTargetWarn: false, trialWarn: false });

  // Tracks whether the badge is currently shown, so we only animate on flips.
  // null means "not decided yet" (nothing rendered so far).
  var isVisible = null;

  // Safely read a scoreboard player name. Missing nodes yield an empty string.
  function GetPlayerName(scoreData, team) {
    try {
      return String(scoreData.team[team].player["1"].name || "").trim();
    } catch (e) {
      return "";
    }
  }

  // Only these fields drive this layout, so diff against them alone.
  function GetRelevantData(scoreData) {
    if (scoreData == null) return null;
    return {
      upset_factor: scoreData.upset_factor,
      p1: GetPlayerName(scoreData, "1"),
      p2: GetPlayerName(scoreData, "2"),
    };
  }

  // Show/hide the badge tile. The desktop stats widget only treats the upset
  // factor as meaningful when both players are selected, so we mirror that.
  function SetBadgeVisible(visible, animate) {
    if (visible) {
      if (animate) {
        gsap.fromTo(
          $(".upset_factor"),
          { autoAlpha: 0, y: -20 },
          { autoAlpha: 1, y: 0, duration: 0.4 }
        );
      } else {
        gsap.set($(".upset_factor"), { autoAlpha: 1, y: 0 });
      }
    } else {
      gsap.to($(".upset_factor"), { autoAlpha: 0, duration: 0.3 });
    }
  }

  // Stats overlays follow colors.stats and fall back to the tournament
  // colors (colors.stats is only present when the operator set it). Applied
  // as CSS variables so every stats template can consume them.
  function ApplyStatsColors(data) {
    const ti = (data && data.tournamentInfo) || {};
    const fc = ((data && data.colors) || {}).stats || {};
    const themeColor = fc.themeColor || ti.themeColor;
    const accentColor = fc.accentColor || ti.accentColor;
    if (themeColor) {
      document.documentElement.style.setProperty("--theme-color", themeColor);
    }
    if (accentColor) {
      document.documentElement.style.setProperty("--accent-color", accentColor);
    }
  }

  Start = async (event) => {
    SetBadgeVisible(isVisible === true, true);
  };

  Update = async (event) => {
    let data = event.data;
    let oldData = event.oldData;

    // Before the score-data guard below, so the colors still apply when the
    // score subtree isn't populated yet.
    ApplyStatsColors(data);

    let scoreData = data.score ? data.score[window.scoreboardNumber] : null;
    if (scoreData == null) return;

    let oldScoreData = oldData.score
      ? oldData.score[window.scoreboardNumber]
      : null;

    if (
      !oldData.score ||
      JSON.stringify(GetRelevantData(oldScoreData)) !=
        JSON.stringify(GetRelevantData(scoreData))
    ) {
      let p1Name = GetPlayerName(scoreData, "1");
      let p2Name = GetPlayerName(scoreData, "2");
      let upsetFactor = scoreData.upset_factor;

      if (upsetFactor == null) upsetFactor = 0;

      SetInnerHtml($(".upset_factor_value"), String(upsetFactor));
      SetInnerHtml($(".upset_factor_matchup"), `${p1Name} vs ${p2Name}`);

      let shouldShow = p1Name.length > 0 && p2Name.length > 0;

      if (shouldShow !== isVisible) {
        SetBadgeVisible(shouldShow, isVisible !== null);
        isVisible = shouldShow;
      }
    }
  };
});
