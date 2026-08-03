LoadEverything().then(() => {
  if (!window.PLAYER) {
    window.PLAYER = 1;
  }

  gsap.config({ nullTargetWarn: false, trialWarn: false });

  // Lets the CSS tint the header with the matching player color.
  $("body").addClass(`player${window.PLAYER}`);

  // Max number of rows rendered by this layout.
  const MAX_SETS = 6;

  let startingAnimation;

  // Safely read this layout's player name. Missing nodes yield an empty string.
  function GetPlayerName(scoreData) {
    try {
      return String(
        scoreData.team[window.PLAYER].player["1"].name || ""
      ).trim();
    } catch (e) {
      return "";
    }
  }

  // last_sets is a dict keyed "1".."N" (not an array), so sort numerically
  // like the desktop stats widget does before rendering.
  function GetLastSets(scoreData) {
    try {
      let raw = scoreData.last_sets[window.PLAYER];
      if (raw == null) return [];
      return Object.keys(raw)
        .sort((a, b) => Number(a) - Number(b))
        .map((key) => raw[key]);
    } catch (e) {
      return [];
    }
  }

  function ValueOr(value, fallback) {
    return value == null ? fallback : value;
  }

  // Win/loss can only be decided when both scores are numeric; anything else
  // (e.g. "W"/"L" placeholders) renders without a result color.
  function GetSetResult(_set) {
    let playerScore = Number(ValueOr(_set.player_score, 0));
    let oponentScore = Number(ValueOr(_set.oponent_score, 0));

    if (isNaN(playerScore) || isNaN(oponentScore)) {
      return { className: "result_unknown", label: "" };
    }

    return playerScore > oponentScore
      ? { className: "result_win", label: "WIN" }
      : { className: "result_loss", label: "LOSS" };
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
    if (startingAnimation) {
      startingAnimation.restart();
    }
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

    SetInnerHtml($(`.last_sets_player_name`), GetPlayerName(scoreData));

    if (
      !oldData.score ||
      JSON.stringify(GetLastSets(oldScoreData)) !=
        JSON.stringify(GetLastSets(scoreData))
    ) {
      let sets = GetLastSets(scoreData).slice(0, MAX_SETS);

      if (sets.length == 0) {
        $(".last_sets_content").html(
          `<div class="last_sets_empty">No sets found</div>`
        );
        startingAnimation = null;
        return;
      }

      let setsHtml = "";

      for (const [s, _set] of sets.entries()) {
        setsHtml += `
          <div class="set${s + 1} last_set_container ${
          GetSetResult(_set).className
        }">
            <div class="last_set_result"></div>
            <div class="last_set_info">
              <div class="last_set_round"></div>
              <div class="last_set_oponent"></div>
            </div>
            <div class="last_set_score"></div>
          </div>`;
      }

      $(".last_sets_content").html(setsHtml);

      startingAnimation = gsap.timeline({ paused: true });

      for (const [s, _set] of sets.entries()) {
        let roundText = [
          ValueOr(_set.phase_name, ""),
          ValueOr(_set.round_name, ""),
        ]
          .filter((part) => String(part).length > 0)
          .join(" &middot; ");

        let oponentText = String(ValueOr(_set.oponent_name, ""));

        if (_set.oponent_team) {
          oponentText = `<span class="oponent_team">${_set.oponent_team}</span>${oponentText}`;
        }

        SetInnerHtml(
          $(`.last_sets_content .set${s + 1} .last_set_result`),
          GetSetResult(_set).label
        );
        SetInnerHtml(
          $(`.last_sets_content .set${s + 1} .last_set_info .last_set_round`),
          roundText
        );
        SetInnerHtml(
          $(`.last_sets_content .set${s + 1} .last_set_info .last_set_oponent`),
          oponentText
        );
        SetInnerHtml(
          $(`.last_sets_content .set${s + 1} .last_set_score`),
          `${ValueOr(_set.player_score, 0)} - ${ValueOr(_set.oponent_score, 0)}`
        );

        startingAnimation.from(
          $(`.last_sets_content .set${s + 1}`),
          { x: -100, autoAlpha: 0, duration: 0.3 },
          0.2 + 0.15 * s
        );
      }

      startingAnimation.restart();
    }
  };
});
