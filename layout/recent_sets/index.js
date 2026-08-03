LoadEverything().then(() => {
  
  let startingAnimation = gsap
    .timeline({ paused: true })
    .from($(".recent_sets"), { autoAlpha: 0 });

  var playersRecentSets = null;

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
    startingAnimation.restart();
  };

  Update = async (event) => {
    let data = event.data;
    let oldData = event.oldData;

    ApplyStatsColors(data);

    if (
      !oldData.score ||
      JSON.stringify(oldData.score[window.scoreboardNumber].recent_sets) !=
        JSON.stringify(data.score[window.scoreboardNumber].recent_sets)
    ) {
      playersRecentSets = data.score[window.scoreboardNumber].recent_sets;
      console.log(playersRecentSets);
    }

    recentSetsHtml = "";

    if (
      playersRecentSets == null ||
      (playersRecentSets.state == "done" && playersRecentSets.sets.length == 0)
    ) {
      recentSetsHtml = `No sets found`;
    } else if (playersRecentSets.state != "done") {
      recentSetsHtml += `<div class="lds-ring"><div></div><div></div><div></div><div></div></div>`;
    } else {
      for (const _set of playersRecentSets.sets.slice(0, 5)) {
        recentSetsHtml += `
            <div class="set_container">
              <div class="${_set.winner == 0 ? "set_winner" : "set_loser"}">
                ${_set.score[0]}
              </div>
              <div class="set_info">
                <div class="set_title">
                  ${_set.online ? `<div class="wifi_icon"></div>` : ""}
                  ${_set.tournament}
                </div>
                <div class="set_date">
                  ${new Date(_set.timestamp * 1000).toLocaleDateString(
                    "en-US",
                    {
                      month: "short",
                      day: "2-digit",
                      year: "numeric",
                    }
                  )}
                </div>
              </div>
              <div class="${_set.winner == 1 ? "set_winner" : "set_loser"}">
                ${_set.score[1]}
              </div>
            </div>
          `;
      }
    }

    SetInnerHtml($(`.recent_sets_content`), recentSetsHtml);
  };
});
